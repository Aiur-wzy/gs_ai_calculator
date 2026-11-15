"""Explain how much of an action axis is assembled and simulated automatically.

The receipt is intentionally descriptive.  It never invents hits, reactions or
gameplay facts; it only classifies native input and fresh compiler receipts.
"""
from __future__ import annotations

import copy
import json
from collections import defaultdict
from decimal import Decimal

from .action_event_schema import APPLICATION_KIND
from .finite_action_schema import KIND as FINITE_APPLICATION_KIND
from .periodic_schema import APPLICATION_KIND as MULTI_APPLICATION_KIND

ATTACK_ACTION_TYPES = frozenset({
    "action.normal_attack",
    "action.charged_attack",
    "action.plunging_attack",
    "action.elemental_skill",
    "action.elemental_burst",
})
NON_DAMAGE_ACTION_TYPES = frozenset({"action.switch", "action.wait"})
APPLICATION_KINDS = frozenset({
    APPLICATION_KIND, MULTI_APPLICATION_KIND, FINITE_APPLICATION_KIND})
MATERIALIZATION_RECEIPT_CONTRACT = "action-materialization-receipt-v1"
MATERIALIZATION_PROOF_MAX_BYTES = 2 * 1024 * 1024


def _string(value):
    return value if isinstance(value, str) and value else None


def _bounded_string(value, limit):
    return value if isinstance(value, str) and 0 < len(value) <= limit else None


def _positive_int(value):
    return value if type(value) is int and value > 0 else None


def _valid_domains(value, allowed):
    return (isinstance(value, list) and len(value) <= len(allowed)
            and all(isinstance(item, str) and item in allowed for item in value)
            and len(value) == len(set(value)))


def _bounded_json(value, *, label):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode("utf8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError(f"{label}必须是有界有限JSON") from exc
    if len(encoded) > MATERIALIZATION_PROOF_MAX_BYTES:
        raise ValueError(f"{label}超过2MiB上限")


def _coverage_expectation(record: dict):
    """Return normalized expectations only when independent source material exists."""
    manifest = record.get("coverage_manifest")
    # A digest and a normalized projection supplied by the dispatch record are
    # not independent evidence: both could have been copied from the same
    # truncated profile.  Recompute from the bounded, closed raw manifest.
    if manifest is None:
        return None
    expectation = record.get("coverage_expectation")
    if not isinstance(expectation, dict):
        return {}
    try:
        from genshin_db.gameplay_action_manifest import validate_manifest
        from genshin_db.runtime_catalog import fingerprint
        from genshin_db.runtime_gameplay_action_manifest import (
            materialization_expectation,
        )
        validate_manifest(manifest)
        manifest_fingerprint = fingerprint(manifest)
        if (manifest.get("selector") != record.get("selector")
                or manifest.get("manifest_id") != record.get(
                    "declared_coverage_manifest_id")
                or manifest_fingerprint != record.get(
                    "expected_coverage_manifest_fingerprint")
                or manifest.get("manifest_id") != record.get("coverage_manifest_id")
                or manifest_fingerprint != record.get("coverage_manifest_fingerprint")
                or fingerprint(materialization_expectation(manifest)) != fingerprint(expectation)):
            return {}
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError):
        return {}
    return expectation


def _component_profile_map(record: dict) -> dict[str, str]:
    materialized = record.get("materialized") or {}
    components = materialized.get("component_ids") or []
    profiles = materialized.get("profile_ids") or []
    result: dict[str, str] = {}
    explicit = materialized.get("component_profiles")
    if isinstance(explicit, dict):
        for component_id, profile_id in explicit.items():
            if _string(component_id) and _string(profile_id):
                result[profile_id] = component_id
    elif isinstance(explicit, list):
        for row in explicit:
            if isinstance(row, dict) and _string(row.get("component_id")) and _string(row.get("profile_id")):
                result[row["profile_id"]] = row["component_id"]
    if not result and len(components) == len(profiles):
        result.update(zip(profiles, components, strict=True))
    if not result and len(components) == 1:
        result.update((profile_id, components[0]) for profile_id in profiles)
    return result


def _event_record_index(event_records) -> dict[str, list[tuple[dict, dict[str, list[dict]]]]]:
    """Index bounded compiler receipts once instead of rescanning per action."""
    result: dict[str, list[tuple[dict, dict[str, list[dict]]]]] = defaultdict(list)
    for record in event_records or ():
        if not isinstance(record, dict) or not isinstance(record.get("profile_id"), str):
            continue
        by_action: dict[str, list[dict]] = defaultdict(list)
        generated = record.get("generated", [])
        if isinstance(generated, list):
            for item in generated:
                if isinstance(item, dict) and isinstance(item.get("action_id"), str):
                    by_action[item["action_id"]].append(item)
        result[record["profile_id"]].append((record, by_action))
    return result


def _event_records_for_profiles(profile_ids, event_index):
    return [item for profile_id in profile_ids
            for item in event_index.get(profile_id, [])]


def _actual_occurrences(action_id: str, record: dict, event_index) -> tuple[list[dict], list[dict]]:
    materialized = record.get("materialized") or {}
    profile_ids = set(materialized.get("profile_ids") or [])
    profile_components = _component_profile_map(record)
    actual, issues = [], []
    seen_hit_ids = set()
    for event_record, by_action in _event_records_for_profiles(profile_ids, event_index):
        profile_id = event_record["profile_id"]
        if event_record.get("status") != "ready":
            issues.append({
                "code": "action_materialization_event_pending",
                "path": f"rotation_model.actions.{action_id}",
                "message": f"动作事件档案 {profile_id} 未成功生成。",
            })
            continue
        for item in by_action.get(action_id, []):
            component_id = _string(item.get("component_id"))
            channel_id = _string(item.get("channel_id"))
            component_id = component_id or channel_id or profile_components.get(profile_id)
            occurrence_id = (_string(item.get("occurrence_id"))
                             or _string(item.get("step_id")))
            if occurrence_id is None and channel_id is not None and _positive_int(item.get("ordinal")):
                occurrence_id = f"{channel_id}:{item['ordinal']}"
            occurrence = {
                "action_id": action_id,
                "component_id": component_id,
                "profile_id": profile_id,
                "hit_id": _string(item.get("hit_id")),
            }
            if occurrence_id is not None:
                occurrence["occurrence_id"] = occurrence_id
            if channel_id is not None:
                occurrence["channel_id"] = channel_id
            ordinal = _positive_int(item.get("ordinal"))
            if ordinal is not None:
                occurrence["ordinal"] = ordinal
            # ``order`` on a generated hit is its global scene ordering.  The
            # independent finite manifest's order is local to the action, so
            # only the compiler's explicit topology proof can satisfy it.
            if type(item.get("topology_order")) is int:
                occurrence["order"] = item["topology_order"]
            if type(item.get("terminal")) is bool:
                occurrence["terminal"] = item["terminal"]
            if type(item.get("offset_ticks")) is int and item["offset_ticks"] >= 0:
                occurrence["offset_ticks"] = item["offset_ticks"]
            actual.append(occurrence)
            hit_id = occurrence["hit_id"]
            if hit_id is None or hit_id in seen_hit_ids:
                issues.append({
                    "code": "action_materialization_occurrence_identity_invalid",
                    "path": f"rotation_model.actions.{action_id}",
                    "message": "派生事件缺少唯一 hit_id，不能作为独立物化发生项。",
                })
            else:
                seen_hit_ids.add(hit_id)
    return actual, issues


