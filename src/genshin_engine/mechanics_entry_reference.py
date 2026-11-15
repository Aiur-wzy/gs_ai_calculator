"""Public verification-only boundary for the version-pinned Mechanics entry."""

from __future__ import annotations

from genshin_engine import mechanics as _owner

execute_mechanics = _owner._REFERENCE_EXECUTE_MECHANICS_ENTRY_V1

__all__ = ["execute_mechanics"]
