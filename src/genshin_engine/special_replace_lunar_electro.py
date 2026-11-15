"""LunarElectro planner; Runtime alone commits its closed transition."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replacement_contracts import replacement_state_fingerprint


@dataclass(frozen=True, slots=True)
class LunarElectroProposal:
    cloud: Mapping[str, Any]
    mechanics: Mapping[str, Any]
    area_id: str
    target_id: str
    owner: str
    replacement_id: str
    time: int
    scheduled_basis: str
    seconds_to_ticks: Callable[[Any, str], int]
    make_tick_request: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class LunarElectroTransition:
    expected_prestate_fingerprint: str
    expected_schedule_fingerprint: str
    expires_at: int
    added_target_id: str | None
    new_target_phase: dict[str, Any] | None
    phase_indices: tuple[tuple[str, int], ...]
    scheduled_requests: list[dict[str, Any]]
    generated_events: list[dict[str, Any]]


def plan_lunar_electro(proposal: LunarElectroProposal) -> LunarElectroTransition:
    cloud = copy.deepcopy(dict(proposal.cloud))
    mechanics, area_id, target_id = proposal.mechanics, proposal.area_id, proposal.target_id
    owner, time = proposal.owner, proposal.time
    replaced = {"id": proposal.replacement_id}
    reaction_type = "lunar_electro"
    generated: list[dict] = []
    scheduled: list[dict] = []
    first = proposal.seconds_to_ticks(mechanics["first_tick_delay_seconds"], "first_tick_delay_seconds")
    interval = proposal.seconds_to_ticks(mechanics["tick_interval_seconds"], "tick_interval_seconds")
    duration = proposal.seconds_to_ticks(mechanics["duration_seconds"], "duration_seconds")
    was_active = cloud["lunar_electro_active"]
    previous_expires = cloud["lunar_electro_expires_at"]
    cloud["lunar_electro_active"] = True
    cloud["lunar_electro_expires_at"] = time+duration
    is_new_target = target_id not in cloud["lunar_electro_target_ids"]
    if is_new_target:
        cloud["lunar_electro_target_ids"].append(target_id)
        cloud["lunar_electro_phase_by_target"][target_id] = {
            "first_attack_at": time+first,
            "next_tick_index": 1,
            "owner": owner,
            "parent_reaction_id": replaced["id"],
        }
    scheduled_count = 0
    scheduled_times: list[int] = []
    for cloud_target in cloud["lunar_electro_target_ids"]:
        phase = cloud["lunar_electro_phase_by_target"][cloud_target]
        index = phase["next_tick_index"]
        tick_at = phase["first_attack_at"] + (index-1)*interval
        while tick_at <= cloud["lunar_electro_expires_at"]:
            request = proposal.make_tick_request(
                area_id=area_id,
                target_id=cloud_target,
                phase=phase,
                tick_index=index,
                time_tick=tick_at,
            )
            scheduled.append(request)
            scheduled_count += 1
            scheduled_times.append(tick_at)
            index += 1
            tick_at += interval
        phase["next_tick_index"] = index
    generated.append({
        "type": "special_reaction_schedule", "event_id": replaced["id"]+":schedule",
        "t": time/TICKS, "time_tick": time, "target_id": target_id,
        "area_id": area_id, "cloud_scope": "area",
        "reaction_type": reaction_type,
        "operation": ("extend_cloud_without_phase_reset" if was_active
                      else "schedule_aura_checked_ticks"),
        "refresh_mode": "extend_without_phase_reset",
        "previous_expires_at": (
            previous_expires/TICKS if previous_expires is not None else None),
        "new_target_added": is_new_target,
        "scheduled_target_ids": copy.deepcopy(
            cloud["lunar_electro_target_ids"]),
        "scheduled_count": scheduled_count,
        "new_scheduled_times": [value/TICKS for value in scheduled_times],
        "first_tick_at": (
            min(scheduled_times)/TICKS if scheduled_times else None),
        "last_tick_at": (
            max(scheduled_times)/TICKS if scheduled_times else None),
        "expires_at": cloud["lunar_electro_expires_at"]/TICKS,
        "status": "executed",
    })
    return LunarElectroTransition(
        expected_prestate_fingerprint=replacement_state_fingerprint(proposal.cloud),
        expected_schedule_fingerprint=proposal.scheduled_basis,
        expires_at=cloud["lunar_electro_expires_at"],
        added_target_id=target_id if is_new_target else None,
        new_target_phase=cloud["lunar_electro_phase_by_target"][target_id] if is_new_target else None,
        phase_indices=tuple((target, cloud["lunar_electro_phase_by_target"][target]["next_tick_index"])
                            for target in cloud["lunar_electro_target_ids"]),
        scheduled_requests=scheduled, generated_events=generated,
    )


__all__ = [
    "LunarElectroProposal",
    "LunarElectroTransition",
    "plan_lunar_electro",
]

