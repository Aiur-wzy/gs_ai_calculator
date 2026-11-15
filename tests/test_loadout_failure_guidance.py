"""Actual loadout failures preserve producer recovery without inventing DB updates."""
from __future__ import annotations

import copy
import json
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.domain_errors import classify_domain_failure
from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.schema import PlanError
from genshin_db import queries
from tests.test_agent_calculation_preflight import state
from tests.test_equipment_panel import database as database  # noqa: PLC0414
from tests.test_equipment_panel import panel_database as panel_database  # noqa: PLC0414
from tests.test_equipment_panel import panel_plan
from tests.test_native_app_tool_host import call, host


def duplicate_plan(db):
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = [{"set_id": 10, "count": 4}]
    report = compose_equipment_plan(plan, db)
    original = report["effect_requirements"][0]["proposed_binding"]
    plan["effect_bindings"] = [{**copy.deepcopy(original), "binding_id": bid}
                               for bid in ("same.owner.one", "same.owner.two")]
    # A separate unresolved source must remain visible after the first actor's
    # duplicate is diagnosed; the first actor stops at the duplicate itself.
    other = copy.deepcopy(plan["manual_template"]["characters"][0])
    other["id"] = "other.actor"
    plan["manual_template"]["characters"].append(other)
    plan["equipment_inputs"]["other.actor"] = copy.deepcopy(plan["equipment_inputs"]["char1"])
    return plan


def test_duplicate_binding_is_an_input_correction_not_database_update(panel_database):
    db, _ = panel_database
    plan = duplicate_plan(db)
    before = copy.deepcopy(plan)
    report = compose_equipment_plan(plan, db)
    issue = next(row for row in report["missing_inputs"] if row["code"] == "invalid_plan")
    assert classify_domain_failure(issue["code"], details=issue)["category"] == "request_error"
    assert issue["path"] == "effect_bindings"
    assert issue["action"] == "correct_arguments"
    assert issue["input_ref"] == "/effect_bindings"
    assert issue["conflicts"] == [
        {"binding_id": "same.owner.one", "input_ref": "/effect_bindings/0"},
        {"binding_id": "same.owner.two", "input_ref": "/effect_bindings/1"},
    ]
    assert "same.owner.one" in issue["repair_hint"] and "same.owner.two" in issue["repair_hint"]
    assert "apply_data_update" not in issue["repair_hint"]
    assert any(row["code"] == "effect_pending" for row in report["missing_inputs"])
    assert plan == before


def test_actual_host_duplicate_preflight_preserves_state_and_report_recovery(tmp_path, panel_database):
    db, _ = panel_database
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", db, "native", duplicate_plan(db))
    before = state(session)
    current = host(tmp_path / "ledger", session, lambda item, name, args: item.call_tool(name, args))
    terminal = call(current, call_id="duplicate-" + uuid.uuid4().hex, tool="gs_calculate", raw='{"expected_revision":0}')
    assert terminal["terminal_state"] == "settled_failure"
    assert terminal["terminal_reason"] == "missing_input"
    assert state(session) == before
    assert terminal["result"]["recovery"]["tool"] == "gs_compose"
    issues = terminal["result"]["details"]["missing_inputs"]
    issue = next(row for row in issues if row["code"] == "invalid_plan")
    assert issue["category"] == "request_error"
    assert [row["input_ref"] for row in issue["conflicts"]] == ["/effect_bindings/0", "/effect_bindings/1"]
    assert issue["recovery"]["action"] == "correct_arguments"
    assert "same.owner.one" in issue["repair_hint"]
    assert "apply_data_update" not in issue["repair_hint"]


def test_real_source_change_retains_explicit_data_refresh(panel_database):
    db, _ = panel_database
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = [{"set_id": 10, "count": 4}]
    plan["equipment_inputs"]["char1"]["data_version"]["upstream_commit"] = "b" * 40
    report = compose_equipment_plan(plan, db)
    issue = next(row for row in report["missing_inputs"] if row["code"] == "source_changed")
    assert issue["action"] == "apply_data_update"


def test_other_typed_error_preserves_own_hint_and_dataset_failure_still_propagates(panel_database, monkeypatch):
    db, _ = panel_database
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = [{"set_id": 10, "count": 4}]
    def reject(*_args, **_kwargs):
        raise PlanError("fixture typed input issue", details={"path": "equipment_inputs.char1.artifact_sets",
            "repair_hint": "inspect_explicit_set_selection"})
    monkeypatch.setattr(queries, "get_artifact_set_detail", reject)
    issue = next(row for row in compose_equipment_plan(plan, db)["missing_inputs"] if row["code"] == "invalid_plan")
    assert issue["repair_hint"] == "inspect_explicit_set_selection"
    assert issue["action"] == "correct_arguments"
    def corrupt(*_args, **_kwargs):
        raise PlanError("fixture corrupted dataset", "dataset_invalid")
    monkeypatch.setattr(queries, "get_artifact_set_detail", corrupt)
    with pytest.raises(PlanError, match="corrupted dataset"):
        compose_equipment_plan(plan, db)


def test_pending_requirement_has_source_navigation_without_automatic_confirmation(tmp_path, panel_database):
    db, _ = panel_database
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = [{"set_id": 10, "count": 4}]
    session = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / "session", db, "native", plan)
    assert session.call_tool("gs_compose", {"expected_revision": 0})["code"] == 0
    issue = next(row for row in session.report["missing_inputs"] if row["code"] == "effect_pending")
    hint = issue["repair_hint"]
    assert "gs_compose" in hint and "/effect_requirements" in hint
    assert "proposed_binding" in hint and "组件source_fingerprint" in hint
    assert "references/app-mechanics/loadouts.md" in hint
    requirement = session.report["effect_requirements"][0]
    response = session.call_tool("gs_read_context", {"resource": "report",
        "pointer": "/effect_requirements/0/proposed_binding", "limit": 50})
    assert response["code"] == 0
    assert requirement["source_fingerprint"] in json.dumps(response)
    assert session.candidate["effect_bindings"] == []
    assert session.report["state"] == "pending"
