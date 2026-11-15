from __future__ import annotations

import hashlib
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_artifact_handoff import (
    ARTIFACT_HANDOFF_CONTRACT,
    DSH_FILE_UPLOAD_CONSUMER,
    PRODUCTION_OWNER,
    ArtifactHandoffRegistry,
    NativeArtifactHandoffError,
)
from genshin_app.agent_chat.native_logical_stream import LogicalStreamArtifact

_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def _spool_root():
    parent = (_ROOT / "build-py").resolve()
    parent.mkdir(parents=True, exist_ok=True)
    root = (parent / f"artifact-handoff-{uuid.uuid4().hex}").resolve()
    if root.parent != parent:
        raise AssertionError("artifact_handoff_test_root_escape")
    root.mkdir()
    try:
        yield root
    finally:
        resolved = root.resolve()
        if resolved.parent != parent:
            raise AssertionError("artifact_handoff_test_cleanup_escape")
        shutil.rmtree(resolved, ignore_errors=False)


def _artifact(root: Path, *, transfer_id: str = "transfer-1", body: bytes = b"abc"):
    path = root / f"{uuid.uuid4().hex}.ready"
    path.write_bytes(body)
    return LogicalStreamArtifact(
        transfer_id=transfer_id,
        path=path,
        total_length=len(body),
        sha256=hashlib.sha256(body).hexdigest(),
        chunk_count=1,
        purpose="dsh_upload_request",
        media_type="application/octet-stream",
        metadata_json="{}",
    )


def _error(code: str, operation) -> NativeArtifactHandoffError:
    with pytest.raises(NativeArtifactHandoffError) as captured:
        operation()
    assert captured.value.code == code
    return captured.value


def _claim(registry: ArtifactHandoffRegistry, ref, *, operation_id="operation-1"):
    return registry.claim(
        artifact_id=ref.artifact_id,
        session_id="session-1",
        operation_id=operation_id,
        runtime_generation=ref.runtime_generation,
        storage_generation=ref.storage_generation,
        expected_length=ref.length,
        expected_sha256=ref.sha256,
    )


def _start(registry: ArtifactHandoffRegistry, lease):
    return registry.start_delivery(
        lease_id=lease.lease_id,
        session_id=lease.session_id,
        operation_id=lease.operation_id,
        runtime_generation=lease.runtime_generation,
    )


def test_register_claim_stream_and_success_release_are_path_free_and_idempotent():
    with _spool_root() as root:
        source = _artifact(root, body=b"abcdef")
        registry = ArtifactHandoffRegistry(spool_root=root)
        ref = registry.register(
            source,
            runtime_generation="runtime-1",
            storage_generation="storage-1",
            artifact_id="artifact-1",
        )
        assert PRODUCTION_OWNER is False
        assert registry.describe(artifact_id=ref.artifact_id) == {
            "contract": ARTIFACT_HANDOFF_CONTRACT,
            "artifact_id": "artifact-1",
            "source_transfer_id": "transfer-1",
            "length": 6,
            "sha256": hashlib.sha256(b"abcdef").hexdigest(),
            "purpose": "dsh_upload_request",
            "media_type": "application/octet-stream",
            "runtime_generation": "runtime-1",
            "storage_generation": "storage-1",
            "production_owner": False,
            "state": "ready",
        }
        assert str(root) not in repr(registry.describe(artifact_id=ref.artifact_id))

        lease = _claim(registry, ref)
        assert lease.consumer_kind == DSH_FILE_UPLOAD_CONSUMER
        assert _claim(registry, ref) == lease
        _start(registry, lease)
        assert b"".join(
            registry.payload_chunks(
                lease_id=lease.lease_id,
                session_id=lease.session_id,
                operation_id=lease.operation_id,
                runtime_generation=lease.runtime_generation,
                chunk_bytes=2,
            )
        ) == b"abcdef"
        receipt = registry.settle_success(
            lease_id=lease.lease_id,
            session_id=lease.session_id,
            operation_id=lease.operation_id,
            runtime_generation=lease.runtime_generation,
            downstream_receipt_id="dsh-receipt-1",
        )
        assert receipt == {
            "kind": "artifact_handoff.source_released",
            "contract": ARTIFACT_HANDOFF_CONTRACT,
            "artifact_id": "artifact-1",
            "lease_id": lease.lease_id,
            "downstream_receipt_id": "dsh-receipt-1",
            "production_owner": False,
        }
        assert not source.path.exists()
        assert registry.settle_success(
            lease_id=lease.lease_id,
            session_id=lease.session_id,
            operation_id=lease.operation_id,
            runtime_generation=lease.runtime_generation,
            downstream_receipt_id="dsh-receipt-1",
        ) == receipt
        _error(
            "artifact_handoff_downstream_receipt_mismatch",
            lambda: registry.settle_success(
                lease_id=lease.lease_id,
                session_id=lease.session_id,
                operation_id=lease.operation_id,
                runtime_generation=lease.runtime_generation,
                downstream_receipt_id="dsh-receipt-other",
            ),
        )


