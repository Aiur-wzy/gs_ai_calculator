"""Independent transport/manager race and quota review. Never calls a model."""
from __future__ import annotations

import threading

import pytest

from genshin_app.agent_chat import transport as transport_module
from genshin_app.agent_chat.manager import AgentManager
from genshin_app.agent_chat.transport import CodexTransport, TransportError
from genshin_app.equipment.schema import PlanError
from tests.test_agent_manager import Candidate, Transport


def fresh(tmp_path, factory=Transport):
    Transport.quota = 30
    value = AgentManager(tmp_path, transport_factory=factory, candidate_factory=Candidate)
    value.detect = lambda: {"candidates": [{"id": "cli", "supported": True}]}
    value.connect("cli")
    return value


def active(value):
    session = {"session_id": "a" * 32, "run_id": "run", "thread_id": "thread", "turn_id": "turn",
               "seq": 0, "events": [], "messages": [], "state": "running", "candidate_summary": {}}
    value._sessions[session["session_id"]] = session
    value._active_id = session["session_id"]
    value._quota()
    return session


def tool_event(**changes):
    return {"id": 21, "method": "item/tool/call", "params": {
        "threadId": "thread", "turnId": "turn", "callId": "call", "tool": "gs_calculate",
        "arguments": {}, **changes}}


@pytest.mark.parametrize("key", ["threadId", "turnId", "callId"])
@pytest.mark.parametrize("value", [None, "", "  "])
def test_missing_tool_binding_never_reaches_candidate(tmp_path, key, value):
    manager = fresh(tmp_path)
    session = active(manager)
    candidate = Candidate("x", tmp_path, None, "native", {})
    with pytest.raises(PlanError) as error:
        manager._handle(session, candidate, tool_event(**{key: value}))
    assert error.value.reason == "agent_permission"
    assert not candidate.calls and manager._transport.rejections == [21]
    manager.shutdown()


@pytest.mark.parametrize("key", ["threadId", "turnId"])
def test_foreign_tool_binding_stops_instead_of_silently_running(tmp_path, key):
    manager = fresh(tmp_path)
    session = active(manager)
    candidate = Candidate("x", tmp_path, None, "native", {})
    with pytest.raises(PlanError) as error:
        manager._handle(session, candidate, tool_event(**{key: "foreign"}))
    assert error.value.reason == "agent_permission" and not candidate.calls
    manager.shutdown()


def test_quota_drop_inside_slow_local_tool_is_display_only(tmp_path):
    manager = fresh(tmp_path)
    session = active(manager)
    owned = manager._transport
    candidate = Candidate("x", tmp_path, None, "native", {})

    def calculate(name, args):
        # The notification arrives while App calculation is occupying _handle.
        owned.emit({"method": "account/rateLimits/updated", "params": {
            "rateLimits": {"primary": {"usedPercent": 95}, "secondary": None}}})
        assert not manager._stop.is_set()
        return {"code": 0, "data": {"calculated": True}}

    candidate.call_tool = calculate
    assert manager._handle(session, candidate, tool_event()) is None
    assert owned.replies and owned.replies[0][0] == 21
    manager.shutdown()
    assert owned.closed


def test_sparse_null_metadata_preserves_valid_windows(tmp_path):
    manager = fresh(tmp_path)
    session = active(manager)
    manager._quota_snapshot = {"rateLimitsByLimitId": {"codex": {
        "primary": {"usedPercent": 30}, "secondary": {"usedPercent": 40}}}}
    event = {"method": "account/rateLimits/updated", "params": {"rateLimits": {
        "limitId": "codex", "primary": None, "secondary": None, "planType": None,
        "credits": {"hasCredits": True}}}}
    manager._transport.emit(event)
    assert not manager._stop.is_set()
    assert manager._quota_snapshot["rateLimitsByLimitId"]["codex"]["secondary"]["usedPercent"] == 40
    assert manager._handle(session, None, event) is None
    manager.shutdown()


