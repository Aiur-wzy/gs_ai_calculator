"""Sourced coefficient adjustments change the base, not the reaction state."""
from __future__ import annotations

import copy

import pytest
from test_direct_stellar_swirl import direct_fixture
from test_elemental_multiaura_v4 import aura
from test_elemental_world_stellar_swirl_deadline_adversarial import (
    AREA,
    area_damage,
    generated_applications,
    generated_since,
    seed_vortex,
    superconduct_proposal,
    world_with_targets,
)
from test_mechanics_execution import second_character
from test_special_reaction_mechanics_projection import _application_fixture, _enable
from test_special_reactions import contributor, mechanics, model, reaction, runtime

from genshin_engine.formulas import reaction_base_value
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.special_reactions import (
    SpecialReactionPending,
    resolve_special_damage_event,
    validate_special_reaction_model,
)


SWIRL = "stellar_swirl"
ANEMO = "stellar_swirl_anemo"
CRYO_ONE = "stellar_swirl_cryo_lv2"
CRYO_TWO = "stellar_swirl_cryo_lv3"
SOURCE = "explicit test fixture: versioned passive"


def adjustment(provider_id="qualification.provider", components=None, multiplier=1.10):
    return {
        "provider_id": provider_id,
        "source": SOURCE,
        "components": [ANEMO, CRYO_ONE, CRYO_TWO] if components is None else components,
        "multiplier": multiplier,
    }


@pytest.mark.parametrize(("component", "vortex_count", "base", "effective"), [
    (ANEMO, 1, .75, .825),
    (CRYO_ONE, 1, 2, 2.2),
    (CRYO_TWO, 3, 3, 3.3),
])
def test_scoped_factor_applies_before_flat_and_keeps_raw_runtime_state(component, vortex_count, base, effective):
    control = model(SWIRL)
    modified = copy.deepcopy(control)
    control["replacements"][SWIRL]["mechanics"] = mechanics(SWIRL, initial_vortex_count=vortex_count)
    modified["replacements"][SWIRL]["mechanics"] = mechanics(
        SWIRL, initial_vortex_count=vortex_count,
        base_factor_adjustment=adjustment(components=[component]))
    actors = [
        contributor("qualification.provider", reaction_flat=100),
        contributor("trigger.actor", reaction_flat=100),
    ]
    outputs = []
    for value in (control, modified):
        engine = runtime(SWIRL, value=value)
        replaced = engine.replace(reaction(SWIRL, area_id="arena"), time=0)
        requests = replaced["generated_events"] + engine.advance(3_000_000)
        outputs.append([
            request for request in requests if request["type"] == "special_reaction_damage_request"])
    for before, after in zip(*outputs, strict=True):
        assert before["reaction_type"] == after["reaction_type"]
        if after["reaction_type"] != component:
            assert "reaction_coefficient" not in after
            continue
        assert before["system_factor"] == after["system_factor"] == base
        receipt = after["reaction_coefficient"]
        assert receipt["effective_factor"] == pytest.approx(effective)
        assert receipt["provider_id"] == "qualification.provider"
        assert receipt["source"] == SOURCE
        assert receipt["status"] == "assumed"
        result = resolve_special_damage_event(after, actors)["damage"]
        assert result["components"][0]["no_crit_damage"] == pytest.approx(
            reaction_base_value(90)*effective + 100)


@pytest.mark.parametrize(("components", "multiplier", "provider_id", "field"), [
    ([ANEMO, ANEMO], 1.1, "a", "components"),
    (["stellar_superconduct"], 1.1, "a", "components"),
    (["cryo"], 1.1, "a", "components"),
    ([{}], 1.1, "a", "components"),
    ([CRYO_TWO], 1.5e308, "a", "multiplier"),
    ([ANEMO], float("inf"), "a", "multiplier"),
    ([ANEMO], True, "a", "multiplier"),
    ([ANEMO], 0, "a", "multiplier"),
    ([ANEMO], 1.1, " ", "provider_id"),
])
def test_invalid_adjustment_is_localized(components, multiplier, provider_id, field):
    value = model(SWIRL)
    value["replacements"][SWIRL]["mechanics"] = mechanics(
        SWIRL, base_factor_adjustment=adjustment(provider_id, components, multiplier))
    assert any(
        e["path"].endswith("base_factor_adjustment."+field)
        for e in validate_special_reaction_model(value))


def test_no_reviewed_claim_and_no_non_party_provider():
    value = model(SWIRL)
    value["replacements"][SWIRL]["mechanics"] = mechanics(
        SWIRL, status="confirmed", base_factor_adjustment=adjustment())
    assert any(row["code"] == "profile_override_status" for row in validate_special_reaction_model(value))
    value["replacements"][SWIRL]["mechanics"] = mechanics(
        SWIRL, base_factor_adjustment=adjustment(provider_id="someone.else"))
    state = runtime(SWIRL, value=value)
    with pytest.raises(SpecialReactionPending) as error:
        state.replace(reaction(SWIRL, area_id="arena"), time=0)
    assert error.value.issue["code"] == "stellar_swirl_factor_provider_absent"
    assert not state.vortex_by_area


