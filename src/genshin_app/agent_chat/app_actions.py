"""Opaque App workspace tools for the embedded Agent.

The model never receives a filesystem path or a general GUI bridge.  It can
inspect a small workspace projection and queue one of the actions implemented
by the renderer.  The user still performs the actual open/save/export action,
so existing dirty-document, CAS and candidate-review guards remain in force.
"""
from __future__ import annotations

import copy
import uuid

from genshin_app.equipment.schema import PlanError

MAX_ACTIONS = 100
ACTION_TYPES = frozenset({
    "save_current", "save_as", "open_document", "export_result",
    "navigate_rotation", "navigate_result", "navigate_whiteboard",
})
_REQUIRES_APPLIED_CANDIDATE = frozenset({
    "save_current", "save_as", "export_result",
})
_WORKSPACE_CAPABILITY = {
    "save_current": "can_save",
    "save_as": "can_save",
    "open_document": "can_open",
    "export_result": "can_export",
}

SPECS = {
    "gs_app_get_workspace": {
        "type": "function",
        "name": "gs_app_get_workspace",
        "description": ("读取当前App工作区的文档类型、显示名、是否已保存及可用操作；"
                        "不返回文件路径、GUI DOM或候选外的数据。"),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "gs_app_request_action": {
        "type": "function",
        "name": "gs_app_request_action",
        "description": ("请求App显示一个保存、另存、打开、导出或页面导航操作卡。"
                        "此工具只排队，不代表操作已经执行；文件选择、覆盖、离开未保存文档"
                        "和候选应用继续由用户及App现有保护逻辑处理。不得声称已经保存或打开。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"enum": sorted(ACTION_TYPES)},
                "reason": {"type": "string", "minLength": 1, "maxLength": 1000},
                "suggested_name": {"type": "string", "maxLength": 128},
            },
            "required": ["action", "reason"],
            "additionalProperties": False,
        },
    },
}


def definitions() -> list[dict]:
    return [copy.deepcopy(value) for value in SPECS.values()]


def normalize_workspace(value, *, kind=None, title=None) -> dict:
    """Validate renderer metadata; paths and arbitrary fields are rejected."""
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - {
            "contract", "document_kind", "display_name", "dirty", "has_path", "current_view",
            "can_save", "can_open", "can_export"}:
        raise PlanError("App工作区摘要不合法", "agent_invalid_input")
    if value.get("contract", "agent-app-workspace-v1") != "agent-app-workspace-v1":
        raise PlanError("App工作区契约不合法", "agent_invalid_input")
    document_kind = value.get("document_kind", kind)
    if document_kind not in ("whiteboard", "native"):
        raise PlanError("App工作区文档类型不合法", "agent_invalid_input")
    display_name = value.get("display_name", title or "未命名方案")
    if not isinstance(display_name, str) or not display_name or len(display_name) > 256:
        raise PlanError("App工作区显示名不合法", "agent_invalid_input")
    current_view = value.get("current_view")
    if current_view is not None and (not isinstance(current_view, str)
                                     or len(current_view) > 64):
        raise PlanError("App工作区页面标识不合法", "agent_invalid_input")
    result = {"contract": "agent-app-workspace-v1", "document_kind": document_kind,
              "display_name": display_name, "current_view": current_view}
    # Missing capability claims fail closed.  The renderer always sends these
    # fields; restored legacy sessions must not acquire new file powers merely
    # because an older record omitted them.
    for field, default in (("dirty", True), ("has_path", False), ("can_save", False),
                           ("can_open", False), ("can_export", False)):
        item = value.get(field, default)
        if type(item) is not bool:
            raise PlanError("App工作区布尔状态不合法", "agent_invalid_input")
        result[field] = item
    return result


def _validate_request(args: dict) -> dict:
    if (not isinstance(args, dict) or set(args) - {"action", "reason", "suggested_name"}
            or args.get("action") not in ACTION_TYPES
            or not isinstance(args.get("reason"), str)
            or not 0 < len(args["reason"].strip()) <= 1000):
        raise PlanError("App操作请求不合法", "agent_tool_input")
    suggested = args.get("suggested_name")
    if suggested is not None and (not isinstance(suggested, str) or len(suggested) > 128
                                  or any(char in suggested for char in "\\/:*?\"<>|")):
        raise PlanError("建议文件名不合法", "agent_tool_input")
    return {"action": args["action"], "reason": args["reason"].strip(),
            "suggested_name": suggested or None}


def _candidate_binding(summary: dict | None, action: str) -> dict:
    summary = summary if isinstance(summary, dict) else {}
    revision = summary.get("revision")
    if type(revision) is not int or revision < 0:
        revision = None
    receipt = summary.get("receipt_fingerprint")
    if not isinstance(receipt, str) or not receipt or len(receipt) > 256:
        receipt = None
    return {
        "candidate_revision": revision,
        "receipt_fingerprint": receipt,
        "requires_applied_candidate": action in _REQUIRES_APPLIED_CANDIDATE,
    }


