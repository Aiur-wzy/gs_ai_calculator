"""Exercise the signed local installation inside the frozen Windows Setup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

CONTRACT = "genshin-frozen-setup-install-verification-v1"
SANDBOX_PREFIX = "genshin-setup-qualification-"


class SetupQualificationError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _require(condition, reason):
    if not condition:
        raise SetupQualificationError(reason)


def installation_error_chain(error):
    """Keep stable error reasons and system error numbers without paths or messages."""
    chain = []
    while error is not None and len(chain) < 8:
        record = {"type": type(error).__name__}
        reason = getattr(error, "reason", None)
        if isinstance(reason, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", reason):
            record["reason"] = reason
        for field in ("errno", "winerror"):
            value = getattr(error, field, None)
            if type(value) is int:
                record[field] = value
        details = getattr(error, "details", None)
        if isinstance(details, dict):
            component, state = details.get("component"), details.get("state")
            if component in ("equipment", "runtime_config", "mechanic_semantics"):
                record["component"] = component
            if state in ("ready", "invalid", "incompatible", "missing", "unavailable"):
                record["state"] = state
        chain.append(record)
        error = error.__cause__
    return chain


def _paths(sandbox_root, report, inputs):
    from .windows_known_folders import reject_reparse_chain

    root, destination = Path(sandbox_root), Path(report)
    temp = reject_reparse_chain(Path(tempfile.gettempdir()), reason="setup_qualification_temp_path_invalid")
    _require(root.is_absolute() and root.parent == temp and not os.path.lexists(root)
             and re.fullmatch(r"genshin-setup-qualification-[a-zA-Z0-9_-]{1,48}", root.name),
             "setup_qualification_sandbox_invalid")
    _require(destination.is_absolute() and destination.parent == root and destination.suffix == ".json",
             "setup_qualification_report_path_invalid")
    paths = []
    for entry in inputs:
        path = reject_reparse_chain(Path(entry), reason="setup_qualification_input_path_invalid")
        _require(path.is_file() and not path.is_relative_to(root), "setup_qualification_input_path_invalid")
        paths.append(path)
    _require(len(set(paths)) == len(paths), "setup_qualification_input_paths_overlap")
    return root, destination, paths


def _install_isolation(root):
    def guard_path(path):
        _require(isinstance(path, (str, bytes, os.PathLike))
                 and Path(os.fsdecode(path)).resolve().is_relative_to(root),
                 "setup_qualification_external_write")

    def audit(event, arguments):
        if event.startswith(("socket.", "winreg.")) or event in {
            "urllib.Request", "subprocess.Popen", "os.system", "os.startfile",
            "os.startfile/2", "os.posix_spawn", "os.exec",
        }:
            raise SetupQualificationError("setup_qualification_external_action")
        write_open = event == "open" and arguments[2] & (
            os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        if write_open or event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime", "shutil.rmtree"}:
            guard_path(arguments[0])
        elif event == "os.rename":
            guard_path(arguments[0])
            guard_path(arguments[1])
        elif event in {"os.link", "os.symlink"}:
            raise SetupQualificationError("setup_qualification_external_action")
        elif event == "sqlite3.connect":
            path = os.fsdecode(arguments[0])
            if path.startswith("file:"):
                address = urlsplit(path)
                _require(not address.netloc, "setup_qualification_external_action")
                guard_path(url2pathname(address.path))
            elif path != ":memory:":
                guard_path(path)

    sys.addaudithook(audit)


def _sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _verify_local_install(root, index, app_archive, database_archive):
    from . import version
    from .product_first_install import REGISTRATION_FILENAME, _FirstInstallCoordinator
    from .product_release_index import (
        LIVE_PAIR_PROOF_FILENAME,
        validate_product_release_index,
    )
    from .release_pair_discovery import MAX_DOCUMENT_BYTES
    from .release_pair_trust import build_trust_verifier

    _require(version.DATABASE_RELEASE_CHANNEL == "stable", "setup_qualification_requires_stable")
    trust_verifier = build_trust_verifier(version.DATABASE_RELEASE_TRUST_KEYS)
    _require(trust_verifier is not None, "setup_qualification_missing_trust_keys")
    with index.open("rb") as stream:
        raw_index = stream.read(MAX_DOCUMENT_BYTES + 1)
    _require(0 < len(raw_index) <= MAX_DOCUMENT_BYTES, "setup_qualification_invalid_index_size")
    checked = validate_product_release_index(json.loads(raw_index), channel="stable", trust_verifier=trust_verifier)
    selection = (version.APP_RELEASE_ID, version.DATABASE_RELEASE_ID)
    _require(checked["latest"] == selection, "setup_qualification_release_mismatch")
    pair_id = checked["combinations"][selection]
    _require(pair_id == version.RELEASE_PAIR_ID, "setup_qualification_release_mismatch")
    signed = checked["pairs"][pair_id]
    pair, manifest, artifacts = signed["pair_manifest"], signed["database_manifest"], signed["artifacts"]
    _require(pair["pair_policy"] == "exact" and pair["pair_sequence"] == version.RELEASE_PAIR_SEQUENCE
             and artifacts["app"]["format"] == "app-zip" and artifacts["database"]["format"] == "sqlite-gzip",
             "setup_qualification_release_mismatch")

    setup = _FirstInstallCoordinator(local=root, url=version.DATABASE_RELEASE_MANIFEST_URL,
                                     trust_verifier=trust_verifier)
    preview = setup.preview(local_index=index)
    _require(preview["mode"] == "local_pair" and preview["release_pair_id"] == pair_id,
             "setup_qualification_preview_mismatch")
    receipt = setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"],
                            local_index=index, local_artifacts={"app": app_archive, "database": database_archive})
    _require(receipt["status"] == "complete" and receipt["release_pair_id"] == pair_id,
             "setup_qualification_install_incomplete")
    profile = root / version.APP_DIR_NAME
    app, data = profile / "app", profile / "data"
    proof = data / LIVE_PAIR_PROOF_FILENAME
    inventory_hash = _sha256(app / "PACKAGE-FILES.sha256")
    database_hash = _sha256(data / "genshin.db")
    registration = json.loads((profile / REGISTRATION_FILENAME).read_bytes())
    _require(json.loads(proof.read_bytes()) == signed and not (app / "_internal" / "bundled").exists()
             and registration["operation_id"] == receipt["operation_id"]
             and inventory_hash == pair["app"]["artifact_manifest"]["sha256"]
             and database_hash == manifest["database"]["sha256"],
             "setup_qualification_installed_identity_mismatch")
    profile_data = resources.files("genshin_runtime").joinpath("resources", "profiles.json").read_bytes()
    return {
        "status": "verified", "app_release_id": selection[0], "database_release_id": selection[1],
        "release_pair_id": pair_id, "index_sha256": hashlib.sha256(raw_index).hexdigest(),
        "app_inventory_sha256": inventory_hash, "database_sha256": database_hash,
        "signed_pair_proof_sha256": _sha256(proof),
        "normalization_registry_sha256": hashlib.sha256(profile_data).hexdigest(),
        "verified_archives": {kind: {field: locator[field] for field in ("sha256", "size_bytes")}
                              for kind, locator in artifacts.items()},
    }


def verify_frozen_install(*, index, app_archive, database_archive, sandbox_root, report):
    if not getattr(sys, "frozen", False) or Path(sys.executable).name.lower() != "genshincalcsetup.exe":
        return 2
    try:
        root, destination, inputs = _paths(sandbox_root, report, (index, app_archive, database_archive))
        root.mkdir()
    except (OSError, RuntimeError, TypeError, ValueError):
        return 2
    _install_isolation(root)
    try:
        result = _verify_local_install(root, *inputs)
    except Exception as error:  # noqa: BLE001 - record failure across the frozen process boundary
        chain = installation_error_chain(error)
        result = {"status": "failed", "reason": chain[0].get("reason", chain[0]["type"]),
                  "cause_chain": chain}
    report_value = {"contract": CONTRACT, "execution_layer": "frozen_setup", **result}
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report_value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return 0 if result["status"] == "verified" else 1
