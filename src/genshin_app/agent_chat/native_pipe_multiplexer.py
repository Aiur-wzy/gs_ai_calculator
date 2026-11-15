"""Transport-free state machines for the native pipe mux.

This module deliberately owns no pipe handle and starts no reader or writer.
It provides the state that a later single-reader/single-writer transport may
drive.  Wire ``priority`` is never trusted: the sender derives it from a local
trusted scheduling class and the receiver treats it as diagnostic metadata.

It is not a production carrier.  The future carrier must perform bounded
exact reads/writes directly over the raw pipe, must not nest this 128 MiB mux
frame inside the legacy 1 MiB frame reader, and must freeze one codec for the
whole post-handshake connection without mid-stream fallback.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass

from .native_mux_protocol import (
    JSON_SAFE_INTEGER_MAX,
    MAX_MUX_PAYLOAD_BYTES,
    MUX_LANES,
    NativeMuxPacket,
    NativeMuxProtocolError,
    decode_mux_packet,
    encode_mux_packet,
    make_mux_header,
)

MUX_LANE_ORDER = (
    "control",
    "legacy_turn",
    "remote_unary",
    "remote_stream",
    "bundle",
)
if frozenset(MUX_LANE_ORDER) != MUX_LANES:  # pragma: no cover - import contract
    raise RuntimeError("native_mux_lane_contract_mismatch")

TRUSTED_SCHEDULING_CLASSES = frozenset({"normal", "urgent", "control"})
_WIRE_PRIORITY = {"normal": "normal", "urgent": "urgent", "control": "control"}

# DSH rc.5 has no native Named-Pipe lane queue and this mux has no measured
# queue high-water yet.  The App-owned transient queue default therefore uses
# the accepted GUI ring envelope; it is intentionally smaller than the bridge
# *connection-lifetime* allowance.  The latter is a cumulative safety cap, not
# a target for resident queue memory.  Larger logical DSH values must later use
# stream/resource chunking rather than widening the 128 MiB packet codec.
OBSERVED_MUX_QUEUE_HIGH_WATER_ITEMS = None
OBSERVED_MUX_QUEUE_HIGH_WATER_BYTES = None
OBSERVED_GUI_PROJECTION_HIGH_WATER = 22_187
REFERENCE_BRIDGE_MAX_FRAMES = 524_288
REFERENCE_BRIDGE_MAX_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_LANE_MAX_ITEMS = 131_072
DEFAULT_LANE_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_TOTAL_MAX_ITEMS = 131_072
DEFAULT_TOTAL_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_CONNECTION_MAX_FRAMES = REFERENCE_BRIDGE_MAX_FRAMES
DEFAULT_CONNECTION_MAX_BYTES = REFERENCE_BRIDGE_MAX_BYTES
HARD_MAX_QUEUE_ITEMS = REFERENCE_BRIDGE_MAX_FRAMES
HARD_MAX_QUEUE_BYTES = REFERENCE_BRIDGE_MAX_BYTES
DEFAULT_DRR_QUANTUM_BYTES = 64 * 1024
DEFAULT_PRIORITY_BURST = 8
DEFAULT_MAX_STREAMS = DEFAULT_TOTAL_MAX_ITEMS
HARD_MAX_STREAMS = HARD_MAX_QUEUE_ITEMS
WRITE_OUTCOMES = frozenset({"sent", "not_sent", "outcome_unknown"})
READ_OUTCOMES = frozenset({"accepted", "not_delivered", "outcome_unknown"})
_MUX_WIRE_PREFIX_BYTES = 8  # uint32 body length + uint32 header length


class NativePipeMultiplexerError(RuntimeError):
    """Sanitized state-machine failure; payload bytes never enter the message."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject(code: str) -> None:
    raise NativePipeMultiplexerError(code)


def _positive(value, code: str) -> int:
    if type(value) is not int or not 1 <= value <= JSON_SAFE_INTEGER_MAX:
        _reject(code)
    return value


def _owned_payload(payload) -> bytes:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        _reject("invalid_mux_payload")
    try:
        view = memoryview(payload)
    except (TypeError, ValueError, BufferError):
        _reject("invalid_mux_payload")
    if view.nbytes > MAX_MUX_PAYLOAD_BYTES:
        _reject("mux_payload_too_large")
    if type(payload) is bytes:
        return payload
    try:
        return view.tobytes()
    except (TypeError, ValueError, BufferError):
        _reject("invalid_mux_payload")


