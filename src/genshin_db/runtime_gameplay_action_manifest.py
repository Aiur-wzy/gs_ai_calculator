"""Exact, fail-closed lookup for independent gameplay action manifests."""
from __future__ import annotations

import copy
import json
from decimal import Decimal
from functools import lru_cache
from importlib import resources

from .gameplay_action_manifest import (
    CONTRACT_ID,
    SCHEMA_VERSION,
    validate_bundle,
    validate_selector,
)
from .runtime_catalog import fingerprint


@lru_cache(maxsize=1)
def load_gameplay_action_manifest_bundle():
    value = json.loads(resources.files("genshin_runtime").joinpath(
        "resources/gameplay_action_manifests.json").read_text("utf8"))
    validate_bundle(value)
    return copy.deepcopy(value)


def _snapshot_view(snapshot):
    normalization = snapshot.get("normalization") or {}
    result = {key: copy.deepcopy(snapshot[key]) for key in (
        "version_line", "snapshot_id", "upstream_commit", "manifest_fingerprint", "indexer_version",
        "schema_version", "normalization_version", "mapping_fingerprint") if key in snapshot}
    result["normalization"] = copy.deepcopy(normalization)
    return result


def _dataset_matches(dataset, snapshot):
    current = _snapshot_view(snapshot)
    if (current.get("version_line") != dataset["version_line"]
            or current.get("upstream_commit") != dataset["upstream_commit"]
            or (current.get("normalization") or {}).get("profile_id")
            != dataset["normalization_profile_id"]):
        return False
    return dataset["snapshot_id"] is None or dataset["snapshot_id"] == current.get("snapshot_id")


def _ticks(value) -> int:
    return int(Decimal(str(value)) * 1_000_000)


def materialization_expectation(manifest):
    """Return the bounded independent component/occurrence expectation."""
    if manifest is None:
        return {"status": "unknown", "topology_kind": None, "components": [],
                "occurrences": [], "channels": [], "lifecycle": None}
    components = [{"component_id": row["component_id"],
        "critical_domains": copy.deepcopy(row["critical_domains"])}
        for row in manifest["components"]]
    topology = manifest["topology"]
    if topology["kind"] == "finite_sequence":
        occurrences = [{"occurrence_id": row["step_id"], "ordinal": row["ordinal"],
            "component_id": row["component_id"], "offset_ticks": _ticks(row["offset_seconds"]),
            "order": row["order"], "terminal": row["step_id"] == topology["terminal_step_id"]}
            for row in topology["steps"]]
        return {"status": "ready", "topology_kind": topology["kind"],
            "components": components, "occurrences": occurrences, "channels": [],
            "lifecycle": None, "interruption_policy": topology["interruption_policy"]}
    lifecycle = topology["lifecycle"]
    pending_policies = [key for key in (
        "refresh_policy", "pause_resume_policy", "off_field_policy")
        if lifecycle[key] == "pending"]
    if pending_policies:
        return {"status": "pending", "topology_kind": topology["kind"],
            "components": components, "occurrences": [], "channels": [],
            "lifecycle": copy.deepcopy(lifecycle),
            "issue": "lifecycle policies remain pending: " + ", ".join(pending_policies)}
    end_ticks = _ticks(lifecycle["instance_end_offset_seconds"])
    channels, occurrences = [], []
    for channel in topology["channels"]:
        schedule = channel["schedule"]
        item = {"channel_id": channel["channel_id"], "component_id": channel["component_id"],
                "schedule": copy.deepcopy(schedule),
                "deliveries": copy.deepcopy(channel.get("deliveries", []))}
        if schedule["kind"] == "fixed":
            item["expected_occurrences"] = 1
            occurrences.append({"occurrence_id": channel["channel_id"] + ":1", "ordinal": 1,
                "component_id": channel["component_id"], "channel_id": channel["channel_id"],
                "offset_ticks": _ticks(schedule["offset_seconds"]), "order": None, "terminal": False,
                "deliveries": copy.deepcopy(channel.get("deliveries", []))})
        elif schedule["kind"] == "explicit_offsets":
            offsets = [_ticks(value) for value in schedule["offsets_seconds"]]
            item["expected_occurrences"] = len(offsets)
            for ordinal, offset in enumerate(offsets, 1):
                occurrences.append({"occurrence_id": f"{channel['channel_id']}:{ordinal}",
                    "ordinal": ordinal, "component_id": channel["component_id"],
                    "channel_id": channel["channel_id"], "offset_ticks": offset,
                    "order": None, "terminal": False,
                    "deliveries": copy.deepcopy(channel.get("deliveries", []))})
        else:
            first, interval = _ticks(schedule["first_offset_seconds"]), _ticks(schedule["interval_seconds"])
            count = schedule["count"]
            if count is None:
                span = end_ticks - first
                if span < 0 or span % interval:
                    return {"status": "pending", "topology_kind": topology["kind"],
                        "components": components, "occurrences": [], "channels": [],
                        "lifecycle": copy.deepcopy(lifecycle),
                        "issue": "derived periodic count is not integral within lifecycle"}
                count = span // interval + 1
                if count > 256:
                    return {"status": "pending", "topology_kind": topology["kind"],
                        "components": components, "occurrences": [], "channels": [],
                        "lifecycle": copy.deepcopy(lifecycle),
                        "issue": "derived periodic count exceeds the bounded occurrence limit"}
            item["expected_occurrences"] = count
            for ordinal in range(1, count + 1):
                occurrences.append({"occurrence_id": f"{channel['channel_id']}:{ordinal}",
                    "ordinal": ordinal, "component_id": channel["component_id"],
                    "channel_id": channel["channel_id"],
                    "offset_ticks": first + (ordinal - 1) * interval,
                    "order": None, "terminal": False,
                    "deliveries": copy.deepcopy(channel.get("deliveries", []))})
        channels.append(item)
    return {"status": "ready", "topology_kind": topology["kind"],
        "components": components, "occurrences": occurrences, "channels": channels,
        "lifecycle": copy.deepcopy(lifecycle)}


