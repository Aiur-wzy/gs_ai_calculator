"""StellarSwirl planner; Runtime alone commits its closed transition."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replacement_contracts import replacement_state_fingerprint


@dataclass(frozen=True, slots=True)
class StellarSwirlProposal:
    vortex: Mapping[str, Any]
    mechanics: Mapping[str, Any]
    reaction: Mapping[str, Any]
    roles: dict[str, Any]
    contributor_ids: list[str]
    area_id: str
    event_id: str
    replacement_id: str
    owner: str
    target_id: str
    time: int
    seconds_to_ticks: Callable[[Any, str], int]
    merge_roles: Callable[..., None]
    damage_request: Callable[..., dict[str, Any]]
    detonate: Callable[..., list[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class StellarSwirlTransition:
    expected_prestate_fingerprint: str
    count: int
    level: int
    detonate_at: int | None
    owner: str | None
    action_id: str | None
    parent_reaction_id: str | None
    contributor_ids: list[str]
    anemo_trigger_ids: list[str]
    cryo_applier_ids: list[str]
    generated_events: list[dict[str, Any]]


def plan_stellar_swirl(proposal: StellarSwirlProposal) -> StellarSwirlTransition:
    vortex = copy.deepcopy(dict(proposal.vortex))
    mechanics, reaction = proposal.mechanics, proposal.reaction
    stellar_swirl_roles = proposal.roles
    contributor_ids = proposal.contributor_ids
    owner, area_id, time = proposal.owner, proposal.area_id, proposal.time
    event_id, target_id = proposal.event_id, proposal.target_id
    replaced = {"id": proposal.replacement_id}
    reaction_type = "stellar_swirl"
    generated: list[dict] = []
    new_vortex = vortex["stellar_swirl_count"] == 0
    vortex["stellar_swirl_count"] += 1
    vortex["stellar_swirl_level"] = (
        2 if vortex["stellar_swirl_count"] >= mechanics["upgrade_threshold"] else 1)
    vortex["stellar_swirl_owner"] = owner
    vortex["stellar_swirl_action_id"] = reaction.get("action_id")
    if new_vortex:
        vortex["stellar_swirl_parent_reaction_id"] = replaced["id"]
    proposal.merge_roles(
        vortex, stellar_swirl_roles, event_id)
    if new_vortex:
        delay = proposal.seconds_to_ticks(
            mechanics["detonation_delay_seconds"], "detonation_delay_seconds")
        vortex["stellar_swirl_detonate_at"] = time + delay
    generated.append(proposal.damage_request(
        event_id=replaced["id"]+":immediate:anemo",
        reaction_type="stellar_swirl_anemo", target_id=target_id,
        owner=owner, parent_reaction_id=replaced["id"], time_tick=time,
        component="immediate_anemo", contributor_ids=contributor_ids,
        area_id=area_id, role_provenance=stellar_swirl_roles))
    generated.append({
        "type": "special_reaction_state", "event_id": replaced["id"]+":vortex",
        "t": time/TICKS, "time_tick": time, "target_id": None,
        "area_id": area_id, "reaction_type": reaction_type,
        "operation": "create_or_upgrade_vortex",
        "packet_size": 1, "packet_started_with_vortex": not new_vortex,
        "count": vortex["stellar_swirl_count"],
        "vortex_level": vortex["stellar_swirl_level"],
        "detonate_at": vortex["stellar_swirl_detonate_at"]/TICKS,
        "status": "executed",
    })
    if vortex["stellar_swirl_count"] >= mechanics["early_detonation_threshold"]:
        generated.extend(proposal.detonate(
            area_id, vortex, time, "sixth_reaction_early"))
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
    )


__all__ = [
    "StellarSwirlProposal",
    "StellarSwirlTransition",
    "plan_stellar_swirl",
]

