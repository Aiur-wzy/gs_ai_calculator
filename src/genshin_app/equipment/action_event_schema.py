"""Bounded native fields for calibrated single-contact action dispatch."""
from __future__ import annotations

import copy
import json
import math
import re
from decimal import Decimal, InvalidOperation

FIELDS = ("action_event_profiles", "action_event_bindings", "action_event_overrides")
KIND = "calibrated_single_contact_v1"
COMPILER = "calibrated-action-events-v1"
APPLICATION_KIND = "calibrated_single_application_v2"
APPLICATION_COMPILER = "calibrated-action-events-v2"
APPLICATION_CONTRACT = {
    'contract_id': 'calibrated-single-application-v2:2', 'ruleset': 'gauge-swirl-dynamic-v3',
    'emission': 'single_damage_application', 'contact': 'not_supported',
    'source_scope': 'exact_character_slot_constellation_and_talent_level',
    'sampling': 'current_action_start_or_explicit_capture',
}
MAX_INPUT_BYTES = 128 * 1024
MAX_RECEIPT_BYTES = 2 * 1024 * 1024
ID = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"}
SOURCE = {"type": "string", "minLength": 1, "maxLength": 2048}
STATUS = {"enum": ["confirmed", "assumed", "pending"]}


def _object(properties, required=None):
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


TOPOLOGY_OCCURRENCE = _object({
    "contract_id": {"enum": ["finite-sequence-step-v1"]},
    "occurrence_id": ID,
    "component_id": ID,
    "ordinal": {"type": "integer", "minimum": 1, "maximum": 256},
    "topology_order": {"type": "integer", "minimum": -10**9, "maximum": 10**9},
    "terminal": {"type": "boolean"},
})

PROFILE = _object({"id": ID, "kind": {"enum": [KIND]}, "prototype_hit_id": ID,
    "variant": {"enum": ["default", "tap", "hold"]},
    "calibration": _object({"offset_seconds": {"type": "number", "minimum": 0, "maximum": 60, "multipleOf": 0.000001},
        "precision_seconds": {"type": "number", "minimum": 0, "maximum": 1}, "status": STATUS, "source": SOURCE}),
    "application_profile_id": ID, "contact_profile_id": ID,
    "default_landed": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
    "self_sampling": {"enum": ["current", "action_start", "action_capture"]},
    "topology_occurrence": TOPOLOGY_OCCURRENCE,
    "accepted_profile_fingerprint": {"anyOf": [{"type": "null"},
        {"type": "string", "pattern": r"^[0-9a-f]{64}$"}]},
}, ["id", "kind", "prototype_hit_id", "variant", "calibration",
    "application_profile_id", "contact_profile_id", "default_landed",
    "self_sampling", "accepted_profile_fingerprint"])
APPLICATION_PROFILE = copy.deepcopy(PROFILE)
APPLICATION_PROFILE['properties']['kind'] = {'enum': [APPLICATION_KIND]}
del APPLICATION_PROFILE['properties']['contact_profile_id']
APPLICATION_PROFILE['required'].remove('contact_profile_id')
BINDING = _object({"ability_id": ID, "profile_id": ID})
_OVERRIDE = {"action_id": ID, "landed": PROFILE["properties"]["default_landed"],
             "order": {"type": "integer", "minimum": -10**9, "maximum": 10**9}, "status": STATUS, "source": SOURCE}
OVERRIDE = {"anyOf": [_object(_OVERRIDE, ["action_id", "landed", "status", "source"]),
                      _object(_OVERRIDE, ["action_id", "order", "status", "source"])]}
from .finite_action_schema import COMPILER as FINITE_APPLICATION_COMPILER
from .finite_action_schema import KIND as FINITE_APPLICATION_KIND
from .finite_action_schema import PROFILE as FINITE_APPLICATION_PROFILE
from .periodic_schema import APPLICATION_CONTRACT as MULTI_APPLICATION_CONTRACT
from .periodic_schema import APPLICATION_KIND as MULTI_APPLICATION_KIND
from .periodic_schema import APPLICATION_OVERRIDE as MULTI_APPLICATION_OVERRIDE
from .periodic_schema import APPLICATION_PROFILE as MULTI_APPLICATION_PROFILE
from .periodic_schema import KIND as MULTI_KIND
from .periodic_schema import OVERRIDE as MULTI_OVERRIDE
from .periodic_schema import PROFILE as MULTI_PROFILE

