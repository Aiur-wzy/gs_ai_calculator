"""Named late-bound installation collaborators; carries no transaction state."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MigrationStagePorts:
    DatabaseMigrationError: Callable[..., Any]
    fail: Callable[..., Any]
    sha256: Callable[..., Any]
    fsync_file: Callable[..., Any]
    validate_database: Callable[..., Any]
    verify_package_bytes: Callable[..., Any]
    check_sqlite_integrity: Callable[..., Any]
    integrity_identity: Callable[..., Any]
    read_live_receipt: Callable[..., Any]
    read_live_manifest: Callable[..., Any]
    rollback_snapshot: Callable[..., Any]
    write_bytes_atomic: Callable[..., Any]
    now: Callable[..., Any]
    fingerprint: Callable[..., Any]
    create_database_live_receipt: Callable[..., Any]
    database_live_receipt_matches: Callable[..., Any]
    required_manifest_components: Callable[..., Any]
    uuid4: Callable[..., Any]
