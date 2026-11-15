"""Readonly lunar electro World contracts; no transaction authority."""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import TYPE_CHECKING

from genshin_engine.constants import LUNAR_FACTOR
from genshin_engine.elemental_world_reaction_port import WorldReactionReadPort
from genshin_engine.rotation_port import TICKS
from genshin_engine.special_reaction_port import (
    lunar_electro_tick_request,
    reaction_config,
    reaction_profile,
)

if TYPE_CHECKING:
    from genshin_engine.elemental import ElementalRuntime
    from genshin_engine.special_reaction_actual import SpecialReactionRuntime


def validate_lunar_electro_tick(
    world: WorldReactionReadPort,
    tick: dict,
    *,
    boundary_tick: int,
    seen_target_areas: dict[tuple[int, str], str],
) -> ElementalRuntime:
    if (not isinstance(tick, dict)
            or tick.get("type") != "special_reaction_tick_request"
            or tick.get("reaction_type") != "lunar_electro"
            or tick.get("status") != "ready_for_aura_check"
            or tick.get("target_selection_policy") != "bound_area_entity"
            or tick.get("time_tick") != boundary_tick
            or not isinstance(tick.get("event_id"), str)
            or not tick["event_id"]
            or not isinstance(tick.get("target_id"), str)
            or not tick["target_id"]
            or not isinstance(tick.get("area_id"), str)
            or not tick["area_id"]):
        raise world.reaction_pending(
            "world_lunar_electro_tick_invalid",
            "月感电世界跳数缺少可核验的时点、目标、区域或调度身份。",
            {"boundary_tick": boundary_tick,
             "tick": copy.deepcopy(tick)},
        )
    target_id = tick["target_id"]
    area_id = tick["area_id"]
    runtime = world.get_target(target_id)
    area_targets = world.targets_in_area(area_id)
    if target_id not in area_targets:
        raise world.reaction_pending(
            "world_lunar_electro_target_outside_area",
            "月感电跳数目标不属于其声明的静态区域。",
            {"target_id": target_id, "area_id": area_id,
             "area_target_ids": list(area_targets)},
        )
    target_key = (boundary_tick, target_id)
    previous_area_id = seen_target_areas.get(target_key)
    if previous_area_id is not None:
        raise world.reaction_pending(
            "world_lunar_electro_schedule_conflict",
            "同一目标同一时点收到多个雷暴云跳数；跨区域叠加顺序尚无受审规则。",
            {"target_id": target_id, "area_id": area_id,
             "previous_area_id": previous_area_id,
             "time_tick": boundary_tick},
        )
    seen_target_areas[target_key] = area_id
    if runtime.time != boundary_tick:
        raise world.reaction_pending(
            "world_clock_desynchronized",
            "月感电目标没有推进到本次世界边界。",
            {"target_id": target_id,
             "target_time_tick": runtime.time,
             "boundary_tick": boundary_tick},
        )
    return runtime


