"""StellarField planner; Runtime alone commits its closed transition."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from genshin_engine.constants import stellar_superconduct_factor
from genshin_engine.rotation_port import TICKS
from genshin_engine.special_replacement_contracts import replacement_state_fingerprint


@dataclass(frozen=True, slots=True)
class StellarFieldProposal:
    field: Mapping[str, Any]
    mechanics: Mapping[str, Any]
    reaction: Mapping[str, Any]
    area_id: str
    event_id: str
    replacement_id: str
    target_id: str
    time: int
    seconds_to_ticks: Callable[[Any, str], int]


@dataclass(frozen=True, slots=True)
class StellarFieldTransition:
    expected_prestate_fingerprint: str
    was_active: bool
    next_settlement: int
    layers: int
    establishing_application: str
    expires_at: int
    generated_event: dict[str, Any]
    initial_grant_layers: int = 0


def plan_stellar_field(proposal: StellarFieldProposal) -> StellarFieldTransition:
    field, mechanics, reaction = proposal.field, proposal.mechanics, proposal.reaction
    duration = proposal.seconds_to_ticks(mechanics["field_duration_seconds"], "field_duration_seconds")
    interval = proposal.seconds_to_ticks(mechanics["settlement_interval_seconds"], "settlement_interval_seconds")
    establishing = (reaction.get("atomic_parent_id")
                    or reaction.get("parent_event_id") or proposal.event_id)
    was_active = field["stellar_superconduct_active"]
    next_settlement = (field["stellar_superconduct_next_settlement"]
                       if was_active else proposal.time + interval)
    layers = field["stellar_superconduct_layers"] if was_active else 0
    expires_at = proposal.time + duration
    initial_grant = 0 if was_active else mechanics.get("initial_grant_layers", 0)
    generated = {
        "type": "special_reaction_state", "event_id": proposal.replacement_id+":field",
        "t": proposal.time/TICKS, "time_tick": proposal.time, "target_id": None,
        "trigger_target_id": proposal.target_id, "area_id": proposal.area_id,
        "reaction_type": "stellar_superconduct", "operation": "establish_or_refresh_field",
        "initial_application_counted": False,
        "trigger_application_layer_eligibility": (
            "later_application" if was_active else "establishing_application_excluded"),
        "current_layers": layers,
        "next_settlement_at": next_settlement/TICKS,
        "expires_at": expires_at/TICKS,
        "physical_res_delta": -.4, "physical_res_shred": .4,
        "physical_res_effect_scope": "polestar_field_lifetime",
        "damage_emitted": False, "damage_policy": "direct_stellar_hits_only",
        "status": "executed",
    }
    if initial_grant:
        generated.update(initial_grant_layers=initial_grant,
                         initial_grant_factor=stellar_superconduct_factor(initial_grant),
                         initial_grant_expires_at=next_settlement/TICKS)
    return StellarFieldTransition(
        expected_prestate_fingerprint=replacement_state_fingerprint(field),
        was_active=was_active, next_settlement=next_settlement, layers=layers,
        establishing_application=establishing, expires_at=expires_at, generated_event=generated,
        initial_grant_layers=initial_grant,
    )


__all__ = [
    "StellarFieldProposal",
    "StellarFieldTransition",
    "plan_stellar_field",
]

