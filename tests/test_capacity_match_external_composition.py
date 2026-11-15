"""Capacity, all three match routes, and external numeric delivery share one proof graph.

The database and combat inputs in this module are synthetic engineering fixtures.
They prove App contracts only and are not character or gameplay facts.
"""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from test_capacity_inputs import input_plan
from test_freeze_review import SAFE_CONTACT, fresh_model
from test_target_state_review import aura, fixed_source

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.data_version import equipment_data_version
from genshin_app.equipment.delivery import (
    build_calculation_delivery,
    validate_delivery_calculated,
)
from genshin_app.equipment.files import (
    context_view,
    fingerprint,
    input_view,
    read_plan,
    save_plan,
)
from genshin_app.equipment.service import result_bytes
from genshin_db.queries import get_meta
from tests.test_external_damage import supplement
from tests.test_plan_mechanics import native, review

pytest_plugins = ("test_capacity_model",)

MATCH_IDS = ("native-match", "fixed-match", "template-match")


def _binding(binding_id, part_id, rule):
    return {
        "binding_id": binding_id,
        "provider_character_id": "a",
        "owner": {"kind": "manual", "note": "通用组合工程来源"},
        "source_refs": [{"kind": "manual", "note": "合成测试，不是游戏事实"}],
        "source_fingerprint": None,
        "coverage": {
            "state": "unreviewed",
            "reviewed_by": None,
            "source_fingerprint": None,
            "note": None,
        },
        "parts": [{
            "part_id": part_id,
            "label": "通用组合工程收益",
            "state": "resolved",
            "conditions": [],
            "rule": rule,
            "delivery": {"kind": rule["kind"]},
            "reason": None,
            "dependencies": {
                "mode": "source_only",
                "context_fingerprint": None,
                "note": "明确的合成测试来源",
            },
        }],
    }


def composition_plan(db, *, capacity=False):
    """Reuse the existing native, fixed-source, template-Buff and capacity fixtures."""
    plan = native()
    capacity_fixture = input_plan()
    gear = copy.deepcopy(capacity_fixture["equipment_inputs"]["a"])
    gear["data_version"] = equipment_data_version(get_meta(db), "release")
    plan["equipment_inputs"] = {"a": gear}

    ability = plan["rotation_model"]["abilities"][0]
    ability.update(
        action_type_id="action.elemental_skill",
        variant="tap",
        cooldown={
            "group": "e",
            "duration": 10,
            "start": "action_start",
            "recovery": "sequential",
        },
        energy=None,
    )
    plan["rotation_model"]["initial_cooldowns"] = [{
        "character": "a", "group": "e", "ready_in": [],
    }]
    plan["rotation_model"]["actions"][0]["repeat"] = {"mode": "once"}

    mechanics = plan["mechanics_model"]
    mechanics["elemental_model"] = fresh_model(aura())
    for descriptor in mechanics["hit_events"]:
        descriptor["application"] = {
            "profile_id": "cryo1", "target_id": "enemy", "landed": True,
        }
        descriptor["contact"] = copy.deepcopy(SAFE_CONTACT)

    fixed = fixed_source()
    fixed["source_id"] = "fixed-binding:fixed"
    mechanics["fixed_effect_sources"] = [fixed]
    plan["effect_bindings"].append(_binding(
        "fixed-binding", "fixed",
        {"kind": "fixed_effect_source", "fixed_source_id": "review-fixed"},
    ))

    plan["manual_template"]["buffs"] = [{
        "id": "manual-bonus",
        "source": "通用组合模板 Buff",
        "start": 0,
        "end": 9,
        "scope": "team",
        "target": "a",
        "extensions": {"owner": "a", "source_id": "manual:template-buff"},
        "effects": [{
            "id": "bonus",
            "benefit_type_id": "benefit.dmg_bonus.all",
            "zone": "dmg_bonus",
            "stat": "all",
            "value": .5,
            "applies_to": {"mode": "all"},
        }],
    }]
    plan["manual_effect_dependencies"] = [{
        "effect_ref": {"buff_id": "manual-bonus", "effect_id": "bonus"},
        "mode": "source_only",
        "context_fingerprint": None,
        "note": "模板常量不依赖配装数值",
    }]
    plan["rotation_tuning"]["buff_hit_matches"] = [
        {
            "id": "native-match",
            "effect_ref": {"buff_id": "boost", "effect_id": "value"},
            "mode": "adjust",
            "include_hit_ids": [],
            "exclude_hit_ids": ["h0"],
            "reason": "工程排除首击原生机制收益",
            "source_refs": [{"kind": "manual", "note": "通用组合检查"}],
            "state": "pending",
            "basis_fingerprint": None,
        },
        {
            "id": "fixed-match",
            "effect_ref": {"fixed_source_id": "review-fixed", "effect_id": "ice"},
            "mode": "adjust",
            "include_hit_ids": ["h0"],
            "exclude_hit_ids": ["h1"],
            "reason": "工程固定效果逐击覆盖",
            "source_refs": [{"kind": "manual", "note": "通用组合检查"}],
            "state": "pending",
            "basis_fingerprint": None,
        },
        {
            "id": "template-match",
            "effect_ref": {"buff_id": "manual-bonus", "effect_id": "bonus"},
            "mode": "adjust",
            "include_hit_ids": [],
            "exclude_hit_ids": ["h2"],
            "reason": "工程排除末击模板收益",
            "source_refs": [{"kind": "manual", "note": "通用组合检查"}],
            "state": "pending",
            "basis_fingerprint": None,
        },
    ]
    plan["external_damage_supplements"] = [supplement(action_id="cast")]
    if capacity:
        plan["action_capacity_sources"] = copy.deepcopy(capacity_fixture["action_capacity_sources"])
        plan["action_capacity_context"] = copy.deepcopy(capacity_fixture["action_capacity_context"])
    return plan


