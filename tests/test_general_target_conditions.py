"""The same target observation drives ordinary and native special damage."""
import copy

import pytest
from test_direct_stellar_damage_contract import direct_stellar_profile
from test_elemental_multiaura_v4 import aura, model
from test_mechanics_execution import fixture
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_engine.elemental import ElementalRuntime
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.target_state import GENERAL_TARGET_PROFILE, general_target_sample


def source():
    return {"id": "condition", "name_zh": "冰底条件", "owner": "a", "source_id": "ice.condition",
        "availability": "fixed", "damage_scope": "declared_damage_systems",
        "damage_systems": ["classic", "stellar", "lunar"], "target_state_profile": GENERAL_TARGET_PROFILE,
        "status": "assumed", "source": "anonymous condition fixture",
        "effects": [{"id": "crit", "benefit_type_id": "benefit.crit_rate.all", "domain": "self", "value": .2,
            "filter": {"event": "hit", "phase": "pre"},
            "target_predicate": {"kind": "target_aura_in", "elements": ["cryo", "frozen"]}}]}


def test_general_observation_is_pure_and_keeps_coexistence():
    runtime = ElementalRuntime(model([aura("electro", .6), aura("hydro", .4)]))
    before = copy.deepcopy(runtime.state())
    sample = general_target_sample(runtime, event_id="x", run_id="0:x", cycle=0, time=0, order=1)
    assert sample["status"] == "known"
    assert set(sample["auras"]) == {"hydro", "electro"}
    assert runtime.state() == before


def test_classic_condition_uses_state_before_own_application():
    template, rotation, mechanics = fixture((0, 0, 0))
    mechanics["elemental_model"] = model()
    mechanics["elemental_model"]["profiles"].append({"id": "physical.none", "element": "physical", "application": "none",
        "status": "assumed", "source": "anonymous physical contact"})
    mechanics["elemental_model"]["profiles"][0]["gauge_units"] = 4
    for index, element in enumerate(("cryo", "pyro", "physical")):
        template["hits"][index]["element"] = element
        if element != "physical":
            mechanics["hit_events"][index]["application"] = {"profile_id": element, "target_id": "enemy", "landed": True}
        else:
            mechanics["hit_events"][index]["application"] = {"profile_id": "physical.none", "target_id": "enemy", "landed": True}
    mechanics["fixed_effect_sources"] = [source()]
    out = execute_mechanics(template, rotation, mechanics)
    assert out["status"] in ("passed", "unproven"), out["issues"]
    checks = [r for r in out["target_condition_trace"] if r["event_id"] in ("h0", "h1", "h2") and r["cycle"] == 0]
    assert [r["automatic_value"] for r in checks] == [False, True, False]


def test_native_stellar_damage_uses_general_target_crit_without_manual_intervals():
    template, rotation, mechanics = _application_fixture("electro", initial=[aura("cryo", .8, "a")], times=(.2, 1))
    rotation["abilities"][0].update(variant="tap", effective_level=10)
    _enable(mechanics, "stellar_superconduct")
    template["hits"][1]["element"] = "cryo"
    mechanics["hit_events"][1]["application"] = {"profile_id": "cryo", "target_id": "enemy", "landed": True}
    mechanics["special_reaction_events"] = [{"id": "direct", "kind": "direct_stellar_damage",
        "action_id": "cast", "target_id": "enemy", "area_id": "battlefield.primary", "t": 4.2, "order": 20,
        "ability_profile": direct_stellar_profile(character_id="a", ability_id="e"),
        "status": "assumed", "source": "explicit anonymous operation"}]
    baseline = execute_mechanics(template, rotation, copy.deepcopy(mechanics))
    mechanics["fixed_effect_sources"] = [source()]
    mechanics["fixed_effect_sources"][0]["effects"][0]["filter"]["event"] = "reaction_damage"
    out = execute_mechanics(template, rotation, mechanics)
    assert out["status"] in ("passed", "unproven"), out["issues"]
    rows = [h for h in out["result"]["hits"] if h["damage_system"] == "stellar"]
    original = [h for h in baseline["result"]["hits"] if h["damage_system"] == "stellar"]
    assert len(rows) == len(original) == 1
    assert rows[0]["expected"] == pytest.approx(original[0]["expected"] * 1.1)
    check = next(r for r in out["target_condition_trace"] if r["event_id"] == rows[0]["id"])
    assert check["applied"] is True
    assert check["target_sample"]["profile_id"] == GENERAL_TARGET_PROFILE


def test_unknown_target_remains_pending_not_assumed_absent():
    data = fixture((.2,))
    data[2]["fixed_effect_sources"] = [source()]
    out = execute_mechanics(*data)
    assert out["status"] == "pending"
    assert any(i["code"] == "unknown_target_state" for i in out["issues"])


def test_general_enemy_effect_must_declare_enemy_domain():
    data = fixture((.2,))
    data[2]["elemental_model"] = model([aura("cryo", .8)])
    data[0]["hits"][0]["element"] = "cryo"
    data[2]["hit_events"][0]["application"] = {"profile_id": "cryo", "target_id": "enemy", "landed": True}
    row = source()
    row["effects"][0].update(benefit_type_id="benefit.res_shred.all", domain="enemy", value=.1)
    data[2]["fixed_effect_sources"] = [row]
    valid = execute_mechanics(*data)
    assert valid["status"] in ("passed", "unproven"), valid["issues"]
    assert valid["result"]["hits"][0]["expected"] == pytest.approx(50)
    row["effects"][0]["domain"] = "self"
    invalid = execute_mechanics(*data)
    assert invalid["status"] == "invalid"
