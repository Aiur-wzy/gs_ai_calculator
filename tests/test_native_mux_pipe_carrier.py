from __future__ import annotations

import struct
import threading
import time
from contextlib import contextmanager

import pytest

from genshin_app.agent_chat.native_mux_pipe_carrier import (
    DEFAULT_MUX_PIPE_FRAME_TIMEOUT_SECONDS,
    PRODUCTION_OWNER,
    NativeMuxPipeCarrier,
    NativeMuxPipeCarrierError,
)
from genshin_app.agent_chat.native_mux_protocol import (
    MAX_MUX_BODY_BYTES,
    decode_mux_packet,
)
from genshin_app.agent_chat.native_pipe_multiplexer import (
    NativeMuxReceiver,
    NativeMuxSendQueue,
)

_UINT32 = struct.Struct(">I")


class _PipeFailure(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _ShortByteChannel:
    def __init__(
        self,
        inbound: bytes = b"",
        *,
        read_pattern: tuple[int, ...] = (),
        write_pattern: tuple[int, ...] = (),
        fail_write_call: int | None = None,
        write_delay: float = 0.0,
    ) -> None:
        self._lock = threading.Lock()
        self.inbound = bytearray(inbound)
        self.outbound = bytearray()
        self.read_pattern = read_pattern
        self.write_pattern = write_pattern
        self.fail_write_call = fail_write_call
        self.write_delay = write_delay
        self.read_calls = 0
        self.write_calls = 0
        self.closed = False
        self.read_deadlines = []
        self.write_deadlines = []
        self.observed_read_deadlines = []
        self.observed_write_deadlines = []
        self._read_deadline_provider = None
        self._write_deadline_provider = None

    def read(self, size: int) -> bytes:
        if self._read_deadline_provider is not None:
            deadline = self._read_deadline_provider()
            self.observed_read_deadlines.append(deadline)
            if deadline <= time.monotonic():
                raise _PipeFailure("pipe_read_timeout")
        with self._lock:
            self.read_calls += 1
            if self.closed or not self.inbound:
                return b""
            limit = size
            if self.read_pattern:
                limit = min(
                    limit,
                    self.read_pattern[(self.read_calls - 1) % len(self.read_pattern)],
                )
            count = min(limit, len(self.inbound))
            value = bytes(self.inbound[:count])
            del self.inbound[:count]
            return value

    def write(self, value: bytes) -> int:
        if self._write_deadline_provider is not None:
            deadline = self._write_deadline_provider()
            self.observed_write_deadlines.append(deadline)
            if deadline <= time.monotonic():
                raise _PipeFailure("pipe_write_timeout")
        with self._lock:
            self.write_calls += 1
            if self.closed:
                raise OSError("closed")
            if self.fail_write_call == self.write_calls:
                raise OSError("injected")
            count = len(value)
            if self.write_pattern:
                count = min(
                    count,
                    self.write_pattern[(self.write_calls - 1) % len(self.write_pattern)],
                )
            if self.write_delay:
                time.sleep(self.write_delay)
            self.outbound.extend(value[:count])
            return count

    @contextmanager
    def read_deadline(self, provider):
        self.read_deadlines.append(provider)
        previous = self._read_deadline_provider
        self._read_deadline_provider = provider
        try:
            yield
        finally:
            self._read_deadline_provider = previous

    @contextmanager
    def write_deadline(self, provider):
        self.write_deadlines.append(provider)
        previous = self._write_deadline_provider
        self._write_deadline_provider = provider
        try:
            yield
        finally:
            self._write_deadline_provider = previous

    def close(self) -> None:
        with self._lock:
            self.closed = True


class _BlockingReadChannel(_ShortByteChannel):
    def __init__(self) -> None:
        super().__init__()
        self.read_started = threading.Event()
        self.read_released = threading.Event()

    def read(self, size: int) -> bytes:
        self.read_started.set()
        assert self.read_released.wait(timeout=2)
        with self._lock:
            self.read_calls += 1
            return b""

    def close(self) -> None:
        with self._lock:
            self.closed = True
        self.read_released.set()


class _IdleTimeoutThenDataChannel(_ShortByteChannel):
    def __init__(self, inbound: bytes, *, timeout_count: int) -> None:
        super().__init__(inbound)
        self.timeout_count = timeout_count

    def read(self, size: int) -> bytes:
        if self.timeout_count:
            self.timeout_count -= 1
            raise _PipeFailure("pipe_read_timeout")
        return super().read(size)


def _queue(role: str) -> NativeMuxSendQueue:
    return NativeMuxSendQueue(
        sender_role=role,
        connection_generation=1,
        connection_id="connection-1",
        runtime_incarnation="runtime-1",
    )


def _receiver(role: str) -> NativeMuxReceiver:
    return NativeMuxReceiver(
        sender_role=role,
        connection_generation=1,
        connection_id="connection-1",
        runtime_incarnation="runtime-1",
    )


def _carrier(channel: _ShortByteChannel, *, chunk_bytes: int = 17) -> NativeMuxPipeCarrier:
    return NativeMuxPipeCarrier(
        channel,
        send_queue=_queue("host"),
        receiver=_receiver("runtime"),
        chunk_bytes=chunk_bytes,
    )


def _runtime_wire(message_id: str, payload: bytes) -> bytes:
    queue = _queue("runtime")
    queue.enqueue(
        lane="legacy_turn",
        payload_kind="legacy_frame",
        payload=payload,
        message_id=message_id,
    )
    prepared = queue.prepare_next()
    assert prepared is not None
    queue.begin_write()
    queue.settle_write("sent")
    return prepared.wire


def _split_wires(raw: bytes) -> list[bytes]:
    wires = []
    offset = 0
    while offset < len(raw):
        assert len(raw) - offset >= _UINT32.size
        (body_length,) = _UINT32.unpack_from(raw, offset)
        end = offset + _UINT32.size + body_length
        assert end <= len(raw)
        wires.append(raw[offset:end])
        offset = end
    return wires


def _carrier_error(code: str, operation) -> NativeMuxPipeCarrierError:
    with pytest.raises(NativeMuxPipeCarrierError) as caught:
        operation()
    assert caught.value.code == code
    return caught.value


def test_qualification_carrier_is_explicitly_not_the_production_owner():
    assert PRODUCTION_OWNER is False
    carrier = _carrier(_ShortByteChannel())
    assert carrier.diagnostic_snapshot()["production_owner"] is False
    carrier.close()


def test_short_reads_preserve_one_wire_and_retry_delivery_without_pipe_reread():
    wire = _runtime_wire("runtime-short-read", b"payload" * 40)
    channel = _ShortByteChannel(wire, read_pattern=(1, 2, 3, 5))
    carrier = _carrier(channel, chunk_bytes=11)
    deadline_value = time.monotonic() + 60.0
    deadline_calls = 0

    def deadline():
        nonlocal deadline_calls
        deadline_calls += 1
        return deadline_value

    first = carrier.receive_one(partial_deadline_provider=deadline)
    assert first.packet.payload == b"payload" * 40
    physical_reads = channel.read_calls
    first.retry_delivery()

    second = carrier.receive_one(partial_deadline_provider=deadline)
    assert second.packet.header == first.packet.header
    assert second.packet.payload == first.packet.payload
    assert channel.read_calls == physical_reads
    second.accept()

    snapshot = carrier.diagnostic_snapshot()
    assert len(channel.read_deadlines) == 1
    assert deadline_calls > 1
    assert channel.observed_read_deadlines
    assert set(channel.observed_read_deadlines) == {deadline_value}
    assert snapshot["accepted_frames"] == 1
    assert snapshot["delivery_retries"] == 1
    assert snapshot["active_delivery"] is False
    assert snapshot["retained_delivery"] is False
    assert snapshot["io"]["read_frames"] == 1
    assert snapshot["io"]["partial_read_calls"] > 0


def test_short_writes_are_exact_and_capture_lane_high_water():
    channel = _ShortByteChannel(write_pattern=(1, 2, 3, 5))
    carrier = _carrier(channel, chunk_bytes=13)
    deadline_value = time.monotonic() + 60.0
    deadline_calls = 0

    def deadline():
        nonlocal deadline_calls
        deadline_calls += 1
        return deadline_value
    payload = b"outbound" * 45
    carrier.enqueue(
        lane="bundle",
        payload_kind="bundle_chunk",
        payload=payload,
        message_id="host-short-write",
    )

    prepared = carrier.flush_one(deadline_provider=deadline)
    assert prepared is not None
    assert bytes(channel.outbound) == prepared.wire
    assert decode_mux_packet(channel.outbound).payload == payload

    snapshot = carrier.diagnostic_snapshot()
    assert len(channel.write_deadlines) == 1
    assert deadline_calls > 1
    assert channel.observed_write_deadlines
    assert set(channel.observed_write_deadlines) == {deadline_value}
    assert snapshot["queue"]["current_items"] == 0
    assert snapshot["queue"]["current_reserved_wire_bytes"] == 0
    assert snapshot["queue"]["peak_items"] == 1
    assert snapshot["queue"]["peak_payload_bytes"] == len(payload)
    assert snapshot["queue"]["peak_reserved_wire_bytes"] >= len(prepared.wire)
    assert snapshot["lanes"]["bundle"]["peak_items"] == 1
    assert snapshot["lanes"]["bundle"]["sent_frames"] == 1
    assert snapshot["io"]["partial_write_calls"] > 0


def test_oversized_body_prefix_is_rejected_before_any_body_read_or_allocation():
    prefix = _UINT32.pack(MAX_MUX_BODY_BYTES + 1)
    channel = _ShortByteChannel(prefix)
    carrier = _carrier(channel)

    _carrier_error("mux_pipe_frame_too_large", carrier.receive_one)

    snapshot = carrier.diagnostic_snapshot()
    assert channel.read_calls == 1
    assert channel.inbound == bytearray()
    assert channel.closed is True
    assert snapshot["poisoned"] is True
    assert snapshot["io"]["read_frames"] == 0


def test_failure_after_first_physical_write_poisons_and_never_retries_frame():
    channel = _ShortByteChannel(write_pattern=(7,), fail_write_call=2)
    carrier = _carrier(channel, chunk_bytes=7)
    carrier.enqueue(
        lane="legacy_turn",
        payload_kind="legacy_frame",
        payload=b"ambiguous" * 30,
        message_id="host-ambiguous-write",
    )

    error = _carrier_error("mux_pipe_write_outcome_unknown", carrier.flush_one)
    assert error.cause_code == "OSError"
    assert 0 < len(channel.outbound) <= 7
    assert channel.closed is True
    snapshot = carrier.diagnostic_snapshot()
    assert snapshot["poisoned"] is True
    assert snapshot["poison_code"] == "mux_pipe_write_outcome_unknown"
    assert snapshot["queue"]["current_items"] == 0
    assert snapshot["queue"]["current_reserved_wire_bytes"] == 0
    assert snapshot["started_wire_bytes"] > 0
    assert snapshot["outcome_unknown_wire_bytes"] == snapshot["started_wire_bytes"]
    _carrier_error(
        "mux_pipe_connection_poisoned",
        lambda: carrier.enqueue(
            lane="control",
            payload_kind="control",
            payload=b"retry-forbidden",
            message_id="retry-forbidden",
            trusted_class="control",
        ),
    )


def test_unknown_delivery_outcome_poisons_receiver_and_connection():
    wire = _runtime_wire("runtime-unknown", b"possibly-applied")
    channel = _ShortByteChannel(wire)
    carrier = _carrier(channel)
    lease = carrier.receive_one()

    lease.outcome_unknown()

    assert lease.settled is True
    assert channel.closed is True
    assert carrier.diagnostic_snapshot()["poisoned"] is True
    _carrier_error("mux_pipe_connection_poisoned", carrier.receive_one)


def test_concurrent_flushes_never_interleave_physical_frame_chunks():
    channel = _ShortByteChannel(write_pattern=(3,), write_delay=0.0001)
    carrier = _carrier(channel, chunk_bytes=9)
    payloads = [b"first" * 30, b"second" * 30]
    for index, payload in enumerate(payloads, 1):
        carrier.enqueue(
            lane="legacy_turn",
            payload_kind="legacy_frame",
            payload=payload,
            message_id=f"ordered-{index}",
        )

    gate = threading.Barrier(3)
    results = []
    errors = []

    def flush() -> None:
        try:
            gate.wait(timeout=2)
            results.append(carrier.flush_one())
        except Exception as error:  # noqa: BLE001 - return worker failure
            errors.append(error)

    workers = [threading.Thread(target=flush) for _ in range(2)]
    for worker in workers:
        worker.start()
    gate.wait(timeout=2)
    for worker in workers:
        worker.join(timeout=5)

    assert not errors
    assert all(not worker.is_alive() for worker in workers)
    assert len(results) == 2
    packets = [decode_mux_packet(wire) for wire in _split_wires(bytes(channel.outbound))]
    assert [packet.header["message_id"] for packet in packets] == [
        "ordered-1",
        "ordered-2",
    ]
    assert [packet.payload for packet in packets] == payloads
    snapshot = carrier.diagnostic_snapshot()
    assert snapshot["queue"]["peak_items"] == 2
    assert snapshot["lanes"]["legacy_turn"]["sent_frames"] == 2


def test_active_delivery_must_be_settled_before_another_read():
    first_wire = _runtime_wire("runtime-first", b"first")
    second_wire = _runtime_wire("runtime-second", b"second")
    # Each physical connection has one global sequence. Re-encode the second
    # frame with the same runtime queue so it receives sequence two.
    runtime = _queue("runtime")
    wires = []
    for message_id, payload in (("runtime-first", b"first"), ("runtime-second", b"second")):
        runtime.enqueue(
            lane="legacy_turn",
            payload_kind="legacy_frame",
            payload=payload,
            message_id=message_id,
        )
        prepared = runtime.prepare_next()
        assert prepared is not None
        runtime.begin_write()
        runtime.settle_write("sent")
        wires.append(prepared.wire)
    assert first_wire != second_wire  # ensure helper frames are independently encoded

    carrier = _carrier(_ShortByteChannel(b"".join(wires)))
    first = carrier.receive_one()
    _carrier_error("mux_pipe_delivery_already_active", carrier.receive_one)
    first.accept()
    second = carrier.receive_one()
    assert second.packet.payload == b"second"
    second.accept()


def test_close_after_physical_write_before_settlement_is_outcome_unknown():
    channel = _ShortByteChannel()
    carrier = _carrier(channel, chunk_bytes=1024)
    carrier.enqueue(
        lane="legacy_turn",
        payload_kind="legacy_frame",
        payload=b"already-written" * 20,
        message_id="close-after-write",
    )
    write_returning = threading.Event()
    allow_return = threading.Event()
    original_write_wire = carrier._frame_io.write_wire

    def write_wire(*args, **kwargs):
        result = original_write_wire(*args, **kwargs)
        write_returning.set()
        assert allow_return.wait(timeout=2)
        return result

    carrier._frame_io.write_wire = write_wire  # type: ignore[method-assign]
    errors: list[BaseException] = []

    def flush() -> None:
        try:
            carrier.flush_one()
        except BaseException as error:  # noqa: BLE001 - return worker failure
            errors.append(error)

    worker = threading.Thread(target=flush)
    worker.start()
    assert write_returning.wait(timeout=2)
    carrier.close()
    allow_return.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], NativeMuxPipeCarrierError)
    assert errors[0].code == "mux_pipe_write_outcome_unknown"
    assert errors[0].cause_code == "mux_pipe_close_during_write"
    assert channel.outbound
    snapshot = carrier.diagnostic_snapshot()
    assert snapshot["closed"] is True
    assert snapshot["poisoned"] is True
    assert snapshot["poison_code"] == "mux_pipe_write_outcome_unknown"
    assert snapshot["write_in_flight"] is False
    assert snapshot["queue"]["current_items"] == 0


