"""Boundary and protocol tests; no real model turns or credential access."""
from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading

import pytest

from genshin_app.agent_chat import transport as module
from genshin_app.agent_chat.transport import (
    CodexTransport,
    TransportError,
    _OwnedWindowsProcess,
    _quota_summary,
    clean_environment,
)


def transport(tmp_path, events=None):
    return CodexTransport({}, tmp_path, (events if events is not None else []).append)


def model():
    return {"id": "gpt-test", "model": "gpt-test", "supportedReasoningEfforts": [
        {"reasoningEffort": "high"}, {"reasoningEffort": "ultra"}]}


def quota(used=40, secondary=None):
    return {"rateLimitsByLimitId": {"codex": {
        "primary": {"usedPercent": used, "windowDurationMins": 10080, "resetsAt": 100},
        "secondary": secondary, "credits": {"balance": "secret balance"}}}}


def thread_result():
    return {"thread": {"id": "thread-1", "ephemeral": True, "path": None},
            "runtimeWorkspaceRoots": [], "sandbox": {"type": "readOnly", "networkAccess": False},
            "approvalPolicy": "never", "modelProvider": "openai", "model": "gpt-test"}


class FakeProcess:
    def __init__(self, output=b""):
        self.stdin = io.BytesIO()
        self.stdout = io.BytesIO(output)
        self.stderr = io.BytesIO()
        self.closed = False

    def close(self):
        self.closed = True

    def wait(self, timeout=2):
        return 0


def test_environment_never_forwards_api_auth_or_custom_endpoint():
    source = {"PATH": "x", "APPDATA": "existing-profile", "OPENAI_API_KEY": "secret",
              "openai_base_url": "http://other", "ANTHROPIC_API_KEY": "secret",
              "DEEPSEEK_API_KEY": "native-secret", "DEEPSEEK_BASE_URL": "https://other",
              "AWS_PROFILE": "p", "HTTPS_PROXY": "proxy", "CODEX_HOME": "custom",
              "NODE_OPTIONS": "--inspect", "ordinary": "keep"}
    assert clean_environment(source) == {"PATH": "x", "APPDATA": "existing-profile", "ordinary": "keep"}
    assert source["OPENAI_API_KEY"] == "secret"  # Never mutate the parent's environment.


@pytest.mark.parametrize("used", [95, 96, 100])
def test_low_or_zero_quota_is_informational(used):
    result = _quota_summary(quota(used))
    assert result["known"] and result["can_start"] and result["managed_by_user"]
    assert "secret balance" not in str(result)


def test_quota_displays_more_exhausted_window_without_gating():
    assert _quota_summary(quota(20, {"usedPercent": 98}))["can_start"]
    assert _quota_summary(quota(20, {"usedPercent": 98}))["remaining_percent"] == 2
    assert _quota_summary(quota(94))["remaining_percent"] == 6


@pytest.mark.parametrize("used", [None, True, -1, 101, "2", float("inf"), float("nan")])
def test_unknown_quota_remains_non_blocking(used):
    result = _quota_summary(quota(used))
    assert not result["known"] and result["can_start"] and result["managed_by_user"]


def test_non_codex_bucket_is_not_assumed_ordinary_quota():
    result = _quota_summary({"rateLimitsByLimitId": {"other": {"primary": {"usedPercent": 1}}}})
    assert not result["known"]


def test_legacy_quota_compatible_without_credit_leak():
    result = _quota_summary({"rateLimits": {"primary": {"usedPercent": 10}, "credits": {"balance": "private"}}})
    assert result["can_start"] and "private" not in str(result)


def test_unverified_builtin_isolation_never_sends_turn(tmp_path, monkeypatch):
    client = transport(tmp_path)
    monkeypatch.setattr(module, "BUILTIN_ISOLATION_VERIFIED", False)
    monkeypatch.setattr(client, "_rpc", lambda *args: pytest.fail("must not send any RPC"))
    with pytest.raises(TransportError, match="内置工具") as error:
        client.start_turn("t", "hello", "gpt-test", "high")
    assert error.value.code == module.ISOLATION_BLOCK


@pytest.mark.parametrize("method", ["turn/start", "account/login/start", "account/rateLimitResetCredit/consume",
                                    "config/value/write", "process/spawn", "thread/start"])
def test_public_rpc_cannot_bypass_mutation_guards(tmp_path, method):
    with pytest.raises(TransportError) as error:
        transport(tmp_path).request(method, {})
    assert error.value.code == "method_forbidden"


