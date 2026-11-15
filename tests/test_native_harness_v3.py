from __future__ import annotations

import itertools

import pytest

from genshin_app.agent_chat.harness_v3 import (
    COMMAND_CONTRACT,
    HarnessCommandError,
    ResumeCapabilityIssuer,
    build_harness_projection,
    classify_native_failure,
    default_artifact_registry,
    derive_candidate_state,
    failure_requires_runtime_teardown,
    normalize_artifact_fact,
    project_interruptions,
    project_legacy_harness,
    propagate_artifact_freshness,
    validate_harness_command,
)

HASH_A = "a" * 64
HASH_B = "b" * 64


def _artifact(kind, *, revision=1, dependencies=(), freshness="fresh"):
    return {
        "artifact_id": f"{kind}:main",
        "kind": kind,
        "schema_version": 1,
        "revision": revision,
        "content_fingerprint": HASH_A,
        "bindings": {},
        "dependencies": list(dependencies),
        "presence": "present",
        "validation": "valid",
        "freshness": freshness,
        "completeness": "complete",
        "issues": [],
    }


def test_artifact_registry_propagates_only_concrete_dependency_closure():
    registry = default_artifact_registry()
    values = [
        _artifact("loadout", revision=2),
        _artifact("action_graph"),
        _artifact("event_topology", dependencies=[{
            "artifact_id": "action_graph:main",
            "revision": 1,
            "fingerprint": HASH_A,
        }]),
        _artifact("numeric_bindings", dependencies=[{
            "artifact_id": "loadout:main",
            "revision": 1,
            "fingerprint": HASH_A,
        }]),
        _artifact("calculation_attempt", dependencies=[{
            "artifact_id": "numeric_bindings:main",
            "revision": 1,
            "fingerprint": HASH_A,
        }]),
    ]

    result = {
        row["kind"]: row
        for row in propagate_artifact_freshness(values, registry)
    }

    assert result["action_graph"]["freshness"] == "fresh"
    assert result["event_topology"]["freshness"] == "fresh"
    assert result["numeric_bindings"]["freshness"] == "stale"
    assert result["calculation_attempt"]["freshness"] == "stale"
    assert result["numeric_bindings"]["freshness_causes"] == [{
        "code": "dependency_revision_changed",
        "artifact_id": "loadout:main",
    }]


def test_artifact_axes_are_orthogonal_and_never_persist_blocked():
    fact = normalize_artifact_fact({
        "artifact_id": "event_topology:main",
        "kind": "event_topology",
        "schema_version": 1,
        "revision": 7,
        "presence": "draft",
        "validation": "invalid",
        "freshness": "stale",
        "completeness": "partial",
        "dependencies": [],
        "issues": [],
    }, default_artifact_registry())

    assert fact["presence"] == "draft"
    assert fact["validation"] == "invalid"
    assert fact["freshness"] == "stale"
    assert fact["completeness"] == "partial"
    assert "blocked" not in fact


def test_typed_interruption_uses_opaque_host_capability():
    issuer = ResumeCapabilityIssuer(b"x" * 32)
    projected = project_interruptions(
        issuer=issuer,
        questions=[{
            "question_id": "q-1",
            "turn_id": "turn-1",
            "call_id": "call-1",
            "state": "open",
            "items": [{"allow_skip": False}],
        }],
    )

    assert projected[0]["kind"] == "user_question"
    assert projected[0]["allowed_responses"] == ["answer"]
    assert projected[0]["resume_capability"].startswith("hri1.")
    authority = {
        key: projected[0][key]
        for key in (
            "interruption_id", "kind", "source_turn_id", "source_step_id",
            "allowed_responses", "parameter_summary", "impact_domain",
            "blocking_scope", "source_authority", "source_revision",
        )
    }
    assert issuer.verify(authority, projected[0]["resume_capability"])
    authority["allowed_responses"] = ["answer", "approve"]
    assert not issuer.verify(authority, projected[0]["resume_capability"])


