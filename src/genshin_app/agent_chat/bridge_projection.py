"""Projection of validated DSH bridge events into App events.

The projector is deliberately transport- and persistence-free.  It accepts
one already-decoded V2 bridge frame at a time, applies the strict per-turn
ordering/binding rules from :mod:`bridge_protocol`, and returns the small
event dialect currently consumed by ``AgentManager``.

Partial tool arguments remain private to this object.  Reasoning text follows
the reviewed message event path under a distinct ``reasoning:`` identity so
the Host can persist and the GUI can disclose it separately from the answer.
It is never copied into diagnostic snapshots or error events.  Cancellation
and invalidation still clear unfinished local assemblies.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .bridge_protocol import (
    JSON_SAFE_INTEGER_MAX,
    MAX_NESTING_DEPTH,
    MAX_TURN_FRAMES,
    BridgeProtocolError,
    TurnFrameValidator,
    make_frame,
    validate_frame,
)

MAX_ASSISTANT_TEXT_CHARS = 16 * 1024 * 1024
MAX_REASONING_CHARS = 16 * 1024 * 1024
MAX_TOOL_ARGUMENT_CHARS = 256 * 1024
MAX_TOOL_CALLS = 16_384
MAX_USAGE_VALUE = 10**12

_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SENSITIVE_CODE_PREFIXES = (
    "api-key:",
    "api_key:",
    "authorization:",
    "basic:",
    "bearer:",
    "secret:",
    "sk-",
    "token:",
)
_USAGE_KEYS = frozenset({
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "reasoning_tokens",
})
_STATUS_TYPES = frozenset({
    "turn.started",
    "turn.status",
    "step.started",
    "step.status",
    "step.completed",
})
_CANCEL_SAFE_TYPES = frozenset({
    "context.compaction.completed",
    "context.compaction.failed",
    "context.compaction.started",
    "turn.status",
    "step.status",
    "step.completed",
    "tool.result",
    "usage",
    "turn.completed",
    "turn.cancelled",
    "turn.error",
    "turn.segment_restart_required",
})
_SEGMENT_BUDGET_USAGE_KEYS = frozenset({
    "elapsed_seconds", "provider_steps", "app_tool_calls", "web_tool_calls",
    "total_tool_calls", "web_search_calls", "web_search_queries",
    "web_fetch_calls", "read_tool_calls", "mutation_tool_calls",
    "cumulative_output_tokens", "cumulative_input_tokens",
    "cumulative_uncached_input_tokens",
})


class BridgeProjectionError(BridgeProtocolError):
    """A stable projection/state-machine rejection."""


def _reject(code: str, message: str) -> None:
    raise BridgeProjectionError(code, message)


def _exact_payload(payload: Mapping[str, Any], required: set[str], optional=()) -> None:
    keys = set(payload)
    allowed = required | set(optional)
    if not required <= keys or keys - allowed:
        _reject("invalid_event_payload", "桥事件payload字段不符合投影合同")


def _text(value: Any, *, name: str, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value) or len(value) > maximum:
        _reject("invalid_event_payload", f"桥事件{name}不合法")
    return value


def _code(value: Any, *, name: str) -> str:
    if not isinstance(value, str) or _CODE.fullmatch(value) is None:
        _reject("invalid_event_payload", f"桥事件{name}不是合法机器码")
    if value.lower().startswith(_SENSITIVE_CODE_PREFIXES):
        _reject("sensitive_identity", f"桥事件{name}不能承载凭据或秘密")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("duplicate_tool_argument_key", "工具参数JSON包含重复字段")
        result[key] = value
    return result


def _reject_constant(_value):
    _reject("invalid_tool_argument_number", "工具参数JSON包含NaN或Infinity")


def _check_json_tree(value: Any, depth: int = 0) -> None:
    if depth > MAX_NESTING_DEPTH:
        _reject("tool_arguments_too_deep", "工具参数JSON嵌套超过上限")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _reject("invalid_tool_arguments", "工具参数字段名必须是字符串")
            _check_json_tree(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_json_tree(item, depth + 1)
    elif value is None or isinstance(value, (str, bool)):
        return
    elif type(value) is int:
        if abs(value) > JSON_SAFE_INTEGER_MAX:
            _reject("tool_argument_integer_out_of_range", "工具参数整数超过跨语言安全范围")
    elif isinstance(value, float):
        if not math.isfinite(value):
            _reject("invalid_tool_argument_number", "工具参数JSON包含NaN或Infinity")
    else:
        _reject("invalid_tool_arguments", "工具参数不是受支持的JSON值")


def _parse_arguments_json(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except BridgeProjectionError:
        raise
    except (ValueError, TypeError, json.JSONDecodeError, RecursionError) as exc:
        raise BridgeProjectionError(
            "invalid_tool_arguments", "工具参数block不是严格JSON对象",
        ) from exc
    if not isinstance(value, dict):
        _reject("invalid_tool_arguments", "工具参数block必须结束为JSON对象")
    _check_json_tree(value)
    return value


def _raw_arguments_hash(raw: str) -> str:
    try:
        encoded = raw.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise BridgeProjectionError(
            "invalid_tool_arguments", "工具参数block不是有效UTF-8文本",
        ) from exc
    if len(encoded) > MAX_TOOL_ARGUMENT_CHARS:
        _reject("tool_arguments_too_large", "工具参数超过投影上限")
    return hashlib.sha256(encoded).hexdigest()


def _local_json_bytes(value: Any) -> bytes:
    """Local-only semantic comparison; never used as a wire hash."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise BridgeProjectionError(
            "tool_schema_invalid", "App工具校验器返回了非JSON对象",
        ) from exc