@pytest.mark.parametrize("account", [None, {"type": "apiKey"}, {"type": "amazonBedrock"}])
def test_only_subscription_auth_accepted(tmp_path, monkeypatch, account):
    client = transport(tmp_path)
    monkeypatch.setattr(client, "_rpc", lambda *args: {"account": account})
    with pytest.raises(TransportError) as error:
        client._read_account_limits()
    assert error.value.code == "auth_unsupported"


def test_account_summary_removes_email(tmp_path, monkeypatch):
    client = transport(tmp_path)
    answers = iter([{"account": {"type": "chatgpt", "email": "private@example.test", "planType": "pro"}}, quota()])
    monkeypatch.setattr(client, "_rpc", lambda *args: next(answers))
    client._read_account_limits()
    assert client.account == {"type": "chatgpt", "plan_type": "pro"}


def test_unavailable_quota_summary_does_not_block_authenticated_account(tmp_path, monkeypatch):
    client = transport(tmp_path)

    def rpc(method, params):
        if method == "account/read":
            return {"account": {"type": "chatgpt", "planType": "pro"}}
        raise TransportError("rpc_rejected", "额度摘要暂不可用")

    monkeypatch.setattr(client, "_rpc", rpc)
    client._read_account_limits()
    assert client.account == {"type": "chatgpt", "plan_type": "pro"}
    assert not client.limits["known"] and client.limits["can_start"]
    assert client.limits["managed_by_user"]


def test_start_thread_disables_mcp_before_any_server_thread(tmp_path, monkeypatch):
    client = transport(tmp_path)
    client.models = [model()]
    client._mcp_names = ["node_repl"]
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        return thread_result() if method == "thread/start" else {"data": [{"name": "node_repl", "tools": {}}], "nextCursor": None}

    monkeypatch.setattr(client, "_rpc", rpc)
    assert client.start_thread([], "controlled instruction", "gpt-test") == "thread-1"
    assert calls[0][1]["config"] == {"mcp_servers.node_repl.enabled": False}
    assert calls[0][1]["environments"] == [] and calls[0][1]["ephemeral"]
    assert calls[0][1]["selectedCapabilityRoots"] == []
    assert calls[0][1]["allowProviderModelFallback"] is False
    assert calls[0][1]["modelProvider"] == "openai"


@pytest.mark.parametrize("field,value", [("modelProvider", "other"), ("model", "silent-fallback"),
                                         ("approvalPolicy", "on-request"), ("runtimeWorkspaceRoots", ["C:/"]),
                                         ("sandbox", {"type": "readOnly", "networkAccess": True})])
def test_thread_echo_mismatch_kills_connection(tmp_path, monkeypatch, field, value):
    client = transport(tmp_path)
    client.models = [model()]
    result = thread_result()
    result[field] = value
    monkeypatch.setattr(client, "_rpc", lambda *args: result)
    with pytest.raises(TransportError) as error:
        client.start_thread([], "instructions", "gpt-test")
    assert error.value.code == "thread_isolation"
    assert client._closed


def test_extra_mcp_tool_on_later_page_kills_connection(tmp_path, monkeypatch):
    client = transport(tmp_path)
    client.models = [model()]
    client._mcp_names = ["node_repl"]
    answers = iter([thread_result(), {"data": [], "nextCursor": "page-2"},
                    {"data": [{"name": "node_repl", "tools": {"js": {}}}], "nextCursor": None}])
    monkeypatch.setattr(client, "_rpc", lambda *args: next(answers))
    with pytest.raises(TransportError) as error:
        client.start_thread([], "instructions", "gpt-test")
    assert error.value.code == "mcp_not_isolated" and client._closed


def test_mcp_disabled_directory_rows_allowed_but_unknown_server_not(tmp_path, monkeypatch):
    client = transport(tmp_path)
    client.models = [model()]
    answers = iter([thread_result(), {"data": [{"name": "new_server", "tools": {}}]}])
    monkeypatch.setattr(client, "_rpc", lambda *args: next(answers))
    with pytest.raises(TransportError, match="额外 MCP"):
        client.start_thread([], "instructions", "gpt-test")


def test_ultra_mode_cannot_enable_multiagent(tmp_path):
    client = transport(tmp_path)
    client.models = [model()]
    with pytest.raises(TransportError) as error:
        client._model("gpt-test", "ultra")
    assert error.value.code == "effort_unsupported"


