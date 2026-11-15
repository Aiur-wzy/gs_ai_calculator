from __future__ import annotations

import json
import os
import queue
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from genshin_app.agent_chat.native_dsh_mux_client import (
    NativeDshMuxClient,
    NativeDshMuxError,
    decode_dsh_fetch_payload,
    encode_dsh_fetch_payload,
)
from genshin_app.agent_chat.native_dsh_mux_process import NativeDshMuxProcess
from genshin_app.agent_chat.native_dsh_shell_host import NativeDshShellHost
from genshin_app.agent_chat.native_mux_pipe_carrier import NativeMuxPipeCarrier
from genshin_app.agent_chat.native_pipe import NativePipeServer
from genshin_app.agent_chat.native_pipe_multiplexer import (
    NativeMuxReceiver,
    NativeMuxSendQueue,
)
from genshin_app.agent_chat.runtime_integrity import audit_node_executable
from genshin_app.gui.api import GuiApi

ROOT = Path(__file__).resolve().parents[1]


class Carrier:
    def __init__(self):
        self.sent = queue.Queue()
        self.received = queue.Queue()

    def enqueue(self, **packet):
        self.sent.put(packet)

    def flush_one(self):
        return None

    def receive_one(self):
        packet = self.received.get(timeout=5)
        if packet is None:
            raise OSError
        return SimpleNamespace(packet=packet, accept=lambda: None,
                               outcome_unknown=lambda: None, settled=False)

    def inject(self, kind, value, *, stream=None, correlation=None, lane="remote_stream"):
        self.received.put(SimpleNamespace(
            header={"payload_kind": kind, "stream_id": stream, "correlation_id": correlation, "lane": lane},
            payload=json.dumps(value).encode(),
        ))

    def close(self):
        self.received.put(None)


def test_pending_stream_read_cancel_does_not_block_independent_rpc():
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    try:
        stream = client.open_stream("probe/count", {"args": {"limit": 2}})
        carrier.sent.get(timeout=1)
        with ThreadPoolExecutor() as pool:
            reading = pool.submit(client.stream_next, stream)
            assert carrier.sent.get(timeout=1)["payload_kind"] == "dsh_stream_credit"
            client.cancel_stream(stream)
            with pytest.raises(NativeDshMuxError, match="stream_cancelled"):
                reading.result(timeout=1)
            carrier.sent.get(timeout=1)
            pending = pool.submit(client.fetch, {"method": "GET", "path": "/api", "headers": []})
            outbound = carrier.sent.get(timeout=1)
            carrier.received.put(SimpleNamespace(
                header={"payload_kind": "dsh_fetch_result", "stream_id": None,
                        "correlation_id": outbound["correlation_id"]},
                payload=encode_dsh_fetch_payload({"status": 200, "headers": []}, b"ok"),
            ))
            assert pending.result(timeout=1)[1] == b"ok"
    finally:
        client.close()


def test_disconnect_wakes_every_waiter_without_retry():
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    try:
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(client.fetch, {"method": "GET", "path": "/api", "headers": []})
            carrier.sent.get(timeout=1)
            carrier.close()
            with pytest.raises(NativeDshMuxError, match="connection_lost"):
                pending.result(timeout=1)
            assert carrier.sent.empty()
    finally:
        client.close()


def test_peer_disconnect_preserves_delivered_shutdown_reply(monkeypatch):
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    send = client._send
    operation = "dsh_runtime_shutdown"
    result = {"state": "stopping"}

    def send_and_wait_for_peer_exit(lane, kind, payload, **kwargs):
        send(lane, kind, payload, **kwargs)
        outbound = carrier.sent.get(timeout=1)
        assert outbound["payload_kind"] == operation
        carrier.inject("dsh_runtime_control_result", {
            "contract": "genshin-native-dsh-runtime-control-v1",
            "operation": operation,
            "result": result,
        }, correlation=outbound["correlation_id"], lane="control")
        carrier.close()
        client._reader.join(timeout=5)
        assert not client._reader.is_alive()

    monkeypatch.setattr(client, "_send", send_and_wait_for_peer_exit)
    try:
        assert client.control(operation) == result
    finally:
        client.close()


