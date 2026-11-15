from __future__ import annotations

import copy
import hashlib
import json
import uuid

import pytest

from genshin_app.agent_chat.bridge_protocol import make_frame
from genshin_app.agent_chat.native_app_tool_bridge import (
    NativeAppToolBridgeError,
    build_native_app_tool_settlement,
    make_native_app_tool_settle_request,
    validate_native_app_tool_settle_ack,
    validate_native_app_tool_settlement,
)
from genshin_app.agent_chat.native_app_tool_catalog import (
    build_native_app_tool_snapshot,
)
from genshin_app.equipment.schema import PlanError


class Session:
    workflow = "staged"

    def __init__(self, *, revision=4, stage_revision=7, phase="event_topology"):
        self.workflow_stage = phase
        self.revision = revision
        self.stage_state = {"stage_revision": stage_revision}

    def _is_board(self):
        return True


def snapshot(session=None, *, tool_manifest_hash="b" * 64):
    return build_native_app_tool_snapshot(
        session or Session(),
        document_id="document-1",
        database_fingerprint="a" * 64,
        tool_manifest_hash=tool_manifest_hash,
        runtime_generation="dsh-v2",
        writer_fence="fence-1",
    )


def result(value):
    raw = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def terminal(current, *, tool="gs_read_context", value=None, **changes):
    value = value or {"code": 0, "data": {"text": "中文"}}
    row = {
        "status": "settled_success",
        "terminal_state": "settled_success",
        "call_id": "call-1",
        "tool_name": tool,
        "effect": current["policies"][tool]["effect"],
        "result": value,
        "result_hash": result(value),
        "before_state_hash": "c" * 64,
        "after_state_hash": "c" * 64,
        "state_changes": [],
        "disallowed_state_changes": [],
        "next_snapshot": copy.deepcopy(current),
    }
    row.update(changes)
    return row


def test_success_payload_and_exact_ack_round_trip():
    current = snapshot()
    payload = build_native_app_tool_settlement(
        terminal(current),
        tool_name="gs_read_context",
        current_snapshot=current,
    )
    assert payload["result_json"] == '{"code":0,"data":{"text":"中文"}}'
    request = make_native_app_tool_settle_request(
        settlement=payload,
        current_snapshot=current,
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1-1",
        epoch_id="epoch-1",
        request_id="request-1",
        event_seq=2,
        call_id="call-1",
        canonical_args_hash="d" * 64,
    )
    ack_payload = {
        "contract": "genshin-native-app-tool-settlement-ack-v1",
        "disposition": "accepted",
        "settlement_status": "settled_success",
        "terminal_state": "settled_success",
        "result_hash": payload["result_hash"],
        "runtime_generation": "dsh-v2",
        "writer_fence": "fence-1",
        "catalog_hash": current["catalog_hash"],
        "next_catalog_hash": current["catalog_hash"],
        "rollover_required": False,
    }
    ack = make_frame(
        kind="response",
        frame_type="tool.settle.ack",
        message_id=str(uuid.uuid4()),
        runtime_incarnation=request["runtime_incarnation"],
        conversation_id=request["conversation_id"],
        turn_id=request["turn_id"],
        step_id=request["step_id"],
        epoch_id=request["epoch_id"],
        event_seq=3,
        payload=ack_payload,
        **{
            field: request[field]
            for field in (
                "request_id",
                "call_id",
                "document_id",
                "candidate_revision",
                "stage_revision",
                "database_fingerprint",
                "tool_manifest_hash",
                "canonical_args_hash",
            )
        },
    )
    assert validate_native_app_tool_settle_ack(ack, request=request) == ack


def test_candidate_write_projects_changed_revision_and_rollover():
    current = snapshot(Session(revision=4))
    following = snapshot(Session(revision=5))
    value = {"code": 0, "data": {"revision": 5}}
    payload = build_native_app_tool_settlement(
        terminal(
            current,
            tool="gs_patch_candidate",
            value=value,
            result_hash=result(value),
            after_state_hash="e" * 64,
            state_changes=["candidate_hash", "candidate_revision"],
            next_snapshot=following,
        ),
        tool_name="gs_patch_candidate",
        current_snapshot=current,
    )
    assert payload["next_snapshot"]["candidate_revision"] == 5
    assert payload["next_snapshot"]["catalog_hash"] != current["catalog_hash"]


def test_candidate_write_accepts_durable_checkpoint_change_component():
    """The Host's aggregate recovery hash is a first-class state component."""

    current = snapshot(Session(revision=4))
    following = snapshot(Session(revision=5))
    value = {"code": 0, "data": {"revision": 5}}
    payload = build_native_app_tool_settlement(
        terminal(
            current,
            tool="gs_patch_candidate",
            value=value,
            result_hash=result(value),
            after_state_hash="e" * 64,
            state_changes=[
                "candidate_hash",
                "candidate_revision",
                "durable_checkpoint_hash",
            ],
            next_snapshot=following,
        ),
        tool_name="gs_patch_candidate",
        current_snapshot=current,
    )
    assert payload["state_changes"][-1] == "durable_checkpoint_hash"


