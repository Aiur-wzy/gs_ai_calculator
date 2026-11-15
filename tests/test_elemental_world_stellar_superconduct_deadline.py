"""World-owned Star-Superconduct settlement and expiry contracts."""
from __future__ import annotations

import copy

import pytest
from test_elemental_multiaura_v4 import model as elemental_model
from test_special_reactions import reaction
from test_special_reactions import runtime as special_runtime

from genshin_engine.elemental_world import (
    ElementalWorldPending,
    ElementalWorldRuntime,
)
from genshin_engine.special_reactions import SpecialReactionRuntime

AREA = "arena.stellar-superconduct"
TARGET = "enemy"


def world_with_stellar_field(*, layers: int = 1) -> ElementalWorldRuntime:
    special = special_runtime("stellar_superconduct")
    special.replace(
        reaction(
            "stellar_superconduct",
            target=TARGET,
            area_id=AREA,
        ),
        time=0,
    )
    for index in range(layers):
        special.record_stellar_superconduct_application(
            event_id=f"application.{index}",
            target_id=TARGET,
            actor="trigger.actor",
            element="cryo" if index % 2 == 0 else "electro",
            time=0,
            application_allowed=True,
            atomic_parent_id=f"later.hit.{index}",
            area_id=AREA,
        )
    world = ElementalWorldRuntime(special_runtime=special)
    target = elemental_model([], freezable=False)
    target["target"]["id"] = TARGET
    world.register_target(target, area_ids=(AREA,))
    return world


def stellar_rows(world: ElementalWorldRuntime) -> list[dict]:
    return [
        row for row in world.drain_generated_events()["events"]
        if row.get("reaction_type") == "stellar_superconduct"
    ]


def rollback_probe(world: ElementalWorldRuntime) -> tuple[dict, dict, object, object]:
    return (
        copy.deepcopy(world.state()),
        copy.deepcopy(world.drain_generated_events()),
        world.special_runtime,
        world.get_target(TARGET),
    )


def assert_rolled_back(
    world: ElementalWorldRuntime,
    probe: tuple[dict, dict, object, object],
) -> None:
    state, events, special_identity, target_identity = probe
    assert world.state() == state
    assert world.drain_generated_events() == events
    assert world.special_runtime is special_identity
    assert world.get_target(TARGET) is target_identity


def test_world_consumes_settlement_field_expiry_and_buff_expiry_in_order():
    world = world_with_stellar_field(layers=3)
    aura_before = copy.deepcopy(world.get_target(TARGET).state()["auras"])

    settled = world.advance(4_000_000)
    settlement = stellar_rows(world)[-1]
    assert settled["generated_event_count"] == 1
    assert settlement["operation"] == "settle_four_second_window"
    assert settlement["layers"] == 3
    assert settlement["system_factor"] == pytest.approx(1.55)
    assert settlement["next_settlement_at"] == world.special_runtime.stellar_field_by_area[AREA][
        "stellar_superconduct_next_settlement"] / 1_000_000 == 8
    assert world.special_runtime.stellar_superconduct_snapshot(AREA) == {
        "area_id": AREA,
        "active": True,
        "settled_buff_active": True,
        "layers": 3,
        "system_factor": pytest.approx(1.55),
        "ordinary_cryo_electro_bonus": pytest.approx(.31),
        "field_base_cryo_electro_bonus": pytest.approx(.2),
        "settled_cryo_electro_bonus": pytest.approx(.31),
        "field_expires_at": 6.0,
        "physical_res_delta": pytest.approx(-.4),
        "buff_expires_at": 8.0,
        "sample_policy": "last_completed_four_second_window",
    }

    expired = world.advance(6_000_000)
    field_expiry = stellar_rows(world)[-1]
    assert expired["generated_event_count"] == 1
    assert field_expiry["operation"] == "expire_field"
    snapshot = world.special_runtime.stellar_superconduct_snapshot(AREA)
    assert snapshot["active"] is False
    assert snapshot["settled_buff_active"] is True
    assert snapshot["layers"] == 3
    assert snapshot["physical_res_delta"] == 0

    buff_expired = world.advance(8_000_000)
    buff_expiry = stellar_rows(world)[-1]
    assert buff_expired["generated_event_count"] == 1
    assert buff_expiry["operation"] == "expire_settlement_buff"
    snapshot = world.special_runtime.stellar_superconduct_snapshot(AREA)
    assert snapshot["settled_buff_active"] is False
    assert snapshot["layers"] == 0
    assert snapshot["system_factor"] == 1
    assert world.get_target(TARGET).state()["auras"] == aura_before
    assert [row["operation"] for row in stellar_rows(world)] == [
        "settle_four_second_window",
        "expire_field",
        "expire_settlement_buff",
    ]