def derive_lunar_electro_schedule_contract(
    world: WorldReactionReadPort,
    *,
    boundary_tick: int,
    clouds_before: dict[str, dict],
    reference_runtime: SpecialReactionRuntime,
) -> tuple[dict[str, dict], dict[str, dict]]:
    """Derive every remaining cloud tick without trusting queue or trace.

    ``scheduled`` is only an execution cache and ``trace`` is only an
    audit log.  Either can lose the same row, so using one to validate the
    other cannot prove completeness.  The cloud phase, expiry and reviewed
    interval uniquely define all ticks after the current special clock.
    """
    if world.special_runtime is None:
        raise RuntimeError("月感电调度合同缺少世界级SpecialReactionRuntime。")
    config = reaction_config(reference_runtime, "lunar_electro")
    active_clouds = [
        area_id for area_id, cloud in clouds_before.items()
        if isinstance(cloud, dict)
        and cloud.get("lunar_electro_active") is True
    ]
    if config is None:
        if active_clouds:
            raise world.reaction_pending(
                "world_lunar_electro_schedule_conflict",
                "未启用月感电机制却存在活动雷暴云。",
                {"boundary_tick": boundary_tick,
                 "area_ids": sorted(active_clouds)},
            )
        return {}, {}
    interval_decimal = (
        Decimal(str(config["mechanics"]["tick_interval_seconds"]))
        * TICKS
    )
    interval_tick = int(interval_decimal)
    if interval_decimal != interval_tick or interval_tick <= 0:
        raise world.reaction_pending(
            "world_lunar_electro_schedule_conflict",
            "月感电跳数间隔无法表示为正整数微秒。",
            {"boundary_tick": boundary_tick,
             "tick_interval_seconds": config["mechanics"].get(
                 "tick_interval_seconds")},
        )

    current_tick = world.special_runtime.time
    expected_schedule: dict[str, dict] = {}
    expected_due: dict[str, dict] = {}
    cloud_keys = {
        "lunar_electro_active",
        "lunar_electro_expires_at",
        "lunar_electro_target_ids",
        "lunar_electro_last_attack_by_target",
        "lunar_electro_phase_by_target",
    }
    phase_keys = {
        "first_attack_at", "next_tick_index", "owner",
        "parent_reaction_id",
    }
    for area_id in sorted(clouds_before):
        cloud = clouds_before[area_id]
        if type(cloud) is not dict or set(cloud) != cloud_keys:
            raise world.reaction_pending(
                "world_lunar_electro_schedule_conflict",
                "月感电雷暴云状态不是可核验的有限结构。",
                {"boundary_tick": boundary_tick, "area_id": area_id,
                 "cloud": copy.deepcopy(cloud)},
            )
        if cloud["lunar_electro_active"] is False:
            if cloud != {
                "lunar_electro_active": False,
                "lunar_electro_expires_at": None,
                "lunar_electro_target_ids": [],
                "lunar_electro_last_attack_by_target": {},
                "lunar_electro_phase_by_target": {},
            }:
                raise world.reaction_pending(
                    "world_lunar_electro_schedule_conflict",
                    "非活动雷暴云仍携带调度相位或攻击记录。",
                    {"boundary_tick": boundary_tick, "area_id": area_id,
                     "cloud": copy.deepcopy(cloud)},
                )
            continue
        expires = cloud["lunar_electro_expires_at"]
        targets = cloud["lunar_electro_target_ids"]
        phases = cloud["lunar_electro_phase_by_target"]
        last_attacks = cloud["lunar_electro_last_attack_by_target"]
        if (not isinstance(targets, list)
                or type(phases) is not dict
                or set(phases) != set(targets)):
            raise world.reaction_pending(
                "lunar_electro_phase_contract_invalid",
                "月感电雷暴云目标与相位绑定无法完整推导跳数。",
                {"boundary_tick": boundary_tick, "area_id": area_id,
                 "target_ids": copy.deepcopy(targets),
                 "phase_target_ids": (
                     sorted(phases) if isinstance(phases, dict) else None)},
            )
        valid_cloud = (
            cloud["lunar_electro_active"] is True
            and type(expires) is int
            and expires > current_tick
            and isinstance(targets, list) and bool(targets)
            and all(isinstance(value, str) and value for value in targets)
            and len(targets) == len(set(targets))
            and type(last_attacks) is dict
            and set(last_attacks) <= set(targets)
            and all(type(value) is int and value <= current_tick
                    for value in last_attacks.values())
        )
        if not valid_cloud:
            raise world.reaction_pending(
                "world_lunar_electro_schedule_conflict",
                "活动雷暴云的到期、目标、相位或攻击记录无法完整推导调度。",
                {"boundary_tick": boundary_tick, "area_id": area_id,
                 "cloud": copy.deepcopy(cloud)},
            )
        for target_id in targets:
            phase = phases[target_id]
            valid_phase = (
                type(phase) is dict and set(phase) == phase_keys
                and type(phase.get("first_attack_at")) is int
                and type(phase.get("next_tick_index")) is int
                and phase["next_tick_index"] >= 1
                and isinstance(phase.get("owner"), str)
                and bool(phase["owner"])
                and isinstance(phase.get("parent_reaction_id"), str)
                and bool(phase["parent_reaction_id"])
            )
            if not valid_phase:
                raise world.reaction_pending(
                    "lunar_electro_phase_contract_invalid",
                    "月感电目标相位缺少可推导的首跳、索引或来源。",
                    {"boundary_tick": boundary_tick, "area_id": area_id,
                     "target_id": target_id,
                     "phase": copy.deepcopy(phase)},
                )
            first_tick = phase["first_attack_at"]
            final_index = (
                0 if first_tick > expires
                else (expires-first_tick)//interval_tick + 1
            )
            if (phase["next_tick_index"] != final_index+1
                    or final_index > 1_000_000):
                raise world.reaction_pending(
                    "lunar_electro_phase_contract_invalid",
                    "月感电相位索引与到期前应排完的跳数不一致。",
                    {"boundary_tick": boundary_tick, "area_id": area_id,
                     "target_id": target_id,
                     "expected_next_tick_index": final_index+1,
                     "actual_next_tick_index": phase["next_tick_index"]},
                )
            for tick_index in range(1, final_index+1):
                tick_at = first_tick+(tick_index-1)*interval_tick
                if tick_at <= current_tick:
                    continue
                row = lunar_electro_tick_request(reference_runtime,
                    area_id=area_id,
                    target_id=target_id,
                    phase=phase,
                    tick_index=tick_index,
                    time_tick=tick_at,
                )
                row.pop("schedule_key", None)
                event_id = row["event_id"]
                if event_id in expected_schedule:
                    raise world.reaction_pending(
                        "world_lunar_electro_schedule_conflict",
                        "从雷暴云相位推导出的月感电事件ID发生冲突。",
                        {"boundary_tick": boundary_tick,
                         "event_id": event_id},
                    )
                expected_schedule[event_id] = row
                if tick_at <= boundary_tick:
                    expected_due[event_id] = row
    return expected_schedule, expected_due


