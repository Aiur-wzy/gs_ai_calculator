"""Phase 3A sequential native read dispatch tests; no provider or pipe."""

from __future__ import annotations

import copy
import json
import threading

import pytest

from genshin_app.agent_chat import native_read_dispatch as dispatch_module
from genshin_app.agent_chat import tools
from genshin_app.agent_chat.bridge_protocol import make_frame, raw_json_hash
from genshin_app.agent_chat.native_read_dispatch import (
    MAX_SEQUENTIAL_READ_CALLS,
    MAX_TURN_DIRECT_BYTES,
    MAX_TURN_RESOURCE_REFS,
    NativeSequentialReadHost,
    build_native_read_snapshot,
    validate_native_read_snapshot,
)
from genshin_app.agent_chat.native_read_tool import (
    SETTLEMENT_ACK_CONTRACT,
    validate_tool_settle_ack,
    validate_tool_settle_request,
)
from genshin_app.agent_chat.native_tool_receipt import NativeToolReceipt
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError


class _Session:
    workflow = "staged"

    def __init__(self, phase="numeric_model", revision=7, stage_revision=12):
        self.workflow_stage = phase
        self.revision = revision
        self.candidate = {"value": "unchanged"}
        self.resources = {}
        self.stage_state = {
            "stage_revision": stage_revision,
            "resource_stages": {},
            "resource_descriptors": {},
        }
        self._lock = threading.RLock()

    def _is_board(self):
        return True

    def register(self, resource_id, value, *, query_kind="context"):
        self.resources[resource_id] = copy.deepcopy(value)
        self.stage_state["resource_stages"][resource_id] = self.workflow_stage
        self.stage_state["resource_descriptors"][resource_id] = {
            "id": resource_id,
            "stage": self.workflow_stage,
            "query_kind": query_kind,
            "action": None,
            "entities": [],
            "selectors": [],
            "fingerprint": fingerprint(value),
            "bytes": len(json_bytes(value)),
        }


def _binding(**changes):
    value = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "epoch_id": "epoch-1",
        "runtime_incarnation": "runtime-1",
        "runtime_generation": "dsh-v2",
        "database_fingerprint": "a" * 64,
        "fence_token": "fence-1",
    }
    value.update(changes)
    return value


def _live_binding(state):
    return lambda: {
        "database_fingerprint": state["database_fingerprint"],
        "fence_token": state["fence_token"],
    }


def _development_host(*args, **kwargs):
    """Construct the unit-only static-binding fixture explicitly."""
    kwargs["development_allow_static_binding"] = True
    return NativeSequentialReadHost(*args, **kwargs)


def _call(
    host,
    *,
    step_id="step-1",
    call_id="call-1",
    tool="gs_read_context",
    raw='{"resource":"workflow_stage"}',
    **changes,
):
    values = {
        "step_id": step_id,
        "call_id": call_id,
        "tool": tool,
        "arguments_json": raw,
        "canonical_args_hash": raw_json_hash(raw),
        "candidate_revision": host.snapshot["candidate_revision"],
        "stage_revision": host.snapshot["stage_revision"],
    }
    values.update(changes)
    return host.execute(**values)


def test_production_host_requires_authoritative_live_binding_reader():
    with pytest.raises(PlanError) as missing:
        NativeSequentialReadHost(_Session(), binding=_binding())
    assert missing.value.reason == "native_read_live_binding_required"

    live = {
        "database_fingerprint": _binding()["database_fingerprint"],
        "fence_token": _binding()["fence_token"],
    }
    host = NativeSequentialReadHost(
        _Session(),
        binding=_binding(),
        live_binding_reader=_live_binding(live),
    )
    assert host.snapshot["candidate_revision"] == 7
    assert host.diagnostic_snapshot()["binding_mode"] == "authoritative_live"

    development = _development_host(_Session(), binding=_binding())
    assert development.diagnostic_snapshot()["binding_mode"] == "development_static"


