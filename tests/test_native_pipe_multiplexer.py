from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_mux_protocol import (
    decode_mux_packet,
    encode_mux_packet,
    make_mux_header,
)
from genshin_app.agent_chat.native_pipe_multiplexer import (
    DEFAULT_CONNECTION_MAX_BYTES,
    DEFAULT_CONNECTION_MAX_FRAMES,
    DEFAULT_TOTAL_MAX_BYTES,
    DEFAULT_TOTAL_MAX_ITEMS,
    HARD_MAX_QUEUE_BYTES,
    HARD_MAX_QUEUE_ITEMS,
    OBSERVED_GUI_PROJECTION_HIGH_WATER,
    OBSERVED_MUX_QUEUE_HIGH_WATER_BYTES,
    OBSERVED_MUX_QUEUE_HIGH_WATER_ITEMS,
    REFERENCE_BRIDGE_MAX_BYTES,
    REFERENCE_BRIDGE_MAX_FRAMES,
    NativeMuxReceiver,
    NativeMuxSendQueue,
    NativePipeMultiplexerError,
)

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _ROOT / "agent-runtime"
_FIXTURE = json.loads(
    (_RUNTIME / "native-pipe-mux-core-v1-vectors.json").read_text(encoding="utf-8")
)
_NODE_PROBE = _RUNTIME / "src" / "native-pipe-multiplexer-probe.mjs"


def _error(code: str, operation) -> NativePipeMultiplexerError:
    with pytest.raises(NativePipeMultiplexerError) as caught:
        operation()
    assert caught.value.code == code
    return caught.value


def _queue(**overrides) -> NativeMuxSendQueue:
    options = {
        "sender_role": "host",
        "connection_generation": 1,
        "connection_id": "connection-1",
        "runtime_incarnation": "runtime-1",
    }
    options.update(overrides)
    return NativeMuxSendQueue(**options)


def _receiver(**overrides) -> NativeMuxReceiver:
    options = {
        "sender_role": "host",
        "connection_generation": 1,
        "connection_id": "connection-1",
        "runtime_incarnation": "runtime-1",
    }
    options.update(overrides)
    return NativeMuxReceiver(**options)


def _enqueue(queue: NativeMuxSendQueue, row: dict) -> None:
    queue.enqueue(
        lane=row["lane"],
        payload_kind=row["payload_kind"],
        payload=row["payload"].encode("utf-8"),
        message_id=row["message_id"],
        correlation_id=row.get("correlation_id"),
        stream_id=row.get("stream_id"),
        stream_seq=row.get("stream_seq"),
        trusted_class=row["trusted_class"],
    )


def _send_one(queue: NativeMuxSendQueue) -> tuple[bytes, dict]:
    prepared = queue.prepare_next()
    assert prepared is not None
    packet = decode_mux_packet(prepared.wire)
    queue.begin_write()
    queue.settle_write("sent")
    return prepared.wire, dict(packet.header)


def _wire(
    *,
    global_seq: int,
    lane_seq: int,
    message_id: str,
    lane: str = "legacy_turn",
    connection_id: str = "connection-1",
    stream_id: str | None = None,
    stream_seq: int | None = None,
) -> bytes:
    payload = f"payload:{message_id}".encode()
    header = make_mux_header(
        lane=lane,
        sender_role="host",
        connection_generation=1,
        connection_id=connection_id,
        runtime_incarnation="runtime-1",
        global_seq=global_seq,
        lane_seq=lane_seq,
        message_id=message_id,
        correlation_id=None,
        stream_id=stream_id,
        stream_seq=stream_seq,
        payload_kind="legacy_frame" if lane == "legacy_turn" else "stream_data",
        payload=payload,
        priority="control",  # Receiver must not treat this as authority.
    )
    return encode_mux_packet(header, payload)


