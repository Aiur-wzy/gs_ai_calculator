"""Emission/deadline ports preserve full execution, causal identity and ownership."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from test_elemental_multiaura_v4 import aura
from test_freeze_runtime import inputs as freeze_inputs
from test_mechanics_execution import add, fixture, rule, second_character
from test_mechanics_swirl import setup as swirl_inputs
from test_special_reaction_mechanics_projection import _application_fixture, _enable

from genshin_app.damage_explanation_reporting import validate_damage_explanations
from genshin_app.equipment.files import fingerprint
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.mechanics_scheduling import (
    ObserverEventEmitter,
    deadline_is_current,
    select_elemental_deadline,
)


def pre_observation_output(output):
    """Keep the frozen scheduling/numerical oracle, excluding exact new evidence.

    Four inputs were independently executed against commit 01eef88. Removing
    only these NEXT-01/04 additions restores all four original hashes and full
    dictionaries, including every existing reaction, time, value and identity.
    Ordinary elemental reaction traces already had action_id/run_id; preserve
    them rather than broadly removing all provenance fields.
    """
    legacy = copy.deepcopy(output)
    legacy.pop("special_state_timeline", None)
    legacy.pop("damage_execution_counts", None)
    result = legacy["result"]
    result.pop("damage_execution_counts", None)
    result.pop("special_state_timeline", None)
    result.pop("damage_explanations", None)
    result.pop("damage_explanation_fingerprint", None)
    for hit in result["hits"]:
        hit.pop("explanation_ref", None)
    for hit in legacy["resolved_template"]["hits"]:
        hit.get("extensions", {}).pop("execution_ref", None)
        if hit.get("extensions") == {}:
            hit.pop("extensions")
    for row in legacy["mechanics_trace"]:
        if row.get("type") not in ("hit", "reaction_damage"):
            continue
        row.pop("ability_id", None)
        row.pop("execution_ref", None)
        row.pop("in_input_window", None)
        row.pop("report_disposition", None)
        if not (row["type"] == "reaction_damage" and "reaction_run_id" in row):
            row.pop("action_id", None)
            row.pop("run_id", None)
    return legacy


@pytest.mark.parametrize(("case", "expected"), [
    ("delayed", "e7fbf6a388fd647171b1aad50917af5a113c06767156acb1a1f84e8ac903a608"),
    ("freeze", "94566a8ea28b5b48853442dd42fc8e6d2917900d1f5c7a37736ee047fc7dc723"),
    ("swirl", "df66ecffcc7bdef50e542c83a547381874c709c64323e5042792a2d7d2989dc2"),
    ("special", "f0760207c63c60658abc9bec0214a8af9cfec97f93cd509713d5c6cad596911a"),
])
def test_complete_execution_matches_accepted_pre_extraction_trace(case, expected):
    if case == "delayed":
        values = fixture((.2, .2, .4))
        boost = rule(duration=.2)
        boost["timing"]["delay"] = .15
        add(values[2], boost)
    elif case == "freeze":
        values = freeze_inputs()
    elif case == "swirl":
        values = swirl_inputs((.2, .3))
    else:
        values = _application_fixture("anemo", initial=[aura("cryo", .7, "b")])
        second_character(values[0], values[1], "b")
        _enable(values[2], "stellar_swirl")
    before = copy.deepcopy(values)
    output = execute_mechanics(*values)
    historical = pre_observation_output(output)
    if case == "special":
        # R13's shared special fixture now explicitly scopes its application to
        # the primary battlefield. Independent 4cb10a8 replay differs at this
        # single provenance leaf; every value, event and ordering stays frozen.
        assert values[2]["hit_events"][0]["application"]["area_id"] == "battlefield.primary"
        elemental = historical["resolved_template"]["hits"][0]["extensions"]["elemental"]
        assert elemental.pop("area_id") == "battlefield.primary"
    assert fingerprint(historical) == expected
    # New observational fields are not simply discarded: their own complete,
    # same-result binding and exact parent metadata are independently checked.
    result = output["result"]
    assert validate_damage_explanations(result) == "complete"
    assert result["damage_explanations"]["coverage"]["eligible"] == len(result["hits"])
    assert all(hit["explanation_ref"] in result["damage_explanations"]["events"] for hit in result["hits"])
    actions = {action["id"]: action for action in values[1]["actions"]}
    for row in output["mechanics_trace"]:
        if row.get("type") not in ("hit", "reaction_damage"):
            continue
        assert {"action_id", "ability_id", "run_id"} <= row.keys()
        if row["action_id"] is not None:
            assert row["ability_id"] == actions[row["action_id"]]["ability_id"]
        else:
            assert row["ability_id"] is None
        if row["type"] == "hit":
            assert row["run_id"] is None  # These explicit fixture hits have no parent run identity.
        elif "reaction_run_id" not in row:
            assert row["run_id"] == f"{row['cycle']}:{row['event_id']}"
    if case == "special":
        timeline = output["special_state_timeline"]
        assert timeline == result["special_state_timeline"]
        assert timeline["contract"] == "special-state-timeline-v1" and timeline["version"] == 1
        assert timeline["states"] == []  # Stellar swirl does not invent a superconduct field.
    assert values == before


def test_batch_identity_causal_serial_and_payload_alias_are_preserved():
    reserved = []
    emitter = ObserverEventEmitter(reserve_event=lambda: reserved.append(1), to_ticks=lambda t: int(t * 100))
    parent = {"id": "cast", "origin": 2, "time": 30, "order": 7, "causal": (1, 3)}
    payload = {"fixture": True}
    first = emitter.emit(parent, "hit", .2, payload, "damage")
    batch = emitter.batch
    assert first == {"id": "cast~damage", "run_id": "2:cast~damage", "time": 50,
                     "order": 7, "origin": 2, "causal": (1, 3, 1), "kind": "hit", "data": payload}
    assert first["data"] is payload
    emitter.begin_batch()
    second = emitter.emit(parent, "effect", 0, {}, "benefit")
    assert batch == [first] and batch is not emitter.batch
    assert emitter.batch == [second] and second["causal"] == (1, 3, 2)
    assert reserved == [1, 1]
    assert not hasattr(emitter, "time")


def test_reservation_failure_occurs_before_serial_increment_or_batch_write():
    failure = ValueError("budget fixture")

    def reserve():
        raise failure

    emitter = ObserverEventEmitter(reserve_event=reserve, to_ticks=lambda t: t)
    with pytest.raises(ValueError) as caught:
        emitter.emit({"id": "p", "time": 0, "order": 0, "origin": 0}, "hit", 0, {}, "x")
    assert caught.value is failure
    assert emitter.serial == 0 and emitter.batch == []


def test_deadline_offer_retains_priority_identity_and_deduplicates_without_queue_commit():
    reserved = []
    emitter = ObserverEventEmitter(reserve_event=lambda: reserved.append(1), to_ticks=lambda t: t)
    proposal = {"time": 100, "revision": 3, "state_kind": "frozen"}
    event = emitter.offer_deadline(proposal, target="enemy", origin=2)
    assert event == {"id": "@elemental_state:enemy:100:3", "run_id": "@elemental_state:enemy:100:3",
                     "time": 100, "origin": 2, "order": 0, "system_priority": -1,
                     "kind": "elemental_state_deadline", "data": {"revision": 3, "state_kind": "frozen"}}
    assert emitter.offer_deadline(proposal, target="enemy", origin=3) is None
    assert reserved == [1] and emitter.batch == [] and emitter.serial == 0
    assert deadline_is_current(event, proposal)
    assert not deadline_is_current(event, {**proposal, "revision": 4})
    assert emitter.offer_deadline(None, target="enemy") is None
    assert emitter.deadline_queued is None
    assert emitter.offer_deadline(proposal, target="enemy") == {**event, "origin": 0}


@pytest.mark.parametrize(("elemental_time", "special_time", "expected"), [
    (10, 20, "frozen"), (20, 10, "special_reaction_runtime"),
    (10, 10, "frozen"), (None, 10, "special_reaction_runtime"),
    (None, 101, None), (None, None, None),
])
def test_deadline_queries_are_read_only_ordered_and_keep_ties(elemental_time, special_time, expected):
    calls = []
    ordinary = ({"time": elemental_time, "revision": 1, "state_kind": "frozen"}
                if elemental_time is not None else None)

    def elemental(horizon):
        calls.append(("elemental", horizon))
        return ordinary

    def special():
        calls.append("special")
        return special_time

    def get_runtime():
        calls.append("runtime")
        return SimpleNamespace(next_deadline=special)

    result = select_elemental_deadline(SimpleNamespace(next_state_deadline=elemental), get_runtime, 100)
    assert calls == [("elemental", 100), "runtime", "special"]
    assert (result or {}).get("state_kind") == expected
    if expected == "frozen":
        assert result is ordinary


def test_copied_owner_and_emitter_retain_one_budget_owner():
    class Owner:
        def __init__(self):
            self.calls = 0
            self.emitter = ObserverEventEmitter(reserve_event=self.reserve, to_ticks=int)

        def reserve(self):
            self.calls += 1

    original = Owner()
    copied = copy.deepcopy(original)
    copied.emitter.emit({"id": "p", "time": 0, "order": 0, "origin": 0}, "hit", 0, {}, "x")
    assert copied.calls == 1 and original.calls == 0
