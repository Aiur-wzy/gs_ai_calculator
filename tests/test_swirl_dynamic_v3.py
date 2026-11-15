"""Versioned ordinary fire Swirl and bounded dynamic EM, with hand oracles."""
from __future__ import annotations

import copy

import pytest
from test_elemental_execution import attempt
from test_elemental_swirl import swirl_model
from test_mechanics_execution import ability, action, add, rule, second_character
from test_mechanics_swirl import setup as old_setup
from test_mechanics_swirl import swirls

from genshin_engine.elemental import (
    ElementalPending,
    ElementalRuntime,
    validate_elemental_model,
)
from genshin_engine.elemental_schema import elemental_capabilities
from genshin_engine.mechanics import execute_mechanics

RULESET = "gauge-swirl-dynamic-v3"


def setup(times=(.2,), element="pyro"):
    t, r, m = old_setup(times)
    m["elemental_model"] = swirl_model(element)
    m["elemental_model"]["ruleset"] = RULESET
    return t, r, m


def expected(em, resistance=.9, bonus=0):
    return .6 * 1446.85 * (1 + 16 * em / (2000 + em) + bonus) * resistance


def sampled(out):
    return [h["extensions"]["elemental"]["provider_stat_sample"]
            for h in out["resolved_template"]["hits"] if h["damage_system"] == "transformative"]


def check_sample_projection(out):
    """Compare the audit sample against actual numerical output, not itself."""
    by_id = {h["id"]: h for h in out["result"]["hits"]}
    for h in out["resolved_template"]["hits"]:
        if h["damage_system"] != "transformative":
            continue
        sample = h["extensions"]["elemental"]["provider_stat_sample"]
        em = sample["value"]
        assert by_id[h["id"]]["zones"]["em_zone"] == pytest.approx(1 + 16 * em / (2000 + em))


def manual_em(value=200, start=0, end=3, target=None, scope="team"):
    return {"id": "em", "start": start, "end": end, "scope": scope,
            **({"target": target} if target else {}),
            "effects": [{"id": "value", "benefit_type_id": "benefit.em.all", "zone": "em", "stat": "all",
                         "value": value, "applies_to": {"mode": "all"}}]}


@pytest.mark.parametrize("element", ["pyro", "hydro", "cryo"])
def test_v3_swirl_single_aura_consumption_damage_identity_and_no_reapplication(element):
    m = swirl_model(element, .7)
    m["ruleset"] = RULESET
    m["profiles"][-1]["gauge_units"] = 2
    assert validate_elemental_model(m) == []
    runtime = ElementalRuntime(m)
    hit = attempt(runtime, "anemo", eid="wind")
    assert hit["reaction"]["consumed_gu"] == .7
    assert hit["reaction"]["reaction_element"] == element
    assert hit["reaction"]["ruleset"] == RULESET
    assert hit["after"]["aura"] is None
    assert runtime.attempt_reaction_damage(hit["reaction"])["event_id"] == "wind:reaction_damage:0"
    assert attempt(runtime, "anemo", .1)["outcome"] == "anemo_without_aura"


def test_fire_damage_uses_fire_resistance_and_ignores_classic_multipliers():
    t, r, m = setup()
    t["enemy"]["resistance"]["pyro"] = .3
    t["characters"][0]["stats"].update(atk=10000, crit_rate=1, crit_dmg=9, dmg_bonus={"pyro": 8})
    out = execute_mechanics(t, r, m)
    assert swirls(out)[0]["expected"] == pytest.approx(expected(0, .7))
    h = out["resolved_template"]["hits"][1]
    assert h["hit_name"] == "火扩散"
    assert h["extensions"]["elemental"]["ruleset"] == RULESET


@pytest.mark.parametrize("delivery", ["rule", "manual"])
def test_constant_em_changes_expire_and_are_not_cached(delivery):
    t, r, m = setup((.2, .3, .7))
    if delivery == "manual":
        t["buffs"] = [manual_em(200, .25, .7)]
    else:
        add(m, rule("em", "benefit.em.all", 200,
                    trigger={"event": "hit", "phase": "post", "event_ids": ["h0"]}, duration=.5))
    original = copy.deepcopy((t, r, m))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in swirls(out)] == pytest.approx([expected(0), expected(200), expected(0)])
    samples = sampled(out)
    assert [s["value"] for s in samples] == [0, 200, 0]
    assert all(s["phase"] == "reaction_created" and s["basis"] == "total" for s in samples)
    assert samples[1]["dependencies"][0]["value"] == 200
    assert samples[1]["dependencies"][0]["path"] in ("rules.em.value", "buffs.em.value")
    check_sample_projection(out)
    assert (t, r, m) == original


