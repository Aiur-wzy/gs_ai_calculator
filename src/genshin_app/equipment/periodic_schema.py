"""Native source-backed multi-contact declarations; no interval defaults."""
from __future__ import annotations

import copy

from .application_evidence import (
    APPLICATION_ADAPTER,
    APPLICATION_ADAPTER_AUDIT,
    APPLICATION_RULE_AUDIT,
    RESEARCH_REPORT,
    VERIFICATION,
)

ID = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"}
SOURCE = {"type": "string", "minLength": 1, "maxLength": 2048}
STATUS = {"enum": ["confirmed", "assumed", "pending"]}


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


SELECTION = _object({"character_id": {"type": "integer", "minimum": 1, "maximum": 2**53-1},
    "slot": {"enum": ["normal", "skill", "burst"]}, "skill_id": {"type": "integer", "minimum": 1, "maximum": 2**53-1},
    "skill_depot_id": {"type": "integer", "minimum": 1, "maximum": 2**53-1},
    "constellation": {"type": "integer", "minimum": 0, "maximum": 6},
    "talent_level": {"type": "integer", "minimum": 1, "maximum": 15}}, ["character_id", "constellation"])

KIND = "calibrated_multi_contact_v1"
APPLICATION_KIND = "calibrated_multi_application_v2"
MULTI_KINDS = (KIND, APPLICATION_KIND)
COMPILER = "database-periodic-attack-v1"
APPLICATION_CONTRACT = {
    "contract_id": APPLICATION_KIND + ":1",
    "ruleset": "gauge-swirl-dynamic-v3",
    "emission": "finite_multi_damage_application",
    "contact": "not_supported",
    "source_scope": "exact_character_slot_constellation_and_talent_level",
    "sampling": "current_action_start_or_explicit_capture",
}
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
TIME = {"anyOf": [{"type": "number", "exclusiveMinimum": 0, "maximum": 10**9, "multipleOf": .000001}, {"type": "null"}]}
OFFSET_TIME = {"anyOf": [{"type": "number", "minimum": 0, "maximum": 10**9, "multipleOf": .000001}, {"type": "null"}]}
PRECISION = {"anyOf": [{"type": "number", "minimum": 0, "maximum": 1}, {"type": "null"}]}
KNOWN_BOOL = {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
HASH = {"anyOf": [{"type": "string", "pattern": r"^[0-9a-f]{64}$"}, {"type": "null"}]}
CALIBRATION = {"precision_seconds": PRECISION, "status": STATUS, "source": SOURCE}
DERIVED_COUNT_POLICY = "through_instance_end_inclusive"
REPLACEMENT_POLICY = "replace_previous"
REFRESH_OPERATION = "replace_active"
REFRESH_SNAPSHOT_POLICY = "preserve_active_generation"
APPLICATION_CADENCE_KIND = "every_n_landed_hits"
APPLICATION_CADENCE = _object({
    "kind": {"enum": [APPLICATION_CADENCE_KIND]},
    "period_hits": {"type": "integer", "minimum": 1, "maximum": 256},
    "first_attempt_ordinal": {"type": "integer", "minimum": 1, "maximum": 256},
    "scope": {"enum": ["channel_generation"]},
    "status": STATUS,
    "source": SOURCE,
})
SCHEDULE = {"anyOf": [
    _object({"kind": {"enum": ["periodic"]}, "first_offset_seconds": OFFSET_TIME,
             "count": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 256}, {"type": "null"}]},
             "derive_count": {"enum": [DERIVED_COUNT_POLICY]}, **CALIBRATION},
            ["kind", "first_offset_seconds", "count", "precision_seconds", "status", "source"]),
    _object({"kind": {"enum": ["explicit_offsets"]},
             "offsets_seconds": {"type": "array", "minItems": 1, "maxItems": 256,
                                  "items": OFFSET_TIME}, **CALIBRATION}),
    _object({"kind": {"enum": ["single"]}, "offset_seconds": OFFSET_TIME, **CALIBRATION})]}