@dataclass
class _ToolAssembly:
    step_id: str
    delta_text: str = ""
    arguments: dict[str, Any] | None = None
    arguments_hash: str | None = None
    tool: str | None = None
    ready_hash: str | None = None
    ready: bool = False
    emitted: bool = False

    def clear_raw(self) -> None:
        self.delta_text = ""
        self.arguments = None


@dataclass(frozen=True)
class ProjectedEventReceipt:
    """One successfully projected event and its cumulative ACK frame."""

    projected: tuple[dict[str, Any], ...]
    acknowledgement: dict[str, Any]
    duplicate: bool


class NativeBridgeEventProjector:
    """Project one native-agent turn without owning its transport or tools.

    ``validate_tool_call`` is the App-owned schema/permission boundary.  It
    receives the tool name and a detached argument dictionary, and must return
    the normalized argument dictionary that may be dispatched.  Raising from
    it rejects the bridge event and never produces an executable tool event.
    """

    def __init__(
        self,
        *,
        runtime_incarnation: str,
        conversation_id: str,
        turn_id: str,
        epoch_id: str,
        validate_tool_call: Callable[[str, dict[str, Any]], Mapping[str, Any]],
        first_event_seq: int = 1,
    ):
        if not callable(validate_tool_call):
            _reject("tool_validator_required", "原生桥投影必须配置App工具校验器")
        self.runtime_incarnation = runtime_incarnation
        self.conversation_id = conversation_id
        self.turn_id = turn_id
        self.epoch_id = epoch_id
        for name, value in (
            ("runtime_incarnation", runtime_incarnation),
            ("conversation_id", conversation_id),
            ("turn_id", turn_id),
            ("epoch_id", epoch_id),
        ):
            _code(value, name=name)
        self._validator = TurnFrameValidator(
            runtime_incarnation=runtime_incarnation,
            conversation_id=conversation_id,
            turn_id=turn_id,
            epoch_id=epoch_id,
            first_event_seq=first_event_seq,
        )
        self._validate_tool_call = validate_tool_call
        self._assistant_text: dict[str, str] = {}
        self._completed_text_steps: set[str] = set()
        self._reasoning = ""
        self._reasoning_active = False
        self._reasoning_item_id: str | None = None
        self._reasoning_block_index = 0
        self._compactions: set[str] = set()
        self._tools: dict[str, _ToolAssembly] = {}
        self._settled_calls: set[str] = set()
        self._cancel_requested = False
        self._invalidated = False
        # The terminal machine code is intentionally kept separately from the
        # visible projection.  ``turn.error`` already crosses the authenticated
        # bridge with a bounded, secret-filtered code; retaining that code lets
        # the coordinator persist an actionable reason without ever retaining
        # provider text or exception messages.
        self._terminal_reason: str | None = None
        self._segment_restart_reason: str | None = None
        self._segment_budget_usage: dict[str, int] | None = None

    @property
    def terminal_type(self) -> str | None:
        return self._validator.terminal_type

    @property
    def terminal_reason(self) -> str | None:
        """Return the validated terminal machine code, never provider text."""
        return self._terminal_reason

    @property
    def segment_restart_reason(self) -> str | None:
        """Return the bounded Host continuation receipt, when present."""
        return self._segment_restart_reason

    @property
    def segment_budget_usage(self) -> dict[str, int] | None:
        return (
            None
            if self._segment_budget_usage is None
            else dict(self._segment_budget_usage)
        )

    def diagnostic_snapshot(self) -> dict[str, Any]:
        """Return counters/identities only; never raw reasoning or arguments."""
        return {
            "runtime_incarnation": self.runtime_incarnation,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "epoch_id": self.epoch_id,
            "next_event_seq": self._validator.next_event_seq,
            "terminal_type": self.terminal_type,
            "terminal_reason": self.terminal_reason,
            "segment_restart_reason": self.segment_restart_reason,
            "segment_budget_usage": self.segment_budget_usage,
            "cancel_requested": self._cancel_requested,
            "invalidated": self._invalidated,
            "assistant_text_chars": sum(len(value) for value in self._assistant_text.values()),
            "completed_text_blocks": len(self._completed_text_steps),
            "reasoning_chars": len(self._reasoning),
            "pending_tool_calls": sum(not item.emitted for item in self._tools.values()),
            "ignored_late_events": self._validator.ignored_late_events,
        }

    def request_cancel(self) -> None:
        """Fence new model output while allowing settlement/status to drain."""
        self._cancel_requested = True
        self._clear_volatile()

    def invalidate(self) -> None:
        """Fence the projector after a runtime generation/epoch is replaced."""
        self._invalidated = True
        self._clear_volatile()

    def finalize(self) -> None:
        """Require a terminal frame and clear all local assemblies.

        The completed reasoning event has already crossed the reviewed stream
        boundary and the DSH Session owns its durable copy.  Retaining another
        Python assembly after terminal would create an unnecessary second
        in-memory authority.
        """
        self._validator.finalize()
        self._clear_volatile()

    def ingest(self, value: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Validate and project one frame.

        Duplicate and harmless late display frames return an empty list.  No
        executable tool event is returned until both ``arguments.completed``
        and ``tool.call.ready`` have arrived and App schema validation passes.
        """
        if self._invalidated:
            _reject("projection_invalidated", "原生桥投影已被新runtime或epoch替换")
        try:
            return self._ingest_frame(value)
        except BridgeProtocolError:
            # A rejected frame may already have advanced the per-turn validator
            # or partially changed an assembly.  Continuing the same projector
            # would let a later terminal frame bless a semantically corrupt turn.
            self._invalidated = True
            self._clear_volatile()
            raise

    def _ingest_frame(self, value: Mapping[str, Any]) -> list[dict[str, Any]]:
        frame = validate_frame(value)
        decision = self._validator.accept(frame)
        if decision.status in {"duplicate", "ignored_late"}:
            return []

        event_type = frame["type"]
        _code(frame["step_id"], name="step_id")
        if "call_id" in frame:
            _code(frame["call_id"], name="call_id")
        if self._cancel_requested and event_type not in _CANCEL_SAFE_TYPES:
            # Sequence/binding still advances above, but cancelled output can
            # neither become visible content nor open a new App tool request.
            return []

        if event_type in _STATUS_TYPES:
            return self._status(frame)
        if event_type == "assistant.text.delta":
            return self._text_delta(frame)
        if event_type == "assistant.text.completed":
            return self._text_completed(frame)
        if event_type.startswith("assistant.reasoning."):
            return self._reasoning_event(frame)
        if event_type.startswith("tool.arguments."):
            return self._tool_arguments(frame)
        if event_type == "tool.call.ready":
            return self._tool_ready(frame)
        if event_type == "tool.result":
            return self._tool_result(frame)
        if event_type == "usage":
            return self._usage(frame)
        if event_type.startswith("context.compaction."):
            return self._compaction_event(frame)
        if event_type == "turn.segment_restart_required":
            return self._segment_restart(frame)
        if event_type in {"turn.completed", "turn.cancelled", "turn.error"}:
            return self._terminal(frame)
        _reject("unsupported_projection_event", f"未实现桥事件投影：{event_type}")

    def _compaction_event(
        self, frame: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        """Validate DSH compaction lifecycle without copying its summary."""

        event_type = frame["type"]
        payload = frame["payload"]
        if event_type == "context.compaction.started":
            _exact_payload(payload, {"compaction_id"})
            compaction_id = _code(
                payload["compaction_id"], name="compaction_id"
            )
            if compaction_id in self._compactions:
                _reject("compaction_start_duplicate", "上下文整理身份重复启动")
            self._compactions.add(compaction_id)
            status = "context_compaction_started"
            details = {"id": compaction_id}
        elif event_type == "context.compaction.failed":
            _exact_payload(payload, {"compaction_id", "code"})
            compaction_id = _code(
                payload["compaction_id"], name="compaction_id"
            )
            if compaction_id not in self._compactions:
                _reject("compaction_end_without_start", "上下文整理缺少启动事件")
            if _code(payload["code"], name="compaction failure") != (
                "compaction_failed"
            ):
                _reject("compaction_failure_invalid", "上下文整理失败码不合法")
            self._compactions.remove(compaction_id)
            status = "context_compaction_failed"
            details = {
                "id": compaction_id,
                "failureCode": "compaction_failed",
            }
        else:
            _exact_payload(
                payload,
                {
                    "compaction_id",
                    "route",
                    "usage",
                    "shadowed_token_count",
                },
            )
            compaction_id = _code(
                payload["compaction_id"], name="compaction_id"
            )
            if compaction_id not in self._compactions:
                _reject("compaction_end_without_start", "上下文整理缺少启动事件")
            route = payload["route"]
            if (
                not isinstance(route, Mapping)
                or not {"provider", "model"} <= set(route)
                or set(route) - {"provider", "model", "max_tokens"}
            ):
                _reject("compaction_route_invalid", "上下文整理模型路由不合法")
            provider = _code(route["provider"], name="compaction provider")
            model = _code(route["model"], name="compaction model")
            if "max_tokens" in route and (
                type(route["max_tokens"]) is not int
                or not 1 <= route["max_tokens"] <= MAX_USAGE_VALUE
            ):
                _reject("compaction_route_invalid", "上下文整理输出上限不合法")
            usage = payload["usage"]
            if usage is not None and (
                not isinstance(usage, Mapping)
                or not usage
                or set(usage) - _USAGE_KEYS
                or any(
                    type(value) is not int
                    or not 0 <= value <= MAX_USAGE_VALUE
                    for value in usage.values()
                )
            ):
                _reject("compaction_usage_invalid", "上下文整理用量不合法")
            shadowed = payload["shadowed_token_count"]
            if type(shadowed) is not int or not 0 <= shadowed <= MAX_USAGE_VALUE:
                _reject("compaction_shadow_invalid", "上下文整理替换量不合法")
            self._compactions.remove(compaction_id)
            status = "context_compaction_completed"
            details = {
                "id": compaction_id,
                "provider": provider,
                "model": model,
                "shadowedTokens": shadowed,
                "usage": None if usage is None else dict(usage),
            }
            if "max_tokens" in route:
                details["maxTokens"] = route["max_tokens"]
        return [{
            "method": "native/bridge/status",
            "params": {
                **self._base_params(frame),
                "event": event_type,
                "status": status,
                "compaction": details,
            },
        }]

    def _segment_restart(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        if self._segment_restart_reason is not None:
            _reject("segment_restart_duplicate", "同一执行段只能请求一次续段")
        _exact_payload(frame["payload"], {"reason", "budget_usage"})
        reason = _code(frame["payload"]["reason"], name="segment restart reason")
        usage = frame["payload"]["budget_usage"]
        if (
            not isinstance(usage, Mapping)
            or set(usage) != _SEGMENT_BUDGET_USAGE_KEYS
            or any(type(value) is not int or value < 0 for value in usage.values())
        ):
            _reject("segment_restart_budget_invalid", "续段预算回执不合法")
        self._segment_restart_reason = reason
        self._segment_budget_usage = dict(usage)
        return [{
            "method": "turn/continued",
            "params": {**self._base_params(frame), "reason": reason},
        }]

    def _base_params(self, frame: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "threadId": self.conversation_id,
            "turnId": self.turn_id,
            "stepId": frame["step_id"],
            "epochId": self.epoch_id,
        }

    def _status(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = frame["payload"]
        _exact_payload(payload, set(), {"status"})
        status = payload.get("status", frame["type"].rsplit(".", 1)[-1])
        _code(status, name="status")
        return [{
            "method": "native/bridge/status",
            "params": {**self._base_params(frame), "event": frame["type"], "status": status},
        }]

    def _text_delta(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = frame["payload"]
        _exact_payload(payload, {"text"})
        delta = _text(payload["text"], name="text", maximum=MAX_ASSISTANT_TEXT_CHARS)
        step = frame["step_id"]
        if step in self._completed_text_steps:
            _reject("text_after_completion", "文本block结束后又收到增量")
        combined = self._assistant_text.get(step, "") + delta
        if len(combined) > MAX_ASSISTANT_TEXT_CHARS:
            _reject("assistant_text_too_large", "Assistant文本超过投影上限")
        self._assistant_text[step] = combined
        item_id = f"{self.turn_id}.{step}.message"
        return [{
            "method": "item/agentMessage/delta",
            "params": {**self._base_params(frame), "itemId": item_id, "delta": delta},
        }]

    def _text_completed(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = frame["payload"]
        _exact_payload(payload, {"text"})
        text = _text(
            payload["text"], name="text", maximum=MAX_ASSISTANT_TEXT_CHARS, allow_empty=True,
        )
        step = frame["step_id"]
        if step in self._completed_text_steps:
            _reject("duplicate_text_completion", "同一文本block重复结束")
        streamed = self._assistant_text.get(step, "")
        if streamed and streamed != text:
            _reject("text_completion_mismatch", "文本结束内容与已接收增量不一致")
        self._assistant_text[step] = text
        self._completed_text_steps.add(step)
        item_id = f"{self.turn_id}.{step}.message"
        return [{
            "method": "item/completed",
            "params": {
                **self._base_params(frame),
                "item": {"type": "agentMessage", "id": item_id, "text": text},
            },
        }]

    def _reasoning_event(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = frame["payload"]
        event_type = frame["type"]
        if event_type == "assistant.reasoning.started":
            _exact_payload(payload, set())
            if self._reasoning_active:
                _reject("reasoning_already_started", "Reasoning block重复开始")
            self._reasoning = ""
            self._reasoning_active = True
            self._reasoning_block_index += 1
            digest = hashlib.sha256(
                (
                    f"{self.turn_id}\0{frame['step_id']}\0"
                    f"{self._reasoning_block_index}"
                ).encode()
            ).hexdigest()[:32]
            self._reasoning_item_id = f"reasoning:{digest}"
            status = "started"
        elif event_type == "assistant.reasoning.delta":
            _exact_payload(payload, {"text"})
            if not self._reasoning_active:
                _reject("reasoning_not_started", "Reasoning增量缺少开始事件")
            delta = _text(payload["text"], name="reasoning", maximum=MAX_REASONING_CHARS)
            if len(self._reasoning) + len(delta) > MAX_REASONING_CHARS:
                _reject("reasoning_too_large", "Reasoning流超过内存上限")
            self._reasoning += delta
            status = "streaming"
        else:
            _exact_payload(payload, set())
            if not self._reasoning_active:
                _reject("reasoning_not_started", "Reasoning结束事件缺少开始事件")
            self._reasoning_active = False
            status = "completed"
        status_event = {
            "method": "native/reasoning/status",
            "params": {
                **self._base_params(frame),
                "status": status,
                "chars": len(self._reasoning),
            },
        }
        # Reasoning uses the existing bounded message transport but carries a
        # distinct identity and item type.  The Router therefore gets live
        # deltas and a durable terminal record without treating the text as an
        # ordinary answer or copying it into diagnostics.
        item_id = self._reasoning_item_id
        if item_id is None:
            _reject("reasoning_not_started", "Reasoning事件缺少稳定身份")
        if event_type == "assistant.reasoning.delta":
            return [status_event, {
                "method": "item/agentMessage/delta",
                "params": {
                    **self._base_params(frame),
                    "itemId": item_id,
                    "delta": delta,
                },
            }]
        if event_type == "assistant.reasoning.completed":
            return [status_event, {
                "method": "item/completed",
                "params": {
                    **self._base_params(frame),
                    "item": {
                        "type": "reasoning",
                        "id": item_id,
                        "text": self._reasoning,
                    },
                },
            }]
        return [status_event]

    def _assembly(self, frame: Mapping[str, Any]) -> _ToolAssembly:
        call_id = frame["call_id"]
        item = self._tools.get(call_id)
        if item is None:
            if len(self._tools) >= MAX_TOOL_CALLS:
                _reject("tool_call_limit", "本轮工具调用超过上限")
            item = _ToolAssembly(step_id=frame["step_id"])
            self._tools[call_id] = item
        elif item.step_id != frame["step_id"]:
            _reject("tool_step_mismatch", "同一call_id跨越不同step")
        return item

    def _tool_arguments(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = frame["payload"]
        item = self._assembly(frame)
        if item.emitted:
            _reject("tool_arguments_after_dispatch", "工具派发后又收到参数")
        if frame["type"] == "tool.arguments.delta":
            _exact_payload(payload, {"delta"})
            if item.arguments is not None:
                _reject("tool_arguments_after_completion", "工具参数结束后又收到增量")
            delta = _text(payload["delta"], name="arguments delta", maximum=MAX_TOOL_ARGUMENT_CHARS)
            combined = item.delta_text + delta
            try:
                byte_length = len(combined.encode("utf-8", errors="strict"))
            except UnicodeEncodeError as exc:
                raise BridgeProjectionError(
                    "invalid_tool_arguments", "工具参数增量不是有效UTF-8文本",
                ) from exc
            if byte_length > MAX_TOOL_ARGUMENT_CHARS:
                _reject("tool_arguments_too_large", "工具参数超过投影上限")
            item.delta_text = combined
            return [self._tool_status(frame, "assembling")]

        _exact_payload(payload, {"arguments_json"})
        if item.arguments is not None:
            _reject("duplicate_tool_arguments_completion", "工具参数重复结束")
        raw = _text(
            payload["arguments_json"],
            name="arguments_json",
            maximum=MAX_TOOL_ARGUMENT_CHARS,
        )
        digest = _raw_arguments_hash(raw)
        if item.delta_text and item.delta_text != raw:
            _reject("tool_arguments_mismatch", "工具参数结束内容与增量不一致")
        declared = frame.get("canonical_args_hash")
        if declared is None:
            _reject("missing_tool_arguments_hash", "工具参数结束事件缺少原始UTF-8 hash")
        if declared != digest:
            _reject("tool_arguments_hash_mismatch", "工具参数hash不一致")
        item.arguments = _parse_arguments_json(raw)
        item.arguments_hash = digest
        return [self._tool_status(frame, "completed"), *self._maybe_dispatch(frame, item)]

    def _tool_ready(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = frame["payload"]
        _exact_payload(payload, {"tool"})
        tool = _code(payload["tool"], name="tool")
        if not tool.startswith("gs_") and tool not in {"web_search", "web_fetch"}:
            _reject("tool_not_allowed", "桥事件请求了非App工具")
        item = self._assembly(frame)
        if item.ready:
            _reject("duplicate_tool_ready", "工具调用重复ready")
        item.tool = tool
        declared = frame.get("canonical_args_hash")
        if declared is None:
            _reject("missing_tool_arguments_hash", "工具ready事件缺少原始UTF-8 hash")
        item.ready_hash = declared
        item.ready = True
        return [self._tool_status(frame, "ready"), *self._maybe_dispatch(frame, item)]

    def _maybe_dispatch(
        self, frame: Mapping[str, Any], item: _ToolAssembly,
    ) -> list[dict[str, Any]]:
        if item.emitted or not item.ready or item.arguments is None:
            return []
        if item.ready_hash != item.arguments_hash:
            item.clear_raw()
            _reject("tool_arguments_hash_mismatch", "工具ready与参数结束事件的hash不一致")
        try:
            normalized = self._validate_tool_call(item.tool or "", dict(item.arguments))
        except Exception as exc:
            item.clear_raw()
            raise BridgeProjectionError(
                "tool_schema_invalid", "工具参数未通过App schema与权限校验",
            ) from exc
        if not isinstance(normalized, Mapping):
            item.clear_raw()
            _reject("tool_schema_invalid", "App工具校验器未返回JSON对象")
        normalized = dict(normalized)
        _check_json_tree(normalized)
        # Normalization may remove aliases/default omissions, but must not
        # silently alter the signed model arguments at this trust boundary.
        if _local_json_bytes(normalized) != _local_json_bytes(item.arguments):
            item.clear_raw()
            _reject("tool_schema_changed_arguments", "App schema校验不得静默改写桥参数")
        raw_hash = item.arguments_hash
        item.emitted = True
        arguments = normalized
        item.clear_raw()
        call_id = frame["call_id"]
        return [{
            "method": "item/tool/call",
            "id": call_id,
            "params": {
                **self._base_params(frame),
                "callId": call_id,
                "tool": item.tool,
                "arguments": arguments,
                "canonicalArgsHash": raw_hash,
            },
        }]

    def _tool_status(self, frame: Mapping[str, Any], status: str) -> dict[str, Any]:
        return {
            "method": "native/tool/status",
            "params": {
                **self._base_params(frame),
                "callId": frame["call_id"],
                "status": status,
            },
        }

    def _tool_result(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = frame["payload"]
        _exact_payload(payload, {"status"}, {"code", "resource_ids"})
        status = _code(payload["status"], name="tool result status")
        if status not in {"settled_success", "settled_failure", "unknown_after_interruption"}:
            _reject("invalid_tool_result", "工具结果不是受支持的结算终态")
        item = self._tools.get(frame["call_id"])
        if item is None or not item.emitted:
            _reject("tool_result_without_dispatch", "工具结果没有对应的已派发调用")
        if frame["call_id"] in self._settled_calls:
            _reject("duplicate_tool_settlement", "同一工具调用出现多个结算终态")
        code = payload.get("code")
        if code is not None:
            if type(code) is int and -(2**53 - 1) <= code <= 2**53 - 1:
                pass
            elif isinstance(code, str):
                _code(code, name="tool result code")
            else:
                _reject("invalid_tool_result", "工具结果code不合法")
        resources = payload.get("resource_ids", [])
        if not isinstance(resources, list) or len(resources) > 64:
            _reject("invalid_tool_result", "工具结果resource_ids不合法")
        for resource in resources:
            _code(resource, name="resource_id")
        self._settled_calls.add(frame["call_id"])
        return [{
            "method": "native/tool/result",
            "params": {
                **self._base_params(frame),
                "callId": frame["call_id"],
                "status": status,
                "code": code,
                "resourceIds": list(resources),
            },
        }]

    def _usage(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = frame["payload"]
        metadata_keys = {"provider_attempt_id", "provider_purpose"}
        present_metadata = set(payload) & metadata_keys
        if (
            not payload
            or set(payload) - (_USAGE_KEYS | metadata_keys)
            or (present_metadata and present_metadata != metadata_keys)
        ):
            _reject("invalid_usage", "usage字段不受支持")
        usage = {key: value for key, value in payload.items() if key in _USAGE_KEYS}
        if not usage:
            _reject("invalid_usage", "usage缺少token计数")
        for value in usage.values():
            if type(value) is not int or not 0 <= value <= MAX_USAGE_VALUE:
                _reject("invalid_usage", "usage必须是非负有界整数")
        params = {**self._base_params(frame), "usage": usage}
        if present_metadata:
            params["providerAttemptId"] = _code(
                payload["provider_attempt_id"], name="provider attempt id"
            )
            purpose = _code(
                payload["provider_purpose"], name="provider purpose"
            )
            if purpose not in {"agent", "compaction", "overflow-retry"}:
                _reject("invalid_usage", "provider purpose不受支持")
            params["providerPurpose"] = purpose
        return [{
            "method": "native/usage",
            "params": params,
        }]

    def _terminal(self, frame: Mapping[str, Any]) -> list[dict[str, Any]]:
        payload = frame["payload"]
        event_type = frame["type"]
        if event_type == "turn.completed":
            if self._compactions:
                _reject(
                    "compaction_lifecycle_incomplete",
                    "上下文整理尚未结束，当前轮不能成功结束",
                )
            if any(
                not item.emitted or call_id not in self._settled_calls
                for call_id, item in self._tools.items()
            ):
                _reject(
                    "tool_lifecycle_incomplete",
                    "工具调用未完成参数、派发和唯一结算，当前轮不能成功结束",
                )
            _exact_payload(payload, set(), {"status"})
            if "status" in payload and payload["status"] != "completed":
                _reject("invalid_terminal_payload", "turn.completed状态不合法")
            projected = [{
                "method": "turn/completed",
                "params": {
                    "threadId": self.conversation_id,
                    "turn": {"id": self.turn_id, "status": "completed"},
                },
            }]
            self._terminal_reason = None
        elif event_type == "turn.cancelled":
            _exact_payload(payload, set(), {"reason"})
            if "reason" in payload:
                self._terminal_reason = _code(
                    payload["reason"], name="cancel reason",
                )
            else:
                self._terminal_reason = "interrupted"
            projected = [{
                "method": "turn/completed",
                "params": {
                    "threadId": self.conversation_id,
                    "turn": {"id": self.turn_id, "status": "interrupted"},
                },
            }]
        else:
            _exact_payload(payload, {"code"})
            code = _code(payload["code"], name="error code")
            self._terminal_reason = code
            projected = [{
                "method": "error",
                "params": {
                    "threadId": self.conversation_id,
                    "turnId": self.turn_id,
                    "willRetry": False,
                    "error": {"type": code, "message": "原生Agent运行失败。"},
                },
            }]
        self._clear_volatile()
        return projected

    def _clear_volatile(self, *, preserve_reasoning: bool = False) -> None:
        self._assistant_text.clear()
        self._completed_text_steps.clear()
        if not preserve_reasoning:
            self._reasoning = ""
            self._reasoning_item_id = None
        self._reasoning_active = False
        for item in self._tools.values():
            item.clear_raw()
        self._compactions.clear()


class NativeBridgeProjectionAcker:
    """Atomically pair successful App projection with a cumulative ACK.

    The returned projected events must only be published after this method
    returns.  A projection or ACK-construction failure poisons the projector
    and returns no receipt.  Duplicate transport events are validated by the
    projector and receive the exact previously-built ACK identity, allowing a
    lost ACK to be retried without advancing the independent control stream.
    """

    def __init__(
        self,
        projector: NativeBridgeEventProjector,
        *,
        first_control_seq: int = 1,
        message_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ):
        if not isinstance(projector, NativeBridgeEventProjector):
            raise TypeError("projector must be a NativeBridgeEventProjector")
        if (
            type(first_control_seq) is not int
            or not 1 <= first_control_seq <= JSON_SAFE_INTEGER_MAX
        ):
            raise ValueError("first_control_seq must be a positive safe integer")
        if not callable(message_id_factory):
            raise TypeError("message_id_factory must be callable")
        self._projector = projector
        self._next_control_seq = first_control_seq
        self._message_id_factory = message_id_factory
        self._receipts: dict[str, dict[str, Any]] = {}

    def ingest(self, value: Mapping[str, Any]) -> ProjectedEventReceipt:
        """Project one event and return its sendable cumulative ACK."""
        try:
            frame = validate_frame(value)
        except BridgeProtocolError:
            self._projector.invalidate()
            raise
        if frame["kind"] != "event":
            self._projector.invalidate()
            _reject("invalid_event_frame", "投影确认器只接受event桥帧")
        projected = self._projector.ingest(frame)
        previous = self._receipts.get(frame["message_id"])
        if previous is not None:
            return ProjectedEventReceipt(tuple(projected), copy.deepcopy(previous), True)
        if len(self._receipts) >= MAX_TURN_FRAMES:
            self._projector.invalidate()
            _reject("event_ack_limit", "事件确认数量超过单轮上限")
        if self._next_control_seq > JSON_SAFE_INTEGER_MAX:
            self._projector.invalidate()
            _reject("event_ack_sequence_exhausted", "事件确认控制序号已耗尽")
        try:
            acknowledgement = make_frame(
                kind="control",
                frame_type="events.ack",
                message_id=self._message_id_factory(),
                runtime_incarnation=self._projector.runtime_incarnation,
                conversation_id=self._projector.conversation_id,
                turn_id=self._projector.turn_id,
                step_id=frame["step_id"],
                epoch_id=self._projector.epoch_id,
                event_seq=self._next_control_seq,
                payload={"through_event_seq": frame["event_seq"]},
            )
        except (BridgeProtocolError, StopIteration, TypeError, ValueError):
            self._projector.invalidate()
            raise BridgeProjectionError(
                "event_ack_build_failed", "无法为已投影事件建立确认帧",
            ) from None
        self._next_control_seq += 1
        self._receipts[frame["message_id"]] = copy.deepcopy(acknowledgement)
        return ProjectedEventReceipt(
            tuple(projected), copy.deepcopy(acknowledgement), False,
        )

    def diagnostic_snapshot(self) -> dict[str, Any]:
        """Return identity-free counters; ACK payloads are never logged."""
        return {
            "next_control_seq": self._next_control_seq,
            "cached_acknowledgements": len(self._receipts),
        }


__all__ = [
    "MAX_ASSISTANT_TEXT_CHARS",
    "MAX_REASONING_CHARS",
    "MAX_TOOL_ARGUMENT_CHARS",
    "MAX_TOOL_CALLS",
    "BridgeProjectionError",
    "NativeBridgeEventProjector",
    "NativeBridgeProjectionAcker",
    "ProjectedEventReceipt",
]
