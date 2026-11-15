"""Exact lookup and conservative mapping for reviewed action dispatch profiles."""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from importlib import resources

from genshin_db.action_dispatch_schema import (
    CONTRACT_ID,
    EVIDENCE_CATEGORIES,
    MAX_BUNDLE_BYTES,
    MAX_EMISSIONS,
    PROFILE_VERSION,
    SCHEMA_VERSION,
    validate_bundle,
    validate_selector,
)
from genshin_db.runtime_catalog import fingerprint

_ATTACK_TAG_CLASSIFICATION = {
    "Elemental_Art": "damage.elemental_skill",
    "Elemental_Burst": "damage.elemental_burst",
    "Normal_Attack": "damage.normal_attack",
    "Charged_Attack": "damage.charged_attack",
    "Plunging_Attack": "damage.plunging_attack",
}


def action_dispatch_query_capabilities():
    """Advertise only the database-owned lookup and evidence contract.

    The historical helper name now aliases this same neutral contract. App
    callers compose the separate materialization capabilities at the App layer.
    """
    return {
        "contract_id": CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "profile_version": PROFILE_VERSION,
        "selection": ["character_id", "skill_depot_id", "skill_id", "slot", "effective_level",
                      "constellation", "variant", "cast_stage"],
        "emission_kinds": ["fixed", "periodic"],
        "evidence_categories": sorted(EVIDENCE_CATEGORIES),
        "coverage_manifest": {
            "contract_id": "gameplay-action-manifest-v1",
            "topology_kinds": ["finite_sequence", "periodic_channels"],
            "selection": "same exact selector as dispatch profile",
            "acceptance": ["coverage_manifest_id", "expected_coverage_manifest_fingerprint"],
            "unaccepted_or_missing": "coverage unknown; never self-certify completeness",
        },
        "limits": {"max_bundle_bytes": MAX_BUNDLE_BYTES,
                   "max_emissions_per_profile": MAX_EMISSIONS},
        "safety": {
            "selection": "exact_only",
            "missing": "pending",
            "ambiguous": "pending",
            "stale_dataset": "pending",
            "execution_authority": False,
        },
    }


def action_dispatch_capabilities():
    """Compatibility name for the database-only evidence query contract."""
    return action_dispatch_query_capabilities()


@lru_cache(maxsize=1)
def load_action_dispatch_bundle():
    value = json.loads(resources.files("genshin_runtime").joinpath(
        "resources/action_dispatch_profiles.json").read_text("utf8"))
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
    required = {key: dataset[key] for key in ("version_line", "upstream_commit")}
    if (any(current.get(key) != value for key, value in required.items())
            or (current.get("normalization") or {}).get("profile_id") != dataset["normalization_profile_id"]):
        return False
    return dataset["snapshot_id"] is None or dataset["snapshot_id"] == current["snapshot_id"]


def _independent_schedule_components(expectation):
    if not isinstance(expectation, dict):
        return set()
    if expectation.get("topology_kind") == "finite_sequence":
        return {row.get("component_id") for row in expectation.get("components", [])}
    if expectation.get("topology_kind") == "periodic_channels":
        return {row.get("component_id") for row in expectation.get("channels", [])
                if (row.get("schedule") or {}).get("kind") == "explicit_offsets"}
    return set()


