"""Authenticated local CLI facade over the *existing* App workspace owner.

No Session, candidate, approval or execution state is recreated in this service.
The descriptor is a local capability, not a model/provider credential. A CLI
disconnect never rolls back or blindly repeats an admitted edit.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import socketserver
import threading
import uuid
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError

CONTRACT = "genshin-live-candidate-cli-v1"
MAX_BYTES = 128 * 1024 * 1024


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode(raw):
    return json.loads(raw, object_pairs_hook=_object,
                      parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))


class _LoopbackCandidateHTTPServer(ThreadingHTTPServer):
    """Bind local IPC without HTTPServer's display-only hostname lookup."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


class LiveCandidateCliLink:
    def __init__(self, directory, dispatch, *, diagnostic_data_dir=None):
        self._dispatch = dispatch
        self._diagnostic_data_dir = diagnostic_data_dir
        self._instance = uuid.uuid4().hex
        self._closing = False
        self._capabilities = {}
        self._descriptors = {}
        self._requests = set()
        self._capability_lock = threading.RLock()
        self._server = _LoopbackCandidateHTTPServer(("127.0.0.1", 0), self._handler())
        # A process cannot announce that its writer retired while a CLI edit
        # or calculation is still settling against that same writer.
        self._server.daemon_threads = False
        root = Path(directory).absolute()
        root.mkdir(parents=True, exist_ok=True)
        self._directory = root
        self.descriptor = self.descriptor_for(None)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name="genshin-live-cli", daemon=True)
        self._thread.start()

    def descriptor_for(self, session_id):
        """A task capability cannot select another Session's App candidate."""
        with self._capability_lock:
            if session_id in self._descriptors:
                return self._descriptors[session_id]
            token = secrets.token_urlsafe(48)
            path = self._directory / (uuid.uuid4().hex + ".json")
            self._publish_descriptor(path, token, session_id)
            self._capabilities[hashlib.sha256(token.encode("ascii")).digest()] = (token, session_id)
            self._descriptors[session_id] = path
            return path

    def _publish_descriptor(self, path, token, session_id):
        value = {"contract": CONTRACT, "instance": self._instance, "pid": os.getpid(),
                 "host": "127.0.0.1", "port": self._server.server_port, "token": token,
                 "session_id": session_id}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                if owner._closing:
                    self.send_error(503)
                    return
                # A browser page cannot use this endpoint with ambient access.
                # The CLI uses HTTPConnection directly, never environment proxies.
                auth = self.headers.get("Authorization", "")
                token = auth[7:] if auth.startswith("Bearer ") else ""
                with owner._capability_lock:
                    capability = owner._capabilities.get(hashlib.sha256(token.encode("utf-8")).digest())
                if (self.path != "/candidate" or self.headers.get("Origin") is not None
                        or self.headers.get("Host") != f"127.0.0.1:{owner._server.server_port}"
                        or capability is None
                        or not hmac.compare_digest(token.encode("utf-8"), capability[0].encode("ascii"))
                        or self.headers.get("Transfer-Encoding") is not None):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "-1"))
                    if not 0 < size <= MAX_BYTES:
                        raise ValueError("size")
                    self.connection.settimeout(30)  # receiving bytes, not running a model/calculation
                    raw = self.rfile.read(size)
                    if len(raw) != size:
                        raise ValueError("incomplete body")
                    request = _decode(raw)
                    if (not isinstance(request, dict) or request.get("instance") != owner._instance
                            or set(request) != {"instance", "operation", "session_id", "request"}
                            or not isinstance(request["operation"], str)
                            or not isinstance(request["session_id"], str)):
                        raise ValueError("shape")
                except (ValueError, TypeError, OSError):
                    self.send_error(400)
                    return
                if ((capability[1] is None and request["operation"] != "settings.describe")
                        or (capability[1] is not None and request["session_id"] != capability[1])):
                    self.send_error(403)
                    return
                from .native_cli_cancellation import (
                    CliRequestCancellation,
                    cli_request_scope,
                )
                control = CliRequestCancellation()
                finished = threading.Event()
                with owner._capability_lock:
                    owner._requests.add(control)
                    if owner._closing:
                        control.cancel()
                observer = threading.Thread(target=control.watch_disconnect,
                    args=(self.connection, finished), name="genshin-cli-disconnect", daemon=True)
                observer.start()
                try:
                    with cli_request_scope(control):
                        result = {"code": 0, "data": owner._dispatch(
                            request["operation"], request["session_id"], request["request"])}
                except Exception as error:  # noqa: BLE001 - final authenticated RPC boundary
                    from genshin_engine.execution_control import ExecutionCancelled
                    if isinstance(error, ExecutionCancelled):
                        from genshin_app.execution_cancellation import (
                            cancellation_response,
                        )
                        result = cancellation_response(error, operation="cli." + request["operation"])
                    elif isinstance(error, PlanError) or hasattr(error, "code"):
                        result = {"code": 1, "reason": getattr(error, "reason", getattr(error, "code", "cli_domain_failed")),
                                  "message": str(error), "details": getattr(error, "details", None)}
                    else:
                        from genshin_app.app_fault_store import fallback_fault_scope
                        from genshin_app.app_system_fault import system_failure
                        with fallback_fault_scope(owner._diagnostic_data_dir, session_id=request["session_id"]):
                            operation = request["operation"]
                            fault_operation = "cli." + operation if operation in {
                                "read", "edit", "compose", "calculate", "settings.describe", "settings.edit",
                            } else "cli.dispatch"
                            fault = system_failure(error, component="app_domain_tools", operation=fault_operation)
                        result = {"code": 1, **fault}
                finally:
                    finished.set()
                    observer.join()
                    with owner._capability_lock:
                        owner._requests.discard(control)
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
                if len(encoded) > MAX_BYTES:
                    encoded = b'{"code":1,"reason":"cli_response_capacity","operation_may_have_completed":true}'
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except OSError:
                    # The existing candidate CAS is authoritative after lost ACK.
                    pass

        return Handler

    def close(self):
        self._closing = True
        with self._capability_lock:
            active = list(self._requests)
        for control in active:
            control.cancel()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        with self._capability_lock:
            for path in self._descriptors.values():
                path.unlink(missing_ok=True)
            self._descriptors.clear()
            self._capabilities.clear()


