"""Version-pinned, app-owned Codex stdio transport.

The exact native binary was tested against a local fake Responses endpoint:
empty environments remove host tools; remaining skill package access is empty.
Dynamic tools are additive, so other binaries remain unsupported.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import queue
import re
import subprocess
import threading
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

SUPPORTED_VERSION = "0.147.0"
SUPPORTED_SHA256 = "935a1911ed2556e4ffcec995f4886ac2ac425863ba26fed264df62e30272ad9d"
BUILTIN_ISOLATION_VERIFIED = True
ISOLATION_BLOCK = "builtin_tool_isolation_unverified"
MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_MODELS = 500
RPC_TIMEOUT = 20.0
DISABLED_FEATURES = (
    "apps", "plugins", "remote_plugin", "hooks", "shell_tool", "shell_snapshot",
    "unified_exec", "multi_agent", "computer_use", "browser_use", "in_app_browser",
    "view_image", "image_generation", "skill_search", "skill_mcp_dependency_install",
    "workspace_dependencies", "code_mode_host", "tool_suggest", "goals",
    "code_mode", "code_mode_only", "code_mode_buffered_exec", "artifact",
    "standalone_web_search", "executor_capability_discovery", "multi_agent_v2",
    "deferred_executor", "request_permissions_tool", "enable_mcp_apps", "memories",
    "auth_elicitation", "tool_call_mcp_elicitation", "browser_use_external",
    "browser_use_full_cdp_access", "realtime_conversation", "external_agent_memory_import",
    "chronicle", "network_proxy",
)
CONFIG_OVERRIDES = (
    'apps._default.enabled=false', 'web_search="disabled"', 'approval_policy="never"',
    'sandbox_mode="read-only"', 'analytics.enabled=false', 'feedback.enabled=false',
    'model_provider="openai"', 'forced_login_method="chatgpt"', 'notify=[]',
    # Use the user's Windows network route for model HTTPS/WebSocket traffic;
    # this does not enable network or host tools in the empty execution environment.
    'features.respect_system_proxy=true',
)
READ_METHODS = frozenset({
    "model/list", "account/read", "account/rateLimits/read", "config/read",
    "mcpServerStatus/list", "thread/read",
})


class TransportError(RuntimeError):
    """Sanitized failure suitable for the GUI; never include raw CLI stderr."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _native_executable(path: Path) -> bool:
    try:
        if path.suffix.lower() != ".exe" or not path.is_file():
            return False
        with path.open("rb") as stream:
            return stream.read(2) == b"MZ"
    except OSError:
        return False


def clean_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    """Remove provider/secret overrides only in our child environment."""
    source = os.environ if env is None else env
    blocked_prefixes = ("OPENAI_", "ANTHROPIC_", "AZURE_OPENAI_", "AWS_", "BEDROCK_",
                        "GOOGLE_API_", "GEMINI_", "DEEPSEEK_", "CODEX_", "CHATGPT_")
    blocked_keys = {"CODEX_HOME", "CODEX_CONFIG", "CODEX_CONFIG_TOML", "CODEX_MODEL",
                    "CODEX_MODEL_PROVIDER", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                    "NODE_OPTIONS", "PYTHONSTARTUP", "SSLKEYLOGFILE"}
    return {key: value for key, value in source.items()
            if key.upper() not in blocked_keys
            and not key.upper().startswith(blocked_prefixes)}


def detect_cli() -> dict:
    """Discover all registered local CLIs; execution trust stays in transport."""
    from .discovery import discover_agents

    return discover_agents(codex_version=SUPPORTED_VERSION, codex_sha256=SUPPORTED_SHA256,
                           digest_file=_digest)


