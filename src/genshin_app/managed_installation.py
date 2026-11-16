"""User-selected product locations and recoverable Windows maintenance.

Download/signature/DB transactions remain in the existing release coordinator.
This layer owns location, publication, Windows registration and removal only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from uuid import uuid4

from genshin_db.locking import file_lock

from . import version
from .database_transaction import atomic_json, profile_writer
from .installation_inventory import (
    MAINTENANCE_MEMBER,
    delete_members,
    is_managed_relative,
    managed_members,
    save_maintenance_proof,
    sha256,
    signed_inventory,
)
from .installation_location import (
    LAYOUT_VERSION,
    InstallationLayout,
    checked_path,
    disjoint_roots,
    path_identity,
    read_registration,
    recheck_identity,
    resolve_runtime_root,
    safe_files,
    validate_selected_root,
)
from .product_first_install import _emit_phase, _FirstInstallCoordinator
from .product_install_contract import (
    FIRST_INSTALL_CONTRACT,
    REGISTRATION_FILENAME,
    FirstInstallError,
)
from .product_release_index import digest
from .release_pair_update import _verify_app
from .windows_product_registration import APP_USER_MODEL_ID, DISPLAY_NAME


def _quiet(root=None):
    from .database_legacy_processes import packaged_peer_processes
    if root is not None:
        from .installation_runtime import assert_installation_idle
        assert_installation_idle(root)
    if packaged_peer_processes(include_updaters=True):
        raise FirstInstallError("database_legacy_instance_running")


def refresh_registered_installation(root):
    """Post-commit hook: registration failures do not undo an accepted update."""
    root = checked_path(root)
    if not (root / REGISTRATION_FILENAME).is_file():
        return None
    row = read_registration(root)
    if (row.get("layout_version") == LAYOUT_VERSION and row.get("installation_state") == "installed"
            and row.get("app_release_id") == version.APP_RELEASE_ID):
        return {"registration_state": "installed", "refresh_required": False}
    from .product_first_install import _PROGRAMS_FOLDER_ID
    from .release_pair_trust import build_trust_verifier
    from .windows_known_folders import known_folder_path
    from .windows_product_registration import current_build_registration
    production = os.name == "nt" and bool(getattr(sys, "frozen", False))
    try:
        registration = current_build_registration() if production else None
        coordinator = ManagedInstallationCoordinator(local=root.parent, install_root=root,
            url=version.DATABASE_RELEASE_MANIFEST_URL, trust_verifier=build_trust_verifier(version.DATABASE_RELEASE_TRUST_KEYS),
            system_registration=registration,
            programs_folder=(lambda: registration.programs_folder(known_folder_path(_PROGRAMS_FOLDER_ID))) if production else None)
        return coordinator.refresh_registration()
    except (OSError, RuntimeError, ValueError, TypeError) as error:
        try:
            atomic_json(root / REGISTRATION_FILENAME, {**row, "installation_state": "registration_pending",
                                                     "registration_warnings": [str(error)]})
        except OSError:
            pass
        return {"registration_state": "registration_pending", "registration_warnings": [str(error)]}


class ManagedInstallationCoordinator(_FirstInstallCoordinator):
    def __init__(self, *, local, install_root=None, system_registration=None, **kwargs):
        super().__init__(local=local, **kwargs)
        self._system = system_registration
        located = self._system.read_location() if self._system else None
        own = resolve_runtime_root(sys.executable, allow_maintenance=True) if getattr(sys, "frozen", False) else None
        root = install_root or own or (located and located["root"]) or self._profile
        self.layout = InstallationLayout.at(root)
        self._profile = self.layout.root
        self._local = self._profile
        self._cache = self.layout.cache
        self._location_at_open = located
        self._assert_target()

    def _assert_target(self):
        root = validate_selected_root(self._profile)
        # A dedicated root may be created under any user-writable parent; an
        # existing arbitrary directory must never become product-owned.
        for parent in root.parents:
            if (parent / REGISTRATION_FILENAME).is_file():
                raise FirstInstallError("product_install_roots_overlap")
        if root.exists():
            if not root.is_dir():
                raise FirstInstallError("product_install_path_invalid")
            if any(root.iterdir()) and not (root / REGISTRATION_FILENAME).is_file():
                raise FirstInstallError("product_install_target_not_empty")
            if (root / REGISTRATION_FILENAME).is_file():
                read_registration(root)
        if self._location_at_open:
            other = checked_path(self._location_at_open["root"])
            if other != root:
                disjoint_roots(other, root)
                if self._location_at_open.get("state", "installed") not in {"uninstalled", "uninstalled_cleanup_pending"}:
                    raise FirstInstallError("product_install_existing_location_requires_migration")

    def _require_update_idle(self, profile, data, *, allow_prepared=False):
        super()._require_update_idle(profile, data, allow_prepared=allow_prepared)
        from genshin_db.release_migration import JOURNAL_FILE, _read_journal
        path = checked_path(data / JOURNAL_FILE)
        journal = _read_journal(path)
        if journal and journal["state"] not in {"cleanup_complete", "rolled_back"}:
            raise FirstInstallError("product_setup_database_recovery_pending")

    def _initialize_root(self):
        self._assert_target()
        root = self._profile
        root.mkdir(parents=True, exist_ok=True)
        registration = root / REGISTRATION_FILENAME
        if not registration.exists():
            atomic_json(registration, {"contract": FIRST_INSTALL_CONTRACT, "scope": "current_user",
                "app_directory": "app", "data_directory": "data", "layout_version": LAYOUT_VERSION,
                "installation_id": uuid4().hex, "installation_state": "preparing"})
        checked_path(self.layout.maintenance).mkdir(exist_ok=True)
        checked_path(self.layout.maintenance / "operations").mkdir(exist_ok=True)

    @contextmanager
    def _location_guard(self):
        guard = self._system.mutation_lock() if self._system and hasattr(self._system, "mutation_lock") else nullcontext()
        with guard:
            current = self._system.read_location() if self._system else None
            if (current and checked_path(current["root"]) != self._profile
                    and current.get("state", "installed") not in {"uninstalled", "uninstalled_cleanup_pending"}):
                raise FirstInstallError("product_install_location_changed")
            yield

    def _require_new_profile(self):
        if not self._profile.exists() or not any(self._profile.iterdir()):
            return
        row = read_registration(self._profile)
        if (row.get("installation_state") == "uninstalled" and not (self.layout.data / "genshin.db").exists()
                and not (self.layout.app / "GenshinCalcGUI.exe").exists()):
            return
        if row.get("installation_state") != "preparing" or any(
                (self._profile / name).exists() for name in ("app", "data")):
            raise FirstInstallError("product_first_install_existing_profile")

    def _reinstall_selection(self):
        if self._profile.exists() and (self._profile / REGISTRATION_FILENAME).exists():
            row = read_registration(self._profile)
            if row.get("installation_state") in {"preparing", "uninstalled"}:
                return None
        return super()._reinstall_selection()

    def preview(self, **kwargs):
        self._initialize_root()
        choice = super().preview(**kwargs)
        identity = path_identity(self._profile)
        original = choice["confirmation_digest"]
        choice.update(layout=self.layout.public(), location_identity=identity,
                      selection_digest=original, installation_id=read_registration(self._profile)["installation_id"])
        choice["confirmation_digest"] = digest({"selection": original, "identity": identity,
                                                "id": choice["installation_id"]})
        return choice

    def install(self, *, confirmation_digest, **kwargs):
        choice = self.preview(**{key: value for key, value in kwargs.items() if key in {"app_only", "local_index"}})
        if choice["confirmation_digest"] != confirmation_digest:
            raise FirstInstallError("product_first_install_confirmation_mismatch")
        recheck_identity(choice["location_identity"])
        result = super().install(confirmation_digest=choice["selection_digest"], **kwargs)
        return {**result, **self.refresh_registration()}

    def _publish_first_profile(self, profile, registration):
        with self._location_guard():
            return self._publish_first_profile_locked(profile, registration)

    def _publish_first_profile_locked(self, profile, registration):
        self._require_new_profile()
        root = self._profile
        row = read_registration(root)
        registration = {**registration, "layout_version": LAYOUT_VERSION,
                        "installation_id": row["installation_id"], "installation_state": "publishing"}
        document, _ = self._signed_installed_source(profile / "data", registration)
        _verify_app(profile / "app", document["pair_manifest"], independent=True)
        members = {}
        for name in ("app", "data"):
            if not (profile / name).exists():
                continue
            for path in safe_files(profile / name):
                relative = path.relative_to(profile).as_posix()
                if relative in {"data/.update.lock", "data/.genshin.db.write.lock"}:
                    continue
                if (root / relative).exists():
                    error = FirstInstallError("product_install_target_file_exists")
                    error.add_note(relative)
                    raise error
                members[relative] = sha256(path)
        operation = {"kind": "publish", "staged": str(profile), "registration": registration,
                     "signed_release": document, "identity": path_identity(root), "state": "prepared", "members": members}
        journal = self.layout.maintenance / "operations" / "publish.json"
        save_maintenance_proof(root, document, app_root=profile / "app")
        atomic_json(journal, operation)
        atomic_json(root / REGISTRATION_FILENAME, registration)
        if self._system:
            self._system.write_location({"root": str(root), "installation_id": registration["installation_id"],
                                         "state": "publishing"})
        self._finish_publication(operation)

    def _cleanup_private_transaction(self, transaction):
        row = read_registration(self._profile)
        if row.get("installation_state") == "publishing":
            return
        super()._cleanup_private_transaction(transaction)

    def _finish_publication(self, operation):
        root = recheck_identity(operation["identity"])
        if root != self._profile or read_registration(root).get("installation_id") != operation["registration"]["installation_id"]:
            raise FirstInstallError("product_install_recovery_path_invalid")
        staged = checked_path(operation["staged"])
        if not staged.is_relative_to(self._cache / "pending-install"):
            raise FirstInstallError("product_install_recovery_path_invalid")
        document, _, inventory = signed_inventory(root, operation["registration"], self._trust_verifier)
        if document != operation["signed_release"] or any(
                not is_managed_relative(name, inventory, keep_user_data=True) for name in operation["members"]):
            raise FirstInstallError("product_install_recovery_proof_invalid")
        for name, expected in operation["members"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts or relative.parts[0] not in {"app", "data"}:
                raise FirstInstallError("product_install_recovery_path_invalid")
            source, target = checked_path(staged / name), checked_path(root / name)
            if source.exists():
                if target.exists():
                    raise FirstInstallError("product_install_recovery_conflict")
                if sha256(source) != expected:
                    raise FirstInstallError("product_install_recovery_content_changed")
                target.parent.mkdir(parents=True, exist_ok=True)
                source.rename(target)
            elif not target.is_file() or sha256(target) != expected:
                raise FirstInstallError("product_install_recovery_content_changed")
        _verify_app(root / "app", document["pair_manifest"], independent=True)
        database = root / "data/genshin.db"
        if (operation["registration"].get("database_state") != "database_not_installed"
                and (not database.is_file() or sha256(database) != document["database_manifest"]["database"]["sha256"])):
            raise FirstInstallError("product_install_recovery_database_mismatch")
        save_maintenance_proof(root, document)
        row = {**operation["registration"], "installation_state": "registration_pending"}
        atomic_json(root / REGISTRATION_FILENAME, row)
        atomic_json(self.layout.maintenance / "operations" / "publish.json", {**operation, "state": "complete"})

    def maintenance_preview(self):
        if not (self._profile / REGISTRATION_FILENAME).exists():
            return None
        row = read_registration(self._profile)
        migration = self.layout.maintenance / "operations" / "migration.json"
        if migration.is_file():
            from .installation_migration import migration_recovery_preview
            recovery = migration_recovery_preview(self, migration)
            if recovery:
                return recovery
        if row.get("installation_state") in {"publishing", "registration_pending"}:
            return {"mode": "recover_installation", "install_directory": str(self._profile),
                    "confirmation_digest": digest(row)}
        if row.get("installation_state") in {"uninstalling", "uninstalled_cleanup_pending"}:
            return {"mode": "recover_uninstall", **self.uninstall_preview()}
        return None

    def recover_installation(self, confirmation_digest, *, user_confirmed):
        choice = self.maintenance_preview()
        if not user_confirmed or not choice or choice["confirmation_digest"] != confirmation_digest:
            raise FirstInstallError("product_first_install_confirmation_mismatch")
        with file_lock(self.layout.maintenance / "installation.lock", timeout=0):
            _quiet(self._profile)
            if read_registration(self._profile)["installation_state"] == "publishing":
                operation = json.loads((self.layout.maintenance / "operations" / "publish.json").read_text("utf-8"))
                self._finish_publication(operation)
            return {"status": "complete", "install_directory": str(self._profile),
                    "database_state": read_registration(self._profile).get("database_state", "installed"),
                    **self.refresh_registration()}

    def _write_shortcut(self, registration):
        # Publish both current-user entries only after maintenance proof exists.
        return "registration_pending"

    def _publish_shortcuts(self, row, maintenance):
        from .shortcuts import _windows_link
        if self._programs_folder is None:
            return [], []
        folder = checked_path(self._programs_folder())
        folder.mkdir(parents=True, exist_ok=True)
        display_name = getattr(self._system, "display_name", DISPLAY_NAME)
        app_id = getattr(self._system, "app_user_model_id", APP_USER_MODEL_ID)
        prior = {item["name"]: item for item in row.get("shortcuts", [])}
        specifications = [(display_name + ".lnk", self.layout.app / "GenshinCalcGUI.exe", ""),
                          ("卸载" + display_name + ".lnk", maintenance,
                           subprocess.list2cmdline(["--uninstall", "--install-root", str(self._profile)]))]
        saved, warnings = [], []
        def owned(path, previous):
            return previous is not None and sha256(path) in {previous.get("sha256"), previous.get("pending_sha256")}
        for name, target, arguments in specifications:
            path = checked_path(folder / name)
            previous = prior.get(name)
            if path.exists() and not owned(path, previous):
                # Preserve a user's same-name shortcut and create a stable,
                # installation-owned alternative instead of retrying forever.
                name = name[:-4] + "-" + row["installation_id"][:8] + ".lnk"
                path = checked_path(folder / name)
                previous = prior.get(name)
                if path.exists() and not owned(path, previous):
                    warnings.append("start_menu_user_modified:" + name)
                    continue
            if not target.is_file():
                warnings.append("start_menu_target_missing:" + name)
                continue
            raw = _windows_link({"target": str(target), "arguments": arguments,
                "working_directory": str(target.parent), "description": display_name,
                "app_user_model_id": app_id + (".Uninstall" if target == maintenance else "")})
            temporary = path.with_name(f".{uuid4().hex}.lnk")
            try:
                temporary.write_bytes(raw)
                if path.exists() and not owned(path, previous):
                    raise FirstInstallError("product_start_menu_changed")
                target_hash = hashlib.sha256(raw).hexdigest()
                prior[name] = {**(previous or {}), "name": name, "pending_sha256": target_hash}
                row["shortcuts"] = list(prior.values())
                atomic_json(self._profile / REGISTRATION_FILENAME, row)
                os.replace(temporary, path)
                prior[name] = {"name": name, "sha256": target_hash}
                row["shortcuts"] = list(prior.values())
                atomic_json(self._profile / REGISTRATION_FILENAME, row)
                saved.append(prior[name])
            finally:
                temporary.unlink(missing_ok=True)
        return saved, warnings

    def refresh_registration(self):
        row = read_registration(self._profile)
        document, _ = self._signed_installed_source(self.layout.data, row)
        expected = save_maintenance_proof(self._profile, document)
        row = {**row, "layout_version": LAYOUT_VERSION,
               "installation_id": row.get("installation_id") or uuid4().hex,
               "app_release_id": document["pair_manifest"]["app"]["app_release_id"],
               "installation_state": "registration_pending"}
        atomic_json(self._profile / REGISTRATION_FILENAME, row)
        source = self.layout.app / MAINTENANCE_MEMBER
        maintenance = self.layout.maintenance / "GenshinCalcSetup.exe"
        warnings = []
        if MAINTENANCE_MEMBER not in expected or not source.is_file() or sha256(source) != expected[MAINTENANCE_MEMBER]:
            warnings.append("product_maintenance_binary_missing")
        else:
            try:
                if not maintenance.exists() or sha256(maintenance) != expected[MAINTENANCE_MEMBER]:
                    temporary = maintenance.with_suffix(".new")
                    shutil.copyfile(source, temporary)
                    os.replace(temporary, maintenance)
                row["maintenance_sha256"] = expected[MAINTENANCE_MEMBER]
                entries, shortcut_warnings = self._publish_shortcuts(row, maintenance)
                row["shortcuts"] = entries
                warnings.extend(shortcut_warnings)
                if self._system:
                    self._system.register_app(root=self._profile, installation_id=row["installation_id"],
                        version=document["pair_manifest"]["app"]["app_version"], executable=maintenance)
            except (OSError, RuntimeError) as error:
                warnings.append(str(error))
        row["installation_state"] = "registration_pending" if warnings else "installed"
        row["registration_warnings"] = warnings
        atomic_json(self._profile / REGISTRATION_FILENAME, row)
        if self._system:
            try:
                self._system.write_location({"root": str(self._profile), "installation_id": row["installation_id"],
                                             "state": row["installation_state"]})
            except OSError as error:
                warnings.append(str(error))
                row["installation_state"] = "registration_pending"
                atomic_json(self._profile / REGISTRATION_FILENAME, row)
        return {"registration_state": row["installation_state"], "registration_warnings": warnings,
                "shortcut": "created" if row.get("shortcuts") else "registration_pending",
                "start_menu_names": [entry["name"][:-4] for entry in row.get("shortcuts", [])]}

    def repair_install(self, *args, **kwargs):
        self._initialize_root()
        with self._location_guard(), file_lock(self.layout.maintenance / "installation.lock", timeout=0):
            if read_registration(self._profile).get("installation_state", "installed") not in {"installed", "registration_pending"}:
                raise FirstInstallError("product_installation_not_active")
            result = super().repair_install(*args, **kwargs)
            return {**result, **self.refresh_registration()}

    def uninstall_preview(self, *, keep_user_data=True):
        row = read_registration(self._profile)
        document, _, expected = signed_inventory(self._profile, row, self._trust_verifier)
        self._require_update_idle(self._profile, self.layout.data)
        operation_file = self.layout.maintenance / "operations" / "uninstall.json"
        if row.get("installation_state") in {"uninstalling", "uninstalled_cleanup_pending"}:
            operation = json.loads(checked_path(operation_file).read_text("utf-8"))
            keep_user_data = operation["keep_user_data"]
            names = operation["members"]
            # Signed names and runtime categories, not a writable journal,
            # remain the authority. Existing foreign files are never adopted.
            if any(not is_managed_relative(name, expected, keep_user_data=keep_user_data) for name in names):
                raise FirstInstallError("product_uninstall_plan_invalid")
        else:
            names = managed_members(self._profile, expected, keep_user_data=keep_user_data)
        for name in names:
            checked_path(self._profile / name)
        result = {"app_release_id": document["pair_manifest"]["app"]["app_release_id"],
                  "install_directory": str(self._profile), "app_directory": str(self.layout.app),
                  "preserved_data_directory": str(self.layout.data) if keep_user_data else None,
                  "keep_user_data": keep_user_data, "members": names, "database_removed": True,
                  "identity": path_identity(self._profile), "installation_id": row.get("installation_id")}
        result["confirmation_digest"] = digest(result)
        return result

    def uninstall(self, confirmation_digest, *, user_confirmed, keep_user_data=True, cancel_check=None, progress=None):
        if user_confirmed is not True:
            raise FirstInstallError("product_uninstall_confirmation_required")
        if cancel_check and cancel_check():
            raise FirstInstallError("product_uninstall_cancelled")
        self._initialize_root()
        lock = self.layout.maintenance / "installation.lock"
        with self._location_guard(), file_lock(lock, timeout=0), profile_writer(self.layout.data, timeout=0):
            _quiet(self._profile)
            choice = self.uninstall_preview(keep_user_data=keep_user_data)
            if confirmation_digest != choice["confirmation_digest"]:
                raise FirstInstallError("product_uninstall_confirmation_mismatch")
            root = recheck_identity(choice["identity"])
            row = read_registration(root)
            document, _, _ = signed_inventory(root, row, self._trust_verifier)
            if not (self.layout.maintenance / "proof" / "signed-release.json").exists():
                save_maintenance_proof(root, document)
            operation_file = self.layout.maintenance / "operations" / "uninstall.json"
            operation = {**choice, "state": "deleting"}
            atomic_json(operation_file, operation)
            row.update(installation_state="uninstalling", installation_id=row.get("installation_id") or uuid4().hex)
            atomic_json(root / REGISTRATION_FILENAME, row)
            _quiet(self._profile)
            _emit_phase(progress, "committing")
            failures = delete_members(root, choice["members"])
            shortcut = self._remove_owned_shortcuts(row)
            if self._system:
                try:
                    self._system.unregister_app(row["installation_id"])
                except OSError:
                    failures.append("windows_registration")
            # The running maintenance EXE and proof intentionally remain
            # discoverable until a separate maintenance process can clean them.
            row["installation_state"] = "uninstalled_cleanup_pending"
            row["database_state"] = "database_not_installed"
            row["signed_app_source"] = document
            atomic_json(root / REGISTRATION_FILENAME, row)
            atomic_json(operation_file, {**operation, "state": "cleanup_pending", "remaining": failures})
            if self._system:
                self._system.write_location({"root": str(root), "installation_id": row["installation_id"],
                    "state": "uninstalled_cleanup_pending"})
            result = {"status": "uninstalled", "database_removed": not (self.layout.data / "genshin.db").exists(),
                    "preserved_data_directory": choice["preserved_data_directory"], "shortcut": shortcut,
                    "cleanup_pending": True, "remaining": failures, "install_directory": str(root)}
        if not failures and not Path(sys.executable).absolute().is_relative_to(self.layout.maintenance):
            result["cleanup_pending"] = self._finish_uninstall_maintenance(row)
        return result

    def _finish_uninstall_maintenance(self, row):
        """An external Setup may finish the exact old maintenance files."""
        with self._location_guard(), file_lock(self.layout.maintenance / "installation.lock", timeout=0):
            return self._finish_uninstall_maintenance_locked(row)

    def _finish_uninstall_maintenance_locked(self, row):
        maintenance = checked_path(self.layout.maintenance)
        names = ["GenshinCalcSetup.exe", "GenshinCalcSetup.new", "proof/PACKAGE-FILES.sha256",
                 "proof/signed-release.json", "operations/publish.json", "operations/uninstall.json"]
        proof = checked_path(maintenance / "proof")
        if proof.is_dir():
            for directory in proof.iterdir():
                inventory = directory / "PACKAGE-FILES.sha256"
                if len(directory.name) == 64 and inventory.is_file() and sha256(inventory) == directory.name:
                    names.append(inventory.relative_to(maintenance).as_posix())
        # Preserve evidence until every removable binary/cache item is gone.
        binary = maintenance / "GenshinCalcSetup.exe"
        if binary.exists() and row.get("maintenance_sha256") != sha256(binary):
            return True
        failures = delete_members(maintenance, names[:2])
        if failures:
            return True
        # Keep signed metadata in registration for a fresh authenticated
        # reinstall; no database controls remain pretending a DB is installed.
        row["installation_state"] = "uninstalled"
        atomic_json(self._profile / REGISTRATION_FILENAME, row)
        if self._system:
            self._system.remove_location(row["installation_id"])
        delete_members(maintenance, names[2:])
        return False

    def _remove_owned_shortcuts(self, row):
        if self._programs_folder is None:
            return "not_registered"
        status = "removed"
        folder = checked_path(self._programs_folder())
        display_name = getattr(self._system, "display_name", DISPLAY_NAME)
        allowed = {display_name + ".lnk", "卸载" + display_name + ".lnk"}
        allowed.update(name[:-4] + "-" + row["installation_id"][:8] + ".lnk" for name in tuple(allowed))
        for entry in row.get("shortcuts", []):
            if entry["name"] not in allowed:
                raise FirstInstallError("product_start_menu_ownership_invalid")
            path = checked_path(folder / entry["name"])
            if not path.exists():
                continue
            if sha256(path) not in {entry.get("sha256"), entry.get("pending_sha256")}:
                status = "preserved_modified"
            else:
                path.unlink()
        # r19/r20 ownership uses the historical link's saved byte digest.
        if row.get("start_menu_shortcut_sha256"):
            legacy = super()._remove_shortcut(row)
            if legacy in {"preserved_modified", "cleanup_pending"}:
                status = legacy
        return status

    def migration_preview(self, target):
        from .installation_migration import preview_migration
        return preview_migration(self, target)

    def migrate(self, target, confirmation_digest, *, user_confirmed, progress=None, cancel_check=None):
        from .installation_migration import migrate_installation
        return migrate_installation(self, target, confirmation_digest, user_confirmed=user_confirmed,
                                    progress=progress, cancel_check=cancel_check)
