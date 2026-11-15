from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest

from genshin_app.agent_chat.native_logical_stream import (
    DEFAULT_LOGICAL_STREAM_CHUNK_BYTES,
    DEFAULT_LOGICAL_STREAM_CREDIT_BYTES,
    DEFAULT_LOGICAL_STREAM_MAX_BYTES,
    LOGICAL_STREAM_CONTRACT,
    PRODUCTION_OWNER,
    LogicalStreamAssembler,
    LogicalStreamCreditTracker,
    NativeLogicalStreamError,
)

_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME = _ROOT / "agent-runtime"
_FIXTURE_PATH = _RUNTIME / "native-logical-stream-v1-vectors.json"
_NODE_PROBE = _RUNTIME / "src" / "native-logical-stream-probe.mjs"
_FIXTURE = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


@contextmanager
def _spool_root():
    parent = (_ROOT / "build-py").resolve()
    parent.mkdir(parents=True, exist_ok=True)
    root = (parent / f"logical-stream-{uuid.uuid4().hex}").resolve()
    if root.parent != parent:
        raise AssertionError("logical_stream_test_root_escape")
    root.mkdir()
    try:
        yield root
    finally:
        resolved = root.resolve()
        if resolved.parent != parent:
            raise AssertionError("logical_stream_test_cleanup_escape")
        shutil.rmtree(resolved, ignore_errors=False)


def _assembler(root: Path, **overrides) -> LogicalStreamAssembler:
    policy = _FIXTURE["policy"]
    options = {
        "spool_root": root,
        "allowed_purposes": frozenset({"dsh_fetch_request"}),
        "metadata_keys_by_purpose": {
            "dsh_fetch_request": frozenset({"method", "route"})
        },
        "maximum_bytes": policy["maximum_bytes"],
        "credit_window_bytes": policy["credit_window_bytes"],
        "minimum_chunk_bytes": policy["minimum_chunk_bytes"],
        "maximum_chunk_bytes": policy["maximum_chunk_bytes"],
    }
    options.update(overrides)
    return LogicalStreamAssembler(**options)


def _error(code: str, operation) -> NativeLogicalStreamError:
    with pytest.raises(NativeLogicalStreamError) as captured:
        operation()
    assert captured.value.code == code
    return captured.value


def test_cross_language_fixture_is_file_backed_and_atomically_committed():
    with _spool_root() as root:
        assembler = _assembler(root)
        accepted = assembler.open(_FIXTURE["open"])
        assert accepted == {
            "kind": "logical_stream.accept",
            "contract": LOGICAL_STREAM_CONTRACT,
            "transfer_id": "fixture-transfer-1",
            "accepted_through_seq": 1,
            "grant_bytes": _FIXTURE["expected"]["initial_credit"],
            "production_owner": False,
        }
        credits = []
        for row in _FIXTURE["chunks"]:
            receipt = assembler.append_chunk(
                transfer_id="fixture-transfer-1",
                stream_seq=row["stream_seq"],
                payload=bytes.fromhex(row["payload_hex"]),
            )
            credits.append(receipt["grant_bytes"])
        assert credits == _FIXTURE["expected"]["chunk_credits"]
        artifact = assembler.commit(
            transfer_id="fixture-transfer-1",
            stream_seq=_FIXTURE["commit"]["stream_seq"],
            message=_FIXTURE["commit"]["payload"],
        )
        assert artifact.path.suffix == ".ready"
        assert artifact.path.read_bytes().hex() == _FIXTURE["expected"]["payload_hex"]
        assert artifact.metadata_json == _FIXTURE["expected"]["metadata_json"]
        assert artifact.total_length == 10
        assert assembler.active_transfers == 0
        assert assembler.retained_transfer_ids == 1
        assert assembler.aggregate_spool_bytes == 10
        assembler.release_artifact(transfer_id="fixture-transfer-1")
        assert not artifact.path.exists()
        assert assembler.aggregate_spool_bytes == 0


def test_default_contract_admits_300_mib_declaration_without_allocating_it():
    assert DEFAULT_LOGICAL_STREAM_CHUNK_BYTES == 8 * 1024 * 1024
    assert DEFAULT_LOGICAL_STREAM_CREDIT_BYTES == 16 * 1024 * 1024
    assert DEFAULT_LOGICAL_STREAM_MAX_BYTES == 300 * 1024 * 1024
    assert PRODUCTION_OWNER is False
    with _spool_root() as root:
        assembler = LogicalStreamAssembler(
            spool_root=root,
            allowed_purposes=frozenset({"dsh_fetch_request"}),
            metadata_keys_by_purpose={
                "dsh_fetch_request": frozenset({"method", "route"})
            },
        )
        message = {
            **_FIXTURE["open"],
            "transfer_id": "max-contract",
            "maximum_bytes": DEFAULT_LOGICAL_STREAM_MAX_BYTES,
            "declared_length": DEFAULT_LOGICAL_STREAM_MAX_BYTES,
            "declared_sha256": None,
            "chunk_bytes": DEFAULT_LOGICAL_STREAM_CHUNK_BYTES,
        }
        receipt = assembler.open(message)
        assert receipt["grant_bytes"] == DEFAULT_LOGICAL_STREAM_CREDIT_BYTES
        assembler.cancel(transfer_id="max-contract")
        _error(
            "logical_stream_maximum_exceeded",
            lambda: assembler.open(
                {
                    **message,
                    "transfer_id": "too-large-contract",
                    "maximum_bytes": DEFAULT_LOGICAL_STREAM_MAX_BYTES + 1,
                }
            ),
        )
        _error(
            "logical_stream_metadata_key_not_allowed",
            lambda: assembler.open(
                {
                    **message,
                    "transfer_id": "metadata-contract",
                    "metadata": {"host_path": "forbidden"},
                }
            ),
        )


