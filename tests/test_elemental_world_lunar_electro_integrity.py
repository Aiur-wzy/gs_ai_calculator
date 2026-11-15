"""Fail-closed integrity checks for world-owned Lunar-Charged batches."""

from __future__ import annotations

import copy

import pytest
from test_elemental_world_lunar_electro_adversarial import (
    FIRST_TICK,
    create_world,
)

from genshin_engine.elemental import ElementalRuntime
from genshin_engine.elemental_world import ElementalWorldPending
from genshin_engine.special_reactions import SpecialReactionRuntime


def _world():
    return create_world(
        [{"target_id": "enemy.a"}],
        bindings=[("enemy.a", "area.primary")],
    )


def _assert_rollback(world, before_state, before_events):
    assert world.state() == before_state
    assert world.drain_generated_events(0) == before_events


@pytest.mark.parametrize(
    "mode",
    ["missing_first", "missing_middle", "empty", "extra", "past_expiry"],
)
def test_entire_pending_schedule_must_match_phase_and_expiry(mode):
    world = _world()
    scheduled = world.special_runtime.scheduled
    if mode == "missing_first":
        scheduled[:] = [row for row in scheduled if row["tick_index"] != 1]
    elif mode == "missing_middle":
        scheduled[:] = [row for row in scheduled if row["tick_index"] != 2]
    elif mode == "empty":
        scheduled.clear()
    else:
        forged = copy.deepcopy(scheduled[-1])
        forged["tick_index"] = 99
        forged["event_id"] = forged["event_id"].rsplit(":", 1)[0] + ":99"
        if mode == "extra":
            forged["time_tick"] = 5_250_000
            forged["t"] = 5.25
        else:
            forged["time_tick"] = 6_250_000
            forged["t"] = 6.25
        scheduled.append(forged)
    before_state = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_schedule_conflict"
    _assert_rollback(world, before_state, before_events)


def test_forged_trace_cannot_hide_a_missing_phase_tick():
    world = _world()
    hidden = next(
        copy.deepcopy(row)
        for row in world.special_runtime.scheduled
        if row["tick_index"] == 2
    )
    world.special_runtime.scheduled.remove(next(
        row for row in world.special_runtime.scheduled
        if row["event_id"] == hidden["event_id"]
    ))
    hidden.pop("schedule_key", None)
    world.special_runtime.trace.append(hidden)
    before_state = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)

    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_schedule_conflict"
    _assert_rollback(world, before_state, before_events)


@pytest.mark.parametrize(
    "mode",
    [
        "admission_id", "zero_consumption", "damage_owner", "damage_parent",
        "damage_profile", "damage_component", "damage_policy",
        "damage_reaction_type", "damage_system", "damage_factor",
        "admission_extra", "damage_extra", "unrelated_area",
        "current_cloud_expiry", "current_target_ledger", "non_cloud_field",
    ],
)
def test_world_admission_wrapper_cannot_forge_receipt_or_candidate(
    monkeypatch,
    mode,
):
    world = _world()
    world.special_runtime._lunar_cloud_state("area.unrelated")
    before_state = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = SpecialReactionRuntime.admit_lunar_electro_tick_for_world

    def tamper(runtime, tick, aura_state):
        admitted = original(runtime, tick, aura_state)
        if mode == "admission_id":
            admitted["admission"]["event_id"] += ":forged"
        elif mode == "zero_consumption":
            admitted["admission"]["consumed_gu"] = {
                "hydro": 0, "electro": 0,
            }
            admitted["aura_after"] = copy.deepcopy(admitted["aura_before"])
        elif mode == "damage_owner":
            admitted["damage_event"]["owner"] = "fourth"
        elif mode == "damage_parent":
            admitted["damage_event"]["parent_reaction_id"] = "forged"
        elif mode == "damage_profile":
            admitted["damage_event"]["profile"] = {"profile_id": "forged"}
        elif mode == "damage_component":
            admitted["damage_event"]["component"] = "forged"
        elif mode == "damage_policy":
            admitted["damage_event"]["contributor_sample_policy"] = "forged"
        elif mode == "damage_reaction_type":
            admitted["damage_event"]["reaction_type"] = "lunar_bloom"
        elif mode == "damage_system":
            admitted["damage_event"]["damage_system"] = "stellar"
        elif mode == "damage_factor":
            admitted["damage_event"]["system_factor"] = 999
        elif mode == "admission_extra":
            admitted["admission"]["forged"] = True
        elif mode == "damage_extra":
            admitted["damage_event"]["forged"] = True
        elif mode == "unrelated_area":
            runtime.lunar_cloud_by_area["area.unrelated"][
                "lunar_electro_active"] = True
        elif mode == "current_cloud_expiry":
            runtime.lunar_cloud_by_area["area.primary"][
                "lunar_electro_expires_at"] += 1
        elif mode == "current_target_ledger":
            runtime.lunar_cloud_by_area["area.primary"][
                "lunar_electro_last_attack_by_target"]["enemy.forged"] = 1
        else:
            runtime.party_state["lunar_bloom_dew"] = 999
        return admitted

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "admit_lunar_electro_tick_for_world",
        tamper,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_lunar_electro_admission_mismatch"
    _assert_rollback(world, before_state, before_events)


