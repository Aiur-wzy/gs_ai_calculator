"""Atomic native scaffolding for the existing calibrated attack-source pipeline."""
from __future__ import annotations

import copy
from decimal import InvalidOperation

from .action_event_schema import (
    FIELDS,
    ID,
    KIND,
    PROFILE,
    SOURCE,
    STATUS,
    _check,
    _object,
)
from .application_evidence import RESEARCH_REPORT
from .attack_metadata_schema import (
    SELECTION,
    SOURCE_SCHEMA,
    VERIFICATION,
)
from .composer import compose_equipment_plan
from .files import fingerprint, input_view, json_bytes
from .schema import PlanError, _json_guard, validate_plan

MAX_REQUEST_BYTES = 32 * 1024
CONTRACT = "source-action-assembly-v1"
REQUEST_APPLICATION_MODES = {"enum": ["sequence", "no_icd", "no_application", "pending"]}
REQUEST_SCHEMA = _object({
    "schema_version": {"type": "integer", "enum": [1]},
    "profile_id": ID,
    "ability_ids": {"type": "array", "minItems": 1, "maxItems": 64, "items": ID},
    "version_line": SOURCE_SCHEMA["properties"]["version_line"],
    "selection": SELECTION,
    "candidate_id": SOURCE_SCHEMA["properties"]["candidate_id"],
    "damage_type_ids": {"type": "array", "minItems": 1, "maxItems": 32, "items": ID},
    "scaling_stat": {"enum": ["atk"]},
    **{k: PROFILE["properties"][k] for k in (
        "calibration", "contact_profile_id", "default_landed", "self_sampling")},
    "counter_tag": ID, "shared_group": ID, "verification": VERIFICATION,
    "research_report": RESEARCH_REPORT,
    "application_mode": REQUEST_APPLICATION_MODES, "status": STATUS, "source": SOURCE,
}, ["schema_version", "profile_id", "ability_ids", "version_line", "selection", "candidate_id",
    "damage_type_ids", "scaling_stat", "calibration", "contact_profile_id", "default_landed",
    "self_sampling", "application_mode", "status", "source"])
_HIT_LABELS = {"default": "默认形态命中", "tap": "点按命中", "hold": "长按命中"}


def attack_source_assembly_capabilities():
    return {
        "contract": CONTRACT, "schema_version": 1,
        "request_schema": copy.deepcopy(REQUEST_SCHEMA),
        "limits": {"max_request_bytes": MAX_REQUEST_BYTES, "max_abilities": 64},
        "profile_kind": KIND, "plan_version": 3,
        "confirmation": "action_event_profiles.accepted_profile_fingerprint",
        "auto_accept": False, "dispatch_mode": "calibrated", "skill_coverage": "partial",
        "generated_fields": ["manual_template.hits", "action_event_profiles",
                             "action_event_bindings", "attack_metadata_sources"],
        "scope": "现有C0有效10级E、classic单ATK单接触；沿用来源和校准守卫；计数标签按具体攻击组件核实，不从动作类别推断",
        "application_identity": "counter_tag是计数标签（ICD Tag）；旧shared_group只兼容读取；attenuationGroup只选择计时/序列规则模板",
        "research_report_input": "可选逐攻击分量审计记录；原样保存到attack_metadata_sources[].research_report，execution_authority固定为none且不替代verification",
        "override_policy": "数据库缺attenuationTag时，sequence的外部逐段声明须status=confirmed且携带结构化verification；自由文本/assumed只生成待核实回执",
        "application_modes": {"sequence": "按counter_tag与rule_group执行有限序列",
                              "no_icd": "每次命中均允许附着，不声明计数标签",
                              "no_application": "该攻击组件不尝试附着",
                              "pending": "具体攻击组件尚未核实，阻止确定性装配"},
        "deprecated_application_mode_aliases": {"unknown": "pending"},
        "legacy": "已有attack_metadata_sources缺application_mode或使用unknown时迁移为pending；新装配请求必须显式声明canonical application_mode",
        "id_policy": "assembly.hit./assembly.application. + sha256(JSON(profile_id))",
        "conflict_policy": "exact_existing_or_reject; no_deletion_or_migration",
    }