def test_capacity_fields_separate_observation_default_and_hard_cap():
    assert OBSERVED_MUX_QUEUE_HIGH_WATER_ITEMS is None
    assert OBSERVED_MUX_QUEUE_HIGH_WATER_BYTES is None
    assert OBSERVED_GUI_PROJECTION_HIGH_WATER == 22_187
    assert DEFAULT_TOTAL_MAX_ITEMS == 131_072
    assert DEFAULT_TOTAL_MAX_BYTES == 512 * 1024 * 1024
    assert DEFAULT_CONNECTION_MAX_FRAMES == REFERENCE_BRIDGE_MAX_FRAMES == 524_288
    assert DEFAULT_CONNECTION_MAX_BYTES == REFERENCE_BRIDGE_MAX_BYTES == 2 * 1024**3
    assert HARD_MAX_QUEUE_ITEMS == DEFAULT_CONNECTION_MAX_FRAMES
    assert HARD_MAX_QUEUE_BYTES == DEFAULT_CONNECTION_MAX_BYTES
    assert DEFAULT_TOTAL_MAX_ITEMS < HARD_MAX_QUEUE_ITEMS
    assert DEFAULT_TOTAL_MAX_BYTES < HARD_MAX_QUEUE_BYTES


def test_send_lease_rolls_back_only_before_write_and_poison_on_ambiguity():
    queue = _queue()
    row = {
        "lane": "legacy_turn",
        "payload_kind": "legacy_frame",
        "payload": "legacy\u0000bytes",
        "message_id": "message-1",
        "trusted_class": "normal",
    }
    _enqueue(queue, row)
    first = queue.prepare_next()
    assert first is not None
    _error("mux_writer_already_reserved", queue.prepare_next)
    queue.settle_write("not_sent")
    second = queue.prepare_next()
    assert second == first
    queue.begin_write()
    queue.settle_write("sent")
    assert queue.queued_items == 0

    ambiguous = _queue()
    _enqueue(ambiguous, {**row, "message_id": "message-2"})
    assert ambiguous.prepare_next() is not None
    ambiguous.begin_write()
    ambiguous.settle_write("outcome_unknown")
    assert ambiguous.poisoned is True
    assert ambiguous.retained_message_ids == 0
    _error("mux_connection_poisoned", ambiguous.prepare_next)

    illegal_retry = _queue()
    _enqueue(illegal_retry, {**row, "message_id": "message-3"})
    assert illegal_retry.prepare_next() is not None
    illegal_retry.begin_write()
    _error(
        "mux_write_started_cannot_retry", lambda: illegal_retry.settle_write("not_sent")
    )
    assert illegal_retry.poisoned is True


def test_lane_fifo_priority_derivation_and_bounded_burst_are_fair():
    queue = _queue(priority_burst=2, drr_quantum_bytes=4)
    rows = []
    for index in range(12):
        rows.append(
            {
                "lane": "control",
                "payload_kind": "control",
                "payload": "c",
                "message_id": f"control-{index}",
                "trusted_class": "control",
            }
        )
    rows.extend(
        [
            {
                "lane": "legacy_turn",
                "payload_kind": "legacy_frame",
                "payload": "normal-first",
                "message_id": "legacy-normal",
                "trusted_class": "normal",
            },
            {
                "lane": "legacy_turn",
                "payload_kind": "legacy_frame",
                "payload": "urgent-second",
                "message_id": "legacy-urgent",
                "trusted_class": "urgent",
            },
            {
                "lane": "remote_unary",
                "payload_kind": "remote_call",
                "payload": "u",
                "message_id": "unary-normal",
                "trusted_class": "normal",
            },
            {
                "lane": "bundle",
                "payload_kind": "bundle_chunk",
                "payload": "b",
                "message_id": "bundle-normal",
                "trusted_class": "normal",
            },
        ]
    )
    for row in rows:
        _enqueue(queue, row)
    order = []
    priorities = {}
    while queue.queued_items:
        _raw, header = _send_one(queue)
        order.append(header["message_id"])
        priorities[header["message_id"]] = header["priority"]

    # Continuous control traffic cannot starve normal lane heads.
    # The 12-byte head needs three DRR quanta, but is still selected while
    # bounded control work exists rather than being permanently starved.
    assert order.index("legacy-normal") <= 14
    assert order.index("unary-normal") < 14
    assert order.index("bundle-normal") < 15
    # Urgent never jumps over an earlier item in the same lane.
    assert order.index("legacy-normal") < order.index("legacy-urgent")
    assert priorities["legacy-normal"] == "normal"
    assert priorities["legacy-urgent"] == "urgent"


