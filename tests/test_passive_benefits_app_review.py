"""Independent EC12 qualification, execution-copy and acceptance boundaries."""
from __future__ import annotations

import copy

import pytest
from test_mechanics_execution import EVIDENCE, action
from test_passive_sources_app import accept
from test_passive_sources_app import (
    passive_attack_db as passive_attack_db,  # noqa: PLC0414
)
from test_passive_sources_app import passive_plan as passive_plan  # noqa: PLC0414

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError


def switch_to_receiver(plan):
    plan["rotation_model"]["abilities"].append({"id": "switch.b", "character": "a", "action_type_id": "action.switch",
        "cooldown": None, "energy": None, **EVIDENCE})
    plan["rotation_model"]["actions"].append(action("switch", "switch.b", .6, .7, order=30,
        switch_to="b", repeat={"mode": "once"}))


def ready(plan, db):
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "ready", out["missing_inputs"]
    return out


def independent_owner_action(plan):
    plan["rotation_model"]["abilities"].append({"id": "manual.skill", "character": "a", "action_type_id": "action.elemental_skill",
        "cooldown": None, "energy": None, **EVIDENCE})
    plan["rotation_model"]["actions"].append(action("manual.cast", "manual.skill", .6, .9, order=20, repeat={"mode": "once"}))


