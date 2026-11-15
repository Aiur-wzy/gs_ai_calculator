from __future__ import annotations

import io
import struct
import uuid

import pytest

from genshin_app.agent_chat.bridge_protocol import (
    MAX_FRAME_BYTES,
    encode_frame,
    make_frame,
)
from genshin_app.agent_chat.bridge_wire import (
    BridgeWireError,
    HandshakeContract,
    HeartbeatMonitor,
    LengthPrefixedBridgeIO,
    build_handshake_payload,
    validate_handshake_frame,
)


def _hash(char="a"):
    return char * 64


def _contract():
    return HandshakeContract(
        runtime_generation="dsh_v2",
        app_version="0.44.0",
        node_version="24.19.0",
        dsh_release="dsh-v0.2.1-rc.5",
        profile_version="genshin-native-v2.1",
        composition_manifest_hash=_hash("a"),
        tool_catalog_hash=_hash("b"),
        prompt_profile_hash=_hash("c"),
        heartbeat_interval_ms=5_000,
        heartbeat_timeout_ms=15_000,
        cancel_timeout_ms=10_000,
    )


def _frame(*, payload=None, kind="event", frame_type="turn.started", **optional):
    return make_frame(
        kind=kind,
        frame_type=frame_type,
        message_id=str(uuid.uuid4()),
        runtime_incarnation="runtime-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        step_id="step-1",
        epoch_id="epoch-1",
        event_seq=1,
        payload={} if payload is None else payload,
        **optional,
    )


def _error(code, call):
    with pytest.raises(BridgeWireError) as caught:
        call()
    assert caught.value.code == code
    return caught.value


class ChunkReader:
    def __init__(self, raw, chunk=3):
        self.raw = bytearray(raw)
        self.chunk = chunk

    def __call__(self, size):
        size = min(size, self.chunk, len(self.raw))
        output = bytes(self.raw[:size])
        del self.raw[:size]
        return output


class ChunkWriter:
    def __init__(self, chunk=5):
        self.raw = bytearray()
        self.chunk = chunk

    def __call__(self, raw):
        count = min(len(raw), self.chunk)
        self.raw.extend(raw[:count])
        return count


def test_length_prefixed_round_trip_supports_partial_binary_io():
    frame = _frame(payload={"state": "running"})
    writer = ChunkWriter()
    output = LengthPrefixedBridgeIO(lambda _n: b"", writer)
    assert output.write_frame(frame) == len(encode_frame(frame))
    reader = LengthPrefixedBridgeIO(ChunkReader(writer.raw), lambda _raw: None)
    assert reader.read_frame() == frame


def test_length_prefix_rejects_clean_eof_truncation_and_oversize_before_allocation():
    _error("bridge_wire_eof", LengthPrefixedBridgeIO(
        lambda _n: b"", lambda _raw: None).read_frame)
    _error("bridge_wire_truncated", LengthPrefixedBridgeIO(
        ChunkReader(b"\x00\x01"), lambda _raw: None).read_frame)
    oversized = struct.pack(">I", MAX_FRAME_BYTES + 1)
    reader = ChunkReader(oversized)
    _error("bridge_wire_length", LengthPrefixedBridgeIO(
        reader, lambda _raw: None).read_frame)
    assert reader.raw == bytearray()


def test_frame_decode_failure_is_sanitized_but_keeps_machine_cause():
    invalid = b'{"secret":"must-not-be-reflected"}'
    wire = struct.pack(">I", len(invalid)) + invalid
    error = _error("bridge_frame_invalid", LengthPrefixedBridgeIO(
        ChunkReader(wire), lambda _raw: None).read_frame)
    assert error.cause_code == "missing_frame_field"
    assert "secret" not in str(error)


def test_write_failure_and_close_are_fail_closed_and_idempotent():
    frame = _frame()
    _error("bridge_wire_write_failed", lambda: LengthPrefixedBridgeIO(
        lambda _n: b"", lambda _raw: 0).write_frame(frame))
    closed = []
    bridge = LengthPrefixedBridgeIO(
        lambda _n: b"", lambda raw: len(raw), close=lambda: closed.append(True))
    bridge.close()
    bridge.close()
    assert closed == [True] and bridge.closed
    _error("bridge_wire_closed", lambda: bridge.write_frame(frame))
    _error("bridge_wire_closed", bridge.read_frame)


def test_binary_stream_adapter_flushes_and_closes_both_streams():
    frame = _frame(payload={"state": "ready"})
    writer = io.BytesIO()
    bridge = LengthPrefixedBridgeIO.from_streams(io.BytesIO(), writer)
    bridge.write_frame(frame)
    packet = writer.getvalue()
    reader = LengthPrefixedBridgeIO.from_streams(io.BytesIO(packet), io.BytesIO())
    assert reader.read_frame() == frame
    bridge.close()
    reader.close()


def test_binary_stream_adapter_attempts_reader_close_when_writer_close_fails():
    closed = []

    class Reader(io.BytesIO):
        def close(self):
            closed.append("reader")
            super().close()

    class Writer(io.BytesIO):
        def close(self):
            closed.append("writer")
            raise OSError("writer close failed")

    bridge = LengthPrefixedBridgeIO.from_streams(Reader(), Writer())
    _error("bridge_wire_close_failed", bridge.close)
    assert closed == ["writer", "reader"]


def _handshake_frame(payload, *, response=True):
    return _frame(
        payload=payload,
        kind="response" if response else "request",
        frame_type="handshake.response" if response else "handshake.request",
        request_id="request-1",
    )