@dataclass(frozen=True, slots=True)
class _PendingMessage:
    lane: str
    payload_kind: str
    payload: bytes
    message_id: str
    correlation_id: str | None
    stream_id: str | None
    stream_seq: int | None
    trusted_class: str
    reserved_wire_bytes: int

    @property
    def cost(self) -> int:
        return max(1, len(self.payload))


@dataclass(frozen=True, slots=True)
class PreparedMuxWrite:
    """One complete wire frame reserved for the sole physical writer."""

    wire: bytes
    lane: str
    message_id: str
    global_seq: int
    lane_seq: int


@dataclass(slots=True)
class _ScheduleState:
    drr_cursor: int
    priority_cursor: int
    priority_burst: int
    deficits: dict[str, int]

    def copy(self) -> _ScheduleState:
        return _ScheduleState(
            drr_cursor=self.drr_cursor,
            priority_cursor=self.priority_cursor,
            priority_burst=self.priority_burst,
            deficits=dict(self.deficits),
        )


@dataclass(slots=True)
class _Inflight:
    pending: _PendingMessage
    prepared: PreparedMuxWrite
    proposed_schedule: _ScheduleState
    started: bool = False


@dataclass(slots=True)
class _PendingRead:
    packet: NativeMuxPacket
    wire_bytes: int


