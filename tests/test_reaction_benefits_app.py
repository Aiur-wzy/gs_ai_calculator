"""Fresh source composition and bounded scope for ordinary reaction benefits."""
from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from test_mechanics_execution import EVIDENCE, ability, action, fixture

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint, input_view, read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_engine.elemental_schema import FREEZE_RULESET, elemental_capabilities


@pytest.fixture
def reaction_plan(tmp_path, monkeypatch):
    from genshin_db import queries, runtime_queries
    from genshin_engine.runtime_reaction_benefit_compiler import (
        reaction_benefit_compiler_caps,
    )

    template, rotation, model = fixture((.2,))
    template["characters"][0]["stats"]["energy_recharge"] = 1
    template["hits"][0]["element"] = "anemo"
    rotation["characters"][0]["element"] = "anemo"
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    contract = elemental_capabilities()["reference_contract"]
    model["elemental_model"] = {
        "schema_version": 1, "ruleset": FREEZE_RULESET,
        "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known", **EVIDENCE,
                   "initial_state": {"ordinary_aura": {"element": "cryo", "remaining_gu": 2,
                       "decay_gu_per_second": .01, "source": "有限工程冰底"},
                       "frozen": None, "freeze_history_seconds": 0, **EVIDENCE},
                   "freeze": {"freezable": True, "resistance": 0, **EVIDENCE}},
        "profiles": [{"id": "wind", "element": "anemo", "application": "apply", "gauge_units": 1,
                      "icd": {"kind": "none"}, **EVIDENCE}], "contact_profiles": [],
        "swirl_damage": {"icd": {"kind": "sequence", "reset_seconds": .5, "sequence": [1, 1, 0]}, **EVIDENCE},
        "reference_acceptance": {"contract_id": contract["contract_id"], "contract_fingerprint": contract["contract_fingerprint"],
                                 "acknowledged": True, "source": "明确参考边界的工程试验"}}
    model["hit_events"][0].update(application={"profile_id": "wind", "target_id": "enemy", "landed": True},
                                  contact={"kind": "none", **EVIDENCE})
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    db = tmp_path / "reaction-fixture.db"
    sqlite3.connect(db).close()
    bundle = json.loads((Path(__file__).parent / "fixtures/runtime_reaction_benefit_vv_current.json").read_text("utf8"))
    version = {"version_line": "release", "upstream_commit": bundle["source"]["upstream_commit"],
               "equipment_schema_version": 1, "built_at": "isolated-test"}
    monkeypatch.setattr(queries, "get_meta", lambda *a, **k: {"datasets": {"equipment": {"lines": {"release": {
        "state": "ready", "upstream_commit": version["upstream_commit"], "schema_version": 1, "built_at": "isolated-test"}}}}})
    monkeypatch.setattr(queries, "get_artifact_set_detail", lambda *a, **k: {
        "artifact_set": {"name": "真实原文试验套装"}, "effects": [{"required_count": 4, "effect": {
            "affix_id": bundle["source"]["affix_id"], "name": "普通扩散效果", "source_refs": [{"kind": "database"}]}}],
        "annotations": []})
    reads = []

    def assemble(*args):
        reads.append(args)
        return copy.deepcopy(bundle)

    monkeypatch.setattr(runtime_queries, "assemble_runtime_source", assemble)
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **k: pytest.fail("reaction source must not query burst identity"))
    plan["equipment_inputs"] = {"a": {"mode": "panel", "panel_policy": "static_included", "character_ref": None,
        "weapon": None, "artifact_sets": [{"set_id": 900001, "count": 4}],
        "source_refs": [{"kind": "manual", "note": "给定面板含常驻风伤，未重复计入扩散加成"}], "data_version": version}}
    requirement = compose_equipment_plan(plan, db)["effect_requirements"][0]
    binding = copy.deepcopy(requirement["proposed_binding"])
    scenario = reaction_benefit_compiler_caps()["default_scenario"]
    scenario.update(target_id="enemy", origin_target_range="within_source_range", initial_source_effects="none",
                    event_order_policy="declared_atomic_hit_order", status="assumed", source="明确原目标、固定单人装备与原子顺序工程假设")
    binding["upstream_recipe"] = {"schema_version": 1, "scenario": scenario}
    plan["effect_bindings"] = [binding]
    return plan, db, bundle, reads


