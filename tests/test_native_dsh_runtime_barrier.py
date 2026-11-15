from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from genshin_app.agent_chat.native_dsh_mux_client import (
    NativeDshMuxClient,
    NativeDshMuxError,
)
from tests.test_native_dsh_mux_client import Carrier


def test_domain_barrier_waits_actual_worker_without_blocking_reader():
    carrier = Carrier()
    entered, release = threading.Event(), threading.Event()

    def domain(_kind, request):
        entered.set()
        assert release.wait(5)
        return {"request_id": request["request_id"], "session_id": request["session_id"], "snapshot": {}}

    client = NativeDshMuxClient(carrier, domain_handler=domain)
    try:
        carrier.inject("dsh_app_bind_request", {"request_id": "blocked", "session_id": "one"},
                       correlation="blocked", lane="remote_unary")
        assert entered.wait(1)
        assert client.snapshot()["active_domain_requests"] == 1
        with ThreadPoolExecutor() as pool:
            barrier = pool.submit(client.wait_domain_idle)
            with pytest.raises(TimeoutError):
                barrier.result(timeout=.05)
            release.set()
            barrier.result(timeout=1)
        assert client.snapshot()["active_domain_requests"] == 0
        assert "blocked" in client._domain_requests  # Dedup history is not inflight work.
    finally:
        release.set()
        client.close()


def test_close_cancels_queued_domain_future_and_wakes_barrier_as_unknown():
    carrier = Carrier()
    entered, release = threading.Event(), threading.Event()

    def domain(_kind, request):
        entered.set()
        assert release.wait(5)
        return {"request_id": request["request_id"], "session_id": request["session_id"], "snapshot": {}}

    client = NativeDshMuxClient(carrier, domain_handler=domain, domain_workers=1)
    try:
        for identity in ("one", "two"):
            carrier.inject("dsh_app_bind_request", {"request_id": identity, "session_id": identity},
                           correlation=identity, lane="remote_unary")
        assert entered.wait(1)
        # A reader-delivered sentinel proves both submissions were processed.
        carrier.inject("dsh_runtime_control_result", {}, correlation="unrelated", lane="control")
        with client._condition:
            client._condition.wait_for(lambda: len(client._active_domain_requests) == 2, timeout=1)
        assert client.snapshot()["active_domain_requests"] == 2
        with ThreadPoolExecutor() as pool:
            barrier = pool.submit(client.wait_domain_idle)
            client.close()
            with pytest.raises(NativeDshMuxError, match="closed|connection_lost"):
                barrier.result(timeout=1)
        assert client.snapshot()["active_domain_requests"] == 1
        release.set()
        with client._condition:
            client._condition.wait_for(lambda: not client._active_domain_requests, timeout=1)
        assert client.snapshot()["active_domain_requests"] == 0
    finally:
        release.set()
        client.close()