def _validate_request(request):
    try:
        _json_guard(request, "request")
        size = len(json_bytes(request))
        errors = _check(request, REQUEST_SCHEMA, "request")
    except (TypeError, ValueError, OverflowError, RecursionError, InvalidOperation) as exc:
        raise PlanError("来源装配请求不是有界合法JSON", "attack_source_assembly_schema") from exc
    if size > MAX_REQUEST_BYTES:
        raise PlanError("来源装配请求超过32KiB", "attack_source_assembly_limit")
    if errors:
        raise PlanError("来源装配请求结构不合法", "attack_source_assembly_schema", errors[:128])
    for key in ("ability_ids", "damage_type_ids"):
        if len(set(request[key])) != len(request[key]):
            raise PlanError("来源装配选择不能重复", "attack_source_assembly_schema", {"field": key})
    selection = request["selection"]
    if ("slot" in selection) == ("skill_id" in selection):
        raise PlanError("须明确slot或skill_id且只能一种", "attack_source_assembly_schema")
    mode = request["application_mode"]
    if request.get("counter_tag") and request.get("shared_group"):
        raise PlanError("计数标签不得同时使用counter_tag与旧shared_group字段", "attack_source_assembly_schema")
    counter_tag = request.get("counter_tag") or request.get("shared_group")
    if mode == "sequence" and not (counter_tag or "").strip():
        raise PlanError("序列附着模式必须声明附着计数标签（ICD Tag）", "attack_source_assembly_schema")
    if mode != "sequence" and counter_tag:
        raise PlanError("非序列附着模式不得同时声明计数标签", "attack_source_assembly_schema")
    if mode == "pending" and request["status"] != "pending":
        raise PlanError("待核实附着模式必须保持pending", "attack_source_assembly_schema")


def _conflict(message):
    raise PlanError(message, "attack_source_assembly_conflict")


def _objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _objects(child)


def _same(left, right):
    # JSON identity also distinguishes bool from number; no Python True == 1 alias.
    return json_bytes(left) == json_bytes(right)


def _ensure_unoccupied(plan, ids):
    """Reject definitions and structural references; do not search prose text."""
    scalar_refs = {"id", "hit_id", "prototype_hit_id", "application_profile_id", "profile_id",
                   "action_event_profile_id", "event_id", "self_snapshot_event"}
    list_refs = {"hit_ids", "include_hit_ids", "exclude_hit_ids", "event_ids"}
    for obj in _objects(input_view(plan)):
        if any(isinstance(obj.get(k), str) and obj[k] in ids for k in scalar_refs):
            _conflict("生成ID已被原方案对象或引用占用；不自动覆盖或收编")
        if any(isinstance(obj.get(k), list) and any(isinstance(v, str) and v in ids for v in obj[k])
               for k in list_refs):
            _conflict("生成ID已被原方案选择器引用；不自动改变收益作用范围")