def _quota_summary(raw: dict) -> dict:
    """Expose ordinary quota as read-only account information.

    The App does not reserve a percentage or decide whether a model turn may
    start.  Provider enforcement remains authoritative, while credit balances
    stay private.
    """
    buckets = raw.get("rateLimitsByLimitId")
    if not buckets:
        legacy = raw.get("rateLimits")
        buckets = {"codex": legacy} if isinstance(legacy, dict) else {}
    if not isinstance(buckets, dict):
        return {"known": False, "remaining_percent": None, "windows": [],
                "exhausted": False, "can_start": True, "managed_by_user": True}
    bucket = buckets.get("codex")
    if not isinstance(bucket, dict):
        return {"known": False, "remaining_percent": None, "windows": [],
                "exhausted": False, "can_start": True, "managed_by_user": True}
    windows = []
    for key in ("primary", "secondary"):
        window = bucket.get(key)
        # A null secondary means the account has no advertised second window.
        if window is None and key == "secondary":
            continue
        if not isinstance(window, dict):
            return {"known": False, "remaining_percent": None, "windows": [],
                    "exhausted": False, "can_start": True, "managed_by_user": True}
        value = window.get("usedPercent")
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0 <= value <= 100):
            return {"known": False, "remaining_percent": None, "windows": [],
                    "exhausted": False, "can_start": True, "managed_by_user": True}
        windows.append({"kind": key, "usedPercent": value,
                        "remainingPercent": 100 - value,
                        "resetsAt": window.get("resetsAt"),
                        "windowDurationMins": window.get("windowDurationMins")})
    remaining = min((item["remainingPercent"] for item in windows), default=None)
    exhausted = bool(bucket.get("spendControlReached") or bucket.get("rateLimitReachedType"))
    return {"known": remaining is not None, "remaining_percent": remaining,
            "windows": windows, "exhausted": exhausted,
            "can_start": True, "managed_by_user": True,
            "billing_atomic_guarantee": False}