def accept(plan, db):
    report = compose_equipment_plan(plan, db)
    plan["effect_bindings"][0]["upstream_recipe"]["scenario"].update(report["upstream_compilations"][0]["required_confirmation"])
    return report


def withdrawn(report):
    assert report["state"] == "pending"
    assert report["plan"]["resolved_template"] is None
    compiled = report["upstream_compilations"][0]
    assert all(compiled.get(field) == [] for field in ("parts", "static_parts", "rules", "initial_rules"))
    assert not report["mechanics_model"]["rules"]
    assert not report["mechanics_model"]["initial_state"]["rules"]
    assert not compiled["coverage_certificate"]["projection_complete"]


def test_fresh_real_source_uses_reaction_route_three_distinct_parts_and_preserves_native(reaction_plan, tmp_path):
    plan, db, bundle, reads = reaction_plan
    withdrawn(accept(plan, db))
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == before
    compiled = out["report"]["upstream_compilations"][0]
    assert len({p["part_id"] for p in compiled["parts"]}) == 3
    assert {p["part_id"] for p in compiled["parts"]} == {"upstream.swirl_bonus", "upstream.shred.hydro", "upstream.shred.cryo"}
    assert len(out["report"]["mechanics_model"]["rules"]) == 2
    assert all("action_ids" not in rule["trigger"] for rule in compiled["rules"])
    assert compiled["execution_binding"]["kind"] == "elemental_reaction"
    assert compiled["whole_source_status"] == "partial"
    assert compiled["coverage_certificate"]["source_complete"] is False
    bonus, shred = bundle["param_list"][:2]
    assert [hit["expected"] for hit in out["result"]["hits"]] == pytest.approx([45, 868.11*(1+bonus)*(1-(.1-shred)/2)])
    assert out["result_receipt"]["input_fingerprint"] == fingerprint(input_view(before))
    assert not out["plan"]["effect_bindings"][0]["parts"]
    assert not out["plan"]["manual_template"]["buffs"] and not out["plan"]["mechanics_model"]["rules"]
    saved = tmp_path / "native.plan.json"
    saved.write_text(json.dumps(out["plan"]), "utf8")
    assert run_plan_calc(read_plan(saved)["plan"], db)["result"] == out["result"]
    assert len(reads) == 3


@pytest.mark.parametrize("mutation", ["space", "initial", "atomic", "status", "target", "reference", "known", "once", "qrefs", "lunar", "stellar"])
def test_scope_and_unknowns_withdraw_every_generated_delivery(reaction_plan, mutation):
    plan, db, _, _ = reaction_plan
    accept(plan, db)
    scenario = plan["effect_bindings"][0]["upstream_recipe"]["scenario"]
    model = plan["mechanics_model"]
    if mutation in ("space", "initial", "atomic"):
        field = {"space": "origin_target_range", "initial": "initial_source_effects", "atomic": "event_order_policy"}[mutation]
        scenario[field] = "unknown"
    elif mutation == "status":
        scenario["status"] = "pending"
    elif mutation == "target":
        scenario["target_id"] = "other-target"
    elif mutation == "reference":
        model["elemental_model"]["reference_acceptance"]["acknowledged"] = False
    elif mutation == "known":
        model["elemental_model"]["target"]["knowledge"] = "unknown"
    elif mutation == "once":
        model["manual_buff_repeat"] = "once"
    elif mutation == "qrefs":
        plan["effect_bindings"][0]["upstream_recipe"]["ability_refs"] = [{"ability_id": "e", "skill_id": 10019}]
    else:
        plan["manual_template"]["hits"][0]["damage_system"] = mutation
    accept(plan, db)  # A fresh acceptance must never upgrade an unknown scope.
    withdrawn(compose_equipment_plan(plan, db))