def _occurrence_matches(expected: dict, actual: dict) -> bool:
    if expected["component_id"] != actual.get("component_id"):
        return False
    return all(expected[key] == actual.get(key) for key in
               ("occurrence_id", "channel_id", "ordinal", "offset_ticks", "order", "terminal")
               if key in expected)


def _compare_occurrences(expected: list[dict], actual: list[dict]):
    """Compare occurrence proofs in linear time by their stable identity.

    Independent manifests require an ``occurrence_id`` and component ownership
    for every expected row.  Generated rows use that same pair, so scanning all
    remaining rows for each expectation is both unnecessary and unsafe for the
    bounded 32-channel x 256-occurrence periodic case.  Keep original indexes
    so unexpected rows retain deterministic compiler order.
    """
    by_identity: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, occurrence in enumerate(actual):
        component_id = occurrence.get("component_id")
        occurrence_id = occurrence.get("occurrence_id")
        if isinstance(component_id, str) and isinstance(occurrence_id, str):
            by_identity[(component_id, occurrence_id)].append(index)
    matched = set()
    unresolved = []
    for occurrence in expected:
        identity = (occurrence.get("component_id"), occurrence.get("occurrence_id"))
        candidates = by_identity.get(identity, [])
        # A repeated stable identity is invalid source material.  Do not pick a
        # favourable duplicate: leaving all candidates unexpected guarantees a
        # diagnostic result even if one duplicate happens to match.
        if len(candidates) != 1 or not _occurrence_matches(
                occurrence, actual[candidates[0]]):
            unresolved.append(copy.deepcopy(occurrence))
        else:
            matched.add(candidates[0])
    unexpected = [copy.deepcopy(occurrence) for index, occurrence in enumerate(actual)
                  if index not in matched]
    return unresolved, unexpected


def _ticks(value) -> int:
    scaled = Decimal(str(value)) * 1_000_000
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        raise ValueError("action time must be exactly representable in microseconds")
    return int(scaled)


def _lifecycle_projection_integrity(action, relevant):
    projections = [row.get("lifecycle_projection") for row in relevant
                   if row.get("lifecycle_projection") is not None]
    if not projections:
        return None, []
    issue = {"code": "lifecycle_projection_invalid",
        "path": f"rotation_model.actions.{action.get('id')}",
        "message": "统一周期生命周期投影与编译事件或代次回执不一致。"}
    if len(projections) != 1:
        return None, [issue]
    projection = projections[0]
    if (not isinstance(projection, dict)
            or projection.get("contract") != "periodic-lifecycle-projection-v1"
            or projection.get("status") != "ready"):
        return None, [issue]
    generations = projection.get("generations")
    occurrences = projection.get("occurrences")
    cancelled = projection.get("cancelled")
    summary = projection.get("summary")
    if not all(isinstance(rows, list) for rows in (generations, occurrences, cancelled)):
        return None, [issue]
    if not isinstance(summary, dict):
        return None, [issue]
    trigger_generation = {}
    for index, generation in enumerate(generations, 1):
        if (not isinstance(generation, dict)
                or generation.get("generation") != index
                or _string(generation.get("trigger_id")) is None
                or generation["trigger_id"] in trigger_generation):
            return None, [issue]
        trigger_generation[generation["trigger_id"]] = index
    projected, cancelled_keys = {}, set()
    for row in occurrences:
        if not isinstance(row, dict):
            return None, [issue]
        key = (row.get("trigger_id"), row.get("channel_id"), row.get("ordinal"))
        if (key in projected or row.get("generation") != trigger_generation.get(key[0])
                or type(row.get("time_ticks")) is not int or type(row.get("order")) is not int):
            return None, [issue]
        projected[key] = (row["time_ticks"], row["order"])
    for row in cancelled:
        if not isinstance(row, dict):
            return None, [issue]
        key = (row.get("trigger_id"), row.get("channel_id"), row.get("ordinal"))
        if (key in projected or key in cancelled_keys
                or row.get("generation") != trigger_generation.get(key[0])
                or _string(row.get("reason")) is None):
            return None, [issue]
        cancelled_keys.add(key)
    generated = {}
    for record in relevant:
        for row in record.get("generated", []):
            if not isinstance(row, dict):
                return None, [issue]
            key = (row.get("instance_trigger_id", row.get("action_id")),
                   row.get("channel_id"), row.get("ordinal"))
            if (key in generated or type(row.get("expected_time_ticks")) is not int
                    or type(row.get("order")) is not int):
                return None, [issue]
            generated[key] = (row["expected_time_ticks"], row["order"])
    if generated != projected:
        return None, [issue]
    expected_summary = {"generations": len(generations),
        "materialized_occurrences": len(occurrences),
        "cancelled_occurrences": len(cancelled),
        "deferred_intervals": len(projection.get("deferred", [])),
        "skipped_controls": len(projection.get("skipped", []))}
    if any(summary.get(key) != value for key, value in expected_summary.items()):
        return None, [issue]
    return projection, []


