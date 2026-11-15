"""Non-production raw-pipe carrier qualification for the native mux.

The legacy bridge remains the production owner.  This module proves the
physical invariants needed by a later, explicit launch-selected mux codec:
bounded exact I/O, one writer, one delivery lease, deterministic settlement,
and sanitized telemetry.  It deliberately owns no runtime, DSH Session, Turn,
or App-domain routing state.
"""
from __future__ import annotations

import struct
import threading
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from .native_mux_protocol import (
    MAX_MUX_BODY_BYTES,
    MAX_MUX_WIRE_BYTES,
    NativeMuxPacket,
    NativeMuxProtocolError,
)
from .native_pipe import MAX_PIPE_IO_BYTES
from .native_pipe_multiplexer import (
    MUX_LANE_ORDER,
    NativeMuxReceiver,
    NativeMuxSendQueue,
    NativePipeMultiplexerError,
    PreparedMuxWrite,
)

MUX_PIPE_CARRIER_CONTRACT = "genshin-native-mux-pipe-carrier-v1"
PRODUCTION_OWNER = False
DEFAULT_MUX_PIPE_IO_CHUNK_BYTES = 1024 * 1024
DEFAULT_MUX_PIPE_FRAME_TIMEOUT_SECONDS = 10 * 60.0
_UINT32 = struct.Struct(">I")


class NativeMuxPipeCarrierError(RuntimeError):
    """Sanitized carrier failure; wire and payload bytes are never reflected."""

    def __init__(self, code: str, *, cause_code: str | None = None):
        super().__init__(code)
        self.code = code
        self.cause_code = cause_code


def _reject(code: str, *, cause_code: str | None = None) -> None:
    raise NativeMuxPipeCarrierError(code, cause_code=cause_code)


def _cause_code(error: BaseException) -> str | None:
    value = getattr(error, "code", None)
    return value if isinstance(value, str) else None


def _payload_size(payload: Any) -> int:
    try:
        return memoryview(payload).nbytes
    except (TypeError, ValueError, BufferError):
        return 0


def _deadline_value(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not float("-inf") < float(value) < float("inf")
    ):
        _reject("mux_pipe_deadline_invalid")
    return float(value)


@dataclass(slots=True)
class _BoundedDynamicDeadline:
    """Dynamic owner deadline bounded by one immutable carrier cap."""

    owner: Callable[[], float] | None
    cap: float | None

    def __call__(self) -> float:
        owner_deadline = None if self.owner is None else _deadline_value(self.owner())
        if self.cap is None:
            if owner_deadline is None:  # constructor never permits both absent
                _reject("mux_pipe_deadline_invalid")
            return owner_deadline
        if owner_deadline is None:
            return self.cap
        return min(self.cap, owner_deadline)