def call_live_candidate(connection, operation, session_id, request=None):
    """One physical request. Never retry after unknown completion."""
    path = Path(connection)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
        raise PlanError("本地连接文件不存在或不合法，请读取当前会话的CLI入口", "cli_connection_invalid")
    value = _decode(path.read_bytes())
    if (not isinstance(value, dict) or value.get("contract") != CONTRACT or value.get("host") != "127.0.0.1"
            or type(value.get("port")) is not int or not 1 <= value["port"] <= 65535
            or not isinstance(value.get("token"), str) or not isinstance(value.get("instance"), str)):
        raise PlanError("本地连接文件不合法", "cli_connection_invalid")
    if session_id is None and operation.startswith("settings."):
        session_id = value.get("session_id") or ""
    if value.get("session_id") is not None and session_id != value["session_id"]:
        raise PlanError("这个CLI入口只属于原会话，不能选择其他候选", "cli_session_mismatch")
    body = json.dumps({"instance": value["instance"], "operation": operation,
                       "session_id": session_id, "request": request or {}}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(body) > MAX_BYTES:
        raise PlanError("本地请求超过128MiB传输容量", "cli_request_capacity")
    client = HTTPConnection("127.0.0.1", value["port"], timeout=None)
    try:
        client.connect()
    except OSError as error:
        client.close()
        raise PlanError("尚未连接到当前App，未执行操作。请核对入口；若DSH沙箱阻止本机通信，按DSH正常流程申请该命令的权限，不要绕过。",
                        "cli_connection_unavailable", {"execution_state": "did_not_execute",
                        "system_code": getattr(error, "winerror", None)}) from error
    try:
        client.request("POST", "/candidate", body=body,
                       headers={"Authorization": "Bearer " + value["token"], "Content-Type": "application/json"})
        response = client.getresponse()
        if response.status != 200:
            raise PlanError("当前App连接已不可用，请重新读取CLI入口", "cli_connection_rejected")
        raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise PlanError("响应超过容量；操作可能已完成，请先读取当前候选", "cli_operation_unknown")
        result = _decode(raw)
        if not isinstance(result, dict) or type(result.get("code")) is not int:
            raise PlanError("无法核对本地操作回执，请先读取当前候选", "cli_operation_unknown")
    except PlanError:
        raise
    except (OSError, ValueError) as error:
        raise PlanError("未能确认操作结果，请先读取当前候选，不要直接重复修改", "cli_operation_unknown") from error
    finally:
        client.close()
    if result.get("code") != 0:
        details = result.get("details")
        if result.get("failure_kind") == "system":
            from genshin_app.app_system_fault import validate_system_failure

            # Preserve the producer's safe diagnostic ID through the ordinary
            # CLI error envelope, without forwarding arbitrary server fields.
            details = {key: result[key] for key in (
                "code", "reason", "message", "failure_kind", "retryable",
                "diagnostic_storage", "system_fault", "recovery",
            ) if key in result}
            try:
                validate_system_failure(details)
            except (TypeError, ValueError) as error:
                raise PlanError("无法核对本地系统故障回执，请先检查当前候选", "cli_operation_unknown") from error
        raise PlanError(result.get("message", "本地候选操作失败"), result.get("reason", "cli_domain_failed"), details)
    return result["data"]


def workspace_cli_call(workspace, operation, session_id, request):
    """Thin transport adapter; candidate lock and edit/qualification stay shared."""
    from genshin_app.app_fault_store import fallback_fault_scope
    from genshin_app.input_editing import edit_candidate, edit_contract

    if operation in {"settings.describe", "settings.edit"} and type(request) is dict:
        remote = workspace._cli_settings_remote
        if not callable(remote):
            raise PlanError("当前连接尚未接入官方DSH配置服务", "cli_settings_unavailable")
        request_id = str(uuid.uuid4())
        with workspace._lock:
            if not workspace._cli_link_enabled:
                raise PlanError("本地配置入口正在关闭，未提交修改", "cli_connection_unavailable")
            workspace._cli_settings_calls.add(request_id)
        try:
            from .native_cli_cancellation import current_cli_cancellation
            cancellation = current_cli_cancellation()
            if cancellation is not None and callable(workspace._cli_settings_cancel):
                cancellation.register(lambda: workspace._cli_settings_cancel({"request_id": request_id}))
            response = remote({"request_id": request_id, "channel": "/api",
                "endpoint": "genshinProfile/configDescribe" if operation == "settings.describe" else "genshinProfile/configRequestEdit",
                "payload": {"args": {"request": request, **({"agentId": session_id} if operation == "settings.edit" else {})}}})
        finally:
            with workspace._lock:
                workspace._cli_settings_calls.discard(request_id)
        if response.get("ok") is not True:
            error = response.get("error", {})
            raise PlanError(error.get("message", "官方DSH配置未接受这次修改"), error.get("code", "dsh_settings_rejected"), error.get("details"))
        return response["value"]
    if operation not in {"read", "edit", "compose", "calculate"} or type(request) is not dict:
        raise PlanError("本地候选入口仅支持read、edit、compose、calculate", "cli_request_invalid")
    target = workspace.resolve_target(session_id)
    if not hasattr(target, "tool_host"):
        raise PlanError(getattr(target, "message", "当前会话未绑定候选"), getattr(target, "code", "dsh_domain_session_unbound"))
    session = target.tool_host.session
    tool = {"read": "gs_read_context", "edit": "gs_patch_candidate",
            "compose": "gs_compose", "calculate": "gs_calculate"}[operation]
    with fallback_fault_scope(workspace.diagnostic_data_dir, session_id=session_id, operation="cli_" + operation):  # noqa: SIM117 - domain lease is deliberately scoped
        with workspace._tool_boundary(workspace.activate(session_id), tool):
            from genshin_engine.execution_control import check_execution_cancelled
            check_execution_cancelled()
            if operation == "read":
                if request:
                    from .tools import (
                        _read_context,
                        validate_tool_arguments_for_session,
                    )
                    request = validate_tool_arguments_for_session(session, "gs_read_context", request)
                    return _read_context(session, request)
                return {"input": copy.deepcopy(session.candidate), "summary": session.summary(),
                        "input_fingerprint": fingerprint(session.candidate), "edit_contract": edit_contract()}
            if operation == "edit":
                if request.get("mode") == "import":
                    raise PlanError("请由CLI读取文件快照后提交replace；App不会代CLI读取任意路径", "cli_request_invalid")
                return edit_candidate(session, request)
            # Reuse the current Session, revision CAS and fixed DB context.
            if set(request) != {"expected_revision"}:
                raise PlanError("组合或计算请求需要且仅接受expected_revision", "cli_request_invalid")
            if operation == "compose":
                from .tools import _compose, validate_tool_arguments_for_session
                request = validate_tool_arguments_for_session(session, "gs_compose", request)
                return _compose(session, request)
            from .calculation_attempt_failure import (
                CalculationOutputValidationFailure,
                permits_output_validation_failure,
            )
            from .tools import _calculate
            try:
                return _calculate(session, request)
            except CalculationOutputValidationFailure as error:
                # Keep this producer's original safe diagnostic identity, only
                # for the exact live attempt. An arbitrary .response attribute
                # never grants a failure settlement or calculation authority.
                failure = {key: copy.deepcopy(error.response[key]) for key in (
                    "code", "reason", "message", "failure_kind", "retryable",
                    "diagnostic_storage", "system_fault", "recovery", "details",
                ) if key in error.response}
                if not permits_output_validation_failure(failure,
                        diagnostic_cache=session._diagnostic_cache, require_cached_verification=True,
                        candidate_state={"revision": session.revision, "fingerprint": fingerprint(session.candidate)}):
                    raise
                raise PlanError(failure["message"], failure["reason"], failure) from error
