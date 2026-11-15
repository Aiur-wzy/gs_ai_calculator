from __future__ import annotations

import base64
import copy
import hashlib
import json
import struct
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_mux_protocol import (
    MAX_MUX_BODY_BYTES,
    MAX_MUX_HEADER_BYTES,
    MAX_MUX_PAYLOAD_BYTES,
    MAX_MUX_WIRE_BYTES,
    MUX_HEADER_FIELDS,
    NativeMuxProtocolError,
    decode_mux_packet,
    encode_mux_packet,
    make_mux_header,
    validate_mux_packet,
)

_ROOT = Path(__file__).resolve().parents[1]
_VECTORS = json.loads(
    (_ROOT / "agent-runtime" / "native-mux-v1-vectors.json").read_text(encoding="utf-8")
)


def _raw_packet(header_bytes: bytes, payload: bytes) -> bytes:
    body_length = 4 + len(header_bytes) + len(payload)
    return struct.pack(">II", body_length, len(header_bytes)) + header_bytes + payload


def _canonical(header: dict) -> bytes:
    return json.dumps(
        header,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _vector(name: str) -> tuple[dict, bytes, bytes]:
    row = next(item for item in _VECTORS["vectors"] if item["name"] == name)
    return (
        row["header"],
        base64.b64decode(row["payload_base64"]),
        base64.b64decode(row["packet_base64"]),
    )


def _error(code: str, operation) -> NativeMuxProtocolError:
    with pytest.raises(NativeMuxProtocolError) as caught:
        operation()
    assert caught.value.code == code
    return caught.value


def test_python_and_node_golden_vectors_are_byte_identical_and_round_trip():
    assert _VECTORS["contract"] == "genshin-native-pipe-mux-v1-golden"
    assert _VECTORS["limits"] == {
        "header_bytes": MAX_MUX_HEADER_BYTES,
        "body_bytes": MAX_MUX_BODY_BYTES,
        "payload_bytes": MAX_MUX_PAYLOAD_BYTES,
        "wire_bytes": MAX_MUX_WIRE_BYTES,
    }
    assert {row["producer"] for row in _VECTORS["vectors"]} == {"python", "node"}
    for row in _VECTORS["vectors"]:
        header = row["header"]
        payload = base64.b64decode(row["payload_base64"])
        expected = base64.b64decode(row["packet_base64"])
        assert encode_mux_packet(header, payload) == expected
        decoded = decode_mux_packet(expected)
        assert decoded.header == header
        assert decoded.payload == payload


def test_encoding_is_deterministic_and_legacy_payload_is_never_interpreted():
    header, payload, expected = _vector("python_legacy_frame")
    reversed_header = dict(reversed(tuple(header.items())))
    assert encode_mux_packet(reversed_header, bytearray(payload)) == expected
    assert encode_mux_packet(copy.deepcopy(header), memoryview(payload)) == expected
    decoded = decode_mux_packet(expected)
    assert decoded.payload == payload
    assert decoded.payload.endswith(b"\x00\xff\x80\x7f")
    with pytest.raises(TypeError):
        decoded.header["lane"] = "control"
    assert decoded.header == header
    assert encode_mux_packet(decoded.header, decoded.payload) == expected


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda h: h.update(extra=True), "unknown_header_field"),
        (lambda h: h.pop("priority"), "missing_header_field"),
        (lambda h: h.update(protocol="other"), "unknown_protocol"),
        (lambda h: h.update(version=2), "unknown_version"),
        (lambda h: h.update(lane="other"), "unknown_lane"),
        (lambda h: h.update(sender_role="other"), "unknown_sender_role"),
        (lambda h: h.update(priority="other"), "unknown_priority"),
        (lambda h: h.update(global_seq=True), "invalid_global_seq"),
        (lambda h: h.update(payload_kind="bad kind"), "invalid_payload_kind"),
    ],
)
def test_header_schema_is_exact(mutation, code):
    header, payload, _expected = _vector("python_legacy_frame")
    changed = copy.deepcopy(header)
    mutation(changed)
    _error(code, lambda: validate_mux_packet(changed, payload))


def test_stream_identity_is_lane_scoped():
    header, payload, _expected = _vector("node_remote_stream")
    missing_stream = {**header, "stream_id": None}
    _error("stream_identity_required", lambda: validate_mux_packet(missing_stream, payload))

    legacy_header, legacy_payload, _legacy = _vector("python_legacy_frame")
    unexpected_stream = {**legacy_header, "stream_id": "stream-1", "stream_seq": 1}
    _error("stream_identity_forbidden", lambda: validate_mux_packet(unexpected_stream, legacy_payload))


def test_decoder_rejects_duplicate_invalid_utf8_and_noncanonical_header_json():
    header, payload, _expected = _vector("python_legacy_frame")
    canonical = _canonical(header)
    duplicate = canonical[:-1] + b',"version":1}'
    _error("duplicate_header_key", lambda: decode_mux_packet(_raw_packet(duplicate, payload)))

    invalid_utf8 = b'{"protocol":"genshin-native-pipe-mux","x":"\xff"}'
    _error("invalid_header_utf8", lambda: decode_mux_packet(_raw_packet(invalid_utf8, payload)))

    noncanonical = json.dumps(header, ensure_ascii=False, indent=1).encode("utf-8")
    _error("noncanonical_header_json", lambda: decode_mux_packet(_raw_packet(noncanonical, payload)))


