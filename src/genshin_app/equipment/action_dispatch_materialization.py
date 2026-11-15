"""App-owned materialization of reviewed action-dispatch resolutions.

The database resolver returns neutral evidence and reviewed profiles. This
module owns conversion of that neutral result into executable App input
shapes. Database capability output describes evidence queries; executable App
projections are described only by this module and the App capability aggregator.
"""
from __future__ import annotations

import copy
import math
from decimal import Decimal

from genshin_db.action_dispatch_schema import (
    APPLICATION_CADENCE_KIND,
    CONTRACT_ID,
    validate_profile,
)
from genshin_db.runtime_catalog import fingerprint

_SOURCE_VERIFICATION_ISSUES = {
    "action_dispatch_source_verification_invalid",
    "action_dispatch_source_verification_unmatched",
    "action_dispatch_source_fingerprint_mismatch",
    "action_dispatch_interval_mismatch",
    "action_dispatch_classification_mismatch",
    "action_dispatch_application_identity_unverified",
    "action_dispatch_application_identity_conflict",
    "action_dispatch_source_verification_required",
}


def action_dispatch_materialization_capabilities():
    """Advertise App-owned executable input projections."""
    return {
        "contract_id": "action-dispatch-materialization-v1",
        "source_contract_id": CONTRACT_ID,
        "application_cadence": {
            "kind": APPLICATION_CADENCE_KIND,
            "scope": "channel_generation",
            "fields": ["period_hits", "first_attempt_ordinal"],
            "semantics": "pre-ICD尝试门控；不替代普通计时器与命中计数器。",
        },
        "application_adapter": {
            "contract_id": "application-adapter-v1",
            "scope": "one exact fixed or periodic emission component",
            "accepted_baseline": "sha256(reviewed emission.application)",
            "public_audit": ["base_rule", "effective_rule", "adapter_fingerprint",
                             "database_identity", "identity_divergence",
                             "base_icd_rule_fingerprint", "base_icd_rule", "effective_icd_rule"],
            "mode_projection": ["inherit", "sequence", "no_icd", "no_application", "pending"],
            "attempt_gate_projection": {
                "periodic": ["inherit", "none", APPLICATION_CADENCE_KIND],
                "fixed": ["inherit", "none"],
            },
            "icd_rule_projection": {
                "mode": "timer_counter", "first_hit": 1,
                "semantics": "首次或计时到期允许；随后第1+N*hit_interval次允许；计数不移动计时锚点。",
                "base": "fresh database attenuation rule",
                "effective_rule_group": "stable derived identity; never overwrites database rule_group",
            },
            "sequence_source": "database rule_group; confirmed component evidence may adapt counter_tag; no inline sequence arrays",
        },
        "mapping_targets": [
            "calibrated_single_application_v2",
            "calibrated_multi_application_v2",
            "calibrated_finite_application_v1",
        ],
        "coverage_materialization": {
            "source_contract": "gameplay-action-manifest-v1",
            "receipt": ["expected components", "expected occurrences", "materialized occurrences", "unresolved occurrences"],
            "finite_timing": "fixed offset=0 prototypes; occurrence timing/order come only from topology.steps",
            "unaccepted_or_missing": "coverage unknown; never self-certify completeness",
        },
        "safety": {
            "ready_resolution_required": True,
            "partial_materialization": False,
            "role_specific_runtime_branching": False,
        },
    }


def _adapter_verification_ready(adapter, mode, component=None):
    verification = adapter.get("verification") or {}
    required = set()
    if adapter.get("mode") not in (None, "inherit", "pending"):
        required.update(("application_mode", "shared_scope"))
        if mode == "sequence":
            required.add("counter_tag")
    if (adapter.get("attempt_gate") or {}).get("mode") not in (None, "inherit"):
        required.add("attempt_gate")
    if (adapter.get("icd_rule") or {}).get("mode") not in (None, "inherit"):
        required.add("icd_rule")
    return (adapter.get("status") == "confirmed"
            and bool(required)
            and verification.get("status") == "confirmed"
            and all(isinstance(verification.get(key), str) and verification[key].strip()
                    for key in ("locator", "version", "reviewed_at", "component_scope"))
            and (component is None or verification.get("component_scope") == component)
            and required <= set(verification.get("confirmed_fields") or []))


def _independent_schedule_components(expectation):
    if not isinstance(expectation, dict):
        return set()
    if expectation.get("topology_kind") == "finite_sequence":
        return {row.get("component_id") for row in expectation.get("components", [])}
    if expectation.get("topology_kind") == "periodic_channels":
        return {row.get("component_id") for row in expectation.get("channels", [])
                if (row.get("schedule") or {}).get("kind") == "explicit_offsets"}
    return set()


