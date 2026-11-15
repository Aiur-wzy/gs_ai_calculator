"""Private Windows named-pipe transport for the native Agent sidecar.

The pipe is deliberately narrower than the bridge protocol.  This module owns
only the local byte channel: a random pipe name, an explicit current-user DACL,
single-instance/local-only creation, bounded overlapped I/O, and deterministic
handle cleanup.  Handshake nonces and provider credentials are not accepted by
this API and must travel through a separately inherited bootstrap handle.
"""
from __future__ import annotations

import ctypes
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from ctypes import wintypes
from typing import Any

from .bridge_wire import LengthPrefixedBridgeIO

PIPE_NAME_RE = re.compile(r"\\\\\.\\pipe\\genshin-native-[0-9a-f]{64}\Z")

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_FILE_FLAG_OVERLAPPED = 0x40000000
_FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
_SECURITY_SQOS_PRESENT = 0x00100000
_SECURITY_IDENTIFICATION = 0x00010000
_PIPE_ACCESS_DUPLEX = 0x00000003
_PIPE_TYPE_BYTE = 0x00000000
_PIPE_READMODE_BYTE = 0x00000000
_PIPE_WAIT = 0x00000000
_PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
_ERROR_IO_PENDING = 997
_ERROR_PIPE_CONNECTED = 535
_ERROR_BROKEN_PIPE = 109
_ERROR_PIPE_NOT_CONNECTED = 233
_ERROR_OPERATION_ABORTED = 995
_ERROR_INSUFFICIENT_BUFFER = 122
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258
_WAIT_FAILED = 0xFFFFFFFF
_INFINITE = 0xFFFFFFFF
_TOKEN_QUERY = 0x0008
_TOKEN_USER = 1
_SDDL_REVISION_1 = 1
_BUFFER_BYTES = 64 * 1024
MAX_PIPE_IO_BYTES = 1024 * 1024 + 4
_MAX_IO_BYTES = MAX_PIPE_IO_BYTES
_CANCEL_SETTLE_TIMEOUT_MS = 1_000
_DYNAMIC_DEADLINE_POLL_MS = 250
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class NativePipeError(RuntimeError):
    """Sanitized local-pipe failure with a stable machine code."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _fail(code: str, message: str) -> None:
    raise NativePipeError(code, message)


def _timeout_ms(value: float, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("timeout must be numeric")
    checked = float(value)
    minimum = 0.0 if allow_zero else 0.001
    if not math.isfinite(checked) or checked < minimum or checked > 60.0:
        raise ValueError("timeout must be finite and within the supported range")
    return max(0 if allow_zero else 1, math.ceil(checked * 1000))


def connect_timeout_milliseconds(value: float) -> int:
    """Validate a finite cold-start wait without changing fragment I/O limits.

    Win32 reserves DWORD ``0xffffffff`` for INFINITE.  A connection grace may
    exceed the ordinary 60-second fragment budget, but never becomes an
    infinite wait or wraps at that sentinel.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("connect timeout must be numeric")
    try:
        checked = float(value)
    except OverflowError as error:
        raise ValueError("connect timeout exceeds the finite Win32 range") from error
    maximum_ms = _INFINITE - 1
    if (not math.isfinite(checked) or checked < 0.001
            or checked > maximum_ms / 1000):
        raise ValueError("connect timeout must be finite and within the Win32 range")
    milliseconds = math.ceil(checked * 1000)
    if milliseconds > maximum_ms:
        raise ValueError("connect timeout exceeds the finite Win32 range")
    return milliseconds


def random_pipe_name() -> str:
    """Return a local-only namespace name with 256 bits of randomness."""
    return rf"\\.\pipe\genshin-native-{secrets.token_hex(32)}"


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", wintypes.DWORD),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", wintypes.BOOL),
    ]


class _Overlapped(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_size_t),
        ("InternalHigh", ctypes.c_size_t),
        ("Offset", wintypes.DWORD),
        ("OffsetHigh", wintypes.DWORD),
        ("hEvent", wintypes.HANDLE),
    ]


