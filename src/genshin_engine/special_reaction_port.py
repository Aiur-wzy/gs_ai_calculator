"""Reviewed public helpers shared by mechanics and special reactions.

This boundary prevents the mechanics coordinator from importing private
implementation functions from :mod:`genshin_engine.special_reactions`.
Concrete reaction-family handlers can move without changing their consumers.
"""

from __future__ import annotations

from genshin_engine import special_reactions as _special

LUNAR_STELLAR_WEIGHTS = _special.LUNAR_STELLAR_WEIGHTS
SpecialReactionPending = _special.SpecialReactionPending


def validate_stellar_swirl_role_provenance(*args, **kwargs):
    return _special._validate_stellar_swirl_role_provenance(*args, **kwargs)


def weighted_outcome_ranking(*args, **kwargs):
    return _special._weighted_outcome_ranking(*args, **kwargs)


def capture_direct_damage_runtime(runtime):
    """Freeze the existing direct-delivery transaction basis without executing it."""

    return runtime.transaction_snapshot()


def restore_direct_damage_runtime(runtime, snapshot):
    """Restore only the existing direct-delivery transaction scope."""

    return runtime.restore_transaction(snapshot)


def reaction_config(runtime, family):
    """Read the exact runtime-bound family config, including canonical copies."""
    return runtime._config(family)


def reaction_profile(runtime, family):
    """Project the bound profile through the runtime's existing audit owner."""
    return runtime._profile_audit(family)


def lunar_electro_tick_request(runtime, **kwargs):
    """Derive one cloud phase request without advancing or committing a runtime."""
    return runtime._lunar_electro_tick_request(**kwargs)


def stellar_superconduct_ordinary_bonus(layers):
    """Project the accepted settlement bonus without adding another formula."""
    return _special.SpecialReactionRuntime._ordinary_bonus(layers)


validate_direct_lunar_damage_profile = _special.validate_direct_lunar_damage_profile
validate_direct_stellar_damage_profile = _special.validate_direct_stellar_damage_profile
validate_special_reaction_model = _special.validate_special_reaction_model


__all__ = [
    "LUNAR_STELLAR_WEIGHTS",
    "SpecialReactionPending",
    "capture_direct_damage_runtime",
    "lunar_electro_tick_request",
    "reaction_config",
    "reaction_profile",
    "restore_direct_damage_runtime",
    "stellar_superconduct_ordinary_bonus",
    "validate_direct_lunar_damage_profile",
    "validate_direct_stellar_damage_profile",
    "validate_special_reaction_model",
    "validate_stellar_swirl_role_provenance",
    "weighted_outcome_ranking",
]
