"""Input vocabulary and scalar checks; no runtime state or commit authority."""
from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

from genshin_engine.rotation import TICKS

SPECIAL_REACTION_SCHEMA_VERSION = 2

SPECIAL_REACTION_TYPES = (
    "stellar_superconduct",
    "stellar_swirl",
    "lunar_electro",
    "lunar_bloom",
    "lunar_crystallize",
)

REQUIRED_CAPABILITY_TAGS = {
    reaction_type: f"native_reaction_qualification.{reaction_type}"
    for reaction_type in SPECIAL_REACTION_TYPES
}

WEIGHTED_REACTION_TYPES = (
    "stellar_swirl_anemo",
    "stellar_swirl_cryo_lv2",
    "stellar_swirl_cryo_lv3",
    "lunar_electro",
    "lunar_crystallize",
)

LUNAR_STELLAR_WEIGHTS = (0.6, 0.3, 0.05, 0.05)

DIRECT_LUNAR_SCALING_STATS = frozenset(("atk", "hp", "def", "em"))

DIRECT_LUNAR_ELEMENTS = {
    "lunar_electro": "electro",
    "lunar_bloom": "dendro",
    "lunar_crystallize": "geo",
}

DIRECT_STELLAR_SCALING_STATS = DIRECT_LUNAR_SCALING_STATS

DIRECT_STELLAR_ELEMENTS = frozenset(("cryo", "electro"))
DIRECT_STELLAR_ELEMENT_BY_REACTION = {
    "stellar_superconduct": DIRECT_STELLAR_ELEMENTS,
    "stellar_swirl": frozenset(("cryo", "anemo")),
}

DIRECT_ABILITY_BINDING_KEYS = frozenset((
    "character_id", "ability_id", "component_id", "variant_id",
    "effective_level", "database_fingerprint",
))

# Unordered pairs: gauge receipts retain the actual aura/trigger direction.
REPLACEMENT_CONTRACTS = {
    "stellar_superconduct": {
        "ordinary_type": "superconduct",
        "elements": frozenset(("cryo", "electro")),
        "system": "stellar",
    },
    "stellar_swirl": {
        "ordinary_type": "swirl",
        "elements": frozenset(("anemo", "cryo")),
        "system": "stellar",
    },
    "lunar_electro": {
        "ordinary_type": "electro_charged",
        "elements": frozenset(("hydro", "electro")),
        "system": "lunar",
    },
    "lunar_bloom": {
        "ordinary_type": "bloom",
        "elements": frozenset(("hydro", "dendro")),
        "system": "lunar",
    },
    "lunar_crystallize": {
        "ordinary_type": "crystallize",
        "elements": frozenset(("geo", "hydro")),
        "system": "lunar",
    },
}

_QUALIFICATION_KEYS = frozenset(("status", "provider_ids", "capability_tag", "source"))

_PROFILE_META_KEYS = frozenset((
    "status", "source", "profile_id", "profile_revision", "profile_content_hash",
    "override_reason",
))

_BASE_MECHANICS_KEYS = _PROFILE_META_KEYS

# Engine extensions are explicit assumptions layered over the unchanged DB
# base profile. They are not silently registered as reviewed database facts.
OPTIONAL_MECHANICS_KEYS = {
    "stellar_superconduct": frozenset(("initial_grant_layers", "settlement_bonus_layers")),
    "stellar_swirl": frozenset(("initial_vortex_count", "initial_upgrade_cooldown_seconds")),
}

_MECHANICS_KEYS = {
    "stellar_superconduct": _BASE_MECHANICS_KEYS | frozenset((
        "field_duration_seconds", "settlement_interval_seconds", "refresh_mode",
        "initial_grant_layers", "settlement_bonus_layers",
    )),
    "stellar_swirl": _BASE_MECHANICS_KEYS | frozenset((
        "upgrade_threshold", "early_detonation_threshold", "detonation_delay_seconds",
        "ice_application_gu", "creation_batch_cap_once",
        "existing_vortex_counts_each_reaction",
        "initial_vortex_count", "initial_upgrade_cooldown_seconds",
    )),
    "lunar_electro": _BASE_MECHANICS_KEYS | frozenset((
        "first_tick_delay_seconds", "tick_interval_seconds", "duration_seconds",
        "refresh_mode", "aura_consumption_gu",
    )),
    "lunar_bloom": _BASE_MECHANICS_KEYS | frozenset((
        "dew_interval_seconds", "dew_cap", "backlog_per_reaction",
        "same_parent_backlog_cap_once", "full_cap_queue_policy",
    )),
    "lunar_crystallize": _BASE_MECHANICS_KEYS | frozenset((
        "harmony_threshold", "cages_per_reaction", "common_cooldown_seconds",
        "cage_duration_seconds", "harmony_counter_cap",
        "harmony_attack_cooldown_seconds", "harmony_attack_cooldown_scope",
    )),
}

_CONTRIBUTOR_KEYS = frozenset((
    "id", "level", "em", "crit_rate", "crit_dmg", "reaction_bonus",
    "reaction_flat", "resistance", "lunar_base", "lunar_mult",
    "lunar_elevate", "stellar_base", "stellar_mult", "stellar_elevate",
    "source_refs",
))


def _issue(code: str, message: str, path: str, **details) -> dict:
    return {"code": code, "message": message, "path": path, **details}


def _finite_number(value: Any, *, minimum: float | None = None, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        finite = math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False
    if not finite:
        return False
    if positive and value <= 0:
        return False
    return minimum is None or value >= minimum


def _seconds_to_ticks(value: float, path: str) -> int:
    if not _finite_number(value, minimum=0):
        raise ValueError(f"{path}必须是有限非负秒数。")
    ticks = Decimal(str(value)) * TICKS
    if ticks != ticks.to_integral_value():
        raise ValueError(f"{path}必须精确到微秒。")
    return int(ticks)
