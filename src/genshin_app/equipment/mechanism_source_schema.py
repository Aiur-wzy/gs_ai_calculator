"""Native references to typed, reviewed mechanism data; never native executable rules."""
from __future__ import annotations

import copy
import hashlib
import json
import re

from genshin_runtime.mechanism_qualification import CONTACT_SCENARIO_REQUIREMENTS

COMPILER = "typed-mechanism-app-v1"
CAST_COMPILER = "typed-mechanism-app-v2"
CONTACT_COMPILER = "typed-mechanism-app-v3"
PREFIX = "auto.mechanism."
SUPERVISED_PROFILE = "agent-supervised-v1"
LEGACY_PROFILE = "legacy-reviewed-mechanism-v1"
LIMITS = {"max_sources": 8, "max_source_material_bytes": 8 * 1024 * 1024,
          "max_parent_links": 512}


def _obj(fields, required=None):
    return {"type": "object", "properties": fields,
            "required": list(fields) if required is None else required, "additionalProperties": False}


def _nullable(schema):
    return {"anyOf": [schema, {"type": "null"}]}


TEXT = {"type": "string", "maxLength": 4096}
STATUS = {"enum": ["pending", "assumed", "confirmed"]}
SECONDS = _nullable({"type": "number", "minimum": 0, "maximum": 1000000000})
SCENARIO_SCHEMA = _obj({
    "target_id": _nullable({"type": "string", "minLength": 1, "maxLength": 128}),
    "values": _obj({key: {"enum": [value, None, "unknown"]}
                    for key, value in CONTACT_SCENARIO_REQUIREMENTS.items()}, required=[]),
    "status": STATUS, "source": TEXT})
CALIBRATION_SCHEMA = _nullable(_obj({"activation_offset_seconds": SECONDS,
    "precision_seconds": SECONDS, "status": STATUS, "source": TEXT}))
CAST_BINDING_SCHEMA = _obj({
    "ability_ids": {"type": "array", "minItems": 1, "maxItems": 64, "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1, "maxLength": 128}},
    "selection": _nullable({**_obj({
        "skill_depot_id": {"type": "integer", "minimum": 1, "maximum": 2**53-1},
        "skill_id": {"type": "integer", "minimum": 1, "maximum": 2**53-1},
        "constellation": {"type": "integer", "minimum": 0, "maximum": 6},
        "talent_level": {"type": "integer", "minimum": 1, "maximum": 15}}, required=[]), "minProperties": 1})})
SOURCE_SCHEMA = _obj({
    "id": {"type": "string", "minLength": 1, "maxLength": 64},
    "actor": {"type": "string", "minLength": 1, "maxLength": 128},
    "version_line": {"enum": ["release", "beta"]},
    "mechanism_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._-]{0,79}$"},
    "revision": _nullable({"type": "integer", "minimum": 1, "maximum": 2147483647}),
    "scenario": SCENARIO_SCHEMA, "calibration": CALIBRATION_SCHEMA,
    "accepted_fingerprint": _nullable({"type": "string", "pattern": "^[0-9a-f]{64}$"})})
SOURCE_SCHEMA['properties']['cast_binding'] = CAST_BINDING_SCHEMA
SOURCES_SCHEMA = {"type": "array", "items": SOURCE_SCHEMA, "maxItems": LIMITS["max_sources"]}
DEFAULT_SOURCE = {"id": "mechanism-source", "actor": "", "version_line": "release",
    "mechanism_id": "", "revision": None,
    "scenario": {"target_id": None, "values": {}, "status": "pending", "source": ""},
    "calibration": None, "accepted_fingerprint": None}