def test_sequence_chunk_and_credit_violations_abort_partial_transfer():
    cases = [
        ("logical_stream_sequence_mismatch", 3, b"a"),
        ("logical_stream_chunk_too_large", 2, b"abcde"),
        ("logical_stream_credit_exhausted", 2, b"abcd"),
    ]
    for index, (code, sequence, payload) in enumerate(cases):
        with _spool_root() as root:
            assembler = _assembler(
                root,
                credit_window_bytes=2
                if code == "logical_stream_credit_exhausted"
                else 8,
            )
            message = {**_FIXTURE["open"], "transfer_id": f"failure-{index}"}
            assembler.open(message)
            _error(
                code,
                lambda assembler=assembler, message=message, sequence=sequence, payload=payload: (
                    assembler.append_chunk(
                        transfer_id=message["transfer_id"],
                        stream_seq=sequence,
                        payload=payload,
                    )
                ),
            )
            assert assembler.active_transfers == 0
            assert not list(root.glob("*.part"))


def test_bad_commit_never_publishes_and_transfer_id_is_not_reused():
    with _spool_root() as root:
        assembler = _assembler(root)
        message = {
            **_FIXTURE["open"],
            "transfer_id": "bad-commit",
            "declared_length": 4,
            "declared_sha256": hashlib.sha256(b"abcd").hexdigest(),
        }
        assembler.open(message)
        assembler.append_chunk(transfer_id="bad-commit", stream_seq=2, payload=b"abcd")
        _error(
            "logical_stream_sha256_mismatch",
            lambda: assembler.commit(
                transfer_id="bad-commit",
                stream_seq=3,
                message={
                    "transfer_id": "bad-commit",
                    "chunk_count": 1,
                    "total_length": 4,
                    "sha256": "0" * 64,
                    "terminal": True,
                },
            ),
        )
        assert not list(root.glob("*.ready"))
        assert not list(root.glob("*.part"))
        _error("logical_stream_transfer_id_reused", lambda: assembler.open(message))


def test_cancel_cleans_partial_spool_and_settles_once():
    with _spool_root() as root:
        assembler = _assembler(root)
        message = {
            **_FIXTURE["open"],
            "transfer_id": "cancel-me",
            "declared_length": None,
            "declared_sha256": None,
        }
        assembler.open(message)
        assembler.append_chunk(transfer_id="cancel-me", stream_seq=2, payload=b"ab")
        state = assembler._active["cancel-me"]
        state.handle.close()
        state.partial_path.unlink()
        receipt = assembler.cancel(transfer_id="cancel-me", reason="user_cancelled")
        assert receipt["kind"] == "logical_stream.cancelled"
        assert assembler.aggregate_spool_bytes == 0
        assert not list(root.glob("*.part"))
        _error(
            "logical_stream_transfer_not_active",
            lambda: assembler.cancel(transfer_id="cancel-me"),
        )


def test_sender_credit_tracker_is_bounded():
    tracker = LogicalStreamCreditTracker(
        transfer_id="credit-transfer", maximum_credit_bytes=8
    )
    tracker.apply_grant(
        transfer_id="credit-transfer", accepted_through_seq=1, grant_bytes=8
    )
    tracker.reserve(4)
    assert tracker.available_bytes == 4
    _error("logical_stream_credit_exhausted", lambda: tracker.reserve(5))
    tracker.apply_grant(
        transfer_id="credit-transfer", accepted_through_seq=2, grant_bytes=4
    )
    _error(
        "logical_stream_credit_replayed",
        lambda: tracker.apply_grant(
            transfer_id="credit-transfer", accepted_through_seq=2, grant_bytes=1
        ),
    )
    _error(
        "logical_stream_credit_sequence_mismatch",
        lambda: tracker.apply_grant(
            transfer_id="credit-transfer", accepted_through_seq=4, grant_bytes=1
        ),
    )
    _error(
        "logical_stream_credit_transfer_mismatch",
        lambda: tracker.apply_grant(
            transfer_id="other-transfer", accepted_through_seq=3, grant_bytes=1
        ),
    )
    _error(
        "logical_stream_credit_overflow",
        lambda: tracker.apply_grant(
            transfer_id="credit-transfer", accepted_through_seq=3, grant_bytes=1
        ),
    )