def test_offfield_only_stops_dynamic_shred_not_fixed_owner_bonus(reaction_plan):
    plan, db, bundle, _ = reaction_plan
    template, rotation = plan["manual_template"], plan["rotation_model"]
    other = copy.deepcopy(template["characters"][0])
    other["id"] = "b"
    template["characters"].append(other)
    other = copy.deepcopy(rotation["characters"][0])
    other["id"] = "b"
    rotation["characters"].append(other)
    rotation["abilities"].append(ability("switch", kind="action.switch"))
    rotation["actions"].append(action("leave", "switch", .15, .16, 2, switch_to="b", repeat={"mode": "once"}))
    accept(plan, db)
    out = run_plan_calc(plan, db)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 781.299*(1+bundle["param_list"][0])])
    assert not any(row["type"] == "rule_triggered" for row in out["result"]["mechanics_trace"])


@pytest.mark.parametrize("line", ["release", "beta"])
def test_other_equipped_provider_is_detected_without_needing_second_binding(reaction_plan, line):
    plan, db, _, _ = reaction_plan
    other = copy.deepcopy(plan["manual_template"]["characters"][0])
    other["id"] = "b"
    plan["manual_template"]["characters"].append(other)
    plan["equipment_inputs"]["b"] = copy.deepcopy(plan["equipment_inputs"]["a"])
    plan["equipment_inputs"]["b"]["data_version"]["version_line"] = line
    accept(plan, db)
    report = compose_equipment_plan(plan, db)
    withdrawn(report)
    compiled = report["upstream_compilations"][0]
    assert compiled["app_scope"]["status"] == "pending"
    assert any("另一提供者实际装备同源四件套" in issue["message"] for issue in compiled["issues"])


def test_forged_saved_receipt_and_changed_source_never_restore_old_deliveries(reaction_plan):
    plan, db, bundle, _ = reaction_plan
    accept(plan, db)
    old = run_plan_calc(plan, db)
    cached = copy.deepcopy(old["plan"])
    cached["resolution"]["upstream_compilations"][0]["status"] = "ready"
    cached["resolved_template"]["characters"][0]["stats"]["atk"] = 999999
    assert run_plan_calc(cached, db)["result"] == old["result"]
    bundle["abilities"][0]["payload"]["unreviewed_callback"] = {"value": 1}
    changed = compose_equipment_plan(cached, db)
    withdrawn(changed)
    assert changed["source_fingerprint"] != old["report"]["source_fingerprint"]


def test_unavailable_fresh_source_withdraws_static_and_dynamic_cache_together(reaction_plan, monkeypatch):
    from genshin_db import runtime_queries
    plan, db, _, _ = reaction_plan
    accept(plan, db)
    old = run_plan_calc(plan, db)

    def unavailable(*args):
        raise ValueError("受控来源不可读")

    monkeypatch.setattr(runtime_queries, "assemble_runtime_source", unavailable)
    withdrawn(compose_equipment_plan(old["plan"], db))


@pytest.mark.parametrize("remaining_count", [0, 2])
def test_clearly_unequipped_saved_automatic_recipe_is_inactive_not_a_competitor(reaction_plan, remaining_count):
    from test_mechanics_execution import second_character
    plan, db, _, _ = reaction_plan
    second_character(plan["manual_template"], plan["rotation_model"])
    plan["manual_template"]["characters"][1]["stats"]["energy_recharge"] = 1
    plan["equipment_inputs"]["b"] = copy.deepcopy(plan["equipment_inputs"]["a"])
    other = copy.deepcopy(plan["effect_bindings"][0])
    other.update(binding_id="old-source-b", provider_character_id="b")
    plan["effect_bindings"].append(other)
    plan["equipment_inputs"]["b"]["artifact_sets"] = ([{"set_id": 900001, "count": remaining_count}] if remaining_count else [])
    accept(plan, db)
    out = run_plan_calc(plan, db)
    assert [r["status"] for r in out["report"]["upstream_compilations"]] == ["ready", "inactive"]
    assert len(out["report"]["mechanics_model"]["rules"]) == 2


