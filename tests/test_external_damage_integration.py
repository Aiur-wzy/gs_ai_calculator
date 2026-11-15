"""Actual source compilation, admission and dependency coexistence of external values."""

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.external_damage import (
    validate_external_damage_result_receipt,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from tests.test_external_damage import confirm, supplement
from tests.test_plan_mechanics import native, review
from tests.test_verified_empty_damage_engine import missed_fixture


def miss_plan():
    template, rotation, mechanics = missed_fixture()
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics,
                external_damage_supplements=[supplement(action_id="cast")])
    return plan


def test_full_miss_engine_zero_can_add_external_without_faking_a_hit(tmp_path):
    db = tmp_path / "none.db"
    out = run_plan_calc(confirm(miss_plan(), db), db)
    assert out["result"]["hits"] == []
    assert out["result"]["summary"]["total_damage"] == 0
    assert out["result"]["damage_reporting"]["status"] == "verified_zero"
    assert out["result"]["calculation_totals"]["total_damage"] == 12
    assert out["result"]["calculation_totals"]["dps"] == 1.2
    result_bytes(out["result"], out["result_receipt"], "json")


@pytest.mark.parametrize("failure", ["energy", "cooldown"])
def test_failed_parent_cannot_be_replaced_with_external_damage(tmp_path, failure):
    db, plan = tmp_path / "none.db", native()
    plan["rotation_model"]["actions"][0]["repeat"] = {"mode": "once"}
    # A first real hit runs; the second action fails after the prefix. No empty-result shortcut.
    second = copy.deepcopy(plan["rotation_model"]["actions"][0])
    second.update(id="later", start=2, occupation_end=2.1, order=1)
    plan["rotation_model"]["actions"].append(second)
    ability = plan["rotation_model"]["abilities"][0]
    if failure == "energy":
        ability["energy"] = {"required": 10, "amount": 10}
        # Existing explicit-spend contract remains valid; the second cannot be admitted.
        for index, aid in enumerate(("cast", "later")):
            plan["rotation_model"]["events"].append({"id": "spend" + str(index), "type": "energy_spend",
                "t": 2 * index, "order": 5, "character": "a", "amount": 10, "required": 10,
                "action_id": aid, "repeat": {"mode": "once"}, "status": "assumed", "source": "工程扣能"})
    else:
        ability["cooldown"] = {"group": "e", "duration": 5, "charges": 1, "start": "action_start", "recovery": "sequential"}
        plan["rotation_model"]["initial_cooldowns"] = [{"character": "a", "group": "e", "ready_in": []}]
    review(plan, db)
    baseline = run_plan_calc(plan, db)
    assert baseline["result"]["rotation_check"]["status"] == "failed"
    plan["external_damage_supplements"] = [supplement(action_id="later", t=2.5)]
    confirm(plan, db)
    with pytest.raises(PlanError, match="父动作|失败|未完成"):
        run_plan_calc(plan, db)


def test_manual_context_and_match_stay_accepted_but_business_edits_do_not(tmp_path):
    db, plan = tmp_path / "none.db", native()
    part = plan["effect_bindings"][0]["parts"][0]
    part["dependencies"]["mode"] = "manual_context"
    match = {"id": "exception", "effect_ref": {"buff_id": "boost", "effect_id": "value"}, "mode": "adjust",
             "include_hit_ids": [], "exclude_hit_ids": ["h0"], "reason": "工程排除首击收益",
             "source_refs": [{"kind": "manual", "note": "工程"}], "state": "pending", "basis_fingerprint": None}
    plan["rotation_tuning"]["buff_hit_matches"] = [match]
    review(plan, db)
    preview = compose_equipment_plan(plan, db)
    part["dependencies"]["context_fingerprint"] = preview["context_fingerprint"]
    match.update(state="confirmed", basis_fingerprint=preview["match_previews"][0]["basis_fingerprint"])
    baseline = run_plan_calc(plan, db)
    plan["external_damage_supplements"] = [supplement(action_id="cast")]
    confirm(plan, db)
    actual = run_plan_calc(plan, db)
    assert actual["result"]["hits"] == baseline["result"]["hits"]
    plan["external_damage_supplements"][0]["expected"] = 13
    issues = compose_equipment_plan(plan, db)["missing_inputs"]
    assert {i["code"] for i in issues} == {"external_damage_confirmation_required"}
    plan["mechanics_model"]["rules"][0]["timing"]["duration"] = 3
    issues = compose_equipment_plan(plan, db)["missing_inputs"]
    assert {i["code"] for i in issues} - {"external_damage_confirmation_required"}


def test_rehashing_outer_and_external_proof_cannot_make_arithmetic_consistent(tmp_path):
    from genshin_app.equipment.external_damage import _proof
    from tests.test_external_damage import external_plan
    db = tmp_path / "none.db"
    calculated = run_plan_calc(confirm(external_plan(), db), db)
    result, receipt = calculated["result"], calculated["result_receipt"]
    result["calculation_totals"]["external_total_damage"] = 999
    receipt["external_damage"] = _proof(result)
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_external_damage_result_receipt(result, receipt)
