"""V2-only App tools for reading and closing the Web research ledger."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from genshin_app.equipment.schema import PlanError

from .native_research_ledger import REPORT_CONTRACT, NativeResearchLedger

LIST_TOOL = "gs_list_research_calls"
REPORT_TOOL = "gs_report_research_usage"

_USAGE_PROPERTIES = {
    "research_id": {"type": "string", "maxLength": 64},
    "disposition": {"type": "string", "enum": ["adopted", "background", "rejected"]},
    "claim_summary": {
        "anyOf": [
            {"type": "string", "maxLength": 1024},
            {"type": "null"},
        ]
    },
    "applied_to": {
        "type": "array",
        "maxItems": 32,
        "description": (
            "采用位置的候选、资源或finding稳定ID；只允许ASCII身份字符。"
            "仅作为背景或未采用时传空数组，不要填写中文说明。"
        ),
        "items": {
            "type": "string",
            "maxLength": 128,
            "description": "候选、资源或finding的稳定ASCII ID。",
        },
    },
    "system_gap_class": {
        "type": "string",
        "enum": [
            "database_missing",
            "dynamic_web_preferred",
            "verification_only",
            "unused",
        ],
    },
    "reason": {
        "anyOf": [
            {"type": "string", "maxLength": 1024},
            {"type": "null"},
        ]
    },
}

_SPECS = {
    LIST_TOOL: {
        "type": "function",
        "name": LIST_TOOL,
        "description": (
            "读取当前用户轮已经登记的Web研究调用、实际query或URL、来源与结算状态。"
            "用于生成完整研究采用报告；不会返回网页正文。"
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    },
    REPORT_TOOL: {
        "type": "function",
        "name": REPORT_TOOL,
        "description": (
            "对当前研究账revision中的本轮全部Web调用逐项报告采用、背景使用或舍弃。"
            "applied_to只能填写候选、资源或finding的稳定ID；背景或舍弃项填空数组。"
            "遗漏、重复、未知、未结算或旧revision会返回可修复诊断。"
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "contract",
                "research_ledger_revision",
                "entries",
                "complete_for_turn",
            ],
            "properties": {
                "contract": {"const": REPORT_CONTRACT},
                "research_ledger_revision": {"type": "integer", "minimum": 0},
                "entries": {
                    "type": "array",
                    "maxItems": 2048,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(_USAGE_PROPERTIES),
                        "properties": copy.deepcopy(_USAGE_PROPERTIES),
                    },
                },
                "complete_for_turn": {"type": "boolean"},
            },
        },
    },
}


def definitions() -> list[dict[str, Any]]:
    """Return fresh schemas so callers cannot mutate the V2 tool policy."""

    return [copy.deepcopy(_SPECS[name]) for name in (LIST_TOOL, REPORT_TOOL)]


def _input(value: object, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - allowed:
        raise PlanError("研究工具参数字段不合法", "agent_tool_input")
    return copy.deepcopy(dict(value))


def call(
    name: str,
    arguments: Mapping[str, Any],
    *,
    ledger: NativeResearchLedger,
    turn_id: str,
) -> dict[str, Any]:
    """Execute a research bookkeeping tool under host-supplied turn identity."""

    if not isinstance(ledger, NativeResearchLedger):
        raise TypeError("ledger must be NativeResearchLedger")
    if name == LIST_TOOL:
        value = _input(arguments, frozenset({"offset", "limit"}))
        return ledger.list_calls(
            turn_id=turn_id,
            offset=value.get("offset", 0),
            limit=value.get("limit", 50),
        )
    if name == REPORT_TOOL:
        value = _input(
            arguments,
            frozenset(
                {
                    "contract",
                    "research_ledger_revision",
                    "entries",
                    "complete_for_turn",
                }
            ),
        )
        if value.get("contract") != REPORT_CONTRACT:
            raise PlanError("研究使用报告合同不匹配", "agent_tool_input")
        required = {"research_ledger_revision", "entries", "complete_for_turn"}
        if not required <= set(value):
            raise PlanError("研究使用报告参数不完整", "agent_tool_input")
        return ledger.report_usage(
            turn_id=turn_id,
            research_ledger_revision=value["research_ledger_revision"],
            entries=value["entries"],
            complete_for_turn=value["complete_for_turn"],
        )
    raise PlanError("研究工具不在固定目录", "agent_tool_rejected")


__all__ = [
    "LIST_TOOL",
    "REPORT_TOOL",
    "call",
    "definitions",
]
