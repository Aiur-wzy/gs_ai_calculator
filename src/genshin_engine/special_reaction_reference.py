"""Version-pinned canonical special-reaction reference boundary.

The reference call graph is frozen by :mod:`special_reactions` at import time.
This module gives verification consumers stable public names without granting
the production runtime a fallback path.  Reference functions remain suitable
only for shadow comparison and invariant checks.
"""

from __future__ import annotations

from genshin_engine import special_reactions as _legacy

admit_lunar_electro_tick = _legacy._REFERENCE_ADMIT_LUNAR_ELECTRO_TICK_IMPL
advance_world_pre_expiry = _legacy._REFERENCE_ADVANCE_WORLD_PRE_EXPIRY
finalize_world_lunar_electro_expiry = (
    _legacy._REFERENCE_FINALIZE_WORLD_LUNAR_ELECTRO_EXPIRY
)
record_stellar_superconduct_application = (
    _legacy._REFERENCE_RECORD_STELLAR_SUPERCONDUCT_APPLICATION
)
replace_special_reaction = _legacy._REFERENCE_REPLACE_SPECIAL_REACTION
replace_stellar_swirl_packet = _legacy._REFERENCE_REPLACE_STELLAR_SWIRL_PACKET
stellar_swirl_detonation = _legacy._REFERENCE_STELLAR_SWIRL_DETONATION
copy_runtime = _legacy._copy_special_runtime_for_reference
calculate_weighted_special_damage = _legacy._REFERENCE_CALCULATE_WEIGHTED_SPECIAL_DAMAGE
calculate_direct_special_damage = _legacy._REFERENCE_CALCULATE_DIRECT_SPECIAL_DAMAGE
resolve_special_damage_event = _legacy._REFERENCE_RESOLVE_SPECIAL_DAMAGE_EVENT


__all__ = [
    "admit_lunar_electro_tick",
    "advance_world_pre_expiry",
    "calculate_direct_special_damage",
    "calculate_weighted_special_damage",
    "copy_runtime",
    "finalize_world_lunar_electro_expiry",
    "record_stellar_superconduct_application",
    "replace_special_reaction",
    "replace_stellar_swirl_packet",
    "resolve_special_damage_event",
    "stellar_swirl_detonation",
]
