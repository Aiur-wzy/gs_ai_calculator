"""Integrated ordinary Crystallize checks on the v4 elemental runtime."""
from __future__ import annotations

import pytest
from test_elemental_multiaura_v4 import attempt, aura, layers, model

from genshin_engine.elemental import ElementalPending, ElementalRuntime
from genshin_engine.elemental_crystallize import CrystallizeWorldState


def test_geo_creates_typed_shard_without_damage_and_consumes_half_trigger_gu():
    runtime = ElementalRuntime(model([aura("electro", .8, "electro-owner")]))
    row = attempt(runtime, "geo", actor="geo-owner", event_id="geo-hit")
    reaction = row["reaction"]
    assert reaction["type"] == "crystallize"
    assert reaction["reaction_element"] == "electro"
    assert reaction["owner"] == "geo-owner"
    assert reaction["aura_owner"] == "electro-owner"
    assert reaction["damage"] is False
    assert reaction["consumed_gu"] == pytest.approx(.5)
    assert layers(row)["electro"] == pytest.approx(.3)
    assert row["crystallize_shards"][0]["element"] == "electro"
    assert row["after"]["crystallize"]["next_reaction_t"] == 1


def test_target_wide_one_second_gate_blocks_without_consuming_aura():
    runtime = ElementalRuntime(model([aura("hydro", 1.5)]))
    first = attempt(runtime, "geo", actor="geo-a", event_id="first")
    before = layers(first)["hydro"]
    blocked = attempt(runtime, "geo", .999999, actor="geo-b", event_id="blocked")
    assert blocked["reaction"] is None
    assert blocked["outcome"] == "crystallize_gcd_blocked"
    assert blocked["crystallize_attempts"][0]["reason"] == "crystallize_gcd"
    # Only natural decay occurred; the blocked reaction consumed no gauge.
    elapsed_decay = .999999 * .001
    assert layers(blocked)["hydro"] == pytest.approx(before - elapsed_decay)
    allowed = attempt(runtime, "geo", 1, actor="geo-b", event_id="boundary")
    assert allowed["reaction"]["type"] == "crystallize"
    assert allowed["reaction"]["owner"] == "geo-b"


@pytest.mark.parametrize("hydro_gu,cryo_gu", [(.1, .8), (.8, .1)])
def test_geo_fixed_route_is_hydro_then_cryo_independent_of_gauge(hydro_gu, cryo_gu):
    runtime = ElementalRuntime(model([aura("hydro", hydro_gu), aura("cryo", cryo_gu)],
                                     freezable=False))
    row = attempt(runtime, "geo", event_id="priority")
    assert row["reaction_priority"]["route"] == ["hydro", "cryo"]
    assert row["reaction"]["reaction_element"] == "hydro"
    assert row["crystallize_attempts"][0]["aura_element"] == "hydro"
    # The first successful ordinary Crystallize starts the shared GCD, so any
    # trigger remainder cannot create a second ordinary shard on this target.
    if len(row["crystallize_attempts"]) > 1:
        assert row["crystallize_attempts"][1]["reason"] == "crystallize_gcd"
        assert row["reaction_priority"]["ranked_candidates"][1]["skip_reason"] == "crystallize_gcd"


def test_geo_route_marks_second_candidate_when_trigger_is_exhausted():
    runtime = ElementalRuntime(model([aura("hydro", .8), aura("cryo", .8)],
                                     freezable=False))
    row = attempt(runtime, "geo", event_id="geo-exhaust")
    steps = row["reaction_priority"]["ranked_candidates"]
    assert steps[0]["status"] == "reacted"
    assert steps[1]["status"] == "skipped"
    assert steps[1]["skip_reason"] == "trigger_exhausted"


def test_crystallize_gate_is_not_shared_between_targets():
    left_model = model([aura("pyro", .8)])
    right_model = model([aura("cryo", .8)])
    right_model["target"]["id"] = "enemy-b"
    left = ElementalRuntime(left_model)
    right = ElementalRuntime(right_model)
    assert attempt(left, "geo", event_id="left")["reaction"]["type"] == "crystallize"
    row = right.attempt(event_id="right", stable_event_id="right", action_id="action",
                        actor="geo", time=0,
                        application={"profile_id": "geo", "target_id": "enemy-b",
                                     "landed": True},
                        trigger_stat_sample={
                            "contract": "ordinary-crystallize-trigger-stat-v1",
                            "character": "geo", "phase": "reaction_created",
                            "time_tick": 0, "level": 90, "em": 0.0,
                            "source": "controlled v4 adversarial fixture",
                        })
    assert row["reaction"]["type"] == "crystallize"


def test_runtime_missing_trigger_panel_sample_is_pending_and_atomic():
    runtime = ElementalRuntime(model([aura("hydro", .8, "hydro-owner")]))
    before = runtime.state()
    with pytest.raises(ElementalPending) as error:
        runtime.attempt(
            event_id="missing-sample", stable_event_id="missing-sample",
            action_id="action", actor="geo", time=0,
            application={"profile_id": "geo", "target_id": "enemy",
                         "landed": True})
    assert error.value.issue["code"] == "crystallize_shield_sample_missing"
    assert runtime.state() == before


def test_explicit_shared_world_has_independent_target_gcd_and_global_shards():
    world = CrystallizeWorldState()
    left_model = model([aura("pyro", .8)])
    right_model = model([aura("cryo", .8)])
    right_model["target"]["id"] = "enemy-b"
    left = ElementalRuntime(left_model, crystallize_world=world)
    right = ElementalRuntime(right_model, crystallize_world=world)
    attempt(left, "geo", actor="geo-a", event_id="left")
    sample = {
        "contract": "ordinary-crystallize-trigger-stat-v1",
        "character": "geo-b", "phase": "reaction_created",
        "time_tick": 0, "level": 90, "em": 0.0,
        "source": "controlled shared-world fixture",
    }
    row = right.attempt(
        event_id="right", stable_event_id="right", action_id="action",
        actor="geo-b", time=0,
        application={"profile_id": "geo", "target_id": "enemy-b",
                     "landed": True}, trigger_stat_sample=sample)
    assert row["reaction"]["type"] == "crystallize"
    assert len(world.state()["shards"]) == 2
    assert {item["target_id"] for item in world.state()["shards"]} == {
        "enemy", "enemy-b"}
    before = world.state()
    with pytest.raises(ElementalPending) as cross_target:
        right.pickup_crystallize_shard(
            event_id="bad-pickup", shard_id="left:reaction:0:shard",
            character="geo-b", target_id="enemy-b", time=0,
            source="controlled cross-target rejection fixture")
    assert cross_target.value.issue["code"] == (
        "crystallize_pickup_shard_target_mismatch")
    assert world.state() == before
    with pytest.raises(ElementalPending) as unknown:
        right.pickup_crystallize_shard(
            event_id="unknown-pickup", shard_id="missing",
            character="geo-b", target_id="enemy-b", time=0,
            source="controlled missing-shard rejection fixture")
    assert unknown.value.issue["code"] == "crystallize_shard_unavailable"
    assert world.state() == before
