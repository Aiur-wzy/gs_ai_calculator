"""Adversarial integration checks for the v4 freezable Frozen child state."""
from __future__ import annotations

import copy

import pytest
from test_elemental_multiaura_v4 import attempt, aura, model
from test_mechanics_execution import fixture

from genshin_engine.elemental import ElementalPending, ElementalRuntime
from genshin_engine.mechanics import execute_mechanics


def by_element(state):
    return {item["element"]: item for item in state["auras"]}


def test_freeze_creation_retains_stronger_underlying_aura_and_providers():
    runtime = ElementalRuntime(model([aura("hydro", 1.4, "water-owner")]))
    row = attempt(runtime, "cryo", actor="ice-owner", event_id="freeze")
    assert row["reaction"]["type"] == "freeze"
    assert row["reaction"]["aura_owner"] == "water-owner"
    assert row["reaction"]["trigger_owner"] == "ice-owner"
    assert row["reaction"]["new_frozen_gu"] == pytest.approx(2)
    assert row["after"]["frozen"]["remaining_gu"] == pytest.approx(2)
    assert by_element(row["after"])["hydro"]["remaining_gu"] == pytest.approx(.4)
    assert row["freeze_update"]["retained_ordinary_aura"]["element"] == "hydro"


def test_refreeze_uses_max_gauge_and_preserves_accumulated_history():
    runtime = ElementalRuntime(model([aura("hydro", 1.4)]))
    attempt(runtime, "cryo", event_id="first")
    runtime.advance(1_000_000)
    assert runtime.state()["frozen"]["remaining_gu"] == pytest.approx(1.55)
    attempt(runtime, "hydro", t=1, event_id="water-refresh")
    row = attempt(runtime, "cryo", t=1, event_id="refreeze")
    assert row["freeze_update"]["operation"] == "frozen_refreshed"
    assert row["reaction"]["previous_frozen_gu"] == pytest.approx(1.55)
    assert row["reaction"]["new_frozen_gu"] == pytest.approx(1.6)
    assert row["after"]["frozen"]["remaining_gu"] == pytest.approx(1.6)
    assert row["after"]["freeze_history_seconds"] == pytest.approx(1)
    assert row["freeze_update"]["history_preserved"] is True


def test_anemo_consumes_underlying_hydro_before_frozen_layer():
    runtime = ElementalRuntime(model([aura("hydro", 1.4, "water-owner")]))
    attempt(runtime, "cryo", actor="ice-owner", event_id="freeze")
    row = attempt(runtime, "anemo", actor="wind-owner", event_id="wind")
    assert row["reaction_priority"]["policy"] == "ordinary_aura_then_frozen_v1"
    candidates = row["reaction_priority"]["ranked_candidates"]
    assert [(item["layer"], item["reaction_element"], item["status"])
            for item in candidates] == [
                ("hydro", "hydro", "reacted"),
                ("frozen", "cryo", "reacted"),
            ]
    assert [reaction["reaction_element"] for reaction in row["reactions"]] == ["hydro", "cryo"]
    assert candidates[0]["consumed_gu"] == pytest.approx(.4)
    assert candidates[0]["trigger_gu_after"] == pytest.approx(.2)
    assert candidates[1]["consumed_gu"] == pytest.approx(.1)
    assert candidates[1]["trigger_gu_after"] == pytest.approx(0)
    assert row["after"]["frozen"]["remaining_gu"] == pytest.approx(1.9)


def test_anemo_trace_marks_frozen_layer_skipped_when_underlying_exhausts_trigger():
    runtime = ElementalRuntime(model([aura("hydro", 2)]))
    attempt(runtime, "cryo", event_id="freeze")
    row = attempt(runtime, "anemo", event_id="wind")
    candidates = row["reaction_priority"]["ranked_candidates"]
    assert candidates[0]["status"] == "reacted"
    assert candidates[1]["status"] == "skipped"
    assert candidates[1]["skip_reason"] == "trigger_exhausted"
    assert [reaction["reaction_element"] for reaction in row["reactions"]] == ["hydro"]
    assert row["after"]["frozen"]["remaining_gu"] == pytest.approx(2)


def test_same_element_underlying_and_frozen_layers_aggregate_one_cryo_swirl():
    runtime = ElementalRuntime(model([aura("cryo", 1.4, "old-ice")]))
    attempt(runtime, "hydro", actor="water", event_id="freeze")
    row = attempt(runtime, "anemo", actor="wind", event_id="wind")
    assert len(row["reactions"]) == 1
    assert row["reactions"][0]["reaction_element"] == "cryo"
    assert row["reactions"][0]["consumed_gu"] == pytest.approx(.5)
    assert [item["layer"] for item in row["reactions"][0]["layers"]] == ["cryo", "frozen"]
    assert [item["status"] for item in row["reaction_priority"]["ranked_candidates"]] == [
        "reacted", "reacted"]


