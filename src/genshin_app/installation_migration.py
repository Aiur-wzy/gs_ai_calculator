"""Relocate one verified Stable installation, with one writable DB location."""
from __future__ import annotations

import copy
import json
import shutil
import sys
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

from genshin_db.locking import file_lock

from .database_transaction import atomic_json, profile_writer
from .installation_inventory import (
    delete_members,
    is_managed_relative,
    managed_members,
    save_maintenance_proof,
    sha256,
    signed_inventory,
)
from .installation_location import (
    InstallationLayout,
    checked_path,
    disjoint_roots,
    path_identity,
    read_registration,
    recheck_identity,
    safe_files,
    validate_selected_root,
)
from .product_install_contract import REGISTRATION_FILENAME, FirstInstallError
from .product_release_index import digest
from .release_pair_update import _verify_app


def preview_migration(coordinator, target):
    source, target = disjoint_roots(coordinator._profile, validate_selected_root(target))
    coordinator._require_update_idle(source, source / "data")
    row = read_registration(source)
    if row.get("installation_state", "installed") not in {"installed", "registration_pending"}:
        raise FirstInstallError("product_migration_source_not_active")
    previous = source / "maintenance/operations/migration.json"
    if previous.is_file() and json.loads(checked_path(previous).read_text("utf-8"))["state"] != "complete":
        raise FirstInstallError("product_migration_recovery_pending")
    for parent in target.parents:
        if (parent / REGISTRATION_FILENAME).exists():
            raise FirstInstallError("product_install_roots_overlap")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        cancelled = read_registration(target) if (target / REGISTRATION_FILENAME).is_file() else {}
        reusable = (cancelled.get("installation_state") == "migration_cancelled"
                    and cancelled.get("migration_source") == str(source)
                    and cancelled.get("installation_id") == row.get("installation_id"))
        if not reusable or any(path.relative_to(target).as_posix() not in {
                REGISTRATION_FILENAME, "maintenance/installation.lock"} for path in safe_files(target)):
            raise FirstInstallError("product_install_target_not_empty")
    if target == Path(target.anchor):
        raise FirstInstallError("product_install_path_invalid")
    document, _, expected = signed_inventory(source, row, coordinator._trust_verifier)
    _verify_app(source / "app", document["pair_manifest"], independent=True)
    capability = "maintenance/installation-layout.json"
    if (capability not in expected or json.loads((source / "app" / capability).read_text("utf-8")).get("contract")
            != "genshin-installation-layout-v2"):
        raise FirstInstallError("product_migration_requires_app_update")
    names = managed_members(source, expected, keep_user_data=False)
    # Update artifacts and cache are reproducible and aren't migrated. Existing
    # user-owned foreign paths remain at source, never enrolled by extension.
    names = [name for name in names if not name.startswith(("cache/", "update-workspace/", "data/cache/", "data/logs/", "data/build/"))]
    sizes = {name: checked_path(source / name).stat().st_size for name in names}
    existing = target
    while not existing.exists():
        existing = existing.parent
    required = sum(sizes.values()) + max(32 * 1024**2, sum(sizes.values()) // 20)
    if shutil.disk_usage(existing).free < required:
        raise FirstInstallError("product_migration_insufficient_space")
    result = {"mode": "migrate_installation", "source": str(source), "target": str(target),
              "installation_id": row.get("installation_id"), "source_identity": path_identity(source),
              "target_identity": path_identity(target), "members": names, "copy_bytes": sum(sizes.values()),
              "required_free_bytes": required,
              "app_release_id": document["pair_manifest"]["app"]["app_release_id"]}
    result["confirmation_digest"] = digest(result)
    return result


def _journal_paths(operation):
    return [checked_path(Path(operation[key]) / "maintenance" / "operations" / "migration.json")
            for key in ("source", "target")]


def _save(operation, state):
    operation["state"] = state
    for path in _journal_paths(operation):
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(path, operation)


def _target_coordinator(coordinator, target):
    result = copy.copy(coordinator)
    result._profile = result._local = target
    result.layout = InstallationLayout.at(target)
    result._cache = result.layout.cache
    result._location_at_open = None
    return result


def migrate_installation(coordinator, target, confirmation_digest, *, user_confirmed, progress=None, cancel_check=None):
    from .managed_installation import _quiet
    if user_confirmed is not True:
        raise FirstInstallError("product_migration_confirmation_required")
    choice = preview_migration(coordinator, target)
    if choice["confirmation_digest"] != confirmation_digest:
        raise FirstInstallError("product_first_install_confirmation_mismatch")
    source, target = recheck_identity(choice["source_identity"]), recheck_identity(choice["target_identity"])
    source_maintenance = checked_path(source / "maintenance")
    source_maintenance.mkdir(exist_ok=True)
    # No user data may drift between its final snapshot and source deactivation.
    with coordinator._location_guard(), file_lock(source_maintenance / "installation.lock", timeout=0), profile_writer(source / "data", timeout=0):
        _quiet(source)
        if preview_migration(coordinator, target)["confirmation_digest"] != confirmation_digest:
            raise FirstInstallError("product_first_install_confirmation_mismatch")
        target.mkdir(parents=True, exist_ok=True)
        (target / "maintenance" / "operations").mkdir(parents=True, exist_ok=True)
        with file_lock(target / "maintenance" / "installation.lock", timeout=0):
            row = read_registration(source)
            document, _, expected = signed_inventory(source, row, coordinator._trust_verifier)
            save_maintenance_proof(source, document)
            row["installation_id"] = row.get("installation_id") or uuid4().hex
            operation = {**choice, "operation_id": uuid4().hex, "registration": row,
                         "signed_release": document, "state": "copying",
                         "target_identity": path_identity(target), "hashes": {},
                         "cleanup_members": managed_members(source, expected, keep_user_data=False)}
            candidate = target / "maintenance" / "operations" / operation["operation_id"] / "candidate"
            rollback = source / "maintenance" / "operations" / operation["operation_id"] / "rollback"
            operation.update(candidate=str(candidate), rollback=str(rollback))
            atomic_json(target / REGISTRATION_FILENAME, {**row, "installation_state": "migration_candidate"})
            _save(operation, "copying")
            for name in choice["members"]:
                if cancel_check and cancel_check():
                    _discard_copy(operation)
                    raise FirstInstallError("product_first_install_cancelled")
                origin, output = checked_path(source / name), checked_path(candidate / name)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, output)
                if sha256(origin) != sha256(output):
                    raise FirstInstallError("product_migration_copy_mismatch")
                operation["hashes"][name] = sha256(output)
            _verify_app(candidate / "app", document["pair_manifest"], independent=True)
            database = candidate / "data" / "genshin.db"
            if database.exists() and sha256(database) != document["database_manifest"]["database"]["sha256"]:
                raise FirstInstallError("product_migration_database_mismatch")
            _quiet(source)
            recheck_identity(choice["source_identity"])
            for name, expected_hash in operation["hashes"].items():
                if sha256(source / name) != expected_hash:
                    raise FirstInstallError("product_migration_source_changed")
            if progress:
                progress({"phase": "committing"})
            _save(operation, "deactivating_source")
            return _finish(coordinator, operation)