def test_verified_turn_pins_permissions_without_quota_gate(tmp_path, monkeypatch):
    client = transport(tmp_path)
    client.models = [model()]
    client._threads["t"] = {"model": "gpt-test"}
    calls = []

    def rpc(method, params):
        calls.append((method, params))
        return {"turn": {"id": "turn-1"}}

    monkeypatch.setattr(module, "BUILTIN_ISOLATION_VERIFIED", True)
    monkeypatch.setattr(client, "_rpc", rpc)
    assert client.start_turn("t", "hello", "gpt-test", "high") == "turn-1"
    assert [item[0] for item in calls] == ["turn/start"]
    sent = calls[-1][1]
    assert sent["environments"] == [] and sent["runtimeWorkspaceRoots"] == []
    assert sent["approvalPolicy"] == "never" and not sent["sandboxPolicy"]["networkAccess"]


def test_verified_turn_does_not_refuse_low_displayed_quota(tmp_path, monkeypatch):
    client = transport(tmp_path)
    client.models = [model()]
    client._threads["t"] = {"model": "gpt-test"}
    calls = []

    def rpc(method, params):
        calls.append(method)
        return {"turn": {"id": "turn-low-quota"}}

    monkeypatch.setattr(module, "BUILTIN_ISOLATION_VERIFIED", True)
    monkeypatch.setattr(client, "_rpc", rpc)
    client.limits = _quota_summary(quota(100))
    assert client.start_turn("t", "hello", "gpt-test", "high") == "turn-low-quota"
    assert calls == ["turn/start"]


def test_concurrent_rpc_matches_reversed_responses(tmp_path, monkeypatch):
    client = transport(tmp_path)
    barrier = threading.Barrier(2)
    results = {}

    def write(message):
        barrier.wait(timeout=2)
        client._pending[message["id"]].put({"id": message["id"], "result": {"value": message["params"]["value"]}})

    monkeypatch.setattr(client, "_write", write)
    workers = [threading.Thread(target=lambda value=i: results.update({value: client._rpc("model/list", {"value": value})})) for i in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=3)
    assert results == {0: {"value": 0}, 1: {"value": 1}}
    assert not client._pending


def test_rpc_timeout_closes_owned_process(tmp_path):
    client = transport(tmp_path)
    client._process = FakeProcess()
    with pytest.raises(TransportError) as error:
        client._rpc("model/list", {}, timeout=.001)
    assert error.value.code == "request_timeout" and client._process.closed


def test_blocked_stdin_write_is_bounded_and_kills_owned_process(tmp_path, monkeypatch):
    client = transport(tmp_path)
    client._process = FakeProcess()
    release = threading.Event()
    monkeypatch.setattr(module, "RPC_TIMEOUT", .01)
    monkeypatch.setattr(client, "_write_bytes", lambda payload: release.wait(.5))
    try:
        with pytest.raises(TransportError) as error:
            client._write({"method": "initialize"})
        assert error.value.code == "write_timeout" and client._process.closed
    finally:
        release.set()


def test_rpc_raw_error_secret_is_not_exposed(tmp_path, monkeypatch):
    client = transport(tmp_path)

    def write(message):
        client._pending[message["id"]].put({"id": message["id"], "error": {"message": "SECRET_TOKEN"}})

    monkeypatch.setattr(client, "_write", write)
    with pytest.raises(TransportError) as error:
        client._rpc("model/list", {})
    assert "SECRET_TOKEN" not in str(error.value)


def test_reader_dispatches_response_and_dynamic_callback_separately(tmp_path):
    events = []
    client = transport(tmp_path, events)
    messages = [{"id": 1, "result": {"ok": True}},
                {"id": 1, "method": "item/tool/call", "params": {"tool": "controlled"}},
                {"method": "item/agentMessage/delta", "params": {"delta": "hello"}}]
    client._process = FakeProcess(b"".join((json.dumps(item) + "\n").encode() for item in messages))
    client._pending[1] = queue.Queue()
    client._reader()
    assert client._pending[1].get_nowait()["result"] == {"ok": True}
    assert [event["method"] for event in events if "method" in event] == ["item/tool/call", "item/agentMessage/delta"]
    assert 1 in client._requests


def test_unknown_server_approval_denied_without_forwarding(tmp_path):
    events = []
    client = transport(tmp_path, events)
    message = {"id": "server-1", "method": "item/commandExecution/requestApproval", "params": {"command": "secret"}}
    client._process = FakeProcess((json.dumps(message) + "\n").encode())
    client._reader()
    written = json.loads(client._process.stdin.getvalue())
    assert written["error"]["code"] == -32601
    assert events == [{"_transport_error": "unexpected_server_request"}]


