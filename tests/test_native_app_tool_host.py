from __future__ import annotations

import copy
import threading

import pytest

from genshin_app.agent_chat import native_app_call_ledger as ledger_module
from genshin_app.agent_chat.bridge_protocol import raw_json_hash
from genshin_app.agent_chat.native_app_action_ledger import NativeAppActionLedger
from genshin_app.agent_chat.native_app_action_tools import NativeAppActionToolHost
from genshin_app.agent_chat.native_app_call_ledger import NativeAppCallLedger
from genshin_app.agent_chat.native_app_tool_host import NativeAppToolHost
from genshin_app.agent_chat.native_research_ledger import NativeResearchLedger
from genshin_app.equipment.schema import PlanError

HASH = "a" * 64


class Session:
    workflow = "staged"

    def __init__(self, phase="event_topology"):
        self.workflow_stage = phase
        self.revision = 2
        self.stage_state = {
            "stage_revision": 3,
            "resource_stages": {},
            "resource_descriptors": {},
            "finding_stages": [],
        }
        self.candidate = {"value": 1}
        self.baseline = {"value": 1}
        self.changes = []
        self.provisional_authority = {"revision": 0}
        self.database_snapshot = {"version": "test"}
        self.document = {"value": 1}
        self.document_fingerprint = "document-fingerprint"
        self.resources = {}
        self.findings = []
        self.report = None
        self.calculated = None
        self._diagnostic_cache = None
        self._review_projection_records = {}
        self._review_projection_orphans = []
        self._lock = threading.RLock()

    def _is_board(self):
        return True


def host(tmp_path, session, executor, *, research_ledger=None, app_action_host=None):
    store = NativeAppCallLedger(tmp_path, conversation_id="conversation-1")
    return NativeAppToolHost(
        session,
        conversation_id="conversation-1",
        document_id="document-1",
        database_fingerprint="b" * 64,
        tool_manifest_hash="c" * 64,
        runtime_generation="dsh-v2",
        writer_fence="fence-1",
        ledger=store,
        domain_read_executor=executor,
        domain_write_executor=executor,
        development_allow_static_binding=True,
        research_ledger=research_ledger,
        app_action_host=app_action_host,
    )


def call(current, *, call_id="call-1", tool="gs_read_context", raw=None, **changes):
    raw = raw or '{"resource":"workflow_stage"}'
    snapshot = current.snapshot
    values = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "step_id": "step-1",
        "epoch_id": "epoch-1",
        "call_id": call_id,
        "tool_name": tool,
        "arguments_json": raw,
        "raw_arguments_hash": raw_json_hash(raw),
        "document_id": snapshot["document_id"],
        "candidate_revision": snapshot["candidate_revision"],
        "stage_revision": snapshot["stage_revision"],
        "database_fingerprint": snapshot["database_fingerprint"],
        "tool_manifest_hash": snapshot["tool_manifest_hash"],
        "runtime_generation": snapshot["runtime_generation"],
        "writer_fence": snapshot["writer_fence"],
        "catalog_hash": snapshot["catalog_hash"],
    }
    values.update(changes)
    return current.execute(**values)


def test_unexpected_program_fault_is_settled_once_without_task_repair(tmp_path):
    attempts = []

    def failing(*_args):
        attempts.append(1)
        raise ModuleNotFoundError("internal schema registry missing")

    current = host(tmp_path, Session(), failing)
    result = call(current)
    assert result["terminal_state"] == "settled_failure"
    assert result["result"]["failure_kind"] == "system"
    assert result["result"]["system_fault"]["cause_code"] == "module_unavailable"
    assert result["result"]["recovery"]["action"] == "report_system_fault"
    assert "category" not in result["result"]
    from genshin_app.app_fault_store import read_system_diagnostic
    diagnostic = read_system_diagnostic(tmp_path, result["result"]["system_fault"]["diagnostic_id"])
    assert diagnostic["context"] == {"conversation_id": "conversation-1", "turn_id": "turn-1", "step_id": "step-1",
        "call_id": "call-1", "document_id": "document-1", "candidate_revision": 2, "stage_revision": 3}
    replay = call(current)
    assert replay["result"] == result["result"]
    assert attempts == [1]
    assert len(list((tmp_path / "logs/app-system-faults").glob("*.json"))) == 1


