from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from genshin_app.agent_chat.native_dsh_domain_calls import (
    DshDomainTarget,
    NativeDshDomainCalls,
)
from genshin_app.agent_chat.native_task_source import capture_task_source
from genshin_app.equipment.files import fingerprint
from genshin_engine.execution_control import ExecutionCancelled
from tests.test_native_app_tool_host import Session, host


def session_fixture(tmp_path):
    session = Session()
    session.session_id = "candidate-1"
    session.candidate = {"board_id": "board-1", "team": [], "actions": []}
    session._document_fingerprint = fingerprint
    session.save = lambda: (tmp_path / "findings.json").write_text(
        json.dumps(session.findings, ensure_ascii=False), encoding="utf-8")
    return session


def source(**changes):
    return {"session_id": "dsh-one", "event_seq": 5, "message_id": "message-one",
            "text": "两轮；每组AE至少扩冰一次。\n不能为了收益修改给定命座。", **changes}


def test_real_source_persists_once_without_candidate_or_permission_change(tmp_path):
    session = session_fixture(tmp_path)
    before = (fingerprint(session.candidate), session.revision, session.provisional_authority.copy())
    first = capture_task_source(session, source(), session_id="dsh-one")
    again = capture_task_source(session, source(), session_id="dsh-one")
    assert first["status"] == again["status"] == "captured"
    saved = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert len(saved) == 1
    assert saved[0]["description"] == source()["text"]
    assert saved[0]["source_refs"][0]["origin"] == "dsh_user_message"
    assert (fingerprint(session.candidate), session.revision, session.provisional_authority) == before


@pytest.mark.parametrize("changes", [
    {"session_id": "other"}, {"event_seq": True}, {"event_seq": -1},
    {"approved": True}, {"message_id": "../fake"}, {"text": ""},
])
def test_malformed_or_foreign_quote_cannot_write_but_is_only_advisory(tmp_path, changes):
    session = session_fixture(tmp_path)
    value = capture_task_source(session, source(**changes), session_id="dsh-one")
    assert value["code"] == "task_source_invalid"
    assert session.findings == []


def test_task_source_capture_does_not_swallow_cancellation(tmp_path):
    session = session_fixture(tmp_path)
    session.save = lambda: (_ for _ in ()).throw(ExecutionCancelled())
    with pytest.raises(ExecutionCancelled):
        capture_task_source(session, source(), session_id="dsh-one")
    assert session.findings == []


def test_bind_uses_serial_note_boundary_and_failure_keeps_domain_available(tmp_path):
    session = session_fixture(tmp_path)
    session.save = lambda: (_ for _ in ()).throw(OSError("disk failure"))
    current = host(tmp_path, session, lambda *_: {"code": 0})
    leases = []

    @contextmanager
    def boundary(tool):
        leases.append(tool)
        yield

    dispatcher = NativeDshDomainCalls(lambda _: DshDomainTarget(
        "dsh-one", "conversation-1", current, tool_boundary=boundary))
    result = dispatcher("dsh_app_bind_request", {
        "contract": "genshin-native-dsh-domain-bind-v1", "request_id": "bind-1",
        "session_id": "dsh-one", "task_source": source()})
    assert leases == ["gs_record_finding"]
    assert result["binding_state"] == "bound"
    assert result["snapshot"]["tools"]
    assert result["runtime_context"]["task_source_capture"]["code"] == "task_source_recording_failed"
    assert session.findings == []
    assert session.revision == 2
