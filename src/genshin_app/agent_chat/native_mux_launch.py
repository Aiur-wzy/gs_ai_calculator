"""Strict qualification-only launch contract for the native mux carrier.

The production runtime continues to use ``genshin-native-launch-v1`` and the
legacy bridge.  This module deliberately defines a different one-shot launch
contract so a future runtime generation can select the mux codec before any
pipe byte is interpreted.  There is no protocol sniffing or fallback.
"""
from __future__ import annotations

import json
import re
import struct
from dataclasses import dataclass
from typing import Any

from .native_mux_protocol import JSON_SAFE_INTEGER_MAX

MUX_LAUNCH_CONTRACT = "genshin-native-mux-launch-v1"
MUX_TRANSPORT_CODEC = "genshin-native-pipe-mux-v1"
MAX_MUX_LAUNCH_BYTES = 4096
PRODUCTION_OWNER = False

_UINT32 = struct.Struct(">I")
_PIPE_NAME = re.compile(r"\\\\\.\\pipe\\genshin-native-[0-9a-f]{64}\Z")
_NONCE = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_FIELDS = frozenset({
    "contract",
    "pipe_name",
    "nonce",
    "transport_codec",
    "connection_generation",
    "connection_id",
    "runtime_incarnation",
})


class NativeMuxLaunchError(ValueError):
    """Sanitized launch rejection which never reflects inherited bytes."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _reject(code: str) -> None:
    raise NativeMuxLaunchError(code)


def _canonical(value: dict[str, Any]) -> bytes:
    try:
        body = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, UnicodeError, RecursionError):
        _reject("mux_launch_contract_json")
    if not body or len(body) > MAX_MUX_LAUNCH_BYTES:
        _reject("mux_launch_contract_size")
    return body


def _validate(value: Any) -> dict[str, Any]:
    if type(value) is not dict:
        _reject("mux_launch_contract_shape")
    fields = frozenset(value)
    if fields != _FIELDS:
        _reject("mux_launch_contract_shape")
    if value["contract"] != MUX_LAUNCH_CONTRACT:
        _reject("mux_launch_contract_version")
    if value["transport_codec"] != MUX_TRANSPORT_CODEC:
        _reject("mux_launch_transport_codec")
    if type(value["pipe_name"]) is not str or _PIPE_NAME.fullmatch(value["pipe_name"]) is None:
        _reject("mux_launch_pipe_name")
    if type(value["nonce"]) is not str or _NONCE.fullmatch(value["nonce"]) is None:
        _reject("mux_launch_nonce")
    generation = value["connection_generation"]
    if type(generation) is not int or not 1 <= generation <= JSON_SAFE_INTEGER_MAX:
        _reject("mux_launch_connection_generation")
    for field, code in (
        ("connection_id", "mux_launch_connection_id"),
        ("runtime_incarnation", "mux_launch_runtime_incarnation"),
    ):
        item = value[field]
        if type(item) is not str or _IDENTITY.fullmatch(item) is None:
            _reject(code)
    return dict(value)


@dataclass(frozen=True, slots=True)
class NativeMuxLaunch:
    """Validated immutable launch identity selected before pipe decoding."""

    pipe_name: str
    nonce: str
    transport_codec: str
    connection_generation: int
    connection_id: str
    runtime_incarnation: str


def _launch(value: dict[str, Any]) -> NativeMuxLaunch:
    checked = _validate(value)
    return NativeMuxLaunch(
        pipe_name=checked["pipe_name"],
        nonce=checked["nonce"],
        transport_codec=checked["transport_codec"],
        connection_generation=checked["connection_generation"],
        connection_id=checked["connection_id"],
        runtime_incarnation=checked["runtime_incarnation"],
    )


def encode_mux_launch_contract(
    *,
    pipe_name: str,
    nonce: str,
    connection_generation: int,
    connection_id: str,
    runtime_incarnation: str,
    transport_codec: str = MUX_TRANSPORT_CODEC,
) -> bytes:
    """Encode one exact, canonical inherited launch packet."""

    value = {
        "contract": MUX_LAUNCH_CONTRACT,
        "pipe_name": pipe_name,
        "nonce": nonce,
        "transport_codec": transport_codec,
        "connection_generation": connection_generation,
        "connection_id": connection_id,
        "runtime_incarnation": runtime_incarnation,
    }
    _launch(value)
    body = _canonical(value)
    return _UINT32.pack(len(body)) + body


def decode_mux_launch_contract(packet: Any) -> NativeMuxLaunch:
    """Decode exactly one canonical packet; legacy input is never accepted."""

    if not isinstance(packet, (bytes, bytearray, memoryview)):
        _reject("mux_launch_contract_bytes")
    try:
        raw = bytes(packet)
    except (TypeError, ValueError, BufferError):
        _reject("mux_launch_contract_bytes")
    if len(raw) < _UINT32.size + 1:
        _reject("mux_launch_contract_truncated")
    (length,) = _UINT32.unpack_from(raw)
    if length < 1 or length > MAX_MUX_LAUNCH_BYTES or len(raw) != length + _UINT32.size:
        _reject("mux_launch_contract_length")
    try:
        text = raw[_UINT32.size:].decode("utf-8", errors="strict")
        value = json.loads(text, parse_constant=lambda _item: _reject("mux_launch_contract_json"))
    except NativeMuxLaunchError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, RecursionError):
        _reject("mux_launch_contract_json")
    checked = _validate(value)
    if _canonical(checked) != raw[_UINT32.size:]:
        _reject("mux_launch_contract_noncanonical")
    return _launch(checked)
