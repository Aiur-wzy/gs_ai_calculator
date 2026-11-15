"""Prove target aura reads and cloud admissions on an isolated candidate."""

from __future__ import annotations

import copy

from genshin_engine.elemental import ElementalPending, ElementalRuntime
from genshin_engine.elemental_target_port import lunar_electro_aura_snapshot_at
from genshin_engine.elemental_world_lunar_planning_port import WorldLunarPlanningPort
from genshin_engine.elemental_world_lunar_plans import (
    LunarElectroTargetAdmission,
    LunarElectroWorldBatchPlan,
)
from genshin_engine.special_reaction_actual import SpecialReactionPending
from genshin_engine.special_reaction_reference import (
    admit_lunar_electro_tick as _REFERENCE_ADMIT_LUNAR_ELECTRO_TICK,
)
from genshin_engine.special_reaction_reference import (
    copy_runtime as _copy_special_runtime_for_reference,
)
from genshin_engine.special_reaction_reference import (
    finalize_world_lunar_electro_expiry as _REFERENCE_FINALIZE_WORLD_LUNAR_ELECTRO_EXPIRY,
)


def plan_lunar_electro_world_batch(world: WorldLunarPlanningPort, due, *, boundary_tick) -> LunarElectroWorldBatchPlan:
    ticks = [row for row in due
             if row.get("type") == "special_reaction_tick_request"]

    event_ids = [row.get("event_id") for row in ticks]
    if (any(not isinstance(value, str) or not value for value in event_ids)
            or len(event_ids) != len(set(event_ids))):
        raise world.reaction_pending(
            "world_lunar_electro_tick_invalid",
            "同批月感电跳数必须使用唯一非空事件ID。",
            {"event_ids": copy.deepcopy(event_ids)},
        )

    seen_target_areas: dict[tuple[int, str], str] = {}
    targets: dict[str, ElementalRuntime] = {}
    snapshots: dict[str, dict] = {}
    for tick in ticks:
        runtime = world.validate_lunar_electro_tick(
            tick,
            boundary_tick=boundary_tick,
            seen_target_areas=seen_target_areas,
        )
        targets[tick["event_id"]] = runtime
        target_state_before = world.target_commit_snapshot(runtime)
        reference_target = world.reference_target_copy(runtime)
        try:
            expected_snapshot = (
                lunar_electro_aura_snapshot_at(reference_target,
                    boundary_tick))
            snapshot = lunar_electro_aura_snapshot_at(runtime,
                boundary_tick)
        except ElementalPending as exc:
            raise world.reaction_pending_from_runtime(exc) from exc
        if (snapshot != expected_snapshot
                or world.target_commit_snapshot(reference_target)
                != target_state_before
                or world.target_commit_snapshot(runtime)
                != target_state_before):
            raise world.reaction_pending(
                "world_lunar_electro_target_snapshot_mismatch",
                "月感电实时元素快照或目标后态与隔离规范读取不一致。",
                {"event_id": tick["event_id"],
                 "target_id": tick["target_id"]},
            )
        snapshots[tick["event_id"]] = snapshot

    # The live special state has advanced to this boundary exactly once.
    # Admissions run only on a candidate until every target is known to be
    # committable, so one late pending child cannot leak last-attack state
    # or trace rows from an earlier child.
    candidate = copy.deepcopy(world.special_runtime)
    previews: dict[str, dict] = {}
    for tick in ticks:
        candidate_before = copy.deepcopy(candidate)
        reference_candidate = _copy_special_runtime_for_reference(
            candidate)
        try:
            expected_admitted = _REFERENCE_ADMIT_LUNAR_ELECTRO_TICK(
                reference_candidate,
                copy.deepcopy(tick),
                copy.deepcopy(snapshots[tick["event_id"]]),
            )
            admitted = candidate.admit_lunar_electro_tick_for_world(
                tick, snapshots[tick["event_id"]])
        except SpecialReactionPending as exc:
            raise world.reaction_pending_from_runtime(exc) from exc
        if (admitted != expected_admitted
                or world.special_commit_snapshot(candidate)
                != world.special_commit_snapshot(reference_candidate)):
            raise world.reaction_pending(
                "world_lunar_electro_admission_mismatch",
                "月感电候选接纳回执或完整后态与隔离规范重放不一致。",
                {"event_id": tick["event_id"],
                 "target_id": tick["target_id"],
                 "area_id": tick["area_id"]},
            )
        world.validate_lunar_electro_admission_contract(
            tick, admitted, snapshots[tick["event_id"]])
        admission = admitted.get("admission")
        expected_candidate = candidate_before
        expected_candidate.trace.append(copy.deepcopy(admission))
        damage = admitted.get("damage_event")
        if damage is not None:
            expected_candidate.trace.append(copy.deepcopy(damage))
        if admission["allowed"]:
            expected_candidate.lunar_cloud_by_area[tick["area_id"]][
                "lunar_electro_last_attack_by_target"
            ][tick["target_id"]] = tick["time_tick"]
        if (world.special_commit_snapshot(candidate)
                != world.special_commit_snapshot(expected_candidate)):
            raise world.reaction_pending(
                "world_lunar_electro_admission_mismatch",
                "月感电候选接纳改写了跳数轨迹与当前目标攻击记录以外的特殊状态。",
                {"event_id": tick["event_id"],
                 "target_id": tick["target_id"],
                 "area_id": tick["area_id"]},
            )
        if (not admission["allowed"]
                and admission.get("reason") == "per_target_attack_cooldown"):
            raise world.reaction_pending(
                "world_lunar_electro_schedule_conflict",
                "世界调度器生成了违反雷暴云目标冷却的重复跳数。",
                {"event_id": tick["event_id"],
                 "target_id": tick["target_id"],
                 "area_id": tick["area_id"]},
            )
        previews[tick["event_id"]] = admitted
    return LunarElectroWorldBatchPlan(candidate, tuple(
        LunarElectroTargetAdmission(tick, targets[tick["event_id"]], previews[tick["event_id"]])
        for tick in ticks))


def finalize_lunar_electro_candidate(world: WorldLunarPlanningPort, candidate, *, boundary_tick) -> list[dict]:
    reference_candidate = _copy_special_runtime_for_reference(candidate)
    try:
        expected_expiry_rows = (
            _REFERENCE_FINALIZE_WORLD_LUNAR_ELECTRO_EXPIRY(
                reference_candidate,
                boundary_tick,
            )
        )
        expiry_rows = candidate.finalize_world_lunar_electro_expiry(
            boundary_tick)
    except SpecialReactionPending as exc:
        raise world.reaction_pending_from_runtime(exc) from exc
    if (expiry_rows != expected_expiry_rows
            or world.special_commit_snapshot(candidate)
            != world.special_commit_snapshot(reference_candidate)):
        raise world.reaction_pending(
            "world_lunar_electro_tick_invalid",
            "月感电到期提交的事件或完整特殊状态与隔离规范重放不一致。",
            {
                "boundary_tick": boundary_tick,
                "expected_event_ids": [
                    row.get("event_id") for row in expected_expiry_rows
                ],
                "returned_event_ids": [
                    row.get("event_id")
                    for row in expiry_rows
                    if isinstance(row, dict)
                ] if isinstance(expiry_rows, list) else None,
            },
        )
    return expiry_rows