def test_byte_drr_handles_large_and_small_heads_without_starvation():
    queue = _queue(priority_burst=1, drr_quantum_bytes=4)
    _enqueue(
        queue,
        {
            "lane": "legacy_turn",
            "payload_kind": "legacy_frame",
            "payload": "x" * 33,
            "message_id": "large",
            "trusted_class": "normal",
        },
    )
    _enqueue(
        queue,
        {
            "lane": "bundle",
            "payload_kind": "bundle_chunk",
            "payload": "x",
            "message_id": "small",
            "trusted_class": "normal",
        },
    )
    _first_wire, first = _send_one(queue)
    _second_wire, second = _send_one(queue)
    assert [first["message_id"], second["message_id"]] == ["small", "large"]


def test_queue_capacity_rejection_is_atomic_and_message_ids_are_unique():
    queue = _queue(
        lane_max_items=1,
        lane_max_bytes=16,
        total_max_items=2,
        total_max_bytes=32,
    )
    row = {
        "lane": "bundle",
        "payload_kind": "bundle_chunk",
        "payload": "1234",
        "message_id": "capacity-1",
        "trusted_class": "normal",
    }
    _enqueue(queue, row)
    before = (queue.queued_items, queue.queued_bytes)
    _error("mux_message_id_reused", lambda: _enqueue(queue, row))
    _error(
        "mux_lane_item_limit",
        lambda: _enqueue(
            queue,
            {**row, "message_id": "capacity-2"},
        ),
    )
    assert (queue.queued_items, queue.queued_bytes) == before


def test_connection_wire_capacity_rejects_atomically_at_enqueue():
    queue = _queue(connection_max_bytes=100)
    row = {
        "lane": "control",
        "payload_kind": "control",
        "payload": "x",
        "message_id": "too-large-for-connection",
        "trusted_class": "control",
    }
    _error("mux_connection_byte_limit", lambda: _enqueue(queue, row))
    assert queue.queued_items == 0
    assert queue.queued_bytes == 0
    assert queue.retained_message_ids == 0
    assert queue.prepare_next() is None


def test_connection_reservation_is_stable_across_not_sent_at_exact_capacity():
    rows = [
        {
            "lane": "legacy_turn",
            "payload_kind": "legacy_frame",
            "payload": "first" * 20,
            "message_id": "critical-first",
            "trusted_class": "normal",
        },
        {
            "lane": "bundle",
            "payload_kind": "bundle_chunk",
            "payload": "second" * 20,
            "message_id": "critical-second",
            "trusted_class": "normal",
        },
    ]
    sizing = _queue()
    for row in rows:
        _enqueue(sizing, row)
    exact_capacity = sizing.reserved_connection_bytes

    queue = _queue(connection_max_bytes=exact_capacity)
    for row in rows:
        _enqueue(queue, row)
    assert queue.reserved_connection_bytes == exact_capacity
    _error(
        "mux_connection_byte_limit",
        lambda: _enqueue(queue, {**rows[0], "message_id": "critical-third"}),
    )

    first = queue.prepare_next()
    assert first is not None
    queue.settle_write("not_sent")
    assert queue.reserved_connection_bytes == exact_capacity
    assert queue.prepare_next() == first
    queue.begin_write()
    queue.settle_write("sent")
    assert queue.reserved_connection_bytes <= exact_capacity
    assert queue.prepare_next() is not None
    queue.begin_write()
    queue.settle_write("sent")
    assert queue.queued_items == 0