def test_success_requires_one_complete_verified_reader_and_blocks_concurrent_settlement():
    with _spool_root() as root:
        source = _artifact(root, body=b"abcdef")
        registry = ArtifactHandoffRegistry(spool_root=root)
        ref = registry.register(
            source,
            runtime_generation="runtime-1",
            storage_generation="storage-1",
        )
        lease = _claim(registry, ref)
        _start(registry, lease)
        success = lambda: registry.settle_success(
            lease_id=lease.lease_id,
            session_id=lease.session_id,
            operation_id=lease.operation_id,
            runtime_generation=lease.runtime_generation,
            downstream_receipt_id="dsh-receipt-1",
        )
        _error("artifact_handoff_payload_not_verified", success)

        reader = registry.payload_chunks(
            lease_id=lease.lease_id,
            session_id=lease.session_id,
            operation_id=lease.operation_id,
            runtime_generation=lease.runtime_generation,
            chunk_bytes=2,
        )
        assert next(reader) == b"ab"
        _error("artifact_handoff_reader_active", success)
        _error(
            "artifact_handoff_reader_active",
            lambda: registry.settle_not_committed(
                lease_id=lease.lease_id,
                session_id=lease.session_id,
                operation_id=lease.operation_id,
                runtime_generation=lease.runtime_generation,
                reason="downstream_rejected",
            ),
        )
        reader.close()
        _error("artifact_handoff_payload_not_verified", success)

        assert b"".join(
            registry.payload_chunks(
                lease_id=lease.lease_id,
                session_id=lease.session_id,
                operation_id=lease.operation_id,
                runtime_generation=lease.runtime_generation,
                chunk_bytes=2,
            )
        ) == b"abcdef"
        assert success()["kind"] == "artifact_handoff.source_released"


def test_source_release_failure_does_not_retain_host_path_in_exception_graph(monkeypatch):
    with _spool_root() as root:
        source = _artifact(root, body=b"abcdef")
        registry = ArtifactHandoffRegistry(spool_root=root)
        ref = registry.register(
            source,
            runtime_generation="runtime-1",
            storage_generation="storage-1",
        )
        lease = _claim(registry, ref)
        _start(registry, lease)
        assert b"".join(
            registry.payload_chunks(
                lease_id=lease.lease_id,
                session_id=lease.session_id,
                operation_id=lease.operation_id,
                runtime_generation=lease.runtime_generation,
                chunk_bytes=2,
            )
        ) == b"abcdef"

        original_unlink = Path.unlink

        def fail_unlink(path, *args, **kwargs):
            if path == source.path:
                raise OSError(f"private path: {path}")
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", fail_unlink)
        error = _error(
            "artifact_handoff_source_release_failed",
            lambda: registry.settle_success(
                lease_id=lease.lease_id,
                session_id=lease.session_id,
                operation_id=lease.operation_id,
                runtime_generation=lease.runtime_generation,
                downstream_receipt_id="dsh-receipt-1",
            ),
        )
        seen = set()
        pending = [error]
        rendered = []
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            rendered.append(f"{current!s}\n{current!r}\n{vars(current)!r}")
            pending.extend(
                item
                for item in (current.__cause__, current.__context__)
                if isinstance(item, BaseException)
            )
        assert str(root) not in "\n".join(rendered)


def test_claim_binds_all_identity_fields_and_only_one_consumer():
    with _spool_root() as root:
        source = _artifact(root)
        registry = ArtifactHandoffRegistry(spool_root=root)
        ref = registry.register(
            source,
            runtime_generation="runtime-1",
            storage_generation="storage-1",
        )
        cases = (
            (
                "artifact_handoff_runtime_generation_mismatch",
                {"runtime_generation": "runtime-other"},
            ),
            (
                "artifact_handoff_storage_generation_mismatch",
                {"storage_generation": "storage-other"},
            ),
            ("artifact_handoff_length_mismatch", {"expected_length": 4}),
            ("artifact_handoff_sha256_mismatch", {"expected_sha256": "0" * 64}),
        )
        base = {
            "artifact_id": ref.artifact_id,
            "session_id": "session-1",
            "operation_id": "operation-1",
            "runtime_generation": ref.runtime_generation,
            "storage_generation": ref.storage_generation,
            "expected_length": ref.length,
            "expected_sha256": ref.sha256,
        }
        for code, override in cases:
            _error(code, lambda override=override: registry.claim(**(base | override)))
        lease = registry.claim(**base)
        _error(
            "artifact_handoff_artifact_not_ready",
            lambda: registry.claim(**(base | {"operation_id": "operation-2"})),
        )
        _error(
            "artifact_handoff_lease_context_mismatch",
            lambda: registry.start_delivery(
                lease_id=lease.lease_id,
                session_id="session-other",
                operation_id=lease.operation_id,
                runtime_generation=lease.runtime_generation,
            ),
        )
        assert source.path.exists()