def test_handshake_accepts_exact_contract_and_explicit_capability_subset():
    nonce = "n" * 32
    payload = build_handshake_payload(
        _contract(), nonce=nonce, role="sidecar",
        capabilities=["bridge.events", "credential.memory", "session.ephemeral"],
    )
    frame = _handshake_frame(payload)
    checked = validate_handshake_frame(
        frame,
        contract=_contract(),
        nonce=nonce,
        expected_role="sidecar",
        expected_request_id="request-1",
        required_capabilities=frozenset({"bridge.events", "session.ephemeral"}),
        allowed_capabilities=frozenset(payload["capabilities"]),
    )
    assert checked == frame
    # This value is the only runtime incarnation the connection may bind to
    # when it creates per-turn projectors.
    assert checked["runtime_incarnation"] == "runtime-1"


@pytest.mark.parametrize("mutation,code", [
    (lambda payload: payload.update(nonce="x" * 32), "handshake_nonce_mismatch"),
    (lambda payload: payload.update(role="app"), "handshake_role_mismatch"),
    (lambda payload: payload.update(dsh_release="dsh-v9"), "handshake_contract_mismatch"),
    (lambda payload: payload["limits"].update(max_frame_bytes=100),
     "handshake_contract_mismatch"),
    (lambda payload: payload.update(unexpected=True), "handshake_payload_shape"),
])
def test_handshake_rejects_mismatches_without_reflecting_values(mutation, code):
    nonce = "n" * 32
    payload = build_handshake_payload(
        _contract(), nonce=nonce, role="sidecar", capabilities=["bridge.events"],
    )
    mutation(payload)
    frame = _handshake_frame(payload)
    error = _error(code, lambda: validate_handshake_frame(
        frame, contract=_contract(), nonce=nonce, expected_role="sidecar",
        expected_request_id="request-1",
        allowed_capabilities=frozenset({"bridge.events"}),
    ))
    assert nonce not in str(error) and "dsh-v9" not in str(error)


def test_handshake_capabilities_are_sorted_unique_bounded_and_authorized():
    contract = _contract()
    _error("handshake_invalid_capabilities", lambda: build_handshake_payload(
        contract, nonce="n" * 32, role="sidecar", capabilities=["z", "a"],
    ))
    payload = build_handshake_payload(
        contract, nonce="n" * 32, role="sidecar", capabilities=["bridge.events"],
    )
    frame = _handshake_frame(payload)
    _error("handshake_missing_capability", lambda: validate_handshake_frame(
        frame, contract=contract, nonce="n" * 32, expected_role="sidecar",
        expected_request_id="request-1",
        required_capabilities=frozenset({"credential.memory"}),
        allowed_capabilities=frozenset({"bridge.events", "credential.memory"}),
    ))
    _error("handshake_extra_capability", lambda: validate_handshake_frame(
        frame, contract=contract, nonce="n" * 32, expected_role="sidecar",
        expected_request_id="request-1",
        allowed_capabilities=frozenset(),
    ))


def test_handshake_rejects_wrong_request_and_requires_explicit_allowlist():
    contract = _contract()
    payload = build_handshake_payload(
        contract, nonce="n" * 32, role="sidecar", capabilities=["bridge.events"],
    )
    frame = _handshake_frame(payload)
    _error("handshake_request_mismatch", lambda: validate_handshake_frame(
        frame,
        contract=contract,
        nonce="n" * 32,
        expected_role="sidecar",
        expected_request_id="request-other",
        allowed_capabilities=frozenset({"bridge.events"}),
    ))
    with pytest.raises(TypeError):
        validate_handshake_frame(
            frame,
            contract=contract,
            nonce="n" * 32,
            expected_role="sidecar",
            expected_request_id="request-1",
        )


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


def test_heartbeat_tracks_due_ping_exact_pong_activity_and_timeout():
    clock = Clock()
    heartbeat = HeartbeatMonitor(interval_seconds=5, timeout_seconds=10, clock=clock)
    assert not heartbeat.ping_due()
    clock.value = 5
    assert heartbeat.ping_due()
    heartbeat.begin_ping("ping-1")
    clock.value = 8
    assert not heartbeat.ping_due()
    _error("heartbeat_token_mismatch", lambda: heartbeat.acknowledge("ping-2"))
    heartbeat.acknowledge("ping-1")
    clock.value = 13
    assert heartbeat.ping_due()
    heartbeat.begin_ping("ping-2")
    heartbeat.record_activity()
    clock.value = 23
    _error("heartbeat_timeout", heartbeat.check_timeout)
    assert heartbeat.state == "timed_out"
    _error("heartbeat_timeout", heartbeat.ping_due)


def test_heartbeat_close_clears_pending_and_is_idempotent():
    clock = Clock()
    heartbeat = HeartbeatMonitor(interval_seconds=1, timeout_seconds=2, clock=clock)
    heartbeat.begin_ping("ping-1")
    heartbeat.close()
    heartbeat.close()
    assert heartbeat.state == "closed"
    assert heartbeat.pending_token is None and heartbeat.deadline_at is None
    _error("heartbeat_closed", heartbeat.record_activity)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_heartbeat_rejects_non_finite_clock_values(value):
    heartbeat = HeartbeatMonitor(
        interval_seconds=1, timeout_seconds=2, clock=lambda: value,
    )
    _error("heartbeat_clock_invalid", heartbeat.ping_due)


def test_handshake_contract_rejects_unsafe_limits_and_timing():
    values = _contract().__dict__.copy()
    values["heartbeat_timeout_ms"] = values["heartbeat_interval_ms"]
    _error("handshake_invalid_limit", lambda: HandshakeContract(**values))
