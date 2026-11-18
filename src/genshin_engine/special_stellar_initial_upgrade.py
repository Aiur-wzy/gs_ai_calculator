"""Optional sourced creation benefit; never throttles normal reaction counting."""
from __future__ import annotations


def apply_initial_vortex_upgrade(vortex, mechanics, time, seconds_to_ticks):
    count = mechanics.get("initial_vortex_count", 1)
    ready_at = vortex.get("stellar_swirl_initial_upgrade_ready_at", 0)
    if count > 1 and time >= ready_at:
        vortex["stellar_swirl_count"] = count
        vortex["stellar_swirl_level"] = 2 if count >= mechanics["upgrade_threshold"] else 1
        vortex["stellar_swirl_initial_upgrade_ready_at"] = time + seconds_to_ticks(
            mechanics.get("initial_upgrade_cooldown_seconds", 0), "initial_upgrade_cooldown_seconds")