class NativeMuxSendQueue:
    """Bounded local scheduler with transactional write settlement.

    Sequence numbers are assigned only when ``prepare_next`` selects a frame.
    A ``not_sent`` settlement rolls that selection back.  Once any byte may
    have been written, callers must settle ``outcome_unknown``; the queue then
    poisons the connection and never retries the ambiguous frame.
    """

    def __init__(
        self,
        *,
        sender_role: str,
        connection_generation: int,
        connection_id: str,
        runtime_incarnation: str,
        lane_max_items: int = DEFAULT_LANE_MAX_ITEMS,
        lane_max_bytes: int = DEFAULT_LANE_MAX_BYTES,
        total_max_items: int = DEFAULT_TOTAL_MAX_ITEMS,
        total_max_bytes: int = DEFAULT_TOTAL_MAX_BYTES,
        drr_quantum_bytes: int = DEFAULT_DRR_QUANTUM_BYTES,
        priority_burst: int = DEFAULT_PRIORITY_BURST,
        connection_max_frames: int = DEFAULT_CONNECTION_MAX_FRAMES,
        connection_max_bytes: int = DEFAULT_CONNECTION_MAX_BYTES,
    ):
        self._lane_max_items = _positive(lane_max_items, "invalid_lane_item_limit")
        self._lane_max_bytes = _positive(lane_max_bytes, "invalid_lane_byte_limit")
        self._total_max_items = _positive(total_max_items, "invalid_total_item_limit")
        self._total_max_bytes = _positive(total_max_bytes, "invalid_total_byte_limit")
        self._quantum = _positive(drr_quantum_bytes, "invalid_drr_quantum")
        self._burst_limit = _positive(priority_burst, "invalid_priority_burst")
        self._connection_max_frames = _positive(
            connection_max_frames,
            "invalid_connection_frame_limit",
        )
        self._connection_max_bytes = _positive(
            connection_max_bytes,
            "invalid_connection_byte_limit",
        )
        if (
            self._lane_max_items > HARD_MAX_QUEUE_ITEMS
            or self._lane_max_bytes > HARD_MAX_QUEUE_BYTES
            or self._total_max_items > HARD_MAX_QUEUE_ITEMS
            or self._total_max_bytes > HARD_MAX_QUEUE_BYTES
            or self._connection_max_frames > HARD_MAX_QUEUE_ITEMS
            or self._connection_max_bytes > HARD_MAX_QUEUE_BYTES
        ):
            _reject("mux_queue_limit_too_large")

        # Reuse the codec as the single identity grammar authority.
        make_mux_header(
            lane="control",
            sender_role=sender_role,
            connection_generation=connection_generation,
            connection_id=connection_id,
            runtime_incarnation=runtime_incarnation,
            global_seq=1,
            lane_seq=1,
            message_id="mux-constructor-check",
            correlation_id=None,
            stream_id=None,
            stream_seq=None,
            payload_kind="control",
            payload=b"",
            priority="control",
        )
        self._sender_role = sender_role
        self._connection_generation = connection_generation
        self._connection_id = connection_id
        self._runtime_incarnation = runtime_incarnation
        self._queues = {lane: deque() for lane in MUX_LANE_ORDER}
        self._lane_bytes = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._total_items = 0
        self._total_bytes = 0
        self._queued_wire_bytes = 0
        self._next_global_seq = 1
        self._next_lane_seq = dict.fromkeys(MUX_LANE_ORDER, 1)
        self._schedule = _ScheduleState(
            drr_cursor=0,
            priority_cursor=0,
            priority_burst=0,
            deficits=dict.fromkeys(MUX_LANE_ORDER, 0),
        )
        self._inflight: _Inflight | None = None
        self._sent_frames = 0
        self._sent_bytes = 0
        self._message_ids: set[str] = set()
        self._poisoned = False

    @property
    def queued_items(self) -> int:
        return self._total_items

    @property
    def queued_bytes(self) -> int:
        return self._total_bytes

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    @property
    def retained_message_ids(self) -> int:
        """Number of connection-scoped IDs retained for duplicate rejection."""

        return len(self._message_ids)

    @property
    def reserved_connection_bytes(self) -> int:
        """Actual sent bytes plus conservative reservations for queued frames."""

        return self._sent_bytes + self._queued_wire_bytes

    def _poison_and_clear(self) -> None:
        self._poisoned = True
        self._inflight = None
        for queue in self._queues.values():
            queue.clear()
        self._lane_bytes = dict.fromkeys(MUX_LANE_ORDER, 0)
        self._total_items = 0
        self._total_bytes = 0
        self._queued_wire_bytes = 0
        self._message_ids.clear()

    def poison(self) -> None:
        self._poison_and_clear()

    def enqueue(
        self,
        *,
        lane: str,
        payload_kind: str,
        payload,
        message_id: str,
        correlation_id: str | None = None,
        stream_id: str | None = None,
        stream_seq: int | None = None,
        trusted_class: str = "normal",
    ) -> None:
        if self._poisoned:
            _reject("mux_connection_poisoned")
        if lane not in MUX_LANES:
            _reject("unknown_mux_lane")
        if trusted_class not in TRUSTED_SCHEDULING_CLASSES:
            _reject("untrusted_scheduling_class")
        raw = _owned_payload(payload)
        # Validate all message-shaped fields before occupying queue capacity.
        admission_header = make_mux_header(
            lane=lane,
            sender_role=self._sender_role,
            connection_generation=self._connection_generation,
            connection_id=self._connection_id,
            runtime_incarnation=self._runtime_incarnation,
            # A frame accepted by this connection can never receive a larger
            # sequence.  Using the cap reserves enough JSON digits regardless
            # of later lane scheduling without allocating the payload wire.
            global_seq=self._connection_max_frames,
            lane_seq=self._connection_max_frames,
            message_id=message_id,
            correlation_id=correlation_id,
            stream_id=stream_id,
            stream_seq=stream_seq,
            payload_kind=payload_kind,
            payload=raw,
            priority=_WIRE_PRIORITY[trusted_class],
        )
        reserved_wire_bytes = (
            _MUX_WIRE_PREFIX_BYTES
            + len(
                json.dumps(
                    admission_header,
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            + len(raw)
        )
        if message_id in self._message_ids:
            _reject("mux_message_id_reused")
        queue = self._queues[lane]
        if len(queue) + 1 > self._lane_max_items:
            _reject("mux_lane_item_limit")
        if self._lane_bytes[lane] + len(raw) > self._lane_max_bytes:
            _reject("mux_lane_byte_limit")
        if self._total_items + 1 > self._total_max_items:
            _reject("mux_total_item_limit")
        if self._total_bytes + len(raw) > self._total_max_bytes:
            _reject("mux_total_byte_limit")
        if self._sent_frames + self._total_items + 1 > self._connection_max_frames:
            _reject("mux_connection_frame_limit")
        if (
            self._sent_bytes + self._queued_wire_bytes + reserved_wire_bytes
            > self._connection_max_bytes
        ):
            _reject("mux_connection_byte_limit")
        queue.append(
            _PendingMessage(
                lane=lane,
                payload_kind=payload_kind,
                payload=raw,
                message_id=message_id,
                correlation_id=correlation_id,
                stream_id=stream_id,
                stream_seq=stream_seq,
                trusted_class=trusted_class,
                reserved_wire_bytes=reserved_wire_bytes,
            )
        )
        self._lane_bytes[lane] += len(raw)
        self._total_items += 1
        self._total_bytes += len(raw)
        self._queued_wire_bytes += reserved_wire_bytes
        self._message_ids.add(message_id)

    def _select_priority(self, state: _ScheduleState) -> str | None:
        if state.priority_burst >= self._burst_limit:
            return None
        for trusted_class in ("control", "urgent"):
            for offset in range(len(MUX_LANE_ORDER)):
                index = (state.priority_cursor + offset) % len(MUX_LANE_ORDER)
                lane = MUX_LANE_ORDER[index]
                queue = self._queues[lane]
                if queue and queue[0].trusted_class == trusted_class:
                    state.priority_cursor = (index + 1) % len(MUX_LANE_ORDER)
                    state.priority_burst += 1
                    return lane
        return None

    def _select_drr(self, state: _ScheduleState) -> str | None:
        nonempty = [lane for lane in MUX_LANE_ORDER if self._queues[lane]]
        if not nonempty:
            return None
        rounds = min(
            max(
                0,
                (self._queues[lane][0].cost - state.deficits[lane] + self._quantum - 1)
                // self._quantum,
            )
            for lane in nonempty
        )
        if rounds:
            for lane in nonempty:
                state.deficits[lane] += rounds * self._quantum
        for offset in range(len(MUX_LANE_ORDER)):
            index = (state.drr_cursor + offset) % len(MUX_LANE_ORDER)
            lane = MUX_LANE_ORDER[index]
            queue = self._queues[lane]
            if queue and queue[0].cost <= state.deficits[lane]:
                state.deficits[lane] -= queue[0].cost
                state.drr_cursor = (index + 1) % len(MUX_LANE_ORDER)
                state.priority_burst = 0
                return lane
        _reject("mux_scheduler_invariant")

    def prepare_next(self) -> PreparedMuxWrite | None:
        if self._poisoned:
            _reject("mux_connection_poisoned")
        if self._inflight is not None:
            _reject("mux_writer_already_reserved")
        proposed = self._schedule.copy()
        lane = self._select_priority(proposed)
        if lane is None:
            lane = self._select_drr(proposed)
        if lane is None:
            return None
        pending = self._queues[lane][0]
        if (
            self._next_global_seq > JSON_SAFE_INTEGER_MAX
            or self._next_lane_seq[lane] > JSON_SAFE_INTEGER_MAX
        ):
            _reject("mux_sequence_exhausted")
        if self._sent_frames + 1 > self._connection_max_frames:
            _reject("mux_connection_frame_limit")
        global_seq = self._next_global_seq
        lane_seq = self._next_lane_seq[lane]
        header = make_mux_header(
            lane=lane,
            sender_role=self._sender_role,
            connection_generation=self._connection_generation,
            connection_id=self._connection_id,
            runtime_incarnation=self._runtime_incarnation,
            global_seq=global_seq,
            lane_seq=lane_seq,
            message_id=pending.message_id,
            correlation_id=pending.correlation_id,
            stream_id=pending.stream_id,
            stream_seq=pending.stream_seq,
            payload_kind=pending.payload_kind,
            payload=pending.payload,
            priority=_WIRE_PRIORITY[pending.trusted_class],
        )
        prepared = PreparedMuxWrite(
            wire=encode_mux_packet(header, pending.payload),
            lane=lane,
            message_id=pending.message_id,
            global_seq=global_seq,
            lane_seq=lane_seq,
        )
        if self._sent_bytes + len(prepared.wire) > self._connection_max_bytes:
            self._poison_and_clear()
            _reject("mux_connection_admission_invariant")
        self._inflight = _Inflight(pending, prepared, proposed)
        return prepared

    def begin_write(self) -> None:
        """Commit the selected sequence immediately before the OS write call."""

        if self._poisoned:
            _reject("mux_connection_poisoned")
        if self._inflight is None:
            _reject("mux_writer_not_reserved")
        if self._inflight.started:
            _reject("mux_write_already_started")
        inflight = self._inflight
        queue = self._queues[inflight.pending.lane]
        if not queue or queue[0] is not inflight.pending:
            self._poison_and_clear()
            _reject("mux_queue_invariant")
        queue.popleft()
        size = len(inflight.pending.payload)
        self._lane_bytes[inflight.pending.lane] -= size
        self._total_items -= 1
        self._total_bytes -= size
        self._queued_wire_bytes -= inflight.pending.reserved_wire_bytes
        self._schedule = inflight.proposed_schedule
        if not queue:
            self._schedule.deficits[inflight.pending.lane] = 0
        self._next_global_seq += 1
        self._next_lane_seq[inflight.pending.lane] += 1
        self._sent_frames += 1
        self._sent_bytes += len(inflight.prepared.wire)
        inflight.started = True

    def settle_write(self, outcome: str) -> None:
        if self._inflight is None:
            _reject("mux_writer_not_reserved")
        if outcome not in WRITE_OUTCOMES:
            _reject("invalid_mux_write_outcome")
        inflight = self._inflight
        if outcome == "outcome_unknown":
            self._poison_and_clear()
            return
        if outcome == "not_sent":
            if inflight.started:
                self._poison_and_clear()
                _reject("mux_write_started_cannot_retry")
            self._inflight = None
            return
        if not inflight.started:
            _reject("mux_write_not_started")
        self._inflight = None


class NativeMuxReceiver:
    """Connection-bound, stateful verifier for one physical reader."""

    def __init__(
        self,
        *,
        sender_role: str,
        connection_generation: int,
        connection_id: str,
        runtime_incarnation: str,
        max_streams: int = DEFAULT_MAX_STREAMS,
        connection_max_frames: int = DEFAULT_CONNECTION_MAX_FRAMES,
        connection_max_bytes: int = DEFAULT_CONNECTION_MAX_BYTES,
    ):
        self._max_streams = _positive(max_streams, "invalid_stream_limit")
        if self._max_streams > HARD_MAX_STREAMS:
            _reject("mux_stream_limit_too_large")
        self._connection_max_frames = _positive(
            connection_max_frames,
            "invalid_connection_frame_limit",
        )
        self._connection_max_bytes = _positive(
            connection_max_bytes,
            "invalid_connection_byte_limit",
        )
        if (
            self._connection_max_frames > HARD_MAX_QUEUE_ITEMS
            or self._connection_max_bytes > HARD_MAX_QUEUE_BYTES
        ):
            _reject("mux_connection_limit_too_large")
        # Validate the complete connection identity with the codec grammar.
        make_mux_header(
            lane="control",
            sender_role=sender_role,
            connection_generation=connection_generation,
            connection_id=connection_id,
            runtime_incarnation=runtime_incarnation,
            global_seq=1,
            lane_seq=1,
            message_id="mux-receiver-check",
            correlation_id=None,
            stream_id=None,
            stream_seq=None,
            payload_kind="control",
            payload=b"",
            priority="control",
        )
        self._sender_role = sender_role
        self._connection_generation = connection_generation
        self._connection_id = connection_id
        self._runtime_incarnation = runtime_incarnation
        self._next_global_seq = 1
        self._next_lane_seq = dict.fromkeys(MUX_LANE_ORDER, 1)
        self._next_stream_seq: dict[str, int] = {}
        self._message_ids: set[str] = set()
        self._accepted_frames = 0
        self._accepted_bytes = 0
        self._pending_read: _PendingRead | None = None
        self._poisoned = False

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    @property
    def retained_message_ids(self) -> int:
        return len(self._message_ids)

    @property
    def tracked_streams(self) -> int:
        return len(self._next_stream_seq)

    def poison(self) -> None:
        self._poisoned = True
        self._pending_read = None
        self._message_ids.clear()
        self._next_stream_seq.clear()

    def prepare_wire(self, wire) -> NativeMuxPacket:
        if self._poisoned:
            _reject("mux_connection_poisoned")
        if self._pending_read is not None:
            _reject("mux_reader_already_reserved")
        try:
            try:
                wire_bytes = memoryview(wire).nbytes
            except (TypeError, ValueError, BufferError):
                _reject("invalid_mux_wire")
            if self._accepted_frames + 1 > self._connection_max_frames:
                _reject("mux_connection_frame_limit")
            if self._accepted_bytes + wire_bytes > self._connection_max_bytes:
                _reject("mux_connection_byte_limit")
            packet = decode_mux_packet(wire)
            header: Mapping = packet.header
            if header["sender_role"] != self._sender_role:
                _reject("mux_sender_role_mismatch")
            if header["connection_generation"] != self._connection_generation:
                _reject("mux_connection_generation_mismatch")
            if header["connection_id"] != self._connection_id:
                _reject("mux_connection_id_mismatch")
            if header["runtime_incarnation"] != self._runtime_incarnation:
                _reject("mux_runtime_incarnation_mismatch")
            lane = header["lane"]
            if self._next_global_seq > JSON_SAFE_INTEGER_MAX:
                _reject("mux_sequence_exhausted")
            if header["global_seq"] != self._next_global_seq:
                _reject("mux_global_sequence_mismatch")
            if self._next_lane_seq[lane] > JSON_SAFE_INTEGER_MAX:
                _reject("mux_sequence_exhausted")
            if header["lane_seq"] != self._next_lane_seq[lane]:
                _reject("mux_lane_sequence_mismatch")
            if header["message_id"] in self._message_ids:
                _reject("mux_message_id_reused")
            stream_id = header["stream_id"]
            if lane == "remote_stream":
                if stream_id not in self._next_stream_seq:
                    if len(self._next_stream_seq) >= self._max_streams:
                        _reject("mux_stream_limit")
                    expected_stream = 1
                else:
                    expected_stream = self._next_stream_seq[stream_id]
                if expected_stream > JSON_SAFE_INTEGER_MAX:
                    _reject("mux_sequence_exhausted")
                if header["stream_seq"] != expected_stream:
                    _reject("mux_stream_sequence_mismatch")
            self._pending_read = _PendingRead(packet=packet, wire_bytes=wire_bytes)
            return packet
        except (NativeMuxProtocolError, NativePipeMultiplexerError):
            self.poison()
            raise

    def settle_read(self, outcome: str) -> None:
        if self._pending_read is None:
            _reject("mux_reader_not_reserved")
        if outcome not in READ_OUTCOMES:
            _reject("invalid_mux_read_outcome")
        pending = self._pending_read
        self._pending_read = None
        if outcome == "outcome_unknown":
            self.poison()
            return
        if outcome == "not_delivered":
            return
        header = pending.packet.header
        lane = header["lane"]
        self._next_global_seq += 1
        self._next_lane_seq[lane] += 1
        self._message_ids.add(header["message_id"])
        if lane == "remote_stream":
            self._next_stream_seq[header["stream_id"]] = header["stream_seq"] + 1
        self._accepted_frames += 1
        self._accepted_bytes += pending.wire_bytes


__all__ = [
    "DEFAULT_CONNECTION_MAX_BYTES",
    "DEFAULT_CONNECTION_MAX_FRAMES",
    "DEFAULT_DRR_QUANTUM_BYTES",
    "DEFAULT_LANE_MAX_BYTES",
    "DEFAULT_LANE_MAX_ITEMS",
    "DEFAULT_MAX_STREAMS",
    "DEFAULT_PRIORITY_BURST",
    "DEFAULT_TOTAL_MAX_BYTES",
    "DEFAULT_TOTAL_MAX_ITEMS",
    "HARD_MAX_QUEUE_BYTES",
    "HARD_MAX_QUEUE_ITEMS",
    "HARD_MAX_STREAMS",
    "MUX_LANE_ORDER",
    "OBSERVED_GUI_PROJECTION_HIGH_WATER",
    "OBSERVED_MUX_QUEUE_HIGH_WATER_BYTES",
    "OBSERVED_MUX_QUEUE_HIGH_WATER_ITEMS",
    "READ_OUTCOMES",
    "REFERENCE_BRIDGE_MAX_BYTES",
    "REFERENCE_BRIDGE_MAX_FRAMES",
    "TRUSTED_SCHEDULING_CLASSES",
    "WRITE_OUTCOMES",
    "NativeMuxReceiver",
    "NativeMuxSendQueue",
    "NativePipeMultiplexerError",
    "PreparedMuxWrite",
]