def _discard_copy(operation):
    """Cancellation before deactivation removes only this candidate's paths."""
    source, target = disjoint_roots(operation["source"], operation["target"])
    candidate = checked_path(operation["candidate"])
    if candidate != target / "maintenance/operations" / operation["operation_id"] / "candidate":
        raise FirstInstallError("product_migration_recovery_path_invalid")
    delete_members(candidate, operation["members"])
    delete_members(target, ["maintenance/operations/migration.json"])
    atomic_json(target / REGISTRATION_FILENAME, {**operation["registration"], "installation_state": "migration_cancelled",
                                                "migration_source": str(source)})
    delete_members(source, ["maintenance/operations/migration.json"])


def _finish(coordinator, operation):
    source = recheck_identity(operation["source_identity"])
    target = recheck_identity(operation["target_identity"])
    source, target = disjoint_roots(source, target)
    if coordinator._profile not in {source, target}:
        raise FirstInstallError("product_migration_recovery_path_invalid")
    candidate, rollback = checked_path(operation["candidate"]), checked_path(operation["rollback"])
    expected_candidate = target / "maintenance" / "operations" / operation["operation_id"] / "candidate"
    expected_rollback = source / "maintenance" / "operations" / operation["operation_id"] / "rollback"
    if candidate != expected_candidate or rollback != expected_rollback:
        raise FirstInstallError("product_migration_recovery_path_invalid")
    row = operation["registration"]
    for root in (source, target):
        present = read_registration(root)
        if present.get("installation_id") not in {None, row["installation_id"]}:
            raise FirstInstallError("product_migration_recovery_identity_changed")
    document, _, expected = signed_inventory(source, row, coordinator._trust_verifier)
    if document != operation["signed_release"] or any(
            not is_managed_relative(name, expected, keep_user_data=False)
            for name in operation["members"] + operation["cleanup_members"]):
        raise FirstInstallError("product_migration_recovery_proof_mismatch")
    if set(operation["members"]) != set(operation["hashes"]):
        raise FirstInstallError("product_migration_recovery_proof_mismatch")
    # Once deactivation begins we recover forward. A source DB is never opened
    # again after the target can have accepted new writes.
    if operation["state"] == "deactivating_source":
        for name, expected_hash in operation["hashes"].items():
            if not name.startswith("app/") and name not in {"data/genshin.db", "data/genshin.db-wal", "data/genshin.db-shm"}:
                continue
            original, backup = checked_path(source / name), checked_path(rollback / name)
            if original.exists():
                if backup.exists() or sha256(original) != expected_hash:
                    raise FirstInstallError("product_migration_source_changed")
                backup.parent.mkdir(parents=True, exist_ok=True)
                original.rename(backup)
            elif not backup.exists() or sha256(backup) != expected_hash:
                raise FirstInstallError("product_migration_source_missing")
        atomic_json(source / REGISTRATION_FILENAME, {**row, "installation_state": "migrated", "moved_to": str(target)})
        coordinator._remove_owned_shortcuts(row)
        _save(operation, "source_disabled")
    if operation["state"] == "source_disabled":
        for name in ("data", "app"):
            origin, destination = checked_path(candidate / name), checked_path(target / name)
            if origin.exists():
                if destination.exists():
                    raise FirstInstallError("product_migration_target_changed")
                origin.rename(destination)
        _verify_app(target / "app", operation["signed_release"]["pair_manifest"], independent=True)
        _save(operation, "target_published")
    moved = _target_coordinator(coordinator, target)
    if operation["state"] == "target_published":
        atomic_json(target / REGISTRATION_FILENAME, {**row, "installation_state": "registration_pending"})
        if coordinator._system:
            coordinator._system.write_location({"root": str(target), "installation_id": row.get("installation_id"),
                                                 "state": "registration_pending", "operation_id": operation["operation_id"]})
        _save(operation, "location_committed")
    result = moved.refresh_registration()
    failures = delete_members(source, [name for name in operation["cleanup_members"] if (source / name).is_file()])
    failures.extend(delete_members(rollback, [name for name in operation["members"] if (rollback / name).is_file()]))
    maintenance_binary = source / "maintenance" / "GenshinCalcSetup.exe"
    if maintenance_binary.exists():
        if Path(sys.executable).absolute() == maintenance_binary:
            failures.append("maintenance/GenshinCalcSetup.exe")
        elif operation["registration"].get("maintenance_sha256") == sha256(maintenance_binary):
            failures.extend(delete_members(source, ["maintenance/GenshinCalcSetup.exe"]))
    _save(operation, "complete" if not failures else "cleanup_pending")
    return {"status": "complete", "install_directory": str(target), "source_directory": str(source),
            "cleanup_pending": bool(failures), "remaining": failures, **result}


