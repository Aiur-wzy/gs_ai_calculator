"""Readonly Lunar-Charged admission and owner-residual computation."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replacement_contracts import replacement_state_fingerprint


@dataclass(frozen=True, slots=True)
class LunarTickAdmissionProposal:
    cloud: Mapping[str, Any]
    tick: Mapping[str, Any]
    aura_state: Mapping[str, Any]
    party_member_ids: tuple[str, ...] | None
    area_id: str
    event_id: str
    seconds_to_ticks: Callable[..., int]
    finite_number: Callable[..., bool]
    pending_error: Callable[..., Exception]


@dataclass(frozen=True, slots=True)
class LunarTickAdmissionPlan:
    expected_cloud_fingerprint: str
    attack_at: int | None
    admission: dict[str, Any]
    aura_before: dict[str, Any]
    aura_after: dict[str, Any]


def plan_lunar_tick_admission(proposal: LunarTickAdmissionProposal) -> LunarTickAdmissionPlan:
    cloud, tick = proposal.cloud, proposal.tick
    aura_state = dict(proposal.aura_state)
    area_id, event_id = proposal.area_id, proposal.event_id
    basis = replacement_state_fingerprint(cloud)
    before = copy.deepcopy(aura_state)
    consume = tick["requirements"]["consume_each_gu"]
    cooldown = proposal.seconds_to_ticks(
        tick["requirements"]["per_target_attack_cooldown_seconds"],
        "per_target_attack_cooldown_seconds")
    last_attack = cloud["lunar_electro_last_attack_by_target"].get(
        tick["target_id"])
    if (last_attack is not None
            and tick["time_tick"] < last_attack+cooldown):
        row = {
            "type": "special_reaction_tick_admission",
            "event_id": event_id+":admission", "t": tick["t"],
            "time_tick": tick["time_tick"], "target_id": tick["target_id"],
            "area_id": area_id, "reaction_type": "lunar_electro",
            "allowed": False, "reason": "per_target_attack_cooldown",
            "last_attack_at": last_attack/TICKS,
            "next_attack_at": (last_attack+cooldown)/TICKS,
            "consumed_gu": {"hydro": 0, "electro": 0},
            "status": "executed", "profile": None,
        }
        return LunarTickAdmissionPlan(basis, None, row, before, copy.deepcopy(aura_state))
    if aura_state.get("owner_residual_contract") != "same-element-owner-residual-max-v1":
        raise proposal.pending_error(
            "lunar_electro_owner_residual_unproven",
            "月感电贡献者必须来自按元素与提供者维护的实时剩余元素量账。",
            event_id,
            {"owner_residual_contract": aura_state.get("owner_residual_contract")},
        )
    if aura_state.get("sample_time_tick") != tick.get("time_tick"):
        raise ValueError("月感电元素残量快照必须在当前跳数时点采样。")
    components = aura_state.get("components")
    if not isinstance(components, dict):
        raise ValueError("aura_state.components必须提供水雷分层状态。")  # noqa: TRY004
    eligible = True
    contributor_ids: list[str] = []
    allocations_before: dict[str, list[dict]] = {}
    stale_contributor_ids: list[str] = []
    sample_seconds = tick["time_tick"]/TICKS
    for element in ("hydro", "electro"):
        component = components.get(element)
        if not isinstance(component, dict) or not proposal.finite_number(component.get("remaining_gu"), minimum=0):
            eligible = False
            continue
        if component.get("owner_residual_status") != "tracked":
            raise proposal.pending_error(
                "lunar_electro_owner_residual_unproven",
                "月感电水雷分层缺少可审计的提供者实时残量账。",
                event_id, {"element": element,
                           "owner_residual_status": component.get(
                               "owner_residual_status")})
        allocations = component.get("contributor_allocations")
        if not isinstance(allocations, list):
            raise ValueError("水雷分层的contributor_allocations必须是数组。")  # noqa: TRY004
        owners: set[str] = set()
        positive_owners: list[str] = []
        for allocation in allocations:
            if not isinstance(allocation, dict):
                raise ValueError("水雷提供者残量必须是对象。")  # noqa: TRY004
            owner = allocation.get("owner")
            remaining = allocation.get("remaining_gu")
            last_application_at = allocation.get("last_application_at")
            if not isinstance(owner, str) or not owner or owner in owners:
                raise ValueError("同一元素的提供者残量必须使用唯一非空角色ID。")
            if not proposal.finite_number(remaining, minimum=0):
                raise ValueError("提供者remaining_gu必须是有限非负数。")
            if not proposal.finite_number(last_application_at, minimum=0):
                raise ValueError("提供者last_application_at必须是有限非负秒数。")
            if last_application_at > sample_seconds+1e-12:
                raise ValueError("提供者last_application_at不能晚于当前月感电跳数。")
            if not isinstance(allocation.get("source"), str) or not allocation["source"]:
                raise ValueError("提供者残量必须保留非空来源。")
            owners.add(owner)
            if remaining > 1e-12:
                positive_owners.append(owner)
                if sample_seconds-last_application_at <= 5+1e-12:
                    contributor_ids.append(owner)
                else:
                    stale_contributor_ids.append(owner)
        apparent = max(
            (allocation["remaining_gu"] for allocation in allocations),
            default=0.0,
        )
        if abs(apparent-component["remaining_gu"]) > 1e-9:
            raise ValueError("元素表观GU必须等于同元素各提供者实时残量的最大值。")
        ids = component.get("contributor_ids", [])
        if ids != positive_owners:
            raise ValueError("contributor_ids必须与扣除前正残量提供者一致。")
        allocations_before[element] = copy.deepcopy(allocations)
        if component["remaining_gu"] + 1e-12 < consume:
            eligible = False
    if stale_contributor_ids:
        raise proposal.pending_error(
            "lunar_electro_contributor_recency_conflict",
            "月感电存在仍有正残量但最近一次对应元素附着早于5秒的提供者；"
            "公开资料对其参与资格存在冲突，本跳拒绝猜测。",
            event_id,
            {"excluded_stale_contributor_ids": list(dict.fromkeys(
                stale_contributor_ids)),
             "owner_residual_contract": "same-element-owner-residual-max-v1",
             "contributor_application_window_seconds": 5},
        )
    if not eligible:
        row = {
            "type": "special_reaction_tick_admission", "event_id": event_id+":admission",
            "t": tick["t"], "time_tick": tick["time_tick"],
            "target_id": tick["target_id"], "area_id": area_id,
            "reaction_type": "lunar_electro",
            "allowed": False, "reason": "hydro_electro_coexistence_or_gauge_insufficient",
            "consumed_gu": {"hydro": 0, "electro": 0}, "status": "executed",
            "profile": None,
        }
        return LunarTickAdmissionPlan(basis, None, row, before, copy.deepcopy(aura_state))
    after = copy.deepcopy(aura_state)
    allocations_after: dict[str, list[dict]] = {}
    for element in ("hydro", "electro"):
        reduced = []
        for allocation in after["components"][element]["contributor_allocations"]:
            allocation["remaining_gu"] = max(
                0.0, allocation["remaining_gu"]-consume)
            if allocation["remaining_gu"] > 1e-12:
                reduced.append(allocation)
        allocations_after[element] = copy.deepcopy(reduced)
        if reduced:
            after["components"][element]["contributor_allocations"] = reduced
            after["components"][element]["remaining_gu"] = max(
                allocation["remaining_gu"] for allocation in reduced)
            after["components"][element]["contributor_ids"] = [
                allocation["owner"] for allocation in reduced]
        else:
            # ElementalMultiAuraState deletes a fully consumed layer.  The
            # admission proposal must use the same absence representation,
            # otherwise an exactly sufficient 0.4U layer can never commit.
            del after["components"][element]
    contributor_ids = list(dict.fromkeys(contributor_ids))
    if len(contributor_ids) > 4:
        raise ValueError("月感电单次跳数最多接受四名实际队伍贡献者。")
    absent = sorted(set(contributor_ids) - set(proposal.party_member_ids or ()))
    if absent:
        raise ValueError("实时附着贡献者必须属于实际队伍: "+", ".join(absent))
    if not contributor_ids:
        raise proposal.pending_error(
            "lunar_electro_contributors_missing",
            "月感电扣除前没有可审计的水雷正残量提供者。",
            event_id, {"target_id": tick["target_id"], "area_id": area_id})
    row = {
        "type": "special_reaction_tick_admission", "event_id": event_id+":admission",
        "t": tick["t"], "time_tick": tick["time_tick"],
        "target_id": tick["target_id"], "area_id": area_id,
        "reaction_type": "lunar_electro",
        "allowed": True, "reason": "live_hydro_electro_coexistence",
        "consumed_gu": {"hydro": consume, "electro": consume},
        "contributor_ids": contributor_ids,
        "consumed_contributor_ids": copy.deepcopy(contributor_ids),
        "owner_residual_contract": "same-element-owner-residual-max-v1",
        "contributor_allocations_before": allocations_before,
        "contributor_allocations_after": allocations_after,
        "excluded_stale_contributor_ids": [],
        "status": "executed",
        "profile": None,
    }
    return LunarTickAdmissionPlan(basis, tick["time_tick"], row, before, after)

