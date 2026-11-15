"""Typed interruption projection and Host-opaque resume capabilities."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Iterable, Mapping
from typing import Any

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}\Z")
INTERRUPTION_KINDS = frozenset({
    "user_question",
    "tool_approval",
    "file_action",
    "side_effect_recovery",
    "document_conflict",
    "system_recovery",
})


class InterruptionContractError(ValueError):
    pass


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        raise InterruptionContractError(f"{field}_invalid")
    return value


def _canonical(value: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise InterruptionContractError("interruption_payload_invalid") from None


class ResumeCapabilityIssuer:
    """Issue non-self-describing process capabilities for projected responses.

    The capability is intentionally process-local.  Durable interruption facts
    survive restart and are projected again with the same allowed responses;
    old opaque capabilities expire rather than becoming a second persistence
    authority.
    """

    def __init__(self, secret: bytes | None = None) -> None:
        self._secret = secret if secret is not None else secrets.token_bytes(32)
        if not isinstance(self._secret, bytes) or len(self._secret) < 32:
            raise InterruptionContractError("resume_capability_secret_invalid")

    def issue(self, payload: Mapping[str, Any]) -> str:
        body = _canonical(payload)
        digest = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return f"hri1.{digest}"

    def verify(self, payload: Mapping[str, Any], capability: object) -> bool:
        if not isinstance(capability, str):
            return False
        return hmac.compare_digest(self.issue(payload), capability)


def _typed_interruption(
    *,
    issuer: ResumeCapabilityIssuer,
    interruption_id: str,
    kind: str,
    source_turn_id: str | None,
    source_step_id: str | None,
    parameter_summary: Mapping[str, Any],
    impact_domain: str,
    allowed_responses: Iterable[str],
    blocking_scope: Mapping[str, Any] | None = None,
    source_authority: str = "host",
    source_revision: int | None = None,
) -> dict[str, Any]:
    interruption_id = _identity(interruption_id, "interruption_id")
    if kind not in INTERRUPTION_KINDS:
        raise InterruptionContractError("interruption_kind_invalid")
    if source_turn_id is not None:
        source_turn_id = _identity(source_turn_id, "source_turn_id")
    if source_step_id is not None:
        source_step_id = _identity(source_step_id, "source_step_id")
    impact_domain = _identity(impact_domain, "impact_domain")
    source_authority = _identity(source_authority, "source_authority")
    if source_revision is not None and (
        type(source_revision) is not int or source_revision < 0
    ):
        raise InterruptionContractError("source_revision_invalid")
    responses = tuple(_identity(item, "allowed_response") for item in allowed_responses)
    if not responses or len(set(responses)) != len(responses):
        raise InterruptionContractError("interruption_responses_invalid")
    authority = {
        "interruption_id": interruption_id,
        "kind": kind,
        "source_turn_id": source_turn_id,
        "source_step_id": source_step_id,
        "allowed_responses": list(responses),
        "parameter_summary": copy.deepcopy(dict(parameter_summary)),
        "impact_domain": impact_domain,
        "blocking_scope": copy.deepcopy(dict(blocking_scope or {})),
        "source_authority": source_authority,
        "source_revision": source_revision,
    }
    return {
        **authority,
        "resume_capability": issuer.issue(authority),
    }


def project_interruptions(
    *,
    issuer: ResumeCapabilityIssuer,
    questions: Iterable[Mapping[str, Any]] = (),
    app_actions: Iterable[Mapping[str, Any]] = (),
    application_state: str = "none",
    binding_conflict: bool = False,
    runtime_state: str = "healthy",
    runtime_failure_code: str | None = None,
    explicit: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Project all pending interruptions from durable Host facts."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def append(row: dict[str, Any]) -> None:
        if row["interruption_id"] in seen:
            raise InterruptionContractError("interruption_id_duplicate")
        seen.add(row["interruption_id"])
        result.append(row)

    for question in questions:
        if not isinstance(question, Mapping):
            continue
        # Only the durable owner state ``open`` accepts a new answer.  The
        # other states are settlement/replay windows: publishing another
        # resume capability there would let a client submit the same answer
        # twice while the first mutation is still being reconciled.
        if question.get("state") != "open":
            continue
        question_id = question.get("question_id") or question.get("batch_id")
        if not isinstance(question_id, str):
            continue
        items = question.get("items") if isinstance(question.get("items"), list) else []
        allow_skip = bool(items) and all(
            isinstance(item, Mapping) and item.get("allow_skip") is True
            for item in items
        )
        approval_scope = question.get("approval_scope")
        is_approval = isinstance(approval_scope, str) and bool(approval_scope)
        append(_typed_interruption(
            issuer=issuer,
            interruption_id=f"question:{question_id}",
            kind="tool_approval" if is_approval else "user_question",
            source_turn_id=(
                question.get("turn_id") if isinstance(question.get("turn_id"), str) else None
            ),
            source_step_id=(
                question.get("call_id") if isinstance(question.get("call_id"), str) else None
            ),
            parameter_summary={
                "question_id": question_id,
                "item_count": len(items),
                "state": question.get("state"),
                "approval_scope": approval_scope if is_approval else None,
            },
            impact_domain=(approval_scope if is_approval else "conversation_turn"),
            allowed_responses=(
                ("approve", "reject", "inspect_impact")
                if is_approval
                else ("answer", "skip") if allow_skip else ("answer",)
            ),
            blocking_scope={"turn_id": question.get("turn_id")},
            source_authority=(
                question.get("content_authority")
                if isinstance(question.get("content_authority"), str)
                else "native_workbench_ledger"
            ),
            source_revision=(
                question.get("revision")
                if type(question.get("revision")) is int
                else None
            ),
        ))

    for action in app_actions:
        if not isinstance(action, Mapping):
            continue
        if action.get("status") not in {
            "queued", "presented", "claimed", "outcome_unknown",
        }:
            continue
        action_id = action.get("action_id")
        if not isinstance(action_id, str):
            continue
        unknown = action.get("status") == "outcome_unknown"
        append(_typed_interruption(
            issuer=issuer,
            interruption_id=f"action:{action_id}",
            kind="side_effect_recovery" if unknown else "file_action",
            source_turn_id=(
                action.get("turn_id") if isinstance(action.get("turn_id"), str) else None
            ),
            source_step_id=action_id,
            parameter_summary={
                "action_id": action_id,
                "action": action.get("action"),
                "status": action.get("status"),
            },
            impact_domain="app_side_effect",
            allowed_responses=(
                ("verify", "mark_completed", "mark_not_applied")
                if unknown
                else ("complete", "cancel")
            ),
            blocking_scope={"action_id": action_id},
            source_authority="native_app_action_ledger",
            source_revision=(
                action.get("revision")
                if type(action.get("revision")) is int
                else None
            ),
        ))

    if application_state == "outcome_unknown":
        append(_typed_interruption(
            issuer=issuer,
            interruption_id="application:outcome-unknown",
            kind="side_effect_recovery",
            source_turn_id=None,
            source_step_id=None,
            parameter_summary={"application_state": application_state},
            impact_domain="candidate_application",
            allowed_responses=("verify", "export_diagnostics", "manual_recover"),
            blocking_scope={"application": "candidate"},
            source_authority="native_candidate_application_intent",
        ))
    if binding_conflict:
        append(_typed_interruption(
            issuer=issuer,
            interruption_id="document:binding-conflict",
            kind="document_conflict",
            source_turn_id=None,
            source_step_id=None,
            parameter_summary={"binding_state": "conflict"},
            impact_domain="document_binding",
            allowed_responses=("inspect", "rebind", "discard_candidate"),
            blocking_scope={"document": "current"},
            source_authority="native_workbench_document_binding",
        ))
    if runtime_state == "crashed":
        append(_typed_interruption(
            issuer=issuer,
            interruption_id="runtime:system-recovery",
            kind="system_recovery",
            source_turn_id=None,
            source_step_id=None,
            parameter_summary={"failure_code": runtime_failure_code},
            impact_domain="runtime",
            allowed_responses=("restart", "inspect_diagnostics"),
            blocking_scope={"runtime": "native_agent"},
            source_authority="native_runtime_status",
        ))
    for raw in explicit:
        if not isinstance(raw, Mapping):
            raise InterruptionContractError("explicit_interruption_invalid")
        append(_typed_interruption(
            issuer=issuer,
            interruption_id=_identity(raw.get("interruption_id"), "interruption_id"),
            kind=raw.get("kind"),
            source_turn_id=raw.get("source_turn_id"),
            source_step_id=raw.get("source_step_id"),
            parameter_summary=(
                raw.get("parameter_summary")
                if isinstance(raw.get("parameter_summary"), Mapping)
                else {}
            ),
            impact_domain=raw.get("impact_domain", "conversation_turn"),
            allowed_responses=raw.get("allowed_responses", ()),
            blocking_scope=(
                raw.get("blocking_scope")
                if isinstance(raw.get("blocking_scope"), Mapping)
                else {}
            ),
            source_authority=raw.get("source_authority", "host"),
            source_revision=raw.get("source_revision"),
        ))
    return result


__all__ = [
    "INTERRUPTION_KINDS",
    "InterruptionContractError",
    "ResumeCapabilityIssuer",
    "project_interruptions",
]
