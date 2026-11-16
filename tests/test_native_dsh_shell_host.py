from __future__ import annotations

import base64
import hashlib
import json
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import pytest

from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_shell_host import (
    RESOURCE_CHUNK_BYTES,
    NativeDshShellHost,
)


class Client:
    def __init__(self):
        self.fetches = []
        self.opens = []
        self.sends = []
        self.releases = []
        self.cancelled_requests = []
        self.reads = 0
        self.values = deque()
        self.closed = False

    def fetch(self, metadata, body, *, request_id):
        self.fetches.append((metadata, json.loads(body)))
        return {"status": 200}, json.dumps({"type": "server-response", "rpcId": request_id,
                                            "result": {"ok": True, "value": "done"}}).encode()

    def cancel_fetch(self, identity):
        self.cancelled_requests.append(identity)

    def open_stream(self, endpoint, payload, *, has_uplink, stream_id):
        self.opens.append((stream_id, endpoint, payload, has_uplink))

    def stream_next(self, identity):
        self.reads += 1
        return self.values.popleft()

    def stream_send(self, identity, value=None, *, done=False):
        self.sends.append((identity, value, done))

    def release_stream(self, identity):
        self.releases.append(identity)

    def close(self):
        self.closed = True


def host_fixture(**kwargs):
    client = Client()
    body = b"window.__dsh = 'ice';"
    host = NativeDshShellHost(client, boot=kwargs.get("boot", lambda: {"injections": []}),
                             bundle=kwargs.get("bundle", lambda _url: {
                                 "status": 200, "contentType": "text/javascript", "body": body,
                             }))
    return host, client


def rpc_request(identity=None):
    return {"request_id": identity or str(uuid.uuid4()), "channel": "/api",
            "endpoint": "session/read", "payload": {"args": {"sessionId": "session-a"}}}


def stream_request(identity=None, *, uplink=False):
    return {"stream_id": identity or str(uuid.uuid4()), "channel": "/api",
            "endpoint": "session/subscribe", "payload": {"args": {}}, "has_uplink": uplink}


def test_unary_preserves_dsh_envelope_and_rejects_request_reuse():
    host, client = host_fixture()
    request = rpc_request()
    assert host.dsh_client_rpc(request) == {"ok": True, "value": "done"}
    metadata, body = client.fetches[0]
    assert metadata["path"] == "/api/session/read"
    assert body["rpcId"] == request["request_id"]
    assert body["payload"] == request["payload"]
    with pytest.raises(NativeDshMuxError, match="request_duplicate"):
        host.dsh_client_rpc(request)
    assert len(client.fetches) == 1


def test_cancel_before_request_and_open_prevents_late_dispatch():
    host, client = host_fixture()
    request, stream = rpc_request(), stream_request()
    for _ in range(2):
        assert host.dsh_client_request_cancel(request) == {"cancelled": True}
        assert host.dsh_client_stream_cancel(stream) == {"cancelled": True}
    with pytest.raises(NativeDshMuxError, match="request_cancelled"):
        host.dsh_client_rpc(request)
    with pytest.raises(NativeDshMuxError, match="stream_cancelled"):
        host.dsh_client_stream_open(stream)
    assert client.fetches == client.opens == []
    assert client.cancelled_requests == [request["request_id"]]
    assert client.releases == [stream["stream_id"]]


def test_structured_dsh_fetch_failure_reaches_the_browser_without_generic_replacement():
    host, client = host_fixture()
    failure = {"code": "remote-access/denied", "message": "Denied", "details": {"scope": "session/read"}}

    def fetch(*_args, **_kwargs):
        raise NativeDshMuxError(failure["code"], failure=failure)

    client.fetch = fetch
    response = host.dsh_client_rpc(rpc_request())
    assert response == {"ok": False, "error": failure}
    response["error"]["details"]["scope"] = "tampered"
    assert failure["details"]["scope"] == "session/read"