def test_v3_execution_projects_provenance_and_does_not_modify_contributor_slots():
    template, rotation, input_model = _application_fixture(
        "anemo", initial=[aura("cryo", .7, "b")])
    second_character(template, rotation, "b")
    _enable(input_model, SWIRL)
    template["characters"][0]["stats"].update(em=0, crit_rate=0)
    template["characters"][1]["stats"].update(em=0, crit_rate=0)
    before = execute_mechanics(template, rotation, input_model)
    input_model["special_reaction_model"]["replacements"][SWIRL]["mechanics"] = mechanics(
        SWIRL, base_factor_adjustment=adjustment(provider_id="a", components=[ANEMO]))
    after = execute_mechanics(template, rotation, input_model)
    assert after["status"] in ("passed", "unproven"), after["issues"]
    expected_hits = {hit["id"]: hit for hit in before["result"]["hits"]}
    explanations = after["result"]["damage_explanations"]
    anemo_count = 0
    for hit in after["result"]["hits"]:
        matched = expected_hits[hit["id"]]
        if ":immediate:anemo:" not in hit["id"]:
            assert hit["expected"] == matched["expected"]
            continue
        anemo_count += 1
        assert hit["result_weight"] == matched["result_weight"]
        assert hit["expected"] == pytest.approx(matched["expected"]*1.1)
        explained = explanations["events"][hit["explanation_ref"]]
        assert explained["zones"]["system_factor"] == pytest.approx(.825)
        source = explanations["sources"][explained["source_ref"]]["reaction_coefficient"]
        assert source["base_factor"] == .75
        assert source["effective_factor"] == pytest.approx(.825)
        assert source["source"] == SOURCE
    assert anemo_count == 2


@pytest.mark.parametrize(("components", "factor"), [([ANEMO], 1), ([SWIRL], 1.1)])
def test_ability_direct_is_only_adjusted_when_explicitly_selected(components, factor):
    template, rotation, input_model = direct_fixture()
    baseline = execute_mechanics(template, rotation, input_model)
    input_model["special_reaction_model"]["replacements"][SWIRL]["mechanics"] = mechanics(
        SWIRL, base_factor_adjustment=adjustment(provider_id="a", components=components))
    amended = execute_mechanics(template, rotation, input_model)
    assert amended["status"] in ("passed", "unproven"), amended["issues"]
    row = next(hit for hit in amended["result"]["hits"] if hit["damage_system"] == "stellar")
    old_row = next(hit for hit in baseline["result"]["hits"] if hit["damage_system"] == "stellar")
    assert row["expected"] == pytest.approx(old_row["expected"] * factor)
    request = next(e for e in amended["mechanics_trace"]
                   if e.get("reaction_type") == SWIRL
                   and e.get("type") == "special_reaction_damage_request")
    assert request["layers"] is None and request["system_factor"] == 1
    if factor > 1:
        assert request["reaction_coefficient"]["effective_factor"] == factor
        assert request["reaction_coefficient"]["source"] == SOURCE
    else:
        assert "reaction_coefficient" not in request


def test_world_keeps_two_target_selection_ice_application_and_superconduct_while_adjusting_ice():
    expected_snapshots = []
    for multiplier in (None, 1.1):
        world = world_with_targets([
            ("enemy.b", (), "known"),
            ("enemy.a", (), "known"),
        ], enabled=(SWIRL, "stellar_superconduct"))
        if multiplier is not None:
            world.special_runtime.model["replacements"][SWIRL]["mechanics"] = mechanics(
                SWIRL, base_factor_adjustment=adjustment(
                    provider_id="provider", components=[CRYO_ONE], multiplier=multiplier))
        seed_vortex(world)
        proposal = superconduct_proposal(target_id="enemy.a")
        world.special_runtime.replace(proposal, time=0)
        world.advance(3_000_000)
        rows = generated_since(world)
        damage = area_damage(rows)
        assert damage["resolved_target_ids"] == ["enemy.a", "enemy.b"]
        if multiplier is not None:
            assert damage["reaction_coefficient"]["base_factor"] == 2
            assert damage["reaction_coefficient"]["effective_factor"] == pytest.approx(2.2)
        assert [(app["target_id"], app["application_gu"]) for app in generated_applications(rows)] == [
            ("enemy.a", 1), ("enemy.b", 1)]
        world.advance(4_000_000)
        expected_snapshots.append(world.special_runtime.stellar_superconduct_snapshot(AREA))
    assert expected_snapshots[0] == expected_snapshots[1]
