"""CLI checkpoint helpers; native recovery belongs to the DSH shell tests."""
from __future__ import annotations

import copy

from genshin_app.agent_chat.session_lifecycle import (
    acknowledge_recovery,
    activation_summary,
    append_request_epoch,
    begin_tool,
    mark_resume,
    repair_interrupted,
    request_reason,
    settle_tool,
)


def _session():
    return {
        "session_id": "a" * 32,
        "provider_id": "cli",
        "connection_kind": "cli",
        "model": "test-model",
        "effort": "high",
        "workflow": "staged",
        "messages": [{"message_id": "m1", "role": "user", "message": "开始"}],
        "run_id": "run-1",
    }


def _tools():
    return [{"type": "function", "name": "gs_read_context",
             "description": "read", "inputSchema": {"type": "object"}}]


def test_request_epochs_append_without_mutating_prior_rows():
    session = _session()
    summary = {"revision": 1, "workflow_stage": "loadout_draft", "stage_revision": 2}
    first = append_request_epoch(
        session, reason="initial", candidate_summary=summary,
        tools=_tools(), instructions="instructions")
    frozen = copy.deepcopy(session["request_epochs"][0])
    assert request_reason(session, summary, _tools(), "instructions") == "series"
    mark_resume(session)
    assert request_reason(session, summary, _tools(), "instructions") == "resume"
    second = append_request_epoch(
        session, reason="resume", candidate_summary=summary,
        tools=_tools(), instructions="instructions")
    assert session["request_epochs"][0] == frozen == first
    assert second["epoch"] == 2
    assert activation_summary(session)["last_request_reason"] == "resume"


def test_unknown_tool_outcome_becomes_recovery_notice_without_retry():
    session = _session()
    begin_tool(
        session, tool_id="call-1", name="gs_patch_candidate",
        candidate_summary={"revision": 3, "workflow_stage": "event_topology"})
    notice = repair_interrupted(session, turn_interrupted=True)
    assert notice["code"] == "agent_tool_outcome_unknown"
    assert session["inflight_tool"] is None
    assert session["resume_pending"] is True
    assert repair_interrupted(session) is None


def test_recovery_notice_stays_until_a_successful_turn_acknowledges_it():
    session = _session()
    notice = repair_interrupted(session, turn_interrupted=True)
    assert activation_summary(session)["recovery"]["marker"] == notice["marker"]
    append_request_epoch(
        session, reason="resume", candidate_summary={"revision": 1},
        tools=_tools(), instructions="instructions")
    assert activation_summary(session)["recovery"] is not None
    closed = acknowledge_recovery(session)
    assert closed["acknowledged_epoch"] == 1
    assert closed["acknowledged_run_id"] == "run-1"
    assert activation_summary(session)["recovery"] is None


def test_successful_turn_acknowledges_the_whole_pending_recovery_chain():
    session = _session()
    append_request_epoch(
        session, reason="initial", candidate_summary={"revision": 1},
        tools=_tools(), instructions="instructions")
    repair_interrupted(session, turn_interrupted=True)
    session["run_id"] = "run-2"
    repair_interrupted(session, turn_interrupted=True)

    assert len([row for row in session["recovery_notices"]
                if row["acknowledged_at"] is None]) == 2

    session["run_id"] = "run-3"
    closed = acknowledge_recovery(session)

    assert closed["marker"].startswith("run-2:")
    assert activation_summary(session)["recovery"] is None
    assert all(row["acknowledged_run_id"] == "run-3"
               for row in session["recovery_notices"])


def test_settled_tool_has_bounded_result_fingerprint():
    session = _session()
    begin_tool(
        session, tool_id="call-1", name="gs_read_context",
        candidate_summary={"revision": 1})
    row = settle_tool(
        session, tool_id="call-1", result={"code": 0, "data": {"ok": True}},
        candidate_summary={"revision": 1})
    assert row["result_code"] == 0 and len(row["result_hash"]) == 64
    assert session["inflight_tool"] is None


# Native owner cold-resume execution is retired. Read-only historical records
# and rejected native send/resume are covered in test_agent_cli_only_boundary.
