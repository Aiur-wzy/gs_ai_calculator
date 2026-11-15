"""Read/diagnostic port for family contracts; no commit or publication API.

The World remains the only owner of target roots, generated-event publication,
clock advancement and rollback. Family validators can inspect a snapshot,
derive expected rows, select registered targets and create domain diagnostics.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from genshin_engine.elemental import ElementalRuntime
    from genshin_engine.special_reaction_actual import SpecialReactionRuntime


class WorldReactionReadPort(Protocol):
    special_runtime: SpecialReactionRuntime | None

    def get_target(self, target_id: str) -> ElementalRuntime: ...

    def targets_in_area(self, area_id: str) -> tuple[str, ...]: ...

    def reaction_pending(self, code: str, message: str,
                         details: dict | None = None) -> Exception: ...

    def reaction_pending_from_runtime(self, exc: Exception) -> Exception: ...

    def is_stellar_superconduct_deadline_row(self, event: dict) -> bool: ...

    def validate_stellar_superconduct_field_state(
        self, state: Any, *, area_id: str, phase: str, boundary_tick: int,
    ) -> None: ...
