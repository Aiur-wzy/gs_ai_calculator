"""Map DSH tool identities to the existing App exactly-once execution owner."""
from __future__ import annotations

import copy
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genshin_app.equipment.schema import PlanError

from .native_app_call_ledger import make_app_call_identity
from .native_app_call_queue import AppCallDeferred
from .native_app_tool_host import NativeAppToolHost
from .native_dsh_mux_client import NativeDshMuxError

CALL_CONTRACT = "genshin-native-dsh-domain-call-v1"
RESULT_CONTRACT = "genshin-native-dsh-domain-result-v1"
_FIELDS = frozenset({"contract", "request_id", "session_id", "turn", "step", "call_id",
                     "call_seq", "tool", "arguments_json", "raw_arguments_hash", "catalog_hash",
                     "candidate_revision", "stage_revision", "document_id", "database_fingerprint",
                     "writer_fence", "tool_manifest_hash", "runtime_generation"})


@dataclass(frozen=True)
class DshDomainTarget:
    """Trusted references, not a second copy of Session/candidate facts."""
    dsh_session_id: str
    conversation_id: str
    tool_host: NativeAppToolHost
    execution_boundary: Callable[[], AbstractContextManager] | None = None
    model_metadata_reader: Callable[[], dict] | None = None
    tool_boundary: Callable[[str], AbstractContextManager] | None = None


@dataclass(frozen=True)
class DshDomainUnavailable:
    """An explicit, recoverable domain absence, not a fabricated binding."""
    dsh_session_id: str
    code: str
    message: str
    details: dict | None = None