def _preconditions(session: dict, action: dict, candidate_summary: dict | None) -> dict:
    """Evaluate an immutable action binding against current App-owned state."""
    summary = candidate_summary if isinstance(candidate_summary, dict) else {}
    current_revision = summary.get("revision")
    current_receipt = summary.get("receipt_fingerprint")
    issues = []

    if (current_revision != action.get("candidate_revision")
            or current_receipt != action.get("receipt_fingerprint")):
        issues.append({
            "code": "candidate_binding_stale",
            "message": "候选修订或计算回执已变化，请让Agent重新创建操作卡。",
        })

    requires_applied = bool(action.get("requires_applied_candidate"))
    if requires_applied:
        if action.get("receipt_fingerprint") is None or not bool(summary.get("ready")):
            issues.append({
                "code": "candidate_not_ready",
                "message": "候选尚未形成可审核应用的稳定回执。",
            })
        applied = session.get("applied_candidate")
        if (not isinstance(applied, dict)
                or applied.get("candidate_revision") != action.get("candidate_revision")
                or applied.get("receipt_fingerprint") != action.get("receipt_fingerprint")):
            issues.append({
                "code": "candidate_not_applied",
                "message": "请先审核并应用该操作卡绑定的候选。",
            })

    capability = _WORKSPACE_CAPABILITY.get(action.get("action"))
    workspace = session.get("workspace") if isinstance(session.get("workspace"), dict) else {}
    if capability and workspace.get(capability) is not True:
        issues.append({
            "code": "workspace_capability_unavailable",
            "message": "当前工作区不允许执行这项文件操作。",
        })

    codes = {row["code"] for row in issues}
    status = ("stale" if "candidate_binding_stale" in codes else
              "blocked" if issues else "ready")
    return {
        "contract": "agent-app-action-preconditions-v1",
        "status": status,
        "issues": issues,
        "candidate_revision": action.get("candidate_revision"),
        "receipt_fingerprint": action.get("receipt_fingerprint"),
        "requires_applied_candidate": requires_applied,
    }


def _public(action: dict) -> dict:
    return {key: copy.deepcopy(action.get(key)) for key in (
        "action_id", "action", "reason", "suggested_name", "status", "workspace",
        "candidate_revision", "receipt_fingerprint", "requires_applied_candidate",
        "preconditions", "result_message") if key in action}


def refresh_preconditions(session: dict, candidate_summary: dict | None) -> list[dict]:
    """Refresh pending cards and return the public cards whose gate changed."""
    changed = []
    for action in session.get("app_actions", []):
        if action.get("status") not in {"queued", "presented"}:
            continue
        before = action.get("preconditions")
        after = _preconditions(session, action, candidate_summary)
        if before != after:
            action["preconditions"] = after
            changed.append(_public(action))
    return changed


def call(session: dict, name: str, args: dict,
         candidate_summary: dict | None = None) -> tuple[dict, dict | None]:
    workspace = copy.deepcopy(session["workspace"])
    if name == "gs_app_get_workspace":
        if args not in ({}, None):
            raise PlanError("工作区读取工具不接受参数", "agent_tool_input")
        return {"workspace": workspace,
                "policy": "文件操作只排队为用户可见操作卡；返回queued不等于已执行。"}, None
    if name != "gs_app_request_action":
        raise PlanError("未知App工作区工具", "agent_tool_forbidden")
    request = _validate_request(args)
    binding = _candidate_binding(candidate_summary, request["action"])
    action = {"action_id": uuid.uuid4().hex, **request, **binding,
              "status": "queued", "workspace": workspace}
    action["preconditions"] = _preconditions(session, action, candidate_summary)
    actions = session.setdefault("app_actions", [])
    actions.append(action)
    del actions[:-MAX_ACTIONS]
    public = _public(action)
    return {"status": "queued_for_user", "action": public,
            "warning": "该操作尚未执行；等待用户在App操作卡中处理。"}, public


def resolve(session: dict, action_id: str, status: str, workspace: dict | None,
            message: str | None = None, candidate_summary: dict | None = None) -> dict:
    if not isinstance(action_id, str) or len(action_id) != 32:
        raise PlanError("App操作标识不合法", "agent_invalid_input")
    if status not in ("presented", "completed", "cancelled", "failed"):
        raise PlanError("App操作结果不合法", "agent_invalid_input")
    action = next((row for row in session.get("app_actions", [])
                   if row.get("action_id") == action_id), None)
    allowed_previous = {"queued"} if status == "presented" else {"queued", "presented"}
    if action is None or action.get("status") not in allowed_previous:
        raise PlanError("App操作不存在或已经处理", "agent_action_stale")
    if message is not None and (not isinstance(message, str) or len(message) > 1000):
        raise PlanError("App操作说明不合法", "agent_invalid_input")
    action["preconditions"] = _preconditions(session, action, candidate_summary)
    if status in {"presented", "completed"} and action["preconditions"]["status"] != "ready":
        raise PlanError("App操作前置条件尚未满足或已经过期", "agent_action_precondition",
                        copy.deepcopy(action["preconditions"]))
    if workspace is not None:
        session["workspace"] = normalize_workspace(
            workspace, kind=session.get("kind"), title=session.get("title"))
    action["status"] = status
    action["result_message"] = message or ""
    action["workspace"] = copy.deepcopy(session["workspace"])
    return _public(action)


__all__ = ["ACTION_TYPES", "SPECS", "call", "definitions", "normalize_workspace",
           "refresh_preconditions", "resolve"]
