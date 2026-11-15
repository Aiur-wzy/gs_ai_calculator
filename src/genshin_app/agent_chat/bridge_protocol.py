"""Strict, side-effect-free protocol primitives for the native DSH bridge.

This module deliberately owns no process, pipe, credential, model, or domain
state.  It validates the private bridge envelope before a future transport can
project events into :class:`AgentManager`.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass

PROTOCOL_NAME = "genshin-native-bridge"
PROTOCOL_VERSION = 2
MAX_FRAME_BYTES = 1024 * 1024
MAX_NESTING_DEPTH = 64
JSON_SAFE_INTEGER_MAX = (1 << 53) - 1
MAX_TURN_FRAMES = 524_288
MAX_TURN_BYTES = 2 * 1024 * 1024 * 1024
MAX_TOOL_ARGUMENT_BYTES = 256 * 1024

FRAME_KINDS = frozenset({"request", "response", "event", "control"})

REQUEST_TYPES = frozenset({
    "handshake.request",
    "heartbeat.ping",
    "models.list",
    "question.answer",
    "question.resume",
    "question.resume.status",
    "research.pre_register",
    "research.settle",
    "runtime.shutdown",
    "session.destroy",
    "session.history.get",
    "session.projection.get",
    "session.search",
    "tool.settle",
    "turn.cancel",
    "turn.start",
    "turn.steer",
})
RESPONSE_TYPES = frozenset({
    "handshake.response",
    "heartbeat.pong",
    "models.list.result",
    "question.answer.ack",
    "question.resume.ack",
    "question.resume.status.result",
    "research.pre_register.ack",
    "research.settle.ack",
    "runtime.shutdown.ack",
    "session.destroy.ack",
    "session.history.result",
    "session.projection.result",
    "session.search.result",
    "tool.settle.ack",
    "turn.cancel.ack",
    "turn.finalized",
    "turn.start.ack",
    "turn.steer.ack",
})
CONTROL_TYPES = frozenset({
    "events.ack",
    "runtime.stop",
    "secret.clear",
    "secret.set",
})
EVENT_TYPES = frozenset({
    "assistant.reasoning.completed",
    "assistant.reasoning.delta",
    "assistant.reasoning.started",
    "assistant.text.completed",
    "assistant.text.delta",
    "context.compaction.completed",
    "context.compaction.failed",
    "context.compaction.started",
    "step.completed",
    "step.started",
    "step.status",
    "tool.arguments.completed",
    "tool.arguments.delta",
    "tool.call.ready",
    "tool.result",
    "user.question.opened",
    "user.question.suspended",
    "turn.cancelled",
    "turn.completed",
    "turn.error",
    "turn.segment_restart_required",
    "turn.started",
    "turn.status",
    "usage",
})
TYPES_BY_KIND = {
    "request": REQUEST_TYPES,
    "response": RESPONSE_TYPES,
    "event": EVENT_TYPES,
    "control": CONTROL_TYPES,
}

TERMINAL_EVENT_TYPES = frozenset({
    "turn.cancelled",
    "turn.completed",
    "turn.error",
})

# These events affect authoritative execution, accounting, or final visible
# state.  Receiving one after a terminal event is a protocol failure.  Late
# streaming/status deltas are harmlessly discarded and counted instead.
CRITICAL_EVENT_TYPES = frozenset({
    "assistant.text.completed",
    "context.compaction.completed",
    "context.compaction.failed",
    "context.compaction.started",
    "step.completed",
    "step.started",
    "tool.arguments.completed",
    "tool.call.ready",
    "tool.result",
    "user.question.opened",
    "user.question.suspended",
    "turn.cancelled",
    "turn.completed",
    "turn.error",
    "turn.segment_restart_required",
    "turn.started",
    "usage",
})

REQUIRED_FIELDS = frozenset({
    "protocol",
    "version",
    "message_id",
    "kind",
    "runtime_incarnation",
    "conversation_id",
    "turn_id",
    "step_id",
    "epoch_id",
    "event_seq",
    "type",
    "payload",
    "payload_hash",
})
OPTIONAL_FIELDS = frozenset({
    "request_id",
    "call_id",
    "document_id",
    "candidate_revision",
    "stage_revision",
    "database_fingerprint",
    "tool_manifest_hash",
    "canonical_args_hash",
})

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PROTOCOL_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z")
_TOOL_TYPES = frozenset({
    "research.pre_register",
    "research.pre_register.ack",
    "research.settle",
    "research.settle.ack",
    "tool.arguments.completed",
    "tool.arguments.delta",
    "tool.call.ready",
    "tool.result",
    "tool.settle",
    "tool.settle.ack",
    "question.answer",
    "question.answer.ack",
    "question.resume",
    "question.resume.ack",
    "user.question.opened",
    "user.question.suspended",
})


class BridgeProtocolError(ValueError):
    """A deterministic protocol rejection with a stable machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _reject(code: str, message: str) -> None:
    raise BridgeProtocolError(code, message)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("duplicate_json_key", "桥帧JSON包含重复字段")
        result[key] = value
    return result


