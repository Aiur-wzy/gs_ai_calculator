"""Phase 3 bounded App read-tool tests; no provider, pipe, or GUI is used."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import uuid

import pytest

from genshin_app.agent_chat import tools
from genshin_app.agent_chat.bridge_protocol import make_frame, raw_json_hash
from genshin_app.agent_chat.native_read_tool import (
    MAX_TOOL_RESULT_BYTES,
    SETTLEMENT_ACK_CONTRACT,
    NativeReadToolHost,
    build_read_tool_snapshot,
    validate_read_tool_snapshot,
    validate_settlement_payload,
    validate_tool_settle_ack,
    validate_tool_settle_request,
    visible_tool_names,
)
from genshin_app.equipment.schema import PlanError


class _Session:
    workflow = "staged"

    def __init__(self, phase="calculation_closure", revision=7, stage_revision=12):
        self.workflow_stage = phase
        self.revision = revision
        self.stage_state = {"stage_revision": stage_revision}
        self._lock = threading.RLock()

    def _is_board(self):
        return True


def _binding(**changes):
    value = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "step_id": "step-1",
        "epoch_id": "epoch-1",
        "runtime_incarnation": "runtime-1",
        "runtime_generation": "dsh-v2",
        "database_fingerprint": "a" * 64,
        "fence_token": "fence-1",
    }
    value.update(changes)
    return value


def _call(host, *, call_id="call-1", raw='{"resource":"workflow_stage"}', **changes):
    values = {
        "call_id": call_id,
        "tool": "gs_read_context",
        "arguments_json": raw,
        "canonical_args_hash": raw_json_hash(raw),
        "candidate_revision": host.snapshot["candidate_revision"],
        "stage_revision": host.snapshot["stage_revision"],
    }
    values.update(changes)
    return host.execute(**values)


def test_snapshot_uses_real_stage_projection_and_is_strictly_hash_bound():
    session = _Session()
    snapshot = build_read_tool_snapshot(session)

    assert snapshot["contract"] == "genshin-native-read-tool-snapshot-v1"
    assert [row["name"] for row in snapshot["tools"]] == ["gs_read_context"]
    assert snapshot["tools"][0] == next(
        row
        for row in tools.definitions_for_session(session)
        if row["name"] == "gs_read_context"
    )
    assert validate_read_tool_snapshot(snapshot) == snapshot

    changed = copy.deepcopy(snapshot)
    changed["tools"][0]["description"] += "tampered"
    with pytest.raises(PlanError) as error:
        validate_read_tool_snapshot(changed)
    assert error.value.reason == "native_read_tool_hash_mismatch"


def test_stage_rollback_removes_future_tool_and_invalidates_old_snapshot():
    session = _Session(phase="calculation_closure", stage_revision=21)
    old = build_read_tool_snapshot(session)
    assert "gs_calculate" in visible_tool_names(session)

    session.workflow_stage = "action_checkpoint"
    session.stage_state["stage_revision"] += 1
    current = build_read_tool_snapshot(session)

    assert "gs_calculate" not in visible_tool_names(session)
    assert [row["name"] for row in current["tools"]] == ["gs_read_context"]
    assert current["tool_manifest_hash"] != old["tool_manifest_hash"]


def test_host_delegates_to_real_server_gate_and_exact_replay_does_not_reexecute(
    monkeypatch,
):
    calls = []

    def gated(session, name, args):
        calls.append((session, name, copy.deepcopy(args)))
        return {"code": 0, "data": {"phase": session.workflow_stage}}

    monkeypatch.setattr(tools, "call_tool", gated)
    session = _Session()
    host = NativeReadToolHost(session, binding=_binding())

    first = _call(host)
    replay = _call(host)

    assert first == replay
    assert first["status"] == "settled_success"
    assert json.loads(first["result_json"])["data"]["phase"] == "calculation_closure"
    assert calls == [(session, "gs_read_context", {"resource": "workflow_stage"})]


def test_host_rejects_argument_conflict_and_stale_stage_without_second_execution(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        tools, "call_tool", lambda *_args: calls.append(True) or {"code": 0}
    )
    session = _Session()
    host = NativeReadToolHost(session, binding=_binding())
    _call(host)

    raw = '{"resource":"candidate"}'
    with pytest.raises(PlanError) as conflict:
        _call(host, raw=raw)
    assert conflict.value.reason == "call_id_conflict"

    session.stage_state["stage_revision"] += 1
    with pytest.raises(PlanError) as stale:
        _call(host, call_id="call-2")
    assert stale.value.reason == "stale_tool_snapshot"
    assert len(calls) == 1


def test_host_turns_oversized_read_result_into_bounded_failure(monkeypatch):
    monkeypatch.setattr(
        tools,
        "call_tool",
        lambda *_args: {"code": 0, "data": {"text": "x" * MAX_TOOL_RESULT_BYTES}},
    )
    host = NativeReadToolHost(_Session(), binding=_binding())
    result = _call(host)

    assert result["status"] == "settled_failure"
    assert result["result_code"] == "native_read_result_limit"
    assert len(result["result_json"].encode("utf-8")) < 1024


def test_settle_request_and_ack_are_strictly_bound(monkeypatch):
    monkeypatch.setattr(
        tools, "call_tool", lambda *_args: {"code": 0, "data": {"ok": True}}
    )
    host = NativeReadToolHost(_Session(), binding=_binding())
    raw = '{"resource":"workflow_stage"}'
    args_hash = raw_json_hash(raw)
    settlement = _call(host, raw=raw)
    request = host.make_settle_request(
        settlement=settlement,
        call_id="call-1",
        canonical_args_hash=args_hash,
        request_id="request-1",
        event_seq=1,
        message_id="11111111-1111-4111-8111-111111111111",
    )
    assert (
        validate_tool_settle_request(
            request,
            binding=_binding(),
            snapshot=host.snapshot,
            call_id="call-1",
            canonical_args_hash=args_hash,
        )
        == request
    )

    ack_payload = {
        "contract": SETTLEMENT_ACK_CONTRACT,
        "disposition": "accepted",
        "settlement_status": settlement["status"],
        "result_hash": settlement["result_hash"],
        "runtime_generation": _binding()["runtime_generation"],
        "fence_token": _binding()["fence_token"],
    }
    ack = make_frame(
        kind="response",
        frame_type="tool.settle.ack",
        message_id="22222222-2222-4222-8222-222222222222",
        runtime_incarnation=request["runtime_incarnation"],
        conversation_id=request["conversation_id"],
        turn_id=request["turn_id"],
        step_id=request["step_id"],
        epoch_id=request["epoch_id"],
        event_seq=1,
        payload=ack_payload,
        **{
            name: request[name]
            for name in (
                "request_id",
                "call_id",
                "candidate_revision",
                "stage_revision",
                "database_fingerprint",
                "tool_manifest_hash",
                "canonical_args_hash",
            )
        },
    )
    assert validate_tool_settle_ack(ack, request=request) == ack

    stale = copy.deepcopy(request)
    stale["stage_revision"] += 1
    # Rebuild only the generic payload hash is insufficient to evade the
    # explicit stage binding (the payload did not change).
    with pytest.raises(PlanError) as error:
        validate_tool_settle_request(
            stale,
            binding=_binding(),
            snapshot=host.snapshot,
            call_id="call-1",
            canonical_args_hash=args_hash,
        )
    assert error.value.reason == "stale_tool_settlement"

    old_fence = copy.deepcopy(request)
    old_fence["payload"]["fence_token"] = "fence-old"
    from genshin_app.agent_chat.bridge_protocol import canonical_payload_hash

    old_fence["payload_hash"] = canonical_payload_hash(old_fence["payload"])
    with pytest.raises(PlanError) as error:
        validate_tool_settle_request(
            old_fence,
            binding=_binding(),
            snapshot=host.snapshot,
            call_id="call-1",
            canonical_args_hash=args_hash,
        )
    assert error.value.reason == "stale_tool_settlement"


def test_settlement_payload_hash_and_size_tampering_fail_closed(monkeypatch):
    monkeypatch.setattr(
        tools, "call_tool", lambda *_args: {"code": 0, "data": {"ok": True}}
    )
    host = NativeReadToolHost(_Session(), binding=_binding())
    raw = '{"resource":"workflow_stage"}'
    args_hash = raw_json_hash(raw)
    settlement = _call(host, raw=raw)
    request = host.make_settle_request(
        settlement=settlement,
        call_id="call-1",
        canonical_args_hash=args_hash,
        request_id="request-1",
        event_seq=1,
        message_id=str(uuid.uuid4()),
    )
    changed = copy.deepcopy(request)
    changed["payload"]["result_json"] = '{"code":0,"data":{"ok":false}}'
    from genshin_app.agent_chat.bridge_protocol import canonical_payload_hash

    changed["payload_hash"] = canonical_payload_hash(changed["payload"])
    with pytest.raises(PlanError) as error:
        validate_tool_settle_request(
            changed,
            binding=_binding(),
            snapshot=host.snapshot,
            call_id="call-1",
            canonical_args_hash=args_hash,
        )
    assert error.value.reason == "native_tool_settlement_hash_mismatch"


def test_session_lock_covers_snapshot_check_execution_and_settlement(monkeypatch):
    entered_gate = threading.Event()
    release_gate = threading.Event()
    mutation_attempted = threading.Event()
    mutation_entered = threading.Event()

    def gated(*_args):
        entered_gate.set()
        assert release_gate.wait(2)
        return {"code": 0, "data": {"phase": "calculation_closure"}}

    monkeypatch.setattr(tools, "call_tool", gated)
    session = _Session()
    host = NativeReadToolHost(session, binding=_binding())
    outcome = {}

    def execute():
        outcome["value"] = _call(host)

    def mutate_stage():
        mutation_attempted.set()
        with session._lock:
            mutation_entered.set()
            session.workflow_stage = "action_checkpoint"
            session.stage_state["stage_revision"] += 1

    executor = threading.Thread(target=execute)
    executor.start()
    assert entered_gate.wait(2)
    mutator = threading.Thread(target=mutate_stage)
    mutator.start()
    assert mutation_attempted.wait(2)
    assert not mutation_entered.wait(0.05)
    release_gate.set()
    executor.join(2)
    mutator.join(2)

    assert not executor.is_alive() and not mutator.is_alive()
    assert outcome["value"]["status"] == "settled_success"
    assert host.ledger["inflight"] == {}
    assert mutation_entered.is_set()


def test_unexpected_gate_failure_settles_and_replays_without_inflight(monkeypatch):
    calls = []

    def broken_gate(*_args):
        calls.append(True)
        raise RuntimeError("fixture failure must not escape into the ledger")

    monkeypatch.setattr(tools, "call_tool", broken_gate)
    host = NativeReadToolHost(_Session(), binding=_binding())

    first = _call(host)
    replay = _call(host)

    assert first == replay
    assert first["status"] == "settled_failure"
    assert first["result_code"] == "native_read_internal_failure"
    assert host.ledger["inflight"] == {}
    assert len(calls) == 1


def test_settle_request_cannot_rebind_one_call_result_to_another(monkeypatch):
    def gated(_session, _name, args):
        return {"code": 0, "data": {"resource": args["resource"]}}

    monkeypatch.setattr(tools, "call_tool", gated)
    host = NativeReadToolHost(_Session(), binding=_binding())
    raw_a = '{"resource":"workflow_stage"}'
    raw_b = '{"resource":"candidate_board"}'
    result_a = _call(host, call_id="call-a", raw=raw_a)
    result_b = _call(host, call_id="call-b", raw=raw_b)

    with pytest.raises(PlanError) as error:
        host.make_settle_request(
            settlement=result_a,
            call_id="call-b",
            canonical_args_hash=raw_json_hash(raw_b),
            request_id="request-swapped",
            event_seq=1,
        )
    assert error.value.reason == "native_read_settlement_mismatch"

    accepted = host.make_settle_request(
        settlement=result_b,
        call_id="call-b",
        canonical_args_hash=raw_json_hash(raw_b),
        request_id="request-b",
        event_seq=2,
    )
    assert accepted["payload"] == result_b


def test_production_host_and_stage_diagnostics_require_shared_rlock():
    session = _Session()
    del session._lock

    with pytest.raises(PlanError) as host_error:
        NativeReadToolHost(session, binding=_binding())
    assert host_error.value.reason == "native_read_session_lock_required"

    with pytest.raises(PlanError) as diagnostic_error:
        visible_tool_names(session)
    assert diagnostic_error.value.reason == "native_read_session_lock_required"


def _settlement_payload_for(result, *, status, result_code, resource_ids=None):
    raw = json.dumps(
        result,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "contract": "genshin-native-tool-settlement-v1",
        "tool": "gs_read_context",
        "status": status,
        "result_json": raw,
        "result_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "result_code": result_code,
        "resource_ids": [] if resource_ids is None else resource_ids,
        "runtime_generation": "dsh-v2",
        "fence_token": "fence-1",
    }


def test_settlement_payload_strictly_binds_inner_and_outer_terminal_state():
    success = _settlement_payload_for(
        {"code": 0, "data": {}},
        status="settled_success",
        result_code=0,
    )
    assert validate_settlement_payload(success) == success

    code_failure = _settlement_payload_for(
        {"code": 7},
        status="settled_failure",
        result_code=7,
    )
    assert validate_settlement_payload(code_failure) == code_failure

    reason_failure = _settlement_payload_for(
        {"reason": "read_failed", "message": "failed"},
        status="settled_failure",
        result_code="read_failed",
    )
    assert validate_settlement_payload(reason_failure) == reason_failure

    for invalid in (
        _settlement_payload_for({"code": 1}, status="settled_success", result_code=0),
        _settlement_payload_for(
            {"code": 0, "reason": "read_failed"},
            status="settled_success",
            result_code=0,
        ),
        _settlement_payload_for(
            {"code": 7, "reason": "read_failed"},
            status="settled_failure",
            result_code="different_failure",
        ),
        _settlement_payload_for({"code": 0}, status="settled_failure", result_code=1),
        _settlement_payload_for(
            {"code": 0, "reason": "read_failed"},
            status="settled_failure",
            result_code="read_failed",
        ),
        _settlement_payload_for(
            {"code": 0, "reason": 123},
            status="settled_success",
            result_code=0,
        ),
        _settlement_payload_for(
            {"code": 7, "reason": "read_failed"},
            status="settled_failure",
            result_code=7,
        ),
    ):
        with pytest.raises(PlanError) as error:
            validate_settlement_payload(invalid)
        assert error.value.reason == "native_tool_settlement_invalid"


def test_unknown_settlement_uses_reserved_marker_and_no_resources():
    unknown = _settlement_payload_for(
        {"reason": "outcome_unknown", "message": "outcome not observable"},
        status="unknown_after_interruption",
        result_code="outcome_unknown",
    )
    assert validate_settlement_payload(unknown) == unknown

    with pytest.raises(PlanError) as resources:
        validate_settlement_payload(
            {**unknown, "resource_ids": ["query-1"]},
        )
    assert resources.value.reason == "native_tool_settlement_invalid"

    wrong_reason = _settlement_payload_for(
        {"code": 1, "reason": "read_failed"},
        status="unknown_after_interruption",
        result_code="read_failed",
    )
    with pytest.raises(PlanError) as reason:
        validate_settlement_payload(wrong_reason)
    assert reason.value.reason == "native_tool_settlement_invalid"

    contradictory_code = _settlement_payload_for(
        {"code": 7, "reason": "outcome_unknown"},
        status="unknown_after_interruption",
        result_code="outcome_unknown",
    )
    with pytest.raises(PlanError) as code:
        validate_settlement_payload(contradictory_code)
    assert code.value.reason == "native_tool_settlement_invalid"


def test_code_only_tool_failure_uses_matching_outer_result_code(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", lambda *_args: {"code": 7})
    host = NativeReadToolHost(_Session(), binding=_binding())
    result = _call(host)

    assert result["status"] == "settled_failure"
    assert result["result_code"] == 7
    assert validate_settlement_payload(result) == result


def test_legacy_producer_preserves_valid_page_continuation_pointer(monkeypatch):
    value = {"large": {f"field-{index}": "x" * 200 for index in range(100)}}
    monkeypatch.setattr(
        tools,
        "call_tool",
        lambda *_args: {
            "code": 0,
            "data": tools.page(value, "workflow_stage"),
        },
    )
    host = NativeReadToolHost(_Session(), binding=_binding())
    settlement = _call(host)
    result = json.loads(settlement["result_json"])

    reference = result["data"]["items"][0]["value"]["reference"]
    assert reference == {"resource": "workflow_stage", "pointer": "/large"}
    assert validate_settlement_payload(settlement) == settlement


@pytest.mark.parametrize(
    "response",
    (
        {"code": 1, "reason": "outcome_unknown"},
        {"code": "outcome_unknown"},
    ),
)
def test_legacy_producer_normalizes_reserved_unknown_marker(response, monkeypatch):
    monkeypatch.setattr(tools, "call_tool", lambda *_args: response)
    host = NativeReadToolHost(_Session(), binding=_binding())
    result = _call(host)

    assert result["status"] == "settled_failure"
    assert result["result_code"] == "native_read_result_invalid"
    assert validate_settlement_payload(result) == result


def test_failure_settlement_rejects_reserved_unknown_code():
    invalid = _settlement_payload_for(
        {"code": "outcome_unknown"},
        status="settled_failure",
        result_code="outcome_unknown",
    )

    with pytest.raises(PlanError) as error:
        validate_settlement_payload(invalid)
    assert error.value.reason == "native_tool_settlement_invalid"