def test_tool_dispatch_internal_typeerror_retains_system_fault_through_host(tmp_path, monkeypatch):
    from genshin_app.agent_chat import tools
    session = Session()

    def invalid_internal(*_args):
        raise TypeError("schema internal traversal bug")

    monkeypatch.setitem(tools.DISPATCH, "gs_read_context", invalid_internal)
    current = host(tmp_path, session, lambda name, args: tools.call_tool(session, name, args))
    result = call(current)
    assert result["terminal_state"] == "settled_failure"
    assert result["result"]["reason"] == "app_internal_error"
    assert result["result"]["system_fault"]["operation"] == "gs_read_context"


def test_strict_json_duplicate_and_unsafe_number_rejected_before_execution(tmp_path):
    calls = []
    current = host(tmp_path, Session(), lambda *_args: calls.append(1) or {"code": 0})
    with pytest.raises(PlanError) as duplicate:
        call(current, raw='{"resource":"a","resource":"b"}')
    assert duplicate.value.reason == "duplicate_json_key"
    with pytest.raises(PlanError) as unsafe:
        call(current, raw='{"resource":"x","offset":9007199254740992}')
    assert unsafe.value.reason == "json_integer_out_of_range"
    assert calls == []


def test_ordered_tuple_read_result_is_normalized_before_call_ledger(tmp_path):
    current = host(
        tmp_path,
        Session(),
        lambda *_args: {
            "code": 0,
            "data": {"character": ("skills", "talents", "annotations")},
        },
    )

    terminal = call(current)

    assert terminal["terminal_state"] == "settled_success"
    assert terminal["result"] == {
        "code": 0,
        "data": {"character": ["skills", "talents", "annotations"]},
    }


def test_schema_failure_keeps_repairable_field_details_end_to_end(tmp_path):
    calls = []
    current = host(tmp_path, Session(), lambda *_args: calls.append(1) or {"code": 0})

    result = call(current, raw="{}")

    assert result["terminal_state"] == "settled_failure"
    assert result["terminal_reason"] == "agent_tool_input"
    failure = result["result"]
    assert failure["reason"] == "agent_tool_input"
    assert failure["message"] == "工具参数不合法"
    assert failure["field"] == "request.resource"
    assert failure["path"] == "/resource"
    assert failure["contract_ref"] == {"resource": "tool_contracts", "pointer": "/tools/gs_read_context"}
    assert failure["repair_hint"] == "缺少必需字段"
    assert failure["retryable"] is True
    assert failure["details"] == [
        {"path": "request.resource", "message": "缺少必需字段"}
    ]
    assert calls == []


def test_app_tool_host_honors_ledger_transport_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "MAX_CALLS_PER_TURN", 8)
    current = host(
        tmp_path,
        Session(),
        lambda *_args: {"code": 0, "data": {"ok": True}},
    )
    for index in range(8):
        result = call(current, call_id=f"call-{index}")
        assert result["terminal_state"] == "settled_success"
    with pytest.raises(PlanError) as caught:
        call(current, call_id="call-9")
    assert caught.value.reason == "native_app_call_budget_exhausted"


def test_exact_replay_does_not_execute_twice_and_changed_args_conflict(tmp_path):
    count = 0

    def executor(*_args):
        nonlocal count
        count += 1
        return {"code": 0, "data": {"count": count}}

    current = host(tmp_path, Session(), executor)
    first = call(current)
    second = call(current)
    assert first["result"]["data"]["count"] == 1
    assert second["status"] == "replay"
    assert count == 1

    raw = '{"resource":"candidate_board"}'
    with pytest.raises(PlanError) as caught:
        call(current, raw=raw)
    assert caught.value.reason == "call_id_conflict"


def test_terminal_replay_rejects_domain_state_that_changed_out_of_band(tmp_path):
    session = Session()
    current = host(tmp_path, session, lambda *_args: {"code": 0, "data": {}})
    call(current)
    session.candidate["value"] = 7
    with pytest.raises(PlanError) as caught:
        call(current)
    assert caught.value.reason == "native_app_terminal_state_mismatch"


