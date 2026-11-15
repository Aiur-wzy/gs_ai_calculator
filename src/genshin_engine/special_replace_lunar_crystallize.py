"""LunarCrystallize planner; Runtime alone commits its closed transition."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replacement_contracts import replacement_state_fingerprint


@dataclass(frozen=True, slots=True)
class LunarCrystallizeProposal:
    moondrift: Mapping[str, Any]
    party_state: Mapping[str, Any]
    mechanics: Mapping[str, Any]
    contributor_ids: list[str]
    area_id: str
    event_id: str
    replacement_id: str
    owner: str
    target_id: str
    time: int
    duration_ticks: int
    seconds_to_ticks: Callable[[Any, str], int]
    pending_error: Callable[..., Exception]
    damage_request: Callable[..., dict[str, Any]]


@dataclass(frozen=True, slots=True)
class LunarCrystallizeTransition:
    expected_party_fingerprint: str
    expected_moondrift_fingerprint: str
    new_set: bool
    cages: int
    expires_at: int
    appended_ledger_entry: dict[str, Any] | None
    ledger_consume_count: int
    harmony_count: int
    contributor_ids: list[str]
    global_attack_write: bool
    area_attack_write: bool
    harmony_executed: bool
    global_last_attack: int | None
    area_last_attack: int | None
    generated_events: list[dict[str, Any]]


def plan_lunar_crystallize(proposal: LunarCrystallizeProposal) -> LunarCrystallizeTransition:
    moondrift = copy.deepcopy(dict(proposal.moondrift))
    party = copy.deepcopy(dict(proposal.party_state))
    mechanics, contributor_ids = proposal.mechanics, proposal.contributor_ids
    area_id, event_id, target_id = proposal.area_id, proposal.event_id, proposal.target_id
    owner, time, duration = proposal.owner, proposal.time, proposal.duration_ticks
    replaced = {"id": proposal.replacement_id}
    reaction_type = "lunar_crystallize"
    generated: list[dict] = []
    new_set = moondrift["lunar_crystallize_cages"] == 0
    if new_set:
        moondrift["lunar_crystallize_cages"] = mechanics["cages_per_reaction"]
        moondrift["lunar_crystallize_expires_at"] = time + duration
    ledger = party["lunar_crystallize_harmony_ledger"]
    cap = mechanics["harmony_counter_cap"]
    counter_capped = len(ledger) >= cap
    if not counter_capped:
        ledger.append({
            "reaction_id": replaced["id"],
            "area_id": area_id,
            "contributor_ids": copy.deepcopy(contributor_ids),
        })
    count = min(cap, len(ledger))
    party["lunar_crystallize_harmony_count"] = count
    party["lunar_crystallize_contributor_ids"] = list(dict.fromkeys(
        contributor
        for entry in ledger
        for contributor in entry["contributor_ids"]
    ))[:4]
    state_event = {
        "type": "special_reaction_state", "event_id": replaced["id"]+":moon-cages",
        "t": time/TICKS, "time_tick": time, "target_id": None,
        "trigger_target_id": target_id, "area_id": area_id,
        "reaction_type": reaction_type,
        "operation": "create_moondrift_set" if new_set else "advance_harmony",
        "count": count, "cages": mechanics["cages_per_reaction"],
        "cages_created": mechanics["cages_per_reaction"] if new_set else 0,
        "ordinary_shard_created": False, "common_cooldown_seconds": 0,
        "harmony_scope": "party", "moondrift_scope": "area",
        "harmony_counter_cap": cap, "counter_capped": counter_capped,
        "contributor_ids": copy.deepcopy(
            party["lunar_crystallize_contributor_ids"]),
        "expires_at": moondrift["lunar_crystallize_expires_at"]/TICKS,
        "harmony_dispatch": "not_ready",
        "status": "executed",
    }
    generated.append(state_event)
    threshold = mechanics["harmony_threshold"]
    if count >= threshold and new_set:
        # The creation reaction counts, but a newly created set may
        # not immediately spend historical overflow to attack.
        state_event["harmony_dispatch"] = "suppressed_on_initial_creation"
    elif count >= threshold:
        cooldown = mechanics.get("harmony_attack_cooldown_seconds")
        scope = mechanics.get("harmony_attack_cooldown_scope")
        if cooldown is None or scope is None:
            raise proposal.pending_error(
                "lunar_crystallize_harmony_cooldown_pending",
                "月笼谐奏攻击冷却的精确值与作用域尚未核实。",
                event_id,
                {"area_id": area_id, "harmony_count": count},
            )
        cooldown_ticks = proposal.seconds_to_ticks(
            cooldown, "harmony_attack_cooldown_seconds")
        last_attack = (
            party["lunar_crystallize_last_harmony_attack_at"]
            if scope == "global"
            else moondrift["lunar_crystallize_last_harmony_attack_at"]
        )
        if last_attack is not None and time-last_attack < cooldown_ticks:
            state_event["harmony_dispatch"] = "blocked_by_assumed_attack_cooldown"
            state_event["harmony_attack_cooldown_seconds"] = cooldown
            state_event["harmony_attack_cooldown_scope"] = scope
        else:
            cycle_entries = copy.deepcopy(ledger[:threshold])
            harmony_contributors = list(dict.fromkeys(
                contributor
                for entry in cycle_entries
                for contributor in entry["contributor_ids"]
            ))[:4]
            del ledger[:threshold]
            party["lunar_crystallize_harmony_count"] = len(ledger)
            party["lunar_crystallize_contributor_ids"] = list(dict.fromkeys(
                contributor
                for entry in ledger
                for contributor in entry["contributor_ids"]
            ))[:4]
            if scope == "global":
                party["lunar_crystallize_last_harmony_attack_at"] = time
            else:
                moondrift["lunar_crystallize_last_harmony_attack_at"] = time
            state_event["harmony_dispatch"] = "executed"
            state_event["harmony_count_after"] = len(ledger)
            state_event["harmony_cycle_reaction_ids"] = [
                entry["reaction_id"] for entry in cycle_entries]
            state_event["harmony_attack_cooldown_seconds"] = cooldown
            state_event["harmony_attack_cooldown_scope"] = scope
        # A Harmony attack refreshes inactivity lifetime only for
        # the Moondrifts that actually attacked in this area.
            moondrift["lunar_crystallize_expires_at"] = time + duration
            for cage in range(1, min(
                    moondrift["lunar_crystallize_cages"],
                    mechanics["cages_per_reaction"])+1):
                generated.append(proposal.damage_request(
                    event_id=replaced["id"]+f":harmony:cage:{cage}",
                    reaction_type="lunar_crystallize", target_id=None,
                    owner=owner, parent_reaction_id=replaced["id"], time_tick=time,
                    component=f"moon_cage_harmony_{cage}",
                    contributor_ids=harmony_contributors, area_id=area_id,
                    sample_policy="harmony_cycle_contributors_event_time"))
                generated[-1]["trigger_target_id"] = target_id
                generated[-1]["target_selection_policy"] = (
                    "event_time_nearby_entities_in_area")
                generated[-1]["damage_instance_index"] = cage
                generated[-1]["independent_crit_instance"] = True
    executed = state_event["harmony_dispatch"] == "executed"
    scope = mechanics.get("harmony_attack_cooldown_scope")
    return LunarCrystallizeTransition(
        expected_party_fingerprint=replacement_state_fingerprint(proposal.party_state),
        expected_moondrift_fingerprint=replacement_state_fingerprint(proposal.moondrift),
        new_set=new_set, cages=moondrift["lunar_crystallize_cages"],
        expires_at=moondrift["lunar_crystallize_expires_at"],
        appended_ledger_entry=({
            "reaction_id": replaced["id"], "area_id": area_id,
            "contributor_ids": copy.deepcopy(contributor_ids),
        } if not counter_capped else None),
        ledger_consume_count=threshold if executed else 0,
        harmony_count=party["lunar_crystallize_harmony_count"],
        contributor_ids=party["lunar_crystallize_contributor_ids"],
        global_attack_write=executed and scope == "global",
        area_attack_write=executed and scope != "global",
        harmony_executed=executed,
        global_last_attack=party["lunar_crystallize_last_harmony_attack_at"],
        area_last_attack=moondrift["lunar_crystallize_last_harmony_attack_at"],
        generated_events=generated,
    )


__all__ = [
    "LunarCrystallizeProposal",
    "LunarCrystallizeTransition",
    "plan_lunar_crystallize",
]

