"""Rejected calculation input is not an interrupted App state mutation."""
from __future__ import annotations

import copy
import uuid

import pytest

from genshin_app.agent_chat import candidate as candidate_module
from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.agent_chat.board_candidate import BoardCandidateSession
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.native_app_tool_host import (
    _derived_view,
    _durable_candidate_view,
)
from genshin_app.agent_chat.staged_candidate import StagedCandidateSession
from genshin_app.equipment.composer import compose_equipment_plan
from genshin_app.equipment.files import json_bytes
from genshin_app.whiteboard import new_whiteboard
from tests.test_calculation_delivery import delivery_plan
from tests.test_calculation_diagnostics import failed
from tests.test_equipment_plan import binding
from tests.test_native_app_tool_host import call, host


def pending_plan(count=1):
    plan = delivery_plan()
    for index in range(count):
        effect = binding([])
        effect.update(binding_id=f"pending.{index}", provider_character_id="a")
        plan["effect_bindings"].append(effect)
    return plan


def state(session):
    return copy.deepcopy({"derived": _derived_view(session), "durable": _durable_candidate_view(session)})


@pytest.mark.parametrize("session_class", [CandidateSession, ArtifactCandidateSession])
@pytest.mark.parametrize("prior", ["empty", "report", "diagnostic"])
def test_real_missing_input_is_known_failure_without_touching_candidate_state(tmp_path, session_class, prior):
    session = session_class(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", pending_plan())
    if prior == "report":
        session.report = {"state": "pending", "missing_inputs": [{"code": "prior_report", "message": "older explicit compose"}]}
    if prior == "diagnostic":
        session._diagnostic_cache = {"revision": 0, "attempt_id": "old-attempt", "candidate_fingerprint": "a" * 64, "value": failed()}
    before = state(session)
    checkpoint = (session.root / "checkpoint.json").read_bytes()
    current = host(tmp_path / "ledger", session, lambda current, name, arguments: current.call_tool(name, arguments))
    terminal = call(current, call_id="new-preflight-" + uuid.uuid4().hex, tool="gs_calculate", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "settled_failure"
    assert terminal["terminal_reason"] == "missing_input"
    assert terminal["state_changes"] == []
    assert state(session) == before
    assert (session.root / "checkpoint.json").read_bytes() == checkpoint
    failure = terminal["result"]
    assert failure["details"]["missing_inputs"][0]["code"] == "effect_pending"
    assert failure["details"]["missing_inputs_total"] == 1
    assert failure["details"]["report_updated"] is False
    assert failure["contract_ref"] == {"resource": "tool_contracts", "pointer": "/tools/gs_calculate"}
    assert "gs_compose" in failure["repair_hint"]
    assert "effect_requirements" in failure["repair_hint"]
    assert failure["details"]["recovery"]["tool"] == "gs_compose"
    assert failure["details"].get("resource") != "diagnostic_error"


def test_preflight_rejection_preserves_an_existing_real_calculation(tmp_path, monkeypatch):
    session = CandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())
    session.calculate(0)
    before = state(session)
    # An independently composed rejected input is a deterministic preflight
    # fault oracle. The previous result above is a genuine engine receipt.
    pending = compose_equipment_plan(pending_plan(), session.db_path)
    monkeypatch.setattr(candidate_module, "compose_equipment_plan", lambda *_args, **_kwargs: copy.deepcopy(pending))
    current = host(tmp_path / "ledger", session, lambda current, name, arguments: current.call_tool(name, arguments))
    terminal = call(current, call_id="new-recheck-" + uuid.uuid4().hex, tool="gs_calculate", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "settled_failure"
    assert terminal["terminal_reason"] == "missing_input"
    assert terminal["state_changes"] == [] and state(session) == before


def test_large_missing_input_failure_contains_fresh_bounded_issues_and_explicit_compose_recovery(tmp_path):
    session = CandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", pending_plan(80))
    failure = session.call_tool("gs_calculate", {"expected_revision": 0})
    assert failure["reason"] == "missing_input"
    assert session.report is None and session.calculated is None
    details = failure["details"]
    assert details["missing_inputs_total"] == 80
    assert details["missing_inputs_truncated"] is True
    assert details["missing_inputs"][0]["path"] == "effect_bindings.pending.0.coverage"
    assert len(json_bytes(details)) <= 16000
    assert details["recovery"]["tool"] == "gs_compose"
    assert details.get("resource") != "report"
    assert session.call_tool("gs_compose", {"expected_revision": 0})["code"] == 0
    assert len(session.report["missing_inputs"]) == 80
    page = session.call_tool("gs_read_context", {"resource": "report", "pointer": "/missing_inputs", "limit": 1})
    assert page["code"] == 0 and page["data"]["total"] == 80


def test_known_mechanics_diagnostic_remains_a_narrow_settled_failure(tmp_path, monkeypatch):
    session = CandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())

    def fail_engine(*_args, **_kwargs):
        value = failed()
        raise candidate_module.PlanError("explicit mechanics diagnostic", value["reason"], value["details"])

    monkeypatch.setattr(candidate_module, "run_plan_calc", fail_engine)
    current = host(tmp_path / "ledger", session, lambda current, name, arguments: current.call_tool(name, arguments))
    terminal = call(current, call_id="new-diagnostic-" + uuid.uuid4().hex, tool="gs_calculate", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "settled_failure"
    assert terminal["terminal_reason"] == "mechanics_pending"
    assert terminal["result"]["details"]["resource"] == "diagnostic_error"
    assert session._diagnostic_cache is not None and session.calculated is None


def test_real_artifact_diagnostic_rejection_supersedes_old_failure_cache(tmp_path):
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())
    # A numeric-only formula is valid without an action/energy claim. Declare
    # the execution goal explicitly so this still tests a real rejected result.
    session.stage_state["energy_intent"] = "single_window_legal"
    session._diagnostic_cache = {"revision": 0, "attempt_id": "old-attempt", "value": failed()}
    current = host(tmp_path / "ledger", session, lambda current, name, arguments: current.call_tool(name, arguments))
    terminal = call(current, call_id="new-rejected-result-" + uuid.uuid4().hex, tool="gs_calculate", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "settled_failure"
    assert terminal["terminal_reason"] == "agent_calculation_rejected"
    assert session.calculated is not None and session._diagnostic_cache is None
    assert session.calculation_acceptance()["can_accept"] is False


@pytest.mark.parametrize("session_class", [BoardCandidateSession, StagedCandidateSession, ArtifactCandidateSession])
def test_board_or_stage_prerequisite_rejection_does_not_erase_prior_state(tmp_path, session_class):
    session = session_class(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "whiteboard", new_whiteboard())
    session._diagnostic_cache = {"revision": 0, "attempt_id": "older-diagnostic", "value": failed()}
    before = state(session)
    result = session.call_tool("gs_calculate", {"expected_revision": 0})
    assert result["code"] == 1
    assert result["reason"] in {"agent_tool_forbidden", "agent_native_artifact_required"}
    assert state(session) == before


def test_real_publication_failure_is_not_hidden_by_preflight_atomicity(tmp_path, monkeypatch):
    session = CandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())

    def fail_save():
        raise OSError("publication status remains uncertain")

    monkeypatch.setattr(session, "save", fail_save)
    current = host(tmp_path / "ledger", session, lambda current, name, arguments: current.call_tool(name, arguments))
    terminal = call(current, call_id="new-storage-" + uuid.uuid4().hex, tool="gs_calculate", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "unknown_after_interruption"
    assert terminal["terminal_reason"] == "failed_tool_changed_state"
    assert session.calculated is None and session.report is not None


def test_execution_side_effect_and_error_still_triggers_unknown(tmp_path, monkeypatch):
    session = CandidateSession(str(uuid.uuid4()), tmp_path / "session", tmp_path / "absent.db", "native", delivery_plan())

    def incomplete_execution(*_args, **_kwargs):
        session.candidate["manual_template"]["characters"][0]["stats"]["atk"] = 321
        raise OSError("actual side effect then execution failure")

    monkeypatch.setattr(candidate_module, "run_plan_calc", incomplete_execution)
    current = host(tmp_path / "ledger", session, lambda current, name, arguments: current.call_tool(name, arguments))
    terminal = call(current, call_id="new-execution-" + uuid.uuid4().hex, tool="gs_calculate", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "unknown_after_interruption"
    assert terminal["terminal_reason"] == "effect_mask_violation"
    assert "candidate_hash" in terminal["disallowed_state_changes"]
