from __future__ import annotations

import json
import threading
import uuid
from contextlib import contextmanager

import pytest

from genshin_app.agent_chat.bridge_protocol import raw_json_hash
from genshin_app.agent_chat.native_app_action_controller import (
    NativeAppActionControllerError,
)
from genshin_app.agent_chat.native_app_call_queue import AppCallDeferred
from genshin_app.agent_chat.native_dsh_domain_calls import (
    DshDomainTarget,
    NativeDshDomainCalls,
)
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from tests.test_native_app_tool_host import Session, host


def request(current, **changes):
    snapshot = current.current_snapshot()
    raw = '{"resource":"workflow_stage"}'
    value = {"contract": "genshin-native-dsh-domain-call-v1", "request_id": str(uuid.uuid4()),
             "session_id": "dsh-one", "turn": 1, "step": 1, "call_id": "call-one", "call_seq": 5,
             "tool": "gs_read_context", "arguments_json": raw, "raw_arguments_hash": raw_json_hash(raw),
             **{key: snapshot[key] for key in ("catalog_hash", "candidate_revision", "stage_revision",
                "document_id", "database_fingerprint", "writer_fence", "tool_manifest_hash", "runtime_generation")}}
    value.update(changes)
    return value


def setup(tmp_path, execute):
    current = host(tmp_path, Session(), execute)
    target = DshDomainTarget("dsh-one", "conversation-1", current)
    return current, NativeDshDomainCalls(lambda sid: target if sid == target.dsh_session_id else None)


def test_true_ledger_settlement_and_explicit_lost_ack_readback(tmp_path):
    calls = []
    current, dispatcher = setup(tmp_path, lambda *_: calls.append(1) or {"code": 0, "data": {"value": 7}})
    bind = dispatcher("dsh_app_bind_request", {"contract": "genshin-native-dsh-domain-bind-v1",
        "request_id": "bind-one", "session_id": "dsh-one"})
    assert bind["contract"] == "genshin-native-dsh-domain-bound-v1"
    assert bind["snapshot"] == current.current_snapshot()
    original = request(current)
    first = dispatcher("dsh_app_call_request", original)
    assert first["status"] == "settled_success"
    assert first["result"] == {"code": 0, "data": {"value": 7}}
    saved = current.ledger.read()["entries"]["call-one"]
    assert saved["identity"]["turn_id"] == "dsh-turn-1"
    assert saved["identity"]["step_id"] == "dsh-step-1"
    assert saved["identity"]["epoch_id"] == saved["identity"]["writer_fence"] == "fence-1"
    assert json.loads(saved["result_json"]) == first["result"]
    second = dispatcher("dsh_app_call_request", {**original, "request_id": "explicit-readback"})
    assert second["status"] == "settled_success" and second["result"] == first["result"]
    assert calls == [1]
    next_turn = dispatcher("dsh_app_call_request", request(current, turn=2, call_id="call-two", call_seq=9))
    assert next_turn["status"] == "settled_success" and calls == [1, 1]


def test_unknown_effect_uses_durable_unknown_not_fabricated_success(tmp_path):
    def corrupt_read(session, *_):
        session.candidate["value"] = 2
        return {"code": 0}

    current, dispatcher = setup(tmp_path, corrupt_read)
    result = dispatcher("dsh_app_call_request", request(current))
    assert result["status"] == "unknown_after_interruption"
    assert result["result"]["code"] == 1
    assert current.ledger.read()["entries"]["call-one"]["state"] == "unknown_after_interruption"


def test_stale_snapshot_is_unsettled_rejection_without_tool_execution(tmp_path):
    current, dispatcher = setup(tmp_path, lambda *_: pytest.fail("must reject first"))
    with pytest.raises(NativeDshMuxError, match="stale_tool_snapshot") as failure:
        dispatcher("dsh_app_call_request", request(current, candidate_revision=999))
    assert failure.value.outcome_unknown is False
    assert current.ledger.read()["entries"] == {}


def test_unbound_session_cannot_reuse_other_domain_snapshot(tmp_path):
    current, dispatcher = setup(tmp_path, lambda *_: pytest.fail("must not execute"))
    with pytest.raises(NativeDshMuxError, match="session_unbound"):
        dispatcher("dsh_app_call_request", request(current, session_id="dsh-other"))
    assert current.ledger.read()["entries"] == {}


