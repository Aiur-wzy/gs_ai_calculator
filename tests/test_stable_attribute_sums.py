"""Additive contributions retain small finite values across cancellation/order."""
import copy
import itertools

import pytest
from test_engine_core import buff, hit, make_template
from test_mechanics_execution import add, fixture, rule
from test_swirl_dynamic_v3 import manual_em, sampled, setup

from genshin_engine import calculate
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.template import TemplateError


@pytest.mark.parametrize("zone,stat", [
    ("em", "all"), ("stat_add", "atk"), ("stat_pct", "atk"),
    ("dmg_bonus", "all"), ("crit_rate", "all"), ("crit_dmg", "all"),
    ("res_shred", "all"), ("def_reduction", "all"),
    ("def_ignore", "all"), ("base_add", "all"),
])
def test_additive_benefits_are_independent_of_declaration_order(zone, stat):
    template = make_template()
    template["characters"][0]["stats"].update(base_atk=1000, crit_rate=.5, crit_dmg=1)
    template["hits"] = [hit(1, "a", reaction={"type": "amplify", "factor": 2})]
    # The large signed pair cancels exactly; one half remains in every order.
    expected = copy.deepcopy(template)
    expected["buffs"] = [buff("small", [{"zone": zone, "stat": stat, "value": .5}])]
    oracle = calculate(expected)["hits"][0]
    for order in itertools.permutations((1e16, .5, -1e16)):
        template["buffs"] = [buff(str(i), [{"zone": zone, "stat": stat, "value": value}])
                             for i, value in enumerate(order)]
        actual = calculate(template)["hits"][0]
        assert actual["expected"] == pytest.approx(oracle["expected"], rel=1e-14)
        assert actual["zones"] == pytest.approx(oracle["zones"], rel=1e-14)


@pytest.mark.parametrize("zone,stat,panel_key", [
    ("em", "all", "em"), ("stat_add", "atk", "atk"),
    ("stat_add", "hp", "hp"), ("stat_add", "def", "def"),
    ("stat_pct", "atk", "atk"), ("crit_dmg", "all", "crit_dmg"),
])
def test_panel_base_and_each_contribution_share_one_sum(zone, stat, panel_key):
    template = make_template()
    stats = template["characters"][0]["stats"]
    stats.update({panel_key: 1e16, "crit_rate": 1, "base_atk": 1})
    scaling = panel_key if panel_key in ("atk", "hp", "def") else "atk"
    template["hits"] = [hit(1, "a", scaling_stat=scaling,
                             reaction={"type": "amplify", "factor": 2})]
    oracle_template = copy.deepcopy(template)
    oracle_template["characters"][0]["stats"][panel_key] = 1
    oracle = calculate(oracle_template)["hits"][0]
    for order in itertools.permutations((-1e16, 1)):
        template["buffs"] = [buff(str(i), [{"zone": zone, "stat": stat, "value": value}])
                             for i, value in enumerate(order)]
        actual = calculate(template)["hits"][0]
        assert actual["expected"] == pytest.approx(oracle["expected"], rel=1e-14)
        assert actual["zones"] == pytest.approx(oracle["zones"], rel=1e-14)


def test_element_all_and_effect_damage_bonuses_do_not_round_between_groups():
    template = make_template()
    template["characters"][0]["stats"]["dmg_bonus"] = {"pyro": 1e16, "all": 1}
    template["buffs"] = [buff("cancel", [{"zone": "dmg_bonus", "stat": "all", "value": -1e16}])]
    template["hits"] = [hit(1, "a")]
    result = calculate(template)["hits"][0]
    assert result["zones"]["dmg_bonus"] == 2
    assert result["expected"] == 1800


def test_stable_sum_overflow_is_an_explicit_template_error():
    template = make_template()
    template["characters"][0]["stats"]["em"] = 1e308
    template["buffs"] = [buff("overflow", [{"zone": "em", "stat": "all", "value": 1e308}])]
    template["hits"] = [hit(1, "a", reaction={"type": "amplify", "factor": 2})]
    with pytest.raises(TemplateError, match="数值|溢出|有限"):
        calculate(template)


@pytest.mark.parametrize("manual,automatic", [(1, -1e16), (-1e16, 1)])
def test_dynamic_em_sample_retains_small_value_in_each_delivery_order(manual, automatic):
    template, rotation, model = setup()
    template["characters"][0]["stats"]["em"] = 1e16
    template["buffs"] = [manual_em(manual)]
    add(model, rule("adjust", "benefit.em.all", automatic))
    output = execute_mechanics(template, rotation, model)
    assert sampled(output)[0]["value"] == 1
    child = next(h for h in output["result"]["hits"] if h["damage_system"] == "transformative")
    assert child["zones"]["em_zone"] == 1 + 16 / 2001


@pytest.mark.parametrize("stat", ["atk", "hp", "def", "em"])
def test_expression_reads_the_same_stable_ordinary_panel(stat):
    template, rotation, model = fixture((.2,))
    template["characters"][0]["stats"][stat] = 1e16
    zone, subtype = ("em", "all") if stat == "em" else ("stat_add", stat)
    manual = manual_em(1)
    manual["effects"][0].update(zone=zone, stat=subtype, benefit_type_id=f"benefit.{zone}.{subtype}")
    template["buffs"] = [manual]
    add(model, rule("cancel", f"benefit.{zone}.{subtype}", -1e16),
        rule("read", "benefit.base_add.all", {"kind": "stat", "subject": "owner", "stat": stat,
                                                "factor": 1, "sample": "current"}))
    output = execute_mechanics(template, rotation, model)
    reads = [entry for entry in output["sampling_trace"] if entry["rule_id"] == "read"]
    assert reads and all(entry["source_value"] == 1 for entry in reads)
