"""Public production boundary for the current special-reaction runtime.

Production consumers import this module; verification consumers import
``special_reaction_reference``.  Keeping the boundaries separate prevents the
canonical reference graph from becoming a runtime fallback.
"""

from __future__ import annotations

from genshin_engine.special_reactions import (
    DIRECT_LUNAR_ELEMENTS,
    REQUIRED_CAPABILITY_TAGS,
    REVIEWED_SPECIAL_REACTION_PROFILES,
    SpecialReactionPending,
    SpecialReactionRuntime,
    validate_direct_lunar_damage_profile,
    validate_direct_stellar_damage_profile,
    validate_special_reaction_model,
)

__all__ = [
    "DIRECT_LUNAR_ELEMENTS",
    "REQUIRED_CAPABILITY_TAGS",
    "REVIEWED_SPECIAL_REACTION_PROFILES",
    "SpecialReactionPending",
    "SpecialReactionRuntime",
    "validate_direct_lunar_damage_profile",
    "validate_direct_stellar_damage_profile",
    "validate_special_reaction_model",
]