def test_arguments_are_validated_as_the_exact_node_serialized_string(tmp_path):
    current, dispatcher = setup(tmp_path, lambda *_: pytest.fail("duplicate fields must fail"))
    raw = '{"resource":"workflow_stage","resource":"candidate"}'
    with pytest.raises(NativeDshMuxError, match="duplicate_json_key") as failure:
        dispatcher("dsh_app_call_request", request(current, arguments_json=raw, raw_arguments_hash=raw_json_hash(raw)))
    assert failure.value.outcome_unknown is False


def test_bind_snapshot_and_metadata_share_lease_against_gui_edits():
    candidate_lock = threading.Lock()
    snapshot_read, edit_attempt, edit_finished = threading.Event(), threading.Event(), threading.Event()
    state = {"revision": 0}
    calls = []

    @contextmanager
    def boundary(tool):
        assert tool == "gs_read_context"
        with candidate_lock:
            calls.append("lease")
            yield

    class Host:
        @staticmethod
        def current_snapshot():
            snapshot_read.set()
            assert edit_attempt.wait(1)
            return {"candidate_revision": state["revision"]}

    def metadata():
        assert not edit_finished.is_set()
        return {"runtime_context": {"candidate_revision": state["revision"]}, "prompt_bundle": {}}

    def gui_edit():
        assert snapshot_read.wait(1)
        edit_attempt.set()
        with candidate_lock:
            state["revision"] = 1
            edit_finished.set()

    dispatcher = NativeDshDomainCalls(lambda _sid: DshDomainTarget(
        "one", "conversation-1", Host(), model_metadata_reader=metadata, tool_boundary=boundary))
    editor = threading.Thread(target=gui_edit)
    editor.start()
    try:
        result = dispatcher("dsh_app_bind_request", {"contract": "genshin-native-dsh-domain-bind-v1",
            "request_id": str(uuid.uuid4()), "session_id": "one"})
        assert result["snapshot"]["candidate_revision"] == result["runtime_context"]["candidate_revision"] == 0
        assert calls == ["lease"]
    finally:
        editor.join(2)
    assert edit_finished.is_set()


def test_bind_program_fault_is_diagnosable_without_mutation_unknown():
    from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
    def broken():
        raise ModuleNotFoundError("domain metadata module missing")
    request = {"contract": "genshin-native-dsh-domain-bind-v1", "request_id": str(uuid.uuid4()), "session_id": "one"}
    dispatcher = NativeDshDomainCalls(lambda _sid: DshDomainTarget("one", "conversation",
        type("Host", (), {"current_snapshot": lambda _self: {}})(), model_metadata_reader=broken))
    with pytest.raises(NativeDshMuxError) as caught:
        dispatcher("dsh_app_bind_request", request)
    assert caught.value.code == "app_component_unavailable"
    assert caught.value.outcome_unknown is False
    assert caught.value.failure["details"]["system_fault"]["component"] == "app_domain_binding"
    assert caught.value.failure["details"]["recovery"]["action"] == "report_system_fault"


def test_bind_busy_before_lease_defers_but_metadata_failure_does_not():
    @contextmanager
    def busy(_tool):
        raise NativeAppActionControllerError("app_action_serial_gate_busy")
        yield  # pragma: no cover - context manager shape

    request = {"contract": "genshin-native-dsh-domain-bind-v1", "request_id": str(uuid.uuid4()), "session_id": "one"}
    dispatcher = NativeDshDomainCalls(lambda _sid: DshDomainTarget("one", "conversation", None, tool_boundary=busy))
    with pytest.raises(AppCallDeferred):
        dispatcher("dsh_app_bind_request", request)

    def bad_metadata():
        raise NativeAppActionControllerError("app_action_serial_gate_busy")

    dispatcher = NativeDshDomainCalls(lambda _sid: DshDomainTarget("one", "conversation",
        type("Host", (), {"current_snapshot": lambda _self: {}})(), model_metadata_reader=bad_metadata))
    with pytest.raises(NativeAppActionControllerError):
        dispatcher("dsh_app_bind_request", request)
