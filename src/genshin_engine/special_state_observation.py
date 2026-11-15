"""Narrow compatibility for new read-only stellar clock observations.

Frozen reaction references retain their original rows. Only the explicitly
named observation on a settlement row is excluded from canonical comparison;
all state, formula, identity and other trace fields remain exact.
"""

from __future__ import annotations

import math

from genshin_engine.rotation_port import TICKS


def canonical_stellar_deadline_row(row):
    value = dict(row)
    if (value.get("type") == "special_reaction_state"
            and value.get("reaction_type") == "stellar_superconduct"
            and value.get("operation") == "settle_four_second_window"):
        value.pop("next_settlement_at", None)
    return value


def canonical_stellar_deadline_rows(rows):
    return {event_id: canonical_stellar_deadline_row(row) for event_id, row in rows.items()}


def canonical_stellar_trace(rows):
    return [canonical_stellar_deadline_row(row) for row in rows]


def canonical_stellar_runtime_snapshot(snapshot):
    return {**snapshot, "trace": canonical_stellar_trace(snapshot["trace"])}


def stellar_clock_observation_matches(row, field):
    """Compare an optional observation with the already committed scheduler."""
    if "next_settlement_at" not in row:
        return True  # Legacy row: unknown, never inferred from Buff expiry.
    value = row["next_settlement_at"]
    expected_tick = field["stellar_superconduct_next_settlement"]
    if expected_tick is None:
        return value is None
    return (type(value) in (int, float) and math.isfinite(value)
            and value == expected_tick / TICKS)
