"""Fresh metadata fills a private work plan; EC09 owns the only acceptance."""
from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import dataclass, field

from genshin_app.task_capability_producer import emit_task_missing
from genshin_db.equipment import EquipmentError

from .application_evidence import research_report_template, verification_guidance
from .attack_metadata_schema import (
    COMPILER,
    MAX_RECEIPT_BYTES,
    attack_metadata_capabilities,
    has_verified_application_evidence,
)
from .files import fingerprint
from .schema import PlanError

__all__ = ["PreparedAttackMetadata", "attack_metadata_capabilities", "prepare_attack_metadata"]


@dataclass
class PreparedAttackMetadata:
    """Server-local objects only; never include these caches in JSON receipts."""

    records: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    parameter_contexts: dict = field(default_factory=dict)
    attenuation_profiles: dict = field(default_factory=dict)
    application_identities: dict = field(default_factory=dict)


class _Pending(ValueError):
    pass


# A selected attack node may still be useful when its application mode proves
# that a subset of the elemental fields is irrelevant.  These are deliberately
# narrow, source-level diagnostics: structural, ownership, parameter and
# element failures remain blocking in every mode.
_OPTIONAL_CANDIDATE_ISSUES = {
    "sequence": set(),
    "no_icd": {"attack_attenuation_pending", "attack_attenuation_unavailable"},
    "no_application": {"attack_gauge_unknown_or_unsupported", "attack_attenuation_pending",
                       "attack_attenuation_unavailable", "attack_attenuation_not_admitted"},
}


def _counter_tag(binding):
    return binding.get("counter_tag") or binding.get("shared_group")


def _adapter_override_ready(binding, mode):
    audit = binding.get("application_adapter") or {}
    declaration = audit.get("declaration") or {}
    return (declaration.get("mode") == mode
            and mode not in ("inherit", "pending")
            and (declaration.get("verification") or {}).get("component_scope")
            == audit.get("component_id")
            and has_verified_application_evidence(
                declaration, mode, require_binding_status=True))


def _issue(record, missing, code, message):
    issue = {"code": code, "path": "attack_metadata_sources." + record["action_event_profile_id"], "message": message[:1024]}
    record["issues"].append(issue)
    receipt_limit = code == "attack_metadata_receipt_limit"
    emit_task_missing(
        missing, code, issue["path"], issue["message"],
        "review_attack_metadata",
        consumer="attack_metadata:" + record["action_event_profile_id"],
        category=("engine_capability_missing" if receipt_limit
                  else "knowledge_missing"),
        impact="critical" if receipt_limit else "high",
        blocking_scope=("engine_capability" if receipt_limit
                        else "agent_materialization"),
        retryable=not receipt_limit,
        repair_hint=(
            "缩小当前元信息批次或扩展受审回执承载能力"
            if receipt_limit else
            "核对这一具体攻击组件的参数、元素、附着模式、ICD身份和结构化证据"
        ),
    )