@pytest.mark.parametrize("time, reacts", [(1.999999, True), (2, False), (2.000001, False)])
def test_expiry_boundary_never_exposes_ghost_frozen_layer(time, reacts):
    runtime = ElementalRuntime(model([aura("hydro", .5)]))
    attempt(runtime, "cryo", event_id="freeze")
    row = attempt(runtime, "anemo", t=time, event_id="wind")
    assert bool(row["reactions"]) is reacts
    assert runtime.state()["frozen"] is None


def test_v4_deadline_decay_and_history_recovery_match_reviewed_freeze_state():
    runtime = ElementalRuntime(model([aura("hydro", .5)]))
    attempt(runtime, "cryo", event_id="freeze")
    first = runtime.next_state_deadline()
    assert first == {"time": 2_000_000, "revision": 1,
                     "reason": "frozen_expired", "state_kind": "multi_aura_freeze"}
    runtime.advance(first["time"])
    assert runtime.state()["frozen"] is None
    assert runtime.state()["freeze_history_seconds"] == pytest.approx(2)
    second = runtime.next_state_deadline()
    assert second["time"] == 3_000_000
    assert second["reason"] == "freeze_history_recovered"
    runtime.advance(second["time"])
    assert runtime.state()["freeze_history_seconds"] == pytest.approx(0)
    assert [row["reason"] for row in runtime.trace
            if row["type"] == "elemental_state_transition"] == [
                "frozen_expired", "freeze_history_recovered"]


@pytest.mark.parametrize("element", ["pyro", "geo"])
def test_undefined_frozen_reactions_are_pending_and_atomically_rolled_back(element):
    runtime = ElementalRuntime(model([aura("hydro", .7)]))
    attempt(runtime, "cryo", event_id="freeze")
    runtime.profiles[element]["icd"] = {"kind": "standard", "group": "pending-contact"}
    before = copy.deepcopy(runtime.state())
    before_rules = copy.deepcopy(runtime.icd_rules)
    with pytest.raises(ElementalPending) as error:
        attempt(runtime, element, event_id="unsupported")
    assert error.value.issue["code"] == "unsupported_frozen_reaction"
    assert runtime.state() == before
    assert runtime.icd == {}
    assert runtime.icd_rules == before_rules


def test_nonfrozen_amplifying_reaction_consumes_the_freeze_adapter_aura_without_ghosting():
    runtime = ElementalRuntime(model([aura("hydro", .7)]))
    row = attempt(runtime, "pyro", event_id="vape")
    assert row["reaction"]["type"] == "vaporize"
    assert by_element(row["after"])["hydro"]["remaining_gu"] == pytest.approx(.2)
    assert runtime.multi_state.freeze_state.aura["remaining_gu"] == pytest.approx(.2)


def test_real_queue_schedules_v4_frozen_expiry_and_history_recovery():
    template, rotation, mechanics = fixture((0, 0))
    template["loop_duration"] = rotation["period"] = 2
    rotation["max_cycles"] = 3
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    mechanics["elemental_model"] = model()
    for hit, descriptor, element in zip(
            template["hits"], mechanics["hit_events"], ("hydro", "cryo"), strict=True):
        hit["element"] = element
        descriptor["application"] = {
            "profile_id": element, "target_id": "enemy", "landed": True}
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] in ("passed", "unproven"), output["issues"]
    transitions = [row for row in output["elemental_trace"]
                   if row["type"] == "elemental_state_transition"]
    assert [row["reason"] for row in transitions] == [
        "frozen_expired", "freeze_history_recovered"]
    final = output["result"]["elemental"]["final_checked_state"]
    assert final["frozen"] is None
    assert final["freeze_history_seconds"] == pytest.approx(0)


def test_real_queue_projects_underlying_hydro_then_frozen_cryo_swirl_damage():
    template, rotation, mechanics = fixture((0, 0))
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    mechanics["elemental_model"] = model([aura("hydro", 1.4, "water-owner")])
    for hit, descriptor, element in zip(
            template["hits"], mechanics["hit_events"], ("cryo", "anemo"), strict=True):
        hit["element"] = element
        descriptor["application"] = {
            "profile_id": element, "target_id": "enemy", "landed": True}
    output = execute_mechanics(template, rotation, mechanics)
    assert output["status"] in ("passed", "unproven"), output["issues"]
    derived = [hit for hit in output["resolved_template"]["hits"]
               if hit["damage_system"] == "transformative"]
    assert [hit["id"] for hit in derived] == [
        "h1:reaction_damage:swirl:hydro",
        "h1:reaction_damage:swirl:cryo",
    ]
