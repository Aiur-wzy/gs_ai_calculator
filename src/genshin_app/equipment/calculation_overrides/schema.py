"""Closed declaration contract for Agent-reviewed calculation overrides.

This module deliberately validates declarations only.  It does not apply an
override and it never makes a declaration executable.  Composition owns the
future AutomaticBaselineBundle -> EffectiveExecutionBundle transition.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

CONTRACT = "calculation-event-overrides-v1"
PROVISIONAL_REFERENCE_CONTRACT = "provisional-entity-registry-reference-v1"
FIELD = "calculation_event_overrides"
MAX_ITEMS = 256
MAX_BYTES = 512 * 1024
MAX_ITEM_BYTES = 64 * 1024
MAX_DEPTH = 32

MODES = (
    "add_event",
    "replace_event",
    "suppress_event",
    "replace_input",
    "replace_state_transition",
    "replace_computed_value",
    "provisional_entity",
)
CONTRACT_READY_MODES = frozenset(
    {
        "add_event",
        "replace_event",
        "suppress_event",
        "replace_input",
        "replace_state_transition",
        "replace_computed_value",
    }
)
REASON_CODES = (
    "engine_disagreement",
    "unsupported_special_case",
    "user_scenario",
    "source_correction",
    "approximation",
    "provisional_public_entity",
)
TARGET_DOMAINS = (
    "manual_hit",
    "manual_buff",
    "rotation_action",
    "rotation_event",
    "mechanics_rule",
    "mechanics_hit_event",
    "mechanics_hit_pattern",
    "mechanics_application_event",
    "mechanics_condition_event",
    "parameter_input",
    "state_transition",
    "computed_event",
    "public_entity",
)
EVENT_TARGET_DOMAINS = frozenset(
    {
        "manual_hit",
        "manual_buff",
        "rotation_action",
        "rotation_event",
        "mechanics_rule",
        "mechanics_hit_event",
        "mechanics_hit_pattern",
        "mechanics_application_event",
        "mechanics_condition_event",
    }
)
IMPACT_DOMAINS = (
    "damage",
    "elemental_application",
    "reaction",
    "buff",
    "resource",
    "lifecycle",
    "action_timing",
    "equipment",
    "public_identity",
)
SOURCE_KINDS = (
    "user_input",
    "database",
    "appendix",
    "web",
    "engine_observation",
    "local_document",
)

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "result",
        "results",
        "summary",
        "total",
        "total_damage",
        "dps",
        "trace",
        "receipt",
        "result_receipt",
        "calculation_totals",
        "calculated",
    }
)


class OverrideDeclarationError(ValueError):
    """Stable declaration error; callers may map it to their domain error."""

    def __init__(self, reason: str, message: str | None = None, *, path: str | None = None):
        self.reason = reason
        self.path = path
        super().__init__(message or reason)


def _enum(*values: str) -> dict[str, Any]:
    return {"enum": list(values)}


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def _closed(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


_ID = {"type": "string", "minLength": 1, "maxLength": 128, "pattern": _ID_RE.pattern}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 4096}
_SHA = {"type": "string", "pattern": _SHA_RE.pattern}
_NULLABLE_SHA = _nullable(_SHA)

SOURCE_REF_SCHEMA = _closed(
    {
        "source_id": _ID,
        "kind": _enum(*SOURCE_KINDS),
        "locator": {"type": "string", "minLength": 1, "maxLength": 2048},
        "summary": {"type": "string", "minLength": 1, "maxLength": 512},
        "fingerprint": _NULLABLE_SHA,
    }
)

TARGET_SCHEMA = _closed(
    {
        "domain": _enum(*TARGET_DOMAINS),
        "entity_id": _ID,
        "scope_id": _ID,
        "topology_node_id": _nullable(_ID),
        "execution_binding_id": _nullable(_ID),
        "action_id": _nullable(_ID),
        "component_id": _nullable(_ID),
        "occurrence_id": _nullable(_ID),
        "target_id": _nullable(_ID),
    }
)

BASIS_SCHEMA = _closed(
    {
        "plan_input_fingerprint": _SHA,
        "automatic_baseline_fingerprint": _SHA,
        "database_snapshot_fingerprint": _SHA,
        "engine_contract_fingerprint": _SHA,
        "topology_fingerprint": _NULLABLE_SHA,
        "execution_bindings_fingerprint": _NULLABLE_SHA,
    }
)

ITEM_SCHEMA = _closed(
    {
        "override_id": _ID,
        "mode": _enum(*MODES),
        "target": TARGET_SCHEMA,
        "coverage_key": _ID,
        "supersedes": {
            "type": "array",
            "items": _ID,
            "maxItems": 32,
        },
        "reason_code": _enum(*REASON_CODES),
        "reason": _TEXT,
        "source_refs": {
            "type": "array",
            "items": SOURCE_REF_SCHEMA,
            "minItems": 1,
            "maxItems": 16,
        },
        "impact_domains": {
            "type": "array",
            "items": _enum(*IMPACT_DOMAINS),
            "minItems": 1,
            "maxItems": len(IMPACT_DOMAINS),
        },
        "payload": {"type": "object"},
        "automatic_target_fingerprint": _SHA,
        "accepted_fingerprint": _NULLABLE_SHA,
        "applies_to_version": {"type": "string", "minLength": 1, "maxLength": 128},
        "unknowns": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 1024},
            "maxItems": 32,
        },
        "invalidation_conditions": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 1024},
            "minItems": 1,
            "maxItems": 32,
        },
    }
)

OVERRIDES_SCHEMA = _closed(
    {
        "contract": _enum(CONTRACT),
        "basis": BASIS_SCHEMA,
        "items": {"type": "array", "items": ITEM_SCHEMA, "maxItems": MAX_ITEMS},
    }
)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def execution_support(mode: str) -> str:
    if mode not in MODES:
        raise OverrideDeclarationError("override_mode_invalid", "未知覆盖模式", path="mode")
    return "implemented" if mode in CONTRACT_READY_MODES else "not_implemented"


def _exact_object(value: Any, keys: set[str], path: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise OverrideDeclarationError("override_shape_invalid", "需要对象", path=path)
    unknown = set(value) - keys
    missing = keys - set(value)
    if unknown or missing:
        message = "字段不完整或包含未知字段"
        raise OverrideDeclarationError("override_shape_invalid", message, path=path)
    return value


def _text_value(value: Any, path: str, *, maximum: int, ident: bool = False) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise OverrideDeclarationError("override_text_invalid", "文本为空或超过长度限制", path=path)
    if ident and _ID_RE.fullmatch(value) is None:
        raise OverrideDeclarationError("override_identity_invalid", "稳定ID格式不合法", path=path)
    return value


def _sha(value: Any, path: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or _SHA_RE.fullmatch(value) is None:
        raise OverrideDeclarationError("override_fingerprint_invalid", "需要小写SHA-256", path=path)
    return value


def _finite_json(
    value: Any,
    path: str,
    *,
    depth: int = 0,
    parents: set[int] | None = None,
    forbid_outputs: bool = False,
) -> None:
    if depth > MAX_DEPTH:
        raise OverrideDeclarationError("override_payload_too_deep", "JSON嵌套过深", path=path)
    parents = set() if parents is None else parents
    if type(value) is dict:
        if id(value) in parents:
            raise OverrideDeclarationError("override_payload_cycle", "JSON存在循环引用", path=path)
        parents.add(id(value))
        for key, child in value.items():
            if type(key) is not str:
                raise OverrideDeclarationError("override_payload_invalid", "JSON键必须是文本", path=path)
            if forbid_outputs and key.casefold() in _FORBIDDEN_PAYLOAD_KEYS:
                raise OverrideDeclarationError(
                    "override_final_result_forbidden",
                    "覆盖不得写入结果、汇总、DPS、trace或回执",
                    path=f"{path}.{key}",
                )
            _finite_json(
                child,
                f"{path}.{key}",
                depth=depth + 1,
                parents=parents,
                forbid_outputs=forbid_outputs,
            )
        parents.remove(id(value))
    elif type(value) is list:
        if id(value) in parents:
            raise OverrideDeclarationError("override_payload_cycle", "JSON存在循环引用", path=path)
        parents.add(id(value))
        for index, child in enumerate(value):
            _finite_json(
                child,
                f"{path}[{index}]",
                depth=depth + 1,
                parents=parents,
                forbid_outputs=forbid_outputs,
            )
        parents.remove(id(value))
    elif value is None or type(value) in (str, bool, int):
        return
    elif type(value) is float:
        if not math.isfinite(value):
            raise OverrideDeclarationError("override_number_invalid", "数值必须有限", path=path)
    else:
        raise OverrideDeclarationError("override_payload_invalid", "必须是JSON兼容值", path=path)


def _unique(values: list[Any], path: str) -> None:
    try:
        if len(values) != len(set(values)):
            raise OverrideDeclarationError("override_duplicate", "列表项目不能重复", path=path)
    except TypeError as exc:
        raise OverrideDeclarationError("override_shape_invalid", "列表项目格式不合法", path=path) from exc


def validate_declarations(value: Any, *, plan_version: int | None = None) -> dict[str, Any]:
    """Validate an untrusted declaration without granting execution authority."""
    root_keys = {"contract", "basis", "items"}
    basis_keys = set(BASIS_SCHEMA["properties"])
    item_keys = set(ITEM_SCHEMA["properties"])
    target_keys = set(TARGET_SCHEMA["properties"])
    source_keys = set(SOURCE_REF_SCHEMA["properties"])
    root = _exact_object(value, root_keys, FIELD)
    _finite_json(root, FIELD)
    try:
        serialized = canonical_bytes(root)
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise OverrideDeclarationError("override_payload_invalid", str(exc), path=FIELD) from exc
    if len(serialized) > MAX_BYTES:
        raise OverrideDeclarationError("override_declaration_too_large", "覆盖声明超过512KiB", path=FIELD)
    if root["contract"] != CONTRACT:
        raise OverrideDeclarationError("override_contract_invalid", "覆盖合同版本不受支持", path=f"{FIELD}.contract")
    if plan_version is not None and plan_version != 3:
        raise OverrideDeclarationError(
            "override_plan_version_invalid", "统一覆盖声明需要原生方案版本3", path=FIELD
        )
    basis = _exact_object(root["basis"], basis_keys, f"{FIELD}.basis")
    for key in basis_keys:
        _sha(basis[key], f"{FIELD}.basis.{key}", nullable=key.endswith("_fingerprint") and key in {
            "topology_fingerprint", "execution_bindings_fingerprint"
        })
    items = root["items"]
    if type(items) is not list or len(items) > MAX_ITEMS:
        raise OverrideDeclarationError("override_items_invalid", "覆盖项目必须是最多256项的列表", path=f"{FIELD}.items")
    override_ids: list[str] = []
    coverage_keys: list[str] = []
    for index, raw in enumerate(items):
        path = f"{FIELD}.items[{index}]"
        if len(canonical_bytes(raw)) > MAX_ITEM_BYTES:
            raise OverrideDeclarationError("override_item_too_large", "单项覆盖超过64KiB", path=path)
        item = _exact_object(raw, item_keys, path)
        override_ids.append(_text_value(item["override_id"], f"{path}.override_id", maximum=128, ident=True))
        coverage_keys.append(_text_value(item["coverage_key"], f"{path}.coverage_key", maximum=128, ident=True))
        mode = item["mode"]
        execution_support(mode)
        target = _exact_object(item["target"], target_keys, f"{path}.target")
        if target["domain"] not in TARGET_DOMAINS:
            raise OverrideDeclarationError("override_target_invalid", "目标域不受支持", path=f"{path}.target.domain")
        for key in ("entity_id", "scope_id"):
            _text_value(target[key], f"{path}.target.{key}", maximum=128, ident=True)
        for key in target_keys - {"domain", "entity_id", "scope_id"}:
            if target[key] is not None:
                _text_value(target[key], f"{path}.target.{key}", maximum=128, ident=True)
        if (target["topology_node_id"] is None
                and target["execution_binding_id"] is None
                and target["domain"] != "public_entity"):
            raise OverrideDeclarationError(
                "override_target_unbound",
                "非公共实体覆盖至少需要拓扑节点或执行绑定身份",
                path=f"{path}.target",
            )
        supersedes = item["supersedes"]
        if type(supersedes) is not list or len(supersedes) > 32:
            raise OverrideDeclarationError("override_supersedes_invalid", "supersedes最多32项", path=f"{path}.supersedes")
        for offset, owner_id in enumerate(supersedes):
            _text_value(owner_id, f"{path}.supersedes[{offset}]", maximum=128, ident=True)
        _unique(supersedes, f"{path}.supersedes")
        if mode == "add_event" and supersedes:
            raise OverrideDeclarationError("override_supersedes_invalid", "新增事件不能释放既有所有权", path=f"{path}.supersedes")
        if mode != "add_event" and not supersedes:
            raise OverrideDeclarationError("override_supersedes_required", "替换或排除必须声明supersedes", path=f"{path}.supersedes")
        if item["reason_code"] not in REASON_CODES:
            raise OverrideDeclarationError("override_reason_invalid", "覆盖原因分类无效", path=f"{path}.reason_code")
        _text_value(item["reason"], f"{path}.reason", maximum=4096)
        sources = item["source_refs"]
        if type(sources) is not list or not 1 <= len(sources) <= 16:
            raise OverrideDeclarationError("override_sources_invalid", "覆盖需要1至16条来源", path=f"{path}.source_refs")
        source_ids: list[str] = []
        for offset, raw_source in enumerate(sources):
            source_path = f"{path}.source_refs[{offset}]"
            source = _exact_object(raw_source, source_keys, source_path)
            source_ids.append(_text_value(source["source_id"], f"{source_path}.source_id", maximum=128, ident=True))
            if source["kind"] not in SOURCE_KINDS:
                raise OverrideDeclarationError("override_source_invalid", "来源类型无效", path=f"{source_path}.kind")
            _text_value(source["locator"], f"{source_path}.locator", maximum=2048)
            _text_value(source["summary"], f"{source_path}.summary", maximum=512)
            _sha(source["fingerprint"], f"{source_path}.fingerprint", nullable=True)
        _unique(source_ids, f"{path}.source_refs")
        impacts = item["impact_domains"]
        if type(impacts) is not list or not 1 <= len(impacts) <= len(IMPACT_DOMAINS):
            raise OverrideDeclarationError("override_impact_invalid", "影响域不能为空", path=f"{path}.impact_domains")
        if any(domain not in IMPACT_DOMAINS for domain in impacts):
            raise OverrideDeclarationError("override_impact_invalid", "存在未知影响域", path=f"{path}.impact_domains")
        _unique(impacts, f"{path}.impact_domains")
        if type(item["payload"]) is not dict or not item["payload"]:
            raise OverrideDeclarationError("override_payload_invalid", "覆盖payload必须是非空对象", path=f"{path}.payload")
        _finite_json(item["payload"], f"{path}.payload", forbid_outputs=True)
        _sha(item["automatic_target_fingerprint"], f"{path}.automatic_target_fingerprint")
        _sha(item["accepted_fingerprint"], f"{path}.accepted_fingerprint", nullable=True)
        _text_value(item["applies_to_version"], f"{path}.applies_to_version", maximum=128)
        for key, minimum in (("unknowns", 0), ("invalidation_conditions", 1)):
            values = item[key]
            if type(values) is not list or len(values) < minimum or len(values) > 32:
                raise OverrideDeclarationError("override_notes_invalid", "覆盖说明列表长度无效", path=f"{path}.{key}")
            for offset, note in enumerate(values):
                _text_value(note, f"{path}.{key}[{offset}]", maximum=1024)
            _unique(values, f"{path}.{key}")
        if mode == "provisional_entity":
            if item["reason_code"] != "provisional_public_entity" or target["domain"] != "public_entity":
                raise OverrideDeclarationError(
                    "override_provisional_invalid",
                    "临时公共实体必须使用专用原因和public_entity目标域",
                    path=path,
                )
            reference_keys = {
                "contract", "entity_type", "provisional_id",
                "authorization_kind", "payload_fingerprint",
                "lookup_receipt_id", "attestation_id", "approval_id",
            }
            if set(item["payload"]) != reference_keys:
                raise OverrideDeclarationError(
                    "override_provisional_invalid",
                    "临时实体覆盖只能引用当前方案registry，不能内嵌第二套实体payload",
                    path=f"{path}.payload",
                )
            if item["payload"]["contract"] != PROVISIONAL_REFERENCE_CONTRACT:
                raise OverrideDeclarationError(
                    "override_provisional_invalid", "临时实体registry引用合同不受支持",
                    path=f"{path}.payload.contract",
                )
            for key in ("entity_type", "provisional_id", "lookup_receipt_id"):
                _text_value(item["payload"][key], f"{path}.payload.{key}", maximum=128,
                            ident=True)
            if item["payload"]["authorization_kind"] not in {
                "database_missing", "public_data_patch"
            }:
                raise OverrideDeclarationError(
                    "override_provisional_invalid", "临时实体授权类型不受支持",
                    path=f"{path}.payload.authorization_kind",
                )
            for key in ("attestation_id", "approval_id"):
                if item["payload"][key] is not None:
                    _text_value(item["payload"][key], f"{path}.payload.{key}",
                                maximum=128, ident=True)
            if (item["payload"]["authorization_kind"] == "database_missing"
                    and (item["payload"]["attestation_id"] is None
                         or item["payload"]["approval_id"] is not None)):
                raise OverrideDeclarationError(
                    "override_provisional_invalid",
                    "数据库缺失型临时实体需要Agent材料化回执且不以用户批准授权",
                    path=f"{path}.payload",
                )
            if (item["payload"]["authorization_kind"] == "public_data_patch"
                    and (item["payload"]["approval_id"] is None
                         or item["payload"]["attestation_id"] is not None)):
                raise OverrideDeclarationError(
                    "override_provisional_invalid",
                    "公共数据纠错型临时实体需要用户批准且不能冒充缺失型回执",
                    path=f"{path}.payload",
                )
            _sha(item["payload"]["payload_fingerprint"],
                 f"{path}.payload.payload_fingerprint")
            if item["payload"]["provisional_id"] != target["entity_id"]:
                raise OverrideDeclarationError(
                    "override_provisional_invalid",
                    "临时实体payload身份与稳定目标身份不一致",
                    path=f"{path}.payload.provisional_id",
                )
        elif item["reason_code"] == "provisional_public_entity":
            raise OverrideDeclarationError("override_reason_invalid", "临时实体原因只适用于provisional_entity", path=path)
        if mode in {"add_event", "replace_event", "suppress_event"} and target["domain"] not in EVENT_TARGET_DOMAINS:
            raise OverrideDeclarationError("override_target_invalid", "事件覆盖必须指向事件域", path=f"{path}.target.domain")
        if mode == "replace_input" and target["domain"] != "parameter_input":
            raise OverrideDeclarationError("override_target_invalid", "输入替换必须指向parameter_input", path=f"{path}.target.domain")
        if mode == "replace_state_transition" and target["domain"] != "state_transition":
            raise OverrideDeclarationError("override_target_invalid", "状态替换必须指向state_transition", path=f"{path}.target.domain")
        if mode == "replace_computed_value" and target["domain"] != "computed_event":
            raise OverrideDeclarationError("override_target_invalid", "固定值替换必须绑定computed_event", path=f"{path}.target.domain")
        if mode == "replace_state_transition" and (
            set(item["payload"]) != {"transition"}
            or type(item["payload"].get("transition")) is not dict
        ):
            raise OverrideDeclarationError(
                "override_state_transition_invalid",
                "状态迁移替换需要且只能提供完整transition对象",
                path=f"{path}.payload",
            )
        if mode == "replace_computed_value":
            if set(item["payload"]) != {"expected", "precision"}:
                raise OverrideDeclarationError(
                    "override_computed_value_invalid",
                    "固定单事件值必须提供expected与precision说明",
                    path=f"{path}.payload",
                )
            expected = item["payload"].get("expected")
            if type(expected) not in (int, float) or isinstance(expected, bool) or not math.isfinite(expected) or expected < 0:
                raise OverrideDeclarationError(
                    "override_computed_value_invalid",
                    "固定单事件值需要有限非负expected，不能提供总数",
                    path=f"{path}.payload.expected",
                )
            precision = item["payload"].get("precision")
            if (
                type(precision) is not dict
                or set(precision)
                != {"kind", "method", "uncertainty", "state_effects"}
                or precision.get("kind") not in {"exact", "approximate"}
                or precision.get("state_effects") != "none"
            ):
                raise OverrideDeclarationError(
                    "override_computed_precision_invalid",
                    "固定值必须声明精度、方法、不确定性且不得伪造状态后继",
                    path=f"{path}.payload.precision",
                )
            for key in ("method", "uncertainty"):
                _text_value(
                    precision.get(key),
                    f"{path}.payload.precision.{key}",
                    maximum=4096,
                )
            if (item["reason_code"] == "approximation"
                    and precision["kind"] != "approximate"):
                raise OverrideDeclarationError(
                    "override_computed_precision_required",
                    "近似原因必须使用approximate精度声明",
                    path=f"{path}.payload.precision",
                )
    _unique(override_ids, f"{FIELD}.items.override_id")
    _unique(coverage_keys, f"{FIELD}.items.coverage_key")
    return {"valid": True, "contract": CONTRACT, "item_count": len(items)}


def capabilities() -> dict[str, Any]:
    """Publish the execution subsystem, not merely the declaration syntax."""
    return {
        "contract": CONTRACT,
        "field": FIELD,
        "execution_connected": True,
        "modes": {mode: execution_support(mode) for mode in MODES},
        "execution_targets": {
            "add_event": {
                "domains": ["mechanics_hit_event"],
                "relation": "bound_manual_hit_plus_exact_action_anchor",
            },
            "replace_event": {
                "domains": [
                    "manual_hit", "manual_buff", "rotation_event",
                    "mechanics_rule", "mechanics_hit_event",
                    "mechanics_hit_pattern", "mechanics_application_event",
                    "mechanics_condition_event",
                ],
                "relation": "exact_topology_execution_binding",
            },
            "suppress_event": {
                "domains": [
                    "manual_hit", "manual_buff", "rotation_event",
                    "mechanics_rule", "mechanics_hit_event",
                    "mechanics_hit_pattern", "mechanics_application_event",
                    "mechanics_condition_event",
                ],
                "relation": "exact_topology_execution_binding",
            },
            "replace_input": {
                "domains": ["parameter_input"],
                "relation": "explicit_non_identity_scalar_field_allowlist",
            },
            "replace_state_transition": {
                "domains": ["state_transition"],
                "scopes": [
                    "template.buffs",
                    "rotation_model.events",
                    "mechanics_model.application_events",
                    "mechanics_model.condition_events",
                    "mechanics_model.state_runtime.lifecycles",
                    "mechanics_model.state_runtime.timers",
                ],
                "relation": "exact_topology_execution_binding_then_full_transition_replay",
            },
            "replace_computed_value": {
                "domains": ["computed_event"],
                "scopes": ["result.hits"],
                "relation": "exact_bound_damage_event_then_rederive_aggregates",
                "state_effects": "none",
            },
        },
        "review_applies_payload": False,
        "provisional_entity_authority": {
            "database_missing": "app_database_lookup_plus_agent_materialization_attestation",
            "public_data_patch": "app_database_lookup_plus_user_decision_ledger",
            "unavailable": "never_authorizes_formal_use",
        },
        "warning": "审阅函数只判断新鲜度与接受状态；组合器负责原子材料化并把有效图交给引擎。",
    }