def _verify_source_receipts(profile, source_receipts, application_adapters=None, *,
                            topology_kind=None, independent_schedule_components=()):
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
        adapter_audit = (application_adapters or {}).get(component)
        adapter = (adapter_audit or {}).get("declaration") or {}
        mode_overridden = adapter.get("mode") not in (None, "inherit")
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
            if (not identity_ready and not (
                    mode_overridden and _adapter_verification_ready(adapter, "sequence", component))
                    and application["verification"] is None):
                issues.append({"code": "action_dispatch_application_identity_unverified",
                               "component_id": component,
                               "message": "数据库未确认同一counter_tag，且档案没有逐组件结构化证据"})
            if (receipt["application_identity_status"] == "ready"
                    and receipt["application_identity_tag"] != application["counter_tag"]
                    and not (mode_overridden and _adapter_verification_ready(
                        adapter, "sequence", component))):
                issues.append({"code": "action_dispatch_application_identity_conflict",
                               "component_id": component,
                               "message": "档案counter_tag与数据库确认身份冲突"})
        elif ((receipt["application_identity_status"] == "ready"
               or receipt["application_identity_tag"] is not None)
              and not (mode_overridden and _adapter_verification_ready(
                  adapter, application["mode"], component))):
            issues.append({"code": "action_dispatch_application_identity_conflict",
                           "component_id": component,
                           "message": "档案声明无序列计数，但新鲜数据库候选存在附着计数身份"})
        audit.append(copy.deepcopy(receipt))
    for component in sorted(set(expected) - seen):
        issues.append({"code": "action_dispatch_source_verification_required", "component_id": component,
                       "message": "缺少该攻击组件的同快照新鲜来源核对"})
    return audit, issues


def _gate_snapshot(application):
    cadence = application.get("cadence")
    if cadence is None:
        return {"mode": "none", "period_hits": None, "first_attempt_ordinal": None}
    return {"mode": APPLICATION_CADENCE_KIND, "period_hits": cadence["period_hits"],
            "first_attempt_ordinal": cadence["first_attempt_ordinal"]}


def _rule_snapshot(application):
    return {"mode": application["mode"], "counter_tag": application["counter_tag"],
            "attempt_gate": _gate_snapshot(application)}


def _project_application_adapters(profile, scenario_calibration):
    """Apply bounded per-component policy deltas to a fresh reviewed profile copy."""
    supplied = scenario_calibration or {}
    issues, audits = [], {}
    emissions = {row["component_id"]: row for row in profile["emissions"]}
    for component, calibration in supplied.items():
        if not isinstance(calibration, dict):
            continue
        adapter = calibration.get("application_adapter")
        if adapter is None:
            continue
        emission = emissions.get(component)
        if emission is None:
            continue  # ordinary scenario validation reports the unknown component
        base = copy.deepcopy(emission["application"])
        base_fingerprint = fingerprint(base)
        declaration = copy.deepcopy(adapter)
        audit = {"contract_id": "application-adapter-v1", "component_id": component,
            "accepted_base_fingerprint": declaration.get("accepted_base_fingerprint"),
            "base_application_fingerprint": base_fingerprint,
            "adapter_fingerprint": fingerprint(declaration), "declaration": declaration,
            "base_rule": _rule_snapshot(base), "effective_rule": _rule_snapshot(base)}
        audits[component] = audit
        if declaration.get("accepted_base_fingerprint") != base_fingerprint:
            issues.append({"code": "action_dispatch_adapter_base_stale", "component_id": component,
                           "message": "附着适配接受的受审基底指纹已变化，需要重新核对"})
            continue
        if declaration.get("status") != "confirmed":
            issues.append({"code": "action_dispatch_adapter_pending", "component_id": component,
                           "message": "附着适配仍为pending，不能生成确定性事件"})
            continue
        effective = copy.deepcopy(base)
        mode = declaration.get("mode")
        if mode == "pending":
            issues.append({"code": "action_dispatch_adapter_pending", "component_id": component,
                           "message": "附着适配模式尚未核实"})
            continue
        gate = declaration.get("attempt_gate") or {}
        icd_rule = declaration.get("icd_rule") or {"mode": "inherit"}
        if emission["kind"] == "fixed" and gate.get("mode") == APPLICATION_CADENCE_KIND:
            issues.append({"code": "action_dispatch_adapter_fixed_gate_unsupported",
                           "component_id": component,
                           "message": "fixed组件没有代内命中节律；只允许inherit或none门控"})
            continue
        if ((mode != "inherit" or gate.get("mode") != "inherit"
             or icd_rule.get("mode") != "inherit")
                and not _adapter_verification_ready(declaration, mode, component)):
            issues.append({"code": "action_dispatch_adapter_verification_required",
                           "component_id": component,
                           "message": "附着模式或尝试门控修改需要逐组件confirmed结构化证据"})
            continue
        if mode != "inherit":
            mode_verification = copy.deepcopy(declaration.get("verification"))
            mode_verification["confirmed_fields"] = [field for field in
                mode_verification["confirmed_fields"] if field != "attempt_gate"]
            effective.update(mode=mode, counter_tag=declaration.get("counter_tag"),
                             verification=mode_verification)
        gate_mode = gate.get("mode")
        if gate_mode == "none":
            effective.pop("cadence", None)
        elif gate_mode == APPLICATION_CADENCE_KIND:
            effective["cadence"] = {"kind": APPLICATION_CADENCE_KIND,
                "period_hits": gate.get("period_hits"),
                "first_attempt_ordinal": gate.get("first_attempt_ordinal"),
                "scope": "channel_generation"}
        elif gate_mode != "inherit":
            issues.append({"code": "action_dispatch_adapter_gate_invalid", "component_id": component,
                           "message": "附着适配门控不受支持"})
            continue
        if icd_rule.get("mode") == "timer_counter":
            if effective["mode"] != "sequence":
                issues.append({"code": "action_dispatch_adapter_icd_mode_invalid",
                               "component_id": component,
                               "message": "timer_counter局部规则只适用于sequence附着模式"})
                continue
            interval = icd_rule.get("hit_interval")
            reset = icd_rule.get("reset_seconds")
            if (type(interval) is not int or not 1 <= interval <= 64
                    or reset is not None and (
                        type(reset) not in (int, float) or isinstance(reset, bool)
                        or not math.isfinite(reset) or not 0 < reset <= 10**9
                        or (Decimal(str(reset)) * 1_000_000)
                        != (Decimal(str(reset)) * 1_000_000).to_integral_value())):
                issues.append({"code": "action_dispatch_adapter_icd_rule_invalid",
                               "component_id": component,
                               "message": "timer_counter命中周期或显式计时参数不合法"})
                continue
        if ((effective["mode"] == "sequence") != (effective.get("counter_tag") is not None)
                or effective.get("cadence") is not None
                and effective["mode"] not in ("sequence", "no_icd")):
            issues.append({"code": "action_dispatch_adapter_effective_rule_invalid",
                           "component_id": component,
                           "message": "附着适配后的模式、计数标签与尝试门控不自洽"})
            continue
        cadence = effective.get("cadence")
        if cadence is not None and (type(cadence.get("period_hits")) is not int
                or type(cadence.get("first_attempt_ordinal")) is not int
                or not 1 <= cadence["first_attempt_ordinal"] <= cadence["period_hits"] <= 256):
            issues.append({"code": "action_dispatch_adapter_gate_invalid", "component_id": component,
                           "message": "附着适配门控周期或首次位置不合法"})
            continue
        emission["application"] = effective
        audit["effective_rule"] = _rule_snapshot(effective)
    return audits, issues


