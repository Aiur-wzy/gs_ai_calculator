"""Strict, transport-free primitives for the native pipe mux v1 envelope.

The mux envelope deliberately treats its payload as opaque bytes.  This module
does not own a pipe, a reader loop, routing state, or any Agent semantics.  It
only freezes the cross-language bytes that a later transport may carry.

``priority`` is untrusted wire metadata, not an authority grant.  A receiver
must never promote work because the peer selected a priority value; a future
scheduler must derive effective priority from its own trusted classification.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

MUX_PROTOCOL = "genshin-native-pipe-mux"
MUX_VERSION = 1
JSON_SAFE_INTEGER_MAX = (1 << 53) - 1
UINT32_MAX = (1 << 32) - 1
MAX_MUX_HEADER_BYTES = 64 * 1024
MAX_MUX_BODY_BYTES = 128 * 1024 * 1024

MUX_LANES = frozenset({
    "control",
    "legacy_turn",
    "remote_unary",
    "remote_stream",
    "bundle",
})
MUX_SENDER_ROLES = frozenset({"host", "runtime"})
MUX_PRIORITIES = frozenset({"normal", "urgent", "control"})

MUX_HEADER_FIELDS = frozenset({
    "protocol",
    "version",
    "lane",
    "sender_role",
    "connection_generation",
    "connection_id",
    "runtime_incarnation",
    "global_seq",
    "lane_seq",
    "message_id",
    "correlation_id",
    "stream_id",
    "stream_seq",
    "payload_kind",
    "payload_length",
    "payload_sha256",
    "priority",
})

_UINT32 = struct.Struct(">I")
_HEADER_LENGTH_BYTES = _UINT32.size
# Reserve the full header budget so the independently bounded header and
# payload can always coexist inside one body.  The body also contains the
# uint32 header-length field; the outer body-length prefix is not in the body.
MAX_MUX_PAYLOAD_BYTES = (
    MAX_MUX_BODY_BYTES - _HEADER_LENGTH_BYTES - MAX_MUX_HEADER_BYTES
)
MAX_MUX_WIRE_BYTES = _UINT32.size + MAX_MUX_BODY_BYTES
# A future streaming reader MUST reject a body prefix above
# MAX_MUX_BODY_BYTES before reading or allocating that body.
_MAPPING_PROXY_TYPE = type(MappingProxyType({}))
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_PAYLOAD_KIND = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class NativeMuxProtocolError(ValueError):
    """A deterministic rejection whose message never reflects wire bytes."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject(code: str) -> None:
    raise NativeMuxProtocolError(code)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _reject("duplicate_header_key")
        result[key] = value
    return result


def _reject_constant(_value):
    _reject("invalid_header_json")