def test_terminal_replay_binds_full_durable_candidate_checkpoint(tmp_path):
    session = Session()
    current = host(tmp_path, session, lambda *_args: {"code": 0, "data": {}})
    call(current)

    # These facts used to be absent from NativeAppStateVector.  A cold or
    # in-process replay must not treat a changed baseline/D2 authority as the
    # same committed App state merely because candidate JSON stayed equal.
    session.baseline["value"] = 99
    session.provisional_authority["revision"] = 1
    session.changes.append({"revision": 2, "message": "changed"})

    with pytest.raises(PlanError) as caught:
        call(current)
    assert caught.value.reason == "native_app_terminal_state_mismatch"


def test_read_tool_cannot_mutate_unprojected_durable_candidate_fact(tmp_path):
    session = Session()

    def bad_read(current, _name, _arguments):
        current.baseline["value"] = 5
        return {"code": 0, "data": {}}

    current = host(tmp_path, session, bad_read)
    result = call(current)

    assert result["terminal_state"] == "unknown_after_interruption"
    assert result["disallowed_state_changes"] == ["durable_checkpoint_hash"]


def test_successful_candidate_write_must_change_only_declared_state(tmp_path):
    session = Session()

    def executor(current, name, _arguments):
        assert name == "gs_patch_candidate"
        current.candidate["value"] = 2
        current.revision += 1
        return {"code": 0, "data": {"revision": current.revision}}

    current = host(tmp_path, session, executor)
    result = call(
        current,
        tool="gs_patch_candidate",
        raw='{"expected_revision":2,"reason":"test","operations":[{"op":"replace","path":"/value","value":2}]}',
    )
    assert result["terminal_state"] == "settled_success"
    assert set(result["state_changes"]) == {
        "candidate_hash", "candidate_revision", "durable_checkpoint_hash",
    }
    assert result["next_snapshot"]["candidate_revision"] == 3


def test_exact_write_replay_survives_advanced_candidate_snapshot(tmp_path):
    session = Session()
    executions = 0

    def executor(current, _name, _arguments):
        nonlocal executions
        executions += 1
        current.candidate["value"] = 2
        current.revision += 1
        return {"code": 0, "data": {"revision": current.revision}}

    current = host(tmp_path, session, executor)
    raw = '{"expected_revision":2,"reason":"test","operations":[{"op":"replace","path":"/value","value":2}]}'
    old = copy.deepcopy(current.snapshot)
    request = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "step_id": "step-1",
        "epoch_id": "epoch-1",
        "call_id": "write-1",
        "tool_name": "gs_patch_candidate",
        "arguments_json": raw,
        "raw_arguments_hash": raw_json_hash(raw),
        "document_id": old["document_id"],
        "candidate_revision": old["candidate_revision"],
        "stage_revision": old["stage_revision"],
        "database_fingerprint": old["database_fingerprint"],
        "tool_manifest_hash": old["tool_manifest_hash"],
        "runtime_generation": old["runtime_generation"],
        "writer_fence": old["writer_fence"],
        "catalog_hash": old["catalog_hash"],
    }
    first = current.execute(**request)
    second = current.execute(**request)
    assert first["terminal_state"] == "settled_success"
    assert second["status"] == "replay"
    assert second["result"] == first["result"]
    assert second["next_snapshot"] == first["next_snapshot"]
    assert second["next_snapshot"]["candidate_revision"] == 3
    assert executions == 1


def test_success_without_required_effect_becomes_failure(tmp_path):
    current = host(tmp_path, Session(), lambda *_args: {"code": 0, "data": {}})
    result = call(
        current,
        tool="gs_patch_candidate",
        raw='{"expected_revision":2,"reason":"test","operations":[{"op":"replace","path":"/value","value":2}]}',
    )
    assert result["terminal_state"] == "settled_failure"
    assert result["result"]["reason"] == "native_app_tool_success_without_effect"


def test_stage_tool_cannot_succeed_with_only_an_unrelated_candidate_change(tmp_path):
    session = Session()

    def executor(current, name, _arguments):
        assert name == "gs_transition_phase"
        current.candidate["value"] = 9
        current.revision += 1
        return {"code": 0, "data": {"revision": current.revision}}

    current = host(tmp_path, session, executor)
    result = call(
        current,
        tool="gs_transition_phase",
        raw=(
            '{"target_phase":"numeric_model","expected_revision":2,'
            '"expected_stage_revision":3,"reason":"test"}'
        ),
    )

    assert result["terminal_state"] == "unknown_after_interruption"
    assert result["terminal_reason"] == "native_app_stage_transition_missing"
    assert result["stage_transition"] is None
    assert result["state_changes"] == [
        "candidate_hash", "candidate_revision", "durable_checkpoint_hash",
    ]