def _ack(host, result, *, step_id="step-1", call_id="call-1"):
    return host.acknowledge_settlement(
        step_id=step_id,
        call_id=call_id,
        result_hash=result["result_hash"],
    )


def test_snapshot_is_explicit_policy_intersection_and_hash_bound():
    session = _Session(phase="event_topology")
    snapshot = build_native_read_snapshot(session)
    names = [row["name"] for row in snapshot["tools"]]

    assert names == sorted(
        {
            "gs_hash_input",
            "gs_preview_attribute_recovery",
            "gs_read_context",
            "gs_read_skill",
            "gs_query_context",
            "gs_query_runtime",
            "gs_query_appendix",
            "gs_list_appendix_categories",
            "gs_get_character_appendix_index",
            "gs_character_panel_basis",
            "gs_weapon_stats",
            "gs_reference_panel",
            "gs_search_catalog",
        }
    )
    assert set(snapshot["read_policies"]) == set(names)
    assert snapshot["read_policies"]["gs_preview_attribute_recovery"] == "pure_read"
    assert "gs_patch_candidate" not in names
    assert "gs_transition_phase" not in names
    assert validate_native_read_snapshot(snapshot) == snapshot

    changed = copy.deepcopy(snapshot)
    changed["read_policies"]["gs_read_context"] = "session_resource_read"
    with pytest.raises(PlanError) as error:
        validate_native_read_snapshot(changed)
    assert error.value.reason == "native_read_dispatch_invalid"


