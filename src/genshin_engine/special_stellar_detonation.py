"""Vortex detonation planner; the caller alone clears its bound vortex."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replace_stellar_swirl import StellarSwirlTransition
from genshin_engine.special_replacement_contracts import replacement_state_fingerprint


@dataclass(frozen=True, slots=True)
class StellarVortexDetonationProposal:
    vortex: Mapping[str, Any]
    mechanics: Mapping[str, Any]
    profile: Mapping[str, Any]
    area_id: str
    time_tick: int
    reason: str
    damage_request: Callable[..., dict[str, Any]]
    vortex_provenance: Callable[..., dict[str, Any]]


def plan_stellar_vortex_detonation(proposal: StellarVortexDetonationProposal) -> StellarSwirlTransition:
    state = copy.deepcopy(dict(proposal.vortex))
    mechanics, profile = proposal.mechanics, dict(proposal.profile)
    area_id, time_tick, reason = proposal.area_id, proposal.time_tick, proposal.reason
    level = max(1, min(state["stellar_swirl_level"], 2))
    factor_level = 2 if level == 1 else 3
    parent = state["stellar_swirl_parent_reaction_id"] or f"{area_id}:stellar-swirl"
    owner = state["stellar_swirl_owner"]
    action_id = state["stellar_swirl_action_id"]
    contributors = copy.deepcopy(state["stellar_swirl_contributor_ids"])
    damage = proposal.damage_request(
        event_id=parent+f":detonation:cryo:lv{factor_level}",
        reaction_type=f"stellar_swirl_cryo_lv{factor_level}", target_id=None,
        owner=owner, parent_reaction_id=parent, time_tick=time_tick,
        component=f"cryo_vortex_level_{level}_detonation", contributor_ids=contributors,
        area_id=area_id,
        role_provenance=proposal.vortex_provenance(state))
    damage["vortex_level"] = level
    damage["base_factor"] = factor_level
    application = {
        "type": "special_reaction_application_request",
        "event_id": damage["event_id"]+":application",
        "t": time_tick/TICKS,
        "time_tick": time_tick,
        "target_id": None,
        "area_id": area_id,
        "target_selection_policy": "event_time_entities_in_area",
        "parent_damage_event_id": damage["event_id"],
        "owner": owner,
        "action_id": action_id,
        "contributor_ids": contributors,
        "reaction_type": "stellar_swirl",
        "element": "cryo",
        "gauge_units": mechanics["ice_application_gu"],
        "application_order": "after_damage",
        "icd": {"kind": "none"},
        "direct_hit": False,
        "counts_toward_stellar_superconduct": True,
        "source": mechanics["source"],
        "profile": copy.deepcopy(profile),
        "status": "ready_for_elemental_runtime",
    }
    audit = {
        "type": "special_reaction_state",
        "event_id": parent+":detonation",
        "t": time_tick/TICKS,
        "time_tick": time_tick,
        "target_id": None,
        "area_id": area_id,
        "reaction_type": "stellar_swirl",
        "operation": "detonate_vortex",
        "reason": reason,
        "count": state["stellar_swirl_count"],
        "vortex_level": level,
        "ice_factor": 2 if level == 1 else 3,
        "profile": copy.deepcopy(profile),
        "status": "executed",
    }
    state.update(
        stellar_swirl_count=0, stellar_swirl_level=0,
        stellar_swirl_detonate_at=None,
        stellar_swirl_owner=None, stellar_swirl_action_id=None,
        stellar_swirl_parent_reaction_id=None,
        stellar_swirl_contributor_ids=[],
        stellar_swirl_anemo_trigger_ids=[],
        stellar_swirl_cryo_applier_ids=[])
    return StellarSwirlTransition(
        expected_prestate_fingerprint=replacement_state_fingerprint(proposal.vortex),
        count=0, level=0, detonate_at=None, owner=None, action_id=None,
        parent_reaction_id=None, contributor_ids=[], anemo_trigger_ids=[], cryo_applier_ids=[],
        generated_events=[audit, damage, application],
    )