def assemble_attack_source(plan, request, db_path):
    """Return a fresh preview and a separate native draft; never accept or save it."""
    _validate_request(request)
    validate_plan(plan)
    if plan["plan_version"] != 3:
        raise PlanError("来源装配仅适用于原生v3", "attack_source_assembly_schema")
    rotation, model = plan.get("rotation_model"), plan.get("mechanics_model")
    if not isinstance(rotation, dict) or not isinstance(model, dict):
        raise PlanError("来源装配须先提供动作和机制模型", "reference")
    abilities = {a["id"]: a for a in rotation["abilities"]}
    ability_ids = sorted(request["ability_ids"])
    if any(aid not in abilities for aid in ability_ids):
        raise PlanError("来源装配引用不存在的能力", "reference")
    selected = [abilities[aid] for aid in ability_ids]
    actor, variant = selected[0]["character"], selected[0].get("variant")
    if (variant not in _HIT_LABELS or any(a["character"] != actor or a.get("variant") != variant
            or a["action_type_id"] != "action.elemental_skill" for a in selected)):
        raise PlanError("所选能力须同角色、同明确变体且为元素战技", "attack_source_assembly_scope")
    if actor not in {c["id"] for c in plan["manual_template"]["characters"]}:
        raise PlanError("来源装配能力引用不存在的角色", "reference")
    character_ref = (plan.get("equipment_inputs", {}).get(actor) or {}).get("character_ref")
    if character_ref and (character_ref["db_character_id"] != request["selection"]["character_id"]
                          or character_ref["version_line"] != request["version_line"]):
        raise PlanError("所选来源与角色已声明数据库身份不符", "attack_source_assembly_scope")
    elemental = model.get("elemental_model") or {}
    if not any(p["id"] == request["contact_profile_id"] for p in elemental.get("contact_profiles", [])):
        raise PlanError("来源装配须引用已有接触档案", "reference")
    action_ids = {a["id"] for a in rotation["actions"] if a["ability_id"] in ability_ids}
    for collection in (model.get("hit_events", []), model.get("hit_patterns", []),
                       model.get("application_events", []), rotation.get("action_hit_links", [])):
        if any(row.get("action_id") in action_ids for row in collection):
            _conflict("所选能力的实际动作已有手工命中/模式/附着/动作命中关联；不自动删除")

    pid = request["profile_id"]
    suffix = fingerprint(pid)
    hid, app = "assembly.hit." + suffix, "assembly.application." + suffix
    hit = {"id": hid, "damage_type_ids": copy.deepcopy(request["damage_type_ids"]), "t": 0,
           "character": actor, "skill": "元素战技", "hit_name": _HIT_LABELS[variant],
           "damage_system": "classic", "scaling_stat": request["scaling_stat"], "count": 1}
    profile = {"id": pid, "kind": KIND, "prototype_hit_id": hid, "variant": variant,
               "application_profile_id": app, "accepted_profile_fingerprint": None,
               **{k: copy.deepcopy(request[k]) for k in (
                   "calibration", "contact_profile_id", "default_landed", "self_sampling")}}
    source = {"action_event_profile_id": pid, "purpose": "single_stat_damage_multiplier",
              **{k: copy.deepcopy(request[k]) for k in (
                  "version_line", "selection", "candidate_id", "status", "source")}}
    source["application_mode"] = request["application_mode"]
    counter_tag = request.get("counter_tag") or request.get("shared_group")
    if counter_tag is not None:
        source["counter_tag"] = counter_tag
    if "verification" in request:
        source["verification"] = copy.deepcopy(request["verification"])
    if "research_report" in request:
        source["research_report"] = copy.deepcopy(request["research_report"])
    bindings = [{"ability_id": aid, "profile_id": pid} for aid in ability_ids]
    existing = [p for p in plan.get("action_event_profiles", []) if p["id"] == pid]
    selected_bindings = [b for b in plan.get("action_event_bindings", [])
                         if b["ability_id"] in ability_ids or b["profile_id"] == pid]
    if existing:
        actual_profile = copy.deepcopy(existing[0])
        actual_profile["accepted_profile_fingerprint"] = None
        actual_hits = [h for h in plan["manual_template"]["hits"] if h["id"] == hid]
        actual_sources = [s for s in plan.get("attack_metadata_sources", []) if s["action_event_profile_id"] == pid]
        if not (_same(actual_profile, profile) and _same(actual_hits, [hit])
                and _same(actual_sources, [source])
                and _same(sorted(selected_bindings, key=lambda b: b["ability_id"]), bindings)):
            _conflict("该档案ID已有不同内容或归属；只允许完全一致的重复装配")
        draft = copy.deepcopy(plan)
        status = "existing"
    else:
        if selected_bindings:
            _conflict("能力或档案已有派发绑定；不自动替换")
        _ensure_unoccupied(plan, {pid, hid, app})
        draft = copy.deepcopy(plan)
        draft["manual_template"]["hits"].append(hit)
        for field in FIELDS:
            draft.setdefault(field, [])
        draft["action_event_profiles"].append(profile)
        draft["action_event_bindings"].extend(bindings)
        draft.setdefault("attack_metadata_sources", []).append(source)
        draft["resolution"] = {"state": "pending"}
        draft["resolved_template"] = None
        status = "created"
    validate_plan(draft)
    # Reuse all source/ownership/calibration/queue guards and the existing single
    # read session. No query-before-compose, prepared client object or bypass.
    report = compose_equipment_plan(draft, db_path)
    created = status == "created"
    assembly = {"schema_version": 1, "status": status, "profile_id": pid,
                "prototype_hit_id": hid, "application_profile_id": app,
                "ability_ids": ability_ids, "actor": actor, "variant": variant,
                "created": {"hits": int(created), "profiles": int(created),
                            "bindings": len(bindings) if created else 0, "sources": int(created)},
                "base_input_fingerprint": fingerprint(input_view(plan)),
                "input_fingerprint": fingerprint(input_view(draft))}
    return {"plan": draft, "assembly": assembly, "report": report}
