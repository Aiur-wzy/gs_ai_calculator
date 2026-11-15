"""V2 model-facing tools for path-free App operation cards."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from .native_app_action_ledger import (
    ACTION_TYPES,
    NativeAppActionLedger,
    normalize_action_binding,
    normalize_action_workspace,
)

GET_WORKSPACE_TOOL = "gs_app_get_workspace"
REQUEST_ACTION_TOOL = "gs_app_request_action"

_SPECS = {
    GET_WORKSPACE_TOOL: {
        "type": "function",
        "name": GET_WORKSPACE_TOOL,
        "description": (
            "读取当前App工作区不含路径的文档类型、显示名、脏状态和可用操作。"
            "返回值不是文件操作授权。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    REQUEST_ACTION_TOOL: {
        "type": "function",
        "name": REQUEST_ACTION_TOOL,
        "description": (
            "请求一个由用户在App界面处理的保存、另存、打开、导出或导航操作卡。"
            "queued或presented均不代表执行成功；只有同一action_id返回completed才成功。"
            "不得在参数中传递文件路径。"
            "save_current/另存为保存正式文档；保存未完成AI候选请用export_candidate_input_draft并提供expected_revision。"
            "navigate_result只打开当前报告。候选已可审核时，请告诉用户在当前对话点「审核候选」，"
            "查看变化后点「同意并同步」。这一步不需要先创建导航卡。"
        ),
        "inputSchema": {
            "type": "object",
            "required": ["action", "reason"],
            "additionalProperties": False,
            "properties": {
                "action": {"type": "string", "enum": sorted(ACTION_TYPES)},
                "expected_revision": {"type": "integer", "minimum": 0},
                "reason": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1000,
                },
                "suggested_name": {
                    "anyOf": [
                        {"type": "string", "maxLength": 128},
                        {"type": "null"},
                    ]
                },
            },
        },
    },
}


class NativeAppActionToolError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeAppActionToolError(code)


def definitions() -> list[dict[str, Any]]:
    return [
        copy.deepcopy(_SPECS[GET_WORKSPACE_TOOL]),
        copy.deepcopy(_SPECS[REQUEST_ACTION_TOOL]),
    ]


class NativeAppActionToolHost:
    """Execute the two fixed tools from App-owned current facts."""

    def __init__(
        self,
        *,
        ledger: NativeAppActionLedger,
        workspace_reader: Callable[[], Mapping[str, Any]],
        binding_reader: Callable[[], Mapping[str, Any]],
        candidate_target_factory: Callable[[int], Mapping[str, Any]] | None = None,
    ) -> None:
        if not isinstance(ledger, NativeAppActionLedger):
            raise TypeError("ledger must be NativeAppActionLedger")
        if not callable(workspace_reader) or not callable(binding_reader):
            raise TypeError("workspace_reader and binding_reader must be callable")
        self._ledger = ledger
        self._workspace_reader = workspace_reader
        self._binding_reader = binding_reader
        self._candidate_target_factory = candidate_target_factory

    def _read_current_context(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Take a stable, path-free view across independently owned readers."""

        first_binding = normalize_action_binding(self._binding_reader())
        first_workspace = normalize_action_workspace(self._workspace_reader())
        second_workspace = normalize_action_workspace(self._workspace_reader())
        second_binding = normalize_action_binding(self._binding_reader())
        if (
            first_binding != second_binding
            or first_workspace != second_workspace
        ):
            _fail("app_action_context_changed")
        return second_binding, second_workspace

    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, Mapping):
            _fail("app_action_tool_arguments_invalid")
        try:
            args = copy.deepcopy(dict(arguments))
        except (TypeError, ValueError, RecursionError):
            _fail("app_action_tool_arguments_invalid")
        if name == GET_WORKSPACE_TOOL:
            if args:
                _fail("app_action_tool_arguments_invalid")
            binding, workspace = self._read_current_context()
            expired = self._ledger.refresh_binding(current_binding=binding)
            return {
                "workspace": workspace,
                "expired_action_ids": [row["action_id"] for row in expired],
                "policy": (
                    "文件操作只生成用户操作卡；模型看不到路径，queued或presented不等于完成。"
                ),
                "export_result_help": (
                    "结果导出只消费当前正式结果，必须与App已有的精确应用证明相符；"
                    "can_export=false不阻止保存正式文档或候选输入草稿。"
                    "请让用户显示已同步且未被修改的计算结果，再请求结果导出；"
                    "尚未同步或证明缺失时先保留输入，重新校验并审核同步，不能伪造导出成功。"
                ),
            }
        if name != REQUEST_ACTION_TOOL:
            _fail("app_action_tool_forbidden")
        if set(args) - {"action", "reason", "suggested_name", "expected_revision"}:
            _fail("app_action_tool_arguments_invalid")
        action = args.get("action")
        reason = args.get("reason")
        suggested_name = args.get("suggested_name")
        if (
            action not in ACTION_TYPES
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 1000
            or suggested_name is not None
            and (not isinstance(suggested_name, str) or len(suggested_name) > 128)
        ):
            _fail("app_action_tool_arguments_invalid")
        binding, workspace = self._read_current_context()
        target = None
        if action == "export_candidate_input_draft":
            revision = args.get("expected_revision")
            if type(revision) is not int or revision < 0:
                _fail("app_action_candidate_revision_required")
            if self._candidate_target_factory is None:
                _fail("app_action_candidate_draft_unavailable")
            target = self._candidate_target_factory(revision)
        elif "expected_revision" in args:
            _fail("app_action_candidate_revision_unexpected")
        snapshot = self._ledger.read()
        result = self._ledger.request(
            expected_revision=snapshot["revision"],
            action=action,
            reason=reason,
            suggested_name=suggested_name,
            binding=binding,
            workspace=workspace,
            target=target,
        )
        card = result["card"]
        response = {
            "status": result["status"],
            "ledger_revision": result["ledger_revision"],
            "card": {
                "action_id": card["action_id"],
                "action": card["action"],
                "suggested_name": card["suggested_name"],
                "status": card["status"],
                "target": copy.deepcopy(card["target"]),
            },
            "warning": "该操作尚未执行；等待用户在App操作卡中处理。",
        }
        if result.get("notice_code") == "save_as_required":
            response["notice_code"] = "save_as_required"
            response["notice"] = "当前文档没有保存路径，已改为由用户处理的另存为操作卡。"
        return response


__all__ = [
    "GET_WORKSPACE_TOOL",
    "REQUEST_ACTION_TOOL",
    "NativeAppActionToolError",
    "NativeAppActionToolHost",
    "definitions",
]