def migration_recovery_preview(coordinator, journal):
    operation = json.loads(checked_path(journal).read_text("utf-8"))
    if operation["state"] == "complete":
        return None
    return {"mode": "recover_migration", "state": operation["state"], "source": operation["source"],
            "target": operation["target"], "confirmation_digest": digest(operation)}


def recover_migration(coordinator, confirmation_digest, *, user_confirmed):
    from .managed_installation import _quiet
    journal = coordinator.layout.maintenance / "operations" / "migration.json"
    operation = json.loads(checked_path(journal).read_text("utf-8"))
    if not user_confirmed or digest(operation) != confirmation_digest:
        raise FirstInstallError("product_first_install_confirmation_mismatch")
    source, target = disjoint_roots(operation["source"], operation["target"])
    registry_guard = (coordinator._system.mutation_lock() if coordinator._system and hasattr(coordinator._system, "mutation_lock")
                      else nullcontext())
    with registry_guard, file_lock(source / "maintenance" / "installation.lock", timeout=0), file_lock(target / "maintenance" / "installation.lock", timeout=0), profile_writer(source / "data", timeout=0):
        _quiet(source)
        _quiet(target)
        # Validate the signed authority independently of this mutable journal.
        document, _, _ = signed_inventory(source, operation["registration"], coordinator._trust_verifier)
        if document != operation["signed_release"]:
            raise FirstInstallError("product_migration_recovery_proof_mismatch")
        if operation["state"] == "copying":
            if read_registration(source).get("installation_state", "installed") not in {"installed", "registration_pending"}:
                raise FirstInstallError("product_migration_recovery_conflict")
            _, _, expected = signed_inventory(source, operation["registration"], coordinator._trust_verifier)
            coordinator._require_update_idle(source, source / "data")
            operation["cleanup_members"] = managed_members(source, expected, keep_user_data=False)
            operation["members"] = [name for name in operation["cleanup_members"] if not name.startswith(
                ("cache/", "update-workspace/", "data/cache/", "data/logs/", "data/build/"))]
            operation["hashes"] = {}
            candidate = checked_path(target / "maintenance" / "operations" / operation["operation_id"] / "candidate")
            if str(candidate) != operation["candidate"] or any(
                    not is_managed_relative(name, expected, keep_user_data=False) for name in operation["members"]):
                raise FirstInstallError("product_migration_recovery_path_invalid")
            for name in operation["members"]:
                origin, output = checked_path(source / name), checked_path(candidate / name)
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(origin, output)
                if sha256(origin) != sha256(output):
                    raise FirstInstallError("product_migration_copy_mismatch")
                operation["hashes"][name] = sha256(output)
            _verify_app(candidate / "app", document["pair_manifest"], independent=True)
            database = candidate / "data" / "genshin.db"
            if database.exists() and sha256(database) != document["database_manifest"]["database"]["sha256"]:
                raise FirstInstallError("product_migration_database_mismatch")
            _save(operation, "deactivating_source")
        return _finish(coordinator, operation)
