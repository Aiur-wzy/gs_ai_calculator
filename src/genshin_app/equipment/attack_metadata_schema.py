"""Native ownership declarations for fresh attack metadata; no engine defaults."""
from __future__ import annotations

import copy
import json

from .action_event_schema import (
    APPLICATION_KIND,
    ID,
    KIND,
    SOURCE,
    STATUS,
    _check,
    _object,
)
from .application_evidence import (
    APPLICATION_ADAPTER,
    APPLICATION_ADAPTER_AUDIT,
    APPLICATION_MODES,
    APPLICATION_RULE_AUDIT,
    RESEARCH_REPORT,
    VERIFICATION,
    VERIFICATION_FIELDS,  # noqa: F401 - compatibility re-export
    has_verified_application_evidence,  # noqa: F401 - compatibility re-export
    research_report_template,
)

COMPILER = "database-attack-metadata-v1"
MAX_INPUT_BYTES = 128 * 1024
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
SELECTION = _object({"character_id": {"type": "integer", "minimum": 1, "maximum": 2**53-1},
    "slot": {"enum": ["normal", "skill", "burst"]}, "skill_id": {"type": "integer", "minimum": 1, "maximum": 2**53-1},
    "skill_depot_id": {"type": "integer", "minimum": 1, "maximum": 2**53-1},
    "constellation": {"type": "integer", "minimum": 0, "maximum": 6},
    "talent_level": {"type": "integer", "minimum": 1, "maximum": 15}}, ["character_id", "constellation"])
SOURCE_SCHEMA = _object({"action_event_profile_id": ID, "version_line": {"enum": ["release", "beta"]},
    "selection": SELECTION, "candidate_id": {"anyOf": [ID, {"type": "null"}]},
    "counter_tag": ID, "shared_group": ID, "verification": VERIFICATION,
    "application_adapter": APPLICATION_ADAPTER_AUDIT,
    "application_rule_audit": APPLICATION_RULE_AUDIT,
    "research_report": RESEARCH_REPORT,
    "application_mode": APPLICATION_MODES,
    "purpose": {"enum": ["single_stat_damage_multiplier"]}, "status": STATUS, "source": SOURCE},
    ["action_event_profile_id", "version_line", "selection", "candidate_id", "purpose", "status", "source"])
SOURCES_SCHEMA = {"type": "array", "maxItems": 32, "items": SOURCE_SCHEMA}


