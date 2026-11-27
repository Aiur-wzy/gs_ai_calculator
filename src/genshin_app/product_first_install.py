"""Product port for a signed, unelevated first installation on Windows."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from genshin_app import version
from genshin_app.database_transaction import (
    ACTIVATION_JOURNAL,
    atomic_json,
    journal_pending,
    profile_writer,
    read_activation_journal,
)
from genshin_app.installation_timing import InstallationTiming
from genshin_app.product_install_contract import (
    FIRST_INSTALL_CONTRACT,
    REGISTRATION_FILENAME,
    FirstInstallError,
)
from genshin_app.product_release_index import (
    LIVE_PAIR_PROOF_FILENAME,
    digest,
    discover_product_index,
    first_install_option,
    validate_product_release_index,
)
from genshin_app.release_pair_discovery import (
    MAX_DOCUMENT_BYTES,
    _now,
    _result,
    validate_latest_document,
)
from genshin_app.release_pair_helper import _install_in_fresh_profile
from genshin_app.release_pair_storage import preflight_before_download
from genshin_app.release_pair_transport import stage_release_pair_artifacts
from genshin_app.release_pair_trust import build_trust_verifier
from genshin_app.release_pair_update import (
    LIVE_DATABASE_MANIFEST_FILENAME,
    LIVE_DATABASE_RECEIPT_FILENAME,
    LIVE_PAIR_MANIFEST_FILENAME,
    PAIR_UPDATE_JOURNAL_FILENAME,
    _verify_app,
    create_signed_discovery_trust_assertion,
    prepare_release_pair_handoff,
    read_live_product_proof,
)
from genshin_app.windows_known_folders import (
    FOLDERID_LOCAL_APP_DATA,
    is_reparse_point,
    known_folder_path,
    reject_reparse_chain,
)
from genshin_app.windows_known_folders import (
    is_windows_elevated as _windows_is_elevated,
)
from genshin_db.locking import LockBusyError, file_lock
from genshin_db.release_profile import (
    load_database_release_manifest,
    load_release_pair_manifest,
)

_PROGRAMS_FOLDER_ID = "a77f5d77-2e2b-44c3-a6a2-aba601054a51"
_START_MENU_SHORTCUT = "原神拉表（当前用户）.lnk"


def _emit_phase(progress, phase):
    if progress is not None:
        progress({"phase": phase})


class _FirstInstallCoordinator:
    _verifies_published_app = False
    def __init__(self, *, local: Path, url: str, trust_verifier, programs_folder=None):
        self._local = local
        self._url = url
        self._trust_verifier = trust_verifier
        self._profile = local / version.APP_DIR_NAME
        self._cache = local / f".{version.APP_DIR_NAME}-install-cache"
        self._programs_folder = programs_folder

    def _installed_registration(self):
        profile = reject_reparse_chain(self._profile, reason="product_first_install_profile_path_invalid")
        try:
            registration = reject_reparse_chain(profile / REGISTRATION_FILENAME,
                                                reason="product_uninstall_registration_invalid")
            if not registration.is_file() or registration.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("registration size")
            row = json.loads(registration.read_text(encoding="utf-8"))
        except (OSError, RuntimeError, ValueError) as error:
            raise FirstInstallError("product_uninstall_registration_invalid") from error
        if (type(row) is not dict or row.get("contract") != FIRST_INSTALL_CONTRACT
                or row.get("scope") != "current_user" or row.get("app_directory") != "app"
                or row.get("data_directory") != "data"):
            raise FirstInstallError("product_uninstall_registration_invalid")
        return profile, row

    def _signed_installed_source(self, data, row):
        has_database = (data / LIVE_PAIR_MANIFEST_FILENAME).is_file()
        if has_database:
            reject_reparse_chain(data, reason="product_uninstall_profile_path_invalid")
            controls = [reject_reparse_chain(data / name, reason="product_uninstall_profile_path_invalid")
                        for name in (LIVE_PAIR_MANIFEST_FILENAME, LIVE_DATABASE_MANIFEST_FILENAME, LIVE_PAIR_PROOF_FILENAME)]
            pair, database = load_release_pair_manifest(controls[0]), load_database_release_manifest(controls[1])
            document = read_live_product_proof(data, pair, database)
            if document is None:
                raise FirstInstallError("product_uninstall_release_proof_missing")
        elif row.get("database_state") == "database_not_installed":
            document, trusted = validate_latest_document(
                row.get("signed_app_source"), expected_channel="stable",
                trust_verifier=self._trust_verifier, allow_unsigned_dev=False,
            )
            if not trusted or document["artifacts"]["app"]["format"] != "app-zip":
                raise FirstInstallError("product_uninstall_release_proof_missing")
        else:
            raise FirstInstallError("product_uninstall_release_proof_missing")
        return document, has_database

    def uninstall_preview(self):
        profile, row = self._installed_registration()
        data = profile / "data"
        self._require_update_idle(profile, data)
        app = reject_reparse_chain(profile / "app", reason="product_uninstall_app_path_invalid")
        document, _ = self._signed_installed_source(data, row)
        pair = document["pair_manifest"]
        manifest = _verify_app(app, pair, independent=True)
        return {"app_release_id": pair["app"]["app_release_id"], "app_directory": str(app),
                "preserved_data_directory": str(data),
                "confirmation_digest": digest({"registration": row, "signed_release": document,
                                               "manifest_sha256": manifest["manifest_sha256"]})}

    def _pair_journal(self, profile):
        from .release_pair_update import read_pair_update_journal

        workspace = profile / "update-workspace"
        if not os.path.lexists(workspace):
            return None
        workspace = reject_reparse_chain(workspace, reason="product_repair_profile_path_invalid")
        journal_path = workspace / PAIR_UPDATE_JOURNAL_FILENAME
        if os.path.lexists(journal_path):
            reject_reparse_chain(journal_path, reason="product_repair_profile_path_invalid")
        return read_pair_update_journal(workspace)

    def _require_update_idle(self, profile, data, *, allow_prepared=False):
        for path in (data, data / ".update.lock", data / ACTIVATION_JOURNAL):
            if os.path.lexists(path):
                reject_reparse_chain(path, reason="product_repair_profile_path_invalid")
        if journal_pending(read_activation_journal(data)):
            raise FirstInstallError("product_setup_database_recovery_pending")
        journal = self._pair_journal(profile)
        if journal and journal["state"] != "cleanup_complete" and not (
            allow_prepared and journal["state"] == "handoff_ready"
        ):
            raise FirstInstallError("product_setup_pair_recovery_pending")

    def _repair_profile(self):
        profile, row = self._installed_registration()
        app = reject_reparse_chain(profile / "app", reason="product_repair_app_path_invalid")
        data_path = profile / "data"
        if row.get("database_state") == "database_not_installed" and not os.path.lexists(
            data_path / LIVE_PAIR_MANIFEST_FILENAME
        ):
            raise FirstInstallError("product_repair_database_not_installed")
        data = reject_reparse_chain(data_path, reason="product_repair_data_path_invalid")
        self._require_update_idle(profile, data, allow_prepared=True)
        workspace = profile / "update-workspace"
        cache = data / "cache"
        paths = (cache, cache / "product-release-index.json", cache / "product-release-index.lock",
                 data / "genshin.db", data / LIVE_DATABASE_RECEIPT_FILENAME)
        for path in paths:
            if os.path.lexists(path):
                reject_reparse_chain(path, reason="product_repair_profile_path_invalid")
        if row.get("installation_state") == "uninstalled" or not (data / "genshin.db").is_file():
            raise FirstInstallError("product_repair_signed_install_required")
        document, has_database = self._signed_installed_source(data, row)
        if not has_database:
            raise FirstInstallError("product_repair_signed_install_required")
        return profile, app, data, workspace, document

    def interrupted_update_preview(self):
        """Offer a separate recovery action without selecting any new release."""
        if not os.path.lexists(self._profile / "update-workspace"):
            return None
        profile, row = self._installed_registration()
        if row.get("installation_state") == "uninstalled":
            return None
        journal = self._pair_journal(profile)
        if not journal or journal["state"] not in {
            "handoff_ready", "activation_started", "postchecked", "committed", "operational", "rolled_back",
        }:
            return None
        return {"mode": "recover_interrupted_update", "install_directory": str(profile),
                "operation_id": journal["operation_id"], "update_state": journal["state"],
                "previous_pair_id": journal["rollback_release_pair_id"],
                "target_pair_id": journal["target_release_pair_id"],
                "confirmation_digest": digest(journal)}

    def recover_interrupted_update(self, confirmation_digest, *, user_confirmed, progress=None):
        from .database_legacy_processes import packaged_peer_processes
        from .release_pair_helper import recover_interrupted_pair_update

        if user_confirmed is not True:
            raise FirstInstallError("product_first_install_confirmation_required")
        lock = self._cache / "first-install.lock"
        self._check_cache_path(lock)
        try:
            with file_lock(lock, timeout=0):
                choice = self.interrupted_update_preview()
                if choice is None or choice["confirmation_digest"] != confirmation_digest:
                    raise FirstInstallError("product_first_install_confirmation_mismatch")
                root = Path(choice["install_directory"])
                data = reject_reparse_chain(root / "data", reason="product_repair_data_path_invalid")
                if os.path.lexists(data / ".update.lock"):
                    reject_reparse_chain(data / ".update.lock", reason="product_repair_data_path_invalid")
                with profile_writer(data, timeout=0):
                    if self.interrupted_update_preview() != choice:
                        raise FirstInstallError("product_first_install_confirmation_mismatch")
                    if packaged_peer_processes(include_updaters=True):
                        raise FirstInstallError("database_legacy_instance_running")
                    _emit_phase(progress, "committing")
                    return recover_interrupted_pair_update(root / "update-workspace", root / "app", data,
                                                           operation_id=choice["operation_id"], user_confirmed=True)
        except LockBusyError as error:
            raise FirstInstallError("product_first_install_busy") from error

    def _repair_coordinator(self, app, data, workspace, *, launcher=None):
        from .release_pair_coordinator import ReleasePairCoordinator

        executable = Path(sys.executable).resolve()
        if executable.is_relative_to(app):
            raise FirstInstallError("product_repair_requires_standalone_setup")
        return ReleasePairCoordinator(data_dir=data, active_app_root=app, workspace=workspace,
                                      discovery_url=self._url, channel="stable", trust_verifier=self._trust_verifier,
                                      updater_locator=lambda: {"executable": executable},
                                      restart_relative="GenshinCalcGUI.exe",
                                      **({"launcher": launcher} if launcher is not None else {}))

    def _repair_selection(self, coordinator, installed):
        discovered = coordinator.discover()
        if not discovered.get("authoritative") or not discovered.get("product_index"):
            raise FirstInstallError(discovered.get("reason") or "product_repair_release_unavailable")
        pair, database = installed["pair_manifest"], installed["database_manifest"]
        indexed = discovered["product_index"]["pairs"].get(pair["release_pair_id"])
        # Both documents have passed signature validation. A new signature or a
        # qualified envelope may attest to the same installed release bytes.
        if (discovered["installed_pair"] != pair or discovered["installed_database"] != database
                or indexed is None or indexed["pair_manifest"] != pair
                or indexed["database_manifest"] != database or indexed["artifacts"] != installed["artifacts"]):
            raise FirstInstallError("product_repair_installed_pair_not_in_signed_index")
        choice = next((option for option in discovered["update_options"]
                       if option["mode"] == "app_only" and option["reuse_database"] and not option["reuse_app"]
                       and option["document"]["database_manifest"] == database), None)
        if choice is None:
            raise FirstInstallError("product_repair_no_signed_app_successor")
        return discovered, choice

    def repair_preview(self):
        """Offer a signed successor App for this exact installed database."""
        profile, app, data, workspace, current = self._repair_profile()
        _, choice = self._repair_selection(self._repair_coordinator(app, data, workspace), current)
        document = choice["document"]
        pair = document["pair_manifest"]
        return {"mode": "repair_installed_app", "install_directory": str(profile),
                "installed_app_release_id": current["pair_manifest"]["app"]["app_release_id"],
                "app_release_id": pair["app"]["app_release_id"],
                "database_release_id": document["database_manifest"]["database_release_id"],
                "release_pair_id": pair["release_pair_id"], "confirmation_digest": choice["confirmation_digest"],
                "download_bytes": {"app": document["artifacts"]["app"]["size_bytes"], "database": 0}}

    def repair_install(self, confirmation_digest, *, user_confirmed, cancel_check=None, progress=None):
        """Apply the signed App successor using Setup instead of the old installed updater."""
        from .database_legacy_processes import packaged_peer_processes
        from .release_pair_helper import execute_external_pair_update
        from .release_pair_update import inspect_pair_update_handoff

        if user_confirmed is not True:
            raise FirstInstallError("product_first_install_confirmation_required")

        def check_quiet(profile, data):
            if cancel_check is not None and cancel_check():
                raise FirstInstallError("product_first_install_cancelled")
            if packaged_peer_processes(include_updaters=True):
                raise FirstInstallError("database_legacy_instance_running")
            self._require_update_idle(profile, data, allow_prepared=True)

        lock = self._cache / "first-install.lock"
        self._check_cache_path(lock)
        try:
            with file_lock(lock, timeout=0):
                profile, app, data, workspace, current = self._repair_profile()
                check_quiet(profile, data)
                completed = None

                def activate(_executable, request):
                    nonlocal completed
                    match = re.fullmatch(r"helper-request-([0-9a-f]{32})\.json", request.name)
                    if match is None or request.parent != workspace:
                        raise FirstInstallError("product_repair_handoff_invalid")
                    operation_id = match[1]
                    with profile_writer(data, timeout=0):
                        check_quiet(profile, data)
                        handoff = inspect_pair_update_handoff(workspace, operation_id=operation_id)
                        if (handoff["database_mode"] != "reuse_installed" or not handoff["independent_app"]
                                or handoff["pair_manifest"] != choice["document"]["pair_manifest"]):
                            raise FirstInstallError("product_repair_handoff_invalid")
                        _emit_phase(progress, "committing")
                        completed = execute_external_pair_update(
                            workspace, app, data, operation_id=operation_id, current_user=True)
                    return {"accepted": True, "method": "setup_in_process"}

                coordinator = self._repair_coordinator(app, data, workspace, launcher=activate)
                discovered, choice = self._repair_selection(coordinator, current)
                if confirmation_digest != choice["confirmation_digest"]:
                    raise FirstInstallError("product_first_install_confirmation_mismatch")
                _emit_phase(progress, "downloading")
                coordinator.prepare_and_launch(discovered, confirmed_document_sha256=confirmation_digest,
                                               user_confirmed=True, cancel_check=cancel_check, progress=progress)
                if completed is None or completed.get("status") != "complete":
                    raise FirstInstallError("product_repair_incomplete")
        except LockBusyError as error:
            raise FirstInstallError("product_first_install_busy") from error
        return {"status": "complete", "install_directory": str(profile),
                "release_pair_id": completed["release_pair_id"], "database_state": "preserved",
                "requires_restart": True}

    def uninstall(self, confirmation_digest, *, user_confirmed, cancel_check=None, progress=None):
        """Move only the authenticated App tree out of service, retaining the profile."""
        from genshin_app.database_legacy_processes import packaged_peer_processes

        if user_confirmed is not True:
            raise FirstInstallError("product_uninstall_confirmation_required")
        if cancel_check is not None and cancel_check():
            raise FirstInstallError("product_uninstall_cancelled")
        profile, registration = self._installed_registration()
        data = profile / "data"
        self._require_update_idle(profile, data)
        lock = self._cache / "first-install.lock"
        self._check_cache_path(lock)
        try:
            with file_lock(lock, timeout=0), profile_writer(data, timeout=0):
                if packaged_peer_processes(include_updaters=True):
                    raise FirstInstallError("database_legacy_instance_running")
                selected = self.uninstall_preview()
                if selected["confirmation_digest"] != confirmation_digest:
                    raise FirstInstallError("product_uninstall_confirmation_mismatch")
                if cancel_check is not None and cancel_check():
                    raise FirstInstallError("product_uninstall_cancelled")
                app = Path(selected["app_directory"])
                removed = self._cache / f"uninstalled-app-{uuid4().hex}"
                _emit_phase(progress, "committing")
                # Record intent before moving the only installed App. If the
                # process exits after the rename, the signed source remains
                # recoverable from this registration. The data is never moved.
                registration_path = profile / REGISTRATION_FILENAME
                atomic_json(registration_path, {**registration, "installation_state": "uninstalled"})
                try:
                    app.rename(removed)
                except OSError as error:
                    atomic_json(registration_path, registration)
                    raise FirstInstallError("product_uninstall_move_failed") from error
                shortcut = self._remove_shortcut(registration)
                cleanup_pending = not self._clean_removed_app(removed)
        except LockBusyError as error:
            raise FirstInstallError("product_first_install_busy") from error
        return {"status": "uninstalled", "preserved_data_directory": str(data),
                "shortcut": shortcut, "cleanup_pending": cleanup_pending}

    def _clean_removed_app(self, path):
        try:
            self._check_cache_path(path)
            if path.exists() and (path.resolve() != self._cache / path.name or not path.is_dir()):
                return False
            shutil.rmtree(path, ignore_errors=False)
        except FileNotFoundError:
            return not path.exists()
        except (OSError, FirstInstallError):
            return False
        return True

    def _registered_shortcut_path(self):
        if self._programs_folder is None:
            return None
        directory = reject_reparse_chain(self._programs_folder(),
                                         reason="product_start_menu_path_invalid")
        return directory / _START_MENU_SHORTCUT

    def _remove_shortcut(self, row):
        expected = row.get("start_menu_shortcut_sha256")
        if not isinstance(expected, str):
            return "not_registered"
        try:
            shortcut = self._registered_shortcut_path()
            if shortcut is None or not os.path.lexists(shortcut):
                return "not_found"
            if (is_reparse_point(shortcut) or not shortcut.is_file()
                    or shortcut.stat().st_size > 1024 * 1024
                    or hashlib.sha256(shortcut.read_bytes()).hexdigest() != expected):
                return "preserved_modified"
            shortcut.unlink()
        except (OSError, RuntimeError):
            return "cleanup_pending"
        return "removed"

    def _write_shortcut(self, registration):
        from genshin_app.shortcuts import _windows_link

        try:
            shortcut = self._registered_shortcut_path()
            target = self._profile / "app" / "GenshinCalcGUI.exe"
            if shortcut is None or not target.is_file():
                return "unavailable"
            if os.path.lexists(shortcut):
                return "preserved_existing"
            raw = _windows_link({"target": str(target), "arguments": "",
                                 "working_directory": str(target.parent), "description": "原神拉表"})
            if not 1 <= len(raw) <= 1024 * 1024:
                return "creation_failed"
            row = {**registration, "start_menu_shortcut_sha256": hashlib.sha256(raw).hexdigest()}
            temporary = shortcut.with_name(f".{uuid4().hex}.lnk")
            try:
                with temporary.open("xb") as stream:
                    stream.write(raw)
                atomic_json(self._profile / REGISTRATION_FILENAME, row)
                # Windows rename fails if a user creates the name concurrently.
                # Keep the same exclusive publication semantics in portable tests.
                if os.name == "nt":
                    temporary.rename(shortcut)
                else:
                    os.link(temporary, shortcut)
            finally:
                temporary.unlink(missing_ok=True)
        except FileExistsError:
            return "preserved_existing"
        except (OSError, RuntimeError):
            return "creation_failed"
        return "created"

    def _require_new_profile(self) -> None:
        if os.path.lexists(self._profile):
            raise FirstInstallError("product_first_install_existing_profile")

    def _publish_first_profile(self, profile, registration):
        profile.rename(self._profile)

    def _reinstall_selection(self):
        if not os.path.lexists(self._profile):
            return None
        if os.path.lexists(self._profile / "app"):
            raise FirstInstallError("product_first_install_existing_profile")
        try:
            profile, row = self._installed_registration()
        except (FirstInstallError, RuntimeError) as error:
            raise FirstInstallError("product_first_install_existing_profile") from error
        if row.get("installation_state") != "uninstalled":
            raise FirstInstallError("product_first_install_existing_profile")
        data = profile / "data"
        self._require_update_idle(profile, data)
        document, has_database = self._signed_installed_source(data, row)
        return {"mode": "reinstall", "document": document, "has_database": has_database,
                "confirmation_digest": digest({"mode": "reinstall", "signed_release": document,
                                               "registration": row})}

    def _check_cache_path(self, *paths: Path) -> None:
        if any(os.path.lexists(path) and is_reparse_point(path) for path in (self._cache, *paths)):
            raise FirstInstallError("product_first_install_cache_path_invalid")

    def _discover(self, *, app_only=False) -> tuple[dict | None, dict]:
        reinstall = self._reinstall_selection()
        if reinstall is not None:
            return None, reinstall
        cache_path = self._cache / "release-index.json"
        self._check_cache_path(cache_path, cache_path.with_suffix(".lock"))
        index = discover_product_index(
            self._url, cache_path, channel="stable", trust_verifier=self._trust_verifier,
        )
        return index, first_install_option(index, app_only=app_only)

    @contextmanager
    def _private_transaction(self, *, reinstall=None):
        lock = self._cache / "first-install.lock"
        transaction = self._cache / "pending-install"
        self._check_cache_path(lock, transaction)
        try:
            with file_lock(lock, timeout=0):
                if reinstall is None:
                    self._require_new_profile()
                elif self._reinstall_selection() != reinstall:
                    raise FirstInstallError("product_first_install_confirmation_mismatch")
                self._check_cache_path(lock, transaction)
                if transaction.exists():
                    checked = reject_reparse_chain(transaction, reason="product_first_install_cache_path_invalid")
                    if checked != self._local / self._cache.name / "pending-install" or not checked.is_dir():
                        raise FirstInstallError("product_first_install_cache_path_invalid")
                    shutil.rmtree(checked)
                transaction.mkdir()
                try:
                    yield transaction
                finally:
                    # An interrupted bootstrap may leave this directory; the next lock owner cleans it.
                    self._cleanup_private_transaction(transaction)
        except LockBusyError as error:
            raise FirstInstallError("product_first_install_busy") from error

    def _cleanup_private_transaction(self, transaction):
        shutil.rmtree(transaction, ignore_errors=True)

    def _local_pair(self, index_path):
        path = reject_reparse_chain(Path(index_path), reason="product_first_install_local_index_invalid")
        if path.stat().st_size > MAX_DOCUMENT_BYTES:
            raise FirstInstallError("product_first_install_local_index_invalid")
        self._require_new_profile()
        index = validate_product_release_index(json.loads(path.read_text(encoding="utf-8")),
                                               channel="stable", trust_verifier=self._trust_verifier)
        return index, first_install_option(index)

    def preview(self, *, app_only: bool = False, local_index=None) -> dict:
        """Return the selected versions, download sizes, and confirmation digest."""
        _, choice = (self._local_pair(local_index) if local_index is not None else self._discover(app_only=app_only))
        document = choice["document"]
        app, database = document["pair_manifest"]["app"], document["database_manifest"]
        artifacts = document["artifacts"]
        reinstall = choice.get("mode") == "reinstall"
        mode = ("local_pair" if local_index is not None else "reinstall" if reinstall else "app_only" if app_only else None)
        return {
            "contract": FIRST_INSTALL_CONTRACT,
            "status": "confirmation_required",
            "confirmation_digest": choice["confirmation_digest"],
            "release_pair_id": document["pair_manifest"]["release_pair_id"],
            "app_version": app["app_version"],
            "app_release_id": app["app_release_id"],
            "database_release_id": (database["database_release_id"]
                                    if not reinstall or choice["has_database"] else None),
            "install_directory": str(self._profile),
            "download_bytes": {"app": artifacts["app"]["size_bytes"],
                               "database": 0 if app_only or reinstall else artifacts["database"]["size_bytes"]},
            **({"mode": mode} if mode else {}),
        }

    def install(
        self,
        *,
        user_confirmed: bool,
        confirmation_digest: str,
        cancel_check: Callable[[], bool] | None = None,
        app_only: bool = False,
        progress=None,
        local_index=None,
        local_artifacts=None,
    ) -> dict:
        """Verify and install both artifacts in private before publishing the profile."""
        timing = InstallationTiming(progress)
        if user_confirmed is not True:
            raise FirstInstallError("product_first_install_confirmation_required")

        def check_cancelled() -> None:
            if cancel_check is not None and cancel_check():
                raise FirstInstallError("product_first_install_cancelled")

        check_cancelled()
        if type(app_only) is not bool:
            raise FirstInstallError("product_first_install_mode_invalid")
        if local_index is not None and (app_only or type(local_artifacts) is not dict):
            raise FirstInstallError("product_first_install_local_artifacts_required")
        if local_index is None and local_artifacts is not None:
            raise FirstInstallError("product_first_install_local_index_invalid")
        index, choice = (self._local_pair(local_index) if local_index is not None
                         else self._discover(app_only=app_only))
        if confirmation_digest != choice["confirmation_digest"]:
            raise FirstInstallError("product_first_install_confirmation_mismatch")
        document = choice["document"]
        reinstall = choice if choice.get("mode") == "reinstall" else None
        if app_only or reinstall is not None:
            result = self._install_app_only(document, check_cancelled, cancel_check, timing, reinstall=reinstall)
            return {**result, "timing": timing.finish()}
        pair, database = document["pair_manifest"], document["database_manifest"]
        selected = _result(status="available", source="signed_index", checked_at=_now(), document=document,
                           trusted=True)
        assertion = create_signed_discovery_trust_assertion(
            selected, pair, database, production=True,
            product_selection={"signed_index": index["signed_index"], "confirmation_digest": confirmation_digest},
        )
        app_artifact, database_artifact = document["artifacts"]["app"], document["artifacts"]["database"]
        with self._private_transaction() as transaction:
            workspace = transaction / "workspace"
            staged = workspace / "staging"
            profile = transaction / "profile"
            preflight_before_download(
                workspace=workspace, active_app_root=profile / "app", data_dir=profile / "data",
                app_archive_bytes=app_artifact["size_bytes"], app_unpacked_bytes=app_artifact["unpacked_size_bytes"],
                app_unpacked_files=app_artifact["unpacked_files"], target_database_bytes=database["database"]["size_bytes"],
                database_archive_bytes=(database_artifact["size_bytes"]
                                        if database_artifact["format"] == "sqlite-gzip" else 0),
                current_database_bytes=0,
                **({"download_cache": self._cache / "artifacts"} if progress is not None and local_index is None else {}),
            )
            check_cancelled()
            staged_artifacts = stage_release_pair_artifacts(
                selected, workspace, confirmed_document_sha256=selected["document_sha256"],
                cancel_check=cancel_check, progress=timing,
                verify_app_on_extract=self._verifies_published_app,
                **({"local_artifacts": local_artifacts} if local_index is not None else
                   {"cache_dir": self._cache / "artifacts"} if progress is not None else {}))
            verified_app = staged_artifacts.get("verified_app")
            check_cancelled()
            _emit_phase(timing, "qualifying")
            handoff = prepare_release_pair_handoff(
                workspace, staged / "app", staged / "genshin.db", database, pair,
                user_confirmed=True, production=True, trust_assertion=assertion,
                **({"_verified_app": verified_app} if verified_app is not None else {}),
            )
            operation_id = handoff["operation_id"]
            check_cancelled()
            _emit_phase(timing, "activating")
            _install_in_fresh_profile(transaction, operation_id=operation_id,
                                      progress=timing,
                                      **({"_verified_app": verified_app} if verified_app is not None else {}))
            registration = {
                "contract": FIRST_INSTALL_CONTRACT,
                "scope": "current_user",
                "operation_id": operation_id,
                "app_directory": "app",
                "data_directory": "data",
                "initial_pair_id": pair["release_pair_id"],
                "registered_at": _now(),
            }
            atomic_json(profile / REGISTRATION_FILENAME, registration)
            check_cancelled()
            self._require_new_profile()
            _emit_phase(timing, "committing")
            # Same-volume Windows rename fails if a different process created the destination.
            try:
                self._publish_first_profile(profile, registration)
            except OSError as error:
                reason = ("product_first_install_existing_profile" if os.path.lexists(self._profile)
                          else "product_first_install_publish_failed")
                raise FirstInstallError(reason) from error
            _emit_phase(timing, "registering")
            shortcut = self._write_shortcut(registration)
            _emit_phase(timing, "cleanup")
        return {**registration, "status": "complete", "install_directory": str(self._profile),
                "release_pair_id": pair["release_pair_id"], "shortcut": shortcut, "timing": timing.finish()}

    def _install_app_only(self, document, check_cancelled, cancel_check, progress, *, reinstall=None):
        from genshin_app.release_pair_transport import (
            MAX_APP_ARCHIVE_BYTES,
            _download_https,
            _extract_app_archive,
        )
        from genshin_app.release_pair_update import _verify_app

        pair = document["pair_manifest"]
        artifact = document["artifacts"]["app"]
        cache = self._cache / "artifacts"
        with self._private_transaction(reinstall=reinstall) as transaction:
            profile = transaction / "profile"
            profile.mkdir()
            archive = transaction / "app.zip"
            preflight_before_download(
                workspace=transaction, active_app_root=profile / "app", data_dir=profile,
                app_archive_bytes=artifact["size_bytes"], app_unpacked_bytes=artifact["unpacked_size_bytes"],
                app_unpacked_files=artifact["unpacked_files"], target_database_bytes=0,
                current_database_bytes=0, reuse_installed_database=True, download_cache=cache,
            )
            _emit_phase(progress, "download")
            _download_https(artifact, archive, timeout=30, maximum_bytes=MAX_APP_ARCHIVE_BYTES,
                            cancel_check=cancel_check, cache_dir=cache, progress=progress)
            _emit_phase(progress, "extract_app")
            proof_options = ({"expected_manifest_sha256": pair["app"]["artifact_manifest"]["sha256"]}
                             if self._verifies_published_app and reinstall is None else {})
            extracted = _extract_app_archive(archive, profile / "app", cancel_check=cancel_check, **proof_options)
            if (extracted["unpacked_size_bytes"], extracted["files"]) != (
                    artifact["unpacked_size_bytes"], artifact["unpacked_files"]):
                raise FirstInstallError("release_pair_app_archive_declared_shape_mismatch")
            _emit_phase(progress, "qualifying")
            verified_app = extracted.get("verified_app")
            _verify_app(profile / "app", pair, independent=True,
                        **({"_verified_app": verified_app} if verified_app is not None else {}))
            registration = {"contract": FIRST_INSTALL_CONTRACT, "scope": "current_user",
                            "operation_id": uuid4().hex, "app_directory": "app", "data_directory": "data",
                            "app_release_id": pair["app"]["app_release_id"],
                            "database_state": "database_not_installed", "registered_at": _now(),
                            "signed_app_source": document}
            atomic_json(profile / REGISTRATION_FILENAME, registration)
            check_cancelled()
            if reinstall is None:
                self._require_new_profile()
                _emit_phase(progress, "committing")
                try:
                    self._publish_first_profile(profile, registration)
                except OSError as error:
                    raise FirstInstallError("product_first_install_publish_failed") from error
            else:
                registration = self._publish_reinstall(profile / "app", reinstall, check_cancelled, progress)
            _emit_phase(progress, "registering")
            shortcut = self._write_shortcut(registration)
            _emit_phase(progress, "cleanup")
        # Expose provenance identity, not the entire embedded signed document.
        return {**{key: value for key, value in registration.items() if key != "signed_app_source"},
                "status": "complete", "install_directory": str(self._profile), "shortcut": shortcut}

    def _publish_reinstall(self, staged_app, selection, check_cancelled, progress):
        from genshin_app.database_legacy_processes import packaged_peer_processes

        profile, registration = self._installed_registration()
        data = profile / "data"
        self._require_update_idle(profile, data)
        with profile_writer(data, timeout=0):
            if self._reinstall_selection() != selection:
                raise FirstInstallError("product_first_install_confirmation_mismatch")
            if packaged_peer_processes(include_updaters=True):
                raise FirstInstallError("database_legacy_instance_running")
            check_cancelled()
            _emit_phase(progress, "committing")
            self._remove_shortcut(registration)
            registration.pop("start_menu_shortcut_sha256", None)
            registration["installation_state"] = "installed"
            try:
                staged_app.rename(profile / "app")
            except OSError as error:
                raise FirstInstallError("product_first_install_publish_failed") from error
            # A crash after publishing the verified App leaves a usable
            # installation. Registration metadata is advisory after that point.
            try:
                atomic_json(profile / REGISTRATION_FILENAME, registration)
            except OSError:
                pass
        return registration


def build_first_install_coordinator(*, install_root=None, system_registration=None) -> _FirstInstallCoordinator:
    """Bind a setup frontend to the product's pinned feed and current user."""
    if _windows_is_elevated():
        raise FirstInstallError("product_first_install_elevation_unsupported")
    if version.DATABASE_RELEASE_CHANNEL != "stable":
        raise FirstInstallError("product_first_install_channel_unconfigured")
    verifier = build_trust_verifier(version.DATABASE_RELEASE_TRUST_KEYS)
    if verifier is None:
        raise FirstInstallError("product_first_install_trust_unconfigured")
    local = reject_reparse_chain(known_folder_path(FOLDERID_LOCAL_APP_DATA),
                                 reason="product_first_install_profile_path_invalid")
    from .managed_installation import ManagedInstallationCoordinator
    from .windows_product_registration import current_build_registration

    if system_registration is None and os.name == "nt" and getattr(sys, "frozen", False):
        system_registration = current_build_registration()
    def programs():
        base = known_folder_path(_PROGRAMS_FOLDER_ID)
        locate = getattr(system_registration, "programs_folder", None)
        return locate(base) if callable(locate) else base
    return ManagedInstallationCoordinator(
        local=local, install_root=install_root, url=version.DATABASE_RELEASE_MANIFEST_URL,
        trust_verifier=verifier, programs_folder=programs,
        system_registration=system_registration,
    )
