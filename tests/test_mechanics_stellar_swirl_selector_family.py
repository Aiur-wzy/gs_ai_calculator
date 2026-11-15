"""The public reaction family must match each supported runtime damage component."""
from __future__ import annotations

import copy

import pytest
from test_elemental_multiaura_v4 import aura
from test_mechanics_execution import add, rule, second_character
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.mechanics_validation import _selector


@pytest.mark.parametrize(("actual", "matches"), [
    ("stellar_swirl", True),
    ("stellar_swirl_anemo", True),
    ("stellar_swirl_cryo_lv2", True),
    ("stellar_swirl_cryo_lv3", True),
    ("stellar_swirl_cryo_lv4", False),
    ("stellar_swirl_anemo_extra", False),
    ("stellar_swirl_fake", False),
    ("stellar_superconduct", False),
    ("swirl", False),
    ("lunar_electro", False),
])
def test_public_stellar_swirl_damage_family_is_finite(actual, matches):
    assert _selector(
        {"event": "reaction_damage", "phase": "pre", "reaction_types": ["stellar_swirl"]},
        {"event": "reaction_damage", "id": "sample", "reaction_type": actual},
    ) is matches


@pytest.mark.parametrize("reaction", ["swirl", "stellar_superconduct", "lunar_electro"])
def test_other_public_reaction_damage_selectors_keep_exact_semantics(reaction):
    selector = {"event": "reaction_damage", "phase": "pre", "reaction_types": [reaction]}
    assert _selector(selector, {"event": "reaction_damage", "id": "sample", "reaction_type": reaction})
    assert not _selector(selector, {
        "event": "reaction_damage", "id": "sample", "reaction_type": "stellar_swirl_anemo",
    })


def test_family_does_not_change_elemental_trigger_tags_or_other_selector_fields():
    selector = {"event": "elemental_reaction", "phase": "post", "reaction_types": ["stellar_swirl"]}
    assert _selector(selector, {
        "event": "elemental_reaction", "id": "trigger", "reaction_type": "stellar_swirl",
        "reaction_trigger_tags": ["stellar_swirl"],
    })
    assert not _selector(selector, {
        "event": "elemental_reaction", "id": "trigger", "reaction_type": "stellar_swirl_anemo",
    })
    assert not _selector({
        "event": "reaction_damage", "phase": "pre", "reaction_types": ["stellar_swirl"],
        "characters": ["provider"],
    }, {"event": "reaction_damage", "id": "component", "reaction_type": "stellar_swirl_anemo",
        "character": "other"})


def test_public_family_buff_reaches_real_immediate_and_delayed_contributor_damage():
    template, rotation, mechanics = _application_fixture(
        "anemo", initial=[aura("cryo", .7, "b")])
    second_character(template, rotation, "b")
    template["characters"][1]["stats"].update(crit_rate=1, crit_dmg=1)
    _enable(mechanics, "stellar_swirl")
    baseline = execute_mechanics(*copy.deepcopy((template, rotation, mechanics)))
    boost = rule("family_cd", "benefit.crit_dmg.all", .5, duration=9,
                 beneficiaries={"mode": "characters", "characters": ["b"], "scope": "locked"})
    boost["effects"][0]["filter"] = {
        "event": "reaction_damage", "phase": "pre", "reaction_types": ["stellar_swirl"],
    }
    add(mechanics, boost)
    actual = execute_mechanics(template, rotation, mechanics)
    assert actual["status"] in ("passed", "unproven"), actual["issues"]
    before = {h["id"]: h for h in baseline["result"]["hits"]}
    affected = []
    for hit in actual["result"]["hits"]:
        if hit["character"] == "b" and hit["damage_system"] == "stellar":
            affected.append(hit)
            assert hit["expected"] == pytest.approx(before[hit["id"]]["expected"] * 1.25)
            assert any(m["effect_id"] == "family_cd:value:0" for m in hit["matched_effect_refs"])
        else:
            assert hit["expected"] == before[hit["id"]]["expected"]
    assert len(affected) == 2  # Immediate Anemo and the delayed Cryo vortex.
    assert {h["t"] for h in affected} == {.2, 3.2}
