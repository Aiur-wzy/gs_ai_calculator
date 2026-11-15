"""Common target and shared-state proof reads; no world mutation methods."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from genshin_engine.elemental_world_reaction_port import WorldReactionReadPort

if TYPE_CHECKING:
    from genshin_engine.elemental import ElementalRuntime
    from genshin_engine.special_reaction_actual import SpecialReactionRuntime


class WorldTargetProofPort(WorldReactionReadPort, Protocol):
    def target_commit_snapshot(self, runtime: ElementalRuntime) -> dict: ...

    def target_identity_snapshot(self, runtime: ElementalRuntime) -> dict: ...

    def reference_target_copy(self, runtime: ElementalRuntime,
                              *, preserve_trace: bool = True) -> ElementalRuntime: ...

    def special_commit_snapshot(self, runtime: SpecialReactionRuntime) -> dict: ...
