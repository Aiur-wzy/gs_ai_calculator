"""Exact current-stage tool catalog for the DSH V2 App host.

The model-facing catalog is an intersection, never a name-prefix grant.  The
domain portion comes verbatim from :func:`tools.definitions_for_session`; the
only additions are the two audited research-ledger tools and the two path-free
App action tools.  Official DSH ``web_search``/``web_fetch`` are mounted by the
Node composition and deliberately do not enter this App snapshot.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError

from . import tools
from .bridge_protocol import JSON_SAFE_INTEGER_MAX
from .native_app_action_tools import definitions as action_definitions
from .native_app_call_limits import MAX_APP_CALLS
from .native_app_tool_effects import is_known_effect
from .native_research_tools import definitions as research_definitions

CONTRACT = "genshin-native-app-tool-snapshot-v1"
# Catalog/transport capacity.  Per-turn consumable limits come from the frozen
# native Agent budget policy and may be lower.

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_WEB_TOOLS = frozenset({"web_search", "web_fetch"})
_SNAPSHOT_KEYS = frozenset({
    "contract",
    "workflow",
    "workflow_stage",
    "document_id",
    "candidate_revision",
    "stage_revision",
    "database_fingerprint",
    "tool_manifest_hash",
    "runtime_generation",
    "writer_fence",
    "max_app_calls",
    "tools",
    "policies",
    "catalog_hash",
})

_FIXED_POLICIES: dict[str, dict[str, str]] = {
    "gs_list_research_calls": {
        "policy": "research_ledger_read",
        "effect": "external_research_ledger",
    },
    "gs_report_research_usage": {
        "policy": "research_ledger_write",
        "effect": "external_research_ledger",
    },
    "gs_app_get_workspace": {
        "policy": "app_workspace_read",
        "effect": "external_action_ledger",
    },
    "gs_app_request_action": {
        "policy": "app_action_request",
        "effect": "external_action_ledger",
    },
}


def _fail(message: str, reason: str = "native_app_tool_catalog_invalid", details=None) -> None:
    raise PlanError(message, reason, details)


def _identity(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不符合原生工具身份语法")
    return value


def _digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}必须是小写SHA-256")
    return value


def _revision(value: object, name: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"{name}必须是非负安全整数")
    return value


def _definition(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "type", "name", "description", "inputSchema",
    }:
        _fail("工具定义字段不合法")
    row = copy.deepcopy(dict(value))
    if (
        row["type"] != "function"
        or not isinstance(row["name"], str)
        or _IDENTITY.fullmatch(row["name"]) is None
        or not isinstance(row["description"], str)
        or not row["description"]
        or not isinstance(row["inputSchema"], dict)
        or row["inputSchema"].get("type") != "object"
        or row["inputSchema"].get("additionalProperties") is not False
    ):
        _fail("工具定义内容不合法")
    return row


def _stage_revision(session: object) -> int:
    stage_state = getattr(session, "stage_state", None)
    value = stage_state.get("stage_revision", 0) if isinstance(stage_state, dict) else 0
    return _revision(value, "stage_revision")


def native_app_tool_policy(name: str) -> dict[str, str]:
    """The same effect producer used by execution catalogs and discovery."""
    policy = tools.NATIVE_V2_TOOL_POLICIES.get(name) or _FIXED_POLICIES.get(name)
    if policy is None or set(policy) != {"policy", "effect"}:
        _fail("工具缺少显式策略与效果分类", details={"tool": name})
    result = {key: _identity(policy[key], key) for key in ("policy", "effect")}
    if not is_known_effect(result["effect"]):
        _fail("工具目录包含未知效果分类", details={"tool": name})
    return result


def _catalog_body(
    session: object,
    *,
    document_id: str,
    database_fingerprint: str,
    tool_manifest_hash: str,
    runtime_generation: str,
    writer_fence: str,
) -> dict[str, Any]:
    workflow = _identity(getattr(session, "workflow", None), "workflow")
    workflow_stage = _identity(
        getattr(session, "authorization_stage", getattr(session, "workflow_stage", workflow)), "workflow_stage",
    )
    definitions = [
        *tools.definitions_for_session(session),
        *research_definitions(),
        *action_definitions(),
    ]
    selected: list[dict[str, Any]] = []
    policies: dict[str, dict[str, str]] = {}
    seen: set[str] = set()
    for raw in definitions:
        row = _definition(raw)
        name = row["name"]
        if name in seen or name in _WEB_TOOLS:
            _fail("工具目录包含重复或不应进入App快照的工具")
        seen.add(name)
        policies[name] = native_app_tool_policy(name)
        selected.append(row)
    selected.sort(key=lambda item: item["name"])
    if not selected or len(selected) > 64:
        _fail("当前阶段原生App工具数量不合法")
    policies = {name: policies[name] for name in sorted(policies)}
    return {
        "contract": CONTRACT,
        "workflow": workflow,
        "workflow_stage": workflow_stage,
        "document_id": _identity(document_id, "document_id"),
        "candidate_revision": _revision(
            getattr(session, "revision", None), "candidate_revision",
        ),
        "stage_revision": _stage_revision(session),
        "database_fingerprint": _digest(
            database_fingerprint, "database_fingerprint",
        ),
        "tool_manifest_hash": _digest(tool_manifest_hash, "tool_manifest_hash"),
        "runtime_generation": _identity(runtime_generation, "runtime_generation"),
        "writer_fence": _identity(writer_fence, "writer_fence"),
        "max_app_calls": MAX_APP_CALLS,
        "tools": selected,
        "policies": policies,
    }


def build_native_app_tool_snapshot(
    session: object,
    *,
    document_id: str,
    database_fingerprint: str,
    tool_manifest_hash: str,
    runtime_generation: str,
    writer_fence: str,
) -> dict[str, Any]:
    """Build one immutable, current-stage App tool projection."""

    body = _catalog_body(
        session,
        document_id=document_id,
        database_fingerprint=database_fingerprint,
        tool_manifest_hash=tool_manifest_hash,
        runtime_generation=runtime_generation,
        writer_fence=writer_fence,
    )
    return {**body, "catalog_hash": fingerprint(body)}


def validate_native_app_tool_snapshot(value: object) -> dict[str, Any]:
    """Validate a detached snapshot, including schemas, policies and hash."""

    if not isinstance(value, Mapping) or set(value) != _SNAPSHOT_KEYS:
        _fail("原生App工具快照字段不合法")
    row = copy.deepcopy(dict(value))
    if row.get("contract") != CONTRACT or row.get("max_app_calls") != MAX_APP_CALLS:
        _fail("原生App工具快照合同不匹配")
    for name in ("workflow", "workflow_stage", "document_id", "runtime_generation", "writer_fence"):
        _identity(row.get(name), name)
    for name in ("database_fingerprint", "tool_manifest_hash", "catalog_hash"):
        _digest(row.get(name), name)
    _revision(row.get("candidate_revision"), "candidate_revision")
    _revision(row.get("stage_revision"), "stage_revision")
    raw_tools = row.get("tools")
    raw_policies = row.get("policies")
    if not isinstance(raw_tools, list) or not isinstance(raw_policies, dict):
        _fail("原生App工具快照目录不合法")
    checked_tools = [_definition(item) for item in raw_tools]
    names = [item["name"] for item in checked_tools]
    if names != sorted(set(names)) or set(names) != set(raw_policies) or _WEB_TOOLS & set(names):
        _fail("原生App工具快照名称集合不合法")
    for name, policy in raw_policies.items():
        if not isinstance(policy, dict) or set(policy) != {"policy", "effect"}:
            _fail("原生App工具策略字段不合法")
        _identity(policy.get("policy"), "policy")
        _identity(policy.get("effect"), "effect")
        expected = tools.NATIVE_V2_TOOL_POLICIES.get(name) or _FIXED_POLICIES.get(name)
        if expected is None or dict(expected) != policy:
            _fail("原生App工具策略被篡改", details={"tool": name})
    body = {key: row[key] for key in row if key != "catalog_hash"}
    if fingerprint(body) != row["catalog_hash"]:
        _fail("原生App工具快照哈希不匹配")
    return row


__all__ = [
    "CONTRACT",
    "MAX_APP_CALLS",
    "build_native_app_tool_snapshot",
    "validate_native_app_tool_snapshot",
]
