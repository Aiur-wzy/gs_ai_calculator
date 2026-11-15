"""Bounded wire primitives for the private native-Agent bridge.

This module stops at the transport seam.  It does not create a Windows named
pipe, launch a sidecar, own credentials, or execute App tools.  A future pipe
adapter supplies byte-oriented read/write callbacks; this layer supplies the
framing, handshake admission, heartbeat state, and sanitized failure contract.
"""
from __future__ import annotations

import math
import re
import struct
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .bridge_protocol import (
    JSON_SAFE_INTEGER_MAX,
    MAX_FRAME_BYTES,
    MAX_NESTING_DEPTH,
    BridgeProtocolError,
    decode_frame,
    encode_frame,
    validate_frame,
)

_LENGTH = struct.Struct(">I")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_NONCE = re.compile(r"[A-Za-z0-9_-]{32,128}\Z")

HANDSHAKE_PAYLOAD_FIELDS = frozenset({
    "nonce",
    "role",
    "runtime_generation",
    "app_version",
    "node_version",
    "dsh_release",
    "profile_version",
    "composition_manifest_hash",
    "runtime_closure_hash",
    "dependency_lock_hash",
    "node_binary_sha256",
    "tool_catalog_hash",
    "prompt_profile_hash",
    "capabilities",
    "encoding",
    "limits",
})
HANDSHAKE_LIMIT_FIELDS = frozenset({
    "max_frame_bytes",
    "max_nesting_depth",
    "heartbeat_interval_ms",
    "heartbeat_timeout_ms",
    "cancel_timeout_ms",
})


class BridgeWireError(RuntimeError):
    """A sanitized bridge-wire failure with a stable machine code."""

    def __init__(self, code: str, message: str, *, cause_code: str | None = None):
        super().__init__(message)
        self.code = code
        self.cause_code = cause_code


def _fail(code: str, message: str, *, cause_code: str | None = None) -> None:
    raise BridgeWireError(code, message, cause_code=cause_code)


def _identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail("handshake_invalid_identity", f"握手{field}不合法")
    return value


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail("handshake_invalid_hash", f"握手{field}不是小写SHA-256")
    return value


def _milliseconds(value: Any, field: str) -> int:
    if type(value) is not int or not 1 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail("handshake_invalid_limit", f"握手{field}必须是正整数")
    return value


@dataclass(frozen=True)
class HandshakeContract:
    """Exact release and resource limits both bridge peers must agree on."""

    runtime_generation: str
    app_version: str
    node_version: str
    dsh_release: str
    profile_version: str
    composition_manifest_hash: str
    tool_catalog_hash: str
    prompt_profile_hash: str
    heartbeat_interval_ms: int
    heartbeat_timeout_ms: int
    cancel_timeout_ms: int
    max_frame_bytes: int = MAX_FRAME_BYTES
    max_nesting_depth: int = MAX_NESTING_DEPTH
    runtime_closure_hash: str = "0" * 64
    dependency_lock_hash: str = "0" * 64
    node_binary_sha256: str = "0" * 64

    def __post_init__(self) -> None:
        for field in (
            "runtime_generation", "app_version", "node_version", "dsh_release",
            "profile_version",
        ):
            _identity(getattr(self, field), field)
        for field in (
            "composition_manifest_hash", "runtime_closure_hash",
            "dependency_lock_hash", "node_binary_sha256", "tool_catalog_hash",
            "prompt_profile_hash",
        ):
            _digest(getattr(self, field), field)
        for field in (
            "heartbeat_interval_ms", "heartbeat_timeout_ms", "cancel_timeout_ms",
            "max_frame_bytes", "max_nesting_depth",
        ):
            _milliseconds(getattr(self, field), field)
        if self.max_frame_bytes > MAX_FRAME_BYTES:
            _fail("handshake_invalid_limit", "握手帧上限不能超过本地协议上限")
        if self.max_nesting_depth > MAX_NESTING_DEPTH:
            _fail("handshake_invalid_limit", "握手嵌套上限不能超过本地协议上限")
        if self.heartbeat_timeout_ms <= self.heartbeat_interval_ms:
            _fail("handshake_invalid_limit", "心跳超时必须大于心跳间隔")

    @property
    def limits(self) -> dict[str, int]:
        return {
            "max_frame_bytes": self.max_frame_bytes,
            "max_nesting_depth": self.max_nesting_depth,
            "heartbeat_interval_ms": self.heartbeat_interval_ms,
            "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
            "cancel_timeout_ms": self.cancel_timeout_ms,
        }


