"""The manager persists safe provider diagnostics and stops without retrying."""
from __future__ import annotations

import json

import pytest

from genshin_app.agent_chat.manager import AgentManager
from tests.test_agent_manager import Candidate, Transport, finish, request


class ErrorTransport(Transport):
    failure_mode = "notification"
    will_retry = False

    def start_turn(self, thread, message, model, effort):
        self.turns += 1
        turn = f"turn-{self.turns}"
        error = {"message": "SECRET TOKEN and C:/private/auth.json",
                 "additionalDetails": "SECRET proxy configuration",
                 "codexErrorInfo": {"responseStreamConnectionFailed": {"httpStatusCode": 403}}}
        if self.failure_mode in {"notification", "both"}:
            self.emit({"method": "error", "params": {"threadId": thread, "turnId": turn,
                       "willRetry": self.will_retry, "error": error}})
        if self.failure_mode in {"terminal", "both", "missing_error", "interrupted"}:
            status = "interrupted" if self.failure_mode == "interrupted" else "failed"
            payload = {"id": turn, "status": status}
            if self.failure_mode != "missing_error":
                payload["error"] = error
            self.emit({"method": "turn/completed", "params": {"threadId": thread, "turn": payload}})
        return turn


@pytest.fixture
def manager(tmp_path):
    ErrorTransport.failure_mode, ErrorTransport.will_retry, Transport.quota = "notification", False, 30
    value = AgentManager(tmp_path, transport_factory=ErrorTransport, candidate_factory=Candidate)
    value.detect = lambda: {"candidates": [{"id": "cli", "supported": True}]}
    value.connect("cli")
    yield value
    value.shutdown()


@pytest.mark.parametrize("mode", ["notification", "terminal", "both"])
def test_provider_failures_show_http_and_hint_and_close_without_raw_details(manager, mode):
    ErrorTransport.failure_mode = mode
    owned = manager._transport
    sid = manager.start(request())["session_id"]
    finish(manager)
    result = manager.poll(sid)
    errors = [event for event in result["events"] if event["type"] == "error"]
    assert result["state"] == "failed" and len(errors) == 1
    assert "HTTP 403" in errors[0]["message"] and errors[0]["hint"] in errors[0]["message"]
    assert errors[0]["provider_error"]["error_type"] == "responseStreamConnectionFailed"
    assert errors[0]["provider_error"]["http_status"] == 403
    assert owned.closed and owned.turns == 1 and manager._transport is None
    saved = (manager._path(sid) / "chat.json").read_text(encoding="utf-8")
    assert "SECRET" not in saved and "private/auth" not in saved
    assert "SECRET" not in json.dumps(result)


def test_provider_will_retry_does_not_authorize_client_retry(manager):
    ErrorTransport.will_retry = True
    owned = manager._transport
    sid = manager.start(request())["session_id"]
    finish(manager)
    errors = [event for event in manager.poll(sid)["events"] if event["type"] == "error"]
    assert errors[0]["provider_error"]["cli_will_retry"] is True
    assert owned.closed and owned.turns == 1 and owned.replies == []
    assert manager.poll(sid)["can_continue"] is False


def test_failed_completion_without_structured_error_has_safe_fallback(manager):
    ErrorTransport.failure_mode = "missing_error"
    sid = manager.start(request())["session_id"]
    finish(manager)
    errors = [event for event in manager.poll(sid)["events"] if event["type"] == "error"]
    assert len(errors) == 1 and errors[0]["provider_error"]["error_type"] == "unknown"
    assert errors[0]["provider_error"]["http_status"] is None
    assert "结构化原因" in errors[0]["message"]


def test_interrupted_completion_does_not_report_provider_failure(manager):
    ErrorTransport.failure_mode = "interrupted"
    owned = manager._transport
    sid = manager.start(request())["session_id"]
    finish(manager)
    view = manager.poll(sid)
    assert view["state"] == "stopped" and owned.closed
    assert not [event for event in view["events"] if event["type"] == "error"]


def test_duplicate_error_notification_and_completion_record_once_per_run(manager):
    session = {"run_id": "run-1", "turn_id": "turn-1", "thread_id": "thread",
               "seq": 0, "events": [], "messages": []}
    error = {"message": "SECRET", "codexErrorInfo": "badRequest"}
    first = {"method": "error", "params": {"threadId": "thread", "turnId": "turn-1", "error": error}}
    final = {"method": "turn/completed", "params": {"threadId": "thread", "turn": {
        "id": "turn-1", "status": "failed", "error": error}}}
    assert manager._handle(session, None, first) == "failed"
    assert manager._handle(session, None, final) == "failed"
    assert len([event for event in session["events"] if event["type"] == "error"]) == 1
    session["run_id"] = "run-2"
    manager._record_provider_error(session, error)
    assert len([event for event in session["events"] if event["type"] == "error"]) == 2


def test_explicit_new_attempt_resets_failure_summary_and_still_requires_connection(manager):
    sid = manager.start(request())["session_id"]
    finish(manager)
    first = manager._sessions[sid]["provider_error"]["run_id"]
    manager.connect("cli")
    manager.send(sid, "明确继续工程测试", "request-new-attempt")
    finish(manager)
    assert manager._sessions[sid]["provider_error"]["run_id"] != first
    assert len([event for event in manager.poll(sid)["events"] if event["type"] == "error"]) == 2