class _SidAndAttributes(ctypes.Structure):
    _fields_ = [("Sid", ctypes.c_void_p), ("Attributes", wintypes.DWORD)]


class _TokenUser(ctypes.Structure):
    _fields_ = [("User", _SidAndAttributes)]


class _OverlappedOperation:
    """Own OVERLAPPED storage and its event until Win32 has finished with it.

    A timed-out ``CancelIoEx`` request is only a request to cancel. Windows
    may still access the OVERLAPPED structure after the caller's timeout. In
    the exceptional case where cancellation does not settle within the
    bounded foreground wait, ownership is transferred to a daemon reaper.
    The reaper (or, on an unrecoverable wait failure, the process lifetime)
    keeps this object alive so Python never frees memory still owned by Win32.
    """

    def __init__(self, api: _WindowsApi, event: Any):
        self.api = api
        self.event = event
        self.overlapped = _Overlapped(hEvent=event)
        self._lock = threading.Lock()
        self._deferred = False
        self._closed = False
        self._keepalive: list[Any] = []

    def retain(self, *values: Any) -> None:
        """Retain every ctypes object whose address was passed to Win32."""
        with self._lock:
            if self._closed or self._deferred:
                raise RuntimeError("cannot retain values after operation cleanup")
            self._keepalive.extend(values)

    def close(self) -> None:
        """Close the event unless ownership was transferred to the reaper."""
        with self._lock:
            if self._closed or self._deferred:
                return
            self._closed = True
            event, self.event = self.event, None
            self._keepalive.clear()
        self.api.close_handle(event)

    def mark_deferred(self) -> bool:
        with self._lock:
            if self._closed or self._deferred:
                return False
            self._deferred = True
            return True

    def close_from_reaper(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            event, self.event = self.event, None
            self._keepalive.clear()
        self.api.close_handle(event)


class _WindowsApi:
    def __init__(self):
        if os.name != "nt" or not hasattr(ctypes, "WinDLL"):
            _fail("platform_unsupported", "原生 Agent 私有管道仅支持 Windows。")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self._deferred_lock = threading.Lock()
        self._deferred_operations: set[_OverlappedOperation] = set()

        k = self.kernel
        k.GetCurrentProcess.argtypes = []
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.CloseHandle.restype = wintypes.BOOL
        k.LocalFree.argtypes = [ctypes.c_void_p]
        k.LocalFree.restype = ctypes.c_void_p
        k.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL,
                                   wintypes.LPCWSTR]
        k.CreateEventW.restype = wintypes.HANDLE
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
        k.CancelIoEx.restype = wintypes.BOOL
        k.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped),
                                          ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
        k.GetOverlappedResult.restype = wintypes.BOOL
        k.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.DWORD, ctypes.POINTER(_SecurityAttributes)]
        k.CreateNamedPipeW.restype = wintypes.HANDLE
        k.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.POINTER(_Overlapped)]
        k.ConnectNamedPipe.restype = wintypes.BOOL
        k.DisconnectNamedPipe.argtypes = [wintypes.HANDLE]
        k.DisconnectNamedPipe.restype = wintypes.BOOL
        k.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        k.WaitNamedPipeW.restype = wintypes.BOOL
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_Overlapped)]
        k.ReadFile.restype = wintypes.BOOL
        k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(_Overlapped)]
        k.WriteFile.restype = wintypes.BOOL

        a = self.advapi
        a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
        a.OpenProcessToken.restype = wintypes.BOOL
        a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_uint, ctypes.c_void_p,
                                          wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        a.GetTokenInformation.restype = wintypes.BOOL
        a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p,
                                             ctypes.POINTER(wintypes.LPWSTR)]
        a.ConvertSidToStringSidW.restype = wintypes.BOOL
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        ]
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL

    @staticmethod
    def valid_handle(handle: Any) -> bool:
        return handle not in (None, 0, _INVALID_HANDLE_VALUE)

    def close_handle(self, handle: Any) -> None:
        if self.valid_handle(handle):
            self.kernel.CloseHandle(handle)

    def current_user_sid(self) -> str:
        token = wintypes.HANDLE()
        if not self.advapi.OpenProcessToken(
            self.kernel.GetCurrentProcess(), _TOKEN_QUERY, ctypes.byref(token),
        ):
            _fail("pipe_acl_failed", "无法读取当前 Windows 用户身份。")
        try:
            size = wintypes.DWORD()
            self.advapi.GetTokenInformation(
                token, _TOKEN_USER, None, 0, ctypes.byref(size),
            )
            if ctypes.get_last_error() != _ERROR_INSUFFICIENT_BUFFER or size.value == 0:
                _fail("pipe_acl_failed", "无法读取当前 Windows 用户身份。")
            buffer = ctypes.create_string_buffer(size.value)
            if not self.advapi.GetTokenInformation(
                token, _TOKEN_USER, buffer, size, ctypes.byref(size),
            ):
                _fail("pipe_acl_failed", "无法读取当前 Windows 用户身份。")
            user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
            text = wintypes.LPWSTR()
            if not self.advapi.ConvertSidToStringSidW(user.User.Sid, ctypes.byref(text)):
                _fail("pipe_acl_failed", "无法规范化当前 Windows 用户身份。")
            try:
                value = text.value
                if not isinstance(value, str) or not value.startswith("S-1-"):
                    _fail("pipe_acl_failed", "当前 Windows 用户身份不合法。")
                return value
            finally:
                self.kernel.LocalFree(text)
        finally:
            self.close_handle(token)

    def security_attributes(self) -> tuple[_SecurityAttributes, ctypes.c_void_p, str]:
        sid = self.current_user_sid()
        # Protected DACL: exactly the current user receives Generic All.  The
        # descriptor is explicit; it never inherits the process default DACL.
        sddl = f"D:P(A;;GA;;;{sid})"
        descriptor = ctypes.c_void_p()
        if not self.advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, _SDDL_REVISION_1, ctypes.byref(descriptor), None,
        ):
            _fail("pipe_acl_failed", "无法创建当前用户专用的管道 ACL。")
        attributes = _SecurityAttributes(
            ctypes.sizeof(_SecurityAttributes), descriptor, False,
        )
        return attributes, descriptor, sddl

    def overlapped(self) -> _OverlappedOperation:
        event = self.kernel.CreateEventW(None, True, False, None)
        if not self.valid_handle(event):
            _fail("pipe_event_failed", "无法创建私有管道 I/O 事件。")
        return _OverlappedOperation(self, event)

    def _defer_overlapped_cleanup(
        self,
        operation: _OverlappedOperation,
    ) -> None:
        """Retain an unsettled OVERLAPPED without blocking the caller.

        The handle is intentionally not closed here: it belongs to the pipe
        connection/server and its normal fail-closed lifecycle will close it.
        A permanently failed event wait leaks one tiny operation until process
        exit, which is safer than freeing storage that Win32 may still touch.
        """
        if not operation.mark_deferred():
            return
        with self._deferred_lock:
            self._deferred_operations.add(operation)

        def reap() -> None:
            result = self.kernel.WaitForSingleObject(operation.event, _INFINITE)
            if result != _WAIT_OBJECT_0:
                # Keep the operation in the retained set for process lifetime.
                return
            # The manual-reset event is signalled only after the overlapped
            # operation has completed. Do not call GetOverlappedResult here:
            # the owning connection may already have closed its pipe handle,
            # and Windows can reuse that numeric handle for another object.
            operation.close_from_reaper()
            with self._deferred_lock:
                self._deferred_operations.discard(operation)

        threading.Thread(
            target=reap,
            name="genshin-native-pipe-cancel-reaper",
            daemon=True,
        ).start()

    def _cancel_and_settle(
        self,
        handle: Any,
        operation: _OverlappedOperation,
    ) -> bool:
        """Request cancellation and wait a bounded time for Win32 to settle."""
        self.kernel.CancelIoEx(handle, ctypes.byref(operation.overlapped))
        result = self.kernel.WaitForSingleObject(
            operation.event,
            _CANCEL_SETTLE_TIMEOUT_MS,
        )
        if result == _WAIT_OBJECT_0:
            return True
        self._defer_overlapped_cleanup(operation)
        return False

    def wait_overlapped(
        self,
        handle: Any,
        operation: _OverlappedOperation,
        timeout_ms: int,
        *,
        timeout_code: str,
        eof_errors: frozenset[int] = frozenset(),
        deadline_provider: Callable[[], float] | None = None,
        deadline_invalid_code: str = "pipe_read_deadline_invalid",
    ) -> int:
        while True:
            if deadline_provider is None:
                wait_ms = timeout_ms
            else:
                try:
                    deadline = deadline_provider()
                except Exception:  # noqa: BLE001 - cancel before rejecting a bad owner
                    if not self._cancel_and_settle(handle, operation):
                        _fail(
                            "pipe_cancel_settle_timeout",
                            "私有管道取消操作未在限定时间内完成。",
                        )
                    _fail(deadline_invalid_code, "私有管道操作期限不合法。")
                if (
                    isinstance(deadline, bool)
                    or not isinstance(deadline, (int, float))
                    or not math.isfinite(float(deadline))
                ):
                    if not self._cancel_and_settle(handle, operation):
                        _fail(
                            "pipe_cancel_settle_timeout",
                            "私有管道取消操作未在限定时间内完成。",
                        )
                    _fail(deadline_invalid_code, "私有管道操作期限不合法。")
                remaining_ms = math.ceil((float(deadline) - time.monotonic()) * 1000)
                wait_ms = max(0, min(remaining_ms, _DYNAMIC_DEADLINE_POLL_MS))
            result = self.kernel.WaitForSingleObject(operation.event, wait_ms)
            if result != _WAIT_TIMEOUT or deadline_provider is None:
                break
            # Re-evaluate the owner-provided deadline on every bounded poll.
            # This lets an already-blocked turn read observe a newly published
            # ten-second cancel deadline without truncating healthy long work.
            if remaining_ms <= 0:
                break
        if result == _WAIT_TIMEOUT:
            if not self._cancel_and_settle(handle, operation):
                _fail(
                    "pipe_cancel_settle_timeout",
                    "私有管道取消操作未在限定时间内完成。",
                )
            _fail(timeout_code, "私有管道操作超时。")
        if result != _WAIT_OBJECT_0:
            if not self._cancel_and_settle(handle, operation):
                _fail(
                    "pipe_cancel_settle_timeout",
                    "私有管道取消操作未在限定时间内完成。",
                )
            _fail("pipe_wait_failed", "无法等待私有管道操作。")
        transferred = wintypes.DWORD()
        if not self.kernel.GetOverlappedResult(
            handle,
            ctypes.byref(operation.overlapped),
            ctypes.byref(transferred),
            False,
        ):
            error = ctypes.get_last_error()
            if error in eof_errors:
                return 0
            if error == _ERROR_OPERATION_ABORTED:
                _fail(timeout_code, "私有管道操作已取消。")
            _fail("pipe_io_failed", "私有管道 I/O 失败。")
        return transferred.value


