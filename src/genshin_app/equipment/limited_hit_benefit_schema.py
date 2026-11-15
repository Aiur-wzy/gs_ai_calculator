"""Native declarations for reviewed per-recipient, finite-hit benefits."""
from __future__ import annotations

import copy
import json

COMPILER = "source-bound-limited-hit-benefit-v1"
PREFIX = "auto.hit-benefit."
PART = "damage.limited_hit_base_add"
LIMITS = {"max_sources": 1, "max_native_bytes": 128 * 1024,
          "max_receipt_bytes": 2 * 1024 * 1024, "max_parent_links": 512,
          "max_ledger_bytes": 8 * 1024 * 1024}


def _obj(fields):
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


ID = {"type": "string", "minLength": 1, "maxLength": 128}
TEXT = {"type": "string", "maxLength": 4096}
HASH = {"anyOf": [{"type": "string", "pattern": "^[0-9a-f]{64}$"}, {"type": "null"}]}
SECONDS = {"anyOf": [{"type": "number", "minimum": 0, "maximum": 1000000000}, {"type": "null"}]}
NULL_BOOL = {"anyOf": [{"type": "boolean"}, {"type": "null"}]}
STATUS = {"enum": ["pending", "assumed", "confirmed"]}
SCENARIO_SCHEMA = _obj({
    "target_kind": {"enum": ["unknown", "ordinary_non_animal_monster"]},
    "party_mode": {"enum": ["unknown", "single_player_fixed"]},
    "all_party_alive": NULL_BOOL,
    "all_party_in_apply_predicate": NULL_BOOL,
    "all_party_in_retention_predicate": NULL_BOOL,
    "initial_effect": {"enum": ["unknown", "none"]},
    "no_external_quill_changes": NULL_BOOL,
    "hitlag_policy": {"enum": ["unknown", "zero_hitlag_reference"]},
    "status": STATUS, "source": TEXT})
CALIBRATION_SCHEMA = _obj({"activation_offset_seconds": SECONDS, "precision_seconds": SECONDS,
                          "status": STATUS, "source": TEXT})
SOURCE_SCHEMA = _obj({"id": ID, "action_event_profile_id": {"anyOf": [ID, {"type": "null"}]},
                     "scenario": SCENARIO_SCHEMA, "calibration": CALIBRATION_SCHEMA,
                     "accepted_fingerprint": HASH})
SOURCES_SCHEMA = {"type": "array", "items": SOURCE_SCHEMA, "maxItems": LIMITS["max_sources"]}
DEFAULT_SOURCE = {"id": "limited-hit-source", "action_event_profile_id": None,
    "scenario": {"target_kind": "unknown", "party_mode": "unknown", "all_party_alive": None,
                 "all_party_in_apply_predicate": None, "all_party_in_retention_predicate": None,
                 "initial_effect": "unknown", "no_external_quill_changes": None,
                 "hitlag_policy": "unknown", "status": "pending", "source": ""},
    "calibration": {"activation_offset_seconds": None, "precision_seconds": None,
                    "status": "pending", "source": ""}, "accepted_fingerprint": None}


def limited_hit_benefit_contract():
    return {"compiler": COMPILER, "binding_field": "limited_hit_benefit_sources",
        "report_field": "limited_hit_benefit_compilations", "schema": copy.deepcopy(SOURCE_SCHEMA),
        "sources_schema": copy.deepcopy(SOURCES_SCHEMA), "scenario_schema": copy.deepcopy(SCENARIO_SCHEMA),
        "default_source": copy.deepcopy(DEFAULT_SOURCE), "limits": dict(LIMITS),
        "confirmable_issue_codes": ["limited_hit_benefit_confirmation_required"],
        "whole_source_status": "partial", "gameplay_interpretation": "reference_adapted_not_decoded",
        "supported_variants": ["hold"],
        "supported": ["单个C0、有效10级提供者的受审长按战技冰翎", "每人独立次数、重施刷新、提供者当前攻击力取样"],
        "unsupported": ["同轴混合点按与长按", "命座或多个提供者", "多目标、真实距离、死亡和卡肉",
                        "取消中的延迟激活", "受命中筛选/次数约束的攻击面板或递归属性转换"]}


def validate_limited_hit_benefit_sources(plan):
    from .schema import PlanError

    declared_rules = {PREFIX + row["id"] for row in plan.get("limited_hit_benefit_sources", [])}

    def reserved(value, key="", path=()):
        if isinstance(value, dict):
            for name, child in value.items():
                reserved(child, name, (*path, name))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reserved(child, key, (*path, index))
        elif isinstance(value, str) and value.startswith(PREFIX) and (key == "id" or key.endswith(("_id", "_ids"))):
            if (path[:2] == ("rotation_tuning", "buff_hit_matches")
                    and path[-2:] == ("effect_ref", "buff_id") and value in declared_rules):
                return
            raise PlanError("原生标识占用有限次数收益的保留命名空间", "limited_hit_benefit_namespace")

    reserved({k: v for k, v in plan.items() if k not in ("resolution", "resolved_template")})
    if "limited_hit_benefit_sources" not in plan:
        return
    rows = plan["limited_hit_benefit_sources"]
    if plan["plan_version"] != 3:
        raise PlanError("有限次数收益来源需要原生方案版本3")
    if (len(rows) > LIMITS["max_sources"]
            or len(json.dumps(rows, ensure_ascii=False, allow_nan=False).encode("utf8")) > LIMITS["max_native_bytes"]):
        raise PlanError("有限次数收益来源超过声明限额", "limited_hit_benefit_limit")
    if any(not row["id"].strip() for row in rows):
        raise PlanError("有限次数收益来源ID不能为空")