class _OwnedWindowsProcess:
    """Native process suspended until bound to a kill-on-close Job Object."""

    def __init__(self, args: list[str], cwd: Path, env: dict[str, str]):
        if os.name != "nt":
            raise TransportError("platform_unsupported", "首版受控连接仅支持 Windows。")
        import _winapi
        import msvcrt

        class BasicLimit(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(key, ctypes.c_ulonglong) for key in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class ExtendedLimit(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimit), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                    ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._kernel, self._winapi = kernel, _winapi
        self._close_lock = threading.Lock()
        self._job = kernel.CreateJobObjectW(None, None)
        self._handle = None
        self.stdin = self.stdout = self.stderr = None
        handles: list[int] = []
        hp = ht = None
        try:
            if not self._job:
                raise OSError("job_create")
            limits = ExtendedLimit()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE, no breakaway
            if not kernel.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise OSError("job_configure")
            in_read, in_write = _winapi.CreatePipe(None, 0)
            handles += [in_read, in_write]
            out_read, out_write = _winapi.CreatePipe(None, 0)
            handles += [out_read, out_write]
            err_read, err_write = _winapi.CreatePipe(None, 0)
            handles += [err_read, err_write]
            child_handles = [in_read, out_write, err_write]
            for handle in child_handles:
                os.set_handle_inheritable(handle, True)
            startup = subprocess.STARTUPINFO()
            startup.dwFlags = subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = subprocess.SW_HIDE
            startup.hStdInput, startup.hStdOutput, startup.hStdError = child_handles
            startup.lpAttributeList = {"handle_list": child_handles}
            hp, ht, self.pid, _ = _winapi.CreateProcess(
                args[0], subprocess.list2cmdline(args), None, None, True,
                subprocess.CREATE_NO_WINDOW | 0x00000004,  # CREATE_SUSPENDED
                env, str(cwd), startup)
            self._handle = hp
            if not kernel.AssignProcessToJobObject(self._job, hp):
                raise OSError("job_assign")
            if kernel.ResumeThread(ht) == 0xFFFFFFFF:
                raise OSError("thread_resume")
            for handle in child_handles:
                _winapi.CloseHandle(handle)
                handles.remove(handle)
            for name, handle, flag, mode in (("stdin", in_write, os.O_WRONLY, "wb"),
                                              ("stdout", out_read, os.O_RDONLY, "rb"),
                                              ("stderr", err_read, os.O_RDONLY, "rb")):
                fd = msvcrt.open_osfhandle(handle, flag | os.O_BINARY)
                handles.remove(handle)
                setattr(self, name, os.fdopen(fd, mode, buffering=0))
        except Exception:  # noqa: BLE001 - ensure a failed native setup cannot leave a suspended child
            if hp is not None:
                _winapi.TerminateProcess(hp, 1)
            self.close()
            raise TransportError("process_isolation_failed", "无法隔离 Agent 子进程，连接已取消。") from None
        finally:
            if ht is not None:
                _winapi.CloseHandle(ht)
            for handle in handles:
                _winapi.CloseHandle(handle)

    def poll(self):
        if self._handle is None:
            return 0
        if self._winapi.WaitForSingleObject(self._handle, 0) == 0x102:
            return None
        return self._winapi.GetExitCodeProcess(self._handle)

    def wait(self, timeout=3):
        if self._handle is not None:
            milliseconds = self._winapi.INFINITE if timeout is None else int(timeout * 1000)
            result = self._winapi.WaitForSingleObject(self._handle, milliseconds)
            if result == 0x102:
                raise TimeoutError
        return self.poll()

    def terminate_tree(self):
        """End owned descendants while retaining the parent handle for wait()."""
        with self._close_lock:
            if self._job:
                self._kernel.CloseHandle(self._job)
                self._job = None

    def close(self):
        self.terminate_tree()
        with self._close_lock:
            if self._handle is not None:
                self._winapi.CloseHandle(self._handle)
                self._handle = None
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream:
                    try:
                        stream.close()
                    except OSError:
                        pass


class CodexTransport:
    """One owned stdio process; callbacks must only enqueue and return quickly."""

    def __init__(self, cli_dict: dict, cwd: str | Path, on_event: Callable[[dict], None]):
        self.cli = dict(cli_dict)
        self.cwd = Path(cwd).resolve()
        self.on_event = on_event
        self._process = None
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._pending: dict[int, queue.Queue] = {}
        self._requests: set[int | str] = set()
        self._seq = 0
        self._closed = False
        self._failure: TransportError | None = None
        self._threads: dict[str, dict] = {}
        self._mcp_names: list[str] = []
        self.models: list[dict] = []
        self.account: dict = {}
        self.limits: dict = {}
        self.state = "disconnected"

    def _emit(self, event: dict):
        try:
            self.on_event(event)
        except Exception:  # noqa: BLE001 - callback failures must terminate owned execution
            # A broken callback cannot allow orphaned execution to continue.
            self._fail("event_delivery_failed", "Agent 事件接收失败，连接已关闭。")

    def _fail(self, code: str, message: str):
        with self._lock:
            if self._failure is not None:
                return
            self._failure = TransportError(code, message)
            self.state = "failed"
            pending = list(self._pending.values())
        for target in pending:
            target.put({"_transport_error": self._failure})
        try:
            self.on_event({"_transport_error": code})
        except Exception:  # noqa: BLE001, S110 - best effort only; failure is already terminal
            pass
        if self._process:
            self._process.close()

    def _reader(self):
        import io

        stream = io.BufferedReader(self._process.stdout)
        try:
            while not self._closed:
                line = stream.readline(MAX_LINE_BYTES + 1)
                if not line:
                    if not self._closed:
                        self._fail("process_exited", "Agent 连接已断开。")
                    break
                if len(line) > MAX_LINE_BYTES or not line.endswith(b"\n"):
                    self._fail("protocol_size", "Agent 返回超出协议大小限制。")
                    break
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        raise TypeError
                except (ValueError, TypeError, UnicodeError):
                    self._fail("protocol_invalid", "Agent 返回无效协议数据。")
                    break
                if "method" in message:
                    if "id" in message:
                        request_id = message["id"]
                        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
                            self._fail("protocol_invalid", "Agent 请求标识无效。")
                            break
                        with self._lock:
                            if request_id in self._requests or len(self._requests) >= 256:
                                self._fail("request_repeated", "Agent 工具请求重复或过多，连接已停止。")
                                break
                            self._requests.add(request_id)
                        if message["method"] == "item/tool/requestUserInput":
                            self.reject_request(request_id)
                            self._emit({"method": "transport/requestRejected",
                                        "params": {"reason": "use_plain_text_question"}})
                            continue
                        if message["method"] != "item/tool/call":
                            self.reject_request(request_id)
                            self._fail("unexpected_server_request", "Agent 请求了未开放的执行或审批，已停止连接。")
                            break
                    if (message["method"] == "account/updated"
                            and (message.get("params") or {}).get("authMode") != "chatgpt"):
                        self._fail("auth_changed", "Agent 登录类型已变化，已停止连接。")
                        break
                    self._emit(message)
                elif "id" in message:
                    if isinstance(message["id"], bool) or not isinstance(message["id"], (str, int)):
                        self._fail("protocol_invalid", "Agent 响应标识无效。")
                        break
                    with self._lock:
                        target = self._pending.get(message["id"])
                    if target is not None:
                        target.put(message)
                else:
                    self._fail("protocol_invalid", "Agent 返回缺少消息类型。")
                    break
        except (OSError, ValueError, TypeError):
            if not self._closed:
                self._fail("protocol_io", "Agent 连接读取失败。")

    def _stderr_reader(self):
        # Drain bounded chunks; never retain stderr which may contain secrets.
        try:
            while not self._closed and self._process.stderr.read(4096):
                pass
        except (OSError, ValueError):
            pass

    def _write(self, message: dict):
        payload = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode()
        if len(payload) > MAX_LINE_BYTES:
            raise TransportError("request_size", "Agent 请求超出大小限制。")
        done = threading.Event()
        errors = []

        def write_bytes():
            try:
                self._write_bytes(payload)
            except TransportError as exc:
                errors.append(exc)
            finally:
                done.set()

        # Pipe writes can block before an RPC response timeout starts. A bounded
        # writer lets failure close the Job, which breaks a stalled child's pipe.
        threading.Thread(target=write_bytes, daemon=True, name="genshin-agent-write").start()
        if not done.wait(RPC_TIMEOUT):
            self._fail("write_timeout", "Agent 连接写入超时，已停止自有进程。")
            raise self._failure
        if errors:
            raise errors[0]

    def _write_bytes(self, payload: bytes):
        with self._write_lock:
            if self._closed or self._failure or not self._process:
                raise self._failure or TransportError("not_connected", "Agent 尚未连接。")
            try:
                view = memoryview(payload)
                while view:
                    written = self._process.stdin.write(view)
                    if not written:
                        raise OSError("short_write")
                    view = view[written:]
                self._process.stdin.flush()
            except (OSError, ValueError):
                self._fail("protocol_io", "Agent 连接写入失败。")
                raise self._failure from None

    def _rpc(self, method: str, params: dict, timeout=RPC_TIMEOUT):
        with self._lock:
            self._seq += 1
            request_id = self._seq
            target: queue.Queue = queue.Queue()
            self._pending[request_id] = target
        try:
            self._write({"id": request_id, "method": method, "params": params})
            try:
                response = target.get(timeout=timeout)
            except queue.Empty:
                self._fail("request_timeout", "Agent 响应超时，连接已关闭。")
                raise self._failure from None
            if "_transport_error" in response:
                raise response["_transport_error"]
            if "error" in response:
                # Deliberately discard potentially sensitive raw error text/data.
                raise TransportError("rpc_rejected", "Agent 拒绝了协议请求。")
            result = response.get("result")
            if not isinstance(result, dict):
                self._fail("protocol_result", "Agent 返回格式不符合协议。")
                raise self._failure
            return result
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def request(self, method: str, params: dict):
        if method not in READ_METHODS:
            raise TransportError("method_forbidden", "该 Agent 接口未开放。")
        return self._rpc(method, params)

    def _read_account_limits(self):
        raw = self._rpc("account/read", {"refreshToken": False})
        account = raw.get("account") or {}
        if account.get("type") != "chatgpt":
            raise TransportError("auth_unsupported", "只支持现有 ChatGPT 订阅登录，请在 Codex 中完成登录。")
        self.account = {"type": "chatgpt", "plan_type": account.get("planType")}
        try:
            quota = self._rpc("account/rateLimits/read", {})
        except TransportError:
            # Quota is an optional read-only display.  Failure to read it must
            # not turn unknown account state into an App-side inference gate.
            quota = {}
        self.limits = _quota_summary(quota)

    def connect(self) -> dict:
        if self._process or self._closed:
            raise TransportError("connection_state", "此 Agent 连接已经使用，请重新建立连接。")
        path = Path(self.cli.get("path", ""))
        if (self.cli.get("version") != SUPPORTED_VERSION or not _native_executable(path)
                or self.cli.get("sha256") != SUPPORTED_SHA256
                or _digest(path) != self.cli["sha256"]):
            raise TransportError("cli_changed", "Codex 版本或文件已变化，请重新检测。")
        if not self.cwd.is_dir():
            raise TransportError("session_directory", "Agent 会话目录不存在。")
        args = [str(path), "app-server", "--stdio"]
        for feature in DISABLED_FEATURES:
            args += ["--disable", feature]
        for value in CONFIG_OVERRIDES:
            args += ["-c", value]
        # close() can run while hashing/validating the executable. Publication
        # must be atomic with the final closed check, or a cancelled connection
        # could create a process after its idempotent close already returned.
        with self._lock:
            if self._closed:
                raise TransportError("connection_closed", "Agent连接已取消。")
            self._process = _OwnedWindowsProcess(args, self.cwd, clean_environment())
        threading.Thread(target=self._reader, daemon=True, name="genshin-agent-stdio").start()
        threading.Thread(target=self._stderr_reader, daemon=True, name="genshin-agent-stderr").start()
        try:
            self._rpc("initialize", {"clientInfo": {"name": "genshin_app", "version": "1"},
                                     "capabilities": {"experimentalApi": True}})
            self._write({"method": "initialized"})
            raw = self._rpc("config/read", {"includeLayers": False, "cwd": str(self.cwd)})
            config = raw.get("config")
            if not isinstance(config, dict):
                raise TransportError("config_unknown", "无法核验 Codex 隔离配置。")
            # Legacy notify independently spawns an external command after a
            # completed turn; features.hooks=false does not disable it.
            if config.get("notify") != []:
                raise TransportError("notify_not_isolated", "无法确认外部通知命令已禁用，连接已停止。")
            servers = config.get("mcp_servers") or {}
            if not isinstance(servers, dict) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", name) for name in servers):
                raise TransportError("mcp_name_unsupported", "当前 MCP 配置无法安全隔离。")
            self._mcp_names = sorted(servers)
            if config.get("model_provider") != "openai" or config.get("forced_login_method") != "chatgpt":
                raise TransportError("provider_unknown", "无法确认 ChatGPT 提供方约束。")
            providers = config.get("model_providers")
            if not isinstance(providers, dict) or providers.get("openai"):
                raise TransportError("provider_override", "检测到不支持的 OpenAI 提供方覆盖，连接已停止。")
            for field in ("chatgpt_base_url", "chatgpt_auth_base_url"):
                if config.get(field):
                    raise TransportError("provider_override", "检测到自定义 ChatGPT 端点，连接已停止。")
            del raw, config, servers
            self._read_account_limits()
            cursor = None
            seen = set()
            while True:
                page = self._rpc("model/list", {"limit": 100, "includeHidden": False, **({"cursor": cursor} if cursor else {})})
                for model in page.get("data", []):
                    if not isinstance(model, dict) or not isinstance(model.get("id"), str):
                        raise TransportError("model_catalog", "模型目录格式未知。")
                    self.models.append({key: model.get(key) for key in (
                        "id", "model", "displayName", "defaultReasoningEffort", "supportedReasoningEfforts")})
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen or len(self.models) > MAX_MODELS:
                    raise TransportError("model_catalog", "模型目录分页异常。")
                seen.add(cursor)
            self.state = "ready" if BUILTIN_ISOLATION_VERIFIED else "blocked"
            return {"models": self.models, "account": self.account, "limits": self.limits,
                    "cli": self.cli, "state": self.state,
                    "blocking_reason": None if BUILTIN_ISOLATION_VERIFIED else ISOLATION_BLOCK}
        except Exception:
            self.close()
            raise

    def _model(self, selected: str, effort: str | None = None):
        model = next((item for item in self.models if selected in (item.get("id"), item.get("model"))), None)
        if model is None:
            raise TransportError("model_unavailable", "所选模型不在当前可用目录中。")
        efforts = {item.get("reasoningEffort") for item in model.get("supportedReasoningEfforts") or []
                   if isinstance(item, dict)}
        if effort is not None and (effort not in efforts or effort == "ultra"):
            raise TransportError("effort_unsupported", "此推理档位尚不支持受控模式。")
        return model

    def start_thread(self, dynamic_tools: list[dict], instructions: str, model: str) -> str:
        self._model(model)
        params = {"cwd": str(self.cwd), "environments": [], "runtimeWorkspaceRoots": [],
                  "selectedCapabilityRoots": [],
                  "ephemeral": True, "approvalPolicy": "never", "approvalsReviewer": "user",
                  "sandbox": "read-only", "modelProvider": "openai", "model": model,
                  "allowProviderModelFallback": False, "developerInstructions": instructions,
                  "dynamicTools": dynamic_tools,
                  "config": {"mcp_servers." + name + ".enabled": False for name in self._mcp_names}}
        try:
            result = self._rpc("thread/start", params)
            thread = result.get("thread") or {}
            tid = thread.get("id")
            sandbox = result.get("sandbox") or {}
            if (not isinstance(tid, str) or thread.get("ephemeral") is not True
                    or thread.get("path") is not None or result.get("runtimeWorkspaceRoots") != []
                    or sandbox.get("type") != "readOnly" or sandbox.get("networkAccess") is not False
                    or result.get("approvalPolicy") != "never" or result.get("modelProvider") != "openai"
                    or result.get("model") != model):
                raise TransportError("thread_isolation", "Agent 线程隔离核验失败。")
            cursor = None
            seen = set()
            while True:
                page = self._rpc("mcpServerStatus/list", {"threadId": tid, "detail": "toolsAndAuthOnly", "limit": 100,
                                                          **({"cursor": cursor} if cursor else {})})
                for server in page.get("data", []):
                    if server.get("tools") or server.get("name") not in self._mcp_names:
                        raise TransportError("mcp_not_isolated", "检测到额外 MCP 能力，已停止连接。")
                cursor = page.get("nextCursor")
                if not cursor:
                    break
                if cursor in seen or len(seen) > 100:
                    raise TransportError("mcp_unknown", "无法完成 MCP 隔离核验。")
                seen.add(cursor)
            self._threads[tid] = {"model": model}
            return tid
        except Exception:
            self.close()
            raise

    def start_turn(self, thread_id: str, message: str, model: str, effort: str) -> str:
        if not BUILTIN_ISOLATION_VERIFIED:
            raise TransportError(ISOLATION_BLOCK, "尚未证实内置工具完全隔离，已禁止启动真实推理。")
        if thread_id not in self._threads:
            raise TransportError("thread_unknown", "Agent 会话未通过隔离检查。")
        self._model(model, effort)
        result = self._rpc("turn/start", {"threadId": thread_id, "environments": [],
                                          "runtimeWorkspaceRoots": [], "approvalPolicy": "never",
                                          "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                                          "model": model, "effort": effort,
                                          "input": [{"type": "text", "text": message, "text_elements": []}]})
        turn_id = (result.get("turn") or {}).get("id")
        if not isinstance(turn_id, str):
            self.close()
            raise TransportError("turn_invalid", "Agent 未返回有效轮次。")
        return turn_id

    def reply_tool(self, request_id, toolresult: dict):
        if "success" in toolresult and "contentItems" in toolresult:
            result = toolresult
        else:
            result = {"success": not bool(toolresult.get("code")), "contentItems": [
                {"type": "inputText", "text": json.dumps(toolresult, ensure_ascii=False, allow_nan=False)}]}
        with self._lock:
            if request_id not in self._requests:
                raise TransportError("request_unknown", "工具请求已失效。")
            self._requests.remove(request_id)
        self._write({"id": request_id, "result": result})

    def reject_request(self, request_id):
        with self._lock:
            self._requests.discard(request_id)
        self._write({"id": request_id, "error": {"code": -32601,
                     "message": "This interaction is unavailable. Ask the user in ordinary text when input is needed."}})

    def interrupt(self, thread_id: str, turn_id: str):
        if thread_id not in self._threads:
            raise TransportError("thread_unknown", "Agent 会话已失效。")
        return self._rpc("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self.state = "closed"
            targets = list(self._pending.values())
        for target in targets:
            target.put({"_transport_error": TransportError("connection_closed", "Agent 连接已关闭。")})
        if self._process:
            try:
                self._process.stdin.close()
                self._process.wait(timeout=2)
            except (OSError, ValueError, TimeoutError):
                pass
            finally:
                self._process.close()