def test_stage_change_invalidates_host_without_removing_knowledge_reads(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", lambda *_args: {"code": 0, "data": {}})
    session = _Session(phase="numeric_model")
    host = _development_host(session, binding=_binding())
    assert "gs_character_panel_basis" in {row["name"] for row in host.snapshot["tools"]}

    session.workflow_stage = "action_checkpoint"
    session.stage_state["stage_revision"] += 1
    current = build_native_read_snapshot(session)
    assert current["tool_manifest_hash"] != host.snapshot["tool_manifest_hash"]
    assert "gs_character_panel_basis" in {row["name"] for row in current["tools"]}
    with pytest.raises(PlanError) as error:
        _call(host)
    assert error.value.reason == "stale_tool_snapshot"


def test_pending_replay_and_ack_fail_after_stage_change(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", lambda *_args: {"code": 0, "data": {}})
    session = _Session()
    host = _development_host(session, binding=_binding())
    result = _call(host)
    session.stage_state["stage_revision"] += 1

    with pytest.raises(PlanError) as replay_error:
        _call(host)
    assert replay_error.value.reason == "stale_tool_snapshot"
    with pytest.raises(PlanError) as ack_error:
        _ack(host, result)
    assert ack_error.value.reason == "native_read_host_terminated"


def test_public_preflight_reuses_server_permission_and_schema_gate():
    session = _Session(phase="event_topology")
    accepted = tools.validate_tool_arguments_for_session(
        session, "gs_read_context", {"resource": "workflow_stage"}
    )
    assert accepted == {"resource": "workflow_stage"}

    with pytest.raises(PlanError) as invalid:
        tools.validate_tool_arguments_for_session(session, "gs_read_context", {})
    assert invalid.value.reason == "agent_tool_input"

    with pytest.raises(PlanError) as forbidden:
        tools.validate_tool_arguments_for_session(
            session, "gs_calculate", {"expected_revision": session.revision}
        )
    assert forbidden.value.reason == "agent_tool_forbidden"


def test_two_sequential_tools_require_ack_and_use_distinct_steps(monkeypatch):
    calls = []

    def gated(session, name, args):
        calls.append((name, copy.deepcopy(args)))
        return {"code": 0, "data": {"tool": name}}

    monkeypatch.setattr(tools, "call_tool", gated)
    host = _development_host(_Session(), binding=_binding())
    first = _call(host)

    with pytest.raises(PlanError) as pending:
        _call(
            host,
            step_id="step-2",
            call_id="call-2",
            tool="gs_read_skill",
            raw="{}",
        )
    assert pending.value.reason == "native_read_settlement_ack_pending"

    assert _call(host) == first
    _ack(host, first)
    second = _call(
        host,
        step_id="step-2",
        call_id="call-2",
        tool="gs_read_skill",
        raw="{}",
    )
    _ack(host, second, step_id="step-2", call_id="call-2")

    assert calls == [
        ("gs_read_context", {"resource": "workflow_stage"}),
        ("gs_read_skill", {}),
    ]
    assert json.loads(second["result_json"])["data"]["tool"] == "gs_read_skill"
    with pytest.raises(PlanError) as reused:
        _call(host)
    assert reused.value.reason == "native_read_step_reused"


def test_make_settle_request_binds_each_tool_to_its_own_step(monkeypatch):
    monkeypatch.setattr(
        tools,
        "call_tool",
        lambda _session, name, _args: {"code": 0, "data": {"tool": name}},
    )
    host = _development_host(_Session(), binding=_binding())
    first_raw = "{}"
    first = _call(host, tool="gs_read_skill", raw=first_raw)
    frame = host.make_settle_request(
        settlement=first,
        step_id="step-1",
        call_id="call-1",
        canonical_args_hash=raw_json_hash(first_raw),
        request_id="request-1",
        event_seq=1,
        message_id="11111111-1111-4111-8111-111111111111",
    )

    assert frame["type"] == "tool.settle"
    assert frame["step_id"] == "step-1"
    assert frame["call_id"] == "call-1"
    assert frame["payload"]["tool"] == "gs_read_skill"
    assert (
        validate_tool_settle_request(
            frame,
            binding=_binding(),
            snapshot=host.snapshot,
            call_id="call-1",
            canonical_args_hash=raw_json_hash(first_raw),
            expected_tool="gs_read_skill",
            snapshot_validator=validate_native_read_snapshot,
            expected_step_id="step-1",
        )
        == frame
    )
    ack = make_frame(
        kind="response",
        frame_type="tool.settle.ack",
        message_id="22222222-2222-4222-8222-222222222222",
        runtime_incarnation=frame["runtime_incarnation"],
        conversation_id=frame["conversation_id"],
        turn_id=frame["turn_id"],
        step_id=frame["step_id"],
        epoch_id=frame["epoch_id"],
        event_seq=1,
        payload={
            "contract": SETTLEMENT_ACK_CONTRACT,
            "disposition": "accepted",
            "settlement_status": first["status"],
            "result_hash": first["result_hash"],
            "runtime_generation": _binding()["runtime_generation"],
            "fence_token": _binding()["fence_token"],
        },
        **{
            name: frame[name]
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
    assert (
        validate_tool_settle_ack(
            ack,
            request=frame,
            expected_tool="gs_read_skill",
        )
        == ack
    )

    with pytest.raises(PlanError) as wrong_step:
        host.make_settle_request(
            settlement=first,
            step_id="step-other",
            call_id="call-1",
            canonical_args_hash=raw_json_hash(first_raw),
            request_id="request-other",
            event_seq=2,
        )
    assert wrong_step.value.reason == "native_read_settlement_mismatch"

    _ack(host, first)
    second_raw = '{"resource":"workflow_stage"}'
    second = _call(
        host,
        step_id="step-2",
        call_id="call-2",
        tool="gs_read_context",
        raw=second_raw,
    )
    second_frame = host.make_settle_request(
        settlement=second,
        step_id="step-2",
        call_id="call-2",
        canonical_args_hash=raw_json_hash(second_raw),
        request_id="request-2",
        event_seq=2,
    )
    assert second_frame["step_id"] == "step-2"
    assert second_frame["payload"]["tool"] == "gs_read_context"


def test_make_settle_request_rejects_tampered_cached_payload(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", lambda *_args: {"code": 0, "data": {}})
    host = _development_host(_Session(), binding=_binding())
    result = _call(host)
    tampered = copy.deepcopy(result)
    tampered["result_code"] = 1

    with pytest.raises(PlanError) as error:
        host.make_settle_request(
            settlement=tampered,
            step_id="step-1",
            call_id="call-1",
            canonical_args_hash=raw_json_hash('{"resource":"workflow_stage"}'),
            request_id="request-1",
            event_seq=1,
        )
    assert error.value.reason in {
        "native_tool_settlement_invalid",
        "native_read_settlement_mismatch",
    }


def test_query_resource_is_created_once_and_replay_uses_same_receipt(monkeypatch):
    session = _Session()
    calls = []

    def query_gate(current, name, args):
        calls.append((name, copy.deepcopy(args)))
        value = {"skills": [{"id": 1}]}
        current.register("query-1", value, query_kind="context")
        return {
            "code": 0,
            "data": {
                "resource": "query-1",
                "items": [],
                "source_ref": {
                    "kind": "database",
                    "reference": "query-1",
                    "fingerprint": fingerprint(value),
                    "status": "verified",
                },
            },
        }

    monkeypatch.setattr(tools, "call_tool", query_gate)
    host = _development_host(session, binding=_binding())
    raw = '{"request":{}}'
    first = _call(host, tool="gs_query_context", raw=raw)
    replay = _call(host, tool="gs_query_context", raw=raw)

    assert first == replay
    assert first["resource_ids"] == ["query-1"]
    assert len(calls) == 1


def test_dispatch_never_inferrs_writer_authority_from_tool_name(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools,
        "call_tool",
        lambda *_args: calls.append(True) or {"code": 0, "data": {}},
    )
    host = _development_host(_Session(), binding=_binding())
    raw = '{"target_phase":"calculation_closure","expected_revision":7,"expected_stage_revision":12,"reason":"x"}'

    with pytest.raises(PlanError) as error:
        _call(host, tool="gs_transition_phase", raw=raw)
    assert error.value.reason == "native_read_tool_forbidden"
    assert calls == []


def test_dispatch_enforces_transport_call_bound(monkeypatch):
    assert MAX_SEQUENTIAL_READ_CALLS == 16_384
    # Exercise the boundary without turning a protocol-capacity test into
    # thousands of cryptographic receipt/ACK iterations.
    monkeypatch.setattr(dispatch_module, "MAX_SEQUENTIAL_READ_CALLS", 8)
    monkeypatch.setattr(tools, "call_tool", lambda *_args: {"code": 0, "data": {}})
    host = _development_host(_Session(), binding=_binding())

    for index in range(8):
        step_id = f"step-{index}"
        call_id = f"call-{index}"
        result = _call(host, step_id=step_id, call_id=call_id)
        _ack(host, result, step_id=step_id, call_id=call_id)

    with pytest.raises(PlanError) as error:
        _call(host, step_id="step-overflow", call_id="call-overflow")
    assert error.value.reason == "native_read_call_limit"


def test_snapshot_builder_fails_closed_when_no_read_tool_is_visible(monkeypatch):
    monkeypatch.setattr(tools, "definitions_for_session", lambda _session: [])
    with pytest.raises(PlanError) as error:
        build_native_read_snapshot(_Session())
    assert error.value.reason == "native_read_tools_unavailable"


def test_snapshot_builder_rejects_non_closed_root_schema(monkeypatch):
    malformed = copy.deepcopy(tools.definitions_for_session(_Session()))
    for row in malformed:
        if row.get("name") in tools.NATIVE_V2_READ_POLICIES:
            row["inputSchema"]["additionalProperties"] = True
            break
    monkeypatch.setattr(tools, "definitions_for_session", lambda _session: malformed)

    with pytest.raises(PlanError) as error:
        build_native_read_snapshot(_Session())
    assert error.value.reason == "native_read_dispatch_invalid"


def test_snapshot_validator_contains_non_object_definition_as_plan_error():
    snapshot = build_native_read_snapshot(_Session())
    malformed = copy.deepcopy(snapshot)
    malformed["tools"] = [None]
    malformed["read_policies"] = {}

    with pytest.raises(PlanError) as error:
        validate_native_read_snapshot(malformed)
    assert error.value.reason == "native_read_dispatch_invalid"


def test_read_tool_candidate_mutation_becomes_unknown_and_terminates_host(
    monkeypatch,
):
    session = _Session()

    def mutating_gate(current, _name, _args):
        current.candidate["injected"] = True
        return {"code": 0, "data": {"ok": True}}

    monkeypatch.setattr(tools, "call_tool", mutating_gate)
    host = _development_host(session, binding=_binding())

    with pytest.raises(PlanError) as error:
        _call(host)
    assert error.value.reason == "native_read_authority_changed"
    assert host.ledger["settlements"]["call-1"]["terminal_state"] == (
        "unknown_after_interruption"
    )
    assert host.ledger["inflight"] == {}
    with pytest.raises(PlanError) as terminated:
        _call(host, step_id="step-2", call_id="call-2")
    assert terminated.value.reason == "native_read_host_terminated"


def test_live_database_or_fence_change_after_tool_terminates_unknown(monkeypatch):
    session = _Session()
    live = {
        "database_fingerprint": _binding()["database_fingerprint"],
        "fence_token": _binding()["fence_token"],
    }

    def stale_gate(*_args):
        live["fence_token"] = "fence-2"
        return {"code": 0, "data": {"ok": True}}

    monkeypatch.setattr(tools, "call_tool", stale_gate)
    host = _development_host(
        session,
        binding=_binding(),
        live_binding_reader=_live_binding(live),
    )
    with pytest.raises(PlanError) as error:
        _call(host)
    assert error.value.reason == "native_read_authority_changed"
    assert host.ledger["settlements"]["call-1"]["terminal_state"] == (
        "unknown_after_interruption"
    )


def test_tool_exception_cannot_hide_stage_mutation(monkeypatch):
    session = _Session()

    def broken_gate(current, _name, _args):
        current.stage_state["stage_revision"] += 1
        raise RuntimeError("must not become an ordinary tool failure")

    monkeypatch.setattr(tools, "call_tool", broken_gate)
    host = _development_host(session, binding=_binding())
    with pytest.raises(PlanError) as error:
        _call(host)
    assert error.value.reason == "native_read_authority_changed"
    assert host.ledger["settlements"]["call-1"]["terminal_state"] == (
        "unknown_after_interruption"
    )


def _query_gate(current, name, _args):
    if name == "gs_query_context":
        value = {"skills": [{"id": 1}]}
        current.register("query-1", value, query_kind="context")
        return {"code": 0, "data": {"resource": "query-1", "items": []}}
    if name == "gs_read_context":
        return {"code": 0, "data": {"resource": "query-1", "items": []}}
    raise AssertionError(name)


def _issue_query_reference(host):
    raw = '{"request":{}}'
    result = _call(host, tool="gs_query_context", raw=raw)
    _ack(host, result)
    return host.issue_resource_reference("query-1")


def test_scoped_resource_reference_rejects_cross_session_alias_and_replacement(
    monkeypatch,
):
    monkeypatch.setattr(tools, "call_tool", _query_gate)
    session_a = _Session()
    host_a = _development_host(session_a, binding=_binding())
    reference = _issue_query_reference(host_a)

    session_b = _Session()
    session_b.register("query-1", {"skills": [{"id": 1}]})
    host_b = _development_host(
        session_b,
        binding=_binding(conversation_id="conversation-2"),
    )
    with pytest.raises(PlanError) as cross_session:
        host_b.resolve_resource_reference(reference)
    assert cross_session.value.reason == "native_resource_scope_mismatch"

    session_a.register("query-1", {"skills": [{"id": 2}]})
    with pytest.raises(PlanError) as replaced:
        host_a.resolve_resource_reference(reference)
    assert replaced.value.reason == "native_resource_reference_stale"


def test_resource_reference_detects_descriptor_change_and_stage_reentry(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", _query_gate)
    descriptor_session = _Session()
    descriptor_host = _development_host(descriptor_session, binding=_binding())
    reference = _issue_query_reference(descriptor_host)
    descriptor_session.stage_state["resource_descriptors"]["query-1"]["selectors"] = [
        "changed=true"
    ]
    with pytest.raises(PlanError) as descriptor_changed:
        descriptor_host.resolve_resource_reference(reference)
    assert descriptor_changed.value.reason == "native_resource_reference_stale"

    stage_session = _Session()
    stage_host = _development_host(
        stage_session,
        binding=_binding(conversation_id="conversation-stage"),
    )
    stage_reference = _issue_query_reference(stage_host)
    stage_session.workflow_stage = "action_checkpoint"
    stage_session.stage_state["stage_revision"] += 1
    stage_session.workflow_stage = "numeric_model"
    stage_session.stage_state["stage_revision"] += 1
    with pytest.raises(PlanError) as reentered:
        stage_host.resolve_resource_reference(stage_reference)
    assert reentered.value.reason == "stale_tool_snapshot"


def test_dedicated_resource_reader_revalidates_reference_before_alias_read(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", _query_gate)
    host = _development_host(_Session(), binding=_binding())
    reference = _issue_query_reference(host)

    result = host.read_resource_reference(
        reference,
        step_id="step-2",
        call_id="call-2",
    )
    assert json.loads(result["result_json"])["data"]["resource"] == "query-1"


def test_referenced_continuation_calls_disclosed_tool_with_issued_id(monkeypatch):
    calls = []

    def gate(current, name, args):
        calls.append((name, copy.deepcopy(args)))
        if name == "gs_query_context":
            current.register("query-1", {"skills": [{"id": 1}]})
            return {
                "code": 0,
                "data": {"resource": "query-1", "inline": "x" * (48 * 1024)},
            }
        if name == "gs_read_context":
            return {"code": 0, "data": {"resource": args["resource"], "items": []}}
        raise AssertionError(name)

    monkeypatch.setattr(tools, "call_tool", gate)
    host = _development_host(_Session(), binding=_binding())
    query_raw = '{"request":{}}'
    first = _call(host, tool="gs_query_context", raw=query_raw)
    parsed = json.loads(first["result_json"])
    continuation = parsed["data"]["continuations"][0]
    assert continuation["tool"] == "gs_read_context"
    assert continuation["arguments"]["resource"] == "query-1"
    _ack(host, first)

    read_raw = json.dumps(
        continuation["arguments"],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    second = _call(
        host,
        step_id="step-2",
        call_id="call-2",
        tool=continuation["tool"],
        raw=read_raw,
    )
    assert json.loads(second["result_json"])["data"]["resource"] == "query-1"
    assert [row[0] for row in calls] == ["gs_query_context", "gs_read_context"]


def test_registered_alias_cannot_be_read_without_host_issued_reference(monkeypatch):
    calls = []
    monkeypatch.setattr(
        tools,
        "call_tool",
        lambda *_args: calls.append(True) or {"code": 0, "data": {}},
    )
    session = _Session()
    session.register("query-1", {"value": 1})
    host = _development_host(session, binding=_binding())
    raw = '{"resource":"query-1"}'

    with pytest.raises(PlanError) as error:
        _call(host, raw=raw)
    assert error.value.reason == "native_resource_scope_required"
    assert calls == []


def test_turn_direct_budget_is_cumulative(monkeypatch):
    monkeypatch.setattr(dispatch_module, "MAX_SEQUENTIAL_READ_CALLS", 8)
    monkeypatch.setattr(
        tools,
        "call_tool",
        lambda *_args: {"code": 0, "data": {"text": "x" * 40000}},
    )
    host = _development_host(_Session(), binding=_binding())
    reasons = []
    for index in range(8):
        step_id = f"step-{index}"
        call_id = f"call-{index}"
        result = _call(host, step_id=step_id, call_id=call_id)
        parsed = json.loads(result["result_json"])
        reasons.append(parsed.get("reason"))
        _ack(host, result, step_id=step_id, call_id=call_id)

    diagnostic = host.diagnostic_snapshot()
    assert diagnostic["direct_result_bytes"] <= MAX_TURN_DIRECT_BYTES
    assert "native_result_requires_resource" in reasons


def test_turn_resource_reference_limit_fails_before_ledger_limit(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", lambda *_args: {"code": 0, "data": {}})
    resource_ids = tuple(
        f"query-{index}" for index in range(MAX_TURN_RESOURCE_REFS + 1)
    )
    oversized_receipt = NativeToolReceipt(
        {
            "code": 0,
            "data": {},
            "native_receipt": {
                "contract": "genshin-native-read-receipt-v1",
                "mode": "direct",
                "tool": "gs_read_context",
                "resources": [],
            },
        },
        resource_ids,
    )
    monkeypatch.setattr(
        dispatch_module,
        "build_native_tool_receipt",
        lambda *_args, **_kwargs: oversized_receipt,
    )
    host = _development_host(_Session(), binding=_binding())
    result = _call(host)
    parsed = json.loads(result["result_json"])

    assert parsed["reason"] == "native_turn_resource_limit"
    assert result["resource_ids"] == []


def test_ack_race_has_one_winner(monkeypatch):
    monkeypatch.setattr(tools, "call_tool", lambda *_args: {"code": 0, "data": {}})
    host = _development_host(_Session(), binding=_binding())
    result = _call(host)
    barrier = threading.Barrier(3)
    outcomes = []

    def acknowledge():
        barrier.wait()
        try:
            outcomes.append(("ok", _ack(host, result)))
        except PlanError as exc:
            outcomes.append(("error", exc.reason))

    left = threading.Thread(target=acknowledge)
    right = threading.Thread(target=acknowledge)
    left.start()
    right.start()
    barrier.wait()
    left.join(2)
    right.join(2)

    assert not left.is_alive() and not right.is_alive()
    assert sorted(row[0] for row in outcomes) == ["error", "ok"]
    assert next(row[1] for row in outcomes if row[0] == "error") == (
        "native_read_ack_unexpected"
    )


def test_concurrent_second_call_waits_then_observes_ack_gate(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    second_started = threading.Event()
    second_finished = threading.Event()
    calls = []

    def gated(*_args):
        calls.append(True)
        entered.set()
        assert release.wait(2)
        return {"code": 0, "data": {"ok": True}}

    monkeypatch.setattr(tools, "call_tool", gated)
    host = _development_host(_Session(), binding=_binding())
    outcomes = {}

    def first_call():
        outcomes["first"] = _call(host)

    def second_call():
        second_started.set()
        try:
            outcomes["second"] = _call(
                host,
                step_id="step-2",
                call_id="call-2",
            )
        except PlanError as exc:
            outcomes["second_error"] = exc.reason
        finally:
            second_finished.set()

    first = threading.Thread(target=first_call)
    second = threading.Thread(target=second_call)
    first.start()
    assert entered.wait(2)
    second.start()
    assert second_started.wait(2)
    assert not second_finished.wait(0.05)
    release.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive() and not second.is_alive()
    assert outcomes["first"]["status"] == "settled_success"
    assert outcomes["second_error"] == "native_read_settlement_ack_pending"
    assert len(calls) == 1


def test_cancel_during_tool_execution_marks_unknown_and_terminates(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def gated(*_args):
        entered.set()
        assert release.wait(2)
        return {"code": 0, "data": {"ok": True}}

    monkeypatch.setattr(tools, "call_tool", gated)
    host = _development_host(_Session(), binding=_binding())
    outcome = {}

    def execute():
        try:
            outcome["result"] = _call(host)
        except PlanError as exc:
            outcome["error"] = exc.reason

    worker = threading.Thread(target=execute)
    worker.start()
    assert entered.wait(2)
    assert host.request_cancel()["disposition"] == "requested"
    release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert outcome == {"error": "native_read_cancelled"}
    assert host.ledger["settlements"]["call-1"]["terminal_state"] == (
        "unknown_after_interruption"
    )