def test_decoder_rejects_outer_header_payload_length_and_digest_mismatches():
    header, payload, expected = _vector("python_legacy_frame")
    truncated = expected[:7]
    _error("mux_packet_truncated", lambda: decode_mux_packet(truncated))

    wrong_body = bytearray(expected)
    wrong_body[:4] = struct.pack(">I", len(expected))
    _error("mux_body_length_mismatch", lambda: decode_mux_packet(wrong_body))

    wrong_header = bytearray(expected)
    wrong_header[4:8] = struct.pack(">I", len(expected))
    _error("mux_header_length_mismatch", lambda: decode_mux_packet(wrong_header))

    wrong_length_header = {**header, "payload_length": len(payload) + 1}
    _error(
        "payload_length_mismatch",
        lambda: decode_mux_packet(_raw_packet(_canonical(wrong_length_header), payload)),
    )
    wrong_digest_header = {**header, "payload_sha256": "0" * 64}
    _error(
        "payload_sha256_mismatch",
        lambda: decode_mux_packet(_raw_packet(_canonical(wrong_digest_header), payload)),
    )


def test_framing_limits_accept_boundaries_and_reject_oversize_prefixes_early():
    boundary_body = struct.pack(">I", MAX_MUX_BODY_BYTES)
    _error("mux_packet_truncated", lambda: decode_mux_packet(boundary_body))
    _error(
        "mux_packet_too_large",
        lambda: decode_mux_packet(struct.pack(">I", MAX_MUX_BODY_BYTES + 1)),
    )
    _error(
        "mux_packet_too_large",
        lambda: decode_mux_packet(struct.pack(">I", (1 << 32) - 1)),
    )

    boundary_header = b" " * MAX_MUX_HEADER_BYTES
    _error(
        "invalid_header_json",
        lambda: decode_mux_packet(_raw_packet(boundary_header, b"")),
    )
    oversized_header = b" " * (MAX_MUX_HEADER_BYTES + 1)
    _error(
        "mux_header_too_large",
        lambda: decode_mux_packet(_raw_packet(oversized_header, b"")),
    )

    header, _payload, _wire = _vector("python_control_empty")
    boundary_payload = {**header, "payload_length": MAX_MUX_PAYLOAD_BYTES}
    _error(
        "payload_length_mismatch",
        lambda: validate_mux_packet(boundary_payload, b""),
    )
    oversized_payload = {**header, "payload_length": MAX_MUX_PAYLOAD_BYTES + 1}
    _error(
        "mux_packet_too_large",
        lambda: validate_mux_packet(oversized_payload, b""),
    )


def test_builder_derives_length_and_digest_and_packet_owns_payload_copy():
    payload = bytearray(b"mutable")
    header = make_mux_header(
        lane="bundle",
        sender_role="runtime",
        connection_generation=5,
        connection_id="connection-5",
        runtime_incarnation="runtime-5",
        global_seq=8,
        lane_seq=2,
        message_id="message-8",
        correlation_id="bundle-1",
        stream_id=None,
        stream_seq=None,
        payload_kind="bundle_chunk",
        payload=payload,
        priority="normal",
    )
    packet = validate_mux_packet(header, payload)
    payload[:] = b"changed"
    assert packet.payload == b"mutable"
    assert header["payload_length"] == 7
    assert header["payload_sha256"] == hashlib.sha256(b"mutable").hexdigest()
    assert frozenset(header) == MUX_HEADER_FIELDS


def test_rejections_never_reflect_payload_or_header_body_in_diagnostics():
    secret = b"MUX_SECRET_MUST_NOT_APPEAR"
    header = make_mux_header(
        lane="legacy_turn",
        sender_role="host",
        connection_generation=1,
        connection_id="connection-secret",
        runtime_incarnation="runtime-secret",
        global_seq=1,
        lane_seq=1,
        message_id="message-secret",
        correlation_id=None,
        stream_id=None,
        stream_seq=None,
        payload_kind="legacy_frame",
        payload=secret,
        priority="normal",
    )
    damaged = {**header, "payload_sha256": "0" * 64}
    error = _error("payload_sha256_mismatch", lambda: validate_mux_packet(damaged, secret))
    rendered = f"{error!s}\n{error!r}\n{vars(error)!r}"
    assert secret.decode("ascii") not in rendered

    invalid_utf8 = b'{"MUX_SECRET_MUST_NOT_APPEAR":"\xff"}'
    error = _error(
        "invalid_header_utf8",
        lambda: decode_mux_packet(_raw_packet(invalid_utf8, b"")),
    )
    assert error.__context__ is None
    assert secret.decode("ascii") not in f"{error!s}\n{error!r}\n{vars(error)!r}"
