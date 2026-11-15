"""Target-preview proof port. World retains real commit and publication."""

from __future__ import annotations

from typing import Protocol

from genshin_engine.elemental_world_target_proof_port import WorldTargetProofPort


class WorldStarPlanningPort(WorldTargetProofPort, Protocol):
    def bind_stellar_layer_to_target_receipt(self, *args, **kwargs) -> None: ...

    def stellar_swirl_pairs(self, events, *, time_tick: int, source: str): ...

    def validate_stellar_swirl_passthrough(self, event, *, time_tick: int, source: str) -> None: ...
