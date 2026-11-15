from types import SimpleNamespace

from test_elemental_multiaura_v4 import aura, model
from test_mechanics_execution import add, fixture, rule, second_character

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.reaction_package_dependencies import (
    independent_attribute_write,
    package_attribute_reads,
)


def test_double_swirl_accepts_independent_constant_teammate_em():
    template, rotation, mechanics = fixture((.2,))
    second_character(template, rotation, "b")
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    template["hits"][0]["element"] = "anemo"
    mechanics["elemental_model"] = model([aura("hydro", .2), aura("cryo", .7)], freezable=False)
    mechanics["hit_events"][0]["application"] = {"profile_id": "anemo", "target_id": "enemy", "landed": True}
    share = rule("share", "benefit.em.all", 50, trigger={"event": "elemental_reaction", "phase": "post", "reaction_types": ["swirl"]})
    share["beneficiaries"] = {"mode": "characters", "characters": ["b"], "scope": "locked"}
    add(mechanics, share)
    out = execute_mechanics(template, rotation, mechanics)
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert len([r for r in out["mechanics_trace"] if r.get("type") == "elemental_reaction"]) >= 2
    assert any(r.get("rule_id") == "share" and r.get("type") == "activation" for r in out["mechanics_trace"])


def test_transitive_cross_actor_hp_to_trigger_em_is_not_independent():
    conversion = rule("conversion", "benefit.em.all", {"kind": "stat", "stat": "hp", "subject": "owner", "sample": "current", "scale": .01})
    conversion["owner"] = "b"
    conversion["beneficiaries"] = {"mode": "characters", "characters": ["a"], "scope": "locked"}
    hp = rule("hp", "benefit.stat_add.hp", 100)
    hp["beneficiaries"] = {"mode": "characters", "characters": ["b"], "scope": "locked"}
    runtime = SimpleNamespace(rules={"conversion": conversion}, data={"chars": ["a", "b"]},
                              state={"conversion": {"instances": [{'beneficiaries': ['a']}]}}, attribute_runtime=None,
                              frontline='a')
    reads = package_attribute_reads(runtime, "a")
    assert ("b", "hp", "total") in reads
    assert not independent_attribute_write(runtime, hp, hp["effects"][0], reads)
    conversion["effects"][0]["value"]["sample"] = "trigger"
    reads = package_attribute_reads(runtime, "a")
    assert independent_attribute_write(runtime, hp, hp["effects"][0], reads)


def test_future_unactivated_conversion_does_not_block_current_independent_write():
    conversion = rule("future", "benefit.em.all", {"kind": "stat", "stat": "hp", "subject": "owner", "sample": "current", "scale": .01})
    conversion["owner"] = "b"
    runtime = SimpleNamespace(rules={"future": conversion}, data={"chars": ["a", "b"]},
                              state={"future": {"instances": []}}, attribute_runtime=None, frontline='a')
    assert package_attribute_reads(runtime, "a") == {("a", "em", "total")}
    assert ("b", "hp", "total") in package_attribute_reads(runtime, "a", candidate_rule_ids={"future"})


def test_current_frontline_scope_respects_effective_instance_membership():
    conversion = rule('frontline', 'benefit.em.all',
        {'kind': 'stat', 'stat': 'hp', 'subject': 'owner', 'sample': 'current', 'factor': .01},
        owner='b', beneficiaries={'mode': 'team', 'scope': 'current_frontline'})
    runtime = SimpleNamespace(rules={'frontline': conversion}, data={'chars': ['a', 'b']},
        state={'frontline': {'instances': [{'beneficiaries': ['a', 'b']}]}},
        attribute_runtime=None, frontline='b')
    assert package_attribute_reads(runtime, 'a') == {('a', 'em', 'total')}
    assert ('b', 'hp', 'total') in package_attribute_reads(runtime, 'b')


def test_candidate_selection_map_uses_actual_package_not_whole_axis_candidates():
    conversion = rule('candidate', 'benefit.em.all',
        {'kind': 'stat', 'stat': 'hp', 'subject': 'owner', 'sample': 'current', 'factor': .01},
        owner='b', beneficiaries={'mode': 'frontline', 'scope': 'locked'})
    runtime = SimpleNamespace(rules={'candidate': conversion}, data={'chars': ['a', 'b']},
        state={'candidate': {'instances': []}}, attribute_runtime=None, frontline='a')
    assert package_attribute_reads(runtime, 'a', candidate_rule_ids={'candidate'},
        candidate_recipients={'candidate': ['b']}) == {('a', 'em', 'total')}
    assert ('b', 'hp', 'total') in package_attribute_reads(runtime, 'a', candidate_rule_ids={'candidate'},
        candidate_recipients={'candidate': ['a']})