class NativeMuxPipeFrameIO:
    """Exact mux-wire reads and writes over one bounded raw byte channel."""

    def __init__(
        self,
        connection: Any,
        *,
        chunk_bytes: int = DEFAULT_MUX_PIPE_IO_CHUNK_BYTES,
    ) -> None:
        if not all(callable(getattr(connection, name, None)) for name in ("read", "write", "close")):
            raise TypeError("connection must provide read, write, and close")
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= MAX_PIPE_IO_BYTES:
            raise ValueError("chunk_bytes is outside the raw pipe limit")
        self._connection = connection
        self._chunk_bytes = chunk_bytes
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._closed = False
        self._poisoned = False
        self._poison_code: str | None = None
        self._close_failed = False
        self._metrics: dict[str, int | float] = {
            "read_frames": 0,
            "read_wire_bytes": 0,
            "read_calls": 0,
            "partial_read_calls": 0,
            "idle_read_timeouts": 0,
            "partial_frame_timeouts": 0,
            "read_seconds_total": 0.0,
            "read_seconds_max": 0.0,
            "write_frames": 0,
            "write_wire_bytes": 0,
            "write_calls": 0,
            "partial_write_calls": 0,
            "write_seconds_total": 0.0,
            "write_seconds_max": 0.0,
        }

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def poisoned(self) -> bool:
        with self._state_lock:
            return self._poisoned

    def _ensure_open(self) -> None:
        with self._state_lock:
            if self._poisoned:
                _reject("mux_pipe_connection_poisoned", cause_code=self._poison_code)
            if self._closed:
                _reject("mux_pipe_connection_closed")

    def _deadline_scope(
        self,
        name: str,
        provider: Callable[[], float] | None,
    ):
        if provider is None:
            return nullcontext()
        if not callable(provider):
            raise TypeError("deadline provider must be callable")
        factory = getattr(self._connection, name, None)
        if not callable(factory):
            _reject("mux_pipe_deadline_unsupported")
        return factory(provider)

    @staticmethod
    def _prepare_deadline(
        provider: Callable[[], float] | _BoundedDynamicDeadline | None,
        *,
        cap_seconds: float | None,
        expired_code: str,
    ) -> _BoundedDynamicDeadline | None:
        """Preflight a dynamic owner and bind it below one physical cap.

        The owner remains live so cancellation or an earlier deadline is
        observed by every raw operation.  Extending the owner can never move a
        partial-frame/write past the immutable ten-minute safety cap.  This is
        carrier qualification safety, not an App task budget or measured
        production high-water.
        """
        if isinstance(provider, _BoundedDynamicDeadline):
            return provider
        if provider is not None and not callable(provider):
            raise TypeError("deadline provider must be callable")
        cap = None if cap_seconds is None else time.monotonic() + cap_seconds
        if provider is None and cap is None:
            return None
        if provider is not None:
            try:
                initial = _deadline_value(provider())
            except NativeMuxPipeCarrierError:
                raise
            except Exception as error:  # noqa: BLE001 - normalize owner callback
                raise NativeMuxPipeCarrierError(
                    "mux_pipe_deadline_invalid",
                    cause_code=type(error).__name__,
                ) from None
            if initial <= time.monotonic():
                _reject(expired_code)
        return _BoundedDynamicDeadline(provider, cap)

    def _record_duration(self, operation: str, started: float) -> None:
        duration = max(0.0, time.monotonic() - started)
        with self._state_lock:
            total_key = f"{operation}_seconds_total"
            maximum_key = f"{operation}_seconds_max"
            self._metrics[total_key] = float(self._metrics[total_key]) + duration
            self._metrics[maximum_key] = max(
                float(self._metrics[maximum_key]),
                duration,
            )

    def _read_once(self, requested: int) -> bytes:
        chunk = self._connection.read(requested)
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            _reject("mux_pipe_read_invalid")
        raw = bytes(chunk)
        with self._state_lock:
            self._metrics["read_calls"] = int(self._metrics["read_calls"]) + 1
            if len(raw) < requested:
                self._metrics["partial_read_calls"] = (
                    int(self._metrics["partial_read_calls"]) + 1
                )
        if len(raw) > requested:
            _reject("mux_pipe_read_invalid")
        return raw

    def _read_exact(self, size: int, *, initial: bytes = b"") -> bytes:
        output = bytearray(initial)
        while len(output) < size:
            requested = min(self._chunk_bytes, size - len(output))
            raw = self._read_once(requested)
            if not raw:
                _reject("mux_pipe_frame_truncated")
            output.extend(raw)
        return bytes(output)

    def _read_first_prefix_bytes(
        self,
        idle_deadline: _BoundedDynamicDeadline | None,
    ) -> bytes:
        with self._deadline_scope("read_deadline", idle_deadline):
            while True:
                try:
                    raw = self._read_once(min(self._chunk_bytes, _UINT32.size))
                except Exception as error:
                    if _cause_code(error) == "pipe_read_timeout":
                        with self._state_lock:
                            self._metrics["idle_read_timeouts"] = (
                                int(self._metrics.get("idle_read_timeouts", 0)) + 1
                            )
                        if idle_deadline is None:
                            continue
                        _reject("mux_pipe_read_idle_timeout")
                    if _cause_code(error) == "pipe_read_deadline_invalid":
                        _reject("mux_pipe_deadline_invalid")
                    raise
                if not raw:
                    _reject("mux_pipe_eof")
                return raw

    def read_wire(
        self,
        *,
        idle_deadline_provider: Callable[[], float] | None = None,
        partial_deadline_provider: Callable[[], float] | None = None,
    ) -> bytes:
        """Read one wire with retryable idle and fatal partial-frame timeout."""
        with self._read_lock:
            self._ensure_open()
            started = time.monotonic()
            frame_started = False
            try:
                idle_deadline = self._prepare_deadline(
                    idle_deadline_provider,
                    cap_seconds=None,
                    expired_code="mux_pipe_read_idle_timeout",
                )
                first = self._read_first_prefix_bytes(idle_deadline)
                frame_started = True
                partial_deadline = self._prepare_deadline(
                    partial_deadline_provider,
                    cap_seconds=DEFAULT_MUX_PIPE_FRAME_TIMEOUT_SECONDS,
                    expired_code="mux_pipe_partial_frame_timeout",
                )
                try:
                    with self._deadline_scope("read_deadline", partial_deadline):
                        prefix = self._read_exact(_UINT32.size, initial=first)
                        (body_length,) = _UINT32.unpack(prefix)
                        if body_length > MAX_MUX_BODY_BYTES:
                            _reject("mux_pipe_frame_too_large")
                        body = self._read_exact(body_length)
                except Exception as error:
                    if _cause_code(error) == "pipe_read_timeout":
                        with self._state_lock:
                            self._metrics["partial_frame_timeouts"] = (
                                int(self._metrics.get("partial_frame_timeouts", 0)) + 1
                            )
                        _reject("mux_pipe_partial_frame_timeout")
                    if _cause_code(error) == "pipe_read_deadline_invalid":
                        _reject("mux_pipe_partial_deadline_invalid")
                    raise
                wire = prefix + body
                with self._state_lock:
                    self._metrics["read_frames"] = int(self._metrics["read_frames"]) + 1
                    self._metrics["read_wire_bytes"] = (
                        int(self._metrics["read_wire_bytes"]) + len(wire)
                    )
                return wire
            except Exception as error:
                code = _cause_code(error)
                if not frame_started and code in {
                    "mux_pipe_read_idle_timeout",
                    "mux_pipe_deadline_invalid",
                }:
                    if isinstance(error, NativeMuxPipeCarrierError):
                        raise
                    raise NativeMuxPipeCarrierError(
                        code,
                        cause_code=type(error).__name__,
                    ) from None
                self.poison(_cause_code(error) or "mux_pipe_read_failed")
                if isinstance(error, NativeMuxPipeCarrierError):
                    raise
                raise NativeMuxPipeCarrierError(
                    "mux_pipe_read_failed",
                    cause_code=_cause_code(error) or type(error).__name__,
                ) from None
            finally:
                self._record_duration("read", started)

    def write_wire(
        self,
        wire: bytes | bytearray | memoryview,
        *,
        deadline_provider: Callable[[], float] | None = None,
    ) -> int:
        """Write one complete mux wire without allowing chunk interleaving."""
        if not isinstance(wire, (bytes, bytearray, memoryview)):
            raise TypeError("mux wire must be bytes")
        raw = bytes(wire)
        if not _UINT32.size <= len(raw) <= MAX_MUX_WIRE_BYTES:
            _reject("mux_pipe_wire_length_invalid")
        (body_length,) = _UINT32.unpack_from(raw)
        if body_length > MAX_MUX_BODY_BYTES:
            _reject("mux_pipe_frame_too_large")
        if body_length != len(raw) - _UINT32.size:
            _reject("mux_pipe_body_length_mismatch")

        with self._write_lock:
            self._ensure_open()
            started = time.monotonic()
            written = 0
            try:
                bounded_deadline = self._prepare_deadline(
                    deadline_provider,
                    cap_seconds=DEFAULT_MUX_PIPE_FRAME_TIMEOUT_SECONDS,
                    expired_code="mux_pipe_deadline_expired",
                )
                with self._deadline_scope("write_deadline", bounded_deadline):
                    while written < len(raw):
                        end = min(len(raw), written + self._chunk_bytes)
                        requested = end - written
                        count = self._connection.write(raw[written:end])
                        with self._state_lock:
                            self._metrics["write_calls"] = int(self._metrics["write_calls"]) + 1
                        if type(count) is not int or not 0 < count <= requested:
                            _reject("mux_pipe_write_invalid")
                        if count < requested:
                            with self._state_lock:
                                self._metrics["partial_write_calls"] = (
                                    int(self._metrics["partial_write_calls"]) + 1
                                )
                        written += count
                with self._state_lock:
                    self._metrics["write_frames"] = int(self._metrics["write_frames"]) + 1
                    self._metrics["write_wire_bytes"] = (
                        int(self._metrics["write_wire_bytes"]) + written
                    )
                return written
            except Exception as error:
                self.poison(_cause_code(error) or "mux_pipe_write_failed")
                if isinstance(error, NativeMuxPipeCarrierError):
                    raise
                raise NativeMuxPipeCarrierError(
                    "mux_pipe_write_failed",
                    cause_code=_cause_code(error) or type(error).__name__,
                ) from None
            finally:
                self._record_duration("write", started)

    def poison(self, code: str) -> None:
        with self._state_lock:
            if self._poisoned:
                return
            self._poisoned = True
            self._poison_code = code if isinstance(code, str) else "mux_pipe_poisoned"
        self.close()

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._connection.close()
        except Exception:  # noqa: BLE001 - best effort after transport failure
            with self._state_lock:
                self._close_failed = True

    def diagnostic_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "contract": MUX_PIPE_CARRIER_CONTRACT,
                "production_owner": PRODUCTION_OWNER,
                "closed": self._closed,
                "poisoned": self._poisoned,
                "poison_code": self._poison_code,
                "close_failed": self._close_failed,
                "chunk_bytes": self._chunk_bytes,
                **self._metrics,
            }


