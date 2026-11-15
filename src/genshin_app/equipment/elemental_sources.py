"""Source-bound elemental application assembly; never infer from action class."""
from __future__ import annotations

import copy
import sqlite3

from genshin_app.task_capability_producer import emit_task_missing
from genshin_db.equipment import EquipmentError

from .application_evidence import research_report_template, verification_guidance
from .attack_metadata_schema import has_verified_application_evidence
from .files import fingerprint
from .schema import PlanError


def _groups(binding):
    return (binding.get("counter_tag") or binding.get("shared_group"),
            binding.get("rule_group") or binding.get("attenuation_group"))


def _identity(binding, prepared, counter_tag):
    """Return the reviewed counter identity for sequence mode only."""
    supplied = prepared.get(binding["profile_id"])
    if supplied is not None and supplied.get("tag") != counter_tag:
        raise PlanError("服务器内部附着计数身份与来源绑定不一致", "source_conflict",
                        {"path": "elemental_sources." + binding["profile_id"]})
    if supplied is not None:
        return copy.deepcopy(supplied)
    if has_verified_application_evidence(binding, "sequence"):
        return {"status": "declared", "tag": counter_tag,
            "evidence": "structured_plan_verification", "source": copy.deepcopy(binding["verification"]),
            "next_action": "可将已确认的逐攻击段证据回填公共数据库；同技能不同攻击分量仍须分别核实"}
    return {"status": "pending", "tag": counter_tag, "evidence": "structured_verification_missing",
            "source": None, "next_action": "提供逐攻击段的定位、版本、检索日期、组件范围与确认字段；自由文本不能升格"}


def _application_uses(plan, profile_id):
    rotation = plan.get("rotation_model") or {}
    abilities = {row.get("id"): row for row in rotation.get("abilities", [])}
    actions = {row.get("id"): row for row in rotation.get("actions", [])}
    hits = {row.get("id"): row for row in (plan.get("manual_template") or {}).get("hits", [])}
    target_id = (((plan.get("mechanics_model") or {}).get("elemental_model") or {}).get("target") or {}).get("id")
    bindings = {}
    for row in plan.get("action_event_bindings", []):
        bindings.setdefault(row.get("profile_id"), []).append(row.get("ability_id"))
    source_records = {}
    for field in ("attack_metadata_sources", "periodic_attack_sources"):
        for row in plan.get(field, []):
            source_records[row.get("action_event_profile_id")] = row
    uses = []

    def add(*, component, action_id=None, ability_id=None, hit_id=None, event_id=None,
            profile=None, channel_id=None):
        action = actions.get(action_id) or {}
        ability_id = ability_id or action.get("ability_id")
        ability = abilities.get(ability_id) or {}
        hit = hits.get(hit_id) or {}
        source_record = source_records.get((profile or {}).get("id"), {})
        selection = source_record.get("selection") or {}
        candidate_id = source_record.get("candidate_id")
        if channel_id is not None:
            channel_source = next((row for row in source_record.get("channels", [])
                                   if row.get("channel_id") == channel_id), {})
            candidate_id = channel_source.get("candidate_id", candidate_id)
        uses.append({"actor": hit.get("character") or ability.get("character"),
            "ability_id": ability_id, "action_id": action_id, "hit_id": hit_id,
            "event_id": event_id, "action_event_profile_id": (profile or {}).get("id"),
            "channel_id": channel_id, "component": component,
            "variant": (profile or {}).get("variant") or ability.get("variant"),
            "character_id": selection.get("character_id"), "slot": selection.get("slot"),
            "skill_id": selection.get("skill_id"), "candidate_id": candidate_id,
            "constellation": selection.get("constellation"),
            "version_line": source_record.get("version_line"), "target_id": target_id})

    for name in ("hit_events", "hit_patterns", "application_events"):
        for row in (plan.get("mechanics_model") or {}).get(name, []):
            if (row.get("application") or {}).get("profile_id") == profile_id:
                add(component=name.removesuffix("s"), action_id=row.get("action_id"),
                    hit_id=row.get("hit_id"), event_id=row.get("id"))
    for profile in plan.get("action_event_profiles", []):
        components = []
        if profile.get("application_profile_id") == profile_id:
            components.append((profile.get("prototype_hit_id"), None))
        for channel in profile.get("channels", []):
            if channel.get("application_profile_id") == profile_id:
                components.append((channel.get("prototype_hit_id"), channel.get("channel_id")))
        for hit_id, channel_id in components:
            ability_ids = bindings.get(profile.get("id"), []) or [None]
            for ability_id in ability_ids:
                action_ids = [row["id"] for row in rotation.get("actions", [])
                              if row.get("ability_id") == ability_id] if ability_id else []
                for action_id in action_ids or [None]:
                    add(component="action_event_profile", action_id=action_id, ability_id=ability_id,
                        hit_id=hit_id, profile=profile, channel_id=channel_id)
    unique = {fingerprint(row): row for row in uses}
    return [unique[key] for key in sorted(unique)][:128]


