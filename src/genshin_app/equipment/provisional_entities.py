"""Current-plan public-data fallbacks with an explicit authority split.

Public game entities normally belong to the database.  D2 has two deliberately
different recovery paths:

* ``database_missing``: an App-minted database query receipt proves that the
  current compatible snapshot does not contain the requested entity or field.
  The Agent may then provide a sourced, plan-local value without asking the
  user to approve every missing fact.
* ``public_data_patch``: the database already has a value (or conflicting
  candidates) and the Agent wants to depart from it.  This additionally needs
  a trusted user-decision-ledger approval for the exact payload.

``unavailable`` is a query/service failure, not proof of missing data, and can
never authorize a formal fallback.  Nothing here writes the public database or
makes a temporary value reusable by another plan.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from typing import Any

CONTRACT = "provisional-public-entities-v1"
APPROVAL_CONTRACT = "provisional-public-entity-user-approval-v1"
LOOKUP_RECEIPT_CONTRACT = "public-entity-database-lookup-receipt-v1"
ATTESTATION_CONTRACT = "provisional-public-entity-agent-attestation-v1"
USAGE_RECEIPT_CONTRACT = "provisional-public-entity-usage-receipt-v1"
USAGE_AUDIT_CONTRACT = "provisional-public-entity-used-value-audit-v1"
PROVISIONAL_RESULT_FIDELITY = "approximate_with_provisional_entities"
FORMAL_RESULT_FIDELITY = "formal_without_provisional_entities"
REGISTRY_REFERENCE_CONTRACT = "provisional-entity-registry-reference-v1"
LOADOUT_VALUES_CONTRACT = "provisional-loadout-entity-values-v1"
FIELD = "provisional_entities"
ENTITY_TYPES = frozenset({"character", "weapon", "artifact_set"})
LOOKUP_STATES = frozenset({"not_found", "conflict", "unavailable"})
AUTHORIZATION_KINDS = frozenset({"database_missing", "public_data_patch"})
SOURCE_STATES = frozenset({"verified", "declared", "unknown"})
MAX_ENTITIES = 32
MAX_VALUES_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 512 * 1024
# Production loadout consumption is deliberately narrower than the registry.
# Only the closed entity-specific value contract below can reach Composer;
# arbitrary registry JSON remains review material rather than executable data.
SUPPORTED_CONSUMERS = frozenset({
    "identity_preview",
    "equipment_composer_preview",
    "equipment_composer",
})
FORMAL_RESULT_CONSUMERS = frozenset({"agent_materialization", "equipment_composer"})
RESOLVED_AUTHORITIES = frozenset({
    "agent_reported_database_missing",
    "user_confirmed_public_data_patch",
})

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


TEXT = {"type": "string", "minLength": 1, "maxLength": 4096}
IDENTITY = {"type": "string", "pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}
HASH = {"type": "string", "pattern": r"^[0-9a-f]{64}$"}
SOURCE_REF_SCHEMA = _obj({
    "kind": IDENTITY,
    "reference": TEXT,
    "status": {"enum": sorted(SOURCE_STATES)},
    "fingerprint": _nullable(HASH),
    "note": {"type": "string", "maxLength": 4096},
})
VERSION_CLAIM_SCHEMA = _obj({
    "version_line": {"enum": ["release", "beta"]},
    "game_version": _nullable({"type": "string", "minLength": 1, "maxLength": 64}),
})
LOOKUP_RECEIPT_SCHEMA = _obj({
    "contract": {"enum": [LOOKUP_RECEIPT_CONTRACT]},
    "receipt_id": IDENTITY,
    "issued_by": {"enum": ["app_database_query"]},
    "status": {"enum": sorted(LOOKUP_STATES)},
    "query": {"type": "object"},
    "database_snapshot_fingerprint": HASH,
    "observed_candidates": {"type": "array", "maxItems": 16,
                            "items": {"type": "object"}},
    "message": TEXT,
    "issued_at": TEXT,
})
PUBLIC_DATA_DIFF_SCHEMA = _nullable(_obj({
    "baseline_candidate_fingerprint": HASH,
    "before": {"type": "object", "minProperties": 1},
    "after": {"type": "object", "minProperties": 1},
    "changed_paths": {
        "type": "array", "minItems": 1, "maxItems": 256,
        "items": {"type": "string", "minLength": 1, "maxLength": 1024},
    },
}))
APPROVAL_SCHEMA = _nullable(_obj({
    "contract": {"enum": [APPROVAL_CONTRACT]},
    "approval_id": IDENTITY,
    "status": {"enum": ["user_confirmed"]},
    "issued_by": {"enum": ["user_decision_ledger"]},
    "plan_id": IDENTITY,
    "candidate_revision": {"type": "integer", "minimum": 0},
    "stage_revision": {"type": "integer", "minimum": 0},
    "authorization_basis_fingerprint": HASH,
    "database_snapshot_fingerprint": HASH,
    "payload_fingerprint": HASH,
    "question_id": IDENTITY,
    "answer_receipt": IDENTITY,
    "approved_at": TEXT,
}))
ATTESTATION_SCHEMA = _nullable(_obj({
    "contract": {"enum": [ATTESTATION_CONTRACT]},
    "attestation_id": IDENTITY,
    "status": {"enum": ["agent_reported"]},
    "issued_by": {"enum": ["app_agent_materialization"]},
    "plan_id": IDENTITY,
    "candidate_revision": {"type": "integer", "minimum": 0},
    "stage_revision": {"type": "integer", "minimum": 0},
    "authorization_basis_fingerprint": HASH,
    "database_snapshot_fingerprint": HASH,
    "payload_fingerprint": HASH,
    "disclosed_at": TEXT,
}))
ENTITY_SCHEMA = _obj({
    "contract": {"enum": [CONTRACT]},
    "provisional_id": IDENTITY,
    "entity_type": {"enum": sorted(ENTITY_TYPES)},
    "authorization_kind": {"enum": sorted(AUTHORIZATION_KINDS)},
    "display_name": {"type": "string", "minLength": 1, "maxLength": 256},
    "version_claim": VERSION_CLAIM_SCHEMA,
    "values": {"type": "object", "minProperties": 1},
    "source_refs": {"type": "array", "minItems": 1, "maxItems": 16,
                    "items": SOURCE_REF_SCHEMA},
    "reason": TEXT,
    "necessity": TEXT,
    "impact": TEXT,
    "risk": TEXT,
    "alternatives": {"type": "array", "minItems": 1, "maxItems": 16,
                     "items": TEXT},
    # Agent may author an inert proposal before the App performs the trusted
    # lookup.  ``None`` is a draft state, never evidence that data is missing.
    "database_lookup": _nullable(LOOKUP_RECEIPT_SCHEMA),
    "public_data_diff": PUBLIC_DATA_DIFF_SCHEMA,
    "scope": {"enum": ["current_plan_only"]},
    "database_authority": {"enum": [False]},
    "payload_fingerprint": _nullable(HASH),
    "attestation": ATTESTATION_SCHEMA,
    "approval": APPROVAL_SCHEMA,
})
PROVISIONAL_ENTITIES_SCHEMA = {
    "type": "array", "maxItems": MAX_ENTITIES, "items": ENTITY_SCHEMA,
}
DATABASE_ENTITY_REF_SCHEMA = _obj({
    "kind": {"enum": ["database"]},
    "entity_type": {"enum": sorted(ENTITY_TYPES)},
    "database_id": {"type": "integer", "minimum": 1},
    "version_line": {"enum": ["release", "beta"]},
})
PROVISIONAL_ENTITY_REF_SCHEMA = _obj({
    "kind": {"enum": ["provisional"]},
    "entity_type": {"enum": sorted(ENTITY_TYPES)},
    "provisional_id": IDENTITY,
})
ENTITY_REF_SCHEMA = {
    "anyOf": [DATABASE_ENTITY_REF_SCHEMA, PROVISIONAL_ENTITY_REF_SCHEMA],
}

_LOADOUT_EFFECT_TREATMENTS = frozenset({
    "included_in_panel",
    "no_effect_in_scope",
    "agent_materialization_required",
})


def validate_loadout_entity_values(entity_type: str, value: Any) -> dict[str, Any]:
    """Validate the only provisional value shape executable by loadout consumers.

    The public registry deliberately permits bounded JSON because it is also a
    reviewed knowledge envelope.  A loadout consumer has a narrower boundary:
    it accepts only this closed, versioned shape.  This prevents a newly added
    free-form field from silently becoming a character, weapon or set rule.
    """

    path = "provisional_entity.values"
    if entity_type not in ENTITY_TYPES or not isinstance(value, dict):
        _error("provisional_loadout_values_invalid", "临时配装实体值合同不合法",
               category="request_error", path=path)
    common = {"contract", "extensions"}
    if value.get("contract") != LOADOUT_VALUES_CONTRACT:
        _error("provisional_loadout_values_contract",
               "临时实体尚未采用可执行配装值合同；自由文本资料不能直接进入计算",
               category="engine_capability_missing", path=f"{path}.contract")
    if not isinstance(value.get("extensions"), dict):
        _error("provisional_loadout_values_invalid", "临时配装扩展必须为对象",
               category="request_error", path=f"{path}.extensions")

    def number(raw: Any, child_path: str) -> float:
        if (type(raw) not in (int, float) or not math.isfinite(raw)
                or raw < 0 or raw > 1_000_000_000):
            _error("provisional_loadout_value_invalid", "临时配装数值必须是有限非负数",
                   category="request_error", path=child_path)
        return float(raw)

    def integer(raw: Any, low: int, high: int, child_path: str) -> int:
        if type(raw) is not int or not low <= raw <= high:
            _error("provisional_loadout_value_invalid", "临时配装等级或件数超出范围",
                   category="request_error", path=child_path)
        return raw

    if entity_type == "character":
        _closed(value, common | {"weapon_type", "panel_basis"}, path)
        _text(value["weapon_type"], f"{path}.weapon_type", 128)
        rows = value["panel_basis"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
            _error("provisional_loadout_basis_invalid", "临时角色需提供1至16个等级基础面板",
                   category="request_error", path=f"{path}.panel_basis")
        seen: set[int] = set()
        for index, row in enumerate(rows):
            row_path = f"{path}.panel_basis.{index}"
            _closed(row, {"level", "base_atk", "base_hp", "base_def"}, row_path)
            level = integer(row["level"], 1, 100, f"{row_path}.level")
            if level in seen:
                _error("provisional_loadout_basis_duplicate", "临时角色等级基础面板重复",
                       category="request_error", path=row_path)
            seen.add(level)
            for key in ("base_atk", "base_hp", "base_def"):
                number(row[key], f"{row_path}.{key}")
    elif entity_type == "weapon":
        _closed(value, common | {
            "weapon_type", "panel_basis", "passive_treatment", "passive_note",
        }, path)
        _text(value["weapon_type"], f"{path}.weapon_type", 128)
        if value["passive_treatment"] not in _LOADOUT_EFFECT_TREATMENTS:
            _error("provisional_loadout_treatment_invalid", "临时武器被动处理方式不受支持",
                   category="request_error", path=f"{path}.passive_treatment")
        _text(value["passive_note"], f"{path}.passive_note")
        rows = value["panel_basis"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
            _error("provisional_loadout_basis_invalid", "临时武器需提供1至32个等级突破基础值",
                   category="request_error", path=f"{path}.panel_basis")
        seen: set[tuple[int, int]] = set()
        for index, row in enumerate(rows):
            row_path = f"{path}.panel_basis.{index}"
            _closed(row, {"level", "ascension", "base_atk"}, row_path)
            identity = (
                integer(row["level"], 1, 100, f"{row_path}.level"),
                integer(row["ascension"], 0, 6, f"{row_path}.ascension"),
            )
            if identity in seen:
                _error("provisional_loadout_basis_duplicate", "临时武器等级突破基础值重复",
                       category="request_error", path=row_path)
            seen.add(identity)
            number(row["base_atk"], f"{row_path}.base_atk")
    else:
        _closed(value, common | {"thresholds"}, path)
        rows = value["thresholds"]
        if not isinstance(rows, list) or len(rows) > 5:
            _error("provisional_loadout_threshold_invalid", "临时套装阈值最多五项",
                   category="request_error", path=f"{path}.thresholds")
        seen: set[int] = set()
        for index, row in enumerate(rows):
            row_path = f"{path}.thresholds.{index}"
            _closed(row, {"required_count", "treatment", "note"}, row_path)
            count = integer(row["required_count"], 1, 5, f"{row_path}.required_count")
            if count in seen:
                _error("provisional_loadout_threshold_duplicate", "临时套装件数阈值重复",
                       category="request_error", path=row_path)
            seen.add(count)
            if row["treatment"] not in _LOADOUT_EFFECT_TREATMENTS:
                _error("provisional_loadout_treatment_invalid", "临时套装效果处理方式不受支持",
                       category="request_error", path=f"{row_path}.treatment")
            _text(row["note"], f"{row_path}.note")
    _finite_json(value, path)
    return copy.deepcopy(value)


def provisional_panel_basis(
    character_values: Mapping[str, Any], *, level: int,
    weapon_values: Mapping[str, Any] | None = None,
    weapon_level: int | None = None, weapon_ascension: int | None = None,
) -> dict[str, float]:
    """Resolve only the white/base panel facts declared by reviewed entities."""

    character = validate_loadout_entity_values("character", character_values)
    matches = [row for row in character["panel_basis"] if row["level"] == level]
    if len(matches) != 1:
        _error("provisional_character_basis_missing", "临时角色缺少当前等级的唯一基础面板",
               category="core_data_missing", path="provisional_entity.values.panel_basis")
    row = matches[0]
    result = {key: float(row[key]) for key in ("base_atk", "base_hp", "base_def")}
    if weapon_values is not None:
        weapon = validate_loadout_entity_values("weapon", weapon_values)
        if character["weapon_type"] != weapon["weapon_type"]:
            _error("provisional_weapon_type_mismatch", "临时武器类型与角色不匹配",
                   category="request_error", path="equipment_inputs.weapon.entity_ref")
        matches = [item for item in weapon["panel_basis"]
                   if item["level"] == weapon_level
                   and item["ascension"] == weapon_ascension]
        if len(matches) != 1:
            _error("provisional_weapon_basis_missing", "临时武器缺少当前等级突破的唯一基础攻击力",
                   category="core_data_missing", path="provisional_entity.values.panel_basis")
        result["base_atk"] += float(matches[0]["base_atk"])
    return result


class ProvisionalEntityError(ValueError):
    def __init__(
        self,
        reason: str,
        message: str,
        *,
        category: str,
        path: str = FIELD,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.category = category
        self.details = details if details is not None else {
            "category": category, "path": path, "message": message,
        }


def _error(reason: str, message: str, *, category: str, path: str = FIELD) -> None:
    raise ProvisionalEntityError(reason, message, category=category, path=path)


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False,
                          separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ProvisionalEntityError(
            "provisional_entity_json_invalid",
            "临时公共实体必须是有限、无循环的JSON值",
            category="request_error",
        ) from exc


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _closed(value: Any, keys: set[str], path: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        _error("provisional_entity_shape", "临时实体字段不完整或包含未知字段",
               category="request_error", path=path)


def _text(value: Any, path: str, maximum: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _error("provisional_entity_text", "临时实体文本为空或过长",
               category="request_error", path=path)
    return value


def _identity(value: Any, path: str) -> str:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        _error("provisional_entity_identity", "临时实体身份不合法",
               category="request_error", path=path)
    return value


def _digest(value: Any, path: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _error("provisional_entity_fingerprint", "临时实体指纹不合法",
               category="request_error", path=path)
    return value


def _finite_json(value: Any, path: str, depth: int = 0) -> None:
    if depth > 64:
        _error("provisional_entity_depth", "临时实体嵌套超过限制",
               category="request_error", path=path)
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            _error("provisional_entity_number", "临时实体数值必须有限",
                   category="request_error", path=path)
        return
    if isinstance(value, list):
        if len(value) > 256:
            _error("provisional_entity_cardinality", "临时实体列表超过限制",
                   category="request_error", path=path)
        for index, item in enumerate(value):
            _finite_json(item, f"{path}.{index}", depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 256 or any(not isinstance(key, str) for key in value):
            _error("provisional_entity_cardinality", "临时实体对象超过限制",
                   category="request_error", path=path)
        for key, item in value.items():
            _finite_json(item, f"{path}.{key}", depth + 1)
        return
    _error("provisional_entity_json_invalid", "临时实体包含非JSON值",
           category="request_error", path=path)


_ENTITY_KEYS = {
    "contract", "provisional_id", "entity_type", "authorization_kind",
    "display_name", "version_claim",
    "values", "source_refs", "reason", "necessity", "impact", "risk", "alternatives",
    "database_lookup", "scope", "database_authority", "payload_fingerprint",
    "public_data_diff", "attestation", "approval",
}
_SOURCE_KEYS = {"kind", "reference", "status", "fingerprint", "note"}
_LOOKUP_KEYS = {
    "contract", "receipt_id", "issued_by", "status", "query",
    "database_snapshot_fingerprint", "observed_candidates", "message", "issued_at",
}
_APPROVAL_KEYS = {
    "contract", "approval_id", "status", "issued_by", "plan_id", "candidate_revision",
    "stage_revision", "authorization_basis_fingerprint",
    "database_snapshot_fingerprint", "payload_fingerprint",
    "question_id", "answer_receipt", "approved_at",
}
_ATTESTATION_KEYS = {
    "contract", "attestation_id", "status", "issued_by", "plan_id",
    "candidate_revision", "stage_revision", "authorization_basis_fingerprint",
    "database_snapshot_fingerprint",
    "payload_fingerprint", "disclosed_at",
}
_USAGE_RECEIPT_KEYS = {
    "contract", "issued_by", "status", "scope", "database_authority",
    "plan_id", "plan_fingerprint", "candidate_revision", "stage_revision",
    "database_snapshot_fingerprint", "entities", "receipt_fingerprint",
}
_USAGE_ENTITY_KEYS = {
    "entity_type", "provisional_id", "consumer", "authority",
    "payload_fingerprint", "lookup_receipt_id", "attestation_id", "approval_id",
    "audit",
}
_USAGE_AUDIT_KEYS = {
    "contract", "audit_only", "entity_type", "provisional_id", "display_name",
    "authorization_kind", "version_claim", "values", "source_refs", "reason",
    "necessity", "impact", "risk", "database_lookup", "public_data_diff",
    "scope", "database_authority", "payload_fingerprint", "authority_proof_ids",
    "usage_context", "audit_fingerprint",
}
_USAGE_AUDIT_PROOF_KEYS = {
    "lookup_receipt_id", "attestation_id", "approval_id",
}
_USAGE_AUDIT_CONTEXT_KEYS = {
    "plan_id", "plan_fingerprint", "candidate_revision", "stage_revision",
    "database_snapshot_fingerprint", "consumer", "authority",
}


def payload_view(entity: Mapping[str, Any]) -> dict[str, Any]:
    """Return the exact user-approved payload, excluding its signature receipt."""

    return {key: copy.deepcopy(value) for key, value in entity.items()
            if key not in {"payload_fingerprint", "attestation", "approval"}}


def entity_payload_fingerprint(entity: Mapping[str, Any]) -> str:
    return _fingerprint(payload_view(entity))


def _lookup_matches_entity(entity: Mapping[str, Any], lookup: Mapping[str, Any]) -> bool:
    query = lookup.get("query")
    if not isinstance(query, Mapping):
        return False
    if (query.get("kind") != entity.get("entity_type")
            or query.get("version_line")
            != entity.get("version_claim", {}).get("version_line")):
        return False
    if "exact_name" in query:
        return query.get("exact_name") == entity.get("display_name")
    return type(query.get("database_id")) is int and query["database_id"] > 0


def _plan_fingerprint(plan: Mapping[str, Any]) -> str:
    """Bind a receipt to the exact reusable calculation input.

    ``resolution`` and ``resolved_template`` are output/cache projections that
    may be refreshed without changing the declared calculation input.  This
    mirrors the equipment input view without importing ``files`` (which would
    introduce a schema import cycle).
    """

    return _fingerprint({
        key: copy.deepcopy(value) for key, value in plan.items()
        if key not in {"resolution", "resolved_template"}
    })


_AUTHORIZATION_ONLY_ENTITY_FIELDS = {
    "database_lookup", "public_data_diff", "payload_fingerprint",
    "attestation", "approval",
}


def authorization_basis_fingerprint(plan: Mapping[str, Any]) -> str:
    """Fingerprint business input while ignoring App-issued authority facts.

    A second lookup, attestation, or user approval must not invalidate an
    already-authorized sibling entity.  Conversely, every real plan edit must
    invalidate the trusted authority ledgers even when the document is later
    changed back.  The session ledger supplies that irreversible invalidation;
    this content fingerprint makes each proof independently auditable.
    """

    if not isinstance(plan, Mapping):
        _error("provisional_entity_authorization_basis_invalid",
               "临时实体授权缺少当前方案", category="request_error")
    projected: dict[str, Any] = {}
    for key, value in plan.items():
        if key in {"revision", "resolution", "resolved_template"}:
            continue
        if key != FIELD:
            projected[key] = copy.deepcopy(value)
            continue
        if not isinstance(value, list):
            projected[key] = copy.deepcopy(value)
            continue
        projected[key] = [
            {
                field: copy.deepcopy(child)
                for field, child in row.items()
                if field not in _AUTHORIZATION_ONLY_ENTITY_FIELDS
            }
            if isinstance(row, Mapping) else copy.deepcopy(row)
            for row in value
        ]
    return _fingerprint(projected)


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _project_like(source: Any, template: Any, path: str) -> Any:
    if isinstance(template, dict):
        if not isinstance(source, Mapping):
            _error("provisional_entity_patch_baseline_mismatch",
                   "公共数据纠错基线没有拟修改字段", category="request_error", path=path)
        result = {}
        for key, child in template.items():
            if key not in source:
                _error("provisional_entity_patch_baseline_mismatch",
                       "公共数据纠错基线缺少拟修改字段", category="request_error",
                       path=f"{path}/{_pointer_token(key)}")
            result[key] = _project_like(
                source[key], child, f"{path}/{_pointer_token(key)}"
            )
        return result
    if isinstance(template, list):
        if not isinstance(source, list):
            _error("provisional_entity_patch_baseline_mismatch",
                   "公共数据纠错基线字段类型不一致", category="request_error", path=path)
        return copy.deepcopy(source)
    return copy.deepcopy(source)


def _changed_paths(before: Any, after: Any, path: str = "") -> list[str]:
    if type(before) is type(after) and before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        rows: list[str] = []
        for key in sorted(before.keys() | after.keys()):
            pointer = f"{path}/{_pointer_token(key)}"
            if key not in before or key not in after:
                rows.append(pointer)
            else:
                rows.extend(_changed_paths(before[key], after[key], pointer))
        return rows
    if isinstance(before, list) and isinstance(after, list):
        rows = []
        for index in range(max(len(before), len(after))):
            pointer = f"{path}/{index}"
            if index >= len(before) or index >= len(after):
                rows.append(pointer)
            else:
                rows.extend(_changed_paths(before[index], after[index], pointer))
        return rows
    return [path or "/"]


def build_public_data_diff(
    database_lookup: Mapping[str, Any], replacement: Mapping[str, Any], *,
    candidate_index: int = 0,
) -> dict[str, Any]:
    """Build an exact field-level diff against one observed DB candidate."""

    candidates = (database_lookup.get("observed_candidates")
                  if isinstance(database_lookup, Mapping) else None)
    if (not isinstance(candidates, list) or not candidates
            or type(candidate_index) is not int
            or not 0 <= candidate_index < len(candidates)):
        _error("provisional_entity_patch_baseline_missing",
               "公共数据纠错需要选择一条数据库候选作为精确基线",
               category="request_error")
    if not isinstance(replacement, Mapping) or not replacement:
        _error("provisional_entity_patch_replacement_invalid",
               "公共数据纠错替换值不能为空", category="request_error")
    candidate = candidates[candidate_index]
    if not isinstance(candidate, Mapping):
        _error("provisional_entity_patch_baseline_missing",
               "数据库候选不是可比较对象", category="request_error")
    if replacement.get("contract") == LOADOUT_VALUES_CONTRACT:
        from .provisional_baseline import CONTRACT as BASELINE_CONTRACT
        from .provisional_baseline import FIELD as BASELINE_FIELD
        from .provisional_baseline import loadout_comparison_selection
        projection = candidate.get(BASELINE_FIELD)
        if (not isinstance(projection, Mapping) or projection.get("contract") != BASELINE_CONTRACT
                or projection.get("entity_type") != database_lookup.get("query", {}).get("kind")
                or not isinstance(projection.get("field_sources"), Mapping)
                or projection.get("selection") != loadout_comparison_selection(projection["entity_type"], replacement)):
            _error("provisional_entity_patch_baseline_mismatch",
                   "可执行配装纠错缺少App按真实数据库建立的同形比较投影；请重新核对来源",
                   category="request_error")
        before = copy.deepcopy(projection["values"])
    else:
        before = _project_like(candidate, replacement, "public_data_diff.before")
    after = copy.deepcopy(dict(replacement))
    changed = _changed_paths(before, after)
    if not changed:
        _error("provisional_entity_patch_no_change", "公共数据纠错没有任何实际差异",
               category="request_error")
    return {
        "baseline_candidate_fingerprint": _fingerprint(candidate),
        "before": before,
        "after": after,
        "changed_paths": changed,
    }


def validate_entity(entity: Any, *, path: str = FIELD) -> dict[str, Any]:
    _closed(entity, _ENTITY_KEYS, path)
    if entity["contract"] != CONTRACT:
        _error("provisional_entity_contract", "临时实体合同版本不受支持",
               category="request_error", path=f"{path}.contract")
    _identity(entity["provisional_id"], f"{path}.provisional_id")
    if entity["entity_type"] not in ENTITY_TYPES:
        _error("provisional_entity_type", "临时实体类型不受支持",
               category="request_error", path=f"{path}.entity_type")
    if entity["authorization_kind"] not in AUTHORIZATION_KINDS:
        _error("provisional_entity_authorization_kind", "临时实体授权类型不受支持",
               category="request_error", path=f"{path}.authorization_kind")
    _text(entity["display_name"], f"{path}.display_name", 256)
    version = entity["version_claim"]
    _closed(version, {"version_line", "game_version"}, f"{path}.version_claim")
    if version["version_line"] not in {"release", "beta"}:
        _error("provisional_entity_version", "临时实体资料线不合法",
               category="request_error", path=f"{path}.version_claim.version_line")
    if version["game_version"] is not None:
        _text(version["game_version"], f"{path}.version_claim.game_version", 64)
    if not isinstance(entity["values"], dict) or not entity["values"]:
        _error("provisional_entity_values", "临时实体必须声明至少一个精确字段",
               category="request_error", path=f"{path}.values")
    _finite_json(entity["values"], f"{path}.values")
    if len(_json_bytes(entity["values"])) > MAX_VALUES_BYTES:
        _error("provisional_entity_values_limit", "临时实体字段超过64KiB",
               category="request_error", path=f"{path}.values")
    sources = entity["source_refs"]
    if not isinstance(sources, list) or not 1 <= len(sources) <= 16:
        _error("provisional_entity_sources", "临时实体需要1至16条来源",
               category="request_error", path=f"{path}.source_refs")
    for index, source in enumerate(sources):
        source_path = f"{path}.source_refs.{index}"
        _closed(source, _SOURCE_KEYS, source_path)
        _identity(source["kind"], f"{source_path}.kind")
        _text(source["reference"], f"{source_path}.reference")
        if source["status"] not in SOURCE_STATES:
            _error("provisional_entity_source_status", "临时实体来源状态不合法",
                   category="request_error", path=f"{source_path}.status")
        if source["fingerprint"] is not None:
            _digest(source["fingerprint"], f"{source_path}.fingerprint")
        if not isinstance(source["note"], str) or len(source["note"]) > 4096:
            _error("provisional_entity_source_note", "临时实体来源说明过长",
                   category="request_error", path=f"{source_path}.note")
    for field in ("reason", "necessity", "impact", "risk"):
        _text(entity[field], f"{path}.{field}")
    alternatives = entity["alternatives"]
    if not isinstance(alternatives, list) or not 1 <= len(alternatives) <= 16:
        _error("provisional_entity_alternatives", "临时实体需说明至少一个替代方案",
               category="request_error", path=f"{path}.alternatives")
    for index, alternative in enumerate(alternatives):
        _text(alternative, f"{path}.alternatives.{index}")
    lookup = entity["database_lookup"]
    if entity["scope"] != "current_plan_only" or entity["database_authority"] is not False:
        _error("provisional_entity_authority", "临时实体只能用于当前方案且不得冒充数据库权威",
               category="request_error", path=path)
    if lookup is None:
        if any(entity[field] is not None for field in (
                "public_data_diff", "payload_fingerprint", "attestation", "approval")):
            _error("provisional_entity_lookup_required",
                   "尚未由App核对数据库的提案不能携带差异或授权回执",
                   category="request_error", path=f"{path}.database_lookup")
        return copy.deepcopy(entity)
    _closed(lookup, _LOOKUP_KEYS, f"{path}.database_lookup")
    if (lookup["contract"] != LOOKUP_RECEIPT_CONTRACT
            or lookup["issued_by"] != "app_database_query"):
        _error("provisional_entity_lookup_receipt_invalid", "数据库查询回执不是App签发的受信合同",
               category="request_error", path=f"{path}.database_lookup")
    _identity(lookup["receipt_id"], f"{path}.database_lookup.receipt_id")
    if lookup["status"] not in LOOKUP_STATES:
        _error("provisional_entity_lookup_status", "数据库查询回执不支持该状态",
               category="request_error", path=f"{path}.database_lookup.status")
    if not isinstance(lookup["query"], dict) or not lookup["query"]:
        _error("provisional_entity_lookup_query", "数据库查询回执缺少精确查询",
               category="request_error", path=f"{path}.database_lookup.query")
    _finite_json(lookup["query"], f"{path}.database_lookup.query")
    _digest(lookup["database_snapshot_fingerprint"],
            f"{path}.database_lookup.database_snapshot_fingerprint")
    candidates = lookup["observed_candidates"]
    if not isinstance(candidates, list) or len(candidates) > 16:
        _error("provisional_entity_lookup_candidates", "数据库候选回执超过限制",
               category="request_error", path=f"{path}.database_lookup.observed_candidates")
    _finite_json(candidates, f"{path}.database_lookup.observed_candidates")
    _text(lookup["message"], f"{path}.database_lookup.message")
    _text(lookup["issued_at"], f"{path}.database_lookup.issued_at")
    patch_diff = entity["public_data_diff"]
    if (entity["authorization_kind"] == "database_missing"
            and lookup["status"] not in {"not_found", "unavailable"}):
        _error("provisional_entity_authorization_mismatch",
               "数据库缺失型临时输入需要not_found查询回执；服务故障只能保留草稿",
               category="request_error", path=f"{path}.database_lookup.status")
    if entity["authorization_kind"] == "database_missing" and patch_diff is not None:
        _error("provisional_entity_patch_diff_not_applicable",
               "数据库缺失型临时输入不能携带公共数据纠错差异",
               category="request_error", path=f"{path}.public_data_diff")
    if (entity["authorization_kind"] == "public_data_patch"
            and lookup["status"] not in {"conflict", "unavailable"}):
        _error("provisional_entity_authorization_mismatch",
               "数据库纠错型临时输入需要conflict查询回执；服务故障只能保留草稿",
               category="request_error", path=f"{path}.database_lookup.status")
    if entity["authorization_kind"] == "public_data_patch":
        diff_path = f"{path}.public_data_diff"
        if not isinstance(patch_diff, dict) or set(patch_diff) != {
                "baseline_candidate_fingerprint", "before", "after", "changed_paths"}:
            _error("provisional_entity_patch_diff_required",
                   "公共数据纠错需要对数据库候选给出精确字段差异",
                   category="request_error", path=diff_path)
        baseline_fp = _digest(
            patch_diff["baseline_candidate_fingerprint"],
            f"{diff_path}.baseline_candidate_fingerprint",
        )
        matches = [candidate for candidate in candidates
                   if _fingerprint(candidate) == baseline_fp]
        if not matches:
            _error("provisional_entity_patch_baseline_stale",
                   "公共数据纠错基线不属于当前数据库查询候选",
                   category="core_data_missing", path=diff_path)
        expected_before = build_public_data_diff(lookup, entity["values"],
            candidate_index=candidates.index(matches[0]))["before"]
        if patch_diff["before"] != expected_before or patch_diff["after"] != entity["values"]:
            _error("provisional_entity_patch_diff_mismatch",
                   "公共数据纠错差异与数据库基线或拟采用值不一致",
                   category="request_error", path=diff_path)
        expected_paths = _changed_paths(patch_diff["before"], patch_diff["after"])
        if (not expected_paths or patch_diff["changed_paths"] != expected_paths
                or len(expected_paths) > 256):
            _error("provisional_entity_patch_diff_mismatch",
                   "公共数据纠错的字段路径没有精确描述全部变化",
                   category="request_error", path=f"{diff_path}.changed_paths")
        _finite_json(patch_diff, diff_path)
    expected = entity_payload_fingerprint(entity)
    if entity["payload_fingerprint"] is not None:
        _digest(entity["payload_fingerprint"], f"{path}.payload_fingerprint")
        if entity["payload_fingerprint"] != expected:
            _error("provisional_entity_payload_stale", "临时实体字段或证据变化，审批载荷已失效",
                   category="core_data_missing", path=f"{path}.payload_fingerprint")
    attestation = entity["attestation"]
    if attestation is not None:
        _closed(attestation, _ATTESTATION_KEYS, f"{path}.attestation")
        if (attestation["contract"] != ATTESTATION_CONTRACT
                or attestation["status"] != "agent_reported"
                or attestation["issued_by"] != "app_agent_materialization"):
            _error("provisional_entity_attestation_invalid",
                   "数据库缺失型回执不是App签发的Agent材料化证明",
                   category="request_error", path=f"{path}.attestation")
        _identity(attestation["attestation_id"], f"{path}.attestation.attestation_id")
        _identity(attestation["plan_id"], f"{path}.attestation.plan_id")
        for field in ("candidate_revision", "stage_revision"):
            if type(attestation[field]) is not int or attestation[field] < 0:
                _error("provisional_entity_attestation_revision",
                       "Agent材料化回执修订不合法", category="request_error",
                       path=f"{path}.attestation.{field}")
        _digest(attestation["authorization_basis_fingerprint"],
                f"{path}.attestation.authorization_basis_fingerprint")
        _digest(attestation["database_snapshot_fingerprint"],
                f"{path}.attestation.database_snapshot_fingerprint")
        _digest(attestation["payload_fingerprint"],
                f"{path}.attestation.payload_fingerprint")
        _text(attestation["disclosed_at"], f"{path}.attestation.disclosed_at")
    approval = entity["approval"]
    if approval is not None:
        _closed(approval, _APPROVAL_KEYS, f"{path}.approval")
        if (approval["contract"] != APPROVAL_CONTRACT
                or approval["status"] != "user_confirmed"
                or approval["issued_by"] != "user_decision_ledger"):
            _error("provisional_entity_approval_invalid", "临时实体批准不是受信用户决策回执",
                   category="request_error", path=f"{path}.approval")
        _identity(approval["approval_id"], f"{path}.approval.approval_id")
        _identity(approval["plan_id"], f"{path}.approval.plan_id")
        for field in ("candidate_revision", "stage_revision"):
            if type(approval[field]) is not int or approval[field] < 0:
                _error("provisional_entity_approval_revision", "临时实体批准修订不合法",
                       category="request_error", path=f"{path}.approval.{field}")
        _digest(approval["authorization_basis_fingerprint"],
                f"{path}.approval.authorization_basis_fingerprint")
        _digest(approval["database_snapshot_fingerprint"],
                f"{path}.approval.database_snapshot_fingerprint")
        _digest(approval["payload_fingerprint"], f"{path}.approval.payload_fingerprint")
        _identity(approval["question_id"], f"{path}.approval.question_id")
        _identity(approval["answer_receipt"], f"{path}.approval.answer_receipt")
        _text(approval["approved_at"], f"{path}.approval.approved_at")
    return copy.deepcopy(entity)


def validate_registry(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = plan.get(FIELD, [])
    if not isinstance(raw, list) or len(raw) > MAX_ENTITIES:
        _error("provisional_entity_limit", "当前方案临时公共实体最多32项",
               category="request_error")
    if len(_json_bytes(raw)) > MAX_TOTAL_BYTES:
        _error("provisional_entity_total_limit", "临时公共实体声明超过512KiB",
               category="request_error")
    rows = [validate_entity(item, path=f"{FIELD}.{index}")
            for index, item in enumerate(raw)]
    identities = [(row["entity_type"], row["provisional_id"]) for row in rows]
    if len(identities) != len(set(identities)):
        _error("provisional_entity_duplicate", "临时公共实体身份重复",
               category="request_error")
    return rows


def approval_request(
    plan: Mapping[str, Any],
    entity: Mapping[str, Any],
    *,
    candidate_revision: int,
    stage_revision: int,
    database_snapshot_fingerprint: str,
) -> dict[str, Any]:
    validate_entity(entity)
    if entity["authorization_kind"] != "public_data_patch":
        _error("provisional_entity_approval_not_applicable",
               "用户批准只用于偏离数据库已有事实的纠错型输入",
               category="request_error")
    if (not isinstance(entity["database_lookup"], Mapping)
            or entity["database_lookup"]["status"] != "conflict"):
        _error("provisional_entity_lookup_unavailable",
               "数据库查询服务不可用时不能生成公共数据纠错批准请求",
               category="engine_capability_missing")
    plan_id = _identity(plan.get("plan_id"), "plan.plan_id")
    _digest(database_snapshot_fingerprint, "database_snapshot_fingerprint")
    expected_db = entity["database_lookup"]["database_snapshot_fingerprint"]
    if expected_db != database_snapshot_fingerprint:
        _error("provisional_entity_database_stale", "数据库快照变化，临时实体提案已失效",
               category="core_data_missing")
    if type(candidate_revision) is not int or candidate_revision < 0:
        _error("provisional_entity_revision", "候选修订不合法", category="request_error")
    if type(stage_revision) is not int or stage_revision < 0:
        _error("provisional_entity_stage_revision", "阶段修订不合法", category="request_error")
    payload_fp = entity_payload_fingerprint(entity)
    return {
        "contract": APPROVAL_CONTRACT,
        "plan_id": plan_id,
        "candidate_revision": candidate_revision,
        "stage_revision": stage_revision,
        "authorization_basis_fingerprint": authorization_basis_fingerprint(plan),
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
        "provisional_id": entity["provisional_id"],
        "entity_type": entity["entity_type"],
        "payload": payload_view(entity),
        "payload_fingerprint": payload_fp,
        "scope": "current_plan_only",
        "database_authority": False,
    }


def attestation_request(
    plan: Mapping[str, Any],
    entity: Mapping[str, Any],
    *,
    candidate_revision: int,
    stage_revision: int,
    database_snapshot_fingerprint: str,
) -> dict[str, Any]:
    """Return the exact payload an App materialization ledger may attest.

    This is not a self-authorizing signature.  The returned fields must be
    recorded by the trusted App ledger and matched byte-for-byte at resolve
    time, just like a user approval is matched against its decision ledger.
    """

    validate_entity(entity)
    if entity["authorization_kind"] != "database_missing":
        _error("provisional_entity_attestation_not_applicable",
               "Agent材料化回执只适用于数据库确实缺失的临时输入",
               category="request_error")
    if (not isinstance(entity["database_lookup"], Mapping)
            or entity["database_lookup"]["status"] != "not_found"):
        _error("provisional_entity_lookup_unavailable",
               "数据库查询服务不可用时不能生成缺失型材料化回执",
               category="engine_capability_missing")
    plan_id = _identity(plan.get("plan_id"), "plan.plan_id")
    _digest(database_snapshot_fingerprint, "database_snapshot_fingerprint")
    if entity["database_lookup"]["database_snapshot_fingerprint"] != database_snapshot_fingerprint:
        _error("provisional_entity_database_stale", "数据库快照变化，临时实体提案已失效",
               category="core_data_missing")
    if type(candidate_revision) is not int or candidate_revision < 0:
        _error("provisional_entity_revision", "候选修订不合法", category="request_error")
    if type(stage_revision) is not int or stage_revision < 0:
        _error("provisional_entity_stage_revision", "阶段修订不合法", category="request_error")
    return {
        "contract": ATTESTATION_CONTRACT,
        "plan_id": plan_id,
        "candidate_revision": candidate_revision,
        "stage_revision": stage_revision,
        "authorization_basis_fingerprint": authorization_basis_fingerprint(plan),
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
        "provisional_id": entity["provisional_id"],
        "entity_type": entity["entity_type"],
        "payload": payload_view(entity),
        "payload_fingerprint": entity_payload_fingerprint(entity),
        "scope": "current_plan_only",
        "database_authority": False,
    }


def _validate_ref(ref: Any) -> dict[str, Any]:
    if isinstance(ref, dict) and ref.get("contract") == REGISTRY_REFERENCE_CONTRACT:
        _closed(ref, {"contract", "entity_type", "provisional_id"}, "entity_ref")
        ref = {
            "kind": "provisional",
            "entity_type": ref["entity_type"],
            "provisional_id": ref["provisional_id"],
        }
    if not isinstance(ref, dict) or ref.get("kind") not in {"database", "provisional"}:
        _error("entity_ref_invalid", "公共实体引用必须明确database或provisional",
               category="request_error", path="entity_ref")
    if ref["kind"] == "database":
        _closed(ref, {"kind", "entity_type", "database_id", "version_line"}, "entity_ref")
        if (ref["entity_type"] not in ENTITY_TYPES
                or type(ref["database_id"]) is not int or ref["database_id"] < 1
                or ref["version_line"] not in {"release", "beta"}):
            _error("entity_ref_invalid", "数据库公共实体引用不合法",
                   category="request_error", path="entity_ref")
    else:
        _closed(ref, {"kind", "entity_type", "provisional_id"}, "entity_ref")
        if ref["entity_type"] not in ENTITY_TYPES:
            _error("entity_ref_invalid", "临时公共实体引用类型不合法",
                   category="request_error", path="entity_ref.entity_type")
        _identity(ref["provisional_id"], "entity_ref.provisional_id")
    return copy.deepcopy(ref)


def validate_entity_ref(ref: Any, *, expected_type: str | None = None) -> dict[str, Any]:
    """Public structural validator used by whiteboard and native-plan schemas."""

    checked = _validate_ref(ref)
    if expected_type is not None and checked["entity_type"] != expected_type:
        _error("entity_ref_type_mismatch", "公共实体引用类型与所在配装位置不一致",
               category="request_error", path="entity_ref.entity_type")
    return checked


_RESOLUTION_SEAL = object()


class _TrustedResolvedUse(Mapping[str, Any]):
    """Non-serializable proof that one real resolver performed the lookup.

    The public projection remains mapping-like for consumers, but a hand-made
    dict can never be promoted into a formal usage receipt merely because its
    values resemble a registry row.
    """

    __slots__ = ("_context", "_projection", "_seal")

    def __init__(self, projection: Mapping[str, Any], context: Mapping[str, Any]) -> None:
        self._projection = copy.deepcopy(dict(projection))
        self._context = copy.deepcopy(dict(context))
        self._seal = _RESOLUTION_SEAL

    def __getitem__(self, key: str) -> Any:
        return self._projection[key]

    def __iter__(self):
        return iter(self._projection)

    def __len__(self) -> int:
        return len(self._projection)


def resolve_entity_ref(
    plan: Mapping[str, Any],
    ref: Mapping[str, Any],
    *,
    consumer: str,
    database_snapshot_fingerprint: str,
    candidate_revision: int,
    stage_revision: int,
    approval_ledger: Mapping[str, Mapping[str, Any]],
    database_lookup_ledger: Mapping[str, Mapping[str, Any]] | None = None,
    agent_attestation_ledger: Mapping[str, Mapping[str, Any]] | None = None,
    database_resolver: Callable[[dict[str, Any]], Mapping[str, Any] | None] | None = None,
    supported_consumers: frozenset[str] = SUPPORTED_CONSUMERS,
) -> Mapping[str, Any]:
    """Resolve one entity without granting a provisional value implicit trust."""

    checked_ref = _validate_ref(ref)
    if checked_ref["kind"] == "database":
        if database_resolver is None:
            _error("database_entity_resolver_missing", "当前消费者未提供数据库实体解析器",
                   category="engine_capability_missing", path="entity_ref")
        resolved = database_resolver(checked_ref)
        if not isinstance(resolved, Mapping):
            _error("entity_not_found", "数据库未收录所选公共实体；不能伪造数据库ID",
                   category="core_data_missing", path="entity_ref.database_id")
        return {"authority": "database", "ref": checked_ref,
                "value": copy.deepcopy(dict(resolved))}

    if consumer not in supported_consumers:
        _error("provisional_entity_consumer_unsupported",
               "当前消费者尚未接入临时公共实体，不能静默采用",
               category="engine_capability_missing", path="entity_ref")
    rows = validate_registry(plan)
    matches = [row for row in rows if row["entity_type"] == checked_ref["entity_type"]
               and row["provisional_id"] == checked_ref["provisional_id"]]
    if len(matches) != 1:
        _error("provisional_entity_not_found", "当前方案没有唯一的临时公共实体",
               category="core_data_missing", path="entity_ref.provisional_id")
    entity = matches[0]
    payload_fp = entity_payload_fingerprint(entity)
    if entity["payload_fingerprint"] != payload_fp:
        _error("provisional_entity_payload_stale", "临时实体载荷尚未封存或已变化",
               category="core_data_missing")
    if not isinstance(entity["database_lookup"], Mapping):
        _error("provisional_entity_lookup_required",
               "临时公共实体尚未经过App数据库核对，不能进入消费者",
               category="core_data_missing",
               path=f"{FIELD}.{entity['provisional_id']}.database_lookup")
    if entity["database_lookup"]["database_snapshot_fingerprint"] != database_snapshot_fingerprint:
        _error("provisional_entity_database_stale", "数据库快照变化，临时实体已失效",
               category="core_data_missing")
    lookup = entity["database_lookup"]
    lookup_ledger = database_lookup_ledger or {}
    trusted_lookup = lookup_ledger.get(lookup["receipt_id"])
    if not isinstance(trusted_lookup, Mapping) or dict(trusted_lookup) != lookup:
        _error("provisional_entity_lookup_untrusted",
               "数据库缺失或冲突没有可核验的App查询回执；Agent不能自行声明",
               category="request_error", path=f"{FIELD}.{entity['provisional_id']}.database_lookup")
    if not _lookup_matches_entity(entity, lookup):
        _error("provisional_entity_lookup_stale",
               "App查询回执不属于当前临时实体身份或版本线",
               category="core_data_missing",
               path=f"{FIELD}.{entity['provisional_id']}.database_lookup.query")
    if lookup["status"] == "unavailable":
        _error("provisional_entity_lookup_unavailable",
               "数据库查询服务不可用，不能据此认定公共数据缺失或错误",
               category="engine_capability_missing",
               path=f"{FIELD}.{entity['provisional_id']}.database_lookup.status")

    authorization_kind = entity["authorization_kind"]
    if authorization_kind == "database_missing":
        if lookup["status"] != "not_found":
            _error("provisional_entity_authorization_mismatch",
                   "数据库缺失型临时输入没有not_found查询证明",
                   category="request_error")
        if not any(source.get("status") == "verified" for source in entity["source_refs"]):
            _error("provisional_entity_verified_source_required",
                   "数据库缺失型临时输入至少需要一条已核实来源",
                   category="core_data_missing", path=f"{FIELD}.{entity['provisional_id']}.source_refs")
        attestation = entity["attestation"]
        if attestation is None:
            _error("provisional_entity_agent_attestation_required",
                   "数据库缺失型临时输入需要绑定当前方案的Agent材料化回执",
                   category="core_data_missing")
        expected = {
            "plan_id": plan.get("plan_id"),
            "stage_revision": stage_revision,
            "authorization_basis_fingerprint": authorization_basis_fingerprint(plan),
            "database_snapshot_fingerprint": database_snapshot_fingerprint,
            "payload_fingerprint": payload_fp,
        }
        if (attestation.get("candidate_revision", candidate_revision + 1)
                > candidate_revision
                or any(attestation.get(key) != value
                       for key, value in expected.items())):
            _error("provisional_entity_attestation_stale",
                   "Agent材料化回执与当前方案、修订或数据库不一致",
                   category="core_data_missing",
                   path=f"{FIELD}.{entity['provisional_id']}.attestation")
        trusted_attestation = (agent_attestation_ledger or {}).get(
            attestation["attestation_id"]
        )
        if (not isinstance(trusted_attestation, Mapping)
                or dict(trusted_attestation) != attestation):
            _error("provisional_entity_attestation_untrusted",
                   "Agent材料化回执不在受信App账本中，方案不能自行确认",
                   category="request_error",
                   path=f"{FIELD}.{entity['provisional_id']}.attestation")
        projection = {
            "authority": "agent_reported_database_missing",
            "consumer": consumer,
            "scope": "current_plan_only",
            "database_authority": False,
            "ref": checked_ref,
            "value": copy.deepcopy(entity["values"]),
            "payload_fingerprint": payload_fp,
            "lookup_receipt_id": lookup["receipt_id"],
            "attestation_id": attestation["attestation_id"],
            "source_disclosure_required": True,
        }
        return _TrustedResolvedUse(projection, {
            "plan_id": plan.get("plan_id"),
            "authorization_basis_fingerprint": authorization_basis_fingerprint(plan),
            "candidate_revision": candidate_revision,
            "stage_revision": stage_revision,
            "database_snapshot_fingerprint": database_snapshot_fingerprint,
        })

    if lookup["status"] != "conflict":
        _error("provisional_entity_authorization_mismatch",
               "公共数据纠错没有conflict查询证明",
               category="request_error")
    approval = entity["approval"]
    if approval is None:
        _error("provisional_entity_user_approval_required", "修改数据库已有事实需要用户批准精确差异",
               category="core_data_missing")
    expected = {
        "plan_id": plan.get("plan_id"),
        "stage_revision": stage_revision,
        "authorization_basis_fingerprint": authorization_basis_fingerprint(plan),
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
        "payload_fingerprint": payload_fp,
    }
    if (approval.get("candidate_revision", candidate_revision + 1)
            > candidate_revision
            or any(approval.get(key) != value for key, value in expected.items())):
        _error("provisional_entity_approval_stale", "临时实体批准与当前方案、修订或数据库不一致",
               category="core_data_missing", path=f"{FIELD}.{entity['provisional_id']}.approval")
    trusted = approval_ledger.get(approval["approval_id"])
    if not isinstance(trusted, Mapping) or dict(trusted) != approval:
        _error("provisional_entity_approval_untrusted",
               "临时实体批准不在受信用户决策账本中；Agent不能自行确认",
               category="request_error", path=f"{FIELD}.{entity['provisional_id']}.approval")
    projection = {
        "authority": "user_confirmed_public_data_patch",
        "consumer": consumer,
        "scope": "current_plan_only",
        "database_authority": False,
        "ref": checked_ref,
        "value": copy.deepcopy(entity["values"]),
        "payload_fingerprint": payload_fp,
        "approval_id": approval["approval_id"],
        "lookup_receipt_id": lookup["receipt_id"],
    }
    return _TrustedResolvedUse(projection, {
        "plan_id": plan.get("plan_id"),
        "authorization_basis_fingerprint": authorization_basis_fingerprint(plan),
        "candidate_revision": candidate_revision,
        "stage_revision": stage_revision,
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
    })


def _declared_provisional_usages(
    value: Any, *, root: bool = True, _path: tuple[Any, ...] = (),
) -> list[tuple[str, str]]:
    """Return every declared provisional use, preserving multiplicity.

    The registry is only an available catalog.  An entity becomes a use only
    when another part of the effective plan references it.  Multiplicity is
    retained so one trusted resolution cannot silently cover extra references.
    """

    rows: list[tuple[str, str]] = []
    if isinstance(value, Mapping):
        if ((value.get("kind") == "provisional"
             or value.get("contract") == REGISTRY_REFERENCE_CONTRACT)
                and value.get("entity_type") in ENTITY_TYPES
                and isinstance(value.get("provisional_id"), str)):
            rows.append((value["entity_type"], value["provisional_id"]))
        for key, child in value.items():
            if root and key in {FIELD, "resolution", "resolved_template"}:
                continue
            if _path == ("extensions",) and key == "whiteboard_handoff":
                # The retained board is provenance, not a second executable
                # loadout.  Its refs are already represented by equipment_inputs.
                continue
            rows.extend(_declared_provisional_usages(
                child, root=False, _path=(*_path, key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(_declared_provisional_usages(
                child, root=False, _path=(*_path, index)))
    return rows


def declared_provisional_usages(value: Any) -> list[tuple[str, str]]:
    """Canonical executable-use projection shared by execution and acceptance.

    Provenance-only snapshots such as ``extensions.whiteboard_handoff`` and
    the registry catalog itself are intentionally excluded.  Callers must not
    reimplement a broader tree walk, because doing so can turn retained UI
    provenance into a false formal-result dependency.
    """

    return list(_declared_provisional_usages(value))


def _revision(value: Any, path: str) -> int:
    if type(value) is not int or value < 0:
        _error("provisional_entity_usage_revision", "临时实体使用回执修订不合法",
               category="request_error", path=path)
    return value


def _usage_audit_projection(
    entity: Mapping[str, Any], *, consumer: str, authority: str,
    lookup_receipt_id: str, attestation_id: str | None,
    approval_id: str | None, plan: Mapping[str, Any],
    candidate_revision: int, stage_revision: int,
    database_snapshot_fingerprint: str,
) -> dict[str, Any]:
    """Freeze only the public-data fallback that the calculation consumed.

    The projection is result evidence, not a second execution input.  Its
    closed shape lets an exported approximate result explain the exact value,
    source, risk and authority proof that were used without retaining unused
    plan-local proposals.  Execution still requires a sealed resolver output;
    no consumer reads this audit projection as authority.
    """

    unsigned = {
        "contract": USAGE_AUDIT_CONTRACT,
        "audit_only": True,
        "entity_type": copy.deepcopy(entity["entity_type"]),
        "provisional_id": copy.deepcopy(entity["provisional_id"]),
        "display_name": copy.deepcopy(entity["display_name"]),
        "authorization_kind": copy.deepcopy(entity["authorization_kind"]),
        "version_claim": copy.deepcopy(entity["version_claim"]),
        "values": copy.deepcopy(entity["values"]),
        "source_refs": copy.deepcopy(entity["source_refs"]),
        "reason": copy.deepcopy(entity["reason"]),
        "necessity": copy.deepcopy(entity["necessity"]),
        "impact": copy.deepcopy(entity["impact"]),
        "risk": copy.deepcopy(entity["risk"]),
        "database_lookup": copy.deepcopy(entity["database_lookup"]),
        "public_data_diff": copy.deepcopy(entity["public_data_diff"]),
        "scope": "current_plan_only",
        "database_authority": False,
        "payload_fingerprint": entity_payload_fingerprint(entity),
        "authority_proof_ids": {
            "lookup_receipt_id": lookup_receipt_id,
            "attestation_id": attestation_id,
            "approval_id": approval_id,
        },
        "usage_context": {
            "plan_id": plan["plan_id"],
            "plan_fingerprint": _plan_fingerprint(plan),
            "candidate_revision": candidate_revision,
            "stage_revision": stage_revision,
            "database_snapshot_fingerprint": database_snapshot_fingerprint,
            "consumer": consumer,
            "authority": authority,
        },
    }
    return {**unsigned, "audit_fingerprint": _fingerprint(unsigned)}


def _validate_usage_audit(
    value: Any, entity: Mapping[str, Any], *, consumer: str, authority: str,
    lookup_receipt_id: str, attestation_id: str | None,
    approval_id: str | None, plan: Mapping[str, Any],
    candidate_revision: int, stage_revision: int,
    database_snapshot_fingerprint: str, path: str,
) -> None:
    """Verify the frozen audit projection against its live plan-local source."""

    _closed(value, _USAGE_AUDIT_KEYS, path)
    proof_path = f"{path}.authority_proof_ids"
    _closed(value["authority_proof_ids"], _USAGE_AUDIT_PROOF_KEYS, proof_path)
    context_path = f"{path}.usage_context"
    _closed(value["usage_context"], _USAGE_AUDIT_CONTEXT_KEYS, context_path)
    _digest(value["audit_fingerprint"], f"{path}.audit_fingerprint")
    unsigned = {
        key: copy.deepcopy(item) for key, item in value.items()
        if key != "audit_fingerprint"
    }
    if value["audit_fingerprint"] != _fingerprint(unsigned):
        _error(
            "provisional_entity_usage_audit_fingerprint",
            "临时公共实体的实际采用值审计指纹无效",
            category="request_error",
            path=f"{path}.audit_fingerprint",
        )
    expected = _usage_audit_projection(
        entity,
        consumer=consumer,
        authority=authority,
        lookup_receipt_id=lookup_receipt_id,
        attestation_id=attestation_id,
        approval_id=approval_id,
        plan=plan,
        candidate_revision=candidate_revision,
        stage_revision=stage_revision,
        database_snapshot_fingerprint=database_snapshot_fingerprint,
    )
    if value != expected:
        _error(
            "provisional_entity_usage_audit_mismatch",
            "临时公共实体的实际采用值、来源、理由或授权审计投影已变化",
            category="core_data_missing",
            path=path,
        )


def _usage_row_from_resolved(
    resolved: Mapping[str, Any], registry: Mapping[tuple[str, str], Mapping[str, Any]],
    *, plan: Mapping[str, Any], candidate_revision: int, stage_revision: int,
    database_snapshot_fingerprint: str,
) -> dict[str, Any]:
    if (not isinstance(resolved, _TrustedResolvedUse)
            or resolved._seal is not _RESOLUTION_SEAL):
        _error("provisional_entity_usage_resolution_invalid",
               "临时实体使用项不是本次App解析器签发的结果", category="request_error")
    expected_context = {
        "plan_id": plan.get("plan_id"),
        "authorization_basis_fingerprint": authorization_basis_fingerprint(plan),
        "candidate_revision": candidate_revision,
        "stage_revision": stage_revision,
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
    }
    if resolved._context != expected_context:
        _error("provisional_entity_usage_resolution_stale",
               "临时实体解析结果不属于当前方案、修订、阶段或数据库",
               category="core_data_missing")
    ref = resolved.get("ref")
    checked_ref = _validate_ref(ref)
    if checked_ref["kind"] != "provisional":
        _error("provisional_entity_usage_resolution_invalid",
               "数据库实体不应进入临时实体使用回执", category="request_error")
    identity = (checked_ref["entity_type"], checked_ref["provisional_id"])
    entity = registry.get(identity)
    if entity is None:
        _error("provisional_entity_usage_registry_mismatch",
               "解析结果没有对应的当前方案临时实体", category="core_data_missing")
    if (resolved.get("scope") != "current_plan_only"
            or resolved.get("database_authority") is not False
            or resolved.get("payload_fingerprint") != entity_payload_fingerprint(entity)
            or resolved.get("value") != entity["values"]):
        _error("provisional_entity_usage_resolution_stale",
               "解析结果与当前方案临时实体的载荷或权限不一致",
               category="core_data_missing")
    consumer = _identity(resolved.get("consumer"), "resolved.consumer")
    if consumer not in FORMAL_RESULT_CONSUMERS:
        _error("provisional_entity_usage_consumer_not_executable",
               "只读预览解析不能证明临时实体已进入实际计算",
               category="engine_capability_missing")
    authority = resolved.get("authority")
    if authority not in RESOLVED_AUTHORITIES:
        _error("provisional_entity_usage_authority_invalid",
               "临时实体解析结果缺少受支持的权限来源", category="request_error")
    lookup_id = entity["database_lookup"]["receipt_id"]
    attestation_id: str | None = None
    approval_id: str | None = None
    if entity["authorization_kind"] == "database_missing":
        attestation = entity.get("attestation")
        if (authority != "agent_reported_database_missing"
                or not isinstance(attestation, Mapping)
                or resolved.get("lookup_receipt_id") != lookup_id
                or resolved.get("attestation_id") != attestation.get("attestation_id")):
            _error("provisional_entity_usage_authority_mismatch",
                   "数据库缺失型使用没有匹配的App查询与Agent材料化证明",
                   category="request_error")
        attestation_id = attestation["attestation_id"]
    else:
        approval = entity.get("approval")
        if (authority != "user_confirmed_public_data_patch"
                or not isinstance(approval, Mapping)
                or resolved.get("lookup_receipt_id") != lookup_id
                or resolved.get("approval_id") != approval.get("approval_id")):
            _error("provisional_entity_usage_authority_mismatch",
                   "公共数据纠错使用没有匹配的App查询与用户精确批准",
                   category="request_error")
        approval_id = approval["approval_id"]
    row = {
        "entity_type": identity[0],
        "provisional_id": identity[1],
        "consumer": consumer,
        "authority": authority,
        "payload_fingerprint": entity_payload_fingerprint(entity),
        "lookup_receipt_id": lookup_id,
        "attestation_id": attestation_id,
        "approval_id": approval_id,
    }
    row["audit"] = _usage_audit_projection(
        entity,
        consumer=consumer,
        authority=authority,
        lookup_receipt_id=lookup_id,
        attestation_id=attestation_id,
        approval_id=approval_id,
        plan=plan,
        candidate_revision=candidate_revision,
        stage_revision=stage_revision,
        database_snapshot_fingerprint=database_snapshot_fingerprint,
    )
    return row


def build_usage_receipt(
    plan: Mapping[str, Any], resolved_uses: list[Mapping[str, Any]], *,
    candidate_revision: int, stage_revision: int,
    database_snapshot_fingerprint: str,
) -> dict[str, Any]:
    """Freeze App-resolved provisional uses for one exact calculation input.

    Callers must pass the outputs of :func:`resolve_entity_ref` from the actual
    execution consumers.  Declarations that are merely present in the registry
    do not enter this receipt and therefore do not affect result eligibility.
    """

    if not isinstance(plan, Mapping):
        _error("provisional_entity_usage_plan_invalid", "临时实体使用回执缺少当前方案",
               category="request_error")
    plan_id = _identity(plan.get("plan_id"), "plan.plan_id")
    _revision(candidate_revision, "candidate_revision")
    _revision(stage_revision, "stage_revision")
    _digest(database_snapshot_fingerprint, "database_snapshot_fingerprint")
    registry_rows = validate_registry(plan)
    registry = {
        (row["entity_type"], row["provisional_id"]): row for row in registry_rows
    }
    if (not isinstance(resolved_uses, list) or not resolved_uses
            or len(resolved_uses) > MAX_ENTITIES):
        _error("provisional_entity_usage_count",
               "临时实体使用回执需要1至32项实际解析结果", category="request_error")
    entities = [
        _usage_row_from_resolved(
            row, registry, plan=plan, candidate_revision=candidate_revision,
            stage_revision=stage_revision,
            database_snapshot_fingerprint=database_snapshot_fingerprint,
        )
        for row in resolved_uses
    ]
    declared = sorted(declared_provisional_usages(plan))
    resolved_identities = sorted(
        (row["entity_type"], row["provisional_id"]) for row in entities
    )
    if declared != resolved_identities:
        _error("provisional_entity_usage_coverage_mismatch",
               "临时实体使用回执没有逐项覆盖当前有效方案中的全部引用",
               category="core_data_missing")
    entities.sort(key=lambda row: (
        row["entity_type"], row["provisional_id"], row["consumer"],
        row["authority"], row["lookup_receipt_id"],
        row["attestation_id"] or "", row["approval_id"] or "",
    ))
    unsigned = {
        "contract": USAGE_RECEIPT_CONTRACT,
        "issued_by": "app_provisional_entity_resolver",
        "status": "verified",
        "scope": "current_plan_only",
        "database_authority": False,
        "plan_id": plan_id,
        "plan_fingerprint": _plan_fingerprint(plan),
        "candidate_revision": candidate_revision,
        "stage_revision": stage_revision,
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
        "entities": entities,
    }
    return {**unsigned, "receipt_fingerprint": _fingerprint(unsigned)}


def validate_usage_receipt(
    value: Any, plan: Mapping[str, Any], *, candidate_revision: int,
    stage_revision: int, database_snapshot_fingerprint: str,
) -> dict[str, Any]:
    """Validate one usage receipt against the current plan and App context."""

    _closed(value, _USAGE_RECEIPT_KEYS, "provisional_entity_usage_receipt")
    if (value["contract"] != USAGE_RECEIPT_CONTRACT
            or value["issued_by"] != "app_provisional_entity_resolver"
            or value["status"] != "verified"
            or value["scope"] != "current_plan_only"
            or value["database_authority"] is not False):
        _error("provisional_entity_usage_receipt_invalid",
               "临时实体使用回执合同或权限范围无效", category="request_error")
    plan_id = _identity(plan.get("plan_id"), "plan.plan_id")
    _identity(value["plan_id"], "provisional_entity_usage_receipt.plan_id")
    _digest(value["plan_fingerprint"],
            "provisional_entity_usage_receipt.plan_fingerprint")
    _revision(value["candidate_revision"],
              "provisional_entity_usage_receipt.candidate_revision")
    _revision(value["stage_revision"],
              "provisional_entity_usage_receipt.stage_revision")
    _revision(candidate_revision, "candidate_revision")
    _revision(stage_revision, "stage_revision")
    _digest(value["database_snapshot_fingerprint"],
            "provisional_entity_usage_receipt.database_snapshot_fingerprint")
    _digest(database_snapshot_fingerprint, "database_snapshot_fingerprint")
    expected_context = {
        "plan_id": plan_id,
        "plan_fingerprint": _plan_fingerprint(plan),
        "candidate_revision": candidate_revision,
        "stage_revision": stage_revision,
        "database_snapshot_fingerprint": database_snapshot_fingerprint,
    }
    if any(value.get(key) != expected for key, expected in expected_context.items()):
        _error("provisional_entity_usage_receipt_stale",
               "临时实体使用回执不属于当前方案、修订、阶段或数据库快照",
               category="core_data_missing")
    entities = value["entities"]
    if not isinstance(entities, list) or not 1 <= len(entities) <= MAX_ENTITIES:
        _error("provisional_entity_usage_count", "临时实体使用回执项数不合法",
               category="request_error")
    registry = {
        (row["entity_type"], row["provisional_id"]): row
        for row in validate_registry(plan)
    }
    identities: list[tuple[str, str]] = []
    for index, row in enumerate(entities):
        path = f"provisional_entity_usage_receipt.entities.{index}"
        _closed(row, _USAGE_ENTITY_KEYS, path)
        if row["entity_type"] not in ENTITY_TYPES:
            _error("provisional_entity_usage_type", "临时实体使用类型不合法",
                   category="request_error", path=f"{path}.entity_type")
        provisional_id = _identity(row["provisional_id"], f"{path}.provisional_id")
        consumer = _identity(row["consumer"], f"{path}.consumer")
        if consumer not in FORMAL_RESULT_CONSUMERS:
            _error("provisional_entity_usage_consumer_not_executable",
                   "只读预览使用不能作为正式结果的临时实体审计",
                   category="engine_capability_missing",
                   path=f"{path}.consumer")
        if row["authority"] not in RESOLVED_AUTHORITIES:
            _error("provisional_entity_usage_authority_invalid",
                   "临时实体使用权限来源不合法", category="request_error",
                   path=f"{path}.authority")
        _digest(row["payload_fingerprint"], f"{path}.payload_fingerprint")
        lookup_id = _identity(row["lookup_receipt_id"], f"{path}.lookup_receipt_id")
        for proof in ("attestation_id", "approval_id"):
            if row[proof] is not None:
                _identity(row[proof], f"{path}.{proof}")
        identity = (row["entity_type"], provisional_id)
        entity = registry.get(identity)
        if entity is None:
            _error("provisional_entity_usage_registry_mismatch",
                   "使用回执引用了当前方案未登记的临时实体",
                   category="core_data_missing", path=path)
        if (row["payload_fingerprint"] != entity_payload_fingerprint(entity)
                or lookup_id != entity["database_lookup"]["receipt_id"]):
            _error("provisional_entity_usage_receipt_stale",
                   "使用回执的实体载荷或数据库查询证明已变化",
                   category="core_data_missing", path=path)
        if entity["authorization_kind"] == "database_missing":
            attestation = entity.get("attestation") or {}
            valid_authority = (
                row["authority"] == "agent_reported_database_missing"
                and row["attestation_id"] == attestation.get("attestation_id")
                and row["approval_id"] is None
            )
        else:
            approval = entity.get("approval") or {}
            valid_authority = (
                row["authority"] == "user_confirmed_public_data_patch"
                and row["approval_id"] == approval.get("approval_id")
                and row["attestation_id"] is None
            )
        if not valid_authority:
            _error("provisional_entity_usage_authority_mismatch",
                   "使用回执与实体的缺失证明或用户纠错批准不匹配",
                   category="request_error", path=path)
        _validate_usage_audit(
            row["audit"],
            entity,
            consumer=consumer,
            authority=row["authority"],
            lookup_receipt_id=lookup_id,
            attestation_id=row["attestation_id"],
            approval_id=row["approval_id"],
            plan=plan,
            candidate_revision=candidate_revision,
            stage_revision=stage_revision,
            database_snapshot_fingerprint=database_snapshot_fingerprint,
            path=f"{path}.audit",
        )
        identities.append(identity)
    if sorted(identities) != sorted(declared_provisional_usages(plan)):
        _error("provisional_entity_usage_coverage_mismatch",
               "使用回执没有逐项覆盖当前有效方案中的全部临时实体引用",
               category="core_data_missing")
    unsigned = {
        key: copy.deepcopy(item) for key, item in value.items()
        if key != "receipt_fingerprint"
    }
    _digest(value["receipt_fingerprint"],
            "provisional_entity_usage_receipt.receipt_fingerprint")
    if value["receipt_fingerprint"] != _fingerprint(unsigned):
        _error("provisional_entity_usage_receipt_fingerprint",
               "临时实体使用回执指纹无效", category="request_error")
    return copy.deepcopy(value)


def validate_current_session_usage_receipt(
    result: Mapping[str, Any], execution_receipt: Mapping[str, Any],
    candidate_session: Any,
) -> dict[str, Any] | None:
    """Authorize a provisional-entity result against its live candidate session.

    ``candidate_session`` is deliberately separate from the internal execution
    verification session used by ``verification_scope``.  The latter only
    deduplicates trusted replays inside one call and must never become authority
    for plan-local public-data fallbacks.
    """

    result_receipt = result.get("provisional_entity_usage_receipt")
    calculated_receipt = execution_receipt.get(
        "provisional_entity_usage_receipt"
    )
    result_fidelity = result.get("result_fidelity")
    receipt_fidelity = execution_receipt.get("result_fidelity")
    if result_receipt is None and calculated_receipt is None:
        # A formal result may still be exported through the long-standing
        # result-only API.  In that path there is deliberately no execution
        # receipt to compare.  Validate every fidelity marker that is actually
        # present instead of treating an omitted optional receipt marker as a
        # contradictory value.  Provisional results remain fail-closed below:
        # they always carry a usage receipt and require the live session.
        present_fidelities = tuple(
            value for value in (result_fidelity, receipt_fidelity)
            if value is not None
        )
        if any(
            value != FORMAL_RESULT_FIDELITY
            for value in present_fidelities
        ):
            _error(
                "provisional_entity_result_fidelity_invalid",
                "结果精度标识与实际临时公共资料使用情况不一致。",
                category="request_error",
                path="result_fidelity",
            )
        return None
    if (not isinstance(result_receipt, Mapping)
            or dict(result_receipt) != calculated_receipt):
        _error(
            "provisional_entity_usage_receipt_invalid",
            "临时公共实体的结果与执行回执不一致，请在原会话重新计算。",
            category="request_error",
            path="provisional_entity_usage_receipt",
        )
    if candidate_session is None:
        _error(
            "provisional_entity_usage_receipt_untrusted",
            "临时公共实体只在当前原生Agent方案中有效；导出需要原会话的受信回执。",
            category="request_error",
            path="provisional_entity_usage_receipt",
        )
    if (execution_receipt.get("plan_id") != result_receipt.get("plan_id")
            or execution_receipt.get("result_fingerprint") != _fingerprint(result)):
        _error(
            "provisional_entity_usage_receipt_invalid",
            "临时公共实体使用回执没有绑定当前计算结果，请在原会话重新计算。",
            category="request_error",
            path="provisional_entity_usage_receipt",
        )
    if (
        result_fidelity != PROVISIONAL_RESULT_FIDELITY
        or receipt_fidelity != PROVISIONAL_RESULT_FIDELITY
    ):
        _error(
            "provisional_entity_result_fidelity_invalid",
            "临时公共资料结果必须在结果与执行回执中明确标注为近似。",
            category="request_error",
            path="result_fidelity",
        )
    try:
        trusted = candidate_session.provisional_usage_receipt()
        if trusted != result_receipt:
            raise ValueError("App保留的临时实体使用回执不一致")
        return validate_usage_receipt(
            result_receipt,
            candidate_session.candidate,
            candidate_revision=candidate_session.revision,
            stage_revision=getattr(candidate_session, "stage_state", {}).get(
                "stage_revision", 0
            ),
            database_snapshot_fingerprint=_fingerprint(
                candidate_session.database_snapshot
            ),
        )
    except ProvisionalEntityError:
        raise
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        _error(
            "provisional_entity_usage_receipt_untrusted",
            "临时公共实体的当前会话授权已失效，请重新核对后计算："
            + str(exc),
            category="request_error",
            path="provisional_entity_usage_receipt",
        )


def capabilities() -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "field": FIELD,
        "entity_types": sorted(ENTITY_TYPES),
        "scope": "current_plan_only",
        "database_authority": False,
        "authorization_kinds": {
            "database_missing": "trusted_app_lookup_plus_verified_agent_source_and_app_attestation",
            "public_data_patch": "trusted_app_lookup_plus_user_decision_ledger",
        },
        "unavailable_is_missing": False,
        "typed_loadout_comparison": {
            "contract": "public-entity-loadout-baseline-projection-v1",
            "lookup_candidate_field": "app_loadout_comparison",
            "scope": "actual character level columns, exact weapon-state numerical queries and set thresholds",
            "source": "raw query objects and full numeric responses retained; wrappers/policies are explicitly App/Agent metadata",
            "missing": "explicit source_missing/source_unavailable; never zero or copied replacement",
        },
        "formal_result_usage": {
            "contract": USAGE_RECEIPT_CONTRACT,
            "used_value_audit_contract": USAGE_AUDIT_CONTRACT,
            "authority": "trusted_out_of_band_app_receipt",
            "audit_only": True,
            "unused_registry_payloads_exported": False,
            "unused_registry_blocks": False,
            "status": "equipment_composer_wired_other_consumers_fail_closed",
        },
        "supported_consumers": sorted(SUPPORTED_CONSUMERS),
        "formal_result_consumers": sorted(FORMAL_RESULT_CONSUMERS),
        "production_consumer_status": {
            "identity_preview": "diagnostic_only_not_formal_usage",
            "agent_materialization": "shared_resolver_facade_ready_actual_consumer_unwired",
            "equipment_composer": "formal_usage_receipt_wired",
            "character_panel_basis": "resolved_inside_equipment_composer",
        },
        "unsupported_consumers_fail_as": "engine_capability_missing",
        "schema": copy.deepcopy(PROVISIONAL_ENTITIES_SCHEMA),
        "entity_ref_schema": copy.deepcopy(ENTITY_REF_SCHEMA),
    }
