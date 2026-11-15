"""Cross-process Phase 4B bridge for one App-owned candidate write.

The Node/DSH sidecar may request ``gs_patch_candidate`` and wait for its
settlement, but it never owns candidate state or the write transaction ledger.
This module only projects the current App schema, converts the authoritative
``NativeWriteCoordinator`` result into a bounded wire settlement, and validates
the exact acknowledgement returned by the sidecar.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping
from typing import Any

from genshin_app.equipment.schema import PlanError

from . import tools
from .bridge_protocol import JSON_SAFE_INTEGER_MAX, make_frame, validate_frame
from .native_tool_receipt import sanitize_native_response
from .native_write_ledger import PATCH_TOOL
from .session_lifecycle_v2 import MAX_TOOL_RESULT_BYTES

SNAPSHOT_CONTRACT = "genshin-native-write-tool-snapshot-v1"
SETTLEMENT_CONTRACT = "genshin-native-write-tool-settlement-v1"
SETTLEMENT_ACK_CONTRACT = "genshin-native-write-tool-settlement-ack-v1"
WRITE_POLICY = "candidate_patch"
TERMINAL_STATES = frozenset({
    "settled_success",
    "settled_failure",
    "unknown_after_interruption",
})

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_SNAPSHOT_BODY_KEYS = {
    "contract",
    "workflow",
    "workflow_stage",
    "candidate_revision",
    "stage_revision",
    "tools",
    "write_policies",
}
_SETTLEMENT_KEYS = {
    "contract",
    "tool",
    "status",
    "result_json",
    "result_hash",
    "result_code",
    "resource_ids",
    "runtime_generation",
    "writer_fence",
}
_ACK_KEYS = {
    "contract",
    "disposition",
    "settlement_status",
    "result_hash",
    "runtime_generation",
    "writer_fence",
}
_SETTLEMENT_FRAME_OPTIONALS = {
    "request_id",
    "call_id",
    "document_id",
    "candidate_revision",
    "stage_revision",
    "database_fingerprint",
    "tool_manifest_hash",
    "canonical_args_hash",
}


def _fail(message: str, reason: str = "native_write_bridge_invalid") -> None:
    raise PlanError(message, reason)


def _identity(value: Any, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不符合桥协议身份语法")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}必须是小写SHA-256")
    return value


def _revision(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"{name}必须是非负安全整数")
    return value


def _portable_json(value: Any, *, depth: int = 0) -> None:
    if depth > 64:
        _fail("原生写桥JSON嵌套超过上限")
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        if abs(value) > JSON_SAFE_INTEGER_MAX:
            _fail("原生写桥JSON整数超过JavaScript安全范围")
        return
    if isinstance(value, float):
        if (
            not math.isfinite(value)
            or value == 0.0 and math.copysign(1.0, value) < 0
        ):
            _fail("原生写桥JSON包含非有限数值")
        return
    if isinstance(value, list):
        for item in value:
            _portable_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("原生写桥JSON对象字段名必须是字符串")
            _portable_json(item, depth=depth + 1)
        return
    _fail("原生写桥内容不属于可移植JSON")


def _canonical_bytes(value: Any, *, maximum: int = MAX_TOOL_RESULT_BYTES) -> bytes:
    _portable_json(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError, OverflowError) as exc:
        raise PlanError(
            "原生写桥内容不是规范JSON", "native_write_bridge_invalid",
        ) from exc
    if not encoded or len(encoded) > maximum:
        _fail("原生写桥内容超过64KiB", "native_write_bridge_size")
    return encoded


def _snapshot_body(session) -> dict:
    definitions = tools.definitions_for_session(session)
    selected = [
        copy.deepcopy(row)
        for row in definitions
        if isinstance(row, dict) and row.get("name") == PATCH_TOOL
    ]
    if len(selected) != 1:
        _fail(
            "当前阶段必须且只能披露一个gs_patch_candidate定义",
            "native_write_tool_unavailable",
        )
    definition = selected[0]
    if (
        set(definition) != {"type", "name", "description", "inputSchema"}
        or definition.get("type") != "function"
        or not isinstance(definition.get("description"), str)
        or not isinstance(definition.get("inputSchema"), dict)
    ):
        _fail("候选写工具定义不合法")
    workflow = _identity(getattr(session, "workflow", None), "workflow")
    workflow_stage = _identity(
        getattr(session, "workflow_stage", workflow), "workflow_stage",
    )
    stage_state = getattr(session, "stage_state", {})
    stage_revision = (
        stage_state.get("stage_revision", 0)
        if isinstance(stage_state, dict)
        else 0
    )
    return {
        "contract": SNAPSHOT_CONTRACT,
        "workflow": workflow,
        "workflow_stage": workflow_stage,
        "candidate_revision": _revision(
            getattr(session, "revision", None), "candidate_revision",
        ),
        "stage_revision": _revision(stage_revision, "stage_revision"),
        "tools": [definition],
        "write_policies": {PATCH_TOOL: WRITE_POLICY},
    }


def build_native_write_snapshot(session) -> dict:
    """Project the one current candidate-write schema and bind its revision."""
    lock = getattr(session, "_lock", None)
    if lock is None:
        _fail(
            "候选写工具快照需要session共享RLock",
            "native_write_lock_required",
        )
    with lock:
        body = _snapshot_body(session)
        digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
        return {**body, "tool_manifest_hash": digest}


def validate_native_write_snapshot(value: Mapping) -> dict:
    if not isinstance(value, Mapping):
        _fail("候选写工具快照必须是JSON对象")
    snapshot = copy.deepcopy(dict(value))
    if set(snapshot) != _SNAPSHOT_BODY_KEYS | {"tool_manifest_hash"}:
        _fail("候选写工具快照字段不合法")
    claimed = _digest(snapshot.pop("tool_manifest_hash"), "tool_manifest_hash")
    if snapshot.get("contract") != SNAPSHOT_CONTRACT:
        _fail("候选写工具快照版本不受支持")
    _identity(snapshot.get("workflow"), "workflow")
    _identity(snapshot.get("workflow_stage"), "workflow_stage")
    _revision(snapshot.get("candidate_revision"), "candidate_revision")
    _revision(snapshot.get("stage_revision"), "stage_revision")
    definitions = snapshot.get("tools")
    if not isinstance(definitions, list) or len(definitions) != 1:
        _fail("候选写工具快照必须只含一个工具")
    definition = definitions[0]
    if (
        not isinstance(definition, dict)
        or set(definition) != {"type", "name", "description", "inputSchema"}
        or definition.get("type") != "function"
        or definition.get("name") != PATCH_TOOL
        or not isinstance(definition.get("description"), str)
        or not isinstance(definition.get("inputSchema"), dict)
    ):
        _fail("候选写工具快照定义不合法")
    if snapshot.get("write_policies") != {PATCH_TOOL: WRITE_POLICY}:
        _fail("候选写策略不合法")
    actual = hashlib.sha256(_canonical_bytes(snapshot)).hexdigest()
    if actual != claimed:
        _fail("候选写工具快照哈希不匹配", "native_write_tool_hash_mismatch")
    return {**snapshot, "tool_manifest_hash": claimed}


def validate_native_write_arguments(
    session,
    snapshot: Mapping,
    tool: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    checked = validate_native_write_snapshot(snapshot)
    if tool != PATCH_TOOL or not isinstance(arguments, dict):
        _fail("原生写轮次请求了未披露工具", "native_write_tool_mismatch")
    if checked["tools"][0]["name"] != tool:
        _fail("原生写轮次请求了未披露工具", "native_write_tool_mismatch")
    try:
        return tools.validate_tool_arguments_for_session(session, tool, arguments)
    except Exception as exc:
        raise PlanError(
            "原生写参数不符合当前阶段权限",
            "native_write_arguments_invalid",
        ) from exc


def build_native_write_settlement(
    response: Mapping | None,
    *,
    runtime_generation: str,
    writer_fence: str,
    outcome_unknown: bool = False,
) -> dict:
    """Convert one authoritative ledger outcome into the write wire contract."""
    if outcome_unknown:
        result = {
            "reason": "outcome_unknown",
            "message": "候选写结果未知，必须核对当前候选后再继续。",
        }
        status = "unknown_after_interruption"
        result_code: str | int = "outcome_unknown"
    else:
        try:
            result = sanitize_native_response(
                copy.deepcopy(dict(response)) if isinstance(response, Mapping) else response,
                tool=PATCH_TOOL,
                arguments=None,
            )
        except Exception as exc:
            raise PlanError(
                "候选写结果不能安全投影", "native_write_bridge_invalid",
            ) from exc
        if not isinstance(result, dict):
            _fail("候选写结果必须是JSON对象")
        code = result.get("code")
        reason = result.get("reason")
        if type(code) is int and code == 0 and "reason" not in result:
            status = "settled_success"
            result_code = 0
        elif (
            type(code) is int
            and code != 0
            and ("reason" not in result or (
                isinstance(reason, str) and _IDENTITY.fullmatch(reason) is not None
            ))
        ):
            status = "settled_failure"
            result_code = reason if isinstance(reason, str) else code
        else:
            _fail(
                "候选写结果缺少一致的终态机器码",
                "native_write_settlement_invalid",
            )
    encoded = _canonical_bytes(result)
    return {
        "contract": SETTLEMENT_CONTRACT,
        "tool": PATCH_TOOL,
        "status": status,
        "result_json": encoded.decode("utf-8"),
        "result_hash": hashlib.sha256(encoded).hexdigest(),
        "result_code": result_code,
        "resource_ids": [],
        "runtime_generation": _identity(
            runtime_generation, "runtime_generation",
        ),
        "writer_fence": _identity(writer_fence, "writer_fence"),
    }


def validate_native_write_settlement(value: Mapping) -> dict:
    if not isinstance(value, Mapping) or set(value) != _SETTLEMENT_KEYS:
        _fail("写tool.settle payload字段不合法", "native_write_settlement_invalid")
    payload = copy.deepcopy(dict(value))
    if payload.get("contract") != SETTLEMENT_CONTRACT or payload.get("tool") != PATCH_TOOL:
        _fail("写tool.settle契约或工具不受支持", "native_write_settlement_invalid")
    if payload.get("status") not in TERMINAL_STATES:
        _fail("写tool.settle终态不合法", "native_write_settlement_invalid")
    raw = payload.get("result_json")
    if not isinstance(raw, str):
        _fail("写tool.settle结果必须是JSON字符串", "native_write_settlement_invalid")
    try:
        encoded = raw.encode("utf-8", errors="strict")
        parsed = json.loads(raw)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise PlanError(
            "写tool.settle结果不是有效JSON", "native_write_settlement_invalid",
        ) from exc
    if not isinstance(parsed, dict) or encoded != _canonical_bytes(parsed):
        _fail("写tool.settle结果不是规范JSON", "native_write_settlement_invalid")
    if hashlib.sha256(encoded).hexdigest() != _digest(
        payload.get("result_hash"), "result_hash",
    ):
        _fail("写tool.settle结果哈希不匹配", "native_write_settlement_invalid")
    if payload.get("resource_ids") != []:
        _fail("候选写结算不能携带资料引用", "native_write_settlement_invalid")
    _identity(payload.get("runtime_generation"), "runtime_generation")
    _identity(payload.get("writer_fence"), "writer_fence")
    status = payload["status"]
    code = payload.get("result_code")
    if code is not None:
        if type(code) is int:
            if abs(code) > JSON_SAFE_INTEGER_MAX:
                _fail("写结算result_code超过安全整数", "native_write_settlement_invalid")
        elif isinstance(code, str):
            _identity(code, "result_code")
        else:
            _fail("写结算result_code不合法", "native_write_settlement_invalid")
    if status == "settled_success":
        if code != 0 or type(parsed.get("code")) is not int or parsed["code"] != 0:
            _fail("成功写结算的内外code必须都是整数0", "native_write_settlement_invalid")
        if "reason" in parsed:
            _fail("成功写结算不能携带reason", "native_write_settlement_invalid")
    elif status == "settled_failure":
        inner_code = parsed.get("code")
        inner_reason = parsed.get("reason")
        has_code = "code" in parsed
        has_reason = "reason" in parsed
        valid_code = (
            type(inner_code) is int
            and -JSON_SAFE_INTEGER_MAX <= inner_code <= JSON_SAFE_INTEGER_MAX
            and inner_code != 0
        ) or (
            isinstance(inner_code, str)
            and _IDENTITY.fullmatch(inner_code) is not None
            and inner_code != "0"
        )
        valid_reason = (
            isinstance(inner_reason, str)
            and _IDENTITY.fullmatch(inner_reason) is not None
        )
        expected = inner_reason if has_reason else inner_code
        if (
            not has_code and not has_reason
            or has_code and not valid_code
            or has_reason and not valid_reason
            or expected != code
        ):
            _fail("失败写结算机器码不一致", "native_write_settlement_invalid")
        if inner_code == "outcome_unknown" or inner_reason == "outcome_unknown":
            _fail("未知结果不能伪装成失败结算", "native_write_settlement_invalid")
    else:
        if (
            code != "outcome_unknown"
            or parsed.get("reason") != "outcome_unknown"
            or ("code" in parsed and parsed["code"] != "outcome_unknown")
            or set(parsed) - {"code", "reason", "message"}
            or ("message" in parsed and not isinstance(parsed["message"], str))
        ):
            _fail("未知写结算字段不合法", "native_write_settlement_invalid")
    return payload


def make_native_write_settle_request(
    *,
    settlement: Mapping,
    runtime_incarnation: str,
    conversation_id: str,
    turn_id: str,
    step_id: str,
    epoch_id: str,
    request_id: str,
    event_seq: int,
    call_id: str,
    document_id: str,
    candidate_revision: int,
    stage_revision: int,
    database_fingerprint: str,
    tool_manifest_hash: str,
    canonical_args_hash: str,
) -> dict:
    payload = validate_native_write_settlement(settlement)
    return make_frame(
        kind="request",
        frame_type="tool.settle",
        message_id=str(uuid.uuid4()),
        runtime_incarnation=_identity(runtime_incarnation, "runtime_incarnation"),
        conversation_id=_identity(conversation_id, "conversation_id"),
        turn_id=_identity(turn_id, "turn_id"),
        step_id=_identity(step_id, "step_id"),
        epoch_id=_identity(epoch_id, "epoch_id"),
        event_seq=event_seq,
        payload=payload,
        request_id=_identity(request_id, "request_id"),
        call_id=_identity(call_id, "call_id"),
        document_id=_identity(document_id, "document_id"),
        candidate_revision=_revision(candidate_revision, "candidate_revision"),
        stage_revision=_revision(stage_revision, "stage_revision"),
        database_fingerprint=_digest(
            database_fingerprint, "database_fingerprint",
        ),
        tool_manifest_hash=_digest(tool_manifest_hash, "tool_manifest_hash"),
        canonical_args_hash=_digest(
            canonical_args_hash, "canonical_args_hash",
        ),
    )


def validate_native_write_settle_ack(value: Mapping, *, request: Mapping) -> dict:
    request_frame = validate_frame(request)
    response = validate_frame(value)
    if request_frame["kind"] != "request" or request_frame["type"] != "tool.settle":
        _fail("写结算原请求类型不合法", "native_write_settlement_ack_invalid")
    if response["kind"] != "response" or response["type"] != "tool.settle.ack":
        _fail("写结算确认类型不合法", "native_write_settlement_ack_invalid")
    actual_request_optional = set(request_frame) - {
        "protocol", "version", "message_id", "kind", "runtime_incarnation",
        "conversation_id", "turn_id", "step_id", "epoch_id", "event_seq",
        "type", "payload", "payload_hash",
    }
    actual_response_optional = set(response) - {
        "protocol", "version", "message_id", "kind", "runtime_incarnation",
        "conversation_id", "turn_id", "step_id", "epoch_id", "event_seq",
        "type", "payload", "payload_hash",
    }
    if actual_request_optional != _SETTLEMENT_FRAME_OPTIONALS:
        _fail("写结算请求绑定字段不完整", "native_write_settlement_ack_invalid")
    if actual_response_optional != _SETTLEMENT_FRAME_OPTIONALS:
        _fail("写结算确认绑定字段不完整", "native_write_settlement_ack_invalid")
    validate_native_write_settlement(request_frame["payload"])
    for field in (
        "runtime_incarnation", "conversation_id", "turn_id", "step_id",
        "epoch_id", "request_id", "call_id", "document_id",
        "candidate_revision", "stage_revision", "database_fingerprint",
        "tool_manifest_hash", "canonical_args_hash",
    ):
        if response.get(field) != request_frame.get(field):
            _fail("写结算确认绑定不匹配", "native_write_settlement_ack_invalid")
    payload = response.get("payload")
    if not isinstance(payload, dict) or set(payload) != _ACK_KEYS:
        _fail("写结算确认payload字段不合法", "native_write_settlement_ack_invalid")
    settlement = request_frame["payload"]
    expected = {
        "contract": SETTLEMENT_ACK_CONTRACT,
        "disposition": "accepted",
        "settlement_status": settlement["status"],
        "result_hash": settlement["result_hash"],
        "runtime_generation": settlement["runtime_generation"],
        "writer_fence": settlement["writer_fence"],
    }
    if payload != expected:
        _fail("写结算确认内容不匹配", "native_write_settlement_ack_invalid")
    return response