def test_close_releases_active_delivery_and_retained_wire():
    wire = _runtime_wire("runtime-close-active", b"retained" * 20)
    carrier = _carrier(_ShortByteChannel(wire))
    lease = carrier.receive_one()
    assert carrier.diagnostic_snapshot()["active_delivery"] is True

    carrier.close()

    snapshot = carrier.diagnostic_snapshot()
    assert snapshot["closed"] is True
    assert snapshot["active_delivery"] is False
    assert snapshot["retained_delivery"] is False
    assert snapshot["queue"]["current_items"] == 0
    _carrier_error("mux_pipe_connection_closed", lease.accept)


def test_close_during_physical_receive_releases_reader_state():
    channel = _BlockingReadChannel()
    carrier = _carrier(channel)
    errors: list[BaseException] = []

    def receive() -> None:
        try:
            carrier.receive_one()
        except BaseException as error:  # noqa: BLE001 - return worker failure
            errors.append(error)

    worker = threading.Thread(target=receive)
    worker.start()
    assert channel.read_started.wait(timeout=2)
    carrier.close()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], NativeMuxPipeCarrierError)
    assert errors[0].code == "mux_pipe_eof"
    snapshot = carrier.diagnostic_snapshot()
    assert snapshot["closed"] is True
    assert snapshot["poisoned"] is True
    assert snapshot["active_delivery"] is False
    assert snapshot["retained_delivery"] is False