def test_early_cancel_rejects_late_fetch_and_stream_open():
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    identity = str(uuid.uuid4())
    try:
        client.cancel_fetch(identity)
        client.cancel_stream(identity)
        with pytest.raises(NativeDshMuxError, match="request_cancelled"):
            client.fetch({}, request_id=identity)
        with pytest.raises(NativeDshMuxError, match="stream_cancelled"):
            client.open_stream("probe/count", {}, stream_id=identity)
        assert carrier.sent.qsize() == 1  # no stream was opened; fetch cancel is transport control
    finally:
        client.close()


def test_fetch_binary_body_preserved_and_metadata_bounded():
    body = bytes(range(256)) * 8193
    metadata = {"method": "POST", "path": "/api/example", "headers": [["content-type", "x/test"]]}
    assert decode_dsh_fetch_payload(encode_dsh_fetch_payload(metadata, body)) == (metadata, body)
    for raw in (b"", b"\0\0\0\x05{}", b"\xff\xff\xff\xff"):
        with pytest.raises(NativeDshMuxError):
            decode_dsh_fetch_payload(raw)


def test_reverse_domain_call_does_not_block_pending_gui_rpc():
    carrier = Carrier()
    calls = []

    def domain(kind, value):
        calls.append(kind)
        return {"request_id": value["request_id"], "session_id": value["session_id"], "snapshot": {}}

    client = NativeDshMuxClient(carrier, domain_handler=domain)
    try:
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(client.fetch, {"method": "GET", "path": "/api", "headers": []})
            outbound = carrier.sent.get(timeout=1)
            reverse = {"contract": "genshin-native-dsh-domain-bind-v1", "request_id": "domain-bind",
                       "session_id": "dsh-one"}
            carrier.inject("dsh_app_bind_request", reverse, correlation="domain-bind", lane="remote_unary")
            response = carrier.sent.get(timeout=1)
            assert response["payload_kind"] == "dsh_app_bind_result"
            assert response["correlation_id"] == "domain-bind"
            assert calls == ["dsh_app_bind_request"]
            carrier.received.put(SimpleNamespace(
                header={"payload_kind": "dsh_fetch_result", "stream_id": None,
                        "correlation_id": outbound["correlation_id"]},
                payload=encode_dsh_fetch_payload({"status": 200, "headers": []}, b"bound"),
            ))
            assert pending.result(timeout=1)[1] == b"bound"
    finally:
        client.close()


def test_missing_domain_owner_returns_rejection_not_false_settlement():
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    try:
        carrier.inject("dsh_app_bind_request", {"request_id": "unbound", "session_id": "one"},
                       correlation="unbound", lane="remote_unary")
        response = carrier.sent.get(timeout=1)
        assert response["payload_kind"] == "dsh_app_bind_error"
        value = json.loads(response["payload"])
        assert value["code"] == "dsh_domain_unavailable"
        assert value["outcome_unknown"] is False
        assert "status" not in value
    finally:
        client.close()


