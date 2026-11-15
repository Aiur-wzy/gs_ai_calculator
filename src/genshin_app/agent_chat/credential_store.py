"""OS-backed credentials for the App-native Agent.

Only this module knows the Windows Credential Manager target name.  Callers
receive the secret only when establishing a provider connection; ordinary
configuration, sessions and renderer state retain status metadata instead.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
from contextlib import contextmanager
from ctypes import wintypes

from genshin_app.version import APP_DIR_NAME

DEEPSEEK_CREDENTIAL_TARGET = f"{APP_DIR_NAME}/NativeAgent/DeepSeek"
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168
_MAX_SECRET_BYTES = 2048
_MAX_CREDENTIAL_GENERATION = (1 << 53) - 1
# Legacy API keys reject control characters, so the NUL-delimited prefix can
# never be mistaken for a previously stored plaintext key.
_VERSIONED_PREFIX = b"\x00GCA-CREDENTIAL-V1\x00"
_WAIT_OBJECT_0 = 0
_WAIT_ABANDONED = 0x80
_MUTEX_WAIT_MILLISECONDS = 30_000
_TOKEN_QUERY = 0x0008
_TOKEN_USER_CLASS = 1
_TOKEN_APP_CONTAINER_SID_CLASS = 31
_SECURITY_DESCRIPTOR_REVISION = 1
_TARGET_LOCKS: dict[str, threading.RLock] = {}
_TARGET_LOCKS_GUARD = threading.Lock()
_STORE_ERROR_CODES = frozenset({
    "credential_store_invalid",
    "credential_store_unavailable",
    "credential_store_read_failed",
    "credential_store_lock_failed",
    "credential_store_write_failed",
})


def _target_lock(target: str) -> threading.RLock:
    with _TARGET_LOCKS_GUARD:
        return _TARGET_LOCKS.setdefault(target, threading.RLock())


def _validated_windows_sid(value: object) -> str:
    """Accept only the canonical numeric SID form returned by Win32."""

    if not isinstance(value, str):
        raise CredentialStoreError("credential_store_unavailable")
    parts = value.split("-")
    if (len(parts) < 4 or parts[0] != "S" or parts[1] != "1"
            or any(not part.isascii() or not part.isdecimal() for part in parts[2:])):
        raise CredentialStoreError("credential_store_unavailable")
    return value


def _global_mutex_name(target: str, user_sid: str) -> str:
    """Use one cross-session mutex namespace per Windows account and target."""

    sid = _validated_windows_sid(user_sid)
    suffix = hashlib.sha256(f"{sid}\0{target}".encode()).hexdigest()
    return f"Global\\GenshinCalc.NativeAgent.Credential.{suffix}"


def _mutex_security_sddl(
    user_sid: str,
    app_container_sid: str | None = None,
) -> str:
    """Grant the account, its exact AppContainer when present, and SYSTEM."""

    sid = _validated_windows_sid(user_sid)
    app_sid = (
        "" if app_container_sid is None
        else f"(A;;GA;;;{_validated_windows_sid(app_container_sid)})"
    )
    # AppContainer access checks require an ACE for the exact package SID in
    # addition to the Windows-account ACE.  This stays narrower than AC (all
    # application packages) and preserves the current-user check.
    return f"O:{sid}D:P(A;;GA;;;{sid}){app_sid}(A;;GA;;;SY)"


def _validate_secret_worker(secret: object) -> str | None:
    """Return a valid secret, or ``None``, without exporting an exception."""

    try:
        if (not isinstance(secret, str) or not secret or len(secret) > 512
                or any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7f
                       for char in secret)
                or len(secret.encode("utf-8")) > _MAX_SECRET_BYTES):
            return None
    except Exception:  # noqa: BLE001 - reject hostile strings and lone surrogates
        return None
    return secret


def _validate_secret(secret: object) -> str:
    """Validate without retaining the submitted value in a failure traceback."""

    validated = None
    try:
        validated = _validate_secret_worker(secret)
    except Exception:  # noqa: BLE001 - keep the public codec boundary sanitized
        validated = None
    finally:
        secret = None
    if validated is None:
        raise CredentialStoreError("credential_store_invalid")
    return validated


def _encode_versioned_worker(
    generation: object,
    secret: object,
) -> bytes | None:
    """Encode a record, reducing every invalid input to ``None``."""

    if (isinstance(generation, bool) or not isinstance(generation, int) or generation < 1
            or generation > _MAX_CREDENTIAL_GENERATION):
        return None
    if secret is not None:
        secret = _validate_secret_worker(secret)
        if secret is None:
            return None
    try:
        raw = _VERSIONED_PREFIX + json.dumps(
            {"generation": generation, "secret": secret},
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except Exception:  # noqa: BLE001 - never export secret-bearing JSON errors
        return None
    if len(raw) > _MAX_SECRET_BYTES:
        return None
    return raw


def _encode_versioned(generation: int, secret: str | None) -> bytes:
    """Encode a versioned record with a credential-free failure traceback."""

    raw = None
    try:
        raw = _encode_versioned_worker(generation, secret)
    except Exception:  # noqa: BLE001 - keep the public codec boundary sanitized
        raw = None
    finally:
        secret = None
    if raw is None:
        raise CredentialStoreError("credential_store_invalid")
    return raw


def _decode_versioned_worker(raw: object) -> tuple[int, str | None] | None:
    """Decode a record without allowing raw/provider exceptions to escape."""

    if not isinstance(raw, bytes):
        return None
    if not raw.startswith(_VERSIONED_PREFIX):
        try:
            decoded = raw.decode("utf-8")
        except Exception:  # noqa: BLE001 - invalid legacy blobs are one safe code
            return None
        secret = _validate_secret_worker(decoded)
        return None if secret is None else (1, secret)
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError("duplicate key")
                value[key] = item
            return value

        value = json.loads(
            raw[len(_VERSIONED_PREFIX):].decode("utf-8"),
            object_pairs_hook=unique,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("constant")),
        )
    except Exception:  # noqa: BLE001 - discard parser errors and their source text
        return None
    if not isinstance(value, dict) or set(value) != {"generation", "secret"}:
        return None
    generation = value["generation"]
    if (isinstance(generation, bool) or not isinstance(generation, int) or generation < 1
            or generation > _MAX_CREDENTIAL_GENERATION):
        return None
    secret = value["secret"]
    if secret is not None:
        secret = _validate_secret_worker(secret)
        if secret is None:
            return None
    return generation, secret


def _decode_versioned(raw: bytes) -> tuple[int, str | None]:
    """Decode without retaining a raw envelope in a failure traceback."""

    record = None
    try:
        record = _decode_versioned_worker(raw)
    except Exception:  # noqa: BLE001 - keep the public codec boundary sanitized
        record = None
    finally:
        raw = b""
    if record is None:
        raise CredentialStoreError("credential_store_invalid")
    return record


class CredentialStoreError(RuntimeError):
    """A sanitized local credential-store failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _store_error_code(error: object, default: str) -> str:
    """Reduce an arbitrary backend exception to a reviewed, secret-free code."""

    try:
        code = error.code if isinstance(error, CredentialStoreError) else None
    except Exception:  # noqa: BLE001 - hostile exception attributes are untrusted
        code = None
    return code if type(code) is str and code in _STORE_ERROR_CODES else default


