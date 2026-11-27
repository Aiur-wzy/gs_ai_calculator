"""Database installation leaves with no lock, journal, active swap or rollback authority.

The coordinator alone chooses execution order and owns the accepted boundary.
Each leaf returns explicit artifacts and can operate only at the paths supplied
by that coordinator; it cannot publish an installed release by itself.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genshin_db.release_migration_ports import MigrationStagePorts
from genshin_db.release_profile import DatabaseReleaseError
from genshin_db.release_qualification import (
    DatabaseInstallQualification,
    copy_qualified_database,
    qualified_database_identity,
    read_qualified_database_identity,
)
from genshin_runtime.diagnostics import diagnosed


def _migration_diagnostic_metadata(arguments):
    identity = (arguments.get("target_database") or arguments.get("active_identity")
                or arguments.get("package_identity")
                or (arguments.get("checked_manifest") or {}).get("database") or {})
    return {"source_commit": identity.get("upstream_commit")}


@dataclass(frozen=True, slots=True)
class PreparedInstallation:
    previous_receipt_bytes: bytes | None
    previous_manifest_bytes: bytes | None
    operation_id: str
    staging: Path
    rollback_dir: Path
    rollback: Path
    rollback_receipt: Path
    rollback_manifest: Path
    old_receipt_present: bool
    old_manifest_present: bool
    old_sha: str | None


@dataclass(frozen=True, slots=True)
class CurrentReleaseSnapshot:
    old_sha: str | None
    rollback_created: bool


@diagnosed('database-install', 'prepare-installation-artifacts-v1', metadata=_migration_diagnostic_metadata, result_metadata=lambda _arguments, result: {
    "subject_operation_id": result.operation_id})
def prepare_installation_artifacts(
    *,
    data_dir: Path,
    live: Path,
    build: Path,
    package_identity: Mapping[str, Any],
    production: bool,
    receipt_path: Path,
    manifest_path: Path,
    minimum_headroom_bytes: int,
    sqlite_work_allowance_bytes: int,
    ports: MigrationStagePorts,
    current_qualification: DatabaseInstallQualification | None = None,
) -> PreparedInstallation:
    try:
        previous_receipt_bytes = (
            receipt_path.read_bytes() if receipt_path.exists() else None
        )
        previous_manifest_bytes = (
            manifest_path.read_bytes() if manifest_path.exists() else None
        )
    except OSError as error:
        raise ports.DatabaseMigrationError(
            "database_live_identity_unreadable", "无法读取当前数据库本机身份文件"
        ) from error
    if (previous_receipt_bytes is None) != (previous_manifest_bytes is None):
        ports.fail(
            "database_migration_recovery_required",
            "accepted manifest 与 receipt 不完整，需要人工恢复",
        )
    if not live.exists() and (
        previous_receipt_bytes is not None or previous_manifest_bytes is not None
    ):
        ports.fail(
            "database_migration_recovery_required",
            "在用数据库缺失但本机发行身份仍存在，需要人工恢复",
        )
    live_size = live.stat().st_size if live.exists() else 0
    required_free = (
        minimum_headroom_bytes
        + package_identity["size_bytes"]
        + 2 * live_size
        + sqlite_work_allowance_bytes
    )
    free = shutil.disk_usage(data_dir).free
    if free < required_free:
        ports.fail(
            "database_migration_space_insufficient",
            "数据库升级空间不足",
            {"required_free": required_free, "available_free": free},
        )
    operation_id = "dbm_" + ports.uuid4().hex
    staging = build / f"database-release-{operation_id}.db"
    rollback_dir = data_dir / ("build" if production else "backups")
    rollback = rollback_dir / f"database-rollback-{operation_id}.db"
    rollback_receipt = rollback_dir / f"database-rollback-{operation_id}.receipt.json"
    rollback_manifest = rollback_dir / f"database-rollback-{operation_id}.manifest.json"
    old_receipt_present = previous_receipt_bytes is not None
    old_manifest_present = previous_manifest_bytes is not None
    old_sha = None
    if live.exists():
        old_sha = current_qualification.sha256 if current_qualification else ports.sha256(live)
    return PreparedInstallation(
        previous_receipt_bytes,
        previous_manifest_bytes,
        operation_id,
        staging,
        rollback_dir,
        rollback,
        rollback_receipt,
        rollback_manifest,
        old_receipt_present,
        old_manifest_present,
        old_sha,
    )


@diagnosed('database-install', 'copy-and-verify-package-v1', metadata=_migration_diagnostic_metadata)
def copy_and_verify_package(
    *,
    package: Path,
    staging: Path,
    target_database: Mapping[str, Any],
    ports: MigrationStagePorts,
    qualification: DatabaseInstallQualification | None = None,
) -> None:
    if qualification is not None:
        copy_qualified_database(package, staging, qualification)
        return
    shutil.copyfile(package, staging)
    ports.fsync_file(staging)
    ports.verify_package_bytes(
        staging, target_database, reason="database_migration_staging_identity_mismatch",
    )


@diagnosed('database-install', 'snapshot-current-release-v1', metadata=_migration_diagnostic_metadata)
def snapshot_current_release(
    *,
    live: Path,
    manifest_path: Path,
    receipt_path: Path,
    old_manifest_present: bool,
    rollback_dir: Path,
    rollback: Path,
    rollback_receipt: Path,
    rollback_manifest: Path,
    previous_receipt_bytes: bytes | None,
    previous_manifest_bytes: bytes | None,
    ports: MigrationStagePorts,
    current_qualification: DatabaseInstallQualification | None = None,
) -> CurrentReleaseSnapshot:
    if current_qualification is not None and not old_manifest_present:
        ports.fail("database_migration_recovery_required")
    old_sha = None
    if live.exists():
        old_sha = current_qualification.sha256 if current_qualification else ports.sha256(live)
    if old_manifest_present:
        old_manifest = ports.read_live_manifest(manifest_path)
        old_receipt = ports.read_live_receipt(receipt_path)
        if old_manifest is None or old_receipt is None or (not live.exists()):
            ports.fail(
                "database_migration_recovery_required",
                "当前数据库的 accepted 身份不完整，需要人工恢复",
            )
        if current_qualification is not None:
            old_identity = qualified_database_identity(old_manifest, current_qualification)
        else:
            old_identity = ports.integrity_identity(
                live,
                ports.validate_database(
                    live,
                    required_components=ports.required_manifest_components(old_manifest),
                ),
                old_manifest["database"],
                mismatch_reason="database_migration_current_release_core_mismatch",
            )
        try:
            old_identity_matches = ports.database_live_receipt_matches(
                old_receipt, old_identity, old_manifest
            )
        except DatabaseReleaseError:
            old_identity_matches = False
        if not old_identity_matches:
            ports.fail(
                "database_migration_recovery_required",
                "当前数据库、accepted manifest 与 receipt 不一致",
            )
    rollback_created = False
    if live.exists():
        rollback_dir.mkdir(parents=True, exist_ok=True)
        if current_qualification is not None:
            copy_qualified_database(live, rollback, current_qualification)
        else:
            ports.rollback_snapshot(live, rollback)
        rollback_created = True
    if previous_receipt_bytes is not None:
        ports.write_bytes_atomic(rollback_receipt, previous_receipt_bytes)
    if previous_manifest_bytes is not None:
        ports.write_bytes_atomic(rollback_manifest, previous_manifest_bytes)
    return CurrentReleaseSnapshot(old_sha, rollback_created)


@diagnosed('database-install', 'validate-prepublication-candidate-v1', metadata=_migration_diagnostic_metadata)
def validate_prepublication_candidate(
    *,
    staging: Path,
    target_database: Mapping[str, Any],
    ports: MigrationStagePorts,
) -> None:
    # Nothing is rewritten after the original package's deep validation.
    # Verify bytes again after the snapshot/fault boundary instead of rebuilding
    # the same runtime indexes from the identical source JSON.
    ports.verify_package_bytes(
        staging, target_database, reason="database_migration_release_core_changed",
    )


@diagnosed('database-install', 'postcheck-active-database-v1', metadata=_migration_diagnostic_metadata)
def postcheck_active_database(
    *,
    live: Path,
    package_identity: Mapping[str, Any],
    target_database: Mapping[str, Any],
    ports: MigrationStagePorts,
    qualification: DatabaseInstallQualification | None = None,
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if qualification is not None:
        if manifest is None:
            ports.fail("database_install_qualification_mismatch")
        return read_qualified_database_identity(live, manifest, qualification)
    # Keep an independent SQLite open/physical integrity check at the actual
    # active path. The hash binds the logical checks to those same bytes.
    ports.check_sqlite_integrity(live)
    ports.verify_package_bytes(
        live, target_database, reason="database_migration_active_postcheck_failed",
    )
    return dict(package_identity)


@diagnosed('database-install', 'prepare-acceptance-receipt-v1', metadata=_migration_diagnostic_metadata)
def prepare_acceptance_receipt(
    *,
    checked_manifest: Mapping[str, Any],
    active_identity: Mapping[str, Any],
    ports: MigrationStagePorts,
) -> dict[str, Any]:
    try:
        live_receipt = ports.create_database_live_receipt(
            checked_manifest, active_identity, installed_at=ports.now()
        )
    except DatabaseReleaseError as receipt_error:
        raise ports.DatabaseMigrationError(
            receipt_error.reason, str(receipt_error)
        ) from receipt_error
    return live_receipt


@diagnosed('database-install', 'cleanup-installation-artifacts-v1', metadata=_migration_diagnostic_metadata)
def cleanup_installation_artifacts(
    *,
    staging: Path | None,
    rollback: Path | None,
    rollback_receipt: Path | None,
    rollback_manifest: Path | None,
    production: bool,
    staging_first: bool = False,
) -> None:
    rollback_paths = (
        (rollback, rollback_receipt, rollback_manifest) if production else ()
    )
    paths = (staging, *rollback_paths) if staging_first else (*rollback_paths, staging)
    for path in paths:
        if path is not None:
            path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class VerifiedAcceptedIdentity:
    active_identity: dict[str, Any]
    receipt: dict[str, Any]
    live_manifest: dict[str, Any]


@diagnosed('database-install', 'verify-accepted-recovery-identity-v1', metadata=_migration_diagnostic_metadata)
def verify_accepted_recovery_identity(
    *,
    live: Path,
    receipt_path: Path,
    manifest_path: Path,
    checked_manifest: Mapping[str, Any],
    previous: Mapping[str, Any],
    ports: MigrationStagePorts,
    qualification: DatabaseInstallQualification | None = None,
) -> VerifiedAcceptedIdentity:
    if qualification is not None:
        active_identity = read_qualified_database_identity(live, checked_manifest, qualification)
    else:
        active_identity = ports.validate_database(
            live, required_components=ports.required_manifest_components(checked_manifest)
        )
        active_identity = ports.integrity_identity(
            live,
            active_identity,
            checked_manifest["database"],
            mismatch_reason="database_migration_active_release_core_mismatch",
        )
    receipt = ports.read_live_receipt(receipt_path)
    live_manifest = ports.read_live_manifest(manifest_path)
    try:
        receipt_matches = receipt is not None and ports.database_live_receipt_matches(
            receipt, active_identity, checked_manifest
        )
    except DatabaseReleaseError:
        receipt_matches = False
    if (
        receipt is None
        or live_manifest is None
        or live_manifest != checked_manifest
        or (ports.fingerprint(live_manifest) != previous["live_manifest_fingerprint"])
        or (receipt["receipt_fingerprint"] != previous["live_receipt_fingerprint"])
        or (not receipt_matches)
    ):
        ports.fail(
            "database_migration_recovery_required",
            "已发布数据库与迁移日志不一致，需要人工恢复",
            {"state": previous["state"]},
        )
    return VerifiedAcceptedIdentity(active_identity, receipt, live_manifest)