def test_stage_transition_and_candidate_rollback_carry_explicit_proof():
    current = snapshot(Session(revision=4, stage_revision=7, phase="event_topology"))
    numeric = snapshot(
        Session(revision=4, stage_revision=8, phase="numeric_model"),
        tool_manifest_hash="c" * 64,
    )
    value = {"code": 0, "data": {"phase": "numeric_model"}}
    forward = build_native_app_tool_settlement(
        terminal(
            current,
            tool="gs_transition_phase",
            value=value,
            result_hash=result(value),
            after_state_hash="e" * 64,
            state_changes=[
                "stage_revision", "tool_manifest_hash", "workflow_stage",
            ],
            next_snapshot=numeric,
        ),
        tool_name="gs_transition_phase",
        current_snapshot=current,
    )
    assert forward["stage_transition"]["kind"] == "explicit_forward"

    topology = snapshot(
        Session(revision=5, stage_revision=9, phase="event_topology"),
        tool_manifest_hash="d" * 64,
    )
    patched = {"code": 0, "data": {"revision": 5}}
    rollback = build_native_app_tool_settlement(
        terminal(
            numeric,
            tool="gs_patch_candidate",
            value=patched,
            result_hash=result(patched),
            after_state_hash="f" * 64,
            state_changes=[
                "candidate_revision", "stage_revision", "tool_manifest_hash",
                "workflow_stage",
            ],
            next_snapshot=topology,
        ),
        tool_name="gs_patch_candidate",
        current_snapshot=numeric,
    )
    assert rollback["stage_transition"]["kind"] == "automatic_invalidation"


def test_energy_policy_revision_is_an_explicit_backward_transition():
    """Changing closure energy policy invalidates closure; it is not same-stage."""

    closure = snapshot(
        Session(revision=4, stage_revision=7, phase="calculation_closure"),
        tool_manifest_hash="b" * 64,
    )
    numeric = snapshot(
        Session(revision=4, stage_revision=8, phase="numeric_model"),
        tool_manifest_hash="c" * 64,
    )
    value = {"code": 0, "data": {"phase": "numeric_model"}}

    payload = build_native_app_tool_settlement(
        terminal(
            closure,
            tool="gs_transition_phase",
            value=value,
            result_hash=result(value),
            after_state_hash="e" * 64,
            state_changes=[
                "derived_hash", "stage_revision", "stage_state_hash",
                "tool_manifest_hash", "workflow_stage",
            ],
            next_snapshot=numeric,
        ),
        tool_name="gs_transition_phase",
        current_snapshot=closure,
    )

    assert payload["stage_transition"]["kind"] == "explicit_backward"
    assert payload["stage_transition"]["from_stage"] == "calculation_closure"
    assert payload["stage_transition"]["to_stage"] == "numeric_model"


def test_same_stage_revision_cannot_be_blessed_as_successful_stage_transition():
    current = snapshot(
        Session(revision=4, stage_revision=7, phase="calculation_closure"),
        tool_manifest_hash="b" * 64,
    )
    invalid = snapshot(
        Session(revision=4, stage_revision=8, phase="calculation_closure"),
        tool_manifest_hash="b" * 64,
    )
    value = {"code": 0, "data": {"phase": "calculation_closure"}}

    with pytest.raises(NativeAppToolBridgeError) as caught:
        build_native_app_tool_settlement(
            terminal(
                current,
                tool="gs_transition_phase",
                value=value,
                result_hash=result(value),
                after_state_hash="e" * 64,
                state_changes=["stage_revision", "stage_state_hash"],
                next_snapshot=invalid,
            ),
            tool_name="gs_transition_phase",
            current_snapshot=current,
        )

    assert caught.value.code == "native_app_settlement_stage_transition_missing"


def test_candidate_cannot_use_stage_fields_without_valid_rollback_proof():
    current = snapshot(Session(revision=4, stage_revision=7, phase="event_topology"))
    invalid = snapshot(
        Session(revision=5, stage_revision=8, phase="numeric_model"),
        tool_manifest_hash="c" * 64,
    )
    value = {"code": 0, "data": {"revision": 5}}
    with pytest.raises(NativeAppToolBridgeError) as caught:
        build_native_app_tool_settlement(
            terminal(
                current,
                tool="gs_patch_candidate",
                value=value,
                result_hash=result(value),
                after_state_hash="e" * 64,
                state_changes=[
                    "candidate_revision", "stage_revision",
                    "tool_manifest_hash", "workflow_stage",
                ],
                next_snapshot=invalid,
            ),
            tool_name="gs_patch_candidate",
            current_snapshot=current,
        )
    assert caught.value.code == "native_app_settlement_effect_mask"