def test_sparse_update_preserves_secondary_without_stopping(tmp_path):
    manager = fresh(tmp_path)
    active(manager)
    manager._quota_snapshot = {"rateLimitsByLimitId": {"codex": {
        "primary": {"usedPercent": 30}, "secondary": {"usedPercent": 96}}}}
    manager._transport.emit({"method": "account/rateLimits/updated", "params": {
        "rateLimits": {"primary": {"usedPercent": 20}, "secondary": None}}})
    assert not manager._stop.is_set()
    assert manager._quota_snapshot["rateLimitsByLimitId"]["codex"]["secondary"]["usedPercent"] == 96
    assert manager._connection["limits"]["remaining_percent"] == 4
    manager.shutdown()


def test_stale_connection_events_do_not_poison_new_connection(tmp_path):
    manager = fresh(tmp_path)
    old = manager._transport
    manager.connect("cli")
    active(manager)
    old.emit({"_transport_error": "old_eof"})
    old.emit({"method": "account/rateLimits/updated", "params": {"rateLimits": {"primary": {"usedPercent": 100}}}})
    assert manager._events.empty() and not manager._stop.is_set()
    manager.shutdown()


def test_stop_closes_even_when_interrupt_never_answers(tmp_path, monkeypatch):
    from genshin_app.agent_chat import manager as module
    monkeypatch.setattr(module, "STOP_GRACE_SECONDS", .01)
    manager = fresh(tmp_path)
    session = active(manager)
    owned = manager._transport
    release = threading.Event()
    closed = threading.Event()
    owned.interrupt = lambda *args: release.wait(2)
    owned.close = closed.set
    try:
        manager.stop(session["session_id"])
        assert closed.wait(.5)
    finally:
        release.set()
        manager.shutdown()


def test_shutdown_can_cancel_connection_handshake_without_waiting_for_it(tmp_path):
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()

    class BlockingTransport(Transport):
        def connect(self):
            entered.set()
            release.wait(2)
            return super().connect()

        def close(self):
            super().close()
            closed.set()

    manager = AgentManager(tmp_path, transport_factory=BlockingTransport, candidate_factory=Candidate)
    manager.detect = lambda: {"candidates": [{"id": "cli", "supported": True}]}
    errors = []

    def connect():
        try:
            manager.connect("cli")
        except PlanError as exc:
            errors.append(exc.reason)

    worker = threading.Thread(target=connect)
    worker.start()
    assert entered.wait(1)
    try:
        manager.shutdown()
        assert closed.is_set()
    finally:
        release.set()
        worker.join(2)
    assert errors == ["agent_connection_cancelled"]
    assert manager._transport is None and not manager._connecting


def test_unknown_normalized_quota_remains_informational(tmp_path):
    class UnknownQuota(Transport):
        def connect(self):
            result = super().connect()
            result["limits"] = {"remaining_percent": None, "known": False}
            return result

    manager = fresh(tmp_path, UnknownQuota)
    assert manager._connection["limits"]["allowed"] is True
    assert manager._connection["limits"]["managed_by_user"] is True
    manager.shutdown()


def test_close_during_executable_validation_cannot_publish_late_process(tmp_path, monkeypatch):
    client = CodexTransport({"path": str(tmp_path / "codex.exe"), "version": transport_module.SUPPORTED_VERSION,
                             "sha256": transport_module.SUPPORTED_SHA256}, tmp_path, lambda _: None)
    monkeypatch.setattr(transport_module, "_native_executable", lambda path: True)

    def digest(path):
        client.close()
        return transport_module.SUPPORTED_SHA256

    monkeypatch.setattr(transport_module, "_digest", digest)
    monkeypatch.setattr(transport_module, "_OwnedWindowsProcess", lambda *args: pytest.fail("cancelled connect must not spawn"))
    with pytest.raises(TransportError) as error:
        client.connect()
    assert error.value.code == "connection_closed"


def test_detection_never_executes_unverified_native_file(tmp_path, monkeypatch):
    from genshin_app.agent_chat import discovery

    path = tmp_path / "codex.exe"
    path.write_bytes(b"MZnot-a-verified-cli")
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(discovery, "_common_directories", lambda env: [])
    monkeypatch.setattr(discovery._Scanner, "expand_known_directories", lambda self: None)
    monkeypatch.setattr(transport_module.subprocess, "run", lambda *args, **kwargs: pytest.fail("unknown binary executed"))
    monkeypatch.setattr(transport_module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unknown process spawned"))
    result = transport_module.detect_cli()
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["version"] is None and not result["candidates"][0]["supported"]