def test_app_safe_group_uses_actual_host_ledger_and_keeps_control_slot_free(tmp_path):
    import threading

    from genshin_app.agent_chat.native_dsh_domain_calls import (
        DshDomainTarget,
        NativeDshDomainCalls,
    )
    from tests.test_native_app_tool_host import Session, host
    from tests.test_native_dsh_domain_calls import request

    carrier = Carrier()
    entered, release = threading.Event(), threading.Event()
    executed = []

    def read(_session, _name, _arguments):
        executed.append(1)
        entered.set()
        assert release.wait(3)
        return {"code": 0, "data": {"read": len(executed)}}

    current = host(tmp_path, Session(), read)
    dispatcher = NativeDshDomainCalls(lambda _sid: DshDomainTarget("dsh-one", "conversation-1", current))

    class Owner:
        @staticmethod
        def domain_route_cached(_sid):
            return "conversation-1"

        def __call__(self, kind, value):
            if kind == "dsh_budget_acquire_request":
                return {"request_id": value["request_id"], "session_id": value["session_id"], "control": True}
            return dispatcher(kind, value)

    client = NativeDshMuxClient(carrier, domain_handler=Owner(), domain_workers=1)
    try:
        first, second = request(current), request(current, call_id="second", call_seq=6)
        carrier.inject("dsh_app_call_request", first, correlation=first["request_id"], lane="remote_unary")
        assert entered.wait(1)
        carrier.inject("dsh_app_call_request", second, correlation=second["request_id"], lane="remote_unary")
        budget = {"request_id": str(uuid.uuid4()), "session_id": "dsh-one"}
        carrier.inject("dsh_budget_acquire_request", budget, correlation=budget["request_id"], lane="remote_unary")
        assert carrier.sent.get(timeout=1)["payload_kind"] == "dsh_budget_acquire_result"
        assert executed == [1]
        release.set()
        results = [json.loads(carrier.sent.get(timeout=2)["payload"]) for _ in range(2)]
        client.wait_domain_idle()
        assert [row["status"] for row in results] == ["settled_success", "settled_success"]
        assert len(current.ledger.read()["entries"]) == 2
        assert executed == [1, 1]
    finally:
        release.set()
        client.close()


def test_mux_cancel_control_prevents_queued_callback_and_counts_drain():
    import threading

    carrier = Carrier()
    entered, release = threading.Event(), threading.Event()
    executed = []

    def owner(_kind, value):
        executed.append(value["request_id"])
        entered.set()
        assert release.wait(3)
        return {"request_id": value["request_id"], "session_id": value["session_id"], "status": "settled_success"}

    client = NativeDshMuxClient(carrier, domain_handler=owner, domain_workers=1)
    first, second = [{"request_id": str(uuid.uuid4()), "session_id": "one",
                     "call_id": str(uuid.uuid4()), "raw_arguments_hash": "a" * 64} for _ in range(2)]
    try:
        carrier.inject("dsh_app_call_request", first, correlation=first["request_id"], lane="remote_unary")
        assert entered.wait(1)
        carrier.inject("dsh_app_call_request", second, correlation=second["request_id"], lane="remote_unary")
        cancel = {"contract": "genshin-native-dsh-domain-cancel-v1", "request_id": str(uuid.uuid4()),
            "target_request_id": second["request_id"], "target_kind": "dsh_app_call_request",
            "session_id": "one", "call_id": second["call_id"], "raw_arguments_hash": "a" * 64, "reason": "user_cancel"}
        carrier.inject("dsh_app_call_cancel", cancel, correlation=cancel["request_id"], lane="control")
        messages = [carrier.sent.get(timeout=1) for _ in range(2)]
        ack = next(json.loads(row["payload"]) for row in messages if row["lane"] == "control")
        assert ack["execution_state"] == "did_not_execute" and not ack["outcome_unknown"]
        error = next(json.loads(row["payload"]) for row in messages if row["lane"] == "remote_unary")
        assert error["outcome_unknown"] is False
        release.set()
        assert carrier.sent.get(timeout=1)["payload_kind"] == "dsh_app_call_result"
        client.wait_domain_idle()
        assert executed == [first["request_id"]]
    finally:
        release.set()
        client.close()


