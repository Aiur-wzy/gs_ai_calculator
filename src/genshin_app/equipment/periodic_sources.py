"""Fresh periodic sources materialize kind-neutral damage/application inputs.

Freeze contact belongs to the legacy dispatch profile and is deliberately not
invented by this database projection layer.
"""
from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import dataclass, field

from genshin_app.task_capability_producer import emit_task_missing
from genshin_db.equipment import EquipmentError

from .application_evidence import (
    has_verified_application_evidence,
    research_report_template,
    verification_guidance,
)
from .files import fingerprint
from .periodic_schema import (
    APPLICATION_KIND,
    COMPILER,
    MAX_RECEIPT_BYTES,
    periodic_attack_capabilities,
)
from .schema import PlanError

__all__ = ["periodic_attack_capabilities", "prepare_periodic_sources"]

_OPTIONAL_CANDIDATE_ISSUES = {
    "sequence": set(),
    "no_icd": {"periodic_attenuation_pending", "periodic_attenuation_unavailable",
               "periodic_attenuation_not_admitted", "periodic_attenuation_tag_invalid"},
    "no_application": {"periodic_gauge_unsupported", "periodic_attenuation_pending",
                       "periodic_attenuation_unavailable", "periodic_attenuation_not_admitted",
                       "periodic_attenuation_tag_invalid"},
}


@dataclass
class PreparedPeriodicSources:
    records: list = field(default_factory=list)
    sources: list = field(default_factory=list)
    parameter_contexts: dict = field(default_factory=dict)
    attenuation_profiles: dict = field(default_factory=dict)
    application_identities: dict = field(default_factory=dict)


def _counter_tag(mapping):
    return mapping.get("counter_tag") or mapping.get("shared_group")


def _adapter_override_ready(mapping, mode):
    audit = mapping.get("application_adapter") or {}
    declaration = audit.get("declaration") or {}
    verification = declaration.get("verification") or {}
    return (declaration.get("mode") not in (None, "inherit", "pending")
            and audit.get("component_id") == mapping.get("channel_id")
            and verification.get("component_scope") == mapping.get("channel_id")
            and has_verified_application_evidence(
                declaration, mode, require_binding_status=True))


