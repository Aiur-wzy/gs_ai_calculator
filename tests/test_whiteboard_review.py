"""Independent counterexamples for preserving a user's action intentions."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from genshin_app import whiteboard as wb
from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError, new_plan


def board_fixture():
    board = wb.new_whiteboard()
    board.update(title="独立审查白板", duration=10)
    panel = wb.default_panel()
    panel.update(atk=100, crit_rate=0.5, energy_recharge=1.8)
    panel["dmg_bonus"]["cryo"] = 0.466
    board["team"] = [{"id": "a", "db_character_id": 1, "name": "测试角色甲", "level": 90,
                      "constellation": 0, "weapon": {"weapon_id": 11, "name": "工程武器",
                          "level": 90, "ascension": 6, "refinement": 1},
                      "artifact_sets": [{"set_id": 21, "name": "工程套装", "count": 4}],
                      "panel": panel, "notes": "用户固定面板"}]
    board["actions"] = [{"id": "e", "character": "a", "action_type_id": "action.elemental_skill",
                          "label": "点按战技", "start": 1, "variant": "tap", "notes": ""},
                         {"id": "q", "character": "a", "action_type_id": "action.elemental_burst",
                          "label": "元素爆发", "start": 1, "variant": "", "notes": "同秒在战技后"}]
    return board


def test_handoff_does_not_invent_damage_or_game_timing():
    board = board_fixture()
    original = copy.deepcopy(board)
    plan = wb.make_handoff(board)
    assert board == original
    assert plan["resolution"]["state"] == "pending"
    assert plan["manual_template"]["hits"] == []
    assert plan["mechanics_model"] is None
    assert all(a["cooldown"] is None and a["energy"] is None for a in plan["rotation_model"]["abilities"])
    assert all(a["occupation_end"] is None for a in plan["rotation_model"]["actions"])
    assert "hp" not in plan["manual_template"]["characters"][0]["stats"]
    assert wb.validate_result_intent(board, plan)["user_action_count"] == 2


@pytest.mark.parametrize("change", [
    lambda p: p["manual_template"].update(loop_duration=11),
    lambda p: p["rotation_model"].update(period=11),
    lambda p: p["rotation_model"]["actions"][0].update(start=0.5),
    lambda p: p["rotation_model"]["actions"][0].update(repeat={"mode": "each_cycle"}),
    lambda p: p["rotation_model"]["abilities"][0].update(variant="hold"),
    lambda p: p["rotation_model"]["abilities"][0].update(action_type_id="action.normal_attack"),
    lambda p: p["manual_template"]["characters"][0].update(level=80),
    lambda p: p["manual_template"]["characters"][0]["stats"].update(atk=200),
    lambda p: p["manual_template"]["characters"][0]["stats"].update(energy_recharge=3),
    lambda p: p["manual_template"]["characters"][0]["stats"]["dmg_bonus"].update(cryo=0.9),
    lambda p: p["equipment_inputs"]["a"]["character_ref"].update(db_character_id=2),
    lambda p: p["equipment_inputs"]["a"]["weapon"].update(weapon_id=12),
    lambda p: p["equipment_inputs"]["a"]["weapon"].update(level=80),
    lambda p: p["equipment_inputs"]["a"].update(artifact_sets=[{"set_id": 22, "count": 4}]),
    lambda p: p["extensions"]["whiteboard_handoff"]["constellations"].update(a=6),
])
def test_copying_original_snapshot_and_hash_cannot_hide_native_changes(change):
    board = board_fixture()
    plan = wb.make_handoff(board)
    declared = copy.deepcopy(plan["extensions"]["whiteboard_handoff"])
    change(plan)
    # A copied local digest is not authority over what the native plan executes.
    assert plan["extensions"]["whiteboard_handoff"]["board_fingerprint"] == declared["board_fingerprint"]
    with pytest.raises(PlanError):
        wb.validate_result_intent(board, plan)


def test_storage_array_reorder_is_allowed_but_same_second_execution_reorder_is_not():
    board = board_fixture()
    plan = wb.make_handoff(board)
    plan["rotation_model"]["abilities"].reverse()
    plan["rotation_model"]["actions"].reverse()
    assert wb.validate_result_intent(board, plan)["user_action_count"] == 2
    actions = plan["rotation_model"]["actions"]
    actions[0]["order"], actions[1]["order"] = actions[1]["order"], actions[0]["order"]
    with pytest.raises(PlanError):
        wb.validate_result_intent(board, plan)


def add_auxiliary(plan, kind="action.wait"):
    plan["rotation_model"]["abilities"].append({"id": "aux.ability", "character": "a", "action_type_id": kind,
                                             "cooldown": None, "energy": None, "rules": []})
    plan["rotation_model"]["actions"].append({"id": "aux", "ability_id": "aux.ability", "start": 2,
                                            "order": 10, "occupation_end": 2.5, "repeat": {"mode": "once"}})
    plan["extensions"]["whiteboard_handoff"]["auxiliary_action_ids"] = ["aux"]


def test_declared_auxiliary_cannot_hide_an_extra_attack_and_needs_explanation():
    board = board_fixture()
    plan = wb.make_handoff(board)
    add_auxiliary(plan)
    with pytest.raises(PlanError):
        wb.validate_result_intent(board, plan)
    plan["extensions"]["whiteboard_handoff"]["completion_notes"] = ["补写等待，用户既定战技和爆发不变"]
    assert wb.validate_result_intent(board, plan)["auxiliary_action_count"] == 1
    plan["rotation_model"]["abilities"][-1]["action_type_id"] = "action.normal_attack"
    with pytest.raises(PlanError):
        wb.validate_result_intent(board, plan)


def test_unknown_panel_completion_is_allowed_only_with_explicit_notes():
    board = board_fixture()
    plan = wb.make_handoff(board)
    plan["manual_template"]["characters"][0]["stats"]["hp"] = 12000
    with pytest.raises(PlanError):
        wb.validate_result_intent(board, plan)
    plan["extensions"]["whiteboard_handoff"]["completion_notes"] = ["为未知生命面板采用12000，来源见独立事实记录"]
    assert wb.validate_result_intent(board, plan)["user_action_count"] == 2


@pytest.mark.parametrize("family", ["hit_parameter_sources", "action_resource_sources", "mechanism_sources"])
def test_actual_typed_source_constellation_cannot_override_user_choice(family):
    board = board_fixture()
    plan = wb.make_handoff(board)
    if family == "hit_parameter_sources":
        plan["manual_template"]["hits"].append({"id": "h", "character": "a", "skill": "skill", "t": 1,
            "damage_type_ids": ["damage.elemental_skill"], "damage_system": "classic", "element": "cryo", "multiplier": 1})
        source = {"hit_id": "h", "version_line": "release", "selection": {"character_id": 1, "slot": "skill", "constellation": 6},
                  "param_index": 0, "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier", "status": "pending",
                  "source": "独立反例：用户零命而倍率来源六命", "expected_snapshot_id": None, "expected_node_sha256": None}
    elif family == "action_resource_sources":
        source = {"ability_id": "wb.ability.q", "version_line": "release",
                  "selection": {"character_id": 1, "slot": "burst", "constellation": 6},
                  "purpose": "ordinary_fixed_burst_base_resources", "variant": "base", "status": "pending", "source": "反例",
                  "expected_snapshot_id": None, "expected_resource_fingerprint": None, "expected_scope_fingerprint": None}
    else:
        from genshin_app.equipment.mechanism_source_schema import DEFAULT_SOURCE
        source = copy.deepcopy(DEFAULT_SOURCE)
        source.update(id="source", actor="a", mechanism_id="fixture",
                      cast_binding={"ability_ids": ["wb.ability.e"], "selection": {"constellation": 6}})
    plan[family] = [source]
    # First ensure the counterexample reaches intent validation, not a typo/schema error.
    wb.validate_plan(plan)
    with pytest.raises(PlanError):
        wb.validate_result_intent(board, plan)


def test_present_weapon_effect_refinement_must_match_user_choice():
    board = board_fixture()
    plan = wb.make_handoff(board)
    plan["equipment_inputs"]["a"]["weapon"]["refinements"] = [{"source_index": 0, "affix_group_id": 100, "refinement": 5}]
    with pytest.raises(PlanError):
        wb.validate_result_intent(board, plan)


def test_missing_bonus_key_is_unknown_and_still_requires_completion_notes():
    board = board_fixture()
    board["team"][0]["panel"]["dmg_bonus"] = {}
    plan = wb.make_handoff(board)
    plan["manual_template"]["characters"][0]["stats"]["dmg_bonus"]["pyro"] = 0.466
    with pytest.raises(PlanError):
        wb.validate_result_intent(board, plan)


def test_switch_intention_locks_destination_but_allows_real_previous_actor():
    board = board_fixture()
    second = copy.deepcopy(board["team"][0])
    second.update(id="b", db_character_id=2, name="测试角色乙")
    board["team"].append(second)
    board["actions"].append({"id": "switch", "character": "b", "action_type_id": "action.switch",
                             "label": "切换至乙", "start": 2, "variant": "", "notes": ""})
    plan = wb.make_handoff(board)
    plan["rotation_model"]["initial_frontline"] = "a"
    plan["rotation_model"]["abilities"][-1]["character"] = "a"
    plan["rotation_model"]["actions"][-1]["switch_to"] = "b"
    plan["extensions"]["whiteboard_handoff"]["completion_notes"] = ["补齐切人前前台为甲，切入乙"]
    assert wb.validate_result_intent(board, plan)["user_action_count"] == 3


def test_edit_after_export_invalidates_previous_result_even_with_old_snapshot():
    board = board_fixture()
    plan = wb.make_handoff(board)
    board["actions"][0]["start"] = 0.5
    with pytest.raises(PlanError):
        wb.validate_result_intent(board, plan)


def test_shorter_window_rejects_without_deleting_user_actions():
    board = board_fixture()
    board["duration"] = 1
    before = copy.deepcopy(board)
    with pytest.raises(PlanError):
        wb.validate_whiteboard(board)
    assert board == before


def test_save_conflict_preserves_external_edits_and_does_not_replace_native_plan(tmp_path):
    board = board_fixture()
    path = tmp_path / "whiteboard.json"
    saved = wb.save_whiteboard(path, board)
    other = copy.deepcopy(board)
    other["notes"] = "来自另一个窗口的修改"
    external = json_bytes(other)
    path.write_bytes(external)
    with pytest.raises(PlanError):
        wb.save_whiteboard(path, board, saved["file_sha"])
    assert path.read_bytes() == external
    native_path = tmp_path / "existing.plan.json"
    native = json_bytes(new_plan())
    native_path.write_bytes(native)
    with pytest.raises(PlanError):
        wb.save_whiteboard(native_path, board, hashlib.sha256(native).hexdigest())
    assert native_path.read_bytes() == native


def test_export_is_no_clobber_and_keeps_unknowns_pending(tmp_path):
    board = board_fixture()
    receipt = wb.export_handoff(board, tmp_path)
    original = {key: (tmp_path / name).read_bytes() for key, name in (
        ("board", "whiteboard.json"), ("native", "native.pending.plan.json"), ("instructions", "交给Agent.md"))}
    assert receipt["board_fingerprint"] == wb.board_fingerprint(board)
    board["notes"] = "新的用户意图"
    with pytest.raises(PlanError):
        wb.export_handoff(board, tmp_path)
    assert all((tmp_path / name).read_bytes() == original[key] for key, name in (
        ("board", "whiteboard.json"), ("native", "native.pending.plan.json"), ("instructions", "交给Agent.md")))


@pytest.fixture
def real_package(tmp_path, monkeypatch):
    """Real composer/executor/receipt; fake only the read-only public DB facts."""
    from genshin_app.equipment.delivery import (
        PACKAGE_KIND,
        PACKAGE_SCHEMA,
        validate_delivery_calculated,
    )
    from genshin_app.equipment.service import run_plan_calc
    from genshin_db import queries
    board = board_fixture()
    board["team"][0].update(weapon=None, artifact_sets=[])
    board["actions"] = board["actions"][:1]
    db = tmp_path / "engineering.db"
    db.touch()
    version = {"version_line": "release", "upstream_commit": "a" * 40,
               "equipment_schema_version": 1, "built_at": "2026-10-05T00:00:00+00:00"}
    meta = {"version_lines": ["release"], "datasets": {"equipment": {"lines": {"release": {"state": "ready", "schema_version": 1,
        **{key: version[key] for key in ("upstream_commit", "built_at")}}}}}}
    monkeypatch.setattr(queries, "get_meta", lambda _: copy.deepcopy(meta))
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {"character": {"id": 1, "name": "测试角色甲"}})
    plan = wb.make_handoff(board)
    plan["equipment_inputs"]["a"]["data_version"] = version
    stats = plan["manual_template"]["characters"][0]["stats"]
    stats.update({"hp": 12000, "def": 500, "crit_dmg": 0.5, "em": 0})
    evidence = {"status": "assumed", "source": "独立工程输入，不宣称游戏事实"}
    rotation = plan["rotation_model"]
    rotation.update(initial_frontline="a", max_cycles=3, initial_cooldowns=[])
    rotation["characters"][0].update(element="cryo", energy_capacity=10, initial_energy=10, **evidence)
    rotation["abilities"][0].update(**evidence)
    rotation["actions"][0].update(occupation_end=1.1, switch_allowed_at=1.1, **evidence)
    plan["manual_template"]["hits"] = [{"id": "h", "t": 1.2, "character": "a", "skill": "工程战技",
        "hit_name": "独立工程命中", "damage_type_ids": ["damage.elemental_skill"],
        "damage_system": "classic", "element": "cryo", "multiplier": 1}]
    plan["mechanics_model"] = {"schema_version": 1, "rules": [], "hit_events": [{"hit_id": "h", "action_id": "e", "order": 10}],
                               "hit_patterns": [], "condition_events": [], "initial_state": {"rules": []}}
    plan["extensions"]["whiteboard_handoff"]["completion_notes"] = ["补齐未知面板、占用和工程敌人条件；没有移动用户动作"]
    calculated = run_plan_calc(plan, db, capture_display=True)
    checked = validate_delivery_calculated(calculated)
    package = {"package_schema": PACKAGE_SCHEMA, "kind": PACKAGE_KIND, "calculated": calculated, "integrity": checked["integrity"]}
    return board, plan, package, db


def test_actual_calculated_package_returns_readonly_without_reinterpreting_diagnostic_state(real_package, tmp_path):
    board, _, package, _ = real_package
    path = tmp_path / "calculated.json"
    path.write_bytes(json_bytes(package))
    response = wb.read_whiteboard_result(board, path)
    assert response["readonly"] is True
    assert response["calculated"]["result"]["hits"][0]["expected"] == pytest.approx(82.4625)
    producer_energy = package["calculated"]["result"]["rotation_check"]["energy_check"]
    assert producer_energy["status"] == "not_verified"
    assert response["calculated"]["result"]["rotation_check"]["energy_check"] == producer_energy
    assert "不保证" in response["scope"]
    assert json.loads(path.read_text("utf8")) == package


def test_valid_recalculation_with_wrong_user_time_cannot_return_as_current(real_package, tmp_path):
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.service import run_plan_calc
    board, plan, package, db = real_package
    # Recalculate to get a valid new result/receipt. This is not a broken-hash test.
    plan["rotation_model"]["actions"][0]["start"] = 0.5
    changed = run_plan_calc(plan, db, capture_display=True)
    package.update(calculated=changed, integrity=validate_delivery_calculated(changed)["integrity"])
    path = tmp_path / "changed.calculated.json"
    path.write_bytes(json_bytes(package))
    with pytest.raises(PlanError) as error:
        wb.read_whiteboard_result(board, path)
    assert error.value.reason == "whiteboard_intent_mismatch"


def test_result_package_edit_without_fresh_receipt_is_rejected_before_board_binding(real_package, tmp_path):
    board, _, package, _ = real_package
    package["calculated"]["result"]["hits"][0]["expected"] += 123
    path = tmp_path / "tampered.calculated.json"
    path.write_bytes(json_bytes(package))
    with pytest.raises(PlanError) as error:
        wb.read_whiteboard_result(board, path)
    assert error.value.reason == "invalid_calculation_package"
