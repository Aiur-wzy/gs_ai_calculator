"""Fresh full-source target projection integrated with native equipment plans."""
from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from test_fixed_source_plan import native

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.schema import PlanError
from genshin_app.formula_workbook import _Projection
from genshin_engine.runtime_target_state_compiler import target_state_compiler_caps


@pytest.fixture
def target_plan(tmp_path, monkeypatch):
    from genshin_db import queries, runtime_queries
    plan, _ = native(tmp_path)
    db = tmp_path / "test.db"
    sqlite3.connect(db).close()
    bundle = json.loads((Path(__file__).parent / "fixtures/runtime_target_state_blizzard_current.json").read_text(encoding="utf8"))
    version = {"version_line": "release", "upstream_commit": bundle["source"]["upstream_commit"],
               "equipment_schema_version": 1, "built_at": "test-build"}
    monkeypatch.setattr(queries, "get_meta", lambda *a, **k: {"datasets": {"equipment": {"lines": {
        "release": {"state": "ready", "upstream_commit": version["upstream_commit"], "schema_version": 1, "built_at": "test-build"}}}}})
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda *a, **k: {
        "artifact_set": {"name": "冰风迷途的勇士"}, "effects": [{"required_count": 4,
        "effect": {"affix_id": 2140011, "name": "真实上游四件套", "source_refs": [{"kind": "database"}]}}],
        "annotations": []})
    monkeypatch.setattr(runtime_queries, "assemble_runtime_source", lambda *a: copy.deepcopy(bundle))
    # Fixed sources must not require a Q identity or even a database character binding.
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **k: pytest.fail("fixed sources do not use Q identities"))
    plan["mechanics_model"].pop("fixed_effect_sources")
    plan["effect_bindings"] = []
    plan["equipment_inputs"] = {"a": {"mode": "panel", "panel_policy": "static_included", "character_ref": None,
        "weapon": None, "artifact_sets": [{"set_id": 14001, "count": 4}],
        "source_refs": [{"kind": "manual", "note": "工程给定面板，含常驻、不含条件暴率"}], "data_version": version}}
    req = compose_equipment_plan(plan, db)["effect_requirements"][0]
    binding = copy.deepcopy(req["proposed_binding"])
    binding["parts"] = []
    scenario = target_state_compiler_caps()["default_scenario"]
    scenario.update(status="assumed", source="受控单人固定四件装备；普通可冻目标；经典命中条件暴率不含于面板")
    binding["upstream_recipe"] = {"schema_version": 1, "scenario": scenario}
    plan["effect_bindings"] = [binding]
    return plan, db, bundle


def accept(plan, db):
    report = compose_equipment_plan(plan, db)
    compiled = report["upstream_compilations"][0]
    plan["effect_bindings"][0]["upstream_recipe"]["scenario"].update(compiled["required_confirmation"])
    return report


def test_full_bundle_partial_projection_persists_recipe_and_recomputes_without_q(target_plan, tmp_path):
    plan, db, _ = target_plan
    first = accept(plan, db)
    assert first["state"] == "pending"
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == before
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 54, 63])
    compiled = out["report"]["upstream_compilations"][0]
    assert compiled["whole_source_status"] == "partial" and compiled["projection_status"] == "ready"
    assert compiled["coverage_certificate"]["source_complete"] is False
    assert len(compiled["branches"]) == 4
    assert out["plan"]["effect_bindings"][0]["parts"] == []
    assert "fixed_effect_sources" not in out["plan"]["mechanics_model"]
    assert not out["report"]["mechanics_model"]["initial_state"]["rules"]
    assert out["equipment_summary"]["characters"][0]["effect_statuses"][0]["whole_source_status"] == "partial"
    assert out["result_receipt"]["upstream_sources"][0]["coverage_certificate"]["source_complete"] is False
    from genshin_app.cli import _calculation_output_summary
    summary = _calculation_output_summary(out)
    assert summary["target_conditions"]["source_coverage"] == "partial"
    assert summary["target_conditions"]["partial_source_count"] == 1
    assert "branches" not in summary["target_conditions"]
    with ExecutionVerificationSession() as session:
        assert _Projection(out, _session=session).finish().summary.rows[5][1].value == pytest.approx(162)
    saved = tmp_path / "native.plan.json"
    saved.write_text(json.dumps(out["plan"]), encoding="utf8")
    assert run_plan_calc(read_plan(saved)["plan"], db)["result"] == out["result"]


@pytest.mark.parametrize("mutation", ["pending", "lunar", "stellar", "wrong-policy", "q-refs", "reference-unaccepted", "freezable", "owner"])
def test_target_scope_changes_never_publish_partial_answer(target_plan, mutation):
    plan, db, _ = target_plan
    accept(plan, db)
    scenario = plan["effect_bindings"][0]["upstream_recipe"]["scenario"]
    if mutation == "pending":
        scenario["status"] = "pending"
        accept(plan, db)
    elif mutation in ("lunar", "stellar"):
        plan["manual_template"]["hits"][0]["damage_system"] = mutation
    elif mutation == "wrong-policy":
        scenario["conditional_crit_policy"] = "included"
    elif mutation == "q-refs":
        plan["effect_bindings"][0]["upstream_recipe"]["ability_refs"] = [{"ability_id": "fake", "skill_id": 1}]
    elif mutation == "reference-unaccepted":
        plan["mechanics_model"]["elemental_model"]["reference_acceptance"]["acknowledged"] = False
    elif mutation == "freezable":
        plan["mechanics_model"]["elemental_model"]["target"]["freeze"]["freezable"] = False
    elif mutation == "owner":
        plan["effect_bindings"][0]["owner"]["set_id"] = 1
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending"
    assert not out["mechanics_model"].get("fixed_effect_sources")


def test_changed_unknown_sibling_source_invalidates_confirmation_and_keeps_all_branches(target_plan):
    plan, db, bundle = target_plan
    accept(plan, db)
    old = run_plan_calc(plan, db)
    bundle["abilities"][0]["payload"]["unknown_new_member"] = 5
    report = compose_equipment_plan(old["plan"], db)
    assert report["state"] == "pending" and report["plan"]["resolved_template"] is None
    assert len(report["upstream_compilations"][0]["branches"]) == 4
    assert not report["mechanics_model"].get("fixed_effect_sources")


def test_unselected_set_withdraws_fixed_source(target_plan):
    plan, db, _ = target_plan
    accept(plan, db)
    plan["equipment_inputs"]["a"]["artifact_sets"] = []
    out = run_plan_calc(plan, db)
    assert out["report"]["upstream_compilations"][0]["status"] == "inactive"
    assert "target_condition_trace" not in out["result"]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 45, 45])


def test_client_cannot_duplicate_generated_fixed_source(target_plan):
    plan, db, _ = target_plan
    accept(plan, db)
    out = run_plan_calc(plan, db)
    plan["mechanics_model"]["fixed_effect_sources"] = out["report"]["mechanics_model"]["fixed_effect_sources"]
    with pytest.raises(PlanError, match="保留命名空间"):
        compose_equipment_plan(plan, db)