def _verification_task(binding, profile, hit, projection=None, *, conflicts=()):
    projection = projection or {}
    selection = binding["selection"]
    identity = projection.get("application_identity") or {}
    raw_mode = binding.get("application_mode")
    mode = "pending" if raw_mode in (None, "unknown") else raw_mode
    fields = {
        "element": projection.get("element") or {"status": "pending", "source": None},
        "gauge": projection.get("gauge") or {"status": "pending", "source": None},
        "rule_group": projection.get("attenuation") or {"status": "pending", "source": None},
        "counter_tag": identity or {"status": "pending", "source": None},
    }
    required = {"element"}
    if mode in ("sequence", "no_icd"):
        required.add("gauge")
    if mode == "sequence":
        required.update(("rule_group", "counter_tag"))
    known, missing_fields = {}, []
    for name, value in fields.items():
        if mode != "pending" and name not in required:
            known[name] = {"status": "not_applicable", "value": None, "source": None}
            continue
        status = value.get("status")
        known[name] = {"status": status, "value": value.get("value", value.get("group", value.get("tag"))),
                       "source": copy.deepcopy(value.get("source"))}
        if mode != "pending" and name in required and status not in ("ready", "observed"):
            missing_fields.append(name)
    if mode == "pending":
        missing_fields.append("application_mode")
    elif not (mode == "sequence" and identity.get("status") == "ready") and not has_verified_application_evidence(
            binding, mode):
        missing_fields.append("structured_verification")
    guidance = verification_guidance(mode, missing_fields, component="该角色这一具体攻击分量")
    pointers = sorted({(item.get("source") or {}).get("source_path", "") +
                       (item.get("source") or {}).get("json_pointer", "")
                       for item in fields.values() if isinstance(item.get("source"), dict)})
    return {
        "attack_identity": {"actor": hit.get("character"), "character_id": selection.get("character_id"),
            "slot": selection.get("slot"),
            "skill_id": selection.get("skill_id"), "candidate_id": binding.get("candidate_id"),
            "component_profile_id": profile["id"], "prototype_hit_id": profile["prototype_hit_id"],
            "constellation": selection.get("constellation"), "version_line": binding["version_line"]},
        "known_fields": known, "missing_fields": sorted(set(missing_fields)),
        "conflicts": list(copy.deepcopy(conflicts)),
        "local_checks": [{"kind": "runtime_attack_metadata", "candidate_id": binding.get("candidate_id")},
                         *({"kind": "raw_pointer", "reference": pointer} for pointer in pointers)],
        "targeted_research_questions": guidance["targeted_research_questions"],
        "keywords": [str(v) for v in (hit.get("character"), selection.get("slot"), selection.get("skill_id"),
                                       binding.get("candidate_id"), "attenuationTag", "attenuationGroup") if v is not None],
        "acceptance_required": guidance["acceptance_required"],
        "database_patch_targets": ["runtime_attack_metadata.application_identity",
                                   "attack_component.application_mode", "attack_component.application_profile"],
        "research_report": copy.deepcopy(binding.get("research_report")),
        "research_report_template": research_report_template(),
    }


def _record(binding, profile, hit):
    raw_mode = binding.get("application_mode")
    mode = "pending" if raw_mode in (None, "unknown") else raw_mode
    record = {"action_event_profile_id": profile["id"], "prototype_hit_id": profile["prototype_hit_id"],
        "application_profile_id": profile["application_profile_id"], "actor": hit["character"],
        "compiler": COMPILER, "status": "pending", "whole_source_status": "partial",
        "gameplay_interpretation": "declared_not_verified", "version_line": binding["version_line"],
        "selection": copy.deepcopy(binding["selection"]), "candidate_id": binding["candidate_id"],
        "snapshot": None, "source_fingerprint": None, "semantic_dependency": None,
        "metadata_dependency_fingerprint": None, "projection": None, "compiled_fields": {},
        "application_mode": {"status": "pending", "value": mode, "source": binding["source"],
            **({"deprecated_alias": "unknown" if raw_mode == "unknown" else "missing",
                "canonical": "pending"} if raw_mode in (None, "unknown") else {})},
        "application_identity": {"status": "pending", "tag": _counter_tag(binding),
            "evidence": None, "source": None,
            "next_action": "核实该角色这一具体攻击段的attenuationTag；不得按动作类别猜测"},
        "verification_task": _verification_task(binding, profile, hit),
        "issues": []}
    if "application_adapter" in binding:
        record["application_adapter"] = copy.deepcopy(binding["application_adapter"])
        record["application_rule_audit"] = copy.deepcopy(binding["application_rule_audit"])
    return record


