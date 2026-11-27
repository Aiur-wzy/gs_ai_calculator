"""Readonly stellar swirl World contracts; no transaction authority."""

from __future__ import annotations

import copy

from genshin_engine.constants import STELLAR_FACTOR
from genshin_engine.elemental_world_reaction_port import WorldReactionReadPort
from genshin_engine.rotation_port import TICKS
from genshin_engine.special_reaction_actual import SpecialReactionPending
from genshin_engine.special_reaction_contracts import stellar_swirl_coefficient
from genshin_engine.special_reaction_port import reaction_config, reaction_profile
from genshin_engine.special_reaction_reference import (
    copy_runtime as _copy_special_runtime_for_reference,
)
from genshin_engine.special_reaction_reference import (
    stellar_swirl_detonation as _REFERENCE_STELLAR_SWIRL_DETONATION,
)


def validate_stellar_swirl_passthrough(
    world: WorldReactionReadPort,
    event: dict,
    *,
    time_tick: int,
    source: str,
) -> None:
    """Validate a Star-Swirl row that is not an area application pair."""
    if world.special_runtime is None:
        raise RuntimeError("星扩散世界批次缺少SpecialReactionRuntime。")
    profile = reaction_profile(world.special_runtime, "stellar_swirl")
    if (event.get("time_tick") != time_tick
            or event.get("t") != time_tick / TICKS
            or event.get("profile") != profile):
        raise world.reaction_pending(
            "world_stellar_swirl_event_invalid",
            "星扩散派生事件的时点或受审档案不一致。",
            {"event": copy.deepcopy(event), "source": source},
        )
    event_type = event.get("type")
    if event_type == "special_reaction_damage_request":
        target_id = event.get("target_id")
        area_id = event.get("area_id")
        reaction = "stellar_swirl_anemo"
        base_factor = STELLAR_FACTOR[reaction]
        adjustment = reaction_config(world.special_runtime, "stellar_swirl")["mechanics"].get("base_factor_adjustment")
        valid = (
            source == "reaction_packet"
            and event.get("reaction_type") == reaction
            and isinstance(target_id, str)
            and bool(target_id)
            and isinstance(area_id, str)
            and bool(area_id)
            and event.get("target_selection_policy") == "declared_target"
            and event.get("status") == "ready_for_stat_sample"
            and event.get("damage_system") == "stellar"
            and event.get("reaction_base") is True
            and event.get("application") is None
            and event.get("component") == "immediate_anemo"
            and event.get("system_factor") == base_factor
            and event.get("reaction_coefficient") == stellar_swirl_coefficient(adjustment, reaction, base_factor)
        )
        if valid:
            valid = target_id in world.targets_in_area(area_id)
    elif event_type == "special_reaction_state":
        allowed_operations = {
            "detonate_vortex",
        }
        if source == "reaction_packet":
            allowed_operations.update({
                "create_vortex_from_packet",
                "advance_existing_vortex_from_packet_child",
                "remaining_packet_children_do_not_reseed",
            })
        valid = (
            event.get("reaction_type") == "stellar_swirl"
            and event.get("operation") in allowed_operations
            and event.get("status") == "executed"
            and event.get("target_id") is None
            and isinstance(event.get("area_id"), str)
            and bool(event["area_id"])
        )
        if valid:
            world.targets_in_area(event["area_id"])
    else:
        valid = False
    if not valid:
        raise world.reaction_pending(
            "world_stellar_swirl_event_invalid",
            "星扩散世界批次包含本入口不能证明的派生事件。",
            {"event": copy.deepcopy(event), "source": source},
        )


