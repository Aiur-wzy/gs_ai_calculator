"""V2 App-owned tool settlement tests; no model or network is used."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.agent_chat.session_lifecycle_v2 import (
    activate_context,
    assert_current_context,
    begin_call,
    canonical_args_hash,
    create_ledger,
    lookup_call,
    make_call_identity,
    mark_unknown_after_interruption,
    settle_call,
    snapshot,
)
from genshin_app.equipment.schema import PlanError

MANIFEST = "1" * 64


def _ledger():
    ledger = create_ledger("conversation-1")
    activate_context(
        ledger,
        turn_id="turn-1",
        step_id="step-1",
        epoch_id="epoch-1",
        runtime_generation="dsh_v2",
        db_revision="db-2026-10-22",
        manifest_hash=MANIFEST,
        fence_token="fence-1",
    )
    return ledger


def _identity(call_id="call-1", args=None, **changes):
    values = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "step_id": "step-1",
        "epoch_id": "epoch-1",
        "call_id": call_id,
        "tool": "gs_patch_candidate",
        "args_hash": canonical_args_hash(
            json.dumps({"value": 1} if args is None else args,
                       ensure_ascii=False, allow_nan=False,
                       sort_keys=True, separators=(",", ":"))),
        "runtime_generation": "dsh_v2",
        "db_revision": "db-2026-10-22",
        "manifest_hash": MANIFEST,
        "fence_token": "fence-1",
    }
    values.update(changes)
    return make_call_identity(**values)


def test_argument_identity_hashes_exact_completed_utf8_text():
    compact = '{"value":1}'
    spaced = '{"value":1} '
    assert canonical_args_hash(compact) != canonical_args_hash(spaced)
    assert canonical_args_hash(compact) == canonical_args_hash(compact)
    for invalid in ({"value": 1}, "", "\ud800"):
        with pytest.raises(PlanError) as error:
            canonical_args_hash(invalid)  # type: ignore[arg-type]
        assert error.value.reason == "agent_checkpoint_invalid"


@pytest.mark.parametrize("result", [
    {1: "non-string-key"},
    {"value": 2**53},
])
def test_settlement_result_digest_rejects_nonportable_json(result):
    ledger = _ledger()
    identity = _identity()
    begin_call(ledger, identity=identity)
    frozen = copy.deepcopy(ledger)

    with pytest.raises(PlanError) as error:
        settle_call(
            ledger,
            identity=identity,
            terminal_state="settled_success",
            result=result,
        )

    assert error.value.reason == "agent_checkpoint_invalid"
    assert ledger == frozen


def test_identity_carries_every_context_dimension_and_only_argument_hash():
    identity = _identity()

    assert set(identity) == {
        "conversation_id", "turn_id", "step_id", "epoch_id", "call_id",
        "tool", "args_hash", "runtime_generation", "db_revision",
        "manifest_hash", "fence_token",
    }
    assert identity["runtime_generation"] == "dsh_v2"
    assert identity["db_revision"] == "db-2026-10-22"
    assert len(identity["args_hash"]) == 64


@pytest.mark.parametrize(("field", "value"), [
    ("conversation_id", "会话-1"),
    ("turn_id", "turn 1"),
    ("step_id", "step\n1"),
    ("epoch_id", "-epoch-1"),
    ("call_id", "call/1"),
    ("tool", "gs patch"),
    ("runtime_generation", "dsh/v2"),
    ("db_revision", "数据库版本"),
    ("fence_token", "sk-secret-must-not-be-an-identity"),
    ("call_id", "x" * 129),
])
def test_every_identity_like_field_uses_bridge_ascii_grammar(field, value):
    with pytest.raises(PlanError) as error:
        _identity(**{field: value})

    assert error.value.reason == "agent_checkpoint_invalid"


def test_conversation_identity_uses_bridge_ascii_grammar():
    for value in ("会话", "contains space", "line\nbreak", "-leading", "x" * 129,
                  "authorization:secret"):
        with pytest.raises(PlanError) as error:
            create_ledger(value)
        assert error.value.reason == "agent_checkpoint_invalid"


def test_terminal_settlement_is_exactly_once_and_identical_call_replays():
    ledger = _ledger()
    identity = _identity()

    assert begin_call(ledger, identity=identity)["disposition"] == "execute"
    first = settle_call(
        ledger,
        identity=identity,
        terminal_state="settled_success",
        result={"code": 0, "data": {"revision": 2}},
        result_code=0,
        resource_ids=["candidate:2"],
    )
    frozen = copy.deepcopy(ledger)

    replay_begin = begin_call(ledger, identity=identity)
    replay_settle = settle_call(
        ledger,
        identity=identity,
        terminal_state="settled_success",
        result={"data": {"revision": 2}, "code": 0},
        result_code=0,
        resource_ids=["candidate:2"],
    )

    assert first["disposition"] == "settled"
    assert replay_begin == {
        "disposition": "replay", "settlement": first["settlement"]}
    assert replay_settle == replay_begin
    assert ledger == frozen


@pytest.mark.parametrize("changed", [
    {"args_hash": "2" * 64},
    {"tool": "gs_read_context"},
    {"runtime_generation": "python_v1"},
    {"db_revision": "db-other"},
    {"manifest_hash": "3" * 64},
])
def test_same_call_id_with_mismatched_identity_conflicts(changed):
    ledger = _ledger()
    identity = _identity()
    begin_call(ledger, identity=identity)
    conflicting = _identity(**changed)

    with pytest.raises(PlanError) as error:
        begin_call(ledger, identity=conflicting)

    assert error.value.reason == "call_id_conflict"
    assert lookup_call(ledger, identity=identity)["state"] == "inflight"


def test_second_different_terminal_settlement_conflicts_without_mutation():
    ledger = _ledger()
    identity = _identity()
    begin_call(ledger, identity=identity)
    settle_call(
        ledger,
        identity=identity,
        terminal_state="settled_success",
        result={"ok": True},
        result_code=0,
    )
    frozen = copy.deepcopy(ledger)

    with pytest.raises(PlanError) as error:
        settle_call(
            ledger,
            identity=identity,
            terminal_state="settled_failure",
            result={"ok": False},
            result_code="tool_failure",
        )

    assert error.value.reason == "settlement_conflict"
    assert ledger == frozen


@pytest.mark.parametrize("result_code", [-(2**53 - 1), 2**53 - 1])
def test_result_code_accepts_json_safe_integer_boundaries(result_code):
    ledger = _ledger()
    identity = _identity(call_id=f"call-{abs(result_code)}")
    begin_call(ledger, identity=identity)

    row = settle_call(
        ledger,
        identity=identity,
        terminal_state="settled_failure",
        result={"ok": False},
        result_code=result_code,
    )

    assert row["settlement"]["result_code"] == result_code


@pytest.mark.parametrize("result_code", [-(2**53), 2**53, True])
def test_result_code_rejects_values_outside_json_safe_integer_contract(result_code):
    ledger = _ledger()
    identity = _identity()
    begin_call(ledger, identity=identity)
    frozen = copy.deepcopy(ledger)

    with pytest.raises(PlanError) as error:
        settle_call(
            ledger,
            identity=identity,
            terminal_state="settled_failure",
            result={"ok": False},
            result_code=result_code,
        )

    assert error.value.reason == "agent_checkpoint_invalid"
    assert ledger == frozen


@pytest.mark.parametrize(("field", "value"), [
    ("result_code", "错误码"),
    ("result_code", "bad code"),
    ("result_code", "sk-secret-result-code"),
    ("terminal_reason", "line\nbreak"),
    ("terminal_reason", "authorization:secret"),
])
def test_terminal_machine_codes_use_non_secret_bridge_identity_grammar(field, value):
    ledger = _ledger()
    identity = _identity()
    begin_call(ledger, identity=identity)
    frozen = copy.deepcopy(ledger)
    options = {field: value}

    with pytest.raises(PlanError) as error:
        settle_call(
            ledger,
            identity=identity,
            terminal_state="settled_failure",
            result={"ok": False},
            **options,
        )

    assert error.value.reason == "agent_checkpoint_invalid"
    assert ledger == frozen


@pytest.mark.parametrize("resource_id", [
    "资源:1",
    "resource 1",
    "resource/1",
    "resource\n1",
    ":resource",
    "x" * 129,
    "sk-secret-must-not-be-a-resource",
    "bearer:must-not-be-a-resource",
])
def test_resource_ids_use_bridge_ascii_grammar_and_reject_secret_markers(resource_id):
    ledger = _ledger()
    identity = _identity()
    begin_call(ledger, identity=identity)
    frozen = copy.deepcopy(ledger)

    with pytest.raises(PlanError) as error:
        settle_call(
            ledger,
            identity=identity,
            terminal_state="settled_success",
            result={"ok": True},
            resource_ids=[resource_id],
        )

    assert error.value.reason == "agent_checkpoint_invalid"
    assert ledger == frozen


def test_unknown_settlement_rejection_does_not_disturb_other_inflight_calls():
    ledger = _ledger()
    first = _identity("call-1")
    second = _identity("call-2", args={"value": 2})
    begin_call(ledger, identity=first)
    begin_call(ledger, identity=second)
    frozen = copy.deepcopy(ledger["inflight"])

    with pytest.raises(PlanError) as error:
        settle_call(
            ledger,
            identity=_identity("unknown-call", args={"value": 99}),
            terminal_state="settled_success",
            result={"ok": True},
        )

    assert error.value.reason == "unknown_settlement"
    assert ledger["inflight"] == frozen
    assert ledger["settlements"] == {}


def test_epoch_advance_terminalizes_old_inflight_and_rejects_late_frames():
    ledger = _ledger()
    old = _identity()
    begin_call(ledger, identity=old)

    changed = activate_context(
        ledger,
        turn_id="turn-1",
        step_id="step-2",
        epoch_id="epoch-2",
        runtime_generation="dsh_v2",
        db_revision="db-2026-10-22",
        manifest_hash=MANIFEST,
        fence_token="fence-2",
    )

    assert changed["invalidated"][0]["terminal_state"] == "unknown_after_interruption"
    assert lookup_call(ledger, identity=old)["settlement"]["terminal_state"] == (
        "unknown_after_interruption")
    with pytest.raises(PlanError) as error:
        assert_current_context(ledger, old)
    assert error.value.reason == "stale_context"

    # A late success cannot replace the conservative unknown terminal row.
    frozen = copy.deepcopy(ledger)
    with pytest.raises(PlanError) as error:
        settle_call(
            ledger,
            identity=old,
            terminal_state="settled_success",
            result={"late": True},
        )
    assert error.value.reason == "settlement_conflict"
    assert ledger == frozen


def test_unregistered_late_settlement_is_stale_and_keeps_current_inflight():
    ledger = _ledger()
    activate_context(
        ledger,
        turn_id="turn-1",
        step_id="step-2",
        epoch_id="epoch-2",
        runtime_generation="dsh_v2",
        db_revision="db-2026-10-22",
        manifest_hash=MANIFEST,
        fence_token="fence-2",
    )
    current = _identity(
        call_id="call-current",
        step_id="step-2",
        epoch_id="epoch-2",
        fence_token="fence-2",
    )
    begin_call(ledger, identity=current)
    frozen = copy.deepcopy(ledger["inflight"])

    with pytest.raises(PlanError) as error:
        settle_call(
            ledger,
            identity=_identity(call_id="late-never-admitted"),
            terminal_state="settled_success",
            result={"late": True},
        )

    assert error.value.reason == "stale_context"
    assert ledger["inflight"] == frozen


def test_fence_change_invalidates_same_epoch_and_new_call_can_settle():
    ledger = _ledger()
    old = _identity()
    begin_call(ledger, identity=old)
    activate_context(
        ledger,
        turn_id="turn-1",
        step_id="step-1",
        epoch_id="epoch-1",
        runtime_generation="dsh_v2",
        db_revision="db-2026-10-22",
        manifest_hash=MANIFEST,
        fence_token="fence-2",
    )
    new = _identity(call_id="call-2", fence_token="fence-2")

    assert begin_call(ledger, identity=new)["disposition"] == "execute"
    settled = settle_call(
        ledger,
        identity=new,
        terminal_state="settled_failure",
        result={"code": "validation_failed"},
        result_code="validation_failed",
    )

    assert settled["settlement"]["terminal_state"] == "settled_failure"
    assert lookup_call(ledger, identity=old)["settlement"]["terminal_state"] == (
        "unknown_after_interruption")


def test_explicit_interruption_is_terminal_and_never_automatically_reexecutes():
    ledger = _ledger()
    identity = _identity()
    begin_call(ledger, identity=identity)

    first = mark_unknown_after_interruption(ledger, identity=identity)
    replay = begin_call(ledger, identity=identity)

    assert first["settlement"]["terminal_state"] == "unknown_after_interruption"
    assert replay["disposition"] == "replay"
    assert replay["settlement"] == first["settlement"]


def test_snapshot_never_persists_raw_arguments_results_reasoning_or_secret():
    ledger = _ledger()
    secret = "sk-test-must-not-survive"
    reasoning = "private chain of thought must not survive"
    raw_arguments = {"api_key": secret, "prompt": reasoning}
    identity = _identity(args=raw_arguments)
    begin_call(ledger, identity=identity)
    settle_call(
        ledger,
        identity=identity,
        terminal_state="settled_success",
        result={"authorization": secret, "reasoning": reasoning, "ok": True},
        result_code=0,
    )

    persisted = json.dumps(snapshot(ledger), ensure_ascii=False, sort_keys=True)

    assert secret not in persisted
    assert reasoning not in persisted
    assert "api_key" not in persisted
    assert "authorization" not in persisted
    assert '"reasoning"' not in persisted
    assert '"args"' not in persisted
    assert '"result"' not in persisted


def test_tool_result_hash_input_is_bounded_before_settlement():
    ledger = _ledger()
    identity = _identity()
    begin_call(ledger, identity=identity)

    with pytest.raises(PlanError) as error:
        settle_call(
            ledger,
            identity=identity,
            terminal_state="settled_success",
            result={"payload": "x" * (64 * 1024)},
        )

    assert error.value.reason == "agent_checkpoint_invalid"
    assert lookup_call(ledger, identity=identity)["state"] == "inflight"
