"""LunarBloom planner; Runtime alone commits its closed transition."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replacement_contracts import replacement_state_fingerprint


@dataclass(frozen=True, slots=True)
class LunarBloomProposal:
    party_state: Mapping[str, Any]
    mechanics: Mapping[str, Any]
    reaction: Mapping[str, Any]
    event_id: str
    replacement_id: str
    target_id: str
    time: int
    seconds_to_ticks: Callable[[Any, str], int]


@dataclass(frozen=True, slots=True)
class LunarBloomTransition:
    expected_prestate_fingerprint: str
    operation: Literal["duplicate", "pending", "discard", "retain", "queue"]
    atomic_parent: str
    atomic_parents: list[str]
    full_cap_unresolved: list[str]
    backlog: int
    backlog_sources: list[str]
    next_dew_at: int | None
    start_fill: bool
    generated_events: list[dict[str, Any]]


def plan_lunar_bloom(proposal: LunarBloomProposal) -> LunarBloomTransition:
    party = copy.deepcopy(dict(proposal.party_state))
    mechanics, reaction = proposal.mechanics, proposal.reaction
    event_id, target_id, time = proposal.event_id, proposal.target_id, proposal.time
    replaced = {"id": proposal.replacement_id}
    reaction_type = "lunar_bloom"
    generated: list[dict] = []
    interval = proposal.seconds_to_ticks(mechanics["dew_interval_seconds"], "dew_interval_seconds")
    atomic_parent = reaction.get("package_id") or reaction.get("parent_event_id") or event_id
    already_counted = atomic_parent in party["lunar_bloom_atomic_parents"]
    backlog_before = party["lunar_bloom_backlog"]
    unresolved_before = len(party["lunar_bloom_full_cap_unresolved"])
    backlog_added = 0
    queue_policy_status = "not_applicable"
    if not already_counted:
        party["lunar_bloom_atomic_parents"] = list(dict.fromkeys(
            [*party["lunar_bloom_atomic_parents"], atomic_parent]))[-256:]
        if party["lunar_bloom_dew"] >= mechanics["dew_cap"]:
            policy = mechanics["full_cap_queue_policy"]
            if policy == "pending":
                party["lunar_bloom_full_cap_unresolved"] = list(dict.fromkeys(
                    [*party["lunar_bloom_full_cap_unresolved"],
                     atomic_parent]))[-256:]
                queue_policy_status = "pending_until_resource_is_consumed"
            elif policy == "discard_new":
                queue_policy_status = "assumed_discarded"
            else:  # retain_active_fill_window, necessarily an assumed override
                party["lunar_bloom_backlog"] = max(
                    1, party["lunar_bloom_backlog"])
                party["lunar_bloom_backlog_sources"] = [atomic_parent]
                queue_policy_status = "assumed_single_window_retained"
        else:
            backlog_added = mechanics["backlog_per_reaction"]
            party["lunar_bloom_backlog"] += backlog_added
            party["lunar_bloom_backlog_sources"].append(atomic_parent)
            queue_policy_status = "queued"
            if backlog_before == 0:
                party["lunar_bloom_next_dew_at"] = time + interval
    generated.append({
        "type": "special_reaction_core_request", "event_id": replaced["id"]+":core",
        "t": time/TICKS, "time_tick": time, "target_id": target_id,
        "reaction_type": reaction_type, "operation": "generate_ordinary_dendro_core",
        "ordinary_core_created": True, "lunar_damage_emitted": False,
        "status": "ready_for_core_runtime",
    })
    generated.append({
        "type": "special_reaction_state", "event_id": replaced["id"]+":backlog",
        "t": time/TICKS, "time_tick": time, "target_id": target_id,
        "reaction_type": reaction_type, "operation": "enqueue_party_dew_fill",
        "resource_scope": "party", "dew_added_immediately": 0,
        "lunar_damage_emitted": False,
        "atomic_parent_id": atomic_parent,
        "backlog_added": backlog_added,
        "backlog_before": backlog_before,
        "backlog_after": party["lunar_bloom_backlog"],
        "full_cap_queue_policy": mechanics["full_cap_queue_policy"],
        "queue_policy_status": (
            "same_parent_already_counted" if already_counted else queue_policy_status),
        "unresolved_full_cap_before": unresolved_before,
        "unresolved_full_cap_after": len(
            party["lunar_bloom_full_cap_unresolved"]),
        "next_dew_at": (
            party["lunar_bloom_next_dew_at"]/TICKS
            if party["lunar_bloom_next_dew_at"] is not None else None),
        "status": "executed",
    })
    operation = ("duplicate" if already_counted else {
        "pending_until_resource_is_consumed": "pending",
        "assumed_discarded": "discard",
        "assumed_single_window_retained": "retain",
        "queued": "queue",
    }[queue_policy_status])
    return LunarBloomTransition(
        expected_prestate_fingerprint=replacement_state_fingerprint(proposal.party_state),
        operation=operation, atomic_parent=atomic_parent,
        atomic_parents=party["lunar_bloom_atomic_parents"],
        full_cap_unresolved=party["lunar_bloom_full_cap_unresolved"],
        backlog=party["lunar_bloom_backlog"],
        backlog_sources=party["lunar_bloom_backlog_sources"],
        next_dew_at=party["lunar_bloom_next_dew_at"],
        start_fill=operation == "queue" and backlog_before == 0,
        generated_events=generated,
    )


__all__ = [
    "LunarBloomProposal",
    "LunarBloomTransition",
    "plan_lunar_bloom",
]