@pytest.mark.parametrize("forged", [99, True, None])
def test_forged_clock_observation_is_rejected_without_weakening_canonical_state_check(monkeypatch, forged):
    world = world_with_stellar_field()
    before = rollback_probe(world)
    original = world.special_runtime.advance_world_pre_expiry

    def corrupted(time_tick):
        rows = original(time_tick)
        for row in rows:
            if row.get("operation") == "settle_four_second_window":
                row["next_settlement_at"] = forged
        return rows

    monkeypatch.setattr(world.special_runtime, "advance_world_pre_expiry", corrupted)
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(4_000_000)
    assert error.value.issue["code"] == "world_stellar_superconduct_deadline_invalid"
    assert_rolled_back(world, before)


def test_legacy_settlement_without_clock_is_still_accepted_as_unknown(monkeypatch):
    world = world_with_stellar_field()
    original = world.special_runtime.advance_world_pre_expiry

    def legacy(time_tick):
        rows = original(time_tick)
        for row in rows:
            row.pop("next_settlement_at", None)
        return rows

    monkeypatch.setattr(world.special_runtime, "advance_world_pre_expiry", legacy)
    world.advance(4_000_000)
    row, = stellar_rows(world)
    assert "next_settlement_at" not in row
    assert world.special_runtime.stellar_field_by_area[AREA]["stellar_superconduct_next_settlement"] == 8_000_000


def test_settlement_at_field_expiry_observes_cleared_scheduler_separately_from_buff():
    world = world_with_stellar_field()
    world.advance(2_000_000)
    world.special_runtime.replace(reaction("stellar_superconduct", 2, target=TARGET, area_id=AREA),
                                  time=2_000_000)  # Refresh expiry to 8s, retain the 4s clock.
    world.advance(8_000_000)
    last = [row for row in stellar_rows(world) if row.get("operation") == "settle_four_second_window"][-1]
    assert last["t"] == 8
    assert last["next_settlement_at"] is None
    field = world.special_runtime.stellar_field_by_area[AREA]
    assert field["stellar_superconduct_next_settlement"] is None
    assert field["stellar_superconduct_buff_expires_at"] == 12_000_000


def test_segmented_and_single_long_world_advance_have_identical_state_rows():
    segmented = world_with_stellar_field(layers=2)
    segmented.advance(4_000_000)
    segmented.advance(6_000_000)
    segmented.advance(8_000_000)

    long = world_with_stellar_field(layers=2)
    long.advance(8_000_000)

    assert long.state() == segmented.state()
    assert stellar_rows(long) == stellar_rows(segmented)