CHANNEL = _object({"channel_id": ID, "prototype_hit_id": ID, "application_profile_id": ID,
    "contact_profile_id": ID, "schedule": SCHEDULE, "default_landed": KNOWN_BOOL,
    "self_sampling": {"enum": ["current", "action_start", "action_capture"]},
    "application_cadence": APPLICATION_CADENCE},
    ["channel_id", "prototype_hit_id", "application_profile_id", "contact_profile_id",
     "schedule", "default_landed", "self_sampling"])
PROFILE = _object({"id": ID, "kind": {"enum": [KIND]}, "variant": {"enum": ["default", "tap", "hold"]},
    "lifecycle": _object({"establishment_offset_seconds": OFFSET_TIME, "instance_end_offset_seconds": TIME,
        **CALIBRATION, "owner_and_target_survive": KNOWN_BOOL, "no_interrupt": KNOWN_BOOL,
        "no_refresh_replacement_or_early_despawn": KNOWN_BOOL,
        "replacement_policy": {"enum": [REPLACEMENT_POLICY]}, "no_early_despawn": KNOWN_BOOL},
        ["establishment_offset_seconds", "instance_end_offset_seconds", "precision_seconds", "status", "source",
         "owner_and_target_survive", "no_interrupt", "no_refresh_replacement_or_early_despawn"]),
    "channels": {"type": "array", "minItems": 1, "maxItems": 8, "items": CHANNEL},
    "accepted_profile_fingerprint": HASH})
APPLICATION_CHANNEL = copy.deepcopy(CHANNEL)
del APPLICATION_CHANNEL["properties"]["contact_profile_id"]
APPLICATION_CHANNEL["required"].remove("contact_profile_id")
APPLICATION_PROFILE = copy.deepcopy(PROFILE)
APPLICATION_PROFILE["properties"]["kind"] = {"enum": [APPLICATION_KIND]}
APPLICATION_PROFILE["properties"]["channels"]["items"] = APPLICATION_CHANNEL
PROFILE_SCHEMA = {"anyOf": [PROFILE, APPLICATION_PROFILE]}
_CONTACT = {"channel_id": ID, "ordinal": {"type": "integer", "minimum": 1, "maximum": 256},
    "landed": KNOWN_BOOL, "order": {"type": "integer", "minimum": -10**9, "maximum": 10**9},
    "status": STATUS, "source": SOURCE}
OVERRIDE = _object({"kind": {"enum": [KIND]}, "action_id": ID,
    "contacts": {"type": "array", "minItems": 1, "maxItems": 2048, "items": {"anyOf": [
        _object(_CONTACT, ["channel_id", "ordinal", "landed", "status", "source"]),
        _object(_CONTACT, ["channel_id", "ordinal", "order", "status", "source"])]}}})
APPLICATION_OVERRIDE = copy.deepcopy(OVERRIDE)
APPLICATION_OVERRIDE["properties"]["kind"] = {"enum": [APPLICATION_KIND]}
OVERRIDE_SCHEMA = {"anyOf": [OVERRIDE, APPLICATION_OVERRIDE]}
SOURCE_CHANNEL = _object({"channel_id": ID,
    "candidate_id": {"anyOf": [ID, {"type": "null"}]},
    "counter_tag": ID, "shared_group": ID,
    "application_mode": {"enum": ["sequence", "no_icd", "no_application", "pending", "unknown"]},
    "verification": VERIFICATION, "research_report": RESEARCH_REPORT,
    "application_adapter": APPLICATION_ADAPTER_AUDIT,
    "application_rule_audit": APPLICATION_RULE_AUDIT}, ["channel_id", "candidate_id"])
SOURCE_SCHEMA = _object({"action_event_profile_id": ID, "version_line": {"enum": ["release", "beta"]},
    "selection": SELECTION, "origin_reference_id": HASH,
    "channels": {"type": "array", "minItems": 1, "maxItems": 64, "items": SOURCE_CHANNEL},
    "purpose": {"enum": ["calibrated_periodic_attack"]}, "status": STATUS, "source": SOURCE})