def test_read_or_failed_tool_cannot_hide_state_change(tmp_path):
    session = Session()

    def bad_read(current, _name, _arguments):
        current.candidate["value"] = 9
        return {"code": 0, "data": {}}

    current = host(tmp_path, session, bad_read)
    result = call(current)
    assert result["terminal_state"] == "unknown_after_interruption"
    assert result["disallowed_state_changes"] == [
        "candidate_hash", "durable_checkpoint_hash",
    ]

    session = Session()

    def failed_write(current, _name, _arguments):
        current.candidate["value"] = 9
        current.revision += 1
        return {"code": 1, "reason": "rejected", "message": "no"}

    current = host(tmp_path / "second", session, failed_write)
    result = call(
        current,
        tool="gs_patch_candidate",
        raw='{"expected_revision":2,"reason":"test","operations":[{"op":"replace","path":"/value","value":2}]}',
    )
    assert result["terminal_state"] == "unknown_after_interruption"
    assert result["terminal_reason"] == "failed_tool_changed_state"


@pytest.mark.parametrize("reason", ["mechanics_pending", "agent_calculation_rejected"])
def test_calculation_diagnostic_failure_commits_declared_derived_state(
    tmp_path, reason,
):
    session = Session(phase="calculation_closure")

    def diagnostic(current, name, _arguments):
        assert name == "gs_calculate"
        current.report = {"missing_inputs": [{"code": reason}]}
        current._diagnostic_cache = {"reason": reason}
        return {"code": 1, "reason": reason, "message": "诊断结果待补齐"}

    current = host(tmp_path, session, diagnostic)
    result = call(
        current,
        tool="gs_calculate",
        raw='{"expected_revision":2}',
    )

    assert result["terminal_state"] == "settled_failure"
    assert result["terminal_reason"] == reason
    assert result["result"] == {
        "code": 1,
        "reason": reason,
        "message": "诊断结果待补齐",
    }
    assert result["disallowed_state_changes"] == []
    assert set(result["state_changes"]) == {
        "derived_hash", "durable_checkpoint_hash",
    }

    replay = call(
        current,
        tool="gs_calculate",
        raw='{"expected_revision":2}',
    )
    assert replay["status"] == "replay"
    assert replay["terminal_state"] == "settled_failure"
    assert replay["result"] == result["result"]


def test_unlisted_calculation_failure_with_derived_change_remains_unknown(tmp_path):
    session = Session(phase="calculation_closure")

    def diagnostic(current, name, _arguments):
        assert name == "gs_calculate"
        current.report = {"missing_inputs": [{"code": "engine_failed"}]}
        return {"code": 1, "reason": "engine_failed", "message": "引擎失败"}

    current = host(tmp_path, session, diagnostic)
    result = call(current, tool="gs_calculate", raw='{"expected_revision":2}')

    assert result["terminal_state"] == "unknown_after_interruption"
    assert result["terminal_reason"] == "failed_tool_changed_state"
    assert result["state_changes"] == [
        "derived_hash", "durable_checkpoint_hash",
    ]


def test_calculation_diagnostic_failure_cannot_commit_candidate_change(tmp_path):
    session = Session(phase="calculation_closure")

    def diagnostic(current, name, _arguments):
        assert name == "gs_calculate"
        current.report = {"missing_inputs": [{"code": "mechanics_pending"}]}
        current.candidate["value"] = 9
        current.revision += 1
        return {
            "code": 1,
            "reason": "mechanics_pending",
            "message": "诊断结果待补齐",
        }

    current = host(tmp_path, session, diagnostic)
    result = call(current, tool="gs_calculate", raw='{"expected_revision":2}')

    assert result["terminal_state"] == "unknown_after_interruption"
    assert result["terminal_reason"] == "effect_mask_violation"
    assert result["disallowed_state_changes"] == [
        "candidate_hash", "candidate_revision",
    ]


