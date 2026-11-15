from __future__ import annotations

import ctypes
import gc
import os
import queue
import threading
import time
import uuid
import weakref

import pytest

from genshin_app.agent_chat import native_pipe as native_pipe_module
from genshin_app.agent_chat.bridge_protocol import make_frame
from genshin_app.agent_chat.native_pipe import (
    PIPE_NAME_RE,
    NativePipeError,
    NativePipeServer,
    current_user_sid,
    open_local_pipe_client,
    random_pipe_name,
)

_OS_SMOKE = os.name == "nt" and os.environ.get("GENSHIN_NATIVE_PIPE_OS_SMOKE") == "1"


def _error(code, call):
    with pytest.raises(NativePipeError) as caught:
        call()
    assert caught.value.code == code


def _frame(seq=1, frame_type="turn.started"):
    return make_frame(
        kind="event",
        frame_type=frame_type,
        message_id=str(uuid.uuid4()),
        runtime_incarnation="runtime-pipe",
        conversation_id="conversation-pipe",
        turn_id="turn-pipe",
        step_id="step-pipe",
        epoch_id="epoch-pipe",
        event_seq=seq,
        payload={"status": "running"},
    )


class _CancelKernel:
    def __init__(self, foreground_results, *, reaper_gate=None):
        self.foreground_results = list(foreground_results)
        self.reaper_gate = reaper_gate
        self.cancel_calls = 0
        self.close_calls = []
        self.result_calls = 0
        self.reaper_started = threading.Event()
        self.wait_timeouts = []

    def WaitForSingleObject(self, _event, timeout_ms):
        self.wait_timeouts.append(timeout_ms)
        if timeout_ms == native_pipe_module._INFINITE:
            self.reaper_started.set()
            if self.reaper_gate is None:
                return native_pipe_module._WAIT_FAILED
            self.reaper_gate.wait(timeout=2.0)
            return native_pipe_module._WAIT_OBJECT_0
        return self.foreground_results.pop(0)

    def CancelIoEx(self, _handle, _overlapped):
        self.cancel_calls += 1
        return True

    def GetOverlappedResult(self, _handle, _overlapped, _transferred, _wait):
        self.result_calls += 1
        return False

    def CloseHandle(self, handle):
        self.close_calls.append(handle)
        return True


class _CompletingKernel(_CancelKernel):
    def GetOverlappedResult(self, _handle, _overlapped, transferred, _wait):
        self.result_calls += 1
        transferred._obj.value = 0
        return True


def _fake_windows_api(kernel):
    api = object.__new__(native_pipe_module._WindowsApi)
    api.kernel = kernel
    api._deferred_lock = threading.Lock()
    api._deferred_operations = set()
    return api


def test_cancel_settle_success_keeps_original_timeout_and_closes_event_once():
    kernel = _CancelKernel([
        native_pipe_module._WAIT_TIMEOUT,
        native_pipe_module._WAIT_OBJECT_0,
    ])
    api = _fake_windows_api(kernel)
    operation = native_pipe_module._OverlappedOperation(api, 901)

    _error(
        "pipe_read_timeout",
        lambda: api.wait_overlapped(
            7, operation, 5, timeout_code="pipe_read_timeout",
        ),
    )
    operation.close()
    operation.close()

    assert kernel.cancel_calls == 1
    assert kernel.close_calls == [901]
    assert api._deferred_operations == set()


def test_dynamic_read_deadline_polls_owner_without_cancelling_healthy_io():
    kernel = _CompletingKernel([
        native_pipe_module._WAIT_TIMEOUT,
        native_pipe_module._WAIT_OBJECT_0,
    ])
    api = _fake_windows_api(kernel)
    operation = native_pipe_module._OverlappedOperation(api, 903)
    deadline = time.monotonic() + 60.0

    assert api.wait_overlapped(
        7,
        operation,
        5,
        timeout_code="pipe_read_timeout",
        deadline_provider=lambda: deadline,
    ) == 0
    operation.close()

    assert kernel.cancel_calls == 0
    assert kernel.wait_timeouts == [
        native_pipe_module._DYNAMIC_DEADLINE_POLL_MS,
        native_pipe_module._DYNAMIC_DEADLINE_POLL_MS,
    ]