_api_lock = threading.Lock()
_api_instance: _WindowsApi | None = None


def _api() -> _WindowsApi:
    global _api_instance
    with _api_lock:
        if _api_instance is None:
            _api_instance = _WindowsApi()
        return _api_instance


def current_user_sid() -> str:
    """Return the SID used by the explicit pipe DACL."""
    return _api().current_user_sid()


class NativePipeConnection:
    """One connected overlapped byte channel."""

    def __init__(
        self,
        handle: Any,
        *,
        io_timeout: float,
        server_end: bool,
        api: _WindowsApi | None = None,
    ):
        self._api = api or _api()
        if not self._api.valid_handle(handle):
            _fail("pipe_invalid_handle", "私有管道句柄不合法。")
        self._handle = handle
        self._timeout_ms = _timeout_ms(io_timeout)
        self._server_end = bool(server_end)
        self._read_lock = threading.Lock()
        self._read_deadline_local = threading.local()
        self._write_lock = threading.Lock()
        self._write_deadline_local = threading.local()
        self._close_lock = threading.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _ensure_open(self) -> Any:
        if self._closed or not self._api.valid_handle(self._handle):
            _fail("pipe_closed", "私有管道已经关闭。")
        return self._handle

    @staticmethod
    def _preflight_deadline(
        provider: Callable[[], float] | None,
        *,
        invalid_code: str,
        timeout_code: str,
    ) -> None:
        """Reject an invalid or already-expired dynamic deadline before I/O.

        Overlapped waits re-evaluate the provider while an operation is
        pending.  This preflight is also required for synchronous completions:
        otherwise a long exact frame made of many immediate chunks could pass
        an absolute protocol deadline without ever entering the wait path.
        """
        if provider is None:
            return
        try:
            deadline = provider()
        except Exception:  # noqa: BLE001 - normalize an untrusted owner callback
            _fail(invalid_code, "私有管道操作期限不合法。")
        if (
            isinstance(deadline, bool)
            or not isinstance(deadline, (int, float))
            or not math.isfinite(float(deadline))
        ):
            _fail(invalid_code, "私有管道操作期限不合法。")
        if float(deadline) <= time.monotonic():
            _fail(timeout_code, "私有管道操作超时。")

    def read(self, size: int) -> bytes:
        if type(size) is not int or not 1 <= size <= _MAX_IO_BYTES:
            raise ValueError("read size is outside the pipe limit")
        with self._read_lock:
            handle = self._ensure_open()
            deadline_provider = getattr(
                self._read_deadline_local,
                "provider",
                None,
            )
            self._preflight_deadline(
                deadline_provider,
                invalid_code="pipe_read_deadline_invalid",
                timeout_code="pipe_read_timeout",
            )
            buffer = ctypes.create_string_buffer(size)
            transferred = wintypes.DWORD()
            operation = self._api.overlapped()
            operation.retain(buffer, transferred)
            try:
                ok = self._api.kernel.ReadFile(
                    handle,
                    buffer,
                    size,
                    ctypes.byref(transferred),
                    ctypes.byref(operation.overlapped),
                )
                if ok:
                    count = transferred.value
                else:
                    error = ctypes.get_last_error()
                    if error in {_ERROR_BROKEN_PIPE, _ERROR_PIPE_NOT_CONNECTED}:
                        return b""
                    if error != _ERROR_IO_PENDING:
                        _fail("pipe_read_failed", "无法读取私有管道。")
                    count = self._api.wait_overlapped(
                        handle, operation, self._timeout_ms,
                        timeout_code="pipe_read_timeout",
                        eof_errors=frozenset(
                            {_ERROR_BROKEN_PIPE, _ERROR_PIPE_NOT_CONNECTED},
                        ),
                        deadline_provider=deadline_provider,
                        deadline_invalid_code="pipe_read_deadline_invalid",
                    )
                return buffer.raw[:count]
            finally:
                operation.close()

    @contextmanager
    def read_deadline(
        self,
        provider: Callable[[], float],
    ) -> Iterator[None]:
        """Temporarily derive one reader's deadline from its protocol owner.

        The ordinary pipe timeout continues to protect startup and short
        control calls.  A long official turn can instead expose its current
        heartbeat/cancel/work deadline; the overlapped read polls that owner
        without cancelling and restarting a partial frame.
        """
        if not callable(provider):
            raise TypeError("read deadline provider must be callable")
        marker = object()
        previous = getattr(self._read_deadline_local, "provider", marker)
        self._read_deadline_local.provider = provider
        try:
            yield
        finally:
            if previous is marker:
                del self._read_deadline_local.provider
            else:
                self._read_deadline_local.provider = previous

    def write(self, value: bytes) -> int:
        if not isinstance(value, (bytes, bytearray, memoryview)):
            raise TypeError("pipe writes require bytes")
        raw = bytes(value)
        if not raw or len(raw) > _MAX_IO_BYTES:
            raise ValueError("write size is outside the pipe limit")
        with self._write_lock:
            handle = self._ensure_open()
            deadline_provider = getattr(
                self._write_deadline_local,
                "provider",
                None,
            )
            self._preflight_deadline(
                deadline_provider,
                invalid_code="pipe_write_deadline_invalid",
                timeout_code="pipe_write_timeout",
            )
            buffer = ctypes.create_string_buffer(raw, len(raw))
            transferred = wintypes.DWORD()
            operation = self._api.overlapped()
            operation.retain(buffer, transferred)
            try:
                ok = self._api.kernel.WriteFile(
                    handle,
                    buffer,
                    len(raw),
                    ctypes.byref(transferred),
                    ctypes.byref(operation.overlapped),
                )
                if ok:
                    count = transferred.value
                else:
                    error = ctypes.get_last_error()
                    if error in {_ERROR_BROKEN_PIPE, _ERROR_PIPE_NOT_CONNECTED}:
                        _fail("pipe_disconnected", "私有管道对端已经断开。")
                    if error != _ERROR_IO_PENDING:
                        _fail("pipe_write_failed", "无法写入私有管道。")
                    count = self._api.wait_overlapped(
                        handle, operation, self._timeout_ms,
                        timeout_code="pipe_write_timeout",
                        deadline_provider=deadline_provider,
                        deadline_invalid_code="pipe_write_deadline_invalid",
                    )
                if not 0 < count <= len(raw):
                    _fail("pipe_write_failed", "私有管道返回了无效写入长度。")
                return count
            finally:
                operation.close()

    @contextmanager
    def write_deadline(
        self,
        provider: Callable[[], float],
    ) -> Iterator[None]:
        """Apply one protocol owner's absolute deadline to physical writes.

        A mux frame may require several bounded raw-pipe writes.  Reusing the
        ordinary per-call timeout for every chunk would silently refresh the
        budget.  The carrier can instead expose one absolute deadline which
        every overlapped write re-evaluates without changing startup I/O.
        """
        if not callable(provider):
            raise TypeError("write deadline provider must be callable")
        marker = object()
        previous = getattr(self._write_deadline_local, "provider", marker)
        self._write_deadline_local.provider = provider
        try:
            yield
        finally:
            if previous is marker:
                del self._write_deadline_local.provider
            else:
                self._write_deadline_local.provider = previous

    def bridge_io(self) -> LengthPrefixedBridgeIO:
        """Adapt this connection to the strict bridge frame codec."""
        return LengthPrefixedBridgeIO(self.read, self.write, close=self.close)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            handle, self._handle = self._handle, None
            if self._api.valid_handle(handle):
                self._api.kernel.CancelIoEx(handle, None)
                if self._server_end:
                    self._api.kernel.DisconnectNamedPipe(handle)
                self._api.close_handle(handle)

    def __enter__(self):
        self._ensure_open()
        return self

    def __exit__(self, *_exc):
        self.close()