def build_handshake_payload(
    contract: HandshakeContract,
    *,
    nonce: str,
    role: str,
    capabilities: list[str] | tuple[str, ...],
) -> dict[str, Any]:
    """Build the exact payload signed by a normal bridge frame."""
    if not isinstance(nonce, str) or _NONCE.fullmatch(nonce) is None:
        _fail("handshake_invalid_nonce", "握手nonce不合法")
    _identity(role, "role")
    normalized = _capabilities(capabilities)
    return {
        "nonce": nonce,
        "role": role,
        "runtime_generation": contract.runtime_generation,
        "app_version": contract.app_version,
        "node_version": contract.node_version,
        "dsh_release": contract.dsh_release,
        "profile_version": contract.profile_version,
        "composition_manifest_hash": contract.composition_manifest_hash,
        "runtime_closure_hash": contract.runtime_closure_hash,
        "dependency_lock_hash": contract.dependency_lock_hash,
        "node_binary_sha256": contract.node_binary_sha256,
        "tool_catalog_hash": contract.tool_catalog_hash,
        "prompt_profile_hash": contract.prompt_profile_hash,
        "capabilities": normalized,
        "encoding": "utf-8",
        "limits": contract.limits,
    }


def _capabilities(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > 128:
        _fail("handshake_invalid_capabilities", "握手能力表不合法")
    result: list[str] = []
    for item in value:
        result.append(_identity(item, "capability"))
    if result != sorted(set(result)):
        _fail("handshake_invalid_capabilities", "握手能力表必须去重并排序")
    return result


def validate_handshake_frame(
    frame: Mapping[str, Any],
    *,
    contract: HandshakeContract,
    nonce: str,
    expected_role: str,
    expected_request_id: str,
    allowed_capabilities: frozenset[str],
    required_capabilities: frozenset[str] = frozenset(),
    response: bool = True,
) -> dict[str, Any]:
    """Validate an exact peer handshake without echoing untrusted values."""
    try:
        checked = validate_frame(frame)
    except BridgeProtocolError as exc:
        raise BridgeWireError(
            "handshake_invalid_frame", "桥握手帧未通过协议校验", cause_code=exc.code,
        ) from None
    expected_kind = "response" if response else "request"
    expected_type = "handshake.response" if response else "handshake.request"
    if checked["kind"] != expected_kind or checked["type"] != expected_type:
        _fail("handshake_wrong_frame", "桥握手帧类型不匹配")
    _identity(expected_request_id, "request_id")
    if checked.get("request_id") != expected_request_id:
        _fail("handshake_request_mismatch", "桥握手请求身份不匹配")
    payload = checked["payload"]
    if set(payload) != HANDSHAKE_PAYLOAD_FIELDS:
        _fail("handshake_payload_shape", "桥握手字段集合不兼容")
    if not isinstance(nonce, str) or _NONCE.fullmatch(nonce) is None:
        _fail("handshake_invalid_nonce", "本地握手nonce不合法")
    if payload["nonce"] != nonce:
        _fail("handshake_nonce_mismatch", "桥握手nonce不匹配")
    if payload["role"] != expected_role:
        _fail("handshake_role_mismatch", "桥握手角色不匹配")
    expected = build_handshake_payload(
        contract,
        nonce=nonce,
        role=expected_role,
        capabilities=payload.get("capabilities", []),
    )
    for field in HANDSHAKE_PAYLOAD_FIELDS - {"capabilities"}:
        if payload[field] != expected[field]:
            _fail("handshake_contract_mismatch", "桥握手发行身份或资源合同不匹配")
    capabilities = frozenset(_capabilities(payload["capabilities"]))
    for capability in required_capabilities:
        _identity(capability, "required capability")
    if not required_capabilities <= capabilities:
        _fail("handshake_missing_capability", "桥握手缺少必需能力")
    if not isinstance(allowed_capabilities, frozenset):
        _fail("handshake_invalid_capabilities", "本地允许能力表必须显式冻结")
    for capability in allowed_capabilities:
        _identity(capability, "allowed capability")
    if not capabilities <= allowed_capabilities:
        _fail("handshake_extra_capability", "桥握手声明了未授权能力")
    return checked


class LengthPrefixedBridgeIO:
    """Read and write strict bridge frames with a 32-bit big-endian length."""

    def __init__(
        self,
        read: Callable[[int], bytes],
        write: Callable[[bytes], int | None],
        *,
        flush: Callable[[], Any] | None = None,
        close: Callable[[], Any] | None = None,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ):
        if not callable(read) or not callable(write):
            raise TypeError("read and write must be callables")
        if type(max_frame_bytes) is not int or not 1 <= max_frame_bytes <= MAX_FRAME_BYTES:
            raise ValueError("max_frame_bytes must be within the bridge protocol limit")
        self._read = read
        self._write = write
        self._flush = flush
        self._close = close
        self.max_frame_bytes = max_frame_bytes
        self._closed = False
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._close_lock = threading.Lock()

    @classmethod
    def from_streams(cls, reader, writer, *, max_frame_bytes: int = MAX_FRAME_BYTES):
        """Adapt ordinary binary streams without taking ownership twice."""
        closer = getattr(reader, "close", None)
        if writer is not reader:
            writer_close = getattr(writer, "close", None)

            def close_both():
                try:
                    if callable(writer_close):
                        writer_close()
                finally:
                    if callable(closer):
                        closer()
        else:
            close_both = closer
        return cls(
            reader.read,
            writer.write,
            flush=getattr(writer, "flush", None),
            close=close_both,
            max_frame_bytes=max_frame_bytes,
        )

    @property
    def closed(self) -> bool:
        return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            _fail("bridge_wire_closed", "桥传输已经关闭")

    def _read_exact(self, size: int, *, clean_eof: bool = False) -> bytes:
        output = bytearray()
        while len(output) < size:
            try:
                chunk = self._read(size - len(output))
            except (OSError, ValueError):
                _fail("bridge_wire_read_failed", "无法读取桥传输")
            if not isinstance(chunk, (bytes, bytearray, memoryview)):
                _fail("bridge_wire_read_failed", "桥读取器返回了非字节数据")
            if not chunk:
                if clean_eof and not output:
                    _fail("bridge_wire_eof", "桥对端已关闭")
                _fail("bridge_wire_truncated", "桥帧在完成前中断")
            output.extend(chunk)
        return bytes(output)

    def read_frame(self) -> dict[str, Any]:
        with self._read_lock:
            self._ensure_open()
            prefix = self._read_exact(_LENGTH.size, clean_eof=True)
            (size,) = _LENGTH.unpack(prefix)
            if not 1 <= size <= self.max_frame_bytes:
                _fail("bridge_wire_length", "桥帧长度前缀超出允许范围")
            raw = self._read_exact(size)
            try:
                return decode_frame(raw)
            except BridgeProtocolError as exc:
                raise BridgeWireError(
                    "bridge_frame_invalid", "桥帧未通过协议校验", cause_code=exc.code,
                ) from None

    def write_frame(self, frame: Mapping[str, Any]) -> int:
        with self._write_lock:
            self._ensure_open()
            try:
                raw = encode_frame(frame)
            except BridgeProtocolError as exc:
                raise BridgeWireError(
                    "bridge_frame_invalid", "桥帧未通过协议校验", cause_code=exc.code,
                ) from None
            if len(raw) > self.max_frame_bytes:
                _fail("bridge_wire_length", "桥帧超过当前连接协商上限")
            packet = memoryview(_LENGTH.pack(len(raw)) + raw)
            written = 0
            while written < len(packet):
                try:
                    count = self._write(packet[written:].tobytes())
                except (OSError, ValueError):
                    _fail("bridge_wire_write_failed", "无法写入桥传输")
                if count is None:
                    count = len(packet) - written
                if type(count) is not int or count <= 0 or written + count > len(packet):
                    _fail("bridge_wire_write_failed", "桥写入器返回了无效长度")
                written += count
            if self._flush is not None:
                try:
                    self._flush()
                except (OSError, ValueError):
                    _fail("bridge_wire_write_failed", "无法刷新桥传输")
            return len(raw)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            if self._close is not None:
                try:
                    self._close()
                except (OSError, ValueError):
                    _fail("bridge_wire_close_failed", "无法关闭桥传输")


class HeartbeatMonitor:
    """Deterministic one-ping-at-a-time heartbeat state machine."""

    def __init__(
        self,
        *,
        interval_seconds: float,
        timeout_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ):
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, (int, float)):
            raise TypeError("interval_seconds must be numeric")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be numeric")
        if interval_seconds <= 0 or timeout_seconds <= interval_seconds:
            raise ValueError("timeout_seconds must be greater than a positive interval")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self.interval_seconds = float(interval_seconds)
        self.timeout_seconds = float(timeout_seconds)
        self._clock = clock
        self.state = "open"
        self.last_activity_at = float(clock())
        self.pending_token: str | None = None
        self.deadline_at: float | None = None

    def _now(self) -> float:
        value = self._clock()
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            _fail("heartbeat_clock_invalid", "心跳时钟返回值不合法")
        checked = float(value)
        if not math.isfinite(checked):
            _fail("heartbeat_clock_invalid", "心跳时钟返回值不合法")
        return checked

    def _ensure_open(self) -> None:
        if self.state == "timed_out":
            _fail("heartbeat_timeout", "桥心跳已经超时")
        if self.state != "open":
            _fail("heartbeat_closed", "桥心跳已经关闭")

    def record_activity(self) -> None:
        self._ensure_open()
        self.last_activity_at = self._now()

    def ping_due(self) -> bool:
        self._ensure_open()
        now = self._now()
        if self.pending_token is not None:
            self.check_timeout(now=now)
            return False
        return now - self.last_activity_at >= self.interval_seconds

    def begin_ping(self, token: str) -> None:
        self._ensure_open()
        _identity(token, "heartbeat token")
        if self.pending_token is not None:
            _fail("heartbeat_ping_pending", "已有等待响应的桥心跳")
        now = self._now()
        self.pending_token = token
        self.deadline_at = now + self.timeout_seconds

    def acknowledge(self, token: str) -> None:
        self._ensure_open()
        if self.pending_token is None:
            _fail("heartbeat_unexpected_pong", "收到没有对应请求的桥心跳响应")
        if token != self.pending_token:
            _fail("heartbeat_token_mismatch", "桥心跳响应身份不匹配")
        now = self._now()
        if self.deadline_at is not None and now >= self.deadline_at:
            self.state = "timed_out"
            self.pending_token = None
            self.deadline_at = None
            _fail("heartbeat_timeout", "桥心跳响应超过期限")
        self.pending_token = None
        self.deadline_at = None
        self.last_activity_at = now

    def check_timeout(self, *, now: float | None = None) -> None:
        self._ensure_open()
        if self.pending_token is None:
            return
        if now is None:
            checked = self._now()
        elif isinstance(now, bool) or not isinstance(now, (int, float)):
            _fail("heartbeat_clock_invalid", "心跳时钟返回值不合法")
        else:
            checked = float(now)
            if not math.isfinite(checked):
                _fail("heartbeat_clock_invalid", "心跳时钟返回值不合法")
        if self.deadline_at is not None and checked >= self.deadline_at:
            self.state = "timed_out"
            self.pending_token = None
            self.deadline_at = None
            _fail("heartbeat_timeout", "桥心跳响应超过期限")

    def close(self) -> None:
        if self.state == "closed":
            return
        self.state = "closed"
        self.pending_token = None
        self.deadline_at = None


__all__ = [
    "HANDSHAKE_LIMIT_FIELDS",
    "HANDSHAKE_PAYLOAD_FIELDS",
    "BridgeWireError",
    "HandshakeContract",
    "HeartbeatMonitor",
    "LengthPrefixedBridgeIO",
    "build_handshake_payload",
    "validate_handshake_frame",
]