FIELD_SCHEMAS = {name: {"type": "array", "maxItems": limit, "items": schema} for name, schema, limit in
                zip(FIELDS, ({"anyOf": [PROFILE, APPLICATION_PROFILE, MULTI_PROFILE,
                                        MULTI_APPLICATION_PROFILE, FINITE_APPLICATION_PROFILE]}, BINDING,
                             {"anyOf": [OVERRIDE, MULTI_OVERRIDE, MULTI_APPLICATION_OVERRIDE]}), (32, 64, 256), strict=True)}
SCHEMA = _object(FIELD_SCHEMAS)


def action_event_receipt_compiler(records):
    """Keep the old aggregate protocol for records without an explicit v2 kind."""
    return APPLICATION_COMPILER if any(r.get('kind') in (
        APPLICATION_KIND, MULTI_APPLICATION_KIND, FINITE_APPLICATION_KIND)
                                       for r in records) else COMPILER


def _check(value, schema, path):
    if "anyOf" in schema:
        errors = [_check(value, option, path) for option in schema["anyOf"]]
        return [] if any(not error for error in errors) else min(errors, key=len)
    kind = schema.get("type")
    types = {"object": isinstance(value, dict), "array": isinstance(value, list), "string": isinstance(value, str),
             "boolean": type(value) is bool, "null": value is None, "integer": type(value) is int,
             "number": type(value) in (int, float)}
    if kind and not types[kind]:
        return [{"path": path, "message": "动作档案字段类型不符"}]
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append({"path": path, "message": "动作档案字段值不在支持范围"})
    if isinstance(value, str) and (not value.strip() or len(value) > schema.get("maxLength", 10**9)
                                 or "pattern" in schema and not re.fullmatch(schema["pattern"], value)):
        errors.append({"path": path, "message": "动作档案文本格式或长度不符"})
    if type(value) in (int, float):
        if (not math.isfinite(value) or value < schema.get("minimum", -math.inf)
                or value > schema.get("maximum", math.inf) or value <= schema.get("exclusiveMinimum", -math.inf)):
            errors.append({"path": path, "message": "动作档案数值超界"})
        if "multipleOf" in schema and Decimal(str(value)) % Decimal(str(schema["multipleOf"])) != 0:
            errors.append({"path": path, "message": "偏移须精确到微秒，不自动舍入"})
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append({"path": path, "message": "动作档案条目不可为空"})
        if len(value) > schema.get("maxItems", 0):
            return [{"path": path, "message": "动作档案条目超过上限"}]
        for i, child in enumerate(value):
            errors.extend(_check(child, schema["items"], f"{path}[{i}]"))
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append({"path": path + "." + key, "message": "动作档案缺少必填字段"})
        for key, child in value.items():
            if key not in props:
                errors.append({"path": path + "." + key, "message": "动作档案不接受未知字段"})
            else:
                errors.extend(_check(child, props[key], path + "." + key))
    return errors


