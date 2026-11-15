"""Closed artifacts for one World-owned Star-Swirl application transaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from genshin_engine.elemental import ElementalRuntime
    from genshin_engine.special_reaction_actual import SpecialReactionRuntime


@dataclass(frozen=True, slots=True)
class OrdinaryStarPreview:
    runtime: ElementalRuntime
    target_event_id: str
    target_state_before: dict
    target_identity_before: dict
    attempt: dict
    reference_receipt: dict
    previewed: dict


@dataclass(frozen=True, slots=True)
class StellarSpecialRoutes:
    routes: list[dict]
    generated: list[dict]
    layer_event: dict | None


@dataclass(frozen=True, slots=True)
class StellarRoutedTargetProof:
    receipt: dict
    generated: list[dict]
    state_after: dict
    profiles_after_registration: dict


@dataclass(frozen=True, slots=True)
class StellarTargetApplicationPlan:
    pair: dict
    target_id: str
    target_event_id: str
    target_state_before: dict
    target_identity_before: dict
    request: dict
    attempt: dict
    routes: list[dict]
    expected_commit_receipt: dict
    expected_commit_generated: list[dict]
    expected_target_state_after: dict
    expected_profiles_after_registration: dict
    routed_generated: list[dict]
    layer_event: dict | None


@dataclass(frozen=True, slots=True)
class StellarWorldBatchPlan:
    candidate: SpecialReactionRuntime
    pairs: tuple[dict, ...]
    paired_event_ids: frozenset[str]
    target_ids: tuple[str, ...]
    target_applications: tuple[StellarTargetApplicationPlan, ...]