def _accept_bindings(plan, db):
    review(plan, db)


def _accept_capacity(plan, report):
    records = report.get("action_capacity_compilations") or []
    assert len(records) == 1 and records[0]["required_confirmation"]
    plan["action_capacity_sources"][0].update(records[0]["required_confirmation"])


def _accept_matches(plan, report):
    previews = {row["id"]: row for row in report["match_previews"]}
    assert set(previews) == set(MATCH_IDS)
    for match in plan["rotation_tuning"]["buff_hit_matches"]:
        basis = previews[match["id"]]["basis_fingerprint"]
        assert basis
        match.update(state="confirmed", basis_fingerprint=basis)


def _accept_external(plan, report):
    review_rows = report["external_damage_review"]["items"]
    assert len(review_rows) == 1 and review_rows[0]["required_confirmation"]
    plan["external_damage_supplements"][0].update(review_rows[0]["required_confirmation"])


def confirm_all(plan, db):
    _accept_bindings(plan, db)
    report = compose_equipment_plan(plan, db)
    capacity_fp = None
    if plan.get("action_capacity_sources"):
        record = report["action_capacity_compilations"][0]
        source = next(row for row in report["external_damage_source_components"]
                      if row.get("kind") == "action_capacity" and row.get("id") == record["id"])
        capacity_fp = source["compilation_fingerprint"]
        if record["status"] != "ready":
            _accept_capacity(plan, report)
    _accept_matches(plan, report)
    match_bases = {row["id"]: row["basis_fingerprint"] for row in report["match_previews"]}
    _accept_external(plan, report)
    external_basis = report["external_damage_review"]["basis_fingerprint"]

    # Capacity, all three match routes and external numeric are written back
    # from this one report.  The next compose must settle without another
    # acknowledgement round.
    ready_report = compose_equipment_plan(plan, db)
    assert ready_report["state"] == "ready", ready_report["missing_inputs"]
    if capacity_fp is not None:
        assert ready_report["action_capacity_compilations"][0]["compilation_fingerprint"] == capacity_fp
    assert ready_report["external_damage_review"]["basis_fingerprint"] == external_basis
    assert {row["id"]: row["basis_fingerprint"] for row in ready_report["match_previews"]} == match_bases
    assert compose_equipment_plan(plan, db) == ready_report
    return ready_report


