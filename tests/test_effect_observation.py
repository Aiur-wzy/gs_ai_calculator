from genshin_app.effect_observation import observe_effects


def test_identifier_presence_is_not_consumption_and_caps_do_not_erase_consumption():
    plan = {"mechanics_model": {"rules": [{"id": "r", "source_id": "s", "effects": [{"id": "e"}, {"id": "missing"}]}]}}
    result = {"mechanics_trace": [
        {"type": "effect_applied", "rule_id": "r", "effect_id": "e", "event_id": "hit", "projected_effect_id": "p"},
        {"type": "some_unrelated_observation", "effect_id": "missing"}],
        "damage_explanations": {"status": "complete", "modifiers": {"m": {"effect_id": "p", "zone": "crit_rate"}},
            "panels": {"panel": {"inputs": {"crit_rate_raw": 1.2}}},
            "events": {"hit:hit": {"event_id": "hit", "modifier_refs": ["m"], "panel_ref": "panel", "outcome": {"origin": "engine_formula"}}}}}
    output = observe_effects(plan, result)
    a, b = output["effects"]
    assert a["state"] == "formula_consumed" and a["formula_consumed_count"] == 1
    assert a["samples"][-1]["crit_rate_at_cap"] is True
    assert a["samples"][-1]["marginal_gain"] == "not_measured"
    assert b["state"] == "no_saved_consumption"


def test_fixed_and_manual_effects_share_exact_formula_receipts():
    result = {"target_condition_trace": [{"fixed_source_id": "condition", "effect_id": "e", "event_id": "hit",
               "applied": True, "projected_effect_id": "fixed:one", "target_sample_ref": "sample"}],
        "damage_explanations": {"status": "complete", "modifiers": {"m": {"effect_id": "fixed:one"}},
            "events": {"h": {"event_id": "hit", "modifier_refs": ["m"], "outcome": {"origin": "engine_formula"}}}}}
    effects = observe_effects({}, result)["effects"]
    assert len(effects) == 1 and effects[0]["kind"] == "fixed"
    assert effects[0]["formula_consumed_count"] == effects[0]["applied_count"] == 1


def test_external_or_fixed_override_does_not_claim_engine_formula_consumed_final_value():
    result = {"damage_explanations": {"status": "complete", "modifiers": {"m": {"effect_id": "e", "buff_id": "b"}},
        "events": {"h": {"event_id": "h", "modifier_refs": ["m"], "outcome": {"origin": "fixed_override"}}}}}
    assert observe_effects({}, result)["effects"][0]["formula_consumed_count"] == 0


def test_observation_pointers_identify_resource_and_escape_real_keys():
    plan = {"manual_template": {"buffs": [{"id": "b", "effects": [{"id": "e"}]}]}}
    result = {"damage_explanations": {"status": "complete", "modifiers": {"m/~": {"effect_id": "e", "buff_id": "b"}},
        "events": {"h/~": {"event_id": "h", "modifier_refs": ["m/~"], "outcome": {"origin": "engine_formula"}}}}}
    record = observe_effects(plan, result)["effects"][0]
    assert record["input_resource"] == "plan"
    assert record["input_ref"] == "/manual_template/buffs/0/effects/0"
    assert record["samples"][0]["resource"] == "result"
    assert record["samples"][0]["ref"] == "/damage_explanations/events/h~1~0"
    assert record["samples"][0]["modifier_ref"] == "/damage_explanations/modifiers/m~1~0"
