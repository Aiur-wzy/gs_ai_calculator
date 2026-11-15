"""Crash, concurrency, and rollback safety for the formal production migrator."""

from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (
    ROOT
    / "scripts"
    / "maintenance"
    / "formal_production_apply.py"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def migration(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        f"formal_apply_{tmp_path.name}", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    database = tmp_path / "live.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('baseline')")
        connection.commit()
    baseline = _sha(database)
    paths = {
        "DIRECTORY": tmp_path,
        "DATABASE": database,
        "BACKUP_DIRECTORY": tmp_path / "backups",
        "FORMAL_REPORT": tmp_path / "formal.json",
        "FAILURE_REPORT": tmp_path / "failure.json",
        "DRY_RUN_REPORT": tmp_path / "dry-run.json",
        "PENDING": tmp_path / "pending.json",
        "COMPLETE_STATE": tmp_path / "complete-state.json",
        "MIGRATION_LOCK": tmp_path / "operation.lock",
        "CLEANUP_REPORT": tmp_path / "cleanup.json",
    }
    for name, value in paths.items():
        monkeypatch.setattr(module, name, value)
    monkeypatch.setattr(module, "BASELINE_SHA256", baseline)
    monkeypatch.setattr(module, "_bundle", lambda: {"id": "fixture"})

    def fake_import(path, _bundle, **_options):
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("UPDATE marker SET value='migrated'")
            connection.commit()
        return {
            "applied": True,
            "status": "ready",
            "conflicts": [],
            "bundle_id": "fixture",
        }

    monkeypatch.setattr(module, "import_bundle", fake_import)

    def fake_verify_database():
        live = _sha(database)
        return {
            "contract": "fixture-verification",
            "verified_at": module._now(),
            "database_sha256_before": live,
            "database_sha256_after": live,
            "checks": {"fixture": True},
        }

    monkeypatch.setattr(module, "_verify_database", fake_verify_database)
    monkeypatch.setattr(
        module,
        "_run_child",
        lambda *_arguments: (_ for _ in ()).throw(
            AssertionError("unit coordinator must not launch a verification child")
        ),
    )
    return module


def _coordinate(module, operation="fixture-operation"):
    verification = module.DATABASE.parent / "verification.json"
    module._coordinator_worker(module.PENDING, verification, operation)
    return module._load(module.PENDING), module._load(module.FORMAL_REPORT)


def test_coordinator_closes_sha_chain_and_persists_complete_before_cleanup(migration):
    state, report = _coordinate(migration)
    chain = report["sha_chain"]
    assert len(set(chain.values())) == 1
    assert state["state"] == "complete"
    assert (
        state["database_sha256_final"]
        == chain["final_live"]
        == _sha(migration.DATABASE)
    )
    assert state["formal_report_sha256"] == _sha(migration.FORMAL_REPORT)
    assert Path(state["backup"]["path"]).is_file()

    migration._archive_complete_state(migration.PENDING)
    cleanup = migration._cleanup_backup(state, recovered=False)
    assert migration.COMPLETE_STATE.is_file() and not migration.PENDING.exists()
    assert cleanup["status"] == "clean"
    assert not Path(state["backup"]["path"]).exists()


@pytest.mark.parametrize(
    "fault",
    ["after_backup", "after_import", "after_verify", "after_report"],
)
def test_fault_before_complete_is_byte_exactly_rollbackable(
    migration, monkeypatch, fault
):
    def fail(name):
        if name == fault:
            raise RuntimeError(f"fault:{name}")

    monkeypatch.setattr(migration, "_checkpoint", fail)
    with pytest.raises(RuntimeError, match="fault:"):
        _coordinate(migration)
    state = migration._load(migration.PENDING)
    backup = Path(state["backup"]["path"])
    output = migration.DATABASE.parent / "rollback.json"
    migration._restore_worker(migration.PENDING, output)
    rollback = migration._load(output)
    assert rollback["restored"] is True
    assert _sha(migration.DATABASE) == migration.BASELINE_SHA256
    assert backup.is_file()


def test_apply_locked_fault_after_report_revokes_success_and_records_rollback(
    migration, monkeypatch
):
    workers = []

    def fail_after_report(name):
        if name == "after_report":
            raise RuntimeError("fault:after_report")

    def run_in_process(*arguments):
        worker = arguments[1]
        workers.append(worker)
        try:
            if worker == "coordinate":
                migration._coordinator_worker(
                    Path(arguments[2]), Path(arguments[3]), arguments[4]
                )
            elif worker == "restore":
                migration._restore_worker(Path(arguments[2]), Path(arguments[3]))
            else:  # pragma: no cover - a new worker must be routed explicitly
                raise AssertionError(f"unexpected worker: {worker}")
        except RuntimeError as error:
            return subprocess.CompletedProcess(
                arguments,
                1,
                "",
                f"{type(error).__name__}: {error}",
            )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(migration, "_checkpoint", fail_after_report)
    monkeypatch.setattr(migration, "_run_child", run_in_process)

    with pytest.raises(RuntimeError):
        migration._apply_locked()

    state = migration._load(migration.PENDING)
    backup = Path(state["backup"]["path"])
    verification = Path(state["verification_path"])
    failure = migration._load(migration.FAILURE_REPORT)
    assert _sha(migration.DATABASE) == migration.BASELINE_SHA256
    assert workers == ["coordinate", "restore"]
    assert state["state"] == "rollback_complete"
    assert state["database_sha256_final"] == migration.BASELINE_SHA256
    assert state["formal_report_removed"] is True
    assert failure["formal_report_removed"] is True
    assert failure["rollback"]["restored"] is True
    assert failure["rollback"]["operation_id"] == state["operation_id"]
    assert failure["rollback"]["database_sha256"] == migration.BASELINE_SHA256
    assert not migration.FORMAL_REPORT.exists()
    assert not migration.COMPLETE_STATE.exists()
    assert backup.is_file()
    assert verification.is_file()

    monkeypatch.setattr(
        migration,
        "_run_child",
        lambda *_arguments: (_ for _ in ()).throw(
            AssertionError("rollback_complete recovery must not launch a worker")
        ),
    )
    assert migration._recover_stale_pending() is None
    assert migration._recover_stale_pending() is None
    assert _sha(migration.DATABASE) == migration.BASELINE_SHA256
    assert not migration.FAILURE_REPORT.exists()
    assert not migration.PENDING.exists()
    assert not backup.exists()
    assert not verification.exists()
    assert not migration.FORMAL_REPORT.exists()


def test_restore_refusal_quarantines_after_report_success_document(
    migration, monkeypatch
):
    workers = []

    def fail_after_report(name):
        if name == "after_report":
            raise RuntimeError("fault:after_report")

    def run_in_process(*arguments):
        worker = arguments[1]
        workers.append(worker)
        try:
            if worker == "coordinate":
                migration._coordinator_worker(
                    Path(arguments[2]), Path(arguments[3]), arguments[4]
                )
            elif worker == "restore":
                sidecar = migration.DATABASE.with_name(migration.DATABASE.name + "-wal")
                sidecar.write_bytes(b"simulated external SQLite writer")
                migration._restore_worker(Path(arguments[2]), Path(arguments[3]))
            else:  # pragma: no cover - a new worker must be routed explicitly
                raise AssertionError(f"unexpected worker: {worker}")
        except RuntimeError as error:
            return subprocess.CompletedProcess(
                arguments,
                1,
                "",
                f"{type(error).__name__}: {error}",
            )
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(migration, "_checkpoint", fail_after_report)
    monkeypatch.setattr(migration, "_run_child", run_in_process)

    with pytest.raises(RuntimeError, match="automatic rollback"):
        migration._apply_locked()

    state = migration._load(migration.PENDING)
    failure = migration._load(migration.FAILURE_REPORT)
    invalidated = failure["invalidated_formal_report"]
    assert workers == ["coordinate", "restore"]
    assert _sha(migration.DATABASE) != migration.BASELINE_SHA256
    assert not migration.FORMAL_REPORT.exists()
    assert invalidated["status"] == "invalidated"
    assert invalidated["operation_id"] == state["operation_id"]
    assert Path(invalidated["path"]).is_file()
    assert _sha(Path(invalidated["path"])) == invalidated["sha256"]
    assert failure["rollback"]["restored"] is False
    assert Path(state["backup"]["path"]).is_file()


def test_fault_after_complete_recovers_as_success_without_rollback(
    migration, monkeypatch
):
    monkeypatch.setattr(
        migration,
        "_checkpoint",
        lambda name: (
            (_ for _ in ()).throw(RuntimeError("after complete"))
            if name == "after_complete"
            else None
        ),
    )
    with pytest.raises(RuntimeError, match="after complete"):
        _coordinate(migration)
    migrated = _sha(migration.DATABASE)
    assert migrated != migration.BASELINE_SHA256
    report = migration._recover_stale_pending()
    assert report["state"] == "complete"
    assert _sha(migration.DATABASE) == migrated
    assert migration.COMPLETE_STATE.is_file() and not migration.PENDING.exists()
    assert migration._load(migration.CLEANUP_REPORT)["status"] == "clean"


def test_complete_pending_recovers_when_backup_was_already_deleted(migration):
    state, _report = _coordinate(migration)
    Path(state["backup"]["path"]).unlink()
    migrated = _sha(migration.DATABASE)
    report = migration._recover_stale_pending()
    assert report["state"] == "complete"
    assert migration.COMPLETE_STATE.is_file() and not migration.PENDING.exists()
    assert migration._load(migration.CLEANUP_REPORT)["status"] == "clean"
    assert _sha(migration.DATABASE) == migrated


def test_legacy_rollback_complete_cleans_owned_failure_and_verification(migration):
    operation = "legacy-rollback"
    backup_path = migration.BACKUP_DIRECTORY / f"action-admission-v2-r1-{operation}.db"
    backup = migration._create_backup(backup_path)
    verification = migration.DIRECTORY / f".formal-production-verify-{operation}.json"
    verification.write_text("legacy verification residue\n", encoding="utf-8")
    migration._write_json(
        migration.FAILURE_REPORT,
        {
            "contract": "legacy-failure",
            "rollback": {"operation_id": operation, "restored": True},
        },
    )
    migration._write_json(
        migration.PENDING,
        {
            "operation_id": operation,
            "state": "rollback_complete",
            "backup": backup,
        },
    )

    assert migration._recover_stale_pending() is None
    assert migration._recover_stale_pending() is None
    assert _sha(migration.DATABASE) == migration.BASELINE_SHA256
    assert not backup_path.exists()
    assert not verification.exists()
    assert not migration.FAILURE_REPORT.exists()
    assert not migration.PENDING.exists()


def test_rollback_refuses_sidecars_and_retains_evidence(migration):
    backup = migration._create_backup(migration.DATABASE.parent / "backup.db")
    with closing(sqlite3.connect(migration.DATABASE)) as connection:
        connection.execute("UPDATE marker SET value='migrated'")
        connection.commit()
    changed = _sha(migration.DATABASE)
    state = {
        "operation_id": "sidecar",
        "database_sha256_before": migration.BASELINE_SHA256,
        "database_sha256_after_import": changed,
        "known_live_sha256": [migration.BASELINE_SHA256, changed],
        "backup": backup,
    }
    migration._write_json(migration.PENDING, state)
    sidecar = migration.DATABASE.with_name(migration.DATABASE.name + "-wal")
    sidecar.write_bytes(b"evidence")
    output = migration.DATABASE.parent / "rollback-sidecar.json"
    with pytest.raises(RuntimeError, match="sidecars"):
        migration._restore_worker(migration.PENDING, output)
    report = migration._load(output)
    assert report["restored"] is False
    assert str(sidecar) in report["sidecars"]
    assert Path(backup["path"]).is_file()
    assert _sha(migration.DATABASE) == changed


def test_rollback_refuses_unknown_phase_sha(migration):
    backup = migration._create_backup(migration.DATABASE.parent / "backup.db")
    with closing(sqlite3.connect(migration.DATABASE)) as connection:
        connection.execute("UPDATE marker SET value='external-write'")
        connection.commit()
    changed = _sha(migration.DATABASE)
    state = {
        "operation_id": "unknown-stage",
        "database_sha256_before": migration.BASELINE_SHA256,
        "known_live_sha256": [migration.BASELINE_SHA256],
        "backup": backup,
    }
    migration._write_json(migration.PENDING, state)
    output = migration.DATABASE.parent / "rollback-unknown.json"
    with pytest.raises(RuntimeError, match="unknown live SHA"):
        migration._restore_worker(migration.PENDING, output)
    assert _sha(migration.DATABASE) == changed
    assert Path(backup["path"]).is_file()


def test_windows_replace_failure_retains_backup_state_and_report(
    migration, monkeypatch
):
    backup = migration._create_backup(migration.DATABASE.parent / "backup.db")
    with closing(sqlite3.connect(migration.DATABASE)) as connection:
        connection.execute("UPDATE marker SET value='migrated'")
        connection.commit()
    changed = _sha(migration.DATABASE)
    state = {
        "operation_id": "windows-handle",
        "database_sha256_before": migration.BASELINE_SHA256,
        "database_sha256_after_import": changed,
        "known_live_sha256": [migration.BASELINE_SHA256, changed],
        "backup": backup,
    }
    migration._write_json(migration.PENDING, state)
    original_replace = migration.os.replace

    def occupied(source, destination):
        if Path(destination).resolve() == migration.DATABASE.resolve():
            raise PermissionError("simulated Windows database handle")
        return original_replace(source, destination)

    monkeypatch.setattr(migration.os, "replace", occupied)
    output = migration.DATABASE.parent / "rollback-windows.json"
    with pytest.raises(RuntimeError, match="Windows handle"):
        migration._restore_worker(migration.PENDING, output)
    report = migration._load(output)
    assert report["restored"] is False
    assert report["temporary_path"]
    assert Path(backup["path"]).is_file() and migration.PENDING.is_file()
    assert _sha(migration.DATABASE) == changed


def test_external_write_between_verify_and_finalize_breaks_sha_chain(
    migration, monkeypatch
):
    original = migration._verify_database

    def write_after_verify():
        result = original()
        with closing(sqlite3.connect(migration.DATABASE)) as connection:
            connection.execute("UPDATE marker SET value='external-after-verify'")
            connection.commit()
        return result

    monkeypatch.setattr(migration, "_verify_database", write_after_verify)
    with pytest.raises(RuntimeError, match="SHA chain"):
        _coordinate(migration)
    state = migration._load(migration.PENDING)
    assert state["state"] == "external_write_conflict"
    assert not migration.FORMAL_REPORT.exists()
    output = migration.DATABASE.parent / "rollback-external.json"
    with pytest.raises(RuntimeError, match="unknown live SHA"):
        migration._restore_worker(migration.PENDING, output)


def test_coordinator_holds_database_lock_during_fresh_verification(
    migration, monkeypatch
):
    fixture_verify = migration._verify_database
    observations = []
    code = """
import sys
import sqlite3
from contextlib import closing
from pathlib import Path
sys.path.insert(0, sys.argv[2])
from genshin_db.locking import LockBusyError, database_lock
try:
    with database_lock(Path(sys.argv[1]), timeout=0):
        with closing(sqlite3.connect(sys.argv[1])) as connection:
            connection.execute("UPDATE marker SET value='cooperating-writer'")
            connection.commit()
        print('written')
except LockBusyError:
    print('busy')
"""

    def run_writer():
        return subprocess.run(
            [
                sys.executable,
                "-c",
                code,
                str(migration.DATABASE),
                str(ROOT / "src"),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )

    def verify_while_probing_lock():
        before = _sha(migration.DATABASE)
        probe = run_writer()
        observations.append(probe.stdout.strip())
        assert probe.stdout.strip() == "busy", probe.stderr
        assert _sha(migration.DATABASE) == before
        return fixture_verify()

    monkeypatch.setattr(migration, "_verify_database", verify_while_probing_lock)
    state, report = _coordinate(migration, "lock-coverage")
    assert observations == ["busy"]
    assert state["state"] == report["state"] == "complete"
    positive_control = run_writer()
    assert positive_control.stdout.strip() == "written", positive_control.stderr
    with closing(sqlite3.connect(migration.DATABASE)) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone()[0] == (
            "cooperating-writer"
        )


def test_operation_lock_rejects_concurrent_apply_before_pending_read(migration):
    code = """
import importlib.util
import sys
from pathlib import Path
from genshin_db.locking import file_lock
spec = importlib.util.spec_from_file_location('migration_lock_holder', Path(sys.argv[1]))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with file_lock(Path(sys.argv[2]), timeout=1):
    print('locked', flush=True)
    sys.stdin.readline()
"""
    holder = subprocess.Popen(
        [sys.executable, "-c", code, str(SCRIPT), str(migration.MIGRATION_LOCK)],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeError, match="another production migration"):
            migration._apply()
        assert not migration.PENDING.exists()
    finally:
        assert holder.stdin is not None
        holder.stdin.write("release\n")
        holder.stdin.flush()
        holder.wait(timeout=5)
        if holder.returncode:
            assert holder.stderr is not None
            pytest.fail(holder.stderr.read())


def test_backup_cleanup_failure_is_deferred_without_rollback(migration, monkeypatch):
    state, _report = _coordinate(migration)
    backup = Path(state["backup"]["path"])
    migrated = _sha(migration.DATABASE)
    original_unlink = Path.unlink

    def fail_backup(self, *args, **kwargs):
        if self.resolve() == backup.resolve():
            raise PermissionError("simulated Windows handle")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_backup)
    migration._archive_complete_state(migration.PENDING)
    cleanup = migration._cleanup_backup(state, recovered=False)
    assert cleanup["status"] == "cleanup_pending"
    assert backup.is_file()
    assert _sha(migration.DATABASE) == migrated


def test_full_verifier_reenters_database_lock_on_real_migrated_copy(
    tmp_path, monkeypatch, accepted_source_data_dir
):
    spec = importlib.util.spec_from_file_location("formal_apply_real_lock", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    source = accepted_source_data_dir / "genshin.db"
    # A literal URI fragment character is legal in a Windows filename too.
    database = tmp_path / "real-verifier-#copy.db"
    shutil.copy2(source, database)
    monkeypatch.setattr(module, "DATABASE", database)
    result = module.import_bundle(
        database,
        module._bundle(),
        version_line="release",
        dry_run=False,
    )
    assert result["applied"] is True and result["status"] == "ready"

    with module.database_lock(database):
        report = module._verify_database()
        module._require_verification(report)

    assert all(report["checks"].values())
    assert report["database_sha256_before"] == report["database_sha256_after"]