def test_known_failure_returns_ready_but_retry_requires_a_new_operation():
    with _spool_root() as root:
        source = _artifact(root)
        registry = ArtifactHandoffRegistry(spool_root=root)
        ref = registry.register(
            source,
            runtime_generation="runtime-1",
            storage_generation="storage-1",
        )
        first = _claim(registry, ref)
        receipt = registry.settle_not_committed(
            lease_id=first.lease_id,
            session_id=first.session_id,
            operation_id=first.operation_id,
            runtime_generation=first.runtime_generation,
            reason="downstream_rejected",
        )
        assert receipt["kind"] == "artifact_handoff.known_not_committed"
        assert registry.describe(artifact_id=ref.artifact_id)["state"] == "ready"
        assert source.path.exists()
        assert registry.settle_not_committed(
            lease_id=first.lease_id,
            session_id=first.session_id,
            operation_id=first.operation_id,
            runtime_generation=first.runtime_generation,
            reason="downstream_rejected",
        ) == receipt
        _error(
            "artifact_handoff_settlement_mismatch",
            lambda: registry.settle_unknown(
                lease_id=first.lease_id,
                session_id=first.session_id,
                operation_id=first.operation_id,
                runtime_generation=first.runtime_generation,
                reason="connection_lost",
            ),
        )
        _error(
            "artifact_handoff_operation_already_settled",
            lambda: _claim(registry, ref),
        )
        second = _claim(registry, ref, operation_id="operation-2")
        assert second.lease_id != first.lease_id
        assert registry.settle_not_committed(
            lease_id=first.lease_id,
            session_id=first.session_id,
            operation_id=first.operation_id,
            runtime_generation=first.runtime_generation,
            reason="downstream_rejected",
        ) == receipt


def test_unknown_is_only_allowed_after_delivery_and_quarantines_exact_artifact():
    with _spool_root() as root:
        source = _artifact(root)
        registry = ArtifactHandoffRegistry(spool_root=root)
        ref = registry.register(
            source,
            runtime_generation="runtime-1",
            storage_generation="storage-1",
        )
        lease = _claim(registry, ref)
        _error(
            "artifact_handoff_unknown_before_delivery",
            lambda: registry.settle_unknown(
                lease_id=lease.lease_id,
                session_id=lease.session_id,
                operation_id=lease.operation_id,
                runtime_generation=lease.runtime_generation,
                reason="connection_lost",
            ),
        )
        _start(registry, lease)
        receipt = registry.settle_unknown(
            lease_id=lease.lease_id,
            session_id=lease.session_id,
            operation_id=lease.operation_id,
            runtime_generation=lease.runtime_generation,
            reason="connection_lost",
        )
        assert receipt["kind"] == "artifact_handoff.outcome_unknown"
        assert registry.describe(artifact_id=ref.artifact_id)["state"] == (
            "quarantined_unknown"
        )
        assert source.path.exists()
        _error(
            "artifact_handoff_artifact_not_ready",
            lambda: _claim(registry, ref, operation_id="operation-2"),
        )


def test_source_replacement_and_outside_spool_are_rejected_without_path_leakage():
    with _spool_root() as root, _spool_root() as other:
        registry = ArtifactHandoffRegistry(spool_root=root)
        outside = _artifact(other)
        error = _error(
            "artifact_handoff_source_outside_spool",
            lambda: registry.register(
                outside,
                runtime_generation="runtime-1",
                storage_generation="storage-1",
            ),
        )
        assert str(other) not in str(error.details)

        source = _artifact(root)
        ref = registry.register(
            source,
            runtime_generation="runtime-1",
            storage_generation="storage-1",
        )
        source.path.write_bytes(b"replacement")
        _error(
            "artifact_handoff_source_identity_changed",
            lambda: _claim(registry, ref),
        )


def test_concurrent_claim_has_one_winner_and_telemetry_tracks_app_owned_high_water():
    now = [100.0]
    with _spool_root() as root:
        registry = ArtifactHandoffRegistry(spool_root=root, clock=lambda: now[0])
        ref = registry.register(
            _artifact(root, body=b"payload"),
            runtime_generation="runtime-1",
            storage_generation="storage-1",
        )
        barrier = threading.Barrier(3)
        leases = []
        errors = []

        def worker(operation_id: str):
            barrier.wait()
            try:
                leases.append(_claim(registry, ref, operation_id=operation_id))
            except NativeArtifactHandoffError as exc:
                errors.append(exc.code)

        threads = [
            threading.Thread(target=worker, args=("operation-a",)),
            threading.Thread(target=worker, args=("operation-b",)),
        ]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        assert len(leases) == 1
        assert errors == ["artifact_handoff_artifact_not_ready"]
        now[0] = 107.5
        telemetry = registry.telemetry()
        assert telemetry["capacity_source"] == "qualification_telemetry_only"
        assert telemetry["production_owner"] is False
        assert telemetry["current"]["consumer_lease_count"] == 1
        assert telemetry["current"]["consumer_lease_bytes"] == 7
        assert telemetry["current"]["oldest_lease_seconds"] == 7.5
        assert telemetry["peak"]["ready_count"] == 1
        assert telemetry["peak"]["consumer_lease_count"] == 1
        assert telemetry["outcomes"]["registered"] == 1
        assert telemetry["outcomes"]["claimed"] == 1
