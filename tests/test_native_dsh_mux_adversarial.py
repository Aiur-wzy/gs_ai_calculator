from __future__ import annotations

import json
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from genshin_app.agent_chat.native_dsh_mux_client import (
    NativeDshMuxClient,
    NativeDshMuxError,
    encode_dsh_fetch_payload,
)


class Carrier:
    def __init__(self):
        self.outgoing = queue.Queue()
        self.incoming = queue.Queue()
        self.enqueue_failure = False
        self.flush_failure = False
        self.closed = threading.Event()

    def enqueue(self, **packet):
        if self.enqueue_failure:
            raise OSError("carrier unavailable")
        self.outgoing.put(packet)

    def flush_one(self):
        if self.flush_failure:
            raise OSError("partial physical write")

    def receive_one(self):
        packet = self.incoming.get(timeout=5)
        if packet is None:
            raise OSError("pipe closed")
        return SimpleNamespace(packet=packet, accept=lambda: None,
                               outcome_unknown=lambda: None, settled=False)

    def inject(self, kind, value, *, stream=None, correlation=None):
        self.incoming.put(SimpleNamespace(
            header={"payload_kind": kind, "stream_id": stream, "correlation_id": correlation},
            payload=json.dumps(value).encode(),
        ))

    def reply(self, request, body=b"ok"):
        self.incoming.put(SimpleNamespace(
            header={"payload_kind": "dsh_fetch_result", "stream_id": None,
                    "correlation_id": request["correlation_id"]},
            payload=encode_dsh_fetch_payload({"status": 200, "headers": []}, body),
        ))

    def close(self):
        self.closed.set()
        self.incoming.put(None)


METADATA = {"method": "GET", "path": "/api", "headers": []}


@pytest.mark.parametrize("kind", ["enqueue", "flush"])
def test_carrier_write_failure_wakes_rpc_and_stream_waiters(kind):
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    try:
        stream = client.open_stream("session/subscribe", {})
        carrier.outgoing.get(timeout=1)
        with ThreadPoolExecutor() as pool:
            reading = pool.submit(client.stream_next, stream)
            assert carrier.outgoing.get(timeout=1)["payload_kind"] == "dsh_stream_credit"
            fetching = pool.submit(client.fetch, METADATA)
            carrier.outgoing.get(timeout=1)
            setattr(carrier, f"{kind}_failure", True)
            with pytest.raises(NativeDshMuxError, match="write_failed"):
                client.fetch(METADATA)
            for pending in [reading, fetching]:
                with pytest.raises(NativeDshMuxError, match="write_failed"):
                    pending.result(timeout=1)
        assert carrier.closed.is_set()
        assert client.snapshot()["pending_requests"] == 0
    finally:
        client.close()


@pytest.mark.parametrize("bad_sequence", [True, "2", 0, 1, 99])
def test_unmatched_uplink_ack_cannot_release_backpressure(bad_sequence):
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    try:
        stream = client.open_stream("session/upload", {}, has_uplink=True)
        carrier.outgoing.get(timeout=1)
        with ThreadPoolExecutor() as pool:
            sending = pool.submit(client.stream_send, stream, "value")
            packet = carrier.outgoing.get(timeout=1)
            assert packet["stream_seq"] == 2
            carrier.inject("dsh_stream_ack", {"sequence": bad_sequence}, stream=stream)
            with pytest.raises(NativeDshMuxError, match="stream_ack_invalid"):
                sending.result(timeout=1)
        assert carrier.closed.is_set()
    finally:
        client.close()


def test_ack_matches_uplink_sequence_even_when_a_read_credit_was_sent_later():
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    try:
        stream = client.open_stream("session/upload", {}, has_uplink=True)
        carrier.outgoing.get(timeout=1)
        with ThreadPoolExecutor() as pool:
            sending = pool.submit(client.stream_send, stream, "value")
            item = carrier.outgoing.get(timeout=1)
            reading = pool.submit(client.stream_next, stream)
            credit = carrier.outgoing.get(timeout=1)
            assert item["stream_seq"] == 2 and credit["stream_seq"] == 3
            carrier.inject("dsh_stream_ack", {"sequence": 2}, stream=stream)
            assert sending.result(timeout=1) == 2
            carrier.inject("dsh_stream_item", {"value": "reply"}, stream=stream)
            assert reading.result(timeout=1) == {"done": False, "value": "reply"}
        assert not carrier.closed.is_set()
    finally:
        client.close()


def test_remote_failure_preserves_code_and_details_and_does_not_poison_other_rpc():
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    failure = {"code": "remote-access/denied", "message": "Owner permission is required", "details": {"scope": "session/read"}}
    try:
        with ThreadPoolExecutor() as pool:
            denied = pool.submit(client.fetch, METADATA)
            packet = carrier.outgoing.get(timeout=1)
            carrier.inject("dsh_fetch_error", failure, correlation=packet["correlation_id"])
            with pytest.raises(NativeDshMuxError) as caught:
                denied.result(timeout=1)
            assert caught.value.code == failure["code"]
            assert caught.value.failure == failure
            following = pool.submit(client.fetch, METADATA)
            carrier.reply(carrier.outgoing.get(timeout=1))
            assert following.result(timeout=1)[1] == b"ok"
        assert not carrier.closed.is_set()
    finally:
        client.close()


def test_stream_error_is_local_and_does_not_discard_a_buffered_final_item():
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    error = {"code": "provider/closed", "message": "Stream failed", "details": {}}
    try:
        stream = client.open_stream("session/subscribe", {})
        carrier.outgoing.get(timeout=1)
        with ThreadPoolExecutor() as pool:
            reading = pool.submit(client.stream_next, stream)
            carrier.outgoing.get(timeout=1)
            carrier.inject("dsh_stream_item", {"value": "final"}, stream=stream)
            carrier.inject("dsh_stream_error", error, stream=stream)
            assert reading.result(timeout=1) == {"done": False, "value": "final"}
            assert client.stream_next(stream) == {"done": True, "error": error}
            following = pool.submit(client.fetch, METADATA)
            carrier.reply(carrier.outgoing.get(timeout=1))
            assert following.result(timeout=1)[1] == b"ok"
        assert not carrier.closed.is_set()
    finally:
        client.close()


def test_early_cancel_and_released_stream_methods_are_local_not_key_errors():
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    try:
        early = str(uuid.uuid4())
        client.cancel_stream(early)
        with pytest.raises(NativeDshMuxError, match="stream_cancelled"):
            client.open_stream("session/subscribe", {}, stream_id=early)
        stream = client.open_stream("session/upload", {}, has_uplink=True)
        carrier.outgoing.get(timeout=1)
        client.release_stream(stream)
        assert carrier.outgoing.get(timeout=1)["payload_kind"] == "dsh_stream_cancel"
        for call in [lambda: client.stream_next(stream), lambda: client.stream_send(stream, "late")]:
            with pytest.raises(NativeDshMuxError, match="stream_cancelled"):
                call()
        assert not carrier.closed.is_set()
    finally:
        client.close()


def test_downlink_only_stream_rejects_uplink_without_poisoning_connection():
    carrier = Carrier()
    client = NativeDshMuxClient(carrier)
    try:
        stream = client.open_stream("session/subscribe", {})
        carrier.outgoing.get(timeout=1)
        with pytest.raises(NativeDshMuxError, match="uplink_closed"):
            client.stream_send(stream, "invalid")
        assert carrier.outgoing.empty()
        assert not carrier.closed.is_set()
    finally:
        client.close()