SOURCES_SCHEMA = {"type": "array", "maxItems": 32, "items": SOURCE_SCHEMA}

REFRESH_ACTION_START_TRIGGER = _object({
    "kind": {"enum": ["action_start"]},
    "action_id": ID,
    "phase": {"enum": ["pre", "post"]},
})
REFRESH_ACTION_END_TRIGGER = _object({
    "kind": {"enum": ["action_end"]},
    "action_id": ID,
    "phase": {"enum": ["post"]},
    "order": {"type": "integer", "minimum": -10**9, "maximum": 10**9},
})
_REFRESH_TRIGGER_COMMON = {
    "id": ID,
    "operation": {"enum": [REFRESH_OPERATION]},
    "trigger": {"anyOf": [REFRESH_ACTION_START_TRIGGER, REFRESH_ACTION_END_TRIGGER]},
    "snapshot_policy": {"enum": [REFRESH_SNAPSHOT_POLICY]},
    "status": {"enum": ["confirmed"]},
    "source": SOURCE,
}
# Public plans may name an authored native profile directly, or name the
# reviewed dispatch declaration that privately materializes one.  The latter
# deliberately uses the public ability identity: callers must never reproduce
# the compiler's hidden ``dispatch.<hash>`` profile prefix.
REFRESH_TRIGGER = {"anyOf": [
    _object({**_REFRESH_TRIGGER_COMMON, "action_event_profile_id": ID}),
    _object({**_REFRESH_TRIGGER_COMMON, "action_dispatch_ability_id": ID}),
]}
REFRESH_TRIGGERS_SCHEMA = {
    "type": "array", "maxItems": 64, "items": REFRESH_TRIGGER,
}


def profile_channels(profile):
    from .finite_action_schema import KIND as FINITE_APPLICATION_KIND
    return profile["channels"] if profile.get("kind") in (
        *MULTI_KINDS, FINITE_APPLICATION_KIND) else [profile]


