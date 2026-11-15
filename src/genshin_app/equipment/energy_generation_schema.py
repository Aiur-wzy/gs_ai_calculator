"""Native source declarations for the bounded deterministic particle adapter."""
from __future__ import annotations

import copy
import json
import re

COMPILER = "source-bound-energy-generation-v1"
PREFIX = "auto.energy."
PART = "energy.standard_particles"
LIMITS = {"max_sources": 8, "max_native_bytes": 128 * 1024,
          "max_receipt_bytes": 2 * 1024 * 1024, "max_parent_links": 512}


def _obj(fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


TEXT = {"type": "string", "maxLength": 4096}
ID = {"type": "string", "minLength": 1, "maxLength": 128}
NULL_ID = {"anyOf": [ID, {"type": "null"}]}
HASH = {"anyOf": [{"type": "string", "pattern": "^[0-9a-f]{64}$"}, {"type": "null"}]}
SECONDS = {"anyOf": [{"type": "number", "minimum": 0, "maximum": 1000000000}, {"type": "null"}]}
NULL_BOOL = {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
STATUS = {"enum": ["pending", "assumed", "confirmed"]}
SCENARIO_SCHEMA = _obj({
    "target_kind": {"enum": ["unknown", "ordinary_non_animal_monster"]},
    "drop_allowed": NULL_BOOL,
    "initial_controller": {"enum": ["unknown", "inactive"]},
    "pickup": {"enum": ["unknown", "all_at_once"]},
    "hitlag_policy": {"enum": [None, "unknown", "zero_hitlag_reference"]},
    "no_external_controller_changes": NULL_BOOL,
    "all_party_alive": NULL_BOOL, "status": STATUS, "source": TEXT})
CALIBRATION_SCHEMA = _obj({"spawn_delay_seconds": SECONDS, "travel_seconds": SECONDS,
                          "precision_seconds": SECONDS, "status": STATUS, "source": TEXT})
SOURCE_SCHEMA = _obj({"id": ID, "action_event_profile_id": NULL_ID, "origin_reference_id": HASH,
    "scenario": SCENARIO_SCHEMA, "calibration": CALIBRATION_SCHEMA,
    "replaces_spawn_ids": {"type": "array", "items": ID, "maxItems": 512}, "accepted_fingerprint": HASH})
SOURCES_SCHEMA = {"type": "array", "items": SOURCE_SCHEMA, "maxItems": LIMITS["max_sources"]}
DEFAULT_SOURCE = {"id": "energy-source", "action_event_profile_id": None, "origin_reference_id": None,
    "scenario": {"target_kind": "unknown", "drop_allowed": None, "initial_controller": "unknown",
                 "pickup": "unknown", "hitlag_policy": "unknown", "no_external_controller_changes": None,
                 "all_party_alive": None, "status": "pending", "source": ""},
    "calibration": {"spawn_delay_seconds": None, "travel_seconds": None, "precision_seconds": None,
                    "status": "pending", "source": ""}, "replaces_spawn_ids": [], "accepted_fingerprint": None}


def energy_generation_contract():
    return {"compiler": COMPILER, "binding_field": "energy_generation_sources",
            "report_field": "energy_generation_compilations", "schema": copy.deepcopy(SOURCE_SCHEMA),
            "sources_schema": copy.deepcopy(SOURCES_SCHEMA), "scenario_schema": copy.deepcopy(SCENARIO_SCHEMA),
            "default_source": copy.deepcopy(DEFAULT_SOURCE), "limits": dict(LIMITS),
            "confirmable_issue_codes": ["energy_generation_confirmation_required"],
            "dispatch_mode": "calibrated_reference", "whole_source_status": "partial",
            "quantity_scope": "此来源编译器仅自动派发已受审固定整商。平均产球可在显式 energy_spawn/energy_credit 或 particles 机制资源输入中声明。",
            "supported": ["C0单次E固定变体的受审初始命中", "数量/元素/类型及逐动作所有权自动交付"],
            "unsupported": ["真实卡肉延寿", "完整Unique", "混合变体", "随机产球", "未受审产球分支", "逐粒拾取监听"]}


def validate_energy_generation_sources(plan):
    from .schema import PlanError
    def check_reserved(value, key=""):
        if isinstance(value, dict):
            for name, child in value.items():
                check_reserved(child, name)
        elif isinstance(value, list):
            for child in value:
                check_reserved(child, key)
        elif isinstance(value, str) and value.startswith(PREFIX) and (key == "id" or key.endswith(("_id", "_ids"))):
            raise PlanError("原生标识占用产球派发保留命名空间", "energy_generation_namespace")
    check_reserved({k: v for k, v in plan.items() if k not in ("resolution", "resolved_template")})
    if "energy_generation_sources" not in plan:
        return
    rows = plan["energy_generation_sources"]
    if plan["plan_version"] != 3:
        raise PlanError("产球来源需要原生方案版本3")
    if len(rows) > LIMITS["max_sources"] or len(json.dumps(rows, ensure_ascii=False).encode("utf8")) > LIMITS["max_native_bytes"]:
        raise PlanError("产球来源声明超过有界限额", "energy_generation_limit")
    if len({r["id"] for r in rows}) != len(rows):
        raise PlanError("产球来源ID重复")
    profiles = [r["action_event_profile_id"] for r in rows if r["action_event_profile_id"] is not None]
    if len(set(profiles)) != len(profiles):
        raise PlanError("同一命中档案不能重复绑定产球来源", "duplicate_delivery")
    for row in rows:
        if not row["id"].strip() or len(row["id"]) > 128 or row["id"].startswith(PREFIX):
            raise PlanError("产球来源ID不合法或占用派生命名空间")
        if row["action_event_profile_id"] is not None and len(row["action_event_profile_id"]) > 128:
            raise PlanError("父动作档案标识过长")
        if any(len(row[k]["source"]) > 4096 for k in ("scenario", "calibration")):
            raise PlanError("产球依据文本超过4096字符", "energy_generation_limit")
        for key in ("origin_reference_id", "accepted_fingerprint"):
            if row[key] is not None and not re.fullmatch("[0-9a-f]{64}", row[key]):
                raise PlanError("产球来源指纹必须为64位小写十六进制")
        if len(row["replaces_spawn_ids"]) > 512 or any(len(v) > 128 for v in row["replaces_spawn_ids"]):
            raise PlanError("产球接管清单超过有界限额", "energy_generation_limit")
        if len(set(row["replaces_spawn_ids"])) != len(row["replaces_spawn_ids"]):
            raise PlanError("产球接管清单重复", "duplicate_delivery")
