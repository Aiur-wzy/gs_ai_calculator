"""Signed program ownership and explicit runtime-data removal categories."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from .database_transaction import atomic_json
from .installation_location import checked_path, safe_files
from .package_integrity import PACKAGE_MANIFEST_FILENAME, _read_package_manifest
from .product_install_contract import FirstInstallError
from .release_pair_discovery import validate_latest_document

MAINTENANCE_MEMBER = "maintenance/GenshinCalcSetup.exe"
DATABASE_FILES = frozenset({
    "genshin.db", "genshin.db-wal", "genshin.db-shm", "genshin.db-journal",
    "release-pair-live-manifest.json", "database-live-manifest.json", "database-live-receipt.json",
    "release-pair-live-signed.json", "database-activation-journal.json", "database-release-migration-journal.json",
})
PERSONAL_DIRECTORIES = frozenset({
    "plans", "exports", "agent_sessions", "agent_deliveries", "native_agent_sessions_v2",
    "native-dsh-workspaces", "native-dsh-accounting", "native-dsh-research", "settings", "workspaces",
    "webview-profile",
})
PERSONAL_FILES = frozenset({"settings.json", "gui-settings.json", "agent-settings.json", "preferences.json",
                            "native-agent-dsh-settings.json", "agent-window-geometry.json"})
REBUILDABLE_DIRECTORIES = frozenset({"cache", "build", "logs", "database-releases", "database-backups"})


def is_managed_relative(name, expected, *, keep_user_data):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.as_posix() != name:
        return False
    if name.startswith("app/"):
        return name[4:] in expected or name[4:] in {PACKAGE_MANIFEST_FILENAME, "_internal/bundled/release-pair-manifest.json"}
    if path.parts[0] in {"cache", "update-workspace"}:
        return len(path.parts) > 1 and path.name != "installation.lock"
    if path.parts[0] != "data" or len(path.parts) < 2:
        return False
    return (name[5:] in DATABASE_FILES or path.parts[1] in REBUILDABLE_DIRECTORIES or
            (not keep_user_data and (path.parts[1] in PERSONAL_DIRECTORIES or name[5:] in PERSONAL_FILES)))


def sha256(path):
    result = hashlib.sha256()
    with checked_path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def signed_inventory(root, row, verifier):
    """A damaged payload is removable; its signed inventory must remain authentic."""
    root = checked_path(root)
    proof = root / "maintenance" / "proof"
    source = proof / "signed-release.json"
    if source.is_file():
        if source.stat().st_size > 16 * 1024 * 1024:
            raise FirstInstallError("product_uninstall_release_proof_invalid")
        document = json.loads(checked_path(source).read_text(encoding="utf-8"))
        if document.get("contract") == "genshin-installation-maintenance-proof-v1":
            reference = document.get("inventory_sha256", "")
            if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{64}", reference):
                raise FirstInstallError("product_uninstall_release_proof_invalid")
            inventory_root = checked_path(proof / reference)
            document = document["signed_release"]
            if document["pair_manifest"]["app"]["artifact_manifest"]["sha256"] != reference:
                raise FirstInstallError("product_uninstall_release_proof_invalid")
        else:
            inventory_root = proof  # first layout-v2 development snapshots
    else:
        document = row.get("signed_app_source")
        if document is None:
            from .product_release_index import LIVE_PAIR_PROOF_FILENAME
            source = checked_path(root / "data" / LIVE_PAIR_PROOF_FILENAME)
            if not source.is_file() or source.stat().st_size > 16 * 1024 * 1024:
                raise FirstInstallError("product_uninstall_release_proof_missing")
            document = json.loads(source.read_text(encoding="utf-8"))
        inventory_root = root / "app"
    document, trusted = validate_latest_document(document, expected_channel="stable", trust_verifier=verifier,
                                                  allow_unsigned_dev=False)
    if not trusted:
        raise FirstInstallError("product_uninstall_release_proof_invalid")
    _, raw, expected = _read_package_manifest(inventory_root)
    wanted = document["pair_manifest"]["app"]["artifact_manifest"]["sha256"]
    if hashlib.sha256(raw).hexdigest() != wanted:
        raise FirstInstallError("product_uninstall_release_proof_invalid")
    return document, raw, expected


def save_maintenance_proof(root, document, *, app_root=None):
    root = checked_path(root)
    _, raw, expected = _read_package_manifest(app_root or root / "app")
    if hashlib.sha256(raw).hexdigest() != document["pair_manifest"]["app"]["artifact_manifest"]["sha256"]:
        raise FirstInstallError("product_install_inventory_mismatch")
    proof = checked_path(root / "maintenance" / "proof")
    proof.mkdir(parents=True, exist_ok=True)
    manifest_hash = hashlib.sha256(raw).hexdigest()
    generation = checked_path(proof / manifest_hash)
    generation.mkdir(exist_ok=True)
    temporary = generation / (PACKAGE_MANIFEST_FILENAME + ".new")
    temporary.write_bytes(raw)
    os.replace(temporary, generation / PACKAGE_MANIFEST_FILENAME)
    # A single atomic pointer binds the signed document to an immutable
    # inventory generation. A crash cannot pair new bytes with the old proof.
    atomic_json(proof / "signed-release.json", {"contract": "genshin-installation-maintenance-proof-v1",
                "inventory_sha256": manifest_hash, "signed_release": document})
    return expected


def managed_members(root, expected, *, keep_user_data):
    """List paths, never infer ownership from arbitrary .db/.json extensions."""
    root = checked_path(root)
    names = {"app/" + name for name in expected}
    names.add("app/" + PACKAGE_MANIFEST_FILENAME)
    names.add("app/_internal/bundled/release-pair-manifest.json")
    names.update("data/" + name for name in DATABASE_FILES)
    for directory in (root / "data",):
        if not directory.exists():
            continue
        for entry in directory.iterdir():
            if entry.name in REBUILDABLE_DIRECTORIES or (not keep_user_data and entry.name in PERSONAL_DIRECTORIES):
                names.update(path.relative_to(root).as_posix() for path in safe_files(entry))
            elif not keep_user_data and entry.name in PERSONAL_FILES:
                names.add(entry.relative_to(root).as_posix())
    for directory in (root / "cache", root / "update-workspace"):
        if directory.exists():
            names.update(path.relative_to(root).as_posix() for path in safe_files(directory)
                         if path.name != "installation.lock")
    return sorted(name for name in names if (root / name).is_file())


def delete_members(root, names):
    """Delete exact previously classified paths, preserving unlisted neighbours."""
    root = checked_path(root)
    failures = []
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise FirstInstallError("product_uninstall_plan_invalid")
        path = checked_path(root / relative)
        if root not in path.parents:
            raise FirstInstallError("product_uninstall_plan_invalid")
        try:
            if os.path.lexists(path):
                if not path.is_file():
                    raise FirstInstallError("product_uninstall_path_changed")
                identity = path.stat()
                checked = checked_path(path)
                second = checked.stat()
                if (identity.st_dev, identity.st_ino) != (second.st_dev, second.st_ino):
                    raise FirstInstallError("product_install_location_changed")
                checked.unlink()
        except OSError:
            failures.append(name)
    for directory in sorted({(root / name).parent for name in names}, key=lambda p: len(p.parts), reverse=True):
        while directory != root and root in directory.parents:
            try:
                checked_path(directory).rmdir()
            except OSError:
                break
            directory = directory.parent
    return failures
