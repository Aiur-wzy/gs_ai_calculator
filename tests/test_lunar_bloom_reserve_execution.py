"""Declared party reserves share actual Lunar Bloom spend and damage receipts."""
from __future__ import annotations

import json

import pytest

from genshin_app.authoring_assistance import authoring_assistance
from genshin_app.equipment import enable_mechanics, new_plan
from genshin_app.equipment.schema import validate_plan
from genshin_app.input_editing import prepare_input_edit
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.special_reactions import direct_lunar_damage_profile_content_hash
from tests.test_mechanics_execution import add, rule
from tests.test_special_reaction_mechanics_projection import (
    _application_fixture,
    _enable,
    aura,
    direct_lunar_profile,
)


def scenario(*, ordinary=False, balance=2, duration=8, consume=2, partial=False, mode="scale_multiplier"):
    template, rotation, model = _application_fixture(
        "hydro", initial=[aura("dendro", .7, "a")] if ordinary else [], times=(.2,))
    template["characters"][0]["stats"]["em"] = 500
    rotation["abilities"][0].update(variant="tap", effective_level=10)
    _enable(model, "lunar_bloom")
    profile = direct_lunar_profile("lunar_bloom")
    profile["ability_binding"] = {
        "character_id": "a", "ability_id": "e", "component_id": profile["parameters"]["component"],
        "variant_id": "tap", "effective_level": 10, "database_fingerprint": "1" * 64,
    }
    profile["profile_content_hash"] = direct_lunar_damage_profile_content_hash(profile)
    context = {"action_id": "cast", "target_id": "enemy", "area_id": "battlefield.primary",
               "status": "assumed", "source": "Declared synthetic equivalent resource"}
    model["special_reaction_events"] = [
        {**context, "id": "reserve", "kind": "lunar_bloom_reserve_grant", "t": .3, "order": 16,
         "pool_id": "bonus", "amount": balance, "capacity": 2, "duration_seconds": duration},
        {**context, "id": "spend", "kind": "lunar_bloom_consume", "t": 3, "order": 20,
         "dew": consume, "reserve_pool_ids": ["bonus"],
         **({"spend_policy": "up_to_available"} if partial else {})},
        {**context, "id": "consume-damage", "kind": "direct_lunar_damage", "t": 3, "order": 21,
         "status": profile["status"], "ability_profile": profile,
         "dew_consumption": {"event_id": "spend", "mode": mode}},
    ]
    return template, rotation, model


def receipts(output):
    trace = output["special_reaction_trace"]
    return {row["operation"]: row for row in trace if row.get("operation") in {
        "grant_dew_reserve", "consume_dew", "expire_dew_reserve"}}


@pytest.mark.parametrize(("ordinary", "consume", "expected"), [
    (False, 2, [("bonus", 2)]),
    (True, 3, [("ordinary", 1), ("bonus", 2)]),
])
def test_mixed_reserves_are_spent_in_order_and_the_same_count_drives_damage(ordinary, consume, expected):
    template, rotation, model = scenario(ordinary=ordinary, consume=consume)
    out = execute_mechanics(template, rotation, model)
    assert out["status"] in {"passed", "unproven"}, out["issues"]
    debit = receipts(out)["consume_dew"]
    assert debit["amount"] == consume
    assert [(row["pool_id"], row["amount"]) for row in debit["pool_debits"]] == expected
    hit = next(row for row in out["resolved_template"]["hits"] if row["damage_system"] == "lunar")
    assert hit["multiplier"] == model["special_reaction_events"][2]["ability_profile"]["parameters"]["multiplier"] * consume
    assert hit["extensions"]["elemental"]["dew_consumption"]["run_id"] == debit["event_id"]


def test_expiry_and_insufficiency_do_not_partially_charge_or_emit_damage():
    out = execute_mechanics(*scenario(balance=1, duration=1))
    debit = receipts(out)["consume_dew"]
    assert debit["status"] == "rejected" and debit["available"] == 0
    expiry = receipts(out)["expire_dew_reserve"]
    assert expiry["t"] == 1.3 and expiry["before"] == 1
    assert not any(row["damage_system"] == "lunar" for row in out["resolved_template"]["hits"])
    out = execute_mechanics(*scenario(balance=1))
    assert receipts(out)["consume_dew"]["available"] == 1
    assert receipts(out)["expire_dew_reserve"]["before"] == 1


def test_optional_partial_consumption_uses_the_real_available_debit():
    template, rotation, model = scenario(balance=1, consume=3, partial=True)
    out = execute_mechanics(template, rotation, model)
    debit = receipts(out)["consume_dew"]
    assert debit["amount"] == 1 and debit["requested"] == 3
    hit = next(row for row in out["resolved_template"]["hits"] if row["damage_system"] == "lunar")
    assert hit["extensions"]["elemental"]["dew_consumption"]["actual_dew"] == 1


def test_future_consumption_cannot_authorize_past_damage():
    template, rotation, model = scenario()
    model["special_reaction_events"][1]["t"] = 3.1
    out = execute_mechanics(template, rotation, model)
    assert any(row["code"] == "lunar_bloom_consumption_binding_invalid" for row in out["issues"])