@pytest.mark.parametrize("phase", ["hit", "elemental_reaction"])
def test_same_parent_post_em_is_not_retroactive_but_enemy_shred_is(phase):
    t, r, m = setup((.2, .3))
    add(m, rule("em", "benefit.em.all", 200, trigger={"event": phase, "phase": "post"}),
        rule("shred", "benefit.res_shred.pyro", .4, domain="enemy",
             trigger={"event": "elemental_reaction", "phase": "post", "reaction_elements": ["pyro"]}))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in swirls(out)] == pytest.approx([expected(0, 1.15), expected(200, 1.15)])
    assert [s["value"] for s in sampled(out)] == [0, 200]
    check_sample_projection(out)


def test_trigger_expression_samples_after_parent_post_separately_from_reaction_creation():
    t, r, m = setup((.2, .3))
    add(m, rule("em", "benefit.em.all", 200, trigger={"event": "hit", "phase": "post", "event_ids": ["h0"]}),
        rule("derived", "benefit.dmg_bonus.pyro",
             {"kind": "stat", "subject": "owner", "stat": "em", "factor": .0004, "sample": "trigger"},
             trigger={"event": "elemental_reaction", "phase": "post"}))
    out = execute_mechanics(t, r, m)
    assert sampled(out)[0]["value"] == 0
    rows = [s for s in out["sampling_trace"] if s["rule_id"] == "derived"]
    assert rows and rows[0]["source_value"] == 200 and rows[0]["sample_t"] == .2
    assert rows[0]["expression"]["sample"] == "trigger"
    check_sample_projection(out)


def test_trigger_sampled_bonus_remains_self_domain_and_cannot_enter_old_capture():
    t, r, m = setup((.2, .3))
    t["hits"][1]["element"] = "pyro"
    m["elemental_model"]["profiles"].append({"id": "no_pyro", "element": "pyro", "application": "none",
                                            "status": "assumed", "source": "engineering no application"})
    m["hit_events"][1].update(self_snapshot_event="cast", application={"profile_id": "no_pyro", "target_id": "enemy", "landed": True})
    t["buffs"] = [manual_em()]
    add(m, rule("derived", "benefit.dmg_bonus.pyro",
                {"kind": "stat", "subject": "owner", "stat": "em", "factor": .0004, "sample": "trigger"},
                trigger={"event": "elemental_reaction", "phase": "post"}))
    out = execute_mechanics(t, r, m)
    assert next(h for h in out["result"]["hits"] if h["id"] == "h1")["expected"] == 45
    m["rules"][0]["effects"][0]["domain"] = "provider"
    out = execute_mechanics(t, r, m)
    assert next(h for h in out["result"]["hits"] if h["id"] == "h1")["expected"] == pytest.approx(48.6)


def test_swirl_samples_current_em_independently_of_parent_self_capture():
    t, r, m = setup((.2, .3))
    for d in m["hit_events"]:
        d["self_snapshot_event"] = "cast"
    add(m, rule("em", "benefit.em.all", 200,
                trigger={"event": "hit", "phase": "post", "event_ids": ["h0"]}))
    out = execute_mechanics(t, r, m)
    assert [h["expected"] for h in swirls(out)] == pytest.approx([expected(0), expected(200)])
    assert [s["t"] for s in sampled(out)] == [.2, .3]


def test_amplifying_em_remains_in_explicit_self_snapshot_while_swirl_is_current():
    t, r, m = setup((.2, .3), "cryo")
    t["hits"][0]["element"] = "pyro"
    m["hit_events"][0].update(self_snapshot_event="cast", application={"profile_id": "pyro", "target_id": "enemy", "landed": True})
    t["buffs"] = [manual_em(start=.1)]
    out = execute_mechanics(t, r, m)
    assert out["result"]["hits"][0]["zones"]["amplify"] == 2
    assert sampled(out)[0]["value"] == 200
    check_sample_projection(out)