@pytest.mark.parametrize("payload", [b"not json\n", b"[]\n", b"{}\n"])
def test_malformed_protocol_closes_connection(tmp_path, payload):
    client = transport(tmp_path)
    client._process = FakeProcess(payload)
    client._reader()
    assert client._failure.code == "protocol_invalid" and client._process.closed


def test_overlong_line_rejected_before_json_load(tmp_path, monkeypatch):
    client = transport(tmp_path)
    monkeypatch.setattr(module, "MAX_LINE_BYTES", 8)
    client._process = FakeProcess(b"123456789\n")
    client._reader()
    assert client._failure.code == "protocol_size"


def test_reply_requires_live_callback_id(tmp_path):
    client = transport(tmp_path)
    with pytest.raises(TransportError) as error:
        client.reply_tool("unknown", {"success": True, "contentItems": []})
    assert error.value.code == "request_unknown"


def test_short_pipe_writes_are_completed(tmp_path):
    class ShortPipe(io.BytesIO):
        def write(self, data):
            return super().write(data[:3])

    client = transport(tmp_path)
    client._process = FakeProcess()
    client._process.stdin = ShortPipe()
    client._write({"id": 1, "method": "example"})
    assert json.loads(client._process.stdin.getvalue())["method"] == "example"


def test_tool_envelope_is_adapted_to_dynamic_protocol(tmp_path):
    client = transport(tmp_path)
    client._process = FakeProcess()
    client._requests.add("call-1")
    client.reply_tool("call-1", {"code": 0, "data": {"value": "中文"}})
    response = json.loads(client._process.stdin.getvalue())
    assert response["result"]["success"]
    assert json.loads(response["result"]["contentItems"][0]["text"])["data"]["value"] == "中文"
    with pytest.raises(TransportError):
        client.reply_tool("call-1", {"code": 0})


def test_user_input_tool_is_soft_rejected_but_execution_is_not(tmp_path):
    events = []
    client = transport(tmp_path, events)
    message = {"id": "q-1", "method": "item/tool/requestUserInput", "params": {"questions": []}}
    client._process = FakeProcess((json.dumps(message) + "\n").encode())
    client._reader()
    response = json.loads(client._process.stdin.getvalue())
    assert "ordinary text" in response["error"]["message"]
    assert events[0]["params"]["reason"] == "use_plain_text_question"


@pytest.mark.parametrize("mode", [None, "apikey", "bedrockApiKey", "chatgptAuthTokens"])
def test_mid_turn_auth_changes_fail_closed(tmp_path, mode):
    client = transport(tmp_path)
    message = {"method": "account/updated", "params": {"authMode": mode}}
    client._process = FakeProcess((json.dumps(message) + "\n").encode())
    client._reader()
    assert client._failure.code == "auth_changed"


def test_unknown_bucket_does_not_use_legacy_alias(tmp_path):
    raw = quota()
    raw["rateLimits"] = raw["rateLimitsByLimitId"]["codex"]
    raw["rateLimitsByLimitId"] = {"other": raw["rateLimits"]}
    assert not _quota_summary(raw)["known"]


def connect_client(tmp_path, monkeypatch, *, servers=None, provider="openai"):
    path = tmp_path / "codex.exe"
    path.write_bytes(b"MZsynthetic-test-binary")
    digest = module._digest(path)
    monkeypatch.setattr(module, "SUPPORTED_SHA256", digest)
    cli = {"path": str(path), "version": module.SUPPORTED_VERSION, "sha256": digest}
    client = CodexTransport(cli, tmp_path, lambda _: None)
    monkeypatch.setattr(module, "_OwnedWindowsProcess", lambda *args: FakeProcess())
    monkeypatch.setattr(threading.Thread, "start", lambda self: None)
    monkeypatch.setattr(client, "_write", lambda message: None)
    answers = {"initialize": {}, "config/read": {"config": {"mcp_servers": servers or {},
               "model_provider": provider, "forced_login_method": "chatgpt", "model_providers": {}, "notify": []}},
               "account/read": {"account": {"type": "chatgpt", "planType": "pro", "email": "private"}},
               "account/rateLimits/read": quota(), "model/list": {"data": [model()], "nextCursor": None}}
    monkeypatch.setattr(client, "_rpc", lambda method, params: answers[method])
    return client