def test_review_removing_passive_preserves_parent_but_discards_generated_rules_and_gain(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    switch_to_receiver(plan)
    with_passive = run_plan_calc(plan, db)
    parent = with_passive["report"]["action_event_compilations"][0]["profile_fingerprint"]
    plan["passive_sources"] = []
    without = run_plan_calc(plan, db)
    assert without["report"]["action_event_compilations"][0]["profile_fingerprint"] == parent
    assert not without["report"]["mechanics_model"]["rules"]
    assert "passives" not in without["result_receipt"]
    assert next(h["expected"] for h in without["result"]["hits"] if h["id"] == "cryo.damage") == pytest.approx(45)
    assert with_passive["result_receipt"]["action_events"]["compiled_mechanics_model_fingerprint"] != without["result_receipt"]["action_events"]["compiled_mechanics_model_fingerprint"]
    assert plan["mechanics_model"]["rules"] == []


def test_review_retiming_reuses_calibration_acceptance_but_rebuilds_actual_parent_proof(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    original = ready(plan, db)
    plan["rotation_model"]["actions"][0].update(start=.1, occupation_end=.6)
    retimed = ready(plan, db)
    assert retimed["passive_compilations"][0]["accepted_fingerprint"] == original["passive_compilations"][0]["accepted_fingerprint"]
    assert retimed["action_event_compilations"][0]["profile_fingerprint"] == original["action_event_compilations"][0]["profile_fingerprint"]
    assert retimed["passive_compilations"][0]["scope_fingerprint"] != original["passive_compilations"][0]["scope_fingerprint"]
    assert retimed["passive_compilations"][0]["scope"]["provider_parent_events"] != original["passive_compilations"][0]["scope"]["provider_parent_events"]


def test_review_no_actual_provider_actions_is_not_vacuous_c0_e_proof(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    plan["rotation_model"]["actions"] = [a for a in plan["rotation_model"]["actions"] if a["ability_id"] != "e"]
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending"
    assert out["passive_compilations"][0]["status"] == "pending"
    assert out["passive_compilations"][0]["rules"] == []


def test_review_another_actor_manual_wind_is_outside_provider_qualification(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    model = plan["mechanics_model"]
    model["elemental_model"]["profiles"].append({"id": "other.wind", "element": "anemo", "application": "apply",
        "gauge_units": 1, "icd": {"kind": "none"}, **EVIDENCE})
    model["application_events"] = [{"id": "other.apply", "action_id": "b.cast", "t": 1.05, "order": 12,
        "application": {"profile_id": "other.wind", "target_id": "enemy", "landed": True},
        "contact": {"kind": "none", **EVIDENCE}}]
    out = ready(plan, db)
    assert {p["action_id"] for p in out["passive_compilations"][0]["scope"]["provider_parent_events"]} == {"cast"}


def test_review_explicit_nonapplying_owner_event_is_not_misclassified_as_wind_trigger(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    independent_owner_action(plan)
    plan["mechanics_model"]["application_events"] = [{"id": "no.apply", "action_id": "manual.cast", "t": .8, "order": 21,
        "application": {"profile_id": "no.application", "target_id": "enemy", "landed": True},
        "contact": {"kind": "none", **EVIDENCE}}]
    ready(plan, db)


def test_review_receiver_em_write_does_not_change_fixed_provider_em_basis(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    plan["manual_template"]["buffs"].append({"id": "recipient.em", "start": 0, "end": 2, "target": "b",
        "effects": [{"id": "value", "benefit_type_id": "benefit.em.all", "zone": "em", "stat": "all", "value": 100,
            "applies_to": {"mode": "all"}}], "extensions": {"owner": "b", "source_id": "other.source"}})
    plan["manual_effect_dependencies"].append({"effect_ref": {"buff_id": "recipient.em", "effect_id": "value"},
        "mode": "source_only", "context_fingerprint": None, "note": "Independent receiver-only EM"})
    out = ready(plan, db)
    assert out["passive_compilations"][0]["stat_basis"]["panel_value"] == 900


def test_review_roster_change_requires_new_receiver_declaration_acceptance(passive_plan):
    plan, db = passive_plan
    accept(plan, db)
    actor = copy.deepcopy(plan["manual_template"]["characters"][1])
    actor["id"] = "c"
    plan["manual_template"]["characters"].append(actor)
    actor = copy.deepcopy(plan["rotation_model"]["characters"][1])
    actor["id"] = "c"
    plan["rotation_model"]["characters"].append(actor)
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending"
    assert out["passive_compilations"][0]["required_confirmation"]["accepted_fingerprint"] != plan["passive_sources"][0]["accepted_fingerprint"]


def test_review_assumed_basis_stays_assumed_and_private_rules_never_enter_native(passive_plan):
    plan, db = passive_plan
    plan["passive_sources"][0]["scenario"]["status"] = "assumed"
    plan["equipment_inputs"]["a"]["progression"]["status"] = "assumed"
    accept(plan, db)
    before = copy.deepcopy(plan)
    out = ready(plan, db)
    record = out["passive_compilations"][0]
    assert record["stat_basis"]["status"] == "assumed"
    assert record["scope"]["progression"]["status"] == "assumed"
    assert record["coverage_certificate"]["gameplay_verified"] is False
    assert plan == before and out["plan"]["mechanics_model"]["rules"] == []
    assert fingerprint(out["mechanics_model"]) != fingerprint(plan["mechanics_model"])


@pytest.mark.parametrize("kind", ["hidden_miss", "undeclared_profile"])
def test_review_owner_extra_wind_cannot_borrow_qualified_profile_or_hide_behind_miss(passive_plan, kind):
    plan, db = passive_plan
    accept(plan, db)
    independent_owner_action(plan)
    # Reusing the generated metadata-owned profile is a reference to a valid
    # elemental fact, but is not proof that this extra event belongs to its E.
    plan["mechanics_model"]["application_events"] = [{"id": "extra.apply", "action_id": "manual.cast", "t": .8, "order": 21,
        "application": {"profile_id": "metadata.wind" if kind == "hidden_miss" else "absent.wind", "target_id": "enemy", "landed": False},
        "contact": {"kind": "none", **EVIDENCE}}]
    if kind == "hidden_miss":
        with pytest.raises(PlanError, match="不得借用"):
            compose_equipment_plan(plan, db)
        return
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending"
    assert out["passive_compilations"][0]["status"] == "pending"
    assert out["passive_compilations"][0]["rules"] == []
