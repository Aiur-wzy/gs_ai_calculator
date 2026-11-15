from __future__ import annotations

import pytest

from genshin_engine.elemental_crystallize import (
    TICKS,
    CrystallizeState,
    CrystallizeWorldState,
    crystallize_profile,
    crystallize_shield_absorption,
)


def commit(state, t, suffix="0", element="pyro", owner="geo"):
    return state.commit(time=round(t*TICKS), event_id=f"hit:{suffix}:reaction:crystallize",
                        run_id=f"0:hit:{suffix}:reaction:crystallize", action_id="cast.geo",
                        owner=owner, element=element, source="独立结晶工程夹具",
                        consumed_gu=.5, trigger_gu=1)


def test_crystallize_gcd_is_shared_per_target_and_blocked_attempt_cannot_commit():
    state = CrystallizeState("enemy")
    first = commit(state, 0, element="pyro", owner="a")
    assert first["reaction"]["reaction_element"] == "pyro"
    assert state.admit(round(.999999*TICKS))["reason"] == "crystallize_gcd"
    with pytest.raises(ValueError, match="冷却"):
        commit(state, .5, suffix="blocked", element="hydro", owner="b")
    assert len(state.state()["shards"]) == 1
    second = commit(state, 1, suffix="1", element="hydro", owner="b")
    assert second["reaction"]["reaction_element"] == "hydro"
    assert second["next_reaction_t"] == 2


def test_shards_are_typed_pickable_and_expire_deterministically():
    state = CrystallizeState("enemy")
    receipt = commit(state, 0, element="electro")
    shard = receipt["shard"]
    assert shard["expires_at"] == 17.5
    pickup = state.pickup(shard_id=shard["id"], character="frontline", time=TICKS)
    assert pickup["element"] == "electro"
    assert pickup["shield"]["expires_at"] == 16
    assert pickup["shield"]["source_owner"] == "geo"
    assert state.state()["active_shield"]["same_element_absorption_multiplier"] == 2.5
    assert state.state()["active_shield"]["scope"] == "party"
    assert state.state()["shards"] == []
    shard = commit(state, 2, suffix="later", element="cryo")["shard"]
    transitions = state.advance(round(19.5*TICKS))
    assert transitions[0] == {"type": "crystallize_shield_expired", "t": 16.0,
                              "target_id": "enemy", "character": "frontline",
                              "element": "electro", "source_reaction_id": "hit:0:reaction:crystallize"}
    assert transitions[1] == {"type": "crystallize_shard_expired", "t": 19.5,
                            "target_id": "enemy", "shard_id": shard["id"],
                            "element": "cryo", "reason": "lifetime"}


def test_pickup_replaces_and_refreshes_the_single_party_shield_across_characters():
    state = CrystallizeState("enemy")
    pyro = commit(state, 0, element="pyro")["shard"]
    first = state.pickup(shard_id=pyro["id"], character="frontline", time=0,
                         absorption=1000)
    assert first["replaced_shield"] is None
    hydro = commit(state, 1, suffix="hydro", element="hydro")["shard"]
    second = state.pickup(shard_id=hydro["id"], character="other-member", time=TICKS,
                          absorption=1200)
    assert second["replaced_shield"]["element"] == "pyro"
    assert second["shield"]["element"] == "hydro"
    assert second["shield"]["picked_up_by"] == "other-member"
    assert second["shield"]["expires_at"] == 16
    assert len(state.state()["shields"]) == 1
    assert state.state()["active_shield"]["element"] == "hydro"


def test_shared_world_keeps_target_gates_independent_but_shards_and_party_shield_global():
    world = CrystallizeWorldState()
    left = CrystallizeState("enemy-a", world=world)
    right = CrystallizeState("enemy-b", world=world)
    left_shard = commit(left, 0, suffix="left", element="pyro")["shard"]
    right_shard = commit(right, 0, suffix="right", element="cryo")["shard"]
    assert left.next_reaction_time == right.next_reaction_time == TICKS
    assert {row["target_id"] for row in world.state()["shards"]} == {
        "enemy-a", "enemy-b"}
    with pytest.raises(ValueError, match="不属于"):
        right.pickup(shard_id=left_shard["id"], character="b", time=0)
    first = left.pickup(shard_id=left_shard["id"], character="a", time=0,
                        absorption=100)
    second = right.pickup(shard_id=right_shard["id"], character="b", time=0,
                          absorption=200)
    assert first["replaced_shield"] is None
    assert second["replaced_shield"]["picked_up_by"] == "a"
    assert world.state()["active_shield"]["picked_up_by"] == "b"
    assert len(world.state()["shields"]) == 1


def test_field_overflow_keeps_three_newest_only_after_cleanup_delay():
    state = CrystallizeState("enemy")
    for i, element in enumerate(("pyro", "hydro", "cryo", "electro")):
        commit(state, i, suffix=str(i), element=element)
    assert len(state.state()["shards"]) == 4
    assert state.advance(round(4.999999*TICKS)) == []
    transitions = state.advance(5*TICKS)
    assert [row["reason"] for row in transitions] == ["field_limit"]
    assert [row["element"] for row in state.state()["shards"]] == ["hydro", "cryo", "electro"]


@pytest.mark.parametrize("element", ["geo", "anemo", "dendro", "physical"])
def test_non_crystallizable_elements_are_rejected_without_mutation(element):
    state = CrystallizeState("enemy")
    with pytest.raises(ValueError, match="火、水、冰、雷"):
        commit(state, 0, element=element)
    assert state.state()["next_reaction_t"] == 0
    assert state.state()["shards"] == []


def test_runtime_database_supplies_complete_reviewed_shield_curve_and_em_formula():
    profile = crystallize_profile()
    assert len(profile["shield_level_curve"]) == 100
    assert profile["shield_level_curve"]["90"] == pytest.approx(1851.060302734375)
    assert profile["shield_level_curve"]["100"] == pytest.approx(2198.81396484375)

    no_em = crystallize_shield_absorption(90, 0)
    assert no_em["absorption"] == pytest.approx(no_em["base_absorption"])
    em = crystallize_shield_absorption(90, 1400)
    assert em["em_multiplier"] == pytest.approx(1 + 20 / 9)
    assert em["absorption"] == pytest.approx(1851.060302734375 * (1 + 20 / 9))


@pytest.mark.parametrize(("level", "em"), [(0, 0), (101, 0), (90, -1), (90, float("inf"))])
def test_crystallize_shield_formula_rejects_unavailable_or_invalid_inputs(level, em):
    with pytest.raises(ValueError):
        crystallize_shield_absorption(level, em)


def test_trigger_time_shield_sample_follows_shard_and_pickup_keeps_trigger_owner():
    state = CrystallizeState("enemy")
    receipt = state.commit(
        time=0, event_id="hit:sample:reaction:crystallize",
        run_id="0:hit:sample:reaction:crystallize", action_id="cast.geo",
        owner="geo-trigger", element="cryo", source="reviewed profile test",
        consumed_gu=.5, trigger_gu=1, trigger_level=90, trigger_em=1400,
    )
    sample = receipt["shard"]["shield_absorption_sample"]
    pickup = state.pickup(shard_id=receipt["shard"]["id"], character="collector", time=0)
    assert pickup["shield"]["source_owner"] == "geo-trigger"
    assert pickup["shield"]["absorption"] == pytest.approx(sample["absorption"])
    assert pickup["shield"]["absorption_sample"]["profile_id"] == "native.ordinary-crystallize.v1"