def _verification_task(binding, mode, counter_tag=None, rule_group=None, *, plan=None, identity=None):
    verification = binding.get("verification") or {}
    uses = _application_uses(plan or {}, binding["profile_id"])
    identity_fields = ("actor", "character_id", "slot", "skill_id", "candidate_id", "ability_id",
                       "action_id", "hit_id", "component", "variant", "constellation",
                       "version_line", "target_id")
    unresolved = [field for field in identity_fields if not uses or all(row.get(field) is None for row in uses)]
    missing = []
    if mode == "pending":
        missing.append("application_mode")
    if mode == "sequence" and not counter_tag:
        missing.append("counter_tag")
    if mode == "sequence" and not rule_group:
        missing.append("rule_group")
    database_confirmed = mode == "sequence" and (identity or {}).get("status") in ("ready", "database_confirmed")
    if mode != "pending" and not database_confirmed and not has_verified_application_evidence(binding, mode):
        missing.append("structured_verification")
    known_counter = ({"status": "not_applicable", "value": None} if mode in ("no_icd", "no_application") else
                     {"status": "database_confirmed", "value": (identity or {}).get("tag"),
                      "source": copy.deepcopy((identity or {}).get("source"))}
                     if (identity or {}).get("status") in ("ready", "database_confirmed") else
                     {"status": "undecided" if mode == "pending" else "declared", "value": counter_tag})
    known_rule = ({"status": "not_applicable", "value": None} if mode in ("no_icd", "no_application") else
                  {"status": "undecided" if mode == "pending" else "declared", "value": rule_group})
    guidance = verification_guidance(mode, missing, component="该具体攻击分量")
    return {"attack_identity": {"profile_id": binding["profile_id"], "version_line": binding["version_line"],
                                "uses": copy.deepcopy(uses), "unresolved_identity_fields": unresolved},
        "uses": uses, "unresolved_identity_fields": unresolved,
        "known_fields": {"application_mode": mode, "counter_tag": known_counter, "rule_group": known_rule,
                         "verification": copy.deepcopy(verification) or None},
        "missing_fields": sorted(set(missing)), "conflicts": [],
        "local_checks": [{"kind": "attenuation_profile", "rule_group": rule_group}] if rule_group else [],
        "targeted_research_questions": guidance["targeted_research_questions"],
        "keywords": [str(v) for v in (binding["profile_id"], counter_tag, rule_group,
                                       "attenuationTag", "attenuationGroup") if v],
        "acceptance_required": guidance["acceptance_required"],
        "database_patch_targets": ["attack_component.application_mode", "attack_component.counter_tag",
                                   "attack_component.shared_scope"],
        "research_report": copy.deepcopy(binding.get("research_report")),
        "research_report_template": research_report_template()}


