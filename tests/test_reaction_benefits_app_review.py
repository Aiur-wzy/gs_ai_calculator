"""Independent EC11 App ownership/atomicity review using source-shaped input."""
from __future__ import annotations

import copy
import sqlite3

import pytest
from test_freeze_mechanics_review import setup as freeze_setup
from test_mechanics_execution import second_character
from test_plan_upstream import setup_runtime as setup_runtime  # noqa: PLC0414
from test_reaction_benefits_review import SWIRL, bundle, scope

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError


@pytest.fixture
def swirl_plan(tmp_path, monkeypatch):
    from genshin_app.equipment import upstream
    from genshin_db import queries, runtime_queries

    db = tmp_path / "independent.db"
    sqlite3.connect(db).close()
    data = bundle()
    version = {"version_line": "release", "upstream_commit": data["source"]["upstream_commit"],
               "equipment_schema_version": 1, "built_at": "independent-fixture"}
    monkeypatch.setattr(queries, "get_meta", lambda *a, **k: {"datasets": {"equipment": {"lines": {
        "release": {"state": "ready", "upstream_commit": version["upstream_commit"], "schema_version": 1,
                    "built_at": version["built_at"]}}}}})
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda *a, **k: {
        "artifact_set": {"name": "Independent equipment fixture"}, "effects": [
            {"required_count": 4, "effect": {"affix_id": 2150021, "name": "Four piece raw source",
                                            "source_refs": [{"kind": "database"}]}}], "annotations": []})
    monkeypatch.setattr(runtime_queries, "assemble_runtime_source", lambda *a, **k: copy.deepcopy(data))
    monkeypatch.setattr(upstream, "_burst_actions", lambda *a, **k: pytest.fail("reaction source incorrectly asked for Q identity"))
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **k: pytest.fail("reaction source incorrectly asked for character Q details"))
    t, r, m = freeze_setup()
    t["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(t))
    plan["rotation_model"], plan["mechanics_model"] = r, m
    plan["equipment_inputs"] = {"a": {"mode": "panel", "panel_policy": "static_included", "character_ref": None,
        "weapon": None, "artifact_sets": [{"set_id": 15002, "count": 4}],
        "source_refs": [{"kind": "manual", "note": "Independent fixed panel; no existing reaction bonus"}],
        "data_version": version}}
    requirement = compose_equipment_plan(plan, db)["effect_requirements"][0]
    binding = copy.deepcopy(requirement["proposed_binding"])
    binding["parts"] = []
    binding["upstream_recipe"] = {"schema_version": 1, "scenario": scope()}
    plan["effect_bindings"] = [binding]
    return plan, db, data


def accept(plan, db):
    report = compose_equipment_plan(plan, db)
    plan["effect_bindings"][0]["upstream_recipe"]["scenario"].update(
        report["upstream_compilations"][0]["required_confirmation"])
    return report


def atomically_pending(report):
    assert report["state"] == "pending"
    assert report["plan"]["resolved_template"] is None
    compiled = report["upstream_compilations"][0]
    assert compiled["status"] in ("pending", "invalid")
    assert compiled["parts"] == compiled["static_parts"] == compiled["rules"] == compiled["initial_rules"] == []
    assert compiled["coverage_certificate"]["projection_complete"] is False
    assert report["mechanics_model"]["rules"] == []
    assert report["mechanics_model"]["initial_state"]["rules"] == []
    assert not (report.get("mechanics_input_template") or {}).get("buffs")


def test_review_app_uses_three_private_parts_without_q_identity_or_native_generated_cache(swirl_plan):
    plan, db, _ = swirl_plan
    atomically_pending(accept(plan, db))
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == before
    assert out["result_receipt"]["input_fingerprint"] == fingerprint(input_view(plan))
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 45, 45, SWIRL, SWIRL])
    assert out["plan"]["effect_bindings"][0]["parts"] == []
    assert out["plan"]["manual_template"]["buffs"] == []
    assert out["plan"]["mechanics_model"]["rules"] == []
    compiled = out["report"]["upstream_compilations"][0]
    assert compiled["whole_source_status"] == "partial"
    assert compiled["coverage_certificate"]["source_complete"] is False
    assert len(compiled["parts"]) == 3
    assert len(out["report"]["mechanics_model"]["rules"]) == 2
    assert len(out["report"]["mechanics_input_template"]["buffs"]) == 1
    original = copy.deepcopy(out["plan"])
    original["resolved_template"]["buffs"] = []
    original["resolution"]["state"] = "ready"
    original["resolved_template"]["characters"][0]["stats"]["atk"] = 999999
    assert run_plan_calc(original, db)["result"] == out["result"]