def test_connect_summary_contains_no_credentials_or_email(tmp_path, monkeypatch):
    client = connect_client(tmp_path, monkeypatch, servers={"node_repl": {"env": {"SECRET": "private"}}})
    result = client.connect()
    assert result["account"] == {"type": "chatgpt", "plan_type": "pro"}
    assert "private" not in str(result)
    assert client._mcp_names == ["node_repl"]
    client.close()


def test_launch_explicitly_disables_legacy_notify(tmp_path, monkeypatch):
    client = connect_client(tmp_path, monkeypatch)
    launched = []

    def spawn(args, cwd, env):
        launched.append(args)
        return FakeProcess()

    monkeypatch.setattr(module, "_OwnedWindowsProcess", spawn)
    client.connect()
    pairs = list(zip(launched[0], launched[0][1:]))
    assert ("-c", "notify=[]") in pairs
    assert ("-c", "features.respect_system_proxy=true") in pairs
    assert ("--disable", "hooks") in pairs
    client.close()


@pytest.mark.parametrize("notify", [None, ["private-command", "secret-argument"], "", False, {}])
def test_unverified_legacy_notify_aborts_before_account_or_thread(tmp_path, monkeypatch, notify):
    client = connect_client(tmp_path, monkeypatch)
    original = client._rpc
    methods = []

    def rpc(method, params):
        methods.append(method)
        result = original(method, params)
        if method == "config/read":
            result["config"]["notify"] = notify
        return result

    monkeypatch.setattr(client, "_rpc", rpc)
    with pytest.raises(TransportError) as error:
        client.connect()
    assert error.value.code == "notify_not_isolated" and client._closed
    assert methods == ["initialize", "config/read"]
    assert "private-command" not in str(error.value)
    assert "secret-argument" not in str(error.value)


def test_unsafe_mcp_key_aborts_before_thread(tmp_path, monkeypatch):
    client = connect_client(tmp_path, monkeypatch, servers={"nested.name": {}})
    with pytest.raises(TransportError) as error:
        client.connect()
    assert error.value.code == "mcp_name_unsupported" and client._closed


def test_provider_override_mismatch_aborts_connection(tmp_path, monkeypatch):
    client = connect_client(tmp_path, monkeypatch, provider="custom")
    with pytest.raises(TransportError) as error:
        client.connect()
    assert error.value.code == "provider_unknown" and client._closed


@pytest.mark.parametrize("extra", [{"model_providers": {"openai": {"base_url": "http://other"}}},
                                   {"chatgpt_base_url": "http://other"},
                                   {"chatgpt_auth_base_url": "http://other"}])
def test_custom_endpoint_cannot_reuse_chatgpt_identity(tmp_path, monkeypatch, extra):
    client = connect_client(tmp_path, monkeypatch)
    original = client._rpc

    def rpc(method, params):
        result = original(method, params)
        if method == "config/read":
            result["config"].update(extra)
        return result

    monkeypatch.setattr(client, "_rpc", rpc)
    with pytest.raises(TransportError) as error:
        client.connect()
    assert error.value.code == "provider_override" and client._closed


def test_changed_cli_fails_before_spawn(tmp_path):
    client = CodexTransport({"path": str(tmp_path / "not-a-cli.exe"), "version": "0.147.0", "sha256": "x"}, tmp_path, lambda item: None)
    with pytest.raises(TransportError) as error:
        client.connect()
    assert error.value.code == "cli_changed"


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object")
def test_real_job_runs_hidden_native_python_and_closes(tmp_path):
    process = _OwnedWindowsProcess([sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.read()"], tmp_path, clean_environment())
    try:
        assert process.stdout.readline() == b"ready\r\n"
        assert process.poll() is None
        process.stdin.close()
        assert process.wait(timeout=3) == 0
    finally:
        process.close()
    process.close()  # Idempotent even after natural exit.


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object")
def test_job_close_terminates_owned_child_tree(tmp_path):
    import ctypes
    from ctypes import wintypes

    code = ("import subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(p.pid,flush=True); time.sleep(60)")
    process = _OwnedWindowsProcess([sys.executable, "-c", code], tmp_path, clean_environment())
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    child_handle = None
    try:
        child_pid = int(process.stdout.readline())
        child_handle = kernel.OpenProcess(0x00100000, False, child_pid)  # SYNCHRONIZE
        assert child_handle
        process.close()
        assert kernel.WaitForSingleObject(child_handle, 3000) == 0
    finally:
        process.close()
        if child_handle:
            kernel.CloseHandle(child_handle)
