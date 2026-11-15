"""Frozen replacement-family registry; no runtime state or generic patch language.

Every family owns its planner and closed proposal/transition types in a separate
module. This facade reexports those contracts and binds exact implementation
versions. Runtime owns commits and rollback; World owns atomic publication.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Any

from genshin_engine.special_replace_lunar_bloom import (
    LunarBloomProposal,
    LunarBloomTransition,
    plan_lunar_bloom,
)
from genshin_engine.special_replace_lunar_crystallize import (
    LunarCrystallizeProposal,
    LunarCrystallizeTransition,
    plan_lunar_crystallize,
)
from genshin_engine.special_replace_lunar_electro import (
    LunarElectroProposal,
    LunarElectroTransition,
    plan_lunar_electro,
)
from genshin_engine.special_replace_stellar_field import (
    StellarFieldProposal,
    StellarFieldTransition,
    plan_stellar_field,
)
from genshin_engine.special_replace_stellar_swirl import (
    StellarSwirlProposal,
    StellarSwirlTransition,
    plan_stellar_swirl,
)
from genshin_engine.special_replacement_contracts import (
    ReplacementFamilyBinding,
    replacement_schedule_fingerprint,
    replacement_state_fingerprint,
)

_FAMILY_HANDLERS = MappingProxyType({
    ("stellar_superconduct", 1, 2): ReplacementFamilyBinding(
        "stellar_superconduct", 1, 2, plan_stellar_field, capabilities=(
            "replace", "deadline_production", "state_projection", "direct_damage")),
    ("stellar_swirl", 1, 2): ReplacementFamilyBinding(
        "stellar_swirl", 1, 2, plan_stellar_swirl, capabilities=(
            "replace", "packet_replacement", "deadline_production", "world_consumer", "damage_projection")),
    ("lunar_electro", 1, 2): ReplacementFamilyBinding(
        "lunar_electro", 1, 2, plan_lunar_electro, capabilities=(
            "replace", "deadline_production", "tick_admission", "world_consumer", "direct_damage")),
    ("lunar_bloom", 1, 2): ReplacementFamilyBinding(
        "lunar_bloom", 1, 2, plan_lunar_bloom, capabilities=(
            "replace", "deadline_production", "resource_consumption", "direct_damage")),
    ("lunar_crystallize", 1, 2): ReplacementFamilyBinding(
        "lunar_crystallize", 1, 2, plan_lunar_crystallize, capabilities=(
            "replace", "deadline_production", "direct_damage")),
})


def replacement_family_binding(family: str, *, contract_version: int = 1,
                               implementation_version: int = 2) -> ReplacementFamilyBinding[Any, Any]:
    # Defaults identify this source release, never an implicit newest selector.
    return _FAMILY_HANDLERS[(family, contract_version, implementation_version)]


def legacy_replacement_family_binding(family: str) -> ReplacementFamilyBinding[Any, Any]:
    """Adapt unsigned model-v1 syntax to the same current family algorithm.

    This is not a registry entry and cannot authorize an old signed binding.
    The declaration retains its historic version for schema-v1 trace identity.
    """
    return replace(_FAMILY_HANDLERS[(family, 1, 2)], implementation_version=1)


def registered_replacement_bindings() -> tuple[ReplacementFamilyBinding[Any, Any], ...]:
    return tuple(_FAMILY_HANDLERS[key] for key in sorted(_FAMILY_HANDLERS))


__all__ = [
    "LunarBloomProposal",
    "LunarBloomTransition",
    "LunarCrystallizeProposal",
    "LunarCrystallizeTransition",
    "LunarElectroProposal",
    "LunarElectroTransition",
    "ReplacementFamilyBinding",
    "StellarFieldProposal",
    "StellarFieldTransition",
    "StellarSwirlProposal",
    "StellarSwirlTransition",
    "legacy_replacement_family_binding",
    "registered_replacement_bindings",
    "replacement_family_binding",
    "replacement_schedule_fingerprint",
    "replacement_state_fingerprint",
]
