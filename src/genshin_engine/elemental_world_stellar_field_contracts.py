"""Readonly stellar field World contracts; no transaction authority."""

from __future__ import annotations

import copy
import math
from typing import Any

from genshin_engine.constants import stellar_superconduct_factor
from genshin_engine.elemental_world_reaction_port import WorldReactionReadPort
from genshin_engine.rotation_port import TICKS
from genshin_engine.special_reaction_actual import SpecialReactionPending
from genshin_engine.special_reaction_port import (
    reaction_profile,
    stellar_superconduct_ordinary_bonus,
)
from genshin_engine.special_reaction_reference import (
    advance_world_pre_expiry as _REFERENCE_ADVANCE_WORLD_PRE_EXPIRY,
)
from genshin_engine.special_reaction_reference import (
    copy_runtime as _copy_special_runtime_for_reference,
)
from genshin_engine.special_state_observation import (
    canonical_stellar_deadline_rows,
    stellar_clock_observation_matches,
)


def validate_stellar_superconduct_field_state(
    world: WorldReactionReadPort,
    state: Any,
    *,
    area_id: str,
    phase: str,
    boundary_tick: int,
) -> None:
    """Reject malformed field state before it can become a world baseline."""
    keys = {
        "stellar_superconduct_active",
        "stellar_superconduct_layers",
        "stellar_superconduct_settled_layers",
        "stellar_superconduct_factor",
        "stellar_superconduct_ordinary_bonus",
        "stellar_superconduct_physical_res_delta",
        "stellar_superconduct_buff_expires_at",
        "stellar_superconduct_expires_at",
        "stellar_superconduct_next_settlement",
        "stellar_superconduct_origin_applications",
        "stellar_superconduct_atomic_parents",
    }
    valid = type(state) is dict and set(state) == keys
    if valid:
        active = state["stellar_superconduct_active"]
        layers = state["stellar_superconduct_layers"]
        settled = state["stellar_superconduct_settled_layers"]
        factor = state["stellar_superconduct_factor"]
        ordinary_bonus = state["stellar_superconduct_ordinary_bonus"]
        physical_delta = state["stellar_superconduct_physical_res_delta"]
        deadlines = (
            state["stellar_superconduct_buff_expires_at"],
            state["stellar_superconduct_expires_at"],
            state["stellar_superconduct_next_settlement"],
        )
        origins = state["stellar_superconduct_origin_applications"]
        parents = state["stellar_superconduct_atomic_parents"]
        valid = (
            type(active) is bool
            and type(layers) is int and 0 <= layers <= 12
            and type(settled) is int and 0 <= settled <= 12
            and type(factor) in (int, float) and math.isfinite(factor)
            and factor >= 0
            and type(ordinary_bonus) in (int, float)
            and math.isfinite(ordinary_bonus)
            and type(physical_delta) in (int, float)
            and math.isfinite(physical_delta)
            and all(value is None or type(value) is int
                    for value in deadlines)
            and type(origins) is list
            and type(parents) is list
            and all(isinstance(value, str) and value
                    for value in (*origins, *parents))
            and len(origins) == len(set(origins))
            and len(parents) == len(set(parents))
        )
        if valid:
            buff_expires = state[
                "stellar_superconduct_buff_expires_at"]
            valid = factor == stellar_superconduct_factor(settled)
            if buff_expires is None:
                valid = (
                    valid
                    and settled == 0
                    and factor == 1.0
                    and ordinary_bonus == 0.0
                )
            else:
                valid = (
                    valid
                    and ordinary_bonus
                    == stellar_superconduct_ordinary_bonus(settled)
                )
        if valid and active:
            valid = (
                type(state["stellar_superconduct_expires_at"]) is int
                and type(state[
                    "stellar_superconduct_next_settlement"]) is int
                and physical_delta == -.4
                and len(origins) == 1
                and (
                    len(parents) == layers
                    if layers < 12 else len(parents) >= 12
                )
            )
        elif valid:
            valid = (
                layers == 0
                and state["stellar_superconduct_expires_at"] is None
                and state["stellar_superconduct_next_settlement"] is None
                and physical_delta == 0
                and origins == []
                and parents == []
            )
    if not valid:
        raise world.reaction_pending(
            "world_stellar_superconduct_deadline_invalid",
            "星超导领域截止状态不是可核验的有限结构。",
            {"boundary_tick": boundary_tick, "area_id": area_id,
             "phase": phase, "state": copy.deepcopy(state)},
        )