def test_typed_interruption_reprojects_from_durable_source_after_restart():
    durable_question = {
        "question_id": "q-restart",
        "turn_id": "turn-restart",
        "call_id": "call-restart",
        "state": "open",
        "items": [{"allow_skip": False}],
    }
    before_issuer = ResumeCapabilityIssuer(b"b" * 32)
    after_issuer = ResumeCapabilityIssuer(b"a" * 32)
    before = project_interruptions(
        issuer=before_issuer,
        questions=[durable_question],
    )[0]
    after = project_interruptions(
        issuer=after_issuer,
        questions=[durable_question],
    )[0]

    stable_fields = (
        "interruption_id",
        "kind",
        "source_turn_id",
        "source_step_id",
        "parameter_summary",
        "allowed_responses",
        "blocking_scope",
        "source_authority",
        "source_revision",
    )
    assert {key: before[key] for key in stable_fields} == {
        key: after[key] for key in stable_fields
    }
    assert before["resume_capability"] != after["resume_capability"]
    authority = {
        key: after[key]
        for key in (
            "interruption_id",
            "kind",
            "source_turn_id",
            "source_step_id",
            "allowed_responses",
            "parameter_summary",
            "impact_domain",
            "blocking_scope",
            "source_authority",
            "source_revision",
        )
    }
    assert not after_issuer.verify(authority, before["resume_capability"])
    assert after_issuer.verify(authority, after["resume_capability"])


def test_approval_scope_projects_as_typed_tool_approval():
    projected = project_interruptions(
        issuer=ResumeCapabilityIssuer(b"p" * 32),
        questions=[{
            "question_id": "q-approval",
            "turn_id": "turn-approval",
            "call_id": "call-approval",
            "state": "open",
            "items": [{"allow_skip": False}],
            "approval_scope": "provisional_public_entity",
        }],
    )[0]

    assert projected["kind"] == "tool_approval"
    assert projected["impact_domain"] == "provisional_public_entity"
    assert projected["allowed_responses"] == [
        "approve",
        "reject",
        "inspect_impact",
    ]


@pytest.mark.parametrize(
    "state",
    ("answer_submitting", "answered", "resuming"),
)
def test_question_settlement_window_does_not_reoffer_answer(state):
    projected = project_interruptions(
        issuer=ResumeCapabilityIssuer(b"s" * 32),
        questions=[{
            "question_id": f"q-{state}",
            "turn_id": "turn-settlement",
            "state": state,
            "items": [{"allow_skip": False}],
        }],
    )

    assert projected == []


def test_projection_only_publishes_implemented_interruption_resume_command():
    issuer = ResumeCapabilityIssuer(b"i" * 32)
    interruptions = project_interruptions(
        issuer=issuer,
        questions=[
            {
                "question_id": "q-user",
                "turn_id": "turn-one",
                "state": "open",
                "items": [{"allow_skip": False}],
            },
            {
                "question_id": "q-approval",
                "turn_id": "turn-one",
                "state": "open",
                "items": [{"allow_skip": False}],
                "approval_scope": "provisional_public_entity",
            },
        ],
        app_actions=[
            {
                "action_id": "save-one",
                "turn_id": "turn-one",
                "status": "queued",
                "action": "save_current",
            },
            {
                "action_id": "save-unknown",
                "turn_id": "turn-one",
                "status": "outcome_unknown",
                "action": "save_current",
            },
        ],
        application_state="outcome_unknown",
        binding_conflict=True,
        runtime_state="crashed",
        runtime_failure_code="worker_exited",
    )
    projection = build_harness_projection(
        runtime={
            "runtime_state": "crashed",
            "runtime_health": "crashed",
            "failure_code": "worker_exited",
        },
        turn={"status": "failed", "turn_id": "turn-one"},
        candidate=derive_candidate_state(
            checkpoint={"candidate_revision": 0, "receipt_fingerprint": None},
        ),
        interruptions=interruptions,
    )

    resume_commands = [
        row for row in projection["available_commands"]
        if row["invocation_kind"] == "interruption_resume"
    ]
    assert [row["command_id"] for row in resume_commands] == [
        "interruption.answer", "interruption.answer",
    ]
    assert resume_commands[0]["scope"]["interruption_id"] == "question:q-user"
    assert resume_commands[1]["scope"]["interruption_id"] == (
        "question:q-approval"
    )
    assert projection["required_actions"] == [
        {
            "interruption_id": "question:q-user",
            "kind": "user_question",
            "state": "pending",
            "command_ids": ["interruption.answer"],
        },
        {
            "interruption_id": "question:q-approval",
            "kind": "tool_approval",
            "state": "pending",
            "command_ids": ["interruption.answer"],
        },
    ]
    assert "runtime.restart" not in {
        row["command_id"] for row in projection["available_commands"]
    }
    recovery = next(
        row for row in projection["blocking_conditions"]
        if row.get("interruption_id") == "runtime:system-recovery"
    )
    assert recovery["hard"] is False
    assert recovery["execution_state"] == "diagnostic_only"
    assert recovery["execution_reason"] == (
        "harness_interruption_executor_unavailable"
    )
    recovery_fact = next(
        row for row in projection["interruptions"]
        if row["interruption_id"] == "runtime:system-recovery"
    )
    assert recovery_fact["execution_state"] == "diagnostic_only"
    assert recovery_fact["command_ids"] == []