def test_dropped_settlement_row_rolls_back_entire_world(monkeypatch):
    world = world_with_stellar_field()
    before = rollback_probe(world)
    original = world.special_runtime.advance_world_pre_expiry

    def dropped(time_tick):
        return [
            row for row in original(time_tick)
            if row.get("reaction_type") != "stellar_superconduct"
        ]

    monkeypatch.setattr(
        world.special_runtime, "advance_world_pre_expiry", dropped)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(4_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_superconduct_deadline_invalid")
    assert_rolled_back(world, before)


def test_corrupted_field_poststate_rolls_back_entire_world(monkeypatch):
    world = world_with_stellar_field()
    before = rollback_probe(world)
    original = world.special_runtime.advance_world_pre_expiry

    def corrupted(time_tick):
        rows = original(time_tick)
        world.special_runtime.stellar_field_by_area[AREA][
            "stellar_superconduct_factor"] = 999
        return rows

    monkeypatch.setattr(
        world.special_runtime, "advance_world_pre_expiry", corrupted)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(4_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_superconduct_deadline_invalid")
    assert_rolled_back(world, before)


def test_class_level_producer_patch_cannot_rewrite_isolated_reference(
    monkeypatch,
):
    world = world_with_stellar_field()
    before = rollback_probe(world)
    original = SpecialReactionRuntime.advance_world_pre_expiry

    def dropped(runtime, time_tick):
        return [
            row for row in original(runtime, time_tick)
            if row.get("reaction_type") != "stellar_superconduct"
        ]

    monkeypatch.setattr(
        SpecialReactionRuntime, "advance_world_pre_expiry", dropped)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(4_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_superconduct_deadline_invalid")
    assert_rolled_back(world, before)


def test_consumer_patch_cannot_mutate_target_state(monkeypatch):
    world = world_with_stellar_field()
    before = rollback_probe(world)
    original = ElementalWorldRuntime._consume_stellar_superconduct_deadline_batch

    def polluted(runtime, events, **kwargs):
        rows = original(runtime, events, **kwargs)
        runtime.get_target(TARGET).trace.append({
            "type": "forged_target_mutation",
            "event_id": "forged.target",
        })
        return rows

    monkeypatch.setattr(
        ElementalWorldRuntime,
        "_consume_stellar_superconduct_deadline_batch",
        polluted,
    )

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(4_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_superconduct_target_state_changed")
    assert_rolled_back(world, before)


def test_malformed_field_state_fails_closed_without_publication():
    world = world_with_stellar_field()
    field = world.special_runtime.stellar_field_by_area[AREA]
    field.pop("stellar_superconduct_atomic_parents")
    before = rollback_probe(world)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(4_000_000)

    assert error.value.issue["code"] == (
        "world_stellar_superconduct_deadline_invalid")
    assert_rolled_back(world, before)


@pytest.mark.parametrize(("field", "value"), [
    ("stellar_superconduct_factor", 999),
    ("stellar_superconduct_settled_layers", 1),
])
def test_semantically_corrupt_unsettled_baseline_fails_closed(field, value):
    world = world_with_stellar_field()
    world.special_runtime.stellar_field_by_area[AREA][field] = value
    before = rollback_probe(world)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(1)

    assert error.value.issue["code"] == (
        "world_stellar_superconduct_deadline_invalid")
    assert_rolled_back(world, before)


def test_semantically_corrupt_settled_bonus_fails_closed():
    world = world_with_stellar_field()
    field = world.special_runtime.stellar_field_by_area[AREA]
    field.update(
        stellar_superconduct_settled_layers=1,
        stellar_superconduct_factor=1.45,
        stellar_superconduct_ordinary_bonus=999,
        stellar_superconduct_buff_expires_at=8_000_000,
    )
    before = rollback_probe(world)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(1)

    assert error.value.issue["code"] == (
        "world_stellar_superconduct_deadline_invalid")
    assert_rolled_back(world, before)


def test_active_layer_parent_cardinality_mismatch_fails_closed():
    world = world_with_stellar_field(layers=1)
    world.special_runtime.stellar_field_by_area[AREA][
        "stellar_superconduct_atomic_parents"] = []
    before = rollback_probe(world)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(1)

    assert error.value.issue["code"] == (
        "world_stellar_superconduct_deadline_invalid")
    assert_rolled_back(world, before)


def test_inactive_field_cannot_retain_active_provenance():
    world = world_with_stellar_field(layers=1)
    field = world.special_runtime.stellar_field_by_area[AREA]
    field.update(
        stellar_superconduct_active=False,
        stellar_superconduct_layers=0,
        stellar_superconduct_expires_at=None,
        stellar_superconduct_next_settlement=None,
        stellar_superconduct_physical_res_delta=0.0,
        stellar_superconduct_atomic_parents=[],
    )
    before = rollback_probe(world)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(1)

    assert error.value.issue["code"] == (
        "world_stellar_superconduct_deadline_invalid")
    assert_rolled_back(world, before)


def test_non_stellar_runtime_skips_stellar_deadline_validation(monkeypatch):
    world = ElementalWorldRuntime(special_runtime=special_runtime("lunar_bloom"))

    def unexpected(*_args, **_kwargs):
        raise AssertionError("pure Lunar state entered Star-Superconduct validation")

    monkeypatch.setattr(
        world, "_validate_stellar_superconduct_field_state", unexpected)

    assert world._freeze_stellar_superconduct_deadlines(1) == ({}, {}, {})