def _application_rule_audit(profile, application_adapters, receipts):
    by_component = {row["component_id"]: row for row in receipts}
    rows, issues = [], []
    for emission in profile["emissions"]:
        component = emission["component_id"]
        application = emission["application"]
        adapter = application_adapters.get(component)
        receipt = by_component.get(component) or {}
        identity = {"status": receipt.get("application_identity_status"),
                    "counter_tag": receipt.get("application_identity_tag")}
        effective_rule = _rule_snapshot(application)
        database_tag = identity["counter_tag"] if identity["status"] == "ready" else None
        divergence = (database_tag is not None and (
            effective_rule["mode"] != "sequence"
            or effective_rule["counter_tag"] != database_tag))
        row = {"contract_id": "application-rule-audit-v1", "component_id": component,
            "emission_kind": emission["kind"],
            "base_application_fingerprint": (adapter["base_application_fingerprint"]
                                               if adapter else fingerprint(application)),
            "base_rule": copy.deepcopy(adapter["base_rule"] if adapter else effective_rule),
            "effective_rule": copy.deepcopy(effective_rule),
            "adapter_fingerprint": adapter["adapter_fingerprint"] if adapter else None,
            "database_identity": identity, "identity_divergence": divergence}
        database_rule = receipt.get("database_icd_rule")
        if isinstance(database_rule, dict):
            base_rule_fingerprint = fingerprint(database_rule)
            row.update(base_icd_rule_fingerprint=base_rule_fingerprint,
                       base_icd_rule=copy.deepcopy(database_rule))
            if effective_rule["mode"] == "sequence":
                row["effective_icd_rule"] = {
                    "kind": "sequence", "rule_group": database_rule["rule_group"],
                    "reset_seconds": database_rule["reset_seconds"],
                    "sequence": copy.deepcopy(database_rule["sequence"]),
                    "hit_interval": None,
                    "first_hit": next((index + 1 for index, value in enumerate(
                        database_rule["sequence"]) if value), None)}
        declaration = (adapter or {}).get("declaration") or {}
        icd_rule = declaration.get("icd_rule") or {"mode": "inherit"}
        if icd_rule.get("mode") == "timer_counter":
            if not isinstance(database_rule, dict):
                issues.append({"code": "action_dispatch_adapter_icd_base_pending",
                               "component_id": component,
                               "message": "局部ICD规则适配需要当前数据库衰减规则基线"})
            elif icd_rule.get("accepted_base_rule_fingerprint") != base_rule_fingerprint:
                issues.append({"code": "action_dispatch_adapter_icd_base_stale",
                               "component_id": component,
                               "message": "局部ICD规则接受的数据库基线指纹已变化，需要重新核对"})
            else:
                reset = (database_rule["reset_seconds"] if icd_rule.get("reset_seconds") is None
                         else icd_rule["reset_seconds"])
                derived_group = "adapter.icd." + fingerprint({
                    "base_rule_fingerprint": base_rule_fingerprint,
                    "kind": "timer_counter",
                    "reset_seconds": reset,
                    "hit_interval": icd_rule["hit_interval"],
                })[:48]
                row["effective_icd_rule"] = {
                    "kind": "timer_counter", "rule_group": derived_group,
                    "reset_seconds": reset, "sequence": None,
                    "hit_interval": icd_rule["hit_interval"], "first_hit": 1}
        rows.append(row)
    return rows, issues


