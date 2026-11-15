"""Public target-side world-route operations without owning another runtime."""

from __future__ import annotations


def preview_reaction_routes(runtime, **attempt):
    return runtime._preview_with_world_reaction_routes(**attempt)


def commit_reaction_routes(runtime, *, reaction_routes, **attempt):
    return runtime._attempt_with_world_reaction_routes(reaction_routes=reaction_routes, **attempt)


def lunar_electro_aura_snapshot_at(runtime, time_tick):
    return runtime._lunar_electro_aura_snapshot_at(time_tick)