def _canonical_header_json(header: Mapping) -> bytes:
    encoded = None
    try:
        encoded = json.dumps(
            dict(header),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        pass
    if encoded is None:
        _reject("invalid_header_json")
    if len(encoded) > MAX_MUX_HEADER_BYTES:
        _reject("mux_header_too_large")
    return encoded


def _identity(value, code: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if type(value) is not str or _IDENTITY.fullmatch(value) is None:
        _reject(code)


def _positive_safe_integer(value, code: str) -> None:
    if type(value) is not int or not 1 <= value <= JSON_SAFE_INTEGER_MAX:
        _reject(code)


def _bounded_bytes(payload, maximum_bytes: int) -> bytes:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        _reject("invalid_payload_bytes")
    view = None
    try:
        view = memoryview(payload)
    except (TypeError, ValueError, BufferError):
        pass
    if view is None:
        _reject("invalid_payload_bytes")
    if view.nbytes > maximum_bytes:
        _reject("mux_packet_too_large")
    if type(payload) is bytes:
        return payload
    raw = None
    try:
        raw = view.tobytes()
    except (TypeError, ValueError, BufferError):
        pass
    if raw is None:
        _reject("invalid_payload_bytes")
    return raw


def _validated_header(header: Mapping) -> dict:
    if type(header) not in {dict, _MAPPING_PROXY_TYPE}:
        _reject("invalid_header_object")
    fields = frozenset(header)
    if fields != MUX_HEADER_FIELDS:
        if fields - MUX_HEADER_FIELDS:
            _reject("unknown_header_field")
        _reject("missing_header_field")

    value = dict(header)
    if value["protocol"] != MUX_PROTOCOL:
        _reject("unknown_protocol")
    if type(value["version"]) is not int or value["version"] != MUX_VERSION:
        _reject("unknown_version")
    if type(value["lane"]) is not str or value["lane"] not in MUX_LANES:
        _reject("unknown_lane")
    if type(value["sender_role"]) is not str or value["sender_role"] not in MUX_SENDER_ROLES:
        _reject("unknown_sender_role")
    if type(value["priority"]) is not str or value["priority"] not in MUX_PRIORITIES:
        _reject("unknown_priority")

    _positive_safe_integer(value["connection_generation"], "invalid_connection_generation")
    _identity(value["connection_id"], "invalid_connection_id")
    _identity(value["runtime_incarnation"], "invalid_runtime_incarnation")
    _positive_safe_integer(value["global_seq"], "invalid_global_seq")
    _positive_safe_integer(value["lane_seq"], "invalid_lane_seq")
    _identity(value["message_id"], "invalid_message_id")
    _identity(value["correlation_id"], "invalid_correlation_id", nullable=True)
    _identity(value["stream_id"], "invalid_stream_id", nullable=True)

    if value["lane"] == "remote_stream":
        if value["stream_id"] is None:
            _reject("stream_identity_required")
        _positive_safe_integer(value["stream_seq"], "invalid_stream_seq")
    elif value["stream_id"] is not None or value["stream_seq"] is not None:
        _reject("stream_identity_forbidden")

    if type(value["payload_kind"]) is not str or _PAYLOAD_KIND.fullmatch(value["payload_kind"]) is None:
        _reject("invalid_payload_kind")
    if type(value["payload_length"]) is not int or not 0 <= value["payload_length"] <= UINT32_MAX:
        _reject("invalid_payload_length")
    if value["payload_length"] > MAX_MUX_PAYLOAD_BYTES:
        _reject("mux_packet_too_large")
    if type(value["payload_sha256"]) is not str or _SHA256.fullmatch(value["payload_sha256"]) is None:
        _reject("invalid_payload_sha256")
    return value


@dataclass(frozen=True, slots=True)
class NativeMuxPacket:
    """One validated mux header and an owned copy of its opaque payload."""

    header: Mapping
    payload: bytes


def make_mux_header(
    *,
    lane: str,
    sender_role: str,
    connection_generation: int,
    connection_id: str,
    runtime_incarnation: str,
    global_seq: int,
    lane_seq: int,
    message_id: str,
    correlation_id: str | None,
    stream_id: str | None,
    stream_seq: int | None,
    payload_kind: str,
    payload,
    priority: str,
) -> dict:
    """Build a complete v1 header while deriving byte length and digest."""

    raw = _bounded_bytes(payload, MAX_MUX_PAYLOAD_BYTES)
    return _validated_header({
        "protocol": MUX_PROTOCOL,
        "version": MUX_VERSION,
        "lane": lane,
        "sender_role": sender_role,
        "connection_generation": connection_generation,
        "connection_id": connection_id,
        "runtime_incarnation": runtime_incarnation,
        "global_seq": global_seq,
        "lane_seq": lane_seq,
        "message_id": message_id,
        "correlation_id": correlation_id,
        "stream_id": stream_id,
        "stream_seq": stream_seq,
        "payload_kind": payload_kind,
        "payload_length": len(raw),
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
        "priority": priority,
    })


def validate_mux_packet(header: Mapping, payload) -> NativeMuxPacket:
    """Validate a complete header/payload pair without mutating either input."""

    raw = _bounded_bytes(payload, MAX_MUX_PAYLOAD_BYTES)
    value = _validated_header(header)
    if value["payload_length"] != len(raw):
        _reject("payload_length_mismatch")
    actual = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(value["payload_sha256"], actual):
        _reject("payload_sha256_mismatch")
    return NativeMuxPacket(header=MappingProxyType(value), payload=raw)


def encode_mux_packet(header: Mapping, payload) -> bytes:
    """Encode ``uint32 body + uint32 header + canonical header + payload``."""

    packet = validate_mux_packet(header, payload)
    encoded_header = _canonical_header_json(packet.header)
    header_length = len(encoded_header)
    body_length = _UINT32.size + header_length + len(packet.payload)
    if header_length < 1:
        _reject("mux_header_length_mismatch")
    if body_length > MAX_MUX_BODY_BYTES:
        _reject("mux_packet_too_large")
    return b"".join((
        _UINT32.pack(body_length),
        _UINT32.pack(header_length),
        encoded_header,
        packet.payload,
    ))


def decode_mux_packet(wire) -> NativeMuxPacket:
    """Decode one complete packet; no payload bytes are reflected on failure."""

    raw = _bounded_bytes(wire, MAX_MUX_WIRE_BYTES)
    if len(raw) < _UINT32.size:
        _reject("mux_packet_truncated")
    (body_length,) = _UINT32.unpack_from(raw, 0)
    if body_length > MAX_MUX_BODY_BYTES:
        _reject("mux_packet_too_large")
    if len(raw) < _UINT32.size * 2:
        _reject("mux_packet_truncated")
    if body_length != len(raw) - _UINT32.size:
        _reject("mux_body_length_mismatch")
    (header_length,) = _UINT32.unpack_from(raw, _UINT32.size)
    if header_length > MAX_MUX_HEADER_BYTES:
        _reject("mux_header_too_large")
    if header_length < 1 or header_length > body_length - _UINT32.size:
        _reject("mux_header_length_mismatch")
    payload_length = body_length - _UINT32.size - header_length
    if payload_length > MAX_MUX_PAYLOAD_BYTES:
        _reject("mux_packet_too_large")

    header_start = _UINT32.size * 2
    header_end = header_start + header_length
    encoded_header = raw[header_start:header_end]
    payload = raw[header_end:]
    header_text = None
    try:
        header_text = encoded_header.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        pass
    if header_text is None:
        _reject("invalid_header_utf8")
    header = None
    try:
        header = json.loads(
            header_text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except NativeMuxProtocolError:
        raise
    except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
        pass
    if header is None:
        _reject("invalid_header_json")
    if type(header) is not dict:
        _reject("invalid_header_object")
    if _canonical_header_json(header) != encoded_header:
        _reject("noncanonical_header_json")
    return validate_mux_packet(header, payload)


__all__ = [
    "JSON_SAFE_INTEGER_MAX",
    "MAX_MUX_BODY_BYTES",
    "MAX_MUX_HEADER_BYTES",
    "MAX_MUX_PAYLOAD_BYTES",
    "MAX_MUX_WIRE_BYTES",
    "MUX_HEADER_FIELDS",
    "MUX_LANES",
    "MUX_PRIORITIES",
    "MUX_PROTOCOL",
    "MUX_SENDER_ROLES",
    "MUX_VERSION",
    "NativeMuxPacket",
    "NativeMuxProtocolError",
    "decode_mux_packet",
    "encode_mux_packet",
    "make_mux_header",
    "validate_mux_packet",
]