def _all_emission_evidence_ready(profile, application_adapters=None):
    issues = []
    for emission in profile["emissions"]:
        for field, evidence in emission["evidence"].items():
            adapter = ((application_adapters or {}).get(emission["component_id"]) or {}).get(
                "declaration", {})
            if (field == "application" and adapter.get("mode") not in (None, "inherit", "pending")
                    and _adapter_verification_ready(
                        adapter, adapter["mode"], emission["component_id"])):
                continue
            if evidence["status"] != "confirmed" or evidence["category"] == "unsupported":
                issues.append({"code": "action_dispatch_evidence_pending", "component_id": emission["component_id"],
                               "field": field, "message": "攻击组件证据尚未确认"})
        source = emission["attack_source"]
        if source["candidate_id"] is None or source["source_fingerprint"] is None:
            issues.append({"code": "action_dispatch_source_pending", "component_id": emission["component_id"],
                           "message": "攻击候选或来源指纹缺失"})
        if emission["application"]["mode"] == "pending":
            issues.append({"code": "action_dispatch_application_pending", "component_id": emission["component_id"],
                           "message": "附着方式仍为pending"})
    return issues


def _resolved_landed(profile, scenario_calibration):
    values, issues = {}, []
    supplied = scenario_calibration or {}
    unknown = set(supplied) - {emission["component_id"] for emission in profile["emissions"]}
    if unknown:
        issues.append({"code": "action_dispatch_unknown_calibration", "components": sorted(unknown),
                       "message": "场景校准引用了档案中不存在的攻击组件"})
    for emission in profile["emissions"]:
        component = emission["component_id"]
        calibration = supplied.get(component, {})
        if not isinstance(calibration, dict) or set(calibration) - {
                "landed", "order", "application_adapter"}:
            issues.append({"code": "action_dispatch_invalid_calibration", "component_id": component,
                           "message": "场景校准只允许landed、order与application_adapter"})
            continue
        landed_valid = True
        if emission["landed"]["policy"] == "default":
            if "landed" in calibration:
                issues.append({"code": "action_dispatch_landed_override_binding_required",
                    "component_id": component,
                    "message": "默认命中值的本轴例外需要具体action_id/ordinal，不能在档案层静默覆盖"})
                landed_valid = False
            landed = emission["landed"]["default"]
        else:
            landed = calibration.get("landed")
            if type(landed) is not bool:
                issues.append({"code": "action_dispatch_scenario_required", "component_id": component,
                               "field": "landed", "message": "本轴是否命中需要明确场景输入"})
                landed_valid = False
            else:
                issues.append({"code": "action_dispatch_landed_binding_required", "component_id": component,
                               "field": "landed",
                               "message": "本轴命中值需要具体action_id/ordinal，不能作为同一能力全部实例的默认值"})
                landed_valid = False
        order = calibration.get("order", 0)
        if type(order) is not int or not -10**9 <= order <= 10**9:
            issues.append({"code": "action_dispatch_invalid_calibration", "component_id": component,
                           "field": "order", "message": "同刻顺序必须是有界整数"})
            continue
        if landed_valid:
            values[component] = {"landed": landed, "order": order, "order_explicit": "order" in calibration}
        elif "order" in calibration:
            issues.append({"code": "action_dispatch_order_binding_required", "component_id": component,
                           "message": "本轴同刻顺序需要具体action_id/ordinal，不能在能力档案层静默丢弃"})
    return values, issues


def _ids(prefix, component=None):
    base = prefix if component is None else prefix + "." + component
    return {"profile": prefix, "channel": component, "hit": base + ".hit", "application": base + ".application"}


def _selector_source(profile, component, source_text, *, application_adapter=None,
                     application_rule_audit=None):
    selector, emission = profile["selector"], component
    source = {"action_event_profile_id": source_text, "version_line": selector["version_line"],
        "selection": {"character_id": selector["character_id"], "skill_id": selector["skill_id"],
            "skill_depot_id": selector["skill_depot_id"], "constellation": selector["constellation"],
            "talent_level": selector["effective_level"]},
        "candidate_id": emission["attack_source"]["candidate_id"],
        "application_mode": emission["application"]["mode"], "purpose": "single_stat_damage_multiplier",
        "status": "confirmed", "source": "action_dispatch_profile:" + profile["profile_id"]}
    if emission["application"]["counter_tag"] is not None:
        source["counter_tag"] = emission["application"]["counter_tag"]
    if emission["application"]["verification"] is not None:
        source["verification"] = copy.deepcopy(emission["application"]["verification"])
    if application_adapter is not None:
        source["application_adapter"] = copy.deepcopy(application_adapter)
        source["application_rule_audit"] = copy.deepcopy(application_rule_audit)
    return source


