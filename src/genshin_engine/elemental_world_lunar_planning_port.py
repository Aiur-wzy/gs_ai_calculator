"""Lunar cloud admission proof contract; no target-commit capability."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from genshin_engine.elemental_world_target_proof_port import WorldTargetProofPort

if TYPE_CHECKING:
    from genshin_engine.elemental import ElementalRuntime


class WorldLunarPlanningPort(WorldTargetProofPort, Protocol):
    def validate_lunar_electro_tick(self, tick, *, boundary_tick,
                                   seen_target_areas) -> ElementalRuntime: ...

    def validate_lunar_electro_admission_contract(self, tick, admitted, aura_snapshot) -> None: ...