def _lifecycle_projection(action, record, event_index, expectation, occurrences,
                          component_domains):
    lifecycle = expectation.get("lifecycle") or {}
    if expectation.get("topology_kind") != "periodic_channels":
        return occurrences, [], []
    profile_ids = set((record.get("materialized") or {}).get("profile_ids", []))
    relevant = [row for row, _ in _event_records_for_profiles(profile_ids, event_index)]
    projection, integrity_issues = _lifecycle_projection_integrity(action, relevant)
    if integrity_issues:
        return occurrences, [], integrity_issues
    if lifecycle.get("refresh_policy") in (None, "none"):
        return occurrences, [], []
    if lifecycle.get("refresh_policy") != "replace_previous":
        return occurrences, [], [{
            "code": "lifecycle_projection_unsupported",
            "path": f"rotation_model.actions.{action.get('id')}",
            "message": "该周期生命周期策略尚无逐发生项投影，不能声明动作完整。",
        }]
    if any(row.get("refresh_contract") or row.get("refresh_triggers") for row in relevant):
        return occurrences, [], [{
            "code": "lifecycle_projection_pending",
            "path": f"rotation_model.actions.{action.get('id')}",
            "message": "外部刷新会创建额外实例代次；本版尚无独立代次预期，不能声明动作完整。",
        }]
    replacements = [replacement for row in relevant for replacement in row.get("replacements", [])
                    if isinstance(replacement, dict)
                    and replacement.get("replaced_action_id") == action.get("id")]
    if not replacements:
        return occurrences, [], []
    if len(replacements) != 1:
        return occurrences, [], [{
            "code": "lifecycle_projection_invalid",
            "path": f"rotation_model.actions.{action.get('id')}",
            "message": "周期替换回执不能唯一确定旧实例。",
        }]
    cancelled, effective = [], []
    replacement = replacements[0]
    if projection is not None:
        delivered = {(row.get("channel_id"), row.get("ordinal"))
                     for row in projection.get("occurrences", [])
                     if row.get("root_action_id") == action.get("id")}
        projected_cancelled = {(row.get("channel_id"), row.get("ordinal"))
                               for row in projection.get("cancelled", [])
                               if row.get("root_action_id") == action.get("id")}
        for occurrence in occurrences:
            key = (occurrence.get("channel_id"), occurrence.get("ordinal"))
            if key in projected_cancelled and key not in delivered:
                cancelled.append(_unresolved_occurrence(
                    occurrence, component_domains[occurrence["component_id"]],
                    "cancelled_by_reviewed_replace_previous"))
            elif key in delivered and key not in projected_cancelled:
                effective.append(occurrence)
            else:
                return occurrences, [], [{
                    "code": "lifecycle_projection_invalid",
                    "path": f"rotation_model.actions.{action.get('id')}",
                    "message": "统一生命周期投影未能唯一归类独立清单发生项。",
                }]
    else:
        # Older receipts can be reconstructed only when no occurrence shares
        # the replacement microsecond.  At the boundary, order is semantically
        # required and a timestamp-only fallback would make a false claim.
        cutoff = replacement.get("replacement_time_ticks")
        try:
            start = _ticks(action.get("start"))
        except (TypeError, ValueError):
            start = None
        if (type(cutoff) is not int or start is None
                or any(start + row.get("offset_ticks", -1) == cutoff
                       for row in occurrences)):
            return occurrences, [], [{
                "code": "lifecycle_projection_pending",
                "path": f"rotation_model.actions.{action.get('id')}",
                "message": "周期替换缺少完整时点/order生命周期投影，不能独立判定同刻边界。",
            }]
        for occurrence in occurrences:
            if start + occurrence.get("offset_ticks", -1) > cutoff:
                cancelled.append(_unresolved_occurrence(
                    occurrence, component_domains[occurrence["component_id"]],
                    "cancelled_by_reviewed_replace_previous"))
            else:
                effective.append(occurrence)
    if (type(replacement.get("cancelled_hits")) is not int
            or len(cancelled) != replacement["cancelled_hits"]):
        return occurrences, [], [{
            "code": "lifecycle_projection_invalid",
            "path": f"rotation_model.actions.{action.get('id')}",
            "message": "独立清单推导的取消发生项数与周期编译回执不一致。",
        }]
    return effective, cancelled, []


def _unresolved_component(component_id, domains, reason_code):
    return {"component_id": component_id, "reason_code": reason_code,
            "critical_domains": copy.deepcopy(domains)}


def _unresolved_occurrence(occurrence, domains, reason_code):
    result = {key: copy.deepcopy(occurrence[key]) for key in
              ("occurrence_id", "component_id", "ordinal", "channel_id",
               "offset_ticks", "order", "terminal") if key in occurrence}
    result.setdefault("occurrence_id", "unidentified")
    result.setdefault("component_id", "unknown")
    result.update(reason_code=reason_code, critical_domains=copy.deepcopy(domains))
    return result