def confirm_all_serial(plan, db):
    """Reach a ready legacy plan so execution-receipt bugs remain isolated."""
    _accept_bindings(plan, db)
    report = compose_equipment_plan(plan, db)
    if plan.get("action_capacity_sources") and report["action_capacity_compilations"][0]["status"] != "ready":
        _accept_capacity(plan, report)
        report = compose_equipment_plan(plan, db)
    _accept_matches(plan, report)
    report = compose_equipment_plan(plan, db)
    _accept_external(plan, report)
    ready_report = compose_equipment_plan(plan, db)
    assert ready_report["state"] == "ready", ready_report["missing_inputs"]
    return ready_report


def test_capacity_addition_invalidates_then_all_confirmations_settle_once(ready):
    db = ready[0]
    plan = composition_plan(db)
    before = confirm_all(plan, db)
    old_matches = {row["id"]: row["basis_fingerprint"] for row in before["match_previews"]}
    old_external = before["external_damage_review"]["basis_fingerprint"]

    capacity_fixture = input_plan()
    plan["action_capacity_sources"] = copy.deepcopy(capacity_fixture["action_capacity_sources"])
    plan["action_capacity_context"] = copy.deepcopy(capacity_fixture["action_capacity_context"])
    changed = compose_equipment_plan(plan, db)
    assert changed["state"] == "pending"
    pending_capacity = changed["action_capacity_compilations"][0]
    assert pending_capacity["required_confirmation"]
    assert "compiled_values" not in pending_capacity and "compilation_fingerprint" not in pending_capacity
    ability_id = plan["action_capacity_sources"][0]["ability_ids"][0]
    ability = next(row for row in changed["compiled_rotation_model"]["abilities"] if row["id"] == ability_id)
    assert "charges" not in ability["cooldown"] and "capacity_source" not in ability["cooldown"]
    assert any(row.get("kind") == "action_capacity"
               for row in changed["external_damage_source_components"])
    assert all(row["basis_fingerprint"] != old_matches[row["id"]] for row in changed["match_previews"])
    assert changed["external_damage_review"]["basis_fingerprint"] != old_external
    assert changed["external_damage_review"]["status"] == "pending"

    ready_report = confirm_all(plan, db)
    for _ in range(2):
        assert compose_equipment_plan(plan, db) == ready_report
    first = run_plan_calc(plan, db, capture_display=True)
    second = run_plan_calc(plan, db, capture_display=True)
    assert first["result"] == second["result"]
    assert first["report"]["source_fingerprint"] == second["report"]["source_fingerprint"]
    assert first["report"]["match_previews"] == second["report"]["match_previews"]
    assert first["report"]["external_damage_review"] == second["report"]["external_damage_review"]
    assert first["result_receipt"]["result_fingerprint"] == second["result_receipt"]["result_fingerprint"]
    assert first["result_receipt"]["external_damage"] == second["result_receipt"]["external_damage"]
    assert first["result_receipt"]["action_capacities"] == second["result_receipt"]["action_capacities"]
    assert first["result"]["calculation_totals"]["external_total_damage"] == 12

    capacity_component = next(
        row for row in first["result"]["external_damage"]["source_components"]
        if row.get("kind") == "action_capacity"
    )
    compilation = first["report"]["action_capacity_compilations"][0]
    assert capacity_component == {
        "kind": "action_capacity",
        "id": compilation["id"],
        "source_fingerprint": compilation["source_fingerprint"],
        "compilation_fingerprint": compilation["compilation_fingerprint"],
    }
    components = first["result"]["external_damage"]["source_components"]
    assert fingerprint(sorted(components, key=fingerprint)) == first["report"]["source_fingerprint"]
    expected_native_basis = fingerprint({
        "source_fingerprint": first["report"]["source_fingerprint"],
        "context": context_view(input_view(plan)),
        "match_id": "native-match",
    })
    by_match = {row["id"]: row for row in first["report"]["match_previews"]}
    assert by_match["native-match"]["basis_fingerprint"] == expected_native_basis
    assert by_match["fixed-match"]["basis_fingerprint"] == fingerprint({
        "source_fingerprint": first["report"]["source_fingerprint"],
        "context": context_view(input_view(plan)),
        "match_id": "fixed-match",
    })