def freeze_stellar_superconduct_deadlines(
    world: WorldReactionReadPort,
    time_tick: int,
) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    """Freeze Star-Superconduct deadline rows and their sole legal post-state.

    The public producer mutates the shared area field while emitting a
    settlement or expiry row.  A consumer cannot infer completeness from
    the returned batch itself, because dropping a row can otherwise make
    an empty subset look valid.  Drive an isolated import-time reference
    runtime first, then require the live producer and consumer to match
    its complete rows and post-state exactly.
    """
    if world.special_runtime is None:
        return {}, {}, {}
    if not world.special_runtime.stellar_field_by_area:
        # Keep a pure Lunar or Star-Swirl world independent from the
        # Star-Superconduct reference runtime and its state validators.
        return {}, {}, {}
    fields_before = copy.deepcopy(
        world.special_runtime.stellar_field_by_area)
    for area_id, state in fields_before.items():
        world.validate_stellar_superconduct_field_state(
            state, area_id=area_id, phase="before",
            boundary_tick=time_tick)

    reference_runtime = _copy_special_runtime_for_reference(
        world.special_runtime)
    try:
        reference_due = _REFERENCE_ADVANCE_WORLD_PRE_EXPIRY(
            reference_runtime, time_tick)
    except SpecialReactionPending as exc:
        raise world.reaction_pending_from_runtime(exc) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise world.reaction_pending(
            "world_stellar_superconduct_deadline_invalid",
            "推进前星超导领域无法通过隔离规范运行时推导截止合同。",
            {"boundary_tick": time_tick,
             "fields_before": fields_before},
        ) from exc

    fields_after = copy.deepcopy(
        reference_runtime.stellar_field_by_area)
    for area_id, state in fields_after.items():
        world.validate_stellar_superconduct_field_state(
            state, area_id=area_id, phase="reference_after",
            boundary_tick=time_tick)
    if set(fields_before) != set(fields_after):
        raise world.reaction_pending(
            "world_stellar_superconduct_deadline_invalid",
            "星超导截止推进不得创建或删除区域领域状态。",
            {"boundary_tick": time_tick,
             "before_area_ids": sorted(fields_before),
             "after_area_ids": sorted(fields_after)},
        )

    expected_rows: dict[str, dict] = {}
    for row in reference_due:
        if not world.is_stellar_superconduct_deadline_row(row):
            continue
        event_id = row.get("event_id")
        area_id = row.get("area_id")
        if (not isinstance(event_id, str) or not event_id
                or event_id in expected_rows
                or not isinstance(area_id, str) or not area_id
                or area_id not in fields_before
                or row.get("time_tick") != time_tick
                or row.get("t") != time_tick / TICKS
                or row.get("target_id") is not None
                or row.get("profile")
                != reaction_profile(reference_runtime,
                    "stellar_superconduct")
                or row.get("status") != "executed"):
            raise world.reaction_pending(
                "world_stellar_superconduct_deadline_invalid",
                "隔离规范运行时生成了无法审计的星超导截止状态行。",
                {"boundary_tick": time_tick,
                 "event": copy.deepcopy(row)},
            )
        expected_rows[event_id] = copy.deepcopy(row)
    return fields_before, fields_after, expected_rows


def consume_stellar_superconduct_deadline_batch(
    world: WorldReactionReadPort,
    events: list[dict],
    *,
    expected_rows: dict[str, dict],
    time_tick: int,
    source: str,
) -> list[dict]:
    """Publish reviewed shared-field state rows without touching targets."""
    if source != "deadline":
        raise world.reaction_pending(
            "world_stellar_superconduct_deadline_invalid",
            "星超导领域状态只能由世界截止推进产生。",
            {"time_tick": time_tick, "source": source},
        )
    rows_by_id = {
        row.get("event_id"): row for row in events
        if isinstance(row, dict)
    }
    if (len(rows_by_id) != len(events)
            or canonical_stellar_deadline_rows(rows_by_id)
            != canonical_stellar_deadline_rows(expected_rows)):
        raise world.reaction_pending(
            "world_stellar_superconduct_deadline_invalid",
            "星超导截止批次与推进前隔离推导的完整状态行不一致。",
            {"time_tick": time_tick,
             "expected_event_ids": sorted(expected_rows),
             "returned_event_ids": sorted(
                 value for value in rows_by_id if isinstance(value, str))},
        )
    for row in events:
        if row.get("operation") != "settle_four_second_window":
            continue
        field = world.special_runtime.stellar_field_by_area[row["area_id"]]
        if not stellar_clock_observation_matches(row, field):
            raise world.reaction_pending(
                "world_stellar_superconduct_deadline_invalid",
                "星超导结算的只读时钟观察与已提交的调度时钟不一致。",
                {"time_tick": time_tick, "event_id": row.get("event_id"),
                 "next_settlement_at": row.get("next_settlement_at")},
            )
    return copy.deepcopy(events)