def test_receiver_delivery_lease_commits_only_after_downstream_acceptance():
    queue = _queue()
    row = {
        "lane": "legacy_turn",
        "payload_kind": "legacy_frame",
        "payload": "opaque\u0000\u00ff",
        "message_id": "delivery-1",
        "trusted_class": "normal",
    }
    _enqueue(queue, row)
    wire, header = _send_one(queue)
    receiver = _receiver()
    first = receiver.prepare_wire(wire)
    assert bytes(first.payload) == row["payload"].encode()
    receiver.settle_read("not_delivered")
    retry = receiver.prepare_wire(wire)
    assert retry.header == header
    receiver.settle_read("accepted")
    _error("mux_global_sequence_mismatch", lambda: receiver.prepare_wire(wire))
    assert receiver.poisoned is True


@pytest.mark.parametrize(
    "wire,code",
    [
        (
            _wire(global_seq=2, lane_seq=1, message_id="skip"),
            "mux_global_sequence_mismatch",
        ),
        (
            _wire(
                global_seq=1,
                lane_seq=1,
                message_id="cross",
                connection_id="connection-other",
            ),
            "mux_connection_id_mismatch",
        ),
    ],
)
def test_receiver_rejects_and_poisons_sequence_and_connection_violations(wire, code):
    receiver = _receiver()
    _error(code, lambda: receiver.prepare_wire(wire))
    assert receiver.poisoned is True
    _error("mux_connection_poisoned", lambda: receiver.prepare_wire(wire))


def test_receiver_stream_sequence_and_message_identity_are_connection_scoped():
    receiver = _receiver(max_streams=2)
    first = _wire(
        global_seq=1,
        lane_seq=1,
        message_id="stream-message-1",
        lane="remote_stream",
        stream_id="stream-1",
        stream_seq=1,
    )
    receiver.prepare_wire(first)
    receiver.settle_read("accepted")
    assert receiver.retained_message_ids == 1
    assert receiver.tracked_streams == 1
    skipped = _wire(
        global_seq=2,
        lane_seq=2,
        message_id="stream-message-2",
        lane="remote_stream",
        stream_id="stream-1",
        stream_seq=3,
    )
    _error("mux_stream_sequence_mismatch", lambda: receiver.prepare_wire(skipped))
    assert receiver.poisoned is True
    assert receiver.retained_message_ids == 0
    assert receiver.tracked_streams == 0

    duplicate = _receiver()
    one = _wire(global_seq=1, lane_seq=1, message_id="same-message")
    duplicate.prepare_wire(one)
    duplicate.settle_read("accepted")
    two = _wire(global_seq=2, lane_seq=2, message_id="same-message")
    _error("mux_message_id_reused", lambda: duplicate.prepare_wire(two))


def test_cross_language_fixture_schedule_and_legacy_bytes():
    options = _FIXTURE["scheduler"]
    queue = _queue(
        priority_burst=options["priority_burst"],
        drr_quantum_bytes=options["drr_quantum_bytes"],
    )
    for row in _FIXTURE["messages"]:
        _enqueue(queue, row)
    summary = []
    while queue.queued_items:
        wire, header = _send_one(queue)
        summary.append(
            {
                "message_id": header["message_id"],
                "lane": header["lane"],
                "global_seq": header["global_seq"],
                "lane_seq": header["lane_seq"],
                "priority": header["priority"],
                "payload_hex": decode_mux_packet(wire).payload.hex(),
            }
        )
    assert summary == _FIXTURE["expected"]


def test_node_mux_core_probe_with_pinned_runtime():
    configured = os.environ.get("GENSHIN_NATIVE_NODE")
    if not configured:
        pytest.skip("set GENSHIN_NATIVE_NODE to the pinned Node executable")
    completed = subprocess.run(
        [str(Path(configured).resolve()), str(_NODE_PROBE)],
        cwd=_RUNTIME,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.stderr == ""
    receipt = json.loads(completed.stdout)
    assert receipt == {
        "contract": "genshin-native-pipe-mux-core-v1",
        "status": "passed",
        "messages": len(_FIXTURE["messages"]),
        "productionOwner": False,
    }