@pytest.mark.parametrize(("policy", "ems"), [("independent", [200, 400, 200, 0]), ("refresh", [200, 200, 200, 0])])
def test_constant_em_layer_and_refresh_lifetimes(policy, ems):
    t, r, m = setup((.2, .4, .7, .9))
    # Separate evidence-controlled damage sequence so this test sees every hit.
    m["elemental_model"]["swirl_damage"]["icd"]["sequence"] = [1]*10
    add(m, rule("em", "benefit.em.all", 200, duration=.5,
                trigger={"event": "hit", "phase": "pre", "event_ids": ["h0", "h1"]},
                stacking={"max": 2 if policy == "independent" else 1, "add": 1, "policy": policy}))
    out = execute_mechanics(t, r, m)
    assert [s["value"] for s in sampled(out)] == ems
    check_sample_projection(out)


def test_delayed_em_and_negative_constant_with_valid_total():
    t, r, m = setup((.2, .3))
    t["characters"][0]["stats"]["em"] = 300
    em = rule("em", "benefit.em.all", -100)
    em["timing"]["delay"] = .25
    add(m, em)
    out = execute_mechanics(t, r, m)
    assert [s["value"] for s in sampled(out)] == [300, 200]
    check_sample_projection(out)


def test_unreported_parent_keeps_reaction_consumption_and_sampling_without_damage_delivery():
    t, r, m = setup((.2, .3))
    m["hit_events"][0]["report"] = False
    add(m, rule("em", "benefit.em.all", 200, trigger={"event": "elemental_reaction", "phase": "post"}))
    out = execute_mechanics(t, r, m)
    assert [h["id"] for h in out["result"]["hits"]] == ["h1", "h1:reaction_damage:0"]
    first = next(t for t in out["mechanics_trace"] if t["type"] == "reaction_damage" and t["event_id"] == "h0:reaction_damage:0")
    assert first["reported_damage"] is False and first["provider_stat_sample"]["value"] == 0
    assert sampled(out)[0]["value"] == 200
    check_sample_projection(out)


def test_pyro_damage_icd_is_distinct_and_does_not_suppress_reaction_listeners():
    t, r, m = setup((0, .1, .2, .5, .6))
    add(m, rule("fire_listener", "benefit.dmg_bonus.pyro", .1,
                trigger={"event": "elemental_reaction", "phase": "post", "reaction_elements": ["pyro"]}))
    out = execute_mechanics(t, r, m)
    attempts = [v for v in out["elemental_trace"] if v["type"] == "reaction_damage_attempt" and v["cycle"] == 0]
    assert [v["allowed"] for v in attempts] == [True, True, False, True, True]
    assert len([v for v in out["mechanics_trace"] if v["type"] == "activation" and v["rule_id"] == "fire_listener" and v["cycle"] == 0]) == 5
    assert len(swirls(out)) == 4


def test_unknown_trigger_condition_never_becomes_a_verified_em_bonus():
    t, r, m = setup()
    m["condition_events"] = [{"id": "condition", "t": .1, "order": 3, "condition": "known", "value": None,
                              "repeat": {"mode": "once"}, "status": "assumed", "source": "engineering unknown"}]
    add(m, rule("em", "benefit.em.all", 200, trigger={"event": "hit", "phase": "pre", "condition": "known"}))
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending"
    assert any(v["code"] == "unknown_condition" for v in out["issues"])
    assert all(v["value"] == 0 for v in sampled(out))


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_dynamic_em_is_invalid_before_execution(value):
    t, r, m = setup()
    add(m, rule("em", "benefit.em.all", value))
    out = execute_mechanics(t, r, m)
    assert out["status"] == "invalid" and out["result"] is None


def test_freeze_reference_retains_fixed_em_boundary_and_contract():
    from test_freeze_mechanics_review import setup as freeze_setup

    from genshin_engine.elemental_schema import FREEZE_RULESET, REFERENCE_CONTRACT

    t, r, m = freeze_setup()
    original_contract = copy.deepcopy(REFERENCE_CONTRACT)
    add(m, rule("em", "benefit.em.all", 200))
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending"
    assert out["issues"][-1]["code"] == "swirl_dynamic_em"
    assert m["elemental_model"]["ruleset"] == FREEZE_RULESET
    assert REFERENCE_CONTRACT == original_contract