def attack_metadata_capabilities():
    return {"compiler": COMPILER, "schema": copy.deepcopy(SOURCES_SCHEMA), "max_sources": 32,
        "max_input_bytes": MAX_INPUT_BYTES, "max_receipt_bytes": MAX_RECEIPT_BYTES,
        "confirmation": "action_event_profiles.accepted_profile_fingerprint",
        "whole_source_status": "partial", "dispatch_mode": "calibrated", "skill_coverage": "partial",
        "native_ownership": "省略原型element/multiplier；独占application整项不写原生profiles；不重复手工标准来源",
        "scope": "classic单ATK与精确父链投影；旧冻结接触v1仍限C0有效10级E，新普通附着v2采用精确命座、1–15级有效等级及唯一普攻/战技/爆发槽；时序/变体仍显式声明；计数标签优先采用上游attenuationTag，缺失时要求逐攻击组件提供证据",
        "application_modes": {"sequence": "按counter_tag与rule_group执行有限序列",
            "no_icd": "每次命中均允许附着，编译为icd.kind=none",
            "no_application": "该攻击组件不尝试施加元素；与缺失档案不同",
            "pending": "尚未核实，编译为unknown占位并阻止确定性装配"},
        "mode_dependencies": {"sequence": ["parameter", "element", "gauge", "attenuation", "counter_tag"],
            "no_icd": ["parameter", "element", "gauge"],
            "no_application": ["parameter", "element"],
            "pending": []},
        "extractor_limit": "v1候选仍须来自已审攻击父结构；若原始组件整体形状未准入，即使no_application不需要GU/规则也保持pending并报告结构原因",
        "deprecated_application_mode_aliases": {"unknown": "pending"},
        "application_identity": {"counter_tag": "附着计数标签：attenuationTag或有来源的方案声明",
            "rule_group": "附着规则组：attenuationGroup对应的计时与命中序列模板",
            "never_infer_from": ["attackTag", "normal/skill/burst动作类别", "同技能其他攻击段"],
            "component_boundary": "同技能不同段、召唤物、命座追加攻击分别核实",
            "override_policy": "数据库缺attenuationTag时，只有verification.status=confirmed且包含定位、版本、日期、组件范围及confirmed_fields的逐段声明可执行；整体来源的assumed/confirmed与逐分量机制证据分开，自由文本保持pending"},
        "application_adapter": {
            "contract_id": "application-adapter-v1",
            "declaration_schema": copy.deepcopy(APPLICATION_ADAPTER),
            "scope": "动作派发fixed发射的精确组件",
            "projection": "只修改最终mode/counter_tag；元素、GU与数据库rule_group不可覆盖",
            "fixed_attempt_gate": ["inherit", "none"],
            "receipt": "保留base/effective规则、数据库身份、差异标记与adapter指纹",
        },
        "verification_task": ["attack_identity", "known_fields", "missing_fields", "conflicts", "local_checks",
                              "targeted_research_questions", "keywords", "acceptance_required", "database_patch_targets",
                              "research_report", "research_report_template"],
        "research_report_contract": {"saved_field": "attack_metadata_sources[].research_report",
            "granularity": "one concrete attack component", "execution_authority": "none",
            "template": research_report_template()},
        "labels": {"attack_metadata_sources": "攻击元信息来源", "candidate_id": "精确攻击候选",
            "counter_tag": "附着计数标签（ICD Tag）", "shared_group": "旧版计数标签字段（仅兼容）",
            "verification": "逐攻击段结构化核实证据",
            "research_report": "Agent逐分量查证、来源取舍与公共数据回填记录（无执行权）",
            "source": "用途与计数标签依据"}}