@pytest.mark.parametrize(
    "mode",
    ["drop", "extra", "tamper", "reverse", "state", "rebind"],
)
def test_live_target_commit_must_match_rows_snapshot_and_identity(
    monkeypatch,
    mode,
):
    world = _world()
    before_state = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    target = world.get_target("enemy.a")
    target_identity = target
    original = ElementalRuntime._commit_lunar_electro_tick_receipt

    def tamper(runtime, tick, admitted):
        rows = original(runtime, tick, admitted)
        if mode == "drop":
            return rows[:-1]
        if mode == "extra":
            forged = copy.deepcopy(rows[-1])
            forged["event_id"] += ":forged"
            return [*rows, forged]
        if mode == "tamper":
            rows[-1]["owner"] = "fourth"
        elif mode == "reverse":
            rows.reverse()
        elif mode == "state":
            runtime.generated_events.append({"type": "forged"})
        else:
            runtime.multi_state = copy.deepcopy(runtime.multi_state)
        return rows

    monkeypatch.setattr(
        ElementalRuntime,
        "_commit_lunar_electro_tick_receipt",
        tamper,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == (
        "world_lunar_electro_target_commit_mismatch")
    assert world.get_target("enemy.a") is target_identity
    _assert_rollback(world, before_state, before_events)


def test_live_aura_snapshot_reader_cannot_mutate_target_through_shared_helper(
    monkeypatch,
):
    world = _world()
    before_state = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = ElementalRuntime._lunar_electro_aura_snapshot_at

    def pollute_target(runtime, time_tick):
        snapshot = original(runtime, time_tick)
        runtime.model["audit_pollution"] = "forged"
        return snapshot

    monkeypatch.setattr(
        ElementalRuntime,
        "_lunar_electro_aura_snapshot_at",
        pollute_target,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == (
        "world_lunar_electro_target_snapshot_mismatch")
    _assert_rollback(world, before_state, before_events)


def test_admission_reference_isolates_shared_internal_helper(monkeypatch):
    world = _world()
    before_state = copy.deepcopy(world.state())
    before_events = world.drain_generated_events(0)
    original = SpecialReactionRuntime._profile_audit

    def pollute_candidate(runtime, reaction_type):
        result = original(runtime, reaction_type)
        if reaction_type == "lunar_electro":
            runtime.party_state["lunar_bloom_dew"] = 777
        return result

    monkeypatch.setattr(
        SpecialReactionRuntime,
        "_profile_audit",
        pollute_candidate,
    )
    with pytest.raises(ElementalWorldPending) as error:
        world.advance(FIRST_TICK)

    assert error.value.issue["code"] == "world_special_deadline_producer_mismatch"
    _assert_rollback(world, before_state, before_events)