def test_dynamic_read_deadline_cancels_only_after_owner_deadline_expires():
    kernel = _CancelKernel([
        native_pipe_module._WAIT_TIMEOUT,
        native_pipe_module._WAIT_OBJECT_0,
    ])
    api = _fake_windows_api(kernel)
    operation = native_pipe_module._OverlappedOperation(api, 904)

    _error(
        "pipe_read_timeout",
        lambda: api.wait_overlapped(
            7,
            operation,
            5,
            timeout_code="pipe_read_timeout",
            deadline_provider=lambda: time.monotonic() - 1.0,
        ),
    )
    operation.close()

    assert kernel.cancel_calls == 1
    assert kernel.wait_timeouts[0] == 0


def test_blocked_read_rechecks_a_new_earlier_control_deadline():
    kernel = _CancelKernel([
        native_pipe_module._WAIT_TIMEOUT,
        native_pipe_module._WAIT_TIMEOUT,
        native_pipe_module._WAIT_OBJECT_0,
    ])
    api = _fake_windows_api(kernel)
    operation = native_pipe_module._OverlappedOperation(api, 905)
    deadlines = iter([
        time.monotonic() + 60.0,
        time.monotonic() - 1.0,
    ])

    _error(
        "pipe_read_timeout",
        lambda: api.wait_overlapped(
            7,
            operation,
            5,
            timeout_code="pipe_read_timeout",
            deadline_provider=lambda: next(deadlines),
        ),
    )
    operation.close()

    assert kernel.cancel_calls == 1
    assert kernel.wait_timeouts[:2] == [
        native_pipe_module._DYNAMIC_DEADLINE_POLL_MS,
        0,
    ]


def test_nested_read_deadline_scope_restores_the_outer_owner():
    connection = object.__new__(native_pipe_module.NativePipeConnection)
    connection._read_deadline_local = threading.local()
    outer = lambda: 10.0
    inner = lambda: 20.0

    assert not hasattr(connection._read_deadline_local, "provider")
    with connection.read_deadline(outer):
        assert connection._read_deadline_local.provider is outer
        with connection.read_deadline(inner):
            assert connection._read_deadline_local.provider is inner
        assert connection._read_deadline_local.provider is outer
    assert not hasattr(connection._read_deadline_local, "provider")


def test_nested_write_deadline_scope_restores_the_outer_owner():
    connection = object.__new__(native_pipe_module.NativePipeConnection)
    connection._write_deadline_local = threading.local()
    outer = lambda: 10.0
    inner = lambda: 20.0

    assert not hasattr(connection._write_deadline_local, "provider")
    with connection.write_deadline(outer):
        assert connection._write_deadline_local.provider is outer
        with connection.write_deadline(inner):
            assert connection._write_deadline_local.provider is inner
        assert connection._write_deadline_local.provider is outer
    assert not hasattr(connection._write_deadline_local, "provider")


def test_dynamic_write_deadline_preflight_rejects_expired_or_invalid_owner():
    _error(
        "pipe_write_timeout",
        lambda: native_pipe_module.NativePipeConnection._preflight_deadline(
            lambda: time.monotonic() - 1.0,
            invalid_code="pipe_write_deadline_invalid",
            timeout_code="pipe_write_timeout",
        ),
    )
    _error(
        "pipe_write_deadline_invalid",
        lambda: native_pipe_module.NativePipeConnection._preflight_deadline(
            lambda: float("nan"),
            invalid_code="pipe_write_deadline_invalid",
            timeout_code="pipe_write_timeout",
        ),
    )