@pytest.mark.parametrize("mutation", ["selection", "equipment", "initial_ready_in"])
def test_capacity_business_change_invalidates_capacity_all_matches_and_external(ready, mutation):
    db = ready[0]
    plan = composition_plan(db, capacity=True)
    confirmed = confirm_all(plan, db)
    old_capacity = confirmed["action_capacity_compilations"][0]["source_fingerprint"]
    old_matches = {row["id"]: row["basis_fingerprint"] for row in confirmed["match_previews"]}
    old_external = confirmed["external_damage_review"]["basis_fingerprint"]
    if mutation == "selection":
        plan["equipment_inputs"]["a"]["progression"]["constellation"] = 0
    elif mutation == "equipment":
        plan["equipment_inputs"]["a"]["weapon"]["refinement"] = 2
    else:
        plan["rotation_model"]["initial_cooldowns"][0]["ready_in"] = [5]

    changed = compose_equipment_plan(plan, db)
    assert changed["state"] == "pending"
    capacity_record = changed["action_capacity_compilations"][0]
    assert capacity_record["status"] != "ready"
    assert capacity_record["source_fingerprint"] != old_capacity
    assert all(row["basis_fingerprint"] != old_matches[row["id"]] for row in changed["match_previews"])
    assert changed["external_damage_review"]["basis_fingerprint"] != old_external
    assert changed["external_damage_review"]["status"] == "pending"


def test_confirmation_writeback_and_editor_panel_preference_do_not_cycle(ready):
    db = ready[0]
    plan = composition_plan(db, capacity=True)
    confirmed = confirm_all(plan, db)
    signatures = {
        "capacity": confirmed["action_capacity_compilations"][0]["source_fingerprint"],
        "matches": {row["id"]: row["basis_fingerprint"] for row in confirmed["match_previews"]},
        "external": confirmed["external_damage_review"]["basis_fingerprint"],
    }
    before_context = context_view(input_view(plan))
    assert "panel_basis_mode" not in before_context["equipment_inputs"]["a"]
    plan["equipment_inputs"]["a"]["panel_basis_mode"] = "database"
    assert context_view(input_view(plan)) == before_context
    changed = compose_equipment_plan(plan, db)
    assert changed["state"] == "ready", changed["missing_inputs"]
    assert changed["action_capacity_compilations"][0]["source_fingerprint"] == signatures["capacity"]
    assert {row["id"]: row["basis_fingerprint"] for row in changed["match_previews"]} == signatures["matches"]
    assert changed["external_damage_review"]["basis_fingerprint"] == signatures["external"]


def test_native_combination_save_reopen_offline_and_full_delivery(ready, tmp_path, monkeypatch):
    db = ready[0]
    plan = composition_plan(db, capacity=True)
    confirm_all_serial(plan, db)
    calculated = run_plan_calc(plan, db, capture_display=True)
    saved_path = tmp_path / "capacity-match-external.plan.json"
    saved = save_plan(saved_path, calculated["plan"])
    reopened = run_plan_calc(read_plan(saved_path)["plan"], db, capture_display=True)
    assert reopened["result"] == calculated["result"]
    assert reopened["report"]["source_fingerprint"] == calculated["report"]["source_fingerprint"]

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("offline path opened SQLite"))
    validate_delivery_calculated(calculated)
    validate_delivery_calculated(reopened)
    assert json.loads(result_bytes(calculated["result"], calculated["result_receipt"], "json"))["result"] == calculated["result"]
    built = build_calculation_delivery(calculated, input_file_sha=saved["file_sha"])
    assert set(built["artifacts"]) == {
        "native_plan", "calculated", "result_workbook", "formula_workbook",
    }
    assert all(built["artifacts"][role].startswith(b"PK") for role in ("result_workbook", "formula_workbook"))
    validate_delivery_calculated(json.loads(built["artifacts"]["calculated"])["calculated"])
