"""Independent D18 adversarial cases; expected values come from reviewed rules."""
from __future__ import annotations

import copy

import pytest
from test_mechanics_execution import ability, action, add, fixture, rule

from genshin_engine.elemental import ElementalRuntime, validate_elemental_model
from genshin_engine.mechanics import execute_mechanics


def elemental_model():
    return {
        "schema_version": 1, "ruleset": "gauge-basic-v1",
        "target": {"id": "enemy", "kind": "ordinary", "knowledge": "known",
                   "initial_aura": None, "status": "confirmed", "source": "independent rule audit"},
        "profiles": [
            {"id": f"{element}{gu}", "element": element, "application": "apply",
             "gauge_units": gu, "icd": {"kind": "none"},
             "status": "confirmed", "source": "controlled input, KQM generic gauge rules"}
            for element in ("pyro", "hydro", "cryo") for gu in (1, 2)
        ],
    }


def apply(runtime, profile_id, t, *, event="event", actor="a", landed=True):
    return runtime.attempt(event_id=event, action_id="cast", actor=actor, time=round(t * 1_000_000),
                           application={"profile_id": profile_id, "target_id": "enemy", "landed": landed})


@pytest.mark.parametrize(("element", "first", "later", "later_t", "expires"), [
    ("hydro", 1, 2, 1, 20), ("cryo", 2, 1, 10, 16),
    ("pyro", 1, 2, 1, 13), ("pyro", 2, 1, 10, 19.5),
    ("pyro", 2, 1, 1, 12),
])
def test_review_same_element_refresh_clocks(element, first, later, later_t, expires):
    runtime = ElementalRuntime(elemental_model())
    apply(runtime, f"{element}{first}", 0)
    apply(runtime, f"{element}{later}", later_t)
    runtime.advance(round((expires - .001) * 1_000_000))
    assert runtime.aura is not None
    runtime.advance(round(expires * 1_000_000))
    assert runtime.aura is None


def test_review_standard_icd_timer_reanchors_on_first_late_hit_not_grid():
    model = elemental_model()
    model["profiles"][0]["icd"] = {"kind": "standard", "group": "normal"}
    runtime = ElementalRuntime(model)
    assert [apply(runtime, "pyro1", t)["allowed"] for t in (0, 3, 5.4, 5.5)] == [True, True, False, True]


def test_review_miss_does_not_consume_shared_icd_and_actors_are_independent():
    model = elemental_model()
    model["profiles"][0]["icd"] = {"kind": "standard", "group": "normal"}
    runtime = ElementalRuntime(model)
    assert apply(runtime, "pyro1", 0)["allowed"] is True
    assert apply(runtime, "pyro1", .1, landed=False)["reason"] == "missed"
    assert apply(runtime, "pyro1", .2)["allowed"] is False
    assert apply(runtime, "pyro1", .3, actor="b")["allowed"] is True
    assert apply(runtime, "pyro1", .4)["allowed"] is False
    assert apply(runtime, "pyro1", .5)["allowed"] is True


def test_review_exhausted_aura_never_becomes_surplus_trigger_aura():
    runtime = ElementalRuntime(elemental_model())
    apply(runtime, "hydro1", 0)
    first = apply(runtime, "pyro1", 0)
    second = apply(runtime, "pyro1", 0)
    third = apply(runtime, "pyro1", 0)
    assert first["after"]["aura"]["remaining_gu"] == pytest.approx(.3)
    assert second["reaction"]["factor"] == 1.5
    assert second["reaction"]["consumed_gu"] == pytest.approx(.3)
    assert second["after"]["aura"] is None
    assert third["reaction"] is None
    assert third["after"]["aura"]["element"] == "pyro"


@pytest.mark.parametrize(("field", "value"), [("remaining_gu", 0), ("remaining_gu", -.1),
                                               ("decay_gu_per_second", 0), ("decay_gu_per_second", -.1)])
def test_review_initial_aura_requires_positive_amount_and_decay(field, value):
    model = elemental_model()
    model["target"]["initial_aura"] = {"element": "hydro", "remaining_gu": .8,
        "decay_gu_per_second": .8 / 9.5, "source": "controlled initial state"}
    model["target"]["initial_aura"][field] = value
    assert validate_elemental_model(model), "zero/negative aura must not be accepted"


def test_review_boolean_is_not_a_gauge_unit():
    model = elemental_model()
    model["profiles"][0]["gauge_units"] = True
    assert validate_elemental_model(model)


def input_fixture():
    template, rotation, mechanics = fixture((.2, .3, .4))
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    mechanics["elemental_model"] = elemental_model()
    for hit, descriptor in zip(template["hits"], mechanics["hit_events"], strict=True):
        hit["element"] = "pyro"
        descriptor["application"] = {"profile_id": "pyro1", "target_id": "enemy", "landed": True}
    return template, rotation, mechanics


def test_review_nondamage_application_enters_same_queue_and_changes_damage():
    template, rotation, mechanics = input_fixture()
    mechanics["application_events"] = [{"id": "apply_water", "action_id": "cast", "t": .2, "order": 9,
        "application": {"profile_id": "hydro1", "target_id": "enemy", "landed": True}}]
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] != "invalid", output["issues"]
    assert [hit["expected"] for hit in output["result"]["hits"]] == pytest.approx([67.5, 67.5, 45])