def _mode_record(binding, mode, *, legacy_alias=None, plan=None):
    counter_tag, rule_group = _groups(binding)
    identity = ({"status": "pending", "tag": None, "evidence": "application_mode_unknown",
                 "source": binding["source"],
                 "next_action": "核实该角色这一具体攻击段是否附着、是否无ICD；不得用动作类别默认值"}
                if mode == "pending" else
                {"status": "not_applicable", "tag": None, "evidence": "application_mode",
                 "source": binding["source"], "next_action": None})
    return {
        "profile_id": binding["profile_id"], "status": "pending", "application_mode": mode,
        "counter_tag": None, "rule_group": {"status": "pending" if mode == "pending" else "not_applicable", "group": None},
        "version_line": binding["version_line"], "binding_basis": binding["source"],
        "attack_mapping": "explicitly_declared_not_inferred",
        "application_identity": identity,
        "verification_task": _verification_task(binding, mode, counter_tag, rule_group, plan=plan),
        **({"deprecated_application_mode": legacy_alias, "canonical_application_mode": "pending"}
           if legacy_alias else {}),
    }


def _check_placeholder(profile, mode, path):
    if mode in ("sequence", "no_icd"):
        if profile.get("application") != "apply" or profile.get("icd") != {"kind": "unknown"}:
            raise PlanError("附着来源与手工ICD不能同时交付；序列/无ICD模式要求原始档案icd为unknown占位",
                            "duplicate_delivery", {"path": path})
    elif (mode == "no_application"
          and (profile.get("application") != "unknown" or "gauge_units" in profile or "icd" in profile)):
        raise PlanError("无附着来源接管完整附着语义；原始档案须为application=unknown且不能预填GU/ICD",
                        "duplicate_delivery", {"path": path})


