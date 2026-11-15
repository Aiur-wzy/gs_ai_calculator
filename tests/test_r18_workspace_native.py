from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.tools import _query
from genshin_app.task_evidence import record_task_source
from genshin_app.whiteboard import new_whiteboard


@pytest.fixture
def session(tmp_path):
    return ArtifactCandidateSession(uuid.uuid4().hex, tmp_path / "session", tmp_path / "absent.db",
                                    "whiteboard", new_whiteboard())


def test_batch_findings_and_source_survive_reload_without_candidate_revision(session):
    before = copy.deepcopy(session.candidate)
    first = record_task_source(session, "原题是两轮且动态附着。", "dsh:one:m1")
    duplicate = record_task_source(session, "原题是两轮且动态附着。", "dsh:one:m1")
    assert first["id"] == duplicate["id"] and duplicate["already_recorded"]
    reply = session.call_tool("gs_record_finding", {"records": [
        {"id": "a", "type": "requirement", "description": "两轮", "disposition": "adopted"},
        {"id": "b", "type": "requirement", "description": "动态冰底", "disposition": "pending"}]})
    assert reply["code"] == 0, reply
    assert session.candidate == before
    restored = CandidateSession.load(session.session_id, session.root, session.db_path)
    assert len(restored.findings) == 3
    summary = restored.summary()["task_review"]
    assert summary["task_id"] == session.session_id
    assert [r["id"] for r in summary["differences"]] == ["b"]
    assert restored.call_tool("gs_read_context", {"resource": "task_evidence"})["code"] == 0


def test_query_notebook_is_readable_and_lossless_without_another_database_read(session):
    raw = {"nested": {"refinements": [{"description": "释放后生效；{LINK#7}", "value": 14}]}}
    reply = _query(session, {"request": {}}, lambda *_: raw)
    assert reply["notebook_ref"] == {"resource": "source-notebook:" + reply["resource"]}
    result = session.call_tool("gs_read_context", {**reply["notebook_ref"], "pointer": "/raw/nested/refinements/0/description"})
    assert result["code"] == 0, result
    assert "{LINK#7}" in str(result["data"])


def test_batch_rejected_atomically_and_exact_xor_recovery(session):
    before = len(session.findings)
    reply = session.call_tool("gs_record_finding", {"record": {"id": "x", "type": "issue", "description": "x"}, "records": [{"id": "y", "type": "issue", "description": "y"}]})
    assert reply["code"] == 1 and "只能提供一个" in reply["message"]
    reply = session.call_tool("gs_record_finding", {"records": [
        {"id": "x", "type": "issue", "description": "x"},
        {"id": "x", "type": "issue", "description": "duplicate"}]})
    assert reply["code"] == 1 and len(session.findings) == before