def _materialization_receipt(action: dict, records, event_index, *, not_applicable=False) -> dict:
    action_id = action.get("id")
    base = {
        "action_id": action_id,
        "ability_id": action.get("ability_id"),
        "profile_ids": [],
        "coverage_manifest_id": None,
        "coverage_manifest_fingerprint": None,
        "topology_kind": None,
        "spawned_channels": [],
        "expected_components": None,
        "materialized_components": 0,
        "expected_occurrences": None,
        "materialized_occurrences": 0,
        "unresolved_components": [],
        "unresolved_occurrences": [],
        "cancelled_occurrences": [],
        "unexpected_occurrences": [],
        "coverage_status": "unknown",
        "calculation_class": "diagnostic",
        "coverage_basis": "independent_gameplay_manifest_required",
        "issues": [],
    }
    if not_applicable and not records:
        base.update(
            expected_components=0,
            expected_occurrences=0,
            coverage_status="not_applicable",
            calculation_class="normal",
            coverage_basis="explicit_non_damage_action",
        )
        return base
    if len(records) != 1:
        if len(records) > 1:
            base["issues"].append({
                "code": "action_materialization_dispatch_ambiguous",
                "path": f"rotation_model.actions.{action_id}",
                "message": "同一能力存在多份动作派发回执，不能确定覆盖权威。",
            })
            base["coverage_status"] = "incomplete"
        return base
    record = records[0]
    actual, event_issues = _actual_occurrences(action_id, record, event_index)
    base["profile_ids"] = copy.deepcopy((record.get("materialized") or {}).get("profile_ids", []))
    base["coverage_manifest_id"] = record.get("coverage_manifest_id")
    base["coverage_manifest_fingerprint"] = record.get("coverage_manifest_fingerprint")
    base["materialized_components"] = len(set(
        (record.get("materialized") or {}).get("component_ids", [])))
    base["materialized_occurrences"] = len(actual)
    base["issues"].extend(event_issues)
    expectation = _coverage_expectation(record)
    if expectation is None:
        return base
    components = expectation.get("components")
    occurrences = expectation.get("occurrences")
    if (expectation.get("status") != "ready" or not isinstance(components, list)
            or not components or not isinstance(occurrences, list) or not occurrences):
        base["coverage_status"] = "incomplete"
        base["coverage_basis"] = "independent_manifest_invalid_or_pending"
        base["issues"].append({
            "code": "action_coverage_expectation_invalid",
            "path": f"rotation_model.actions.{action_id}",
            "message": "独立游戏组件清单未能展开为有限预期。",
        })
        return base
    component_domains = {}
    unsupported_domains = []
    for component in components:
        if (not isinstance(component, dict) or _string(component.get("component_id")) is None
                or not isinstance(component.get("critical_domains"), list)):
            base["coverage_status"] = "incomplete"
            base["issues"].append({
                "code": "action_coverage_expectation_invalid",
                "path": f"rotation_model.actions.{action_id}",
                "message": "独立游戏组件清单的组件或关键领域不合法。",
            })
            return base
        component_domains[component["component_id"]] = component["critical_domains"]
        unsupported = sorted(set(component["critical_domains"]) - {"damage", "application"})
        if unsupported:
            unsupported_domains.append((component["component_id"], unsupported))
    if unsupported_domains:
        base["coverage_status"] = "incomplete"
        base["coverage_basis"] = "independent_manifest_invalid_or_pending"
        for component_id, domains in unsupported_domains:
            base["unresolved_components"].append(_unresolved_component(
                component_id, component_domains[component_id],
                "critical_domain_not_materialized"))
        base["issues"].append({
            "code": "action_materialization_critical_domain_unsupported",
            "path": f"rotation_model.actions.{action_id}",
            "message": "独立清单声明了当前编译回执无法核验的资源、Buff或状态交付。",
        })
        return base
    channels = expectation.get("channels")
    if not isinstance(channels, list):
        base["coverage_status"] = "incomplete"
        base["issues"].append({
            "code": "action_coverage_expectation_invalid",
            "path": f"rotation_model.actions.{action_id}",
            "message": "独立游戏组件清单的生成通道不合法。",
        })
        return base
    spawned_channels = []
    for channel in channels:
        if (not isinstance(channel, dict)
                or _string(channel.get("channel_id")) is None
                or channel.get("component_id") not in component_domains
                or type(channel.get("expected_occurrences")) is not int
                or channel["expected_occurrences"] < 1):
            base["coverage_status"] = "incomplete"
            base["issues"].append({
                "code": "action_coverage_expectation_invalid",
                "path": f"rotation_model.actions.{action_id}",
                "message": "独立游戏组件清单的生成通道身份或次数不合法。",
            })
            return base
        spawned_channels.append({key: copy.deepcopy(channel[key]) for key in
            ("channel_id", "component_id", "expected_occurrences")})
    expected_occurrences = []
    for occurrence in occurrences:
        if (not isinstance(occurrence, dict)
                or occurrence.get("component_id") not in component_domains
                or _string(occurrence.get("occurrence_id")) is None):
            base["coverage_status"] = "incomplete"
            base["issues"].append({
                "code": "action_coverage_expectation_invalid",
                "path": f"rotation_model.actions.{action_id}",
                "message": "独立游戏组件清单的发生项身份不合法。",
            })
            return base
        occurrence_keys = ["occurrence_id", "component_id", "ordinal", "channel_id", "offset_ticks"]
        if expectation.get("topology_kind") == "finite_sequence":
            occurrence_keys.extend(("order", "terminal"))
        expected_occurrences.append({key: copy.deepcopy(occurrence[key]) for key in occurrence_keys
                                     if key in occurrence})
    expected_components = list(component_domains)
    effective_occurrences, cancelled_occurrences, lifecycle_issues = _lifecycle_projection(
        action, record, event_index, expectation, expected_occurrences, component_domains)
    base.update(
        topology_kind=expectation.get("topology_kind"),
        spawned_channels=spawned_channels,
        expected_components=len(expected_components),
        expected_occurrences=len(effective_occurrences),
        cancelled_occurrences=cancelled_occurrences,
        coverage_basis="independent_gameplay_manifest",
    )
    base["issues"].extend(lifecycle_issues)
    unresolved, unexpected = _compare_occurrences(effective_occurrences, actual)
    base["unresolved_occurrences"] = [_unresolved_occurrence(
        row, component_domains[row["component_id"]], "expected_occurrence_not_materialized")
        for row in unresolved]
    base["unexpected_occurrences"] = [_unresolved_occurrence(
        row, component_domains.get(row.get("component_id"), []),
        "unexpected_materialized_occurrence") for row in unexpected]
    profile_components = record.get("expected_components")
    materialized_components = (record.get("materialized") or {}).get("component_ids")
    expected_set = set(expected_components)
    profile_set = set(profile_components or []) if isinstance(profile_components, list) else set()
    materialized_set = (set(materialized_components or [])
                        if isinstance(materialized_components, list) else set())
    unresolved_components = []
    for component_id in expected_components:
        if component_id not in profile_set:
            reason = "dispatch_profile_missing_component"
        elif component_id not in materialized_set:
            reason = "dispatch_component_not_materialized"
        else:
            continue
        unresolved_components.append(_unresolved_component(
            component_id, component_domains[component_id], reason))
    base["unresolved_components"] = unresolved_components
    if profile_set != expected_set or materialized_set != expected_set:
        base["issues"].append({
            "code": "action_materialization_profile_coverage_mismatch",
            "path": f"rotation_model.actions.{action_id}",
            "message": "独立覆盖清单、动作档案组件与已物化组件不一致。",
        })
    if (record.get("status") != "ready"
            or (record.get("source_verification") or {}).get("status") != "ready"
            or record.get("coverage_status") != "ready"):
        base["issues"].append({
            "code": "action_materialization_dispatch_not_ready",
            "path": f"rotation_model.actions.{action_id}",
            "message": "动作派发回执或来源核验未就绪。",
        })
    for issue in record.get("coverage_issues", []):
        if isinstance(issue, dict):
            base["issues"].append({
                "code": str(issue.get("code", "action_coverage_issue"))[:128],
                "path": str(issue.get("path", f"rotation_model.actions.{action_id}"))[:1024],
                "message": str(issue.get("message", "独立游戏组件清单未通过覆盖核验。"))[:1024],
            })
    complete = (not base["issues"] and not base["unresolved_components"]
                and not base["unresolved_occurrences"] and not unexpected
                and materialized_set == expected_set)
    base["coverage_status"] = "complete" if complete else "incomplete"
    base["calculation_class"] = "normal" if complete else "diagnostic"
    return base


def _receipt_projection(rows):
    if any(row["coverage_status"] == "incomplete" for row in rows):
        status = "incomplete"
    elif any(row["coverage_status"] == "unknown" for row in rows) or not rows:
        status = "unknown"
    elif any(row["coverage_status"] == "complete" for row in rows):
        status = "complete"
    else:
        status = "not_applicable"
    known = bool(rows) and all(row["expected_components"] is not None
                               and row["expected_occurrences"] is not None for row in rows)
    summary = {
        "actions": len(rows),
        "complete_actions": sum(row["coverage_status"] == "complete" for row in rows),
        "incomplete_actions": sum(row["coverage_status"] == "incomplete" for row in rows),
        "unknown_actions": sum(row["coverage_status"] == "unknown" for row in rows),
        "not_applicable_actions": sum(
            row["coverage_status"] == "not_applicable" for row in rows),
        "expected_components": (sum(row["expected_components"] for row in rows) if known else None),
        "materialized_components": sum(row["materialized_components"] for row in rows),
        "expected_occurrences": (sum(row["expected_occurrences"] for row in rows) if known else None),
        "materialized_occurrences": sum(row["materialized_occurrences"] for row in rows),
        "unresolved_components": sum(len(row["unresolved_components"]) for row in rows),
        "unresolved_occurrences": sum(len(row["unresolved_occurrences"]) for row in rows),
    }
    return status, ("normal" if status in {"complete", "not_applicable"}
                    else "diagnostic"), summary


