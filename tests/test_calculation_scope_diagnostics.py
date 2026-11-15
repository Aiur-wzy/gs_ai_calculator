"""Result diagnostics preserve finite scope without weakening qualification."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest

from genshin_app.agent_chat.calculation_qualification import qualify_calculation
from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.equipment.files import fingerprint
from genshin_app.frozen_selfcheck_inputs import build_fixed_inputs
from genshin_app.whiteboard_equipment import _diagnostics
from tests.test_queries import db_path as db_path  # noqa: PLC0414


@pytest.fixture
def completed(tmp_path, db_path):
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        row = dict(connection.execute("SELECT * FROM character LIMIT 1").fetchone())
        row.update(id=90000002, name="第二工程身份")
        connection.execute("INSERT INTO character (" + ",".join(row) + ") VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    build_fixed_inputs(tmp_path, db_path)
    root = tmp_path / "fixed-input-preflight"
    checkpoint = json.loads((root / "checkpoint.json").read_text(encoding="utf-8"))
    session = CandidateSession.load(checkpoint["session_id"], root, db_path)
    assert session.calculation_acceptance()["status"] == "accepted"
    return session


def _calculate_scope(session, scope):
    response = session.call_tool("gs_transition_phase", {"target_phase": "numeric_model",
        "expected_revision": session.revision, "expected_stage_revision": session.stage_state["stage_revision"],
        "reason": "明确工程验收范围", "energy_intent": scope})
    assert response["code"] == 0, response
    return session.call_tool("gs_calculate", {"expected_revision": session.revision})


def _adopt_after_game_input_edit(session):
    # The shared engineering input now exercises a real attribute model. A
    # rotation edit invalidates its context just as it does in production;
    # explicitly re-adopt these unchanged engineering facts before testing
    # the intended downstream energy/action rejection.
    response = session.call_tool("gs_adopt_attributes", {
        "expected_revision": session.revision,
        "note": "核对游戏输入修改后相同工程属性基底与来源，明确采用当前上下文",
    })
    assert response["code"] == 0, response


@pytest.mark.parametrize("scope", ["single_window_legal", "multi_round_legal"])
def test_actual_two_bursts_are_accepted_in_declared_finite_window(completed, scope):
    assert _calculate_scope(completed, scope)["code"] == 0
    value = completed.calculated
    check = value["result"]["rotation_check"]
    assert check["energy_check"]["status"] == "unproven"
    assert check["energy_check"]["opening_status"] == "passed"
    assert check["cycles"][0]["burst_action_count"] == 2
    assert check["cycles"][0]["burst_energy_spend_count"] == 2
    assert check["damage_interpretation"]["complete_first_cycle"] is True
    before = fingerprint(value)
    acceptance = completed.calculation_acceptance()
    summary = completed.summary()
    assert acceptance["status"] == "accepted" and summary["ready"] is True
    scoped = [row for row in summary["issues"] if row["code"] in {"calculation_energy_check", "calculation_mechanics"}]
    assert len(scoped) == 2
    assert all(row["required"] is False and row["consumed"] is False and row["blocking"] is False for row in scoped)
    assert all(row["severity"] == "info" and row["impact"] == "informational" for row in scoped)
    assert "calculation_coverage" in acceptance
    assert fingerprint(value) == before


def test_second_burst_energy_failure_is_still_blocking(completed):
    event = copy.deepcopy(completed.candidate["rotation_model"]["events"][1])
    event["amounts"]["a"] = 0
    index = next(index for index, row in enumerate(completed.candidate["extensions"]["event_topology_bindings"]["bindings"])
                 if row["node_id"] == "resource.restore")
    response = completed.call_tool("gs_patch_candidate", {"expected_revision": completed.revision, "reason": "真实减少回能",
        "operations": [{"op": "replace", "path": "/rotation_model/events/1", "value": event},
                       {"op": "replace", "path": f"/extensions/event_topology_bindings/bindings/{index}/target_fingerprint",
                        "value": fingerprint(event)}]})
    assert response["code"] == 0, response
    _adopt_after_game_input_edit(completed)
    rejected = _calculate_scope(completed, "multi_round_legal")
    assert rejected["code"] == 1 and rejected["reason"] == "agent_calculation_rejected"
    check = completed.calculated["result"]["rotation_check"]
    assert check["energy_check"]["opening_status"] == "failed"
    assert check["damage_interpretation"]["kind"] == "executed_prefix"
    assert completed.calculation_acceptance()["status"] == "rejected"
    feedback = rejected["details"]["execution_feedback"]
    assert feedback["execution"] == "diagnostic" and feedback["complete_input_window"] is False
    assert feedback["failure_summary"]["first_rejected_event"]["deficit"] > 0
    recovery = rejected["details"]["recovery"]
    assert recovery["tool"] == "gs_diagnose_result" and recovery["request"]["section"] == "energy"
    diagnosed = completed.call_tool(recovery["tool"], {
        "expected_revision": recovery["expected_revision"], "request": recovery["request"]})
    assert diagnosed["code"] == 0, diagnosed
    assert completed.stage_context()["current_problem"] == recovery
    energy = next(row for row in completed.summary()["issues"] if row["code"] == "calculation_energy_check")
    assert energy["required"] is True and energy["blocking"] is True


def test_first_action_rejection_is_not_hidden_by_finite_scope(completed):
    from genshin_engine.mechanics import execute_mechanics
    response = completed.call_tool("gs_patch_candidate", {"expected_revision": completed.revision, "reason": "工程前台冲突",
        "operations": [{"op": "replace", "path": "/rotation_model/initial_frontline", "value": "b"}]})
    assert response["code"] == 0, response
    _adopt_after_game_input_edit(completed)
    rejected = _calculate_scope(completed, "single_window_legal")
    assert rejected["code"] == 1 and rejected["reason"] == "mechanics_pending"
    assert completed.calculated is None and completed.summary()["ready"] is False
    # No damage prefix means the App correctly refuses to mint a calculated
    # artifact. Inspect the same real engine rejection for the projection.
    executed = execute_mechanics(completed.report["plan"]["resolved_template"],
        completed.candidate["rotation_model"], completed.candidate["mechanics_model"])
    assert executed["rotation_check"]["action_check"]["status"] == "failed"
    value = {"result": {**(executed.get("result") or {}), "rotation_check": executed["rotation_check"],
                        "mechanics_status": executed["status"], "mechanics_issues": executed["issues"]}}
    action = next(row for row in _diagnostics(value, energy_intent="single_window_legal") if row["code"] == "calculation_action_check")
    assert action["required"] is True and action["blocking"] is True


@pytest.mark.parametrize("change", ["prefix", "incomplete", "conflicting_completeness", "failed_later"])
def test_unproven_status_alone_never_softens_an_incomplete_producer(completed, change):
    value = copy.deepcopy(completed.calculated)
    result, check = value["result"], value["result"]["rotation_check"]
    if change == "prefix": check["damage_interpretation"]["kind"] = "executed_prefix"
    elif change == "incomplete":
        check["damage_interpretation"]["complete_first_cycle"] = False
        result["mechanics"]["complete_first_cycle"] = False
    elif change == "conflicting_completeness":
        check["damage_interpretation"]["complete_first_cycle"] = False
    else: result["mechanics"]["execution_status"] = "failed"
    rows = _diagnostics(value, energy_intent="multi_round_legal")
    assert all(row["blocking"] for row in rows if row["code"] in {"calculation_energy_check", "calculation_mechanics"})


def test_actual_unknown_condition_remains_blocking(completed):
    from tests.test_mechanics_execution import add, condition, fixture, rule, run
    template, rotation, model = fixture((.2,))
    condition(model, "unknown", .1, 2)
    model["condition_events"][0]["value"] = None
    add(model, rule(trigger={"event": "condition", "phase": "pre", "event_ids": ["unknown"]}))
    executed = run(template, rotation, model)
    assert executed["status"] == "pending"
    value = copy.deepcopy(completed.calculated)
    value["result"].update(mechanics_status=executed["status"], mechanics_issues=executed["issues"])
    rows = _diagnostics(value, energy_intent="single_window_legal")
    unknown = [row for row in rows if row["code"] == "unknown_condition"]
    assert unknown and all(row["blocking"] is True and row["required"] is True for row in unknown)
    acceptance = qualify_calculation(value, energy_intent="single_window_legal",
        topology_receipt=completed.topology_receipt(), topology_binding_receipt=completed.topology_binding_receipt())
    assert acceptance["status"] == "rejected"
    assert "generic_mechanics_validation_failed" in {row["code"] for row in acceptance["issues"]}


def test_warning_is_preserved_without_becoming_a_required_repair(completed):
    value = copy.deepcopy(completed.calculated)
    value["result"]["mechanics_issues"] = [{"code": "engineering_warning", "message": "需查看的明确局限", "severity": "warning"}]
    rows = _diagnostics(value, energy_intent="single_window_legal")
    warning = next(row for row in rows if row["code"] == "engineering_warning")
    assert warning["message"] == "需查看的明确局限" and warning["severity"] == "warning"
    assert warning["blocking"] is False and warning["required"] is False


@pytest.mark.parametrize("raw", [{"code": "code_only", "blocking": True}, {"code": "error_only", "severity": "error"},
                                  {"code": "pending_only", "severity": "pending"}])
def test_code_only_engine_blockers_are_not_silently_omitted(completed, raw):
    value = copy.deepcopy(completed.calculated)
    value["result"]["mechanics_issues"] = [raw]
    rows = _diagnostics(value, energy_intent="single_window_legal")
    blocker = next(row for row in rows if row["code"] == raw["code"])
    assert blocker["required"] is True and blocker["blocking"] is True
    assert blocker["message"] == raw["code"]
    assert all(row["blocking"] for row in rows if row["code"] == "calculation_mechanics")


def test_non_boolean_completeness_does_not_grant_finite_scope(completed):
    value = copy.deepcopy(completed.calculated)
    value["result"]["rotation_check"]["damage_interpretation"]["complete_first_cycle"] = 1
    value["result"]["mechanics"]["complete_first_cycle"] = 1
    energy = next(row for row in _diagnostics(value, energy_intent="single_window_legal") if row["code"] == "calculation_energy_check")
    assert energy["blocking"] is True


@pytest.mark.parametrize("scope", [None, "sustainable_loop"])
def test_unknown_or_sustainable_scope_does_not_infer_a_finite_exemption(completed, scope):
    energy = next(row for row in _diagnostics(completed.calculated, energy_intent=scope) if row["code"] == "calculation_energy_check")
    assert energy["blocking"] is True