class NativePipeServer:
    """One random, local-only, current-user named-pipe server instance."""

    def __init__(self, *, io_timeout: float = 10.0):
        _timeout_ms(io_timeout)
        self._api = _api()
        self.name = random_pipe_name()
        self._io_timeout = float(io_timeout)
        attributes, descriptor, sddl = self._api.security_attributes()
        self.security_sddl = sddl
        try:
            self._handle = self._api.kernel.CreateNamedPipeW(
                self.name,
                _PIPE_ACCESS_DUPLEX | _FILE_FLAG_OVERLAPPED | _FILE_FLAG_FIRST_PIPE_INSTANCE,
                _PIPE_TYPE_BYTE | _PIPE_READMODE_BYTE | _PIPE_WAIT | _PIPE_REJECT_REMOTE_CLIENTS,
                1,
                _BUFFER_BYTES,
                _BUFFER_BYTES,
                0,
                ctypes.byref(attributes),
            )
        finally:
            self._api.kernel.LocalFree(descriptor)
        if not self._api.valid_handle(self._handle):
            self._handle = None
            _fail("pipe_create_failed", "无法创建当前用户专用的私有管道。")
        self._accept_lock = threading.Lock()
        self._close_lock = threading.Lock()
        self._closed = False
        self._accepted = False

    @property
    def closed(self) -> bool:
        return self._closed

    def accept(self, *, timeout: float = 10.0) -> NativePipeConnection:
        timeout_ms = connect_timeout_milliseconds(timeout)
        with self._accept_lock:
            if self._closed:
                _fail("pipe_closed", "私有管道已经关闭。")
            if self._accepted or not self._api.valid_handle(self._handle):
                _fail("pipe_already_accepted", "私有管道只允许一个连接。")
            operation = self._api.overlapped()
            try:
                ok = self._api.kernel.ConnectNamedPipe(
                    self._handle, ctypes.byref(operation.overlapped),
                )
                if not ok:
                    error = ctypes.get_last_error()
                    if error == _ERROR_PIPE_CONNECTED:
                        pass
                    elif error == _ERROR_IO_PENDING:
                        self._api.wait_overlapped(
                            self._handle, operation, timeout_ms,
                            timeout_code="pipe_connect_timeout",
                        )
                    else:
                        _fail("pipe_connect_failed", "无法接受私有管道连接。")
                handle, self._handle = self._handle, None
                self._accepted = True
                return NativePipeConnection(
                    handle,
                    io_timeout=self._io_timeout,
                    server_end=True,
                    api=self._api,
                )
            finally:
                operation.close()

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
            handle, self._handle = self._handle, None
            if self._api.valid_handle(handle):
                self._api.kernel.CancelIoEx(handle, None)
                self._api.kernel.DisconnectNamedPipe(handle)
                self._api.close_handle(handle)

    def __enter__(self):
        if self._closed:
            _fail("pipe_closed", "私有管道已经关闭。")
        return self

    def __exit__(self, *_exc):
        self.close()