def build_action_materialization_receipts(plan: dict, action_event_compilations=(),
                                          action_dispatch_compilations=(), *,
                                          execution_plan: dict | None = None) -> dict:
    """Build public per-action coverage receipts without changing execution.

    Missing or invalid independent coverage evidence makes the public claim
    unknown/diagnostic or incomplete/diagnostic.  It never withdraws already
    atomically compiled fragments and never upgrades a calculation claim from
    the dispatch profile's self-consistency alone.
    """
    compiled_plan = execution_plan or plan
    rotation = compiled_plan.get("rotation_model") or {}
    by_ability: dict[str, list[dict]] = defaultdict(list)
    for record in action_dispatch_compilations or ():
        if isinstance(record, dict) and isinstance(record.get("ability_id"), str):
            by_ability[record["ability_id"]].append(record)
    abilities = {row.get("id"): row for row in rotation.get("abilities", [])
                 if isinstance(row, dict) and isinstance(row.get("id"), str)}
    event_index = _event_record_index(action_event_compilations)
    rows = []
    for action in rotation.get("actions", []):
        if not isinstance(action, dict):
            continue
        ability = abilities.get(action.get("ability_id"), {})
        rows.append(_materialization_receipt(
            action, by_ability.get(action.get("ability_id"), []),
            event_index,
            not_applicable=ability.get("action_type_id") in NON_DAMAGE_ACTION_TYPES))
    status, calculation_class, summary = _receipt_projection(rows)
    return {
        "contract": MATERIALIZATION_RECEIPT_CONTRACT,
        "status": status,
        "calculation_class": calculation_class,
        "summary": summary,
        "actions": rows,
    }