@pytest.mark.parametrize("delivery", ["manual", "rule"])
def test_frontline_em_scope_is_sampled_at_each_swirl(delivery):
    t, r, m = setup((.2, .4))
    second_character(t, r)
    r["abilities"].append(ability("switch", "a", "action.switch"))
    r["actions"].append(action("swap", "switch", .3, .3, 5, switch_to="b", repeat={"mode": "once"}))
    if delivery == "manual":
        t["buffs"] = [manual_em(scope="frontline")]
    else:
        buff = rule("em", "benefit.em.all", 200)
        buff["beneficiaries"] = {"mode": "team", "scope": "current_frontline"}
        add(m, buff)
    out = execute_mechanics(t, r, m)
    assert [s["value"] for s in sampled(out)] == [200, 0]
    assert [h["expected"] for h in swirls(out)] == pytest.approx([expected(200), expected(0)])


@pytest.mark.parametrize("mutation", ["filter", "quota", "conversion", "manual_filter", "match"])
def test_unsupported_em_writers_are_pending_even_if_they_never_activate(mutation):
    t, r, m = setup()
    em = rule("em", "benefit.em.all", 200)
    em["timing"]["delay"] = 5
    matches = None
    if mutation == "filter":
        em["effects"][0]["filter"] = {"event": "hit", "phase": "pre"}
    elif mutation == "quota":
        em["quota"] = {"capacity": 1, "scope": "team", "refresh": "reset", "consume": {"event": "hit", "phase": "pre"}}
    elif mutation == "conversion":
        em["effects"][0]["value"] = {"kind": "stat", "subject": "owner", "stat": "atk", "factor": 2, "sample": "current"}
    elif mutation == "manual_filter":
        t["buffs"] = [manual_em()]
        t["buffs"][0]["effects"][0]["applies_to"] = {"mode": "hits", "hit_ids": ["h0"]}
    else:
        matches = [{"rule_id": "em", "effect_id": "value", "hit_id": "h0", "operation": "exclude"}]
    if mutation != "manual_filter":
        add(m, em)
    out = execute_mechanics(t, r, m, matches)
    assert out["status"] == "pending", out["issues"]
    assert out["result"] is None
    issue = next(i for i in out["issues"] if i["code"] == "swirl_em_sampling_unsupported")
    assert issue["paths"] and issue["unsupported_writes"]


def test_unsupported_writers_for_other_actors_do_not_block_provider():
    t, r, m = setup()
    second_character(t, r)
    em = rule("em", "benefit.em.all", 200)
    em["owner"] = "b"
    em["beneficiaries"] = {"mode": "self", "scope": "locked"}
    em["effects"][0]["filter"] = {"event": "hit", "phase": "pre"}
    add(m, em)
    assert swirls(execute_mechanics(t, r, m))[0]["expected"] == pytest.approx(expected(0))


def test_sampled_negative_em_is_not_silently_clamped():
    t, r, m = setup()
    add(m, rule("em", "benefit.em.all", -1))
    out = execute_mechanics(t, r, m)
    assert out["status"] == "pending"
    assert out["result"] is None
    assert out["issues"][-1]["code"] == "swirl_em_sample_invalid"


def test_missing_level_remains_pending_and_new_contract_is_discoverable():
    t, r, m = setup()
    t["characters"][0]["level"] = 89
    out = execute_mechanics(t, r, m)
    assert out["issues"][-1]["code"] == "swirl_level_unsupported"
    caps = elemental_capabilities()
    assert RULESET in caps["rulesets"]
    assert caps["dynamic_swirl_contract"]["ruleset"] == RULESET


def test_old_v2_retains_pyro_and_dynamic_em_rejection_and_no_new_metadata():
    with pytest.raises(ElementalPending, match="冰/水"):
        attempt(ElementalRuntime(swirl_model("pyro")), "anemo")
    t, r, m = old_setup()
    add(m, rule("em", "benefit.em.all", 200))
    assert execute_mechanics(t, r, m)["issues"][-1]["code"] == "swirl_dynamic_em"
    out = execute_mechanics(*old_setup())
    assert "provider_stat_sample" not in out["resolved_template"]["hits"][1]["extensions"]["elemental"]
