"""Build edits retain action intent and never promote stale numerical evidence."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.equipment.edit_review import (
    prepare_equipment_edit,
    preview_equipment_edit,
)
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi
from genshin_app.whiteboard import make_handoff, validate_result_intent
from genshin_app.whiteboard_equipment import apply_whiteboard_equipment
from genshin_db import queries
from tests.test_equipment_plan import binding, confirm_bindings, part, plan_fixture
from tests.test_equipment_plan import database as _legacy_database
from tests.test_external_damage import confirm, supplement
from tests.test_whiteboard_review import real_package as _real_package

real_package = _real_package
legacy_database = _legacy_database


def save_package(package, path):
    path.write_bytes(json_bytes(package))
    return path


def recalculate_package(package, plan, db):
    calculated = run_plan_calc(plan, db, capture_display=True)
    return {**package, "calculated": calculated,
            "integrity": validate_delivery_calculated(calculated)["integrity"]}


@pytest.fixture
def complete_external_package(real_package):
    """A fully declared engineering case for review of external value freshness.

    The shared fixture intentionally remains diagnostic. These positive cases
    concern equipment review, so author a fixed complete window before handoff
    and declare real generic zero CD/cost facts rather than borrowing its gaps.
    """
    original_board, original_plan, original_package, db = real_package
    board, plan = copy.deepcopy(original_board), copy.deepcopy(original_plan)
    board["damage_report"] = {
        "contract": "damage-report-window-v1", "mode": "explicit", "end": board["duration"]}
    handoff = make_handoff(board)
    handoff["extensions"]["whiteboard_handoff"]["completion_notes"] = copy.deepcopy(
        plan["extensions"]["whiteboard_handoff"]["completion_notes"])
    plan["extensions"] = copy.deepcopy(handoff["extensions"])
    rotation = plan["rotation_model"]
    rotation["damage_report"] = copy.deepcopy(handoff["rotation_model"]["damage_report"])
    rotation["abilities"][0].update(
        cooldown={"group": "engineering.e", "duration": 0, "start": "action_start",
                  "charges": 1, "recovery": "sequential"},
        energy={"required": 0, "amount": 0})
    rotation["initial_cooldowns"] = [
        {"character": "a", "group": "engineering.e", "ready_in": []}]
    rotation["events"] = [{
        "id": "engineering.cost", "type": "energy_spend", "t": 1, "order": 1,
        "character": "a", "action_id": "e", "required": 0, "amount": 0,
        "repeat": {"mode": "once"}, "status": "assumed",
        "source": "独立工程输入，不宣称游戏事实",
    }]
    rotation["action_hit_links"] = [{"action_id": "e", "hit_id": "h"}]
    package = recalculate_package(original_package, plan, db)
    from genshin_app.calculation_completion import require_calculation_complete
    assert require_calculation_complete(package["calculated"])["calculation_status"] == "complete"
    assert validate_result_intent(board, package["calculated"]["plan"])["user_action_count"] == 1
    return board, plan, package, db


def add_manual_buff(plan, db, *, mode="source_only"):
    effect = part("static", {"kind": "constant", "value": .1}, {
        "kind": "buff", "start": 0, "end": 10, "scope": "team", "target": "a",
        "effect": {"id": "effect", "benefit_type_id": "benefit.dmg_bonus.all", "zone": "dmg_bonus",
                   "stat": "all", "applies_to": {"mode": "all"}}})
    effect["dependencies"]["mode"] = mode
    source = binding([effect])
    source["provider_character_id"] = "a"
    plan["effect_bindings"] = [source]
    confirm_bindings(plan, db)
    if mode == "manual_context":
        effect["dependencies"]["context_fingerprint"] = compose_equipment_plan(plan, db)["context_fingerprint"]
    return effect


def weapon_facts(monkeypatch, version, *, effect=False):
    # Read-only comparison may query a weapon. It must never replace a supplied
    # final panel: use conspicuously different reference values to prove that.
    observations = []
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {
        "character": {"id": 1, "name": "测试角色甲", "weapon_type": "WEAPON_SWORD_ONE_HAND"}})
    monkeypatch.setattr(queries, "get_weapon_detail", lambda *a, **kw: {
        "weapon": {"id": 11, "weapon_type": "WEAPON_SWORD_ONE_HAND"}, "annotations": [],
        "data_version": version, "effects": [{"source_index": 0, "affix_group_id": 111,
            "refinements": [{"refinement": 1, "affix_id": 1110, "description": "新的工程被动"}]}] if effect else []})
    def weapon_stats(*args, **kwargs):
        observations.append((args, kwargs))
        return {"properties": [
            {"prop_type": "FIGHT_PROP_BASE_ATTACK", "value": 9999, "source_refs": []},
            {"prop_type": "FIGHT_PROP_CRITICAL_HURT", "value": 9.9, "source_refs": []},
        ]}
    monkeypatch.setattr(queries, "get_weapon_stats", weapon_stats)
    return observations


def selected_weapon():
    return {"weapon_id": 11, "name": "工程武器", "level": 90, "ascension": 6, "refinement": 1}


def test_repeated_build_edits_recalculate_from_original_verified_file_without_drift(real_package, tmp_path):
    original, _, package, db = real_package
    path = save_package(package, tmp_path / "calculated.json")
    before = path.read_bytes()
    for attack in (200, 300, 100):
        board = copy.deepcopy(original)
        board["team"][0]["panel"]["atk"] = attack
        result = apply_whiteboard_equipment(board, original, path, db_path=db)
        assert result["state"] == "ready", result["missing_inputs"]
        assert result["calculated"]["result"]["hits"][0]["expected"] == pytest.approx(82.4625 * attack / 100)
        assert result["plan"]["rotation_model"]["actions"] == package["calculated"]["plan"]["rotation_model"]["actions"]
        assert result["plan"]["manual_template"]["characters"][0]["stats"]["hp"] == 12000
        assert result["source_result"]["path"] == str(path.resolve())
        assert any(issue["code"] == "calculation_energy_check" for issue in result["agent_followup"])
        assert validate_result_intent(board, result["plan"])["user_action_count"] == 1
    assert path.read_bytes() == before
    assert original["team"][0]["panel"]["atk"] == 100


def test_energy_recharge_updates_the_energy_model_as_well_as_damage_panel(real_package, tmp_path):
    original, _, package, db = real_package
    board = copy.deepcopy(original)
    board["team"][0]["panel"]["energy_recharge"] = 2.4
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "ready"
    assert result["plan"]["rotation_model"]["characters"][0]["energy_recharge"] == 2.4
    assert result["plan"]["resolved_template"]["characters"][0]["stats"]["energy_recharge"] == 2.4


@pytest.mark.parametrize("mode", ["database", "manual"])
def test_basis_mode_is_preserved_without_reinterpreting_fixed_stats(real_package, tmp_path, mode):
    original, _, package, db = real_package
    board = copy.deepcopy(original)
    board["team"][0]["panel_basis_mode"] = mode
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "ready"
    cid = board["team"][0]["id"]
    assert result["plan"]["equipment_inputs"][cid]["panel_basis_mode"] == mode
    assert result["calculated"]["result"]["summary"] == package["calculated"]["result"]["summary"]
    assert result["plan"]["manual_template"]["characters"] == package["calculated"]["plan"]["manual_template"]["characters"]
    validate_result_intent(board, result["plan"])


def test_blank_board_has_no_automatic_empty_calculation(real_package):
    original, _, _, db = real_package
    result = apply_whiteboard_equipment(original, db_path=db)
    assert result["state"] == "pending"
    assert "calculated" not in result and "plan" not in result
    assert result["missing_inputs"][0]["code"] == "agent_model_required"


@pytest.mark.parametrize("change", [
    lambda board: board["actions"][0].update(start=.5),
    lambda board: board["team"][0].update(constellation=1),
    lambda board: board["team"][0].update(level=80),
    lambda board: board.update(duration=11),
])
def test_action_or_character_build_semantics_cannot_sneak_into_equipment_reuse(real_package, tmp_path, change):
    original, _, package, db = real_package
    board = copy.deepcopy(original)
    change(board)
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "pending"
    assert result["missing_inputs"][0]["code"] == "whiteboard_model_changed"
    assert "calculated" not in result


def test_disk_receipt_is_revalidated_even_when_frontend_has_a_previous_result(real_package, tmp_path):
    board, _, package, db = real_package
    package["calculated"]["result"]["hits"][0]["expected"] = 999999
    result = apply_whiteboard_equipment(board, board, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "pending"
    assert result["missing_inputs"][0]["code"] == "whiteboard_baseline_unavailable"
    assert "calculated" not in result


def test_clearing_a_known_panel_value_does_not_reuse_old_or_default_zero(real_package, tmp_path):
    original, _, package, db = real_package
    board = copy.deepcopy(original)
    board["team"][0]["panel"]["crit_rate"] = None
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "pending"
    assert result["missing_inputs"][0]["code"] == "unknown_panel"
    assert "calculated" not in result and "plan" not in result


def test_weapon_switch_preserves_fixed_panel_without_adding_growth(real_package, tmp_path, monkeypatch):
    original, plan, package, db = real_package
    observations = weapon_facts(monkeypatch, plan["equipment_inputs"]["a"]["data_version"])
    board = copy.deepcopy(original)
    board["team"][0]["weapon"] = selected_weapon()
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "ready", result["missing_inputs"]
    assert result["plan"]["equipment_inputs"]["a"]["weapon"]["weapon_id"] == 11
    assert result["calculated"]["result"]["hits"][0]["expected"] == pytest.approx(82.4625)
    assert observations, "新辅助比较应查询参考值，不能为了旧测试关闭它"
    assert result["plan"]["manual_template"]["characters"] == plan["manual_template"]["characters"]
    assert result["plan"]["resolved_template"]["characters"] == plan["manual_template"]["characters"]


def test_unknown_new_weapon_passive_returns_native_candidate_with_precise_gap(real_package, tmp_path, monkeypatch):
    original, plan, package, db = real_package
    weapon_facts(monkeypatch, plan["equipment_inputs"]["a"]["data_version"], effect=True)
    board = copy.deepcopy(original)
    board["team"][0]["weapon"] = selected_weapon()
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "pending" and "calculated" not in result
    assert any(item["code"] == "effect_pending" for item in result["missing_inputs"])
    assert result["plan"]["equipment_inputs"]["a"]["weapon"]["weapon_id"] == 11
    assert result["plan"]["manual_template"]["hits"] == plan["manual_template"]["hits"]
    with pytest.raises(PlanError):
        run_plan_calc(result["plan"], db)


def test_equipment_source_ambiguity_is_persisted_not_just_a_ui_warning(real_package, tmp_path, monkeypatch):
    original, plan, package, db = real_package
    weapon_facts(monkeypatch, plan["equipment_inputs"]["a"]["data_version"])
    part = add_manual_buff(plan, db)
    package = recalculate_package(package, plan, db)
    board = copy.deepcopy(original)
    board["team"][0]["weapon"] = selected_weapon()
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "pending" and "calculated" not in result
    changed_part = result["plan"]["effect_bindings"][0]["parts"][0]
    assert changed_part["state"] == "pending"
    assert changed_part["rule"] == part["rule"]
    assert changed_part["dependencies"] == part["dependencies"]
    assert "换装后" in changed_part["reason"]
    with pytest.raises(PlanError):
        run_plan_calc(result["plan"], db)


def test_panel_change_keeps_context_dependency_stale_without_renewing_ack(real_package, tmp_path):
    original, plan, package, db = real_package
    part = add_manual_buff(plan, db, mode="manual_context")
    acknowledged = copy.deepcopy(part["dependencies"])
    package = recalculate_package(package, plan, db)
    board = copy.deepcopy(original)
    board["team"][0]["panel"]["atk"] = 200
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "pending"
    assert any(row["code"] == "dependency_stale" for row in result["missing_inputs"])
    assert result["plan"]["effect_bindings"][0]["parts"][0]["dependencies"] == acknowledged


@pytest.mark.parametrize("confirmation", ["manual_context", "external"])
def test_basis_mode_only_preserves_review_but_changed_base_requires_review(complete_external_package, tmp_path, confirmation):
    original, plan, package, db = complete_external_package
    if confirmation == "manual_context":
        add_manual_buff(plan, db, mode="manual_context")
    else:
        plan["external_damage_supplements"] = [supplement()]
        confirm(plan, db)
    package = recalculate_package(package, plan, db)
    path = save_package(package, tmp_path / "calculated.json")
    board = copy.deepcopy(original)
    board["team"][0]["panel_basis_mode"] = "manual"
    result = apply_whiteboard_equipment(board, original, path, db_path=db)
    assert result["state"] == "ready", result["missing_inputs"]
    assert result["calculated"]["result"]["summary"] == package["calculated"]["result"]["summary"]
    board["team"][0]["panel"]["base_atk"] = 222
    result = apply_whiteboard_equipment(board, original, path, db_path=db)
    assert result["state"] == "pending" and "calculated" not in result
    assert any(row["code"] == "dependency_stale" or "external_damage" in row["code"]
               for row in result["missing_inputs"])


def test_old_external_numbers_are_not_added_to_the_reconfigured_result(complete_external_package, tmp_path):
    original, plan, package, db = complete_external_package
    plan["external_damage_supplements"] = [supplement()]
    confirm(plan, db)
    package = recalculate_package(package, plan, db)
    board = copy.deepcopy(original)
    board["team"][0]["panel"]["atk"] = 200
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "pending" and "calculated" not in result
    assert any("external_damage" in row["code"] for row in result["missing_inputs"])
    assert result["plan"]["external_damage_supplements"] == plan["external_damage_supplements"]


def test_external_numbers_cannot_promote_incomplete_whiteboard_to_formal(real_package):
    _, plan, package, db = real_package
    baseline = copy.deepcopy(package)
    assert any(row["code"] == "damage_report_anchor_missing"
               for row in package["calculated"]["result"]["mechanics_issues"])
    plan["external_damage_supplements"] = [supplement()]
    confirm(plan, db)
    with pytest.raises(PlanError) as caught:
        run_plan_calc(plan, db)
    assert caught.value.reason == "external_damage_pending"
    assert package == baseline
    assert "external_damage" not in package["calculated"]["result"]


def test_native_preview_uses_same_persistent_guard_but_panel_only_does_not_over_invalidate(real_package, monkeypatch):
    _, plan, _, db = real_package
    weapon_facts(monkeypatch, plan["equipment_inputs"]["a"]["data_version"])
    add_manual_buff(plan, db)
    candidate = copy.deepcopy(plan)
    candidate["manual_template"]["characters"][0]["stats"]["atk"] = 200
    panel_report = preview_equipment_edit(candidate, plan, db)
    assert panel_report["state"] == "ready"
    candidate["equipment_inputs"]["a"]["weapon"] = {"weapon_id": 11, "level": 90,
        "ascension": 6, "refinements": [], "selected_by": "user"}
    report = preview_equipment_edit(candidate, plan, db)
    assert report["state"] == "pending"
    assert plan["effect_bindings"][0]["parts"][0]["state"] == "resolved"
    assert candidate["effect_bindings"][0]["parts"][0]["state"] == "resolved"
    assert prepare_equipment_edit(report["plan"], plan)["effect_bindings"] == report["plan"]["effect_bindings"]


def test_gui_bridge_exposes_build_apply_and_guarded_preview(real_package, tmp_path):
    board, plan, package, _db = real_package
    api = GuiApi(tmp_path)
    (tmp_path / "genshin.db").touch()
    path = save_package(package, tmp_path / "calculated.json")
    response = api.whiteboard_apply_equipment(board, board, str(path))
    assert response["code"] == 0 and response["data"]["state"] == "ready"
    response = api.preview_equipment_edit(plan, plan)
    assert response["code"] == 0 and response["data"]["state"] == "ready"


def test_native_panel_er_updates_rotation_without_rewriting_other_game_declarations(real_package):
    _, plan, _, db = real_package
    candidate = copy.deepcopy(plan)
    candidate["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 2.5
    response = preview_equipment_edit(candidate, plan, db)
    assert response["state"] == "ready"
    expected = copy.deepcopy(plan["rotation_model"])
    expected["characters"][0]["energy_recharge"] = 2.5
    assert response["plan"]["rotation_model"] == expected


@pytest.mark.parametrize("key", ["em", "dmg_bonus.cryo"])
def test_native_clearing_known_optional_panel_values_cannot_default_silently_to_zero(real_package, key):
    _, plan, _, db = real_package
    candidate = copy.deepcopy(plan)
    stats = candidate["manual_template"]["characters"][0]["stats"]
    if "." in key:
        stats["dmg_bonus"].pop("cryo")
    else:
        stats.pop(key)
    with pytest.raises(PlanError) as rejected:
        preview_equipment_edit(candidate, plan, db)
    assert rejected.value.reason == "unknown_panel"
    assert rejected.value.details["missing_inputs"][0]["path"].endswith(key)


def test_same_selection_metadata_and_order_do_not_invalidate_manual_review(real_package, monkeypatch):
    _, plan, _, db = real_package
    weapon_facts(monkeypatch, plan["equipment_inputs"]["a"]["data_version"])
    plan["equipment_inputs"]["a"]["weapon"] = {"weapon_id": 11, "level": 90,
        "ascension": 6, "refinements": [], "selected_by": "agent"}
    add_manual_buff(plan, db)
    candidate = copy.deepcopy(plan)
    candidate["equipment_inputs"]["a"]["weapon"]["selected_by"] = "user"
    assert prepare_equipment_edit(candidate, plan)["effect_bindings"] == plan["effect_bindings"]


def test_direct_handwritten_buff_review_is_saved_with_native_candidate(real_package, monkeypatch):
    _, plan, _, db = real_package
    weapon_facts(monkeypatch, plan["equipment_inputs"]["a"]["data_version"])
    plan["manual_template"]["buffs"] = [{"id": "manual.buff", "start": 0, "end": 10, "scope": "team",
        "extensions": {"owner": "a", "source_id": "manual.source"},
        "effects": [{"id": "flat", "zone": "dmg_bonus", "stat": "all", "value": .1,
            "benefit_type_id": "benefit.dmg_bonus.all", "applies_to": {"mode": "all"}}]}]
    plan["manual_effect_dependencies"] = [{"effect_ref": {"buff_id": "manual.buff", "effect_id": "flat"},
        "mode": "source_only", "context_fingerprint": None, "note": "原手工依据"}]
    assert compose_equipment_plan(plan, db)["state"] == "ready"
    candidate = copy.deepcopy(plan)
    candidate["equipment_inputs"]["a"]["weapon"] = {"weapon_id": 11, "level": 90,
        "ascension": 6, "refinements": [], "selected_by": "user"}
    report = preview_equipment_edit(candidate, plan, db)
    assert report["state"] == "pending"
    assert report["plan"]["manual_effect_dependencies"][0]["mode"] == "unreviewed"
    assert report["plan"]["manual_template"]["buffs"] == plan["manual_template"]["buffs"]
    with pytest.raises(PlanError):
        run_plan_calc(report["plan"], db)


def test_artifact_set_switch_returns_actual_new_effect_requirements(real_package, tmp_path, monkeypatch):
    original, _, package, db = real_package
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda *a, **kw: {
        "artifact_set": {"id": 21, "name": "新工程套装"}, "annotations": [],
        "effects": [{"required_count": 2, "effect": {"affix_id": 212, "description": "已含面板的常驻值"}},
                    {"required_count": 4, "effect": {"affix_id": 214, "description": "需要判断的条件效果"}}]})
    board = copy.deepcopy(original)
    board["team"][0]["artifact_sets"] = [{"set_id": 21, "name": "新工程套装", "count": 4}]
    result = apply_whiteboard_equipment(board, original, save_package(package, tmp_path / "calculated.json"), db_path=db)
    assert result["state"] == "pending" and "calculated" not in result
    assert {item["owner"]["required_count"] for item in result["report"]["effect_requirements"]} == {2, 4}
    assert result["plan"]["manual_template"]["characters"][0]["stats"]["atk"] == 100


def test_legacy_equipment_mode_still_rebuilds_its_panel_after_weapon_switch(legacy_database):
    db, _ = legacy_database
    plan = plan_fixture()
    candidate = copy.deepcopy(plan)
    candidate["equipment_inputs"]["char1"]["weapon"]["weapon_id"] = 2
    report = preview_equipment_edit(candidate, plan, db)
    assert report["state"] == "ready"
    assert report["plan"]["resolved_template"]["characters"][0]["stats"]["atk"] == 2000
    assert plan["equipment_inputs"]["char1"]["weapon"]["weapon_id"] == 1


@pytest.mark.parametrize("selection", ["none", "weapon", "artifact_set"])
def test_first_managed_equipment_does_not_bypass_existing_manual_source_review(real_package, selection):
    _, plan, _, db = real_package
    add_manual_buff(plan, db)
    original_equipment = plan["equipment_inputs"].pop("a")
    assert compose_equipment_plan(plan, db)["state"] == "ready"
    candidate = copy.deepcopy(plan)
    candidate["equipment_inputs"]["a"] = original_equipment
    if selection == "weapon":
        candidate["equipment_inputs"]["a"]["weapon"] = {"weapon_id": 11, "level": 90,
            "ascension": 6, "refinements": [], "selected_by": "user"}
    elif selection == "artifact_set":
        candidate["equipment_inputs"]["a"]["artifact_sets"] = [{"set_id": 21, "count": 4}]
    reviewed = prepare_equipment_edit(candidate, plan)
    source = reviewed["effect_bindings"][0]["parts"][0]
    assert source["state"] == ("resolved" if selection == "none" else "pending")
    assert source["rule"] == plan["effect_bindings"][0]["parts"][0]["rule"]
    assert plan["equipment_inputs"] == {}
    assert plan["effect_bindings"][0]["parts"][0]["state"] == "resolved"