def _verification_task(binding, mapping, candidate=None, *, channel=None, hit=None):
    candidate = candidate or {}
    channel = channel or {}
    hit = hit or {}
    projection = candidate.get("projection") or {}
    identity = projection.get("application_identity") or {}
    raw_mode = mapping.get("application_mode")
    mode = "pending" if raw_mode in (None, "unknown") else raw_mode
    missing_fields = []
    if mode == "pending":
        missing_fields.append("application_mode")
    if mode == "sequence" and not _counter_tag(mapping):
        missing_fields.append("counter_tag")
    if (mode != "pending" and not (mode == "sequence" and identity.get("status") == "ready")
            and not has_verified_application_evidence(mapping, mode)):
        missing_fields.append("structured_verification")
    if mode in ("no_icd", "no_application"):
        counter_field = {"status": "not_applicable", "value": None}
        rule_field = {"status": "not_applicable", "value": None}
        database_identity = {"status": "not_applicable", "reason": "application_mode=" + mode}
    else:
        counter_field = ({"status": "database_confirmed", "value": identity.get("tag"),
                          "source": copy.deepcopy(identity.get("source"))}
                         if mode == "sequence" and identity.get("status") == "ready" else
                         {"status": "undecided" if mode == "pending" else "declared",
                          "value": _counter_tag(mapping)})
        attenuation = projection.get("attenuation") or {}
        rule_field = ({"status": "database_confirmed", "value": attenuation.get("group"),
                       "source": copy.deepcopy(attenuation.get("source"))}
                      if mode == "sequence" and attenuation.get("status") == "ready" else
                      {"status": "undecided" if mode == "pending" else attenuation.get("status", "pending"),
                       "value": attenuation.get("group")})
        database_identity = copy.deepcopy(identity) or None
    guidance = verification_guidance(mode, missing_fields, component="该具体周期攻击通道")
    return {"attack_identity": {"actor": hit.get("character"),
            "character_id": binding["selection"]["character_id"],
            "slot": binding["selection"].get("slot"), "skill_id": binding["selection"].get("skill_id"),
            "candidate_id": mapping.get("candidate_id"), "channel_id": mapping["channel_id"],
            "action_event_profile_id": binding["action_event_profile_id"],
            "prototype_hit_id": channel.get("prototype_hit_id"),
            "application_profile_id": channel.get("application_profile_id"),
            "constellation": binding["selection"].get("constellation"),
            "ability_name": ((candidate.get("semantic_dependency") or {}).get("identity") or {}).get("ability_name"),
            "parent_pointer": ((candidate.get("parent_refs") or {}).get("source") or {}).get("json_pointer"),
            "version_line": binding["version_line"]},
        "known_fields": {"application_mode": mode, "counter_tag": counter_field,
            "rule_group": rule_field, "database_application_identity": database_identity,
            "verification": copy.deepcopy(mapping.get("verification"))},
        "missing_fields": sorted(set(missing_fields)), "conflicts": [],
        "local_checks": [{"kind": "runtime_periodic_attack", "candidate_id": mapping.get("candidate_id")},
                         {"kind": "raw_pointer", "reference": ((candidate.get("parent_refs") or {}).get("source") or {}).get("json_pointer")}],
        "targeted_research_questions": guidance["targeted_research_questions"],
        "keywords": [str(v) for v in (binding["selection"]["character_id"], mapping.get("candidate_id"),
                                        _counter_tag(mapping), "attenuationTag", "attenuationGroup") if v is not None],
        "acceptance_required": guidance["acceptance_required"],
        "database_patch_targets": ["runtime_periodic_attack.application_identity",
                                   "periodic_attack_channel.application_mode",
                                   "periodic_attack_channel.counter_tag"],
        "research_report": copy.deepcopy(mapping.get("research_report")),
        "research_report_template": research_report_template()}