def validate_lunar_electro_due_contract(
    world: WorldReactionReadPort,
    event: dict,
    *,
    boundary_tick: int,
    clouds_before: dict[str, dict],
) -> None:
    """Admit only the two Lunar-Charged deadline event shapes we consume."""
    if world.special_runtime is None:
        raise RuntimeError("月感电截止事件缺少世界级SpecialReactionRuntime。")
    invalid = False
    event_type = event.get("type") if isinstance(event, dict) else None
    area_id = event.get("area_id") if isinstance(event, dict) else None
    expected_profile = reaction_profile(world.special_runtime, "lunar_electro")
    common_valid = (
        isinstance(event, dict)
        and event.get("reaction_type") == "lunar_electro"
        and event.get("time_tick") == boundary_tick
        and event.get("t") == boundary_tick / TICKS
        and isinstance(event.get("event_id"), str)
        and bool(event["event_id"])
        and isinstance(area_id, str)
        and bool(area_id)
        and event.get("profile") == expected_profile
    )
    if not common_valid:
        invalid = True
    elif event_type == "special_reaction_tick_request":
        config = reaction_config(world.special_runtime, "lunar_electro")
        mechanics = config["mechanics"]
        interval_decimal = (
            Decimal(str(mechanics["tick_interval_seconds"])) * TICKS)
        interval_tick = int(interval_decimal)
        expected_requirements = {
            "coexisting_elements": ["hydro", "electro"],
            "consume_each_gu": mechanics["aura_consumption_gu"],
            "per_target_attack_cooldown_seconds": mechanics[
                "tick_interval_seconds"],
            "owner_residual_contract": (
                "same-element-owner-residual-max-v1"),
            "contributor_application_window_seconds": 5,
        }
        target_id = event.get("target_id")
        cloud = world.special_runtime.lunar_cloud_by_area.get(area_id, {})
        phase = cloud.get("lunar_electro_phase_by_target", {}).get(
            target_id)
        if not isinstance(phase, dict):
            phase = clouds_before.get(area_id, {}).get(
                "lunar_electro_phase_by_target", {}).get(target_id)
        tick_index = event.get("tick_index")
        phase_valid = False
        if isinstance(phase, dict):
            expected_id = (
                f"{area_id}:lunar-electro:target:{target_id}:"
                f"phase:{phase.get('first_attack_at')}:tick:{tick_index}"
            )
            phase_valid = (
                type(tick_index) is int
                and tick_index > 0
                and event.get("owner") == phase.get("owner")
                and event.get("parent_reaction_id")
                == phase.get("parent_reaction_id")
                and event.get("event_id") == expected_id
                and interval_decimal == interval_tick
                and boundary_tick == phase.get("first_attack_at")
                + (tick_index-1)*interval_tick
            )
        invalid = not (
            event.get("status") == "ready_for_aura_check"
            and event.get("target_selection_policy")
            == "bound_area_entity"
            and event.get("component") == "thundercloud"
            and event.get("contributor_ids") == []
            and event.get("contributor_sample_policy") == (
                "tick_time_live_positive_owner_residual_and_recent_five_seconds")
            and event.get("requirements") == expected_requirements
            and type(tick_index) is int
            and tick_index > 0
            and isinstance(event.get("owner"), str)
            and bool(event["owner"])
            and isinstance(event.get("parent_reaction_id"), str)
            and bool(event["parent_reaction_id"])
            and phase_valid
        )
    elif event_type == "special_reaction_state":
        previous_targets = event.get("previous_target_ids")
        previous_cloud = clouds_before.get(area_id)
        invalid = not (
            event.get("operation") == "expire_area_thundercloud"
            and event.get("status") == "executed"
            and event.get("target_id") is None
            and event.get("event_id")
            == f"{area_id}:lunar-electro:expire:{boundary_tick}"
            and isinstance(previous_targets, list)
            and all(isinstance(value, str) and value
                    for value in previous_targets)
            and len(previous_targets) == len(set(previous_targets))
            and isinstance(previous_cloud, dict)
            and previous_cloud.get("lunar_electro_active") is True
            and previous_cloud.get("lunar_electro_expires_at")
            == boundary_tick
            and previous_targets
            == previous_cloud.get("lunar_electro_target_ids")
        )
    else:
        invalid = True
    if invalid:
        raise world.reaction_pending(
            "world_lunar_electro_tick_invalid",
            "月感电截止事件与当前受审雷暴云合同不一致。",
            {"boundary_tick": boundary_tick,
             "event": copy.deepcopy(event)},
        )


