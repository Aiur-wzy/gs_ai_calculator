"""Product coordinator for one atomic App/database update.

The running GUI may discover and stage a reviewed pair, but it never replaces
its own files or the active database.  This module binds an exact user
confirmation to the signed discovery document, prepares the durable handoff,
and launches the independently installed updater helper.  The helper waits
for the GUI process to exit before it performs the swap and rollback-capable
database subtransaction.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from threading import Event, Lock
from typing import Any
from uuid import uuid4

from genshin_app import release_pair_update
from genshin_app.database_release_reuse import (
    DatabaseReleaseReuseError,
    verified_reusable_database,
)
from genshin_app.database_transaction import profile_writer
from genshin_app.package_integrity import PackageIntegrityError, _read_package_manifest
from genshin_app.product_release_index import PER_USER_UPDATER_RELATIVE
from genshin_app.release_pair_discovery import discover_latest_release_pair
from genshin_app.release_pair_helper import (
    HELPER_RESULT_CONTRACT,
    discard_prepared_pair_update,
)
from genshin_app.release_pair_storage import (
    ReleasePairStorageError,
    preflight_before_download,
)
from genshin_app.release_pair_transport import (
    discard_orphaned_release_staging,
    stage_release_pair_artifacts,
)
from genshin_app.release_pair_trust import build_trust_verifier
from genshin_app.version import APP_DIR_NAME
from genshin_app.windows_known_folders import (
    FOLDERID_LOCAL_APP_DATA,
    FOLDERID_PROGRAM_DATA,
    WindowsPathAuthorityError,
    is_windows_elevated,
    known_folder_path,
    reject_effective_write_access,
    reject_reparse_chain,
)
from genshin_db.locking import LockBusyError, file_lock
from genshin_db.release_profile import (
    DatabaseReleaseError,
    load_database_release_manifest,
    load_release_pair_manifest,
    validate_database_live_receipt,
)

UPDATER_ARTIFACT_CONTRACT = "genshin-updater-artifact-v1"
UPDATER_ARTIFACT_SCHEMA_VERSION = 1
UPDATER_REQUEST_SCHEMA_VERSION = 1
MAX_UPDATER_RECEIPT_BYTES = 1024 * 1024


class ReleasePairCoordinatorError(RuntimeError):
    """Stable product-level failure which is safe to show in the GUI."""

    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


def _fail(reason: str, message: str | None = None) -> None:
    raise ReleasePairCoordinatorError(reason, message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
        "utf-8"
    )
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_updater_receipt(path: Path) -> dict[str, Any]:
    try:
        if _is_reparse_point(path) or not 2 <= path.stat().st_size <= MAX_UPDATER_RECEIPT_BYTES:
            _fail("release_pair_updater_receipt_invalid")
        value = json.loads(path.read_text(encoding="utf-8"))
    except ReleasePairCoordinatorError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleasePairCoordinatorError(
            "release_pair_updater_receipt_invalid"
        ) from error
    if type(value) is not dict or set(value) != {
        "contract",
        "schema_version",
        "build_provenance",
        "updater",
    }:
        _fail("release_pair_updater_receipt_invalid")
    updater = value["updater"]
    if (
        value["contract"] != UPDATER_ARTIFACT_CONTRACT
        or value["schema_version"] != UPDATER_ARTIFACT_SCHEMA_VERSION
        or type(value["build_provenance"]) is not dict
        or type(updater) is not dict
        or set(updater) != {
            "filename",
            "sha256",
            "size_bytes",
            "helper_contract",
            "request_schema_version",
        }
        or updater["helper_contract"] != HELPER_RESULT_CONTRACT
        or updater["request_schema_version"] != UPDATER_REQUEST_SCHEMA_VERSION
        or type(updater["filename"]) is not str
        or not updater["filename"]
        or Path(updater["filename"]).name != updater["filename"]
        or "\\" in updater["filename"]
        or "/" in updater["filename"]
        or ":" in updater["filename"]
        or type(updater["sha256"]) is not str
        or len(updater["sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in updater["sha256"])
        or type(updater["size_bytes"]) is not int
        or updater["size_bytes"] <= 0
    ):
        _fail("release_pair_updater_receipt_invalid")
    return value


def locate_installed_updater(program_data: Path | None = None) -> dict[str, Any]:
    """Verify the admin-installed helper and its build receipt.

    ``build_provenance`` is deliberately not compared with the current App:
    a protocol-compatible helper survives App updates.  If a future request
    schema changes, the explicit compatibility fields fail closed and the
    user must install a newer complete package.
    """

    if program_data is None:
        try:
            program_data = known_folder_path(FOLDERID_PROGRAM_DATA)
        except WindowsPathAuthorityError as error:
            raise ReleasePairCoordinatorError(
                "release_pair_updater_programdata_missing"
            ) from error
    try:
        root = reject_reparse_chain(
            Path(program_data), reason="release_pair_updater_reparse_forbidden"
        )
        directory = reject_reparse_chain(
            root / "GenshinCalc" / "Updater",
            reason="release_pair_updater_reparse_forbidden",
        )
    except WindowsPathAuthorityError as error:
        raise ReleasePairCoordinatorError(error.reason) from error
    receipt_path = directory / "updater-artifact.json"
    if root == directory or root not in directory.parents:
        _fail("release_pair_updater_path_invalid")
    for candidate in (root / "GenshinCalc", directory, receipt_path):
        if _is_reparse_point(candidate):
            _fail("release_pair_updater_reparse_forbidden")
    receipt = _read_updater_receipt(receipt_path)
    executable = directory / receipt["updater"]["filename"]
    try:
        resolved = executable.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError as error:
        raise ReleasePairCoordinatorError("release_pair_updater_missing") from error
    if (
        directory not in resolved.parents
        or _is_reparse_point(executable)
        or not resolved.is_file()
        or size != receipt["updater"]["size_bytes"]
        or _sha256(resolved) != receipt["updater"]["sha256"]
    ):
        _fail("release_pair_updater_artifact_mismatch")
    try:
        for protected in (directory, receipt_path, resolved):
            reject_effective_write_access(
                protected,
                reason="release_pair_updater_permissions_invalid",
            )
    except WindowsPathAuthorityError as error:
        raise ReleasePairCoordinatorError(error.reason) from error
    return {"executable": resolved, "receipt": receipt}


def _launch_updater(executable: Path, request_path: Path) -> dict[str, Any]:
    if os.name == "nt":
        parameters = subprocess.list2cmdline([str(request_path)])
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        shell.ShellExecuteW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        shell.ShellExecuteW.restype = ctypes.c_void_p
        result = shell.ShellExecuteW(
            None,
            "runas",
            str(executable),
            parameters,
            str(executable.parent),
            0,
        )
        if not result or int(result) <= 32:
            _fail("release_pair_updater_launch_failed")
        return {"method": "windows_runas", "accepted": True}
    try:
        process = subprocess.Popen(
            [str(executable), str(request_path)],
            cwd=str(executable.parent),
            shell=False,
            close_fds=True,
        )
    except OSError as error:
        raise ReleasePairCoordinatorError(
            "release_pair_updater_launch_failed"
        ) from error
    return {"method": "subprocess", "accepted": True, "pid": process.pid}


def _current_identity(data_dir: Path, active_app_root: Path) -> dict[str, Any]:
    try:
        pair = load_release_pair_manifest(
            data_dir / release_pair_update.LIVE_PAIR_MANIFEST_FILENAME
        )
        database = load_database_release_manifest(
            data_dir / release_pair_update.LIVE_DATABASE_MANIFEST_FILENAME
        )
        receipt_value = json.loads(
            (data_dir / release_pair_update.LIVE_DATABASE_RECEIPT_FILENAME).read_text(
                encoding="utf-8"
            )
        )
        receipt = validate_database_live_receipt(receipt_value)
    except (OSError, UnicodeError, json.JSONDecodeError, DatabaseReleaseError) as error:
        raise ReleasePairCoordinatorError(
            "release_pair_current_identity_unavailable"
        ) from error
    return {
        "app_root": active_app_root,
        "database": data_dir / "genshin.db",
        "pair_manifest": pair,
        "database_manifest": database,
        "live_receipt": receipt,
    }


def _locate_current_user_updater(app_root: Path, data_dir: Path) -> dict[str, Any]:
    try:
        if is_windows_elevated():
            _fail("release_pair_updater_current_user_elevation_unsupported")
        profile = known_folder_path(FOLDERID_LOCAL_APP_DATA) / APP_DIR_NAME
        if (app_root, data_dir) != (profile / "app", profile / "data"):
            _fail("release_pair_updater_current_user_path_mismatch")
        identity = _current_identity(data_dir, app_root)
        pair, database = identity["pair_manifest"], identity["database_manifest"]
        if release_pair_update.read_live_product_proof(data_dir, pair, database) is None:
            _fail("product_release_live_pair_proof_invalid")
        _root, manifest, entries = _read_package_manifest(app_root)
        expected = entries.get(PER_USER_UPDATER_RELATIVE)
        executable = reject_reparse_chain(app_root / PER_USER_UPDATER_RELATIVE,
                                           reason="release_pair_updater_reparse_forbidden")
        if (hashlib.sha256(manifest).hexdigest() != pair["app"]["artifact_manifest"]["sha256"]
                or expected is None or not executable.is_file() or _sha256(executable) != expected):
            _fail("release_pair_updater_artifact_mismatch")
    except (OSError, PackageIntegrityError, WindowsPathAuthorityError, release_pair_update.ReleasePairUpdateError) as error:
        raise ReleasePairCoordinatorError(getattr(error, "reason", "release_pair_updater_missing")) from error
    return {"executable": executable, "sha256": expected}


def _launch_current_user_updater(executable: Path, request: Path, *, app_root: Path, data_dir: Path) -> dict:
    located = _locate_current_user_updater(app_root, data_dir)
    if located["executable"] != executable:
        _fail("release_pair_updater_artifact_mismatch")
    workspace = reject_reparse_chain(data_dir.parent / "update-workspace", reason="release_pair_updater_path_invalid")
    if request.parent != workspace:
        _fail("release_pair_updater_path_invalid")
    staged = workspace / executable.name
    temporary = workspace / f".{executable.name}-{uuid4().hex}.tmp"
    try:
        with executable.open("rb") as source, temporary.open("xb") as destination:
            shutil.copyfileobj(source, destination)
        if _sha256(temporary) != located["sha256"]:
            _fail("release_pair_updater_artifact_mismatch")
        os.replace(temporary, staged)
        process = subprocess.Popen(
            [str(staged), "--current-user", str(request)], cwd=str(workspace), shell=False, close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            env={**os.environ, "PYINSTALLER_RESET_ENVIRONMENT": "1"},
        )
    except OSError as error:
        raise ReleasePairCoordinatorError("release_pair_updater_launch_failed") from error
    finally:
        temporary.unlink(missing_ok=True)
    return {"method": "windows_current_user", "accepted": True, "pid": process.pid}


class ReleasePairCoordinator:
    """One-process coordinator; all live replacement remains external."""

    def __init__(
        self,
        *,
        data_dir: Path,
        active_app_root: Path,
        workspace: Path,
        discovery_url: str,
        channel: str,
        trust_verifier,
        updater_locator: Callable[[], Mapping[str, Any]],
        launcher: Callable[[Path, Path], Mapping[str, Any]] = _launch_updater,
        restart_relative: str,
        parent_pid: int | None = None,
    ):
        self.data_dir = Path(data_dir).resolve()
        self.active_app_root = Path(active_app_root).resolve()
        self._supplied_workspace = Path(workspace).absolute()
        self.workspace = self._supplied_workspace.resolve()
        self.discovery_url = discovery_url
        self.channel = channel
        self.trust_verifier = trust_verifier
        self.updater_locator = updater_locator
        self.launcher = launcher
        self.restart_relative = restart_relative
        self.parent_pid = os.getpid() if parent_pid is None else parent_pid
        self._cancel = Event()
        self._operation_lock = Lock()
        self._progress = None

    def availability(self) -> dict[str, Any]:
        if not self.discovery_url:
            return {"available": False, "reason": "release_pair_discovery_url_unconfigured"}
        if self.trust_verifier is None:
            return {"available": False, "reason": "release_pair_trust_keys_unconfigured"}
        try:
            located = self.updater_locator()
            executable = Path(located["executable"])
        except (KeyError, OSError, TypeError, ReleasePairCoordinatorError):
            return {"available": False, "reason": "release_pair_updater_unavailable"}
        return {
            "available": True,
            "reason": None,
            "updater_protocol": HELPER_RESULT_CONTRACT,
            "updater_executable": executable.name,
        }

    def discover(self) -> dict[str, Any]:
        if self.channel == "stable" and self.discovery_url and self.trust_verifier is not None:
            from .product_release_index import discover_product_index, release_options
            from .release_pair_discovery import ReleasePairDiscoveryError, _now, _result

            try:
                index = discover_product_index(self.discovery_url,
                    self.data_dir / "cache" / "product-release-index.json", channel=self.channel,
                    trust_verifier=self.trust_verifier)
            except ReleasePairDiscoveryError as error:
                return _result(status="unavailable", source="remote", checked_at=_now(), reason=error.reason)
            current = _current_identity(self.data_dir, self.active_app_root)
            options = release_options(index, current["pair_manifest"], current["database_manifest"])
            if (self.active_app_root / "_internal" / "bundled").is_dir():
                # A paired portable package must move to an independent App before DB-only updates.
                options = [option for option in options if not option["reuse_app"]]
            latest = index["pairs"][index["combinations"][index["latest"]]]
            result = _result(status="available", source="signed_index", checked_at=_now(),
                             document=latest, trusted=True)
            result.update(product_index=index, update_options=options, can_offer_update=bool(options),
                          installed_pair=current["pair_manifest"], installed_database=current["database_manifest"],
                          installed_app_in_index=current["pair_manifest"]["app"]["app_release_id"]
                          in index["app_sequences"])
            return result
        return discover_latest_release_pair(
            url=self.discovery_url or None,
            cache_path=self.data_dir / "cache" / "release-pair-latest.json",
            channel=self.channel,
            trust_verifier=self.trust_verifier,
        )

    def cancel(self) -> dict[str, Any]:
        if self._progress and self._progress["cancellable"] is False:
            return {"cancel_requested": False, "reason": "installation_finishing"}
        self._cancel.set()
        return {"cancel_requested": True}

    def download_progress(self):
        return dict(self._progress) if self._progress is not None else None

    def _record_progress(self, value):
        self._progress = dict(value)

    def _request(self, operation_id: str) -> tuple[Path, dict[str, Any]]:
        request = {
            "workspace": str(self.workspace),
            "active_app_root": str(self.active_app_root),
            "data_dir": str(self.data_dir),
            "operation_id": operation_id,
            "cleanup": True,
            "parent_pid": self.parent_pid,
            "parent_wait_timeout_seconds": 120,
            "restart_relative": self.restart_relative,
        }
        path = self.workspace / f"helper-request-{operation_id}.json"
        _write_json_atomic(path, request)
        return path, request

    def _launch(self, operation_id: str) -> dict[str, Any]:
        located = self.updater_locator()
        executable = Path(located["executable"])
        request_path, _request = self._request(operation_id)
        launched = dict(self.launcher(executable, request_path))
        if launched.get("accepted") is not True:
            _fail("release_pair_updater_launch_failed")
        return {
            "action": "handoff_launched",
            "operation_id": operation_id,
            "requires_exit": True,
            "requires_restart": True,
            "launcher": launched,
        }

    def _clear_orphaned_staging(self, expected_journal):
        if not os.path.lexists(self.workspace / "staging"):
            return
        profile_lock = self.data_dir / ".update.lock"
        if os.path.lexists(profile_lock) and _is_reparse_point(profile_lock):
            _fail("release_pair_transport_workspace_invalid")
        try:
            with profile_writer(self.data_dir, timeout=0):
                current = release_pair_update.read_pair_update_journal(self.workspace)
                if current != expected_journal or (current is not None and current["state"] != "cleanup_complete"):
                    _fail("release_pair_update_recovery_required")
                discard_orphaned_release_staging(self._supplied_workspace)
        except LockBusyError as error:
            raise ReleasePairCoordinatorError("release_pair_update_profile_busy") from error
        except OSError as error:
            raise ReleasePairCoordinatorError("release_pair_transport_cleanup_failed") from error

    @contextmanager
    def _one_operation(self):
        if not self._operation_lock.acquire(blocking=False):
            _fail("release_pair_update_busy")
        try:
            try:
                with file_lock(self.workspace / ".release-prepare.lock", timeout=0):
                    yield
            except LockBusyError as error:
                raise ReleasePairCoordinatorError("release_pair_update_busy") from error
        finally:
            self._operation_lock.release()

    def prepare_and_launch(
        self,
        discovery_result: Mapping[str, Any],
        *,
        confirmed_document_sha256: str,
        user_confirmed: bool,
        cancel_check: Callable[[], bool] | None = None,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if user_confirmed is not True:
            _fail("release_pair_update_confirmation_required")

        def cancelled() -> bool:
            return self._cancel.is_set() or (cancel_check is not None and cancel_check())

        def record(value: dict[str, Any]) -> None:
            self._record_progress(value)
            if progress is not None:
                progress(dict(value))

        with self._one_operation():
            self._cancel.clear()
            self._progress = None
            if cancelled():
                _fail("release_pair_artifact_download_cancelled")
            if self.availability()["available"] is not True:
                _fail("release_pair_update_coordinator_unavailable")
            latest = self.discover()
            reuse_installed_app = False
            selected = None
            product_selection = None
            if latest.get("product_index") is not None:
                from .release_pair_discovery import _now, _result

                selected = next((row for row in latest["update_options"]
                                 if row["confirmation_digest"] == confirmed_document_sha256), None)
                if (selected is None or not any(row["confirmation_digest"] == confirmed_document_sha256
                                                for row in discovery_result.get("update_options", []))):
                    _fail("release_pair_update_confirmation_mismatch")
                product_selection = {"signed_index": latest["product_index"]["signed_index"],
                                     "confirmation_digest": confirmed_document_sha256}
                latest = _result(status="available", source="signed_index", checked_at=_now(),
                                 document=selected["document"], trusted=True)
                reuse_installed_app = selected["reuse_app"]
            elif (
                type(confirmed_document_sha256) is not str
                or latest.get("document_sha256") != confirmed_document_sha256
                or discovery_result.get("document_sha256") != confirmed_document_sha256
            ):
                _fail("release_pair_update_confirmation_mismatch")
            if latest.get("authoritative") is not True:
                _fail("release_pair_discovery_not_authoritative")

            existing = release_pair_update.read_pair_update_journal(self.workspace)
            if existing is not None and existing["state"] == "handoff_ready":
                target_id = latest["pair_manifest"]["release_pair_id"]
                if cancelled():
                    _fail("release_pair_artifact_download_cancelled")
                handoff = release_pair_update._read_handoff(self.workspace, existing)
                previous_selection = handoff["target"]["trust_assertion"].get("product_selection")
                if existing["target_release_pair_id"] == target_id and previous_selection == product_selection:
                    inspected = release_pair_update.inspect_pair_update_handoff(
                        self.workspace,
                        operation_id=existing["operation_id"],
                    )
                    if inspected["pair_manifest"] != latest["pair_manifest"]:
                        _fail("release_pair_update_recovery_required")
                    return self._launch(existing["operation_id"])
                if selected is None:
                    _fail("release_pair_update_recovery_required")
                discard_prepared_pair_update(self.workspace, self.active_app_root, self.data_dir,
                                             operation_id=existing["operation_id"])
                existing = release_pair_update.read_pair_update_journal(self.workspace)
            if existing is not None and existing["state"] != "cleanup_complete":
                _fail("release_pair_update_recovery_required")

            current = _current_identity(self.data_dir, self.active_app_root)
            try:
                reuse_installed_database = (
                    verified_reusable_database(
                        self.data_dir, latest["database_manifest"]
                    )
                    is not None
                )
            except (KeyError, DatabaseReleaseReuseError) as error:
                reason = getattr(error, "reason", "database_reuse_identity_invalid")
                raise ReleasePairCoordinatorError(reason) from error
            if selected is not None and selected["reuse_database"] != reuse_installed_database:
                _fail("release_pair_update_confirmation_mismatch")
            self._clear_orphaned_staging(existing)
            artifacts = latest.get("artifacts")
            try:
                if type(artifacts) is not dict:
                    _fail("release_pair_artifacts_invalid")
                app_artifact = artifacts["app"]
                database_artifact = artifacts["database"]
                storage_plan = preflight_before_download(
                    workspace=self.workspace,
                    active_app_root=self.active_app_root,
                    data_dir=self.data_dir,
                    app_archive_bytes=app_artifact["size_bytes"],
                    app_unpacked_bytes=app_artifact["unpacked_size_bytes"],
                    app_unpacked_files=app_artifact["unpacked_files"],
                    target_database_bytes=database_artifact.get("unpacked_size_bytes", database_artifact["size_bytes"]),
                    current_database_bytes=current["database_manifest"]["database"][
                        "size_bytes"
                    ],
                    reuse_installed_database=reuse_installed_database,
                    **({"reuse_installed_app": reuse_installed_app,
                        "database_archive_bytes": (database_artifact["size_bytes"]
                                                    if database_artifact["format"] == "sqlite-gzip" else 0)}
                        | {"download_cache": self.workspace / "artifact-cache"}
                       if selected is not None else {}),
                )
            except (KeyError, TypeError, ReleasePairStorageError) as error:
                reason = getattr(error, "reason", "release_pair_storage_input_invalid")
                raise ReleasePairCoordinatorError(reason) from error

            stage_release_pair_artifacts(
                latest,
                self.workspace,
                confirmed_document_sha256=latest["document_sha256"] if selected is not None else confirmed_document_sha256,
                cancel_check=cancelled,
                reuse_installed_database=reuse_installed_database,
                **({"reuse_installed_app": reuse_installed_app, "cache_dir": self.workspace / "artifact-cache",
                    "progress": record} if selected is not None else {}),
            )
            try:
                if cancelled():
                    _fail("release_pair_artifact_download_cancelled")
                if selected is not None:
                    from genshin_db.locking import lock_is_held
                    if lock_is_held(self.data_dir / ".update.lock"):
                        _fail("release_pair_update_profile_busy")
                record({"phase": "verifying", "cancellable": False})
                production = self.channel == "stable"
                trust_assertion = (
                    release_pair_update.create_signed_discovery_trust_assertion(
                        latest,
                        latest["pair_manifest"],
                        latest["database_manifest"],
                        production=production,
                        **({"product_selection": product_selection} if product_selection is not None else {}),
                    )
                )
                staging = self.workspace / "staging"
                prepared = release_pair_update.prepare_release_pair_handoff(
                    self.workspace,
                    None if reuse_installed_app else staging / "app",
                    (
                        None
                        if reuse_installed_database
                        else staging / "genshin.db"
                    ),
                    latest["database_manifest"],
                    latest["pair_manifest"],
                    user_confirmed=True,
                    production=production,
                    trust_assertion=trust_assertion,
                    current=current,
                    reuse_installed_database=reuse_installed_database,
                )
            except Exception:
                self._progress = None
                # Before a journal exists, staging has no recovery authority
                # and may be removed.  Once prepare committed a journal, only
                # the external helper/explicit recovery path owns cleanup.
                if release_pair_update.read_pair_update_journal(self.workspace) == existing:
                    shutil.rmtree(self.workspace / "staging", ignore_errors=True)
                raise
            if prepared.get("status") == "already_current":
                shutil.rmtree(self.workspace / "staging", ignore_errors=True)
                return {
                    "action": "already_current",
                    "release_pair_id": prepared["release_pair_id"],
                    "requires_exit": False,
                    "requires_restart": False,
                }
            launched = self._launch(prepared["operation_id"])
            record({"phase": "waiting_for_exit", "cancellable": False})
            return {
                **launched,
                "release_pair_id": latest["release_pair_id"],
                "database_release_id": latest["database_release_id"],
                "database_mode": (
                    "reuse_installed" if reuse_installed_database else "install"
                ),
                "storage_preflight": storage_plan,
            }


def build_default_release_pair_coordinator(data_dir: Path):
    """Compose the frozen product coordinator from immutable build inputs."""

    from genshin_app.paths import is_frozen
    from genshin_app.version import (
        DATABASE_RELEASE_CHANNEL,
        DATABASE_RELEASE_MANIFEST_URL,
        DATABASE_RELEASE_TRUST_KEYS,
    )

    if not is_frozen():
        return None
    verifier = build_trust_verifier(DATABASE_RELEASE_TRUST_KEYS)
    active = Path(sys.executable).resolve().parent
    data = Path(data_dir).resolve()
    updater_locator, launcher = locate_installed_updater, _launch_updater
    if os.name == "nt" and DATABASE_RELEASE_CHANNEL == "stable":
        try:
            profile = known_folder_path(FOLDERID_LOCAL_APP_DATA) / APP_DIR_NAME
        except WindowsPathAuthorityError:
            profile = None
        if profile is not None and (active, data) == (profile / "app", profile / "data"):
            updater_locator = partial(_locate_current_user_updater, active, data)
            launcher = partial(_launch_current_user_updater, app_root=active, data_dir=data)
    return ReleasePairCoordinator(
        data_dir=data,
        active_app_root=active,
        workspace=data.parent / "update-workspace",
        discovery_url=DATABASE_RELEASE_MANIFEST_URL,
        channel=DATABASE_RELEASE_CHANNEL,
        trust_verifier=verifier,
        updater_locator=updater_locator,
        launcher=launcher,
        restart_relative=Path(sys.executable).name,
    )


__all__ = [
    "ReleasePairCoordinator",
    "ReleasePairCoordinatorError",
    "build_default_release_pair_coordinator",
    "locate_installed_updater",
]
