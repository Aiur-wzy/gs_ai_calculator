"""Cold-start allowance is independent of ordinary physical pipe I/O."""
from __future__ import annotations

import math
import os
import queue
import threading
import time

import pytest

from genshin_app.agent_chat import native_dsh_mux_process as mux
from genshin_app.agent_chat import native_pipe as pipe
from genshin_app.agent_chat.native_turn_budget import DEFAULT_BUDGET

_OS_SMOKE = os.name == "nt" and os.environ.get("GENSHIN_NATIVE_PIPE_OS_SMOKE") == "1"
_INVALID_CONNECT = [True, False, None, "7200", 0, -1, 0.0001,
                    math.nan, math.inf, -math.inf, 1e300, 10**1000]


def test_connection_grace_accepts_full_app_budget_and_finite_dword_boundary():
    app_budget = DEFAULT_BUDGET["wall_clock_seconds"]
    assert pipe.connect_timeout_milliseconds(app_budget) == app_budget * 1000
    assert pipe.connect_timeout_milliseconds(7200) == 7_200_000
    assert pipe.connect_timeout_milliseconds(0.001) == 1
    maximum_seconds = 0xFFFFFFFE / 1000
    assert pipe.connect_timeout_milliseconds(maximum_seconds) == 0xFFFFFFFE
    with pytest.raises(ValueError):
        pipe.connect_timeout_milliseconds(math.nextafter(maximum_seconds, math.inf))
    with pytest.raises(ValueError):
        pipe.connect_timeout_milliseconds(0xFFFFFFFF / 1000)


@pytest.mark.parametrize("value", _INVALID_CONNECT)
def test_bad_connection_grace_is_rejected_before_node_or_os(value, monkeypatch, tmp_path):
    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid connection budget reached executable audit or child creation")
    monkeypatch.setattr(mux, "audit_node_executable", forbidden)
    monkeypatch.setattr(mux.subprocess, "Popen", forbidden)
    with pytest.raises((TypeError, ValueError)):
        mux.NativeDshMuxProcess(node=tmp_path / "node.exe", entry=tmp_path / "entry.mjs",
            working=tmp_path, connect_timeout_seconds=value)


@pytest.mark.parametrize("value", [7200, 60.001, True, False, None, "15", 0, -1,
                                  math.nan, math.inf, -math.inf])
def test_regular_io_remains_bounded_and_rejected_before_win32(value, monkeypatch):
    def forbidden():
        pytest.fail("invalid fragment I/O budget reached Win32")
    monkeypatch.setattr(pipe, "_api", forbidden)
    with pytest.raises((TypeError, ValueError)):
        pipe.NativePipeServer(io_timeout=value)


@pytest.mark.parametrize("value", _INVALID_CONNECT)
def test_public_pipe_accept_rejects_bad_grace_without_touching_server(value):
    # Validation precedes reading a handle or taking any server locks.
    uninitialised = object.__new__(pipe.NativePipeServer)
    with pytest.raises((TypeError, ValueError)):
        uninitialised.accept(timeout=value)


@pytest.mark.skipif(not _OS_SMOKE, reason="explicit Windows real named-pipe qualification")
def test_7200_second_grace_connects_after_short_delay_with_short_physical_io():
    clients = queue.Queue()
    server = pipe.NativePipeServer(io_timeout=0.05)
    connection = client = None

    def connect():
        time.sleep(0.04)
        try:
            clients.put(pipe.open_local_pipe_client(server.name,
                connect_timeout=7200, io_timeout=0.05))
        except Exception as error:  # noqa: BLE001 - propagate worker failure
            clients.put(error)

    worker = threading.Thread(target=connect, daemon=True)
    # Test-only watchdog prevents a broken connector from waiting 7200s.
    watchdog = threading.Timer(3, server.close)
    watchdog.daemon = True
    started = time.monotonic()
    try:
        watchdog.start()
        worker.start()
        connection = server.accept(timeout=7200)
        watchdog.cancel()
        client = clients.get(timeout=2)
        if isinstance(client, Exception):
            raise client
        assert time.monotonic() - started < 3
        assert connection._timeout_ms == 50
        read_started = time.monotonic()
        with pytest.raises(pipe.NativePipeError) as caught:
            connection.read(1)
        assert caught.value.code == "pipe_read_timeout"
        assert time.monotonic() - read_started < 2
    finally:
        watchdog.cancel()
        for endpoint in (client, connection):
            if endpoint is not None and not isinstance(endpoint, Exception):
                endpoint.close()
        server.close()
        worker.join(timeout=2)
        assert not worker.is_alive()


@pytest.mark.skipif(not _OS_SMOKE, reason="explicit Windows real named-pipe qualification")
@pytest.mark.parametrize("io_timeout", [None, 0.05])
def test_mux_7200_grace_reaches_popen_through_real_server_with_separate_io(
    tmp_path, monkeypatch, io_timeout,
):
    node = tmp_path / "node.exe"
    node.write_bytes(b"nonexecuted executable identity for the carrier boundary")
    entry = tmp_path / "entry.mjs"
    entry.write_text("// not executed\n", encoding="utf-8")
    closed = []
    actual_close = pipe.NativePipeServer.close

    def observe_close(server):
        closed.append(server._io_timeout)
        actual_close(server)
        assert server.closed

    class ReachedPopen(RuntimeError):
        pass

    calls = []

    def stop_at_popen(*arguments, **options):
        calls.append((arguments, options))
        raise ReachedPopen

    monkeypatch.setattr(pipe.NativePipeServer, "close", observe_close)
    monkeypatch.setattr(mux.subprocess, "Popen", stop_at_popen)
    options = {} if io_timeout is None else {"io_timeout_seconds": io_timeout}
    with pytest.raises(ReachedPopen):
        mux.NativeDshMuxProcess(node=node, entry=entry, working=tmp_path,
            connect_timeout_seconds=7200, **options)
    assert len(calls) == 1
    assert calls[0][0][0] == [str(node), "--expose-internals", str(entry)]
    assert closed == [15.0 if io_timeout is None else io_timeout]
