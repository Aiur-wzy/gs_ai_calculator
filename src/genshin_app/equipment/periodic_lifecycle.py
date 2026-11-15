"""Bounded deterministic projection for periodic schedules and lifecycles.

This module deliberately knows nothing about characters or skills.  Callers
must translate reviewed gameplay data and confirmed axis events into this
small integer-microsecond contract.  A policy name alone never creates a
control event.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field

CONTRACT = "periodic-lifecycle-projection-v1"
RESOURCE_EVENT_CONTRACT = "rotation-resource-event-v1"
from .periodic_contract import DERIVED_COUNT_POLICIES
from .periodic_contract import STRICT_COUNT_POLICY as DERIVED_COUNT_POLICY

MAX_CHANNELS = 32
MAX_CHANNEL_OCCURRENCES = 256
MAX_CONTROLS = 512
MAX_GENERATIONS = 256
MAX_TOTAL_OCCURRENCES = 40000
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
ORDER_MIN = -(10**9)
ORDER_MAX = 10**9
RESOURCE_EVENT_TYPES = frozenset(("energy_spawn", "energy_direct", "cooldown_change"))


class PeriodicLifecycleError(ValueError):
    """The requested projection is ambiguous, unbounded, or unsupported."""


def periodic_lifecycle_capabilities() -> dict:
    """Describe the projector separately from the currently wired App subset."""
    return {
        "contract": CONTRACT,
        "time_unit": "integer_microsecond",
        "schedule_kinds": ["fixed", "equal_interval", "explicit_offsets"],
        "standalone_projector": {
            "refresh_policies": ["none", "replace_previous", "reset_duration",
                                 "extend_duration", "ignore_new"],
            "pause_resume_policies": ["none", "pause_preserve_phase",
                                      "pause_reset_phase"],
            "off_field_policies": ["continue", "stop"],
            "control_basis": "confirmed bounded axis events only",
            "off_field_basis": "confirmed switch positions with time and order",
        },
        "app_execution_subset": {
            "path": "agent_execution_runtime.periodic_channel_v2",
            "schedule_kinds": ["fixed", "equal_interval", "explicit_offsets"],
            "refresh_policies": ["none", "replace_previous", "reset_duration",
                                 "extend_duration", "ignore_new"],
            "pause_resume_policies": ["none", "pause_preserve_phase",
                                      "pause_reset_phase"],
            "off_field_policies": ["continue", "stop"],
            "control_operations": ["replace", "reset_duration", "extend_duration",
                                   "pause", "resume", "cancel"],
            "projection_role": "generation/cancellation/time/order schedule proof",
            "finite_window": "App自动保存边界内事件及active/paused有界未来候选；不把窗口边界当到期，不证明未来交付或稳态",
            "delivery_proof": "resolved later per compiled contact; projector does not self-certify it",
        },
        "standalone_only_until_app_input_exists": [],
        "resource_delivery": {
            "reference_contract": RESOURCE_EVENT_CONTRACT,
            "status": "unavailable",
            "reason": "no closed App consumer",
            "materialization_complete": False,
        },
        "limits": {"channels": MAX_CHANNELS,
                   "occurrences_per_channel_phase": MAX_CHANNEL_OCCURRENCES,
                   "controls": MAX_CONTROLS, "generations": MAX_GENERATIONS,
                   "total_occurrences": MAX_TOTAL_OCCURRENCES,
                   "receipt_bytes": MAX_RECEIPT_BYTES},
    }


def _fail(message: str) -> None:
    raise PeriodicLifecycleError(message)


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _fail(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _identifier(value, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        _fail(f"{name} must be a non-empty identifier of at most 128 characters")
    return value


def _position(row, name: str) -> tuple[int, int]:
    return (_integer(row.get("time_ticks"), name + ".time_ticks", 0, 10**15),
            _integer(row.get("order"), name + ".order", ORDER_MIN, ORDER_MAX))


def _delivery(value, name: str) -> dict:
    if not isinstance(value, dict) or set(value) - {"kind", "contract", "event"}:
        _fail(name + " must be a closed delivery object")
    kind = value.get("kind")
    if kind in ("damage", "application"):
        if set(value) != {"kind"}:
            _fail(name + " damage/application delivery accepts no inferred payload")
        return {"kind": kind}
    if kind != "resource":
        _fail(name + " has an unsupported delivery kind")
    if value.get("contract") != RESOURCE_EVENT_CONTRACT:
        _fail(name + " resource delivery does not map to a supported closed event contract")
    event = value.get("event")
    if not isinstance(event, dict) or event.get("type") not in RESOURCE_EVENT_TYPES:
        _fail(name + " resource delivery must name a supported rotation resource event")
    if set(event) != {"type", "template_id"}:
        _fail(name + " resource event must be an exact type/template reference")
    _identifier(event.get("template_id"), name + ".event.template_id")
    _fail(name + " has no closed App consumer; resource projection remains unavailable")


def _orders(schedule: dict, count: int, name: str) -> list[int]:
    values = schedule.get("orders")
    if values is None:
        value = _integer(schedule.get("order", 0), name + ".order", ORDER_MIN, ORDER_MAX)
        return [value] * count
    if (not isinstance(values, list) or len(values) != count
            or any(type(value) is not int or not ORDER_MIN <= value <= ORDER_MAX for value in values)):
        _fail(name + ".orders must contain one bounded integer per occurrence")
    return list(values)


def _schedule(channel: dict, duration: int, name: str) -> tuple[list[tuple[int, int]], dict | None]:
    schedule = channel.get("schedule")
    if not isinstance(schedule, dict):
        _fail(name + ".schedule must be an object")
    kind = schedule.get("kind")
    derived = None
    if kind == "fixed":
        allowed = {"kind", "offset_ticks", "order", "orders"}
        if set(schedule) - allowed:
            _fail(name + ".schedule contains unsupported fixed fields")
        offsets = [_integer(schedule.get("offset_ticks"), name + ".offset_ticks", 0, duration)]
    elif kind == "explicit_offsets":
        allowed = {"kind", "offset_ticks", "order", "orders"}
        if set(schedule) - allowed:
            _fail(name + ".schedule contains unsupported explicit-offset fields")
        offsets = schedule.get("offset_ticks")
        if (not isinstance(offsets, list) or not 1 <= len(offsets) <= MAX_CHANNEL_OCCURRENCES
                or any(type(value) is not int or not 0 <= value <= duration for value in offsets)
                or offsets != sorted(offsets) or len(offsets) != len(set(offsets))):
            _fail(name + ".offset_ticks must be a unique ordered bounded sequence")
        offsets = list(offsets)
    elif kind == "equal_interval":
        if duration == 0:
            _fail(name + " zero-duration lifecycle cannot use equal-interval scheduling")
        allowed = {"kind", "first_offset_ticks", "interval_ticks", "count", "derive_count", "order", "orders"}
        if set(schedule) - allowed:
            _fail(name + ".schedule contains unsupported equal-interval fields")
        first = _integer(schedule.get("first_offset_ticks"), name + ".first_offset_ticks", 0, duration)
        interval = _integer(schedule.get("interval_ticks"), name + ".interval_ticks", 1, 10**15)
        count = schedule.get("count")
        if count is None:
            if schedule.get("derive_count") not in DERIVED_COUNT_POLICIES:
                _fail(name + ".count=null requires the inclusive lifecycle derivation policy")
            if first > duration or (schedule['derive_count'] == DERIVED_COUNT_POLICY and (duration - first) % interval):
                _fail("derived periodic count does not close exactly at the effective lifecycle end")
            count = (duration - first) // interval + 1
            derived = {"first_offset_ticks": first, "interval_ticks": interval, 'policy': schedule['derive_count']}
        else:
            _integer(count, name + ".count", 1, MAX_CHANNEL_OCCURRENCES)
            if "derive_count" in schedule:
                _fail(name + ".derive_count must be absent when count is explicit")
        if not 1 <= count <= MAX_CHANNEL_OCCURRENCES:
            _fail(name + " derived occurrence count is outside 1..256")
        offsets = [first + index * interval for index in range(count)]
        if derived is None and offsets[-1] > duration:
            _fail(name + " explicit equal-interval occurrences exceed the lifecycle")
    else:
        _fail(name + ".schedule kind is unsupported")
    if derived is not None and schedule.get("orders") is not None:
        values = schedule["orders"]
        if (not isinstance(values, list) or not len(offsets) <= len(values) <= MAX_CHANNEL_OCCURRENCES
                or any(type(value) is not int or not ORDER_MIN <= value <= ORDER_MAX
                       for value in values)):
            _fail(name + ".schedule.orders must cover the base derived schedule and stay within 256")
        orders = list(values[:len(offsets)])
        derived["orders"] = list(values)
    else:
        orders = _orders(schedule, len(offsets), name + ".schedule")
        if derived is not None:
            derived["order"] = orders[0]
    return list(zip(offsets, orders, strict=True)), derived


def _channels(rows, duration: int) -> list[dict]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CHANNELS:
        _fail("channels must contain 1..32 entries")
    result, ids = [], set()
    for index, row in enumerate(rows):
        name = f"channels[{index}]"
        if not isinstance(row, dict) or set(row) != {"channel_id", "schedule", "deliveries"}:
            _fail(name + " must contain channel_id, schedule, and deliveries")
        channel_id = _identifier(row["channel_id"], name + ".channel_id")
        if channel_id in ids:
            _fail("channel_id values must be unique")
        ids.add(channel_id)
        deliveries = row["deliveries"]
        # A caller may use the projector purely as a schedule/lifecycle proof.
        # Empty deliveries make that boundary explicit; they must not be
        # interpreted as proof that a landed hit, damage, or application was
        # actually delivered by a downstream compiler.
        if (not isinstance(deliveries, list) or len(deliveries) > 3
                or len({item.get("kind") for item in deliveries if isinstance(item, dict)}) != len(deliveries)):
            _fail(name + ".deliveries must contain at most one declaration per delivery kind")
        schedule, derived = _schedule(row, duration, name)
        result.append({"channel_id": channel_id, "schedule": schedule, "derived": derived,
                       "deliveries": [_delivery(value, f"{name}.deliveries[{i}]")
                                      for i, value in enumerate(deliveries)]})
    return result


def _intervals(rows, window_end: int) -> list[dict]:
    if not isinstance(rows, list) or len(rows) > MAX_CONTROLS:
        _fail("on_field_intervals must be a bounded list")
    result = []
    for index, row in enumerate(rows):
        required = {"start_ticks", "start_order", "end_ticks", "end_order",
                    "source_event_id", "status"}
        if not isinstance(row, dict) or set(row) != required:
            _fail(f"on_field_intervals[{index}] has an open or incomplete shape")
        if row["status"] != "confirmed":
            _fail("off-field stop requires confirmed frontline intervals")
        start = _integer(row["start_ticks"], "on_field start", 0, window_end)
        end = _integer(row["end_ticks"], "on_field end", 0, window_end)
        start_position = (start, _integer(
            row["start_order"], "on_field start order", ORDER_MIN, ORDER_MAX))
        end_position = (end, _integer(
            row["end_order"], "on_field end order", ORDER_MIN, ORDER_MAX))
        source_event_id = _identifier(
            row["source_event_id"], "frontline boundary source event")
        if start_position >= end_position or result and start_position < result[-1]["end"]:
            _fail("frontline intervals must be ordered, non-overlapping, and non-empty")
        result.append({"start": start_position, "end": end_position,
                       "source_event_id": source_event_id})
    return result


@dataclass
class _Generation:
    generation: int
    trigger_id: str
    root_action_id: str
    started: tuple[int, int]
    base_duration: int
    lifetime_limit: int
    active_elapsed: int = 0
    last_ticks: int = 0
    last_order: int = ORDER_MIN
    paused: bool = False
    phase: int = 1
    phase_started_active: int = 0
    candidates: list[dict] = field(default_factory=list)
    delivered: list[dict] = field(default_factory=list)
    closed: bool = False


def _candidate_rows(channels: list[dict], phase: int, active_base: int = 0) -> list[dict]:
    result = []
    for channel in channels:
        for ordinal, (offset, order) in enumerate(channel["schedule"], 1):
            result.append({"channel_id": channel["channel_id"], "ordinal": ordinal,
                "phase": phase, "due_active_ticks": active_base + offset, "order": order,
                "deliveries": copy.deepcopy(channel["deliveries"])})
    result.sort(key=lambda row: (row["due_active_ticks"], row["order"], row["channel_id"], row["ordinal"]))
    # The closed channel schedule may contain a same-tick placeholder order.
    # Per-occurrence reviewed overrides are applied immediately after this
    # construction and are the authority that disambiguates those contacts.
    # Reject duplicates only after that merge in ``_apply_occurrence_orders``.
    return result


def _occurrence_order_map(rows) -> dict[tuple[str, int, str, int], int]:
    rows = list(rows)
    if len(rows) > MAX_TOTAL_OCCURRENCES:
        _fail("occurrence order overrides exceed the 40000 entry budget")
    result = {}
    for index, row in enumerate(rows):
        required = {"trigger_id", "phase", "channel_id", "ordinal", "order"}
        if not isinstance(row, dict) or set(row) != required:
            _fail(f"occurrence_orders[{index}] has an open or incomplete shape")
        key = (_identifier(row["trigger_id"], "occurrence trigger"),
               _integer(row["phase"], "occurrence phase", 1, MAX_GENERATIONS),
               _identifier(row["channel_id"], "occurrence channel"),
               _integer(row["ordinal"], "occurrence ordinal", 1, MAX_CHANNEL_OCCURRENCES))
        if key in result:
            _fail("occurrence order override identity must be unique")
        result[key] = _integer(row["order"], "occurrence order", ORDER_MIN, ORDER_MAX)
    return result


def _apply_occurrence_orders(candidates, order_map, trigger_id, phase):
    for row in candidates:
        key = (trigger_id, phase, row["channel_id"], row["ordinal"])
        if key in order_map:
            row["order"] = order_map.pop(key)
    candidates.sort(key=lambda row: (
        row["due_active_ticks"], row["order"], row["channel_id"], row["ordinal"]))
    positions = [(row["due_active_ticks"], row["order"]) for row in candidates]
    if len(positions) != len(set(positions)):
        _fail("resolved occurrences at the same active microsecond require distinct order")


def _ensure_derived_candidates(generation: _Generation, channels: list[dict],
                               order_map: dict, target_limit: int) -> None:
    """Lazily extend derived channels to one generation's actual lifetime.

    A global worst-case horizon is both unsafe and overly conservative: a
    reset belonging to one generation must not inflate every other generation.
    This routine is called only after an explicit control changes the active
    lifetime, and therefore expands exactly the affected generation/phase.
    """
    existing = {(row["phase"], row["channel_id"], row["ordinal"])
                for row in generation.candidates + generation.delivered}
    added = []
    for channel in channels:
        derived = channel["derived"]
        if derived is None:
            continue
        first = generation.phase_started_active + derived["first_offset_ticks"]
        interval = derived["interval_ticks"]
        count = ((target_limit - first) // interval + 1
                 if first <= target_limit else 0)
        if count == 0 and derived['policy'] != DERIVED_COUNT_POLICY:
            continue  # A reset phase can have less active time than its first pulse.
        if not 1 <= count <= MAX_CHANNEL_OCCURRENCES:
            _fail("derived occurrence count is outside 1..256")
        order_sequence = derived.get("orders")
        if order_sequence is not None and count > len(order_sequence):
            _fail("derived occurrence order sequence does not cover the extended lifecycle")
        for ordinal in range(1, count + 1):
            identity = (generation.phase, channel["channel_id"], ordinal)
            if identity in existing:
                continue
            order = (order_sequence[ordinal - 1] if order_sequence is not None
                     else derived["order"])
            key = (generation.trigger_id, generation.phase,
                   channel["channel_id"], ordinal)
            if key in order_map:
                order = order_map.pop(key)
            added.append({"channel_id": channel["channel_id"], "ordinal": ordinal,
                "phase": generation.phase,
                "due_active_ticks": first + (ordinal - 1) * interval,
                "order": order, "deliveries": copy.deepcopy(channel["deliveries"])})
    generation.candidates.extend(added)
    generation.candidates.sort(key=lambda row: (
        row["due_active_ticks"], row["order"], row["channel_id"], row["ordinal"]))
    positions = [(row["due_active_ticks"], row["order"])
                 for row in generation.candidates]
    if len(positions) != len(set(positions)):
        _fail("resolved occurrences at the same active microsecond require distinct order")


def _receipt_row(generation: _Generation, row: dict, wall_ticks: int) -> dict:
    return {"occurrence_id": f"{generation.trigger_id}:{row['channel_id']}:{row['phase']}:{row['ordinal']}",
        "generation": generation.generation, "trigger_id": generation.trigger_id,
        "root_action_id": generation.root_action_id, "channel_id": row["channel_id"],
        "phase": row["phase"], "ordinal": row["ordinal"], "time_ticks": wall_ticks,
        "order": row["order"], "active_offset_ticks": row["due_active_ticks"],
        "deliveries": copy.deepcopy(row["deliveries"])}


def _advance(generation: _Generation, position: tuple[int, int], receipt: dict, *,
             inclusive: bool = False, reject_equal: bool = True) -> None:
    ticks, order = position
    if (ticks, order) < (generation.last_ticks, generation.last_order):
        _fail("lifecycle controls are not chronologically monotonic")
    if generation.paused:
        return
    start_active = generation.active_elapsed
    end_active = start_active + ticks - generation.last_ticks
    end_active = min(end_active, generation.lifetime_limit)
    pending = []
    for row in generation.candidates:
        due = row["due_active_ticks"]
        if due < start_active:
            _fail("periodic occurrence queue regressed behind the active clock")
        wall_ticks = generation.last_ticks + due - start_active
        due_position = (wall_ticks, row["order"])
        if reject_equal and due_position == position:
            _fail("lifecycle control and derived occurrence cannot share one time/order position")
        admitted = (due <= end_active and due <= generation.lifetime_limit
                    and (due_position < position or inclusive and due_position <= position))
        if admitted:
            delivered = _receipt_row(generation, row, wall_ticks)
            generation.delivered.append(delivered)
            receipt["occurrences"].append(delivered)
        else:
            pending.append(row)
    generation.candidates = pending
    generation.active_elapsed = end_active
    generation.last_ticks, generation.last_order = ticks, order


def _cancel(generation: _Generation, receipt: dict, reason: str, position: tuple[int, int]) -> int:
    cancelled = 0
    for row in generation.candidates:
        receipt["cancelled"].append({**_receipt_row(generation, row, position[0]),
            "reason": reason, "control_position": {"time_ticks": position[0], "order": position[1]}})
        cancelled += 1
    generation.candidates = []
    generation.closed = True
    return cancelled


def _assert_derived_closure(generation: _Generation, channels: list[dict]) -> None:
    for channel in channels:
        derived = channel["derived"]
        if derived is None or derived['policy'] != DERIVED_COUNT_POLICY:
            continue
        first = generation.phase_started_active + derived["first_offset_ticks"]
        interval = derived["interval_ticks"]
        if (first > generation.lifetime_limit
                or (generation.lifetime_limit - first) % interval):
            _fail("derived periodic count does not close exactly at the effective lifecycle end")


def _control_rows(establishments, controls, intervals, off_field_policy, window_end, *, finite_prefix=False):
    if not isinstance(establishments, list) or not 1 <= len(establishments) <= MAX_GENERATIONS:
        _fail("establishments must contain 1..256 confirmed axis events")
    if not isinstance(controls, list) or len(controls) > MAX_CONTROLS:
        _fail("controls must contain at most 512 entries")
    rows, ids = [], set()
    for index, row in enumerate(establishments):
        if not isinstance(row, dict) or set(row) != {
                "trigger_id", "root_action_id", "time_ticks", "order", "status"}:
            _fail(f"establishments[{index}] has an open or incomplete shape")
        if row["status"] != "confirmed":
            _fail("instance establishment requires a confirmed axis event")
        item = {**copy.deepcopy(row), "operation": "establish"}
        _identifier(item["root_action_id"], f"establishments[{index}].root_action_id")
        if _position(item, f"establishments[{index}]")[0] > window_end:
            _fail("instance establishment exceeds the projection window")
        rows.append(item)
    for index, row in enumerate(controls):
        if not isinstance(row, dict):
            _fail(f"controls[{index}] must be an object")
        required = {"control_id", "operation", "time_ticks", "order", "status"}
        # ``root_action_id`` is optional for direct projector callers, but the
        # App adapter supplies it for controls that start a new generation
        # (currently explicit replace).  Keeping it on the reviewed axis row
        # prevents a replacement cast from being installed under the previous
        # cast's action identity.
        optional = {"duration_ticks", "root_action_id"}
        if required - row.keys() or set(row) - required - optional:
            _fail(f"controls[{index}] has an open or incomplete shape")
        if row["status"] != "confirmed":
            _fail("lifecycle controls require confirmed axis events")
        operation = row["operation"]
        if operation not in {
                "replace", "reset_duration", "extend_duration", "pause", "resume",
                "cancel"}:
            _fail("unsupported lifecycle control operation")
        if operation == "extend_duration":
            _integer(row.get("duration_ticks"), f"controls[{index}].duration_ticks", 1, 10**15 if finite_prefix else window_end)
        elif "duration_ticks" in row:
            _fail("only extend_duration accepts duration_ticks")
        item = copy.deepcopy(row)
        item["trigger_id"] = item.pop("control_id")
        item["root_action_id"] = item.get("root_action_id", "")
        if item["root_action_id"]:
            _identifier(item["root_action_id"], f"controls[{index}].root_action_id")
        if _position(item, f"controls[{index}]")[0] > window_end:
            _fail("lifecycle control exceeds the projection window")
        rows.append(item)
    if off_field_policy == "stop":
        confirmed = _intervals(intervals, window_end)
        explicit_positions = {_position(row, row["trigger_id"]) for row in rows}
        for index, interval in enumerate(confirmed):
            if interval["start"] in explicit_positions:
                _fail("frontline interval start cannot share an explicit axis event position")
            end = interval["end"]
            next_start = (confirmed[index + 1]["start"] if index + 1 < len(confirmed)
                          else (window_end, ORDER_MAX))
            if end < next_start:
                source_id = interval["source_event_id"]
                digest = hashlib.sha256(source_id.encode("utf8")).hexdigest()[:16]
                rows.append({"trigger_id": f"off-field:{index}:{source_id[:80]}:{digest}",
                    "root_action_id": "", "operation": "off_field_stop",
                    "time_ticks": end[0], "order": end[1],
                    "status": "confirmed"})
    elif intervals:
        _intervals(intervals, window_end)
    for row in rows:
        identity = row["trigger_id"]
        _identifier(identity, "axis trigger identity")
        if identity in ids:
            _fail("axis trigger identities must be unique")
        ids.add(identity)
    rows.sort(key=lambda row: (*_position(row, row["trigger_id"]), row["trigger_id"]))
    positions = [_position(row, row["trigger_id"]) for row in rows]
    if len(positions) != len(set(positions)):
        _fail("lifecycle controls and establishments require unique time/order positions")
    return rows


def project_periodic_lifecycle(*, channels, lifecycle, establishments, controls=(),
        on_field_intervals=(), occurrence_orders=(), window_end_ticks, projection_scope="complete_lifecycle"):
    """Project one periodic profile from confirmed, finite axis facts.

    Times are exact integer microseconds.  ``pause_reset_phase`` restarts each
    channel's finite schedule on resume while keeping the remaining active
    lifetime.  ``reset_duration`` restores one full active lifetime without
    resetting the channel phase.  ``extend_duration`` adds an explicit active
    duration.  No operation is inferred from a policy alone.
    """
    window_end = _integer(window_end_ticks, "window_end_ticks", 1, 10**15)
    if projection_scope not in {"complete_lifecycle", "finite_prefix"}:
        _fail("unknown periodic projection scope")
    finite_prefix = projection_scope == "finite_prefix"
    controls = list(controls)
    on_field_intervals = list(on_field_intervals)
    order_map = _occurrence_order_map(occurrence_orders)
    if not isinstance(lifecycle, dict) or set(lifecycle) != {
            "duration_ticks", "refresh_policy", "pause_resume_policy", "off_field_policy"}:
        _fail("lifecycle must use the closed periodic-lifecycle contract")
    duration = _integer(lifecycle["duration_ticks"], "lifecycle.duration_ticks", 0, 10**15 if finite_prefix else window_end)
    refresh = lifecycle["refresh_policy"]
    pause_policy = lifecycle["pause_resume_policy"]
    off_field = lifecycle["off_field_policy"]
    if refresh not in {
            "none", "replace_previous", "reset_duration", "extend_duration",
            "ignore_new"}:
        _fail("lifecycle refresh policy is unsupported or pending")
    if pause_policy not in {"none", "pause_preserve_phase", "pause_reset_phase"}:
        _fail("lifecycle pause/resume policy is unsupported or pending")
    if off_field not in {"continue", "stop"}:
        _fail("lifecycle off-field policy is unsupported or pending")
    if duration == 0 and (refresh in {"reset_duration", "extend_duration"}
                          or pause_policy != "none"):
        _fail("zero-duration lifecycle only supports finite zero-offset delivery and optional replacement")
    normalized = _channels(channels, duration)
    confirmed_intervals = _intervals(on_field_intervals, window_end) if off_field == "stop" else []
    axis = _control_rows(establishments, controls, on_field_intervals, off_field, window_end, finite_prefix=finite_prefix)
    receipt = {"contract": CONTRACT, "status": "ready", "lifecycle": copy.deepcopy(lifecycle),
        "occurrences": [], "generations": [], "controls": [], "cancelled": [],
        "deferred": [], "skipped": [], "summary": {}}
    current = None
    generation_number = 0

    def enforce_budget():
        pending = 0 if current is None or current.closed else len(current.candidates)
        if len(receipt["occurrences"]) + len(receipt["cancelled"]) + pending > MAX_TOTAL_OCCURRENCES:
            _fail("periodic projection exceeds the 40000 occurrence budget")

    def start(row, operation):
        nonlocal current, generation_number
        generation_number += 1
        if generation_number > MAX_GENERATIONS:
            _fail("projected generations exceed 256")
        current = _Generation(generation_number, row["trigger_id"], row["root_action_id"],
            _position(row, row["trigger_id"]), duration, duration,
            last_ticks=row["time_ticks"], last_order=row["order"],
            candidates=_candidate_rows(normalized, 1))
        _apply_occurrence_orders(current.candidates, order_map, row["trigger_id"], 1)
        enforce_budget()
        if any(candidate["due_active_ticks"] == 0 and candidate["order"] <= row["order"]
               for candidate in current.candidates):
            _fail("zero-offset occurrences must be ordered after their establishment event")
        generation_receipt = {"generation": generation_number, "trigger_id": row["trigger_id"],
            "root_action_id": row["root_action_id"], "started": {"time_ticks": row["time_ticks"],
                "order": row["order"]}, "operation": operation, "status": "active"}
        receipt["generations"].append(generation_receipt)
        if off_field == "stop" and not any(
                interval["start"] <= _position(row, row["trigger_id"]) < interval["end"]
                for interval in confirmed_intervals):
            _cancel(current, receipt, "established_off_field", _position(row, row["trigger_id"]))
            generation_receipt["status"] = "stopped_off_field"
        return generation_receipt

    for control in axis:
        position = _position(control, control["trigger_id"])
        operation = control["operation"]
        if current is not None and not current.closed:
            if not current.paused:
                end_ticks = current.last_ticks + current.lifetime_limit - current.active_elapsed
                if (end_ticks, ORDER_MAX) < position:
                    end_position = (end_ticks, ORDER_MAX)
                    _advance(current, end_position, receipt, inclusive=True,
                             reject_equal=False)
                    _assert_derived_closure(current, normalized)
                    _cancel(current, receipt, "lifecycle_complete", end_position)
                    receipt["generations"][current.generation - 1]["status"] = "complete"
            if not current.closed:
                # Replacement owns a precise boundary: old occurrences at or
                # after the replacing position are cancelled.  An occurrence
                # exactly on that boundary is therefore not ambiguous and
                # must remain queued for the cancellation step below.  Other
                # controls still reject a same-position contact because they
                # do not define which side of the control receives it.
                replaces_at_boundary = (
                    operation in {"replace", "cancel"}
                    or (operation == "establish"
                        and refresh == "replace_previous")
                )
                _advance(current, position, receipt,
                         reject_equal=not replaces_at_boundary)
        active = current is not None and not current.closed
        control_receipt = {"trigger_id": control["trigger_id"], "operation": operation,
            "position": {"time_ticks": position[0], "order": position[1]},
            "outcome": None, "reason": None}
        if operation == "establish":
            if active:
                if refresh == "replace_previous":
                    _cancel(current, receipt, "replace_previous", position)
                    receipt["generations"][current.generation - 1]["status"] = "replaced"
                    control_receipt["outcome"] = "replaced_active"
                elif refresh == "reset_duration":
                    current.lifetime_limit = current.active_elapsed + duration
                    _ensure_derived_candidates(
                        current, normalized, order_map, current.lifetime_limit)
                    enforce_budget()
                    control_receipt["outcome"] = "reset_active_duration"
                    receipt["controls"].append(control_receipt)
                    continue
                elif refresh == "extend_duration":
                    current.lifetime_limit += duration
                    if current.lifetime_limit > (10**15 if finite_prefix else window_end):
                        _fail("extended active lifetime exceeds the projection window")
                    _ensure_derived_candidates(
                        current, normalized, order_map, current.lifetime_limit)
                    enforce_budget()
                    control_receipt["outcome"] = "extended_active_duration"
                    receipt["controls"].append(control_receipt)
                    continue
                elif refresh == "ignore_new":
                    control_receipt.update(
                        outcome="ignored_active", reason="ignore_new_policy")
                    receipt["skipped"].append(copy.deepcopy(control_receipt))
                    receipt["controls"].append(control_receipt)
                    continue
                else:
                    _fail("overlapping establishment has no executable refresh policy")
            start(control, "establish")
            control_receipt["outcome"] = "started_generation"
        elif operation == "replace":
            if refresh != "replace_previous":
                _fail("replace control does not match the reviewed refresh policy")
            if not active:
                control_receipt.update(outcome="inactive_noop", reason="no_active_generation")
                receipt["skipped"].append(copy.deepcopy(control_receipt))
            else:
                root = control["root_action_id"] or current.root_action_id
                _cancel(current, receipt, "replace_control", position)
                receipt["generations"][current.generation - 1]["status"] = "replaced"
                start({**control, "root_action_id": root}, "replace")
                control_receipt["outcome"] = "replaced_active"
        elif operation == "reset_duration":
            if refresh != "reset_duration":
                _fail("reset control does not match the reviewed refresh policy")
            if not active:
                control_receipt.update(outcome="inactive_noop", reason="no_active_generation")
                receipt["skipped"].append(copy.deepcopy(control_receipt))
            else:
                current.lifetime_limit = current.active_elapsed + duration
                _ensure_derived_candidates(
                    current, normalized, order_map, current.lifetime_limit)
                enforce_budget()
                control_receipt["outcome"] = "reset_active_duration"
        elif operation == "extend_duration":
            if refresh != "extend_duration":
                _fail("extend control does not match the reviewed refresh policy")
            if not active:
                control_receipt.update(outcome="inactive_noop", reason="no_active_generation")
                receipt["skipped"].append(copy.deepcopy(control_receipt))
            else:
                current.lifetime_limit += control["duration_ticks"]
                if current.lifetime_limit > (10**15 if finite_prefix else window_end):
                    _fail("extended active lifetime exceeds the projection window")
                _ensure_derived_candidates(
                    current, normalized, order_map, current.lifetime_limit)
                enforce_budget()
                control_receipt["outcome"] = "extended_active_duration"
        elif operation == "pause":
            if pause_policy == "none":
                _fail("pause control has no reviewed pause/resume policy")
            if not active or current.paused:
                _fail("pause requires one active, unpaused generation")
            current.paused = True
            control_receipt["outcome"] = "paused"
        elif operation == "resume":
            if pause_policy == "none":
                _fail("resume control has no reviewed pause/resume policy")
            if not active or not current.paused:
                _fail("resume requires one active paused generation")
            paused_ticks = position[0] - current.last_ticks
            if paused_ticks < 0:
                _fail("resume precedes pause")
            current.paused = False
            current.last_ticks, current.last_order = position
            if pause_policy == "pause_reset_phase":
                for row in current.candidates:
                    receipt["cancelled"].append({**_receipt_row(current, row, position[0]),
                        "reason": "pause_reset_phase", "control_position": {
                            "time_ticks": position[0], "order": position[1]}})
                current.phase += 1
                current.phase_started_active = current.active_elapsed
                current.candidates = _candidate_rows(normalized, current.phase, current.active_elapsed)
                _apply_occurrence_orders(
                    current.candidates, order_map, current.trigger_id, current.phase)
                _ensure_derived_candidates(
                    current, normalized, order_map, current.lifetime_limit)
                enforce_budget()
                if any(candidate["due_active_ticks"] == current.active_elapsed
                       and candidate["order"] <= position[1]
                       for candidate in current.candidates):
                    _fail("reset-phase zero-offset occurrences must be ordered after resume")
            else:
                if any(candidate["due_active_ticks"] == current.active_elapsed
                       and candidate["order"] <= position[1]
                       for candidate in current.candidates):
                    _fail("preserved-phase occurrence must be ordered after resume")
                receipt["deferred"].append({"generation": current.generation,
                    "reason": "pause_preserve_phase", "duration_ticks": paused_ticks,
                    "resume_position": {"time_ticks": position[0], "order": position[1]}})
            control_receipt["outcome"] = "resumed_reset_phase" if pause_policy == "pause_reset_phase" else "resumed_preserved_phase"
        elif operation == "cancel":
            if not active:
                control_receipt.update(
                    outcome="inactive_noop", reason="no_active_generation")
                receipt["skipped"].append(copy.deepcopy(control_receipt))
            else:
                _cancel(current, receipt, "cancel_control", position)
                receipt["generations"][current.generation - 1]["status"] = "cancelled"
                control_receipt["outcome"] = "cancelled_active"
        else:  # confirmed off-field boundary synthesized from frontline intervals
            if not active:
                control_receipt.update(outcome="inactive_noop", reason="no_active_generation")
                receipt["skipped"].append(copy.deepcopy(control_receipt))
            else:
                _cancel(current, receipt, "off_field_stop", position)
                receipt["generations"][current.generation - 1]["status"] = "stopped_off_field"
                control_receipt["outcome"] = "stopped_active"
        receipt["controls"].append(control_receipt)
        enforce_budget()
    if finite_prefix:
        receipt['projection_scope'] = 'finite_prefix'
        receipt['boundary'] = {'contract': 'periodic-lifecycle-boundary-v1',
            'time_ticks': window_end, 'status': 'inactive',
            'future_delivery_verified': False, 'steady_state_proven': False}
    if current is not None and not current.closed:
        _assert_derived_closure(current, normalized)
        if finite_prefix and (current.paused or current.last_ticks + current.lifetime_limit - current.active_elapsed > window_end):
            if not current.paused:
                _advance(current, (window_end, ORDER_MAX), receipt, inclusive=True, reject_equal=False)
            receipt['generations'][current.generation - 1]['status'] = 'paused_at_boundary' if current.paused else 'active_at_boundary'
            receipt['boundary'].update(status='paused' if current.paused else 'active',
                generation=current.generation, trigger_id=current.trigger_id,
                root_action_id=current.root_action_id, phase=current.phase,
                phase_started_active_ticks=current.phase_started_active,
                active_elapsed_ticks=current.active_elapsed,
                remaining_active_ticks=current.lifetime_limit-current.active_elapsed,
                pending_occurrences=[{**copy.deepcopy(row), 'time_ticks': None if current.paused else
                    current.last_ticks + row['due_active_ticks'] - current.active_elapsed}
                    for row in current.candidates])
        else:
            if current.paused:
                _fail("paused generation reaches the window without a confirmed resume")
            remaining = current.lifetime_limit - current.active_elapsed
            final_ticks = current.last_ticks + remaining
            if final_ticks > window_end:
                _fail("active lifecycle exceeds the projection window")
            _advance(current, (final_ticks, ORDER_MAX), receipt, inclusive=True,
                     reject_equal=False)
            _assert_derived_closure(current, normalized)
            _cancel(current, receipt, "lifecycle_complete", (final_ticks, ORDER_MAX))
            receipt["generations"][current.generation - 1]["status"] = "complete"
    if order_map:
        _fail("occurrence order overrides reference occurrences that were never projected")
    receipt["occurrences"].sort(key=lambda row: (row["time_ticks"], row["order"], row["occurrence_id"]))
    receipt["summary"] = {"generations": len(receipt["generations"]),
        "materialized_occurrences": len(receipt["occurrences"]),
        "cancelled_occurrences": len(receipt["cancelled"]),
        "deferred_intervals": len(receipt["deferred"]),
        "skipped_controls": len(receipt["skipped"])}
    if len(receipt["occurrences"]) + len(receipt["cancelled"]) > MAX_TOTAL_OCCURRENCES:
        _fail("periodic projection exceeds the 40000 occurrence budget")
    try:
        encoded = json.dumps(receipt, ensure_ascii=False, allow_nan=False,
            separators=(",", ":")).encode("utf8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PeriodicLifecycleError("periodic projection receipt is not finite JSON") from exc
    if len(encoded) > MAX_RECEIPT_BYTES:
        _fail("periodic projection receipt exceeds the 2MiB delivery budget")
    return receipt