def test_concurrent_tool_call_is_rejected(tmp_path):
    entered = threading.Event()
    release = threading.Event()

    def executor(*_args):
        entered.set()
        release.wait(2)
        return {"code": 0}

    current = host(tmp_path, Session(), executor)
    results = []
    thread = threading.Thread(target=lambda: results.append(call(current, call_id="call-1")))
    thread.start()
    assert entered.wait(1)
    with pytest.raises(PlanError) as caught:
        call(current, call_id="call-2")
    assert caught.value.reason == "native_app_call_concurrency"
    release.set()
    thread.join(2)
    assert results[0]["terminal_state"] == "settled_success"


def test_uncaught_process_boundary_interruption_is_recovered_unknown(tmp_path):
    def interrupted(*_args):
        raise SystemExit(9)

    current = host(tmp_path, Session(), interrupted)
    with pytest.raises(SystemExit):
        call(current)
    assert current.recover_interrupted_calls() == ["call-1"]
    replay = call(current)
    assert replay["status"] == "replay"
    assert replay["terminal_state"] == "unknown_after_interruption"


def test_production_host_requires_authoritative_live_binding(tmp_path):
    session = Session()
    ledger = NativeAppCallLedger(tmp_path, conversation_id="conversation-1")
    with pytest.raises(PlanError) as caught:
        NativeAppToolHost(
            session,
            conversation_id="conversation-1",
            document_id="document-1",
            database_fingerprint="b" * 64,
            tool_manifest_hash="c" * 64,
            runtime_generation="dsh-v2",
            writer_fence="fence-1",
            ledger=ledger,
            domain_write_executor=lambda *_args: {"code": 0},
        )
    assert caught.value.reason == "native_app_live_binding_required"


def test_production_host_requires_atomic_domain_write_boundary(tmp_path):
    session = Session()
    ledger = NativeAppCallLedger(tmp_path, conversation_id="conversation-1")
    with pytest.raises(PlanError) as caught:
        NativeAppToolHost(
            session,
            conversation_id="conversation-1",
            document_id="document-1",
            database_fingerprint="b" * 64,
            tool_manifest_hash="c" * 64,
            runtime_generation="dsh-v2",
            writer_fence="fence-1",
            ledger=ledger,
            domain_write_executor=lambda *_args: {"code": 0},
            live_binding_reader=lambda: {
                "document_id": "document-1",
                "database_fingerprint": "b" * 64,
                "tool_manifest_hash": "c" * 64,
                "runtime_generation": "dsh-v2",
                "writer_fence": "fence-1",
            },
        )
    assert caught.value.reason == "native_app_write_boundary_required"


def test_research_and_app_action_tools_delegate_to_existing_hosts(tmp_path):
    research = NativeResearchLedger(
        tmp_path / "research",
        conversation_id="conversation-1",
    )
    action_ledger = NativeAppActionLedger(
        tmp_path / "actions",
        conversation_id="conversation-1",
    )
    binding = {
        "document_id": "document-1",
        "candidate_revision": 2,
        "stage_revision": 3,
        "receipt_fingerprint": HASH,
        "applied_receipt_fingerprint": HASH,
    }
    workspace = {
        "document_kind": "whiteboard",
        "display_name": "测试方案",
        "current_view": "whiteboard",
        "dirty": False,
        "has_path": False,
        "can_save": True,
        "can_open": True,
        "can_export": True,
    }
    action_host = NativeAppActionToolHost(
        ledger=action_ledger,
        binding_reader=lambda: binding,
        workspace_reader=lambda: workspace,
    )
    domain_calls = []
    current = host(
        tmp_path / "calls",
        Session(),
        lambda *_args: domain_calls.append(True) or {"code": 0},
        research_ledger=research,
        app_action_host=action_host,
    )

    listed = call(
        current,
        call_id="research-list",
        tool="gs_list_research_calls",
        raw="{}",
    )
    assert listed["result"]["items"] == []
    assert listed["effect"] == "external_research_ledger"

    requested = call(
        current,
        call_id="action-request",
        tool="gs_app_request_action",
        raw='{"action":"save_as","reason":"保存方案"}',
    )
    assert requested["result"]["status"] == "queued_for_user"
    assert requested["effect"] == "external_action_ledger"
    assert action_ledger.read()["cards"][0]["status"] == "queued"
    assert domain_calls == []