def _verify_source_receipts(profile, source_receipts, *, topology_kind=None,
                            independent_schedule_components=()):
    receipts = source_receipts or []
    issues, audit = [], []
    if not isinstance(receipts, list):
        return [], [{"code": "action_dispatch_source_verification_invalid",
                    "message": "来源核对回执必须是列表"}]
    expected = {emission["component_id"]: emission for emission in profile["emissions"]}
    seen = set()
    required_keys = {"component_id", "kind", "candidate_id", "origin_reference_id", "source_fingerprint",
                     "status", "application_identity_status", "application_identity_tag", "interval_seconds",
                     "classification"}
    optional_keys = {"database_icd_rule", "source_issue_codes"}
    for receipt in receipts:
        if (not isinstance(receipt, dict)
                or required_keys - receipt.keys() or set(receipt) - required_keys - optional_keys
                or ("source_issue_codes" in receipt and (
                    not isinstance(receipt["source_issue_codes"], list)
                    or len(receipt["source_issue_codes"]) > 64
                    or any(not isinstance(code, str) or not code or len(code) > 128
                           for code in receipt["source_issue_codes"])
                    or len(receipt["source_issue_codes"])
                    != len(set(receipt["source_issue_codes"]))))):
            issues.append({"code": "action_dispatch_source_verification_invalid",
                           "message": "来源核对回执字段不完整或含未知字段"})
            continue
        component = receipt["component_id"]
        emission = expected.get(component)
        if emission is None or component in seen:
            issues.append({"code": "action_dispatch_source_verification_unmatched", "component_id": component,
                           "message": "来源回执重复或不属于档案"})
            continue
        seen.add(component)
        source, application = emission["attack_source"], emission["application"]
        mismatch = (receipt["kind"] != source["kind"]
            or receipt["candidate_id"] != source["candidate_id"]
            or receipt["origin_reference_id"] != source["origin_reference_id"]
            or receipt["source_fingerprint"] != source["source_fingerprint"])
        finite_timer_only = (topology_kind == "finite_sequence"
            and source["kind"] == "periodic_attack"
            and receipt["status"] == "pending"
            and receipt.get("source_issue_codes") == ["periodic_timer_not_exact"])
        if (receipt["status"] != "ready" and not finite_timer_only) or mismatch:
            issues.append({"code": "action_dispatch_source_fingerprint_mismatch", "component_id": component,
                           "message": "新鲜查询未确认受审候选、创建来源及来源指纹"})
        expected_interval = emission["schedule"].get("interval_seconds")
        # A reviewed finite topology owns every occurrence time.  Its source
        # candidate timer remains provenance, not an execution schedule.
        if (topology_kind != "finite_sequence"
                and component not in independent_schedule_components
                and receipt["interval_seconds"] != expected_interval):
            issues.append({"code": "action_dispatch_interval_mismatch", "component_id": component,
                           "message": "新鲜周期timer与受审档案间隔不一致"})
        if emission["prototype"]["damage_type_ids"] != [receipt["classification"]]:
            issues.append({"code": "action_dispatch_classification_mismatch", "component_id": component,
                           "message": "原型伤害分类与新鲜攻击候选classification不一致"})
        if application["mode"] == "sequence":
            identity_ready = (receipt["application_identity_status"] == "ready"
                              and receipt["application_identity_tag"] == application["counter_tag"])
            if not identity_ready and application["verification"] is None:
                issues.append({"code": "action_dispatch_application_identity_unverified",
                               "component_id": component,
                               "message": "数据库未确认同一counter_tag，且档案没有逐组件结构化证据"})
            if (receipt["application_identity_status"] == "ready"
                    and receipt["application_identity_tag"] != application["counter_tag"]):
                issues.append({"code": "action_dispatch_application_identity_conflict",
                               "component_id": component,
                               "message": "档案counter_tag与数据库确认身份冲突"})
        elif (receipt["application_identity_status"] == "ready"
              or receipt["application_identity_tag"] is not None):
            issues.append({"code": "action_dispatch_application_identity_conflict",
                           "component_id": component,
                           "message": "档案声明无序列计数，但新鲜数据库候选存在附着计数身份"})
        audit.append(copy.deepcopy(receipt))
    for component in sorted(set(expected) - seen):
        issues.append({"code": "action_dispatch_source_verification_required", "component_id": component,
                       "message": "缺少该攻击组件的同快照新鲜来源核对"})
    return audit, issues


def _database_icd_rule(projection):
    attenuation = projection.get("attenuation") or {}
    profile = attenuation.get("profile") or {}
    projected = profile.get("projection") or {}
    if (attenuation.get("status") != "ready" or profile.get("execution", {}).get("status") != "ready"
            or not isinstance(attenuation.get("group"), str)
            or not isinstance(profile.get("content_fingerprint"), str)):
        return None
    return {"status": "ready", "rule_group": attenuation["group"],
        "reset_seconds": projected.get("reset_cycle_seconds_rounded"),
        "sequence": copy.deepcopy(projected.get("durability_sequence")),
        "source_fingerprint": profile["content_fingerprint"]}