def test_cancel_during_stream_open_does_not_publish_late_success():
    host, client = host_fixture()
    request = stream_request()
    entered, release = threading.Event(), threading.Event()

    def open_stream(*_args, **_kwargs):
        entered.set()
        assert release.wait(2)

    client.open_stream = open_stream
    with ThreadPoolExecutor() as pool:
        opening = pool.submit(host.dsh_client_stream_open, request)
        try:
            assert entered.wait(1)
            assert host.dsh_client_stream_cancel(request) == {"cancelled": True}
        finally:
            release.set()
        with pytest.raises(NativeDshMuxError, match="stream_cancelled"):
            opening.result(timeout=1)
    assert host.dsh_client_stream_read({**request, "cursor": 0})["error"]["code"] == "gateway/cancelled"
    assert client.releases == [request["stream_id"]]


def test_cancel_during_unary_drops_late_result():
    host, client = host_fixture()
    request = rpc_request()
    entered, release = threading.Event(), threading.Event()
    fetch = client.fetch

    def paused_fetch(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return fetch(*args, **kwargs)

    client.fetch = paused_fetch
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(host.dsh_client_rpc, request)
        try:
            assert entered.wait(1)
            host.dsh_client_request_cancel(request)
        finally:
            release.set()
        with pytest.raises(NativeDshMuxError, match="request_cancelled"):
            pending.result(timeout=1)


def test_stream_read_replays_only_last_batch_without_extra_credit_or_mutable_alias():
    host, client = host_fixture()
    request = stream_request()
    host.dsh_client_stream_open(request)
    client.values.extend([{"done": False, "value": {"delta": "a"}}, {"done": False, "value": "b"}])
    first = host.dsh_client_stream_read({**request, "cursor": 0})
    first["items"][0]["delta"] = "tampered"
    assert host.dsh_client_stream_read({**request, "cursor": 0}) == {
        "items": [{"delta": "a"}], "cursor": 1, "done": False,
    }
    assert client.reads == 1
    assert host.dsh_client_stream_read({**request, "cursor": 1})["items"] == ["b"]
    with pytest.raises(NativeDshMuxError, match="cursor_stale"):
        host.dsh_client_stream_read({**request, "cursor": 0})
    assert client.reads == 2


def test_terminal_error_is_preserved_and_replayed_without_advance():
    host, client = host_fixture()
    request = stream_request()
    host.dsh_client_stream_open(request)
    error = {"code": "provider/closed", "message": "Stream closed", "details": {}}
    client.values.extend([{"done": False, "value": "last"}, {"done": True, "error": error}])
    assert host.dsh_client_stream_read({**request, "cursor": 0})["items"] == ["last"]
    terminal = host.dsh_client_stream_read({**request, "cursor": 1})
    assert terminal == {"items": [], "cursor": 2, "done": True, "error": error}
    assert host.dsh_client_stream_read({**request, "cursor": 1}) == terminal
    with pytest.raises(NativeDshMuxError, match="cursor_stale"):
        host.dsh_client_stream_read({**request, "cursor": 2})
    assert client.reads == 2


def test_cancel_wakes_pending_read_without_waiting_for_read_lock():
    host, client = host_fixture()
    request = stream_request()
    host.dsh_client_stream_open(request)
    entered, cancelled = threading.Event(), threading.Event()

    def read(_identity):
        entered.set()
        assert cancelled.wait(2)
        raise NativeDshMuxError("dsh_mux_stream_cancelled")

    def cancel(identity):
        client.releases.append(identity)
        cancelled.set()

    client.stream_next, client.release_stream = read, cancel
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(host.dsh_client_stream_read, {**request, "cursor": 0})
        assert entered.wait(1)
        assert host.dsh_client_stream_cancel(request) == {"cancelled": True}
        terminal = pending.result(timeout=1)
        assert terminal == {"items": [], "cursor": 1, "done": True,
                            "error": {"code": "gateway/cancelled", "message": "窗口已停止读取此流。", "details": {}}}
    assert client.releases == [request["stream_id"]]


def test_retired_reader_is_cancelled_but_live_transport_failure_is_not_hidden():
    host, client = host_fixture()
    request = stream_request()
    host.dsh_client_stream_open(request)
    client.stream_next = lambda _identity: (_ for _ in ()).throw(NativeDshMuxError("dsh_mux_stream_cancelled"))
    with pytest.raises(NativeDshMuxError, match="dsh_mux_stream_cancelled"):
        host.dsh_client_stream_read({**request, "cursor": 0})
    host.retire_stream_reads()
    assert host.dsh_client_stream_read({**request, "cursor": 0})["error"]["code"] == "gateway/cancelled"
    host.close()
    assert host.dsh_client_stream_read({**request, "cursor": 0})["error"]["code"] == "gateway/cancelled"
    with pytest.raises(NativeDshMuxError, match="dsh_shell_closed"):
        host.dsh_client_stream_read({**stream_request(), "cursor": 0})


def test_retirement_does_not_mask_an_unrelated_pending_read_error():
    host, client = host_fixture()
    request = stream_request()
    host.dsh_client_stream_open(request)

    def failed(_identity):
        host.retire_stream_reads()
        raise NativeDshMuxError("dsh_mux_frame_invalid")

    client.stream_next = failed
    with pytest.raises(NativeDshMuxError, match="dsh_mux_frame_invalid"):
        host.dsh_client_stream_read({**request, "cursor": 0})


@pytest.mark.parametrize("retirement", ["live", "cancel_one", "owner_shutdown"])
def test_pipe_eof_is_normal_only_after_explicit_owner_retirement(retirement):
    host, client = host_fixture()
    request = stream_request()
    host.dsh_client_stream_open(request)

    def ending(_identity):
        if retirement == "owner_shutdown":
            host.retire_stream_reads()
        elif retirement == "cancel_one":
            host.dsh_client_stream_cancel(request)
        raise NativeDshMuxError("dsh_mux_connection_lost")

    client.stream_next = ending
    if retirement == "owner_shutdown":
        result = host.dsh_client_stream_read({**request, "cursor": 0})
        assert result["done"] and result["error"]["code"] == "gateway/cancelled"
    else:
        with pytest.raises(NativeDshMuxError, match="dsh_mux_connection_lost"):
            host.dsh_client_stream_read({**request, "cursor": 0})


def test_first_uplink_sequence_is_zero_and_eos_closes_only_uplink():
    host, client = host_fixture()
    request = stream_request(uplink=True)
    host.dsh_client_stream_open(request)
    assert host.dsh_client_stream_send({**request, "sequence": 0, "items": ["a"], "done": False}) == {"sequence": 0}
    with pytest.raises(NativeDshMuxError, match="uplink_sequence"):
        host.dsh_client_stream_send({**request, "sequence": 0, "items": ["a"], "done": False})
    assert host.dsh_client_stream_send({**request, "sequence": 1, "items": ["b"], "done": True}) == {"sequence": 1}
    with pytest.raises(NativeDshMuxError, match="uplink_closed"):
        host.dsh_client_stream_send({**request, "sequence": 2, "items": [], "done": True})
    assert client.sends == [(request["stream_id"], "a", False),
                            (request["stream_id"], "b", False),
                            (request["stream_id"], None, True)]
    client.values.append({"done": False, "value": "response"})
    assert host.dsh_client_stream_read({**request, "cursor": 0})["items"] == ["response"]


def test_downlink_only_stream_does_not_accept_uplink():
    host, client = host_fixture()
    request = stream_request()
    host.dsh_client_stream_open(request)
    with pytest.raises(NativeDshMuxError, match="uplink_closed"):
        host.dsh_client_stream_send({**request, "sequence": 0, "items": ["bad"], "done": False})
    assert client.sends == []


def test_partial_uplink_failure_cannot_replay_delivered_items():
    host, client = host_fixture()
    request = stream_request(uplink=True)
    host.dsh_client_stream_open(request)

    def send(identity, value=None, *, done=False):
        client.sends.append((identity, value, done))
        if value == "b":
            raise NativeDshMuxError("dsh_mux_connection_lost")

    client.stream_send = send
    batch = {**request, "sequence": 0, "items": ["a", "b", "c"], "done": False}
    with pytest.raises(NativeDshMuxError, match="connection_lost"):
        host.dsh_client_stream_send(batch)
    with pytest.raises(NativeDshMuxError, match="uplink_closed"):
        host.dsh_client_stream_send(batch)
    assert [row[1] for row in client.sends] == ["a", "b"]


def test_resource_paging_is_immutable_replayable_and_release_is_idempotent():
    host, _client = host_fixture()
    metadata = host.dsh_client_bundle({"url": "/plugins/a.js?rev=1"})
    resource = metadata["resource"]
    data = b"window.__dsh = 'ice';"
    assert resource["byte_length"] == len(data)
    assert resource["sha256"] == hashlib.sha256(data).hexdigest()
    identity = resource["resource_id"]
    first = host.dsh_client_resource_read({"resource_id": identity, "offset": 0, "length": 7})
    assert base64.b64decode(first["body_base64"]) == data[:7]
    assert host.dsh_client_resource_read({"resource_id": identity, "offset": 0, "length": 7}) == first
    last = host.dsh_client_resource_read({"resource_id": identity, "offset": 7, "length": 100})
    assert base64.b64decode(last["body_base64"]) == data[7:]
    assert last["eof"] is True
    assert host.dsh_client_resource_release({"resource_id": identity}) == {"released": True}
    assert host.dsh_client_resource_release({"resource_id": identity}) == {"released": False}
    with pytest.raises(NativeDshMuxError, match="resource_unknown"):
        host.dsh_client_resource_read({"resource_id": identity, "offset": 0, "length": 7})


@pytest.mark.parametrize("offset,length", [(-1, 1), (True, 1), (0, 0), (0, RESOURCE_CHUNK_BYTES + 1), (0, True)])
def test_resource_invalid_ranges_rejected(offset, length):
    host, _client = host_fixture()
    identity = host.dsh_client_bundle({"url": "/plugins/a.js?rev=1"})["resource"]["resource_id"]
    with pytest.raises(NativeDshMuxError, match="resource_range"):
        host.dsh_client_resource_read({"resource_id": identity, "offset": offset, "length": length})


def test_close_during_bundle_publication_does_not_leave_a_resource():
    entered, release = threading.Event(), threading.Event()

    def bundle(_url):
        entered.set()
        assert release.wait(2)
        return {"status": 200, "contentType": "text/javascript", "body": b"x"}

    host, client = host_fixture(bundle=bundle)
    with ThreadPoolExecutor() as pool:
        pending = pool.submit(host.dsh_client_bundle, {"url": "/plugins/a.js?rev=1"})
        try:
            assert entered.wait(1)
            host.close()
        finally:
            release.set()
        with pytest.raises(NativeDshMuxError, match="shell_closed"):
            pending.result(timeout=1)
    assert host._resources == {}
    assert client.closed
    host.close()
    with pytest.raises(NativeDshMuxError, match="shell_closed"):
        host.dsh_client_boot()


@pytest.mark.parametrize("endpoint", ["../read", "/read", "read//now", "read?x", "read\x00now", "read\x7fnow"])
def test_endpoint_validation_applies_equally_to_unary_and_stream(endpoint):
    host, client = host_fixture()
    with pytest.raises(NativeDshMuxError, match="endpoint_invalid"):
        host.dsh_client_rpc({**rpc_request(), "endpoint": endpoint})
    with pytest.raises(NativeDshMuxError, match="endpoint_invalid"):
        host.dsh_client_stream_open({**stream_request(), "endpoint": endpoint})
    assert not client.fetches and not client.opens