def stellar_swirl_pairs(
    world: WorldReactionReadPort,
    events: list[dict],
    *,
    time_tick: int,
    source: str,
) -> tuple[list[dict], set[str]]:
    """Validate and pair every area damage with its after-damage 1U Cryo."""
    if world.special_runtime is None:
        raise RuntimeError("星扩散世界批次缺少SpecialReactionRuntime。")
    profile = reaction_profile(world.special_runtime, "stellar_swirl")
    mechanics = reaction_config(world.special_runtime, "stellar_swirl")["mechanics"]
    early_threshold = mechanics["early_detonation_threshold"]
    damages = [row for row in events
               if row.get("type") == "special_reaction_damage_request"
               and row.get("reaction_type") in {
                   "stellar_swirl_cryo_lv2",
                   "stellar_swirl_cryo_lv3",
               }]
    applications = [row for row in events
                    if row.get("type")
                    == "special_reaction_application_request"]
    pairs: list[dict] = []
    consumed_ids: set[str] = set()
    for damage in damages:
        damage_id = damage.get("event_id")
        matches = [row for row in applications
                   if row.get("parent_damage_event_id") == damage_id]
        if len(matches) != 1:
            raise world.reaction_pending(
                "world_stellar_swirl_pair_invalid",
                "风旋区域冰伤必须恰好配对一个伤害后1U冰附着请求。",
                {"damage_event_id": damage_id,
                 "application_event_ids": [row.get("event_id")
                                           for row in matches]},
            )
        application = matches[0]
        reaction_type = damage.get("reaction_type")
        factor = 2 if reaction_type.endswith("lv2") else 3
        vortex_level = factor - 1
        area_id = damage.get("area_id")
        parent = damage.get("parent_reaction_id")
        states = [row for row in events
                  if row.get("type") == "special_reaction_state"
                  and row.get("operation") == "detonate_vortex"
                  and row.get("event_id") == f"{parent}:detonation"]
        state = states[0] if len(states) == 1 else None
        expected_reason = (
            "sixth_reaction_early"
            if source == "reaction_packet"
            else "three_second_timeout"
        )
        contributors = damage.get("contributor_ids")
        roles = damage.get("role_provenance")
        role_lists_valid = (
            isinstance(roles, dict)
            and set(roles) == {
                "contract", "component_policy", "participant_ids",
                "anemo_trigger_ids", "cryo_applier_ids", "status",
            }
            and roles.get("contract")
            == "stellar-swirl-role-provenance-v1"
            and roles.get("component_policy")
            == "vortex_cryo_best_then_anemo_best"
            and roles.get("status") == "ready"
            and isinstance(contributors, list)
            and 1 <= len(contributors) <= 4
            and len(contributors) == len(set(contributors))
            and all(isinstance(value, str) and value
                    for value in contributors)
            and roles.get("participant_ids") == contributors
            and all(
                isinstance(roles.get(name), list)
                and bool(roles[name])
                and len(roles[name]) == len(set(roles[name]))
                and all(isinstance(value, str) and value
                        for value in roles[name])
                and set(roles[name]) <= set(contributors)
                for name in ("anemo_trigger_ids", "cryo_applier_ids")
            )
            and not (set(roles["anemo_trigger_ids"])
                     & set(roles["cryo_applier_ids"]))
        )
        state_count = state.get("count") if state is not None else None
        count_valid = (
            type(state_count) is int
            and (state_count == early_threshold
                 if source == "reaction_packet"
                 else 1 <= state_count < early_threshold)
        )
        valid = (
            isinstance(damage_id, str)
            and bool(damage_id)
            and isinstance(parent, str)
            and bool(parent)
            and damage_id
            == f"{parent}:detonation:cryo:lv{factor}"
            and damage.get("time_tick") == time_tick
            and damage.get("t") == time_tick / TICKS
            and damage.get("target_id") is None
            and isinstance(area_id, str)
            and bool(area_id)
            and damage.get("target_selection_policy")
            == "event_time_entities_in_area"
            and damage.get("status") == "ready_for_stat_sample"
            and damage.get("damage_system") == "stellar"
            and damage.get("reaction_base") is True
            and damage.get("system_factor") == factor
            and damage.get("reaction_coefficient") == stellar_swirl_coefficient(
                mechanics.get("base_factor_adjustment"), reaction_type, factor)
            and isinstance(damage.get("owner"), str)
            and bool(damage["owner"])
            and damage.get("application") is None
            and damage.get("component")
            == f"cryo_vortex_level_{vortex_level}_detonation"
            and damage.get("vortex_level") == vortex_level
            and damage.get("base_factor") == factor
            and damage.get("profile") == profile
            and damage.get("contributor_sample_policy")
            == "event_time_declared_contributors"
            and role_lists_valid
            and application.get("event_id") == damage_id + ":application"
            and application.get("time_tick") == time_tick
            and application.get("t") == time_tick / TICKS
            and application.get("target_id") is None
            and application.get("area_id") == area_id
            and application.get("target_selection_policy")
            == "event_time_entities_in_area"
            and application.get("reaction_type") == "stellar_swirl"
            and application.get("element") == "cryo"
            and application.get("gauge_units") == 1
            and application.get("application_order") == "after_damage"
            and application.get("icd") == {"kind": "none"}
            and application.get("direct_hit") is False
            and application.get("counts_toward_stellar_superconduct")
            is True
            and application.get("profile") == profile
            and application.get("status")
            == "ready_for_elemental_runtime"
            and isinstance(application.get("owner"), str)
            and bool(application["owner"])
            and application.get("owner") == damage.get("owner")
            and isinstance(application.get("action_id"), str)
            and bool(application["action_id"])
            and application.get("contributor_ids") == contributors
            and application.get("source") == mechanics["source"]
            and state is not None
            and state.get("time_tick") == time_tick
            and state.get("t") == time_tick / TICKS
            and state.get("target_id") is None
            and state.get("area_id") == area_id
            and state.get("reaction_type") == "stellar_swirl"
            and state.get("reason") == expected_reason
            and count_valid
            and state.get("vortex_level") == vortex_level
            and state.get("ice_factor") == factor
            and state.get("profile") == profile
            and state.get("status") == "executed"
        )
        if not valid:
            raise world.reaction_pending(
                "world_stellar_swirl_pair_invalid",
                "风旋区域冰伤、附着与爆发状态不符合受审配对合同。",
                {"damage": copy.deepcopy(damage),
                 "application": copy.deepcopy(application),
                 "state": copy.deepcopy(state),
                 "source": source},
            )
        target_ids = world.targets_in_area(area_id)
        pairs.append({
            "damage": damage,
            "application": application,
            "state": state,
            "target_ids": target_ids,
        })
        consumed_ids.update((damage_id, application["event_id"]))

    detonation_states = [
        row for row in events
        if row.get("type") == "special_reaction_state"
        and row.get("operation") == "detonate_vortex"
    ]
    expected_state_ids = {pair["state"]["event_id"] for pair in pairs}
    actual_state_ids = [row.get("event_id") for row in detonation_states]
    if (len(actual_state_ids) != len(set(actual_state_ids))
            or set(actual_state_ids) != expected_state_ids):
        raise world.reaction_pending(
            "world_stellar_swirl_pair_invalid",
            "风旋爆发状态行必须与区域冰伤和附着配对一一对应。",
            {"expected_state_event_ids": sorted(expected_state_ids),
             "actual_state_event_ids": copy.deepcopy(actual_state_ids)},
        )

    orphan_applications = [row.get("event_id") for row in applications
                           if row.get("event_id") not in consumed_ids]
    if orphan_applications:
        raise world.reaction_pending(
            "world_stellar_swirl_pair_invalid",
            "风旋世界批次含有无法配对的区域附着请求。",
            {"application_event_ids": orphan_applications},
        )
    pairs.sort(key=lambda item: (
        item["damage"]["area_id"], item["damage"]["event_id"]))
    return pairs, consumed_ids


