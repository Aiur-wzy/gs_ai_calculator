"""Independent reviewed gameplay topology for one exact semantic action.

The action-dispatch profile answers how known attack components are sourced.
This contract answers a different question: which components and occurrences
the game action is expected to produce.  Keeping the two bundles independent
prevents a truncated dispatch profile from certifying its own completeness.
"""
from __future__ import annotations

import json

from .action_dispatch_schema import (
    DERIVED_COUNT_POLICY,
    MAX_BUNDLE_BYTES,
    _id,
    _integer,
    _time,
    _validate_dataset,
    _validate_field_evidence,
    _validate_review,
    validate_selector,
)

CONTRACT_ID = "gameplay-action-manifest-v1"
SCHEMA_VERSION = 1
MAX_MANIFESTS = 256
MAX_COMPONENTS = 64
MAX_STEPS = 256
MAX_CHANNELS = 32
CRITICAL_DOMAINS = frozenset(("damage", "application", "resource", "buff", "state"))
INTERRUPTION_POLICIES = frozenset(("none", "cancel_with_source_action", "scenario_required"))
REFRESH_POLICIES = frozenset(("none", "replace_previous", "reset_duration", "extend_duration", "pending"))
PAUSE_POLICIES = frozenset(("none", "pause_preserve_phase", "pause_reset_phase", "pending"))
OFF_FIELD_POLICIES = frozenset(("continue", "stop", "pending"))
DELIVERY_KINDS = frozenset(("damage", "application", "resource"))
RESOURCE_EVENT_CONTRACT = "rotation-resource-event-v1"
RESOURCE_EVENT_TYPES = frozenset(("energy_spawn", "energy_direct", "cooldown_change"))


class GameplayActionManifestError(ValueError):
    """The independent gameplay topology violates its closed contract."""


def _error(path: str, message: str) -> None:
    raise GameplayActionManifestError(f"{path}: {message}")


