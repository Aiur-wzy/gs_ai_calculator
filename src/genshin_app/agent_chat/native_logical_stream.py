"""Transport-free qualification model for large native Agent payloads.

The mux wire remains bounded to its existing packet limit.  This module models
an application-level open/chunk/commit stream which may carry a larger logical
DSH request without ever constructing one equally large mux packet.  It owns
no pipe, endpoint or GUI state and is deliberately not a production carrier.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGICAL_STREAM_CONTRACT = "genshin-native-logical-byte-stream-v1"
PRODUCTION_OWNER = False
DEFAULT_LOGICAL_STREAM_MAX_BYTES = 300 * 1024 * 1024
HARD_MAX_LOGICAL_STREAM_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_LOGICAL_STREAM_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_LOGICAL_STREAM_CREDIT_BYTES = 2 * DEFAULT_LOGICAL_STREAM_CHUNK_BYTES
DEFAULT_DIRECT_PAYLOAD_THRESHOLD_BYTES = 1024 * 1024
MIN_LOGICAL_STREAM_CHUNK_BYTES = 64 * 1024
MAX_LOGICAL_STREAM_CHUNK_BYTES = 16 * 1024 * 1024
MAX_LOGICAL_STREAM_METADATA_BYTES = 64 * 1024
# Qualification deployment choices only.  DSH defines none of these values,
# and the project has not yet measured production logical-stream high water.
# Production wiring must record active/retained/spool telemetry before tuning.
DEFAULT_LOGICAL_STREAM_MAX_ACTIVE = 4
DEFAULT_LOGICAL_STREAM_MAX_RETAINED = 4096
DEFAULT_LOGICAL_STREAM_AGGREGATE_SPOOL_BYTES = 2 * 1024 * 1024 * 1024
JSON_SAFE_INTEGER_MAX = (1 << 53) - 1

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OPEN_KEYS = frozenset(
    {
        "contract",
        "transfer_id",
        "purpose",
        "media_type",
        "content_encoding",
        "maximum_bytes",
        "declared_length",
        "declared_sha256",
        "chunk_bytes",
        "metadata",
    }
)
_COMMIT_KEYS = frozenset(
    {"transfer_id", "chunk_count", "total_length", "sha256", "terminal"}
)


class NativeLogicalStreamError(RuntimeError):
    """Sanitized qualification failure; payload bytes never enter messages."""

    def __init__(self, code: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = dict(details or {})


def _reject(code: str) -> None:
    raise NativeLogicalStreamError(code)


def _integer(value: Any, *, minimum: int, maximum: int, code: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        _reject(code)
    return value


def _identifier(value: Any, code: str) -> str:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        _reject(code)
    return value


def _optional_sha256(value: Any, code: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256_PATTERN.fullmatch(value) is None:
        _reject(code)
    return value


def _owned_bytes(value: Any, *, maximum: int) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        _reject("logical_stream_chunk_not_bytes")
    try:
        view = memoryview(value)
    except (TypeError, ValueError, BufferError):
        _reject("logical_stream_chunk_not_bytes")
    if view.nbytes > maximum:
        _reject("logical_stream_chunk_too_large")
    if view.nbytes == 0:
        _reject("logical_stream_chunk_empty")
    if type(value) is bytes:
        return value
    try:
        return view.tobytes()
    except (TypeError, ValueError, BufferError):
        _reject("logical_stream_chunk_not_bytes")


def _json_string(value: str) -> str:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        _reject("logical_stream_metadata_invalid")
    return value


def _canonical_object_key(value: str) -> str:
    key = _json_string(value)
    # ECMAScript JSON.stringify enumerates array-index keys before other keys,
    # regardless of insertion order.  Rejecting that narrow key class keeps
    # the Python and Node canonical writers byte-identical without importing a
    # second JSON canonicalization implementation.
    decimal_index = (
        key.isascii()
        and key.isdigit()
        and not key.startswith("0")
        and (len(key) < 10 or (len(key) == 10 and key <= "4294967294"))
    )
    if key == "0" or decimal_index:
        _reject("logical_stream_metadata_invalid")
    return key


def _canonical_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 32:
        _reject("logical_stream_metadata_invalid")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if not -JSON_SAFE_INTEGER_MAX <= value <= JSON_SAFE_INTEGER_MAX:
            _reject("logical_stream_metadata_invalid")
        return value
    if type(value) is str:
        return _json_string(value)
    if type(value) is list:
        return [_canonical_json_value(item, depth=depth + 1) for item in value]
    if type(value) is dict:
        if any(type(key) is not str for key in value):
            _reject("logical_stream_metadata_invalid")
        normalized_items = [
            (
                _canonical_object_key(key),
                _canonical_json_value(item, depth=depth + 1),
            )
            for key, item in value.items()
        ]
        return {
            key: item
            for key, item in sorted(
                normalized_items, key=lambda pair: pair[0].encode("utf-8")
            )
        }
    # Floats (including NaN/Infinity), tuples and custom objects are rejected
    # so Python and JavaScript accept exactly the same JSON-safe domain.
    _reject("logical_stream_metadata_invalid")


def _metadata(value: Any) -> tuple[dict[str, Any], str]:
    if type(value) is not dict:
        _reject("logical_stream_metadata_invalid")
    normalized = _canonical_json_value(value)
    if not isinstance(normalized, dict):  # pragma: no cover - guarded above
        _reject("logical_stream_metadata_invalid")
    try:
        encoded = json.dumps(
            normalized,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        _reject("logical_stream_metadata_invalid")
    if len(encoded) > MAX_LOGICAL_STREAM_METADATA_BYTES:
        _reject("logical_stream_metadata_too_large")
    return normalized, encoded.decode("utf-8")


@dataclass(frozen=True, slots=True)
class LogicalStreamArtifact:
    transfer_id: str
    path: Path
    total_length: int
    sha256: str
    chunk_count: int
    purpose: str
    media_type: str
    metadata_json: str


@dataclass(slots=True)
class _ReceiveState:
    transfer_id: str
    purpose: str
    media_type: str
    maximum_bytes: int
    declared_length: int | None
    declared_sha256: str | None
    chunk_bytes: int
    credit_window_bytes: int
    metadata_json: str
    partial_path: Path
    handle: Any
    digest: Any
    next_stream_seq: int = 2
    total_length: int = 0
    chunk_count: int = 0
    spool_bytes: int = 0
    short_chunk_seen: bool = False
    busy: bool = False


class LogicalStreamCreditTracker:
    """Sender-side credit accounting independent from a transport."""

    def __init__(self, *, transfer_id: str, maximum_credit_bytes: int):
        self._transfer_id = _identifier(
            transfer_id, "logical_stream_transfer_id_invalid"
        )
        self._maximum = _integer(
            maximum_credit_bytes,
            minimum=1,
            maximum=HARD_MAX_LOGICAL_STREAM_BYTES,
            code="logical_stream_credit_limit_invalid",
        )
        self._available = 0
        self._accepted_through_seq = 0

    @property
    def available_bytes(self) -> int:
        return self._available

    @property
    def accepted_through_seq(self) -> int:
        return self._accepted_through_seq

    def apply_grant(
        self, *, transfer_id: str, accepted_through_seq: int, grant_bytes: int
    ) -> None:
        if transfer_id != self._transfer_id:
            _reject("logical_stream_credit_transfer_mismatch")
        sequence = _integer(
            accepted_through_seq,
            minimum=1,
            maximum=JSON_SAFE_INTEGER_MAX,
            code="logical_stream_credit_sequence_invalid",
        )
        if sequence <= self._accepted_through_seq:
            _reject("logical_stream_credit_replayed")
        if sequence != self._accepted_through_seq + 1:
            _reject("logical_stream_credit_sequence_mismatch")
        amount = _integer(
            grant_bytes,
            minimum=1,
            maximum=self._maximum,
            code="logical_stream_credit_invalid",
        )
        if self._available + amount > self._maximum:
            _reject("logical_stream_credit_overflow")
        self._available += amount
        self._accepted_through_seq = sequence

    def reserve(self, value: int) -> None:
        amount = _integer(
            value,
            minimum=1,
            maximum=self._maximum,
            code="logical_stream_credit_invalid",
        )
        if amount > self._available:
            _reject("logical_stream_credit_exhausted")
        self._available -= amount


class LogicalStreamAssembler:
    """File-backed open/chunk/commit receiver used by qualification tests.

    A chunk is credited back only after it is flushed and fsynced to the
    private spool.  Commit verifies the whole-stream length and SHA-256 before
    atomically publishing a ``.ready`` artifact.  Any protocol or I/O failure
    aborts the transfer and removes its partial file.
    """

    def __init__(
        self,
        *,
        spool_root: Path,
        allowed_purposes: frozenset[str],
        metadata_keys_by_purpose: Mapping[str, frozenset[str]] | None = None,
        maximum_bytes: int = DEFAULT_LOGICAL_STREAM_MAX_BYTES,
        credit_window_bytes: int = DEFAULT_LOGICAL_STREAM_CREDIT_BYTES,
        minimum_chunk_bytes: int = MIN_LOGICAL_STREAM_CHUNK_BYTES,
        maximum_chunk_bytes: int = MAX_LOGICAL_STREAM_CHUNK_BYTES,
        max_active_transfers: int = DEFAULT_LOGICAL_STREAM_MAX_ACTIVE,
        max_retained_transfer_ids: int = DEFAULT_LOGICAL_STREAM_MAX_RETAINED,
        aggregate_spool_bytes: int = DEFAULT_LOGICAL_STREAM_AGGREGATE_SPOOL_BYTES,
    ):
        self._maximum_bytes = _integer(
            maximum_bytes,
            minimum=1,
            maximum=HARD_MAX_LOGICAL_STREAM_BYTES,
            code="logical_stream_maximum_invalid",
        )
        self._minimum_chunk_bytes = _integer(
            minimum_chunk_bytes,
            minimum=1,
            maximum=MAX_LOGICAL_STREAM_CHUNK_BYTES,
            code="logical_stream_minimum_chunk_invalid",
        )
        self._maximum_chunk_bytes = _integer(
            maximum_chunk_bytes,
            minimum=self._minimum_chunk_bytes,
            maximum=MAX_LOGICAL_STREAM_CHUNK_BYTES,
            code="logical_stream_maximum_chunk_invalid",
        )
        self._credit_window_bytes = _integer(
            credit_window_bytes,
            minimum=1,
            maximum=HARD_MAX_LOGICAL_STREAM_BYTES,
            code="logical_stream_credit_window_invalid",
        )
        self._max_active_transfers = _integer(
            max_active_transfers,
            minimum=1,
            maximum=DEFAULT_LOGICAL_STREAM_MAX_RETAINED,
            code="logical_stream_active_limit_invalid",
        )
        self._max_retained_transfer_ids = _integer(
            max_retained_transfer_ids,
            minimum=self._max_active_transfers,
            maximum=DEFAULT_LOGICAL_STREAM_MAX_RETAINED,
            code="logical_stream_retained_limit_invalid",
        )
        self._aggregate_spool_limit = _integer(
            aggregate_spool_bytes,
            minimum=1,
            maximum=JSON_SAFE_INTEGER_MAX,
            code="logical_stream_aggregate_spool_limit_invalid",
        )
        if not allowed_purposes or any(
            not isinstance(item, str) or _ID_PATTERN.fullmatch(item) is None
            for item in allowed_purposes
        ):
            _reject("logical_stream_purposes_invalid")
        self._allowed_purposes = frozenset(allowed_purposes)
        raw_metadata_policies = metadata_keys_by_purpose or {}
        if not isinstance(raw_metadata_policies, Mapping):
            _reject("logical_stream_metadata_policy_invalid")
        self._metadata_keys_by_purpose: dict[str, frozenset[str]] = {}
        for purpose in self._allowed_purposes:
            keys = raw_metadata_policies.get(purpose, frozenset())
            if not isinstance(keys, frozenset) or any(
                not isinstance(key, str) for key in keys
            ):
                _reject("logical_stream_metadata_policy_invalid")
            self._metadata_keys_by_purpose[purpose] = keys
        root = Path(spool_root)
        if not root.is_dir() or root.is_symlink():
            _reject("logical_stream_spool_root_invalid")
        try:
            self._spool_root = root.resolve(strict=True)
        except OSError:
            _reject("logical_stream_spool_root_invalid")
        self._active: dict[str, _ReceiveState] = {}
        self._artifacts: dict[str, LogicalStreamArtifact] = {}
        self._retained_ids: set[str] = set()
        self._spool_bytes = 0
        self._lock = threading.RLock()

    @property
    def active_transfers(self) -> int:
        return len(self._active)

    @property
    def retained_transfer_ids(self) -> int:
        return len(self._retained_ids)

    @property
    def aggregate_spool_bytes(self) -> int:
        return self._spool_bytes

    def _claim(self, transfer_id: str) -> _ReceiveState:
        with self._lock:
            state = self._active.get(transfer_id)
            if state is None:
                _reject("logical_stream_transfer_not_active")
            if state.busy:
                _reject("logical_stream_operation_in_progress")
            state.busy = True
            return state

    def _release_claim(self, state: _ReceiveState) -> None:
        with self._lock:
            current = self._active.get(state.transfer_id)
            if current is state:
                state.busy = False

    def _abort(self, transfer_id: str, *, cause: str) -> None:
        with self._lock:
            state = self._active.get(transfer_id)
            if state is None:
                return
        failures: list[str] = []
        try:
            state.handle.close()
        except OSError:
            failures.append("close")
        try:
            state.partial_path.unlink(missing_ok=True)
        except OSError:
            failures.append("unlink")
        with self._lock:
            if not failures or "unlink" not in failures:
                self._spool_bytes -= state.spool_bytes
                self._active.pop(transfer_id, None)
            else:
                state.busy = False
        if failures:
            raise NativeLogicalStreamError(
                "logical_stream_cleanup_failed",
                details={
                    "transfer_id": transfer_id,
                    "cause": cause,
                    "phases": tuple(failures),
                },
            )

    def abort_all(self, *, reason: str = "runtime_shutdown") -> dict[str, Any]:
        _identifier(reason, "logical_stream_cancel_reason_invalid")
        with self._lock:
            busy = sorted(
                transfer_id for transfer_id, state in self._active.items() if state.busy
            )
            transfer_ids = tuple(self._active)
            if busy:
                raise NativeLogicalStreamError(
                    "logical_stream_operation_in_progress",
                    details={"transfer_ids": tuple(busy)},
                )
            for state in self._active.values():
                state.busy = True
        failures: list[dict[str, Any]] = []
        for transfer_id in transfer_ids:
            try:
                self._abort(transfer_id, cause=reason)
            except NativeLogicalStreamError as exc:
                failures.append(exc.details)
        if failures:
            raise NativeLogicalStreamError(
                "logical_stream_cleanup_failed",
                details={"cause": reason, "failures": tuple(failures)},
            )
        return {
            "kind": "logical_stream.abort_all.settled",
            "contract": LOGICAL_STREAM_CONTRACT,
            "aborted": len(transfer_ids),
        }

    def release_artifact(self, *, transfer_id: str) -> dict[str, Any]:
        transfer_id = _identifier(transfer_id, "logical_stream_transfer_id_invalid")
        with self._lock:
            artifact = self._artifacts.get(transfer_id)
            if artifact is None:
                _reject("logical_stream_artifact_not_active")
        try:
            artifact.path.unlink(missing_ok=False)
        except OSError as exc:
            raise NativeLogicalStreamError(
                "logical_stream_cleanup_failed",
                details={
                    "transfer_id": transfer_id,
                    "cause": "artifact_release",
                    "phases": ("unlink",),
                },
            ) from exc
        with self._lock:
            self._artifacts.pop(transfer_id, None)
            self._spool_bytes -= artifact.total_length
        return {
            "kind": "logical_stream.artifact.released",
            "contract": LOGICAL_STREAM_CONTRACT,
            "transfer_id": transfer_id,
        }

    def open(self, message: dict[str, Any]) -> dict[str, Any]:
        if type(message) is not dict:
            _reject("logical_stream_open_invalid")
        if frozenset(message) != _OPEN_KEYS:
            _reject("logical_stream_open_shape_invalid")
        if message.get("contract") != LOGICAL_STREAM_CONTRACT:
            _reject("logical_stream_contract_mismatch")
        transfer_id = _identifier(
            message.get("transfer_id"), "logical_stream_transfer_id_invalid"
        )
        with self._lock:
            if transfer_id in self._retained_ids:
                _reject("logical_stream_transfer_id_reused")
            if len(self._active) >= self._max_active_transfers:
                _reject("logical_stream_active_limit")
            if len(self._retained_ids) >= self._max_retained_transfer_ids:
                _reject("logical_stream_retained_limit")
        purpose = _identifier(message.get("purpose"), "logical_stream_purpose_invalid")
        if purpose not in self._allowed_purposes:
            _reject("logical_stream_purpose_not_allowed")
        media_type = message.get("media_type")
        if not isinstance(media_type, str):
            _reject("logical_stream_media_type_invalid")
        try:
            media_type_bytes = media_type.encode("utf-8", errors="strict")
        except UnicodeError:
            _reject("logical_stream_media_type_invalid")
        if not 1 <= len(media_type_bytes) <= 256:
            _reject("logical_stream_media_type_invalid")
        if message.get("content_encoding") != "identity":
            _reject("logical_stream_encoding_unsupported")
        maximum_bytes = _integer(
            message.get("maximum_bytes"),
            minimum=1,
            maximum=self._maximum_bytes,
            code="logical_stream_maximum_exceeded",
        )
        declared_length = message.get("declared_length")
        if declared_length is not None:
            declared_length = _integer(
                declared_length,
                minimum=0,
                maximum=maximum_bytes,
                code="logical_stream_declared_length_invalid",
            )
        declared_sha256 = _optional_sha256(
            message.get("declared_sha256"), "logical_stream_declared_sha256_invalid"
        )
        chunk_bytes = _integer(
            message.get("chunk_bytes"),
            minimum=self._minimum_chunk_bytes,
            maximum=self._maximum_chunk_bytes,
            code="logical_stream_chunk_size_invalid",
        )
        metadata, metadata_json = _metadata(message.get("metadata"))
        if not metadata.keys() <= self._metadata_keys_by_purpose[purpose]:
            _reject("logical_stream_metadata_key_not_allowed")
        credit_window = min(self._credit_window_bytes, maximum_bytes)
        partial_path = self._spool_root / f"{uuid.uuid4().hex}.part"
        try:
            handle = partial_path.open("xb")
        except OSError:
            _reject("logical_stream_spool_create_failed")
        state = _ReceiveState(
            transfer_id=transfer_id,
            purpose=purpose,
            media_type=media_type,
            maximum_bytes=maximum_bytes,
            declared_length=declared_length,
            declared_sha256=declared_sha256,
            chunk_bytes=chunk_bytes,
            credit_window_bytes=credit_window,
            metadata_json=metadata_json,
            partial_path=partial_path,
            handle=handle,
            digest=hashlib.sha256(),
        )
        with self._lock:
            # Recheck after the file create in case another thread admitted a
            # transfer while this call was validating metadata.
            if (
                transfer_id in self._retained_ids
                or len(self._active) >= self._max_active_transfers
                or len(self._retained_ids) >= self._max_retained_transfer_ids
            ):
                cleanup_failures: list[str] = []
                try:
                    handle.close()
                except OSError:
                    cleanup_failures.append("close")
                try:
                    partial_path.unlink(missing_ok=False)
                except OSError:
                    cleanup_failures.append("unlink")
                if cleanup_failures:
                    raise NativeLogicalStreamError(
                        "logical_stream_cleanup_failed",
                        details={
                            "transfer_id": transfer_id,
                            "cause": "open_race",
                            "phases": tuple(cleanup_failures),
                        },
                    )
                if transfer_id in self._retained_ids:
                    _reject("logical_stream_transfer_id_reused")
                if len(self._active) >= self._max_active_transfers:
                    _reject("logical_stream_active_limit")
                _reject("logical_stream_retained_limit")
            self._active[transfer_id] = state
            self._retained_ids.add(transfer_id)
        return {
            "kind": "logical_stream.accept",
            "contract": LOGICAL_STREAM_CONTRACT,
            "transfer_id": transfer_id,
            "accepted_through_seq": 1,
            "grant_bytes": credit_window,
            "production_owner": PRODUCTION_OWNER,
        }

    def append_chunk(
        self, *, transfer_id: str, stream_seq: int, payload: Any
    ) -> dict[str, Any]:
        transfer_id = _identifier(transfer_id, "logical_stream_transfer_id_invalid")
        state = self._claim(transfer_id)
        try:
            if type(stream_seq) is not int or stream_seq != state.next_stream_seq:
                _reject("logical_stream_sequence_mismatch")
            raw = _owned_bytes(payload, maximum=state.chunk_bytes)
            if state.short_chunk_seen:
                _reject("logical_stream_chunk_after_final")
            if len(raw) > state.credit_window_bytes:
                _reject("logical_stream_credit_exhausted")
            if state.total_length + len(raw) > state.maximum_bytes:
                _reject("logical_stream_length_exceeded")
            if state.declared_length is not None:
                remaining = state.declared_length - state.total_length
                if remaining <= 0:
                    _reject("logical_stream_declared_length_exceeded")
                expected = min(state.chunk_bytes, remaining)
                if len(raw) != expected:
                    _reject("logical_stream_declared_chunk_size_mismatch")
            maximum_chunks = (
                state.maximum_bytes + state.chunk_bytes - 1
            ) // state.chunk_bytes
            if state.chunk_count + 1 > maximum_chunks:
                _reject("logical_stream_chunk_count_exceeded")
            with self._lock:
                if self._spool_bytes + len(raw) > self._aggregate_spool_limit:
                    _reject("logical_stream_aggregate_spool_limit")
                self._spool_bytes += len(raw)
                state.spool_bytes += len(raw)
            written = state.handle.write(raw)
            if written != len(raw):
                _reject("logical_stream_spool_short_write")
            state.handle.flush()
            os.fsync(state.handle.fileno())
            state.digest.update(raw)
            state.total_length += len(raw)
            state.chunk_count += 1
            if len(raw) < state.chunk_bytes:
                state.short_chunk_seen = True
            state.next_stream_seq += 1
            return {
                "kind": "logical_stream.credit",
                "contract": LOGICAL_STREAM_CONTRACT,
                "transfer_id": transfer_id,
                "accepted_through_seq": stream_seq,
                "grant_bytes": len(raw),
            }
        except NativeLogicalStreamError as error:
            self._abort(transfer_id, cause=error.code)
            raise
        except OSError:
            self._abort(transfer_id, cause="logical_stream_spool_write_failed")
            _reject("logical_stream_spool_write_failed")
        finally:
            self._release_claim(state)

    def commit(
        self, *, transfer_id: str, stream_seq: int, message: dict[str, Any]
    ) -> LogicalStreamArtifact:
        transfer_id = _identifier(transfer_id, "logical_stream_transfer_id_invalid")
        state = self._claim(transfer_id)
        try:
            if type(stream_seq) is not int or stream_seq != state.next_stream_seq:
                _reject("logical_stream_sequence_mismatch")
            if type(message) is not dict or frozenset(message) != _COMMIT_KEYS:
                _reject("logical_stream_commit_shape_invalid")
            if message.get("transfer_id") != transfer_id:
                _reject("logical_stream_commit_invalid")
            if message.get("terminal") is not True:
                _reject("logical_stream_commit_not_terminal")
            chunk_count = _integer(
                message.get("chunk_count"),
                minimum=0,
                maximum=state.next_stream_seq,
                code="logical_stream_chunk_count_invalid",
            )
            total_length = _integer(
                message.get("total_length"),
                minimum=0,
                maximum=state.maximum_bytes,
                code="logical_stream_total_length_invalid",
            )
            sha256 = _optional_sha256(
                message.get("sha256"), "logical_stream_sha256_invalid"
            )
            if sha256 is None:
                _reject("logical_stream_sha256_invalid")
            actual_sha256 = state.digest.hexdigest()
            if chunk_count != state.chunk_count:
                _reject("logical_stream_chunk_count_mismatch")
            if total_length != state.total_length:
                _reject("logical_stream_total_length_mismatch")
            if sha256 != actual_sha256:
                _reject("logical_stream_sha256_mismatch")
            if (
                state.declared_length is not None
                and total_length != state.declared_length
            ):
                _reject("logical_stream_declared_length_mismatch")
            if state.declared_sha256 is not None and sha256 != state.declared_sha256:
                _reject("logical_stream_declared_sha256_mismatch")
            state.handle.flush()
            os.fsync(state.handle.fileno())
            state.handle.close()
            ready_path = state.partial_path.with_suffix(".ready")
            os.replace(state.partial_path, ready_path)
            artifact = LogicalStreamArtifact(
                transfer_id=transfer_id,
                path=ready_path,
                total_length=total_length,
                sha256=sha256,
                chunk_count=chunk_count,
                purpose=state.purpose,
                media_type=state.media_type,
                metadata_json=state.metadata_json,
            )
            with self._lock:
                self._active.pop(transfer_id, None)
                self._artifacts[transfer_id] = artifact
            return artifact
        except NativeLogicalStreamError as error:
            self._abort(transfer_id, cause=error.code)
            raise
        except OSError:
            self._abort(transfer_id, cause="logical_stream_spool_commit_failed")
            _reject("logical_stream_spool_commit_failed")
        finally:
            self._release_claim(state)

    def cancel(self, *, transfer_id: str, reason: str = "cancelled") -> dict[str, Any]:
        transfer_id = _identifier(transfer_id, "logical_stream_transfer_id_invalid")
        _identifier(reason, "logical_stream_cancel_reason_invalid")
        state = self._claim(transfer_id)
        try:
            self._abort(transfer_id, cause=reason)
        finally:
            self._release_claim(state)
        return {
            "kind": "logical_stream.cancelled",
            "contract": LOGICAL_STREAM_CONTRACT,
            "transfer_id": transfer_id,
            "reason": reason,
        }