def validate_attack_metadata_sources(plan, *, additional_owned_hits=()):
    from .schema import PlanError

    rows = plan.get("attack_metadata_sources", [])
    if "attack_metadata_sources" in plan:
        if plan.get("plan_version") != 3:
            raise PlanError("攻击元信息来源仅适用于原生v3", "attack_metadata_schema")
        try:
            if len(json.dumps(rows, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_INPUT_BYTES:
                raise ValueError("攻击元信息来源超过128KiB")
            errors = _check(rows, SOURCES_SCHEMA, "attack_metadata_sources")
        except (ValueError, TypeError, OverflowError, RecursionError) as exc:
            raise PlanError(str(exc), "attack_metadata_schema") from exc
        if errors:
            raise PlanError("攻击元信息来源结构不合法", "attack_metadata_schema", errors[:128])
    profiles = {p["id"]: p for p in plan.get("action_event_profiles", [])}
    bound_abilities = {row["profile_id"]: row["ability_id"]
                       for row in plan.get("action_event_bindings", [])}
    dispatch_declarations = {row["ability_id"]: row
                             for row in plan.get("action_dispatch_sources", [])}
    hits = {h["id"]: h for h in plan["manual_template"]["hits"]}
    model = plan.get("mechanics_model") or {}
    elemental = model.get("elemental_model") or {}
    owned_profiles, owned_hits, owned_apps = set(), set(), set()
    for row in rows:
        selection = row["selection"]
        if ("slot" in selection) == ("skill_id" in selection):
            raise PlanError("攻击元信息选择须提供slot或skill_id，且只能一种", "attack_metadata_schema")
        raw_mode = row.get("application_mode")
        mode = "pending" if raw_mode in (None, "unknown") else raw_mode
        if row.get("counter_tag") and row.get("shared_group"):
            raise PlanError("计数标签不得同时使用counter_tag与旧shared_group字段", "attack_metadata_schema")
        counter_tag = row.get("counter_tag") or row.get("shared_group")
        if mode == "sequence" and not (counter_tag or "").strip():
            raise PlanError("序列附着模式必须声明附着计数标签（ICD Tag）", "attack_metadata_schema")
        if mode != "sequence" and counter_tag and raw_mode is not None:
            raise PlanError("非序列附着模式不得再声明计数标签", "attack_metadata_schema")
        verification = row.get("verification")
        if verification and len(set(verification["confirmed_fields"])) != len(verification["confirmed_fields"]):
            raise PlanError("逐段证据confirmed_fields不得重复", "attack_metadata_schema")
        if raw_mode is not None and mode == "pending" and row["status"] != "pending":
            raise PlanError("待核实附着模式必须保持pending", "attack_metadata_schema")
        pid = row["action_event_profile_id"]
        profile = profiles.get(pid)
        if not profile or profile.get("kind") not in (KIND, APPLICATION_KIND) or profile.get("prototype_hit_id") not in hits:
            raise PlanError("攻击元信息须关联已有档案及命中原型", "reference")
        hid, app = profile["prototype_hit_id"], profile["application_profile_id"]
        adapter = row.get("application_adapter")
        rule_audit = row.get("application_rule_audit")
        if (adapter is None) != (rule_audit is None):
            raise PlanError("固定攻击附着适配必须同时携带完整规则审计", "attack_metadata_schema")
        if adapter is not None:
            from .files import fingerprint

            declaration = adapter["declaration"]
            dispatch = dispatch_declarations.get(bound_abilities.get(pid)) or {}
            declared = next((item.get("application_adapter")
                             for item in dispatch.get("scenario_calibrations", [])
                             if item.get("component_id") == adapter["component_id"]), None)
            gate = declaration["attempt_gate"]
            icd_rule = declaration.get("icd_rule") or {"mode": "inherit"}
            changes_rule = (declaration["mode"] not in ("inherit", "pending")
                            or gate["mode"] != "inherit"
                            or icd_rule["mode"] != "inherit")
            if (adapter["accepted_base_fingerprint"] != adapter["base_application_fingerprint"]
                    or adapter["adapter_fingerprint"] != fingerprint(declaration)
                    or declaration["accepted_base_fingerprint"]
                    != adapter["base_application_fingerprint"]
                    or declaration["status"] != "confirmed"
                    or declared != declaration
                    or changes_rule and (declaration.get("verification") or {}).get(
                        "component_scope") != adapter["component_id"]):
                raise PlanError("固定攻击附着适配审计与组件、基底或公开声明不一致",
                                "attack_metadata_schema")
            if gate["mode"] == "every_n_landed_hits":
                raise PlanError("固定攻击不支持代内命中节律门控", "attack_metadata_schema")
            expected_effective = {"mode": mode, "counter_tag": counter_tag,
                "attempt_gate": {"mode": "none", "period_hits": None,
                                 "first_attempt_ordinal": None}}
            if (adapter["effective_rule"] != expected_effective
                    or declaration["mode"] == "inherit" and declaration["counter_tag"] is not None):
                raise PlanError("固定攻击附着适配最终规则与来源投影不一致", "attack_metadata_schema")
            for rule in (adapter["base_rule"], adapter["effective_rule"]):
                gate_snapshot = rule["attempt_gate"]
                if ((rule["mode"] == "sequence") != (rule["counter_tag"] is not None)
                        or gate_snapshot != {"mode": "none", "period_hits": None,
                                             "first_attempt_ordinal": None}):
                    raise PlanError("固定攻击附着适配规则快照不自洽", "attack_metadata_schema")
            identity = rule_audit["database_identity"]
            database_tag = identity["counter_tag"] if identity["status"] == "ready" else None
            divergence = database_tag is not None and (
                mode != "sequence" or counter_tag != database_tag)
            if (rule_audit["component_id"] != adapter["component_id"]
                    or rule_audit["emission_kind"] != "fixed"
                    or rule_audit["effective_rule"] != expected_effective
                    or rule_audit["base_application_fingerprint"]
                    != adapter["base_application_fingerprint"]
                    or rule_audit["base_rule"] != adapter["base_rule"]
                    or rule_audit["adapter_fingerprint"] != adapter["adapter_fingerprint"]
                    or rule_audit["identity_divergence"] != divergence):
                raise PlanError("固定攻击附着规则审计与基线、最终规则或数据库身份不一致",
                                "attack_metadata_schema")
            if icd_rule["mode"] == "timer_counter":
                base_rule = rule_audit.get("base_icd_rule")
                effective_icd = rule_audit.get("effective_icd_rule")
                if (not isinstance(base_rule, dict)
                        or rule_audit.get("base_icd_rule_fingerprint") != fingerprint(base_rule)
                        or icd_rule["accepted_base_rule_fingerprint"]
                        != rule_audit["base_icd_rule_fingerprint"]
                        or effective_icd is None
                        or effective_icd["kind"] != "timer_counter"
                        or effective_icd["hit_interval"] != icd_rule["hit_interval"]
                        or effective_icd["first_hit"] != 1
                        or effective_icd["sequence"] is not None
                        or (icd_rule["reset_seconds"] is not None
                            and effective_icd["reset_seconds"] != icd_rule["reset_seconds"])):
                    raise PlanError("固定攻击局部ICD规则审计与声明或数据库基线不一致",
                                    "attack_metadata_schema")
                expected_reset = (base_rule["reset_seconds"]
                                  if icd_rule["reset_seconds"] is None
                                  else icd_rule["reset_seconds"])
                expected_group = "adapter.icd." + fingerprint({
                    "base_rule_fingerprint": rule_audit["base_icd_rule_fingerprint"],
                    "kind": "timer_counter", "reset_seconds": expected_reset,
                    "hit_interval": icd_rule["hit_interval"],
                })[:48]
                if (effective_icd["reset_seconds"] != expected_reset
                        or effective_icd["rule_group"] != expected_group):
                    raise PlanError("固定攻击局部ICD派生身份与执行语义不一致",
                                    "attack_metadata_schema")
        if pid in owned_profiles or hid in owned_hits or app in owned_apps:
            raise PlanError("攻击元信息的档案/原型/附着档案须唯一所有者", "duplicate_delivery")
        owned_profiles.add(pid)
        owned_hits.add(hid)
        owned_apps.add(app)
        if set(hits[hid]) & {"element", "multiplier"}:
            raise PlanError("攻击元信息接管的原型必须省略element/multiplier，不接受相同值或null双交付", "duplicate_delivery")
        if any(s["hit_id"] == hid for s in plan.get("hit_parameter_sources", [])):
            raise PlanError("同原型不能同时交付攻击元信息和手工倍率来源", "duplicate_delivery")
        if any(p["id"] == app for p in elemental.get("profiles", [])) or any(s["profile_id"] == app for s in plan.get("elemental_sources", [])):
            raise PlanError("攻击元信息独占的附着档案必须从原生profiles及elemental_sources省略", "duplicate_delivery")
        if any(p["id"] != pid and (p.get("prototype_hit_id") == hid or p.get("application_profile_id") == app) for p in profiles.values()):
            raise PlanError("其他动作档案不得复用攻击元信息拥有的原型/附着档案", "duplicate_delivery")
        for name in ("hit_events", "hit_patterns", "application_events"):
            for descriptor in model.get(name, []):
                if (descriptor.get("application") or {}).get("profile_id") == app:
                    raise PlanError("手工命中或附着不得借用源生成的附着档案", "duplicate_delivery")
    for hit in hits.values():
        if "element" not in hit and hit["id"] not in owned_hits and hit["id"] not in additional_owned_hits:
            raise PlanError("未被攻击元信息唯一接管的命中仍须明确element", "attack_metadata_ownership", {"hit_id": hit["id"]})
