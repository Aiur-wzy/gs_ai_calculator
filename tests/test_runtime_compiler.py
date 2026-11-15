"""Independent expectations for a limited, explicitly assumed source adapter."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_mechanics_execution import (
    EVIDENCE,
    ability,
    action,
    fixture,
    second_character,
)

from genshin_engine.core import calculate
from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from genshin_engine.runtime_compiler import compile_source, compiler_caps

SOURCES = json.loads((Path(__file__).parent / "fixtures/runtime-source-bundles.json").read_text("utf-8"))["sources"]


def bundle(affix=2150071):
    return copy.deepcopy(SOURCES[str(affix)])


def compile_accepted(source=None, *, owner="a", source_id="binding:upstream", period=24, static_policy="execute", **scene):
    source = bundle() if source is None else source
    scenario = {**compiler_caps()["default_scenario"], "static_policy": static_policy, **scene}
    preview = compile_source(source, owner, source_id, scenario, period)
    scenario["accepted_assumptions_fingerprint"] = preview["assumptions_fingerprint"]
    return compile_source(source, owner, source_id, scenario, period), scenario


def inject_static(template, compiled):
    # Test-only translation of the public part contract, independent of App.
    for part in compiled["static_parts"]:
        delivery = part["delivery"]
        template["buffs"].append({"id": "compiled:" + part["part_id"], "start": delivery["start"],
            "end": delivery["end"], "scope": delivery["scope"], "target": delivery["target"],
            "effects": [{**delivery["effect"], "value": part["rule"]["value"]}],
            "extensions": {"owner": "a", "source_id": "binding:upstream"}})


def runtime_fixture(times=(1.1, 12.999, 13)):
    template, rotation, mechanics = fixture(times)
    template["loop_duration"] = rotation["period"] = 24
    template["enemy"] = {"level": 90, "resistance": {"default": 0}}
    template["characters"][0]["stats"].update(atk=2000, base_atk=1000)
    rotation["abilities"][0] = ability(kind="action.elemental_burst", energy={"required": 10, "amount": 10})
    rotation["actions"][0] = action(start=1, end=1.05)
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    rotation["events"] = [{"id": "spend", "type": "energy_spend", "t": 1, "order": 1,
        "action_id": "cast", "character": "a", "required": 10, "amount": 10,
        "repeat": {"mode": "once"}, **EVIDENCE}]
    return template, rotation, mechanics


def with_compiled(mechanics, compiled):
    mechanics["rules"] = compiled["rules"]
    mechanics["initial_state"]["rules"] = compiled["initial_rules"]


def test_profile_is_explicit_and_not_game_verification():
    scenario = compiler_caps()["default_scenario"]
    result = compile_source(bundle(), "a", "binding:upstream", scenario, 24)
    assert result["status"] == "pending"
    assert result["rules"] == result["parts"] == []
    assert result["issues"][-1]["code"] == "semantic_profile_acceptance_required"
    ready, _ = compile_accepted()
    assert ready["status"] == "ready", ready["issues"]
    assert ready["gameplay_semantics_status"] == "assumed_in_scope"
    assert not ready["coverage_certificate"]["human_reviewed"]
    assert not ready["coverage_certificate"]["gameplay_verified"]
    assert ready["coverage_certificate"]["assumed_fields"] > 0
    assert any(a["path"].endswith("isDynamicAbility") for a in ready["semantic_assumptions"])


def test_real_source_runs_d20_and_uses_beneficiary_base_attack():
    compiled, _ = compile_accepted()
    t, r, m = runtime_fixture()
    second_character(t, r)
    t["characters"][1]["stats"].update(atk=1500, base_atk=500)
    # Backstage damage is linked to a separately declared earlier action.
    t["hits"][1]["character"] = "b"
    r["abilities"].append(ability("b_attack", "b"))
    r["initial_frontline"] = "b"
    r["actions"].insert(0, action("b_cast", "b_attack", 0, .01))
    r["abilities"].append(ability("switch_a", "b", "action.switch"))
    r["actions"].insert(1, action("to_a", "switch_a", .1, .11, switch_to="a"))
    for a in r["actions"]:
        a["repeat"] = {"mode": "once"}
    m["hit_events"][1]["action_id"] = "b_cast"
    with_compiled(m, compiled)
    assert validate_mechanics_model(m)["status"] == "valid"
    result = execute_mechanics(t, r, m)
    assert result["status"] in ("passed", "unproven"), result["issues"]
    # Same level => defense 0.5, no resistance/crit. 2000+1000*.2,
    # 1500+500*.2, then the half-open buff has expired at t=13.
    assert [h["expected"] for h in result["result"]["hits"]] == pytest.approx([1100, 800, 1000])
    assert any(set(v["beneficiaries"]) == {"a", "b"} for v in result["mechanics_trace"] if v["type"] == "activation")


def test_parameter_changes_are_data_driven_and_input_not_mutated():
    b = bundle()
    b["param_list"][0:2] = [.25, 6]
    b["affix"]["record"]["paramList"][0:2] = [.25, 6]
    before = copy.deepcopy(b)
    compiled, scenario = compile_accepted(b)
    assert compiled["status"] == "ready", compiled["issues"]
    assert b == before
    assert compiled["rules"][0]["timing"]["duration"] == 6
    t, r, m = runtime_fixture((1.1, 6.999, 7))
    with_compiled(m, compiled)
    result = execute_mechanics(t, r, m)
    assert [h["expected"] for h in result["result"]["hits"]] == pytest.approx([1125, 1125, 1000])
    assert scenario["accepted_assumptions_fingerprint"] == compiled["assumptions_fingerprint"]


@pytest.mark.parametrize(("affix", "target_type", "increase"), [
    (2150070, "damage.elemental_burst", .2), (2150320, "damage.elemental_skill", .2),
    (2150390, "damage.plunging_attack", .25)])
def test_multiple_real_sources_share_static_compiler(affix, target_type, increase):
    compiled, _ = compile_accepted(bundle(affix))
    assert compiled["status"] == "ready", compiled["issues"]
    t, _, _ = fixture((.2, .3))
    t["hits"][0]["damage_type_ids"] = [target_type]
    t["hits"][1]["damage_type_ids"] = ["damage.normal_attack"]
    inject_static(t, compiled)
    assert [h["expected"] for h in calculate(t)["hits"]] == pytest.approx([45 * (1 + increase), 45])
    if affix == 2150070:
        assert any(a["path"].endswith("predicates/0/value") for a in compiled["semantic_assumptions"])


def test_display_names_and_ids_do_not_select_a_hardcoded_recipe():
    b = bundle(2150320)
    b["source"]["affix_id"] = b["affix"]["record"]["affixId"] = 987650
    b["abilities"][0]["payload"]["abilityName"] = "DifferentFixtureAbility"
    for op in b["talent"]["operations"]:
        op["abilityName"] = "DifferentFixtureAbility"
    b["param_list"][0] = b["affix"]["record"]["paramList"][0] = .37
    result, _ = compile_accepted(b)
    assert result["status"] == "ready", result["issues"]
    assert result["parts"][0]["rule"]["value"] == .37


@pytest.mark.parametrize("change", ["new_key", "sentinel", "dynamic_flag", "extra_branch", "extra_property", "add_props"])
def test_unknown_source_behavior_never_becomes_partial_active_rule(change):
    b = bundle()
    body = b["abilities"][0]["payload"]
    if change == "new_key":
        body["LKLPDIMKLKI"]["new_key"] = 0
    elif change == "sentinel":
        body["abilityMixins"][0]["actorIceSubHurtDelta"] = 0
    elif change == "dynamic_flag":
        body["isDynamicAbility"] = False
    elif change == "extra_branch":
        body["abilityMixins"][0]["onTriggerElementalSkill"] = []
    elif change == "extra_property":
        next(iter(body["modifiers"].values()))["properties"]["Actor_DefenceRatio"] = .3
    else:
        b["affix"]["record"]["addProps"][0] = {"propType": "FIGHT_PROP_ATTACK_PERCENT", "value": .1}
    result, _ = compile_accepted(b)
    assert result["status"] != "ready"
    assert result["rules"] == result["parts"] == result["static_parts"] == []
    assert any(i["path"].startswith(("/abilities/0/payload", "/affix/record")) for i in result["issues"])


@pytest.mark.parametrize("affix", [2150171, 1143024])
def test_complex_real_sources_are_diagnosed_by_structure(affix):
    result, _ = compile_accepted(bundle(affix))
    assert result["status"] == "pending"
    assert result["parts"] == []
    assert any(i["code"] == "unsupported_field" for i in result["issues"])
    assert all("不支持角色" not in i["message"] for i in result["issues"])


@pytest.mark.parametrize("scene", [{"single_player_fixed": False}, {"max_party_distance": 41},
    {"no_external_state_changes": False}, {"single_unique_provider": False}, {"allow_unknown": True}])
def test_scenario_outside_profile_is_blocked(scene):
    compiled, _ = compile_accepted(**scene)
    assert compiled["status"] != "ready"
    assert compiled["parts"] == []


def test_included_classified_stat_and_stale_acceptance_are_pending():
    result, _ = compile_accepted(bundle(2150070), static_policy="included")
    assert result["issues"][-1]["code"] == "static_coverage_unrepresentable"
    ready, scenario = compile_accepted()
    scenario["accepted_assumptions_fingerprint"] = "0" * 64
    rejected = compile_source(bundle(), "a", "binding:upstream", scenario, 24)
    assert rejected["status"] == "pending"
    assert ready["source_identity"] == rejected["source_identity"]


def test_failed_burst_and_nonburst_do_not_generate_dynamic_buff():
    compiled, _ = compile_accepted()
    t, r, m = runtime_fixture((1.1,))
    with_compiled(m, compiled)
    r["characters"][0]["initial_energy"] = 0
    failed = execute_mechanics(t, r, m)
    assert failed["status"] == "failed"
    assert not failed["result"]["hits"]
    assert not any(v["type"] == "activation" for v in failed["mechanics_trace"])
    r["abilities"][0] = ability(kind="action.elemental_skill")
    r["events"] = []
    result = execute_mechanics(t, r, m)
    assert [h["expected"] for h in result["result"]["hits"]] == pytest.approx([1000])


def test_refresh_does_not_stack_and_explicit_input_remains_unchanged():
    compiled, _ = compile_accepted()
    t, r, m = runtime_fixture((1.1, 13, 22))
    r["abilities"][0]["energy"] = {"required": 0, "amount": 0}  # Explicit engineering scenario.
    r["events"][0].update(required=0, amount=0)
    r["actions"].append(action("second", "e", 10, 10.05))
    r["actions"][-1]["repeat"] = {"mode": "once"}
    r["events"].append({**r["events"][0], "id": "spend_second", "action_id": "second", "t": 10})
    with_compiled(m, compiled)
    before = copy.deepcopy((t, r, m))
    result = execute_mechanics(t, r, m)
    assert (t, r, m) == before
    assert [h["expected"] for h in result["result"]["hits"]] == pytest.approx([1100, 1100, 1000])


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, None])
def test_non_numeric_parameter_is_invalid(value):
    b = bundle()
    b["param_list"][0] = b["affix"]["record"]["paramList"][0] = value
    result, _ = compile_accepted(b)
    assert result["status"] == "invalid"
    assert result["parts"] == []


@pytest.mark.parametrize("key,value", [("source_sha256", None), ("source_sha256", "not-a-hash"),
                                      ("json_pointer", "not-a-pointer")])
def test_affix_provenance_is_required_even_with_valid_payload(key, value):
    source = bundle()
    if value is None:
        del source["affix"][key]
    else:
        source["affix"][key] = value
    result, _ = compile_accepted(source)
    assert result["status"] == "invalid"
    assert result["parts"] == result["rules"] == []
    assert result["issues"][0]["path"] == "/affix/" + key


def test_actual_bundle_fingerprint_covers_pending_input_despite_stale_claimed_hash():
    source = bundle()
    ready, _ = compile_accepted(source)
    altered = copy.deepcopy(source)
    altered["abilities"][0]["payload"]["new_unknown_behavior"] = {"enabled": True}
    pending, _ = compile_accepted(altered)
    assert pending["status"] == "pending"
    assert source["content_fingerprint"] == altered["content_fingerprint"]
    assert ready["actual_bundle_fingerprint"] != pending["actual_bundle_fingerprint"]
    assert pending["actual_bundle_fingerprint"] == pending["provenance"]["actual_bundle_fingerprint"]
    reordered = dict(reversed(list(altered.items())))
    assert compile_accepted(reordered)[0]["actual_bundle_fingerprint"] == pending["actual_bundle_fingerprint"]


@pytest.mark.parametrize("path,value,status", [
    (("source", "extra_source_metadata"), {"raw_unknown": True}, "ready"),
    (("source", "kind"), "future_source_kind", "pending"),
    (("status",), "pending", "pending"),
    (("source", "affix_id"), None, "invalid"),
])
def test_actual_bundle_fingerprint_is_computed_before_any_source_exit(path, value, status):
    source = bundle()
    original, _ = compile_accepted(source)
    target = source
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result, _ = compile_accepted(source)
    assert result["status"] == status
    assert result["actual_bundle_fingerprint"] != original["actual_bundle_fingerprint"]
    assert len(result["actual_bundle_fingerprint"]) == 64
    assert result["actual_bundle_fingerprint"] == result["provenance"]["actual_bundle_fingerprint"]