def _coverage_projection(coverage_bundle, selector, snapshot):
    if coverage_bundle is None:
        return {"status": "unknown", "manifest": None, "manifest_fingerprint": None,
                "expectation": {"status": "unknown", "topology_kind": None,
                    "components": [], "occurrences": [], "channels": [], "lifecycle": None},
                "issues": []}
    from genshin_db.runtime_gameplay_action_manifest import (
        resolve_gameplay_action_manifest,
    )
    result = resolve_gameplay_action_manifest(
        coverage_bundle, selector=selector, snapshot=snapshot)
    if (result.get("status") == "pending"
            and {row.get("code") for row in result.get("issues", [])}
            == {"gameplay_action_manifest_missing"}):
        return {"status": "unknown", "manifest": None,
            "manifest_fingerprint": None,
            "expectation": {"status": "unknown", "topology_kind": None,
                "components": [], "occurrences": [], "channels": [],
                "lifecycle": None},
            "issues": []}
    return result


def _coverage_profile_issues(profile, coverage):
    """Cross-check independent gameplay topology against sourced components."""
    if coverage.get("status") != "ready" or not coverage.get("manifest"):
        return []
    expectation = coverage["expectation"]
    expected = {row["component_id"] for row in expectation["components"]}
    actual = {row["component_id"] for row in profile["emissions"]}
    issues = []
    if expected != actual:
        issues.append({"code": "action_dispatch_coverage_component_mismatch",
            "message": "独立游戏组件清单与派发档案的组件集合不一致",
            "expected_components": sorted(expected),
            "profile_components": sorted(actual)})
        return issues
    if expectation["topology_kind"] == "finite_sequence":
        conflicting = [row["component_id"] for row in profile["emissions"]
                       if row["kind"] != "fixed"
                       or row["schedule"].get("offset_seconds") != 0]
        if conflicting:
            issues.append({"code": "action_dispatch_finite_component_timing_conflict",
                "component_ids": conflicting,
                "message": "有限序列组件须为offset=0的fixed原型；实际发生时点只由独立steps提供"})
    if expectation["topology_kind"] == "periodic_channels":
        emissions = {row["component_id"]: row for row in profile["emissions"]}
        channel_components = [row["component_id"] for row in expectation["channels"]]
        if len(channel_components) != len(set(channel_components)):
            issues.append({"code": "action_dispatch_coverage_component_reused",
                "message": "当前原生周期输入要求每个独立组件只对应一个通道"})
            return issues
        for channel in expectation["channels"]:
            component = channel["component_id"]
            emission = emissions[component]
            schedule = channel["schedule"]
            if schedule["kind"] == "explicit_offsets":
                # The dispatch row is a canonical value/application prototype;
                # the independent manifest exclusively owns non-uniform times.
                if (emission["kind"] != "fixed"
                        or emission["schedule"].get("offset_seconds") != 0
                        or emission["attack_source"].get("kind") != "periodic_attack"):
                    issues.append({"code": "action_dispatch_explicit_component_timing_conflict",
                        "component_id": component,
                        "message": "不等间隔通道须使用offset=0的periodic-source fixed原型"})
            elif schedule["kind"] != emission["kind"]:
                issues.append({"code": "action_dispatch_coverage_schedule_mismatch",
                    "component_id": component,
                    "message": "独立游戏组件清单与派发档案的固定/周期类型不一致"})
                continue
            else:
                keys = ({"offset_seconds"} if schedule["kind"] == "fixed" else
                        {"first_offset_seconds", "interval_seconds", "count", "derive_count"})
                if any(schedule.get(key) != emission["schedule"].get(key)
                       for key in keys if key in schedule or key in emission["schedule"]):
                    issues.append({"code": "action_dispatch_coverage_schedule_mismatch",
                        "component_id": component,
                        "message": "独立游戏组件清单与派发档案的通道时序不一致"})
            if any(delivery.get("kind") == "resource"
                   for delivery in channel.get("deliveries", [])):
                issues.append({"code": "action_dispatch_coverage_resource_delivery_unsupported",
                    "component_id": component,
                    "message": "资源模板虽为封闭引用，但当前App没有闭合消费者，不能声明已物化"})
        lifecycle = expectation.get("lifecycle") or {}
        profile_lifecycle = profile["lifecycle"]
        expected_refresh = lifecycle.get("refresh_policy")
        actual_refresh = profile_lifecycle.get("replacement_policy") or "none"
        if (lifecycle.get("establishment_offset_seconds")
                != profile_lifecycle.get("establishment_offset_seconds")
                or lifecycle.get("instance_end_offset_seconds")
                != profile_lifecycle.get("instance_end_offset_seconds")
                or expected_refresh != actual_refresh):
            issues.append({"code": "action_dispatch_coverage_lifecycle_mismatch",
                "message": "独立游戏组件清单与派发档案的生命周期不一致"})
        if (lifecycle.get("pause_resume_policy") != "none"
                or lifecycle.get("off_field_policy") != "continue"):
            issues.append({"code": "action_dispatch_coverage_lifecycle_unsupported",
                "message": "当前执行器只支持不暂停且退场后继续的周期实例"})
    return issues