def open_local_pipe_client(
    name: str,
    *,
    connect_timeout: float = 10.0,
    io_timeout: float = 10.0,
) -> NativePipeConnection:
    """Open a local client endpoint; production sidecars use their Node peer."""
    if not isinstance(name, str) or PIPE_NAME_RE.fullmatch(name) is None:
        _fail("pipe_name_invalid", "私有管道名称不合法。")
    timeout_ms = connect_timeout_milliseconds(connect_timeout)
    _timeout_ms(io_timeout)
    api = _api()
    if not api.kernel.WaitNamedPipeW(name, timeout_ms):
        _fail("pipe_connect_timeout", "等待私有管道服务端超时。")
    handle = api.kernel.CreateFileW(
        name,
        _GENERIC_READ | _GENERIC_WRITE,
        0,
        None,
        _OPEN_EXISTING,
        _FILE_FLAG_OVERLAPPED | _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION,
        None,
    )
    if not api.valid_handle(handle):
        _fail("pipe_connect_failed", "无法连接私有管道服务端。")
    return NativePipeConnection(
        handle, io_timeout=io_timeout, server_end=False, api=api,
    )


__all__ = [
    "MAX_PIPE_IO_BYTES",
    "PIPE_NAME_RE",
    "NativePipeConnection",
    "NativePipeError",
    "NativePipeServer",
    "connect_timeout_milliseconds",
    "current_user_sid",
    "open_local_pipe_client",
    "random_pipe_name",
]