def test_failed_turn_with_unsealed_draft_is_continuable_not_reviewable():
    candidate = derive_candidate_state(
        checkpoint={
            "candidate_revision": 5,
            "receipt_fingerprint": None,
        },
        summary={"has_calculation": False, "database_current": True},
        application_state="none",
        review_authority_verified=True,
    )
    projection = build_harness_projection(
        runtime={"runtime_state": "ready", "runtime_health": "healthy"},
        turn={
            "state": "failed",
            "turn_id": "turn-16",
            "terminal_reason": "runtime_official_turn_failed",
        },
        candidate=candidate,
    )
    commands = {row["command_id"] for row in projection["available_commands"]}

    assert projection["candidate"]["seal_state"] == "unsealed"
    assert projection["candidate"]["reviewable"] is False
    assert projection["required_actions"] == []
    assert {
        "conversation.send", "turn.retry", "draft.inspect", "draft.continue",
        "candidate.revalidate", "draft.discard",
    } <= commands
    assert "candidate.review" not in commands
    assert all(row["invocation_kind"] for row in projection["available_commands"])


def test_command_admission_requires_current_projection_and_exact_scope():
    candidate = derive_candidate_state(
        checkpoint={"candidate_revision": 0, "receipt_fingerprint": None},
    )
    interruption = project_interruptions(
        issuer=ResumeCapabilityIssuer(b"c" * 32),
        questions=[{
            "question_id": "q-command",
            "turn_id": "turn-command",
            "state": "open",
            "items": [{"allow_skip": False}],
        }],
    )[0]
    projection = build_harness_projection(
        runtime={"runtime_state": "ready", "runtime_health": "healthy"},
        turn={"status": "suspended", "turn_id": "turn-command"},
        candidate=candidate,
        interruptions=[interruption],
    )
    command = next(
        row for row in projection["available_commands"]
        if row["command_id"] == "interruption.answer"
    )
    accepted = validate_harness_command(projection, {
        "contract": COMMAND_CONTRACT,
        "command_id": command["command_id"],
        "scope": command["scope"],
        "payload": {"answers": ["yes"]},
        "expected_projection_revision": projection["projection_revision"],
    })
    assert accepted["invocation_kind"] == "interruption_resume"
    assert accepted["payload"] == {"answers": ["yes"]}

    for mutated, reason in (
        (
            {"expected_projection_revision": projection["projection_revision"] + 1},
            "harness_projection_revision_conflict",
        ),
        (
            {"scope": {"interruption_id": "forged"}},
            "harness_command_scope_conflict",
        ),
        ({"command_id": "candidate.review"}, "harness_command_unavailable"),
        ({"forged": True}, "harness_command_request_invalid"),
    ):
        request = {
            "contract": COMMAND_CONTRACT,
            "command_id": command["command_id"],
            "scope": command["scope"],
            "payload": {},
            "expected_projection_revision": projection["projection_revision"],
            **mutated,
        }
        with pytest.raises(HarnessCommandError) as error:
            validate_harness_command(projection, request)
        assert error.value.reason == reason


def test_failure_layers_do_not_promote_an_ordinary_turn_error_to_runtime_death():
    runtime = {"runtime_state": "ready", "runtime_health": "healthy"}
    turn = classify_native_failure("runtime_official_turn_failed", runtime=runtime)
    provider = classify_native_failure(
        "runtime_official_turn_failed:provider_timeout",
        runtime=runtime,
    )
    transport = classify_native_failure(
        "runtime_official_turn_failed:pipe_read_timeout",
        runtime=runtime,
    )
    protocol = classify_native_failure(
        "native_turn_binding_invalid",
        runtime=runtime,
    )

    assert (turn["layer"], turn["requires_runtime_restart"]) == ("turn", False)
    assert (provider["layer"], provider["requires_runtime_restart"]) == (
        "provider",
        False,
    )
    assert (transport["layer"], transport["requires_runtime_restart"]) == (
        "transport",
        True,
    )
    assert (protocol["layer"], protocol["requires_runtime_restart"]) == (
        "runtime_protocol",
        True,
    )
    assert not failure_requires_runtime_teardown(
        "runtime_official_turn_failed",
        runtime=runtime,
    )


