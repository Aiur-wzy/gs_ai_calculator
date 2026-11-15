"""Atomic native scaffolding for the existing calibrated multi-contact pipeline."""
from __future__ import annotations

import copy
from decimal import InvalidOperation

from .action_event_schema import FIELDS, _check
from .application_evidence import (
    RESEARCH_REPORT,
    VERIFICATION,
    has_verified_application_evidence,
)
from .composer import compose_equipment_plan
from .files import fingerprint, input_view, json_bytes
from .periodic_schema import (
    CHANNEL,
    ID,
    KIND,
    PROFILE,
    SELECTION,
    SOURCE_SCHEMA,
    _object,
)
from .schema import PlanError, _json_guard, validate_plan

MAX_REQUEST_BYTES = 32 * 1024
CONTRACT = "source-periodic-assembly-v1"
_CHANNEL_FIELDS = ("schedule", "contact_profile_id", "default_landed", "self_sampling")
REQUEST_SCHEMA = _object({
    "schema_version": {"type": "integer", "enum": [1]},
    "profile_id": ID,
    "ability_ids": {"type": "array", "minItems": 1, "maxItems": 64, "items": ID},
    **{k: SOURCE_SCHEMA["properties"][k] for k in ("version_line", "origin_reference_id", "status", "source")},
    "selection": SELECTION,
    "damage_type_ids": {"type": "array", "minItems": 1, "maxItems": 1,
                        "items": {"type": "string", "enum": ["damage.elemental_burst"]}},
    "scaling_stat": {"type": "string", "enum": ["atk"]},
    "lifecycle": PROFILE["properties"]["lifecycle"],
    "channels": {"type": "array", "minItems": 1, "maxItems": 8, "items": _object({
        "candidate_id": ID,
        **{k: CHANNEL["properties"][k] for k in _CHANNEL_FIELDS},
        "counter_tag": ID, "shared_group": ID,
        "application_mode": {"enum": ["sequence", "no_icd", "no_application", "pending"]},
        "verification": VERIFICATION, "research_report": RESEARCH_REPORT,
    }, ["candidate_id", *_CHANNEL_FIELDS, "application_mode"])},
})


def periodic_source_assembly_capabilities():
    return {
        "contract": CONTRACT, "schema_version": 1, "request_schema": copy.deepcopy(REQUEST_SCHEMA),
        "limits": {"max_request_bytes": MAX_REQUEST_BYTES, "max_abilities": 64, "max_channels": 8},
        "profile_kind": KIND, "plan_version": 3,
        "confirmation": "action_event_profiles.accepted_profile_fingerprint", "auto_accept": False,
        "dispatch_mode": "calibrated", "whole_source_status": "partial",
        "action_creation_association": "unproven", "dispatch_semantics_status": "not_proven",
        "generated_fields": ["manual_template.hits", "action_event_profiles",
                             "action_event_bindings", "periodic_attack_sources"],
        "scope": "现有C0有效10级Q、classic单ATK、多通道来源与显式校准；不推断完整技能派发",
        "application_identity": "新装配逐通道写counter_tag；shared_group只兼容旧请求。sequence可由数据库已确认attenuationTag直接举证；数据库身份缺失时才须逐通道结构化正向证据，规则组只读自数据库attenuationGroup",
        "research_report_input": "每个通道可选携带审计记录；原样保存到periodic_attack_sources[].channels[].research_report，execution_authority固定为none且不替代verification",
        "application_modes": {"sequence": "按计数标签与数据库规则组执行",
                              "no_icd": "逐次附着，不建立计数状态",
                              "no_application": "保留伤害但不尝试附着",
                              "pending": "创建可继续核实的完整草稿与逐通道工作单，不交付确定性结果"},
        "id_policy": {
            "hash": "sha256", "encoding": "UTF-8", "json": "compact_sorted_keys_ensure_ascii_false",
            "payload": [CONTRACT, "<profile_id>", "<candidate_id>"],
            "prefixes": {"channel_id": "assembly.channel.", "prototype_hit_id": "assembly.hit.",
                         "application_profile_id": "assembly.application."},
        },
        "conflict_policy": "exact_existing_or_reject; no_deletion_or_migration",
    }