def mechanism_rule_id(native_id, key):
    payload = json.dumps([native_id, key], ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf8")
    return PREFIX + hashlib.sha256(payload).hexdigest()[:24]


def mechanism_source_contract():
    return {"compiler": COMPILER, "supported_compilers": [COMPILER, CAST_COMPILER, CONTACT_COMPILER], "binding_field": "mechanism_sources",
        "report_field": "mechanism_compilations", "schema": copy.deepcopy(SOURCE_SCHEMA),
        "sources_schema": copy.deepcopy(SOURCES_SCHEMA), "scenario_schema": copy.deepcopy(SCENARIO_SCHEMA),
        "default_source": copy.deepcopy(DEFAULT_SOURCE), "limits": dict(LIMITS),
        "whole_source_status": "partial", "execution_authority": "none",
        "governance_profile": SUPERVISED_PROFILE,
        "execution_support": "agent_materialization_required",
        "candidate_issue_codes": ["mechanism_materialization_required"],
        "legacy_profile": {"id": LEGACY_PROFILE,
                           "status": "historical_receipt_replay_only",
                           "execution_authority": "trusted_receipt_replay"},
        # Knowledge candidates have no current-plan confirmation action.  The
        # field remains for generic capability consumers but must stay empty;
        # only a future Agent materialization contract may create executable
        # inputs and its own review receipt.
        "confirmable_issue_codes": []}


def mechanism_governance_profile(plan, *, historical_receipt_replay=False):
    """Resolve governance without trusting a candidate to grant authority.

    Current plans always use the supervised profile.  A serialized candidate
    may describe that profile, but cannot opt itself into legacy execution.
    The legacy compiler is available only to the private historical-result
    receipt verifier, whose authority is supplied by App code rather than by
    a field in the candidate JSON.
    """

    profile = (plan.get("extensions") or {}).get("governance_profile")
    if profile is not None and profile not in (SUPERVISED_PROFILE, LEGACY_PROFILE):
        from .schema import PlanError
        raise PlanError("未知机制执行治理档案", "mechanism_governance_profile")
    if historical_receipt_replay:
        if profile == SUPERVISED_PROFILE:
            from .schema import PlanError
            raise PlanError("监督式方案不能按旧机制回执重放", "mechanism_governance_profile")
        return LEGACY_PROFILE
    if profile == LEGACY_PROFILE:
        from .schema import PlanError
        raise PlanError(
            "当前方案不能自行启用旧机制执行权；旧逻辑仅允许受信历史回执只读验证",
            "legacy_mechanism_governance_forbidden",
        )
    return SUPERVISED_PROFILE


def validate_mechanism_sources(plan):
    from .schema import PlanError

    def reserved(value, key="", path=()):
        if isinstance(value, dict):
            for name, child in value.items():
                reserved(child, name, (*path, name))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                reserved(child, key, (*path, index))
        elif isinstance(value, str) and value.startswith(PREFIX) and (key == "id" or key.endswith(("_id", "_ids"))):
            if (path[:2] == ("rotation_tuning", "buff_hit_matches")
                    and path[-2:] == ("effect_ref", "buff_id")):
                return  # Fresh registration decides whether this rule may be tuned.
            raise PlanError("原生标识占用通用机制的保留命名空间", "mechanism_namespace")

    reserved({k: v for k, v in plan.items() if k not in ("resolution", "resolved_template")})
    mechanism_governance_profile(plan)
    if "mechanism_sources" not in plan:
        return
    if plan["plan_version"] != 3:
        raise PlanError("通用机制来源需要原生方案版本3")
    rows = plan["mechanism_sources"]
    if len(rows) > LIMITS["max_sources"]:
        raise PlanError("通用机制来源数量超过限额", "mechanism_limit")
    if len({row["id"] for row in rows}) != len(rows):
        raise PlanError("通用机制来源ID重复")
    for row in rows:
        for key, limit in (("id", 64), ("actor", 128)):
            if not row[key].strip() or len(row[key]) > limit:
                raise PlanError("通用机制来源标识为空或过长：" + key)
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,79}", row["mechanism_id"]):
            raise PlanError("通用机制资料ID格式错误")
        accepted = row["accepted_fingerprint"]
        if accepted is not None and not re.fullmatch(r"[0-9a-f]{64}", accepted):
            raise PlanError("通用机制接受指纹格式错误")
        scenario = row["scenario"]
        target = scenario["target_id"]
        if target is not None and (not target.strip() or len(target) > 128):
            raise PlanError("通用机制目标标识为空或过长")
        for key, value in scenario["values"].items():
            expected = CONTACT_SCENARIO_REQUIREMENTS[key]
            if value is not None and value != "unknown" and (type(value) is not type(expected) or value != expected):
                raise PlanError("通用机制情景值不在受支持类型与范围内：" + key)
        for evidence in (scenario, row["calibration"]):
            if evidence is not None and len(evidence["source"]) > 4096:
                raise PlanError("通用机制证据文本过长")