def validate_action_materialization_receipt(value: dict) -> None:
    top_keys = {"contract", "status", "calculation_class", "summary", "actions"}
    if not isinstance(value, dict) or set(value) != top_keys:
        raise ValueError("动作物化公开回执结构不合法")
    _bounded_json(value, label="动作物化公开回执")
    rows = value["actions"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= 4096:
        raise ValueError("动作物化公开回执必须包含有界动作行")
    row_keys = {"action_id", "ability_id", "profile_ids", "coverage_manifest_id",
        "coverage_manifest_fingerprint", "topology_kind", "spawned_channels",
        "expected_components", "materialized_components", "expected_occurrences",
        "materialized_occurrences", "unresolved_components", "unresolved_occurrences",
        "cancelled_occurrences", "unexpected_occurrences", "coverage_status",
        "calculation_class", "coverage_basis", "issues"}
    action_ids = []
    allowed_domains = {"damage", "application", "resource", "buff", "state"}
    occurrence_keys = {"occurrence_id", "component_id", "ordinal", "channel_id",
                       "offset_ticks", "order", "terminal", "reason_code",
                       "critical_domains"}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != row_keys:
            raise ValueError(f"动作物化公开回执动作行结构不合法：{index}")
        if (_bounded_string(row["action_id"], 128) is None
                or _bounded_string(row["ability_id"], 128) is None
                or row["coverage_status"] not in {
                    "complete", "incomplete", "unknown", "not_applicable"}
                or row["calculation_class"] not in {"normal", "diagnostic"}
                or row["topology_kind"] not in {None, "finite_sequence", "periodic_channels"}
                or row["coverage_basis"] not in {
                    "independent_gameplay_manifest_required",
                    "independent_manifest_invalid_or_pending",
                    "independent_gameplay_manifest",
                    "explicit_non_damage_action",
                }):
            raise ValueError("动作物化公开回执动作身份或状态不合法")
        action_ids.append(row["action_id"])
        if (not isinstance(row["profile_ids"], list)
                or len(row["profile_ids"]) > 64
                or len(row["profile_ids"]) != len(set(row["profile_ids"]))
                or any(_bounded_string(item, 128) is None for item in row["profile_ids"])):
            raise ValueError("动作物化公开回执档案身份不合法")
        manifest_known = row["coverage_manifest_id"] is not None
        if manifest_known != (row["coverage_manifest_fingerprint"] is not None):
            raise ValueError("动作物化公开回执清单身份不完整")
        if manifest_known and (_bounded_string(row["coverage_manifest_id"], 128) is None
                or not isinstance(row["coverage_manifest_fingerprint"], str)
                or len(row["coverage_manifest_fingerprint"]) != 64
                or any(char not in "0123456789abcdef"
                       for char in row["coverage_manifest_fingerprint"])):
            raise ValueError("动作物化公开回执清单指纹不合法")
        for name, limit in (("materialized_components", 64),
                            ("materialized_occurrences", 40000)):
            if type(row[name]) is not int or not 0 <= row[name] <= limit:
                raise ValueError("动作物化公开回执计数不合法")
        for name, limit in (("expected_components", 64), ("expected_occurrences", 40000)):
            if row[name] is not None and (type(row[name]) is not int or not 0 <= row[name] <= limit):
                raise ValueError("动作物化公开回执预期计数不合法")
        channels = row["spawned_channels"]
        if not isinstance(channels, list) or len(channels) > 64:
            raise ValueError("动作物化公开回执通道列表不合法")
        for channel in channels:
            if (not isinstance(channel, dict)
                    or set(channel) != {"channel_id", "component_id", "expected_occurrences"}
                    or _bounded_string(channel["channel_id"], 128) is None
                    or _bounded_string(channel["component_id"], 128) is None
                    or type(channel["expected_occurrences"]) is not int
                    or not 1 <= channel["expected_occurrences"] <= 4096):
                raise ValueError("动作物化公开回执通道行不合法")
        for name in ("unresolved_components", "unresolved_occurrences",
                     "cancelled_occurrences", "unexpected_occurrences"):
            if not isinstance(row[name], list) or len(row[name]) > 4096:
                raise ValueError("动作物化公开回执诊断列表不合法")
        for component in row["unresolved_components"]:
            if (not isinstance(component, dict)
                    or set(component) != {"component_id", "reason_code", "critical_domains"}
                    or _bounded_string(component["component_id"], 128) is None
                    or _bounded_string(component["reason_code"], 128) is None
                    or not _valid_domains(component["critical_domains"], allowed_domains)):
                raise ValueError("动作物化公开回执未解决组件不合法")
        for name in ("unresolved_occurrences", "cancelled_occurrences", "unexpected_occurrences"):
            for occurrence in row[name]:
                if (not isinstance(occurrence, dict) or not set(occurrence) <= occurrence_keys
                        or not {"occurrence_id", "component_id", "reason_code",
                                "critical_domains"} <= set(occurrence)
                        or _bounded_string(occurrence["occurrence_id"], 128) is None
                        or _bounded_string(occurrence["component_id"], 128) is None
                        or _bounded_string(occurrence["reason_code"], 128) is None
                        or not _valid_domains(
                            occurrence["critical_domains"], allowed_domains)
                        or ("ordinal" in occurrence and _positive_int(occurrence["ordinal"]) is None)
                        or ("channel_id" in occurrence
                            and _bounded_string(occurrence["channel_id"], 128) is None)
                        or ("offset_ticks" in occurrence
                            and (type(occurrence["offset_ticks"]) is not int
                                 or not 0 <= occurrence["offset_ticks"] <= 3_600_000_000))
                        or ("order" in occurrence
                            and (type(occurrence["order"]) is not int
                                 or not -10**9 <= occurrence["order"] <= 10**9))
                        or ("terminal" in occurrence
                            and type(occurrence["terminal"]) is not bool)):
                    raise ValueError("动作物化公开回执发生项诊断不合法")
        issues = row["issues"]
        if (not isinstance(issues, list) or len(issues) > 256
                or any(not isinstance(issue, dict)
                       or set(issue) != {"code", "path", "message"}
                       or _bounded_string(issue.get("code"), 128) is None
                       or _bounded_string(issue.get("path"), 1024) is None
                       or _bounded_string(issue.get("message"), 1024) is None
                       for issue in issues)):
            raise ValueError("动作物化公开回执问题列表不合法")
        if row["coverage_status"] == "complete":
            if (row["calculation_class"] != "normal" or row["topology_kind"] is None
                    or not row["profile_ids"] or not manifest_known
                    or row["expected_components"] is None
                    or row["expected_components"] < 1
                    or row["expected_components"] != row["materialized_components"]
                    or row["expected_occurrences"] is None
                    or row["expected_occurrences"] != row["materialized_occurrences"]
                    or row["unresolved_components"] or row["unresolved_occurrences"]
                    or row["unexpected_occurrences"] or row["issues"]):
                raise ValueError("动作物化公开回执完整行自相矛盾")
        elif (row["calculation_class"] != "diagnostic"
              and row["coverage_status"] != "not_applicable"):
            raise ValueError("动作物化公开回执诊断行类别不合法")
        if row["coverage_status"] == "not_applicable" and (
                row["calculation_class"] != "normal" or row["profile_ids"]
                or manifest_known or row["topology_kind"] is not None
                or row["expected_components"] != 0
                or row["materialized_components"] != 0
                or row["expected_occurrences"] != 0
                or row["materialized_occurrences"] != 0
                or row["spawned_channels"] or row["unresolved_components"]
                or row["unresolved_occurrences"] or row["cancelled_occurrences"]
                or row["unexpected_occurrences"] or row["issues"]
                or row["coverage_basis"] != "explicit_non_damage_action"):
            raise ValueError("动作物化公开回执不适用行自相矛盾")
        if (row["coverage_status"] == "unknown"
                and (row["expected_components"] is not None
                     or row["expected_occurrences"] is not None)):
            raise ValueError("动作物化公开回执未知行不能声明预期计数")
    if len(action_ids) != len(set(action_ids)):
        raise ValueError("动作物化公开回执动作身份重复")
    status, calculation_class, summary = _receipt_projection(rows)
    if (value["status"] != status or value["calculation_class"] != calculation_class
            or value["summary"] != summary):
        raise ValueError("动作物化公开回执状态、类别或汇总不能由动作行重建")


def _validate_trusted_manifest_bindings(dispatches, trusted_manifest_bundle=None) -> None:
    """Anchor frozen coverage facts to a bundle outside the exported JSON.

    Fingerprints inside one export detect accidental inconsistency, but a
    coordinated rewrite can recompute all of them.  The installed gameplay
    manifest bundle is the default trust root.  Tests and controlled callers
    may inject an independently held bundle explicitly; the export itself must
    never select its own authority.
    """
    from genshin_db.gameplay_action_manifest import validate_bundle
    from genshin_db.runtime_catalog import fingerprint
    from genshin_db.runtime_gameplay_action_manifest import (
        load_gameplay_action_manifest_bundle,
    )

    bundle = (load_gameplay_action_manifest_bundle()
              if trusted_manifest_bundle is None else trusted_manifest_bundle)
    try:
        validate_bundle(bundle)
        trusted_by_selector = {
            json.dumps(manifest["selector"], ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"), allow_nan=False): manifest
            for manifest in bundle["manifests"]
        }
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("受信游戏动作清单包不合法") from exc
    for record in dispatches:
        manifest = record.get("coverage_manifest")
        if manifest is None:
            continue
        try:
            selector_key = json.dumps(
                record.get("selector"), ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError) as exc:
            raise ValueError("动作物化封存来源的清单选择器不合法") from exc
        trusted = trusted_by_selector.get(selector_key)
        if trusted is None:
            raise ValueError("动作物化清单无法从受信运行时清单包重新锚定")
        trusted_fingerprint = fingerprint(trusted)
        if (trusted.get("status") != "confirmed"
                or (trusted.get("review") or {}).get("status") != "reviewed"
                or manifest.get("selector") != trusted.get("selector")
                or manifest.get("manifest_id") != trusted.get("manifest_id")
                or fingerprint(manifest) != trusted_fingerprint
                or record.get("declared_coverage_manifest_id")
                != trusted.get("manifest_id")
                or record.get("coverage_manifest_id") != trusted.get("manifest_id")
                or record.get("expected_coverage_manifest_fingerprint")
                != trusted_fingerprint
                or record.get("coverage_manifest_fingerprint")
                != trusted_fingerprint):
            raise ValueError("动作物化封存清单与受信运行时清单不一致")


def validate_action_materialization_result_receipt(
        result: dict, receipt: dict, *, trusted_manifest_bundle=None) -> None:
    """Require the public result and result-receipt projections to be identical."""
    materialization = result.get("action_materialization_receipt")
    proof = receipt.get("action_materialization_receipt")
    if materialization is None and proof is None:
        return
    if not isinstance(materialization, dict) or not isinstance(proof, dict):
        raise TypeError("动作物化公开回执缺失")
    validate_action_materialization_receipt(materialization)
    validate_action_materialization_receipt(proof)
    from genshin_db.runtime_catalog import fingerprint
    if fingerprint(materialization) != fingerprint(proof):
        raise ValueError("动作物化结果与公开回执不一致")
    compilations = result.get("action_event_compilations")
    source = result.get("action_materialization_source")
    source_proof = receipt.get("action_materialization_proof")
    if not isinstance(source, dict):
        raise TypeError("动作物化公开回执缺少封存来源")
    _bounded_json(source, label="动作物化封存来源")
    _bounded_json(compilations, label="动作事件编译来源")
    rotation = source.get("rotation_model")
    actions = rotation.get("actions") if isinstance(rotation, dict) else None
    abilities = rotation.get("abilities") if isinstance(rotation, dict) else None
    dispatches = source.get("action_dispatch_compilations")
    generated_total = (sum(len(record.get("generated", [])) for record in compilations)
                       if isinstance(compilations, list)
                       and all(isinstance(record, dict)
                               and isinstance(record.get("generated", []), list)
                               for record in compilations) else None)
    if (not isinstance(compilations, list) or len(compilations) > 256
            or not isinstance(source, dict)
            or set(source) != {
                "contract", "rotation_model", "action_dispatch_compilations"}
            or source.get("contract") != "action-materialization-source-v1"
            or not isinstance(rotation, dict)
            or not isinstance(actions, list) or not 1 <= len(actions) <= 4096
            or any(not isinstance(row, dict) for row in actions)
            or not isinstance(abilities, list) or not 1 <= len(abilities) <= 4096
            or any(not isinstance(row, dict) for row in abilities)
            or not isinstance(dispatches, list) or not 1 <= len(dispatches) <= 256
            or any(not isinstance(row, dict) for row in dispatches)
            or generated_total is None or generated_total > 40000
            or not isinstance(source_proof, dict)
            or set(source_proof) != {
                "contract", "public_receipt_fingerprint",
                "action_event_compilations_fingerprint", "source_fingerprint"}
            or source_proof.get("contract") != "action-materialization-proof-v1"
            or source_proof.get("public_receipt_fingerprint") != fingerprint(materialization)
            or source_proof.get("action_event_compilations_fingerprint")
            != fingerprint(compilations)
            or source_proof.get("source_fingerprint") != fingerprint(source)):
        raise ValueError("动作物化公开回执没有绑定完整动作派发与事件编译来源")
    if receipt.get("result_fingerprint") != fingerprint(result):
        raise ValueError("动作物化公开回执没有绑定完整计算结果")
    try:
        rebuilt = build_action_materialization_receipts(
            {"rotation_model": source["rotation_model"]}, compilations,
            source["action_dispatch_compilations"])
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ValueError("动作物化封存来源无法重建") from exc
    if fingerprint(rebuilt) != fingerprint(materialization):
        raise ValueError("动作物化公开回执不能由封存的独立清单和编译事件重建")
    _validate_trusted_manifest_bindings(dispatches, trusted_manifest_bundle)


def _manual_links(plan: dict) -> dict[str, list[str]]:
    links: dict[str, list[str]] = defaultdict(list)
    rotation = plan.get("rotation_model") or {}
    for row in rotation.get("action_hit_links", []):
        action_id, hit_id = row.get("action_id"), row.get("hit_id")
        if isinstance(action_id, str) and isinstance(hit_id, str):
            links[action_id].append(hit_id)
    return {key: sorted(set(value)) for key, value in links.items()}


def _prototype_ids(profiles) -> set[str]:
    result: set[str] = set()
    for profile in profiles:
        prototype_id = profile.get("prototype_hit_id")
        if isinstance(prototype_id, str):
            result.add(prototype_id)
        for channel in profile.get("channels", []):
            if isinstance(channel, dict) and isinstance(channel.get("prototype_hit_id"), str):
                result.add(channel["prototype_hit_id"])
    return result


def _complete_dispatch_records(records) -> tuple[dict[str, dict], dict]:
    """Index only atomically complete reviewed dispatch materializations.

    An action-event compiler can generate hits from a manually authored reusable
    profile as well.  That is useful, but it is not evidence that E18-01 saved
    the Agent from authoring the profile.  Keep the two origins separate and do
    not accept a dispatch row merely because it has a ``ready`` status.
    """
    by_profile: dict[str, dict] = {}
    expected_total = materialized_total = 0
    pending = 0
    for record in records or ():
        if not isinstance(record, dict):
            pending += 1
            continue
        expected = record.get("expected_components")
        materialized = (record.get("materialized") or {}).get("component_ids")
        profiles = (record.get("materialized") or {}).get("profile_ids")
        native_fragments_fingerprint = record.get("native_fragments_fingerprint")
        has_native_fragment_proof = (
            isinstance(native_fragments_fingerprint, str)
            and len(native_fragments_fingerprint) == 64
            and all(character in "0123456789abcdef" for character in native_fragments_fingerprint)
        )
        complete = (record.get("status") == "ready"
                    and (record.get("source_verification") or {}).get("status") == "ready"
                    and isinstance(expected, list) and expected
                    and expected == materialized
                    and isinstance(profiles, list) and profiles
                    and len(profiles) == len(set(profiles))
                    and has_native_fragment_proof)
        if isinstance(expected, list):
            expected_total += len(expected)
        if isinstance(materialized, list):
            materialized_total += len(materialized)
        if complete:
            for profile_id in profiles:
                if isinstance(profile_id, str) and profile_id not in by_profile:
                    by_profile[profile_id] = record
                else:
                    complete = False
                    break
        if not complete:
            pending += 1
            for profile_id in profiles or ():
                by_profile.pop(profile_id, None)
    status = "absent" if not records else ("complete" if pending == 0 else "pending")
    return by_profile, {
        "status": status,
        "records": len(records or ()),
        "complete_records": len(records or ()) - pending,
        "pending_records": pending,
        "expected_components": expected_total,
        "materialized_components": materialized_total,
    }


def build_action_automation_audit(
    plan: dict,
    action_event_compilations=(),
    action_dispatch_compilations=(),
    *,
    execution_plan: dict | None = None,
) -> dict:
    """Return a bounded work list for E18-01/E18-02.

    ``plan`` is the original native plan. ``execution_plan`` may be the private
    plan assembled in the same compose transaction; it supplies generated
    profiles and bindings for classification, while all manual-input counters
    remain derived from ``plan``. ``action_event_compilations`` must be the fresh
    receipts from that compose call; cached client projections are not accepted
    as execution facts.
    """
    compiled_plan = execution_plan or plan
    rotation = compiled_plan.get("rotation_model") or {}
    mechanics = compiled_plan.get("mechanics_model") or {}
    actions = rotation.get("actions", [])
    abilities = {row.get("id"): row for row in rotation.get("abilities", [])
                 if isinstance(row, dict) and isinstance(row.get("id"), str)}
    profiles = {row.get("id"): row for row in compiled_plan.get("action_event_profiles", [])
                if isinstance(row, dict) and isinstance(row.get("id"), str)}
    native_profiles = {row.get("id"): row for row in plan.get("action_event_profiles", [])
                       if isinstance(row, dict) and isinstance(row.get("id"), str)}
    bindings: dict[str, list[str]] = defaultdict(list)
    for row in compiled_plan.get("action_event_bindings", []):
        if isinstance(row, dict) and row.get("ability_id") in abilities and row.get("profile_id") in profiles:
            bindings[row["ability_id"]].append(row["profile_id"])

    records = {row.get("profile_id"): row for row in action_event_compilations
               if isinstance(row, dict) and isinstance(row.get("profile_id"), str)}
    dispatch_profiles, dispatch_manifest = _complete_dispatch_records(action_dispatch_compilations)
    generated: dict[str, list[dict]] = defaultdict(list)
    for record in records.values():
        if record.get("status") != "ready":
            continue
        for item in record.get("generated", []):
            if isinstance(item, dict) and isinstance(item.get("action_id"), str):
                generated[item["action_id"]].append(item)

    manual = _manual_links(plan)
    rows, category_counts = [], defaultdict(int)
    generated_application_attempts = 0
    for action in actions:
        action_id = action.get("id")
        ability = abilities.get(action.get("ability_id"), {})
        action_type = ability.get("action_type_id")
        bound_profiles = sorted(set(bindings.get(action.get("ability_id"), [])))
        automatic = sorted({item.get("hit_id") for item in generated.get(action_id, [])
                            if isinstance(item.get("hit_id"), str)})
        manual_hits = manual.get(action_id, [])
        issues = []
        for profile_id in bound_profiles:
            record = records.get(profile_id)
            if record and record.get("status") != "ready":
                issues.extend(copy.deepcopy(record.get("issues", [])))

        automation_origin = None
        if automatic:
            category = "automatic_generated"
            generating_profiles = [profile_id for profile_id in bound_profiles
                                   if (records.get(profile_id) or {}).get("status") == "ready"
                                   and any(item.get("action_id") == action_id
                                           for item in (records.get(profile_id) or {}).get("generated", []))]
            automation_origin = ("reviewed_dispatch_profile"
                                 if generating_profiles
                                 and all(profile_id in dispatch_profiles for profile_id in generating_profiles)
                                 else "reusable_plan_profile")
            for profile_id in bound_profiles:
                record = records.get(profile_id, {})
                if record.get("status") == "ready" and record.get("kind") in APPLICATION_KINDS:
                    generated_application_attempts += sum(
                        item.get("action_id") == action_id for item in record.get("generated", []))
        elif manual_hits:
            category = "manual_input_required"
        elif action_type in NON_DAMAGE_ACTION_TYPES:
            category = "not_applicable"
        elif bound_profiles:
            category = "scenario_calibration"
        elif action_type in ATTACK_ACTION_TYPES:
            category = "manual_input_required"
            issues.append({
                "code": "action_dispatch_profile_missing",
                "path": f"rotation_model.actions.{action_id}",
                "message": "攻击动作没有可执行的来源动作档案；仍需建立资料绑定或显式命中。",
            })
        else:
            category = "unclassified"
            issues.append({
                "code": "action_type_not_classified",
                "path": f"rotation_model.actions.{action_id}",
                "message": "动作类别尚未声明其是否产生伤害，不能按零命中处理。",
            })
        category_counts[category] += 1
        rows.append({
            "action_id": action_id,
            "ability_id": action.get("ability_id"),
            "action_type_id": action_type,
            "category": category,
            "automation_origin": automation_origin,
            "profile_ids": bound_profiles,
            "automatic_hit_ids": automatic,
            "manual_hit_ids": manual_hits,
            "issues": issues,
        })

    manual_hits = (plan.get("manual_template") or {}).get("hits", [])
    prototype_ids = _prototype_ids(native_profiles.values())
    reusable_prototype_hits = [hit for hit in manual_hits if hit.get("id") in prototype_ids]
    manual_instance_hits = [hit for hit in manual_hits if hit.get("id") not in prototype_ids]
    reusable_prototype_reactions = [hit.get("id") for hit in reusable_prototype_hits
                                    if isinstance(hit, dict) and isinstance(hit.get("reaction"), dict)]
    manual_instance_reactions = [hit.get("id") for hit in manual_instance_hits
                                 if isinstance(hit, dict) and isinstance(hit.get("reaction"), dict)]
    manual_reactions = reusable_prototype_reactions + manual_instance_reactions
    elemental = mechanics.get("elemental_model")
    elemental_status = "disabled"
    if isinstance(elemental, dict):
        elemental_status = "automatic_active" if generated_application_attempts else "enabled_without_automatic_actions"

    manual_link_count = sum(len(value) for value in manual.values())
    generated_hit_count = sum(len(value) for value in generated.values())
    attack_action_count = sum(row["action_type_id"] in ATTACK_ACTION_TYPES for row in rows)
    automatic_action_count = category_counts["automatic_generated"]
    dispatch_action_count = sum(row["automation_origin"] == "reviewed_dispatch_profile" for row in rows)
    reusable_profile_action_count = sum(row["automation_origin"] == "reusable_plan_profile" for row in rows)
    work_orders = {(issue.get("code"), issue.get("path"))
                   for row in rows for issue in row["issues"] if isinstance(issue, dict)}
    known_pending_contacts = sum(
        int((record.get("stats") or {}).get("planned_hits", 0))
        for record in records.values() if record.get("status") != "ready"
        and type((record.get("stats") or {}).get("planned_hits", 0)) is int)
    unknown_cardinality_actions = sum(
        row["category"] in {"manual_input_required", "unclassified"}
        and not row["manual_hit_ids"] for row in rows)
    result = {
        "contract": "action-automation-audit-v3",
        "categories": {
            "automatic_generated": "由受审来源和动作档案自动派生",
            "scenario_calibration": "已有档案，但仍缺场景时序、命中或来源确认",
            "manual_input_required": "仍依赖手填命中或尚无动作档案",
            "not_applicable": "明确不产生伤害的动作类别",
            "unclassified": "动作类别语义未知，不能假定零命中",
        },
        "actions": rows,
        "summary": {
            "total_actions": len(actions),
            "attack_actions": attack_action_count,
            "automatic_actions": automatic_action_count,
            "reviewed_dispatch_actions": dispatch_action_count,
            "reusable_profile_actions": reusable_profile_action_count,
            "scenario_calibration_actions": category_counts["scenario_calibration"],
            "manual_actions": category_counts["manual_input_required"],
            "unclassified_actions": category_counts["unclassified"],
            "automatic_generated_hits": generated_hit_count,
            "manual_hit_rows": len(manual_hits),
            "reusable_prototype_hit_rows": len(reusable_prototype_hits),
            "manual_per_instance_hit_rows": len(manual_instance_hits),
            "manual_action_hit_links": manual_link_count,
            "manual_reaction_answers": len(manual_reactions),
            "reusable_prototype_reaction_answers": len(reusable_prototype_reactions),
            "manual_per_instance_reaction_answers": len(manual_instance_reactions),
            "automatic_elemental_attempts": generated_application_attempts,
            "known_pending_contacts": known_pending_contacts,
            "unknown_contact_cardinality_actions": unknown_cardinality_actions,
            "unknown_work_orders": len(work_orders),
            "unique_dispatch_profile_decisions": len(dispatch_profiles),
            "unique_reusable_profile_decisions": len(native_profiles),
            "automatic_action_ratio": (automatic_action_count / attack_action_count
                                       if attack_action_count else None),
            "reviewed_dispatch_action_ratio": (dispatch_action_count / attack_action_count
                                                if attack_action_count else None),
        },
        "dispatch_manifest": dispatch_manifest,
        "elemental": {
            "status": elemental_status,
            "ruleset": elemental.get("ruleset") if isinstance(elemental, dict) else None,
            "manual_reaction_hit_ids": manual_reactions,
            "policy": "受支持链路由元素运行时输出；未知资料保持待处理，不以手填反应答案冒充自动证明。",
        },
    }
    if action_dispatch_compilations:
        materialization = build_action_materialization_receipts(
            plan, action_event_compilations, action_dispatch_compilations,
            execution_plan=execution_plan)
        if materialization["actions"]:
            result["materialization_receipt"] = materialization
    return result
