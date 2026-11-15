"""Sequential Phase 3A native dispatcher for explicitly approved read tools.

This module broadens the Phase 3 fixture from one fixed tool/step to a bounded
sequence of App-owned reads.  It intentionally does not own transport, model
streaming, GUI state, or write authority.  Every call is preflighted and then
executed again through :func:`tools.call_tool`, which remains authoritative.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import secrets
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager, nullcontext
from typing import Any, ClassVar

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError

from . import tools
from .bridge_protocol import (
    JSON_SAFE_INTEGER_MAX,
    decode_json_object,
    make_frame,
    raw_json_hash,
)
from .native_read_tool import validate_settlement_payload
from .native_tool_receipt import (
    NativeToolReceipt,
    build_native_tool_receipt,
    canonical_receipt_bytes,
    issue_registered_resource_reference,
    rejected_native_tool_receipt,
    resolve_registered_resource_reference,
)
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

SNAPSHOT_CONTRACT = "genshin-native-read-tools-snapshot-v1"
SETTLEMENT_CONTRACT = "genshin-native-tool-settlement-v1"
MAX_SEQUENTIAL_READ_CALLS = 16_384
MAX_TURN_DIRECT_BYTES = 256 * 1024
MAX_TURN_RESOURCE_REFS = 64

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
    "read_policies",
}
_LIVE_BINDING_KEYS = {"database_fingerprint", "fence_token"}


def _fail(
    message: str, reason: str = "native_read_dispatch_invalid", details=None
) -> None:
    raise PlanError(message, reason, details)


def _identity(value: Any, name: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"{name}不符合桥协议身份语法")
    return value


def _digest(value: Any, name: str) -> str:
    if not isinstance(value, str) or _HASH.fullmatch(value) is None:
        _fail(f"{name}必须是SHA-256")
    return value


def _nonnegative_revision(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"{name}必须是非负安全整数")
    return value


def _existing_session_lock(session):
    lock = getattr(session, "_lock", None)
    if lock is None:
        return None
    if not isinstance(lock, _RLOCK_TYPE):
        _fail("session._lock必须是可重入RLock", "native_read_session_lock_invalid")
    return lock


def _snapshot_body(session) -> dict:
    definitions = tools.definitions_for_session(session)
    selected = []
    seen = set()
    for row in definitions:
        if not isinstance(row, dict) or set(row) != {
            "type",
            "name",
            "description",
            "inputSchema",
        }:
            _fail("阶段工具定义字段不符合快照契约")
        name = row.get("name")
        if name not in tools.NATIVE_V2_READ_POLICIES:
            continue
        if name in seen:
            _fail("阶段工具定义包含重复名称")
        schema = row.get("inputSchema")
        if (
            row.get("type") != "function"
            or not isinstance(row.get("description"), str)
            or not isinstance(schema, dict)
            or schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
        ):
            _fail("阶段工具定义不合法")
        seen.add(name)
        selected.append(copy.deepcopy(row))
    selected.sort(key=lambda row: row["name"])
    if not selected or len(selected) > 64:
        _fail(
            "当前阶段没有可披露的原生只读工具",
            "native_read_tools_unavailable",
        )
    policies = {
        row["name"]: tools.NATIVE_V2_READ_POLICIES[row["name"]] for row in selected
    }
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
        "tools": selected,
        "read_policies": policies,
    }


def build_native_read_snapshot(session) -> dict:
    """Hash the explicit read policy intersected with the real stage projection."""
    lock = _existing_session_lock(session)
    with lock if lock is not None else nullcontext():
        body = _snapshot_body(session)
        encoded = canonical_receipt_bytes(body, maximum=MAX_TOOL_RESULT_BYTES)
        return {**body, "tool_manifest_hash": hashlib.sha256(encoded).hexdigest()}


def validate_native_read_snapshot(value: Mapping) -> dict:
    if not isinstance(value, Mapping):
        _fail("原生只读工具快照必须是JSON对象")
    snapshot = copy.deepcopy(dict(value))
    if set(snapshot) != _SNAPSHOT_BODY_KEYS | {"tool_manifest_hash"}:
        _fail("原生只读工具快照字段不合法")
    claimed = _digest(snapshot.pop("tool_manifest_hash"), "tool_manifest_hash")
    if snapshot.get("contract") != SNAPSHOT_CONTRACT:
        _fail("原生只读工具快照版本不受支持")
    _identity(snapshot.get("workflow"), "workflow")
    _identity(snapshot.get("workflow_stage"), "workflow_stage")
    _nonnegative_revision(snapshot.get("candidate_revision"), "candidate_revision")
    _nonnegative_revision(snapshot.get("stage_revision"), "stage_revision")
    definitions = snapshot.get("tools")
    policies = snapshot.get("read_policies")
    if (
        not isinstance(definitions, list)
        or not 1 <= len(definitions) <= 64
        or not isinstance(policies, dict)
    ):
        _fail("原生只读工具快照定义不合法")
    names = []
    for definition in definitions:
        schema = definition.get("inputSchema") if isinstance(definition, dict) else None
        if (
            not isinstance(definition, dict)
            or set(definition) != {"type", "name", "description", "inputSchema"}
            or definition.get("type") != "function"
            or not isinstance(definition.get("name"), str)
            or not isinstance(definition.get("description"), str)
            or not isinstance(schema, dict)
            or schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
        ):
            _fail("原生只读工具快照定义不合法")
        names.append(definition["name"])
    if names != sorted(set(names)) or set(policies) != set(names):
        _fail("原生只读工具快照名称或顺序不合法")
    if any(
        name not in tools.NATIVE_V2_READ_POLICIES
        or policy != tools.NATIVE_V2_READ_POLICIES[name]
        for name, policy in policies.items()
    ):
        _fail("原生只读工具快照包含未批准的读取策略")
    actual = hashlib.sha256(
        canonical_receipt_bytes(snapshot, maximum=MAX_TOOL_RESULT_BYTES)
    ).hexdigest()
    if actual != claimed:
        _fail("原生只读工具快照哈希不匹配", "native_read_snapshot_hash_mismatch")
    return {**snapshot, "tool_manifest_hash": claimed}


def visible_native_read_tool_names(session) -> tuple[str, ...]:
    return tuple(row["name"] for row in build_native_read_snapshot(session)["tools"])


class NativeSequentialReadHost:
    """Execute bounded ordered reads, one acknowledged call per model step."""

    _BINDING_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "conversation_id",
            "turn_id",
            "epoch_id",
            "runtime_incarnation",
            "runtime_generation",
            "database_fingerprint",
            "fence_token",
        }
    )

    def __init__(
        self,
        session,
        *,
        binding: Mapping,
        ledger: dict | None = None,
        live_binding_reader: Callable[[], Mapping] | None = None,
        development_allow_static_binding: bool = False,
    ):
        if not isinstance(binding, Mapping) or set(binding) != self._BINDING_KEYS:
            _fail("原生顺序只读绑定字段不合法")
        self.session = session
        self.binding = {
            name: _identity(binding[name], name) for name in self._BINDING_KEYS
        }
        _digest(self.binding["database_fingerprint"], "database_fingerprint")
        self._lock = _existing_session_lock(session)
        if self._lock is None:
            _fail(
                "原生顺序只读工具需要session共享RLock",
                "native_read_session_lock_required",
            )
        if type(development_allow_static_binding) is not bool:
            _fail(
                "development_allow_static_binding必须是布尔值",
                "native_read_live_binding_invalid",
            )
        if live_binding_reader is None and not development_allow_static_binding:
            _fail(
                "正式原生顺序只读host必须提供权威实时绑定读取器",
                "native_read_live_binding_required",
            )
        if live_binding_reader is not None and not callable(live_binding_reader):
            _fail("live_binding_reader必须可调用", "native_read_live_binding_invalid")
        self._live_binding_reader = live_binding_reader
        self._development_static_binding = live_binding_reader is None
        with self._lock:
            self.snapshot = build_native_read_snapshot(session)
            self.ledger = (
                ledger
                if ledger is not None
                else create_ledger(self.binding["conversation_id"])
            )
            self._assert_live_binding()
        self._resource_scope = {
            "session_scope_token": secrets.token_hex(32),
            "candidate_revision": self.snapshot["candidate_revision"],
            "stage_revision": self.snapshot["stage_revision"],
            "tool_manifest_hash": self.snapshot["tool_manifest_hash"],
        }
        self._result_cache: dict[str, dict] = {}
        self._request_cache: dict[str, tuple] = {}
        self._issued_resource_refs: dict[str, dict] = {}
        self._issued_resource_refs_by_id: dict[str, dict] = {}
        self._used_steps: set[str] = set()
        self._awaiting_ack: dict | None = None
        self._settled_calls = 0
        self._direct_result_bytes = 0
        self._resource_ref_count = 0
        self._cancel_event = threading.Event()
        self._terminated_reason: str | None = None

    @contextmanager
    def settlement_transaction(self):
        """Expose the shared fence for a future transport admission window."""
        with self._lock:
            yield

    def _assert_live_binding(self) -> None:
        if self._live_binding_reader is None:
            return
        try:
            live = self._live_binding_reader()
        except Exception as exc:
            raise PlanError(
                "无法读取权威运行绑定",
                "native_read_live_binding_invalid",
            ) from exc
        if not isinstance(live, Mapping) or set(live) != _LIVE_BINDING_KEYS:
            _fail(
                "权威运行绑定读取结果不合法",
                "native_read_live_binding_invalid",
            )
        database_fingerprint = _digest(
            live.get("database_fingerprint"), "database_fingerprint"
        )
        fence_token = _identity(live.get("fence_token"), "fence_token")
        if (
            database_fingerprint != self.binding["database_fingerprint"]
            or fence_token != self.binding["fence_token"]
        ):
            _fail(
                "数据库快照或运行栅栏已经变化",
                "native_read_live_binding_stale",
            )

    def _assert_host_active(self) -> None:
        if self._terminated_reason is not None:
            _fail(
                "原生顺序只读host已经终止",
                "native_read_host_terminated",
                {"terminal_reason": self._terminated_reason},
            )
        if self._cancel_event.is_set():
            self._terminated_reason = "cancelled"
            _fail("原生顺序只读调用已取消", "native_read_cancelled")

    def _protected_session_state(self) -> dict:
        present = hasattr(self.session, "candidate")
        try:
            candidate_fingerprint = (
                fingerprint(self.session.candidate) if present else None
            )
        except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
            _fail(
                "候选状态不能生成权威指纹",
                "native_read_candidate_state_invalid",
            )
        return {
            "candidate_present": present,
            "candidate_fingerprint": candidate_fingerprint,
            "candidate_revision": getattr(self.session, "revision", None),
            "workflow": getattr(self.session, "workflow", None),
            "workflow_stage": getattr(self.session, "workflow_stage", None),
            "stage_revision": (
                self.session.stage_state.get("stage_revision")
                if isinstance(getattr(self.session, "stage_state", None), dict)
                else None
            ),
        }

    def _assert_current(self, candidate_revision: Any, stage_revision: Any) -> None:
        candidate_revision = _nonnegative_revision(
            candidate_revision, "candidate_revision"
        )
        stage_revision = _nonnegative_revision(stage_revision, "stage_revision")
        current = build_native_read_snapshot(self.session)
        if (
            candidate_revision != self.snapshot["candidate_revision"]
            or stage_revision != self.snapshot["stage_revision"]
            or current != self.snapshot
        ):
            self._terminated_reason = self._terminated_reason or "snapshot_stale"
            _fail(
                "原生只读工具快照已经因候选或阶段变化失效",
                "stale_tool_snapshot",
            )
        try:
            self._assert_live_binding()
        except PlanError:
            self._terminated_reason = self._terminated_reason or "live_binding_stale"
            raise

    def _mark_unknown_and_terminate(
        self,
        identity: dict,
        *,
        terminal_reason: str,
        message: str,
        reason: str,
    ) -> None:
        state = lookup_call(self.ledger, identity=identity)
        if state["state"] == "inflight":
            mark_unknown_after_interruption(
                self.ledger,
                identity=identity,
                terminal_reason=terminal_reason,
            )
        self._terminated_reason = terminal_reason
        self._awaiting_ack = None
        _fail(message, reason)

    def _assert_post_call_authority(
        self,
        identity: dict,
        *,
        candidate_revision: int,
        stage_revision: int,
        protected_state: dict,
    ) -> None:
        try:
            self._assert_current(candidate_revision, stage_revision)
            if self._protected_session_state() != protected_state:
                _fail(
                    "只读工具越权改变了候选或阶段状态",
                    "native_read_authority_changed",
                )
        except Exception:  # noqa: BLE001 - post-call trust boundary
            self._mark_unknown_and_terminate(
                identity,
                terminal_reason="read_authority_changed",
                message="只读工具执行期间权威候选、阶段、数据库或栅栏发生变化。",
                reason="native_read_authority_changed",
            )

    def request_cancel(self) -> dict:
        """Request cooperative cancellation without waiting for the session lock."""
        self._cancel_event.set()
        return {"disposition": "requested", "reason": "native_read_cancelled"}

    def _record_resource_reference(self, reference: dict) -> dict:
        current = resolve_registered_resource_reference(
            self.session,
            reference,
            resource_scope=self._resource_scope,
        )
        digest = fingerprint(current)
        self._issued_resource_refs[digest] = copy.deepcopy(current)
        self._issued_resource_refs_by_id[current["id"]] = copy.deepcopy(current)
        return current

    def issue_resource_reference(self, resource_id: str) -> dict:
        """Issue an opaque-scope reference for an App-selected session resource."""
        with self._lock:
            self._assert_host_active()
            if self._resource_ref_count >= MAX_TURN_RESOURCE_REFS:
                _fail(
                    "本轮原生资料引用已达到上限",
                    "native_turn_resource_limit",
                )
            self._assert_current(
                self.snapshot["candidate_revision"],
                self.snapshot["stage_revision"],
            )
            reference = issue_registered_resource_reference(
                self.session,
                resource_id,
                resource_scope=self._resource_scope,
            )
            issued = self._record_resource_reference(reference)
            self._resource_ref_count += 1
            return copy.deepcopy(issued)

    def resolve_resource_reference(self, reference: Mapping) -> dict:
        """Resolve only an exact reference previously issued by this host."""
        with self._lock:
            self._assert_host_active()
            self._assert_current(
                self.snapshot["candidate_revision"],
                self.snapshot["stage_revision"],
            )
            current = resolve_registered_resource_reference(
                self.session,
                reference,
                resource_scope=self._resource_scope,
            )
            digest = fingerprint(current)
            issued = self._issued_resource_refs.get(digest)
            if issued != current:
                _fail(
                    "资料引用并非由当前host签发",
                    "native_resource_reference_unissued",
                )
            return copy.deepcopy(current)

    def _resolve_issued_resource_id(self, resource_id: str) -> dict:
        reference = self._issued_resource_refs_by_id.get(resource_id)
        if reference is None:
            _fail(
                "读取会话资料前必须使用当前host签发的引用",
                "native_resource_scope_required",
            )
        return self.resolve_resource_reference(reference)

    def read_resource_reference(
        self,
        reference: Mapping,
        *,
        step_id: str,
        call_id: str,
        pointer: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        """Validate a scoped reference, then execute its bounded page read."""
        resolved = self.resolve_resource_reference(reference)
        arguments = {
            "resource": resolved["id"],
            "pointer": pointer,
            "offset": offset,
            "limit": limit,
        }
        raw = json.dumps(
            arguments,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return self.execute(
            step_id=step_id,
            call_id=call_id,
            tool="gs_read_context",
            arguments_json=raw,
            canonical_args_hash=raw_json_hash(raw),
            candidate_revision=self.snapshot["candidate_revision"],
            stage_revision=self.snapshot["stage_revision"],
        )

    def _request_identity(
        self,
        *,
        step_id: str,
        tool: str,
        canonical_args_hash: str,
        candidate_revision: int,
        stage_revision: int,
    ) -> tuple:
        return (
            step_id,
            tool,
            canonical_args_hash,
            candidate_revision,
            stage_revision,
        )

    def execute(
        self,
        *,
        step_id: str,
        call_id: str,
        tool: str,
        arguments_json: str,
        canonical_args_hash: str,
        candidate_revision: int,
        stage_revision: int,
    ) -> dict:
        """Execute one current-snapshot read and create a bounded settlement."""
        step_id = _identity(step_id, "step_id")
        call_id = _identity(call_id, "call_id")
        tool = _identity(tool, "tool")
        claimed_hash = _digest(canonical_args_hash, "canonical_args_hash")
        if raw_json_hash(arguments_json) != claimed_hash:
            _fail("工具参数哈希不匹配", "native_read_args_hash_mismatch")
        request_identity = self._request_identity(
            step_id=step_id,
            tool=tool,
            canonical_args_hash=claimed_hash,
            candidate_revision=candidate_revision,
            stage_revision=stage_revision,
        )

        with self._lock:
            self._assert_host_active()
            if self._awaiting_ack is not None:
                if (
                    self._awaiting_ack["call_id"] == call_id
                    and self._request_cache.get(call_id) == request_identity
                    and call_id in self._result_cache
                ):
                    self._assert_current(candidate_revision, stage_revision)
                    return copy.deepcopy(self._result_cache[call_id])
                _fail(
                    "上一条工具结算尚未得到远端确认",
                    "native_read_settlement_ack_pending",
                )
            if step_id in self._used_steps:
                _fail("同一step不能执行第二个工具", "native_read_step_reused")
            if self._settled_calls >= MAX_SEQUENTIAL_READ_CALLS:
                _fail("本轮原生只读工具调用达到上限", "native_read_call_limit")
            disclosed = {row["name"] for row in self.snapshot["tools"]}
            if tool not in disclosed or tool not in tools.NATIVE_V2_READ_POLICIES:
                _fail("当前原生运行时未披露该只读工具", "native_read_tool_forbidden")
            arguments = decode_json_object(arguments_json)
            self._assert_current(candidate_revision, stage_revision)
            # Public preflight improves diagnostics.  It is not an authority:
            # call_tool repeats the same gate immediately before dispatch.
            arguments = tools.validate_tool_arguments_for_session(
                self.session, tool, arguments
            )
            if (
                tool == "gs_read_context"
                and isinstance(arguments.get("resource"), str)
                and arguments["resource"] in getattr(self.session, "resources", {})
            ):
                self._resolve_issued_resource_id(arguments["resource"])
            protected_state = self._protected_session_state()
            activate_context(
                self.ledger,
                turn_id=self.binding["turn_id"],
                step_id=step_id,
                epoch_id=self.binding["epoch_id"],
                runtime_generation=self.binding["runtime_generation"],
                db_revision=self.binding["database_fingerprint"],
                manifest_hash=self.snapshot["tool_manifest_hash"],
                fence_token=self.binding["fence_token"],
            )
            identity = make_call_identity(
                conversation_id=self.binding["conversation_id"],
                turn_id=self.binding["turn_id"],
                step_id=step_id,
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

            response = None
            execution_failed = False
            try:
                response = tools.call_tool(self.session, tool, arguments)
            except Exception:  # noqa: BLE001 - contained after authority recheck
                execution_failed = True
            self._assert_post_call_authority(
                identity,
                candidate_revision=candidate_revision,
                stage_revision=stage_revision,
                protected_state=protected_state,
            )
            if self._cancel_event.is_set():
                self._mark_unknown_and_terminate(
                    identity,
                    terminal_reason="read_cancelled_after_execution",
                    message="原生只读工具已取消，结果不会交付。",
                    reason="native_read_cancelled",
                )

            if execution_failed:
                response = {
                    "code": 1,
                    "reason": "native_read_internal_failure",
                    "message": "App只读工具执行失败。",
                }
            try:
                receipt = build_native_tool_receipt(
                    self.session,
                    tool=tool,
                    arguments=arguments,
                    response=response,
                    policy=tools.NATIVE_V2_READ_POLICIES[tool],
                    resource_scope=self._resource_scope,
                    direct_budget=max(
                        0, MAX_TURN_DIRECT_BYTES - self._direct_result_bytes
                    ),
                )
            except Exception:  # noqa: BLE001 - native trust-boundary containment
                receipt = build_native_tool_receipt(
                    self.session,
                    tool=tool,
                    arguments=arguments,
                    response={
                        "code": 1,
                        "reason": "native_read_internal_failure",
                        "message": "App只读工具执行失败。",
                    },
                    policy=tools.NATIVE_V2_READ_POLICIES[tool],
                    resource_scope=self._resource_scope,
                    direct_budget=max(
                        0, MAX_TURN_DIRECT_BYTES - self._direct_result_bytes
                    ),
                )
            if (
                self._resource_ref_count + len(receipt.resource_ids)
                > MAX_TURN_RESOURCE_REFS
            ):
                receipt = rejected_native_tool_receipt(
                    tool=tool,
                    reason="native_turn_resource_limit",
                    message="本轮原生资料引用已达到上限。",
                )
            payload = self._settlement_payload(tool, receipt)
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
                _fail("本地工具结算哈希不一致", "native_read_settlement_mismatch")
            self._result_cache[call_id] = copy.deepcopy(payload)
            self._request_cache[call_id] = request_identity
            receipt_metadata = receipt.result.get("native_receipt", {})
            if receipt_metadata.get("mode") == "direct":
                self._direct_result_bytes += len(
                    canonical_receipt_bytes(receipt.result)
                )
            self._resource_ref_count += len(receipt.resource_ids)
            for reference in receipt.issued_references:
                self._record_resource_reference(reference)
            self._used_steps.add(step_id)
            self._settled_calls += 1
            self._awaiting_ack = {
                "call_id": call_id,
                "step_id": step_id,
                "result_hash": payload["result_hash"],
            }
            return copy.deepcopy(payload)

    def _settlement_payload(self, tool: str, receipt: NativeToolReceipt) -> dict:
        encoded = canonical_receipt_bytes(receipt.result)
        code = receipt.result.get("code")
        if type(code) is int and code == 0:
            status = "settled_success"
            result_code: str | int = 0
        else:
            status = "settled_failure"
            reason = receipt.result.get("reason")
            if isinstance(reason, str) and _IDENTITY.fullmatch(reason):
                result_code = reason
            elif (
                type(code) is int
                and -JSON_SAFE_INTEGER_MAX <= code <= JSON_SAFE_INTEGER_MAX
                and code != 0
            ):
                result_code = code
            else:  # build_native_tool_receipt should already normalize this.
                _fail(
                    "原生只读工具失败结果缺少可绑定的code/reason",
                    "native_read_settlement_invalid",
                )
        return {
            "contract": SETTLEMENT_CONTRACT,
            "tool": tool,
            "status": status,
            "result_json": encoded.decode("utf-8"),
            "result_hash": hashlib.sha256(encoded).hexdigest(),
            "result_code": result_code,
            "resource_ids": list(receipt.resource_ids),
            "runtime_generation": self.binding["runtime_generation"],
            "fence_token": self.binding["fence_token"],
        }

    def acknowledge_settlement(
        self,
        *,
        step_id: str,
        call_id: str,
        result_hash: str,
    ) -> dict:
        """Release the sequential gate only for the exact admitted settlement."""
        step_id = _identity(step_id, "step_id")
        call_id = _identity(call_id, "call_id")
        result_hash = _digest(result_hash, "result_hash")
        with self._lock:
            self._assert_host_active()
            self._assert_current(
                self.snapshot["candidate_revision"],
                self.snapshot["stage_revision"],
            )
            pending = self._awaiting_ack
            if pending is None:
                _fail("当前没有等待确认的工具结算", "native_read_ack_unexpected")
            if pending != {
                "call_id": call_id,
                "step_id": step_id,
                "result_hash": result_hash,
            }:
                _fail("工具结算确认与当前调用不一致", "native_read_ack_mismatch")
            self._awaiting_ack = None
            return {
                "disposition": "accepted",
                "call_id": call_id,
                "step_id": step_id,
                "result_hash": result_hash,
            }

    def make_settle_request(
        self,
        *,
        settlement: Mapping,
        step_id: str,
        call_id: str,
        canonical_args_hash: str,
        request_id: str,
        event_seq: int,
        message_id: str | None = None,
    ) -> dict:
        """Bind one cached App settlement to its sequential step and frame."""
        step_id = _identity(step_id, "step_id")
        call_id = _identity(call_id, "call_id")
        args_hash = _digest(canonical_args_hash, "canonical_args_hash")
        request_id = _identity(request_id, "request_id")
        if not isinstance(settlement, Mapping):
            _fail(
                "tool.settle payload字段不合法",
                "native_tool_settlement_invalid",
            )
        claimed_tool = settlement.get("tool")
        if (
            not isinstance(claimed_tool, str)
            or claimed_tool not in tools.NATIVE_V2_READ_POLICIES
            or claimed_tool not in {row["name"] for row in self.snapshot["tools"]}
        ):
            _fail(
                "tool.settle工具未由当前快照披露",
                "native_tool_settlement_invalid",
            )

        with self._lock:
            self._assert_host_active()
            payload = validate_settlement_payload(
                settlement,
                expected_tool=claimed_tool,
            )
            if (
                payload["runtime_generation"] != self.binding["runtime_generation"]
                or payload["fence_token"] != self.binding["fence_token"]
            ):
                _fail(
                    "工具结算的runtime/fence绑定不匹配",
                    "stale_tool_settlement",
                )
            self._assert_current(
                self.snapshot["candidate_revision"],
                self.snapshot["stage_revision"],
            )
            request_identity = self._request_identity(
                step_id=step_id,
                tool=claimed_tool,
                canonical_args_hash=args_hash,
                candidate_revision=self.snapshot["candidate_revision"],
                stage_revision=self.snapshot["stage_revision"],
            )
            if self._request_cache.get(call_id) != request_identity:
                _fail(
                    "tool.settle未绑定到该step/call的原始请求",
                    "native_read_settlement_mismatch",
                )
            identity = make_call_identity(
                conversation_id=self.binding["conversation_id"],
                turn_id=self.binding["turn_id"],
                step_id=step_id,
                epoch_id=self.binding["epoch_id"],
                call_id=call_id,
                tool=claimed_tool,
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
                    "tool.settle与结算账终态不一致",
                    "native_read_settlement_mismatch",
                )
            if self._awaiting_ack != {
                "call_id": call_id,
                "step_id": step_id,
                "result_hash": payload["result_hash"],
            }:
                _fail(
                    "tool.settle不是当前等待远端确认的结算",
                    "native_read_settlement_mismatch",
                )
            return make_frame(
                kind="request",
                frame_type="tool.settle",
                message_id=message_id or str(uuid.uuid4()),
                runtime_incarnation=self.binding["runtime_incarnation"],
                conversation_id=self.binding["conversation_id"],
                turn_id=self.binding["turn_id"],
                step_id=step_id,
                epoch_id=self.binding["epoch_id"],
                event_seq=event_seq,
                payload=payload,
                request_id=request_id,
                call_id=call_id,
                candidate_revision=self.snapshot["candidate_revision"],
                stage_revision=self.snapshot["stage_revision"],
                database_fingerprint=self.binding["database_fingerprint"],
                tool_manifest_hash=self.snapshot["tool_manifest_hash"],
                canonical_args_hash=args_hash,
            )

    def diagnostic_snapshot(self) -> dict:
        with self._lock:
            return {
                "contract": "genshin-native-read-dispatch-diagnostic-v1",
                "settled_calls": self._settled_calls,
                "max_calls": MAX_SEQUENTIAL_READ_CALLS,
                "direct_result_bytes": self._direct_result_bytes,
                "max_direct_result_bytes": MAX_TURN_DIRECT_BYTES,
                "resource_ref_count": self._resource_ref_count,
                "max_resource_refs": MAX_TURN_RESOURCE_REFS,
                "used_steps": sorted(self._used_steps),
                "awaiting_ack": copy.deepcopy(self._awaiting_ack),
                "cancel_requested": self._cancel_event.is_set(),
                "terminated_reason": self._terminated_reason,
                "binding_mode": (
                    "development_static"
                    if self._development_static_binding
                    else "authoritative_live"
                ),
                "visible_tools": [row["name"] for row in self.snapshot["tools"]],
            }
