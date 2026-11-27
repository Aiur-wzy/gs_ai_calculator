"""Reuse one accepted database release across App/Pair upgrades.

Database release identity is semantic.  App and Pair releases may advance while
the reviewed database content stays unchanged, so coordinated updates must not
copy the SQLite file merely to refresh compatibility metadata.  This module is
the single App-layer owner for verifying that case and rebinding the accepted
manifest/receipt; it never grants compatibility on its own (PairManifest does
that) and never edits ``genshin.db``.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from genshin_app.database_transaction import serialized_profile_writer
from genshin_db.release_profile import (
    LIVE_RECEIPT_FILENAME,
    DatabaseReleaseError,
    create_database_live_receipt,
    database_live_receipt_matches,
    database_release_semantically_equal,
    load_database_release_manifest,
    read_database_identity,
    validate_database_live_receipt,
    validate_database_release_manifest,
)
from genshin_db.release_qualification import (
    DatabaseInstallQualification,
    read_qualified_database_identity,
)

LIVE_MANIFEST_FILENAME = "database-live-manifest.json"


class DatabaseReleaseReuseError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        raw = (
            json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True)
            + "\n"
        ).encode("utf-8")
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def verified_reusable_database(
    data_dir: Path,
    target_manifest: Mapping[str, Any],
    *,
    current_qualification: DatabaseInstallQualification | None = None,
) -> dict[str, Any] | None:
    """Return the live identity only for a valid, semantically equal release."""

    directory = Path(data_dir)
    manifest_path = directory / LIVE_MANIFEST_FILENAME
    receipt_path = directory / LIVE_RECEIPT_FILENAME
    database_path = directory / "genshin.db"
    if not (manifest_path.is_file() and receipt_path.is_file() and database_path.is_file()):
        return None
    try:
        current_manifest = load_database_release_manifest(manifest_path)
        receipt = validate_database_live_receipt(
            json.loads(receipt_path.read_text(encoding="utf-8"))
        )
        if current_qualification is not None:
            identity = read_qualified_database_identity(
                database_path, current_manifest, current_qualification,
            )
        else:
            identity = read_database_identity(database_path)
        valid = database_live_receipt_matches(receipt, identity, current_manifest)
        same_release = database_release_semantically_equal(
            current_manifest,
            target_manifest,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatabaseReleaseReuseError(
            "database_reuse_identity_unreadable"
        ) from error
    except DatabaseReleaseError as error:
        raise DatabaseReleaseReuseError(error.reason) from error
    if not valid:
        raise DatabaseReleaseReuseError("database_reuse_live_identity_mismatch")
    return identity if same_release else None


@serialized_profile_writer()
def rebind_database_sidecars(
    data_dir: Path,
    target_manifest: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> None:
    """Rebind manifest/receipt, restoring both on an ordinary exception.

    The caller must publish PairManifest last.  A process crash between these
    writes remains fail-closed and the explicit repair/update operation can be
    retried; it never leaves a newly authoritative Pair pointing at partial
    database metadata.
    """

    directory = Path(data_dir)
    digest = hashlib.sha256()
    try:
        with (directory / "genshin.db").open("rb") as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise DatabaseReleaseReuseError("database_reuse_identity_unreadable") from error
    if digest.hexdigest() != identity.get("sha256"):
        raise DatabaseReleaseReuseError("database_reuse_live_identity_mismatch")
    manifest_path = directory / LIVE_MANIFEST_FILENAME
    receipt_path = directory / LIVE_RECEIPT_FILENAME
    previous_manifest = manifest_path.read_bytes() if manifest_path.exists() else None
    previous_receipt = receipt_path.read_bytes() if receipt_path.exists() else None
    try:
        manifest = validate_database_release_manifest(dict(target_manifest))
        receipt = create_database_live_receipt(
            manifest,
            identity,
            installed_at=_now(),
        )
        _write_json_atomic(manifest_path, manifest)
        _write_json_atomic(receipt_path, receipt)
    except (DatabaseReleaseError, OSError) as error:
        try:
            for path, previous in (
                (manifest_path, previous_manifest),
                (receipt_path, previous_receipt),
            ):
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    temporary = path.with_name(f".{path.name}.{uuid4().hex}.restore")
                    temporary.write_bytes(previous)
                    os.replace(temporary, path)
        except OSError:
            pass
        reason = getattr(error, "reason", "database_reuse_sidecar_rebind_failed")
        raise DatabaseReleaseReuseError(reason) from error


__all__ = [
    "DatabaseReleaseReuseError",
    "rebind_database_sidecars",
    "verified_reusable_database",
]