def resolve_action_dispatch_profile(bundle, *, selector, snapshot, source_receipts=None,
                                    coverage_bundle=None):
    """Resolve one exact row without falling back to a neighboring action.

    `snapshot` is the already validated runtime snapshot receipt.  The function
    is pure so generic fixture bundles can exercise the data contract without a
    production database.
    """
    validate_bundle(bundle)
    validate_selector(selector)
    current = _snapshot_view(snapshot)
    coverage = _coverage_projection(coverage_bundle, selector, snapshot)
    result = {
        "contract_id": CONTRACT_ID,
        "schema_version": SCHEMA_VERSION,
        "profile_version": PROFILE_VERSION,
        "status": "pending",
        "selector": copy.deepcopy(selector),
        "snapshot": current,
        "profile": None,
        "matched_profile_ids": [],
        "source_verification": {"status": "pending", "receipts": []},
        "coverage_status": ("ready" if coverage.get("status") == "ready" else
                            "unknown" if coverage.get("status") == "unknown" else "pending"),
        "coverage_manifest": copy.deepcopy(coverage.get("manifest")),
        "coverage_manifest_fingerprint": coverage.get("manifest_fingerprint"),
        "coverage_expectation": copy.deepcopy(coverage.get("expectation")),
        "coverage_issues": copy.deepcopy(coverage.get("issues", [])),
        "issues": [],
    }
    semantic = [row for row in bundle["profiles"] if row["selector"] == selector]
    result["matched_profile_ids"] = [row["profile_id"] for row in semantic]
    if not semantic:
        result["issues"].append({"code": "action_dispatch_profile_missing",
                                 "message": "没有与完整动作身份精确匹配的受审发射档案"})
    elif len(semantic) > 1:
        result["issues"].append({"code": "action_dispatch_profile_ambiguous",
                                 "message": "完整动作身份匹配到多份档案，禁止按列表顺序选择"})
    else:
        profile = semantic[0]
        if not _dataset_matches(profile["dataset"], snapshot):
            result["issues"].append({"code": "action_dispatch_profile_stale",
                                     "message": "档案数据集身份与当前运行时快照不一致"})
        elif profile["status"] != "confirmed" or profile["review"]["status"] != "reviewed":
            result["issues"].append({"code": "action_dispatch_profile_unreviewed",
                                     "message": "发射档案尚未完成审查"})
        else:
            result["profile"] = copy.deepcopy(profile)
            coverage_expectation = coverage.get("expectation") or {}
            receipts, issues = _verify_source_receipts(profile, source_receipts,
                topology_kind=coverage_expectation.get("topology_kind"),
                independent_schedule_components=_independent_schedule_components(
                    coverage_expectation))
            coverage_issues = _coverage_profile_issues(profile, coverage)
            result["coverage_issues"].extend(copy.deepcopy(coverage_issues))
            if coverage_issues:
                result["coverage_status"] = "incomplete"
            result["source_verification"] = {"status": "ready" if not issues else "pending",
                                             "receipts": receipts}
            result["issues"].extend(issues)
            if not issues and not coverage_issues:
                result["status"] = "ready"
    result["content_fingerprint"] = fingerprint(result)
    return result