def test_absent_requirement_does_not_prove_unknown_or_manual_equipment_is_inactive(reaction_plan):
    from genshin_app.equipment.upstream import _inactive_equipment_bindings
    plan, db, _, _ = reaction_plan
    requirements = compose_equipment_plan(plan, db)["effect_requirements"]
    other = copy.deepcopy(plan["effect_bindings"][0])
    other.update(binding_id="other-source", provider_character_id="b")
    plan["effect_bindings"].append(other)
    assert _inactive_equipment_bindings(plan, requirements) == set()  # No equipment input: unknown.
    plan["equipment_inputs"]["b"] = copy.deepcopy(plan["equipment_inputs"]["a"])
    assert _inactive_equipment_bindings(plan, requirements) == set()  # Equipped; missing DB requirement cannot excuse it.
    plan["equipment_inputs"]["b"]["artifact_sets"] = []
    assert _inactive_equipment_bindings(plan, requirements) == {"other-source"}
    del other["upstream_recipe"]
    other["owner"] = {"kind": "manual", "note": "显式同源人工收益"}
    assert _inactive_equipment_bindings(plan, requirements) == set()  # Manual declaration is still active scope.


def test_unselected_source_is_inactive_and_no_static_bonus_remains(reaction_plan):
    plan, db, _, _ = reaction_plan
    accept(plan, db)
    plan["equipment_inputs"]["a"]["artifact_sets"] = []
    out = run_plan_calc(plan, db)
    assert out["report"]["upstream_compilations"][0]["status"] == "inactive"
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 781.299])


def test_copied_manual_source_ids_and_typed_affix_alias_cannot_double_deliver(reaction_plan):
    plan, db, bundle, _ = reaction_plan
    accept(plan, db)
    out = run_plan_calc(plan, db)
    copied = copy.deepcopy(out["report"]["mechanics_model"]["rules"])
    plan["mechanics_model"]["rules"] = copied
    plan["mechanics_model"]["initial_state"] = copy.deepcopy(out["report"]["mechanics_model"]["initial_state"])
    with pytest.raises(PlanError, match="保留命名空间"):
        compose_equipment_plan(plan, db)
    plan["mechanics_model"]["rules"] = []
    plan["mechanics_model"]["initial_state"]["rules"] = []
    alias = copy.deepcopy(plan["effect_bindings"][0])
    alias["binding_id"] = "manual-alias"
    alias["owner"] = {"kind": "manual", "note": "同来源精确引用"}
    alias["source_refs"] = [{"kind": "database", "affix_id": bundle["source"]["affix_id"], "version_line": "release"}]
    del alias["upstream_recipe"]
    plan["effect_bindings"].append(alias)
    with pytest.raises(PlanError, match="同一装备效果"):
        compose_equipment_plan(plan, db)


@pytest.mark.parametrize("field,value", [("kind", "unreviewed_certificate"), ("projection_complete", False), ("source_complete", True)])
def test_projection_certificate_whitelist_is_exact(reaction_plan, field, value):
    from genshin_app.equipment.compose_sources import _approved_projection
    plan, db, _, _ = reaction_plan
    accept(plan, db)
    compiled = compose_equipment_plan(plan, db)["upstream_compilations"][0]
    assert _approved_projection(compiled, plan["effect_bindings"][0])
    compiled["coverage_certificate"][field] = value
    assert not _approved_projection(compiled, plan["effect_bindings"][0])
