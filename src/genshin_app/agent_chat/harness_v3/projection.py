"""Single Host projection for GUI and CLI command availability."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Iterable, Mapping
from typing import Any

from .artifacts import (
    ArtifactRegistry,
    default_artifact_registry,
    propagate_artifact_freshness,
)
from .candidate import derive_candidate_state
from .failures import classify_native_failure
from .interruptions import ResumeCapabilityIssuer, project_interruptions

CONTRACT = "native-harness-projection-v3"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _projection_revision(value: object) -> int:
    # 52 bits stay exactly representable in JavaScript while being stable for
    # the same complete fact set.  It is an optimistic projection token, not a
    # replacement for domain CAS/receipt checks.
    return int(hashlib.sha256(_canonical(value)).hexdigest()[:13], 16)


def _runtime_fact(value: Mapping[str, Any]) -> dict[str, Any]:
    raw_health = value.get("runtime_health")
    raw_state = value.get("runtime_state")
    if raw_health == "healthy" or raw_state == "ready":
        state = "healthy"
    elif raw_health in {"unhealthy", "crashed"} or raw_state in {
        "failed", "unhealthy", "crashed",
    }:
        state = "crashed"
    elif raw_state == "starting":
        state = "starting"
    elif raw_health == "stopped" or raw_state in {"cold", "stopped", "closed"}:
        state = "stopped"
    else:
        state = "degraded"
    return {
        "state": state,
        "runtime_incarnation": value.get("runtime_incarnation"),
        "failure_code": value.get("failure_code"),
    }


def _turn_fact(
    value: Mapping[str, Any] | None,
    *,
    active: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if active is not None:
        return {
            "state": "running",
            "turn_id": active.get("turn_id"),
            "error_ref": None,
        }
    value = {} if value is None else value
    raw = value.get("state", value.get("status"))
    state = {
        "preparing": "queued",
        "queued": "queued",
        "streaming": "running",
        "using_tool": "running",
        "running": "running",
        "awaiting_input_live": "suspended",
        "suspended": "suspended",
        "completed": "completed",
        "failed": "failed",
        "error": "failed",
        "interrupted": "failed",
        "cancelled": "cancelled",
    }.get(raw, "completed")
    error = value.get("terminal_reason") or value.get("error")
    return {
        "state": state,
        "turn_id": value.get("turn_id"),
        "error_ref": error if isinstance(error, str) else None,
    }


def _command(
    command_id: str,
    *,
    risk: str,
    scope: Mapping[str, Any] | None = None,
    reason_code: str | None = None,
    invocation_kind: str = "host_command",
) -> dict[str, Any]:
    return {
        "command_id": command_id,
        "enabled": True,
        "scope": copy.deepcopy(dict(scope or {})),
        "risk": risk,
        "reason_code": reason_code,
        "invocation_kind": invocation_kind,
    }


def _derive_commands(
    *,
    runtime: Mapping[str, Any],
    turn: Mapping[str, Any],
    candidate: Mapping[str, Any],
    interruptions: list[Mapping[str, Any]],
    session_continuable: bool,
) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = [
        _command("diagnostics.inspect", risk="read_only"),
    ]
    runtime_state = runtime["state"]
    turn_state = turn["state"]
    if runtime_state == "stopped":
        commands.append(_command("runtime.start", risk="local"))
    elif runtime_state == "starting":
        commands.append(_command("runtime.cancel_start", risk="local"))

    if turn_state == "running":
        commands.extend((
            _command("turn.steer", risk="local", scope={"turn_id": turn.get("turn_id")}),
            _command("turn.cancel", risk="local", scope={"turn_id": turn.get("turn_id")}),
        ))
    elif turn_state == "failed":
        if runtime_state in {"healthy", "degraded"} and session_continuable:
            commands.extend((
                _command("conversation.send", risk="local"),
                _command("turn.retry", risk="local", scope={"turn_id": turn.get("turn_id")}),
            ))
    elif turn_state != "suspended" and runtime_state in {"healthy", "degraded"} and session_continuable:
        commands.append(_command("conversation.send", risk="local"))

    if candidate["draft_present"] and candidate["seal_state"] == "unsealed":
        commands.extend((
            _command("draft.inspect", risk="read_only"),
            _command("draft.continue", risk="local"),
            _command("candidate.revalidate", risk="local"),
            _command("draft.discard", risk="local"),
        ))
    elif candidate["seal_state"] == "sealed":
        commands.append(_command("draft.inspect", risk="read_only"))
        if candidate["reviewable"]:
            commands.extend((
                _command("candidate.review", risk="review"),
                _command("candidate.reject", risk="local"),
            ))
        else:
            commands.append(_command("candidate.revalidate", risk="local"))

    application = candidate["application_state"]
    if application in {"prepared", "baseline_advanced"}:
        commands.extend((
            _command("application.resume", risk="app_side_effect"),
            _command("application.abort", risk="app_side_effect"),
        ))
    elif application == "outcome_unknown":
        commands.extend((
            _command("side_effect.verify", risk="read_only"),
            _command("side_effect.export_diagnostics", risk="read_only"),
            _command("side_effect.manual_recover", risk="app_side_effect"),
        ))

    for interruption in interruptions:
        scope = {
            "interruption_id": interruption["interruption_id"],
            "resume_capability": interruption["resume_capability"],
        }
        kind = interruption["kind"]
        parameter_summary = interruption.get("parameter_summary")
        # ``resume_harness_interruption`` currently has exactly one scoped
        # execution owner: an open durable question batch.  This includes a
        # provisional-entity approval because it is itself a DSH question and
        # the existing answer owner validates and settles that approval. Other
        # typed interruptions remain useful diagnostic facts in V3 shadow
        # mode, but must not advertise commands that the Service would reject
        # after admission. Adding their executors is a separate,
        # permission-bearing change rather than something projection may
        # pretend already exists.
        if (
            kind in {"user_question", "tool_approval"}
            and isinstance(parameter_summary, Mapping)
            and parameter_summary.get("state") == "open"
        ):
            commands.append(_command(
                "interruption.answer",
                risk="local",
                scope=scope,
                invocation_kind="interruption_resume",
            ))

    # Stable order and de-duplication are essential because GUI and CLI use
    # this exact list as the user command contract.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, bytes]] = set()
    for command in commands:
        key = (command["command_id"], _canonical(command["scope"]))
        if key not in seen:
            seen.add(key)
            unique.append(command)
    return unique


def build_harness_projection(
    *,
    runtime: Mapping[str, Any],
    turn: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    artifacts: Iterable[Mapping[str, Any]] = (),
    interruptions: Iterable[Mapping[str, Any]] = (),
    focus_stage: str | None = None,
    focus_reason: str = "host_recommendation",
    session_continuable: bool = True,
    registry: ArtifactRegistry | None = None,
    issues: Iterable[Mapping[str, Any]] = (),
    authority_mode: str = "shadow",
) -> dict[str, Any]:
    """Build the single immutable command projection from Host facts."""

    registry = registry or default_artifact_registry()
    artifact_facts = propagate_artifact_freshness(artifacts, registry)
    runtime_fact = _runtime_fact(runtime)
    turn_fact = _turn_fact(turn)
    interruption_facts = [copy.deepcopy(dict(row)) for row in interruptions]
    candidate_fact = copy.deepcopy(dict(candidate))
    commands = _derive_commands(
        runtime=runtime_fact,
        turn=turn_fact,
        candidate=candidate_fact,
        interruptions=interruption_facts,
        session_continuable=session_continuable,
    )
    resume_commands_by_interruption: dict[str, list[str]] = {}
    for command in commands:
        if command.get("invocation_kind") != "interruption_resume":
            continue
        scope = command.get("scope")
        interruption_id = (
            scope.get("interruption_id") if isinstance(scope, Mapping) else None
        )
        if isinstance(interruption_id, str):
            resume_commands_by_interruption.setdefault(interruption_id, []).append(
                command["command_id"]
            )
    for interruption in interruption_facts:
        command_ids = resume_commands_by_interruption.get(
            interruption["interruption_id"],
            [],
        )
        interruption["execution_state"] = (
            "executable" if command_ids else "diagnostic_only"
        )
        interruption["command_ids"] = list(command_ids)
        interruption["execution_reason"] = (
            None if command_ids
            else "harness_interruption_executor_unavailable"
        )
    blockers: list[dict[str, Any]] = []
    if runtime_fact["state"] == "crashed":
        blockers.append({
            "code": "runtime_crashed",
            "scope": "provider_turn",
            "hard": True,
        })
    if candidate_fact["binding_state"] == "conflict":
        blockers.append({
            "code": "document_binding_conflict",
            "scope": "candidate_application",
            "hard": True,
        })
    if candidate_fact["freshness"] == "stale":
        blockers.append({
            "code": "candidate_stale",
            "scope": "candidate_review",
            "hard": True,
        })
    for interruption in interruption_facts:
        interruption_id = interruption["interruption_id"]
        executable = bool(resume_commands_by_interruption.get(interruption_id))
        blockers.append({
            "code": f"interruption_{interruption['kind']}",
            "scope": copy.deepcopy(interruption.get("blocking_scope", {})),
            "hard": (
                interruption["kind"] in {
                    "side_effect_recovery", "document_conflict",
                }
                or (
                    interruption["kind"] == "system_recovery"
                    and executable
                )
            ),
            "interruption_id": interruption_id,
            "execution_state": (
                "executable" if executable else "diagnostic_only"
            ),
            "execution_reason": (
                None if executable
                else "harness_interruption_executor_unavailable"
            ),
        })
    failure = (
        classify_native_failure(turn_fact["error_ref"], runtime=runtime)
        if turn_fact["error_ref"] is not None
        else None
    )
    source = {
        "runtime": runtime_fact,
        "turn": turn_fact,
        "focus": {
            "recommended_stage": focus_stage,
            "reason": focus_reason,
        },
        "artifacts": artifact_facts,
        "candidate": candidate_fact,
        "interruptions": interruption_facts,
        "blocking_conditions": blockers,
        "issues": [copy.deepcopy(dict(row)) for row in issues],
        "failure": failure,
        "authority_mode": authority_mode,
    }
    revision = _projection_revision(source)
    for command in commands:
        command["expected_projection_revision"] = revision
    command_ids = {row["command_id"] for row in commands}
    recommended = next((
        command_id
        for command_id in (
            "interruption.answer",
            "application.resume",
            "conversation.send",
            "turn.retry",
            "draft.continue",
            "candidate.review",
            "diagnostics.inspect",
        )
        if command_id in command_ids
    ), None)
    return {
        "contract": CONTRACT,
        "projection_revision": revision,
        **source,
        "available_commands": commands,
        # Required actions are executable Host contracts, not a second copy of
        # every observed interruption.  Diagnostic-only V3 shadow facts stay
        # visible in ``interruptions`` and ``blocking_conditions`` without
        # creating a button that cannot be dispatched.
        "required_actions": [
            {
                "interruption_id": row["interruption_id"],
                "kind": row["kind"],
                "state": "pending",
                "command_ids": resume_commands_by_interruption[
                    row["interruption_id"]
                ],
            }
            for row in interruption_facts
            if row["interruption_id"] in resume_commands_by_interruption
        ],
        "recommended_command": recommended,
        "artifact_registry": registry.specifications(),
    }


def _legacy_artifacts(
    snapshot: Mapping[str, Any],
    summary: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Conservative V2 shadow facts; stage never implies V3 validity."""

    summary = {} if summary is None else summary
    checkpoint = snapshot.get("candidate_snapshot")
    checkpoint = checkpoint if isinstance(checkpoint, Mapping) else {}
    revision = checkpoint.get("candidate_revision", 0)
    if type(revision) is not int or revision <= 0:
        return []
    stage = snapshot.get("workflow_stage")
    order = {
        "loadout_draft": ("task_contract", "user_constraints", "database_binding", "loadout"),
        "action_checkpoint": ("task_contract", "user_constraints", "database_binding", "loadout", "action_graph"),
        "event_topology": ("task_contract", "user_constraints", "database_binding", "loadout", "action_graph", "mechanic_materialization", "event_topology"),
        "numeric_model": ("task_contract", "user_constraints", "database_binding", "loadout", "action_graph", "mechanic_materialization", "event_topology", "numeric_bindings"),
        "calculation_closure": ("task_contract", "user_constraints", "database_binding", "loadout", "action_graph", "mechanic_materialization", "event_topology", "numeric_bindings", "calculation_attempt", "calculation_review", "report"),
    }.get(stage, ())
    facts = []
    for kind in order:
        facts.append({
            "artifact_id": f"{kind}:main",
            "kind": kind,
            "schema_version": 1,
            "revision": revision,
            "content_fingerprint": None,
            "bindings": {},
            "dependencies": [],
            "presence": "draft",
            "validation": "unchecked",
            "freshness": (
                "stale" if summary.get("database_current") is False else "fresh"
            ),
            "completeness": "unknown",
            "issues": [{
                "code": "legacy_shadow_unverified",
                "severity": "info",
                "impact_domain": "migration",
            }],
            "source": "legacy_v2_shadow",
        })
    return facts


