"""Prepare closed Star-Swirl target plans. World alone applies the batch."""

from __future__ import annotations

import copy

from genshin_engine.elemental_world_star_ordinary_proof import (
    preview_ordinary_star_target,
)
from genshin_engine.elemental_world_star_planning_port import WorldStarPlanningPort
from genshin_engine.elemental_world_star_routed_proof import prove_star_routed_target
from genshin_engine.elemental_world_star_routes_proof import plan_star_special_routes
from genshin_engine.elemental_world_stellar_plans import (
    StellarTargetApplicationPlan,
    StellarWorldBatchPlan,
)
from genshin_engine.special_reaction_reference import (
    copy_runtime as _copy_special_runtime_for_reference,
)


def plan_stellar_swirl_world_batch(world: WorldStarPlanningPort, events, *, time_tick, source) -> StellarWorldBatchPlan:
    pairs, paired_ids = world.stellar_swirl_pairs(
        events, time_tick=time_tick, source=source)
    for event in events:
        if event.get("event_id") in paired_ids:
            continue
        world.validate_stellar_swirl_passthrough(
            event, time_tick=time_tick, source=source)

    all_pair_targets: list[str] = []
    for pair in pairs:
        all_pair_targets.extend(pair["target_ids"])
    duplicates = sorted({target_id for target_id in all_pair_targets
                         if all_pair_targets.count(target_id) > 1})
    if duplicates:
        raise world.reaction_pending(
            "world_special_simultaneous_order_missing",
            "同一时点多个风旋区域附着命中同一目标，缺少受审区域顺序。",
            {"time_tick": time_tick, "target_ids": duplicates},
        )

    candidate = copy.deepcopy(world.special_runtime)
    reference_candidate = _copy_special_runtime_for_reference(
        world.special_runtime)
    plans: list[StellarTargetApplicationPlan] = []
    for pair in pairs:
        request = pair["application"]
        for ordinal, target_id in enumerate(pair["target_ids"]):
            ordinary = preview_ordinary_star_target(world, request, ordinal=ordinal,
                                                   target_id=target_id, time_tick=time_tick)
            routes = plan_star_special_routes(world, ordinary, candidate, reference_candidate,
                                              request, target_id=target_id, time_tick=time_tick)
            proof = prove_star_routed_target(world, ordinary, routes, request, target_id=target_id)
            plans.append(StellarTargetApplicationPlan(
                pair, target_id, ordinary.target_event_id, ordinary.target_state_before,
                ordinary.target_identity_before, request, ordinary.attempt, routes.routes,
                proof.receipt, proof.generated, proof.state_after, proof.profiles_after_registration,
                routes.generated, routes.layer_event,
            ))
    return StellarWorldBatchPlan(candidate, tuple(pairs), frozenset(paired_ids),
                                 tuple(all_pair_targets), tuple(plans))