def _size(value):
    return len(json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8"))


def _record(binding):
    return {"compiler": COMPILER, "action_event_profile_id": binding["action_event_profile_id"],
        "status": "pending", "actor": None, "whole_source_status": "partial", "dispatch_mode": "calibrated",
        "action_creation_association": "unproven", "dispatch_semantics_status": "not_proven",
        "gameplay_interpretation": "declared_not_verified", "version_line": binding["version_line"],
        "selection": copy.deepcopy(binding["selection"]), "origin_reference_id": binding["origin_reference_id"],
        "query": None, "snapshot": None, "source_fingerprint": None, "semantic_dependency": None,
        "periodic_dependency_fingerprint": None, "channels": [], "compiled_fields": {},
        "verification_tasks": [], "issues": []}


def prepare_periodic_sources(plan, template, db_path, missing):
    result = PreparedPeriodicSources()
    bindings = plan.get("periodic_attack_sources", [])
    if not bindings:
        return result
    from genshin_db.runtime_action_parameter import project_action_parameter
    from genshin_db.runtime_periodic_attack import get_periodic_attack_context
    from genshin_runtime.periodic_attack_identity import (
        periodic_source_fingerprint,
        periodic_source_payload,
    )

    profiles = {p["id"]: p for p in plan["action_event_profiles"]}
    native_hits = {h["id"]: h for h in plan["manual_template"]["hits"]}
    working_hits = {h["id"]: h for h in template["hits"]}
    elemental = (plan.get("mechanics_model") or {}).get("elemental_model")
    cache, snapshots, stages = {}, {}, []

    def issue(record, code, message):
        path = "periodic_attack_sources." + record["action_event_profile_id"]
        record["issues"].append({"code": code, "path": path, "message": message[:1024]})
        receipt_limit = code == "periodic_source_receipt_limit"
        emit_task_missing(
            missing, code, path, message[:1024], "review_periodic_attack",
            consumer="periodic_attack_source:" + record["action_event_profile_id"],
            category=("engine_capability_missing" if receipt_limit
                      else "knowledge_missing"),
            impact="critical" if receipt_limit else "high",
            blocking_scope=("engine_capability" if receipt_limit
                            else "agent_materialization"),
            retryable=not receipt_limit,
            repair_hint=(
                "缩小当前周期来源批次或扩展受审回执承载能力"
                if receipt_limit else
                "核对该周期档案的候选、频率、次数、附着模式和结构化证据"
            ),
        )

    result.records = [_record(binding) for binding in bindings]
    for binding, record in zip(bindings, result.records, strict=True):
        # Work orders exist even when discovery, candidate selection, or parent
        # projection fails before a concrete candidate can be loaded.
        profile_channels = {row["channel_id"]: row for row in profiles[binding["action_event_profile_id"]]["channels"]}
        record["verification_tasks"] = [_verification_task(
            binding, mapping, channel=profile_channels.get(mapping["channel_id"]),
            hit=native_hits.get((profile_channels.get(mapping["channel_id"]) or {}).get("prototype_hit_id")))
            for mapping in binding["channels"]]

    def stop_budget(record, message):
        for other in result.records:
            other.update(status="pending", query=None, channels=[], semantic_dependency=None, compiled_fields={})
            if other is not record:
                other["issues"].append({"code": "periodic_source_group_withdrawn", "path": "periodic_attack_sources." + other["action_event_profile_id"],
                    "message": "同组来源达到累计预算，未交付输入；尚未读取的来源不再查询"})
        issue(record, "periodic_source_receipt_limit", message)
        result.parameter_contexts.clear()
        result.attenuation_profiles.clear()
        result.application_identities.clear()
        return result

    for binding, record in zip(bindings, result.records, strict=True):
        profile = profiles[binding["action_event_profile_id"]]
        # Both multi-periodic and finite-sequence profiles are source-backed
        # native contracts.  Finite steps own occurrence timing, but their
        # component values and application rules still use the exact reviewed
        # level/constellation context instead of the legacy level-10 fallback.
        from .finite_action_schema import KIND as FINITE_APPLICATION_KIND
        dynamic_application = profile["kind"] in (
            APPLICATION_KIND, FINITE_APPLICATION_KIND)
        channels = {c["channel_id"]: c for c in profile["channels"]}
        try:
            ids = [c["candidate_id"] for c in binding["channels"]]
            query_args = {"version_line": binding["version_line"], **binding["selection"]}
            if binding["origin_reference_id"] is not None:
                query_args["origin_reference_id"] = binding["origin_reference_id"]
                if all(candidate is not None for candidate in ids):
                    query_args["candidate_ids"] = sorted(ids)
            key = fingerprint(query_args)
            if key not in cache:
                cache[key] = get_periodic_attack_context(db_path, **query_args)
            context = cache[key]
            query = context.public_projection
            # Charge the complete candidate before duplicating its source body.
            if _size(result.records) + _size(query) > MAX_RECEIPT_BYTES:
                raise OverflowError("全部周期来源完整回执超过2MiB")
            record.update(query=copy.deepcopy(query), snapshot=copy.deepcopy(query.get("snapshot")),
                          source_fingerprint=query.get("source_fingerprint"))
            if binding["origin_reference_id"] is None or any(candidate is None for candidate in ids):
                raise ValueError("请明确选择创建来源及每个攻击通道；发现候选不会自动成为执行来源")
            selected = query.get("selected_candidates") or []
            if (query.get("mode") != "selected" or query.get("issues")
                    or {c["candidate_id"] for c in selected} != set(ids) or len(selected) != len(ids)):
                raise ValueError("整组周期来源尚未通过父链及数值投影，未交付部分通道")
            if (query.get("semantic_dependency") != periodic_source_payload(query)
                    or query.get("source_fingerprint") != periodic_source_fingerprint(query)
                    or query.get("content_fingerprint") != fingerprint({k: v for k, v in query.items() if k != "content_fingerprint"})):
                raise ValueError("新鲜周期来源指纹与完整内容不一致")
            if not isinstance(elemental, dict):
                raise TypeError("周期档案需要已声明的元素目标与运行时模型")
            line, snapshot = binding["version_line"], query["snapshot"]
            if snapshot["version_line"] != line or snapshots.setdefault(line, snapshot["snapshot_id"]) != snapshot["snapshot_id"]:
                raise ValueError("周期来源在本次组合内跨快照")
            if binding["status"] == "pending":
                raise ValueError("周期用途、校准关联及共享ICD采用依据仍待确认")
            local = []
            selection = query["selection"]
            for mapping in binding["channels"]:
                channel = channels[mapping["channel_id"]]
                candidate = next(c for c in selected if c["candidate_id"] == mapping["candidate_id"])
                projection = candidate["projection"]
                task_index = next(i for i, task in enumerate(record["verification_tasks"])
                                  if task["attack_identity"]["channel_id"] == mapping["channel_id"])
                verification_task = _verification_task(
                    binding, mapping, candidate, channel=channel,
                    hit=native_hits.get(channel["prototype_hit_id"]))
                record["verification_tasks"][task_index] = verification_task
                raw_mode = mapping.get("application_mode")
                application_mode = "pending" if raw_mode in (None, "unknown") else raw_mode
                if application_mode == "pending":
                    raise ValueError("周期攻击通道附着模式待核实；必须逐通道确认，不能按元素爆发类别默认sequence")
                counter_tag = _counter_tag(mapping)
                identity = projection.get("application_identity") or {}
                rule_audit = mapping.get("application_rule_audit")
                if rule_audit is not None:
                    expected_database_identity = {"status": identity.get("status"),
                                                  "counter_tag": identity.get("tag")}
                    if rule_audit.get("database_identity") != expected_database_identity:
                        raise ValueError("附着规则审计的数据库身份与新鲜周期候选不一致")
                if application_mode == "sequence" and identity.get("status") == "ready":
                    identity_divergence = counter_tag != identity.get("tag")
                    if identity_divergence and not _adapter_override_ready(mapping, application_mode):
                        verification_task["conflicts"].append({"field": "counter_tag", "database": identity.get("tag"),
                            "declared": counter_tag, "message": "方案计数标签与数据库attenuationTag不一致"})
                        raise ValueError("周期通道计数标签与数据库确认的attenuationTag不一致")
                    identity_basis = ({"status": "adapter_override", "tag": counter_tag,
                        "database_tag": identity.get("tag"),
                        "source": copy.deepcopy(mapping["application_adapter"]),
                        "evidence": "confirmed_component_adapter", "next_action": None}
                        if identity_divergence else
                        {"status": "database_confirmed", "tag": identity["tag"],
                         "source": copy.deepcopy(identity.get("source")), "evidence": identity.get("evidence"),
                         "next_action": None})
                elif (application_mode == "sequence" and binding["status"] == "confirmed"
                      and has_verified_application_evidence(mapping, application_mode)):
                    identity_divergence = False
                    identity_basis = {"status": "declared", "tag": counter_tag,
                        "source": copy.deepcopy(mapping["verification"]), "evidence": "structured_plan_verification",
                        "next_action": "可将已确认的逐通道证据回填公共数据库；其他通道仍须分别核实"}
                elif application_mode == "sequence":
                    raise ValueError("周期通道数据库缺attenuationTag；须提供confirmed逐段结构化证据，自由文本或动作类别不能升格")
                elif not has_verified_application_evidence(mapping, application_mode):
                    raise ValueError("周期无ICD/无附着模式须有confirmed逐通道结构化正向证据；自由文本不能执行")
                else:
                    identity_divergence = identity.get("status") == "ready" or identity.get("tag") is not None
                    identity_basis = {"status": "not_applicable", "tag": None,
                        "source": copy.deepcopy(mapping["verification"]), "evidence": "application_mode",
                        "next_action": None}
                if (rule_audit is not None
                        and rule_audit.get("identity_divergence") != identity_divergence):
                    raise ValueError("附着规则审计的身份差异标记与新鲜周期候选不一致")
                hid, app_id = channel["prototype_hit_id"], channel["application_profile_id"]
                hit = native_hits[hid]
                if (hit.get("damage_system") != "classic" or hit.get("scaling_stat") != "atk"
                        or hit.get("damage_type_ids") != [projection["classification"]["value"]]):
                    raise ValueError("周期原型须显式classic单ATK且伤害类别与fresh候选一致")
                if record["actor"] is not None and record["actor"] != hit["character"]:
                    raise ValueError("同一周期档案不能跨角色")
                record["actor"] = hit["character"]
                required_components = ["parameter", "element", "classification"]
                if application_mode in ("sequence", "no_icd"):
                    required_components.append("gauge")
                if application_mode == "sequence":
                    required_components.append("attenuation")
                if any((projection.get(k) or {}).get("status") != "ready" for k in required_components):
                    raise ValueError("周期通道缺少当前附着模式所需的" + "/".join(required_components) + "来源")
                optional_issues = set(_OPTIONAL_CANDIDATE_ISSUES[application_mode])
                if profile["kind"] == FINITE_APPLICATION_KIND:
                    # The reviewed finite manifest, not the callback timer,
                    # owns all occurrence times.  Keep every other structural
                    # and numeric diagnostic fail-closed.
                    optional_issues.add("periodic_timer_not_exact")
                blocking_issues = [row for row in candidate.get("issues", [])
                                   if row.get("code") not in optional_issues]
                if blocking_issues:
                    raise ValueError("周期候选仍有当前附着模式不可忽略的来源或结构诊断")
                schedule = channel.get("schedule")
                candidate_kind = candidate["channel_kind"]
                if profile["kind"] == FINITE_APPLICATION_KIND:
                    # A finite manifest owns every step time.  The database
                    # candidate remains proof of the component source only;
                    # it must not regain schedule authority here.
                    if schedule is not None:
                        raise ValueError("有限序列组件不能同时声明第二套执行schedule")
                elif candidate_kind == "periodic_callback_attack":
                    if schedule["kind"] not in ("periodic", "explicit_offsets"):
                        raise ValueError("周期callback只能映射等间隔或受审不等间隔schedule")
                    # Explicit offsets replace only execution topology.  A
                    # ready native timer is still required as positive proof
                    # that this fresh candidate really is a periodic callback.
                    if projection.get("timer", {}).get("status") != "ready":
                        raise ValueError("周期timer尚未通过精确微秒来源守卫")
                elif candidate_kind == "ability_start_attack":
                    if schedule["kind"] != "single":
                        raise ValueError("起手攻击候选只能映射single schedule")
                else:
                    raise ValueError("fresh候选的callback类型不受支持")
                parameter = projection["parameter"]
                supplied = project_action_parameter(context.parameter_context, parameter["param_index"])
                if (context.selected_parameter_projections.get(candidate["candidate_id"]) != supplied
                        or supplied["snapshot"] != snapshot or supplied["selection"] != selection
                        or selection["character_id"] != binding["selection"]["character_id"]
                        or (dynamic_application and supplied["level"].get("effective_level") not in range(1, 16))
                        or (not dynamic_application and (selection.get("constellation") != 0
                                                         or supplied["level"]["effective_level"] != 10))
                        or supplied["scalar_projection"]["status"] != "ready"
                        or supplied["scalar_projection"]["value"] != parameter["value"] or supplied["source"] != parameter["source"]):
                    raise ValueError("周期标量与本次精确Q参数上下文或档案版本范围不一致")
                element = projection["element"]["value"]
                gu = (projection.get("gauge") or {}).get("value")
                allowed_elements = (("pyro", "cryo", "hydro", "anemo") if dynamic_application
                                    else ("cryo", "hydro", "anemo"))
                classification = projection["classification"]["value"]
                required_slot = {"damage.normal_attack": "normal", "damage.charged_attack": "normal",
                    "damage.plunging_attack": "normal", "damage.elemental_skill": "skill",
                    "damage.elemental_burst": "burst"}.get(classification)
                if (element not in allowed_elements or required_slot not in selection.get("matching_slots", [])):
                    raise ValueError("周期攻击的元素/分类超出首片范围")
                if application_mode != "no_application" and (
                        type(gu) is not int or gu not in (1, 2, 4)
                        or projection["gauge"].get("tax_applied") is not False):
                    raise ValueError("周期攻击的未扣底税原始GU超出当前附着模式范围")
                group, fresh = None, None
                if application_mode == "sequence":
                    attenuation = projection["attenuation"]
                    group = attenuation["group"]
                    fresh = context.attenuation_profiles.get((group, "application"))
                    if (fresh != attenuation["profile"] or fresh["snapshot"] != snapshot
                            or fresh["execution"]["status"] != "ready"):
                        raise ValueError("周期应用序列与本次fresh衰减来源不一致")
                has_icd_overlay = (((mapping.get("application_adapter") or {}).get(
                    "declaration") or {}).get("icd_rule") or {}).get("mode") == "timer_counter"
                effective_icd_rule = ((rule_audit or {}).get("effective_icd_rule")
                                      if application_mode == "sequence" and has_icd_overlay else None)
                effective_rule_group = (effective_icd_rule["rule_group"]
                                        if effective_icd_rule is not None else group)
                pkey = fingerprint({"selection": binding["selection"], "version_line": line})
                prior = result.parameter_contexts.setdefault(pkey, context.parameter_context)
                if prior != context.parameter_context:
                    raise ValueError("同一Q参数上下文不一致")
                if application_mode == "sequence":
                    prior = result.attenuation_profiles.setdefault((line, group), fresh)
                    if prior != fresh:
                        raise ValueError("同组周期附着序列不一致")
                psource = {"hit_id": hid, "version_line": line, "selection": copy.deepcopy(binding["selection"]),
                    "param_index": parameter["param_index"], "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier",
                    "status": binding["status"], "source": binding["source"], "expected_snapshot_id": snapshot["snapshot_id"],
                    "expected_node_sha256": supplied["source"]["node_sha256"]}
                esource = None
                fields = {"element": element, "multiplier": parameter["value"], "param_index": parameter["param_index"],
                    "application_mode": application_mode, "application_identity_status": identity_basis["status"]}
                if application_mode == "sequence":
                    esource = {"profile_id": app_id, "version_line": line, "rule_group": group,
                        "counter_tag": counter_tag, "application_mode": "sequence", "source": binding["source"],
                        "expected_snapshot_id": snapshot["snapshot_id"], "expected_node_sha256": fresh["source"]["node_sha256"]}
                    if "verification" in mapping:
                        esource["verification"] = copy.deepcopy(mapping["verification"])
                    if effective_icd_rule is not None:
                        esource["application_rule_audit"] = copy.deepcopy(rule_audit)
                    application = {"id": app_id, "element": element, "application": "apply", "gauge_units": gu,
                        "icd": {"kind": "unknown"}, "status": binding["status"], "source": binding["source"]}
                    fields.update(gauge_units=gu, tax_applied=False,
                                  rule_group=effective_rule_group, counter_tag=counter_tag)
                    if effective_icd_rule is not None:
                        fields["database_rule_group"] = group
                elif application_mode == "no_icd":
                    application = {"id": app_id, "element": element, "application": "apply", "gauge_units": gu,
                        "icd": {"kind": "none"}, "status": binding["status"], "source": binding["source"]}
                    fields.update(gauge_units=gu, tax_applied=False, rule_group=None, counter_tag=None)
                else:
                    application = {"id": app_id, "element": element, "application": "none",
                        "status": binding["status"], "source": binding["source"]}
                    fields.update(rule_group=None, counter_tag=None)
                record["channels"].append({"channel_id": channel["channel_id"], "candidate_id": candidate["candidate_id"],
                    "channel_kind": candidate["channel_kind"], "prototype_hit_id": hid, "application_profile_id": app_id,
                    "projection": copy.deepcopy(projection), "application_identity": identity_basis,
                    "verification": copy.deepcopy(mapping.get("verification")), "compiled_fields": fields,
                    **({"database_application_identity": copy.deepcopy(identity),
                        "application_identity_divergence": identity_divergence}
                       if "application_rule_audit" in mapping else {}),
                    **({"application_adapter": copy.deepcopy(mapping["application_adapter"])}
                       if "application_adapter" in mapping else {}),
                    **({"application_rule_audit": copy.deepcopy(mapping["application_rule_audit"])}
                       if "application_rule_audit" in mapping else {}),
                    **({"deprecated_input_fields": ["shared_group"]} if "shared_group" in mapping else {})})
                if application_mode == "sequence":
                    result.application_identities[app_id] = copy.deepcopy(identity_basis)
                local.append((hid, element, psource, esource, application))
            semantics = {"database": copy.deepcopy(query["semantic_dependency"]),
                "interpretation": {k: binding[k] for k in ("purpose", "status", "source")},
                "channels": sorted(({"candidate_id": c["candidate_id"], "application_mode": (
                    "pending" if c.get("application_mode") in (None, "unknown") else c["application_mode"]),
                    "counter_tag": _counter_tag(c), "verification": copy.deepcopy(c.get("verification")),
                    **({"application_adapter": copy.deepcopy(c["application_adapter"])}
                       if "application_adapter" in c else {}),
                    **({"application_rule_audit": copy.deepcopy(c["application_rule_audit"])}
                       if "application_rule_audit" in c else {}),
                    **({"identity_divergence": next(
                        row["application_identity_divergence"] for row in record["channels"]
                        if row["channel_id"] == c["channel_id"])}
                       if "application_rule_audit" in c else {}),
                    **({"deprecated_counter_tag_field": "shared_group"} if "shared_group" in c else {})}
                    for c in binding["channels"]), key=lambda c: c["candidate_id"])}
            record.update(semantic_dependency=semantics, periodic_dependency_fingerprint=fingerprint(semantics))
            stages.append((record, local))
        except OverflowError as exc:
            return stop_budget(record, str(exc))
        except (EquipmentError, ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
            if isinstance(exc, PlanError):
                raise
            issue(record, "periodic_source_pending", str(exc))
        if _size(result.records) > MAX_RECEIPT_BYTES:
            return stop_budget(record, "全部周期来源回执超过2MiB，未交付部分输入")
    if len(stages) != len(bindings):
        for record in result.records:
            record["compiled_fields"] = {}
            for channel in record["channels"]:
                channel["compiled_fields"] = {}
        result.parameter_contexts.clear()
        result.attenuation_profiles.clear()
        result.application_identities.clear()
        return result
    for record, _ in stages:
        record["status"] = "ready"
        record["compiled_fields"] = {c["channel_id"]: copy.deepcopy(c["compiled_fields"]) for c in record["channels"]}
    if _size(result.records) > MAX_RECEIPT_BYTES:
        return stop_budget(result.records[-1], "完整周期来源回执超过2MiB，未交付部分输入")
    for record, local in stages:
        for hid, element, psource, esource, app in local:
            native_hits[hid]["element"] = working_hits[hid]["element"] = element
            plan.setdefault("hit_parameter_sources", []).append(psource)
            # ``no_icd`` and ``no_application`` are already fully compiled
            # application semantics.  Only sequence mode needs the ordinary
            # attenuation-source compiler to materialize a rule template.
            if esource is not None:
                plan.setdefault("elemental_sources", []).append(esource)
            elemental["profiles"].append(app)
        result.sources.append({"kind": "periodic_attack", "action_event_profile_id": record["action_event_profile_id"],
            "source_fingerprint": record["source_fingerprint"], "periodic_dependency_fingerprint": record["periodic_dependency_fingerprint"]})
    return result