def project_legacy_harness(
    *,
    runtime_status: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    workbench: Mapping[str, Any],
    candidate_summary: Mapping[str, Any] | None,
    application_state: str,
    document_binding: Mapping[str, Any] | None,
    app_actions: Iterable[Mapping[str, Any]],
    current_database_fingerprint: str | None,
    issuer: ResumeCapabilityIssuer,
) -> dict[str, Any]:
    """Read-only V2 -> V3 shadow adapter used identically by GUI and CLI."""

    checkpoint = snapshot.get("candidate_snapshot")
    checkpoint = checkpoint if isinstance(checkpoint, Mapping) else {}
    stored_database = checkpoint.get("database_fingerprint")
    database_current = (
        current_database_fingerprint is None
        or not isinstance(stored_database, str)
        or stored_database == current_database_fingerprint
    )
    document_id = checkpoint.get("document_id")
    binding_document = (
        document_binding.get("document_id")
        if isinstance(document_binding, Mapping)
        else None
    )
    binding_conflict = (
        isinstance(document_id, str)
        and isinstance(binding_document, str)
        and document_id != binding_document
    )
    candidate = derive_candidate_state(
        checkpoint=checkpoint,
        summary=candidate_summary,
        application_state=application_state,
        database_current=database_current,
        binding_conflict=binding_conflict,
        review_authority_verified=candidate_summary is not None,
    )
    turns = workbench.get("turns") if isinstance(workbench.get("turns"), list) else []
    latest_turn = turns[-1] if turns else snapshot.get("last_turn")
    active = snapshot.get("active_turn")
    turn = _turn_fact(
        latest_turn if isinstance(latest_turn, Mapping) else None,
        active=active if isinstance(active, Mapping) else None,
    )
    runtime = _runtime_fact(runtime_status)
    questions = workbench.get("questions") if isinstance(workbench.get("questions"), list) else []
    interruption_facts = project_interruptions(
        issuer=issuer,
        questions=questions,
        app_actions=app_actions,
        application_state=application_state,
        binding_conflict=binding_conflict,
        runtime_state=runtime["state"],
        runtime_failure_code=(
            runtime_status.get("failure_code")
            if isinstance(runtime_status.get("failure_code"), str)
            else None
        ),
    )
    return build_harness_projection(
        runtime=runtime_status,
        turn=turn,
        candidate=candidate,
        artifacts=_legacy_artifacts(snapshot, candidate_summary),
        interruptions=interruption_facts,
        focus_stage=(
            snapshot.get("workflow_stage")
            if isinstance(snapshot.get("workflow_stage"), str)
            else None
        ),
        focus_reason="legacy_focus_hint_only",
        session_continuable=True,
        issues=[{
            "code": "legacy_v2_shadow_projection",
            "severity": "info",
            "impact_domain": "migration",
        }],
        authority_mode="shadow",
    )


__all__ = [
    "CONTRACT",
    "build_harness_projection",
    "project_legacy_harness",
]