def get_runtime_action_dispatch_profile(db_path, *, character_id, skill_depot_id, skill_id, slot,
                                        effective_level, constellation, variant, cast_stage=None,
                                        version_line="release", snapshot_id=None):
    """Validate the skill identity and resolve the packaged reviewed profile."""
    from genshin_db.runtime_action_resource import resolve_action_resource_entry
    from genshin_db.runtime_queries import _read

    selector = {"version_line": version_line, "character_id": character_id,
        "skill_depot_id": skill_depot_id, "skill_id": skill_id, "slot": slot,
        "effective_level": effective_level, "constellation": constellation,
        "variant": variant, "cast_stage": cast_stage}
    validate_selector(selector)
    with _read(db_path, version_line, snapshot_id) as (conn, state):
        entry, _ = resolve_action_resource_entry(conn, state, character_id=character_id,
            skill_id=skill_id, skill_depot_id=skill_depot_id)
        selection = entry["selection"]
        if selection["skill_depot_id"] != skill_depot_id or skill_id != selection["skill_id"]:
            raise ValueError("动作发射选择与数据库技能身份不一致")
        if slot not in selection["matching_slots"]:
            raise ValueError("动作发射slot与数据库技能身份不一致")
        snapshot = copy.deepcopy(state)
    from genshin_db.runtime_gameplay_action_manifest import (
        load_gameplay_action_manifest_bundle,
    )
    coverage_bundle = load_gameplay_action_manifest_bundle()
    selected = resolve_action_dispatch_profile(load_action_dispatch_bundle(), selector=selector,
        snapshot=snapshot, coverage_bundle=coverage_bundle)
    profile = selected.get("profile")
    if profile is None:
        return selected
    from genshin_db.runtime_attack_metadata import get_runtime_attack_metadata_profile
    from genshin_db.runtime_periodic_attack import get_runtime_periodic_attack_profile

    query_selection = {"character_id": character_id, "skill_depot_id": skill_depot_id,
        "skill_id": skill_id, "talent_level": effective_level, "constellation": constellation,
        "version_line": version_line, "snapshot_id": snapshot["snapshot_id"]}
    receipts = []
    attack_emissions = [row for row in profile["emissions"] if row["attack_source"]["kind"] == "attack_metadata"]
    for emission in attack_emissions:
        source = emission["attack_source"]
        query = get_runtime_attack_metadata_profile(db_path, candidate_id=source["candidate_id"], **query_selection)
        candidate = query.get("selected_candidate") or {}
        projection = candidate.get("projection") or {}
        identity = projection.get("application_identity") or {}
        attack_tag = projection.get("attack_tag") or {}
        receipts.append({"component_id": emission["component_id"], "kind": "attack_metadata",
            "candidate_id": source["candidate_id"], "origin_reference_id": None,
            "source_fingerprint": query.get("source_fingerprint"), "status": query.get("status"),
            "application_identity_status": identity.get("status"),
            "application_identity_tag": identity.get("tag"), "interval_seconds": None,
            "database_icd_rule": _database_icd_rule(projection),
            "classification": (_ATTACK_TAG_CLASSIFICATION.get(attack_tag.get("value"))
                               if attack_tag.get("status") == "observed" else None)})
    periodic_groups = {}
    for emission in profile["emissions"]:
        source = emission["attack_source"]
        if source["kind"] == "periodic_attack":
            periodic_groups.setdefault(source["origin_reference_id"], []).append(emission)
    for origin, emissions in periodic_groups.items():
        ids = [row["attack_source"]["candidate_id"] for row in emissions]
        query = get_runtime_periodic_attack_profile(db_path, origin_reference_id=origin,
            candidate_ids=ids, **query_selection)
        candidates = {row["candidate_id"]: row for row in query.get("selected_candidates", [])}
        for emission in emissions:
            source = emission["attack_source"]
            candidate = candidates.get(source["candidate_id"], {})
            projection = candidate.get("projection") or {}
            identity = projection.get("application_identity") or {}
            timer = projection.get("timer") or {}
            receipts.append({"component_id": emission["component_id"], "kind": "periodic_attack",
                "candidate_id": source["candidate_id"], "origin_reference_id": origin,
                "source_fingerprint": query.get("source_fingerprint"),
                # The query status covers the complete selected group.  One
                # timer-only pending channel must not contaminate an otherwise
                # ready sibling receipt; verification below evaluates each
                # reviewed emission independently before committing atomically.
                "status": candidate.get("status"),
                "application_identity_status": identity.get("status"),
                "application_identity_tag": identity.get("tag"),
                "database_icd_rule": _database_icd_rule(projection),
                "source_issue_codes": sorted({
                    row.get("code") for row in [*query.get("issues", []),
                                                *candidate.get("issues", [])]
                    if isinstance(row, dict) and isinstance(row.get("code"), str)}),
                "interval_seconds": timer.get("seconds"),
                "classification": (projection.get("classification") or {}).get("value")})
    return resolve_action_dispatch_profile(load_action_dispatch_bundle(), selector=selector,
        snapshot=snapshot, source_receipts=receipts, coverage_bundle=coverage_bundle)