def _reject_constant(_value):
    _reject("invalid_json_number", "桥帧JSON包含NaN或Infinity")


def _canonical_json(value) -> bytes:
    """Encode the deliberately narrow cross-language envelope JSON subset.

    Payload decimals and arbitrary-key domain objects travel as opaque UTF-8
    JSON strings or App resource references.  Restricting envelope numbers to
    safe integers and object keys to ASCII avoids Python/ECMAScript canonical
    JSON disagreements without maintaining a second JCS implementation.
    """
    _check_json_value(value, protocol_object=True, allow_float=False)
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError) as exc:
        raise BridgeProtocolError("invalid_json_value", "桥帧包含不可编码的JSON值") from exc


def canonical_payload_hash(payload: Mapping) -> str:
    """Return the lowercase SHA-256 of canonical UTF-8 payload JSON."""
    if not isinstance(payload, Mapping):
        _reject("invalid_payload", "桥帧payload必须是JSON对象")
    return hashlib.sha256(_canonical_json(dict(payload))).hexdigest()


def _check_json_value(
    value,
    depth: int = 0,
    *,
    protocol_object: bool = False,
    allow_float: bool = True,
) -> None:
    if depth > MAX_NESTING_DEPTH:
        _reject("frame_too_deep", "桥帧JSON嵌套超过上限")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _reject("invalid_json_key", "桥帧JSON对象字段名必须是字符串")
            if protocol_object and _PROTOCOL_KEY.fullmatch(key) is None:
                _reject("invalid_protocol_key", "桥帧JSON对象字段名必须使用ASCII协议语法")
            _check_json_value(
                item,
                depth + 1,
                protocol_object=protocol_object,
                allow_float=allow_float,
            )
    elif isinstance(value, list):
        for item in value:
            _check_json_value(
                item,
                depth + 1,
                protocol_object=protocol_object,
                allow_float=allow_float,
            )
    elif value is None or isinstance(value, (str, bool)):
        return
    elif isinstance(value, int):
        if abs(value) > JSON_SAFE_INTEGER_MAX:
            _reject(
                "json_integer_out_of_range",
                "桥帧JSON整数超过JavaScript安全整数范围",
            )
    elif isinstance(value, float):
        if not math.isfinite(value):
            _reject("invalid_json_number", "桥帧JSON包含NaN或Infinity")
        if not allow_float:
            _reject("non_integer_protocol_number", "桥帧数值必须是JavaScript安全整数")
    else:
        _reject("invalid_json_value", "桥帧包含不可编码的JSON值")