def test_review_unknown_after_verified_prefix_withholds_authoritative_total():
    template, rotation, mechanics = input_fixture()
    mechanics["elemental_model"]["profiles"].append({"id": "unknown", "element": "pyro",
        "application": "unknown", "status": "pending", "source": "unresolved rule"})
    mechanics["hit_events"][1]["application"]["profile_id"] = "unknown"
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] == "pending"
    assert output["result"] is None
    assert output["resolved_template"] is None
    assert output["elemental_trace"][0]["status"] == "executed"
    assert output["elemental_trace"][-1]["status"] == "pending"


def test_review_pending_no_application_is_not_confirmed_no_application():
    template, rotation, mechanics = input_fixture()
    profile = mechanics["elemental_model"]["profiles"][0]
    profile.update(application="none", status="pending")
    del profile["gauge_units"]
    del profile["icd"]
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] == "pending"
    assert output["result"] is None


def test_review_explicit_hit_identity_cannot_collide_with_derived_reaction():
    template, rotation, mechanics = input_fixture()
    mechanics["elemental_model"]["target"]["initial_aura"] = {
        "element": "hydro", "remaining_gu": .8, "decay_gu_per_second": .8 / 9.5, "source": "review"}
    template["hits"][1]["id"] = "h0:reaction"
    mechanics["hit_events"][1]["hit_id"] = "h0:reaction"
    output = execute_mechanics(template, rotation, mechanics)
    if output["status"] == "invalid":
        assert output["issues"]
        return
    ids = [row["event_id"] for row in output["elemental_trace"]]
    ids.extend(row["reaction"]["id"] for row in output["elemental_trace"] if row["reaction"])
    assert len(ids) == len(set(ids))


def test_review_reaction_selector_uses_stable_base_id_across_cycles():
    template, rotation, mechanics = input_fixture()
    rotation["actions"][0]["repeat"] = {"mode": "each_cycle"}
    mechanics["application_events"] = [{"id": "apply_water", "action_id": "cast", "t": .2, "order": 9,
        "application": {"profile_id": "hydro1", "target_id": "enemy", "landed": True}}]
    add(mechanics, rule(trigger={"event": "elemental_reaction", "phase": "post",
                                "event_ids": ["h0:reaction"]}))
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] not in ("invalid", "failed", "pending"), output["issues"]
    assert [hit["expected"] for hit in output["result"]["hits"]] == pytest.approx([67.5, 135, 90])
    activations = [row for row in output["mechanics_trace"] if row["type"] == "activation"]
    assert len(activations) >= 2
    assert {row["event_id"] for row in activations} == {"h0:reaction"}
    assert len({row["cycle"] for row in activations}) == len(activations)


def test_review_reaction_selector_rejects_parent_hit_id_instead_of_silent_miss():
    template, rotation, mechanics = input_fixture()
    add(mechanics, rule(trigger={"event": "elemental_reaction", "phase": "post", "event_ids": ["h0"]}))
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] == "invalid"
    assert any(issue["code"] == "reference" for issue in output["issues"])


def test_review_reaction_resources_enable_later_burst_without_retroactive_parent_buff():
    template, rotation, mechanics = input_fixture()
    initial = mechanics["elemental_model"]["target"]
    initial["initial_aura"] = {"element": "hydro", "remaining_gu": .8,
        "decay_gu_per_second": .8 / 9.5, "source": "controlled initial water"}
    rotation["characters"][0]["initial_energy"] = 0
    rotation["abilities"].append(ability("burst", kind="action.elemental_burst", energy={"required": 10, "amount": 10}))
    rotation["actions"].append(action("burst_cast", "burst", start=.25, end=.26, repeat={"mode": "once"}))
    rotation["events"].append({"id": "spend", "type": "energy_spend", "t": .25, "order": 1,
        "action_id": "burst_cast", "character": "a", "required": 10, "amount": 10,
        "repeat": {"mode": "once"}, "status": "assumed", "source": "engineering check"})
    generated = rule("reaction_resource", trigger={"event": "elemental_reaction", "phase": "post",
         "reaction_types": ["vaporize"]},
         resources=[{"id": "energy", "kind": "energy_direct", "delay": 0, "amounts": {"a": 10}, "source_part": "energy"}])
    add(mechanics, generated)
    mechanics["resource_ownership"] = [{"rule_id": "reaction_resource", "action_id": "cast",
        "source_part": "energy", "replaces_event_ids": []}]
    original = copy.deepcopy((template, rotation, mechanics))
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] not in ("invalid", "failed", "pending"), output["issues"]
    assert [hit["expected"] for hit in output["result"]["hits"]] == pytest.approx([67.5, 135, 90])
    assert any(row["type"] == "action" and row["id"] == "burst_cast" and row["status"] == "passed"
               for row in output["rotation_check"]["event_log"])
    assert (template, rotation, mechanics) == original