def test_default_frame_deadline_is_one_fixed_node_parity_safety_window(monkeypatch):
    channel = _ShortByteChannel(write_pattern=(1,))
    carrier = _carrier(channel, chunk_bytes=3)
    carrier.enqueue(
        lane="legacy_turn",
        payload_kind="legacy_frame",
        payload=b"many-physical-chunks",
        message_id="fixed-default-deadline",
    )
    monotonic_values = iter((100.0, 100.0, 100.0))
    monkeypatch.setattr(
        "genshin_app.agent_chat.native_mux_pipe_carrier.time.monotonic",
        lambda: next(monotonic_values, 100.0),
    )

    carrier.flush_one()

    assert len(channel.write_deadlines) == 1
    provider = channel.write_deadlines[0]
    expected = 100.0 + DEFAULT_MUX_PIPE_FRAME_TIMEOUT_SECONDS
    assert provider() == expected
    assert provider() == expected
    assert channel.observed_write_deadlines
    assert set(channel.observed_write_deadlines) == {expected}


def test_repeated_idle_pipe_timeouts_are_retryable_until_first_frame_bytes():
    wire = _runtime_wire("runtime-idle-retry", b"eventually-ready")
    channel = _IdleTimeoutThenDataChannel(wire, timeout_count=3)
    carrier = _carrier(channel, chunk_bytes=5)

    lease = carrier.receive_one()

    assert lease.packet.payload == b"eventually-ready"
    lease.accept()
    snapshot = carrier.diagnostic_snapshot()
    assert snapshot["poisoned"] is False
    assert snapshot["io"]["idle_read_timeouts"] == 3