def test_shapes_metadata_and_chunk_topology_are_strict():
    with _spool_root() as root:
        assembler = _assembler(root)
        _error(
            "logical_stream_open_shape_invalid",
            lambda: assembler.open({**_FIXTURE["open"], "unexpected": True}),
        )
        _error(
            "logical_stream_metadata_invalid",
            lambda: assembler.open(
                {
                    **_FIXTURE["open"],
                    "transfer_id": "float-metadata",
                    "metadata": {"method": float("nan")},
                }
            ),
        )
        rejection = _FIXTURE["rejections"][0]
        _error(
            rejection["code"],
            lambda: assembler.open(
                {
                    **_FIXTURE["open"],
                    "transfer_id": "array-index-key",
                    "metadata": rejection["metadata"],
                }
            ),
        )
        _error(
            "logical_stream_media_type_invalid",
            lambda: assembler.open(
                {
                    **_FIXTURE["open"],
                    "transfer_id": "wide-media-type",
                    "media_type": "界" * 100,
                }
            ),
        )

        unknown = {
            **_FIXTURE["open"],
            "transfer_id": "short-final",
            "declared_length": None,
            "declared_sha256": None,
        }
        assembler.open(unknown)
        assembler.append_chunk(transfer_id="short-final", stream_seq=2, payload=b"ab")
        _error(
            "logical_stream_chunk_after_final",
            lambda: assembler.append_chunk(
                transfer_id="short-final", stream_seq=3, payload=b"c"
            ),
        )

        empty = {
            **_FIXTURE["open"],
            "transfer_id": "empty-chunk",
            "declared_length": None,
            "declared_sha256": None,
        }
        assembler.open(empty)
        _error(
            "logical_stream_chunk_empty",
            lambda: assembler.append_chunk(
                transfer_id="empty-chunk", stream_seq=2, payload=b""
            ),
        )


def test_active_retained_spool_and_abort_all_limits():
    with _spool_root() as root:
        assembler = _assembler(
            root,
            max_active_transfers=2,
            max_retained_transfer_ids=3,
            aggregate_spool_bytes=4,
        )
        first = {
            **_FIXTURE["open"],
            "transfer_id": "quota-first",
            "declared_length": None,
            "declared_sha256": None,
        }
        second = {**first, "transfer_id": "quota-second"}
        third = {**first, "transfer_id": "quota-third"}
        assembler.open(first)
        assembler.open(second)
        _error("logical_stream_active_limit", lambda: assembler.open(third))
        assembler.append_chunk(transfer_id="quota-first", stream_seq=2, payload=b"abcd")
        _error(
            "logical_stream_aggregate_spool_limit",
            lambda: assembler.append_chunk(
                transfer_id="quota-second", stream_seq=2, payload=b"abcd"
            ),
        )
        # The rejected transfer was fail-closed and its partial file removed.
        assert assembler.active_transfers == 1
        receipt = assembler.abort_all(reason="qualification_shutdown")
        assert receipt["aborted"] == 1
        assert assembler.aggregate_spool_bytes == 0
        assert not list(root.glob("*.part"))
        assembler.open(third)
        assembler.cancel(transfer_id="quota-third")
        _error(
            "logical_stream_retained_limit",
            lambda: assembler.open({**first, "transfer_id": "quota-fourth"}),
        )


def test_singleflight_and_cleanup_failure_are_explicit():
    class _FailingClose:
        def close(self):
            raise OSError("synthetic close failure")

    with _spool_root() as root:
        assembler = _assembler(root)
        message = {
            **_FIXTURE["open"],
            "transfer_id": "singleflight",
            "declared_length": None,
            "declared_sha256": None,
        }
        assembler.open(message)
        state = assembler._active["singleflight"]
        state.busy = True
        _error(
            "logical_stream_operation_in_progress",
            lambda: assembler.cancel(transfer_id="singleflight"),
        )
        state.busy = False
        state.handle.close()
        state.handle = _FailingClose()
        error = _error(
            "logical_stream_cleanup_failed",
            lambda: assembler.cancel(transfer_id="singleflight"),
        )
        assert error.details == {
            "transfer_id": "singleflight",
            "cause": "cancelled",
            "phases": ("close",),
        }
        assert assembler.active_transfers == 0


def test_node_logical_stream_probe_with_pinned_runtime():
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
    assert json.loads(completed.stdout) == {
        "contract": LOGICAL_STREAM_CONTRACT,
        "status": "passed",
        "chunks": len(_FIXTURE["chunks"]),
        "productionOwner": False,
        "maximumLogicalBytes": DEFAULT_LOGICAL_STREAM_MAX_BYTES,
    }
