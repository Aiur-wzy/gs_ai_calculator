"""Compose read-only database evidence and App-owned materialization capabilities."""

from __future__ import annotations

from genshin_db.runtime_queries import runtime_capabilities

from .action_dispatch_materialization import (
    action_dispatch_materialization_capabilities,
)


def app_runtime_capabilities() -> dict:
    result = runtime_capabilities()
    result["action_dispatch_materialization"] = action_dispatch_materialization_capabilities()
    return result