def _identity(value, field: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        _reject("invalid_identity", f"桥帧{field}不是合法身份")
    return value


def _hash(value, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _reject("invalid_hash", f"桥帧{field}不是小写SHA-256")
    return value


def _message_id(value) -> str:
    if not isinstance(value, str):
        _reject("invalid_message_id", "桥帧message_id必须是UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise BridgeProtocolError("invalid_message_id", "桥帧message_id必须是UUID") from exc
    if str(parsed) != value:
        _reject("invalid_message_id", "桥帧message_id必须使用小写规范UUID")
    if parsed.version not in {1, 2, 3, 4, 5} or parsed.variant != uuid.RFC_4122:
        _reject("invalid_message_id", "桥帧message_id必须使用RFC 4122 UUID v1-v5")
    return value


def validate_frame(value: Mapping) -> dict:
    """Validate a decoded bridge frame and return a detached plain dictionary."""
    if not isinstance(value, Mapping):
        _reject("frame_not_object", "桥帧必须是JSON对象")
    frame = dict(value)
    _check_json_value(frame)
    unknown = set(frame) - REQUIRED_FIELDS - OPTIONAL_FIELDS
    missing = REQUIRED_FIELDS - set(frame)
    if missing:
        _reject("missing_frame_field", "桥帧缺少必需字段")
    if unknown:
        _reject("unknown_frame_field", "桥帧包含未知顶层字段")
    if len(_canonical_json(frame)) > MAX_FRAME_BYTES:
        _reject("frame_too_large", "桥帧超过1 MiB上限")
    if frame["protocol"] != PROTOCOL_NAME or frame["version"] != PROTOCOL_VERSION:
        _reject("protocol_mismatch", "桥协议名称或版本不兼容")
    _message_id(frame["message_id"])
    kind = frame["kind"]
    if kind not in FRAME_KINDS:
        _reject("invalid_frame_kind", "桥帧kind不受支持")
    frame_type = frame["type"]
    if not isinstance(frame_type, str) or frame_type not in TYPES_BY_KIND[kind]:
        _reject("unknown_frame_type", "桥帧type不属于声明的kind")
    for field in ("runtime_incarnation", "conversation_id", "turn_id", "step_id", "epoch_id"):
        _identity(frame[field], field)
    if (
        type(frame["event_seq"]) is not int
        or frame["event_seq"] < 1
        or frame["event_seq"] > JSON_SAFE_INTEGER_MAX
    ):
        _reject("invalid_event_sequence", "桥帧event_seq必须是正整数")
    if not isinstance(frame["payload"], dict):
        _reject("invalid_payload", "桥帧payload必须是JSON对象")
    _hash(frame["payload_hash"], "payload_hash")
    if frame["payload_hash"] != canonical_payload_hash(frame["payload"]):
        _reject("payload_hash_mismatch", "桥帧payload哈希不匹配")

    for field in ("request_id", "call_id", "document_id"):
        if field in frame:
            _identity(frame[field], field)
    for field in ("candidate_revision", "stage_revision"):
        if field in frame and (
            type(frame[field]) is not int
            or frame[field] < 0
            or frame[field] > JSON_SAFE_INTEGER_MAX
        ):
            _reject("invalid_revision", f"桥帧{field}必须是非负整数")
    for field in ("database_fingerprint", "tool_manifest_hash", "canonical_args_hash"):
        if field in frame:
            _hash(frame[field], field)
    if kind in {"request", "response"} and "request_id" not in frame:
        _reject("missing_request_id", "请求和响应桥帧必须包含request_id")
    if frame_type in _TOOL_TYPES and "call_id" not in frame:
        _reject("missing_call_id", "工具桥帧必须包含call_id")
    return json.loads(_canonical_json(frame).decode("utf-8"))


def encode_frame(value: Mapping) -> bytes:
    """Validate and encode one frame as canonical UTF-8 JSON (without framing bytes)."""
    encoded = _canonical_json(validate_frame(value))
    if len(encoded) > MAX_FRAME_BYTES:
        _reject("frame_too_large", "桥帧超过1 MiB上限")
    return encoded


def decode_frame(raw: bytes | str) -> dict:
    """Decode strict JSON and validate one complete bridge frame."""
    if isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise BridgeProtocolError("invalid_utf8", "桥帧不是有效UTF-8") from exc
        text = raw
    elif isinstance(raw, bytes):
        encoded = raw
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise BridgeProtocolError("invalid_utf8", "桥帧不是有效UTF-8") from exc
    else:
        _reject("invalid_frame_bytes", "桥帧必须是UTF-8文本或字节")
    if len(encoded) > MAX_FRAME_BYTES:
        _reject("frame_too_large", "桥帧超过1 MiB上限")
    try:
        value = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except BridgeProtocolError:
        raise
    except RecursionError as exc:
        raise BridgeProtocolError("frame_too_deep", "桥帧JSON嵌套超过上限") from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise BridgeProtocolError("invalid_json", "桥帧不是有效JSON") from exc
    return validate_frame(value)


def raw_json_hash(raw: str, *, maximum_bytes: int = MAX_TOOL_ARGUMENT_BYTES) -> str:
    """Hash one exact UTF-8 JSON text without cross-language reserialization."""
    if not isinstance(raw, str):
        _reject("invalid_raw_json", "原始JSON必须是字符串")
    try:
        encoded = raw.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise BridgeProtocolError("invalid_utf8", "原始JSON不是有效UTF-8") from exc
    if not encoded or len(encoded) > maximum_bytes:
        _reject("raw_json_size", "原始JSON为空或超过大小上限")
    return hashlib.sha256(encoded).hexdigest()


def decode_json_object(raw: str, *, maximum_bytes: int = MAX_TOOL_ARGUMENT_BYTES) -> dict:
    """Strictly parse a completed tool-argument JSON object.

    Unlike the bridge envelope, tool arguments may contain decimal numbers and
    arbitrary Unicode property names.  Their identity is the exact raw UTF-8
    hash returned by :func:`raw_json_hash`; this parser is for admission and
    schema validation after DSH reports the argument block complete.
    """
    raw_json_hash(raw, maximum_bytes=maximum_bytes)
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except BridgeProtocolError:
        raise
    except RecursionError as exc:
        raise BridgeProtocolError("frame_too_deep", "工具参数JSON嵌套超过上限") from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise BridgeProtocolError("invalid_raw_json", "工具参数不是有效JSON") from exc
    if not isinstance(value, dict):
        _reject("raw_json_not_object", "工具参数必须是JSON对象")
    _check_json_value(value, protocol_object=False, allow_float=True)
    return value


def make_frame(*, kind: str, frame_type: str, message_id: str,
               runtime_incarnation: str, conversation_id: str, turn_id: str,
               step_id: str, epoch_id: str, event_seq: int, payload: dict,
               **optional) -> dict:
    """Build and validate a frame while deriving its canonical payload hash."""
    frame = {
        "protocol": PROTOCOL_NAME,
        "version": PROTOCOL_VERSION,
        "message_id": message_id,
        "kind": kind,
        "runtime_incarnation": runtime_incarnation,
        "conversation_id": conversation_id,
        "turn_id": turn_id,
        "step_id": step_id,
        "epoch_id": epoch_id,
        "event_seq": event_seq,
        "type": frame_type,
        "payload": payload,
        "payload_hash": canonical_payload_hash(payload),
        **optional,
    }
    return validate_frame(frame)


def validate_event_ack_frame(value: Mapping) -> dict:
    """Validate one cumulative event acknowledgement control frame.

    ``event_seq`` belongs to the independent App-to-sidecar control stream.
    Only ``payload.through_event_seq`` names the highest contiguous Agent
    event that the App has successfully validated and projected.
    """
    frame = validate_frame(value)
    if frame["kind"] != "control" or frame["type"] != "events.ack":
        _reject("invalid_event_ack", "事件确认必须使用events.ack控制帧")
    if set(frame["payload"]) != {"through_event_seq"}:
        _reject("invalid_event_ack_payload", "事件确认payload字段不合法")
    through = frame["payload"]["through_event_seq"]
    if type(through) is not int or not 1 <= through <= JSON_SAFE_INTEGER_MAX:
        _reject("invalid_event_ack_sequence", "事件确认序号必须是正的安全整数")
    return frame


@dataclass(frozen=True)
class FrameDecision:
    status: str
    terminal_type: str | None = None


class TurnFrameValidator:
    """Validate binding, ordering, deduplication, and the unique terminal state."""

    def __init__(self, *, runtime_incarnation: str, conversation_id: str,
                 turn_id: str, epoch_id: str, first_event_seq: int = 1,
                 max_frames: int = MAX_TURN_FRAMES,
                 max_bytes: int = MAX_TURN_BYTES):
        self.runtime_incarnation = _identity(runtime_incarnation, "runtime_incarnation")
        self.conversation_id = _identity(conversation_id, "conversation_id")
        self.turn_id = _identity(turn_id, "turn_id")
        self.epoch_id = _identity(epoch_id, "epoch_id")
        if (
            type(first_event_seq) is not int
            or first_event_seq < 1
            or first_event_seq > JSON_SAFE_INTEGER_MAX
        ):
            _reject("invalid_event_sequence", "首个event_seq必须是正整数")
        for value, field in ((max_frames, "max_frames"), (max_bytes, "max_bytes")):
            if type(value) is not int or value < 1 or value > JSON_SAFE_INTEGER_MAX:
                _reject("invalid_stream_limit", f"{field}必须是正的JavaScript安全整数")
        self.next_event_seq = first_event_seq
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self.accepted_frames = 0
        self.accepted_bytes = 0
        self.terminal_type: str | None = None
        self.ignored_late_events = 0
        self._messages: dict[str, str] = {}

    @staticmethod
    def _fingerprint(frame: Mapping) -> str:
        return hashlib.sha256(_canonical_json(dict(frame))).hexdigest()

    def accept(self, value: Mapping) -> FrameDecision:
        frame = validate_frame(value)
        if frame["kind"] != "event":
            _reject("invalid_event_frame", "轮次事件校验器只接受event桥帧")
        message_id = frame["message_id"]
        fingerprint = self._fingerprint(frame)
        previous = self._messages.get(message_id)
        if previous is not None:
            if previous != fingerprint:
                _reject("message_id_conflict", "相同message_id承载了不同桥帧")
            return FrameDecision("duplicate", self.terminal_type)

        frame_bytes = len(_canonical_json(frame))
        if self.accepted_frames + 1 > self.max_frames:
            _reject("turn_frame_limit", "轮次桥事件数量超过上限")
        if self.accepted_bytes + frame_bytes > self.max_bytes:
            _reject("turn_byte_limit", "轮次桥事件总字节超过上限")

        expected = {
            "runtime_incarnation": self.runtime_incarnation,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "epoch_id": self.epoch_id,
        }
        for field, identity in expected.items():
            if frame[field] != identity:
                _reject("frame_binding_mismatch", f"桥帧{field}不属于当前轮次")

        if self.terminal_type is not None:
            if frame["type"] in CRITICAL_EVENT_TYPES:
                _reject("late_critical_event", "终态后收到关键桥事件")
            if frame["event_seq"] != self.next_event_seq:
                _reject("event_sequence_mismatch", "桥帧event_seq缺失、重复或倒序")
            self.next_event_seq += 1
            self._messages[message_id] = fingerprint
            self.accepted_frames += 1
            self.accepted_bytes += frame_bytes
            self.ignored_late_events += 1
            return FrameDecision("ignored_late", self.terminal_type)

        if frame["event_seq"] != self.next_event_seq:
            _reject("event_sequence_mismatch", "桥帧event_seq缺失、重复或倒序")
        self.next_event_seq += 1
        self._messages[message_id] = fingerprint
        self.accepted_frames += 1
        self.accepted_bytes += frame_bytes
        if frame["type"] in TERMINAL_EVENT_TYPES:
            self.terminal_type = frame["type"]
        return FrameDecision("accepted", self.terminal_type)

    def finalize(self) -> FrameDecision:
        """Prove that the stream ended with exactly one accepted terminal event."""
        if self.terminal_type is None:
            _reject("missing_terminal_event", "轮次事件流结束时缺少终态")
        return FrameDecision("complete", self.terminal_type)


__all__ = [
    "CONTROL_TYPES",
    "CRITICAL_EVENT_TYPES",
    "EVENT_TYPES",
    "FRAME_KINDS",
    "JSON_SAFE_INTEGER_MAX",
    "MAX_FRAME_BYTES",
    "MAX_NESTING_DEPTH",
    "MAX_TOOL_ARGUMENT_BYTES",
    "MAX_TURN_BYTES",
    "MAX_TURN_FRAMES",
    "PROTOCOL_NAME",
    "PROTOCOL_VERSION",
    "REQUEST_TYPES",
    "RESPONSE_TYPES",
    "TERMINAL_EVENT_TYPES",
    "BridgeProtocolError",
    "FrameDecision",
    "TurnFrameValidator",
    "canonical_payload_hash",
    "decode_frame",
    "decode_json_object",
    "encode_frame",
    "make_frame",
    "raw_json_hash",
    "validate_event_ack_frame",
    "validate_frame",
]