def test_dynamic_idle_owner_can_shorten_and_timeout_without_poisoning():
    wire = _runtime_wire("runtime-idle-cancel", b"still-buffered")
    channel = _ShortByteChannel(wire)
    carrier = _carrier(channel)
    deadlines = iter((time.monotonic() + 60.0, time.monotonic() - 1.0))

    _carrier_error(
        "mux_pipe_read_idle_timeout",
        lambda: carrier.receive_one(idle_deadline_provider=lambda: next(deadlines)),
    )

    snapshot = carrier.diagnostic_snapshot()
    assert snapshot["poisoned"] is False
    assert snapshot["closed"] is False
    assert channel.inbound == bytearray(wire)


def test_partial_deadline_starts_after_first_byte_and_timeout_is_fatal():
    wire = _runtime_wire("runtime-partial-timeout", b"incomplete")
    channel = _ShortByteChannel(wire, read_pattern=(1,))
    carrier = _carrier(channel)
    deadlines = iter((time.monotonic() + 60.0, time.monotonic() - 1.0))

    _carrier_error(
        "mux_pipe_partial_frame_timeout",
        lambda: carrier.receive_one(partial_deadline_provider=lambda: next(deadlines)),
    )

    snapshot = carrier.diagnostic_snapshot()
    assert snapshot["poisoned"] is True
    assert snapshot["poison_code"] == "mux_pipe_partial_frame_timeout"
    assert snapshot["io"]["partial_frame_timeouts"] == 1
    assert 0 < len(channel.inbound) < len(wire)