def _validate_request(request):
    try:
        _json_guard(request, "request")
        size = len(json_bytes(request))
        errors = _check(request, REQUEST_SCHEMA, "request")
    except (TypeError, ValueError, OverflowError, RecursionError, InvalidOperation) as exc:
        raise PlanError("多通道装配请求不是有界合法JSON", "periodic_source_assembly_schema") from exc
    if size > MAX_REQUEST_BYTES:
        raise PlanError("多通道装配请求超过32KiB", "periodic_source_assembly_limit")
    if errors:
        raise PlanError("多通道装配请求结构不合法", "periodic_source_assembly_schema", errors[:128])
    if (len(set(request["ability_ids"])) != len(request["ability_ids"])
            or len({c["candidate_id"] for c in request["channels"]}) != len(request["channels"])):
        raise PlanError("能力和攻击候选不能重复", "periodic_source_assembly_schema")
    for channel in request["channels"]:
        if channel.get("counter_tag") and channel.get("shared_group"):
            raise PlanError("周期计数标签不得同时使用counter_tag与旧shared_group字段", "periodic_source_assembly_schema")
        counter_tag = channel.get("counter_tag") or channel.get("shared_group")
        if channel["application_mode"] == "sequence" and not counter_tag:
            raise PlanError("周期sequence通道必须提供counter_tag", "periodic_source_assembly_schema")
        if channel["application_mode"] != "sequence" and counter_tag:
            raise PlanError("周期非sequence通道不得提供counter_tag", "periodic_source_assembly_schema")
        # A sequence declaration may be admitted later by the freshly selected
        # database candidate's exact attenuationTag.  The request validator has
        # no database context, so requiring a duplicate verification here would
        # reject a source the compiler can prove.  Explicit no-ICD and
        # no-application overrides still require per-channel positive evidence.
        if (channel["application_mode"] in ("no_icd", "no_application")
                and not has_verified_application_evidence(channel, channel["application_mode"])):
            raise PlanError("周期通道需要逐攻击组件结构化正向证据", "periodic_source_assembly_schema")
        if channel["application_mode"] == "pending" and channel.get("verification") is not None:
            raise PlanError("周期pending草稿不能把尚未采纳的证据写成confirmed verification", "periodic_source_assembly_schema")
    if ("slot" in request["selection"]) == ("skill_id" in request["selection"]):
        raise PlanError("须明确slot或skill_id且只能一种", "periodic_source_assembly_schema")


def _conflict(message):
    raise PlanError(message, "periodic_source_assembly_conflict")


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _ensure_unoccupied(plan, ids):
    # Match definitions and structural selectors, not strings in descriptions.
    scalar_refs = {"id", "channel_id", "hit_id", "prototype_hit_id", "application_profile_id", "profile_id",
                   "action_event_profile_id", "event_id", "self_snapshot_event"}
    list_refs = {"hit_ids", "include_hit_ids", "exclude_hit_ids", "event_ids"}
    for obj in _objects(input_view(plan)):
        if any(isinstance(obj.get(k), str) and obj[k] in ids for k in scalar_refs):
            _conflict("生成ID已被原方案对象或引用占用；不自动覆盖或收编")
        if any(isinstance(obj.get(k), list) and any(isinstance(v, str) and v in ids for v in obj[k])
               for k in list_refs):
            _conflict("生成ID已被原方案选择器引用；不自动改变收益作用范围")


def _canonical(value, *, profile=False):
    result = copy.deepcopy(value)
    if profile:
        result["accepted_profile_fingerprint"] = None
    if isinstance(result.get("channels"), list):
        result["channels"].sort(key=lambda c: c["channel_id"])
    return result


def _same(left, right):
    return json_bytes(left) == json_bytes(right)


