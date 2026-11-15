"""Deterministic private input normalization for equipment composition."""

from __future__ import annotations

import copy
from typing import Any

_REPLAY_SENSITIVE_KEYS = (
    "calculation_event_overrides",
    "action_dispatch_sources",
    "action_admission_sources",
    "action_capacity_sources",
    "mechanism_sources",
    "limited_hit_benefit_sources",
    "energy_generation_sources",
    "periodic_attack_sources",
    "passive_sources",
    "attack_metadata_sources",
    "action_event_profiles",
    "action_resource_sources",
    "elemental_sources",
    "reaction_damage_sources",
    "hit_parameter_sources",
)


def normalize_compose_input(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize map order and preserve the historical replay basis policy."""

    from .energy_policy import validate_author_energy_policy
    validate_author_energy_policy(plan)
    equipment = plan["equipment_inputs"]
    plan["equipment_inputs"] = {
        character["id"]: equipment[character["id"]]
        for character in plan["manual_template"]["characters"]
        if character["id"] in equipment
    }
    needs_replay_copy = (
        any(plan.get(key) for key in _REPLAY_SENSITIVE_KEYS)
        or plan.get("extensions", {}).get("agent_execution_materialization")
        or any("upstream_recipe" in binding for binding in plan["effect_bindings"])
    )
    original_plan = copy.deepcopy(plan) if needs_replay_copy else plan
    return plan, original_plan


__all__ = ["normalize_compose_input"]