def test_partial_owner_extension_cannot_cross_fixed_frame_cap():
    wire = _runtime_wire("runtime-partial-cap", b"bounded-partial" * 5)
    channel = _ShortByteChannel(wire, read_pattern=(1, 2, 3))
    carrier = _carrier(channel, chunk_bytes=5)
    owner_deadline = time.monotonic() + 10_000.0

    lease = carrier.receive_one(
        partial_deadline_provider=lambda: owner_deadline,
    )

    assert lease.packet.payload == b"bounded-partial" * 5
    lease.accept()
    assert channel.observed_read_deadlines
    cap = channel.observed_read_deadlines[0]
    assert all(value == cap for value in channel.observed_read_deadlines)
    assert cap < owner_deadline


def test_dynamic_write_owner_shortens_but_extension_cannot_cross_frame_cap():
    channel = _ShortByteChannel(write_pattern=(1,))
    carrier = _carrier(channel, chunk_bytes=3)
    carrier.enqueue(
        lane="legacy_turn",
        payload_kind="legacy_frame",
        payload=b"bounded-owner-extension",
        message_id="bounded-owner-extension",
    )
    owner_deadline = time.monotonic() + 10_000.0

    carrier.flush_one(deadline_provider=lambda: owner_deadline)

    assert channel.observed_write_deadlines
    cap = channel.observed_write_deadlines[0]
    assert all(value == cap for value in channel.observed_write_deadlines)
    assert cap < owner_deadline

    cancel_channel = _ShortByteChannel(write_pattern=(1,))
    cancel_carrier = _carrier(cancel_channel, chunk_bytes=3)
    cancel_carrier.enqueue(
        lane="legacy_turn",
        payload_kind="legacy_frame",
        payload=b"dynamic-cancel",
        message_id="dynamic-cancel",
    )
    deadlines = iter((time.monotonic() + 60.0, time.monotonic() + 60.0, time.monotonic() - 1.0))
    error = _carrier_error(
        "mux_pipe_write_outcome_unknown",
        lambda: cancel_carrier.flush_one(deadline_provider=lambda: next(deadlines)),
    )
    assert error.cause_code == "pipe_write_timeout"
    assert 0 < len(cancel_channel.outbound) < cancel_carrier.diagnostic_snapshot()["started_wire_bytes"]


@pytest.mark.parametrize("deadline", [float("nan"), float("inf"), True, "later"])
def test_invalid_frame_deadline_is_rejected_before_physical_write(deadline):
    channel = _ShortByteChannel()
    carrier = _carrier(channel)
    carrier.enqueue(
        lane="legacy_turn",
        payload_kind="legacy_frame",
        payload=b"invalid-deadline",
        message_id=f"invalid-deadline-{type(deadline).__name__}",
    )

    error = _carrier_error(
        "mux_pipe_deadline_invalid",
        lambda: carrier.flush_one(deadline_provider=lambda: deadline),
    )

    assert error.cause_code is None
    assert channel.write_calls == 0
    assert carrier.diagnostic_snapshot()["queue"]["current_items"] == 1
