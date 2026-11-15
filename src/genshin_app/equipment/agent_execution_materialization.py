"""Self-contained Agent execution materialization for generic game-mechanic families.

The knowledge database, appendix and research tools may help an Agent author a
document, but this module never reads those sources.  It validates a frozen,
self-contained input against the current plan and event topology and dispatches
only to App-shipped generic handlers.  Character, skill, constellation and
equipment identities are data; none of them select Python control flow here.

This is deliberately the first, bounded slice of
``agent-execution-materialization-v1``.  It establishes the stable envelope,
source/dependency receipts, handler registry, unique ownership and replay-safe
receipt required by later Composer/runtime consumers.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .agent_materialization_contract import (
    ENTRY_FIELDS,
    ROOT_FIELDS,
    SOURCE_FIELDS,
    materialization_input_schema,
    periodic_payload_schema,
)
from .event_topology import (
    event_topology_fingerprint,
    lint_event_topology,
    validate_event_topology,
)
from .files import execution_input_view, fingerprint

CONTRACT_ID = "agent-execution-materialization-v1"
EFFECTIVE_INPUT_CONTRACT_ID = "agent-execution-effective-input-v1"
RECEIPT_CONTRACT_ID = "agent-execution-materialization-receipt-v1"
SCHEMA_VERSION = 1
AUTHORITY = "agent_materialized_current_plan"
EFFECTIVE_AUTHORITY = "agent_materialized_generic_handler"

MAX_BYTES = 4 * 1024 * 1024
MAX_SOURCES = 256
MAX_ENTRIES = 4096
MAX_NODE_REFS = 4096
MAX_SOURCE_REFS = 64
MAX_COVERAGE_KEYS = 256
MAX_SEQUENCE_STEPS = 2048
MAX_PERIODIC_OCCURRENCES = 256
MAX_EMISSIONS = 256
MAX_OPERATIONS = 256
MAX_TEXT = 4096
MAX_SECONDS = 3600.0

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_HASH = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEYS = frozenset({
    "result", "results", "summary", "total", "total_damage", "dps",
    "calculated_damage", "result_receipt", "execution_receipt", "trace",
    "traces", "execution_trace", "runtime_trace", "event_log", "expected",
})
_SOURCE_KINDS = frozenset({
    "database", "database_appendix", "agent_research", "user_input",
    "derived", "other",
})
_ELEMENTS = frozenset({"pyro", "hydro", "electro", "cryo", "anemo", "geo", "dendro"})
_REFRESH_POLICIES = frozenset({"none", "replace", "extend", "restart", "ignore"})
_OFF_FIELD_POLICIES = frozenset({"continue", "stop"})
_PERIODIC_REFRESH_POLICIES = frozenset({
    "none", "replace_previous", "reset_duration", "extend_duration", "ignore_new",
})
_PERIODIC_PAUSE_POLICIES = frozenset({
    "none", "pause_preserve_phase", "pause_reset_phase",
})
_PERIODIC_CONTROL_OPERATIONS = frozenset({
    "replace", "reset_duration", "extend_duration", "pause", "resume", "cancel",
})
_PERIODIC_CONTROL_EDGE_KINDS = ('triggers', 'refreshes', 'replaces')
from .periodic_contract import (
    CONTROL_FIELDS,
    DERIVED_COUNT_POLICIES,
    DISPATCH_FIELDS,
    EQUAL_SCHEDULE_FIELDS,
    ESTABLISHMENT_FIELDS,
    EXPLICIT_SCHEDULE_FIELDS,
    INTERVAL_FIELDS,
    LIFECYCLE_FIELDS,
    OCCURRENCE_FIELDS,
    PERIODIC_FIELDS,
)
from .periodic_contract import STRICT_COUNT_POLICY as _PERIODIC_DERIVE_COUNT_POLICY

_PERIODIC_TOPOLOGY_REFRESH = {
    "none": "none",
    "replace": "replace_previous",
    "extend": "extend_duration",
    # event-topology-v1 used an ambiguous compatibility label.  Handler v2
    # gives it one narrow meaning: reset the active duration, preserve phase.
    "restart": "reset_duration",
    "ignore": "ignore_new",
}


class AgentExecutionMaterializationError(ValueError):
    """A materialization document is not a valid self-contained execution input."""

    def __init__(self, path: str, message: str,
                 code: str = "invalid_agent_execution_materialization"):
        self.path = path
        self.message = message
        self.code = code
        super().__init__(f"{path}: {message}")


def _fail(path: str, message: str,
          code: str = "invalid_agent_execution_materialization") -> None:
    raise AgentExecutionMaterializationError(path, message, code)


def _json_bytes(value: Any, *, path: str = "materialization") -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise AgentExecutionMaterializationError(path, "must be finite JSON") from exc
    if len(raw) > MAX_BYTES:
        _fail(path, "exceeds the 4 MiB materialization limit",
              "agent_materialization_too_large")
    return raw


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _closed(value: Any, path: str, required: set[str],
            optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        _fail(path, "missing fields: " + ", ".join(sorted(missing)))
    if extra:
        _fail(path, "unknown fields: " + ", ".join(sorted(extra)))
    return value


def _list(value: Any, path: str, maximum: int, *, minimum: int = 0) -> list:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail(path, f"must contain {minimum}..{maximum} items")
    return value


def _id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        _fail(path, "must be a bounded stable identifier")
    return value


def _text(value: Any, path: str, *, empty: bool = False, nullable: bool = False):
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or len(value) > MAX_TEXT
            or (not empty and not value.strip())):
        _fail(path, "must be bounded text" + (" or null" if nullable else ""))
    return value


def _hash(value: Any, path: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        _fail(path, "must be a lowercase sha256 digest")
    return value


def _integer(value: Any, path: str, low: int = 0, high: int = 2**53 - 1) -> int:
    if type(value) is not int or not low <= value <= high:
        _fail(path, f"must be an integer in [{low}, {high}]")
    return value


def _number(value: Any, path: str, *, low: float | None = None,
            high: float | None = None) -> float | int:
    if type(value) not in (int, float) or not math.isfinite(value):
        _fail(path, "must be a finite number")
    if low is not None and value < low:
        _fail(path, f"must be at least {low}")
    if high is not None and value > high:
        _fail(path, f"must be at most {high}")
    return value


def _seconds(value: Any, path: str, *, nullable: bool = False,
             positive: bool = False):
    if value is None and nullable:
        return None
    _number(value, path, low=0, high=MAX_SECONDS)
    if positive and value == 0:
        _fail(path, "must be positive")
    if round(value * 1_000_000) != value * 1_000_000:
        _fail(path, "must be exactly representable in microseconds")
    return value


def _unique(values: list[str], path: str) -> None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            _fail(path, f"contains duplicate identifier {value}",
                  "agent_materialization_duplicate_identity")
        seen.add(value)


def _reject_forbidden(value: Any, path: str) -> None:
    if isinstance(value, dict):
        forbidden = value.keys() & _FORBIDDEN_KEYS
        if forbidden:
            _fail(path, "cannot carry calculated output fields: "
                  + ", ".join(sorted(forbidden)),
                  "agent_materialization_result_payload")
        for key, item in value.items():
            _reject_forbidden(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_forbidden(item, f"{path}[{index}]")


def _ids(value: Any, path: str, maximum: int, *, minimum: int = 0) -> list[str]:
    rows = _list(value, path, maximum, minimum=minimum)
    result = [_id(item, f"{path}[{index}]") for index, item in enumerate(rows)]
    _unique(result, path)
    return result


def _validate_finite_sequence(payload: Any, path: str) -> dict:
    row = _closed(payload, path,
        {"sequence_id", "steps", "interrupt_policy"})
    _id(row["sequence_id"], path + ".sequence_id")
    if row["interrupt_policy"] not in {"none", "cancel_remaining"}:
        _fail(path + ".interrupt_policy", "unsupported interrupt policy")
    steps = _list(row["steps"], path + ".steps", MAX_SEQUENCE_STEPS, minimum=1)
    step_ids: list[str] = []
    positions: list[tuple[int, int]] = []
    for index, step in enumerate(steps):
        spath = f"{path}.steps[{index}]"
        item = _closed(step, spath,
            {"step_id", "offset_seconds", "order", "emits_node_ids"})
        step_ids.append(_id(item["step_id"], spath + ".step_id"))
        offset = _seconds(item["offset_seconds"], spath + ".offset_seconds")
        order = _integer(item["order"], spath + ".order", -(2**31), 2**31 - 1)
        positions.append((round(offset * 1_000_000), order))
        _ids(item["emits_node_ids"], spath + ".emits_node_ids", MAX_EMISSIONS, minimum=1)
    _unique(step_ids, path + ".steps")
    if positions != sorted(positions) or len(positions) != len(set(positions)):
        _fail(path + ".steps", "steps must have unique monotonic microsecond/order positions")
    return row


def _periodic_schedule_offsets(schedule: dict, duration_seconds: float,
                               path: str) -> list[tuple[str, float, int]]:
    duration_ticks = round(duration_seconds * 1_000_000)
    if schedule["kind"] == "equal_interval":
        first_ticks = round(schedule["first_offset_seconds"] * 1_000_000)
        interval_ticks = round(schedule["interval_seconds"] * 1_000_000)
        count = schedule["count"]
        if count is None:
            if schedule["derive_count"] not in DERIVED_COUNT_POLICIES:
                _fail(path + ".derive_count",
                      "count=null requires a published derived count policy")
            if first_ticks > duration_ticks or (schedule['derive_count'] == _PERIODIC_DERIVE_COUNT_POLICY and (duration_ticks - first_ticks) % interval_ticks):
                _fail(path, "derived periodic count must close exactly at lifecycle end")
            count = (duration_ticks - first_ticks) // interval_ticks + 1
            if not 1 <= count <= MAX_PERIODIC_OCCURRENCES:
                _fail(path, "derived occurrence count is outside the supported bound")
        elif schedule["derive_count"] is not None:
            _fail(path + ".derive_count", "explicit count cannot carry derive_count")
        result = [
            (f"{schedule['schedule_id']}:{index + 1}",
             (first_ticks + index * interval_ticks) / 1_000_000,
             schedule["order"])
            for index in range(count)
        ]
    else:
        result = [(item["occurrence_id"], item["offset_seconds"], item["order"])
                  for item in schedule["occurrences"]]
    if any(round(offset * 1_000_000) > duration_ticks for _, offset, _ in result):
        _fail(path, "periodic occurrence exceeds the lifecycle duration")
    return result


def _validate_periodic_schedule(value: Any, path: str,
                                duration_seconds: float) -> dict:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    kind = value.get("kind")
    if kind == "equal_interval":
        row = _closed(value, path, EQUAL_SCHEDULE_FIELDS)
        _id(row["schedule_id"], path + ".schedule_id")
        _seconds(row["first_offset_seconds"], path + ".first_offset_seconds")
        _seconds(row["interval_seconds"], path + ".interval_seconds", positive=True)
        if row["count"] is not None:
            _integer(row["count"], path + ".count", 1, MAX_PERIODIC_OCCURRENCES)
        if row["derive_count"] not in {None, *DERIVED_COUNT_POLICIES}:
            _fail(path + ".derive_count", "unsupported count derivation policy")
        _integer(row["order"], path + ".order", -(10**9), 10**9)
    elif kind == "explicit_offsets":
        row = _closed(value, path, EXPLICIT_SCHEDULE_FIELDS)
        _id(row["schedule_id"], path + ".schedule_id")
        occurrences = _list(
            row["occurrences"], path + ".occurrences",
            MAX_PERIODIC_OCCURRENCES, minimum=1)
        occurrence_ids: list[str] = []
        positions: list[tuple[int, int]] = []
        for index, occurrence in enumerate(occurrences):
            opath = f"{path}.occurrences[{index}]"
            item = _closed(
                occurrence, opath, OCCURRENCE_FIELDS)
            occurrence_ids.append(_id(item["occurrence_id"], opath + ".occurrence_id"))
            offset = _seconds(item["offset_seconds"], opath + ".offset_seconds")
            order = _integer(item["order"], opath + ".order", -(10**9), 10**9)
            positions.append((round(offset * 1_000_000), order))
        _unique(occurrence_ids, path + ".occurrences")
        if positions != sorted(positions) or len(positions) != len(set(positions)):
            _fail(path + ".occurrences",
                  "occurrences must have unique monotonic microsecond/order positions")
    else:
        _fail(path + ".kind", "unsupported periodic schedule kind")
    _periodic_schedule_offsets(row, duration_seconds, path)
    return row


def _validate_periodic_channel(payload: Any, path: str) -> dict:
    row = _closed(payload, path, PERIODIC_FIELDS)
    _id(row["profile_id"], path + ".profile_id")
    _id(row["channel_id"], path + ".channel_id")

    lifecycle = _closed(row["lifecycle"], path + ".lifecycle", LIFECYCLE_FIELDS)
    duration = _seconds(
        lifecycle["duration_seconds"], path + ".lifecycle.duration_seconds")
    if lifecycle["refresh_policy"] not in _PERIODIC_REFRESH_POLICIES:
        _fail(path + ".lifecycle.refresh_policy", "unsupported refresh policy")
    if lifecycle["off_field_policy"] not in _OFF_FIELD_POLICIES:
        _fail(path + ".lifecycle.off_field_policy", "unsupported off-field policy")
    if lifecycle["pause_resume_policy"] not in _PERIODIC_PAUSE_POLICIES:
        _fail(path + ".lifecycle.pause_resume_policy",
              "unsupported pause/resume policy")
    _validate_periodic_schedule(row["schedule"], path + ".schedule", duration)

    establishments = _list(
        row["establishments"], path + ".establishments", 256, minimum=1)
    establishment_ids: list[str] = []
    for index, establishment in enumerate(establishments):
        epath = f"{path}.establishments[{index}]"
        item = _closed(
            establishment, epath,
            ESTABLISHMENT_FIELDS,
        )
        establishment_ids.append(
            _id(item["establishment_id"], epath + ".establishment_id"))
        _id(item["anchor_node_id"], epath + ".anchor_node_id")
        _integer(item["order"], epath + ".order", -(10**9), 10**9)
    _unique(establishment_ids, path + ".establishments")

    controls = _list(row["controls"], path + ".controls", 512)
    control_ids: list[str] = []
    for index, control in enumerate(controls):
        cpath = f"{path}.controls[{index}]"
        item = _closed(control, cpath, CONTROL_FIELDS)
        control_ids.append(_id(item["control_id"], cpath + ".control_id"))
        if item["operation"] not in _PERIODIC_CONTROL_OPERATIONS:
            _fail(cpath + ".operation", "unsupported lifecycle control operation")
        _id(item["anchor_node_id"], cpath + ".anchor_node_id")
        _integer(item["order"], cpath + ".order", -(10**9), 10**9)
        _seconds(item["duration_seconds"], cpath + ".duration_seconds",
                 nullable=True, positive=True)
        if item["operation"] == "extend_duration":
            if item["duration_seconds"] is None:
                _fail(cpath + ".duration_seconds",
                      "extend_duration requires a positive duration")
        elif item["duration_seconds"] is not None:
            _fail(cpath + ".duration_seconds",
                  "only extend_duration accepts duration_seconds")
    _unique(control_ids, path + ".controls")
    _unique(establishment_ids + control_ids, path + ".lifecycle_event_ids")

    intervals = _list(
        row["on_field_intervals"], path + ".on_field_intervals", 512)
    interval_ids: list[str] = []
    for index, interval in enumerate(intervals):
        ipath = f"{path}.on_field_intervals[{index}]"
        item = _closed(
            interval, ipath, INTERVAL_FIELDS)
        interval_ids.append(_id(item["interval_id"], ipath + ".interval_id"))
        _id(item["start_node_id"], ipath + ".start_node_id")
        _id(item["end_node_id"], ipath + ".end_node_id")
    _unique(interval_ids, path + ".on_field_intervals")
    if lifecycle["off_field_policy"] == "stop" and not intervals:
        _fail(path + ".on_field_intervals",
              "off-field stop requires reviewed on-field intervals")
    if lifecycle["off_field_policy"] == "continue" and intervals:
        _fail(path + ".on_field_intervals",
              "continue cannot carry unused on-field intervals")

    dispatches = _list(row["dispatches"], path + ".dispatches", 64, minimum=1)
    dispatch_ids: list[str] = []
    emitted: list[str] = []
    for index, dispatch in enumerate(dispatches):
        dpath = f"{path}.dispatches[{index}]"
        item = _closed(dispatch, dpath, DISPATCH_FIELDS)
        dispatch_ids.append(_id(item["dispatch_id"], dpath + ".dispatch_id"))
        node_ids = _ids(
            item["emits_node_ids"], dpath + ".emits_node_ids",
            MAX_EMISSIONS, minimum=1)
        emitted.extend(node_ids)
    _unique(dispatch_ids, path + ".dispatches")
    _unique(emitted, path + ".dispatches.emits_node_ids")
    return row


def _validate_application(payload: Any, path: str) -> dict:
    row = _closed(payload, path, {
        "element", "gauge_units", "application_mode", "counter_tag",
        "rule_group", "timer_seconds", "hit_count", "sequence",
        "target_ref", "landed", "attempted",
    })
    if row["element"] not in _ELEMENTS:
        _fail(path + ".element", "unsupported element")
    _number(row["gauge_units"], path + ".gauge_units", low=0, high=4)
    if row["gauge_units"] not in {0, 1, 2, 4}:
        _fail(path + ".gauge_units", "the current elemental runtime accepts 0/1/2/4 GU")
    mode = row["application_mode"]
    if mode not in {"timer_counter", "sequence", "no_icd", "no_application"}:
        _fail(path + ".application_mode", "unsupported application mode")
    if row["counter_tag"] is not None:
        _id(row["counter_tag"], path + ".counter_tag")
    if row["rule_group"] is not None:
        _id(row["rule_group"], path + ".rule_group")
    _id(row["target_ref"], path + ".target_ref")
    if type(row["landed"]) is not bool or type(row["attempted"]) is not bool:
        _fail(path, "landed and attempted must be booleans")
    _seconds(row["timer_seconds"], path + ".timer_seconds", nullable=True, positive=True)
    if row["hit_count"] is not None:
        _integer(row["hit_count"], path + ".hit_count", 1, MAX_SEQUENCE_STEPS)
    if row["sequence"] is not None:
        sequence = _list(row["sequence"], path + ".sequence", MAX_SEQUENCE_STEPS, minimum=1)
        if any(type(item) is not bool for item in sequence):
            _fail(path + ".sequence", "must contain booleans")
    if mode == "timer_counter":
        if (row["counter_tag"] is None or row["rule_group"] is None
                or row["timer_seconds"] is None or row["hit_count"] is None):
            _fail(path, "timer_counter requires counter_tag, rule_group, timer_seconds and hit_count")
        if row["sequence"] is not None:
            _fail(path + ".sequence", "timer_counter cannot carry a finite sequence")
    elif mode == "sequence":
        if (row["counter_tag"] is None or row["rule_group"] is None
                or row["timer_seconds"] is None or row["sequence"] is None
                or row["hit_count"] is not None):
            _fail(path, "sequence mode requires counter_tag, rule_group, reset time and a finite sequence")
    elif mode == "no_icd":
        if any(row[key] is not None for key in (
                "counter_tag", "rule_group", "timer_seconds", "hit_count", "sequence")):
            _fail(path, "no_icd cannot carry counter state")
    else:
        if row["gauge_units"] != 0 or any(row[key] is not None for key in (
                "counter_tag", "rule_group", "timer_seconds", "hit_count", "sequence")):
            _fail(path, "no_application requires zero gauge and no counter state")
    return row


def _validate_buff(payload: Any, path: str) -> dict:
    row = _closed(payload, path, {
        "effect_id", "target_scope", "start_offset_seconds", "duration_seconds",
        "refresh_policy", "operations",
    })
    _id(row["effect_id"], path + ".effect_id")
    if row["target_scope"] not in {"owner", "active_character", "team", "enemy", "event_binding"}:
        _fail(path + ".target_scope", "unsupported target scope")
    _seconds(row["start_offset_seconds"], path + ".start_offset_seconds")
    _seconds(row["duration_seconds"], path + ".duration_seconds", nullable=True, positive=True)
    if row["refresh_policy"] not in _REFRESH_POLICIES:
        _fail(path + ".refresh_policy", "unsupported refresh policy")
    operations = _list(row["operations"], path + ".operations", MAX_OPERATIONS, minimum=1)
    for index, operation in enumerate(operations):
        opath = f"{path}.operations[{index}]"
        item = _closed(operation, opath,
            {"stat", "operation", "value", "target_ref"})
        _id(item["stat"], opath + ".stat")
        if item["operation"] not in {
                "add_flat", "add_ratio", "multiply", "set", "resistance_delta"}:
            _fail(opath + ".operation", "unsupported buff operation")
        _number(item["value"], opath + ".value")
        _id(item["target_ref"], opath + ".target_ref")
    return row


def _validate_buff_v2(payload: Any, path: str) -> dict:
    """Validate one self-contained typed D20 benefit rule.

    The v1 buff payload intentionally remains a knowledge-preserving legacy
    shape: ``stat + operation`` is not strong enough to select a benefit zone,
    sampling domain, hit filter and trigger without guessing.  V2 therefore
    uses the engine's existing closed D20 rule contract instead of growing a
    second, almost-compatible buff language.
    """

    row = _validate_mechanics_rule_bundle(payload, path)
    if len(row["rules"]) != 1 or len(row["parts"]) != 1:
        _fail(path, "buff v2 requires exactly one typed rule and one source part")
    rule = row["rules"][0]
    if not rule.get("effects"):
        _fail(path + ".rules[0].effects", "buff v2 requires at least one typed benefit")
    if rule.get("resources") or row["resource_ownership"]:
        _fail(path, "buff v2 cannot hide resource delivery; use a resource family")
    return row


def _validate_resource(payload: Any, path: str) -> dict:
    row = _closed(payload, path, {
        "resource_id", "operation", "amount", "receiver_ref", "delivery",
        "travel_seconds",
    })
    _id(row["resource_id"], path + ".resource_id")
    if row["operation"] not in {"add", "consume", "set"}:
        _fail(path + ".operation", "unsupported resource operation")
    _number(row["amount"], path + ".amount", low=0)
    _id(row["receiver_ref"], path + ".receiver_ref")
    if row["delivery"] not in {"immediate", "projectile"}:
        _fail(path + ".delivery", "unsupported resource delivery")
    _seconds(row["travel_seconds"], path + ".travel_seconds", nullable=True)
    if row["delivery"] == "immediate" and row["travel_seconds"] not in (None, 0):
        _fail(path + ".travel_seconds", "immediate delivery cannot have travel time")
    if row["delivery"] == "projectile" and row["travel_seconds"] is None:
        _fail(path + ".travel_seconds", "projectile delivery requires travel time")
    return row


def _validate_resource_operation(payload: Any, path: str) -> dict:
    """A closed pool/operation bundle, consumed by the existing ledger."""
    from genshin_engine.shared_pools import validate_shared_pools
    row = _closed(payload, path, {'shared_pools'})
    pools = row['shared_pools']
    if not isinstance(pools, dict) or pools.get('consumers'):
        _fail(path, 'resource_operation owns pool operations; damage consumers require their own source bundle')
    records = pools.get('pools', [])
    records = records if isinstance(records, list) else []
    characters = {p['owner'] for p in records if isinstance(p, dict) and isinstance(p.get('owner'), str)}
    characters.update(c for p in records if isinstance(p, dict) and p.get('scope') == 'per_character'
                      and isinstance(p.get('initial'), dict) for c in p['initial'])
    issues = validate_shared_pools(pools, character_ids=sorted(characters) or ['unbound'])
    if issues:
        _fail(path, issues[0]['message'])
    if not pools['operations']:
        _fail(path, 'resource_operation requires actual typed operations')
    return row


def _validate_state(payload: Any, path: str) -> dict:
    row = _closed(payload, path, {
        "state_key", "operation", "value_type", "value", "target_ref",
        "duration_seconds", "refresh_policy",
    })
    _id(row["state_key"], path + ".state_key")
    if row["operation"] not in {"set", "increment", "decrement", "clear", "refresh", "replace"}:
        _fail(path + ".operation", "unsupported state operation")
    if row["value_type"] not in {"number", "integer", "boolean", "string", "reference", "none"}:
        _fail(path + ".value_type", "unsupported state value type")
    value = row["value"]
    if row["value_type"] == "number":
        _number(value, path + ".value")
    elif row["value_type"] == "integer":
        _integer(value, path + ".value", -(2**53) + 1, 2**53 - 1)
    elif row["value_type"] == "boolean" and type(value) is not bool:
        _fail(path + ".value", "must be boolean")
    elif row["value_type"] in {"string", "reference"}:
        (_id if row["value_type"] == "reference" else _text)(value, path + ".value")
    elif row["value_type"] == "none" and value is not None:
        _fail(path + ".value", "none value type requires null")
    if row["operation"] == "clear" and row["value_type"] != "none":
        _fail(path, "clear requires none value type")
    _id(row["target_ref"], path + ".target_ref")
    _seconds(row["duration_seconds"], path + ".duration_seconds", nullable=True, positive=True)
    if row["refresh_policy"] not in _REFRESH_POLICIES:
        _fail(path + ".refresh_policy", "unsupported refresh policy")
    return row


def _validate_state_v2(payload: Any, path: str) -> dict:
    """Validate a self-contained finite lifecycle plus its typed D20 targets.

    This is deliberately a bounded bridge to ``state_runtime``.  It does not
    interpret arbitrary key/value state and it does not accept executable code.
    Every lifecycle/timer target is a normal D20 rule carried by the same
    materialization entry and is later checked against the real rotation graph.
    """

    from genshin_engine.mechanics import validate_mechanics_model
    from genshin_engine.state_runtime_mechanics import route_event_id

    row = _closed(payload, path, {
        "bundle_id", "state_runtime", "rules", "initial_state",
        "resource_ownership", "parts",
    })
    rule_bundle = {
        "bundle_id": row["bundle_id"],
        "rules": row["rules"],
        "initial_state": row["initial_state"],
        "resource_ownership": row["resource_ownership"],
        "parts": row["parts"],
    }
    _validate_mechanics_rule_bundle(rule_bundle, path)
    state_runtime = row["state_runtime"]
    if not isinstance(state_runtime, dict):
        _fail(path + ".state_runtime", "must be a closed finite state-runtime object")
    if state_runtime.get("schema_version") != 1:
        _fail(
            path + ".state_runtime.schema_version",
            "state v2 currently accepts only the finite state-runtime v1 clock",
            "agent_materialization_state_runtime_version_unsupported",
        )
    isolated = {
        "schema_version": 1,
        "rules": copy.deepcopy(row["rules"]),
        "hit_events": [],
        "hit_patterns": [],
        "condition_events": [],
        "initial_state": copy.deepcopy(row["initial_state"]),
        "state_runtime": copy.deepcopy(state_runtime),
    }
    if row["resource_ownership"]:
        isolated["resource_ownership"] = copy.deepcopy(row["resource_ownership"])
    validation = validate_mechanics_model(isolated)
    if validation["status"] == "invalid":
        issue = validation["issues"][0]
        _fail(
            path + ".state_runtime",
            issue.get("message", "finite state-runtime bundle is invalid"),
            "agent_materialization_state_runtime_invalid",
        )
    # The first generic state slice routes only typed D20 rules.  Shared-pool
    # resource operations have their own larger ownership graph and must not be
    # smuggled through a string ref_id.
    lifecycles = {
        lifecycle["id"]: lifecycle
        for lifecycle in state_runtime.get("lifecycles", [])
    }
    target_bindings: list[tuple[dict, str, str]] = []
    for lifecycle in lifecycles.values():
        target_bindings.extend(
            (hook, lifecycle["owner"], lifecycle["source_id"])
            for stage in ("create", "established", "end")
            for hook in lifecycle.get("hooks", {}).get(stage, [])
        )
    for timer in state_runtime.get("timers", []):
        lifecycle = lifecycles.get(timer.get("lifecycle_id"))
        if lifecycle is not None:
            target_bindings.append(
                (timer.get("payload", {}), lifecycle["owner"], timer["source_id"]))
    hooks = [hook for hook, _, _ in target_bindings]
    if any(hook.get("kind") != "dispatch_rule" for hook in hooks):
        _fail(
            path + ".state_runtime",
            "state v2 currently accepts only self-contained dispatch_rule hooks",
            "agent_materialization_state_resource_hook_unsupported",
        )
    rule_ids = {rule["id"] for rule in row["rules"]}
    dangling = sorted({hook.get("ref_id") for hook in hooks} - rule_ids)
    if dangling:
        _fail(
            path + ".state_runtime",
            "state v2 hook references a rule outside this entry: "
            + ", ".join(dangling),
            "agent_materialization_state_reference_missing",
        )
    routed: dict[str, int] = {rule_id: 0 for rule_id in rule_ids}
    for hook in hooks:
        routed[hook["ref_id"]] += 1
    invalid_ownership = sorted(
        rule_id for rule_id, count in routed.items() if count != 1)
    if invalid_ownership:
        _fail(
            path + ".state_runtime",
            "each state v2 rule must be owned by exactly one lifecycle hook or timer: "
            + ", ".join(invalid_ownership),
            "agent_materialization_state_dispatch_ownership",
        )
    rules_by_id = {rule["id"]: rule for rule in row["rules"]}
    for hook, owner, source_id in target_bindings:
        rule = rules_by_id.get(hook.get("ref_id"))
        if rule is not None and (
                rule["owner"] != owner or rule["source_id"] != source_id):
            _fail(
                path + ".state_runtime",
                "finite-clock target owner/source must match its lifecycle delivery",
                "agent_materialization_state_dispatch_source_mismatch",
            )
    for index, rule in enumerate(row["rules"]):
        rpath = f"{path}.rules[{index}]"
        rule_id = rule["id"]
        if rule.get("trigger", {}).get("event_ids") != [
                route_event_id("dispatch_rule", rule_id)]:
            _fail(
                rpath + ".trigger.event_ids",
                "state v2 target must be uniquely routed by its finite clock",
                "agent_materialization_state_dispatch_ownership",
            )
        if (rule.get("quota") is not None
                or rule.get("resource_gate") is not None
                or rule.get("resources")
                or rule["timing"]["delay"] != 0
                or rule["beneficiaries"]["mode"] == "next_switch"
                or rule["stacking"] != {"policy": "refresh", "max": 1, "add": 1}):
            _fail(
                rpath,
                "state v2 target must be a zero-delay, one-layer refresh rule without "
                "quota, resource gate, resource delivery or next-switch targeting",
                "agent_materialization_state_rule_scope_unsupported",
            )
    return row


def _validate_mechanics_rule_bundle(payload: Any, path: str) -> dict:
    """Validate a self-contained Agent-authored projection into D20.

    This deliberately validates only executable declarations.  Source text,
    database identities and compiler previews are not accepted here.  Each D20
    rule has exactly one source part, and every part owns a disjoint set of
    topology nodes.  The runtime adapter performs the final merge validation
    against the current effective mechanics graph.
    """
    from genshin_engine.mechanics import validate_mechanics_model

    row = _closed(payload, path, {
        "bundle_id", "rules", "initial_state", "resource_ownership", "parts",
    })
    _id(row["bundle_id"], path + ".bundle_id")
    rules = _list(row["rules"], path + ".rules", 128, minimum=1)
    initial_state = _closed(
        row["initial_state"], path + ".initial_state", {"rules"})
    _list(initial_state["rules"], path + ".initial_state.rules", 128)
    ownership = _list(
        row["resource_ownership"], path + ".resource_ownership", 256)
    parts = _list(row["parts"], path + ".parts", 128, minimum=1)

    rule_ids: list[str] = []
    rule_sources: dict[str, str] = {}
    for index, rule in enumerate(rules):
        rpath = f"{path}.rules[{index}]"
        if not isinstance(rule, dict):
            _fail(rpath, "must be a D20 rule object")
        rule_id = _id(rule.get("id"), rpath + ".id")
        rule_ids.append(rule_id)
        rule_sources[rule_id] = _id(rule.get("source_id"), rpath + ".source_id")
        if rule.get("status") == "pending":
            _fail(
                rpath + ".status",
                "a frozen Agent execution rule cannot retain pending evidence",
                "agent_materialization_source_pending",
            )
    _unique(rule_ids, path + ".rules")

    part_ids: list[str] = []
    part_rule_ids: list[str] = []
    part_nodes: list[str] = []
    for index, part in enumerate(parts):
        ppath = f"{path}.parts[{index}]"
        item = _closed(part, ppath, {
            "part_id", "rule_id", "node_ids", "source_ids", "note",
        })
        part_id = _id(item["part_id"], ppath + ".part_id")
        rule_id = _id(item["rule_id"], ppath + ".rule_id")
        part_ids.append(part_id)
        part_rule_ids.append(rule_id)
        part_nodes.extend(_ids(
            item["node_ids"], ppath + ".node_ids", MAX_NODE_REFS, minimum=1))
        _ids(item["source_ids"], ppath + ".source_ids", MAX_SOURCE_REFS, minimum=1)
        _text(item["note"], ppath + ".note", empty=True)
        if rule_sources.get(rule_id) != part_id:
            _fail(
                ppath,
                "part_id must equal the bound D20 rule source_id",
                "agent_materialization_rule_part_mismatch",
            )
    _unique(part_ids, path + ".parts")
    _unique(part_rule_ids, path + ".parts.rule_id")
    _unique(part_nodes, path + ".parts.node_ids")
    if set(part_rule_ids) != set(rule_ids):
        _fail(
            path + ".parts",
            "every D20 rule must have exactly one source part",
            "agent_materialization_rule_part_mismatch",
        )

    for index, owner in enumerate(ownership):
        opath = f"{path}.resource_ownership[{index}]"
        if not isinstance(owner, dict):
            _fail(opath, "must be a D20 resource ownership object")
        rule_id = _id(owner.get("rule_id"), opath + ".rule_id")
        source_part = _id(owner.get("source_part"), opath + ".source_part")
        if rule_sources.get(rule_id) != source_part:
            _fail(
                opath,
                "resource ownership must use the rule's exact source part",
                "agent_materialization_rule_part_mismatch",
            )

    # Reuse the engine's closed D20 schema and semantic validation.  This
    # intentionally accepts only bundles that are independently meaningful;
    # rules needing another optional mechanics contract stay unsupported until
    # that contract receives its own typed Agent input family.
    isolated = {
        "schema_version": 1,
        "rules": copy.deepcopy(rules),
        "hit_events": [],
        "hit_patterns": [],
        "condition_events": [],
        "initial_state": copy.deepcopy(initial_state),
    }
    if ownership:
        isolated["resource_ownership"] = copy.deepcopy(ownership)
    validation = validate_mechanics_model(isolated)
    if validation["status"] == "invalid":
        issue = validation["issues"][0]
        _fail(
            path + ".rules",
            issue.get("message", "D20 rule bundle is invalid"),
            "agent_materialization_d20_invalid",
        )
    return row


def _edge_exists(topology: dict, owner_nodes: set[str], emitted_node: str) -> bool:
    return any(
        row["status"] == "bound"
        and row["kind"] in {"contains", "causes", "triggers"}
        and row["from_node_id"] in owner_nodes
        and row["to_node_id"] == emitted_node
        for row in topology["edges"]
    )


def _issue(code: str, path: str, message: str, **identity) -> dict:
    return {"code": code, "path": path, "message": message, **identity}


def _lint_emissions(entry: dict, path: str, topology: dict,
                    node_by_id: dict[str, dict], emitted: list[str]) -> list[dict]:
    issues = []
    owner_nodes = set(entry["node_ids"])
    for emitted_node in emitted:
        if emitted_node not in node_by_id:
            issues.append(_issue(
                "agent_materialization_emission_missing", path + ".payload",
                "派生事件节点不存在。", node_id=emitted_node,
            ))
        elif not _edge_exists(topology, owner_nodes, emitted_node):
            issues.append(_issue(
                "agent_materialization_emission_unbound", path + ".payload",
                "派生事件必须有拓扑 contains/causes/triggers 绑定。",
                node_id=emitted_node,
            ))
    return issues


def _lint_finite_sequence(entry: dict, path: str, topology: dict,
                          node_by_id: dict[str, dict]) -> list[dict]:
    emitted = [node_id for step in entry["payload"]["steps"]
               for node_id in step["emits_node_ids"]]
    return _lint_emissions(entry, path, topology, node_by_id, emitted)


def _lint_periodic_channel(entry: dict, path: str, topology: dict,
                           node_by_id: dict[str, dict]) -> list[dict]:
    payload = entry["payload"]
    emitted = [node_id for dispatch in payload["dispatches"]
               for node_id in dispatch["emits_node_ids"]]
    issues = _lint_emissions(entry, path, topology, node_by_id, emitted)
    owner_id = entry["node_ids"][0]
    owner = node_by_id.get(owner_id)
    if owner is None:
        return issues

    schedule = owner["schedule"]
    authored = payload["schedule"]
    mismatch = False
    if authored["kind"] == "equal_interval":
        mismatch = schedule["kind"] != "periodic"
        if not mismatch:
            mismatch = any((
                schedule["first_offset_seconds"] != authored["first_offset_seconds"],
                schedule["interval_seconds"] != authored["interval_seconds"],
                schedule["count"] != authored["count"],
                schedule["duration_seconds"] != payload["lifecycle"]["duration_seconds"],
                schedule["order"] != authored["order"],
            ))
    else:
        mismatch = schedule["kind"] != "explicit_offsets"
        if not mismatch:
            topology_occurrences = [
                (row["occurrence_id"], row["offset_seconds"], row["order"])
                for row in schedule["occurrences"]
            ]
            authored_occurrences = [
                (row["occurrence_id"], row["offset_seconds"], row["order"])
                for row in authored["occurrences"]
            ]
            mismatch = topology_occurrences != authored_occurrences
    if not mismatch and schedule["kind"] == "periodic":
        topology_refresh = _PERIODIC_TOPOLOGY_REFRESH.get(schedule["refresh_policy"])
        mismatch = any((
            topology_refresh != payload["lifecycle"]["refresh_policy"],
            schedule["off_field_policy"] != payload["lifecycle"]["off_field_policy"],
        ))
    if mismatch:
        issues.append(_issue(
            "agent_materialization_schedule_mismatch", path + ".payload",
            "周期计划/生命周期与已冻结事件拓扑调度不一致。", node_id=owner_id,
        ))

    establishments = payload["establishments"]
    if establishments[0]["anchor_node_id"] != owner_id:
        issues.append(_issue(
            "agent_periodic_primary_establishment_mismatch",
            path + ".payload.establishments[0].anchor_node_id",
            "第一建立点必须引用周期条目的唯一owner节点。",
            node_id=establishments[0]["anchor_node_id"],
        ))

    entry_sources = set(entry["source_ids"])
    relations = {
        (edge["from_node_id"], edge["to_node_id"], edge["kind"])
        for edge in topology["edges"] if edge["status"] == "bound"
    }

    def lint_anchor(node_id: str, anchor_path: str, *, primary: bool = False,
                    require_control_edge: bool = True,
                    require_bound_status: bool = False) -> None:
        node = node_by_id.get(node_id)
        if node is None or node["status"] == "excluded":
            issues.append(_issue(
                "agent_periodic_lifecycle_anchor_missing", anchor_path,
                "周期生命周期锚点不存在或已排除。", node_id=node_id,
            ))
            return
        if require_bound_status and node["status"] != "bound":
            issues.append(_issue(
                "agent_periodic_lifecycle_anchor_not_bound", anchor_path,
                "周期生命周期观察边界必须已绑定到当前拓扑。",
                node_id=node_id, status=node["status"],
            ))
        missing_sources = set(node["source_ids"]) - entry_sources
        if missing_sources:
            issues.append(_issue(
                "agent_periodic_lifecycle_source_missing", anchor_path,
                "周期条目未冻结生命周期锚点使用的全部来源。",
                node_id=node_id, missing_source_ids=sorted(missing_sources),
            ))
        if primary or not require_control_edge:
            return
        if not any((node_id, owner_id, kind) in relations
                   for kind in _PERIODIC_CONTROL_EDGE_KINDS):
            issues.append(_issue(
                "agent_periodic_lifecycle_anchor_unbound", anchor_path,
                "额外建立/控制锚点需以triggers、refreshes或replaces的bound边指向周期owner。", node_id=node_id,
                expected_edge={'from_node_id': node_id, 'to_node_id': owner_id,
                    'kind_options': list(_PERIODIC_CONTROL_EDGE_KINDS), 'status': 'bound'},
            ))

    for index, establishment in enumerate(establishments):
        lint_anchor(
            establishment["anchor_node_id"],
            f"{path}.payload.establishments[{index}].anchor_node_id",
            primary=index == 0,
        )
    for index, control in enumerate(payload["controls"]):
        lint_anchor(
            control["anchor_node_id"],
            f"{path}.payload.controls[{index}].anchor_node_id",
        )
    for index, interval in enumerate(payload["on_field_intervals"]):
        lint_anchor(
            interval["start_node_id"],
            f"{path}.payload.on_field_intervals[{index}].start_node_id",
            require_control_edge=False, require_bound_status=True,
        )
        lint_anchor(
            interval["end_node_id"],
            f"{path}.payload.on_field_intervals[{index}].end_node_id",
            require_control_edge=False, require_bound_status=True,
        )
    return issues


def _lint_mechanics_rule_bundle(entry: dict, path: str, topology: dict,
                                node_by_id: dict[str, dict]) -> list[dict]:
    del topology
    payload = entry["payload"]
    issues = []
    part_nodes = [node_id for part in payload["parts"]
                  for node_id in part["node_ids"]]
    if set(part_nodes) != set(entry["node_ids"]):
        issues.append(_issue(
            "agent_materialization_rule_part_scope_mismatch",
            path + ".payload.parts",
            "规则部件必须恰好覆盖条目声明的拓扑节点。",
        ))
    entry_sources = set(entry["source_ids"])
    for part_index, part in enumerate(payload["parts"]):
        ppath = f"{path}.payload.parts[{part_index}]"
        if set(part["source_ids"]) != entry_sources:
            issues.append(_issue(
                "agent_materialization_rule_part_source_mismatch",
                ppath + ".source_ids",
                "规则部件必须冻结条目的完整来源集合。",
                part_id=part["part_id"],
            ))
            continue
        for node_id in part["node_ids"]:
            node = node_by_id.get(node_id)
            if node is not None and not entry_sources.issubset(
                    set(node["source_ids"])):
                issues.append(_issue(
                    "agent_materialization_rule_part_source_mismatch",
                    ppath + ".node_ids",
                    "规则部件来源必须同时绑定到其拓扑节点。",
                    part_id=part["part_id"], node_id=node_id,
                ))
    return issues


def _lint_typed_bundle_v2(entry: dict, path: str, topology: dict,
                          node_by_id: dict[str, dict]) -> list[dict]:
    """Legacy v1 buff/state remains storable but has no execution binding."""

    if entry["handler_version"] == 1:
        return []
    return _lint_mechanics_rule_bundle(entry, path, topology, node_by_id)


@dataclass(frozen=True)
class HandlerSpec:
    family: str
    node_kind: str
    version: int
    validate_payload: Callable[[Any, str], dict]
    additional_node_kinds: tuple[str, ...] = ()
    lint_topology: Callable[[dict, str, dict, dict[str, dict]], list[dict]] | None = None
    exact_owner_nodes: int | None = None
    legacy_validators: tuple[tuple[int, Callable[[Any, str], dict]], ...] = ()

    @property
    def handler_id(self) -> str:
        return "generic." + self.family

    @property
    def node_kinds(self) -> frozenset[str]:
        return frozenset((self.node_kind, *self.additional_node_kinds))

    @property
    def supported_versions(self) -> tuple[int, ...]:
        return tuple(sorted({self.version, *(version for version, _ in self.legacy_validators)}))

    def validator_for(self, version: int) -> Callable[[Any, str], dict] | None:
        if version == self.version:
            return self.validate_payload
        return next((validator for candidate, validator in self.legacy_validators
                     if candidate == version), None)


_HANDLERS = (
    HandlerSpec(
        "finite_sequence", "finite_sequence", 1, _validate_finite_sequence,
        lint_topology=_lint_finite_sequence,
        exact_owner_nodes=1,
    ),
    HandlerSpec(
        "periodic_channel", "periodic_channel", 2, _validate_periodic_channel,
        lint_topology=_lint_periodic_channel,
        exact_owner_nodes=1,
    ),
    HandlerSpec("application", "application", 2, _validate_application),
    HandlerSpec(
        "buff", "buff_interval", 2, _validate_buff_v2,
        lint_topology=_lint_typed_bundle_v2,
        legacy_validators=((1, _validate_buff),),
    ),
    HandlerSpec("resource", "resource", 1, _validate_resource),
    HandlerSpec('resource_operation', 'resource', 1, _validate_resource_operation),
    HandlerSpec(
        "state", "state", 2, _validate_state_v2,
        lint_topology=_lint_typed_bundle_v2,
        legacy_validators=((1, _validate_state),),
    ),
    HandlerSpec(
        "mechanics_rule_bundle", "other", 1,
        _validate_mechanics_rule_bundle,
        additional_node_kinds=(
            "buff_interval", "resource", "state", "reaction_candidate",
        ),
        lint_topology=_lint_mechanics_rule_bundle,
    ),
)
HANDLER_REGISTRY = {handler.family: handler for handler in _HANDLERS}


def handler_set_fingerprint() -> str:
    """Fingerprint the stable per-entry handler binding protocol.

    The historical field name is retained for schema compatibility, but the
    document is no longer coupled to the complete registry.  Every consumed
    handler is already frozen by ``family`` + ``handler_version`` on its entry.
    Adding an unrelated generic family therefore cannot stale old documents;
    changing a consumed handler version still fails closed at that entry.
    """
    return _sha({
        "contract_id": CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "binding": "per-entry-family-handler-version-v1",
    })


def plan_binding_fingerprint(plan: dict) -> str:
    """Bind game inputs, without downstream review hashes or our own envelope.

    Candidate storage increments ``revision`` after validating a patch.  The
    authored revision remains an audit lower-bound while this fingerprint
    proves that no non-materialization game input changed during that commit.
    Acknowledging the same source/context is not a new game fact. Their exact
    role-scoped hash projection is separate from source approval, which Composer
    must still validate before consuming any effect. Raw execution receipts bind
    the full input including those acknowledgements.
    """
    from .materialization_input_context import materialization_review_input
    value = materialization_review_input(execution_input_view(plan))
    # New materialization authors bind the same deterministic current policy
    # normalization as compose. It remains in the hash; legacy sealed document
    # hashes are not repaired or grandfathered into the changed semantics.
    from .energy_policy import mark_current_energy_policy
    mark_current_energy_policy(value)
    value.pop("revision", None)
    # Reviewed calculation overrides are a downstream transaction over the
    # graph produced by automatic compilers plus this materialization.  Their
    # acceptance fields may change while the Agent is reviewing that graph;
    # binding this upstream declaration to the downstream envelope would form
    # a circular stale loop (materialization -> override basis -> accepted
    # override -> materialization).  The runtime/proof layer still binds both
    # inputs to the same freshly composed effective graph.
    value.pop("calculation_event_overrides", None)
    extensions = value.get("extensions")
    if isinstance(extensions, dict):
        extensions.pop("agent_execution_materialization", None)
        extensions.pop("agent_execution_materialization_receipt", None)
    return fingerprint(value)


def new_agent_execution_materialization(plan: dict, topology: dict,
                                        materialization_id: str) -> dict:
    """Create a bound, empty document without deriving any gameplay fact."""
    validate_event_topology(topology)
    return {
        "contract_id": CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "materialization_id": _id(materialization_id, "materialization_id"),
        "authority": AUTHORITY,
        "plan_binding": {
            "plan_id": plan["plan_id"],
            "revision": plan["revision"],
            "input_fingerprint": plan_binding_fingerprint(plan),
        },
        "topology_binding": {
            "topology_id": topology["topology_id"],
            "topology_fingerprint": event_topology_fingerprint(topology),
            "action_checkpoint_fingerprint": topology["action_checkpoint"]["fingerprint"],
        },
        "handler_set_fingerprint": handler_set_fingerprint(),
        "sources": [],
        "entries": [],
    }


def validate_agent_execution_materialization(document: dict) -> dict:
    _json_bytes(document)
    _reject_forbidden(document, "materialization")
    root = _closed(document, "materialization", ROOT_FIELDS)
    if root["contract_id"] != CONTRACT_ID or root["schema_version"] != SCHEMA_VERSION:
        _fail("materialization", "unsupported contract/version")
    if root["authority"] != AUTHORITY:
        _fail("materialization.authority", "cannot self-assign another execution authority",
              "agent_materialization_authority_invalid")
    _id(root["materialization_id"], "materialization.materialization_id")
    binding = _closed(root["plan_binding"], "materialization.plan_binding",
                      {"plan_id", "revision", "input_fingerprint"})
    _text(binding["plan_id"], "materialization.plan_binding.plan_id")
    _integer(binding["revision"], "materialization.plan_binding.revision")
    _hash(binding["input_fingerprint"], "materialization.plan_binding.input_fingerprint")
    topology = _closed(root["topology_binding"], "materialization.topology_binding", {
        "topology_id", "topology_fingerprint", "action_checkpoint_fingerprint"})
    _id(topology["topology_id"], "materialization.topology_binding.topology_id")
    _hash(topology["topology_fingerprint"], "materialization.topology_binding.topology_fingerprint")
    _hash(topology["action_checkpoint_fingerprint"],
          "materialization.topology_binding.action_checkpoint_fingerprint")
    _hash(root["handler_set_fingerprint"], "materialization.handler_set_fingerprint")

    sources = _list(root["sources"], "materialization.sources", MAX_SOURCES)
    source_ids: list[str] = []
    for index, source in enumerate(sources):
        path = f"materialization.sources[{index}]"
        row = _closed(source, path, SOURCE_FIELDS)
        source_ids.append(_id(row["source_id"], path + ".source_id"))
        if row["kind"] not in _SOURCE_KINDS:
            _fail(path + ".kind", "unsupported source kind")
        _text(row["reference"], path + ".reference")
        _hash(row["fingerprint"], path + ".fingerprint")
        if row["status"] not in {"verified", "user_provided"}:
            _fail(path + ".status", "executable source must be frozen as verified or user_provided")
        _text(row["note"], path + ".note", empty=True)
    _unique(source_ids, "materialization.sources")
    known_sources = set(source_ids)

    entries = _list(root["entries"], "materialization.entries", MAX_ENTRIES)
    entry_ids: list[str] = []
    owned_nodes: list[str] = []
    coverage_keys: list[str] = []
    for index, entry in enumerate(entries):
        path = f"materialization.entries[{index}]"
        row = _closed(entry, path, ENTRY_FIELDS)
        entry_ids.append(_id(row["entry_id"], path + ".entry_id"))
        handler = HANDLER_REGISTRY.get(row["family"])
        if handler is None:
            _fail(path + ".family", "unsupported generic mechanism family",
                  "agent_materialization_handler_unsupported")
        # JSON booleans are Python integers and integral floats compare equal
        # to integer dictionary keys.  A frozen handler contract must retain
        # its exact cross-language type, not merely compare equal to a shipped
        # version number.
        if type(row["handler_version"]) is not int:
            _fail(
                path + ".handler_version",
                "handler version must be an integer",
                "agent_materialization_handler_version_invalid",
            )
        validator = handler.validator_for(row["handler_version"])
        if validator is None:
            _fail(path + ".handler_version", "handler version is not shipped by this App",
                  "agent_materialization_handler_stale")
        nodes = _ids(row["node_ids"], path + ".node_ids", MAX_NODE_REFS, minimum=1)
        if (handler.exact_owner_nodes is not None
                and len(nodes) != handler.exact_owner_nodes):
            _fail(
                path + ".node_ids",
                f"{handler.family} requires exactly "
                f"{handler.exact_owner_nodes} owner node(s)",
                "agent_materialization_owner_node_count",
            )
        owned_nodes.extend(nodes)
        declared_sources = _ids(row["source_ids"], path + ".source_ids",
                                MAX_SOURCE_REFS, minimum=1)
        unknown_sources = set(declared_sources) - known_sources
        if unknown_sources:
            _fail(path + ".source_ids", "references unknown source: "
                  + ", ".join(sorted(unknown_sources)))
        keys = _ids(row["coverage_keys"], path + ".coverage_keys",
                    MAX_COVERAGE_KEYS, minimum=1)
        coverage_keys.extend(keys)
        validator(row["payload"], path + ".payload")
        _text(row["note"], path + ".note", empty=True)
    _unique(entry_ids, "materialization.entries")
    _unique(owned_nodes, "materialization.entries.node_ids")
    _unique(coverage_keys, "materialization.entries.coverage_keys")
    return document


def lint_agent_execution_materialization(plan: dict, topology: dict,
                                         document: dict) -> dict:
    """Validate bindings and return a compact receipt without source text/payloads."""
    issues: list[dict] = []
    try:
        validate_event_topology(topology)
        validate_agent_execution_materialization(document)
    except (AgentExecutionMaterializationError, ValueError, TypeError, KeyError) as exc:
        issue = _issue(getattr(exc, "code", "invalid_agent_execution_materialization"),
                       getattr(exc, "path", "materialization"),
                       getattr(exc, "message", str(exc))[:512])
        invalid_code = getattr(exc, "code", "invalid_agent_execution_materialization")
        receipt = {
            "contract_id": RECEIPT_CONTRACT_ID,
            "schema_version": SCHEMA_VERSION,
            "status": ("stale" if invalid_code == "agent_materialization_handler_stale"
                       else "invalid"),
            "materialization_id": (document.get("materialization_id")
                                   if isinstance(document, dict) else None),
            "plan_input_fingerprint": None,
            "topology_fingerprint": None,
            "handler_set_fingerprint": handler_set_fingerprint(),
            "character_semantics": "agent_declared_unverified",
            "database_access": "none",
            "coverage": {},
            "uncovered_supported_node_ids": [],
            "issues": [issue],
        }
        receipt["receipt_fingerprint"] = _sha(receipt)
        return receipt

    current_plan_fp = plan_binding_fingerprint(plan)
    current_topology_fp = event_topology_fingerprint(topology)
    root_plan = document["plan_binding"]
    root_topology = document["topology_binding"]
    if (root_plan["plan_id"] != plan.get("plan_id")
            or type(plan.get("revision")) is not int
            or root_plan["revision"] > plan.get("revision")):
        issues.append(_issue("agent_materialization_plan_identity_stale",
                             "materialization.plan_binding",
                             "材料化输入不属于当前方案修订。"))
    if root_plan["input_fingerprint"] != current_plan_fp:
        issues.append(_issue("agent_materialization_plan_fingerprint_stale",
                             "materialization.plan_binding.input_fingerprint",
                             "材料化输入依赖的方案内容已经变化。"))
    if (root_topology["topology_id"] != topology["topology_id"]
            or root_topology["topology_fingerprint"] != current_topology_fp
            or root_topology["action_checkpoint_fingerprint"]
            != topology["action_checkpoint"]["fingerprint"]):
        issues.append(_issue("agent_materialization_topology_stale",
                             "materialization.topology_binding",
                             "材料化输入依赖的事件拓扑或动作检查点已经变化。"))
    current_handlers = handler_set_fingerprint()
    if document["handler_set_fingerprint"] != current_handlers:
        issues.append(_issue("agent_materialization_handler_set_stale",
                             "materialization.handler_set_fingerprint",
                             "材料化输入依赖的通用处理器集合已经变化。"))

    node_by_id = {row["node_id"]: row for row in topology["nodes"]}
    family_counts = {family: {"declared": 0, "covered": 0}
                     for family in dict.fromkeys(
                         entry["family"] for entry in document["entries"])}
    for index, entry in enumerate(document["entries"]):
        path = f"materialization.entries[{index}]"
        handler = HANDLER_REGISTRY[entry["family"]]
        family_counts[handler.family]["declared"] += len(entry["node_ids"])
        for node_id in entry["node_ids"]:
            node = node_by_id.get(node_id)
            if node is None or node["status"] == "excluded":
                issues.append(_issue("agent_materialization_node_missing", path + ".node_ids",
                                     "材料化条目指向不存在或已排除的节点。", node_id=node_id))
                continue
            if node["kind"] not in handler.node_kinds:
                issues.append(_issue("agent_materialization_node_kind_mismatch", path + ".node_ids",
                                     "通用处理器与事件节点类型不匹配。",
                                     node_id=node_id,
                                     expected_kinds=sorted(handler.node_kinds),
                                     actual_kind=node["kind"]))
                continue
            if not set(entry["source_ids"]).issubset(set(node["source_ids"])):
                issues.append(_issue("agent_materialization_source_binding_missing",
                                     path + ".source_ids",
                                     "材料化来源必须同时绑定到对应事件节点。", node_id=node_id))
                continue
            family_counts[handler.family]["covered"] += 1

        if handler.lint_topology is not None:
            issues.extend(handler.lint_topology(
                entry, path, topology, node_by_id))

    # Materialization is an Agent fallback over the automatic baseline, not a
    # second mandatory owner for every topology node whose *kind* happens to
    # have a generic handler.  The topology/binding graph may already assign a
    # node to a static profile or another automatic compiler.  Until the
    # topology contract gains an explicit ``materialization_required`` owner,
    # only nodes deliberately claimed by this document belong to this
    # materialization scope.  Each claim is still closed and uniquely owned by
    # the checks above.
    uncovered: list[str] = []

    topology_receipt = lint_event_topology(topology)
    if topology_receipt["status"] != "numeric_ready":
        issues.append(_issue("agent_materialization_topology_not_ready", "topology",
                             "事件拓扑尚未达到数值绑定就绪状态。",
                             topology_status=topology_receipt["status"]))

    stale = any(row["code"].endswith("_stale") for row in issues)
    status = "stale" if stale else ("partial" if issues else "ready")
    receipt = {
        "contract_id": RECEIPT_CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "materialization_id": document["materialization_id"],
        "plan_input_fingerprint": current_plan_fp,
        "topology_fingerprint": current_topology_fp,
        "handler_set_fingerprint": current_handlers,
        "character_semantics": "agent_declared",
        "database_access": "none",
        "coverage": family_counts,
        "uncovered_supported_node_ids": uncovered,
        "issues": issues[:512],
    }
    receipt["receipt_fingerprint"] = _sha(receipt)
    return receipt


def materialize_agent_execution(plan: dict, topology: dict, document: dict) -> dict:
    """Return the generic effective-input bundle when the bound document is ready."""
    receipt = lint_agent_execution_materialization(plan, topology, document)
    if receipt["status"] != "ready":
        _fail("materialization", "materialization is not ready for execution",
              "agent_materialization_not_ready")
    sources = {row["source_id"]: row for row in document["sources"]}
    entries = []
    for row in document["entries"]:
        handler = HANDLER_REGISTRY[row["family"]]
        entries.append({
            "entry_id": row["entry_id"],
            "family": row["family"],
            "handler": {"handler_id": handler.handler_id,
                        "version": row["handler_version"]},
            "node_ids": copy.deepcopy(row["node_ids"]),
            "coverage_keys": copy.deepcopy(row["coverage_keys"]),
            "payload": copy.deepcopy(row["payload"]),
            "source_fingerprints": [sources[source_id]["fingerprint"]
                                    for source_id in row["source_ids"]],
        })
    bundle = {
        "contract_id": EFFECTIVE_INPUT_CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "materialization_id": document["materialization_id"],
        "authority": EFFECTIVE_AUTHORITY,
        "plan_input_fingerprint": receipt["plan_input_fingerprint"],
        "topology_fingerprint": receipt["topology_fingerprint"],
        "handler_set_fingerprint": receipt["handler_set_fingerprint"],
        "entries": entries,
        "materialization_receipt_fingerprint": receipt["receipt_fingerprint"],
    }
    bundle["bundle_fingerprint"] = _sha(bundle)
    return bundle


def agent_execution_materialization_capabilities() -> dict:
    """Publish the App-shipped generic families without claiming role coverage."""
    from genshin_engine.state_runtime_mechanics import route_event_id

    from .agent_execution_runtime import (
        agent_execution_runtime_capabilities,
        runtime_handler_capability,
    )

    runtime_capabilities = agent_execution_runtime_capabilities()
    runtime = runtime_capabilities["families"]
    typed_buff_rule = {
        "id": "rule.buff.example", "name_zh": "示例通用增益",
        "owner": "actor.example", "source_id": "part.buff.example",
        "trigger": {"event": "action_start", "phase": "pre",
                    "action_ids": ["action.example"]},
        "beneficiaries": {"mode": "team", "scope": "locked"},
        "timing": {"delay": 0.0, "duration": 10.0, "cooldown": 0.0,
                   "cooldown_anchor": "success"},
        "stacking": {"max": 1, "add": 1, "policy": "refresh"},
        "effects": [{"id": "effect.buff.example",
                     "benefit_type_id": "benefit.dmg_bonus.cryo",
                     "value": 0.1, "domain": "self"}],
        "status": "assumed", "source": "Agent材料化示例",
    }
    state_rule_id = "rule.state.example"
    typed_state_rule = {
        "id": state_rule_id, "name_zh": "示例有限状态派发",
        "owner": "actor.example", "source_id": "part.state.example",
        "trigger": {"event": "condition", "phase": "pre",
                    "event_ids": [route_event_id("dispatch_rule", state_rule_id)]},
        "beneficiaries": {"mode": "self", "scope": "locked"},
        "timing": {"delay": 0.0, "duration": 1.0, "cooldown": 0.0,
                   "cooldown_anchor": "success"},
        "stacking": {"max": 1, "add": 1, "policy": "refresh"},
        "effects": [], "status": "assumed", "source": "Agent材料化示例",
    }
    templates = {
        'resource_operation': {'shared_pools': {'schema_version': 1, 'pools': [{
            'id': 'pool.example', 'name_zh': '示例资源', 'owner': 'actor.example',
            'source_id': 'part.pool.example', 'scope': 'team', 'capacity': 10,
            'initial': {'team': 0}, 'status': 'assumed', 'source': '工程示例'}],
            'operations': [{'id': 'grant.example', 'pool_id': 'pool.example',
                'source_id': 'part.grant.example', 'trigger': {'event': 'hit', 'phase': 'post'},
                'order': 0, 'operation': 'grant', 'amount': 1, 'targets': {'mode': 'all_pool_keys'},
                'cooldown': {'seconds': 0, 'anchor': 'success'}, 'status': 'assumed', 'source': '工程示例'}],
            'consumers': []}},
        "finite_sequence": {
            "sequence_id": "sequence.example", "steps": [{"step_id": "step.1",
                "offset_seconds": 0.0, "order": 0, "emits_node_ids": ["node.damage"]}],
            "interrupt_policy": "none"},
        "periodic_channel": {
            "profile_id": "profile.periodic.example",
            "channel_id": "channel.example",
            "schedule": {"kind": "equal_interval",
                "schedule_id": "schedule.example", "first_offset_seconds": 0.0,
                "interval_seconds": 1.0, "count": 1, "derive_count": None,
                "order": 1},
            "lifecycle": {"duration_seconds": 1.0, "refresh_policy": "none",
                "off_field_policy": "continue", "pause_resume_policy": "none"},
            "establishments": [{"establishment_id": "establishment.example",
                "anchor_node_id": "node.periodic", "order": 0}],
            "controls": [], "on_field_intervals": [],
            "dispatches": [{"dispatch_id": "dispatch.example",
                "emits_node_ids": ["node.damage"]}]},
        "application": {"element": "cryo", "gauge_units": 1.0,
            "application_mode": "timer_counter", "counter_tag": "ordinary",
            "rule_group": "ordinary.3hit.2_5s", "timer_seconds": 2.5,
            "hit_count": 3, "sequence": None, "target_ref": "enemy",
            "landed": True, "attempted": True},
        "buff": {"bundle_id": "buff.example", "rules": [typed_buff_rule],
            "initial_state": {"rules": [{"rule_id": "rule.buff.example",
                "cooldown_remaining": 0.0, "instances": []}]},
            "resource_ownership": [], "parts": [{"part_id": "part.buff.example",
                "rule_id": "rule.buff.example", "node_ids": ["node.buff"],
                "source_ids": ["source.example"], "note": ""}]},
        "resource": {"resource_id": "energy", "operation": "add", "amount": 1.0,
            "receiver_ref": "actor.example", "delivery": "immediate", "travel_seconds": None},
        "state": {"bundle_id": "state.example", "rules": [typed_state_rule],
            "initial_state": {"rules": [{"rule_id": state_rule_id,
                "cooldown_remaining": 0.0, "instances": []}]},
            "resource_ownership": [], "parts": [{"part_id": "part.state.example",
                "rule_id": state_rule_id, "node_ids": ["node.state"],
                "source_ids": ["source.example"], "note": ""}],
            "state_runtime": {"schema_version": 1, "lifecycles": [{
                "id": "lifecycle.state.example", "name_zh": "示例有限状态",
                "owner": "actor.example", "source_id": "part.state.example",
                "start": {"event": "action_start", "phase": "pre",
                          "action_ids": ["action.example"]},
                "start_order": 1, "establishment_order": 2, "end_order": 3,
                "establishment_delay_us": 0, "duration_us": 1000000,
                "restart": "replace", "pending_cancel": "never_after_success",
                "hooks": {"create": [], "established": [{"kind": "dispatch_rule",
                    "ref_id": state_rule_id, "order": 4}], "end": []},
                "status": "assumed", "source": "Agent材料化示例"}],
                "timers": []}},
        "mechanics_rule_bundle": {
            "bundle_id": "rules.example",
            "rules": [{
                "id": "rule.example", "name_zh": "示例通用规则",
                "owner": "actor.example", "source_id": "part.example",
                "trigger": {"event": "action_start", "phase": "pre",
                            "action_ids": ["action.example"]},
                "beneficiaries": {"mode": "self", "scope": "locked"},
                "timing": {"delay": 0.0, "duration": 1.0, "cooldown": 0.0,
                           "cooldown_anchor": "success"},
                "stacking": {"max": 1, "add": 1, "policy": "refresh"},
                "effects": [], "status": "assumed",
                "source": "Agent材料化示例",
            }],
            "initial_state": {"rules": [{
                "rule_id": "rule.example", "cooldown_remaining": 0.0,
                "instances": [],
            }]},
            "resource_ownership": [],
            "parts": [{
                "part_id": "part.example", "rule_id": "rule.example",
                "node_ids": ["node.mechanic"],
                "source_ids": ["source.example"], "note": "",
            }],
        },
    }
    return {
        "contract_id": CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "database_access": "none",
        "handler_set_fingerprint": handler_set_fingerprint(),
        "input_schema": materialization_input_schema(),
        "authoring": {'cli': 'equipment materialization --plan PLAN [--input DOCUMENT] [--output JSON]',
            'scaffold': 'Without --input, return a fresh empty envelope bound to this plan/topology; never change the plan.',
            'validate': 'With --input, run the same lint as gs_validate_agent_materialization; no DB or execution.',
            'native_resources': ['agent_execution_materialization', 'agent_execution_materialization_receipt'],
            'authority': 'technical binding only; Agent supplies and verifies all sources and payload facts'},
        "families": {row.family: {"handler_id": row.handler_id,
            "handler_version": row.version,
            "supported_handler_versions": list(row.supported_versions),
            "node_kind": row.node_kind,
            "accepted_node_kinds": sorted(row.node_kinds),
            "agent_input": "materialization_supported",
            "runtime_execution": runtime[row.family],
            "runtime_handler_capability": runtime_handler_capability(
                row.family, row.version),
            "runtime_version_capabilities": {
                str(version): runtime_handler_capability(row.family, version)
                for version in row.supported_versions
            },
            "minimal_payload": templates[row.family],
            **({"capability_matrix": runtime_capabilities["periodic_channel_v2"],
                'payload_schema': periodic_payload_schema(),
                'schedule_authoring': {
                    'derive_count': {'enum': [None, *sorted(DERIVED_COUNT_POLICIES)]},
                    'explicit_count': 'count=1..256 and derive_count=null; finite count is not extended by reset_duration',
                    'derived_count': 'count=null and derive_count=through_instance_end_inclusive; first/interval must close exactly at the effective lifecycle end, including refresh/extension',
                    'while_active_inclusive': 'count=null; exact integer ticks first+n*interval <= effective lifecycle end; floor count, no pulse at a nonaligned end; refresh/extension preserves phase',
                    'time_precision': 'integer microseconds, no temporal rounding to satisfy closure'},
                'topology_bindings': {'primary_establishment': 'anchor_node_id is the single owner node of this entry',
                    'extra_establishments_and_controls': {'from_node_id': 'the establishment/control anchor',
                        'to_node_id': 'the periodic owner node', 'status': 'bound',
                        'kind_options': list(_PERIODIC_CONTROL_EDGE_KINDS)},
                    'source_binding': 'all anchor source_ids must be present in the materialization entry'}}
               if row.family == "periodic_channel" else {})}
            for row in _HANDLERS},
        "limits": {"bytes": MAX_BYTES, "sources": MAX_SOURCES,
                   "entries": MAX_ENTRIES, "nodes_per_entry": MAX_NODE_REFS,
                   "periodic_occurrences": MAX_PERIODIC_OCCURRENCES},
        "limitations": [
            "处理器只验证并装配通用机制族，不核实角色特有游戏事实。",
            "角色、技能、命座和装备身份不能选择处理器算法。",
            "材料化输入不得携带结果、总伤或DPS。",
        ],
    }


__all__ = [
    "CONTRACT_ID",
    "EFFECTIVE_INPUT_CONTRACT_ID",
    "HANDLER_REGISTRY",
    "RECEIPT_CONTRACT_ID",
    "AgentExecutionMaterializationError",
    "agent_execution_materialization_capabilities",
    "handler_set_fingerprint",
    "lint_agent_execution_materialization",
    "materialize_agent_execution",
    "new_agent_execution_materialization",
    "plan_binding_fingerprint",
    "validate_agent_execution_materialization",
]
