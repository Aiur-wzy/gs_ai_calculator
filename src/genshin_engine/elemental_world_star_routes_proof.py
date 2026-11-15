"""Star-Swirl routes proof over isolated candidates; never commits live roots."""

from __future__ import annotations

import copy

from genshin_engine.elemental_world_star_planning_port import WorldStarPlanningPort
from genshin_engine.elemental_world_stellar_plans import (
    OrdinaryStarPreview,
    StellarSpecialRoutes,
)
from genshin_engine.special_reaction_actual import SpecialReactionPending
from genshin_engine.special_reaction_port import reaction_config
from genshin_engine.special_reaction_reference import (
    record_stellar_superconduct_application as _REFERENCE_RECORD_STELLAR_SUPERCONDUCT_APPLICATION,
)
from genshin_engine.special_reaction_reference import (
    replace_special_reaction as _REFERENCE_REPLACE_SPECIAL_REACTION,
)


def plan_star_special_routes(world: WorldStarPlanningPort, ordinary: OrdinaryStarPreview,
                             candidate, reference_candidate, request, *, target_id, time_tick) -> StellarSpecialRoutes:
    previewed, target_event_id = ordinary.previewed, ordinary.target_event_id
    routes: list[dict] = []
    routed_generated: list[dict] = []
    for proposal in previewed["proposals"]:
        try:
            routed = candidate.replace(
                proposal, time=time_tick)
            reference_routed = (
                _REFERENCE_REPLACE_SPECIAL_REACTION(
                    reference_candidate,
                    proposal,
                    time=time_tick,
                ))
        except SpecialReactionPending as exc:
            raise world.reaction_pending_from_runtime(exc) from exc
        if (routed != reference_routed
                or world.special_commit_snapshot(candidate)
                != world.special_commit_snapshot(
                    reference_candidate)):
            raise world.reaction_pending(
                "world_stellar_swirl_route_producer_mismatch",
                "风旋冰附着的特殊路由回执或完整后态与隔离参考执行不一致。",
                {"target_id": target_id,
                 "proposal_id": proposal.get("id")},
            )
        reaction = routed.get("reaction")
        decision = routed.get("decision")
        if (not isinstance(reaction, dict)
                or not isinstance(decision, dict)):
            raise world.reaction_pending(
                "world_stellar_swirl_route_invalid",
                "风旋冰附着的世界特殊反应路由回执无效。",
                {"target_id": target_id,
                 "proposal_id": proposal.get("id")},
            )
        generated = routed.get("generated_events")
        if (not isinstance(generated, list)
                or any(not isinstance(row, dict)
                       for row in generated)):
            raise world.reaction_pending(
                "world_stellar_swirl_route_invalid",
                "风旋冰附着的世界特殊反应路由派生行无效。",
                {"target_id": target_id,
                 "proposal_id": proposal.get("id")},
            )
        routed_generated.extend(copy.deepcopy(generated))
        routes.append({
            "expected_proposal": copy.deepcopy(proposal),
            "replacement": copy.deepcopy(reaction),
            "decision": copy.deepcopy(decision),
        })

    layer_event = None
    preview_receipt = previewed["receipt"]
    if (preview_receipt.get("application_allowed") is True
            and reaction_config(candidate, "stellar_superconduct")
            is not None):
        try:
            layer = candidate.record_stellar_superconduct_application(
                event_id=target_event_id,
                target_id=target_id,
                actor=request["owner"],
                element="cryo",
                time=time_tick,
                application_allowed=True,
                direct_hit=True,
                atomic_parent_id=request["event_id"],
                area_id=request["area_id"],
            )
            reference_layer = (
                _REFERENCE_RECORD_STELLAR_SUPERCONDUCT_APPLICATION(
                    reference_candidate,
                    event_id=target_event_id,
                    target_id=target_id,
                    actor=request["owner"],
                    element="cryo",
                    time=time_tick,
                    application_allowed=True,
                    direct_hit=True,
                    atomic_parent_id=request["event_id"],
                    area_id=request["area_id"],
                ))
        except SpecialReactionPending as exc:
            raise world.reaction_pending_from_runtime(exc) from exc
        if (layer != reference_layer
                or world.special_commit_snapshot(candidate)
                != world.special_commit_snapshot(
                    reference_candidate)):
            raise world.reaction_pending(
                "world_stellar_swirl_layer_producer_mismatch",
                "风旋冰附着的星超导计层回执或完整后态与隔离参考执行不一致。",
                {"target_id": target_id,
                 "event_id": target_event_id},
            )
        layer_event = copy.deepcopy(layer.get("event"))
        layer_generated = layer.get("generated_events")
        if (not isinstance(layer_event, dict)
                or layer_generated != [layer_event]):
            raise world.reaction_pending(
                "world_stellar_swirl_layer_receipt_invalid",
                "风旋冰附着的星超导计层回执包含额外截止事件或身份不一致。",
                {"target_id": target_id,
                 "event_id": target_event_id},
            )
        routed_generated.append(copy.deepcopy(layer_event))
    return StellarSpecialRoutes(routes, routed_generated, layer_event)