def _source_summary(report, profile_id, candidate_ids):
    record = next((r for r in report.get("periodic_attack_compilations", [])
                   if r["action_event_profile_id"] == profile_id), {})
    query = record.get("query") or {}
    mode = query.get("mode")
    omitted = None
    if mode == "selected" and isinstance(query.get("omitted_candidates"), list):
        omitted = sorted(query["omitted_candidates"])
    elif mode == "channel_discovery" and isinstance(query.get("candidates"), list):
        omitted = sorted(c["candidate_id"] for c in query["candidates"] if c["candidate_id"] not in candidate_ids)
    return {"status": record.get("status", "pending"), "query_mode": mode,
            "requested_candidate_ids": list(candidate_ids), "omitted_candidate_ids": omitted,
            "omission_status": "known" if omitted is not None else "not_evaluated",
            "whole_source_status": "partial", "action_creation_association": "unproven",
            "dispatch_semantics_status": "not_proven"}


def assemble_periodic_source(plan, request, db_path):
    """Return a native proposal and one fresh compose; never accept, save or migrate."""
    _validate_request(request)
    validate_plan(plan)
    if plan["plan_version"] != 3:
        raise PlanError("多通道装配仅适用于原生v3", "periodic_source_assembly_schema")
    rotation, model = plan.get("rotation_model"), plan.get("mechanics_model")
    if not isinstance(rotation, dict) or not isinstance(model, dict):
        raise PlanError("多通道装配须先提供动作和机制模型", "reference")
    abilities = {a["id"]: a for a in rotation["abilities"]}
    ability_ids = sorted(request["ability_ids"])
    if any(aid not in abilities for aid in ability_ids):
        raise PlanError("多通道装配引用不存在的能力", "reference")
    selected = [abilities[aid] for aid in ability_ids]
    actor, variant = selected[0]["character"], selected[0].get("variant")
    if (variant not in ("default", "tap", "hold") or any(a["character"] != actor or a.get("variant") != variant
            or a["action_type_id"] != "action.elemental_burst" for a in selected)):
        raise PlanError("所选能力须同角色、同明确变体且为元素爆发", "periodic_source_assembly_scope")
    if actor not in {c["id"] for c in plan["manual_template"]["characters"]}:
        raise PlanError("多通道装配能力引用不存在的角色", "reference")
    character_ref = (plan.get("equipment_inputs", {}).get(actor) or {}).get("character_ref")
    if character_ref and (character_ref["db_character_id"] != request["selection"]["character_id"]
                          or character_ref["version_line"] != request["version_line"]):
        raise PlanError("所选来源与角色已声明数据库身份不符", "periodic_source_assembly_scope")
    contacts = {p["id"] for p in (model.get("elemental_model") or {}).get("contact_profiles", [])}
    if any(c["contact_profile_id"] not in contacts for c in request["channels"]):
        raise PlanError("每个通道须引用已有接触档案", "reference")
    action_ids = {a["id"] for a in rotation["actions"] if a["ability_id"] in ability_ids}
    for collection in (model.get("hit_events", []), model.get("hit_patterns", []),
                       model.get("application_events", []), rotation.get("action_hit_links", [])):
        if any(row.get("action_id") in action_ids for row in collection):
            _conflict("所选能力的实际动作已有手工命中/模式/附着/动作命中关联；不自动删除")

    pid = request["profile_id"]
    hits, channels, mappings, identities = [], [], [], []
    for channel in sorted(request["channels"], key=lambda c: c["candidate_id"]):
        candidate = channel["candidate_id"]
        suffix = fingerprint([CONTRACT, pid, candidate])
        cid, hid, app = (prefix + suffix for prefix in ("assembly.channel.", "assembly.hit.", "assembly.application."))
        kind = channel["schedule"]["kind"]
        hits.append({"id": hid, "damage_type_ids": copy.deepcopy(request["damage_type_ids"]), "t": 0,
                     "character": actor, "skill": "元素爆发", "hit_name": "周期接触" if kind == "periodic" else "单次接触",
                     "damage_system": "classic", "scaling_stat": request["scaling_stat"], "count": 1})
        channels.append({"channel_id": cid, "prototype_hit_id": hid, "application_profile_id": app,
                         **{k: copy.deepcopy(channel[k]) for k in _CHANNEL_FIELDS}})
        mapping = {"channel_id": cid, "candidate_id": candidate,
                   "application_mode": channel["application_mode"]}
        if "verification" in channel:
            mapping["verification"] = copy.deepcopy(channel["verification"])
        if "research_report" in channel:
            mapping["research_report"] = copy.deepcopy(channel["research_report"])
        if channel["application_mode"] == "sequence":
            mapping["counter_tag"] = channel.get("counter_tag") or channel.get("shared_group")
        mappings.append(mapping)
        identities.append({"candidate_id": candidate, "channel_id": cid, "prototype_hit_id": hid,
                           "application_profile_id": app, "schedule_kind": kind})
    profile = {"id": pid, "kind": KIND, "variant": variant, "lifecycle": copy.deepcopy(request["lifecycle"]),
               "channels": channels, "accepted_profile_fingerprint": None}
    source = {"action_event_profile_id": pid, "purpose": "calibrated_periodic_attack", "channels": mappings,
              **{k: copy.deepcopy(request[k]) for k in ("version_line", "selection", "origin_reference_id", "status", "source")}}
    bindings = [{"ability_id": aid, "profile_id": pid} for aid in ability_ids]
    existing = [p for p in plan.get("action_event_profiles", []) if p["id"] == pid]
    related_bindings = [b for b in plan.get("action_event_bindings", [])
                        if b["ability_id"] in ability_ids or b["profile_id"] == pid]
    if existing:
        hit_ids = {h["id"] for h in hits}
        actual_hits = [h for h in plan["manual_template"]["hits"] if h["id"] in hit_ids]
        actual_sources = [s for s in plan.get("periodic_attack_sources", []) if s["action_event_profile_id"] == pid]
        if not (_same(_canonical(existing[0], profile=True), _canonical(profile, profile=True))
                and _same(sorted(actual_hits, key=lambda h: h["id"]), sorted(hits, key=lambda h: h["id"]))
                and _same([_canonical(s) for s in actual_sources], [_canonical(source)])
                and _same(sorted(related_bindings, key=lambda b: b["ability_id"]), bindings)):
            _conflict("该档案ID已有不同内容或归属；只允许完整一致的重复装配")
        draft = copy.deepcopy(plan)
        status = "existing"
    else:
        if related_bindings:
            _conflict("能力或档案已有派发绑定；不自动替换")
        ids = {pid} | {row[key] for row in identities for key in ("channel_id", "prototype_hit_id", "application_profile_id")}
        _ensure_unoccupied(plan, ids)
        draft = copy.deepcopy(plan)
        draft["manual_template"]["hits"].extend(hits)
        for field in FIELDS:
            draft.setdefault(field, [])
        draft["action_event_profiles"].append(profile)
        draft["action_event_bindings"].extend(bindings)
        draft.setdefault("periodic_attack_sources", []).append(source)
        draft["resolution"] = {"state": "pending"}
        draft["resolved_template"] = None
        status = "created"
    validate_plan(draft)
    # The existing composer owns the single read transaction, source preparation,
    # semantic acceptance and pending withdrawal. No preliminary DB query here.
    report = compose_equipment_plan(draft, db_path)
    created = status == "created"
    assembly = {"contract": CONTRACT, "schema_version": 1, "status": status, "profile_id": pid,
                "actor": actor, "variant": variant, "ability_ids": ability_ids, "channels": identities,
                "created": {"hits": len(hits) if created else 0, "profiles": int(created),
                            "bindings": len(bindings) if created else 0, "sources": int(created),
                            "channels": len(channels) if created else 0},
                "source_summary": _source_summary(report, pid, [c["candidate_id"] for c in identities]),
                "base_input_fingerprint": fingerprint(input_view(plan)),
                "input_fingerprint": fingerprint(input_view(draft))}
    return {"plan": draft, "assembly": assembly, "report": report}
