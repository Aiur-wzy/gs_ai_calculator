"""Exercise signed installation and App-only repair inside the frozen Windows Setup."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from email.message import Message
from importlib import resources
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import url2pathname
from urllib.response import addinfourl

CONTRACT = "genshin-frozen-setup-install-verification-v1"
UPGRADE_CONTRACT = "genshin-frozen-setup-upgrade-verification-v1"
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


def _stable_trust():
    from . import version
    from .release_pair_trust import build_trust_verifier

    _require(version.DATABASE_RELEASE_CHANNEL == "stable", "setup_qualification_requires_stable")
    trust_verifier = build_trust_verifier(version.DATABASE_RELEASE_TRUST_KEYS)
    _require(trust_verifier is not None, "setup_qualification_missing_trust_keys")
    return trust_verifier


def _read_index(index, trust_verifier):
    from .product_release_index import validate_product_release_index
    from .release_pair_discovery import MAX_DOCUMENT_BYTES

    with index.open("rb") as stream:
        raw_index = stream.read(MAX_DOCUMENT_BYTES + 1)
    _require(0 < len(raw_index) <= MAX_DOCUMENT_BYTES, "setup_qualification_invalid_index_size")
    checked = validate_product_release_index(json.loads(raw_index), channel="stable", trust_verifier=trust_verifier)
    return raw_index, checked


def _compiled_document(checked):
    from . import version

    selection = (version.APP_RELEASE_ID, version.DATABASE_RELEASE_ID)
    _require(checked["latest"] == selection, "setup_qualification_release_mismatch")
    pair_id = checked["combinations"][selection]
    signed = checked["pairs"][pair_id]
    pair, artifacts = signed["pair_manifest"], signed["artifacts"]
    _require(pair_id == version.RELEASE_PAIR_ID
             and pair["pair_policy"] == "exact" and pair["pair_sequence"] == version.RELEASE_PAIR_SEQUENCE
             and artifacts["app"]["format"] == "app-zip" and artifacts["database"]["format"] == "sqlite-gzip",
             "setup_qualification_release_mismatch")
    return signed


def _install(sandbox, setup, signed, index, app_archive, database_archive, *, measurements=None):
    from .product_first_install import REGISTRATION_FILENAME

    pair_id = signed["pair_manifest"]["release_pair_id"]
    preview = setup.preview(local_index=index)
    _require(preview["mode"] == "local_pair" and preview["release_pair_id"] == pair_id,
             "setup_qualification_preview_mismatch")
    receipt = setup.install(user_confirmed=True, confirmation_digest=preview["confirmation_digest"],
                            local_index=index, local_artifacts={"app": app_archive, "database": database_archive})
    _require(receipt["status"] == "complete" and receipt["release_pair_id"] == pair_id,
             "setup_qualification_install_incomplete")
    if measurements is not None:
        measurements.update(receipt.get("timing", {}))
    profile = Path(receipt["install_directory"])
    _require(profile.is_relative_to(sandbox), "setup_qualification_installed_identity_mismatch")
    registration = json.loads((profile / REGISTRATION_FILENAME).read_bytes())
    _require(registration["operation_id"] == receipt["operation_id"],
             "setup_qualification_installed_identity_mismatch")
    return profile


def _installed_identity(profile, signed):
    from .product_release_index import LIVE_PAIR_PROOF_FILENAME

    pair, manifest = signed["pair_manifest"], signed["database_manifest"]
    app, data = profile / "app", profile / "data"
    proof = data / LIVE_PAIR_PROOF_FILENAME
    inventory_hash = _sha256(app / "PACKAGE-FILES.sha256")
    database_hash = _sha256(data / "genshin.db")
    _require(json.loads(proof.read_bytes()) == signed and not (app / "_internal" / "bundled").exists()
             and inventory_hash == pair["app"]["artifact_manifest"]["sha256"]
             and database_hash == manifest["database"]["sha256"],
             "setup_qualification_installed_identity_mismatch")
    return {"app_inventory_sha256": inventory_hash, "database_sha256": database_hash,
            "signed_pair_proof_sha256": _sha256(proof)}


def _verify_local_install(root, index, app_archive, database_archive):
    from . import version
    from .managed_installation import ManagedInstallationCoordinator

    trust_verifier = _stable_trust()
    raw_index, checked = _read_index(index, trust_verifier)
    signed = _compiled_document(checked)
    setup = ManagedInstallationCoordinator(local=root, install_root=root / "自选 安装目录",
        url=version.DATABASE_RELEASE_MANIFEST_URL, trust_verifier=trust_verifier)
    timing = {}
    profile = _install(root, setup, signed, index, app_archive, database_archive, measurements=timing)
    profile_data = resources.files("genshin_runtime").joinpath("resources", "profiles.json").read_bytes()
    return {
        "status": "verified", "app_release_id": version.APP_RELEASE_ID, "database_release_id": version.DATABASE_RELEASE_ID,
        "release_pair_id": version.RELEASE_PAIR_ID, "index_sha256": hashlib.sha256(raw_index).hexdigest(),
        "installation_timing": timing,
        "startup_admission": _verify_startup_admission(profile, signed),
        **_installed_identity(profile, signed),
        "managed_layout": {"layout_version": 2, "all_data_under_selected_root": True,
                           "maintenance_sha256": _sha256(profile / "maintenance" / "GenshinCalcSetup.exe"),
                           "windows_registration": "not_exercised_in_isolation"},
        "normalization_registry_sha256": hashlib.sha256(profile_data).hexdigest(),
        "verified_archives": {kind: {field: locator[field] for field in ("sha256", "size_bytes")}
                              for kind, locator in signed["artifacts"].items()},
    }


def _verify_local_lifecycle(root, index, app_archive, database_archive):
    """One bounded installation at a time; no OS registration or App launch."""
    from . import version
    from .installation_location import read_registration, resolve_runtime_root
    from .managed_installation import ManagedInstallationCoordinator

    started = perf_counter()
    verifier = _stable_trust()
    raw_index, checked = _read_index(index, verifier)
    signed = _compiled_document(checked)

    def coordinator(path):
        return ManagedInstallationCoordinator(local=root, install_root=path,
            url=version.DATABASE_RELEASE_MANIFEST_URL, trust_verifier=verifier)

    setup = coordinator(root / "自选 安装目录")
    timing = {}
    original = _install(root, setup, signed, index, app_archive, database_archive, measurements=timing)
    before = _installed_identity(original, signed)
    startup = _verify_startup_admission(original, signed)
    installed_at = perf_counter()
    names = ("data/plans/qualification.json", "data/webview-profile/Local Storage/qualification.txt",
             "data/native_agent_sessions_v2/dsh-production-v1/credentials/qualification-marker.txt")
    for name in names:
        marker = original / name
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("isolated qualification marker; not a credential", encoding="utf-8")
    external, foreign = root / "外部方案.json", original / "不是产品的文件.txt"
    external.write_text("external", encoding="utf-8")
    foreign.write_text("foreign", encoding="utf-8")
    target = root / "迁移目标 (中文 空格)"
    preview = setup.migration_preview(target)
    migrated = setup.migrate(target, preview["confirmation_digest"], user_confirmed=True)
    _require(migrated["status"] == "complete"
             and read_registration(original)["installation_state"] == "migrated"
             and not (original / "data/genshin.db").exists()
             and not (original / "app/GenshinCalcGUI.exe").exists()
             and _installed_identity(target, signed) == before
             and all((target / name).read_text("utf-8").startswith("isolated") for name in names)
             and foreign.read_text("utf-8") == "foreign"
             and not (target / foreign.name).exists()
             and resolve_runtime_root(target / "app/GenshinCalcGUI.exe") == target,
             "setup_qualification_migration_incomplete")
    migrated_at = perf_counter()
    # The payload can be damaged without destroying its independent ownership proof.
    (target / "app/GenshinCalcGUI.exe").write_bytes(b"isolated damaged payload")
    target_foreign = target / "不是产品的文件.txt"
    target_foreign.write_text("preserve", encoding="utf-8")
    moved = coordinator(target)
    preview = moved.uninstall_preview()
    kept = moved.uninstall(preview["confirmation_digest"], user_confirmed=True)
    _require(kept["status"] == "uninstalled" and kept["database_removed"] and not kept["cleanup_pending"]
             and all((target / name).is_file() for name in names)
             and not (target / "app/GenshinCalcGUI.exe").exists(),
             "setup_qualification_keep_data_uninstall_incomplete")
    retained_at = perf_counter()
    reinstalled = coordinator(target)
    _require(_install(root, reinstalled, signed, index, app_archive, database_archive) == target
             and _installed_identity(target, signed) == before
             and all((target / name).is_file() for name in names),
             "setup_qualification_reinstall_incomplete")
    reinstalled_at = perf_counter()
    preview = reinstalled.uninstall_preview(keep_user_data=False)
    removed = reinstalled.uninstall(preview["confirmation_digest"], user_confirmed=True, keep_user_data=False)
    _require(removed["status"] == "uninstalled" and removed["database_removed"] and not removed["cleanup_pending"]
             and not any((target / name).exists() for name in names)
             and target_foreign.read_text("utf-8") == "preserve" and external.read_text("utf-8") == "external"
             and foreign.read_text("utf-8") == "foreign",
             "setup_qualification_delete_data_uninstall_incomplete")
    deleted_at = perf_counter()
    # Run this only after the first installation is removed: at most one full
    # App/database is retained outside the migration's deliberate copy window.
    later_database = _verify_app_only_then_database(root, signed, raw_index, app_archive, database_archive, verifier)
    return {"status": "verified", "qualification": "frozen_setup_selected_root_lifecycle",
            "app_release_id": version.APP_RELEASE_ID, "database_release_id": version.DATABASE_RELEASE_ID,
            "release_pair_id": version.RELEASE_PAIR_ID, "index_sha256": hashlib.sha256(raw_index).hexdigest(),
            "initial_identity": before, "startup_admission": startup,
            "installation_timing": timing,
            "app_only_then_database": later_database,
            "checked": ["custom_root", "migration_single_active_root", "personal_data_migration",
                        "damaged_app_uninstall", "retain_personal_data", "reinstall",
                        "remove_personal_data", "foreign_and_external_files_preserved",
                        "app_only_without_database_download", "later_database_setup_same_root"],
            "not_exercised": ["windows_registration", "start_menu_search", "gui_rendering", "crash_injection"],
            "elapsed_seconds": {"install_and_admission": round(installed_at - started, 3),
                "seed_and_migrate": round(migrated_at - installed_at, 3),
                "damaged_uninstall_keep": round(retained_at - migrated_at, 3),
                "reinstall": round(reinstalled_at - retained_at, 3),
                "uninstall_delete": round(deleted_at - reinstalled_at, 3),
                "app_only_then_database_and_cleanup": round(perf_counter() - deleted_at, 3)}}


def _verify_app_only_then_database(root, signed, index_bytes, app_archive, database_archive, verifier):
    from . import product_release_index, release_pair_transport, version
    from .installation_location import read_registration, resolve_runtime_root
    from .managed_installation import ManagedInstallationCoordinator
    from .product_database_setup import DatabaseSetup

    selected = root / "只装程序 后补数据库"
    setup = ManagedInstallationCoordinator(local=root, install_root=selected,
        url=version.DATABASE_RELEASE_MANIFEST_URL, trust_verifier=verifier)
    app = signed["pair_manifest"]["app"]
    paths = {"app": app_archive, "database": database_archive}
    urls = {signed["artifacts"][kind]["url"]: kind for kind in paths}
    requests = []
    tag = '"' + hashlib.sha256(index_bytes).hexdigest() + '"'

    def fetch_index(url, *, etag, timeout):
        _require(url == setup._url and urlsplit(url).scheme == "https" and etag in (None, tag),
                 "setup_qualification_unexpected_index_fetch")
        return (304, b"", tag) if etag == tag else (200, index_bytes, tag)

    def open_archive(url, *, timeout, range_headers=None):
        _require(url in urls and not range_headers, "setup_qualification_unexpected_archive_fetch")
        kind = urls[url]
        _require(kind not in requests, "setup_qualification_duplicate_archive_fetch")
        requests.append(kind)
        headers = Message()
        headers["Content-Length"] = str(paths[kind].stat().st_size)
        return addinfourl(paths[kind].open("rb"), headers, url, 200)

    with (patch.object(product_release_index, "_fetch_https", fetch_index),
          patch.object(release_pair_transport, "_open_https", open_archive)):
        preview = setup.preview(app_only=True)
        _require(preview["download_bytes"]["database"] == 0, "setup_qualification_app_only_preview_mismatch")
        result = setup.install(app_only=True, user_confirmed=True, confirmation_digest=preview["confirmation_digest"])
        _require(result["status"] == "complete" and Path(result["install_directory"]) == selected
                 and read_registration(selected)["database_state"] == "database_not_installed"
                 and not (selected / "data/genshin.db").exists()
                 and requests == ["app"] and resolve_runtime_root(selected / "app/GenshinCalcGUI.exe") == selected,
                 "setup_qualification_app_only_incomplete")
        app_before = _sha256(selected / "app/PACKAGE-FILES.sha256")
        database = DatabaseSetup(selected / "data", selected / "app", url=setup._url,
            trust_verifier=verifier, app_release_id=app["app_release_id"], app_version=app["app_version"],
            logic_profile=app["logic_profile"], data_contract_version=app["data_contract_version"])
        _require(database.available(), "setup_qualification_database_setup_unavailable")
        database.validate_app_only()
        preview = database.preview()
        _require(preview["app_bytes"] == 0 and preview["database_release_id"] == signed["database_manifest"]["database_release_id"],
                 "setup_qualification_database_setup_preview_mismatch")
        installed = database.install(preview["confirmation_digest"], user_confirmed=True)
    _require(installed["status"] == "complete" and installed["requires_restart"] is True
             and requests == ["app", "database"] and not database.available()
             and read_registration(selected)["database_state"] == "installed"
             and _sha256(selected / "app/PACKAGE-FILES.sha256") == app_before,
             "setup_qualification_later_database_setup_incomplete")
    identity = _installed_identity(selected, signed)
    startup = _verify_startup_admission(selected, signed)
    preview = setup.uninstall_preview(keep_user_data=False)
    removed = setup.uninstall(preview["confirmation_digest"], user_confirmed=True, keep_user_data=False)
    _require(removed["database_removed"] and not removed["cleanup_pending"]
             and not (selected / "app/GenshinCalcGUI.exe").exists(),
             "setup_qualification_later_database_cleanup_incomplete")
    return {"status": "verified", "transport": "signed_local_files_through_https_adapter",
            "archive_requests": requests, "installed_identity": identity, "startup_admission": startup,
            "app_installation_timing": result.get("timing", {}),
            "database_installation_timing": installed.get("timing", {}),
            "same_selected_root": True, "cleanup_complete": True}


def _validate_successor(previous, target, successor):
    from .product_release_index import _check_index_progress

    previous_id = previous["combinations"][previous["latest"]]
    installed = previous["pairs"][previous_id]
    _check_index_progress(target, previous)
    _require(target["signed_index"]["index_sequence"] > previous["signed_index"]["index_sequence"]
             and target["latest"][0] != previous["latest"][0]
             and target["pairs"].get(previous_id) == installed
             and installed["database_manifest"] == successor["database_manifest"]
             and installed["artifacts"]["database"] == successor["artifacts"]["database"],
             "setup_qualification_not_same_database_successor")
    return installed


def _repair_from_signed_files(setup, installed, target, index_bytes, app_archive, index_url):
    from . import product_release_index, release_pair_transport

    locator = target["artifacts"]["app"]
    target_pair = target["pair_manifest"]
    database_id = target["database_manifest"]["database_release_id"]
    requests = []
    tag = '"' + hashlib.sha256(index_bytes).hexdigest() + '"'

    def fetch_index(url, *, etag, timeout):
        _require(url == index_url and urlsplit(url).scheme == "https" and etag in (None, tag),
                 "setup_qualification_unexpected_index_fetch")
        requests.append("index")
        return (304, b"", tag) if etag == tag else (200, index_bytes, tag)

    def open_archive(url, *, timeout, range_headers=None):
        _require(url == locator["url"] and not range_headers and "app" not in requests,
                 "setup_qualification_unexpected_archive_fetch")
        requests.append("app")
        headers = Message()
        headers["Content-Length"] = str(app_archive.stat().st_size)
        return addinfourl(app_archive.open("rb"), headers, url, 200)

    with (patch.object(product_release_index, "_fetch_https", fetch_index),
          patch.object(release_pair_transport, "_open_https", open_archive)):
        preview = setup.repair_preview()
        _require(preview["mode"] == "repair_installed_app"
                 and preview["installed_app_release_id"] == installed["pair_manifest"]["app"]["app_release_id"]
                 and preview["app_release_id"] == target_pair["app"]["app_release_id"]
                 and preview["database_release_id"] == database_id
                 and preview["release_pair_id"] == target_pair["release_pair_id"]
                 and preview["download_bytes"] == {"app": locator["size_bytes"], "database": 0}
                 and requests and "app" not in requests,
                 "setup_qualification_repair_preview_mismatch")
        receipt = setup.repair_install(preview["confirmation_digest"], user_confirmed=True)
    _require(receipt["status"] == "complete" and receipt["database_state"] == "preserved"
             and receipt["release_pair_id"] == target_pair["release_pair_id"]
             and requests.count("app") == 1,
             "setup_qualification_repair_incomplete")
    return {"index_responses": requests.count("index"), "app_archives": 1, "database_archives": 0}


def _create_profile_markers(data):
    markers = {
        "settings": data / "native-agent-dsh-settings.json",
        "session": data / "native_agent_sessions_v2" / "dsh-production-v1" / "qualification.json",
        "plan": data / "plans" / "qualification.plan.json",
    }
    for label, path in markers.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            json.dump({"setup_qualification_marker": label}, stream)
    return markers


def _verify_startup_admission(profile, target):
    from . import database_bootstrap

    app = profile / "app"
    executable, resource_dir = app / "GenshinCalc.exe", app / "_internal"
    _require(executable.is_file() and resource_dir.is_dir(), "setup_qualification_app_entry_missing")
    started = perf_counter()
    # Setup embeds the same startup gate. Resolve paths against the newly
    # installed App's actual layout without invoking any packaged App process.
    with (patch.object(database_bootstrap, "resource_dir", lambda: resource_dir),
          patch.object(database_bootstrap, "sys", SimpleNamespace(executable=str(executable)))):
        receipt = database_bootstrap.prepare_database_startup(profile / "data")
    pair = target["pair_manifest"]
    expected = {"can_start": True, "pair_policy": "exact", "release_pair_id": pair["release_pair_id"],
                "app_release_id": pair["app"]["app_release_id"],
                "database_release_id": target["database_manifest"]["database_release_id"]}
    _require(all(receipt.get(field) == value for field, value in expected.items()),
             "setup_qualification_startup_admission_mismatch")
    return {"execution_layer": "app_startup_gate_in_frozen_setup",
            "elapsed_seconds": round(perf_counter() - started, 3),
            "database_integrity_scope": receipt.get("database_integrity_scope"),
            "phase_seconds": receipt.get("startup_timings_seconds", {}), **expected}


def _verify_local_upgrade(root, previous_index, previous_app, index, app_archive, database_archive):
    from . import version
    from .managed_installation import ManagedInstallationCoordinator
    from .product_first_install import REGISTRATION_FILENAME, _FirstInstallCoordinator
    from .release_pair_update import read_pair_update_journal

    start = perf_counter()
    trust_verifier = _stable_trust()
    previous_bytes, previous = _read_index(previous_index, trust_verifier)
    index_bytes, checked = _read_index(index, trust_verifier)
    target = _compiled_document(checked)
    installed = _validate_successor(previous, checked, target)
    authenticated = perf_counter()
    index_url = version.DATABASE_RELEASE_MANIFEST_URL
    setup = _FirstInstallCoordinator(local=root, url=index_url, trust_verifier=trust_verifier)
    profile = _install(root, setup, installed, previous_index, previous_app, database_archive)
    before = _installed_identity(profile, installed)
    markers = _create_profile_markers(profile / "data")
    old_registration = json.loads((profile / REGISTRATION_FILENAME).read_bytes())
    preserved = {label: _sha256(path) for label, path in markers.items()}
    baseline_ready = perf_counter()

    setup = ManagedInstallationCoordinator(local=root, install_root=profile, url=index_url, trust_verifier=trust_verifier)

    transport = _repair_from_signed_files(setup, installed, target, index_bytes, app_archive, index_url)
    upgraded = perf_counter()
    startup = _verify_startup_admission(profile, target)
    after = _installed_identity(profile, target)
    new_registration = json.loads((profile / REGISTRATION_FILENAME).read_bytes())
    _require(new_registration["layout_version"] == 2
             and new_registration["operation_id"] == old_registration["operation_id"]
             and (profile / "maintenance" / "GenshinCalcSetup.exe").is_file(),
             "setup_qualification_registration_upgrade_failed")
    _require(before["database_sha256"] == after["database_sha256"]
             and before["app_inventory_sha256"] != after["app_inventory_sha256"]
             and preserved == {label: _sha256(path) for label, path in markers.items()},
             "setup_qualification_profile_preservation_failed")
    journal = read_pair_update_journal(profile / "update-workspace")
    _require(journal["state"] == "cleanup_complete", "setup_qualification_update_cleanup_incomplete")
    return {"status": "verified", "qualification": "frozen_setup_same_database_upgrade",
            "previous": {"app_release_id": previous["latest"][0],
            "release_pair_id": installed["pair_manifest"]["release_pair_id"],
            "index_sha256": hashlib.sha256(previous_bytes).hexdigest(), **before},
            "target": {"app_release_id": checked["latest"][0], "release_pair_id": version.RELEASE_PAIR_ID,
                       "index_sha256": hashlib.sha256(index_bytes).hexdigest(), **after},
            "database_release_id": checked["latest"][1], "startup_admission": startup,
            "preserved_profile_marker_sha256": preserved, "local_transport": transport,
            "registration_migration": {"layout_version": new_registration["layout_version"],
                                       "original_operation_retained": True, "maintenance_ready": True,
                                       "windows_registration": "not_exercised_in_isolation"},
            "journal_state": journal["state"],
            "elapsed_seconds": {"authenticate_indexes": round(authenticated - start, 3),
                                "install_previous_and_seed_profile": round(baseline_ready - authenticated, 3),
                                "repair_and_activate": round(upgraded - baseline_ready, 3),
                                "startup_and_verify_preservation": round(perf_counter() - upgraded, 3)}}


def _run_frozen_qualification(*, sandbox_root, report, inputs, contract, run):
    if not getattr(sys, "frozen", False) or Path(sys.executable).name.lower() != "genshincalcsetup.exe":
        return 2
    try:
        root, destination, inputs = _paths(sandbox_root, report, inputs)
        root.mkdir()
    except (OSError, RuntimeError, TypeError, ValueError):
        return 2
    _install_isolation(root)
    try:
        result = run(root, *inputs)
    except Exception as error:  # noqa: BLE001 - record failure across the frozen process boundary
        chain = installation_error_chain(error)
        result = {"status": "failed", "reason": chain[0].get("reason", chain[0]["type"]),
                  "cause_chain": chain}
    report_value = {"contract": contract, "execution_layer": "frozen_setup", **result}
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report_value, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return 0 if result["status"] == "verified" else 1


def verify_frozen_install(*, index, app_archive, database_archive, sandbox_root, report):
    return _run_frozen_qualification(
        sandbox_root=sandbox_root, report=report, inputs=(index, app_archive, database_archive),
        contract=CONTRACT, run=_verify_local_install,
    )


def verify_frozen_upgrade(*, previous_index, previous_app_archive, index, app_archive,
                          database_archive, sandbox_root, report):
    return _run_frozen_qualification(
        sandbox_root=sandbox_root, report=report,
        inputs=(previous_index, previous_app_archive, index, app_archive, database_archive),
        contract=UPGRADE_CONTRACT, run=_verify_local_upgrade,
    )


def verify_frozen_lifecycle(*, index, app_archive, database_archive, sandbox_root, report):
    return _run_frozen_qualification(
        sandbox_root=sandbox_root, report=report, inputs=(index, app_archive, database_archive),
        contract="genshin-setup-lifecycle-qualification-v1", run=_verify_local_lifecycle,
    )
