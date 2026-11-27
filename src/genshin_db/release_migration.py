"""Transactional installation of an already reviewed database release.

This module is deliberately local-only.  Network discovery, download, user
interaction and GUI progress live at higher layers.  The coordinator accepts a
release package that has already been fetched, verifies it against the release
manifest, and atomically publishes it.  Project-maintained annotations,
appendices and reviewed mechanism records must already be part of that signed
candidate; installation never copies mutable-looking rows from the active
database into a reviewed package.

Production keeps a rollback snapshot only until the new database has passed an
active-path postcheck.  Development mode retains that snapshot for diagnostics.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from genshin_db.atomic_io import atomic_replace
from genshin_db.equipment import validate_equipment
from genshin_db.locking import LockBusyError, database_lock, file_lock
from genshin_db.release_integrity import (
    build_mutable_layer_fingerprint,
    validate_release_core,
)
from genshin_db.release_migration_ports import MigrationStagePorts
from genshin_db.release_migration_stages import (
    cleanup_installation_artifacts,
    copy_and_verify_package,
    postcheck_active_database,
    prepare_acceptance_receipt,
    prepare_installation_artifacts,
    snapshot_current_release,
    validate_prepublication_candidate,
    verify_accepted_recovery_identity,
)
from genshin_db.release_profile import (
    LIVE_RECEIPT_FILENAME,
    DatabaseReleaseError,
    create_database_live_receipt,
    database_live_receipt_matches,
    manifest_fingerprint,
    manifest_supports_app,
    read_database_identity,
    release_pair_matches_database_manifest,
    required_manifest_components,
    validate_database_live_receipt,
    validate_database_release_manifest,
    validate_release_pair_manifest,
)
from genshin_db.release_qualification import (
    QUALIFICATION_CONTRACT,
    DatabaseInstallQualification,
    check_qualified_destination,
    hold_exact_database_bytes,
    load_database_install_qualification,
    qualified_database_identity,
    read_qualified_database_identity,
)
from genshin_db.runtime_store import validate_runtime
from genshin_db.semantic_store import validate_component as validate_semantic_component

JOURNAL_CONTRACT = "genshin-database-release-migration-journal-v2"
JOURNAL_FILE = "database-release-migration-journal.json"
LIVE_MANIFEST_FILENAME = "database-live-manifest.json"
UPDATE_LOCK_FILE = ".update.lock"
MINIMUM_HEADROOM_BYTES = 5 * 1024**3
SQLITE_WORK_ALLOWANCE_BYTES = 64 * 1024**2
_TERMINAL_JOURNAL_STATES = frozenset({"cleanup_complete", "rolled_back"})
_ACCEPTED_JOURNAL_STATES = frozenset({"postchecked", "committed", "operational"})
_JOURNAL_STATES = frozenset(
    {
        "prepared",
        "validated",
        "swap_started",
        *_ACCEPTED_JOURNAL_STATES,
        *_TERMINAL_JOURNAL_STATES,
    }
)


class DatabaseMigrationError(RuntimeError):
    """Stable migration failure for App/CLI adapters."""

    def __init__(self, reason: str, message: str | None = None, details=None):
        self.reason = reason
        self.details = copy.deepcopy(details)
        super().__init__(message or reason)


def _fail(reason: str, message: str | None = None, details=None) -> None:
    raise DatabaseMigrationError(reason, message, details)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _fsync_file(path: Path) -> None:
    # Windows' CRT rejects fsync on a read-only descriptor even though the
    # operation does not change file contents.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability; Windows may reject directory handles."""

    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    raw = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        atomic_replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _write_bytes_atomic(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        atomic_replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _read_live_receipt(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_database_live_receipt(value)
    except (OSError, UnicodeError, json.JSONDecodeError, DatabaseReleaseError) as error:
        raise DatabaseMigrationError(
            "database_live_receipt_invalid",
            "数据库安装receipt损坏，需要人工恢复",
        ) from error


def _read_live_manifest(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return validate_database_release_manifest(value)
    except (OSError, UnicodeError, json.JSONDecodeError, DatabaseReleaseError) as error:
        raise DatabaseMigrationError(
            "database_live_manifest_invalid",
            "数据库本机 accepted manifest 损坏，需要人工恢复",
        ) from error


def _restore_sidecar(path: Path, previous: bytes | None) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
        _fsync_directory(path.parent)
        return
    _write_bytes_atomic(path, previous)


def _restore_live_receipt(path: Path, previous: bytes | None) -> None:
    """Compatibility wrapper retained for migration tests and old callers."""

    _restore_sidecar(path, previous)


def _read_journal(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatabaseMigrationError(
            "database_migration_journal_invalid",
            "数据库迁移日志损坏，需要人工恢复",
        ) from error
    if (
        type(value) is not dict
        or value.get("contract") != JOURNAL_CONTRACT
        or not isinstance(value.get("operation_id"), str)
        or not isinstance(value.get("target_release_id"), str)
        or value.get("state") not in _JOURNAL_STATES
        or type(value.get("production")) is not bool
    ):
        _fail("database_migration_journal_invalid", "数据库迁移日志格式不合法")
    for field in (
        "staging_name",
        "rollback_name",
        "rollback_receipt_name",
        "rollback_manifest_name",
    ):
        name = value.get(field)
        if name is not None and (
            type(name) is not str
            or not name
            or Path(name).name != name
            or "/" in name
            or "\\" in name
        ):
            _fail("database_migration_journal_invalid", "数据库迁移日志包含非法文件名")
    if type(value.get("old_receipt_present", False)) is not bool:
        _fail("database_migration_journal_invalid", "数据库迁移日志receipt状态无效")
    if type(value.get("old_manifest_present", False)) is not bool:
        _fail("database_migration_journal_invalid", "数据库迁移日志manifest状态无效")
    for field in (
        "target_package_sha256",
        "target_manifest_fingerprint",
        "old_sha256",
        "active_sha256",
    ):
        sha256 = value.get(field)
        if field == "target_package_sha256" and sha256 is None:
            _fail("database_migration_journal_invalid", "数据库迁移日志缺少包摘要")
        if sha256 is not None and (
            type(sha256) is not str
            or len(sha256) != 64
            or any(character not in "0123456789abcdef" for character in sha256)
        ):
            _fail("database_migration_journal_invalid", "数据库迁移日志摘要无效")
    receipt_fingerprint = value.get("live_receipt_fingerprint")
    manifest_fingerprint = value.get("live_manifest_fingerprint")
    for label, fingerprint in (
        ("receipt", receipt_fingerprint),
        ("manifest", manifest_fingerprint),
    ):
        if fingerprint is not None and (
            type(fingerprint) is not str
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            _fail(
                "database_migration_journal_invalid",
                f"数据库迁移{label}摘要无效",
            )
    if value["state"] in _ACCEPTED_JOURNAL_STATES and (
        receipt_fingerprint is None or manifest_fingerprint is None
        or value.get("target_manifest_fingerprint") is None
    ):
        _fail("database_migration_journal_invalid", "已发布迁移日志缺少本机身份摘要")
    return value


def _journal(
    path: Path,
    *,
    operation_id: str,
    target_release_id: str,
    state: str,
    production: bool,
    old_sha256: str | None,
    target_package_sha256: str,
    active_sha256: str | None = None,
    staging_name: str | None = None,
    rollback_name: str | None = None,
    rollback_receipt_name: str | None = None,
    rollback_manifest_name: str | None = None,
    old_receipt_present: bool = False,
    old_manifest_present: bool = False,
    target_manifest_fingerprint: str | None = None,
    live_receipt_fingerprint: str | None = None,
    live_manifest_fingerprint: str | None = None,
    error_reason: str | None = None,
) -> dict[str, Any]:
    value = {
        "contract": JOURNAL_CONTRACT,
        "operation_id": operation_id,
        "target_release_id": target_release_id,
        "state": state,
        "production": production,
        "old_sha256": old_sha256,
        "target_package_sha256": target_package_sha256,
        "active_sha256": active_sha256,
        "staging_name": staging_name,
        "rollback_name": rollback_name,
        "rollback_receipt_name": rollback_receipt_name,
        "rollback_manifest_name": rollback_manifest_name,
        "old_receipt_present": old_receipt_present,
        "old_manifest_present": old_manifest_present,
        "target_manifest_fingerprint": target_manifest_fingerprint,
        "live_receipt_fingerprint": live_receipt_fingerprint,
        "live_manifest_fingerprint": live_manifest_fingerprint,
        "error_reason": error_reason,
        "updated_at": _now(),
    }
    _write_json_atomic(path, value)
    return value


def _manifest_database_matches(identity: Mapping[str, Any], database: Mapping[str, Any]) -> bool:
    return all(
        identity.get(key) == database.get(key)
        for key in (
            "sha256",
            "size_bytes",
            "meta_fingerprint",
            "upstream_commit",
            "game_version",
            "upstream_track",
            "upstream_updated_at",
            "upstream_version_evidence",
            "version_lines",
            "indexer_version",
            "normalization_version",
            "datasets",
        )
    )


def _integrity_identity(
    path: Path,
    identity: Mapping[str, Any],
    database: Mapping[str, Any],
    *,
    mismatch_reason: str,
) -> dict[str, Any]:
    # _validate_database already computed the actual SQLite core and mutable
    # layer. The receipt contract fixes the whitelist across both checks.
    expected = validate_release_core(database.get("release_core"))
    actual = identity.get("release_core")
    if actual != expected:
        _fail(
            mismatch_reason,
            "数据库实际 schema 或行内容与发行核心不一致",
            {"expected_root_sha256": expected["root_sha256"],
             "actual_root_sha256": (actual or {}).get("root_sha256")},
        )
    enriched = copy.deepcopy(dict(identity))
    enriched["release_core_root"] = expected["root_sha256"]
    if "mutable_layer_fingerprint" not in enriched:
        enriched["mutable_layer_fingerprint"] = build_mutable_layer_fingerprint(
            path, mutable_tables=expected["mutable_tables"]
        )
    return enriched


def _required_components_ready(
    identity: Mapping[str, Any], required_components: tuple[str, ...] | list[str]
) -> None:
    datasets = identity.get("datasets")
    missing: list[str] = []
    for component in required_components:
        value = datasets.get(component) if type(datasets) is dict else None
        lines = value.get("lines") if type(value) is dict else None
        release = lines.get("release") if type(lines) is dict else None
        if type(release) is not dict or release.get("state") != "ready":
            missing.append(component)
    if missing:
        _fail(
            "database_migration_components_not_ready",
            "数据库发行包缺少必需组件",
            {"missing_components": missing},
        )


def _check_sqlite_integrity(path: Path) -> None:
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                _fail("database_migration_integrity_failed", "数据库完整性检查失败")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                _fail("database_migration_foreign_key_failed", "数据库外键检查失败")
    except (OSError, sqlite3.Error) as error:
        raise DatabaseMigrationError("database_migration_validation_failed", "数据库发行候选校验失败") from error


def _verify_package_bytes(path: Path, database: Mapping[str, Any], *, reason: str) -> None:
    """Verify a self-contained SQLite package at each actual transaction path."""

    def standalone() -> bool:
        return path.is_file() and not path.is_symlink() and not any(
            os.path.lexists(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal")
        )

    try:
        if not standalone() or path.stat().st_size != database["size_bytes"]:
            _fail(reason, "数据库文件与已深度校验的独立发行工件不一致")
        with path.open("rb") as handle:
            header = handle.read(20)
        matches = (
            header.startswith(b"SQLite format 3\x00") and len(header) == 20
            and header[18:20] == b"\x01\x01"
            and _sha256(path) == database["sha256"]
            and standalone()
        )
    except OSError as error:
        raise DatabaseMigrationError(reason, "无法校验数据库发行文件") from error
    if not matches:
        _fail(reason, "数据库文件与已深度校验的独立发行工件不一致")


def _validate_database(
    path: Path,
    *,
    required_components: tuple[str, ...] | list[str] = ("equipment",),
) -> dict[str, Any]:
    """Deep read-only validation used before and after publication."""

    try:
        _check_sqlite_integrity(path)
        identity = read_database_identity(path)
        _required_components_ready(identity, required_components)
        datasets = identity.get("datasets") or {}
        for component in ("equipment", "runtime_config", "mechanic_semantics"):
            state = ((((datasets.get(component) or {}).get("lines") or {}).get("release") or {}).get("state"))
            if state in ("invalid", "incompatible"):
                _fail(
                    "database_migration_component_invalid",
                    "数据库发行候选包含损坏或不兼容组件",
                    {"component": component, "state": state},
                )
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            equipment = ((datasets.get("equipment") or {}).get("lines") or {}).get("release") or {}
            runtime = ((datasets.get("runtime_config") or {}).get("lines") or {}).get("release") or {}
            semantics = ((datasets.get("mechanic_semantics") or {}).get("lines") or {}).get("release") or {}
            if equipment.get("state") == "ready":
                validate_equipment(connection, "release")
            if runtime.get("state") == "ready":
                validate_runtime(connection, "release", deep=True)
            if semantics.get("state") == "ready":
                semantic = validate_semantic_component(connection, "release")
                if semantic["status"] != "ready":
                    _fail(
                        "database_migration_semantics_not_ready",
                        "机制语义组件未就绪",
                    )
        return identity
    except DatabaseMigrationError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as error:
        raise DatabaseMigrationError(
            "database_migration_validation_failed",
            "数据库发行候选校验失败",
        ) from error


def build_database_install_qualification(
    database: Path, manifest: Mapping[str, Any]
) -> dict[str, Any]:
    """Fully validate final frozen bytes before emitting a publisher-signable claim."""

    checked = validate_database_release_manifest(dict(manifest))
    expected = checked["database"]
    with hold_exact_database_bytes(
        database, size=expected["size_bytes"], sha256=expected["sha256"],
    ) as path:
        identity = _validate_database(
            path, required_components=required_manifest_components(checked),
        )
        if not _manifest_database_matches(identity, expected):
            _fail("database_install_qualification_manifest_mismatch")
        identity = _integrity_identity(
            path, identity, expected, mismatch_reason="database_install_qualification_manifest_mismatch"
        )
    qualification = load_database_install_qualification(
        {
            "contract": QUALIFICATION_CONTRACT,
            "database_release_id": checked["database_release_id"],
            "manifest_fingerprint": manifest_fingerprint(checked),
            "sha256": identity["sha256"],
            "size_bytes": identity["size_bytes"],
            "release_core_sha256": identity["release_core_root"],
            "meta_fingerprint": identity["meta_fingerprint"],
            "mutable_layer_fingerprint": identity["mutable_layer_fingerprint"],
            "manual_content_fingerprint": identity.get("manual_content_fingerprint"),
        },
        checked,
    )
    # The final seal is based on the on-disk standalone image. No writes to
    # that image may follow the deep check and precede the publisher's signature.
    read_qualified_database_identity(database, checked, qualification)
    return qualification.payload()


def _rollback_snapshot(live: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(live)) as source, closing(
        sqlite3.connect(target)
    ) as destination:
        source.backup(destination)
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            _fail("database_migration_rollback_invalid", "迁移回滚副本校验失败")
    _fsync_file(target)


def _restore_rollback(*, live: Path, rollback: Path, keep_rollback: bool) -> None:
    if not rollback.exists():
        if live.exists():
            live.unlink()
        return
    if keep_rollback:
        recovery = live.with_name(live.name + ".recovery.tmp")
        if recovery.exists():
            recovery.unlink()
        shutil.copyfile(rollback, recovery)
        _fsync_file(recovery)
        os.replace(recovery, live)
    else:
        os.replace(rollback, live)
    _fsync_directory(live.parent)


def _fault(hook: Callable[[str], None] | None, phase: str) -> None:
    if hook is not None:
        hook(phase)


def _journal_artifact(data_dir: Path, journal: Mapping[str, Any], field: str) -> Path | None:
    name = journal.get(field)
    if name is None:
        return None
    if field == "staging_name" or journal["production"]:
        return data_dir / "build" / name
    return data_dir / "backups" / name


def _finish_accepted_journal(
    *,
    data_dir: Path,
    live: Path,
    journal_path: Path,
    receipt_path: Path,
    manifest_path: Path,
    previous: Mapping[str, Any],
    checked_manifest: Mapping[str, Any],
    qualification: DatabaseInstallQualification | None = None,
) -> dict[str, Any]:
    """Finish cleanup after a crash that occurred after the write fence reopened."""

    expected_manifest_fingerprint = _fingerprint(checked_manifest)
    if (
        previous["target_release_id"] != checked_manifest["database_release_id"]
        or previous.get("target_manifest_fingerprint")
        != expected_manifest_fingerprint
    ):
        _fail(
            "database_migration_recovery_required",
            "上次已发布数据库尚未完成清理，请先使用对应发行包恢复",
            {"state": previous["state"]},
        )
    with database_lock(live, timeout=0):
        verified = verify_accepted_recovery_identity(
            live=live, receipt_path=receipt_path, manifest_path=manifest_path,
            checked_manifest=checked_manifest, previous=previous,
            ports=_migration_stage_ports(), qualification=qualification,
        )
        active_identity = verified.active_identity
        receipt, live_manifest = verified.receipt, verified.live_manifest
        _journal(
            journal_path,
            operation_id=previous["operation_id"],
            target_release_id=previous["target_release_id"],
            state="operational",
            production=previous["production"],
            old_sha256=previous.get("old_sha256"),
            target_package_sha256=previous["target_package_sha256"],
            active_sha256=active_identity["sha256"],
            staging_name=previous.get("staging_name"),
            rollback_name=previous.get("rollback_name"),
            rollback_receipt_name=previous.get("rollback_receipt_name"),
            rollback_manifest_name=previous.get("rollback_manifest_name"),
            old_receipt_present=previous.get("old_receipt_present", False),
            old_manifest_present=previous.get("old_manifest_present", False),
            target_manifest_fingerprint=expected_manifest_fingerprint,
            live_receipt_fingerprint=receipt["receipt_fingerprint"],
            live_manifest_fingerprint=_fingerprint(live_manifest),
        )
    staging = _journal_artifact(data_dir, previous, "staging_name")
    rollback = _journal_artifact(data_dir, previous, "rollback_name")
    rollback_receipt = _journal_artifact(
        data_dir, previous, "rollback_receipt_name"
    )
    rollback_manifest = _journal_artifact(
        data_dir, previous, "rollback_manifest_name"
    )
    cleanup_installation_artifacts(
        staging=staging, rollback=rollback, rollback_receipt=rollback_receipt,
        rollback_manifest=rollback_manifest, production=previous["production"],
        staging_first=True,
    )
    _journal(
        journal_path,
        operation_id=previous["operation_id"],
        target_release_id=previous["target_release_id"],
        state="cleanup_complete",
        production=previous["production"],
        old_sha256=previous.get("old_sha256"),
        target_package_sha256=previous["target_package_sha256"],
        active_sha256=active_identity["sha256"],
        rollback_name=(
            previous.get("rollback_name") if not previous["production"] else None
        ),
        rollback_receipt_name=(
            previous.get("rollback_receipt_name")
            if not previous["production"]
            else None
        ),
        rollback_manifest_name=(
            previous.get("rollback_manifest_name")
            if not previous["production"]
            else None
        ),
        old_receipt_present=previous.get("old_receipt_present", False),
        old_manifest_present=previous.get("old_manifest_present", False),
        target_manifest_fingerprint=expected_manifest_fingerprint,
        live_receipt_fingerprint=receipt["receipt_fingerprint"],
        live_manifest_fingerprint=_fingerprint(live_manifest),
    )
    return active_identity


def _migration_stage_ports() -> MigrationStagePorts:
    return MigrationStagePorts(
        DatabaseMigrationError=lambda *a, **kw: DatabaseMigrationError(*a, **kw),
        fail=lambda *a, **kw: _fail(*a, **kw),
        sha256=lambda *a, **kw: _sha256(*a, **kw),
        fsync_file=lambda *a, **kw: _fsync_file(*a, **kw),
        validate_database=lambda *a, **kw: _validate_database(*a, **kw),
        verify_package_bytes=_verify_package_bytes,
        check_sqlite_integrity=_check_sqlite_integrity,
        integrity_identity=lambda *a, **kw: _integrity_identity(*a, **kw),
        read_live_receipt=lambda *a, **kw: _read_live_receipt(*a, **kw),
        read_live_manifest=lambda *a, **kw: _read_live_manifest(*a, **kw),
        rollback_snapshot=lambda *a, **kw: _rollback_snapshot(*a, **kw),
        write_bytes_atomic=lambda *a, **kw: _write_bytes_atomic(*a, **kw),
        now=lambda *a, **kw: _now(*a, **kw),
        fingerprint=lambda *a, **kw: _fingerprint(*a, **kw),
        create_database_live_receipt=lambda *a, **kw: create_database_live_receipt(*a, **kw),
        database_live_receipt_matches=lambda *a, **kw: database_live_receipt_matches(*a, **kw),
        required_manifest_components=lambda *a, **kw: required_manifest_components(*a, **kw),
        uuid4=lambda *a, **kw: uuid4(*a, **kw),
    )


def _install_locked(
    *,
    data_dir: Path,
    package: Path,
    live: Path,
    build: Path,
    journal_path: Path,
    checked_manifest: Mapping[str, Any],
    package_identity: Mapping[str, Any],
    production: bool,
    fault_hook: Callable[[str], None] | None,
    qualification: DatabaseInstallQualification | None = None,
    current_qualification: DatabaseInstallQualification | None = None,
) -> dict[str, Any]:
    target_release_id = checked_manifest["database_release_id"]
    target_database = checked_manifest["database"]
    target_manifest_fingerprint = _fingerprint(checked_manifest)
    receipt_path = data_dir / LIVE_RECEIPT_FILENAME
    manifest_path = data_dir / LIVE_MANIFEST_FILENAME
    previous = _read_journal(journal_path)
    if previous is not None and previous["state"] in _ACCEPTED_JOURNAL_STATES:
        if previous["production"] is not production:
            _fail(
                "database_migration_recovery_required",
                "上次数据库迁移的运行模式不同，请按原模式完成清理",
                {"state": previous["state"]},
            )
        active_identity = _finish_accepted_journal(
            data_dir=data_dir,
            live=live,
            journal_path=journal_path,
            receipt_path=receipt_path,
            manifest_path=manifest_path,
            previous=previous,
            checked_manifest=checked_manifest,
            qualification=qualification,
        )
        return {
            "status": "already_current",
            "scope": "database_subtransaction",
            "pair_operational": False,
            "target_release_id": target_release_id,
            "active_sha256": active_identity["sha256"],
            "production": production,
            "recovered_cleanup": True,
        }
    if previous is not None and previous["state"] == "swap_started":
        # A hard process stop can happen after the reviewed candidate has been
        # atomically exchanged into the live path but before its manifest and
        # receipt are published.  Resume only when every durable journal pin
        # still identifies this exact package.  This is not a best-effort
        # repair: an unknown live file or a journal from another invocation
        # remains an explicit recovery-required refusal.
        resumable_swap = (
            previous["production"] is production
            and previous["target_release_id"] == target_release_id
            and previous["target_package_sha256"] == package_identity["sha256"]
            and previous.get("target_manifest_fingerprint")
            == target_manifest_fingerprint
            and live.is_file()
        )
        active_identity = None
        if resumable_swap:
            try:
                with database_lock(live, timeout=0):
                    if qualification is not None:
                        active_identity = read_qualified_database_identity(
                            live, checked_manifest, qualification,
                        )
                    else:
                        candidate_identity = _validate_database(
                            live,
                            required_components=required_manifest_components(
                                checked_manifest
                            ),
                        )
                        active_identity = _integrity_identity(
                            live,
                            candidate_identity,
                            target_database,
                            mismatch_reason="database_migration_interrupted_swap_mismatch",
                        )
                    live_receipt = create_database_live_receipt(
                        checked_manifest,
                        active_identity,
                        installed_at=_now(),
                    )
                    _write_json_atomic(manifest_path, checked_manifest)
                    _write_json_atomic(receipt_path, live_receipt)
                    _journal(
                        journal_path,
                        operation_id=previous["operation_id"],
                        target_release_id=previous["target_release_id"],
                        state="postchecked",
                        production=production,
                        old_sha256=previous.get("old_sha256"),
                        target_package_sha256=previous[
                            "target_package_sha256"
                        ],
                        target_manifest_fingerprint=target_manifest_fingerprint,
                        active_sha256=active_identity["sha256"],
                        staging_name=previous.get("staging_name"),
                        rollback_name=previous.get("rollback_name"),
                        rollback_receipt_name=previous.get(
                            "rollback_receipt_name"
                        ),
                        rollback_manifest_name=previous.get(
                            "rollback_manifest_name"
                        ),
                        old_receipt_present=previous.get(
                            "old_receipt_present", False
                        ),
                        old_manifest_present=previous.get(
                            "old_manifest_present", False
                        ),
                        live_receipt_fingerprint=live_receipt[
                            "receipt_fingerprint"
                        ],
                        live_manifest_fingerprint=target_manifest_fingerprint,
                    )
            except (DatabaseMigrationError, DatabaseReleaseError, LockBusyError):
                active_identity = None
        if active_identity is not None:
            resumed = _read_journal(journal_path)
            if resumed is None:
                _fail("database_migration_recovery_required")
            active_identity = _finish_accepted_journal(
                data_dir=data_dir,
                live=live,
                journal_path=journal_path,
                receipt_path=receipt_path,
                manifest_path=manifest_path,
                previous=resumed,
                checked_manifest=checked_manifest,
                qualification=qualification,
            )
            return {
                "status": "already_current",
                "scope": "database_subtransaction",
                "pair_operational": False,
                "target_release_id": target_release_id,
                "active_sha256": active_identity["sha256"],
                "production": production,
                "recovered_cleanup": True,
                "recovered_interrupted_swap": True,
                "rollback_retained": bool(
                    previous.get("rollback_name") and not production
                ),
            }
    if previous is not None and previous["state"] not in _TERMINAL_JOURNAL_STATES:
        _fail(
            "database_migration_recovery_required",
            "上次数据库迁移尚未收敛",
            {"state": previous["state"]},
        )
    if (
        previous is not None
        and previous["state"] == "cleanup_complete"
        and previous["target_release_id"] == target_release_id
        and live.is_file()
    ):
        if qualification is not None:
            current_identity = read_qualified_database_identity(
                live, checked_manifest, qualification,
            )
        else:
            current_identity = _validate_database(
                live,
                required_components=required_manifest_components(checked_manifest),
            )
            current_identity = _integrity_identity(
                live,
                current_identity,
                target_database,
                mismatch_reason="database_migration_active_release_core_mismatch",
            )
        receipt = _read_live_receipt(receipt_path)
        live_manifest = _read_live_manifest(manifest_path)
        try:
            receipt_matches = receipt is not None and database_live_receipt_matches(
                receipt, current_identity, checked_manifest
            )
        except DatabaseReleaseError:
            receipt_matches = False
        if (
            receipt is not None
            and live_manifest == checked_manifest
            and receipt_matches
        ):
            return {
                "status": "already_current",
                "scope": "database_subtransaction",
                "pair_operational": False,
                "target_release_id": target_release_id,
                "active_sha256": current_identity["sha256"],
                "production": production,
                "recovered_cleanup": False,
            }
        _fail(
            "database_migration_recovery_required",
            "数据库与安装receipt不一致，需要人工恢复",
            {"state": previous["state"]},
        )

    prepared = prepare_installation_artifacts(
        data_dir=data_dir, live=live, build=build, package_identity=package_identity,
        production=production, receipt_path=receipt_path, manifest_path=manifest_path,
        minimum_headroom_bytes=MINIMUM_HEADROOM_BYTES,
        sqlite_work_allowance_bytes=SQLITE_WORK_ALLOWANCE_BYTES,
        ports=_migration_stage_ports(),
        current_qualification=current_qualification,
    )
    previous_receipt_bytes = prepared.previous_receipt_bytes
    previous_manifest_bytes = prepared.previous_manifest_bytes
    operation_id = prepared.operation_id
    staging = prepared.staging
    rollback_dir = prepared.rollback_dir
    rollback = prepared.rollback
    rollback_receipt = prepared.rollback_receipt
    rollback_manifest = prepared.rollback_manifest
    old_receipt_present = prepared.old_receipt_present
    old_manifest_present = prepared.old_manifest_present
    old_sha = prepared.old_sha
    live_receipt = None
    publication_attempted = False
    accepted = False
    rollback_recorded = False
    recovery_ambiguous = False
    _journal(
        journal_path,
        operation_id=operation_id,
        target_release_id=target_release_id,
        state="prepared",
        production=production,
        old_sha256=old_sha,
        target_package_sha256=package_identity["sha256"],
        target_manifest_fingerprint=target_manifest_fingerprint,
        staging_name=staging.name,
    )
    try:
        _fault(fault_hook, "before_stage_copy")
        copy_and_verify_package(
            package=package, staging=staging,
            target_database=target_database, ports=_migration_stage_ports(),
            qualification=qualification,
        )
        _journal(
            journal_path,
            operation_id=operation_id,
            target_release_id=target_release_id,
            state="validated",
            production=production,
            old_sha256=old_sha,
            target_package_sha256=package_identity["sha256"],
            target_manifest_fingerprint=target_manifest_fingerprint,
            staging_name=staging.name,
            old_receipt_present=old_receipt_present,
            old_manifest_present=old_manifest_present,
        )
        _fault(fault_hook, "after_candidate_validation")
        with database_lock(live, timeout=0):
            # The preflight hash is advisory.  This is the authoritative old
            # identity after all earlier writers have drained from the fence.
            snapshot = snapshot_current_release(
                live=live, manifest_path=manifest_path, receipt_path=receipt_path,
                old_manifest_present=old_manifest_present, rollback_dir=rollback_dir,
                rollback=rollback, rollback_receipt=rollback_receipt,
                rollback_manifest=rollback_manifest,
                previous_receipt_bytes=previous_receipt_bytes,
                previous_manifest_bytes=previous_manifest_bytes,
                ports=_migration_stage_ports(),
                current_qualification=current_qualification,
            )
            old_sha, rollback_created = snapshot.old_sha, snapshot.rollback_created
            _fault(fault_hook, "after_rollback_snapshot")
            # The staged file is the complete reviewed release.  Never merge
            # project rows from the active database here: that would change a
            # package after its manifest was approved and would make the
            # installed content depend on timing.  Approved project-content
            # changes are applied while building the candidate and are covered
            # by its release manifest before this installer is called.
            validate_prepublication_candidate(
                staging=staging,
                target_database=target_database, ports=_migration_stage_ports(),
            )
            _journal(
                journal_path,
                operation_id=operation_id,
                target_release_id=target_release_id,
                state="swap_started",
                production=production,
                old_sha256=old_sha,
                target_package_sha256=package_identity["sha256"],
                target_manifest_fingerprint=target_manifest_fingerprint,
                staging_name=staging.name,
                rollback_name=rollback.name if rollback_created else None,
                rollback_receipt_name=(
                    rollback_receipt.name if old_receipt_present else None
                ),
                rollback_manifest_name=(
                    rollback_manifest.name if old_manifest_present else None
                ),
                old_receipt_present=old_receipt_present,
                old_manifest_present=old_manifest_present,
            )
            _fault(fault_hook, "before_replace")
            try:
                publication_attempted = True
                os.replace(staging, live)
                _fsync_directory(data_dir)
                _fault(fault_hook, "after_replace")
                active_identity = postcheck_active_database(
                    live=live, package_identity=package_identity,
                    target_database=target_database, ports=_migration_stage_ports(),
                    qualification=qualification, manifest=checked_manifest,
                )
                _fault(fault_hook, "after_active_postcheck")
                live_receipt = prepare_acceptance_receipt(
                    checked_manifest=checked_manifest, active_identity=active_identity,
                    ports=_migration_stage_ports(),
                )
                _write_json_atomic(manifest_path, checked_manifest)
                _fault(fault_hook, "after_manifest_write")
                _write_json_atomic(receipt_path, live_receipt)
                _fault(fault_hook, "after_receipt_write")
                _journal(
                    journal_path,
                    operation_id=operation_id,
                    target_release_id=target_release_id,
                    state="postchecked",
                    production=production,
                    old_sha256=old_sha,
                    target_package_sha256=package_identity["sha256"],
                    target_manifest_fingerprint=target_manifest_fingerprint,
                    active_sha256=active_identity["sha256"],
                    staging_name=staging.name,
                    rollback_name=rollback.name if rollback_created else None,
                    rollback_receipt_name=(
                        rollback_receipt.name if old_receipt_present else None
                    ),
                    rollback_manifest_name=(
                        rollback_manifest.name if old_manifest_present else None
                    ),
                    old_receipt_present=old_receipt_present,
                    old_manifest_present=old_manifest_present,
                    live_receipt_fingerprint=live_receipt["receipt_fingerprint"],
                    live_manifest_fingerprint=target_manifest_fingerprint,
                )
                # From this point the active-path validation has durably passed.
                # Once the fence opens, no failure may replace this file again.
                accepted = True
            except BaseException as error:
                if publication_attempted:
                    try:
                        if current_qualification is not None:
                            _verify_package_bytes(
                                rollback,
                                {"sha256": current_qualification.sha256,
                                 "size_bytes": current_qualification.size_bytes},
                                reason="database_migration_restored_identity_mismatch",
                            )
                        _restore_rollback(
                            live=live,
                            rollback=rollback,
                            keep_rollback=not production,
                        )
                        _restore_sidecar(receipt_path, previous_receipt_bytes)
                        _restore_sidecar(manifest_path, previous_manifest_bytes)
                        if current_qualification is not None:
                            restored_manifest = _read_live_manifest(manifest_path)
                            if restored_manifest is None:
                                _fail("database_migration_restored_identity_mismatch")
                            restored_sha = read_qualified_database_identity(
                                live, restored_manifest, current_qualification,
                            )["sha256"]
                        else:
                            restored_sha = _sha256(live) if live.exists() else None
                        _journal(
                            journal_path,
                            operation_id=operation_id,
                            target_release_id=target_release_id,
                            state="rolled_back",
                            production=production,
                            old_sha256=old_sha,
                            target_package_sha256=package_identity["sha256"],
                            target_manifest_fingerprint=target_manifest_fingerprint,
                            active_sha256=restored_sha,
                            staging_name=staging.name,
                            rollback_name=(
                                rollback.name
                                if rollback.exists() and not production
                                else None
                            ),
                            rollback_receipt_name=(
                                rollback_receipt.name
                                if rollback_receipt.exists() and not production
                                else None
                            ),
                            rollback_manifest_name=(
                                rollback_manifest.name
                                if rollback_manifest.exists() and not production
                                else None
                            ),
                            old_receipt_present=old_receipt_present,
                            old_manifest_present=old_manifest_present,
                            error_reason=getattr(
                                error, "reason", type(error).__name__
                            ),
                        )
                        rollback_recorded = True
                    except BaseException as rollback_error:
                        recovery_ambiguous = True
                        raise DatabaseMigrationError(
                            "database_migration_recovery_required",
                            "数据库发布失败且自动回滚未完成",
                            {
                                "migration_error": getattr(
                                    error, "reason", type(error).__name__
                                ),
                                "rollback_error": type(rollback_error).__name__,
                            },
                        ) from rollback_error
                raise

        # The writer fence has reopened.  The accepted database remains
        # immutable; cleanup faults are resumable and never trigger rollback
        # because active-path validation and publication already completed.
        _fault(fault_hook, "after_commit")
        _journal(
            journal_path,
            operation_id=operation_id,
            target_release_id=target_release_id,
            state="operational",
            production=production,
            old_sha256=old_sha,
            target_package_sha256=package_identity["sha256"],
            target_manifest_fingerprint=target_manifest_fingerprint,
            active_sha256=active_identity["sha256"],
            staging_name=staging.name,
            rollback_name=rollback.name if rollback_created else None,
            rollback_receipt_name=(
                rollback_receipt.name if old_receipt_present else None
            ),
            rollback_manifest_name=(
                rollback_manifest.name if old_manifest_present else None
            ),
            old_receipt_present=old_receipt_present,
            old_manifest_present=old_manifest_present,
            live_receipt_fingerprint=live_receipt["receipt_fingerprint"],
            live_manifest_fingerprint=target_manifest_fingerprint,
        )
        _fault(fault_hook, "before_cleanup")
        cleanup_installation_artifacts(
            staging=staging, rollback=rollback, rollback_receipt=rollback_receipt,
            rollback_manifest=rollback_manifest, production=production,
        )
        _journal(
            journal_path,
            operation_id=operation_id,
            target_release_id=target_release_id,
            state="cleanup_complete",
            production=production,
            old_sha256=old_sha,
            target_package_sha256=package_identity["sha256"],
            target_manifest_fingerprint=target_manifest_fingerprint,
            active_sha256=active_identity["sha256"],
            rollback_name=(rollback.name if rollback_created and not production else None),
            rollback_receipt_name=(
                rollback_receipt.name
                if old_receipt_present and not production
                else None
            ),
            rollback_manifest_name=(
                rollback_manifest.name
                if old_manifest_present and not production
                else None
            ),
            old_receipt_present=old_receipt_present,
            old_manifest_present=old_manifest_present,
            live_receipt_fingerprint=live_receipt["receipt_fingerprint"],
            live_manifest_fingerprint=target_manifest_fingerprint,
        )
        return {
            "status": "installed",
            "scope": "database_subtransaction",
            "pair_operational": False,
            "target_release_id": target_release_id,
            "active_sha256": active_identity["sha256"],
            "old_sha256": old_sha,
            "manual_content_fingerprint": active_identity.get(
                "manual_content_fingerprint"
            ),
            "mutable_layer_fingerprint": active_identity[
                "mutable_layer_fingerprint"
            ],
            "live_manifest_fingerprint": target_manifest_fingerprint,
            "production": production,
            "rollback_retained": bool(rollback_created and not production),
            "live_receipt": copy.deepcopy(live_receipt),
        }
    except BaseException as error:
        if accepted or recovery_ambiguous:
            raise
        if not rollback_recorded:
            try:
                _journal(
                    journal_path,
                    operation_id=operation_id,
                    target_release_id=target_release_id,
                    state="rolled_back",
                    production=production,
                    old_sha256=old_sha,
                    target_package_sha256=package_identity["sha256"],
                    target_manifest_fingerprint=target_manifest_fingerprint,
                    active_sha256=old_sha,
                    staging_name=staging.name,
                    rollback_name=(
                        rollback.name if rollback.exists() and not production else None
                    ),
                    rollback_receipt_name=(
                        rollback_receipt.name
                        if rollback_receipt.exists() and not production
                        else None
                    ),
                    rollback_manifest_name=(
                        rollback_manifest.name
                        if rollback_manifest.exists() and not production
                        else None
                    ),
                    old_receipt_present=old_receipt_present,
                    old_manifest_present=old_manifest_present,
                    error_reason=getattr(error, "reason", type(error).__name__),
                )
            except Exception as journal_error:
                raise DatabaseMigrationError(
                    "database_migration_recovery_required",
                    "数据库保持旧版本，但迁移日志未能收敛",
                    {"migration_error": getattr(error, "reason", type(error).__name__)},
                ) from journal_error
        cleanup_installation_artifacts(
            staging=staging, rollback=rollback, rollback_receipt=rollback_receipt,
            rollback_manifest=rollback_manifest, production=production,
            staging_first=True,
        )
        raise


def abandon_unpublished_database_attempt(
    data_dir: Path, manifest: Mapping[str, Any], *, expected_old_sha256: str | None,
) -> bool:
    """Settle a crashed prepublication attempt only when the old file survived.

    Used by an explicitly confirmed fixed-package recovery. This neither
    replaces the live database nor fabricates an accepted migration. Accepted
    and interrupted-swap journals remain owned by the existing installer.
    """
    directory = Path(data_dir).resolve()
    with file_lock(directory / UPDATE_LOCK_FILE, timeout=0), database_lock(directory / "genshin.db", timeout=0):
        previous = _read_journal(directory / JOURNAL_FILE)
        if previous is None or previous["state"] not in {"prepared", "validated"}:
            return False
        checked = validate_database_release_manifest(dict(manifest))
        if (previous["target_release_id"] != checked["database_release_id"]
                or previous["target_package_sha256"] != checked["database"]["sha256"]
                or previous.get("target_manifest_fingerprint") != _fingerprint(checked)
                or previous.get("old_sha256") != expected_old_sha256):
            _fail("database_migration_recovery_required", "未完成操作的来源或目标已变化。")
        live = directory / "genshin.db"
        actual_sha = _sha256(live) if live.exists() else None
        if actual_sha != expected_old_sha256:
            _fail("database_migration_recovery_required", "活动数据库已变化，不能放弃该尝试。")
        staging = _journal_artifact(directory, previous, "staging_name")
        if staging is not None and staging.exists():
            expected_name = f"database-release-{previous['operation_id']}.db"
            if (staging.name != expected_name or staging.is_symlink()
                    or staging.resolve().parent != directory / "build"):
                _fail("database_migration_journal_invalid", "暂存路径不属于本次操作。")
            staging.unlink()
        settled = dict(previous)
        settled.update(state="rolled_back", staging_name=None,
            active_sha256=actual_sha, error_reason="unpublished_attempt_abandoned_after_confirmation",
            updated_at=_now())
        _write_json_atomic(directory / JOURNAL_FILE, settled)
        return True


def install_database_release(
    data_dir: Path,
    package_database: Path,
    manifest: Mapping[str, Any],
    *,
    logic_profile: str,
    data_contract_version: int,
    user_confirmed: bool,
    production: bool,
    app_release_id: str | None = None,
    release_pair_id: str | None = None,
    required_database_release_id: str | None = None,
    pair_policy: str = "exact",
    app_version: str | None = None,
    app_artifact_manifest_sha256: str | None = None,
    pair_manifest: Mapping[str, Any] | None = None,
    fault_hook: Callable[[str], None] | None = None,
    qualification: DatabaseInstallQualification | None = None,
    current_qualification: DatabaseInstallQualification | None = None,
) -> dict[str, Any]:
    """Install one verified database as a database-only subtransaction.

    The caller must obtain user consent before setting ``user_confirmed=True``.
    This function never performs network I/O and never chooses a package.
    It persists the accepted database manifest and receipt, but deliberately
    cannot create ``release-pair-live-manifest.json`` or declare a full pair
    operational.  Only an outer coordinator holding the verified App artifact
    and ReleasePairManifest has authority to commit that pair-level identity.
    """

    if user_confirmed is not True:
        _fail("database_migration_confirmation_required", "数据库升级需要用户确认")
    if type(production) is not bool:
        _fail("database_migration_mode_invalid")
    if not production and (qualification is not None or current_qualification is not None):
        _fail("database_install_qualification_requires_production")
    try:
        checked_manifest = validate_database_release_manifest(dict(manifest))
    except DatabaseReleaseError as error:
        raise DatabaseMigrationError(error.reason, str(error)) from error
    if production:
        if (
            pair_policy != "exact"
            or checked_manifest["channel"] != "stable"
            or pair_manifest is None
        ):
            _fail(
                "database_migration_production_pair_required",
                "正式数据库子事务必须由stable/exact版本对授权",
            )
        try:
            checked_pair = validate_release_pair_manifest(dict(pair_manifest))
            pair_matches = release_pair_matches_database_manifest(
                checked_pair, checked_manifest
            )
        except DatabaseReleaseError as error:
            raise DatabaseMigrationError(error.reason, str(error)) from error
        if (
            not pair_matches
            or checked_pair["channel"] != "stable"
            or checked_pair["pair_policy"] != "exact"
            or checked_pair["release_pair_id"] != release_pair_id
            or checked_pair["app"]["app_release_id"] != app_release_id
            or checked_pair["app"]["app_version"] != app_version
            or checked_pair["app"]["artifact_manifest"]["sha256"]
            != app_artifact_manifest_sha256
            or checked_pair["app"]["required_database_release_id"]
            != required_database_release_id
        ):
            _fail(
                "database_migration_production_pair_mismatch",
                "正式数据库子事务与App/数据库版本对不一致",
            )
    if not manifest_supports_app(
        checked_manifest,
        logic_profile=logic_profile,
        data_contract_version=data_contract_version,
        app_release_id=app_release_id,
        release_pair_id=release_pair_id,
        required_database_release_id=required_database_release_id,
        pair_policy=pair_policy,
        app_version=app_version,
    ):
        _fail(
            "database_migration_release_pair_incompatible",
            "数据库发行包与当前App逻辑不兼容",
        )
    if qualification is not None or current_qualification is not None:
        data_dir = Path(os.path.abspath(data_dir))
        try:
            check_qualified_destination(data_dir / "build")
        except DatabaseReleaseError as error:
            raise DatabaseMigrationError(error.reason, str(error)) from error
    else:
        data_dir = Path(data_dir).resolve()
    package = (Path(os.path.abspath(package_database)) if qualification is not None
               else Path(package_database).resolve())
    live = data_dir / "genshin.db"
    if package == live or not package.is_file():
        _fail("database_migration_package_invalid", "数据库发行包不存在或指向在用库")
    data_dir.mkdir(parents=True, exist_ok=True)
    build = data_dir / "build"
    build.mkdir(parents=True, exist_ok=True)
    journal_path = data_dir / JOURNAL_FILE
    target_database = checked_manifest["database"]
    if qualification is not None:
        try:
            package_identity = qualified_database_identity(checked_manifest, qualification)
        except DatabaseReleaseError as error:
            raise DatabaseMigrationError(error.reason, str(error)) from error
    else:
        _verify_package_bytes(
            package, target_database, reason="database_migration_package_identity_mismatch",
        )
        package_identity = _validate_database(
            package,
            required_components=required_manifest_components(checked_manifest),
        )
        if not _manifest_database_matches(package_identity, target_database):
            _fail(
                "database_migration_package_identity_mismatch",
                "数据库发行包与manifest身份不一致",
            )
        package_identity = _integrity_identity(
            package,
            package_identity,
            target_database,
            mismatch_reason="database_migration_package_release_core_mismatch",
        )
        _verify_package_bytes(
            package, target_database, reason="database_migration_package_identity_mismatch",
        )

    update_lock = data_dir / UPDATE_LOCK_FILE
    qualification_arguments = {}
    if qualification is not None or current_qualification is not None:
        qualification_arguments = {
            "qualification": qualification,
            "current_qualification": current_qualification,
        }
    try:
        with file_lock(update_lock, timeout=0):
            return _install_locked(
                data_dir=data_dir,
                package=package,
                live=live,
                build=build,
                journal_path=journal_path,
                checked_manifest=checked_manifest,
                package_identity=package_identity,
                production=production,
                fault_hook=fault_hook,
                **qualification_arguments,
            )
    except DatabaseReleaseError as error:
        raise DatabaseMigrationError(error.reason, str(error)) from error
    except LockBusyError as error:
        raise DatabaseMigrationError(
            "database_migration_busy",
            "数据库正在写入或升级，请稍后重试",
        ) from error
