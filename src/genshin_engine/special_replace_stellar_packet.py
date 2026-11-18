"""Readonly Star-Swirl packet planning; one Runtime commits the vortex."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replace_stellar_swirl import StellarSwirlTransition
from genshin_engine.special_replacement_contracts import (
    ReplacementFamilyBinding,
    replacement_state_fingerprint,
)
from genshin_engine.special_stellar_initial_upgrade import apply_initial_vortex_upgrade


@dataclass(frozen=True, slots=True)
class StellarSwirlPacketProposal:
    vortex: Mapping[str, Any]
    mechanics: Mapping[str, Any]
    profile: Mapping[str, Any]
    reactions: tuple[Mapping[str, Any], ...]
    replacement_rows: tuple[Mapping[str, Any], ...]
    packet_roles: tuple[dict[str, Any], ...]
    area_id: str
    event_id: str
    time: int
    seconds_to_ticks: Callable[[Any, str], int]
    merge_roles: Callable[..., None]
    detonate: Callable[..., list[dict[str, Any]]]


def plan_stellar_swirl_packet(proposal: StellarSwirlPacketProposal) -> StellarSwirlTransition:
    vortex = copy.deepcopy(dict(proposal.vortex))
    mechanics, profile = proposal.mechanics, dict(proposal.profile)
    reactions, replaced_rows = proposal.reactions, proposal.replacement_rows
    packet_roles = proposal.packet_roles
    area_id, packet_event_id, time = proposal.area_id, proposal.event_id, proposal.time
    started_with_vortex = vortex["stellar_swirl_count"] > 0
    generated: list[dict[str, Any]] = []
    if not started_with_vortex:
        first = replaced_rows[0]
        first_reaction = reactions[0]
        vortex.update(
            stellar_swirl_count=1,
            stellar_swirl_level=1,
            stellar_swirl_detonate_at=time + proposal.seconds_to_ticks(
                mechanics["detonation_delay_seconds"], "detonation_delay_seconds"),
            stellar_swirl_owner=first_reaction.get("owner"),
            stellar_swirl_action_id=first_reaction.get("action_id"),
            stellar_swirl_parent_reaction_id=first["id"],
        )
        apply_initial_vortex_upgrade(vortex, mechanics, time, proposal.seconds_to_ticks)
        for roles in packet_roles:
            proposal.merge_roles(
                vortex, roles, packet_event_id)
        generated.append({
            "type": "special_reaction_state",
            "event_id": packet_event_id+":create-vortex",
            "t": time/TICKS, "time_tick": time, "target_id": None,
            "area_id": area_id, "reaction_type": "stellar_swirl",
            "operation": "create_vortex_from_packet",
            "packet_size": len(reactions), "packet_started_with_vortex": False,
            "counted_children": 1, "count": vortex["stellar_swirl_count"], "vortex_level": vortex["stellar_swirl_level"],
            "detonate_at": vortex["stellar_swirl_detonate_at"]/TICKS,
            "profile": copy.deepcopy(profile), "status": "executed",
        })
    else:
        detonated = False
        counted_children = 0
        for reaction, replaced, roles in zip(
                reactions, replaced_rows, packet_roles):
            if detonated:
                continue
            counted_children += 1
            vortex["stellar_swirl_count"] += 1
            vortex["stellar_swirl_level"] = (
                2 if vortex["stellar_swirl_count"] >= mechanics["upgrade_threshold"] else 1)
            vortex["stellar_swirl_owner"] = reaction.get("owner")
            vortex["stellar_swirl_action_id"] = reaction.get("action_id")
            proposal.merge_roles(
                vortex, roles, replaced["id"])
            generated.append({
                "type": "special_reaction_state",
                "event_id": replaced["id"]+":advance-vortex",
                "t": time/TICKS, "time_tick": time, "target_id": None,
                "area_id": area_id, "reaction_type": "stellar_swirl",
                "operation": "advance_existing_vortex_from_packet_child",
                "packet_started_with_vortex": True,
                "count": vortex["stellar_swirl_count"],
                "vortex_level": vortex["stellar_swirl_level"],
                "profile": copy.deepcopy(profile), "status": "executed",
            })
            if vortex["stellar_swirl_count"] >= mechanics["early_detonation_threshold"]:
                generated.extend(proposal.detonate(
                    area_id, vortex, time, "sixth_reaction_early"))
                detonated = True
        if detonated and counted_children < len(reactions):
            generated.append({
                "type": "special_reaction_state",
                "event_id": packet_event_id+":post-detonation",
                "t": time/TICKS, "time_tick": time, "target_id": None,
                "area_id": area_id, "reaction_type": "stellar_swirl",
                "operation": "remaining_packet_children_do_not_reseed",
                "remaining_children": len(reactions)-counted_children,
                "interpretation_status": "community_interpretation_v1",
                "profile": copy.deepcopy(profile), "status": "executed",
            })
    return StellarSwirlTransition(
        expected_prestate_fingerprint=replacement_state_fingerprint(proposal.vortex),
        count=vortex["stellar_swirl_count"], level=vortex["stellar_swirl_level"],
        detonate_at=vortex["stellar_swirl_detonate_at"],
        owner=vortex["stellar_swirl_owner"], action_id=vortex["stellar_swirl_action_id"],
        parent_reaction_id=vortex["stellar_swirl_parent_reaction_id"],
        contributor_ids=vortex["stellar_swirl_contributor_ids"],
        anemo_trigger_ids=vortex["stellar_swirl_anemo_trigger_ids"],
        cryo_applier_ids=vortex["stellar_swirl_cryo_applier_ids"],
        generated_events=generated,
        initial_upgrade_ready_at=vortex.get("stellar_swirl_initial_upgrade_ready_at"),
    )


_PACKET_BINDING = ReplacementFamilyBinding("stellar_swirl", 1, 1, plan_stellar_swirl_packet)


def stellar_swirl_packet_binding() -> ReplacementFamilyBinding[StellarSwirlPacketProposal, StellarSwirlTransition]:
    return _PACKET_BINDING

