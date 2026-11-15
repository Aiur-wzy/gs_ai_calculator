"""Given panels select set effects without rebuilding individual equipment stats."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.usefixtures("isolated_cli_database_admission")

from genshin_app.cli import main
from genshin_app.equipment import (
    PlanError,
    apply_data_update,
    compose_equipment_plan,
    load_plan,
    new_plan,
    run_plan_calc,
    save_plan,
    validate_plan,
)
from genshin_app.equipment.service import detach_character, plan_capabilities
from genshin_db import queries
from tests.test_equipment_plan import VERSION, binding, confirm_bindings, part
from tests.test_equipment_plan import database as _database

database = _database


@pytest.fixture
def panel_database(database, monkeypatch):
    def set_detail(_, set_id, line="release"):
        return {"artifact_set": {"id": set_id, "name": f"测试套装{set_id}"}, "annotations": [],
                "effects": [{"required_count": count, "effect": {
                    "affix_id": set_id * 10 + count, "description": f"完整{count}件套机制",
                    "source_refs": [{"kind": "database", "set_id": set_id, "count": count}]}}
                    for count in (2, 4)]}

    def forbidden(*args, **kwargs):
        raise AssertionError("panel mode must not query individual artifacts or weapon growth stats")

    monkeypatch.setattr(queries, "get_artifact_set_detail", set_detail)
    for name in ("get_artifact_detail", "get_artifact_main_stats", "get_weapon_stats"):
        monkeypatch.setattr(queries, name, forbidden)
    return database


def panel_plan():
    plan = new_plan()
    plan["manual_template"].update(
        characters=[{"id": "char1", "level": 90, "stats": {
            "atk": 1800, "crit_rate": 0.5, "crit_dmg": 1.0, "energy_recharge": 2.0}}],
        hits=[{"id": "hit", "damage_type_ids": ["damage.elemental_burst"], "t": 1,
               "character": "char1", "skill": "fixture", "multiplier": 1,
               "damage_system": "classic", "element": "physical"}])
    plan["equipment_inputs"] = {"char1": {
        "mode": "panel", "panel_policy": "static_included", "weapon": None,
        "artifact_sets": [], "source_refs": [{"kind": "manual", "note": "用户给定，已含常驻属性"}],
        "data_version": copy.deepcopy(VERSION)}}
    return plan


def buff_delivery():
    return {"kind": "buff", "start": 0, "end": 10, "scope": "team", "target": "char1",
            "effect": {"id": "effect", "benefit_type_id": "benefit.dmg_bonus.all",
                       "zone": "dmg_bonus", "stat": "all", "applies_to": {"mode": "all"}}}


def review_sets(plan, db):
    report = compose_equipment_plan(plan, db)
    for requirement in report["effect_requirements"]:
        if any(b["binding_id"] == requirement["binding_id"] for b in plan["effect_bindings"]):
            continue
        reviewed = copy.deepcopy(requirement["proposed_binding"])
        if requirement["owner"]["required_count"] == 2:
            effect = part("static_included", {"kind": "constant", "value": .2}, buff_delivery())
            effect.update(state="excluded", rule=None, delivery=None,
                          reason="两件套常驻属性已在用户给定初始面板中计入")
        else:
            effect = part("conditional", {"kind": "stat_scale", "from": {
                "character_id": "char1", "stage": "static", "stat": "energy_recharge"},
                "factor": .25}, buff_delivery())
        reviewed["parts"] = [effect]
        plan["effect_bindings"].append(reviewed)
    confirm_bindings(plan, db)


def test_panel_keeps_stats_exact_and_never_reads_rolls_or_weapon_growth(panel_database):
    db, _ = panel_database
    plan = panel_plan()
    original = copy.deepcopy(plan["manual_template"]["characters"][0])
    gear = plan["equipment_inputs"]["char1"]
    gear["artifact_sets"] = [{"set_id": 10, "count": 2}, {"set_id": 20, "count": 2}]
    review_sets(plan, db)
    for weapon_id in (1, 2, 1):
        gear["weapon"] = {"weapon_id": weapon_id, "refinements": [], "selected_by": "user"}
        report = compose_equipment_plan(plan, db)
        assert report["state"] == "ready", report["missing_inputs"]
        assert report["plan"]["resolved_template"]["characters"][0] == original
        assert report["contribution_report"]["characters"]["char1"] == {
            "base": {}, "static": original["stats"], "final": original["stats"], "contributions": []}
        assert not any("artifacts." in issue["path"] for issue in report["missing_inputs"])
    assert gear.get("basis") is None and gear.get("artifacts") is None


@pytest.mark.parametrize(("sets", "expected"), [
    ([{"set_id": 10, "count": 1}], set()),
    ([{"set_id": 10, "count": 2}, {"set_id": 20, "count": 2}], {(10, 2), (20, 2)}),
    ([{"set_id": 10, "count": 4}], {(10, 2), (10, 4)}),
    ([{"set_id": 10, "count": 5}], {(10, 2), (10, 4)}),
])
def test_panel_set_counts_discover_only_active_thresholds(panel_database, sets, expected):
    db, _ = panel_database
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = sets
    report = compose_equipment_plan(plan, db)
    assert {(r["owner"]["set_id"], r["owner"]["required_count"])
            for r in report["effect_requirements"]} == expected
    assert report["state"] == ("pending" if expected else "ready")


def test_panel_four_piece_buff_uses_given_er_and_removes_old_outputs(panel_database):
    db, _ = panel_database
    plan = panel_plan()
    gear = plan["equipment_inputs"]["char1"]
    gear["artifact_sets"] = [{"set_id": 10, "count": 4}]
    review_sets(plan, db)
    initial = run_plan_calc(plan, db)
    assert initial["plan"]["resolved_template"]["buffs"][0]["effects"][0]["value"] == .5
    plan["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 3
    changed = run_plan_calc(plan, db)
    assert changed["plan"]["resolved_template"]["buffs"][0]["effects"][0]["value"] == .75
    gear["artifact_sets"] = [{"set_id": 10, "count": 2}]
    removed = run_plan_calc(plan, db)
    assert not removed["plan"]["resolved_template"]["buffs"]
    assert any(w["code"] == "inactive_binding" for w in removed["report"]["warnings"])
    assert initial["result"]["summary"]["total_damage"] == pytest.approx(
        removed["result"]["summary"]["total_damage"] * 1.5)


def test_panel_weapon_requires_passive_review_without_growth_inputs(panel_database, monkeypatch):
    db, _ = panel_database
    plan = panel_plan()
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *args: {
        "weapon": {"id": 1, "weapon_type": "WEAPON_SWORD_ONE_HAND"}, "annotations": [],
        "effects": [{"source_index": 0, "affix_group_id": 100,
                     "refinements": [{"refinement": 1, "affix_id": 101, "description": "常驻与条件效果"}]}]})
    plan["equipment_inputs"]["char1"]["weapon"] = {
        "weapon_id": 1, "selected_by": "user",
        "refinements": [{"source_index": 0, "affix_group_id": 100, "refinement": 1}]}
    pending = compose_equipment_plan(plan, db)
    assert pending["state"] == "pending"
    requirement = pending["effect_requirements"][0]
    assert requirement["owner"] == {"kind": "weapon", "weapon_id": 1, "source_index": 0, "affix_id": 101}
    reviewed = requirement["proposed_binding"]
    included = part("included", {"kind": "constant", "value": 0}, buff_delivery())
    included.update(state="excluded", rule=None, delivery=None, reason="常驻已含初始面板")
    reviewed["parts"] = [included, part("conditional", {"kind": "constant", "value": .1}, buff_delivery())]
    plan["effect_bindings"] = [reviewed]
    confirm_bindings(plan, db)
    ready = compose_equipment_plan(plan, db)
    assert ready["state"] == "ready", ready["missing_inputs"]
    assert ready["plan"]["resolved_template"]["characters"] == plan["manual_template"]["characters"]
    assert ready["plan"]["resolved_template"]["buffs"][0]["effects"][0]["value"] == .1


@pytest.mark.parametrize("rule", [
    {"kind": "constant", "value": .2},
    {"kind": "stat_scale", "from": {"character_id": "char1", "stage": "static", "stat": "atk"}, "factor": .2},
])
def test_panel_delivery_is_pending_instead_of_double_counting(panel_database, rule):
    db, _ = panel_database
    plan = panel_plan()
    plan["effect_bindings"] = [binding([part("duplicate", rule,
        {"kind": "panel", "target_character_id": "char1", "stat": "atk", "operation": "percent"})])]
    confirm_bindings(plan, db)
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert any(i["code"] == "unsupported_effect" and "常驻" in i["message"] for i in report["missing_inputs"])
    duplicate = next(i for i in report["missing_inputs"] if i["code"] == "unsupported_effect" and "常驻" in i["message"])
    assert duplicate["category"] == "request_error" and "条件收益" in duplicate["repair_hint"]
    assert report["contribution_report"]["characters"]["char1"]["final"]["atk"] == 1800


@pytest.mark.parametrize(("stage", "stat", "provided", "expected"), [
    ("static", "atk", {}, 1800), ("static", "hp", {}, None),
    ("base", "atk", {}, None), ("base", "atk", {"base_atk": 800}, 800),
    ("base", "hp", {"base_hp": 10000}, 10000), ("base", "em", {"em": 200}, None),
])
def test_panel_scale_requires_explicit_stage_attribute(panel_database, stage, stat, provided, expected):
    db, _ = panel_database
    plan = panel_plan()
    plan["manual_template"]["characters"][0]["stats"].update(provided)
    plan["effect_bindings"] = [binding([part("scale", {"kind": "stat_scale", "from": {
        "character_id": "char1", "stage": stage, "stat": stat}, "factor": 1}, buff_delivery())])]
    confirm_bindings(plan, db)
    report = compose_equipment_plan(plan, db)
    assert report["state"] == ("pending" if expected is None else "ready")
    if expected is not None:
        assert report["plan"]["resolved_template"]["buffs"][0]["effects"][0]["value"] == expected


@pytest.mark.parametrize("sets", [
    [{"set_id": 10, "count": 0}], [{"set_id": 10, "count": 6}],
    [{"set_id": 10, "count": True}], [{"set_id": 10, "count": 2.5}],
    [{"set_id": 10, "count": 2}, {"set_id": 10, "count": 2}],
    [{"set_id": 10, "count": 4}, {"set_id": 20, "count": 2}],
])
def test_panel_rejects_duplicate_or_impossible_counts(sets):
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = sets
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize(("key", "value"), [
    ("basis", {}), ("artifacts", {}), ("manual_contributions", []),
    ("level", 90), ("panel_policy", "recalculate"), ("source_refs", []),
])
def test_panel_rejects_mixed_equipment_inputs_and_missing_policy_evidence(key, value):
    plan = panel_plan()
    plan["equipment_inputs"]["char1"][key] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_panel_update_rechecks_sources_without_changing_given_stats(panel_database):
    db, version = panel_database
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = [{"set_id": 10, "count": 4}]
    review_sets(plan, db)
    stats = copy.deepcopy(plan["manual_template"]["characters"][0]["stats"])
    version["built_at"] = "2026-09-13T00:10:00+00:00"
    stale = compose_equipment_plan(plan, db)
    assert any(i["code"] == "source_changed" for i in stale["missing_inputs"])
    updated = apply_data_update(plan, db)
    assert updated["state"] == "pending"  # Applying source versions does not confirm the effects.
    assert updated["plan"]["manual_template"]["characters"][0]["stats"] == stats
    review_sets(updated["plan"], db)
    assert compose_equipment_plan(updated["plan"], db)["state"] == "ready"


def test_panel_same_version_effect_change_invalidates_coverage(panel_database, monkeypatch):
    db, _ = panel_database
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = [{"set_id": 10, "count": 4}]
    review_sets(plan, db)
    original_query = queries.get_artifact_set_detail

    def changed(*args):
        detail = original_query(*args)
        detail["effects"][1]["effect"]["description"] = "同资料版本中修正的完整机制"
        return detail

    monkeypatch.setattr(queries, "get_artifact_set_detail", changed)
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert any(i["code"] == "effect_pending" and i["path"].endswith(".coverage") for i in report["missing_inputs"])
    assert report["contribution_report"]["characters"]["char1"]["final"] == plan["manual_template"]["characters"][0]["stats"]


def test_panel_changes_invalidate_manual_context_confirmations(panel_database):
    db, _ = panel_database
    plan = panel_plan()
    effect = part("manual_conversion", {"kind": "constant", "value": .3}, buff_delivery())
    effect["dependencies"]["mode"] = "manual_context"
    plan["effect_bindings"] = [binding([effect])]
    confirm_bindings(plan, db)
    effect["dependencies"]["context_fingerprint"] = compose_equipment_plan(plan, db)["context_fingerprint"]
    assert compose_equipment_plan(plan, db)["state"] == "ready"
    plan["manual_template"]["characters"][0]["stats"]["atk"] += 1
    stale = compose_equipment_plan(plan, db)
    assert any(i["code"] == "dependency_stale" for i in stale["missing_inputs"])


def test_panel_cli_compose_reopen_and_calc_preserve_sources(panel_database, tmp_path, capsys):
    db, _ = panel_database
    db = db.rename(tmp_path / "genshin.db")
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = [{"set_id": 10, "count": 4}]
    review_sets(plan, db)
    path, composed = tmp_path / "panel.plan.json", tmp_path / "composed.plan.json"
    save_plan(path, plan)
    assert main(["--data-dir", str(tmp_path), "equipment", "compose", "--plan", str(path),
                 "--output", str(composed)]) == 0
    capsys.readouterr()
    reopened = load_plan(composed, db)["plan"]
    assert reopened["equipment_inputs"] == plan["equipment_inputs"]
    assert reopened["manual_template"] == plan["manual_template"]
    assert main(["--data-dir", str(tmp_path), "equipment", "calc", "--plan", str(composed)]) == 0
    calculated = json.loads(capsys.readouterr().out)["data"]
    assert calculated["result"]["summary"] == run_plan_calc(plan, db)["result"]["summary"]


def test_panel_er_is_authoritative_for_rotation_and_missing_er_stays_unknown(panel_database):
    db, _ = panel_database
    document = json.loads((Path(__file__).resolve().parents[1] / "examples" / "rotation-energy-check.json").read_text("utf-8"))
    plan = new_plan(document["template"])
    plan.update(plan_version=2, rotation_model=document["rotation_model"])
    cid = plan["manual_template"]["characters"][0]["id"]
    plan["equipment_inputs"] = {cid: panel_plan()["equipment_inputs"]["char1"]}
    plan["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 2.0
    plan["rotation_model"]["characters"][0]["energy_recharge"] = 99
    result = run_plan_calc(plan, db)["result"]["rotation_check"]
    assert result["panel_bindings"][0]["declared_value"] == 99
    assert result["panel_bindings"][0]["value"] == 2.0
    del plan["manual_template"]["characters"][0]["stats"]["energy_recharge"]
    result = run_plan_calc(plan, db)["result"]["rotation_check"]
    assert result["panel_bindings"][0]["value"] is None
    assert result["status"] == "pending"


def test_panel_detach_preserves_resolved_effect_and_stats(panel_database):
    db, _ = panel_database
    plan = panel_plan()
    plan["equipment_inputs"]["char1"]["artifact_sets"] = [{"set_id": 10, "count": 4}]
    review_sets(plan, db)
    before = run_plan_calc(plan, db)
    detached = detach_character(plan, "char1", db)
    assert detached["state"] == "ready", detached["missing_inputs"]
    assert not detached["plan"]["equipment_inputs"]
    after = run_plan_calc(detached["plan"], db)
    assert after["result"]["summary"] == before["result"]["summary"]
    assert detached["plan"]["manual_template"]["characters"][0]["stats"] == plan["manual_template"]["characters"][0]["stats"]


def test_capabilities_publish_current_panel_path_and_legacy_union():
    contract = plan_capabilities()
    assert contract["default_input_mode"] == "panel"
    assert not contract["equipment_input_modes"]["panel"]["per_piece_artifacts"]
    assert len(contract["schema"]["properties"]["equipment_inputs"]["additionalProperties"]["anyOf"]) == 2
    assert validate_plan(contract["examples"]["panel_plan"])["valid"]
    assert validate_plan(contract["examples"]["pending_plan"])["valid"]