def periodic_attack_capabilities():
    from .finite_action_schema import KIND as FINITE_APPLICATION_KIND
    from .finite_action_schema import PROFILE as FINITE_APPLICATION_PROFILE
    return {"compiler": COMPILER, "field": "periodic_attack_sources", "query_action": "periodic_attack_profile",
        "profile_kind": KIND, "profile_kinds": [*MULTI_KINDS, FINITE_APPLICATION_KIND],
        "source_backed_profile_kinds": [*MULTI_KINDS, FINITE_APPLICATION_KIND],
        "profile_contracts": {
            KIND: {"ruleset": "gauge-freeze-reference-v1", "contact": "required"},
            APPLICATION_KIND: copy.deepcopy(APPLICATION_CONTRACT),
            FINITE_APPLICATION_KIND: {
                "contract_id": FINITE_APPLICATION_KIND + ":1",
                "ruleset": "gauge-swirl-dynamic-v3",
                "emission": "bounded_finite_damage_application",
                "timing_authority": "reviewed_steps",
                "source_timer": "identity_only; periodic_timer_not_exact is optional only for this kind",
                "lifecycle": "not_applicable; finite steps own the complete occurrence set",
            },
        },
        "schema": copy.deepcopy(SOURCES_SCHEMA), "profile_schema": copy.deepcopy(PROFILE_SCHEMA),
        "refresh_trigger_field": "periodic_refresh_triggers",
        "refresh_trigger_schema": copy.deepcopy(REFRESH_TRIGGERS_SCHEMA),
        "profile_schemas": {KIND: copy.deepcopy(PROFILE), APPLICATION_KIND: copy.deepcopy(APPLICATION_PROFILE),
                            FINITE_APPLICATION_KIND: copy.deepcopy(FINITE_APPLICATION_PROFILE)},
        "override_schema": copy.deepcopy(OVERRIDE_SCHEMA), "report_field": "periodic_attack_compilations",
        "confirmation": "action_event_profiles.accepted_profile_fingerprint",
        "dispatch_mode": "calibrated", "whole_source_status": "partial", "action_creation_association": "unproven",
        "dispatch_semantics_status": "not_proven", "limits": {"max_sources": 32, "max_channels": 8,
            "max_count": 256, "max_refresh_triggers": 64,
            "max_input_bytes": 128 * 1024, "max_receipt_bytes": MAX_RECEIPT_BYTES},
        "per_profile_kind_limits": {
            KIND: {"max_channels": 8, "max_occurrences": 256},
            APPLICATION_KIND: {"max_channels": 8, "max_occurrences": 256},
            FINITE_APPLICATION_KIND: {"max_channels": 64, "max_occurrences": 256},
        },
        "scope": "classic单ATK；旧多接触v1仍限C0/10级Q并要求冻结contact；新多附着v2采用精确命座与1–15级有效天赋等级，按来源一致性支持skill/burst，使用gauge-swirl-dynamic-v3且禁止contact；仅来源周期，实际时序/建立/结束显式校准；逐通道附着身份不得按动作类别推断",
        "application_modes": {
            "sequence": "该通道尝试附着并按counter_tag计数、按数据库rule_group执行序列",
            "no_icd": "该通道尝试附着且每次允许，不创建counter_tag/rule_group计数状态",
            "no_application": "该通道仍造成伤害但不尝试元素附着，不消费GU或ICD状态",
            "pending": "该通道附着模式尚未逐段核实，阻止确定性装配"},
        "application_cadence": {
            "kind": APPLICATION_CADENCE_KIND,
            "scope": "channel_generation",
            "semantics": "只按该代该通道实际命中的接触计数；从first_attempt_ordinal起每period_hits次尝试一次附着。它不替代共享/计时ICD。",
            "miss": "未命中不推进节律位置，也不消费ICD。",
            "replacement": "新generation从第1个实际命中重新计数；共享ICD默认连续。",
        },
        "application_adapter": {
            "contract_id": "application-adapter-v1",
            "declaration_schema": copy.deepcopy(APPLICATION_ADAPTER),
            "baseline": "每个动作派发周期component公开base_application_fingerprint与base_rule",
            "projection": "仅修改精确通道的最终附着模式/counter_tag/尝试门控；元素、GU和DB rule_group不可覆盖",
            "receipt": "保留base/effective规则、数据库身份、差异标记与adapter指纹",
        },
        "external_refresh": {
            "operation": REFRESH_OPERATION,
            "execution": "compile_time_planned",
            "profile_scope": "整份周期档案的全部通道原子替换",
            "targets": {
                "action_event_profile_id": "公开手工周期档案身份",
                "action_dispatch_ability_id": "公开受审动作派发能力身份；私有物化后唯一解析档案",
            },
            "target_receipt": "每项保留declared_target到resolved_profile_id",
            "supported_triggers": ["action_start", "action_end"],
            "source_evidence": "刷新声明、来源once动作及来源能力均须confirmed",
            "ordering": "按实际动作事件的整数微秒与order裁剪；同位置或无法证明触发后首击顺序时拒绝",
            "inactive": "replace_active在无活跃实例时记录inactive_noop且不启动实例",
            "snapshot_policy": REFRESH_SNAPSHOT_POLICY,
            "application_counter": "代内附着节律重开，共享ICD不重置",
            "runtime_boundary": "仅冻结有限轴的编译期计划；不声明StateRuntime动态联动",
            "unsupported": ["condition_event", "resource_threshold", "运行期StateRuntime命中/附着派发"],
        },
        "schedule_kinds": {
            "single": "一个明确偏移",
            "periodic": "首跳、等间隔及严格次数",
            "explicit_offsets": "1–256个严格递增的不等间隔微秒偏移",
        },
        "deprecated_application_mode_aliases": {"unknown": "pending", "missing": "pending"},
        "mode_dependencies": {
            "sequence": ["parameter", "element", "classification", "gauge", "application_identity", "attenuation"],
            "no_icd": ["parameter", "element", "classification", "gauge", "structured_verification"],
            "no_application": ["parameter", "element", "classification", "structured_verification"],
            "pending": ["per_component_application_mode_decision"]},
        "verification_policy": "数据库已确认attenuationTag可证明sequence绑定；否则sequence/no_icd/no_application均需逐通道confirmed结构化正向证据。source自由文本、动作类别和同技能其他段不能升格。",
        "verification_task": {"granularity": "one concrete periodic attack channel",
            "identity_fields": ["actor", "character_id", "slot", "skill_id", "candidate_id",
                                "action_event_profile_id", "channel_id", "prototype_hit_id",
                                "application_profile_id", "constellation", "ability_name", "version_line"],
            "includes": ["known_fields", "missing_fields", "conflicts", "local_checks",
                         "targeted_research_questions", "keywords", "acceptance_required",
                         "database_patch_targets", "research_report", "research_report_template"]},
        "research_report_contract": {"saved_field": "periodic_attack_sources[].channels[].research_report",
            "granularity": "one concrete periodic attack channel", "execution_authority": "none",
            "required_sections": ["actual_queries", "sources_checked", "adopted_findings",
                                  "rejected_findings", "remaining_unknowns", "database_patch_plan"]},
        "labels": {"periodic_attack_sources": "周期攻击来源", "channels": "攻击通道", "lifecycle": "实例生命周期",
            "origin_reference_id": "精确创建来源", "counter_tag": "附着计数标签（ICD Tag）",
            "shared_group": "旧版计数标签字段（仅兼容）", "verification": "逐攻击通道结构化证据",
            "research_report": "Agent逐通道查证、来源取舍与公共数据回填记录（无执行权）", "count": "计划次数"}}


