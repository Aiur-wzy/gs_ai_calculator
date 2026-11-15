"""Star-Swirl ordinary proof over isolated candidates; never commits live roots."""

from __future__ import annotations

from genshin_engine.elemental import ElementalPending
from genshin_engine.elemental_target_port import preview_reaction_routes
from genshin_engine.elemental_world_star_planning_port import WorldStarPlanningPort
from genshin_engine.elemental_world_stellar_plans import OrdinaryStarPreview


def preview_ordinary_star_target(world: WorldStarPlanningPort, request, *, ordinal, target_id, time_tick) -> OrdinaryStarPreview:
    runtime = world.get_target(target_id)
    if runtime.time != time_tick:
        raise world.reaction_pending(
            "world_clock_desynchronized",
            "风旋区域目标没有推进到爆发时点。",
            {"target_id": target_id,
             "target_time_tick": runtime.time,
             "boundary_tick": time_tick},
        )
    if runtime.generated_events:
        raise world.reaction_pending(
            "world_stellar_swirl_target_events_not_collected",
            "风旋区域附着前目标仍有未归集派生事件。",
            {"target_id": target_id,
             "event_ids": [row.get("event_id")
                           for row in runtime.generated_events]},
        )
    target_event_id = (
        f"{request['event_id']}:target:{ordinal}:{target_id}")
    target_state_before = world.target_commit_snapshot(runtime)
    target_identity_before = world.target_identity_snapshot(
        runtime)
    base_attempt = {
        "event_id": target_event_id,
        "stable_event_id": target_event_id,
        "action_id": request["action_id"],
        "actor": request["owner"],
        "time": time_tick,
        "damage_element": "cryo",
    }

    reference_preview = world.reference_target_copy(
        runtime, preserve_trace=False)
    reference_preview_application = (
        reference_preview.register_special_application(
            request, target_id=target_id))
    reference_attempt = {
        **base_attempt,
        "application": reference_preview_application,
    }
    try:
        reference_previewed = (
            preview_reaction_routes(reference_preview,
                **reference_attempt))
    except ElementalPending as exc:
        raise world.reaction_pending_from_runtime(exc) from exc
    reference_preview_generated = (
        reference_preview.drain_generated_events())
    reference_preview_receipt = reference_previewed["receipt"]
    if (reference_preview_receipt.get("after")
            != reference_preview.state()
            or reference_preview.trace
            != [reference_preview_receipt]):
        raise world.reaction_pending(
            "world_stellar_swirl_target_reference_invalid",
            "风旋冰附着的普通参考预演回执、后态或轨迹不一致。",
            {"target_id": target_id,
             "event_id": target_event_id},
        )

    preview = runtime.preview_copy()
    preview_application = preview.register_special_application(
        request, target_id=target_id)
    attempt = {
        **base_attempt,
        "application": preview_application,
    }
    try:
        previewed = preview_reaction_routes(preview,
            **attempt)
    except ElementalPending as exc:
        raise world.reaction_pending_from_runtime(exc) from exc
    preview_generated = preview.drain_generated_events()
    if (previewed != reference_previewed
            or preview_generated != reference_preview_generated
            or world.target_commit_snapshot(preview)
            != world.target_commit_snapshot(reference_preview)
            or world.target_commit_snapshot(runtime)
            != target_state_before):
        raise world.reaction_pending(
            "world_stellar_swirl_target_preview_mismatch",
            "风旋冰附着的普通预演回执或完整后态与隔离参考执行不一致。",
            {"target_id": target_id,
             "event_id": target_event_id},
        )
    return OrdinaryStarPreview(runtime, target_event_id, target_state_before, target_identity_before,
                               base_attempt, reference_preview_receipt, previewed)

