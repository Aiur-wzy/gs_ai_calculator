"""Star-Swirl routed proof over isolated candidates; never commits live roots."""

from __future__ import annotations

import copy

from genshin_engine.elemental import ElementalPending
from genshin_engine.elemental_target_port import commit_reaction_routes
from genshin_engine.elemental_world_layer_binding import require_stellar_layer_binding
from genshin_engine.elemental_world_star_planning_port import WorldStarPlanningPort
from genshin_engine.elemental_world_stellar_plans import (
    OrdinaryStarPreview,
    StellarRoutedTargetProof,
    StellarSpecialRoutes,
)


def prove_star_routed_target(world: WorldStarPlanningPort, ordinary: OrdinaryStarPreview,
                             routes_plan: StellarSpecialRoutes, request, *, target_id) -> StellarRoutedTargetProof:
    runtime, base_attempt = ordinary.runtime, ordinary.attempt
    target_event_id, target_state_before = ordinary.target_event_id, ordinary.target_state_before
    reference_preview_receipt = ordinary.reference_receipt
    routes, layer_event = routes_plan.routes, routes_plan.layer_event
    reference_routed_preview = world.reference_target_copy(runtime)
    reference_routed_application = (
        reference_routed_preview.register_special_application(
            request, target_id=target_id))
    expected_profiles_after_registration = copy.deepcopy(
        reference_routed_preview.profiles)
    reference_routed_attempt = {
        **base_attempt,
        "application": reference_routed_application,
    }
    try:
        expected_commit_receipt = (
            commit_reaction_routes(reference_routed_preview,
                reaction_routes=routes,
                **reference_routed_attempt,
            ))
    except ElementalPending as exc:
        raise world.reaction_pending_from_runtime(exc) from exc
    expected_commit_generated = (
        reference_routed_preview.drain_generated_events())
    reference_receipt_before_layer = copy.deepcopy(
        expected_commit_receipt)
    reference_trace_before_layer = copy.deepcopy(
        reference_routed_preview.trace[-1])
    reference_target_before_layer = world.target_commit_snapshot(
        reference_routed_preview)
    world.bind_stellar_layer_to_target_receipt(
        reference_routed_preview,
        expected_commit_receipt,
        layer_event,
        target_id=target_id,
        event_id=target_event_id,
        issue_code=(
            "world_stellar_swirl_target_reference_invalid"),
    )
    require_stellar_layer_binding(world,
        reference_routed_preview,
        expected_commit_receipt,
        layer_event,
        receipt_before=reference_receipt_before_layer,
        trace_before=reference_trace_before_layer,
        target_snapshot_before=reference_target_before_layer,
        target_id=target_id,
        event_id=target_event_id,
        issue_code=(
            "world_stellar_swirl_target_reference_invalid"),
    )
    if (expected_commit_receipt.get("after")
            != reference_routed_preview.state()
            or expected_commit_receipt.get("after")
            != reference_preview_receipt.get("after")
            or reference_routed_preview.profiles
            != expected_profiles_after_registration
            or reference_routed_preview.trace
            != [*runtime.trace, expected_commit_receipt]):
        raise world.reaction_pending(
            "world_stellar_swirl_target_reference_invalid",
            "风旋附着的规范路由预演回执、元素后态、档案或轨迹不一致。",
            {"target_id": target_id,
             "event_id": target_event_id},
        )
    expected_target_state_after = world.target_commit_snapshot(
        reference_routed_preview)

    routed_preview = runtime.preview_copy()
    routed_preview.trace = copy.deepcopy(runtime.trace)
    routed_preview_application = (
        routed_preview.register_special_application(
            request, target_id=target_id))
    routed_preview_attempt = {
        **base_attempt,
        "application": routed_preview_application,
    }
    try:
        routed_preview_receipt = (
            commit_reaction_routes(routed_preview,
                reaction_routes=routes,
                **routed_preview_attempt,
            ))
    except ElementalPending as exc:
        raise world.reaction_pending_from_runtime(exc) from exc
    routed_preview_generated = (
        routed_preview.drain_generated_events())
    routed_receipt_before_layer = copy.deepcopy(
        routed_preview_receipt)
    routed_trace_before_layer = copy.deepcopy(
        routed_preview.trace[-1])
    routed_target_before_layer = world.target_commit_snapshot(
        routed_preview)
    world.bind_stellar_layer_to_target_receipt(
        routed_preview,
        routed_preview_receipt,
        layer_event,
        target_id=target_id,
        event_id=target_event_id,
        issue_code=(
            "world_stellar_swirl_target_preview_mismatch"),
    )
    require_stellar_layer_binding(world,
        routed_preview,
        routed_preview_receipt,
        layer_event,
        receipt_before=routed_receipt_before_layer,
        trace_before=routed_trace_before_layer,
        target_snapshot_before=routed_target_before_layer,
        target_id=target_id,
        event_id=target_event_id,
        issue_code=(
            "world_stellar_swirl_target_preview_mismatch"),
    )
    if (routed_preview_receipt.get("after")
            != routed_preview.state()
            or routed_preview_receipt != expected_commit_receipt
            or routed_preview_generated
            != expected_commit_generated
            or world.target_commit_snapshot(routed_preview)
            != expected_target_state_after
            or world.target_commit_snapshot(runtime)
            != target_state_before):
        raise world.reaction_pending(
            "world_stellar_swirl_target_preview_mismatch",
            "风旋附着的动态路由预演回执或完整后态与规范参考执行不一致。",
            {"target_id": target_id,
             "event_id": target_event_id},
        )
    return StellarRoutedTargetProof(expected_commit_receipt, expected_commit_generated,
                                    expected_target_state_after, expected_profiles_after_registration)

