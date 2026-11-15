from __future__ import annotations

import copy
import threading
import uuid

import pytest

from genshin_app.agent_chat.bridge_protocol import make_frame
from genshin_app.agent_chat.native_write_bridge import (
    PATCH_TOOL,
    SETTLEMENT_ACK_CONTRACT,
    build_native_write_settlement,
    build_native_write_snapshot,
    make_native_write_settle_request,
    validate_native_write_arguments,
    validate_native_write_settle_ack,
    validate_native_write_settlement,
    validate_native_write_snapshot,
)
from genshin_app.equipment.schema import PlanError


class _Session:
    workflow = "staged"
    workflow_stage = "event_topology"
    revision = 7

    def __init__(self):
        self._lock = threading.RLock()
        self.stage_state = {"stage_revision": 3}


def _arguments():
    return {
        "expected_revision": 7,
        "reason": "phase4 candidate write fixture",
        "operations": [{
            "op": "replace",
            "path": "/title",
            "value": "phase4 native candidate",
        }],
    }


def _request(settlement):
    return make_native_write_settle_request(
        settlement=settlement,
        runtime_incarnation="runtime-phase4",
        conversation_id="conversation-phase4",
        turn_id="turn-phase4",
        step_id="step-phase4",
        epoch_id="epoch-phase4",
        request_id="request-phase4",
        event_seq=4,
        call_id="call-phase4",
        document_id="document-phase4",
        candidate_revision=7,
        stage_revision=3,
        database_fingerprint="a" * 64,
        tool_manifest_hash="b" * 64,
        canonical_args_hash="c" * 64,
    )


def _ack(request):
    settlement = request["payload"]
    return make_frame(
        kind="response",
        frame_type="tool.settle.ack",
        message_id=str(uuid.uuid4()),
        runtime_incarnation=request["runtime_incarnation"],
        conversation_id=request["conversation_id"],
        turn_id=request["turn_id"],
        step_id=request["step_id"],
        epoch_id=request["epoch_id"],
        event_seq=8,
        request_id=request["request_id"],
        call_id=request["call_id"],
        document_id=request["document_id"],
        candidate_revision=request["candidate_revision"],
        stage_revision=request["stage_revision"],
        database_fingerprint=request["database_fingerprint"],
        tool_manifest_hash=request["tool_manifest_hash"],
        canonical_args_hash=request["canonical_args_hash"],
        payload={
            "contract": SETTLEMENT_ACK_CONTRACT,
            "disposition": "accepted",
            "settlement_status": settlement["status"],
            "result_hash": settlement["result_hash"],
            "runtime_generation": settlement["runtime_generation"],
            "writer_fence": settlement["writer_fence"],
        },
    )


def test_write_snapshot_projects_only_patch_and_binds_canonical_body():
    session = _Session()

    snapshot = build_native_write_snapshot(session)

    assert snapshot["tools"][0]["name"] == PATCH_TOOL
    assert snapshot["write_policies"] == {PATCH_TOOL: "candidate_patch"}
    assert snapshot["candidate_revision"] == 7
    assert validate_native_write_snapshot(snapshot) == snapshot
    assert validate_native_write_arguments(
        session, snapshot, PATCH_TOOL, _arguments(),
    ) == _arguments()

    tampered = copy.deepcopy(snapshot)
    tampered["candidate_revision"] = 8
    with pytest.raises(PlanError) as error:
        validate_native_write_snapshot(tampered)
    assert error.value.reason == "native_write_tool_hash_mismatch"


@pytest.mark.parametrize(
    ("response", "unknown", "status", "result_code"),
    [
        ({"code": 0, "data": {"revision": 8}}, False, "settled_success", 0),
        (
            {"code": 1, "reason": "agent_tool_input", "message": "invalid"},
            False,
            "settled_failure",
            "agent_tool_input",
        ),
        (None, True, "unknown_after_interruption", "outcome_unknown"),
    ],
)
def test_write_settlement_and_ack_bind_every_authority_field(
    response, unknown, status, result_code,
):
    settlement = build_native_write_settlement(
        response,
        runtime_generation="dsh-v2",
        writer_fence="writer-fence-1",
        outcome_unknown=unknown,
    )
    assert settlement["status"] == status
    assert settlement["result_code"] == result_code
    assert settlement["resource_ids"] == []
    assert validate_native_write_settlement(settlement) == settlement

    request = _request(settlement)
    ack = _ack(request)
    assert validate_native_write_settle_ack(ack, request=request) == ack

    changed = copy.deepcopy(ack)
    changed["payload"]["result_hash"] = "d" * 64
    changed = make_frame(
        kind="response",
        frame_type="tool.settle.ack",
        message_id=changed["message_id"],
        runtime_incarnation=changed["runtime_incarnation"],
        conversation_id=changed["conversation_id"],
        turn_id=changed["turn_id"],
        step_id=changed["step_id"],
        epoch_id=changed["epoch_id"],
        event_seq=changed["event_seq"],
        payload=changed["payload"],
        request_id=changed["request_id"],
        call_id=changed["call_id"],
        document_id=changed["document_id"],
        candidate_revision=changed["candidate_revision"],
        stage_revision=changed["stage_revision"],
        database_fingerprint=changed["database_fingerprint"],
        tool_manifest_hash=changed["tool_manifest_hash"],
        canonical_args_hash=changed["canonical_args_hash"],
    )
    with pytest.raises(PlanError) as error:
        validate_native_write_settle_ack(changed, request=request)
    assert error.value.reason == "native_write_settlement_ack_invalid"


def test_unknown_cannot_carry_resources_or_masquerade_as_failure():
    unknown = build_native_write_settlement(
        None,
        runtime_generation="dsh-v2",
        writer_fence="writer-fence-1",
        outcome_unknown=True,
    )
    bad = copy.deepcopy(unknown)
    bad["status"] = "settled_failure"

    with pytest.raises(PlanError) as error:
        validate_native_write_settlement(bad)

    assert error.value.reason == "native_write_settlement_invalid"