def test_replay_can_carry_advanced_snapshot_but_has_no_fake_changes():
    current = snapshot(Session(revision=4))
    following = snapshot(Session(revision=5))
    payload = build_native_app_tool_settlement(
        terminal(
            current,
            status="replay",
            next_snapshot=following,
        ),
        tool_name="gs_read_context",
        current_snapshot=current,
    )
    assert payload["status"] == "replay"
    assert payload["state_changes"] == []
    assert payload["next_snapshot"]["candidate_revision"] == 5


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.update(result_hash="f" * 64),
        lambda row: row.update(state_changes=["candidate_revision"]),
        lambda row: row["next_snapshot"].update(writer_fence="fence-2"),
    ],
)
def test_tampered_terminal_or_next_snapshot_fails_closed(mutation):
    current = snapshot()
    row = terminal(current)
    mutation(row)
    with pytest.raises((NativeAppToolBridgeError, PlanError)):
        build_native_app_tool_settlement(
            row,
            tool_name="gs_read_context",
            current_snapshot=current,
        )


def test_unknown_has_no_result_and_requires_reason():
    current = snapshot()
    row = terminal(
        current,
        status="unknown_after_interruption",
        terminal_state="unknown_after_interruption",
        result_hash=None,
        terminal_reason="process_interrupted",
    )
    row.pop("result")
    payload = build_native_app_tool_settlement(
        row,
        tool_name="gs_read_context",
        current_snapshot=current,
    )
    assert payload["result_json"] is None
    assert payload["result_hash"] is None

    changed = copy.deepcopy(payload)
    changed["terminal_reason"] = None
    with pytest.raises(NativeAppToolBridgeError):
        validate_native_app_tool_settlement(changed, current_snapshot=current)


def test_calculation_diagnostic_settled_failure_crosses_bridge_with_state_change():
    current = snapshot(
        Session(revision=4, stage_revision=7, phase="calculation_closure"),
    )
    value = {
        "code": 1,
        "reason": "mechanics_pending",
        "message": "动作投影仍需补齐",
    }
    payload = build_native_app_tool_settlement(
        terminal(
            current,
            tool="gs_calculate",
            value=value,
            status="settled_failure",
            terminal_state="settled_failure",
            terminal_reason="mechanics_pending",
            result_hash=result(value),
            after_state_hash="e" * 64,
            state_changes=["derived_hash", "durable_checkpoint_hash"],
        ),
        tool_name="gs_calculate",
        current_snapshot=current,
    )

    assert payload["status"] == "settled_failure"
    assert payload["terminal_state"] == "settled_failure"
    assert payload["terminal_reason"] == "mechanics_pending"
    assert json.loads(payload["result_json"])["reason"] == "mechanics_pending"
    assert payload["state_changes"] == [
        "derived_hash", "durable_checkpoint_hash",
    ]


def test_ack_rejects_binding_and_rollover_claim_tampering():
    current = snapshot()
    payload = build_native_app_tool_settlement(
        terminal(current),
        tool_name="gs_read_context",
        current_snapshot=current,
    )
    request = make_native_app_tool_settle_request(
        settlement=payload,
        current_snapshot=current,
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1-1",
        epoch_id="epoch-1",
        request_id="request-1",
        event_seq=2,
        call_id="call-1",
        canonical_args_hash="d" * 64,
    )
    ack = make_frame(
        kind="response",
        frame_type="tool.settle.ack",
        message_id=str(uuid.uuid4()),
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1-1",
        epoch_id="epoch-1",
        event_seq=3,
        payload={
            "contract": "genshin-native-app-tool-settlement-ack-v1",
            "disposition": "accepted",
            "settlement_status": "settled_success",
            "terminal_state": "settled_success",
            "result_hash": payload["result_hash"],
            "runtime_generation": "dsh-v2",
            "writer_fence": "fence-1",
            "catalog_hash": current["catalog_hash"],
            "next_catalog_hash": current["catalog_hash"],
            "rollover_required": True,
        },
        **{
            field: request[field]
            for field in _optional_fields()
        },
    )
    with pytest.raises(NativeAppToolBridgeError) as caught:
        validate_native_app_tool_settle_ack(ack, request=request)
    assert caught.value.code == "native_app_settlement_ack_mismatch"


def _optional_fields():
    return (
        "request_id",
        "call_id",
        "document_id",
        "candidate_revision",
        "stage_revision",
        "database_fingerprint",
        "tool_manifest_hash",
        "canonical_args_hash",
    )