class UnavailableCredentialStore:
    """Fail-closed store used on platforms without Windows Credential Manager."""

    available = False

    def has(self) -> bool:
        return False

    def read(self) -> str | None:
        return None

    def write(self, _secret: str) -> None:
        raise CredentialStoreError("credential_store_unavailable")

    def delete(self) -> bool:
        return False

    def read_versioned(self) -> tuple[int, str | None]:
        raise CredentialStoreError("credential_store_unavailable")

    def compare_and_swap(self, **_kwargs) -> bool:
        raise CredentialStoreError("credential_store_unavailable")


if os.name == "nt":
    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]


    class _SidAndAttributes(ctypes.Structure):
        _fields_ = [
            ("Sid", ctypes.c_void_p),
            ("Attributes", wintypes.DWORD),
        ]


    class _TokenUser(ctypes.Structure):
        _fields_ = [("User", _SidAndAttributes)]


    class _CredentialW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", wintypes.FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]


class WindowsCredentialStore:
    """Store a generic credential under the current Windows user."""

    available = True

    def __init__(self, target: str = DEEPSEEK_CREDENTIAL_TARGET):
        if os.name != "nt":
            raise CredentialStoreError("credential_store_unavailable")
        self.target = target
        self._mutation_lock = _target_lock(target)
        try:
            library = ctypes.WinDLL("advapi32", use_last_error=True)
            self._read = library.CredReadW
            self._read.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(_CredentialW))]
            self._read.restype = wintypes.BOOL
            self._write = library.CredWriteW
            self._write.argtypes = [ctypes.POINTER(_CredentialW), wintypes.DWORD]
            self._write.restype = wintypes.BOOL
            self._delete = library.CredDeleteW
            self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
            self._delete.restype = wintypes.BOOL
            self._open_process_token = library.OpenProcessToken
            self._open_process_token.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
            ]
            self._open_process_token.restype = wintypes.BOOL
            self._get_token_information = library.GetTokenInformation
            self._get_token_information.argtypes = [
                wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ]
            self._get_token_information.restype = wintypes.BOOL
            self._convert_sid_to_string = library.ConvertSidToStringSidW
            self._convert_sid_to_string.argtypes = [
                ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR),
            ]
            self._convert_sid_to_string.restype = wintypes.BOOL
            self._convert_sddl = library.ConvertStringSecurityDescriptorToSecurityDescriptorW
            self._convert_sddl.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                ctypes.POINTER(wintypes.DWORD),
            ]
            self._convert_sddl.restype = wintypes.BOOL
            self._free = library.CredFree
            self._free.argtypes = [ctypes.c_void_p]
            self._free.restype = None
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self._get_current_process = kernel.GetCurrentProcess
            self._get_current_process.argtypes = []
            self._get_current_process.restype = wintypes.HANDLE
            self._create_mutex = kernel.CreateMutexW
            self._create_mutex.argtypes = [
                ctypes.POINTER(_SecurityAttributes), wintypes.BOOL, wintypes.LPCWSTR,
            ]
            self._create_mutex.restype = wintypes.HANDLE
            self._wait_mutex = kernel.WaitForSingleObject
            self._wait_mutex.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            self._wait_mutex.restype = wintypes.DWORD
            self._release_mutex = kernel.ReleaseMutex
            self._release_mutex.argtypes = [wintypes.HANDLE]
            self._release_mutex.restype = wintypes.BOOL
            self._close_handle = kernel.CloseHandle
            self._close_handle.argtypes = [wintypes.HANDLE]
            self._close_handle.restype = wintypes.BOOL
            self._local_free = kernel.LocalFree
            self._local_free.argtypes = [ctypes.c_void_p]
            self._local_free.restype = ctypes.c_void_p
            user_sid = self._current_user_sid()
            self._mutex_name = _global_mutex_name(target, user_sid)
            self._mutex_sddl = _mutex_security_sddl(
                user_sid,
                self._current_app_container_sid(),
            )
        except (AttributeError, OSError) as exc:
            raise CredentialStoreError("credential_store_unavailable") from exc

    @staticmethod
    def _failure(code: str) -> CredentialStoreError:
        # The numeric Windows error is deliberately omitted: provider-facing
        # diagnostics only need a stable local code and never need secret data.
        return CredentialStoreError(code)

    def _read_pointer(self):
        pointer = ctypes.POINTER(_CredentialW)()
        if not self._read(self.target, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == _ERROR_NOT_FOUND:
                return None
            raise self._failure("credential_store_read_failed")
        return pointer

    def _current_user_sid(self) -> str:
        """Resolve the token user SID without consulting environment variables."""

        token = wintypes.HANDLE()
        if not self._open_process_token(
            self._get_current_process(), _TOKEN_QUERY, ctypes.byref(token),
        ):
            raise self._failure("credential_store_unavailable")
        try:
            required = wintypes.DWORD()
            self._get_token_information(
                token, _TOKEN_USER_CLASS, None, 0, ctypes.byref(required),
            )
            if required.value <= 0:
                raise self._failure("credential_store_unavailable")
            buffer = ctypes.create_string_buffer(required.value)
            if not self._get_token_information(
                token,
                _TOKEN_USER_CLASS,
                buffer,
                required.value,
                ctypes.byref(required),
            ):
                raise self._failure("credential_store_unavailable")
            token_user = ctypes.cast(buffer, ctypes.POINTER(_TokenUser)).contents
            text = wintypes.LPWSTR()
            if not self._convert_sid_to_string(token_user.User.Sid, ctypes.byref(text)):
                raise self._failure("credential_store_unavailable")
            try:
                return _validated_windows_sid(text.value)
            finally:
                self._local_free(ctypes.cast(text, ctypes.c_void_p))
        finally:
            self._close_handle(token)

    def _current_app_container_sid(self) -> str | None:
        """Return the exact package SID for a restricted AppContainer token."""

        token = wintypes.HANDLE()
        if not self._open_process_token(
            self._get_current_process(), _TOKEN_QUERY, ctypes.byref(token),
        ):
            raise self._failure("credential_store_unavailable")
        try:
            required = wintypes.DWORD()
            self._get_token_information(
                token,
                _TOKEN_APP_CONTAINER_SID_CLASS,
                None,
                0,
                ctypes.byref(required),
            )
            if required.value <= 0:
                return None
            buffer = ctypes.create_string_buffer(required.value)
            if not self._get_token_information(
                token,
                _TOKEN_APP_CONTAINER_SID_CLASS,
                buffer,
                required.value,
                ctypes.byref(required),
            ):
                raise self._failure("credential_store_unavailable")
            sid_pointer = ctypes.cast(
                buffer, ctypes.POINTER(ctypes.c_void_p),
            ).contents.value
            if not sid_pointer:
                return None
            text = wintypes.LPWSTR()
            if not self._convert_sid_to_string(sid_pointer, ctypes.byref(text)):
                raise self._failure("credential_store_unavailable")
            try:
                return _validated_windows_sid(text.value)
            finally:
                self._local_free(ctypes.cast(text, ctypes.c_void_p))
        finally:
            self._close_handle(token)

    @contextmanager
    def _mutex_security_attributes(self):
        """Build a non-inheritable current-user DACL for one CreateMutex call."""

        descriptor = ctypes.c_void_p()
        if not self._convert_sddl(
            self._mutex_sddl,
            _SECURITY_DESCRIPTOR_REVISION,
            ctypes.byref(descriptor),
            None,
        ):
            raise self._failure("credential_store_lock_failed")
        attributes = _SecurityAttributes()
        attributes.nLength = ctypes.sizeof(_SecurityAttributes)
        attributes.lpSecurityDescriptor = descriptor.value
        attributes.bInheritHandle = False
        try:
            yield ctypes.byref(attributes)
        finally:
            if descriptor.value:
                self._local_free(descriptor)

    @contextmanager
    def _mutation_guard(self):
        """Serialize credential CAS across windows and App processes."""
        with self._mutation_lock:
            with self._mutex_security_attributes() as security:
                handle = self._create_mutex(security, False, self._mutex_name)
            if not handle:
                raise self._failure("credential_store_lock_failed")
            acquired = False
            try:
                result = int(self._wait_mutex(handle, _MUTEX_WAIT_MILLISECONDS))
                acquired = result in (_WAIT_OBJECT_0, _WAIT_ABANDONED)
                if not acquired:
                    raise self._failure("credential_store_lock_failed")
                yield
            finally:
                if acquired:
                    self._release_mutex(handle)
                self._close_handle(handle)

    def _read_blob(self) -> bytes | None:
        pointer = self._read_pointer()
        if pointer is None:
            return None
        try:
            size = int(pointer.contents.CredentialBlobSize)
            if size <= 0 or size > _MAX_SECRET_BYTES or not pointer.contents.CredentialBlob:
                raise self._failure("credential_store_invalid")
            return ctypes.string_at(pointer.contents.CredentialBlob, size)
        finally:
            self._free(pointer)

    def _write_blob(self, raw: bytes) -> None:
        if not raw or len(raw) > _MAX_SECRET_BYTES:
            raise CredentialStoreError("credential_store_invalid")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
        credential = _CredentialW()
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = self.target
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "DeepSeek API"
        try:
            if not self._write(ctypes.byref(credential), 0):
                raise self._failure("credential_store_write_failed")
        finally:
            ctypes.memset(ctypes.addressof(blob), 0, len(raw))

    def _read_versioned_fact(
        self,
    ) -> tuple[tuple[int, str | None] | None, str | None]:
        """Read inside a boundary whose secret-bearing frame never escapes."""

        raw = None
        try:
            raw = self._read_blob()
        except Exception as error:  # noqa: BLE001 - export only a reviewed code
            return None, _store_error_code(error, "credential_store_read_failed")
        try:
            if raw is None:
                return (0, None), None
            record = _decode_versioned_worker(raw)
            if record is None:
                return None, "credential_store_invalid"
            return record, None
        except Exception:  # noqa: BLE001 - never export raw/provider exceptions
            return None, "credential_store_read_failed"
        finally:
            raw = None

    def read_versioned(self) -> tuple[int, str | None]:
        record = None
        error_code = "credential_store_read_failed"
        try:
            record, error_code = self._read_versioned_fact()
        except Exception:  # noqa: BLE001 - keep the public traceback sanitized
            record = None
            error_code = "credential_store_read_failed"
        if error_code is not None or record is None:
            raise CredentialStoreError(error_code or "credential_store_read_failed")
        return record

    def _compare_and_swap_fact(
        self,
        *,
        expected_generation: object,
        next_generation: object,
        secret: object,
    ) -> tuple[bool | None, str | None]:
        """Perform CAS while containing every secret-bearing failure frame."""

        try:
            if (isinstance(expected_generation, bool)
                    or not isinstance(expected_generation, int)
                    or expected_generation < 0
                    or expected_generation >= _MAX_CREDENTIAL_GENERATION
                    or next_generation != expected_generation + 1):
                return None, "credential_store_invalid"
            validated_secret = None
            if secret is not None:
                validated_secret = _validate_secret_worker(secret)
                if validated_secret is None:
                    return None, "credential_store_invalid"
            with self._mutation_guard():
                record, error_code = self._read_versioned_fact()
                if error_code is not None or record is None:
                    return None, error_code or "credential_store_read_failed"
                current_generation, _current_secret = record
                if current_generation != expected_generation:
                    return False, None
                raw = _encode_versioned_worker(next_generation, validated_secret)
                if raw is None:
                    return None, "credential_store_invalid"
                self._write_blob(raw)
            return True, None
        except Exception as error:  # noqa: BLE001 - export only a reviewed code
            return None, _store_error_code(error, "credential_store_write_failed")

    def compare_and_swap(
        self,
        *,
        expected_generation: int,
        next_generation: int,
        secret: str | None,
    ) -> bool:
        result = None
        error_code = "credential_store_write_failed"
        try:
            result, error_code = self._compare_and_swap_fact(
                expected_generation=expected_generation,
                next_generation=next_generation,
                secret=secret,
            )
        except Exception:  # noqa: BLE001 - keep the public traceback sanitized
            result = None
            error_code = "credential_store_write_failed"
        finally:
            secret = None
        if error_code is not None or type(result) is not bool:
            raise CredentialStoreError(error_code or "credential_store_write_failed")
        return result

    def has(self) -> bool:
        _generation, secret = self.read_versioned()
        return secret is not None

    def read(self) -> str | None:
        _generation, secret = self.read_versioned()
        return secret

    def _write_fact(self, secret: object) -> str | None:
        """Write while containing the submitted and replaced credentials."""

        try:
            validated_secret = _validate_secret_worker(secret)
            if validated_secret is None:
                return "credential_store_invalid"
            with self._mutation_guard():
                record, error_code = self._read_versioned_fact()
                if error_code is not None or record is None:
                    return error_code or "credential_store_read_failed"
                generation, _old_secret = record
                raw = _encode_versioned_worker(generation + 1, validated_secret)
                if raw is None:
                    return "credential_store_invalid"
                self._write_blob(raw)
            return None
        except Exception as error:  # noqa: BLE001 - export only a reviewed code
            return _store_error_code(error, "credential_store_write_failed")

    def write(self, secret: str) -> None:
        error_code = "credential_store_write_failed"
        try:
            error_code = self._write_fact(secret)
        except Exception:  # noqa: BLE001 - keep the public traceback sanitized
            error_code = "credential_store_write_failed"
        finally:
            secret = None
        if error_code is not None:
            raise CredentialStoreError(error_code)

    def _delete_fact(self) -> tuple[bool | None, str | None]:
        """Write a tombstone while containing the replaced credential."""

        try:
            with self._mutation_guard():
                record, error_code = self._read_versioned_fact()
                if error_code is not None or record is None:
                    return None, error_code or "credential_store_read_failed"
                generation, secret = record
                if secret is None:
                    return False, None
                raw = _encode_versioned_worker(generation + 1, None)
                if raw is None:
                    return None, "credential_store_invalid"
                self._write_blob(raw)
            return True, None
        except Exception as error:  # noqa: BLE001 - export only a reviewed code
            return None, _store_error_code(error, "credential_store_write_failed")

    def delete(self) -> bool:
        # Keep a versioned tombstone.  Removing the record would reset the
        # generation and let an older window recreate a deleted key.
        result = None
        error_code = "credential_store_write_failed"
        try:
            result, error_code = self._delete_fact()
        except Exception:  # noqa: BLE001 - keep the public traceback sanitized
            result = None
            error_code = "credential_store_write_failed"
        if error_code is not None or type(result) is not bool:
            raise CredentialStoreError(error_code or "credential_store_write_failed")
        return result


def default_credential_store():
    """Return the platform store without ever falling back to plaintext."""
    if os.name != "nt":
        return UnavailableCredentialStore()
    try:
        return WindowsCredentialStore()
    except CredentialStoreError:
        return UnavailableCredentialStore()


def credential_status(store=None) -> dict:
    """Return renderer-safe status; the secret never crosses this boundary."""
    store = store or default_credential_store()
    available = bool(getattr(store, "available", False))
    try:
        saved = bool(store.has()) if available else False
    except CredentialStoreError:
        return {"available": available, "saved": False, "status": "error"}
    return {"available": available, "saved": saved,
            "status": "saved" if saved else "empty" if available else "unavailable"}


__all__ = [
    "DEEPSEEK_CREDENTIAL_TARGET",
    "CredentialStoreError",
    "UnavailableCredentialStore",
    "WindowsCredentialStore",
    "credential_status",
    "default_credential_store",
]
