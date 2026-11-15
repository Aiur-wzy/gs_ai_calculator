"""Family preparation returns closed plans without changing live World roots."""

from __future__ import annotations

import copy

from test_elemental_world_lunar_electro_integrity import _world
from test_elemental_world_stellar_swirl_deadline_adversarial import (
    seed_vortex,
    world_with_targets,
)

from genshin_engine.elemental_world_lunar_planner import plan_lunar_electro_world_batch
from genshin_engine.elemental_world_lunar_plans import (
    LunarElectroTargetAdmission,
    LunarElectroWorldBatchPlan,
)
from genshin_engine.elemental_world_star_planner import plan_stellar_swirl_world_batch
from genshin_engine.elemental_world_stellar_plans import (
    StellarTargetApplicationPlan,
    StellarWorldBatchPlan,
)


def test_lunar_preparation_keeps_live_target_cloud_log_and_identity_unchanged():
    world = _world()
    world._advance_targets_to(250_000)
    due = world.special_runtime.advance_world_pre_expiry(250_000)
    state, events = copy.deepcopy(world.state()), world.drain_generated_events()
    special, target = world.special_runtime, world.get_target("enemy.a")
    plan = plan_lunar_electro_world_batch(world, due, boundary_tick=250_000)
    assert isinstance(plan, LunarElectroWorldBatchPlan)
    assert len(plan.admissions) == 1
    assert isinstance(plan.admissions[0], LunarElectroTargetAdmission)
    assert plan.admissions[0].target is target
    assert plan.candidate is not special
    assert world.state() == state and world.drain_generated_events() == events
    assert world.special_runtime is special and world.get_target("enemy.a") is target


def test_star_preparation_keeps_live_target_vortex_log_and_identity_unchanged():
    world = world_with_targets([("enemy", (), "known")])
    seed_vortex(world)
    world._advance_targets_to(3_000_000)
    due = world.special_runtime.advance_world_pre_expiry(3_000_000)
    state, events = copy.deepcopy(world.state()), world.drain_generated_events()
    special, target = world.special_runtime, world.get_target("enemy")
    plan = plan_stellar_swirl_world_batch(world, due, time_tick=3_000_000, source="deadline")
    assert isinstance(plan, StellarWorldBatchPlan)
    assert len(plan.target_applications) == 1
    assert isinstance(plan.target_applications[0], StellarTargetApplicationPlan)
    assert plan.target_ids == ("enemy",)
    assert plan.candidate is not special
    assert world.state() == state and world.drain_generated_events() == events
    assert world.special_runtime is special and world.get_target("enemy") is target