def _object(value, path: str, required: set[str], optional: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        _error(path, "must be an object")
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        _error(path, "missing fields: " + ", ".join(sorted(missing)))
    if extra:
        _error(path, "unknown fields: " + ", ".join(sorted(extra)))
    return value


def _validate_component(value, path: str) -> None:
    row = _object(value, path, {"component_id", "critical_domains", "evidence"})
    _id(row["component_id"], path + ".component_id")
    domains = row["critical_domains"]
    if (not isinstance(domains, list) or not domains or len(domains) > len(CRITICAL_DOMAINS)
            or len(domains) != len(set(domains)) or not set(domains) <= CRITICAL_DOMAINS):
        _error(path + ".critical_domains", "must contain unique supported gameplay domains")
    _validate_field_evidence(row["evidence"], path + ".evidence")


def _validate_finite(value, path: str, components: set[str]) -> None:
    row = _object(value, path,
        {"kind", "steps", "terminal_step_id", "interruption_policy", "evidence"})
    if row["kind"] != "finite_sequence":
        _error(path + ".kind", "must be finite_sequence")
    if row["interruption_policy"] not in INTERRUPTION_POLICIES:
        _error(path + ".interruption_policy", "unsupported interruption policy")
    _validate_field_evidence(row["evidence"], path + ".evidence")
    steps = row["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        _error(path + ".steps", "must contain 1..256 steps")
    seen_ids: set[str] = set()
    seen_ordinals: set[int] = set()
    seen_positions: set[tuple[int, int]] = set()
    used: set[str] = set()
    positions: list[tuple[int, int, int, str]] = []
    for index, step_value in enumerate(steps):
        step_path = f"{path}.steps[{index}]"
        step = _object(step_value, step_path,
            {"step_id", "ordinal", "component_id", "offset_seconds", "order"})
        step_id = _id(step["step_id"], step_path + ".step_id")
        ordinal = _integer(step["ordinal"], step_path + ".ordinal", 1, MAX_STEPS)
        component = _id(step["component_id"], step_path + ".component_id")
        if component not in components:
            _error(step_path + ".component_id", "does not reference a declared component")
        offset = _time(step["offset_seconds"], step_path + ".offset_seconds", zero=True)
        order = _integer(step["order"], step_path + ".order", -10**9, 10**9)
        micros = round(float(offset) * 1_000_000)
        if step_id in seen_ids or ordinal in seen_ordinals:
            _error(step_path, "step_id and ordinal must each be unique")
        if (micros, order) in seen_positions:
            _error(step_path, "steps at the same microsecond must have distinct order")
        seen_ids.add(step_id)
        seen_ordinals.add(ordinal)
        seen_positions.add((micros, order))
        used.add(component)
        positions.append((micros, order, ordinal, step_id))
    if seen_ordinals != set(range(1, len(steps) + 1)):
        _error(path + ".steps", "ordinals must be contiguous from 1")
    if [row[2] for row in sorted(positions)] != list(range(1, len(steps) + 1)):
        _error(path + ".steps", "ordinals must follow chronological offset and order")
    if used != components:
        _error(path + ".steps", "every declared component must be used by at least one step")
    terminal = row["terminal_step_id"]
    if terminal is not None:
        _id(terminal, path + ".terminal_step_id")
        if terminal not in seen_ids:
            _error(path + ".terminal_step_id", "must reference a step")
        if terminal != max(positions)[3]:
            _error(path + ".terminal_step_id", "must reference the last ordered step")


def _validate_channel_schedule(value, path: str) -> None:
    row = _object(value, path, {"kind"},
        {"offset_seconds", "offsets_seconds", "first_offset_seconds", "interval_seconds", "count", "derive_count"})
    if row["kind"] == "fixed":
        if set(row) != {"kind", "offset_seconds"}:
            _error(path, "fixed schedule only accepts offset_seconds")
        _time(row["offset_seconds"], path + ".offset_seconds", zero=True)
        return
    if row["kind"] == "explicit_offsets":
        if set(row) != {"kind", "offsets_seconds"}:
            _error(path, "explicit_offsets schedule only accepts offsets_seconds")
        offsets = row["offsets_seconds"]
        if not isinstance(offsets, list) or not 1 <= len(offsets) <= MAX_STEPS:
            _error(path + ".offsets_seconds", "must contain 1..256 offsets")
        ticks = []
        for index, offset_value in enumerate(offsets):
            offset = _time(offset_value, f"{path}.offsets_seconds[{index}]", zero=True)
            ticks.append(round(float(offset) * 1_000_000))
        if ticks != sorted(ticks) or len(ticks) != len(set(ticks)):
            _error(path + ".offsets_seconds", "must be strictly increasing at integer-microsecond precision")
        return
    if row["kind"] != "periodic":
        _error(path + ".kind", "must be fixed, explicit_offsets, or periodic")
    required = {"kind", "first_offset_seconds", "interval_seconds", "count"}
    if not required <= set(row):
        _error(path, "periodic schedule lacks required timing fields")
    _time(row["first_offset_seconds"], path + ".first_offset_seconds", zero=True)
    _time(row["interval_seconds"], path + ".interval_seconds")
    if row["count"] is None:
        if row.get("derive_count") != DERIVED_COUNT_POLICY:
            _error(path + ".derive_count", "count=null requires inclusive instance-end derivation")
    else:
        _integer(row["count"], path + ".count", 1, MAX_STEPS)
        if "derive_count" in row:
            _error(path + ".derive_count", "must be omitted for explicit count")


def _validate_delivery(value, path: str) -> None:
    row = _object(value, path, {"kind"}, {"contract", "event"})
    kind = row["kind"]
    if kind not in DELIVERY_KINDS:
        _error(path + ".kind", "unsupported occurrence delivery")
    if kind != "resource":
        if set(row) != {"kind"}:
            _error(path, "damage/application delivery accepts no inferred payload")
        return
    if row.get("contract") != RESOURCE_EVENT_CONTRACT:
        _error(path + ".contract", "resource delivery must reference the closed rotation event contract")
    event = _object(row.get("event"), path + ".event", {"type", "template_id"})
    if event["type"] not in RESOURCE_EVENT_TYPES:
        _error(path + ".event.type", "unsupported rotation resource event type")
    _id(event["template_id"], path + ".event.template_id")


def _validate_periodic(value, path: str, components: dict[str, set[str]]) -> None:
    row = _object(value, path, {"kind", "channels", "lifecycle", "evidence"})
    if row["kind"] != "periodic_channels":
        _error(path + ".kind", "must be periodic_channels")
    _validate_field_evidence(row["evidence"], path + ".evidence")
    channels = row["channels"]
    if not isinstance(channels, list) or not 1 <= len(channels) <= MAX_CHANNELS:
        _error(path + ".channels", "must contain 1..32 channels")
    channel_ids: set[str] = set()
    used: set[str] = set()
    for index, channel_value in enumerate(channels):
        channel_path = f"{path}.channels[{index}]"
        channel = _object(channel_value, channel_path, {"channel_id", "component_id", "schedule"},
                          {"deliveries"})
        channel_id = _id(channel["channel_id"], channel_path + ".channel_id")
        component = _id(channel["component_id"], channel_path + ".component_id")
        if channel_id in channel_ids:
            _error(channel_path + ".channel_id", "must be unique")
        if component not in components:
            _error(channel_path + ".component_id", "does not reference a declared component")
        channel_ids.add(channel_id)
        used.add(component)
        _validate_channel_schedule(channel["schedule"], channel_path + ".schedule")
        deliveries = channel.get("deliveries", [])
        if (not isinstance(deliveries, list) or len(deliveries) > 3
                or len({item.get("kind") for item in deliveries if isinstance(item, dict)})
                != len(deliveries)):
            _error(channel_path + ".deliveries", "must contain at most one declaration per delivery kind")
        for delivery_index, delivery in enumerate(deliveries):
            _validate_delivery(delivery, f"{channel_path}.deliveries[{delivery_index}]")
            if delivery["kind"] not in components[component]:
                _error(f"{channel_path}.deliveries[{delivery_index}].kind",
                       "is not declared as a critical domain of the component")
    if used != set(components):
        _error(path + ".channels", "every declared component must be used by at least one channel")
    lifecycle = _object(row["lifecycle"], path + ".lifecycle", {
        "establishment_offset_seconds", "instance_end_offset_seconds", "refresh_policy",
        "pause_resume_policy", "off_field_policy", "evidence",
    })
    establish = _time(lifecycle["establishment_offset_seconds"],
        path + ".lifecycle.establishment_offset_seconds", zero=True)
    end = _time(lifecycle["instance_end_offset_seconds"],
        path + ".lifecycle.instance_end_offset_seconds")
    if float(end) < float(establish):
        _error(path + ".lifecycle", "instance end must not precede establishment")
    if lifecycle["refresh_policy"] not in REFRESH_POLICIES:
        _error(path + ".lifecycle.refresh_policy", "unsupported refresh policy")
    if lifecycle["pause_resume_policy"] not in PAUSE_POLICIES:
        _error(path + ".lifecycle.pause_resume_policy", "unsupported pause/resume policy")
    if lifecycle["off_field_policy"] not in OFF_FIELD_POLICIES:
        _error(path + ".lifecycle.off_field_policy", "unsupported off-field policy")
    _validate_field_evidence(lifecycle["evidence"], path + ".lifecycle.evidence")
    start_value, end_value = float(establish), float(end)
    for index, channel in enumerate(channels):
        schedule = channel["schedule"]
        schedule_path = f"{path}.channels[{index}].schedule"
        if schedule["kind"] == "fixed":
            first = last = float(schedule["offset_seconds"])
        elif schedule["kind"] == "explicit_offsets":
            first = float(schedule["offsets_seconds"][0])
            last = float(schedule["offsets_seconds"][-1])
        else:
            first = float(schedule["first_offset_seconds"])
            count = schedule["count"]
            if count is None:
                first_ticks = round(first * 1_000_000)
                end_ticks = round(end_value * 1_000_000)
                interval_ticks = round(float(schedule["interval_seconds"]) * 1_000_000)
                if first_ticks > end_ticks or (end_ticks - first_ticks) % interval_ticks:
                    _error(schedule_path, "derived count must close exactly at lifecycle end")
                count = (end_ticks - first_ticks) // interval_ticks + 1
                if count > MAX_STEPS:
                    _error(schedule_path, "derived count exceeds 256 occurrences")
            last = first + (count - 1) * float(schedule["interval_seconds"])
        if first < start_value or first > end_value or last > end_value:
            _error(schedule_path, "all occurrences must stay within the lifecycle")


def validate_manifest(value, path: str = "manifest") -> dict:
    row = _object(value, path,
        {"manifest_id", "status", "selector", "components", "topology", "dataset", "review"})
    _id(row["manifest_id"], path + ".manifest_id")
    if row["status"] not in ("confirmed", "pending"):
        _error(path + ".status", "must be confirmed or pending")
    selector = validate_selector(row["selector"], path + ".selector")
    components = row["components"]
    if not isinstance(components, list) or not 1 <= len(components) <= MAX_COMPONENTS:
        _error(path + ".components", "must contain 1..64 components")
    for index, component in enumerate(components):
        _validate_component(component, f"{path}.components[{index}]")
    component_ids = [component["component_id"] for component in components]
    if len(component_ids) != len(set(component_ids)):
        _error(path + ".components", "component_id values must be unique")
    topology = row["topology"]
    if not isinstance(topology, dict):
        _error(path + ".topology", "must be an object")
    if topology.get("kind") == "finite_sequence":
        _validate_finite(topology, path + ".topology", set(component_ids))
    elif topology.get("kind") == "periodic_channels":
        _validate_periodic(topology, path + ".topology", {
            component["component_id"]: set(component["critical_domains"])
            for component in components})
    else:
        _error(path + ".topology.kind", "unsupported topology kind")
    _validate_dataset(row["dataset"], path + ".dataset")
    _validate_review(row["review"], path + ".review")
    if row["dataset"]["version_line"] != selector["version_line"]:
        _error(path, "selector and dataset version_line must match")
    if row["status"] == "confirmed" and row["review"]["status"] != "reviewed":
        _error(path, "confirmed manifest requires reviewed status")
    if row["status"] == "confirmed":
        evidence_rows = [component["evidence"] for component in components]
        evidence_rows.append(topology["evidence"])
        if topology["kind"] == "periodic_channels":
            evidence_rows.append(topology["lifecycle"]["evidence"])
        if any(evidence["status"] != "confirmed" for evidence in evidence_rows):
            _error(path, "confirmed manifest requires confirmed component and topology evidence")
    return row
def validate_bundle(value) -> dict:
    row = _object(value, "bundle", {"schema_version", "contract_id", "manifests"})
    if row["schema_version"] != SCHEMA_VERSION:
        _error("bundle.schema_version", "unsupported schema version")
    if row["contract_id"] != CONTRACT_ID:
        _error("bundle.contract_id", "unsupported contract")
    manifests = row["manifests"]
    if not isinstance(manifests, list) or len(manifests) > MAX_MANIFESTS:
        _error("bundle.manifests", "must be a bounded list")
    for index, manifest in enumerate(manifests):
        validate_manifest(manifest, f"bundle.manifests[{index}]")
    ids = [manifest["manifest_id"] for manifest in manifests]
    selectors = [json.dumps(manifest["selector"], sort_keys=True, separators=(",", ":"))
                 for manifest in manifests]
    if len(ids) != len(set(ids)):
        _error("bundle.manifests", "manifest_id values must be unique")
    if len(selectors) != len(set(selectors)):
        _error("bundle.manifests", "exact selectors must be unique")
    try:
        encoded = json.dumps(row, ensure_ascii=False, allow_nan=False,
            separators=(",", ":")).encode("utf8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise GameplayActionManifestError("bundle: must be finite JSON") from exc
    if len(encoded) > MAX_BUNDLE_BYTES:
        _error("bundle", "encoded bundle exceeds the size limit")
    return row