def compile_elemental_sources(plan, db_path, missing, *, prepared_profiles=None, prepared_identities=None):
    bindings = plan.get("elemental_sources", [])
    if not bindings:
        return [], []
    from genshin_db.runtime_attenuation import get_runtime_attenuation_profile

    model = (plan.get("mechanics_model") or {}).get("elemental_model")
    if model is None:
        emit_task_missing(
            missing, "missing_input", "mechanics_model.elemental_model",
            "附着来源需要先声明元素模型", "edit_mechanics",
            consumer="elemental_source:batch", category="core_data_missing",
            repair_hint="先声明当前方案的元素目标、附着档案和通用元素模型",
        )
        return [], []
    profiles = {p["id"]: p for p in model["profiles"]}
    # These optional caches were produced in the same server-side read session.
    compilations, sources, cached = [], [], dict(prepared_profiles or {})
    identities = copy.deepcopy(prepared_identities or {})
    for binding in bindings:
        path = "elemental_sources." + binding["profile_id"]
        consumer = "elemental_source:" + binding["profile_id"]

        def task_missing(code, message, *, category="knowledge_missing",
                         scope="agent_materialization", retryable=True,
                         _path=path, _consumer=consumer):
            emit_task_missing(
                missing, code, _path, message, "review_elemental_source",
                consumer=_consumer, category=category,
                blocking_scope=scope, retryable=retryable,
                repair_hint="核对该攻击分量的附着模式、计数身份、规则组和结构化证据",
            )

        profile = profiles.get(binding["profile_id"])
        if profile is None:
            raise PlanError("附着来源引用不存在的档案", "reference", {"path": path})
        raw_mode = binding.get("application_mode")
        mode = "pending" if raw_mode in (None, "unknown") else raw_mode
        _check_placeholder(profile, mode, path)

        if mode == "pending":
            legacy_alias = "unknown" if raw_mode == "unknown" else ("missing" if raw_mode is None else None)
            record = _mode_record(binding, mode, legacy_alias=legacy_alias, plan=plan)
            compilations.append(record)
            message = ("该具体攻击组件是否附着及其ICD模式尚未核实；Agent需查询这一攻击段，"
                       "同技能不同段、召唤物、命座追加攻击可不同")
            record["issue"] = {"code": "elemental_application_mode_pending", "message": message}
            task_missing("elemental_application_mode_pending", message)
            continue

        if mode == "no_icd":
            record = _mode_record(binding, mode, plan=plan)
            if not has_verified_application_evidence(binding, mode):
                message = "无ICD需要逐攻击分量的confirmed结构化正向证据；source自由文本不能升格"
                record["issue"] = {"code": "elemental_application_evidence_pending", "message": message}
                task_missing("elemental_application_evidence_pending", message)
                compilations.append(record)
                continue
            profile["icd"] = {"kind": "none"}
            record.update(status="ready", compiled_icd=copy.deepcopy(profile["icd"]),
                          compiled_profile=copy.deepcopy(profile), compiler="declared-elemental-application-v1")
            record["compilation_fingerprint"] = fingerprint(record)
            sources.append({"kind": "elemental_application_mode", "profile_id": profile["id"],
                            "application_mode": mode, "binding_basis": binding["source"],
                            "compilation_fingerprint": record["compilation_fingerprint"]})
            compilations.append(record)
            continue

        if mode == "no_application":
            record = _mode_record(binding, mode, plan=plan)
            if not has_verified_application_evidence(binding, mode):
                message = "无附着需要逐攻击分量的confirmed结构化正向证据；source自由文本不能升格"
                record["issue"] = {"code": "elemental_application_evidence_pending", "message": message}
                task_missing("elemental_application_evidence_pending", message)
                compilations.append(record)
                continue
            profile["application"] = "none"
            record.update(status="ready", compiled_icd=None, compiled_profile=copy.deepcopy(profile),
                          compiler="declared-elemental-application-v1")
            record["compilation_fingerprint"] = fingerprint(record)
            sources.append({"kind": "elemental_application_mode", "profile_id": profile["id"],
                            "application_mode": mode, "binding_basis": binding["source"],
                            "compilation_fingerprint": record["compilation_fingerprint"]})
            compilations.append(record)
            continue

        counter_tag, rule_group = _groups(binding)
        identity = _identity(binding, identities, counter_tag)
        record = {"profile_id": profile["id"], "status": "pending", "application_mode": "sequence",
                  "counter_tag": counter_tag,
                  "version_line": binding["version_line"], "binding_basis": binding["source"],
                  "attack_mapping": "explicitly_declared_not_inferred", "application_identity": identity,
                  "rule_group": {"status": "database_pending", "group": rule_group},
                  "verification_task": _verification_task(
                      binding, "sequence", counter_tag, rule_group, plan=plan, identity=identity),
                  **({"deprecated_input_fields": [name for name in ("shared_group", "attenuation_group") if name in binding]}
                     if "shared_group" in binding or "attenuation_group" in binding else {})}
        compilations.append(record)
        key = (binding["version_line"], rule_group)
        try:
            if key not in cached:
                cached[key] = get_runtime_attenuation_profile(db_path, group=key[1], version_line=key[0])
            queried = cached[key]
        except (EquipmentError, ValueError, OSError, sqlite3.Error) as exc:
            task_missing(
                "elemental_source_unavailable", str(exc),
                category="core_data_missing", scope="task_capability",
            )
            record["issue"] = {"code": getattr(exc, "reason", "unavailable"), "message": str(exc)}
            continue
        record.update(source=copy.deepcopy(queried["source"]), snapshot=copy.deepcopy(queried["snapshot"]),
                      projection=copy.deepcopy(queried["projection"]), execution=copy.deepcopy(queried["execution"]))
        record["rule_group"]["status"] = "database_confirmed"
        record["verification_task"]["known_fields"]["rule_group"] = {
            "status": "database_confirmed", "value": rule_group,
            "source": copy.deepcopy(queried.get("source"))}
        actual_snapshot = queried["snapshot"]["snapshot_id"]
        actual_node = queried["source"]["node_sha256"]
        record["required_confirmation"] = {"expected_snapshot_id": actual_snapshot, "expected_node_sha256": actual_node}
        if binding["expected_snapshot_id"] != actual_snapshot or binding["expected_node_sha256"] != actual_node:
            task_missing(
                "source_changed",
                "请根据当前原始条目复核附着规则和攻击共享关系，再确认本次快照/节点指纹",
            )
            continue
        if queried["execution"]["status"] != "ready":
            task_missing(
                "unsupported_elemental_source",
                "当前只执行已核实的有限二元附着序列；原始资料中的其他系数仍待解释",
                category="engine_capability_missing", scope="engine_capability",
                retryable=False,
            )
            continue
        if identity.get("status") == "pending":
            message = "规则模板已确认，但具体攻击到counter_tag的绑定缺少confirmed结构化逐段证据"
            record["issue"] = {"code": "elemental_application_evidence_pending", "message": message}
            task_missing("elemental_application_evidence_pending", message)
            continue
        projection = queried["projection"]
        rule_audit = binding.get("application_rule_audit")
        effective_icd_rule = (rule_audit or {}).get("effective_icd_rule")
        if effective_icd_rule is not None:
            database_rule = {"status": "ready", "rule_group": rule_group,
                "reset_seconds": projection["reset_cycle_seconds_rounded"],
                "sequence": copy.deepcopy(projection["durability_sequence"]),
                "source_fingerprint": queried["content_fingerprint"]}
            base_fingerprint = fingerprint(database_rule)
            expected_group = "adapter.icd." + fingerprint({
                "base_rule_fingerprint": base_fingerprint,
                "kind": "timer_counter",
                "reset_seconds": effective_icd_rule["reset_seconds"],
                "hit_interval": effective_icd_rule["hit_interval"],
            })[:48]
            if (rule_audit.get("base_icd_rule") != database_rule
                    or rule_audit.get("base_icd_rule_fingerprint") != base_fingerprint
                    or effective_icd_rule.get("kind") != "timer_counter"
                    or effective_icd_rule.get("rule_group") != expected_group
                    or effective_icd_rule.get("sequence") is not None
                    or effective_icd_rule.get("first_hit") != 1):
                raise PlanError("局部ICD规则审计与本次新鲜数据库基线或派生身份不一致",
                                "source_conflict", {"path": path})
            profile["icd"] = {"kind": "timer_counter", "counter_tag": counter_tag,
                              "rule_group": effective_icd_rule["rule_group"],
                              "reset_seconds": effective_icd_rule["reset_seconds"],
                              "hit_interval": effective_icd_rule["hit_interval"]}
            record["database_rule_group"] = rule_group
            record["rule_group"] = {"status": "adapter_derived",
                                    "group": effective_icd_rule["rule_group"]}
            record["application_rule_audit"] = copy.deepcopy(rule_audit)
        else:
            profile["icd"] = {"kind": "sequence", "counter_tag": counter_tag,
                              "rule_group": rule_group,
                              "reset_seconds": projection["reset_cycle_seconds_rounded"],
                              "sequence": copy.deepcopy(projection["durability_sequence"])}
        record.update(status="ready", compiled_icd=copy.deepcopy(profile["icd"]),
                      compiled_profile=copy.deepcopy(profile), compiler="database-icd-v1")
        record["compilation_fingerprint"] = fingerprint(record)
        sources.append({"kind": "elemental_icd", "profile_id": profile["id"], "snapshot_id": actual_snapshot,
                        "node_sha256": actual_node, "compilation_fingerprint": record["compilation_fingerprint"]})

    # Rule-group signatures must remain globally unique. Actor/counter-tag
    # conflicts are checked against actual action bindings after dispatch assembly.
    from genshin_engine.elemental import validate_elemental_model
    compiled_issues = validate_elemental_model(model)
    if compiled_issues:
        for issue in compiled_issues:
            emit_task_missing(
                missing, issue["code"], issue["path"], issue["message"],
                "review_elemental_source", consumer="elemental_source:batch",
                category="engine_capability_missing", impact="high",
                blocking_scope="engine_capability",
                repair_hint="修复组合后的通用元素模型，再重新编译附着来源",
            )
        for record in compilations:
            if record["status"] == "ready":
                record.update(status="pending", assembly_issues=copy.deepcopy(compiled_issues))
                record.pop("compilation_fingerprint", None)
        sources = []
    return compilations, sources