@pytest.mark.parametrize("unknown", [False, True])
def test_safe_resource_defer_requires_host_proof_and_never_retries_unknown(unknown):
    carrier = Carrier()
    calls = []

    def owner(_kind, value):
        calls.append(value["request_id"])
        if len(calls) == 1:
            raise NativeDshMuxError("app_action_serial_gate_busy", outcome_unknown=unknown,
                failure={"code": "app_action_serial_gate_busy", "message": "resource held",
                         "details": {"app_call_admission": "not_recorded"}})
        return {"request_id": value["request_id"], "session_id": value["session_id"], "status": "settled_success"}

    owner.domain_resource_snapshot = lambda _key: {"busy": False, "generation": len(calls)}
    client = NativeDshMuxClient(carrier, domain_handler=owner, domain_workers=1)
    try:
        value = {"request_id": str(uuid.uuid4()), "session_id": "one"}
        carrier.inject("dsh_app_call_request", value, correlation=value["request_id"], lane="remote_unary")
        response = carrier.sent.get(timeout=2)
        body = json.loads(response["payload"])
        client.wait_domain_idle()
        if unknown:
            assert response["payload_kind"] == "dsh_app_call_error"
            assert body["outcome_unknown"] is True and len(calls) == 1
        else:
            assert body["status"] == "settled_success" and len(calls) == 2
    finally:
        client.close()


def test_busy_without_resource_progress_returns_diagnostic_instead_of_spinning():
    carrier, calls = Carrier(), []

    def owner(_kind, _value):
        calls.append(1)
        raise NativeDshMuxError("app_action_serial_gate_busy", failure={"code": "busy",
            "message": "busy", "details": {"app_call_admission": "not_recorded"}})

    client = NativeDshMuxClient(carrier, domain_handler=owner)
    try:
        value = {"request_id": str(uuid.uuid4()), "session_id": "one"}
        carrier.inject("dsh_app_call_request", value, correlation=value["request_id"], lane="remote_unary")
        body = json.loads(carrier.sent.get(timeout=2)["payload"])
        client.wait_domain_idle()
        assert body["code"] == "app_resource_progress_unconfirmed"
        assert body["outcome_unknown"] is False and calls == [1]
    finally:
        client.close()


@pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
                    reason="explicit Windows private-pipe DSH qualification")
def test_real_python_shell_host_node_dsh_remote_chain(tmp_path):
    node = Path(os.environ["GENSHIN_NATIVE_NODE"])
    audit_node_executable(node)
    identity = {"connectionGeneration": 1, "connectionId": "dsh-mux-python",
                "runtimeIncarnation": "dsh-mux-runtime"}
    with NativePipeServer(io_timeout=10.0) as server:
        process = subprocess.Popen(
            [str(node), str(ROOT / "agent-runtime/src/native-dsh-mux-pipe-fixture.mjs"),
             server.name, json.dumps(identity)], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        client = None
        try:
            connection = server.accept(timeout=10.0)
            keywords = {"connection_generation": 1, "connection_id": identity["connectionId"],
                        "runtime_incarnation": identity["runtimeIncarnation"]}
            carrier = NativeMuxPipeCarrier(connection,
                send_queue=NativeMuxSendQueue(sender_role="host", **keywords),
                receiver=NativeMuxReceiver(sender_role="runtime", **keywords))
            client = NativeDshMuxClient(carrier)
            shell = NativeDshShellHost(client, boot=lambda: {"injections": []}, bundle=lambda _: None)
            host = GuiApi(tmp_path, dsh_shell_factory=lambda _: shell)
            assert host.dsh_client_boot() == {"injections": []}
            value = "冰" * (1024 * 1024)
            echoed = host.dsh_client_rpc({"request_id": str(uuid.uuid4()), "channel": "/api",
                                         "endpoint": "probe/echo", "payload": {"args": {"value": value}}})
            assert echoed == {"ok": True, "value": {"echoed": value}}
            stream = str(uuid.uuid4())
            host.dsh_client_stream_open({"stream_id": stream, "channel": "/api",
                "endpoint": "probe/count", "payload": {"args": {"limit": 2}}, "has_uplink": False})
            first = host.dsh_client_stream_read({"stream_id": stream, "cursor": 0})
            assert first == {"items": [{"index": 0}], "cursor": 1, "done": False}
            assert host.dsh_client_stream_read({"stream_id": stream, "cursor": 0}) == first
            assert host.dsh_client_stream_read({"stream_id": stream, "cursor": 1})["items"] == [{"index": 1}]
            assert host.dsh_client_stream_read({"stream_id": stream, "cursor": 2})["done"]
            host.dsh_client_stream_cancel({"stream_id": stream})
            host.dsh_client_stream_cancel({"stream_id": stream})
            # The public DSH event generation crosses the same Host projection.
            event_stream = client.open_stream("$events", {"args": {}})
            assert client.stream_next(event_stream)["value"]["type"] == "ready"
            client.cancel_stream(event_stream)
            # Two independent streams share one physical reader; duplex
            # uplink is ACKed by DSH consumption, never by local enqueue.
            duplex = client.open_stream("probe/duplex", {"args": {}}, has_uplink=True)
            count = client.open_stream("probe/count", {"args": {"limit": 1}})
            with ThreadPoolExecutor() as pool:
                reading = pool.submit(client.stream_next, duplex)
                sending = pool.submit(client.stream_send, duplex, {"ice": 1})
                assert client.stream_next(count)["value"] == {"index": 0}
                assert reading.result(timeout=3)["value"] == {"echoed": {"ice": 1}}
                assert sending.result(timeout=3) >= 2
            client.stream_send(duplex, done=True)
            assert client.stream_next(duplex)["done"]
            client.release_stream(duplex)
            client.release_stream(count)
            client._send("control", "fixture_shutdown", b"{}", correlation=str(uuid.uuid4()))
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stderr
            report = json.loads(stdout)
            assert report["status"] == "passed"
            assert report["metrics"]["countItems"] == 3
            assert report["metrics"]["uplinkItems"] == 1
        finally:
            if client is not None:
                client.close()
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)