def freeze_stellar_swirl_deadlines(
    world: WorldReactionReadPort,
    time_tick: int,
) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    """Derive the complete due-vortex contract from the pre-advance state.

    The deadline producer clears a vortex while emitting its state, damage
    and after-damage application rows.  A consumer must therefore not infer
    completeness from whatever rows happened to be returned: dropping all
    three rows would otherwise be an empty set that validates vacuously.
    This method freezes the authoritative pre-state and derives both the
    exact rows and the only accepted post-state before the producer runs.
    """
    if world.special_runtime is None:
        return {}, {}, {}
    vortices_before = copy.deepcopy(world.special_runtime.vortex_by_area)
    reference_runtime = _copy_special_runtime_for_reference(
        world.special_runtime)
    expected_after = copy.deepcopy(vortices_before)
    expected_rows: dict[str, dict] = {}
    for area_id in sorted(vortices_before):
        state = vortices_before[area_id]
        if not isinstance(state, dict):
            raise world.reaction_pending(
                "world_stellar_swirl_deadline_invalid",
                "风旋截止状态不是可核验的对象。",
                {"boundary_tick": time_tick, "area_id": area_id,
                 "state": copy.deepcopy(state)},
            )
        detonate_at = state.get("stellar_swirl_detonate_at")
        if detonate_at is None:
            continue
        if type(detonate_at) is not int:
            raise world.reaction_pending(
                "world_stellar_swirl_deadline_invalid",
                "风旋爆发时点不是可核验的整数微秒。",
                {"boundary_tick": time_tick, "area_id": area_id,
                 "detonate_at": copy.deepcopy(detonate_at)},
            )
        if detonate_at > time_tick:
            continue
        frozen = copy.deepcopy(state)
        try:
            rows = _REFERENCE_STELLAR_SWIRL_DETONATION(
                reference_runtime,
                area_id,
                frozen,
                detonate_at,
                "three_second_timeout",
            )
        except SpecialReactionPending as exc:
            raise world.reaction_pending_from_runtime(exc) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise world.reaction_pending(
                "world_stellar_swirl_deadline_invalid",
                "推进前风旋状态无法推导出完整爆发合同。",
                {"boundary_tick": time_tick, "area_id": area_id,
                 "state": copy.deepcopy(state)},
            ) from exc
        if (not isinstance(rows, list) or len(rows) != 3
                or any(not isinstance(row, dict) for row in rows)):
            raise world.reaction_pending(
                "world_stellar_swirl_deadline_invalid",
                "推进前风旋状态没有推导出状态、伤害和附着三元组。",
                {"boundary_tick": time_tick, "area_id": area_id},
            )
        for row in rows:
            event_id = row.get("event_id")
            if (not isinstance(event_id, str) or not event_id
                    or event_id in expected_rows):
                raise world.reaction_pending(
                    "world_stellar_swirl_deadline_invalid",
                    "推进前推导的风旋爆发事件ID无效或冲突。",
                    {"boundary_tick": time_tick, "area_id": area_id,
                     "event_id": event_id},
                )
            expected_rows[event_id] = copy.deepcopy(row)
        expected_after[area_id] = frozen
    return vortices_before, expected_after, expected_rows

