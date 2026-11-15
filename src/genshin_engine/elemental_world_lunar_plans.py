"""Closed target admissions for a World-owned Lunar-Charged boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from genshin_engine.elemental import ElementalRuntime
    from genshin_engine.special_reaction_actual import SpecialReactionRuntime


@dataclass(frozen=True, slots=True)
class LunarElectroTargetAdmission:
    tick: dict
    target: ElementalRuntime
    admitted: dict


@dataclass(frozen=True, slots=True)
class LunarElectroWorldBatchPlan:
    candidate: SpecialReactionRuntime
    admissions: tuple[LunarElectroTargetAdmission, ...]
