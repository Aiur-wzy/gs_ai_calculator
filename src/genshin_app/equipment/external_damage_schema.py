"""Bounded native declarations for independently calculated, additive damage."""
from __future__ import annotations

import copy
import json
import math
import re
from decimal import Decimal

CONTRACT = "external-damage-v1"
FIELD = "external_damage_supplements"
MAX_ITEMS = 512
MAX_BYTES = 256 * 1024
_ID = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


def _text(limit, *, ident=False):
    return {"type": "string", "minLength": 1, "maxLength": limit,
            **({"pattern": _ID} if ident else {})}


PRECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["approximate"]},
        "method": _text(4096),
        "uncertainty": _text(4096),
        "state_effects": {"enum": ["none"]},
    },
    "required": ["kind", "method", "uncertainty", "state_effects"],
    "additionalProperties": False,
}


_properties = {
    "id": _text(128, ident=True), "label": _text(256), "character": _text(128),
    "skill": _text(256), "t": {"type": "number", "minimum": 0},
    "expected": {"type": "number", "minimum": 0},
    "action_id": {"anyOf": [_text(128), {"type": "null"}]},
    "coverage_key": _text(128, ident=True), "event_id": _text(128, ident=True),
    **{key: _text(4096) for key in ("reason", "calculation_note", "non_overlap_note")},
    "source_refs": {"type": "array", "minItems": 1, "maxItems": 8, "items": _text(2048)},
    "accepted_fingerprint": {"anyOf": [
        {"type": "string", "pattern": r"^[0-9a-f]{64}$"}, {"type": "null"}]},
    "precision": {"anyOf": [PRECISION_SCHEMA, {"type": "null"}]},
}
_legacy_required = [key for key in _properties if key != "precision"]
ITEM_SCHEMA = {"type": "object", "properties": _properties, "required": _legacy_required,
               "additionalProperties": False}
SUPPLEMENTS_SCHEMA = {"type": "array", "items": ITEM_SCHEMA, "maxItems": MAX_ITEMS}
DEFAULT_ITEM = {key: "" for key in _properties}
DEFAULT_ITEM.update(t=0, expected=0, action_id=None, source_refs=[],
                    accepted_fingerprint=None, precision=None)


def precision_of(item):
    value = item.get("precision")
    return "approximate" if isinstance(value, dict) and value.get("kind") == "approximate" else "exact"


def ticks(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("外部补充时点必须是有限数值")
    number = Decimal(str(value)) * 1_000_000
    if number != number.to_integral_value() or number < 0:
        raise ValueError("外部补充时点必须是非负整数微秒")
    return int(number)


def validate_items(items):
    """Standalone validation also used for untrusted offline receipts."""
    if type(items) is not list or len(items) > MAX_ITEMS:
        raise ValueError("外部补充最多512项")
    if len(json.dumps(items, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")) > MAX_BYTES:
        raise ValueError("外部补充声明超过256KiB")
    for item in items:
        keys = set(item) if type(item) is dict else set()
        if (type(item) is not dict or not keys.issubset(_properties)
                or (keys != set(_legacy_required) and keys != set(_properties))):
            raise ValueError("外部补充字段不完整或包含未知字段")
        for key, schema in _properties.items():
            if key == "precision" and key not in item:
                continue
            value = item[key]
            if key in ("t", "expected"):
                if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                    raise ValueError(f"外部补充{key}必须是有限非负数值")
                if key == "t":
                    ticks(value)
            elif key == "source_refs":
                if (type(value) is not list or not 1 <= len(value) <= 8 or any(
                        type(v) is not str or not v.strip() or len(v) > 2048 for v in value)):
                    raise ValueError("外部补充需1至8条非空来源")
            elif key == "accepted_fingerprint":
                if value is not None and (type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value)):
                    raise ValueError("外部补充确认指纹格式无效")
            elif key == "precision":
                if value is None:
                    continue
                if (type(value) is not dict or set(value) != set(PRECISION_SCHEMA["properties"])
                        or value.get("kind") != "approximate"
                        or value.get("state_effects") != "none"):
                    raise ValueError("近似外部值的精度说明字段不完整")
                for field in ("method", "uncertainty"):
                    text = value.get(field)
                    if type(text) is not str or not text.strip() or len(text) > 4096:
                        raise ValueError("近似外部值需要完整方法和不确定性说明")
            else:
                if key == "action_id" and value is None:
                    continue
                spec = schema["anyOf"][0] if key == "action_id" else schema
                if (type(value) is not str or not value.strip() or len(value) > spec["maxLength"]
                        or "pattern" in spec and not re.fullmatch(spec["pattern"], value)):
                    raise ValueError(f"外部补充{key}文本或身份格式无效")
    for key in ("id", "event_id", "coverage_key"):
        if len({item[key] for item in items}) != len(items):
            raise ValueError(f"外部补充{key}重复，不能重复计数")


def external_damage_contract():
    return {"contract": CONTRACT, "binding_field": FIELD, "report_field": "external_damage_review",
            "schema": copy.deepcopy(SUPPLEMENTS_SCHEMA), "default_item": copy.deepcopy(DEFAULT_ITEM),
            "limits": {"max_items": MAX_ITEMS, "max_declared_bytes": MAX_BYTES},
            "confirmable_issue_codes": ["external_damage_confirmation_required"],
            "evidence_tier": "includes_external_calculation", "engine_events_generated": False,
            "precision": {
                "default": "exact",
                "approximate_requires": ["method", "uncertainty", "state_effects"],
                "propagates_to": ["event", "character", "skill", "total_damage", "dps", "exports", "gui"],
            },
            "supported_exports": ["json", "xlsx", "calculated_package"]}