def test_gui_shell_projection_is_lazy_and_never_falls_back_to_v2(tmp_path, monkeypatch):
    calls = []
    api = GuiApi(tmp_path, dsh_shell_factory=lambda root: calls.append(root) or SimpleNamespace(
        dsh_client_boot=lambda: {"injections": []}, close=lambda: calls.append("closed")))
    assert calls == []
    assert api.dsh_client_boot() == {"injections": []}
    assert api.dsh_client_boot() == {"injections": []}
    assert calls == [tmp_path]
    api._close_dsh_shell()
    with pytest.raises(NativeDshMuxError, match="dsh_shell_closed"):
        api.dsh_client_boot()
    assert calls == [tmp_path, "closed"]
    from genshin_app.agent_chat import native_dsh_production

    def unavailable_factory(*args, **kwargs):
        calls.append("production-factory")
        raise NativeDshMuxError("default_factory_unready")

    monkeypatch.setattr(native_dsh_production, "create_native_dsh_application", unavailable_factory)
    ordinary_api = GuiApi(tmp_path)
    assert calls == [tmp_path, "closed"]
    with pytest.raises(NativeDshMuxError, match="default_factory_unready"):
        ordinary_api.dsh_client_boot()
    assert calls[-1] == "production-factory"
    assert ordinary_api._agent_manager is None


@pytest.mark.skipif(os.name != "nt" or os.environ.get("GENSHIN_NATIVE_DSH_MUX_OS_SMOKE") != "1",
                    reason="explicit Windows inherited mux launch qualification")
def test_inherited_mux_bootstrap_authenticates_before_gui_remote(tmp_path):
    process = NativeDshMuxProcess(node=Path(os.environ["GENSHIN_NATIVE_NODE"]),
        entry=ROOT / "agent-runtime/src/native-dsh-mux-pipe-fixture.mjs", working=ROOT)
    try:
        shell = NativeDshShellHost(process.client, boot=lambda: {"injections": []}, bundle=lambda _: None)
        gui = GuiApi(tmp_path, dsh_shell_factory=lambda _: shell)
        result = gui.dsh_client_rpc({"request_id": str(uuid.uuid4()), "channel": "/api",
            "endpoint": "probe/echo", "payload": {"args": {"value": "inherited-authenticated"}}})
        assert result == {"ok": True, "value": {"echoed": "inherited-authenticated"}}
        process.client._send("control", "fixture_shutdown", b"{}", correlation=str(uuid.uuid4()))
        assert process._process.wait(timeout=5) == 0
    finally:
        process.close()