def validate_periodic_sources(plan):
    from .action_event_schema import _check
    from .files import fingerprint
    from .schema import PlanError

    rows = plan.get("periodic_attack_sources", [])
    refreshes = plan.get("periodic_refresh_triggers", [])
    if "periodic_attack_sources" in plan and plan.get("plan_version") != 3:
        raise PlanError("周期攻击来源只适用于原生v3", "periodic_schema")
    if "periodic_refresh_triggers" in plan and plan.get("plan_version") != 3:
        raise PlanError("周期实例刷新只适用于原生v3", "periodic_schema")
    errors = _check(rows, SOURCES_SCHEMA, "periodic_attack_sources")
    errors.extend(_check(refreshes, REFRESH_TRIGGERS_SCHEMA, "periodic_refresh_triggers"))
    if errors:
        raise PlanError("周期来源结构不合法", "periodic_schema", errors[:128])
    profiles = {p["id"]: p for p in plan.get("action_event_profiles", [])}
    bound_abilities = {row["profile_id"]: row["ability_id"]
                       for row in plan.get("action_event_bindings", [])}
    ability_profiles = {}
    for binding in plan.get("action_event_bindings", []):
        ability_profiles.setdefault(binding["ability_id"], []).append(binding["profile_id"])
    dispatch_declarations = {row["ability_id"]: row
                             for row in plan.get("action_dispatch_sources", [])}
    from .finite_action_schema import KIND as FINITE_APPLICATION_KIND
    source_backed = {key: p for key, p in profiles.items()
                     if p["kind"] in (*MULTI_KINDS, FINITE_APPLICATION_KIND)}
    multi = {key: p for key, p in source_backed.items() if p["kind"] in MULTI_KINDS}
    refresh_ids = [row["id"] for row in refreshes]
    if len(set(refresh_ids)) != len(refresh_ids):
        raise PlanError("周期刷新触发身份不能重复", "periodic_schema")
    action_ids = {row["id"] for row in (plan.get("rotation_model") or {}).get("actions", [])}
    if set(refresh_ids) & action_ids:
        raise PlanError("周期刷新触发身份不能与动作或隐式建立身份冲突", "periodic_schema")
    for refresh in refreshes:
        if "action_event_profile_id" in refresh:
            profile = multi.get(refresh["action_event_profile_id"])
            if profile is None:
                raise PlanError("周期刷新须引用多通道档案", "periodic_ownership")
        else:
            ability_id = refresh["action_dispatch_ability_id"]
            declaration = dispatch_declarations.get(ability_id)
            if declaration is None:
                raise PlanError("周期刷新引用的动作派发能力没有公开声明", "periodic_ownership")
            if declaration.get("status") != "confirmed":
                raise PlanError("周期刷新引用的动作派发声明须为confirmed", "periodic_schema")
            resolved = ability_profiles.get(ability_id, [])
            if not resolved:
                # The public plan is validated before reviewed dispatch rows
                # are privately materialized.  Full target/lifecycle checks
                # run when prepare_action_dispatch_sources validates its
                # candidate containing the generated binding and profile.
                continue
            if len(resolved) != 1 or resolved[0] not in multi:
                raise PlanError("动作派发刷新目标未唯一解析为多通道档案", "periodic_ownership")
            profile = multi[resolved[0]]
        lifecycle = profile["lifecycle"]
        if (lifecycle.get("replacement_policy") != REPLACEMENT_POLICY
                or lifecycle.get("no_refresh_replacement_or_early_despawn") is not False
                or lifecycle.get("no_early_despawn") is not True):
            raise PlanError("外部刷新只允许引用完整replace_previous生命周期", "periodic_schema")
        if refresh["status"] != "confirmed":
            raise PlanError("周期刷新触发依据待确认", "periodic_schema")
    owned, owned_apps, seen = set(), set(), set()
    hits = {h["id"]: h for h in plan["manual_template"]["hits"]}
    model = plan.get("mechanics_model") or {}
    for row in rows:
        pid = row["action_event_profile_id"]
        if pid not in source_backed or pid in seen:
            raise PlanError("数据库攻击来源须唯一对应多通道或有限序列档案", "periodic_ownership")
        seen.add(pid)
        profile = source_backed[pid]
        finite = profile["kind"] == FINITE_APPLICATION_KIND
        if not finite:
            lifecycle = profile["lifecycle"]
            replacement = lifecycle.get("replacement_policy")
            if replacement is None:
                if "no_early_despawn" in lifecycle:
                    raise PlanError("旧生命周期不能单独声明提前消失字段", "periodic_schema")
            elif (replacement != REPLACEMENT_POLICY
                  or lifecycle.get("no_refresh_replacement_or_early_despawn") is not False
                  or lifecycle.get("no_early_despawn") is not True):
                raise PlanError("刷新替换生命周期必须明确旧实例会被替换且不会因其他原因提前消失", "periodic_schema")
        if ("slot" in row["selection"]) == ("skill_id" in row["selection"]):
            raise PlanError("周期来源须明确slot或skill_id二选一", "periodic_schema")
        channels = profile["channels"]
        channel_ids = [c["channel_id"] for c in channels]
        mapped = [c["channel_id"] for c in row["channels"]]
        candidates = [c["candidate_id"] for c in row["channels"] if c["candidate_id"] is not None]
        if not channels or len(set(channel_ids)) != len(channels) or len(set(mapped)) != len(mapped) or set(mapped) != set(channel_ids) or len(set(candidates)) != len(candidates):
            raise PlanError("周期通道与候选映射须非空且一一对应", "periodic_ownership")
        for mapping in row["channels"]:
            if mapping.get("counter_tag") and mapping.get("shared_group"):
                raise PlanError("周期通道计数标签不得同时使用counter_tag与旧shared_group字段", "periodic_schema")
            raw_mode = mapping.get("application_mode")
            mode = "pending" if raw_mode in (None, "unknown") else raw_mode
            counter_tag = mapping.get("counter_tag") or mapping.get("shared_group")
            if mode == "sequence" and not counter_tag:
                raise PlanError("周期序列附着必须声明逐通道计数标签", "periodic_schema")
            if mode != "sequence" and counter_tag and raw_mode is not None:
                raise PlanError("周期非序列附着模式不得声明计数标签", "periodic_schema")
            if mapping.get("verification") and len(set(mapping["verification"]["confirmed_fields"])) != len(mapping["verification"]["confirmed_fields"]):
                raise PlanError("周期逐段证据confirmed_fields不得重复", "periodic_schema")
            adapter = mapping.get("application_adapter")
            if adapter is not None:
                declaration = adapter["declaration"]
                icd_rule = declaration.get("icd_rule") or {"mode": "inherit"}
                dispatch = dispatch_declarations.get(bound_abilities.get(pid)) or {}
                declared = next((item.get("application_adapter")
                                 for item in dispatch.get("scenario_calibrations", [])
                                 if item.get("component_id") == mapping["channel_id"]), None)
                if (adapter["component_id"] != mapping["channel_id"]
                        or adapter["accepted_base_fingerprint"] != adapter["base_application_fingerprint"]
                        or adapter["adapter_fingerprint"] != fingerprint(declaration)
                        or declaration["accepted_base_fingerprint"] != adapter["base_application_fingerprint"]
                        or declaration["status"] != "confirmed"
                        or ((declaration["mode"] not in ("inherit", "pending")
                             or declaration["attempt_gate"]["mode"] != "inherit"
                             or icd_rule["mode"] != "inherit")
                            and (declaration.get("verification") or {}).get("component_scope")
                            != mapping["channel_id"])
                        or declared != declaration):
                    raise PlanError("附着适配审计与通道、基底或声明指纹不一致", "periodic_schema")
                effective = adapter["effective_rule"]
                normalized_mode = "pending" if raw_mode in (None, "unknown") else raw_mode
                channel = next(c for c in channels if c["channel_id"] == mapping["channel_id"])
                cadence = channel.get("application_cadence")
                gate = ({"mode": "none", "period_hits": None, "first_attempt_ordinal": None}
                        if cadence is None else
                        {"mode": cadence["kind"], "period_hits": cadence["period_hits"],
                         "first_attempt_ordinal": cadence["first_attempt_ordinal"]})
                if (effective != {"mode": normalized_mode, "counter_tag": counter_tag,
                                  "attempt_gate": gate}
                        or declaration["mode"] == "inherit" and declaration["counter_tag"] is not None):
                    raise PlanError("附着适配最终规则与周期通道投影不一致", "periodic_schema")
                for rule in (adapter["base_rule"], effective):
                    rule_gate = rule["attempt_gate"]
                    if ((rule["mode"] == "sequence") != (rule["counter_tag"] is not None)
                            or (rule_gate["mode"] == "none") != (
                                rule_gate["period_hits"] is None
                                and rule_gate["first_attempt_ordinal"] is None)
                            or (rule_gate["mode"] == APPLICATION_CADENCE_KIND
                                and (rule_gate["period_hits"] is None
                                     or rule_gate["first_attempt_ordinal"] is None
                                     or rule_gate["first_attempt_ordinal"] > rule_gate["period_hits"]))):
                        raise PlanError("附着适配规则快照不自洽", "periodic_schema")
            rule_audit = mapping.get("application_rule_audit")
            if adapter is not None and rule_audit is None:
                raise PlanError("附着适配必须携带完整基线与最终规则审计", "periodic_schema")
            if adapter is None and rule_audit is not None:
                raise PlanError("附着规则私有审计不能脱离动作派发适配单独声明", "periodic_schema")
            if rule_audit is not None:
                channel = next(c for c in channels if c["channel_id"] == mapping["channel_id"])
                cadence = channel.get("application_cadence")
                gate = ({"mode": "none", "period_hits": None, "first_attempt_ordinal": None}
                        if cadence is None else
                        {"mode": cadence["kind"], "period_hits": cadence["period_hits"],
                         "first_attempt_ordinal": cadence["first_attempt_ordinal"]})
                effective = {"mode": "pending" if raw_mode in (None, "unknown") else raw_mode,
                             "counter_tag": counter_tag, "attempt_gate": gate}
                identity = rule_audit["database_identity"]
                database_tag = identity["counter_tag"] if identity["status"] == "ready" else None
                divergence = database_tag is not None and (
                    effective["mode"] != "sequence" or effective["counter_tag"] != database_tag)
                channel_schedule = channel.get("schedule")
                if (rule_audit["component_id"] != mapping["channel_id"]
                        or rule_audit["emission_kind"] != (
                            "periodic" if channel_schedule is not None
                            and channel_schedule["kind"] == "periodic" else "fixed")
                        or rule_audit["effective_rule"] != effective
                        or rule_audit["identity_divergence"] != divergence
                        or (adapter is None and (rule_audit["adapter_fingerprint"] is not None
                                                 or rule_audit["base_rule"] != effective))
                        or (adapter is not None and (
                            rule_audit["adapter_fingerprint"] != adapter["adapter_fingerprint"]
                            or rule_audit["base_application_fingerprint"]
                            != adapter["base_application_fingerprint"]
                            or rule_audit["base_rule"] != adapter["base_rule"]))):
                    raise PlanError("附着规则公开审计与通道基线、最终规则或数据库身份不一致", "periodic_schema")
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
                        raise PlanError("周期通道局部ICD规则审计与声明或数据库基线不一致",
                                        "periodic_schema")
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
                        raise PlanError("周期通道局部ICD派生身份与执行语义不一致",
                                        "periodic_schema")
        for channel in channels:
            schedule = channel.get("schedule")
            cadence = channel.get("application_cadence")
            if cadence is not None:
                if (schedule["kind"] != "periodic"
                        or cadence["first_attempt_ordinal"] > cadence["period_hits"]
                        or cadence["status"] == "pending"):
                    raise PlanError("附着节律须属于已核实周期通道，且首次位置不能超过周期长度", "periodic_schema")
                mapping = next(m for m in row["channels"] if m["channel_id"] == channel["channel_id"])
                mode = "pending" if mapping.get("application_mode") in (None, "unknown") else mapping["application_mode"]
                if mode not in ("sequence", "no_icd"):
                    raise PlanError("附着节律只能门控实际会尝试附着的周期通道", "periodic_schema")
            if schedule is not None and schedule["kind"] == "periodic":
                derived = schedule.get("derive_count")
                if schedule["count"] is not None and derived is not None:
                    raise PlanError("周期次数已明确时不能重复声明次数派生策略", "periodic_schema")
            hid, app = channel["prototype_hit_id"], channel["application_profile_id"]
            if hid not in hits or hid in owned or app in owned_apps:
                raise PlanError("周期来源原型/附着槽位须唯一存在", "periodic_ownership")
            owned.add(hid)
            owned_apps.add(app)
            if set(hits[hid]) & {"element", "multiplier"}:
                raise PlanError("周期来源拥有的element/multiplier须从原生省略", "duplicate_delivery")
            if any(s["hit_id"] == hid for s in plan.get("hit_parameter_sources", [])):
                raise PlanError("周期原型不能重复交付倍率来源", "duplicate_delivery")
            if (any(p["id"] == app for p in (model.get("elemental_model") or {}).get("profiles", []))
                    or any(s["profile_id"] == app for s in plan.get("elemental_sources", []))):
                raise PlanError("周期附着槽位整项须从原生省略", "duplicate_delivery")
            for other in profiles.values():
                for other_channel in profile_channels(other):
                    if other is profile and other_channel is channel:
                        continue
                    if other_channel["prototype_hit_id"] == hid or other_channel["application_profile_id"] == app:
                        raise PlanError("周期通道原型/附着槽位不能被其他档案共用", "duplicate_delivery")
            for name in ("hit_events", "hit_patterns", "application_events"):
                if any((r.get("application") or {}).get("profile_id") == app for r in model.get(name, [])):
                    raise PlanError("手工事件不得借用周期来源附着槽位", "duplicate_delivery")
    if seen != set(source_backed):
        raise PlanError("每份多通道或有限序列档案须恰有一项数据库攻击来源", "periodic_ownership")
    return owned