def resolve_gameplay_action_manifest(bundle, *, selector, snapshot):
    validate_bundle(bundle)
    validate_selector(selector)
    result = {"contract_id": CONTRACT_ID, "schema_version": SCHEMA_VERSION,
        "status": "pending", "selector": copy.deepcopy(selector), "snapshot": _snapshot_view(snapshot),
        "manifest": None, "manifest_fingerprint": None,
        "expectation": materialization_expectation(None), "matched_manifest_ids": [], "issues": []}
    matched = [row for row in bundle["manifests"] if row["selector"] == selector]
    result["matched_manifest_ids"] = [row["manifest_id"] for row in matched]
    if not matched:
        result["issues"].append({"code": "gameplay_action_manifest_missing",
            "message": "没有与完整动作身份精确匹配的独立游戏组件清单"})
    elif len(matched) > 1:
        result["issues"].append({"code": "gameplay_action_manifest_ambiguous",
            "message": "完整动作身份匹配到多份游戏组件清单"})
    else:
        manifest = matched[0]
        if not _dataset_matches(manifest["dataset"], snapshot):
            result["issues"].append({"code": "gameplay_action_manifest_stale",
                "message": "游戏组件清单与当前运行时快照不一致"})
        elif manifest["status"] != "confirmed" or manifest["review"]["status"] != "reviewed":
            result["issues"].append({"code": "gameplay_action_manifest_unreviewed",
                "message": "游戏组件清单尚未完成审查"})
        else:
            expectation = materialization_expectation(manifest)
            result.update(manifest=copy.deepcopy(manifest),
                manifest_fingerprint=fingerprint(manifest), expectation=expectation)
            if expectation["status"] == "ready":
                result["status"] = "ready"
            else:
                result["issues"].append({"code": "gameplay_action_manifest_topology_pending",
                    "message": expectation.get("issue", "游戏组件清单无法展开")})
    result["content_fingerprint"] = fingerprint(result)
    return result


def get_runtime_gameplay_action_manifest(db_path, *, selector, snapshot):
    """Resolve from the packaged bundle; db_path is kept for runtime API symmetry."""
    del db_path
    return resolve_gameplay_action_manifest(
        load_gameplay_action_manifest_bundle(), selector=selector, snapshot=snapshot)
