#!/usr/bin/env python3
"""Crash-safe production migration for the reviewed action-admission v2 seed.

This module is intentionally tracked under ``scripts/maintenance``.  The
transaction coordinator used to live in a disposable ``outputs`` directory,
which made the rollback safety tests depend on build debris.  Keeping the
coordinator here makes the locking, SHA-chain and recovery contract part of the
maintained product source.

The normal entry point is :func:`_apply`.  Worker entry points are separate so
that a crash in import/verification cannot make the parent process mistake an
incomplete migration for success.  Every destructive restore is guarded by the
database lock, a byte-exact backup digest, the set of known phase digests, and
the absence of live SQLite sidecars.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from genshin_db.locking import LockBusyError, database_lock, file_lock
from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import import_bundle, read_record, validate_component
from scripts.build_action_admission_v2_mechanism_bundle import (
    NEW_ID,
    OLD_ID,
)
from scripts.build_action_admission_v2_mechanism_bundle import (
    build as build_pending_bundle,
)

DIRECTORY = ROOT / "data" / ".maintenance" / "action-admission-v2"
DATABASE = ROOT / "data" / "genshin.db"
BACKUP_DIRECTORY = DIRECTORY / "backups"
FORMAL_REPORT = DIRECTORY / "formal-production.json"
FAILURE_REPORT = DIRECTORY / "formal-production-failure.json"
DRY_RUN_REPORT = DIRECTORY / "formal-production-dry-run.json"
PENDING = DIRECTORY / "formal-production-pending.json"
COMPLETE_STATE = DIRECTORY / "formal-production-complete.json"
MIGRATION_LOCK = DIRECTORY / "formal-production.lock"
CLEANUP_REPORT = DIRECTORY / "formal-production-cleanup.json"

BASELINE_SHA256: str | None = None
_OPERATION = re.compile(r"[A-Za-z0-9._-]{1,96}\Z")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text("utf-8"))
    if type(value) is not dict:
        raise RuntimeError(f"state document is not an object: {Path(path).name}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _operation(value: str) -> str:
    if not isinstance(value, str) or not _OPERATION.fullmatch(value):
        raise RuntimeError("invalid production migration operation id")
    return value


def _sidecars(database: Path | None = None) -> list[Path]:
    database = Path(database or DATABASE)
    return [
        candidate
        for suffix in ("-journal", "-wal", "-shm")
        if (candidate := database.with_name(database.name + suffix)).exists()
    ]


def _checkpoint(_name: str) -> None:
    """Fault-injection hook used by crash-safety tests."""


def _approved_bundle(database: Path) -> dict[str, Any]:
    """Derive v2 from the exact approved v1 evidence already in the database."""

    seed = copy.deepcopy(build_pending_bundle())
    with closing(sqlite3.connect(database)) as connection:
        previous_descriptor = read_record(
            connection, "release", "descriptor", OLD_ID
        )
        previous_qualification = read_record(
            connection,
            "release",
            "fact",
            OLD_ID + ".qualification",
        )
    if previous_descriptor.get("review", {}).get("status") != "approved":
        raise RuntimeError("reviewed v1 descriptor is unavailable")
    if previous_qualification.get("review", {}).get("status") != "approved":
        raise RuntimeError("reviewed v1 qualification is unavailable")

    qualification = seed["facts"][0]
    descriptor = seed["descriptors"][0]
    qualification["evidence"] = copy.deepcopy(previous_qualification["evidence"])
    qualification["applicability"] = copy.deepcopy(
        previous_qualification["applicability"]
    )
    descriptor["evidence"] = copy.deepcopy(previous_descriptor["evidence"])
    descriptor["applicability"] = copy.deepcopy(previous_descriptor["applicability"])
    descriptor["payload"]["source_binding"] = copy.deepcopy(
        previous_descriptor["payload"]["source_binding"]
    )
    for record in (qualification, descriptor):
        record["review"] = {
            "status": "approved",
            "reviewer": "local-independent-design-review-r3",
            "reviewed_at": "2026-10-15T00:00:00+00:00",
        }
    return seed


def _bundle() -> dict[str, Any]:
    return _approved_bundle(DATABASE)


def _verification_checks(database: Path) -> dict[str, bool]:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        component = validate_component(connection, "release")
    legacy = query_runtime(
        database,
        {"action": "mechanism_profile", "mechanism_id": OLD_ID},
    )
    current = query_runtime(
        database,
        {"action": "mechanism_profile", "mechanism_id": NEW_ID},
    )
    # Reviewed semantic records are knowledge candidates.  Since D1 removed
    # their former direct execution authority, verification must prove both
    # that the records remain readable and that no compiler result is silently
    # resurrected.  Agent materialization is the only production route from
    # these descriptors to executable inputs.
    legacy_descriptor = legacy.get("descriptor") or {}
    current_descriptor = current.get("descriptor") or {}
    return {
        "sqlite_integrity": _sqlite_integrity(database),
        "semantic_component_ready": component.get("status") == "ready",
        "legacy_profile_available": (
            legacy_descriptor.get("mechanism_id") == OLD_ID
            and legacy.get("materialization_status") == "pending"
        ),
        "legacy_profile_non_executable": (
            (legacy.get("compiler_result") or {}).get("status") == "pending"
            and (legacy.get("compiler_result") or {}).get("execution_status")
            == "not_evaluated"
            and (legacy.get("compiler_result") or {}).get("execution_qualification")
            is None
        ),
        "v2_profile_available": (
            current_descriptor.get("mechanism_id") == NEW_ID
            and current.get("materialization_status") == "pending"
        ),
        "v2_profile_non_executable": (
            (current.get("compiler_result") or {}).get("status") == "pending"
            and (current.get("compiler_result") or {}).get("execution_status")
            == "not_evaluated"
            and (current.get("compiler_result") or {}).get("execution_qualification")
            is None
        ),
    }


def _sqlite_integrity(database: Path) -> bool:
    with closing(sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)) as connection:
        return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def _verify_database() -> dict[str, Any]:
    with database_lock(DATABASE):
        before = _sha(DATABASE)
        checks = _verification_checks(DATABASE)
        after = _sha(DATABASE)
    return {
        "contract": "formal-production-action-admission-v2-verification-v1",
        "verified_at": _now(),
        "database_sha256_before": before,
        "database_sha256_after": after,
        "checks": checks,
    }


def _require_verification(report: dict[str, Any]) -> None:
    checks = report.get("checks")
    if type(checks) is not dict or not checks or not all(value is True for value in checks.values()):
        raise RuntimeError("formal verification checks failed")
    before = report.get("database_sha256_before")
    after = report.get("database_sha256_after")
    if not isinstance(before, str) or before != after:
        raise RuntimeError("verification changed the database SHA chain")


def _backup_path(operation: str) -> Path:
    return BACKUP_DIRECTORY / f"action-admission-v2-r1-{_operation(operation)}.db"


def _create_backup(path: Path) -> dict[str, Any]:
    path = Path(path)
    if _sidecars():
        raise RuntimeError("SQLite sidecars exist before production backup")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(DATABASE, temporary)
        source_sha = _sha(DATABASE)
        backup_sha = _sha(temporary)
        if source_sha != backup_sha:
            raise RuntimeError("production backup SHA mismatch")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "contract": "formal-production-byte-backup-v1",
        "path": str(path.resolve()),
        "sha256": backup_sha,
        "bytes": path.stat().st_size,
        "database_path": str(DATABASE.resolve()),
        "created_at": _now(),
    }


def _known_shas(state: dict[str, Any]) -> set[str]:
    result = {
        state[key]
        for key in (
            "database_sha256_before",
            "database_sha256_after_import",
            "database_sha256_final",
        )
        if isinstance(state.get(key), str)
    }
    values = state.get("known_live_sha256", [])
    if isinstance(values, list):
        result.update(value for value in values if isinstance(value, str))
    return result


def _validate_backup(state: dict[str, Any]) -> tuple[Path, str]:
    backup = state.get("backup")
    if type(backup) is not dict:
        raise RuntimeError("rollback state is missing its backup")
    path_value = backup.get("path")
    expected = backup.get("sha256")
    if not isinstance(path_value, str) or not isinstance(expected, str):
        raise TypeError("rollback backup identity is invalid")
    path = Path(path_value).resolve()
    allowed_parents = {BACKUP_DIRECTORY.resolve(), DATABASE.resolve().parent}
    if path.parent not in allowed_parents or not path.is_file():
        raise RuntimeError("rollback backup is outside the owned directory")
    contract = backup.get("contract")
    database_path = backup.get("database_path")
    if contract is not None and contract != "formal-production-byte-backup-v1":
        raise RuntimeError("rollback backup contract is invalid")
    if database_path is not None and Path(database_path).resolve() != DATABASE.resolve():
        raise RuntimeError("rollback backup belongs to another database")
    if _sha(path) != expected:
        raise RuntimeError("rollback backup SHA mismatch")
    return path, expected


def _persist_pending(state: dict[str, Any], pending: Path = PENDING) -> None:
    state["updated_at"] = _now()
    _write_json(pending, state)


def _coordinator_worker(
    pending: Path,
    verification_path: Path,
    operation: str,
) -> dict[str, Any]:
    pending = Path(pending)
    verification_path = Path(verification_path)
    operation = _operation(operation)
    with database_lock(DATABASE):
        if _sidecars():
            raise RuntimeError("SQLite sidecars exist before production migration")
        baseline = _sha(DATABASE)
        if BASELINE_SHA256 is not None and baseline != BASELINE_SHA256:
            raise RuntimeError("production baseline SHA mismatch")
        state: dict[str, Any] = {
            "contract": "formal-production-action-admission-v2-state-v1",
            "operation_id": operation,
            "state": "started",
            "started_at": _now(),
            "database_sha256_before": baseline,
            "known_live_sha256": [baseline],
            "verification_path": str(verification_path.resolve()),
        }
        _persist_pending(state, pending)

        state["backup"] = _create_backup(_backup_path(operation))
        state["state"] = "backup_complete"
        _persist_pending(state, pending)
        _checkpoint("after_backup")

        imported = import_bundle(
            DATABASE,
            _bundle(),
            version_line="release",
            dry_run=False,
        )
        if imported.get("applied") is not True or imported.get("status") != "ready":
            raise RuntimeError("production semantic import was not applied")
        imported_sha = _sha(DATABASE)
        state["import"] = imported
        state["database_sha256_after_import"] = imported_sha
        state["known_live_sha256"] = list(dict.fromkeys([baseline, imported_sha]))
        state["state"] = "import_complete"
        _persist_pending(state, pending)
        _checkpoint("after_import")

        verification = _verify_database()
        _require_verification(verification)
        _write_json(verification_path, verification)
        state["verification_sha256"] = _sha(verification_path)
        state["state"] = "verification_complete"
        _persist_pending(state, pending)
        _checkpoint("after_verify")

        final_live = _sha(DATABASE)
        expected_live = verification["database_sha256_after"]
        if expected_live != imported_sha or final_live != expected_live:
            state["state"] = "external_write_conflict"
            state["database_sha256_external"] = final_live
            _persist_pending(state, pending)
            raise RuntimeError("production migration SHA chain changed after verification")

        chain = {
            "after_import": imported_sha,
            "verification_before": verification["database_sha256_before"],
            "verification_after": verification["database_sha256_after"],
            "final_live": final_live,
        }
        report = {
            "contract": "formal-production-action-admission-v2-report-v1",
            "operation_id": operation,
            "state": "complete",
            "completed_at": _now(),
            "database_sha256_before": baseline,
            "database_sha256_final": final_live,
            "backup": copy.deepcopy(state["backup"]),
            "verification_path": str(verification_path.resolve()),
            "verification_sha256": state["verification_sha256"],
            "sha_chain": chain,
            "import": imported,
        }
        _write_json(FORMAL_REPORT, report)
        _checkpoint("after_report")

        state.update(
            state="complete",
            completed_at=report["completed_at"],
            database_sha256_final=final_live,
            formal_report_sha256=_sha(FORMAL_REPORT),
            sha_chain=chain,
        )
        _persist_pending(state, pending)
        _checkpoint("after_complete")
        return report


def _restore_worker(pending: Path, output: Path) -> dict[str, Any]:
    pending = Path(pending)
    output = Path(output)
    state = _load(pending)
    operation = _operation(state.get("operation_id"))
    with database_lock(DATABASE):
        current = _sha(DATABASE)
        report: dict[str, Any] = {
            "contract": "formal-production-action-admission-v2-rollback-v1",
            "operation_id": operation,
            "restored": False,
            "database_sha256_before_restore": current,
            "created_at": _now(),
        }
        sidecars = [str(path.resolve()) for path in _sidecars()]
        if sidecars:
            report["sidecars"] = sidecars
            _write_json(output, report)
            raise RuntimeError("automatic rollback refused because SQLite sidecars exist")
        if current not in _known_shas(state):
            report["known_live_sha256"] = sorted(_known_shas(state))
            _write_json(output, report)
            raise RuntimeError("automatic rollback refused for unknown live SHA")
        backup_path, backup_sha = _validate_backup(state)
        temporary = DATABASE.with_name(
            f".{DATABASE.name}.restore-{operation}-{uuid.uuid4().hex}.tmp"
        )
        try:
            shutil.copy2(backup_path, temporary)
            if _sha(temporary) != backup_sha:
                raise RuntimeError("temporary rollback copy SHA mismatch")
            try:
                os.replace(temporary, DATABASE)
            except PermissionError as error:
                report["temporary_path"] = str(temporary.resolve())
                report["error"] = "windows_handle"
                _write_json(output, report)
                raise RuntimeError(
                    "automatic rollback failed because a Windows handle owns the database"
                ) from error
            restored_sha = _sha(DATABASE)
            if restored_sha != backup_sha:
                raise RuntimeError("restored database SHA mismatch")
            report.update(restored=True, database_sha256=restored_sha)
            state.update(
                state="rollback_complete",
                database_sha256_final=restored_sha,
                rollback=copy.deepcopy(report),
            )
            _persist_pending(state, pending)
            _write_json(output, report)
            return report
        finally:
            # Preserve a failed replacement candidate as evidence; successful
            # replacements consume it through os.replace.
            if temporary.exists() and report.get("error") != "windows_handle":
                temporary.unlink(missing_ok=True)


def _owned_verification(state: dict[str, Any]) -> Path | None:
    value = state.get("verification_path")
    if isinstance(value, str):
        path = Path(value).resolve()
    else:
        operation = state.get("operation_id")
        if not isinstance(operation, str):
            return None
        path = (DIRECTORY / f".formal-production-verify-{operation}.json").resolve()
    return path if path.parent == DIRECTORY.resolve() else None


def _archive_complete_state(pending: Path = PENDING) -> dict[str, Any]:
    pending = Path(pending)
    state = _load(pending)
    if state.get("state") != "complete":
        raise RuntimeError("only a complete migration state can be archived")
    COMPLETE_STATE.parent.mkdir(parents=True, exist_ok=True)
    os.replace(pending, COMPLETE_STATE)
    return state


def _cleanup_backup(state: dict[str, Any], *, recovered: bool) -> dict[str, Any]:
    backup = state.get("backup")
    if type(backup) is not dict:
        raise RuntimeError("cleanup state is missing its backup")
    path_value = backup.get("path")
    backup_sha = backup.get("sha256")
    if not isinstance(path_value, str) or not isinstance(backup_sha, str):
        raise TypeError("cleanup backup identity is invalid")
    backup_path = Path(path_value).resolve()
    if backup_path.parent not in {
        BACKUP_DIRECTORY.resolve(),
        DATABASE.resolve().parent,
    }:
        raise RuntimeError("cleanup backup is outside the owned directory")
    result: dict[str, Any] = {
        "contract": "formal-production-action-admission-v2-cleanup-v1",
        "operation_id": state.get("operation_id"),
        "recovered": recovered,
        "backup_sha256": backup_sha,
        "created_at": _now(),
    }
    if not backup_path.exists():
        result.update(status="clean", already_absent=True)
        _write_json(CLEANUP_REPORT, result)
        return result
    if _sha(backup_path) != backup_sha:
        raise RuntimeError("cleanup backup SHA mismatch")
    try:
        backup_path.unlink()
        result["status"] = "clean"
    except OSError as error:
        result.update(
            status="cleanup_pending",
            error_type=type(error).__name__,
            winerror=getattr(error, "winerror", None),
        )
    _write_json(CLEANUP_REPORT, result)
    return result


def _quarantine_formal_report(operation: str) -> dict[str, Any] | None:
    if not FORMAL_REPORT.is_file():
        return None
    quarantine = DIRECTORY / f"formal-production.invalidated-{operation}.json"
    os.replace(FORMAL_REPORT, quarantine)
    return {
        "status": "invalidated",
        "operation_id": operation,
        "path": str(quarantine.resolve()),
        "sha256": _sha(quarantine),
    }


def _recover_stale_pending() -> dict[str, Any] | None:
    if not PENDING.is_file():
        return None
    state = _load(PENDING)
    status = state.get("state")
    if status == "complete":
        archived = _archive_complete_state(PENDING)
        _cleanup_backup(archived, recovered=True)
        return archived
    if status == "rollback_complete":
        operation = _operation(state.get("operation_id"))
        backup_path, _backup_sha = _validate_backup(state)
        verification = _owned_verification(state)
        backup_path.unlink(missing_ok=True)
        if verification is not None:
            verification.unlink(missing_ok=True)
        if FAILURE_REPORT.is_file():
            try:
                failure = _load(FAILURE_REPORT)
            except (OSError, ValueError, json.JSONDecodeError):
                failure = {}
            failure_operation = failure.get("operation_id")
            if failure_operation is None and type(failure.get("rollback")) is dict:
                failure_operation = failure["rollback"].get("operation_id")
            if failure_operation == operation:
                FAILURE_REPORT.unlink(missing_ok=True)
        FORMAL_REPORT.unlink(missing_ok=True)
        PENDING.unlink(missing_ok=True)
        return None
    return state


def _run_child(script: str, worker: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, script, worker, *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _apply_locked() -> dict[str, Any]:
    recovered = _recover_stale_pending()
    if recovered is not None and recovered.get("state") not in {"complete"}:
        raise RuntimeError("an incomplete production migration requires recovery")
    operation = uuid.uuid4().hex
    verification = DIRECTORY / f".formal-production-verify-{operation}.json"
    result = _run_child(
        str(Path(__file__).resolve()),
        "coordinate",
        str(PENDING),
        str(verification),
        operation,
    )
    if result.returncode == 0:
        state = _load(PENDING)
        archived = _archive_complete_state(PENDING)
        _cleanup_backup(archived, recovered=False)
        return state

    state = _load(PENDING) if PENDING.is_file() else {"operation_id": operation}
    invalidated = _quarantine_formal_report(operation)
    rollback_path = DIRECTORY / f".formal-production-rollback-{operation}.json"
    rollback_result = _run_child(
        str(Path(__file__).resolve()),
        "restore",
        str(PENDING),
        str(rollback_path),
    )
    rollback = _load(rollback_path) if rollback_path.is_file() else {
        "restored": False,
        "error": rollback_result.stderr.strip(),
    }
    failure: dict[str, Any] = {
        "contract": "formal-production-action-admission-v2-failure-v1",
        "operation_id": operation,
        "failed_at": _now(),
        "coordinator_error": (result.stderr or result.stdout).strip(),
        "rollback": rollback,
    }
    if rollback.get("restored") is True:
        if invalidated is not None:
            Path(invalidated["path"]).unlink(missing_ok=True)
        state = _load(PENDING)
        state["formal_report_removed"] = invalidated is not None
        _persist_pending(state, PENDING)
        failure["formal_report_removed"] = invalidated is not None
    elif invalidated is not None:
        failure["invalidated_formal_report"] = invalidated
    _write_json(FAILURE_REPORT, failure)
    raise RuntimeError(
        "production migration failed; automatic rollback "
        + ("completed" if rollback.get("restored") is True else "was refused")
    )


def _apply() -> dict[str, Any]:
    try:
        with file_lock(MIGRATION_LOCK, timeout=0):
            return _apply_locked()
    except LockBusyError as error:
        raise RuntimeError("another production migration is already running") from error


def _main(argv: list[str]) -> int:
    if not argv:
        _apply()
        return 0
    worker, *arguments = argv
    if worker == "coordinate" and len(arguments) == 3:
        _coordinator_worker(Path(arguments[0]), Path(arguments[1]), arguments[2])
        return 0
    if worker == "restore" and len(arguments) == 2:
        _restore_worker(Path(arguments[0]), Path(arguments[1]))
        return 0
    raise SystemExit("usage: formal_production_apply.py [coordinate PENDING VERIFY OPERATION | restore PENDING OUTPUT]")


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