def validate_action_event_fields(plan):
    from .schema import PlanError
    present = set(FIELDS) & plan.keys()
    if not present:
        return
    if present != set(FIELDS) or plan.get("plan_version") != 3:
        raise PlanError("三个动作档案字段须同时提供，且仅适用于原生v3", "action_event_schema")
    selected = {name: plan[name] for name in FIELDS}
    try:
        budgeted = {
            **selected,
            **({"periodic_attack_sources": plan["periodic_attack_sources"]}
               if "periodic_attack_sources" in plan else {}),
            **({"periodic_refresh_triggers": plan["periodic_refresh_triggers"]}
               if "periodic_refresh_triggers" in plan else {}),
        }
        raw = json.dumps(budgeted, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("动作档案输入超过128KiB")
        errors = _check(selected, SCHEMA, "action_events")
    except (ValueError, TypeError, OverflowError, RecursionError, InvalidOperation) as exc:
        raise PlanError(str(exc), "action_event_schema") from exc
    if errors:
        raise PlanError("动作档案结构不合法", "action_event_schema", errors[:256])
    from .finite_action_schema import validate_finite_profiles
    validate_finite_profiles(plan)
    for name, key in zip(FIELDS, ("id", "ability_id", "action_id"), strict=True):
        ids = [row[key] for row in plan[name]]
        if len(set(ids)) != len(ids):
            raise PlanError("动作档案身份不能重复", "action_event_schema", {"path": name})


def action_event_capabilities():
    return {"compiler": COMPILER, "schema_version": 1, "schema": copy.deepcopy(SCHEMA), "profile_kind": KIND,
        "profile_kinds": [KIND, APPLICATION_KIND, MULTI_KIND, MULTI_APPLICATION_KIND,
                          FINITE_APPLICATION_KIND],
        "profile_contracts": {
            KIND: {'compiler': COMPILER, 'ruleset': 'gauge-freeze-reference-v1', 'emission_key': 'primary-contact'},
            APPLICATION_KIND: {'compiler': APPLICATION_COMPILER, 'ruleset': 'gauge-swirl-dynamic-v3',
                               'emission_key': 'primary-application', 'application_contract': copy.deepcopy(APPLICATION_CONTRACT)},
            MULTI_KIND: {'compiler': COMPILER, 'ruleset': 'gauge-freeze-reference-v1'},
            MULTI_APPLICATION_KIND: {'compiler': APPLICATION_COMPILER, 'ruleset': 'gauge-swirl-dynamic-v3',
                                     'emission_key': 'multi-application',
                                     'application_contract': copy.deepcopy(MULTI_APPLICATION_CONTRACT)},
            FINITE_APPLICATION_KIND: {'compiler': FINITE_APPLICATION_COMPILER,
                                      'receipt_compiler': APPLICATION_COMPILER,
                                      'ruleset': 'gauge-swirl-dynamic-v3',
                                      'emission_key': 'finite-application'},
        },
        "ruleset": "gauge-freeze-reference-v1", "dispatch_mode": "calibrated", "skill_coverage": "partial",
        "resource_coverage": "not_generated", "emission_key": "primary-contact",
        "limits": {"max_profiles": 32, "max_bindings": 64, "max_overrides": 256, "max_input_bytes": MAX_INPUT_BYTES,
                   "max_receipt_bytes": MAX_RECEIPT_BYTES, "max_expanded_events": 40000, "max_issues": 256},
        "empty_fields": {name: [] for name in FIELDS},
        "supported": ["共享原型和数据库倍率来源，按能力绑定全部实际once动作", "逐动作命中/同刻顺序例外",
                      "旧单接触v1限C0有效10级E；普通附着v2采用精确命座、1–15级有效等级及唯一普攻/战技/爆发槽；均为classic单ATK、普通单目标、非负微秒偏移", "当前、动作pre后post前或明确关联的动作面板取样；未就绪不回退",
                      "周期来源档案：多通道有限次数、明确建立/结束与脱手；旧v1限C0有效10级Q并要求冻结contact，新v2采用精确命座与1–15级有效Q等级，在动态元素模型中直接派生application且禁止contact，见periodic_attack_contract"],
        "unsupported": ["自动原生派发", "提前取消/发射前切人", "无元素模型或与档案类别不符的规则集", "随机产球",
                        "单接触档案内多接触/脱手后命中", "攻速变化/卡肉/动态形态", "自动多轮/自动排轴"],
        "labels": {"action_event_profiles": "动作命中档案", "action_event_bindings": "能力派发绑定",
                   "action_event_overrides": "本轴命中例外", "calibration": "时序校准", "default_landed": "默认命中",
                   "prototype_hit_id": "共享命中原型", "self_sampling": "自身属性采样", "generated": "自动生成命中"}}