def test_unsettled_cancel_returns_bounded_error_and_reaper_owns_storage():
    reaper_gate = threading.Event()
    kernel = _CancelKernel(
        [native_pipe_module._WAIT_TIMEOUT, native_pipe_module._WAIT_TIMEOUT],
        reaper_gate=reaper_gate,
    )
    api = _fake_windows_api(kernel)
    operation = native_pipe_module._OverlappedOperation(api, 902)
    pending_buffer = ctypes.create_string_buffer(b"pending")
    pending_buffer_ref = weakref.ref(pending_buffer)
    operation.retain(pending_buffer)
    del pending_buffer
    gc.collect()
    assert pending_buffer_ref() is not None

    started = time.monotonic()
    _error(
        "pipe_cancel_settle_timeout",
        lambda: api.wait_overlapped(
            7, operation, 5, timeout_code="pipe_read_timeout",
        ),
    )
    assert time.monotonic() - started < 0.5
    assert kernel.reaper_started.wait(timeout=1.0)
    assert operation in api._deferred_operations

    # The caller's finally block cannot close an event whose OVERLAPPED is
    # still retained by Win32/the reaper.
    operation.close()
    assert kernel.close_calls == []
    gc.collect()
    assert pending_buffer_ref() is not None

    reaper_gate.set()
    deadline = time.monotonic() + 1.0
    while operation in api._deferred_operations and time.monotonic() < deadline:
        time.sleep(0.005)
    assert operation not in api._deferred_operations
    assert kernel.result_calls == 0
    assert kernel.close_calls == [902]
    gc.collect()
    assert pending_buffer_ref() is None


def test_pipe_names_are_random_local_and_not_a_bootstrap_secret_container():
    names = {random_pipe_name() for _ in range(64)}
    assert len(names) == 64
    assert all(PIPE_NAME_RE.fullmatch(name) for name in names)
    assert all("nonce" not in name and "key" not in name for name in names)


@pytest.mark.skipif(os.name != "nt", reason="Windows named pipe contract")
def test_server_uses_explicit_protected_current_user_dacl():
    sid = current_user_sid()
    with NativePipeServer() as server:
        assert server.security_sddl == f"D:P(A;;GA;;;{sid})"
        assert "WD" not in server.security_sddl
        assert "AN" not in server.security_sddl
        assert "AU" not in server.security_sddl
        assert PIPE_NAME_RE.fullmatch(server.name)


def _connected_pair(*, io_timeout=2.0):
    server = NativePipeServer(io_timeout=io_timeout)
    result = queue.Queue()

    def connect():
        try:
            result.put(open_local_pipe_client(
                server.name, connect_timeout=2.0, io_timeout=io_timeout,
            ))
        except Exception as exc:  # noqa: BLE001 - return worker failure to the test
            result.put(exc)

    worker = threading.Thread(target=connect, daemon=True)
    worker.start()
    server_connection = server.accept(timeout=2.0)
    client = result.get(timeout=2.0)
    worker.join(timeout=2.0)
    if isinstance(client, Exception):
        server_connection.close()
        server.close()
        raise client
    return server, server_connection, client


@pytest.mark.skipif(not _OS_SMOKE, reason="requires unsandboxed Windows pipe loopback")
def test_local_pipe_round_trip_adapts_to_strict_bridge_codec():
    server, server_connection, client = _connected_pair()
    try:
        server_bridge = server_connection.bridge_io()
        client_bridge = client.bridge_io()
        first = _frame(1)
        second = _frame(2, "turn.status")

        assert server_bridge.write_frame(first) > 0
        assert client_bridge.read_frame() == first
        assert client_bridge.write_frame(second) > 0
        assert server_bridge.read_frame() == second

        _error("pipe_already_accepted", lambda: server.accept(timeout=0.01))
        server_bridge.close()
        server_bridge.close()
        assert server_connection.closed
    finally:
        client.close()
        server_connection.close()
        server.close()


@pytest.mark.skipif(not _OS_SMOKE, reason="requires unsandboxed Windows pipe loopback")
def test_connect_read_and_close_are_bounded_and_idempotent():
    with NativePipeServer(io_timeout=0.05) as idle:
        _error("pipe_connect_timeout", lambda: idle.accept(timeout=0.05))

    server, server_connection, client = _connected_pair(io_timeout=0.05)
    try:
        _error("pipe_read_timeout", lambda: server_connection.read(1))
        client.close()
        client.close()
        assert client.closed
        assert server_connection.read(1) == b""
    finally:
        client.close()
        server_connection.close()
        server.close()


@pytest.mark.parametrize("name", [
    r"\\.\pipe\genshin-native-short",
    r"\\server\pipe\genshin-native-" + "a" * 64,
    "genshin-native-" + "a" * 64,
])
def test_client_rejects_noncanonical_or_remote_names_before_win32(name):
    _error("pipe_name_invalid", lambda: open_local_pipe_client(name))
