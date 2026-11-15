from __future__ import annotations

import copy

import pytest

from genshin_engine.elemental_world import (
    ElementalWorldPending,
    ElementalWorldRuntime,
)

EVIDENCE = {"status": "assumed", "source": "controlled world-runtime fixture"}


def model(target_id: str) -> dict:
    return {
        "schema_version": 1,
        "ruleset": "gauge-multi-aura-v4",
        "target": {
            "id": target_id,
            "kind": "ordinary",
            "knowledge": "known",
            "initial_auras": [],
            "freeze": {"freezable": True, "resistance": 0, **EVIDENCE},
            **EVIDENCE,
        },
        "profiles": [
            {
                "id": element,
                "element": element,
                "application": "apply",
                "gauge_units": 1,
                "icd": {"kind": "none"},
                **EVIDENCE,
            }
            for element in ("pyro", "hydro", "cryo", "electro", "anemo", "geo")
        ],
        "swirl_damage": {
            "icd": {"kind": "sequence", "reset_seconds": .5,
                    "sequence": [1, 1, 0]},
            **EVIDENCE,
        },
    }


def add_core(world: ElementalWorldRuntime, index: int, target_id: str, time: int) -> dict:
    return world.bloom_core_state.create(
        time=time,
        event_id=f"core.{index}",
        target_id=target_id,
        area_id=world.area_ids_for_target(target_id)[0],
        owner="hydro-owner",
        parent_reaction_id=f"bloom.{index}",
        contributor_ids=["hydro-owner", "dendro-owner"],
        source="controlled world-runtime fixture",
    )


def test_registry_and_area_query_are_stable_and_unknown_area_fails_closed():
    world = ElementalWorldRuntime()
    right = world.register_target(model("enemy-b"), area_ids=("room.b", "shared"))
    left = world.register_target(model("enemy-a"), area_ids=("shared", "room.a"))

    assert world.get_target("enemy-a") is left
    assert world.get_target("enemy-b") is right
    assert world.targets_in_area("shared") == ("enemy-a", "enemy-b")
    assert world.targets_in_area("room.a") == ("enemy-a",)
    with pytest.raises(ElementalWorldPending) as error:
        world.targets_in_area("room.typo")
    assert error.value.issue["code"] == "world_area_unknown"


def test_shared_bloom_field_limit_spans_targets_and_areas():
    world = ElementalWorldRuntime()
    left = world.register_target(model("enemy-a"), area_ids=("room.a",))
    right = world.register_target(model("enemy-b"), area_ids=("room.b",))
    assert left.bloom_core_state is world.bloom_core_state
    assert right.bloom_core_state is world.bloom_core_state

    for index in range(1, 6):
        add_core(world, index, "enemy-a", index)
    sixth = add_core(world, 6, "enemy-b", 6)

    assert sixth["overflow_detonation"]["event_id"] == "core.1:detonate"
    assert sixth["overflow_detonation"]["target_id"] == "enemy-a"
    assert [row["id"] for row in world.bloom_core_state.state()["cores"]] == [
        "core.2", "core.3", "core.4", "core.5", "core.6"]


def test_atomic_rollback_preserves_shared_object_and_target_identities():
    world = ElementalWorldRuntime()
    left = world.register_target(model("enemy-a"))
    right = world.register_target(model("enemy-b"))
    bloom_identity = world.bloom_core_state
    crystallize_identity = world.crystallize_world
    before = copy.deepcopy(world.state())

    def fail(value: ElementalWorldRuntime):
        add_core(value, 1, "enemy-a", 0)
        value.get_target("enemy-a").multi_state.add_or_refresh(
            "hydro", 1, "left-source", "left-owner")
        value.get_target("enemy-b").multi_state.add_or_refresh(
            "cryo", 1, "right-source", "right-owner")
        raise ElementalWorldPending("fixture_pending", "controlled rollback")

    with pytest.raises(ElementalWorldPending):
        world.execute_atomic(fail)

    assert world.state() == before
    assert world.bloom_core_state is bloom_identity
    assert world.crystallize_world is crystallize_identity
    assert world.get_target("enemy-a") is left
    assert world.get_target("enemy-b") is right
    assert left.bloom_core_state is bloom_identity
    assert right.bloom_core_state is bloom_identity
    assert left.crystallize_state.world is crystallize_identity
    assert right.crystallize_state.world is crystallize_identity


def test_generated_event_cursor_is_replayable_and_not_destructive():
    world = ElementalWorldRuntime()
    world.register_target(model("enemy"))
    add_core(world, 1, "enemy", 0)

    receipt = world.advance(6_000_000)
    assert receipt["generated_event_count"] == 1
    first = world.drain_generated_events(0)
    replay = world.drain_generated_events(0)
    assert replay == first
    assert first["cursor"] == 1
    assert first["events"][0]["event_id"] == "core.1:detonate"
    assert world.drain_generated_events(first["cursor"]) == {
        "events": [], "cursor": first["cursor"]}


def test_advance_failure_on_second_target_rolls_back_first_and_world_time(monkeypatch):
    world = ElementalWorldRuntime()
    left = world.register_target(model("enemy-a"))
    right = world.register_target(model("enemy-b"))
    before = copy.deepcopy(world.state())

    def pending(_time):
        raise ElementalWorldPending("fixture_second_target_pending", "controlled")

    monkeypatch.setattr(right, "advance", pending)
    with pytest.raises(ElementalWorldPending):
        world.advance(1_000_000)

    assert world.time == 0
    assert left.time == 0
    assert world.state() == before
