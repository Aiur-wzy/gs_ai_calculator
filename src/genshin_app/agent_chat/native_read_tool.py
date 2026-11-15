"""Bounded Phase 3 App-owned read tool and bridge settlement contract.

This module deliberately does not own a pipe, process, provider, or GUI.  It
projects one existing model-facing tool from the authoritative staged tool
registry and executes that tool through the existing server-side gate.  The
Node runtime may wait for the result, but it never becomes the authority for
game data or workflow permissions.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager, nullcontext
from typing import Any, ClassVar

from genshin_app.equipment.schema import PlanError

from . import tools
from .bridge_protocol import (
    JSON_SAFE_INTEGER_MAX,
    decode_json_object,
    make_frame,
    raw_json_hash,
    validate_frame,
)
from .native_tool_receipt import sanitize_native_response
from .session_lifecycle_v2 import (
    MAX_TOOL_RESULT_BYTES,
    activate_context,
    begin_call,
    create_ledger,
    lookup_call,
    make_call_identity,
    mark_unknown_after_interruption,
    settle_call,
)

READ_TOOL_NAME = "gs_read_context"
SNAPSHOT_CONTRACT = "genshin-native-read-tool-snapshot-v1"
SETTLEMENT_CONTRACT = "genshin-native-tool-settlement-v1"
SETTLEMENT_ACK_CONTRACT = "genshin-native-tool-settlement-ack-v1"
TERMINAL_STATES = frozenset(
    {
        "settled_success",
        "settled_failure",
        "unknown_after_interruption",
    }
)

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_RLOCK_TYPE = type(threading.RLock())
_SNAPSHOT_BODY_KEYS = {
    "contract",
    "workflow",
    "workflow_stage",
    "candidate_revision",
    "stage_revision",
    "tools",
}
_SETTLEMENT_PAYLOAD_KEYS = {
    "contract",
    "tool",
    "status",
    "result_json",
    "result_hash",
    "result_code",
    "resource_ids",
    "runtime_generation",
    "fence_token",
}
_ACK_PAYLOAD_KEYS = {
    "contract",
    "disposition",
    "settlement_status",
    "result_hash",
    "runtime_generation",
    "fence_token",
}
_SETTLEMENT_FRAME_OPTIONALS = {
    "request_id",
    "call_id",
    "candidate_revision",
    "stage_revision",
    "database_fingerprint",
    "tool_manifest_hash",
    "canonical_args_hash",
}


def _fail(message: str, reason: str = "native_read_tool_invalid", details=None) -> None:
    raise PlanError(message, reason, details)


def _portable_json(value: Any, *, depth: int = 0) -> None:
    """Validate the common lossless Python/ECMAScript JSON subset."""
    if depth > 64:
        _fail("原生只读工具JSON嵌套超过上限")
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        if abs(value) > JSON_SAFE_INTEGER_MAX:
            _fail("原生只读工具JSON整数超过JavaScript安全范围")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail("原生只读工具JSON包含非有限数值")
        return
    if isinstance(value, list):
        for item in value:
            _portable_json(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                _fail("原生只读工具JSON对象字段名必须是字符串")
            _portable_json(item, depth=depth + 1)
        return
    _fail("原生只读工具内容不属于可移植JSON")


def _canonical_bytes(value: Any, *, maximum: int) -> bytes:
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
            "原生只读工具内容不是规范JSON", "native_read_tool_invalid"
        ) from exc
    if not encoded or len(encoded) > maximum:
        _fail("原生只读工具内容超过大小上限", "native_read_tool_size")
    return encoded


def _nonnegative_revision(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"{name}必须是非负安全整数")
    return value


def _identity(value: Any, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不符合桥协议身份语法")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}必须是SHA-256")
    return value


def _snapshot_body(session) -> dict:
    definitions = tools.definitions_for_session(session)
    selected = [
        copy.deepcopy(row)
        for row in definitions
        if isinstance(row, dict) and row.get("name") == READ_TOOL_NAME
    ]
    if len(selected) != 1:
        _fail(
            "当前阶段必须且只能披露一个gs_read_context定义",
            "native_read_tool_unavailable",
        )
    definition = selected[0]
    if set(definition) != {"type", "name", "description", "inputSchema"}:
        _fail("只读工具定义字段不符合快照契约")
    if (
        definition["type"] != "function"
        or definition["name"] != READ_TOOL_NAME
        or not isinstance(definition["description"], str)
        or not isinstance(definition["inputSchema"], dict)
    ):
        _fail("只读工具定义不合法")
    workflow = _identity(getattr(session, "workflow", None), "workflow")
    workflow_stage = _identity(
        getattr(session, "workflow_stage", workflow), "workflow_stage"
    )
    stage_state = getattr(session, "stage_state", {})
    stage_revision = (
        stage_state.get("stage_revision", 0) if isinstance(stage_state, dict) else 0
    )
    return {
        "contract": SNAPSHOT_CONTRACT,
        "workflow": workflow,
        "workflow_stage": workflow_stage,
        "candidate_revision": _nonnegative_revision(
            getattr(session, "revision", None), "candidate_revision"
        ),
        "stage_revision": _nonnegative_revision(stage_revision, "stage_revision"),
        "tools": [definition],
    }


def _existing_session_lock(session):
    lock = getattr(session, "_lock", None)
    if lock is None:
        return None
    if not isinstance(lock, _RLOCK_TYPE):
        _fail("session._lock必须是可重入RLock", "native_read_session_lock_invalid")
    return lock


def build_read_tool_snapshot(session) -> dict:
    """Project and hash the one current App-owned read tool.

    The definition comes directly from :func:`tools.definitions_for_session`.
    A stage rollback or candidate/stage revision change therefore creates a new
    bound snapshot; callers holding the old snapshot cannot execute through it.
    """
    lock = _existing_session_lock(session)
    with lock if lock is not None else nullcontext():
        body = _snapshot_body(session)
        encoded = _canonical_bytes(body, maximum=MAX_TOOL_RESULT_BYTES)
        return {**body, "tool_manifest_hash": hashlib.sha256(encoded).hexdigest()}


def validate_read_tool_snapshot(value: Mapping) -> dict:
    if not isinstance(value, Mapping):
        _fail("只读工具快照必须是JSON对象")
    snapshot = copy.deepcopy(dict(value))
    if set(snapshot) != _SNAPSHOT_BODY_KEYS | {"tool_manifest_hash"}:
        _fail("只读工具快照字段不合法")
    claimed = _digest(snapshot.pop("tool_manifest_hash"), "tool_manifest_hash")
    # Reuse the same structural checks without consulting mutable session state.
    if snapshot.get("contract") != SNAPSHOT_CONTRACT:
        _fail("只读工具快照版本不受支持")
    _identity(snapshot.get("workflow"), "workflow")
    _identity(snapshot.get("workflow_stage"), "workflow_stage")
    _nonnegative_revision(snapshot.get("candidate_revision"), "candidate_revision")
    _nonnegative_revision(snapshot.get("stage_revision"), "stage_revision")
    definitions = snapshot.get("tools")
    if not isinstance(definitions, list) or len(definitions) != 1:
        _fail("只读工具快照必须只含一个工具")
    definition = definitions[0]
    if (
        not isinstance(definition, dict)
        or set(definition) != {"type", "name", "description", "inputSchema"}
        or definition.get("type") != "function"
        or definition.get("name") != READ_TOOL_NAME
        or not isinstance(definition.get("description"), str)
        or not isinstance(definition.get("inputSchema"), dict)
    ):
        _fail("只读工具快照定义不合法")
    actual = hashlib.sha256(
        _canonical_bytes(snapshot, maximum=MAX_TOOL_RESULT_BYTES)
    ).hexdigest()
    if actual != claimed:
        _fail("只读工具快照哈希不匹配", "native_read_tool_hash_mismatch")
    return {**snapshot, "tool_manifest_hash": claimed}


def visible_tool_names(session) -> tuple[str, ...]:
    """Expose the real stage projection for diagnostics, without execution."""
    lock = _existing_session_lock(session)
    if lock is None:
        _fail("阶段工具诊断需要session共享RLock", "native_read_session_lock_required")
    with lock:
        definitions = tools.definitions_for_session(session)
        names = []
        for row in definitions:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                _fail("阶段工具定义不合法")
            names.append(row["name"])
        return tuple(names)


def _result_payload(
    response: Any,
    *,
    runtime_generation: str,
    fence_token: str,
    arguments: dict | None = None,
) -> dict:
    try:
        response = sanitize_native_response(
            response,
            tool=READ_TOOL_NAME,
            arguments=arguments,
        )
        encoded = _canonical_bytes(response, maximum=MAX_TOOL_RESULT_BYTES)
        if not isinstance(response, dict):
            _fail("App工具结果必须是JSON对象")
    except PlanError:
        response = {
            "code": 1,
            "reason": "native_read_result_limit",
            "message": "只读结果超过64KiB或不属于可移植JSON，请缩小读取范围。",
        }
        encoded = _canonical_bytes(response, maximum=MAX_TOOL_RESULT_BYTES)
    code = response.get("code")
    reason = response.get("reason")
    valid_reason = (
        isinstance(reason, str)
        and _IDENTITY.fullmatch(reason) is not None
        and reason != "outcome_unknown"
    )
    valid_failure_code = (
        type(code) is int
        and -JSON_SAFE_INTEGER_MAX <= code <= JSON_SAFE_INTEGER_MAX
        and code != 0
    ) or (
        isinstance(code, str) and _IDENTITY.fullmatch(code) is not None and code != "0"
        and code != "outcome_unknown"
    )
    if type(code) is int and code == 0 and "reason" not in response:
        status = "settled_success"
        result_code: str | int | None = 0
    elif (
        valid_failure_code
        and ("reason" not in response or valid_reason)
    ):
        status = "settled_failure"
        result_code = reason if valid_reason else code
    else:
        response = {
            "code": 1,
            "reason": "native_read_result_invalid",
            "message": "App只读工具返回的终态字段不合法。",
        }
        encoded = _canonical_bytes(response, maximum=MAX_TOOL_RESULT_BYTES)
        status = "settled_failure"
        result_code = "native_read_result_invalid"
    return {
        "contract": SETTLEMENT_CONTRACT,
        "tool": READ_TOOL_NAME,
        "status": status,
        "result_json": encoded.decode("utf-8"),
        "result_hash": hashlib.sha256(encoded).hexdigest(),
        "result_code": result_code,
        "resource_ids": [],
        "runtime_generation": _identity(runtime_generation, "runtime_generation"),
        "fence_token": _identity(fence_token, "fence_token"),
    }


class NativeReadToolHost:
    """Execute one disclosed read tool and settle it exactly once in App state."""

    _BINDING_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "conversation_id",
            "turn_id",
            "step_id",
            "epoch_id",
            "runtime_incarnation",
            "runtime_generation",
            "database_fingerprint",
            "fence_token",
        }
    )

    def __init__(self, session, *, binding: Mapping, ledger: dict | None = None):
        if not isinstance(binding, Mapping) or set(binding) != self._BINDING_KEYS:
            _fail("原生只读工具绑定字段不合法")
        self.session = session
        self.binding = {
            name: _identity(binding[name], name) for name in self._BINDING_KEYS
        }
        _digest(self.binding["database_fingerprint"], "database_fingerprint")
        # CandidateSession exposes the same reentrant lock used by every
        # stage/candidate mutation.  Production execution fails closed when a
        # caller cannot supply that shared synchronization boundary.
        self._lock = _existing_session_lock(session)
        if self._lock is None:
            _fail(
                "原生只读工具需要session共享RLock", "native_read_session_lock_required"
            )
        with self._lock:
            self.snapshot = build_read_tool_snapshot(session)
            self.ledger = (
                ledger
                if ledger is not None
                else create_ledger(self.binding["conversation_id"])
            )
            activate_context(
                self.ledger,
                turn_id=self.binding["turn_id"],
                step_id=self.binding["step_id"],
                epoch_id=self.binding["epoch_id"],
                runtime_generation=self.binding["runtime_generation"],
                db_revision=self.binding["database_fingerprint"],
                manifest_hash=self.snapshot["tool_manifest_hash"],
                fence_token=self.binding["fence_token"],
            )
        # Raw results are turn-local only; the persisted ledger keeps hashes.
        self._result_cache: dict[str, dict] = {}

    @contextmanager
    def settlement_transaction(self):
        """Hold the authoritative session fence through remote admission.

        ``execute`` already protects App-owned snapshot validation and ledger
        settlement with this lock.  The process bridge must keep the same
        critical section open until the sidecar has admitted that settlement
        and the App has acknowledged the originating ``tool.call.ready``
        event.  Otherwise a stage transition can invalidate the disclosed
        tool between local execution and remote promise resolution.
        """
        with self._lock:
            yield

    def _assert_current(self, candidate_revision: Any, stage_revision: Any) -> None:
        candidate_revision = _nonnegative_revision(
            candidate_revision, "candidate_revision"
        )
        stage_revision = _nonnegative_revision(stage_revision, "stage_revision")
        current = build_read_tool_snapshot(self.session)
        if (
            candidate_revision != self.snapshot["candidate_revision"]
            or stage_revision != self.snapshot["stage_revision"]
            or current != self.snapshot
        ):
            _fail("只读工具快照已经因候选或阶段变化失效", "stale_tool_snapshot")

    def execute(
        self,
        *,
        call_id: str,
        tool: str,
        arguments_json: str,
        canonical_args_hash: str,
        candidate_revision: int,
        stage_revision: int,
    ) -> dict:
        """Execute through ``tools.call_tool`` and return a bounded settlement."""
        call_id = _identity(call_id, "call_id")
        if tool != READ_TOOL_NAME or tool not in {
            row["name"] for row in self.snapshot["tools"]
        }:
            _fail("当前原生运行时未披露该工具", "native_read_tool_forbidden")
        claimed_hash = _digest(canonical_args_hash, "canonical_args_hash")
        if raw_json_hash(arguments_json) != claimed_hash:
            _fail("工具参数哈希不匹配", "native_read_args_hash_mismatch")
        arguments = decode_json_object(arguments_json)
        with self._lock:
            # Snapshot validation, ledger admission, App execution, terminal
            # settlement, and replay cache publication form one critical
            # section.  A phase transition cannot slip between these steps.
            self._assert_current(candidate_revision, stage_revision)
            identity = make_call_identity(
                conversation_id=self.binding["conversation_id"],
                turn_id=self.binding["turn_id"],
                step_id=self.binding["step_id"],
                epoch_id=self.binding["epoch_id"],
                call_id=call_id,
                tool=tool,
                args_hash=claimed_hash,
                runtime_generation=self.binding["runtime_generation"],
                db_revision=self.binding["database_fingerprint"],
                manifest_hash=self.snapshot["tool_manifest_hash"],
                fence_token=self.binding["fence_token"],
            )
            admission = begin_call(self.ledger, identity=identity)
            if admission["disposition"] == "replay":
                cached = self._result_cache.get(call_id)
                if (
                    cached is None
                    or cached["result_hash"] != admission["settlement"]["result_hash"]
                ):
                    _fail(
                        "工具已结算，但本进程没有可安全重放的原始结果",
                        "native_read_replay_unavailable",
                    )
                return copy.deepcopy(cached)
            if admission["disposition"] != "execute":
                _fail("相同工具调用仍在执行中", "native_read_call_inflight")
            try:
                # call_tool converts expected domain failures to a response.
                # Any unexpected exception is contained because this sole
                # Phase 3 operation is read-only; leaving its ledger row
                # inflight would make retries ambiguous forever.
                response = tools.call_tool(self.session, tool, arguments)
                payload = _result_payload(
                    response,
                    runtime_generation=self.binding["runtime_generation"],
                    fence_token=self.binding["fence_token"],
                    arguments=arguments,
                )
            except Exception:  # noqa: BLE001 - trust-boundary containment
                payload = _result_payload(
                    {
                        "code": 1,
                        "reason": "native_read_internal_failure",
                        "message": "App只读工具执行失败。",
                    },
                    runtime_generation=self.binding["runtime_generation"],
                    fence_token=self.binding["fence_token"],
                )
            try:
                settlement = settle_call(
                    self.ledger,
                    identity=identity,
                    terminal_state=payload["status"],
                    result_hash=payload["result_hash"],
                    result_code=payload["result_code"],
                    resource_ids=payload["resource_ids"],
                    terminal_reason=(
                        None
                        if payload["status"] == "settled_success"
                        else str(payload["result_code"])
                    ),
                )
            except Exception:
                if lookup_call(self.ledger, identity=identity)["state"] == "inflight":
                    mark_unknown_after_interruption(
                        self.ledger,
                        identity=identity,
                        terminal_reason="settlement_internal_failure",
                    )
                raise
            if settlement["settlement"]["result_hash"] != payload["result_hash"]:
                _fail("工具结算哈希与结果不一致", "native_read_settlement_mismatch")
            self._result_cache[call_id] = copy.deepcopy(payload)
            return payload

    def make_settle_request(
        self,
        *,
        settlement: Mapping,
        call_id: str,
        canonical_args_hash: str,
        request_id: str,
        event_seq: int,
        message_id: str | None = None,
    ) -> dict:
        with self._lock:
            payload = validate_settlement_payload(settlement)
            if (
                payload["runtime_generation"] != self.binding["runtime_generation"]
                or payload["fence_token"] != self.binding["fence_token"]
            ):
                _fail("工具结算的runtime/fence绑定不匹配", "stale_tool_settlement")
            self._assert_current(
                self.snapshot["candidate_revision"], self.snapshot["stage_revision"]
            )
            call_id = _identity(call_id, "call_id")
            args_hash = _digest(canonical_args_hash, "canonical_args_hash")
            identity = make_call_identity(
                conversation_id=self.binding["conversation_id"],
                turn_id=self.binding["turn_id"],
                step_id=self.binding["step_id"],
                epoch_id=self.binding["epoch_id"],
                call_id=call_id,
                tool=READ_TOOL_NAME,
                args_hash=args_hash,
                runtime_generation=self.binding["runtime_generation"],
                db_revision=self.binding["database_fingerprint"],
                manifest_hash=self.snapshot["tool_manifest_hash"],
                fence_token=self.binding["fence_token"],
            )
            recorded = lookup_call(self.ledger, identity=identity)
            cached = self._result_cache.get(call_id)
            if recorded["state"] != "settled" or cached is None or cached != payload:
                _fail(
                    "tool.settle结果未绑定到该call的App终态",
                    "native_read_settlement_mismatch",
                )
            row = recorded["settlement"]
            if (
                row["terminal_state"] != payload["status"]
                or row["result_hash"] != payload["result_hash"]
                or row["result_code"] != payload["result_code"]
                or row["resource_ids"] != payload["resource_ids"]
            ):
                _fail(
                    "tool.settle与结算账终态不一致", "native_read_settlement_mismatch"
                )
            return make_frame(
                kind="request",
                frame_type="tool.settle",
                message_id=message_id or str(uuid.uuid4()),
                runtime_incarnation=self.binding["runtime_incarnation"],
                conversation_id=self.binding["conversation_id"],
                turn_id=self.binding["turn_id"],
                step_id=self.binding["step_id"],
                epoch_id=self.binding["epoch_id"],
                event_seq=event_seq,
                payload=payload,
                request_id=_identity(request_id, "request_id"),
                call_id=call_id,
                candidate_revision=self.snapshot["candidate_revision"],
                stage_revision=self.snapshot["stage_revision"],
                database_fingerprint=self.binding["database_fingerprint"],
                tool_manifest_hash=self.snapshot["tool_manifest_hash"],
                canonical_args_hash=args_hash,
            )


def validate_settlement_payload(
    value: Mapping,
    *,
    expected_tool: str = READ_TOOL_NAME,
) -> dict:
    """Validate a settlement payload for one explicitly expected tool.

    Phase 3's original one-tool fixture keeps ``gs_read_context`` as the
    default.  The sequential Phase 3A host passes the tool disclosed for its
    own call so both hosts share one wire validator without widening the
    legacy fixture's authority.
    """
    expected_tool = _identity(expected_tool, "expected_tool")
    if not isinstance(value, Mapping) or set(value) != _SETTLEMENT_PAYLOAD_KEYS:
        _fail("tool.settle payload字段不合法", "native_tool_settlement_invalid")
    payload = copy.deepcopy(dict(value))
    if payload["contract"] != SETTLEMENT_CONTRACT or payload["tool"] != expected_tool:
        _fail("tool.settle契约或工具不受支持", "native_tool_settlement_invalid")
    if payload["status"] not in TERMINAL_STATES:
        _fail("tool.settle终态不合法", "native_tool_settlement_invalid")
    raw = payload["result_json"]
    if not isinstance(raw, str):
        _fail("tool.settle结果必须是原始JSON字符串", "native_tool_settlement_invalid")
    try:
        encoded = raw.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise PlanError(
            "tool.settle结果不是有效UTF-8", "native_tool_settlement_invalid"
        ) from exc
    if not encoded or len(encoded) > MAX_TOOL_RESULT_BYTES:
        _fail("tool.settle结果超过64KiB", "native_tool_settlement_size")
    parsed = decode_json_object(raw, maximum_bytes=MAX_TOOL_RESULT_BYTES)
    _portable_json(parsed)
    claimed = _digest(payload["result_hash"], "result_hash")
    if hashlib.sha256(encoded).hexdigest() != claimed:
        _fail("tool.settle结果哈希不匹配", "native_tool_settlement_hash_mismatch")
    code = payload["result_code"]
    if code is not None and type(code) is not int:
        _identity(code, "result_code")
    elif type(code) is int and abs(code) > JSON_SAFE_INTEGER_MAX:
        _fail("result_code超过安全整数范围", "native_tool_settlement_invalid")
    resources = payload["resource_ids"]
    if (
        not isinstance(resources, list)
        or len(resources) > 64
        or any(
            not isinstance(item, str) or _IDENTITY.fullmatch(item) is None
            for item in resources
        )
    ):
        _fail("tool.settle resource_ids不合法", "native_tool_settlement_invalid")
    _identity(payload["runtime_generation"], "runtime_generation")
    _identity(payload["fence_token"], "fence_token")
    inner_code = parsed.get("code")
    inner_reason = parsed.get("reason")
    valid_inner_code = (
        type(inner_code) is int
        and -JSON_SAFE_INTEGER_MAX <= inner_code <= JSON_SAFE_INTEGER_MAX
        and inner_code != 0
    ) or (
        isinstance(inner_code, str)
        and _IDENTITY.fullmatch(inner_code) is not None
        and inner_code != "0"
    )
    valid_inner_reason = (
        isinstance(inner_reason, str) and _IDENTITY.fullmatch(inner_reason) is not None
    )
    status = payload["status"]
    if status == "settled_success":
        if code != 0 or type(inner_code) is not int or inner_code != 0:
            _fail(
                "成功结算的内外code必须都是整数0",
                "native_tool_settlement_invalid",
            )
        if "reason" in parsed:
            _fail(
                "成功结算不能携带reason字段",
                "native_tool_settlement_invalid",
            )
    elif status == "settled_failure":
        if (
            ("code" in parsed and not valid_inner_code)
            or ("reason" in parsed and not valid_inner_reason)
            or ("code" not in parsed and "reason" not in parsed)
            or inner_code == "outcome_unknown"
            or inner_reason == "outcome_unknown"
        ):
            _fail(
                "失败结算必须携带合法的非零code或机器reason",
                "native_tool_settlement_invalid",
            )
        # Match the App producer's canonical precedence exactly: a machine
        # reason is the public result_code when present; otherwise use code.
        expected_code = inner_reason if "reason" in parsed else inner_code
        if code != expected_code:
            _fail(
                "失败结算的result_code不符合结果JSON规范优先级",
                "native_tool_settlement_invalid",
            )
    else:
        # Unknown is not an ordinary failure result.  It has one reserved
        # machine reason and cannot expose resources whose production may or
        # may not have completed.
        if (
            code != "outcome_unknown"
            or inner_reason != "outcome_unknown"
            or ("code" in parsed and inner_code != "outcome_unknown")
            or bool(set(parsed) - {"code", "reason", "message"})
            or resources
        ):
            _fail(
                "未知结算必须使用outcome_unknown且不能携带资料引用",
                "native_tool_settlement_invalid",
            )
    return payload


def _require_settlement_frame_fields(frame: dict) -> None:
    actual_optional = set(frame) - {
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
    }
    if actual_optional != _SETTLEMENT_FRAME_OPTIONALS:
        _fail("tool.settle绑定字段不完整", "native_tool_settlement_invalid")


def validate_tool_settle_request(
    value: Mapping,
    *,
    binding: Mapping,
    snapshot: Mapping,
    call_id: str,
    canonical_args_hash: str,
    expected_tool: str = READ_TOOL_NAME,
    snapshot_validator=None,
    expected_step_id: str | None = None,
) -> dict:
    expected_tool = _identity(expected_tool, "expected_tool")
    frame = validate_frame(value)
    if frame["kind"] != "request" or frame["type"] != "tool.settle":
        _fail("工具结算必须使用tool.settle请求", "native_tool_settlement_invalid")
    _require_settlement_frame_fields(frame)
    validator = snapshot_validator or validate_read_tool_snapshot
    if not callable(validator):
        _fail(
            "tool.settle快照验证器不合法",
            "native_tool_settlement_invalid",
        )
    checked_snapshot = validator(snapshot)
    disclosed = {
        row.get("name")
        for row in checked_snapshot.get("tools", [])
        if isinstance(row, dict)
    }
    if expected_tool not in disclosed:
        _fail(
            "tool.settle工具未由快照披露",
            "native_tool_settlement_invalid",
        )
    step_id = (
        binding.get("step_id")
        if expected_step_id is None
        else _identity(expected_step_id, "expected_step_id")
    )
    expected = {
        "runtime_incarnation": binding.get("runtime_incarnation"),
        "conversation_id": binding.get("conversation_id"),
        "turn_id": binding.get("turn_id"),
        "step_id": step_id,
        "epoch_id": binding.get("epoch_id"),
        "database_fingerprint": binding.get("database_fingerprint"),
        "candidate_revision": checked_snapshot["candidate_revision"],
        "stage_revision": checked_snapshot["stage_revision"],
        "tool_manifest_hash": checked_snapshot["tool_manifest_hash"],
        "call_id": call_id,
        "canonical_args_hash": canonical_args_hash,
    }
    if any(
        frame.get(name) != expected_value for name, expected_value in expected.items()
    ):
        _fail("tool.settle绑定已经失效或不匹配", "stale_tool_settlement")
    frame["payload"] = validate_settlement_payload(
        frame["payload"],
        expected_tool=expected_tool,
    )
    if frame["payload"]["runtime_generation"] != binding.get(
        "runtime_generation"
    ) or frame["payload"]["fence_token"] != binding.get("fence_token"):
        _fail("tool.settle runtime/fence绑定已经失效", "stale_tool_settlement")
    return frame


def validate_tool_settle_ack(
    value: Mapping,
    *,
    request: Mapping,
    expected_tool: str = READ_TOOL_NAME,
) -> dict:
    expected_tool = _identity(expected_tool, "expected_tool")
    request_frame = validate_frame(request)
    if request_frame["kind"] != "request" or request_frame["type"] != "tool.settle":
        _fail("tool.settle.ack原请求类型不合法", "native_tool_settlement_ack_invalid")
    _require_settlement_frame_fields(request_frame)
    request_frame["payload"] = validate_settlement_payload(
        request_frame["payload"],
        expected_tool=expected_tool,
    )
    frame = validate_frame(value)
    if frame["kind"] != "response" or frame["type"] != "tool.settle.ack":
        _fail(
            "工具结算确认必须使用tool.settle.ack响应",
            "native_tool_settlement_ack_invalid",
        )
    _require_settlement_frame_fields(frame)
    for field in (
        "runtime_incarnation",
        "conversation_id",
        "turn_id",
        "step_id",
        "epoch_id",
        "request_id",
        "call_id",
        "candidate_revision",
        "stage_revision",
        "database_fingerprint",
        "tool_manifest_hash",
        "canonical_args_hash",
    ):
        if frame.get(field) != request_frame.get(field):
            _fail("tool.settle.ack未绑定原请求", "native_tool_settlement_ack_invalid")
    payload = frame["payload"]
    if not isinstance(payload, dict) or set(payload) != _ACK_PAYLOAD_KEYS:
        _fail("tool.settle.ack payload字段不合法", "native_tool_settlement_ack_invalid")
    if (
        payload["contract"] != SETTLEMENT_ACK_CONTRACT
        or payload["disposition"] != "accepted"
        or payload["settlement_status"] != request_frame["payload"].get("status")
        or payload["result_hash"] != request_frame["payload"].get("result_hash")
        or payload["runtime_generation"]
        != request_frame["payload"].get("runtime_generation")
        or payload["fence_token"] != request_frame["payload"].get("fence_token")
    ):
        _fail("tool.settle.ack与原请求结算不一致", "native_tool_settlement_ack_invalid")
    return frame