def compile_action_dispatch_inputs(resolution, *, ability_id, actor=None, actor_character_id=None,
                                   ability_action_type_id=None, ability_variant=None, id_prefix=None,
                                   scenario_calibration=None):
    """Map one ready dispatch row to existing native input fragments.

    This does not mutate a plan.  It creates complete prototype rows only from
    reviewed profile fields and separately declares fields that the native
    metadata source must own.  The current native contracts can represent one
    attack-metadata fixed emission, or a homogeneous periodic-source multi
    profile.  Other combinations remain pending atomically.
    """
    result = {"contract_id": CONTRACT_ID, "status": "pending", "target_contract": None,
        "profile_id": None, "fragments": None, "source_verification": None,
        "application_adapter_audit": [], "application_rule_audit": [],
        "coverage_status": resolution.get("coverage_status", "unknown"),
        "coverage_manifest": copy.deepcopy(resolution.get("coverage_manifest")),
        "coverage_manifest_fingerprint": resolution.get("coverage_manifest_fingerprint"),
        "coverage_expectation": copy.deepcopy(resolution.get("coverage_expectation")),
        "coverage_issues": copy.deepcopy(resolution.get("coverage_issues", [])),
        "issues": []}
    if not resolution.get("profile"):
        result["issues"].append({"code": "action_dispatch_resolution_pending",
                                 "message": "只有精确、当前且已审查的档案可以映射"})
        result["content_fingerprint"] = fingerprint(result)
        return result
    profile = copy.deepcopy(resolution["profile"])
    validate_profile(profile)
    reviewed_profile_fingerprint = fingerprint(profile)
    unhandled_resolution_issues = [copy.deepcopy(issue) for issue in resolution.get("issues", [])
                                   if issue.get("code") not in _SOURCE_VERIFICATION_ISSUES]
    if unhandled_resolution_issues:
        result["issues"].append({"code": "action_dispatch_resolution_pending",
                                 "message": "新鲜档案解析仍有非附着适配可处理的诊断"})
        result["issues"].extend(unhandled_resolution_issues)
    application_adapters, adapter_issues = _project_application_adapters(
        profile, scenario_calibration)
    result["application_adapter_audit"] = [copy.deepcopy(application_adapters[key])
        for key in (row["component_id"] for row in profile["emissions"]) if key in application_adapters]
    result["issues"].extend(adapter_issues)
    validate_profile(profile)
    verification = resolution.get("source_verification") or {}
    topology_kind = (resolution.get("coverage_expectation") or {}).get("topology_kind")
    effective_receipts, verification_issues = _verify_source_receipts(
        profile, verification.get("receipts"), application_adapters,
        topology_kind=topology_kind,
        independent_schedule_components=_independent_schedule_components(
            resolution.get("coverage_expectation") or {}))
    result["source_verification"] = {
        "status": "ready" if not verification_issues else "pending",
        "receipts": effective_receipts,
        "application_adapter_audit": copy.deepcopy(result["application_adapter_audit"]),
    }
    result["application_rule_audit"], icd_rule_issues = _application_rule_audit(
        profile, application_adapters, effective_receipts)
    result["issues"].extend(icd_rule_issues)
    if verification_issues:
        result["issues"].append({"code": "action_dispatch_source_verification_required",
                                 "message": "编译前必须重新核对同快照候选、来源指纹与周期timer"})
        result["issues"].extend(verification_issues)
    result["issues"].extend(_all_emission_evidence_ready(profile, application_adapters))
    landed, landed_issues = _resolved_landed(profile, scenario_calibration)
    result["issues"].extend(landed_issues)
    try:
        from genshin_db.action_dispatch_schema import _id
        _id(ability_id, "ability_id")
    except ValueError as exc:
        result["issues"].append({"code": "action_dispatch_ability_invalid", "message": str(exc)})
    prefix = id_prefix or "dispatch." + profile["profile_id"]
    try:
        _id(prefix, "id_prefix")
    except ValueError as exc:
        result["issues"].append({"code": "action_dispatch_prefix_invalid", "message": str(exc)})
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 128:
        result["issues"].append({"code": "action_dispatch_actor_required",
                                 "message": "需要从方案能力上下文提供原型所属角色"})
    selector = profile["selector"]
    if actor_character_id != selector["character_id"]:
        result["issues"].append({"code": "action_dispatch_actor_identity_mismatch",
                                 "message": "方案角色数据库身份与发射档案character_id不一致"})
    expected_action_type = {"normal": "action.normal_attack", "skill": "action.elemental_skill",
                            "burst": "action.elemental_burst"}[selector["slot"]]
    if ability_action_type_id != expected_action_type or ability_variant != selector["variant"]:
        result["issues"].append({"code": "action_dispatch_ability_identity_mismatch",
                                 "message": "方案能力类别或variant与发射档案选择器不一致"})
    if any(value["order_explicit"] for value in landed.values()):
        result["issues"].append({"code": "action_dispatch_order_binding_required",
            "message": "现有档案映射缺少具体action_id/ordinal，不能静默丢弃场景同刻顺序"})
    if result["issues"]:
        result["content_fingerprint"] = fingerprint(result)
        return result
    emissions = profile["emissions"]
    source_kinds = {item["attack_source"]["kind"] for item in emissions}
    coverage = resolution.get("coverage_expectation") or {}
    finite = (resolution.get("coverage_status") == "ready"
              and coverage.get("topology_kind") == "finite_sequence")
    periodic_topology = (resolution.get("coverage_status") == "ready"
                         and coverage.get("topology_kind") == "periodic_channels")
    periodic_channels = ({row["component_id"]: row for row in coverage.get("channels", [])}
                         if periodic_topology else {})
    variant = profile["selector"]["variant"]
    if variant not in ("default", "tap", "hold"):
        result["issues"].append({"code": "action_dispatch_variant_unrepresentable",
                                 "message": "现有动作事件输入尚不能表达该variant"})
    if profile["selector"]["cast_stage"] is not None:
        result["issues"].append({"code": "action_dispatch_cast_stage_unrepresentable",
                                 "message": "现有能力绑定不携带cast_stage，禁止把阶段档案应用到同能力全部动作"})
    finite_occurrences = coverage.get("occurrences", []) if finite else []
    finite_single = (finite and len(emissions) == 1 and len(finite_occurrences) == 1
        and finite_occurrences[0].get("component_id") == emissions[0]["component_id"]
        and emissions[0]["kind"] == "fixed" and source_kinds == {"attack_metadata"})
    single = ((not finite and len(emissions) == 1 and emissions[0]["kind"] == "fixed"
              and source_kinds == {"attack_metadata"}) or finite_single)
    multi = not finite and source_kinds == {"periodic_attack"}
    finite_native = finite and source_kinds == {"periodic_attack"}
    if not single and not multi and not finite_native:
        result["issues"].append({"code": "action_dispatch_target_contract_unavailable",
            "message": "当前原生输入不能原子表达这组来源；禁止拆成部分结果"})
    if finite:
        if coverage.get("interruption_policy") != "none":
            result["issues"].append({"code": "action_dispatch_finite_interruption_pending",
                "message": "本版只执行独立清单确认不会中断的有限序列"})
        if any(row["kind"] != "fixed" for row in emissions):
            result["issues"].append({"code": "action_dispatch_finite_component_invalid",
                "message": "有限序列的唯一组件必须使用fixed发射；重复由步骤清单表达"})
    lifecycle = profile["lifecycle"]
    if multi or finite_native:
        slot_damage_types = {"normal": {"damage.normal_attack", "damage.charged_attack", "damage.plunging_attack"},
                             "skill": {"damage.elemental_skill"}, "burst": {"damage.elemental_burst"}}
        if any(len(row["prototype"]["damage_type_ids"]) != 1
               or row["prototype"]["damage_type_ids"][0] not in slot_damage_types[selector["slot"]]
               for row in emissions):
            result["issues"].append({"code": "action_dispatch_multi_scope_pending",
                "message": "periodic source要求精确天赋槽与每个发射分量伤害分类一致"})
        if multi:
            required_lifecycle = ("establishment_offset_seconds", "instance_end_offset_seconds", "precision_seconds",
                                  "owner_and_target_survive", "no_interrupt")
            if lifecycle["evidence"]["status"] != "confirmed" or any(lifecycle[key] is None for key in required_lifecycle):
                result["issues"].append({"code": "action_dispatch_lifecycle_pending",
                                         "message": "多通道映射需要完整、已确认的生命周期"})
            replacement = lifecycle.get("replacement_policy")
            if replacement is None and lifecycle["no_refresh_replacement_or_early_despawn"] is not True:
                result["issues"].append({"code": "action_dispatch_lifecycle_pending",
                                         "message": "未声明刷新替换时必须确认实例不会刷新或提前消失"})
            elif replacement is not None and (replacement != "replace_previous"
                    or lifecycle["no_refresh_replacement_or_early_despawn"] is not False
                    or lifecycle.get("no_early_despawn") is not True):
                result["issues"].append({"code": "action_dispatch_lifecycle_pending",
                                         "message": "刷新替换需要明确清空旧实例并独立确认不会提前消失"})
        else:
            pass
        origins = {item["attack_source"]["origin_reference_id"] for item in emissions}
        if None in origins or len(origins) != 1:
            result["issues"].append({"code": "action_dispatch_origin_ambiguous",
                                     "message": "多通道来源必须共享唯一创建来源"})
        candidates = [item["attack_source"]["candidate_id"] for item in emissions]
        if len(candidates) != len(set(candidates)):
            result["issues"].append({"code": "action_dispatch_candidate_reused",
                                     "message": "当前周期来源契约要求每通道使用独立候选"})
    if result["issues"]:
        result["content_fingerprint"] = fingerprint(result)
        return result

    requirements = []
    rule_audits = {row["component_id"]: row for row in result["application_rule_audit"]}
    if single:
        emission = emissions[0]
        ids = _ids(prefix, emission["component_id"])
        finite_occurrence = finite_occurrences[0] if finite_single else None
        offset_seconds = (finite_occurrence["offset_ticks"] / 1_000_000
                          if finite_occurrence is not None
                          else emission["schedule"]["offset_seconds"])
        action_profile = {"id": prefix, "kind": "calibrated_single_application_v2",
            "prototype_hit_id": ids["hit"], "variant": variant,
            "calibration": {"offset_seconds": offset_seconds,
                "precision_seconds": emission["schedule"]["precision_seconds"], "status": "confirmed",
                "source": "action_dispatch_profile:" + profile["profile_id"]},
            "application_profile_id": ids["application"], "default_landed": landed[emission["component_id"]]["landed"],
            "self_sampling": emission["self_sampling"], "accepted_profile_fingerprint": None}
        if finite_occurrence is not None:
            action_profile["topology_occurrence"] = {
                "contract_id": "finite-sequence-step-v1",
                "occurrence_id": finite_occurrence["occurrence_id"],
                "component_id": finite_occurrence["component_id"],
                "ordinal": finite_occurrence["ordinal"],
                "topology_order": finite_occurrence["order"],
                "terminal": finite_occurrence["terminal"],
            }
        component = emission["component_id"]
        source = _selector_source(
            profile,
            emission,
            prefix,
            application_adapter=application_adapters.get(component),
            application_rule_audit=(rule_audits.get(component)
                                    if component in application_adapters else None),
        )
        prototype = {"id": ids["hit"], "damage_type_ids": copy.deepcopy(emission["prototype"]["damage_type_ids"]),
            "t": 0, "character": actor, "skill": emission["prototype"]["skill_label"],
            "hit_name": emission["prototype"]["hit_name"], "description": emission["prototype"]["description"],
            "damage_system": emission["prototype"]["damage_system"],
            "scaling_stat": emission["prototype"]["scaling_stat"], "count": emission["prototype"]["count"]}
        fragments = {"action_event_profiles": [action_profile],
            "action_event_bindings": [{"ability_id": ability_id, "profile_id": prefix}],
            "attack_metadata_sources": [source], "periodic_attack_sources": [],
            "prototype_hits": [prototype],
            "prototype_requirements": [{"id": ids["hit"], "component_id": emission["component_id"],
                "owned_fields": ["element", "multiplier"], "application_profile_id": ids["application"]}],
            "source_expectations": [{"component_id": emission["component_id"],
                "candidate_id": emission["attack_source"]["candidate_id"],
                "source_fingerprint": emission["attack_source"]["source_fingerprint"],
                "origin_reference_id": None}]}
        target = "calibrated_single_application_v2"
    elif finite_native:
        channels, mappings = [], []
        for emission in emissions:
            component, ids = emission["component_id"], _ids(prefix, emission["component_id"])
            channels.append({"channel_id": component, "prototype_hit_id": ids["hit"],
                "application_profile_id": ids["application"],
                "default_landed": landed[component]["landed"],
                "self_sampling": emission["self_sampling"]})
            mapping = {"channel_id": component,
                "candidate_id": emission["attack_source"]["candidate_id"],
                "application_mode": emission["application"]["mode"]}
            if emission["application"]["counter_tag"] is not None:
                mapping["counter_tag"] = emission["application"]["counter_tag"]
            if emission["application"]["verification"] is not None:
                mapping["verification"] = copy.deepcopy(emission["application"]["verification"])
            if component in application_adapters:
                mapping["application_adapter"] = copy.deepcopy(application_adapters[component])
                mapping["application_rule_audit"] = copy.deepcopy(rule_audits[component])
            mappings.append(mapping)
            requirements.append({"id": ids["hit"], "component_id": component, "t": 0,
                "owned_fields": ["element", "multiplier"],
                "application_profile_id": ids["application"],
                "expected_interval_seconds": emission["schedule"].get("interval_seconds")})
        occurrences = coverage["occurrences"]
        steps = [{"step_id": row["occurrence_id"], "ordinal": row["ordinal"],
            "component_id": row["component_id"], "offset_seconds": row["offset_ticks"] / 1_000_000,
            "order": row["order"]} for row in occurrences]
        terminals = [row["occurrence_id"] for row in occurrences if row.get("terminal")]
        if len(terminals) > 1:
            result["issues"].append({"code": "action_dispatch_finite_terminal_invalid",
                "message": "有限序列只能有一个终结步骤"})
        action_profile = {"id": prefix, "kind": "calibrated_finite_application_v1",
            "variant": variant, "interruption_policy": coverage["interruption_policy"],
            "channels": channels, "steps": steps,
            "terminal_step_id": terminals[0] if terminals else None,
            "accepted_profile_fingerprint": None}
        selector = profile["selector"]
        periodic_source = {"action_event_profile_id": prefix,
            "version_line": selector["version_line"],
            "selection": {"character_id": selector["character_id"],
                "skill_id": selector["skill_id"], "skill_depot_id": selector["skill_depot_id"],
                "constellation": selector["constellation"],
                "talent_level": selector["effective_level"]},
            "origin_reference_id": emissions[0]["attack_source"]["origin_reference_id"],
            "channels": mappings, "purpose": "calibrated_periodic_attack",
            "status": "confirmed", "source": "action_dispatch_profile:" + profile["profile_id"]}
        prototypes = [{"id": _ids(prefix, emission["component_id"])["hit"],
            "damage_type_ids": copy.deepcopy(emission["prototype"]["damage_type_ids"]), "t": 0,
            "character": actor, "skill": emission["prototype"]["skill_label"],
            "hit_name": emission["prototype"]["hit_name"],
            "description": emission["prototype"]["description"],
            "damage_system": emission["prototype"]["damage_system"],
            "scaling_stat": emission["prototype"]["scaling_stat"],
            "count": emission["prototype"]["count"]} for emission in emissions]
        fragments = {"action_event_profiles": [action_profile],
            "action_event_bindings": [{"ability_id": ability_id, "profile_id": prefix}],
            "attack_metadata_sources": [], "periodic_attack_sources": [periodic_source],
            "prototype_hits": prototypes, "prototype_requirements": requirements,
            "source_expectations": [{"component_id": emission["component_id"],
                "candidate_id": emission["attack_source"]["candidate_id"],
                "source_fingerprint": emission["attack_source"]["source_fingerprint"],
                "origin_reference_id": emission["attack_source"]["origin_reference_id"]}
                for emission in emissions]}
        target = "calibrated_finite_application_v1"
    else:
        channels, mappings = [], []
        for emission in emissions:
            component, ids = emission["component_id"], _ids(prefix, emission["component_id"])
            reviewed_schedule = ((periodic_channels.get(component) or {}).get("schedule")
                                 if periodic_topology else None)
            if reviewed_schedule and reviewed_schedule["kind"] == "explicit_offsets":
                schedule = {"kind": "explicit_offsets",
                    "offsets_seconds": copy.deepcopy(reviewed_schedule["offsets_seconds"]),
                    "precision_seconds": emission["schedule"]["precision_seconds"],
                    "status": "confirmed",
                    "source": "gameplay_action_manifest:" +
                        resolution["coverage_manifest"]["manifest_id"]}
            else:
                schedule = ({"kind": "single", "offset_seconds": emission["schedule"]["offset_seconds"],
                    "precision_seconds": emission["schedule"]["precision_seconds"], "status": "confirmed",
                    "source": "action_dispatch_profile:" + profile["profile_id"]}
                    if emission["kind"] == "fixed" else
                    {"kind": "periodic", "first_offset_seconds": emission["schedule"]["first_offset_seconds"],
                     "count": emission["schedule"]["count"], "precision_seconds": emission["schedule"]["precision_seconds"],
                     "status": "confirmed", "source": "action_dispatch_profile:" + profile["profile_id"]})
                if emission["kind"] == "periodic" and "derive_count" in emission["schedule"]:
                    schedule["derive_count"] = emission["schedule"]["derive_count"]
            channels.append({"channel_id": component, "prototype_hit_id": ids["hit"],
                "application_profile_id": ids["application"], "schedule": schedule,
                "default_landed": landed[component]["landed"], "self_sampling": emission["self_sampling"]})
            if emission["application"].get("cadence") is not None:
                channels[-1]["application_cadence"] = copy.deepcopy(emission["application"]["cadence"]) | {
                    "status": "confirmed", "source": "action_dispatch_profile:" + profile["profile_id"]}
            mapping = {"channel_id": component, "candidate_id": emission["attack_source"]["candidate_id"],
                       "application_mode": emission["application"]["mode"]}
            if emission["application"]["counter_tag"] is not None:
                mapping["counter_tag"] = emission["application"]["counter_tag"]
            if emission["application"]["verification"] is not None:
                mapping["verification"] = copy.deepcopy(emission["application"]["verification"])
            if component in application_adapters:
                mapping["application_adapter"] = copy.deepcopy(application_adapters[component])
                mapping["application_rule_audit"] = copy.deepcopy(rule_audits[component])
            mappings.append(mapping)
            requirements.append({"id": ids["hit"], "component_id": component, "t": 0,
                "owned_fields": ["element", "multiplier"], "application_profile_id": ids["application"],
                "expected_interval_seconds": emission["schedule"].get("interval_seconds")})
        native_lifecycle = {key: copy.deepcopy(lifecycle[key]) for key in (
                "establishment_offset_seconds", "instance_end_offset_seconds", "precision_seconds",
                "owner_and_target_survive", "no_interrupt", "no_refresh_replacement_or_early_despawn")}
        for key in ("replacement_policy", "no_early_despawn"):
            if key in lifecycle:
                native_lifecycle[key] = copy.deepcopy(lifecycle[key])
        action_profile = {"id": prefix, "kind": "calibrated_multi_application_v2", "variant": variant,
            "lifecycle": native_lifecycle
                | {"status": "confirmed", "source": "action_dispatch_profile:" + profile["profile_id"]},
            "channels": channels, "accepted_profile_fingerprint": None}
        selector = profile["selector"]
        periodic_source = {"action_event_profile_id": prefix, "version_line": selector["version_line"],
            "selection": {"character_id": selector["character_id"], "skill_id": selector["skill_id"],
                "skill_depot_id": selector["skill_depot_id"], "constellation": selector["constellation"],
                "talent_level": selector["effective_level"]},
            "origin_reference_id": emissions[0]["attack_source"]["origin_reference_id"], "channels": mappings,
            "purpose": "calibrated_periodic_attack", "status": "confirmed",
            "source": "action_dispatch_profile:" + profile["profile_id"]}
        prototypes = [{"id": _ids(prefix, emission["component_id"])["hit"],
            "damage_type_ids": copy.deepcopy(emission["prototype"]["damage_type_ids"]), "t": 0,
            "character": actor, "skill": emission["prototype"]["skill_label"],
            "hit_name": emission["prototype"]["hit_name"], "description": emission["prototype"]["description"],
            "damage_system": emission["prototype"]["damage_system"],
            "scaling_stat": emission["prototype"]["scaling_stat"], "count": emission["prototype"]["count"]}
            for emission in emissions]
        source_expectations = []
        for emission in emissions:
            expectation = {"component_id": emission["component_id"],
                "candidate_id": emission["attack_source"]["candidate_id"],
                "source_fingerprint": emission["attack_source"]["source_fingerprint"],
                "origin_reference_id": emission["attack_source"]["origin_reference_id"]}
            reviewed_schedule = ((periodic_channels.get(emission["component_id"]) or {}).get(
                "schedule") if periodic_topology else None)
            # Explicit reviewed offsets are the schedule authority.  Keep the
            # database timer as provenance without requiring it to equal a
            # deliberately absent profile interval.
            if not reviewed_schedule or reviewed_schedule["kind"] != "explicit_offsets":
                expectation["interval_seconds"] = emission["schedule"].get("interval_seconds")
            source_expectations.append(expectation)
        fragments = {"action_event_profiles": [action_profile],
            "action_event_bindings": [{"ability_id": ability_id, "profile_id": prefix}],
            "attack_metadata_sources": [], "periodic_attack_sources": [periodic_source],
            "prototype_hits": prototypes, "prototype_requirements": requirements,
            "source_expectations": source_expectations}
        target = "calibrated_multi_application_v2"
    if result["issues"]:
        result["content_fingerprint"] = fingerprint(result)
        return result
    result.update(status="ready", target_contract=target, profile_id=prefix, fragments=fragments,
                  source_profile_fingerprint=reviewed_profile_fingerprint)
    result["content_fingerprint"] = fingerprint(result)
    return result
