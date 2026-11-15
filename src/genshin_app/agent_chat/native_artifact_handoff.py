"""Qualification-only ownership handoff for committed logical-stream artifacts.

The logical byte-stream receiver owns producer-side spooling.  DSH owns its
attachment store and prompt receipt lifecycle.  This module deliberately owns
only the narrow boundary between those systems: one App artifact may be
claimed by one DSH upload operation, and the App source is removed only after a
durable downstream receipt is reported.

It is not a production owner and it does not implement, emulate, or persist a
DSH Session receipt.  Production wiring must add a durable journal and choose
App-owned capacity limits from observed product high-water telemetry.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .native_logical_stream import LogicalStreamArtifact

ARTIFACT_HANDOFF_CONTRACT = "genshin-native-artifact-handoff-v1"
DSH_FILE_UPLOAD_CONSUMER = "dsh_file_upload"
PRODUCTION_OWNER = False

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STATES = frozenset(
    {
        "ready",
        "leased",
        "streaming",
        "downstream_committed",
        "source_released",
        "quarantined_unknown",
    }
)


class NativeArtifactHandoffError(RuntimeError):
    """Sanitized handoff failure; host paths and downstream payloads are omitted."""

    def __init__(self, code: str, *, details: dict[str, Any] | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def _reject(code: str, *, details: dict[str, Any] | None = None) -> None:
    raise NativeArtifactHandoffError(code, details=details)


def _identifier(value: Any, code: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        _reject(code)
    return value


def _sha256(value: Any, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _reject(code)
    return value


def _length(value: Any, code: str) -> int:
    if type(value) is not int or value < 0:
        _reject(code)
    return value


@dataclass(frozen=True, slots=True)
class CommittedArtifactRef:
    """Path-free identity exposed to a downstream handoff adapter."""

    artifact_id: str
    source_transfer_id: str
    length: int
    sha256: str
    purpose: str
    media_type: str
    runtime_generation: str
    storage_generation: str


@dataclass(frozen=True, slots=True)
class ConsumerLease:
    lease_id: str
    artifact_id: str
    consumer_kind: str
    session_id: str
    operation_id: str
    runtime_generation: str
    state: str
    delivery_started: bool


@dataclass(slots=True)
class _ArtifactRecord:
    ref: CommittedArtifactRef
    path: Path
    file_identity: tuple[int, int, int, int]
    state: str
    created_at: float
    state_changed_at: float
    lease_id: str | None = None
    session_id: str | None = None
    operation_id: str | None = None
    delivery_started: bool = False
    reader_active: bool = False
    payload_verified: bool = False
    downstream_receipt_id: str | None = None
    final_receipt: dict[str, Any] | None = None


def _file_identity(path: Path) -> tuple[int, int, int, int]:
    try:
        if path.is_symlink():
            _reject("artifact_handoff_source_not_regular")
        metadata = path.stat()
    except OSError:
        _reject("artifact_handoff_source_unavailable")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        _reject("artifact_handoff_source_not_regular")
    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
    )


class ArtifactHandoffRegistry:
    """In-memory qualification registry for App-spool to DSH upload handoff.

    No production capacity default is hidden here.  The producer spool already
    enforces its qualification caps; this registry records current/peak use so
    a later production adapter can derive App-owned limits from real tasks.
    """

    def __init__(
        self,
        *,
        spool_root: Path,
        clock: Callable[[], float] = time.monotonic,
    ):
        root = Path(spool_root)
        if not root.is_dir() or root.is_symlink():
            _reject("artifact_handoff_spool_root_invalid")
        try:
            self._spool_root = root.resolve(strict=True)
        except OSError:
            _reject("artifact_handoff_spool_root_invalid")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
        self._records: dict[str, _ArtifactRecord] = {}
        self._leases: dict[str, str] = {}
        self._lease_contexts: dict[str, tuple[str, str, str, str]] = {}
        self._lease_settlements: dict[str, dict[str, Any]] = {}
        self._operation_leases: dict[tuple[str, str, str, str], str] = {}
        self._lock = threading.RLock()
        self._peaks = {
            "ready_count": 0,
            "ready_bytes": 0,
            "consumer_lease_count": 0,
            "consumer_lease_bytes": 0,
            "quarantine_count": 0,
            "quarantine_bytes": 0,
            "cleanup_pending_count": 0,
            "cleanup_pending_bytes": 0,
        }
        self._outcomes = {
            "registered": 0,
            "claimed": 0,
            "source_released": 0,
            "known_not_committed": 0,
            "outcome_unknown": 0,
            "cleanup_failed": 0,
        }

    def _public_ref(self, ref: CommittedArtifactRef) -> dict[str, Any]:
        return {
            "contract": ARTIFACT_HANDOFF_CONTRACT,
            "artifact_id": ref.artifact_id,
            "source_transfer_id": ref.source_transfer_id,
            "length": ref.length,
            "sha256": ref.sha256,
            "purpose": ref.purpose,
            "media_type": ref.media_type,
            "runtime_generation": ref.runtime_generation,
            "storage_generation": ref.storage_generation,
            "production_owner": PRODUCTION_OWNER,
        }

    def _snapshot_locked(self) -> dict[str, int | float | None]:
        now = self._clock()
        counts = {state: 0 for state in _STATES}
        sizes = {state: 0 for state in _STATES}
        oldest: dict[str, float | None] = {state: None for state in _STATES}
        for record in self._records.values():
            counts[record.state] += 1
            sizes[record.state] += record.ref.length
            age = max(0.0, now - record.state_changed_at)
            current = oldest[record.state]
            oldest[record.state] = age if current is None else max(current, age)
        current = {
            "ready_count": counts["ready"],
            "ready_bytes": sizes["ready"],
            "consumer_lease_count": counts["leased"] + counts["streaming"],
            "consumer_lease_bytes": sizes["leased"] + sizes["streaming"],
            "quarantine_count": counts["quarantined_unknown"],
            "quarantine_bytes": sizes["quarantined_unknown"],
            "cleanup_pending_count": counts["downstream_committed"],
            "cleanup_pending_bytes": sizes["downstream_committed"],
        }
        for key, value in current.items():
            self._peaks[key] = max(self._peaks[key], value)
        return {
            **current,
            "oldest_ready_seconds": oldest["ready"],
            "oldest_lease_seconds": max(
                value
                for value in (oldest["leased"], oldest["streaming"], 0.0)
                if value is not None
            )
            if counts["leased"] + counts["streaming"]
            else None,
            "oldest_quarantine_seconds": oldest["quarantined_unknown"],
        }

    def _update_peaks_locked(self) -> None:
        self._snapshot_locked()

    def register(
        self,
        artifact: LogicalStreamArtifact,
        *,
        runtime_generation: str,
        storage_generation: str,
        artifact_id: str | None = None,
    ) -> CommittedArtifactRef:
        runtime_generation = _identifier(
            runtime_generation, "artifact_handoff_runtime_generation_invalid"
        )
        storage_generation = _identifier(
            storage_generation, "artifact_handoff_storage_generation_invalid"
        )
        source_transfer_id = _identifier(
            artifact.transfer_id, "artifact_handoff_source_transfer_id_invalid"
        )
        digest = _sha256(artifact.sha256, "artifact_handoff_sha256_invalid")
        total_length = _length(
            artifact.total_length, "artifact_handoff_length_invalid"
        )
        purpose = _identifier(artifact.purpose, "artifact_handoff_purpose_invalid")
        if not isinstance(artifact.media_type, str) or not artifact.media_type:
            _reject("artifact_handoff_media_type_invalid")
        path = Path(artifact.path)
        if path.is_symlink():
            _reject("artifact_handoff_source_not_regular")
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            _reject("artifact_handoff_source_unavailable")
        if resolved.parent != self._spool_root or resolved.suffix != ".ready":
            _reject("artifact_handoff_source_outside_spool")
        identity = _file_identity(resolved)
        if identity[2] != total_length:
            _reject("artifact_handoff_length_mismatch")
        identifier = (
            _identifier(artifact_id, "artifact_handoff_artifact_id_invalid")
            if artifact_id is not None
            else uuid.uuid4().hex
        )
        ref = CommittedArtifactRef(
            artifact_id=identifier,
            source_transfer_id=source_transfer_id,
            length=total_length,
            sha256=digest,
            purpose=purpose,
            media_type=artifact.media_type,
            runtime_generation=runtime_generation,
            storage_generation=storage_generation,
        )
        now = self._clock()
        with self._lock:
            if identifier in self._records:
                _reject("artifact_handoff_artifact_id_reused")
            if any(
                record.ref.source_transfer_id == source_transfer_id
                for record in self._records.values()
            ):
                _reject("artifact_handoff_source_already_registered")
            self._records[identifier] = _ArtifactRecord(
                ref=ref,
                path=resolved,
                file_identity=identity,
                state="ready",
                created_at=now,
                state_changed_at=now,
            )
            self._outcomes["registered"] += 1
            self._update_peaks_locked()
        return ref

    def describe(self, *, artifact_id: str) -> dict[str, Any]:
        artifact_id = _identifier(
            artifact_id, "artifact_handoff_artifact_id_invalid"
        )
        with self._lock:
            record = self._records.get(artifact_id)
            if record is None:
                _reject("artifact_handoff_artifact_not_found")
            return {**self._public_ref(record.ref), "state": record.state}

    def _lease_locked(self, lease_id: str) -> _ArtifactRecord:
        artifact_id = self._leases.get(lease_id)
        if artifact_id is None:
            _reject("artifact_handoff_lease_not_found")
        record = self._records[artifact_id]
        if record.lease_id != lease_id:
            _reject("artifact_handoff_lease_not_found")
        return record

    @staticmethod
    def _lease_value(record: _ArtifactRecord) -> ConsumerLease:
        if (
            record.lease_id is None
            or record.session_id is None
            or record.operation_id is None
        ):
            _reject("artifact_handoff_lease_not_found")
        return ConsumerLease(
            lease_id=record.lease_id,
            artifact_id=record.ref.artifact_id,
            consumer_kind=DSH_FILE_UPLOAD_CONSUMER,
            session_id=record.session_id,
            operation_id=record.operation_id,
            runtime_generation=record.ref.runtime_generation,
            state=record.state,
            delivery_started=record.delivery_started,
        )

    def claim(
        self,
        *,
        artifact_id: str,
        session_id: str,
        operation_id: str,
        runtime_generation: str,
        storage_generation: str,
        expected_length: int,
        expected_sha256: str,
    ) -> ConsumerLease:
        artifact_id = _identifier(
            artifact_id, "artifact_handoff_artifact_id_invalid"
        )
        session_id = _identifier(session_id, "artifact_handoff_session_id_invalid")
        operation_id = _identifier(
            operation_id, "artifact_handoff_operation_id_invalid"
        )
        runtime_generation = _identifier(
            runtime_generation, "artifact_handoff_runtime_generation_invalid"
        )
        storage_generation = _identifier(
            storage_generation, "artifact_handoff_storage_generation_invalid"
        )
        expected_length = _length(
            expected_length, "artifact_handoff_expected_length_invalid"
        )
        expected_sha256 = _sha256(
            expected_sha256, "artifact_handoff_expected_sha256_invalid"
        )
        operation_key = (
            artifact_id,
            session_id,
            operation_id,
            runtime_generation,
        )
        with self._lock:
            record = self._records.get(artifact_id)
            if record is None:
                _reject("artifact_handoff_artifact_not_found")
            existing_lease_id = self._operation_leases.get(operation_key)
            if existing_lease_id is not None:
                if existing_lease_id in self._lease_settlements:
                    _reject("artifact_handoff_operation_already_settled")
                existing = self._lease_locked(existing_lease_id)
                return self._lease_value(existing)
            if record.state != "ready":
                _reject("artifact_handoff_artifact_not_ready")
            if record.ref.runtime_generation != runtime_generation:
                _reject("artifact_handoff_runtime_generation_mismatch")
            if record.ref.storage_generation != storage_generation:
                _reject("artifact_handoff_storage_generation_mismatch")
            if record.ref.length != expected_length:
                _reject("artifact_handoff_length_mismatch")
            if record.ref.sha256 != expected_sha256:
                _reject("artifact_handoff_sha256_mismatch")
            if _file_identity(record.path) != record.file_identity:
                _reject("artifact_handoff_source_identity_changed")
            lease_id = uuid.uuid4().hex
            record.lease_id = lease_id
            record.session_id = session_id
            record.operation_id = operation_id
            record.state = "leased"
            record.delivery_started = False
            record.reader_active = False
            record.payload_verified = False
            record.downstream_receipt_id = None
            record.final_receipt = None
            record.state_changed_at = self._clock()
            self._leases[lease_id] = artifact_id
            self._lease_contexts[lease_id] = operation_key
            self._operation_leases[operation_key] = lease_id
            self._outcomes["claimed"] += 1
            self._update_peaks_locked()
            return self._lease_value(record)

    def _validate_lease_context_locked(
        self,
        *,
        lease_id: str,
        session_id: str,
        operation_id: str,
        runtime_generation: str,
    ) -> _ArtifactRecord:
        expected = self._lease_contexts.get(lease_id)
        if expected != (
            self._leases.get(lease_id),
            session_id,
            operation_id,
            runtime_generation,
        ):
            _reject("artifact_handoff_lease_context_mismatch")
        record = self._lease_locked(lease_id)
        return record

    def start_delivery(
        self,
        *,
        lease_id: str,
        session_id: str,
        operation_id: str,
        runtime_generation: str,
    ) -> ConsumerLease:
        lease_id = _identifier(lease_id, "artifact_handoff_lease_id_invalid")
        session_id = _identifier(session_id, "artifact_handoff_session_id_invalid")
        operation_id = _identifier(
            operation_id, "artifact_handoff_operation_id_invalid"
        )
        runtime_generation = _identifier(
            runtime_generation, "artifact_handoff_runtime_generation_invalid"
        )
        with self._lock:
            record = self._validate_lease_context_locked(
                lease_id=lease_id,
                session_id=session_id,
                operation_id=operation_id,
                runtime_generation=runtime_generation,
            )
            if record.state == "streaming":
                return self._lease_value(record)
            if record.state != "leased":
                _reject("artifact_handoff_lease_not_startable")
            if _file_identity(record.path) != record.file_identity:
                _reject("artifact_handoff_source_identity_changed")
            record.state = "streaming"
            record.delivery_started = True
            record.state_changed_at = self._clock()
            self._update_peaks_locked()
            return self._lease_value(record)

    def payload_chunks(
        self,
        *,
        lease_id: str,
        session_id: str,
        operation_id: str,
        runtime_generation: str,
        chunk_bytes: int,
    ) -> Iterator[bytes]:
        """Read a leased source without exposing its host path.

        The caller must call :meth:`start_delivery` immediately before the
        first chunk is written to the downstream DSH upload stream.
        """

        lease_id = _identifier(lease_id, "artifact_handoff_lease_id_invalid")
        session_id = _identifier(session_id, "artifact_handoff_session_id_invalid")
        operation_id = _identifier(
            operation_id, "artifact_handoff_operation_id_invalid"
        )
        runtime_generation = _identifier(
            runtime_generation, "artifact_handoff_runtime_generation_invalid"
        )
        if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 8 * 1024 * 1024:
            _reject("artifact_handoff_chunk_bytes_invalid")
        with self._lock:
            record = self._validate_lease_context_locked(
                lease_id=lease_id,
                session_id=session_id,
                operation_id=operation_id,
                runtime_generation=runtime_generation,
            )
            if record.state not in {"leased", "streaming"}:
                _reject("artifact_handoff_lease_not_readable")
            path = record.path
            identity = record.file_identity
            expected_length = record.ref.length
            expected_sha256 = record.ref.sha256

        def generate() -> Iterator[bytes]:
            digest = hashlib.sha256()
            total = 0
            with self._lock:
                active = self._validate_lease_context_locked(
                    lease_id=lease_id,
                    session_id=session_id,
                    operation_id=operation_id,
                    runtime_generation=runtime_generation,
                )
                if active.state != "streaming" or not active.delivery_started:
                    _reject("artifact_handoff_lease_not_streaming")
                if active.reader_active or active.payload_verified:
                    _reject("artifact_handoff_reader_unavailable")
                active.reader_active = True
            try:
                with path.open("rb") as handle:
                    metadata = os.fstat(handle.fileno())
                    current_identity = (
                        int(metadata.st_dev),
                        int(metadata.st_ino),
                        int(metadata.st_size),
                        int(metadata.st_mtime_ns),
                    )
                    if current_identity != identity:
                        _reject("artifact_handoff_source_identity_changed")
                    while True:
                        chunk = handle.read(chunk_bytes)
                        if not chunk:
                            break
                        digest.update(chunk)
                        total += len(chunk)
                        yield chunk
                if total != expected_length:
                    _reject("artifact_handoff_length_mismatch")
                if digest.hexdigest() != expected_sha256:
                    _reject("artifact_handoff_sha256_mismatch")
                with self._lock:
                    active = self._validate_lease_context_locked(
                        lease_id=lease_id,
                        session_id=session_id,
                        operation_id=operation_id,
                        runtime_generation=runtime_generation,
                    )
                    if active.state != "streaming":
                        _reject("artifact_handoff_lease_not_streaming")
                    active.payload_verified = True
            except OSError:
                _reject("artifact_handoff_source_read_failed")
            finally:
                with self._lock:
                    active = self._validate_lease_context_locked(
                        lease_id=lease_id,
                        session_id=session_id,
                        operation_id=operation_id,
                        runtime_generation=runtime_generation,
                    )
                    active.reader_active = False

        return generate()

    def settle_not_committed(
        self,
        *,
        lease_id: str,
        session_id: str,
        operation_id: str,
        runtime_generation: str,
        reason: str,
    ) -> dict[str, Any]:
        return self._settle_failure(
            lease_id=lease_id,
            session_id=session_id,
            operation_id=operation_id,
            runtime_generation=runtime_generation,
            reason=reason,
            outcome="known_not_committed",
        )

    def settle_unknown(
        self,
        *,
        lease_id: str,
        session_id: str,
        operation_id: str,
        runtime_generation: str,
        reason: str,
    ) -> dict[str, Any]:
        return self._settle_failure(
            lease_id=lease_id,
            session_id=session_id,
            operation_id=operation_id,
            runtime_generation=runtime_generation,
            reason=reason,
            outcome="outcome_unknown",
        )

    def _settle_failure(
        self,
        *,
        lease_id: str,
        session_id: str,
        operation_id: str,
        runtime_generation: str,
        reason: str,
        outcome: str,
    ) -> dict[str, Any]:
        lease_id = _identifier(lease_id, "artifact_handoff_lease_id_invalid")
        session_id = _identifier(session_id, "artifact_handoff_session_id_invalid")
        operation_id = _identifier(
            operation_id, "artifact_handoff_operation_id_invalid"
        )
        runtime_generation = _identifier(
            runtime_generation, "artifact_handoff_runtime_generation_invalid"
        )
        _identifier(reason, "artifact_handoff_failure_reason_invalid")
        with self._lock:
            settled = self._lease_settlements.get(lease_id)
            if settled is not None:
                expected = self._lease_contexts.get(lease_id)
                if expected != (
                    self._leases.get(lease_id),
                    session_id,
                    operation_id,
                    runtime_generation,
                ):
                    _reject("artifact_handoff_lease_context_mismatch")
                if settled.get("kind") != f"artifact_handoff.{outcome}":
                    _reject("artifact_handoff_settlement_mismatch")
                return dict(settled)
            record = self._validate_lease_context_locked(
                lease_id=lease_id,
                session_id=session_id,
                operation_id=operation_id,
                runtime_generation=runtime_generation,
            )
            if record.final_receipt is not None:
                return dict(record.final_receipt)
            if record.state not in {"leased", "streaming"}:
                _reject("artifact_handoff_lease_not_settleable")
            if record.reader_active:
                _reject("artifact_handoff_reader_active")
            if outcome == "outcome_unknown" and not record.delivery_started:
                _reject("artifact_handoff_unknown_before_delivery")
            if outcome == "known_not_committed":
                record.state = "ready"
            else:
                record.state = "quarantined_unknown"
            record.state_changed_at = self._clock()
            receipt = {
                "kind": f"artifact_handoff.{outcome}",
                "contract": ARTIFACT_HANDOFF_CONTRACT,
                "artifact_id": record.ref.artifact_id,
                "lease_id": lease_id,
                "production_owner": PRODUCTION_OWNER,
            }
            record.final_receipt = receipt
            self._lease_settlements[lease_id] = receipt
            self._outcomes[outcome] += 1
            self._update_peaks_locked()
            return dict(receipt)

    def settle_success(
        self,
        *,
        lease_id: str,
        session_id: str,
        operation_id: str,
        runtime_generation: str,
        downstream_receipt_id: str,
    ) -> dict[str, Any]:
        lease_id = _identifier(lease_id, "artifact_handoff_lease_id_invalid")
        session_id = _identifier(session_id, "artifact_handoff_session_id_invalid")
        operation_id = _identifier(
            operation_id, "artifact_handoff_operation_id_invalid"
        )
        runtime_generation = _identifier(
            runtime_generation, "artifact_handoff_runtime_generation_invalid"
        )
        downstream_receipt_id = _identifier(
            downstream_receipt_id,
            "artifact_handoff_downstream_receipt_id_invalid",
        )
        with self._lock:
            settled = self._lease_settlements.get(lease_id)
            if settled is not None:
                expected = self._lease_contexts.get(lease_id)
                if expected != (
                    self._leases.get(lease_id),
                    session_id,
                    operation_id,
                    runtime_generation,
                ):
                    _reject("artifact_handoff_lease_context_mismatch")
                if settled.get("downstream_receipt_id") != downstream_receipt_id:
                    _reject("artifact_handoff_downstream_receipt_mismatch")
                return dict(settled)
            record = self._validate_lease_context_locked(
                lease_id=lease_id,
                session_id=session_id,
                operation_id=operation_id,
                runtime_generation=runtime_generation,
            )
            if record.state not in {"streaming", "downstream_committed"}:
                _reject("artifact_handoff_lease_not_committable")
            if not record.delivery_started:
                _reject("artifact_handoff_delivery_not_started")
            if record.reader_active:
                _reject("artifact_handoff_reader_active")
            if not record.payload_verified:
                _reject("artifact_handoff_payload_not_verified")
            if (
                record.downstream_receipt_id is not None
                and record.downstream_receipt_id != downstream_receipt_id
            ):
                _reject("artifact_handoff_downstream_receipt_mismatch")
            record.downstream_receipt_id = downstream_receipt_id
            record.state = "downstream_committed"
            record.state_changed_at = self._clock()
            path = record.path
            release_failed = False
            try:
                path.unlink(missing_ok=False)
            except OSError:
                self._outcomes["cleanup_failed"] += 1
                self._update_peaks_locked()
                release_failed = True
            if release_failed:
                raise NativeArtifactHandoffError(
                    "artifact_handoff_source_release_failed",
                    details={
                        "artifact_id": record.ref.artifact_id,
                        "lease_id": lease_id,
                    },
                ) from None
            record.state = "source_released"
            record.state_changed_at = self._clock()
            receipt = {
                "kind": "artifact_handoff.source_released",
                "contract": ARTIFACT_HANDOFF_CONTRACT,
                "artifact_id": record.ref.artifact_id,
                "lease_id": lease_id,
                "downstream_receipt_id": downstream_receipt_id,
                "production_owner": PRODUCTION_OWNER,
            }
            record.final_receipt = receipt
            self._lease_settlements[lease_id] = receipt
            self._outcomes["source_released"] += 1
            self._update_peaks_locked()
            return dict(receipt)

    def telemetry(self) -> dict[str, Any]:
        with self._lock:
            return {
                "contract": ARTIFACT_HANDOFF_CONTRACT,
                "production_owner": PRODUCTION_OWNER,
                "capacity_source": "qualification_telemetry_only",
                "current": self._snapshot_locked(),
                "peak": dict(self._peaks),
                "outcomes": dict(self._outcomes),
            }