def validate_lunar_electro_admission_contract(
    world: WorldReactionReadPort,
    tick: dict,
    admitted: dict,
    aura_snapshot: dict,
) -> None:
    """Bind one preview receipt to its exact tick and reviewed mechanics."""
    if world.special_runtime is None:
        raise RuntimeError("月感电接纳回执缺少世界级SpecialReactionRuntime。")
    config = reaction_config(world.special_runtime, "lunar_electro")
    if config is None:
        raise RuntimeError("月感电接纳回执缺少已启用机制档案。")
    profile = reaction_profile(world.special_runtime, "lunar_electro")
    consume = config["mechanics"]["aura_consumption_gu"]
    sample_policy = (
        "tick_time_live_positive_owner_residual_and_recent_five_seconds"
    )
    if (not isinstance(admitted, dict)
            or set(admitted) != {
                "admission", "aura_before", "aura_after", "damage_event",
            }
            or admitted.get("aura_before") != aura_snapshot):
        raise world.reaction_pending(
            "world_lunar_electro_admission_invalid",
            "月感电预演没有完整绑定实时元素快照与规范回执结构。",
            {"event_id": tick.get("event_id")},
        )
    admission = admitted.get("admission")
    common = {
        "type": "special_reaction_tick_admission",
        "event_id": tick["event_id"] + ":admission",
        "t": tick["t"],
        "time_tick": tick["time_tick"],
        "target_id": tick["target_id"],
        "area_id": tick["area_id"],
        "reaction_type": "lunar_electro",
        "status": "executed",
        "profile": profile,
    }
    common_valid = (
        isinstance(admission, dict)
        and all(admission.get(key) == value
                for key, value in common.items())
        and type(admission.get("allowed")) is bool
    )
    damage = admitted.get("damage_event")
    if not common_valid:
        invalid = True
    elif admission["allowed"]:
        allowed_admission_keys = set(common) | {
            "allowed", "reason", "consumed_gu", "contributor_ids",
            "consumed_contributor_ids", "owner_residual_contract",
            "contributor_allocations_before",
            "contributor_allocations_after",
            "excluded_stale_contributor_ids",
        }
        damage_keys = {
            "type", "event_id", "t", "time_tick", "target_id",
            "area_id", "target_selection_policy", "owner",
            "parent_reaction_id", "reaction_type", "damage_system",
            "reaction_base", "system_factor", "application",
            "component", "contributor_ids",
            "contributor_sample_policy", "profile", "status",
        }
        contributors = admission.get("contributor_ids")
        contributor_valid = (
            isinstance(contributors, list)
            and 1 <= len(contributors) <= 4
            and all(isinstance(value, str) and value
                    for value in contributors)
            and len(contributors) == len(set(contributors))
            and admission.get("consumed_contributor_ids") == contributors
        )
        damage_valid = (
            isinstance(damage, dict)
            and set(damage) == damage_keys
            and damage.get("type") == "special_reaction_damage_request"
            and damage.get("event_id") == tick["event_id"] + ":damage"
            and damage.get("t") == tick["t"]
            and damage.get("time_tick") == tick["time_tick"]
            and damage.get("target_id") == tick["target_id"]
            and damage.get("area_id") == tick["area_id"]
            and damage.get("target_selection_policy") == "declared_target"
            and damage.get("owner") == tick["owner"]
            and damage.get("parent_reaction_id")
            == tick["parent_reaction_id"]
            and damage.get("reaction_type") == "lunar_electro"
            and damage.get("damage_system") == "lunar"
            and damage.get("reaction_base") is True
            and damage.get("system_factor")
            == LUNAR_FACTOR["lunar_electro"]
            and damage.get("application") is None
            and damage.get("component") == "thundercloud"
            and damage.get("contributor_ids") == contributors
            and damage.get("contributor_sample_policy") == sample_policy
            and damage.get("profile") == profile
            and damage.get("status") == "ready_for_stat_sample"
        )
        invalid = not (
            set(admission) == allowed_admission_keys
            and admission.get("reason")
            == "live_hydro_electro_coexistence"
            and admission.get("consumed_gu") == {
                "hydro": consume, "electro": consume,
            }
            and contributor_valid
            and admission.get("owner_residual_contract")
            == "same-element-owner-residual-max-v1"
            and isinstance(
                admission.get("contributor_allocations_before"), dict)
            and set(admission["contributor_allocations_before"])
            == {"hydro", "electro"}
            and isinstance(
                admission.get("contributor_allocations_after"), dict)
            and set(admission["contributor_allocations_after"])
            == {"hydro", "electro"}
            and admission.get("excluded_stale_contributor_ids") == []
            and damage_valid
        )
    else:
        denied_admission_keys = set(common) | {
            "allowed", "reason", "consumed_gu",
        }
        invalid = not (
            set(admission) == denied_admission_keys
            and admission.get("reason")
            == "hydro_electro_coexistence_or_gauge_insufficient"
            and admission.get("consumed_gu") == {
                "hydro": 0, "electro": 0,
            }
            and damage is None
            and admitted.get("aura_after") == aura_snapshot
        )
    if invalid:
        raise world.reaction_pending(
            "world_lunar_electro_admission_invalid",
            "月感电接纳或伤害回执没有完整绑定跳数身份、机制与固定消费。",
            {"event_id": tick.get("event_id"),
             "admitted": copy.deepcopy(admitted)},
        )