class NativeDshDomainCalls:
    def __init__(self, resolve_target):
        self._resolve = resolve_target

    def _target(self, session_id):
        target = self._resolve(session_id)
        if isinstance(target, DshDomainUnavailable) and target.dsh_session_id == session_id:
            raise NativeDshMuxError(target.code, failure={"code": target.code,
                "message": target.message, "details": copy.deepcopy(target.details or {})})
        if not isinstance(target, DshDomainTarget) or target.dsh_session_id != session_id:
            raise NativeDshMuxError("dsh_domain_session_unbound")
        return target

    @staticmethod
    def _model_metadata(target):
        if target.model_metadata_reader is None:
            return {}
        value = target.model_metadata_reader()
        if type(value) is not dict or set(value) != {"runtime_context", "prompt_bundle"}:
            raise NativeDshMuxError("dsh_domain_model_context_invalid")
        return copy.deepcopy(value)

    def __call__(self, kind: str, request: dict) -> dict[str, Any]:
        if kind == "dsh_app_bind_request":
            if (type(request) is not dict or set(request) - {"task_source"} != {"contract", "request_id", "session_id"}
                    or request["contract"] != "genshin-native-dsh-domain-bind-v1"):
                raise NativeDshMuxError("dsh_domain_bind_invalid")
            target = self._resolve(request["session_id"])
            if isinstance(target, DshDomainUnavailable) and target.dsh_session_id == request["session_id"]:
                return {"contract": "genshin-native-dsh-domain-bound-v1",
                    "request_id": request["request_id"], "session_id": request["session_id"],
                    "binding_state": "unbound", "snapshot": None,
                    "binding_issue": {"code": target.code, "message": target.message,
                        "details": copy.deepcopy(target.details or {})},
                    "runtime_context": {"domain_binding": "unbound", "reason": target.code},
                    "prompt_bundle": {"system_prompt": (
                        "你是原神排轴客户端的助手，用中文交流。当前 App 领域资料尚未绑定或需要更新，"
                        "可以回答问题、核实公开资料，但不能声称已编辑白板、完成计算或保存文件。"
                        "请说明 App 给出的具体问题，并帮助用户在新会话绑定当前版本文档后继续。"),
                        "required_skill_resources": []}}
            if not isinstance(target, DshDomainTarget) or target.dsh_session_id != request["session_id"]:
                raise NativeDshMuxError("dsh_domain_session_unbound")
            binding_tool = "gs_record_finding" if request.get("task_source") is not None else "gs_read_context"
            boundary = (target.tool_boundary(binding_tool) if target.tool_boundary is not None else
                target.execution_boundary() if target.execution_boundary is not None else nullcontext())
            entered = False
            try:
                with boundary:
                    entered = True
                    from .native_task_source import capture_task_source

                    capture = capture_task_source(getattr(target.tool_host, "session", None),
                        request.get("task_source"), session_id=target.dsh_session_id)
                    metadata = self._model_metadata(target)
                    if capture is not None:
                        metadata.setdefault("runtime_context", {})["task_source_capture"] = capture
                    # GUI edits are outside the App request FIFO. Keep both
                    # halves of one published binding under the same candidate
                    # lease, not two separately fresh but divergent samples.
                    return {"contract": "genshin-native-dsh-domain-bound-v1",
                            "request_id": request["request_id"], "session_id": target.dsh_session_id,
                            "binding_state": "bound", "snapshot": target.tool_host.current_snapshot(),
                            **metadata}
            except Exception as error:
                if not entered and getattr(error, "code", None) == "app_action_serial_gate_busy":
                    # Bind has not admitted/executed any tool. The resource
                    # queue, never a DSH retry loop, waits for the file lease.
                    raise AppCallDeferred from None
                from genshin_app.app_system_fault import (
                    is_domain_exception,
                    system_failure,
                )
                if not is_domain_exception(error):
                    from genshin_app.app_fault_store import fallback_fault_scope
                    database = getattr(getattr(target.tool_host, "session", None), "db_path", None)
                    directory = getattr(target.tool_host, "diagnostic_data_dir", None)
                    with fallback_fault_scope(directory if directory is not None else
                            Path(database).parent if database is not None else None,
                            session_id=target.dsh_session_id, conversation_id=target.conversation_id,
                            request_id=request.get("request_id")):
                        fault = system_failure(error, component="app_domain_binding", operation="dsh_app_bind_request")
                    raise NativeDshMuxError(fault["reason"], failure={"code": fault["reason"],
                        "message": fault["message"], "details": fault}, outcome_unknown=False) from None
                raise
        if (kind != "dsh_app_call_request" or type(request) is not dict
                or set(request) - {"input_file_snapshot"} != _FIELDS):
            raise NativeDshMuxError("dsh_domain_call_invalid")
        if request["contract"] != CALL_CONTRACT:
            raise NativeDshMuxError("dsh_domain_call_contract")
        for key in ("turn", "step", "call_seq"):
            if type(request[key]) is not int or not 0 <= request[key] <= 2**53 - 1:
                raise NativeDshMuxError("dsh_domain_call_identity")
        target = self._target(request["session_id"])
        # The ledger keeps real DSH Turn/Step identities, not an App loop.
        # The separate writer fence remains the App's domain write lease.
        values = {
            "conversation_id": target.conversation_id,
            "turn_id": f"dsh-turn-{request['turn']}", "step_id": f"dsh-step-{request['step']}",
            "epoch_id": request["writer_fence"], "call_id": request["call_id"],
            "tool_name": request["tool"], "arguments_json": request["arguments_json"],
            "raw_arguments_hash": request["raw_arguments_hash"], "document_id": request["document_id"],
            "candidate_revision": request["candidate_revision"], "stage_revision": request["stage_revision"],
            "database_fingerprint": request["database_fingerprint"],
            "tool_manifest_hash": request["tool_manifest_hash"], "runtime_generation": request["runtime_generation"],
            "writer_fence": request["writer_fence"], "catalog_hash": request["catalog_hash"],
        }
        try:
            boundary = (target.tool_boundary(request["tool"]) if target.tool_boundary is not None else
                target.execution_boundary() if target.execution_boundary is not None else nullcontext())
            from genshin_app.native_input_snapshot import native_input_snapshot_scope
            with boundary, native_input_snapshot_scope(request.get("input_file_snapshot")):
                settled = target.tool_host.execute(**values)
        except PlanError as error:
            # No transport wrapper may label an unproven operation settled.
            unknown = True
            try:
                identity = make_app_call_identity(**{key: value for key, value in values.items()
                    if key != "arguments_json"})
                prior = target.tool_host.ledger.lookup(identity=identity)
                unknown = prior is not None and prior["entry"]["state"] in {
                    "inflight", "unknown_after_interruption"}
            except PlanError:
                pass
            raise NativeDshMuxError(error.reason, failure={"code": error.reason,
                "message": str(error), "details": error.details or {}}, outcome_unknown=unknown) from None
        terminal = settled.get("terminal_state")
        if terminal not in {"settled_success", "settled_failure", "unknown_after_interruption"}:
            raise NativeDshMuxError("dsh_domain_call_not_settled", outcome_unknown=True)
        result = settled.get("result")
        if result is None:
            if terminal != "unknown_after_interruption":
                raise NativeDshMuxError("dsh_domain_settlement_result_missing")
            result = {"code": 1, "reason": "native_app_tool_outcome_unknown",
                      "message": "这次操作的结果尚未核实，不能自动重做。"}
        response = {"contract": RESULT_CONTRACT, "request_id": request["request_id"],
                "session_id": request["session_id"], "call_id": request["call_id"],
                "call_seq": request["call_seq"], "status": terminal,
                "result": copy.deepcopy(result), "next_snapshot": copy.deepcopy(settled.get("next_snapshot"))}
        try:
            response.update(self._model_metadata(target))
        except Exception:  # noqa: BLE001 - the operation is already durably settled
            response["metadata_issue"] = {"code": "dsh_domain_model_context_unavailable",
                "message": "操作已结算，但最新工作说明暂不可用，请重新读取当前资料。"}
        return response