@pytest.mark.parametrize("change", ["range", "initial", "clock-order", "status", "target", "reference", "q-refs", "manual-repeat"])
def test_review_scope_failure_withdraws_all_outputs_even_after_valid_acceptance(swirl_plan, change):
    plan, db, _ = swirl_plan
    accept(plan, db)
    assert compose_equipment_plan(plan, db)["state"] == "ready"
    scenario = plan["effect_bindings"][0]["upstream_recipe"]["scenario"]
    if change in ("range", "initial", "clock-order"):
        scenario[{"range": "origin_target_range", "initial": "initial_source_effects", "clock-order": "event_order_policy"}[change]] = "unknown"
    elif change == "status":
        scenario["status"] = "pending"
    elif change == "target":
        scenario["target_id"] = "unrelated-target"
        # A fresh source acceptance cannot prove this target exists in the plan.
        accept(plan, db)
    elif change == "reference":
        plan["mechanics_model"]["elemental_model"]["reference_acceptance"]["acknowledged"] = False
    elif change == "q-refs":
        plan["effect_bindings"][0]["upstream_recipe"]["ability_refs"] = [{"ability_id": "e", "skill_id": 10018}]
    else:
        plan["mechanics_model"]["manual_buff_repeat"] = "once"
    atomically_pending(compose_equipment_plan(plan, db))


def test_review_exact_source_mutation_does_not_reuse_prior_numeric_result(swirl_plan):
    plan, db, data = swirl_plan
    accept(plan, db)
    old = run_plan_calc(plan, db)
    data["abilities"][0]["payload"]["new_unsupported_side_effect"] = True
    atomically_pending(compose_equipment_plan(old["plan"], db))


def test_review_removing_equipment_withdraws_both_dynamic_and_static_deliveries(swirl_plan):
    plan, db, _ = swirl_plan
    accept(plan, db)
    plan["equipment_inputs"]["a"]["artifact_sets"] = []
    out = run_plan_calc(plan, db)
    assert out["report"]["upstream_compilations"][0]["status"] == "inactive"
    assert out["report"]["mechanics_model"]["rules"] == []
    assert out["report"]["mechanics_input_template"]["buffs"] == []
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 45, 45, 781.299, 781.299])


def test_review_inactive_other_provider_recipe_does_not_compete_with_actual_equipment(swirl_plan):
    plan, db, _ = swirl_plan
    second_character(plan["manual_template"], plan["rotation_model"])
    plan["manual_template"]["characters"][1]["stats"]["energy_recharge"] = 1
    plan["equipment_inputs"]["b"] = copy.deepcopy(plan["equipment_inputs"]["a"])
    before_removal = compose_equipment_plan(plan, db)
    old_requirement = next(x for x in before_removal["effect_requirements"] if x["provider_character_id"] == "b")
    old_binding = copy.deepcopy(old_requirement["proposed_binding"])
    old_binding["parts"] = []
    old_binding["upstream_recipe"] = {"schema_version": 1, "scenario": scope()}
    plan["effect_bindings"].append(old_binding)
    plan["equipment_inputs"]["b"]["artifact_sets"] = []
    accept(plan, db)
    checked = compose_equipment_plan(plan, db)
    assert checked["state"] == "ready", checked["missing_inputs"]
    out = run_plan_calc(plan, db)
    assert [x["status"] for x in out["report"]["upstream_compilations"]] == ["ready", "inactive"]
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 45, 45, SWIRL, SWIRL])


@pytest.mark.parametrize("reuse", ["static", "rule"])
def test_review_typed_source_cannot_be_reintroduced_under_manual_alias(swirl_plan, reuse):
    plan, db, _ = swirl_plan
    accept(plan, db)
    out = run_plan_calc(plan, db)
    if reuse == "static":
        manual = copy.deepcopy(out["report"]["mechanics_input_template"]["buffs"][0])
        manual["id"] = "local-friendly-alias"
        plan["manual_template"]["buffs"].append(manual)
    else:
        manual = copy.deepcopy(out["report"]["mechanics_model"]["rules"][0])
        manual["id"] = "local-friendly-alias"
        plan["mechanics_model"]["rules"].append(manual)
        plan["mechanics_model"]["initial_state"]["rules"].append(
            {"rule_id": manual["id"], "cooldown_remaining": 0, "instances": []})
    with pytest.raises(PlanError) as exc:
        compose_equipment_plan(plan, db)
    assert exc.value.reason == "duplicate_delivery"


def test_review_data_line_is_not_a_gameplay_namespace_for_unique_equipment(swirl_plan):
    from genshin_app.equipment.reaction_benefits import reaction_projection_scope
    plan, db, _ = swirl_plan
    requirement = compose_equipment_plan(plan, db)["effect_requirements"][0]
    plan["equipment_inputs"]["b"] = copy.deepcopy(plan["equipment_inputs"]["a"])
    plan["equipment_inputs"]["b"]["data_version"]["version_line"] = "beta"
    issues, _ = reaction_projection_scope(plan, plan["effect_bindings"][0], requirement)
    assert any("另一提供者" in message or "版本线" in message for message in issues), issues


def test_review_new_reaction_route_does_not_remove_legacy_burst_identity_gate(setup_runtime):
    db, build, _ = setup_runtime
    plan = build()
    report = compose_equipment_plan(plan, db)
    plan["effect_bindings"][0]["upstream_recipe"]["scenario"]["accepted_assumptions_fingerprint"] = (
        report["upstream_compilations"][0]["assumptions_fingerprint"])
    out = run_plan_calc(plan, db)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([990] * 3)
    plan["effect_bindings"][0]["upstream_recipe"]["ability_refs"][0]["skill_id"] = 10018
    broken = compose_equipment_plan(plan, db)
    assert broken["state"] == "pending"
    assert broken["mechanics_model"]["rules"] == []
