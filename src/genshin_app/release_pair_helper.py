"""External-process coordinator for an already staged App/database pair.

This module is designed to be packaged as a small updater executable located
outside the active App directory.  The running GUI prepares a handoff and
exits; the helper then creates an independent rollback copy, revalidates the
staged bytes, swaps the App, runs the database subtransaction, postchecks the
complete pair and finally cleans coordinator-owned staging.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from genshin_app import release_pair_update as pair_update
from genshin_app.database_release_reuse import (
    DatabaseReleaseReuseError,
    rebind_database_sidecars,
    verified_reusable_database,
)
from genshin_app.database_transaction import profile_writer
from genshin_app.release_pair_storage import (
    ReleasePairStorageError,
    directory_shape,
    preflight_after_stage,
)
from genshin_app.version import APP_DIR_NAME
from genshin_app.windows_known_folders import (
    FOLDERID_LOCAL_APP_DATA,
    WindowsPathAuthorityError,
    is_windows_elevated,
    known_folder_path,
    reject_reparse_chain,
)
from genshin_db.release_migration import JOURNAL_FILE as DATABASE_MIGRATION_JOURNAL
from genshin_db.release_migration import install_database_release

HELPER_RESULT_CONTRACT = "genshin-release-pair-helper-result-v1"
_LAST_RESULT_NAME = "release-pair-helper-last-result.json"
PRODUCT_REGISTRY_SUBKEY = r"Software\GenshinCalc"
PRODUCT_REGISTRY_VALUE = "InstallLocation"
MAX_HELPER_REQUEST_BYTES = 1024 * 1024
_ROLLBACK_CONTROL_FILES = (
    pair_update.LIVE_DATABASE_MANIFEST_FILENAME,
    pair_update.LIVE_DATABASE_RECEIPT_FILENAME,
    pair_update.LIVE_PAIR_MANIFEST_FILENAME,
    pair_update.LIVE_PAIR_PROOF_FILENAME,
    DATABASE_MIGRATION_JOURNAL,
)


class ReleasePairHelperError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _fail(reason: str) -> None:
    raise ReleasePairHelperError(reason)


def _inside(path: Path, directory: Path) -> bool:
    return path == directory or directory in path.parents


def _overlap(first: Path, second: Path) -> bool:
    return _inside(first, second) or _inside(second, first)


def _is_reparse_point(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        junction = getattr(path, "is_junction", None)
        if callable(junction) and junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ReleasePairHelperError(
            "release_pair_helper_rollback_invalid"
        ) from error
    return digest.hexdigest()


def _wait_for_parent_exit(parent_pid: int | None, timeout: float) -> None:
    """Wait for the GUI process without accepting an unbounded helper hang."""

    if parent_pid is None:
        return
    if type(parent_pid) is not int or parent_pid <= 0 or parent_pid == os.getpid():
        _fail("release_pair_helper_parent_pid_invalid")
    if type(timeout) not in (int, float) or not 0 < float(timeout) <= 300:
        _fail("release_pair_helper_parent_wait_invalid")
    if os.name == "nt":
        synchronize = 0x00100000
        wait_object_0 = 0
        wait_timeout = 258
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.WaitForSingleObject.restype = ctypes.c_uint32
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.restype = ctypes.c_bool
        handle = kernel.OpenProcess(synchronize, False, parent_pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # The process has already exited.
                return
            _fail("release_pair_helper_parent_wait_failed")
        try:
            result = kernel.WaitForSingleObject(handle, int(float(timeout) * 1000))
        finally:
            kernel.CloseHandle(handle)
        if result == wait_object_0:
            return
        if result == wait_timeout:
            _fail("release_pair_helper_parent_exit_timeout")
        _fail("release_pair_helper_parent_wait_failed")
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            # A live process that cannot be signalled is still live.
            pass
        time.sleep(0.05)
    _fail("release_pair_helper_parent_exit_timeout")


def _restart_target(active_app_root: Path, relative: str) -> Path:
    if type(relative) is not str or not relative or "\\" in relative or "\x00" in relative:
        _fail("release_pair_helper_restart_entry_invalid")
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail("release_pair_helper_restart_entry_invalid")
    if any(":" in part for part in parsed.parts):
        _fail("release_pair_helper_restart_entry_invalid")
    root = active_app_root.resolve(strict=True)
    try:
        target = active_app_root.joinpath(*parsed.parts).resolve(strict=True)
    except OSError as error:
        raise ReleasePairHelperError("release_pair_helper_restart_entry_missing") from error
    if root == target or root not in target.parents or not target.is_file() or target.is_symlink():
        _fail("release_pair_helper_restart_entry_invalid")
    return target


def _launch_replacement(active_app_root: Path, relative: str) -> int:
    target = _restart_target(active_app_root, relative)
    try:
        process = subprocess.Popen(
            [str(target)],
            cwd=str(active_app_root),
            shell=False,
            close_fds=True,
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
        )
    except OSError as error:
        raise ReleasePairHelperError("release_pair_helper_restart_failed") from error
    return process.pid


def _verify_frozen_active_pair(
    handoff: Mapping[str, Any], active_app_root: Path, data_dir: Path
) -> dict[str, Any] | None:
    """Bind a user-writable handoff to the actual protected installation."""

    if not bool(getattr(sys, "frozen", False)):
        return None
    current = pair_update.inspect_active_release_pair(
        active_app_root,
        data_dir,
        production=handoff["production"],
    )
    target = handoff["pair_manifest"]
    installed = current["pair_manifest"]
    if (
        handoff.get("rollback_required") is not True
        or handoff.get("expected_current_fingerprint")
        != current["identity_fingerprint"]
    ):
        _fail("release_pair_helper_current_identity_mismatch")
    if target["pair_sequence"] < installed["pair_sequence"]:
        _fail("release_pair_update_rollback_forbidden")
    if target["pair_sequence"] == installed["pair_sequence"]:
        _fail("release_pair_update_split_brain")
    return current


def _protected_rollback_root(active: Path, operation_id: str) -> Path:
    return active.parent / f".{active.name}.pair-rollback-{operation_id}"


def _prepare_protected_rollback(
    active_app_root: Path,
    data_dir: Path,
    operation_id: str,
    expected_current: Mapping[str, Any] | None,
    *,
    include_database: bool = True,
) -> Path:
    """Create the rollback authority beside the admin-owned App directory.

    The user-writable workspace rollback remains a journal precondition only;
    it is never used to restore protected files.
    """

    root = _protected_rollback_root(active_app_root, operation_id)
    if root.exists():
        _fail("release_pair_helper_protected_rollback_exists")
    root.mkdir()
    try:
        database = data_dir / "genshin.db"
        if not active_app_root.is_dir() or not database.is_file():
            _fail("release_pair_helper_active_pair_missing")
        protected_database = root / "genshin.db"
        if include_database:
            shutil.copy2(database, protected_database)
        for name in _ROLLBACK_CONTROL_FILES:
            source = data_dir / name
            if source.is_file():
                shutil.copy2(source, root / name)
        if expected_current is not None:
            database_to_verify = protected_database if include_database else database
            if _sha256(database_to_verify) != expected_current["active_database_sha256"]:
                _fail("release_pair_helper_protected_rollback_invalid")
            expected_json = {
                pair_update.LIVE_PAIR_MANIFEST_FILENAME: expected_current[
                    "pair_manifest"
                ],
                pair_update.LIVE_DATABASE_MANIFEST_FILENAME: expected_current[
                    "database_manifest"
                ],
                pair_update.LIVE_DATABASE_RECEIPT_FILENAME: expected_current[
                    "live_receipt"
                ],
            }
            for name, expected in expected_json.items():
                try:
                    actual = json.loads((root / name).read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as error:
                    raise ReleasePairHelperError(
                        "release_pair_helper_protected_rollback_invalid"
                    ) from error
                if actual != expected:
                    _fail("release_pair_helper_protected_rollback_invalid")
        return root
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def _swap_app(
    staged: Path,
    active: Path,
    operation_id: str,
    pair: Mapping[str, Any],
    protected_rollback: Path,
    *,
    independent: bool = False,
) -> None:
    parent = active.parent
    parent.mkdir(parents=True, exist_ok=True)
    replacement = parent / f".{active.name}.next-{operation_id}"
    previous = protected_rollback / "app"
    if replacement.exists() or previous.exists():
        _fail("release_pair_helper_app_swap_recovery_required")
    moved_old = False
    try:
        # Copy user-writable staging into an administrator-owned directory,
        # then authenticate that protected copy before it can become active.
        shutil.copytree(staged, replacement)
        if independent:
            pair_update._verify_app(replacement, pair, independent=True)
        else:
            pair_update._verify_app(replacement, pair)
        if active.exists():
            os.replace(active, previous)
            moved_old = True
        os.replace(replacement, active)
    except OSError as error:
        if moved_old and previous.exists() and not active.exists():
            os.replace(previous, active)
        raise ReleasePairHelperError("release_pair_helper_app_swap_failed") from error
    finally:
        if replacement.exists():
            shutil.rmtree(replacement, ignore_errors=True)


def _restore_protected_rollback(
    rollback: Path,
    active_app_root: Path,
    data_dir: Path,
    expected_current: Mapping[str, Any] | None,
    *,
    include_database: bool = True,
    include_app: bool = True,
) -> None:
    rollback_app = rollback / "app"
    rollback_database = rollback / "genshin.db"
    if (include_app and not rollback_app.is_dir()) or (
        include_database and not rollback_database.is_file()
    ):
        _fail("release_pair_helper_rollback_missing")
    if expected_current is not None:
        previous_pair = expected_current["pair_manifest"]
        previous_manifest = expected_current["database_manifest"]
        previous_independent = pair_update.read_live_product_proof(rollback, previous_pair, previous_manifest) is not None
        pair_update._verify_app(rollback_app if include_app else active_app_root, previous_pair,
                                independent=previous_independent)
        if include_database and (
            _sha256(rollback_database) != expected_current["active_database_sha256"]
        ):
            _fail("release_pair_helper_protected_rollback_invalid")
    if include_app:
        failed_app = rollback / "failed-app"
        if active_app_root.exists():
            if os.path.lexists(failed_app):
                _fail("release_pair_helper_app_swap_recovery_required")
            os.replace(active_app_root, failed_app)
        os.replace(rollback_app, active_app_root)
    data_dir.mkdir(parents=True, exist_ok=True)
    if include_database:
        _replace_from_rollback(rollback_database, data_dir / "genshin.db")
    # The nested DB journal must describe the restored database, or retrying
    # the same DB release will incorrectly take its already-installed branch.
    for name in _ROLLBACK_CONTROL_FILES:
        target = data_dir / name
        saved = rollback / name
        if saved.is_file():
            _replace_from_rollback(saved, target)
        else:
            target.unlink(missing_ok=True)


def _replace_from_rollback(source: Path, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.rollback-{uuid4().hex}")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def recover_interrupted_pair_update(
    workspace: Path, active_app_root: Path, data_dir: Path, *, operation_id: str, user_confirmed: bool,
) -> dict[str, Any]:
    """Restore a signed installed profile, including after a crash during rollback."""
    from genshin_app.database_legacy_processes import packaged_peer_processes

    if user_confirmed is not True:
        _fail("release_pair_update_confirmation_required")
    root, active, data = _product_recovery_paths(workspace, active_app_root, data_dir)
    if _inside(Path(sys.executable).resolve(), active):
        _fail("release_pair_helper_must_run_outside_active_app")
    with profile_writer(data):
        if packaged_peer_processes():
            _fail("database_legacy_instance_running")
        return _recover_interrupted_pair_update(root, active, data, operation_id)


def discard_prepared_pair_update(workspace: Path, active_app_root: Path, data_dir: Path, *, operation_id: str) -> None:
    """Retire staging only, after the coordinator has confirmed a newer selection."""
    root, active, data = _product_recovery_paths(workspace, active_app_root, data_dir)
    with profile_writer(data):
        _recover_interrupted_pair_update(root, active, data, operation_id, prepared_only=True)


def _product_recovery_paths(workspace: Path, active_app_root: Path, data_dir: Path) -> tuple[Path, Path, Path]:
    root = reject_reparse_chain(workspace, reason="release_pair_helper_request_path_invalid")
    data = reject_reparse_chain(data_dir, reason="release_pair_helper_request_path_invalid")
    active = Path(active_app_root).absolute()
    if (root != data.parent / "update-workspace" or data != active.parent / "data"
            or active.name != "app" or (os.path.lexists(active) and _is_reparse_point(active))):
        _fail("release_pair_helper_path_topology_invalid")
    return root, active, data


def _recover_interrupted_pair_update(
    root: Path, active: Path, data: Path, operation_id: str, *, prepared_only: bool = False,
) -> dict[str, Any]:
    journal = pair_update.read_pair_update_journal(root)
    if journal is None or journal["operation_id"] != operation_id or journal["production"] is not True:
        _fail("release_pair_update_operation_mismatch")
    if prepared_only and journal["state"] != "handoff_ready":
        _fail("release_pair_update_recovery_required")
    handoff = pair_update._read_handoff(root, journal)
    pair, _ = pair_update._validated_handoff_target(handoff, journal)
    trust = handoff["target"]["trust_assertion"]
    previous = handoff.get("rollback")
    if not pair_update._independent_app(trust) or previous is None:
        _fail("release_pair_helper_current_user_signed_install_required")
    state = journal["state"]
    protected = _protected_rollback_root(active, operation_id)
    if state in {"handoff_ready", "operational", "rolled_back"}:
        current = pair_update.inspect_active_release_pair(active, data, production=True)
        expected = pair if state == "operational" else previous["pair_manifest"]
        if current["pair_manifest"] != expected or (
            state != "operational" and current["identity_fingerprint"] != journal["rollback_fingerprint"]
        ):
            _fail("release_pair_helper_current_identity_mismatch")
        if state == "handoff_ready":
            # No files had been activated. The signed, intact prior pair is
            # the authority to retire only this obsolete staging operation.
            pair_update._write_transition(root, journal, "rolled_back")
            action = "discarded_prepared_update"
        else:
            action = "completed_cleanup"
    elif state in {"activation_started", "postchecked", "committed"}:
        protected = reject_reparse_chain(protected, reason="release_pair_helper_protected_rollback_invalid")
        if any(os.path.lexists(path) and _is_reparse_point(path)
               for path in (protected / name for name in (*_ROLLBACK_CONTROL_FILES, "app", "genshin.db"))):
            _fail("release_pair_helper_protected_rollback_invalid")
        old_app = protected / "app"
        include_app = old_app.is_dir()
        reuse_app = handoff["staging"]["app_relative"] is None
        reuse_database = handoff["database_subtransaction"]["mode"] == "reuse_installed"
        preserved_database = data / "genshin.db" if reuse_database else protected / "genshin.db"
        pair_update._verify_product_selection(trust, previous, reuse_app=reuse_app,
                                              reuse_database=reuse_database, for_install=False)
        previous_pair, previous_db = previous["pair_manifest"], previous["database_manifest"]
        if pair_update.read_live_product_proof(protected, previous_pair, previous_db) is None:
            _fail("release_pair_helper_protected_rollback_invalid")
        checked = pair_update._validate_current({
            "app_root": old_app if include_app else active,
            "database": preserved_database,
            "pair_manifest": previous_pair,
            "database_manifest": previous_db,
            "live_receipt": pair_update._read_json(protected / pair_update.LIVE_DATABASE_RECEIPT_FILENAME,
                                                  "release_pair_helper_protected_rollback_invalid"),
        }, production=True, proof_directory=protected)
        if checked != previous or checked["identity_fingerprint"] != journal["rollback_fingerprint"]:
            _fail("release_pair_helper_protected_rollback_invalid")
        for name, expected in ((pair_update.LIVE_PAIR_MANIFEST_FILENAME, previous_pair),
                               (pair_update.LIVE_DATABASE_MANIFEST_FILENAME, previous_db)):
            if pair_update._read_json(protected / name, "release_pair_helper_protected_rollback_invalid") != expected:
                _fail("release_pair_helper_protected_rollback_invalid")
        _restore_protected_rollback(protected, active, data, checked, include_database=not reuse_database,
                                    include_app=include_app)
        pair_update.confirm_pair_update_rollback(root, active, data, operation_id=operation_id)
        expected = previous_pair
        action = "restored_previous_pair"
    else:
        _fail("release_pair_update_state_conflict")

    for path in (active.with_name(f".{active.name}.next-{operation_id}"), protected):
        if os.path.lexists(path):
            checked_path = reject_reparse_chain(path, reason="release_pair_cleanup_path_invalid")
            if checked_path != path or not checked_path.is_dir():
                _fail("release_pair_cleanup_path_invalid")
            shutil.rmtree(checked_path)
    pair_update.complete_pair_update_cleanup(root, operation_id=operation_id)
    return {"status": "recovered", "action": action, "release_pair_id": expected["release_pair_id"],
            "operation_id": operation_id}


def prepare_external_pair_update(
    workspace: Path,
    staged_app_root: Path,
    staged_database: Path | None,
    database_manifest: Mapping[str, Any],
    pair_manifest: Mapping[str, Any],
    *,
    user_confirmed: bool,
    production: bool,
    trust_assertion: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None = None,
    reuse_installed_database: bool = False,
) -> dict[str, Any]:
    """Create the durable handoff; does not replace any active path."""

    return pair_update.prepare_release_pair_handoff(
        workspace,
        staged_app_root,
        staged_database,
        database_manifest,
        pair_manifest,
        user_confirmed=user_confirmed,
        production=production,
        trust_assertion=trust_assertion,
        current=current,
        reuse_installed_database=reuse_installed_database,
    )


def execute_external_pair_update(
    workspace: Path,
    active_app_root: Path,
    data_dir: Path,
    *,
    operation_id: str,
    cleanup: bool = True,
    parent_pid: int | None = None,
    parent_wait_timeout: float = 60.0,
    restart_relative: str | None = None,
    current_user: bool = False,
) -> dict[str, Any]:
    """Execute one prepared handoff from outside the active App process."""

    root = Path(workspace).resolve(strict=True)
    active_app = Path(active_app_root).resolve()
    data = Path(data_dir).resolve()
    helper = Path(sys.executable).resolve()
    if type(cleanup) is not bool:
        _fail("release_pair_helper_request_invalid")
    if type(operation_id) is not str or not operation_id or len(operation_id) > 128:
        _fail("release_pair_helper_request_invalid")
    if _overlap(root, active_app) or _overlap(data, active_app) or _overlap(root, data):
        _fail("release_pair_helper_path_topology_invalid")
    if _inside(helper, active_app):
        _fail("release_pair_helper_must_run_outside_active_app")
    _wait_for_parent_exit(parent_pid, parent_wait_timeout)
    with profile_writer(data):
        if current_user:
            from genshin_app.database_legacy_processes import packaged_peer_processes

            if packaged_peer_processes():
                _fail("database_legacy_instance_running")
        handoff = pair_update.inspect_pair_update_handoff(root, operation_id=operation_id)
        expected_current = _verify_frozen_active_pair(handoff, active_app, data)
        if current_user and (
            handoff["production"] is not True or not handoff["independent_app"] or expected_current is None
            or pair_update.read_live_product_proof(data, expected_current["pair_manifest"],
                                                  expected_current["database_manifest"]) is None
        ):
            _fail("release_pair_helper_current_user_signed_install_required")
        result = _activate_prepared_pair(root, active_app, data, handoff, expected_current, cleanup=cleanup)
    if restart_relative is not None:
        pid = _launch_replacement(active_app, restart_relative)
        result["restart"] = {"requested": True, "started": True, "pid": pid}
    return result


def _install_in_fresh_profile(transaction_root: Path, *, operation_id: str) -> dict[str, Any]:
    """Install into a new private profile owned by the unelevated bootstrap."""
    root = Path(transaction_root).resolve(strict=True)
    workspace = root / "workspace"
    profile = root / "profile"
    if _is_reparse_point(workspace) or os.path.lexists(profile):
        _fail("release_pair_helper_fresh_profile_invalid")
    handoff = pair_update.inspect_pair_update_handoff(workspace, operation_id=operation_id)
    if (handoff["state"] != "handoff_ready" or handoff["rollback_required"]
            or handoff["production"] is not True or not handoff["independent_app"]
            or handoff["staged_app_root"] is None or handoff["staged_database"] is None):
        _fail("release_pair_helper_fresh_handoff_invalid")
    profile.mkdir()
    return _activate_prepared_pair(
        workspace, profile / "app", profile / "data", handoff, None,
        cleanup=True,
    )


def _activate_prepared_pair(
    root: Path,
    active_app: Path,
    data: Path,
    handoff: Mapping[str, Any],
    expected_current: dict[str, Any] | None,
    *,
    cleanup: bool,
) -> dict[str, Any]:
    operation_id = handoff["operation_id"]
    pair = handoff["pair_manifest"]
    manifest = handoff["database_manifest"]
    reuse_installed_database = (
        handoff.get("database_mode", "install") == "reuse_installed"
    )
    reuse_installed_app = handoff["staged_app_root"] is None
    rollback_required = handoff["rollback_required"]
    try:
        staged_shape = {"bytes": 0, "files": 0} if reuse_installed_app else directory_shape(handoff["staged_app_root"])
        storage_plan = preflight_after_stage(
            active_app_root=active_app,
            data_dir=data,
            app_unpacked_bytes=staged_shape["bytes"],
            app_unpacked_files=staged_shape["files"],
            target_database_bytes=(
                0
                if handoff["staged_database"] is None
                else handoff["staged_database"].stat().st_size
            ),
            current_database_bytes=(data / "genshin.db").stat().st_size if rollback_required else 0,
            reuse_installed_database=reuse_installed_database,
            reuse_installed_app=reuse_installed_app,
        )
    except (OSError, ReleasePairStorageError) as error:
        raise ReleasePairHelperError(
            getattr(error, "reason", "release_pair_storage_path_unavailable")
        ) from error
    protected_rollback = None
    try:
        if rollback_required:
            protected_rollback = _prepare_protected_rollback(
                active_app,
                data,
                operation_id,
                expected_current,
                include_database=not reuse_installed_database,
            )
        else:
            protected_rollback = _protected_rollback_root(active_app, operation_id)
            if protected_rollback.exists():
                _fail("release_pair_helper_protected_rollback_exists")
            protected_rollback.mkdir()
        pair_update.begin_pair_update_activation(
            root,
            operation_id=operation_id,
            active_app_root=active_app if rollback_required else None,
            active_database=(data / "genshin.db") if rollback_required else None,
        )
    except Exception:
        if protected_rollback is not None:
            shutil.rmtree(protected_rollback, ignore_errors=True)
        raise
    swapped = False
    pair_operational = False
    try:
        if protected_rollback is None:
            _fail("release_pair_helper_protected_rollback_missing")
        if not reuse_installed_app:
            _swap_app(handoff["staged_app_root"], active_app, operation_id, pair, protected_rollback,
                      independent=handoff.get("independent_app", False))
            swapped = True
        app = pair["app"]
        try:
            reusable_identity = verified_reusable_database(data, manifest)
        except DatabaseReleaseReuseError as error:
            raise ReleasePairHelperError(error.reason) from error
        if reuse_installed_database and reusable_identity is None:
            _fail("release_pair_database_reuse_not_verified")
        if reusable_identity is None:
            if handoff["staged_database"] is None:
                _fail("release_pair_staged_database_missing")
            install_database_release(
                data,
                handoff["staged_database"],
                manifest,
                logic_profile=app["logic_profile"],
                data_contract_version=app["data_contract_version"],
                user_confirmed=True,
                production=handoff["production"],
                app_release_id=app["app_release_id"],
                release_pair_id=pair["release_pair_id"],
                required_database_release_id=app["required_database_release_id"],
                pair_policy=pair["pair_policy"],
                app_version=app["app_version"],
                app_artifact_manifest_sha256=app["artifact_manifest"]["sha256"],
                pair_manifest=pair,
            )
        else:
            try:
                rebind_database_sidecars(data, manifest, reusable_identity)
            except DatabaseReleaseReuseError as error:
                raise ReleasePairHelperError(error.reason) from error
        checked = pair_update.postcheck_pair_update(
            root,
            active_app,
            data,
            operation_id=operation_id,
        )
        pair_operational = True
        shutil.rmtree(protected_rollback)
        cleaned = (
            pair_update.complete_pair_update_cleanup(
                root, operation_id=operation_id
            )
            if cleanup
            else None
        )
        return {
            "contract": HELPER_RESULT_CONTRACT,
            "status": "complete",
            "operation_id": operation_id,
            "release_pair_id": checked["release_pair_id"],
            "pair_operational": True,
            "storage_preflight": storage_plan,
            "cleanup": cleaned,
            "restart": {"requested": False, "started": False, "pid": None},
        }
    except Exception as error:
        if pair_operational:
            # Cleanup may have removed the rollback directory after postcheck.
            raise
        if rollback_required:
            try:
                if protected_rollback is None:
                    _fail("release_pair_helper_protected_rollback_missing")
                if swapped or reuse_installed_app:
                    _restore_protected_rollback(
                        protected_rollback,
                        active_app,
                        data,
                        expected_current,
                        include_database=not reuse_installed_database,
                        include_app=swapped,
                    )
                pair_update.confirm_pair_update_rollback(
                    root,
                    active_app,
                    data,
                    operation_id=operation_id,
                )
                shutil.rmtree(protected_rollback)
            except Exception as rollback_error:  # noqa: BLE001 - retain both failures
                if hasattr(error, "add_note"):
                    error.add_note(f"pair rollback failed: {type(rollback_error).__name__}")
                raise ReleasePairHelperError(
                    "release_pair_helper_recovery_required"
                ) from error
            if cleanup:
                try:
                    pair_update.complete_pair_update_cleanup(root, operation_id=operation_id)
                except (OSError, pair_update.ReleasePairUpdateError) as cleanup_error:
                    error.add_note(f"pair rollback cleanup failed: {type(cleanup_error).__name__}")
        elif swapped:
            # First-install failure has no trustworthy previous pair to restore.
            # Leave the journal and active paths for explicit recovery rather
            # than falsely declaring an operational rollback.
            raise ReleasePairHelperError(
                "release_pair_helper_first_install_recovery_required"
            ) from error
        raise


def _request(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > MAX_HELPER_REQUEST_BYTES:
            _fail("release_pair_helper_request_invalid")
        value = json.loads(raw.decode("utf-8"))
    except ReleasePairHelperError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleasePairHelperError("release_pair_helper_request_invalid") from error
    if type(value) is not dict or set(value) != {
        "workspace",
        "active_app_root",
        "data_dir",
        "operation_id",
        "cleanup",
        "parent_pid",
        "parent_wait_timeout_seconds",
        "restart_relative",
    }:
        _fail("release_pair_helper_request_invalid")
    return value


def _installed_product_roots() -> tuple[Path, Path, Path]:
    """Resolve paths from admin-owned install state, never from the request."""

    if os.name != "nt":
        _fail("release_pair_helper_product_roots_unavailable")
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            PRODUCT_REGISTRY_SUBKEY,
            0,
            winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0),
        ) as key:
            installed, value_type = winreg.QueryValueEx(key, PRODUCT_REGISTRY_VALUE)
        local = known_folder_path(FOLDERID_LOCAL_APP_DATA)
    except (OSError, WindowsPathAuthorityError) as error:
        raise ReleasePairHelperError(
            "release_pair_helper_install_identity_missing"
        ) from error
    # The installer writes a literal REG_SZ.  Expanding REG_EXPAND_SZ here
    # would reintroduce user-controlled environment variables into the
    # elevated path decision.
    if value_type != winreg.REG_SZ or not installed:
        _fail("release_pair_helper_install_identity_invalid")
    try:
        active = reject_reparse_chain(
            Path(installed), reason="release_pair_helper_install_identity_invalid"
        )
        local_root = reject_reparse_chain(
            Path(local), reason="release_pair_helper_install_identity_invalid"
        )
        profile = local_root / "GenshinCalc"
        data = reject_reparse_chain(
            profile / "data", reason="release_pair_helper_install_identity_invalid"
        )
        workspace = reject_reparse_chain(
            profile / "update-workspace",
            reason="release_pair_helper_install_identity_invalid",
        )
    except WindowsPathAuthorityError as error:
        raise ReleasePairHelperError(
            error.reason
        ) from error
    return active, data, workspace


def _current_user_product_roots() -> tuple[Path, Path, Path]:
    """Derive all update paths from the unelevated Windows account."""
    try:
        if is_windows_elevated():
            _fail("release_pair_helper_current_user_elevation_unsupported")
        profile = known_folder_path(FOLDERID_LOCAL_APP_DATA) / APP_DIR_NAME
        return tuple(
            reject_reparse_chain(profile / name, reason="release_pair_helper_install_identity_invalid")
            for name in ("app", "data", "update-workspace")
        )
    except WindowsPathAuthorityError as error:
        raise ReleasePairHelperError(error.reason) from error


def _validate_frozen_product_request(
    request_path: Path, request: Mapping[str, Any], *, current_user: bool = False,
) -> tuple[Path, Path, Path]:
    """Stop a user-writable handoff from redirecting the elevated helper."""

    fields = ("active_app_root", "data_dir", "workspace")
    if not (os.name == "nt" and bool(getattr(sys, "frozen", False))):
        if current_user:
            _fail("release_pair_helper_current_user_requires_frozen_windows")
        return tuple(Path(request[field]) for field in fields)
    active, data, workspace = _current_user_product_roots() if current_user else _installed_product_roots()
    try:
        supplied = tuple(reject_reparse_chain(Path(request[field]), reason="release_pair_helper_request_path_invalid")
                         for field in fields)
        checked_request = reject_reparse_chain(request_path, reason="release_pair_helper_request_path_invalid")
    except (KeyError, OSError, TypeError) as error:
        raise ReleasePairHelperError(
            "release_pair_helper_request_path_invalid"
        ) from error
    except WindowsPathAuthorityError as error:
        raise ReleasePairHelperError(error.reason) from error
    if supplied != (active, data, workspace):
        _fail("release_pair_helper_request_path_mismatch")
    if (
        checked_request.parent != workspace
        or checked_request.name != f"helper-request-{request['operation_id']}.json"
    ):
        _fail("release_pair_helper_request_path_invalid")
    if current_user and Path(sys.executable).resolve().parent != workspace:
        _fail("release_pair_helper_current_user_executable_invalid")
    return active, data, workspace


def read_update_outcome(workspace: Path) -> dict[str, Any] | None:
    """Return the current operation's diagnostic, never an authorization."""
    workspace = Path(workspace)
    path = workspace / _LAST_RESULT_NAME
    if not path.is_file() or _is_reparse_point(path):
        return None
    try:
        value = pair_update._read_json(path, "release_pair_helper_result_invalid")
        journal = pair_update.read_pair_update_journal(workspace)
    except (OSError, pair_update.ReleasePairUpdateError):
        return None
    reason = value.get("reason")
    if (journal is None or value.get("contract") != HELPER_RESULT_CONTRACT
            or value.get("operation_id") != journal["operation_id"] or value.get("state") != journal["state"]
            or value.get("status") not in ("complete", "failed")
            or (reason is not None and (type(reason) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", reason)))):
        return None
    return {"status": value["status"], "reason": reason, "state": journal["state"],
            "previous_pair_restored": any(row["state"] == "rolled_back" for row in journal["history"])}


def _record_update_outcome(workspace: Path, operation_id: str, error: Exception | None = None) -> None:
    try:
        journal = pair_update.read_pair_update_journal(workspace)
        if journal is None or journal["operation_id"] != operation_id:
            return
        reason = getattr(error, "reason", None)
        if error is not None and (type(reason) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,127}", reason)):
            reason = type(error).__name__
        pair_update._write_json_atomic(workspace / _LAST_RESULT_NAME, {
            "contract": HELPER_RESULT_CONTRACT, "operation_id": operation_id, "state": journal["state"],
            "status": "complete" if error is None else "failed", "reason": reason,
        })
    except (OSError, pair_update.ReleasePairUpdateError):
        pass  # An unavailable diagnostic does not change the transaction result.


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GenshinCalc external pair updater")
    parser.add_argument("--current-user", action="store_true")
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    result_workspace = None
    try:
        request = _request(args.request)
        active, data, workspace = _validate_frozen_product_request(args.request, request, current_user=args.current_user)
        if args.current_user:
            result_workspace = workspace
        result = execute_external_pair_update(
            workspace,
            active,
            data,
            operation_id=request["operation_id"],
            cleanup=request["cleanup"],
            parent_pid=request["parent_pid"],
            parent_wait_timeout=request["parent_wait_timeout_seconds"],
            restart_relative=request["restart_relative"],
            current_user=args.current_user,
        )
        if result_workspace is not None:
            _record_update_outcome(result_workspace, request["operation_id"])
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:  # noqa: BLE001 - stable external-process envelope
        if result_workspace is not None:
            _record_update_outcome(result_workspace, request["operation_id"], error)
        print(
            json.dumps(
                {
                    "contract": HELPER_RESULT_CONTRACT,
                    "status": "failed",
                    "reason": getattr(error, "reason", type(error).__name__),
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ReleasePairHelperError",
    "execute_external_pair_update",
    "prepare_external_pair_update",
]
