"""Native plans: arithmetic, provenance, match invalidation and durable file contracts."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.equipment import (
    PlanError,
    apply_data_update,
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
    save_plan,
    validate_plan,
)
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.service import pending_example
from genshin_db import queries

VERSION = {"version_line": "release", "upstream_commit": "a" * 40,
           "equipment_schema_version": 1, "built_at": "2026-09-13T00:00:00+00:00"}


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    path.touch()
    current = copy.deepcopy(VERSION)
    monkeypatch.setattr(queries, "get_meta", lambda _: {"datasets": {"equipment": {"lines": {
        "release": {"state": "ready", "schema_version": 1, **{k: current[k] for k in ("upstream_commit", "built_at")}}
    }}}})
    monkeypatch.setattr(queries, "get_weapon_detail", lambda _, wid, line="release": {
        "weapon": {"id": wid, "weapon_type": "WEAPON_SWORD_ONE_HAND"}, "effects": [],
        "annotations": [], "data_version": current}, raising=False)
    monkeypatch.setattr(queries, "get_weapon_stats", lambda _, wid, level, ascension, line="release": {
        "properties": [{"prop_type": "FIGHT_PROP_BASE_ATTACK", "value": {1: 500, 2: 600}[wid],
                        "value_status": "present", "source_refs": []}], "data_version": current}, raising=False)
    return path, current


def plan_fixture():
    plan = pending_example()
    equipment = plan["equipment_inputs"]["char1"]
    equipment["basis"]["character_base_atk"] = 300
    equipment["weapon"] = {"weapon_id": 1, "level": 90, "ascension": 6,
                           "refinements": [], "selected_by": "agent"}
    equipment["data_version"] = copy.deepcopy(VERSION)
    equipment["manual_contributions"] = [
        {"contribution_id": "atk.percent", "stat": "atk", "operation": "percent", "value": 1,
         "source_refs": [{"kind": "manual", "note": "arithmetic fixture"}]},
        {"contribution_id": "atk.flat", "stat": "atk", "operation": "flat", "value": 200,
         "source_refs": [{"kind": "manual", "note": "arithmetic fixture"}]},
    ]
    return plan


def binding(parts):
    return {"binding_id": "test.binding", "provider_character_id": "char1",
            "owner": {"kind": "manual", "note": "synthetic mechanics"},
            "source_refs": [{"kind": "manual", "note": "independent fixture"}],
            "source_fingerprint": None, "coverage": {"state": "unreviewed", "reviewed_by": None,
                "source_fingerprint": None, "note": None}, "parts": parts}


def _manual_finished_panel_plan():
    """A private user panel is not a provisional public-data declaration."""

    plan = new_plan()
    plan["manual_template"]["characters"] = [{
        "id": "manual.actor", "level": 90, "stats": {},
    }]
    plan["equipment_inputs"]["manual.actor"] = {
        "mode": "panel",
        "panel_policy": "static_included",
        "panel_basis_mode": "manual",
        "character_ref": None,
        "character_entity_ref": None,
        "weapon": None,
        "artifact_sets": [],
        "source_refs": [{
            "kind": "user",
            "note": "User supplied the finished panel without claiming a public entity",
        }],
        "data_version": None,
    }
    return plan


def test_manual_finished_panel_does_not_require_public_entity_ref():
    plan = _manual_finished_panel_plan()
    assert validate_plan(plan)["valid"] is True


def test_manual_panel_cannot_smuggle_unregistered_provisional_identity():
    plan = _manual_finished_panel_plan()
    plan["equipment_inputs"]["manual.actor"]["character_entity_ref"] = {
        "kind": "provisional",
        "entity_type": "character",
        "provisional_id": "local.character.unreviewed",
    }
    with pytest.raises(PlanError) as caught:
        validate_plan(plan)
    assert caught.value.reason == "entity_ref_registry_missing"


def part(pid, rule, delivery):
    return {"part_id": pid, "label": pid, "state": "resolved", "rule": rule, "delivery": delivery,
            "conditions": [], "dependencies": {"mode": "stat_rule" if rule["kind"] == "stat_scale" else "source_only",
                "context_fingerprint": None, "note": "fixture"}, "reason": None}


def confirm_bindings(plan, db):
    report = compose_equipment_plan(plan, db)
    for req in report["effect_requirements"]:
        b = next(b for b in plan["effect_bindings"] if b["binding_id"] == req["binding_id"])
        b["source_fingerprint"] = req["source_fingerprint"]
        b["coverage"] = {"state": "complete", "reviewed_by": "test", "note": "reviewed all synthetic parts",
                         "source_fingerprint": req["source_fingerprint"]}


def test_rebuild_from_basis_has_no_drift_or_cached_output_authority(database):
    db, _ = database
    p = plan_fixture()
    for wid, expected in [(1, 1800), (2, 2000), (1, 1800)]:
        p["equipment_inputs"]["char1"]["weapon"]["weapon_id"] = wid
        report = compose_equipment_plan(p, db)
        assert report["state"] == "ready", report["missing_inputs"]
        assert report["plan"]["resolved_template"]["characters"][0]["stats"]["atk"] == expected
        p = report["plan"]
        p["resolved_template"]["characters"][0]["stats"]["atk"] = 999999
    assert p["manual_template"]["characters"][0]["stats"]["atk"] == 100
    assert run_plan_calc(p, db)["plan"]["resolved_template"]["characters"][0]["stats"]["atk"] == 1800


def test_stat_scales_read_static_not_other_scale_outputs(database):
    db, _ = database
    p = plan_fixture()
    p["effect_bindings"] = [binding([
        part("hp_flat", {"kind": "constant", "value": 1000},
             {"kind": "panel", "target_character_id": "char1", "stat": "hp", "operation": "flat"}),
        part("hp_scale", {"kind": "stat_scale", "from": {"character_id": "char1", "stage": "static", "stat": "hp"}, "factor": 1},
             {"kind": "panel", "target_character_id": "char1", "stat": "hp", "operation": "flat"}),
        part("atk_scale", {"kind": "stat_scale", "from": {"character_id": "char1", "stage": "static", "stat": "hp"}, "factor": .01},
             {"kind": "panel", "target_character_id": "char1", "stat": "atk", "operation": "flat"}),
    ])]
    confirm_bindings(p, db)
    report = compose_equipment_plan(p, db)
    assert report["state"] == "ready", report["missing_inputs"]
    panel = report["contribution_report"]["characters"]["char1"]
    assert panel["static"]["hp"] == 11000
    assert panel["final"]["hp"] == 22000
    assert panel["final"]["atk"] == 1910
    p["effect_bindings"][0]["parts"].reverse()
    reordered = compose_equipment_plan(p, db)
    assert reordered["contribution_report"]["characters"]["char1"]["final"] == panel["final"]


def test_manual_context_detects_other_static_contribution_change(database):
    db, _ = database
    p = plan_fixture()
    fixed = part("folded", {"kind": "constant", "value": 100},
                 {"kind": "panel", "target_character_id": "char1", "stat": "atk", "operation": "flat"})
    fixed["dependencies"]["mode"] = "manual_context"
    p["effect_bindings"] = [binding([fixed])]
    confirm_bindings(p, db)
    report = compose_equipment_plan(p, db)
    fixed["dependencies"]["context_fingerprint"] = report["context_fingerprint"]
    assert compose_equipment_plan(p, db)["state"] == "ready"
    p["equipment_inputs"]["char1"]["manual_contributions"][0]["value"] = 2
    report = compose_equipment_plan(p, db)
    assert any(i["code"] == "dependency_stale" for i in report["missing_inputs"])


def test_source_same_commit_rebuild_requires_explicit_apply(database):
    db, current = database
    p = plan_fixture()
    assert compose_equipment_plan(p, db)["state"] == "ready"
    current["built_at"] = "2026-09-13T00:10:00+00:00"
    report = compose_equipment_plan(p, db)
    assert report["state"] == "pending"
    assert p["equipment_inputs"]["char1"]["data_version"] == VERSION
    updated = apply_data_update(p, db)
    assert updated["state"] == "ready"
    assert updated["plan"]["equipment_inputs"]["char1"]["data_version"] == current


@pytest.mark.parametrize("mutation", [
    lambda p: p.update(plan_version=99),
    lambda p: p["equipment_inputs"]["char1"]["weapon"].update(level=True),
    lambda p: p["equipment_inputs"]["char1"]["basis"].update(character_base_atk=float("nan")),
    lambda p: p["equipment_inputs"]["char1"].update(weapon_typo=1),
    lambda p: p["equipment_inputs"]["char1"]["artifacts"].update(EQUIP_BRACER={"state": "equipped", "artifact_id": 1,
        "level": 0, "main_prop_id": 1, "selected_by": "agent"}),
])
def test_invalid_structures_fail_before_database(mutation):
    p = plan_fixture()
    mutation(p)
    with pytest.raises(PlanError):
        validate_plan(p)


def test_pending_states_save_and_file_conflict(tmp_path, database):
    db, _ = database
    p = plan_fixture()
    p["equipment_inputs"]["char1"]["weapon"]["level"] = None
    p["equipment_inputs"]["char1"]["artifacts"]["EQUIP_BRACER"] = None
    report = compose_equipment_plan(p, db)
    assert report["state"] == "pending"
    path = tmp_path / "plan.json"
    saved = save_plan(path, report["plan"])
    assert read_plan(path)["plan"]["resolution"]["state"] == "pending"
    with pytest.raises(PlanError, match="外部修改"):
        save_plan(path, p, "old-fingerprint")
    p["revision"] += 1
    save_plan(path, p, saved["file_sha"])
    assert json.loads(path.with_name("plan.json.previous").read_text("utf-8"))["revision"] == 0


def matching_plan():
    p = new_plan()
    p["manual_template"].update(characters=[{"id": "c", "level": 90, "stats": {"atk": 100}}],
        buffs=[{"id": "b", "start": 0, "end": 10, "effects": [{"id": "e", "benefit_type_id": "benefit.dmg_bonus.all",
            "zone": "dmg_bonus", "stat": "all", "value": 1, "applies_to": {"mode": "types", "damage_type_ids": ["damage.elemental_burst"]}}]}],
        hits=[{"id": hid, "damage_type_ids": [kind], "t": 1, "character": "c", "skill": "fixture",
               "damage_system": "classic", "element": "physical", "multiplier": 1}
              for hid, kind in [("normal", "damage.normal_attack"), ("burst", "damage.elemental_burst"), ("extra", "damage.other")]])
    return p


def test_rotation_match_only_changes_one_effect_and_invalidates_on_new_candidates(tmp_path):
    p = matching_plan()
    db = tmp_path / "unused.db"
    baseline = run_plan_calc(p, db)
    assert [bool(h["matched_effect_refs"]) for h in baseline["result"]["hits"]] == [False, True, False]
    m = {"id": "m", "effect_ref": {"buff_id": "b", "effect_id": "e"}, "mode": "adjust",
         "include_hit_ids": ["extra"], "exclude_hit_ids": [], "reason": "fixture special relation",
         "source_refs": [{"kind": "manual", "note": "confirmed synthetic case"}],
         "state": "pending", "basis_fingerprint": None}
    p["rotation_tuning"]["buff_hit_matches"] = [m]
    report = compose_equipment_plan(p, db)
    assert report["match_previews"][0]["base_hit_ids"] == ["burst"]
    m.update(state="confirmed", basis_fingerprint=report["match_previews"][0]["basis_fingerprint"])
    result = run_plan_calc(p, db)
    assert [bool(h["matched_effect_refs"]) for h in result["result"]["hits"]] == [False, True, True]
    p["revision"] += 1
    p["manual_template"]["hits"].reverse()
    assert compose_equipment_plan(p, db)["state"] == "ready"
    p["manual_template"]["hits"].append({**p["manual_template"]["hits"][0], "id": "new"})
    assert compose_equipment_plan(p, db)["state"] == "pending"
    p["rotation_tuning"]["buff_hit_matches"] = []
    assert compose_equipment_plan(p, db)["state"] == "ready"


def test_unknown_scope_adjust_pending_replace_empty_is_explicit_none(tmp_path):
    p = matching_plan()
    p["manual_template"]["buffs"][0]["effects"][0]["applies_to"] = None
    db = tmp_path / "unused.db"
    pending = compose_equipment_plan(p, db)
    assert pending["state"] == "pending"
    capability = pending["task_capability"]
    assert capability["derivation"]["consumer_authority"] == "effective_graph"
    assert len(capability["derivation"]["effective_graph_fingerprint"]) == 64
    issue = next(row for row in capability["blocking"] if row["code"] == "effect_pending")
    assert issue["consumer"] == "buff.effect.b.e"
    assert issue["blocking_scope"] == "agent_materialization"
    m = {"id": "replace", "effect_ref": {"buff_id": "b", "effect_id": "e"}, "mode": "replace", "hit_ids": [],
         "reason": "explicitly excluded in this scenario", "source_refs": [{"kind": "manual", "note": "fixture"}],
         "state": "pending", "basis_fingerprint": None}
    p["rotation_tuning"]["buff_hit_matches"] = [m]
    report = compose_equipment_plan(p, db)
    m.update(state="confirmed", basis_fingerprint=report["match_previews"][0]["basis_fingerprint"])
    assert compose_equipment_plan(p, db)["state"] == "ready"


def test_cross_character_base_attack_buff_recomputes_after_weapon_change(database):
    db, _ = database
    p = plan_fixture()
    p["manual_template"]["characters"].append({"id": "receiver", "level": 90, "stats": {"atk": 1000}})
    p["manual_template"]["hits"][0]["character"] = "receiver"
    scale = part("base_atk_buff", {"kind": "stat_scale", "from": {
        "character_id": "char1", "stage": "base", "stat": "atk"}, "factor": 1},
        {"kind": "buff", "start": 0, "end": 10, "scope": "team", "target": "receiver",
         "effect": {"id": "e", "benefit_type_id": "benefit.stat_add.atk", "zone": "stat_add",
                    "stat": "atk", "applies_to": {"mode": "all"}}})
    p["effect_bindings"] = [binding([scale])]
    confirm_bindings(p, db)
    before = run_plan_calc(p, db)
    assert before["plan"]["resolved_template"]["buffs"][0]["effects"][0]["value"] == 800
    p["equipment_inputs"]["char1"]["weapon"]["weapon_id"] = 2
    after = run_plan_calc(p, db)
    assert after["plan"]["resolved_template"]["buffs"][0]["effects"][0]["value"] == 900
    assert after["result"]["summary"]["total_damage"] / before["result"]["summary"]["total_damage"] == pytest.approx(1900 / 1800)
    assert p["manual_template"]["characters"][1]["stats"]["atk"] == 1000


def test_manual_final_panel_cannot_supply_static_stage(database):
    db, _ = database
    p = plan_fixture()
    p["manual_template"]["characters"].append({"id": "manual", "level": 90, "stats": {"atk": 1000}})
    scale = part("invalid_base", {"kind": "stat_scale", "from": {
        "character_id": "manual", "stage": "base", "stat": "atk"}, "factor": 1},
        {"kind": "panel", "target_character_id": "char1", "stat": "atk", "operation": "flat"})
    p["effect_bindings"] = [binding([scale])]
    confirm_bindings(p, db)
    report = compose_equipment_plan(p, db)
    assert report["state"] == "pending"
    assert any(i["action"] == "edit_basis" for i in report["missing_inputs"])


def test_artifact_two_and_four_thresholds_and_removal(database, monkeypatch):
    from genshin_app.equipment.schema import SLOTS
    db, current = database
    p = plan_fixture()
    monkeypatch.setattr(queries, "get_artifact_detail", lambda _, aid, line="release": {
        "artifact": {"id": aid, "equip_type": SLOTS[aid - 1], "set_id": 42}, "data_version": current})
    monkeypatch.setattr(queries, "get_artifact_main_stats", lambda _, aid, level, mid, line="release": {
        "stat": {"prop_type": "FIGHT_PROP_HP", "value": 100, "source_refs": []}, "data_version": current})
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda _, sid, line="release": {
        "effects": [{"required_count": count, "effect": {"affix_id": count, "desc_template": "synthetic set effect",
            "source_refs": [{"kind": "manual", "note": "fixture only"}]}} for count in (2, 4)], "data_version": current})

    def select(count):
        for i, slot in enumerate(SLOTS, 1):
            p["equipment_inputs"]["char1"]["artifacts"][slot] = ({"state": "equipped", "artifact_id": i,
                "level": 20, "main_prop_id": 1, "substats": [], "selected_by": "user"} if i <= count
                else {"state": "empty", "reason": "fixture empty"})

    select(2)
    report = compose_equipment_plan(p, db)
    assert [r["owner"]["required_count"] for r in report["effect_requirements"]] == [2]
    req = report["effect_requirements"][0]
    b = req["proposed_binding"]
    b["parts"] = [part("hp", {"kind": "constant", "value": 200}, {
        "kind": "panel", "target_character_id": "char1", "stat": "hp", "operation": "flat"})]
    p["effect_bindings"] = [b]
    confirm_bindings(p, db)
    assert compose_equipment_plan(p, db)["contribution_report"]["characters"]["char1"]["final"]["hp"] == 10400
    select(4)
    report = compose_equipment_plan(p, db)
    assert [r["owner"]["required_count"] for r in report["effect_requirements"]] == [2, 4]
    assert report["state"] == "pending"
    select(1)
    report = compose_equipment_plan(p, db)
    assert report["state"] == "ready"
    assert report["effect_requirements"] == []
    assert report["contribution_report"]["characters"]["char1"]["final"]["hp"] == 10100
    assert report["warnings"][0]["code"] == "inactive_binding"


def test_detach_one_character_freezes_cross_character_conversion(database):
    from genshin_app.equipment import detach_character
    db, _ = database
    p = plan_fixture()
    p["manual_template"]["characters"].append({"id": "receiver", "level": 90, "stats": {"atk": 1000}})
    p["equipment_inputs"]["receiver"] = copy.deepcopy(p["equipment_inputs"]["char1"])
    p["manual_template"]["hits"][0]["character"] = "receiver"
    p["effect_bindings"] = [binding([part("buff", {"kind": "stat_scale", "from": {
        "character_id": "char1", "stage": "base", "stat": "atk"}, "factor": 1},
        {"kind": "buff", "start": 0, "end": 10, "scope": "team", "target": "receiver",
         "effect": {"id": "e", "benefit_type_id": "benefit.stat_add.atk", "zone": "stat_add",
                    "stat": "atk", "applies_to": {"mode": "all"}}})])]
    confirm_bindings(p, db)
    before = run_plan_calc(p, db)
    detached = detach_character(p, "char1", db)
    assert detached["state"] == "ready", detached["missing_inputs"]
    assert set(detached["plan"]["equipment_inputs"]) == {"receiver"}
    assert detached["plan"]["effect_bindings"][0]["parts"][0]["rule"] == {"kind": "constant", "value": 800}
    after = run_plan_calc(detached["plan"], db)
    assert before["result"]["summary"]["total_damage"] == after["result"]["summary"]["total_damage"]
    detached["plan"]["equipment_inputs"]["receiver"]["weapon"]["weapon_id"] = 2
    changed = run_plan_calc(detached["plan"], db)
    assert changed["result"]["summary"]["total_damage"] > after["result"]["summary"]["total_damage"]


@pytest.mark.parametrize("state,reason", [("invalid", "dataset_invalid"), ("incompatible", "unsupported_schema")])
def test_corrupt_or_future_dataset_is_error_not_successful_pending(database, monkeypatch, state, reason):
    db, _ = database
    monkeypatch.setattr(queries, "get_meta", lambda _: {"datasets": {"equipment": {"lines": {"release": {"state": state}}}}})
    with pytest.raises(PlanError) as caught:
        compose_equipment_plan(plan_fixture(), db)
    assert caught.value.reason == reason