@dataclass(slots=True)
class NativeMuxDeliveryLease:
    """One validated inbound packet awaiting explicit downstream settlement."""

    _carrier: NativeMuxPipeCarrier
    _token: int
    packet: NativeMuxPacket
    _settled: bool = False

    @property
    def settled(self) -> bool:
        return self._settled

    def _settle(self, outcome: str) -> None:
        if self._settled:
            _reject("mux_pipe_delivery_already_settled")
        self._carrier._settle_delivery(self._token, outcome)
        self._settled = True

    def accept(self) -> None:
        self._settle("accepted")

    def retry_delivery(self) -> None:
        self._settle("not_delivered")

    def outcome_unknown(self) -> None:
        self._settle("outcome_unknown")


class NativeMuxPipeCarrier:
    """Thread-safe qualification driver for mux queue, receiver, and raw I/O."""

    def __init__(
        self,
        connection: Any,
        *,
        send_queue: NativeMuxSendQueue,
        receiver: NativeMuxReceiver,
        chunk_bytes: int = DEFAULT_MUX_PIPE_IO_CHUNK_BYTES,
    ) -> None:
        if not isinstance(send_queue, NativeMuxSendQueue):
            raise TypeError("send_queue must be NativeMuxSendQueue")
        if not isinstance(receiver, NativeMuxReceiver):
            raise TypeError("receiver must be NativeMuxReceiver")
        self._frame_io = NativeMuxPipeFrameIO(connection, chunk_bytes=chunk_bytes)
        self._send_queue = send_queue
        self._receiver = receiver
        self._state_lock = threading.RLock()
        self._writer_lock = threading.Lock()
        self._reader_lock = threading.Lock()
        self._closed = False
        self._poisoned = False
        self._poison_code: str | None = None
        self._write_in_flight = False
        self._queued_messages: dict[str, tuple[str, int]] = {}
        self._lane_current_items = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._lane_current_payload_bytes = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._lane_peak_items = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._lane_peak_payload_bytes = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._lane_sent_frames = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._lane_sent_wire_bytes = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._peak_queued_items = 0
        self._peak_queued_payload_bytes = 0
        self._peak_queued_reserved_wire_bytes = 0
        self._started_wire_bytes = 0
        self._sent_wire_bytes = 0
        self._outcome_unknown_wire_bytes = 0
        self._accepted_frames = 0
        self._delivery_retries = 0
        self._rejections: dict[str, int] = {}
        self._retained_wire: bytes | None = None
        self._active_delivery_token: int | None = None
        self._next_delivery_token = 1

    def _ensure_open_locked(self) -> None:
        if self._poisoned:
            _reject("mux_pipe_connection_poisoned", cause_code=self._poison_code)
        if self._closed:
            _reject("mux_pipe_connection_closed")

    def _record_rejection_locked(self, error: BaseException) -> None:
        code = _cause_code(error) or type(error).__name__
        self._rejections[code] = self._rejections.get(code, 0) + 1

    def _refresh_high_water_locked(self) -> None:
        self._peak_queued_items = max(
            self._peak_queued_items,
            self._send_queue.queued_items,
        )
        self._peak_queued_payload_bytes = max(
            self._peak_queued_payload_bytes,
            self._send_queue.queued_bytes,
        )
        queued_reserved_wire_bytes = max(
            0,
            self._send_queue.reserved_connection_bytes - self._sent_wire_bytes,
        )
        self._peak_queued_reserved_wire_bytes = max(
            self._peak_queued_reserved_wire_bytes,
            queued_reserved_wire_bytes,
        )
        for lane in MUX_LANE_ORDER:
            self._lane_peak_items[lane] = max(
                self._lane_peak_items[lane],
                self._lane_current_items[lane],
            )
            self._lane_peak_payload_bytes[lane] = max(
                self._lane_peak_payload_bytes[lane],
                self._lane_current_payload_bytes[lane],
            )

    def enqueue(
        self,
        *,
        lane: str,
        payload_kind: str,
        payload: bytes | bytearray | memoryview,
        message_id: str,
        correlation_id: str | None = None,
        stream_id: str | None = None,
        stream_seq: int | None = None,
        trusted_class: str = "normal",
    ) -> None:
        with self._state_lock:
            self._ensure_open_locked()
            try:
                self._send_queue.enqueue(
                    lane=lane,
                    payload_kind=payload_kind,
                    payload=payload,
                    message_id=message_id,
                    correlation_id=correlation_id,
                    stream_id=stream_id,
                    stream_seq=stream_seq,
                    trusted_class=trusted_class,
                )
            except (NativePipeMultiplexerError, NativeMuxProtocolError) as error:
                self._record_rejection_locked(error)
                raise
            size = _payload_size(payload)
            self._queued_messages[message_id] = (lane, size)
            self._lane_current_items[lane] += 1
            self._lane_current_payload_bytes[lane] += size
            self._refresh_high_water_locked()

    def flush_one(
        self,
        *,
        deadline_provider: Callable[[], float] | None = None,
    ) -> PreparedMuxWrite | None:
        """Write one scheduled frame; post-begin failures always poison."""
        with self._writer_lock:
            with self._state_lock:
                self._ensure_open_locked()
            # Validate the live owner and bind it below one immutable frame
            # cap before begin_write.  A bad callback here is a definite local
            # rejection; later shortening/cancellation remains observable.
            bounded_deadline = self._frame_io._prepare_deadline(
                deadline_provider,
                cap_seconds=DEFAULT_MUX_PIPE_FRAME_TIMEOUT_SECONDS,
                expired_code="mux_pipe_deadline_expired",
            )
            with self._state_lock:
                # A concurrent close may have happened while the owner
                # callback supplied its deadline.
                self._ensure_open_locked()
                prepared = self._send_queue.prepare_next()
                if prepared is None:
                    return None
                self._send_queue.begin_write()
                self._write_in_flight = True
                self._started_wire_bytes += len(prepared.wire)
            try:
                self._frame_io.write_wire(
                    prepared.wire,
                    deadline_provider=bounded_deadline,
                )
            except Exception as error:  # noqa: BLE001 - normalize transport failures
                cause_code = (
                    getattr(error, "cause_code", None)
                    or _cause_code(error)
                    or type(error).__name__
                )
                with self._state_lock:
                    self._write_in_flight = False
                    self._outcome_unknown_wire_bytes += len(prepared.wire)
                    try:
                        self._send_queue.settle_write("outcome_unknown")
                    except NativePipeMultiplexerError:
                        self._send_queue.poison()
                    self._record_rejection_locked(error)
                self._poison("mux_pipe_write_outcome_unknown")
                raise NativeMuxPipeCarrierError(
                    "mux_pipe_write_outcome_unknown",
                    cause_code=cause_code,
                ) from None

            with self._state_lock:
                self._write_in_flight = False
                if self._closed or self._poisoned:
                    # A concurrent close may happen after the transport has
                    # accepted the entire wire but before queue settlement.
                    # The peer outcome is therefore unknowable; never expose
                    # the send-queue's internal poisoned/reservation error as
                    # if this were a definite delivery failure.
                    self._poison_locked("mux_pipe_write_outcome_unknown")
                    self._outcome_unknown_wire_bytes += len(prepared.wire)
                    raise NativeMuxPipeCarrierError(
                        "mux_pipe_write_outcome_unknown",
                        cause_code="mux_pipe_close_during_write",
                    )
                try:
                    self._send_queue.settle_write("sent")
                except NativePipeMultiplexerError as error:
                    self._record_rejection_locked(error)
                    self._poison_locked(error.code)
                    self._frame_io.poison(error.code)
                    raise
                lane, payload_bytes = self._queued_messages.pop(prepared.message_id)
                self._lane_current_items[lane] -= 1
                self._lane_current_payload_bytes[lane] -= payload_bytes
                self._lane_sent_frames[lane] += 1
                self._lane_sent_wire_bytes[lane] += len(prepared.wire)
                self._sent_wire_bytes += len(prepared.wire)
                self._refresh_high_water_locked()
            return prepared

    def receive_one(
        self,
        *,
        idle_deadline_provider: Callable[[], float] | None = None,
        partial_deadline_provider: Callable[[], float] | None = None,
    ) -> NativeMuxDeliveryLease:
        """Read or re-present one packet and require explicit settlement."""
        with self._reader_lock:
            with self._state_lock:
                self._ensure_open_locked()
                if self._active_delivery_token is not None:
                    _reject("mux_pipe_delivery_already_active")
                retained = self._retained_wire
            if retained is None:
                try:
                    wire = self._frame_io.read_wire(
                        idle_deadline_provider=idle_deadline_provider,
                        partial_deadline_provider=partial_deadline_provider,
                    )
                except Exception as error:
                    with self._state_lock:
                        self._record_rejection_locked(error)
                    if _cause_code(error) in {
                        "mux_pipe_read_idle_timeout",
                        "mux_pipe_deadline_invalid",
                    }:
                        raise
                    self._poison(_cause_code(error) or "mux_pipe_read_failed")
                    raise
            else:
                wire = retained

            try:
                packet = self._receiver.prepare_wire(wire)
            except (NativePipeMultiplexerError, NativeMuxProtocolError) as error:
                with self._state_lock:
                    self._record_rejection_locked(error)
                self._poison(error.code)
                raise NativeMuxPipeCarrierError(
                    "mux_pipe_receive_rejected",
                    cause_code=error.code,
                ) from None

            with self._state_lock:
                self._ensure_open_locked()
                token = self._next_delivery_token
                self._next_delivery_token += 1
                self._active_delivery_token = token
                self._retained_wire = wire
            return NativeMuxDeliveryLease(self, token, packet)

    def _settle_delivery(self, token: int, outcome: str) -> None:
        with self._state_lock:
            self._ensure_open_locked()
            if token != self._active_delivery_token:
                _reject("mux_pipe_delivery_token_mismatch")
            try:
                self._receiver.settle_read(outcome)
            except NativePipeMultiplexerError as error:
                self._record_rejection_locked(error)
                self._poison_locked(error.code)
                self._frame_io.poison(error.code)
                raise
            self._active_delivery_token = None
            if outcome == "accepted":
                self._retained_wire = None
                self._accepted_frames += 1
            elif outcome == "not_delivered":
                self._delivery_retries += 1
            else:
                self._retained_wire = None
                self._poison_locked("mux_pipe_delivery_outcome_unknown")
                self._frame_io.poison("mux_pipe_delivery_outcome_unknown")

    def _poison_locked(self, code: str) -> None:
        if self._poisoned:
            return
        self._poisoned = True
        self._poison_code = code
        self._send_queue.poison()
        self._receiver.poison()
        self._queued_messages.clear()
        self._lane_current_items = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._lane_current_payload_bytes = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._active_delivery_token = None
        self._retained_wire = None

    def _poison(self, code: str) -> None:
        with self._state_lock:
            self._poison_locked(code)
        self._frame_io.poison(code)

    def close(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            if self._write_in_flight:
                # Closing after begin_write cannot prove whether the peer
                # received none, some, or all of the frame.  Poison the
                # connection under the public outcome-unknown classification
                # and let the writer clear its in-flight marker on unwind.
                self._poison_locked("mux_pipe_write_outcome_unknown")
            else:
                self._send_queue.poison()
                self._receiver.poison()
                self._queued_messages.clear()
                self._lane_current_items = dict.fromkeys(MUX_LANE_ORDER, 0)
                self._lane_current_payload_bytes = dict.fromkeys(MUX_LANE_ORDER, 0)
                self._active_delivery_token = None
                self._retained_wire = None
        self._frame_io.close()

    def diagnostic_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            lanes = {
                lane: {
                    "current_items": self._lane_current_items[lane],
                    "current_payload_bytes": self._lane_current_payload_bytes[lane],
                    "peak_items": self._lane_peak_items[lane],
                    "peak_payload_bytes": self._lane_peak_payload_bytes[lane],
                    "sent_frames": self._lane_sent_frames[lane],
                    "sent_wire_bytes": self._lane_sent_wire_bytes[lane],
                }
                for lane in MUX_LANE_ORDER
            }
            queued_wire_reservation = (
                0
                if self._poisoned or self._closed
                else max(
                    0,
                    self._send_queue.reserved_connection_bytes
                    - self._sent_wire_bytes,
                )
            )
            value = {
                "contract": MUX_PIPE_CARRIER_CONTRACT,
                "production_owner": PRODUCTION_OWNER,
                "closed": self._closed,
                "poisoned": self._poisoned,
                "poison_code": self._poison_code,
                "queue": {
                    "current_items": self._send_queue.queued_items,
                    "current_payload_bytes": self._send_queue.queued_bytes,
                    "current_reserved_wire_bytes": queued_wire_reservation,
                    "peak_items": self._peak_queued_items,
                    "peak_payload_bytes": self._peak_queued_payload_bytes,
                    "peak_reserved_wire_bytes": self._peak_queued_reserved_wire_bytes,
                },
                "lanes": lanes,
                "accepted_frames": self._accepted_frames,
                "delivery_retries": self._delivery_retries,
                "started_wire_bytes": self._started_wire_bytes,
                "sent_wire_bytes": self._sent_wire_bytes,
                "outcome_unknown_wire_bytes": self._outcome_unknown_wire_bytes,
                "active_delivery": self._active_delivery_token is not None,
                "retained_delivery": self._retained_wire is not None,
                "write_in_flight": self._write_in_flight,
                "rejections": dict(sorted(self._rejections.items())),
            }
        value["io"] = self._frame_io.diagnostic_snapshot()
        return value


__all__ = [
    "DEFAULT_MUX_PIPE_FRAME_TIMEOUT_SECONDS",
    "DEFAULT_MUX_PIPE_IO_CHUNK_BYTES",
    "MUX_PIPE_CARRIER_CONTRACT",
    "PRODUCTION_OWNER",
    "NativeMuxDeliveryLease",
    "NativeMuxPipeCarrier",
    "NativeMuxPipeCarrierError",
    "NativeMuxPipeFrameIO",
]