def test_only_verified_seal_is_reviewable():
    unverified = derive_candidate_state(
        checkpoint={"candidate_revision": 8, "receipt_fingerprint": HASH_B},
        application_state="none",
        review_authority_verified=False,
    )
    verified = derive_candidate_state(
        checkpoint={"candidate_revision": 8, "receipt_fingerprint": HASH_B},
        summary={"database_current": True},
        application_state="none",
        review_authority_verified=True,
    )
    assert unverified["seal_state"] == "sealed"
    assert unverified["reviewable"] is False
    assert verified["reviewable"] is True


def test_reference_deadlock_oracle_for_state_cross_product():
    """Independent small oracle: every non-running state has a recovery path."""

    issuer = ResumeCapabilityIssuer(b"r" * 32)
    for runtime_state, turn_state, sealed, draft, interruption in itertools.product(
        ("ready", "failed"),
        ("completed", "error", "cancelled"),
        (False, True),
        (False, True),
        (False, True),
    ):
        receipt = HASH_B if sealed else None
        revision = 1 if draft or sealed else 0
        # This reference fact is assembled independently of the production
        # candidate reducer so the oracle does not merely restate it.
        candidate = {
            "revision": revision,
            "draft_present": revision > 0,
            "seal_state": "sealed" if sealed else "unsealed",
            "assessment": "formal" if sealed else "unknown",
            "freshness": "fresh",
            "binding_state": "aligned",
            "application_state": "none",
            "receipt_ref": receipt,
            "review_checks": {
                "receipt": sealed,
                "schema": sealed,
                "database": True,
                "document_lineage": sealed,
            },
            "reviewable": sealed,
            "issues": [],
        }
        interruptions = (
            project_interruptions(
                issuer=issuer,
                questions=[{
                    "question_id": "q-ref",
                    "turn_id": "turn-ref",
                    "state": "open",
                    "items": [{"allow_skip": False}],
                }],
            )
            if interruption else []
        )
        projection = build_harness_projection(
            runtime={
                "runtime_state": runtime_state,
                "runtime_health": "healthy" if runtime_state == "ready" else "unhealthy",
            },
            turn={"status": turn_state, "turn_id": "turn-ref"},
            candidate=candidate,
            interruptions=interruptions,
        )
        commands = {row["command_id"] for row in projection["available_commands"]}
        # This assertion is deliberately independent of the exact production
        # derivation table: at minimum diagnostics/restart/answer/send/draft
        # recovery must make the snapshot actionable.
        assert commands & {
            "diagnostics.inspect", "runtime.restart", "interruption.answer",
            "conversation.send", "turn.retry", "draft.inspect",
            "candidate.review",
        }
        assert ("candidate.review" in commands) is projection["candidate"]["reviewable"]


def test_legacy_adapter_keeps_stage_as_hint_not_authority():
    projection = project_legacy_harness(
        runtime_status={"runtime_state": "ready", "runtime_health": "healthy"},
        snapshot={
            "workflow_stage": "calculation_closure",
            "candidate_snapshot": {
                "candidate_revision": 5,
                "receipt_fingerprint": None,
                "database_fingerprint": HASH_A,
                "document_id": "document-1",
            },
            "last_turn": {"status": "error", "turn_id": "turn-16"},
            "active_turn": None,
        },
        workbench={"turns": [], "questions": []},
        candidate_summary={"has_calculation": True, "database_current": True},
        application_state="none",
        document_binding={"document_id": "document-1"},
        app_actions=[],
        current_database_fingerprint=HASH_A,
        issuer=ResumeCapabilityIssuer(b"z" * 32),
    )

    assert projection["focus"]["recommended_stage"] == "calculation_closure"
    assert projection["candidate"]["seal_state"] == "unsealed"
    assert projection["candidate"]["assessment"] == "diagnostic"
    assert all(row["validation"] == "unchecked" for row in projection["artifacts"])
    assert all(row["source"] == "legacy_v2_shadow" for row in projection["artifacts"])
