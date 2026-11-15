from __future__ import annotations

import copy

import pytest
from test_elemental_multiaura_v4 import aura
from test_elemental_multiaura_v4 import model as elemental_model
from test_special_reactions import reaction
from test_special_reactions import runtime as special_runtime

from genshin_engine.elemental_world import (
    ElementalWorldPending,
    ElementalWorldRuntime,
)

AREA = "arena.lunar-electro"


def target_model(target_id: str, *, hydro=1.2, electro=1.2,
                 hydro_owner="qualification.provider",
                 electro_owner="trigger.actor") -> dict:
    initial = [
        aura("hydro", hydro, hydro_owner),
        aura("electro", electro, electro_owner),
    ]
    value = elemental_model(initial, freezable=False)
    value["target"]["id"] = target_id
    return value


def cloud_for(*target_ids: str, area_id=AREA):
    special = special_runtime("lunar_electro")
    for index, target_id in enumerate(target_ids, 1):
        special.replace(
            reaction(
                "lunar_electro", index, target=target_id,
                area_id=area_id,
            ),
            time=0,
        )
    return special


def world_with_two_targets(*, right_hydro=1.2,
                           right_area=AREA) -> ElementalWorldRuntime:
    world = ElementalWorldRuntime(
        special_runtime=cloud_for("enemy.a", "enemy.b"))
    world.register_target(target_model("enemy.b", hydro=right_hydro),
                          area_ids=(right_area,))
    world.register_target(target_model("enemy.a"), area_ids=(AREA,))
    return world


def lunar_rows(world: ElementalWorldRuntime) -> list[dict]:
    return [
        row for row in world.drain_generated_events()["events"]
        if row.get("reaction_type") == "lunar_electro"
    ]


def test_world_admits_same_boundary_targets_from_live_owner_ledgers_once():
    world = world_with_two_targets()
    special_identity = world.special_runtime

    receipt = world.advance(250_000)

    assert receipt["lunar_electro_batch_count"] == 1
    assert world.special_runtime is special_identity
    rows = lunar_rows(world)
    assert [(row["target_id"], row["type"]) for row in rows] == [
        ("enemy.a", "special_reaction_tick_request"),
        ("enemy.a", "special_reaction_tick_admission"),
        ("enemy.a", "special_reaction_damage_request"),
        ("enemy.b", "special_reaction_tick_request"),
        ("enemy.b", "special_reaction_tick_admission"),
        ("enemy.b", "special_reaction_damage_request"),
    ]
    admissions = [row for row in rows
                  if row["type"] == "special_reaction_tick_admission"]
    assert all(row["allowed"] for row in admissions)
    assert all(row["contributor_ids"] == [
        "qualification.provider", "trigger.actor"] for row in admissions)
    for target_id in ("enemy.a", "enemy.b"):
        components = world.get_target(
            target_id)._lunar_electro_aura_snapshot()["components"]
        assert components["hydro"]["remaining_gu"] == pytest.approx(.79975)
        assert components["electro"]["remaining_gu"] == pytest.approx(.79975)


def test_known_insufficient_aura_is_committed_as_empty_tick_not_pending():
    world = world_with_two_targets(right_hydro=.3)

    world.advance(250_000)

    rows = lunar_rows(world)
    right = next(row for row in rows
                 if row["type"] == "special_reaction_tick_admission"
                 and row["target_id"] == "enemy.b")
    assert right["allowed"] is False
    assert right["reason"] == "hydro_electro_coexistence_or_gauge_insufficient"
    assert not [row for row in rows
                if row["type"] == "special_reaction_damage_request"
                and row["target_id"] == "enemy.b"]
    right_components = world.get_target(
        "enemy.b")._lunar_electro_aura_snapshot()["components"]
    assert right_components["hydro"]["remaining_gu"] == pytest.approx(.29975)
    assert right_components["electro"]["remaining_gu"] == pytest.approx(1.19975)
    assert world.time == 250_000


def test_exactly_sufficient_live_layers_commit_and_disappear():
    # Initial .40025U decays to exactly .4U at the reviewed .25s first tick.
    world = ElementalWorldRuntime(special_runtime=cloud_for("enemy"))
    world.register_target(
        target_model("enemy", hydro=.40025, electro=.40025),
        area_ids=(AREA,),
    )

    world.advance(250_000)

    rows = lunar_rows(world)
    admission = next(row for row in rows
                     if row["type"] == "special_reaction_tick_admission")
    assert admission["allowed"] is True
    assert len([row for row in rows
                if row["type"] == "special_reaction_damage_request"]) == 1
    assert world.get_target(
        "enemy")._lunar_electro_aura_snapshot()["components"] == {}


def test_target_outside_declared_area_rolls_back_entire_world_batch():
    world = world_with_two_targets(right_area="arena.other")
    before = copy.deepcopy(world.state())
    special_identity = world.special_runtime
    target_identities = {
        target_id: world.get_target(target_id)
        for target_id in ("enemy.a", "enemy.b")
    }

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(250_000)

    assert error.value.issue["code"] == "world_lunar_electro_target_outside_area"
    assert world.state() == before
    assert world.special_runtime is special_identity
    assert all(world.get_target(target_id) is identity
               for target_id, identity in target_identities.items())
    assert world.drain_generated_events() == {"events": [], "cursor": 0}


def test_unattributed_owner_ledger_on_second_target_rolls_back_first_target():
    world = ElementalWorldRuntime(
        special_runtime=cloud_for("enemy.a", "enemy.b"))
    world.register_target(
        target_model("enemy.a"), area_ids=(AREA,))
    world.register_target(
        target_model("enemy.b", hydro_owner=None), area_ids=(AREA,))
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(250_000)

    assert error.value.issue["code"] == "lunar_electro_owner_residual_unproven"
    assert world.state() == before
    assert world.drain_generated_events() == {"events": [], "cursor": 0}


def test_special_clock_advances_once_per_boundary_not_once_per_target(monkeypatch):
    world = world_with_two_targets()
    calls = []
    original = world.special_runtime.advance

    def spy(time_tick):
        calls.append(time_tick)
        return original(time_tick)

    monkeypatch.setattr(world.special_runtime, "advance", spy)

    world.advance(1_000_000)

    assert calls == [250_000, 1_000_000]
    assert len([row for row in lunar_rows(world)
                if row["type"] == "special_reaction_tick_request"]) == 2


def test_external_target_clock_advance_fails_before_world_mutation():
    world = world_with_two_targets()
    world.get_target("enemy.a").advance(1)
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(250_000)

    assert error.value.issue["code"] == "world_clock_desynchronized"
    assert world.state() == before


def test_non_lunar_special_deadline_remains_pending_and_atomic():
    special = special_runtime("lunar_bloom")
    special.replace(reaction("lunar_bloom"), time=0)
    world = ElementalWorldRuntime(special_runtime=special)
    world.register_target(target_model("enemy"), area_ids=("battlefield.primary",))
    before = copy.deepcopy(world.state())

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(2_500_000)

    assert error.value.issue["code"] == "world_special_deadline_consumer_missing"
    assert world.state() == before


def test_world_cursor_replays_lunar_tick_events_without_consuming_them():
    world = world_with_two_targets()
    world.advance(250_000)

    first = world.drain_generated_events(0)
    assert world.drain_generated_events(0) == first
    assert world.drain_generated_events(first["cursor"]) == {
        "events": [], "cursor": first["cursor"]}