def test_dew_tier_effect_uses_actual_debit_instead_of_requested_amount():
    template, rotation, model = scenario(balance=1, consume=3, partial=True)
    model["condition_events"] = [
        {"id": f"debit-{amount}", "condition": f"actual-{amount}", "t": 3, "order": 30 + amount,
         "repeat": {"mode": "once"}, "status": "assumed", "source": "Synthetic actual debit tier",
         "dew_consumption": {"event_id": "spend", "amount": amount}}
        for amount in (1, 3)
    ]
    add(model, *[rule(f"buff-{amount}", trigger={"event": "condition", "phase": "post",
             "event_ids": [f"debit-{amount}"], "condition": f"actual-{amount}"})
             for amount in (1, 3)])
    out = execute_mechanics(template, rotation, model)
    assert out["status"] in {"passed", "unproven"}, out["issues"]
    assert [(row["consumption"]["actual_dew"], row["value"]) for row in out["mechanics_trace"]
            if row["type"] == "dew_consumption_condition"] == [(1, True), (1, False)]
    assert [row["rule_id"] for row in out["mechanics_trace"] if row["type"] == "activation"] == ["buff-1"]
    model["condition_events"][0]["value"] = True
    invalid = execute_mechanics(template, rotation, model)
    assert any(issue["code"] == "condition_value_source" for issue in invalid["issues"])


@pytest.mark.parametrize("event_id,actual", [("absorb", 1), ("second", 0)])
def test_direct_lunar_bonus_uses_real_core_absorption(event_id, actual):
    template, rotation, model = scenario(ordinary=True)
    context = {"action_id": "cast", "character": "a", "target_id": "enemy", "area_id": "battlefield.primary",
               "status": "assumed", "source": "Explicit synthetic conversion window", "repeat": {"mode": "once"}}
    model["elemental_state_events"] = [
        {**context, "id": "convert", "kind": "bloom_core_conversion_open", "t": .31, "order": 5,
         "duration_seconds": 4, "replacement_lifetime_seconds": 6, "convert_existing": True},
        {**context, "id": "absorb", "kind": "bloom_core_absorb", "t": .4, "order": 5, "maximum": 2},
        {**context, "id": "second", "kind": "bloom_core_absorb", "t": .5, "order": 5, "maximum": 2},
    ]
    attack = model["special_reaction_events"][2]
    attack["bloom_core_absorption"] = {"event_id": event_id, "multiplier_bonus_per_core": .25}
    result = execute_mechanics(template, rotation, model)
    assert result["status"] in {"passed", "unproven"}, result["issues"]
    hit = next(row for row in result["resolved_template"]["hits"] if row["damage_system"] == "lunar")
    assert hit["multiplier"] == attack["ability_profile"]["parameters"]["multiplier"] * 2 * (1 + actual * .25)
    absorption = hit["extensions"]["elemental"]["bloom_core_absorption"]
    assert absorption["actual_cores"] == actual
    assert absorption["run_id"] == f"0:{event_id}"
    attack["bloom_core_absorption"]["event_id"] = "convert"
    invalid = execute_mechanics(template, rotation, model)
    assert any(issue["code"] == "bloom_core_absorption_binding_invalid" for issue in invalid["issues"])


def test_real_core_from_lunar_reaction_is_converted_before_ordinary_expiry():
    template, rotation, model = _application_fixture("hydro", initial=[aura("dendro", .7, "a")])
    _enable(model, "lunar_bloom")
    context = {"action_id": "cast", "character": "a", "target_id": "enemy", "area_id": "battlefield.primary",
               "status": "assumed", "source": "Explicit synthetic conversion window", "repeat": {"mode": "once"}}
    model["elemental_state_events"] = [
        {**context, "id": "convert", "kind": "bloom_core_conversion_open", "t": .3, "order": 5,
         "duration_seconds": 4, "replacement_lifetime_seconds": 6, "convert_existing": True},
        {**context, "id": "absorb", "kind": "bloom_core_absorb", "t": .4, "order": 5, "maximum": 1},
        {**context, "id": "second", "kind": "bloom_core_absorb", "t": .5, "order": 5, "maximum": 1},
    ]
    out = execute_mechanics(template, rotation, model)
    assert out["status"] in {"passed", "unproven"}, out["issues"]
    trace = out["mechanics_trace"]
    converted = next(row for row in trace if row["type"] == "bloom_core_conversion_open")
    assert len(converted["converted_core_ids"]) == 1
    assert [row["amount"] for row in trace if row["type"] == "bloom_core_absorb"] == [1, 0]
    assert not any(row.get("reaction", {}).get("type") == "bloom" for row in out["resolved_template"]["hits"])


def test_public_authoring_preserves_dew_and_core_inputs_on_native_plan_roundtrip():
    template, rotation, model = scenario(ordinary=True)
    events = model.pop("special_reaction_events")
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model)
    context = {"action_id": "cast", "character": "a", "target_id": "enemy", "area_id": "battlefield.primary",
               "status": "assumed", "source": "声明测试用转化范围", "repeat": {"mode": "once"}}
    operation = {**context, "id": "convert", "kind": "bloom_core_conversion_open", "t": .31, "order": 5,
                 "duration_seconds": 4, "replacement_lifetime_seconds": 6, "convert_existing": True}
    response = authoring_assistance({"kind": "mechanics_fragment", "version": 1, "request_id": "moon",
        "expected_revision": plan["revision"], "payload": {"mechanics_model": {
            "special_reaction_events": events, "elemental_state_events": [operation]}}}, plan=plan)
    candidate = prepare_input_edit(plan, response["edit_request"])
    reopened = json.loads(json.dumps(candidate, allow_nan=False))
    validate_plan(reopened)
    engine_input = reopened["mechanics_model"]
    assert engine_input["special_reaction_events"] == events
    assert engine_input["elemental_state_events"] == [operation]
    result = execute_mechanics(reopened["manual_template"], reopened["rotation_model"], engine_input)
    assert result["result"] is not None, result["issues"]
    assert receipts(result)["consume_dew"]["pool_debits"] == [{"pool_id": "ordinary", "before": 1, "amount": 1, "after": 0},
        {"pool_id": "bonus", "before": 2, "amount": 1, "after": 1}]
