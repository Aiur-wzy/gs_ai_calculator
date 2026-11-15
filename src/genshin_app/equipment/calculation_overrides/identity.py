"""Stable identity helpers for override targets.

Array positions and JSON Pointers are intentionally absent.  Adapters that
resolve concrete execution objects must map their domain entities to this
identity before declarations can be reviewed.
"""
from __future__ import annotations

from typing import Any

from .schema import TARGET_DOMAINS, OverrideDeclarationError, fingerprint

_OPTIONAL_IDS = (
    "topology_node_id",
    "execution_binding_id",
    "action_id",
    "component_id",
    "occurrence_id",
    "target_id",
)


def stable_target_identity(target: dict[str, Any]) -> str:
    """Return a canonical identity independent from collection ordering."""
    if type(target) is not dict:
        raise OverrideDeclarationError("override_target_invalid", "覆盖目标必须是对象")
    domain = target.get("domain")
    if domain not in TARGET_DOMAINS:
        raise OverrideDeclarationError("override_target_invalid", "覆盖目标域无效")
    required = ("scope_id", "entity_id")
    if any(type(target.get(key)) is not str or not target[key] for key in required):
        raise OverrideDeclarationError("override_target_invalid", "覆盖目标缺少稳定scope/entity身份")
    parts = [domain, target["scope_id"], target["entity_id"]]
    for key in _OPTIONAL_IDS:
        value = target.get(key)
        if value is not None:
            if type(value) is not str or not value:
                raise OverrideDeclarationError("override_target_invalid", f"{key}必须是稳定ID或null")
            parts.append(f"{key}={value}")
    return "|".join(parts)


def target_identity_fingerprint(target: dict[str, Any]) -> str:
    return fingerprint({"stable_target_identity": stable_target_identity(target)})


def build_target_registry(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index adapter-produced targets and fail closed on ambiguous identity."""
    if type(rows) is not list:
        raise OverrideDeclarationError("override_target_registry_invalid", "目标注册表必须是列表")
    registry: dict[str, dict[str, Any]] = {}
    for row in rows:
        if type(row) is not dict or set(row) != {"target", "value"}:
            raise OverrideDeclarationError("override_target_registry_invalid", "目标注册项字段不完整")
        identity = stable_target_identity(row["target"])
        if identity in registry:
            raise OverrideDeclarationError("override_target_ambiguous", "稳定目标身份重复")
        registry[identity] = row
    return registry
