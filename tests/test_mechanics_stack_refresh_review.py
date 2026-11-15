"""Independent queue oracles for the generic common-expiry stack primitive."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_freeze_runtime import CONTACT
from tests.test_freeze_runtime import model as elemental_model
from tests.test_mechanics_execution import (
    ability,
    action,
    add,
    condition,
    fixture,
    rule,
    second_character,
)


def case(times):
    template, rotation, mechanics = fixture(times)
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    return template, rotation, mechanics


def source(*, events=None, duration=6, cooldown=0, policy="add_and_refresh"):
    trigger = {"event": "hit", "phase": "post"}
    if events is not None:
        trigger["event_ids"] = events
    item = rule("review-stack", value=.1, trigger=trigger, duration=duration,
                stacking={"max": 2, "add": 1, "policy": policy})
    item["timing"]["cooldown"] = cooldown
    return item


def run(values):
    before = copy.deepcopy(values)
    output = execute_mechanics(*values)
    assert output["result"] is not None, output["issues"]
    assert output["status"] in ("passed", "unproven"), output["issues"]
    assert values == before
    return output


def damage(output):
    return [row["expected"] for row in output["result"]["hits"]]


def activations(output):
    return [row for row in output["mechanics_trace"]
            if row["type"] == "activation" and row["rule_id"] == "review-stack"
            and row["cycle"] == 0]


def contacts(values, *, apply=False):
    template, _, mechanics = values
    mechanics["elemental_model"] = elemental_model()
    profile = next(p for p in mechanics["elemental_model"]["profiles"] if p["id"] == "cryo")
    if apply:
        profile["icd"] = {"kind": "standard", "group": "shared-test"}
    else:
        profile.clear()
        profile.update(id="cryo", element="cryo", application="none", status="assumed", source="工程明确不附着")
    for hit, descriptor in zip(template["hits"], mechanics["hit_events"], strict=True):
        descriptor.update(application={"profile_id": "cryo", "target_id": "enemy", "landed": True},
                          contact=copy.deepcopy(CONTACT))


def test_review_post_cap_refresh_cd_and_expiry_are_one_shared_clock():
    values = case((.2, .2, 1.2, 2.2, 2.5, 7.9, 8.2, 8.3))
    add(values[2], source(events=["h0", "h1", "h2", "h3", "h4", "h6"], cooldown=1))
    output = run(values)
    # 100 ATK * 0.5 defense * 0.9 resistance; post never applies to its own hit.
    assert damage(output) == pytest.approx([45, 49.5, 49.5, 54, 54, 54, 45, 49.5])
    rows = activations(output)
    assert [r["event_id"] for r in rows] == ["h0", "h2", "h3", "h6"]
    assert [r["layers"] for r in rows] == [1, 2, 2, 1]
    assert [r["expires_at"] for r in rows] == [[6.2], [7.2, 7.2], [8.2, 8.2], [14.2]]
    assert rows[0]["instance_id"] == rows[2]["instance_id"]
    assert rows[-1]["instance_id"] != rows[2]["instance_id"]


def test_review_hidden_hit_triggers_miss_does_not_and_icd_rejection_still_triggers():
    values = case((.2, .4, .6, 1.4))
    contacts(values, apply=True)
    mechanics = values[2]
    mechanics["hit_events"][0]["report"] = False
    mechanics["hit_events"][1]["application"]["landed"] = False
    add(mechanics, source(events=["h0", "h1", "h2"], duration=1))
    output = run(values)
    assert [h["id"] for h in output["result"]["hits"]] == ["h2", "h3"]
    assert damage(output) == pytest.approx([49.5, 54])
    assert [r["event_id"] for r in activations(output)] == ["h0", "h2"]
    assert activations(output)[-1]["expires_at"] == [1.6, 1.6]
    applications = [r for r in output["elemental_trace"]
                    if r["type"] == "elemental_application" and r["cycle"] == 0]
    assert [r["allowed"] for r in applications[:3]] == [True, False, False]
    assert applications[1]["reason"] == "missed"


@pytest.mark.parametrize("presence,expected,trigger_ids", [
    ("frontline", [45, 49.5, 45, 45], ["h0", "h3"]),
    ("any", [45, 49.5, 54, 54], ["h0", "h1", "h2", "h3"]),
])
def test_review_frontline_gates_new_triggers_but_does_not_remove_old_layers(presence, expected, trigger_ids):
    values = case((.2, .4, .8, 1.2))
    template, rotation, mechanics = values
    contacts(values)
    second_character(template, rotation)
    rotation["abilities"].extend([ability("a-to-b", "a", "action.switch"),
                                  ability("b-to-a", "b", "action.switch")])
    rotation["actions"].extend([
        action("leave", "a-to-b", .3, .31, switch_to="b", repeat={"mode": "once"}),
        action("return", "b-to-a", 1, 1.01, switch_to="a", repeat={"mode": "once"}),
    ])
    item = source(duration=.6)
    item["trigger"]["owner_presence"] = presence
    add(mechanics, item)
    output = run(values)
    assert damage(output) == pytest.approx(expected)
    assert [r["event_id"] for r in activations(output)] == trigger_ids


def test_review_partial_initial_expiry_is_removed_before_counting_then_shared_expiry():
    values = case((.5, .6, 1.499999, 1.5))
    item = source(events=["h0"], duration=1)
    item["stacking"]["max"] = 3
    add(values[2], item)
    values[2]["initial_state"]["rules"][0]["instances"] = [
        {"beneficiaries": ["a"], "remaining": [.25, .5, .75]},
    ]
    output = run(values)
    assert damage(output) == pytest.approx([49.5, 54, 54, 45])
    assert activations(output)[0]["layers"] == 2
    assert activations(output)[0]["expires_at"] == [1.5, 1.5]


def test_review_full_refresh_resamples_all_hp_layers_without_retroactive_parent_damage():
    values = case((.2, .4, .5, .6, .7))
    template, _, mechanics = values
    template["characters"][0]["stats"]["hp"] = 2000
    for ident, at in (("hp-first", .3), ("hp-second", .55)):
        condition(mechanics, ident, at)
        add(mechanics, rule(ident, "benefit.stat_add.hp", 2000,
                            trigger={"event": "condition", "phase": "pre", "event_ids": [ident]}))
    item = source(events=["h0", "h1", "h3"])
    item["effects"] = [{"id": "hp-sample", "benefit_type_id": "benefit.base_add.all", "domain": "provider",
                        "value": {"kind": "stat", "subject": "owner", "stat": "hp", "factor": .01, "sample": "trigger"}}]
    add(mechanics, item)
    output = run(values)
    assert damage(output) == pytest.approx([45, 54, 81, 81, 99])
    rows = [r for r in output["sampling_trace"] if r["rule_id"] == "review-stack" and r["cycle"] == 0]
    assert [r["source_value"] for r in rows] == [2000, 4000, 4000, 4000, 4000, 6000, 6000]


@pytest.mark.parametrize("policy,expected", [
    ("refresh", [45, 49.5, 49.5]),
    ("replace", [45, 49.5, 49.5]),
    ("independent", [45, 49.5, 49.5]),
    ("add_and_refresh", [45, 49.5, 54]),
])
def test_review_previous_policies_are_not_reinterpreted(policy, expected):
    values = case((.2, .4, 1.2))
    add(values[2], source(events=["h0", "h1"], duration=1, policy=policy))
    assert damage(run(values)) == pytest.approx(expected)


def test_review_reaction_child_cannot_add_another_ordinary_hit_layer():
    values = case((.2, .4))
    template, rotation, mechanics = values
    mechanics["elemental_model"] = elemental_model(
        {"element": "cryo", "remaining_gu": 3.2, "decay_gu_per_second": .001, "source": "工程冰底"})
    rotation["characters"][0]["element"] = "anemo"
    for hit, descriptor in zip(template["hits"], mechanics["hit_events"], strict=True):
        hit["element"] = "anemo"
        descriptor.update(application={"profile_id": "anemo", "target_id": "enemy", "landed": True}, contact=copy.deepcopy(CONTACT))
    add(mechanics, source())
    output = run(values)
    assert [r["event_id"] for r in activations(output)] == ["h0", "h1"]
    assert [r["layers"] for r in activations(output)] == [1, 2]
    assert len([h for h in output["result"]["hits"] if h["damage_system"] == "transformative"]) == 2


def test_review_pending_delays_count_layers_at_activation_not_trigger():
    values = case((.2, .4, 1.21, 1.35, 1.41))
    item = source(events=["h0", "h1"], duration=.15)
    item["timing"]["delay"] = 1
    add(values[2], item)
    output = run(values)
    # The .2-trigger layer expires at 1.35, before the .4-trigger activates at 1.4.
    assert damage(output) == pytest.approx([45, 45, 49.5, 45, 49.5])
    assert [(r["t"], r["layers"]) for r in activations(output)] == [(1.2, 1), (1.4, 1)]