def prepare_attack_metadata(plan, template, db_path, missing):
    """Atomically materialize all selected sources in composer-owned copies.

    A ready record proves fresh numeric materialization, not user's acceptance
    or native action dispatch. The profile compiler verifies acceptance later.
    """
    result = PreparedAttackMetadata()
    bindings = plan.get("attack_metadata_sources", [])
    if not bindings:
        return result
    from genshin_db.runtime_action_parameter import project_action_parameter
    from genshin_db.runtime_attack_metadata import get_attack_metadata_context

    profiles = {p["id"]: p for p in plan["action_event_profiles"]}
    hits = {h["id"]: h for h in plan["manual_template"]["hits"]}
    working_hits = {h["id"]: h for h in template["hits"]}
    model = plan.get("mechanics_model") or {}
    elemental = model.get("elemental_model")
    cache, snapshots, stages = {}, {}, []
    for binding in bindings:
        raw_application_mode = binding.get("application_mode")
        application_mode = "pending" if raw_application_mode in (None, "unknown") else raw_application_mode
        pid = binding["action_event_profile_id"]
        profile = profiles[pid]
        hid, app_id = profile["prototype_hit_id"], profile["application_profile_id"]
        hit = hits[hid]
        record = _record(binding, profile, hit)
        result.records.append(record)
        try:
            if not isinstance(elemental, dict):
                raise _Pending("元信息装配需要已声明元素模型；不自动创建目标、接触或参考接受")
            if hit.get("damage_system") != "classic" or hit.get("scaling_stat") != "atk":
                raise _Pending("首片只装配显式声明的classic单ATK原型")
            key = fingerprint({k: binding[k] for k in ("version_line", "selection", "candidate_id")})
            if key not in cache:
                cache[key] = get_attack_metadata_context(db_path, candidate_id=binding["candidate_id"],
                    version_line=binding["version_line"], **binding["selection"])
            context = cache[key]
            query = context.public_projection
            record["query"] = copy.deepcopy(query)
            record["snapshot"] = copy.deepcopy(query.get("snapshot"))
            record["source_fingerprint"] = query.get("source_fingerprint")
            candidate = query.get("selected_candidate")
            if binding["candidate_id"] is None or candidate is None:
                raise _Pending("请明确选择当前技能的精确攻击候选；系统不会默认首项")
            projection = candidate.get("projection") or {}
            record["projection"] = copy.deepcopy(projection)
            record["verification_task"] = _verification_task(binding, profile, hit, projection)
            record["verification_task"]["attack_identity"].update(
                ability_name=candidate.get("ability_name"),
                parent_pointer=(candidate.get("parent") or {}).get("json_pointer"))
            if candidate.get("candidate_id") != binding["candidate_id"]:
                raise _Pending("新鲜投影的候选身份与本次选择不符")
            if application_mode == "pending":
                raise _Pending("该具体攻击组件是否附着及其ICD模式尚未核实；不得按动作类别默认")
            required_components = ["parameter", "element"]
            if application_mode in ("sequence", "no_icd"):
                required_components.append("gauge")
            if application_mode == "sequence":
                required_components.append("attenuation")
            if any((projection.get(k) or {}).get("status") != "ready" for k in required_components):
                raise _Pending("选定候选缺少当前附着模式所需的" + "/".join(required_components) + "来源")
            if any(issue.get("blocks_selected", True) for issue in query.get("issues", [])):
                raise _Pending("选定候选的父级来源或选择仍有阻断诊断")
            optional_issues = _OPTIONAL_CANDIDATE_ISSUES[application_mode]
            for optional_issue in candidate.get("issues", []):
                optional_code = optional_issue.get("code")
                if optional_code not in optional_issues:
                    continue
                emit_task_missing(
                    missing, optional_code or "attack_metadata_optional_source",
                    "attack_metadata_sources." + record["action_event_profile_id"],
                    str(optional_issue.get("message") or optional_code)[:1024],
                    "review_attack_metadata",
                    consumer=("attack_metadata:" + record["action_event_profile_id"]
                              + ":optional:" + str(optional_code)),
                    category="knowledge_missing", required=False,
                    consumed=False, impact="informational",
                    blocking_scope="task_capability", retryable=True,
                    repair_hint=(
                        "该字段不被当前附着模式消费；若未来切换附着模式再补齐来源"
                    ),
                )
            blocking_issues = [issue for issue in candidate.get("issues", [])
                               if issue.get("code") not in optional_issues]
            if blocking_issues:
                raise _Pending("选定候选尚未通过当前附着模式所需的结构与来源守卫")
            dependency = query.get("semantic_dependency")
            if not isinstance(dependency, dict) or query.get("source_fingerprint") != fingerprint(dependency):
                raise _Pending("新鲜元信息语义依赖指纹不一致")
            if (candidate.get("semantic_dependency") != dependency
                    or candidate.get("source_fingerprint") != query["source_fingerprint"]
                    or any((dependency.get("projection") or {}).get(k) != projection[k]
                           for k in ("element", "gauge", "application_identity", "attack_tag"))):
                raise _Pending("选定候选的元素/单位/附着身份投影与其来源确认载荷不一致")
            snapshot = query["snapshot"]
            line = binding["version_line"]
            if snapshot["version_line"] != line or snapshots.setdefault(line, snapshot["snapshot_id"]) != snapshot["snapshot_id"]:
                raise _Pending("同次元信息来源快照不一致")
            parameter = projection["parameter"]
            supplied = project_action_parameter(context.parameter_context, parameter["param_index"])
            selection = supplied["selection"]
            dynamic_application = profile.get("kind") == "calibrated_single_application_v2"
            supported_slots = {"normal", "skill", "burst"} if dynamic_application else {"skill"}
            level = supplied["level"].get("effective_level")
            if (supplied["snapshot"] != snapshot or selection != query["selection"]
                    or selection["character_id"] != binding["selection"]["character_id"]
                    or (not dynamic_application and (selection["constellation"] != 0 or level != 10))
                    or level not in range(1, 16)
                    or not set(selection.get("matching_slots", [])) & supported_slots
                    or supplied["scalar_projection"]["status"] != "ready"
                    or supplied["scalar_projection"]["value"] != parameter["value"]
                    or supplied["source"] != parameter["source"]):
                raise _Pending("候选参数与本次精确角色、等级、命座和动作槽上下文不一致")
            element = projection["element"]["value"]
            allowed_elements = ("pyro", "cryo", "hydro", "anemo") if dynamic_application else ("cryo", "hydro", "anemo")
            if element not in allowed_elements:
                raise _Pending("元素超出当前攻击元信息范围")
            gu = projection["gauge"].get("value")
            if application_mode != "no_application" and (
                    type(gu) is not int or gu not in (1, 2, 4)
                    or projection["gauge"].get("tax_applied") is not False):
                raise _Pending("未扣底税的原始GU超出当前附着模式范围")
            attenuation = projection.get("attenuation") or {}
            rule_audit = binding.get("application_rule_audit")
            group, fresh_attenuation = None, None
            if application_mode == "sequence":
                group = attenuation["group"]
                fresh_attenuation = context.attenuation_profiles.get(group)
                if (fresh_attenuation is None or fresh_attenuation != attenuation["profile"]
                        or fresh_attenuation["snapshot"] != snapshot or fresh_attenuation["execution"]["status"] != "ready"):
                    raise _Pending("候选衰减与本次新鲜应用序列不一致")
            has_icd_overlay = (((binding.get("application_adapter") or {}).get(
                "declaration") or {}).get("icd_rule") or {}).get("mode") == "timer_counter"
            effective_icd_rule = ((rule_audit or {}).get("effective_icd_rule")
                                  if application_mode == "sequence" and has_icd_overlay else None)
            effective_rule_group = (effective_icd_rule["rule_group"]
                                    if effective_icd_rule is not None else group)
            if binding["status"] == "pending":
                raise _Pending("攻击用途及共享ICD身份尚未确认；资料就绪不能晋升声明状态")
            identity = projection.get("application_identity") or {}
            if rule_audit is not None:
                fresh_database_identity = {
                    "status": identity.get("status"), "counter_tag": identity.get("tag")}
                if rule_audit["database_identity"] != fresh_database_identity:
                    raise _Pending("固定攻击附着适配记录的数据库身份与本次新鲜投影不一致")
                database_tag = (identity.get("tag")
                                if identity.get("status") == "ready" else None)
                divergence = database_tag is not None and (
                    application_mode != "sequence" or _counter_tag(binding) != database_tag)
                if rule_audit["identity_divergence"] != divergence:
                    raise _Pending("固定攻击附着适配差异标记与本次新鲜投影不一致")
                record["database_application_identity"] = copy.deepcopy(fresh_database_identity)
                record["application_identity_divergence"] = divergence
            if application_mode == "sequence":
                if identity.get("status") == "ready":
                    if (not isinstance(identity.get("tag"), str) or not identity["tag"].strip()
                            or not isinstance(identity.get("source"), dict)):
                        raise _Pending("数据库返回的attenuationTag证据结构不完整")
                    if (_counter_tag(binding) != identity["tag"]
                            and not _adapter_override_ready(binding, application_mode)):
                        record["verification_task"]["conflicts"].append({
                            "field": "counter_tag", "database": identity["tag"],
                            "declared": _counter_tag(binding),
                            "message": "方案计数标签与数据库attenuationTag不一致"})
                        raise _Pending(
                            "方案计数标签与数据库确认的attenuationTag不一致；attackTag或动作类别不能代替计数标签")
                    if _counter_tag(binding) == identity["tag"]:
                        identity_basis = {"status": "database_confirmed", "tag": identity["tag"],
                            "evidence": identity.get("evidence"),
                            "source": copy.deepcopy(identity["source"]), "next_action": None}
                    else:
                        identity_basis = {"status": "adapter_override",
                            "tag": _counter_tag(binding), "database_tag": identity["tag"],
                            "evidence": "confirmed_component_adapter",
                            "source": copy.deepcopy(binding["application_adapter"]),
                            "next_action": None}
                elif identity.get("status") == "pending":
                    # The numeric projection remains usable when the upstream
                    # node omits attenuationTag, but only an explicit, sourced
                    # plan declaration may supply the counter identity.
                    if not has_verified_application_evidence(binding, application_mode):
                        raise _Pending(
                            "数据库缺少该攻击分量的attenuationTag；自由文本或assumed声明不能升格。"
                            "须提供verification.status=confirmed的结构化逐段证据（定位、版本、日期、组件范围及确认字段）")
                    identity_basis = {"status": "declared", "tag": _counter_tag(binding),
                        "evidence": "structured_plan_verification", "source": copy.deepcopy(binding["verification"]),
                        "declaration_status": binding["status"],
                        "next_action": "可将已确认的具体攻击段证据回填公共数据库；同技能不同段、召唤物、命座追加攻击仍须分别核实"}
                else:
                    raise _Pending("攻击组件的附着计数身份状态无法解释")
            else:
                if not has_verified_application_evidence(binding, application_mode):
                    raise _Pending("无ICD或无附着必须有verification.status=confirmed的结构化逐段正向证据；自由文本不能执行")
                identity_basis = {"status": "not_applicable", "tag": None,
                    "evidence": "application_mode", "source": binding["source"], "next_action": None}
            interpretation = {k: copy.deepcopy(binding[k]) for k in ("purpose", "status", "source")}
            interpretation["application_mode"] = application_mode
            if application_mode == "sequence":
                interpretation["counter_tag"] = _counter_tag(binding)
            if "verification" in binding:
                interpretation["verification"] = copy.deepcopy(binding["verification"])
            if "shared_group" in binding:
                interpretation["legacy_counter_tag_field"] = "shared_group"
            if rule_audit is not None:
                interpretation["application_adapter"] = copy.deepcopy(
                    binding["application_adapter"])
                interpretation["application_rule_audit"] = copy.deepcopy(rule_audit)
                interpretation["database_application_identity"] = copy.deepcopy(
                    record["database_application_identity"])
                interpretation["application_identity_divergence"] = record[
                    "application_identity_divergence"]
            semantics = {"database": copy.deepcopy(dependency), "interpretation": interpretation}
            record.update(semantic_dependency=semantics, metadata_dependency_fingerprint=fingerprint(semantics))
            record["application_identity"] = identity_basis
            record["application_mode"] = {"status": "declared", "value": application_mode,
                "source": binding["source"], "declaration_status": binding["status"],
                **({"deprecated_alias": "unknown" if raw_application_mode == "unknown" else "missing",
                    "canonical": "pending"} if raw_application_mode in (None, "unknown") else {})}
            fields = {"element": element, "param_index": parameter["param_index"],
                "application_mode": application_mode, "application_identity_status": identity_basis["status"]}
            if application_mode != "no_application":
                fields.update(gauge_units=gu, tax_applied=False)
            if application_mode == "sequence":
                fields.update(counter_tag=_counter_tag(binding), rule_group=effective_rule_group)
                if effective_icd_rule is not None:
                    fields["database_rule_group"] = group
            else:
                fields.update(counter_tag=None, rule_group=None)
            record["compiled_fields"] = fields
            parameter_key = fingerprint({"selection": binding["selection"], "version_line": line})
            prior = result.parameter_contexts.setdefault(parameter_key, context.parameter_context)
            if prior != context.parameter_context:
                raise _Pending("同一参数选择的新鲜上下文发生冲突")
            if application_mode == "sequence":
                prior_attenuation = result.attenuation_profiles.setdefault((line, group), fresh_attenuation)
                if prior_attenuation != fresh_attenuation:
                    raise _Pending("同一组的新鲜衰减上下文发生冲突")
            psource = {"hit_id": hid, "version_line": line, "selection": copy.deepcopy(binding["selection"]),
                "param_index": parameter["param_index"], "scaling_stat": "atk", "purpose": binding["purpose"],
                "status": binding["status"], "source": binding["source"],
                "expected_snapshot_id": snapshot["snapshot_id"], "expected_node_sha256": supplied["source"]["node_sha256"]}
            esource = None
            if application_mode == "sequence":
                esource = {"profile_id": app_id, "version_line": line, "rule_group": group,
                    "counter_tag": _counter_tag(binding), "application_mode": "sequence",
                    "source": binding["source"], "expected_snapshot_id": snapshot["snapshot_id"],
                    "expected_node_sha256": fresh_attenuation["source"]["node_sha256"]}
                if effective_icd_rule is not None:
                    esource["application_rule_audit"] = copy.deepcopy(rule_audit)
                application = {"id": app_id, "element": element, "application": "apply", "gauge_units": gu,
                    "icd": {"kind": "unknown"}, "status": binding["status"], "source": binding["source"]}
            elif application_mode == "no_icd":
                application = {"id": app_id, "element": element, "application": "apply", "gauge_units": gu,
                    "icd": {"kind": "none"}, "status": binding["status"], "source": binding["source"]}
            else:
                application = {"id": app_id, "element": element, "application": "none",
                    "status": binding["status"], "source": binding["source"]}
            stages.append((record, app_id, element, psource, esource, application))
        except (EquipmentError, ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
            if isinstance(exc, PlanError):
                raise
            _issue(record, missing, "attack_metadata_pending", str(exc))
        if len(json.dumps(result.records, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()) > MAX_RECEIPT_BYTES:
            for row in result.records:
                for key in ("query", "projection", "semantic_dependency"):
                    row.pop(key, None)
                row.update(status="pending", actor=None, compiled_fields={})
            _issue(record, missing, "attack_metadata_receipt_limit", "攻击元信息回执超过2MiB，已省略完整投影，未交付部分输入；可用独立有界查询查看原文")
            for remaining in bindings[len(result.records):]:
                unused_profile = profiles[remaining["action_event_profile_id"]]
                skipped = _record(remaining, unused_profile, hits[unused_profile["prototype_hit_id"]])
                skipped["actor"] = None
                skipped["issues"].append({"code": "attack_metadata_skipped", "message": "本次元信息回执预算已用尽，未查询或物化此项"})
                result.records.append(skipped)
            result.parameter_contexts.clear()
            result.attenuation_profiles.clear()
            result.application_identities.clear()
            return result
    if len(stages) != len(bindings):
        for row in result.records:
            row["compiled_fields"] = {}
            if not row["issues"]:
                row["issues"].append({"code": "attack_metadata_peer_pending", "message": "其他元信息来源待处理；本次未提交任何派生字段"})
        result.parameter_contexts.clear()
        result.attenuation_profiles.clear()
        result.application_identities.clear()
        return result
    # Commit all source-owned fields together. original_plan remains untouched.
    for record, app_id, element, psource, esource, application in stages:
        hid = record["prototype_hit_id"]
        hits[hid]["element"] = working_hits[hid]["element"] = element
        plan.setdefault("hit_parameter_sources", []).append(psource)
        if esource is not None:
            plan.setdefault("elemental_sources", []).append(esource)
        elemental["profiles"].append(application)
        if esource is not None:
            result.application_identities[app_id] = copy.deepcopy(record["application_identity"])
        record["status"] = "ready"
        result.sources.append({"kind": "attack_metadata", "action_event_profile_id": record["action_event_profile_id"],
            "source_fingerprint": record["source_fingerprint"], "metadata_dependency_fingerprint": record["metadata_dependency_fingerprint"]})
    return result
