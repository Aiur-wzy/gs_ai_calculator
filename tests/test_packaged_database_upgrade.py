"""No App EXE runs: real artifacts, controlled CLI and Python timeout child."""
from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_database_release_migration import coordinator as coordinator  # noqa: PLC0414
from test_database_release_profile import _identity, _manifest

from genshin_db.release_profile import (
    create_database_live_receipt,
    create_release_pair_manifest,
    validate_database_release_manifest,
    write_database_release_manifest,
    write_release_pair_manifest,
)
from scripts import build_dist
from scripts import packaged_database_upgrade as upgrade


@pytest.fixture
def artifacts(tmp_path, monkeypatch):
    old, new = tmp_path / "accepted", tmp_path / "dist/new/GenshinCalc/_internal/bundled"
    old.mkdir()
    new.mkdir(parents=True)
    old_db, new_db = old / "genshin.db", new / "genshin.db"
    old_db.write_bytes(b"old-accepted-database")
    new_db.write_bytes(b"new-accepted-database")
    identities, manifests, pairs = {}, [], []
    for index, db in enumerate((old_db, new_db)):
        value = db.read_bytes()
        identity = _identity(sha=hashlib.sha256(value).hexdigest(), meta=str(index + 1) * 64,
            core_marker=f"core-{index}")
        identity["size_bytes"] = len(value)
        identities[value] = identity
        manifest = _manifest(ident=identity, release_id=f"db-7.1.51-r{index + 1}", sequence=index + 2)
        manifest["channel"] = "dev"
        manifest = validate_database_release_manifest(manifest)
        pair = create_release_pair_manifest(manifest, release_pair_id=f"pair-{index}",
            pair_sequence=index + 6, pair_policy="current_only", app_release_id=f"app-{index}",
            app_version="0.46.0", app_artifact_manifest_sha256=str(index + 1) * 64)
        write_database_release_manifest(db.with_name("database-release-manifest.json"), manifest)
        write_release_pair_manifest(db.with_name("release-pair-manifest.json"), pair)
        manifests.append(manifest)
        pairs.append(pair)
    monkeypatch.setattr(upgrade, "read_database_identity", lambda path: identities[path.read_bytes()])
    predecessor = upgrade.load_predecessor_database(pair_path=old / "release-pair-manifest.json",
        predecessor_pair=pairs[0], target_manifest=manifests[1], workspace_root=tmp_path,
        build_root=tmp_path / "build", dist_root=tmp_path / "dist/new",
        expected_pair_sha256=hashlib.sha256((old / "release-pair-manifest.json").read_bytes()).hexdigest())
    return tmp_path, old_db, new_db, manifests, pairs, identities, predecessor


def test_upgrade_loader_uses_real_old_manifest_not_new_labels(artifacts):
    root, old, _, manifests, pairs, _, predecessor = artifacts
    assert predecessor.database == old
    assert predecessor.manifest == manifests[0]
    assert predecessor.pair == pairs[0]
    assert predecessor.manifest["database_release_id"] != manifests[1]["database_release_id"]
    predecessor.assert_unchanged()
    kwargs = {"workspace_root": root, "build_root": root / "build", "dist_root": root / "dist/new",
        "pair_manifest": pairs[1], "database_manifest": manifests[1], "expected_sha256": predecessor.artifact_shas[2]}
    with pytest.raises(upgrade.DatabaseReleaseError, match="predecessor_database_mismatch"):
        build_dist._load_accepted_predecessor_pair(predecessor.pair_path, **kwargs)
    assert build_dist._load_accepted_predecessor_pair(predecessor.pair_path,
        allow_database_upgrade=True, **kwargs) == pairs[0]


@pytest.mark.parametrize("fault,reason", [
    ("missing", "artifact_path_invalid"), ("foreign_pair", "pair_changed"),
    ("not_advanced", "sequence_not_advanced"), ("same_id", "sequence_not_advanced"),
    ("wrong_database", "artifact_mismatch"),
])
def test_upgrade_loader_rejects_missing_forged_or_nonadvancing_sources(artifacts, fault, reason):
    root, old, _, manifests, pairs, _identities, predecessor = artifacts
    target = dict(manifests[1])
    selected_pair = pairs[0]
    if fault == "missing": old.unlink()
    if fault == "foreign_pair": selected_pair = pairs[1]
    if fault == "not_advanced": target["release_sequence"] = 2
    if fault == "same_id": target["database_release_id"] = manifests[0]["database_release_id"]
    if fault == "wrong_database":
        old.write_bytes(b"new-accepted-database")
    with pytest.raises(upgrade.DatabaseReleaseError, match=reason):
        upgrade.load_predecessor_database(pair_path=old.with_name("release-pair-manifest.json"),
            predecessor_pair=selected_pair, target_manifest=target, workspace_root=root,
            build_root=root / "build", dist_root=root / "dist/new",
            expected_pair_sha256=predecessor.artifact_shas[2])


def test_predecessor_same_semantics_byte_drift_still_rejects_original_pin(artifacts):
    root, _old, _, manifests, pairs, _, predecessor = artifacts
    predecessor.pair_path.write_bytes(predecessor.pair_path.read_bytes() + b"\n")
    with pytest.raises(upgrade.DatabaseReleaseError, match="pair_manifest_sha256_mismatch"):
        upgrade.load_predecessor_database(pair_path=predecessor.pair_path, predecessor_pair=pairs[0],
            target_manifest=manifests[1], workspace_root=root, build_root=root / "build",
            dist_root=root / "dist/new", expected_pair_sha256=predecessor.artifact_shas[2])


def test_upgrade_loader_rechecks_baseline_after_identity_read(artifacts, monkeypatch):
    root, _old, _, manifests, pairs, identities, predecessor = artifacts
    def changed(path):
        predecessor.manifest_path.write_bytes(predecessor.manifest_path.read_bytes() + b"\n")
        return identities[path.read_bytes()]
    monkeypatch.setattr(upgrade, "read_database_identity", changed)
    with pytest.raises(upgrade.DatabaseReleaseError, match="artifacts_changed"):
        upgrade.load_predecessor_database(pair_path=predecessor.pair_path, predecessor_pair=pairs[0],
            target_manifest=manifests[1], workspace_root=root, build_root=root / "build",
            dist_root=root / "dist/new", expected_pair_sha256=predecessor.artifact_shas[2])


@pytest.mark.parametrize("fault", [None, "silent_auto_upgrade", "activation_failure", "rollback_missing", "old_bytes_changed"])
@pytest.mark.parametrize("timeout", [None, 7.25], ids=["default_observation", "explicit_observation"])
def test_upgrade_smoke_uses_explicit_cli_consent_and_verifies_rollback(artifacts, monkeypatch, fault, timeout):
    root, old, new, manifests, pairs, identities, predecessor = artifacts
    package = new.parents[2]
    (package / "GenshinCalc.exe").write_bytes(b"inert-file-not-executed")
    calls = []
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "HTTPS_PROXY", "PYTHONPATH", "TEMP", "TMP"):
        monkeypatch.setenv(name, "isolated-ambient-sentinel")
    @contextlib.contextmanager
    def directory():
        sandbox = root / "smoke"
        sandbox.mkdir()
        yield sandbox
    def invoke(cli, package_root, profile, args, env, *, timeout_seconds):
        assert timeout_seconds == (7200 if timeout is None else timeout)
        calls.append(args)
        assert "GenshinCalcGUI.exe" not in str(cli)
        assert env["DSH_HOME"].startswith(str(root / "smoke"))
        assert not any(name in env for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "HTTPS_PROXY", "PYTHONPATH"))
        for name in ("TEMP", "TMP", "APPDATA", "LOCALAPPDATA", "USERPROFILE", "HOME", "DSH_HOME"):
            assert Path(env[name]).is_dir() and Path(env[name]).is_relative_to(root / "smoke")
        if len(calls) == 1:
            assert (profile / "genshin.db").read_bytes() == old.read_bytes()
            assert json.loads((profile / "release-pair-live-manifest.json").read_text()) == pairs[0]
            if fault == "silent_auto_upgrade": return 0, {"code": 0}
            return 1, {"code": 1, "reason": "database_live_manifest_not_bundled_release"}
        if len(calls) == 2:
            assert args == ["db", "activate-bundled", "--consent-source", "user_repair"]
            if fault == "activation_failure": return 1, {"code": 1, "reason": "migration_fault"}
            backups = profile / "backups"
            backups.mkdir()
            shutil.copyfile(profile / "genshin.db", backups / "rollback.db")
            shutil.copyfile(profile / "database-live-manifest.json", backups / "rollback.manifest.json")
            shutil.copyfile(profile / "database-live-receipt.json", backups / "rollback.receipt.json")
            if fault == "rollback_missing": (backups / "rollback.db").unlink()
            shutil.copyfile(new, profile / "genshin.db")
            write_database_release_manifest(profile / "database-live-manifest.json", manifests[1])
            write_release_pair_manifest(profile / "release-pair-live-manifest.json", pairs[1])
            receipt = create_database_live_receipt(manifests[1], identities[new.read_bytes()],
                installed_at=manifests[1]["released_at"])
            (profile / "database-live-receipt.json").write_text(json.dumps(receipt))
            journal = {"state": "operational", "rollback_name": "rollback.db",
                "rollback_manifest_name": "rollback.manifest.json", "rollback_receipt_name": "rollback.receipt.json"}
            (profile / "database-release-migration-journal.json").write_text(json.dumps(journal))
            if fault == "old_bytes_changed": predecessor.pair_path.write_text('{}')
            return 0, {"code": 0, "data": {"status": "activated", "migration_status": "installed", "rollback_retained": True}}
        assert args[:2] == ["whiteboard", "new"]
        Path(args[-1]).write_text('{"board":true}')
        return 0, {"code": 0}
    monkeypatch.setattr(upgrade, "_invoke", invoke)
    observation = {} if timeout is None else {"timeout_seconds": timeout}
    if fault:
        with pytest.raises((ValueError, upgrade.DatabaseReleaseError)):
            upgrade.run_packaged_database_upgrade_smoke(package_root=package, database_manifest=manifests[1],
                pair_manifest=pairs[1], predecessor=predecessor, temporary_directory=directory, **observation)
        assert len(calls) <= 2
    else:
        report = upgrade.run_packaged_database_upgrade_smoke(package_root=package, database_manifest=manifests[1],
            pair_manifest=pairs[1], predecessor=predecessor, temporary_directory=directory, **observation)
        assert len(calls) == 3
        assert report["mode"] == "accepted_predecessor_database_upgrade"
        assert report["prior_pair_sequence"] == 6 and report["current_pair_sequence"] == 7
        assert report["accepted_predecessor_bytes_unchanged"] is True
        assert report["observation_timeout_seconds"] == (7200 if timeout is None else timeout)


def test_cli_smoke_process_is_hidden_and_receipt_is_validated(monkeypatch):
    calls = []
    def run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"code":0}', stderr="")
    monkeypatch.setattr(upgrade.subprocess, "run", run)
    assert upgrade._invoke(Path("GenshinCalc.exe"), Path("package"), Path("profile"),
        ["whiteboard", "new"], {"TEMP": "isolated"}) == (0, {"code": 0})
    assert calls[0][1]["creationflags"] == getattr(upgrade.subprocess, "CREATE_NO_WINDOW", 0)
    assert calls[0][1]["env"] == {"TEMP": "isolated"}
    assert calls[0][1]["timeout"] == upgrade.DEFAULT_OBSERVATION_TIMEOUT_SECONDS == 7200


@pytest.mark.parametrize("timeout", [0.001, 7.25, 14400, 10**1000])
def test_explicit_positive_observation_has_no_arbitrary_size_cap(monkeypatch, timeout):
    calls = []
    environment = {"TEMP": "controlled"}
    def invoke(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout='{"code":0}', stderr="")
    monkeypatch.setattr(upgrade, "subprocess", SimpleNamespace(run=invoke, CREATE_NO_WINDOW=0x08000000))
    assert upgrade._invoke(Path("GenshinCalc.exe"), Path("package"), Path("profile"),
                           ["whiteboard", "new"], environment, timeout_seconds=timeout) == (0, {"code": 0})
    assert len(calls) == 1
    assert calls[0][1]["timeout"] == timeout
    assert calls[0][1]["env"] is environment
    assert calls[0][1]["creationflags"] == 0x08000000


@pytest.mark.parametrize("timeout", [True, False, None, 0, -1, 0.0, -.5,
                                     float("nan"), float("inf"), -float("inf"), "7200", [], {}])
def test_invalid_observation_rejected_before_files_or_process(monkeypatch, timeout):
    def unexpected(*_args, **_kwargs):
        pytest.fail("invalid observation must fail before subprocess or profile creation")
    monkeypatch.setattr(upgrade, "subprocess", SimpleNamespace(run=unexpected))
    with pytest.raises(ValueError, match="observation_timeout_invalid"):
        upgrade.run_packaged_database_upgrade_smoke(package_root="unused", database_manifest={}, pair_manifest={},
            predecessor=None, temporary_directory=unexpected, timeout_seconds=timeout)
    with pytest.raises(ValueError, match="observation_timeout_invalid"):
        upgrade._invoke(Path("unused"), Path("unused"), Path("unused"), [], {}, timeout_seconds=timeout)


def test_real_python_child_timeout_propagates_without_retry(tmp_path, monkeypatch):
    from genshin_app.frozen_selfcheck_isolation import clean_environment

    original = subprocess.run
    calls = []
    def controlled_python(*args, **kwargs):
        calls.append((args, kwargs))
        assert args[0] == ["inert-GenshinCalc.exe", "--data-dir", str(tmp_path), "whiteboard", "new"]
        # A real subprocess timeout at a controlled CLI seam; no App, GUI,
        # updater, API or credential path is invoked by this fixture.
        return original([sys.executable, "-c", "import time; time.sleep(30)"], **kwargs)
    monkeypatch.setattr(upgrade, "subprocess", SimpleNamespace(run=controlled_python,
        CREATE_NO_WINDOW=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        upgrade._invoke(Path("inert-GenshinCalc.exe"), tmp_path, tmp_path,
                        ["whiteboard", "new"], clean_environment(tmp_path), timeout_seconds=.05)
    assert caught.value.timeout == .05
    assert len(calls) == 1


@pytest.mark.parametrize("execute", [False, True])
def test_qualification_routes_new_database_without_same_database_relabel(monkeypatch, execute):
    calls = []
    predecessor = object()
    def invoke(**kwargs):
        calls.append(kwargs)
        return {"status": "passed", "mode": "accepted_predecessor_database_upgrade"}
    monkeypatch.setattr(build_dist, "run_packaged_database_upgrade_smoke", invoke)
    monkeypatch.setattr(build_dist, "_run_packaged_startup_rebind_smoke", lambda **_: pytest.fail("new DB must not use rebind"))
    result = build_dist._qualify_packaged_app_startup(execute_app=execute, package_root="package",
        database_manifest={}, pair_manifest={"pair_sequence": 7, "release_pair_id": "new"},
        predecessor_pair={"pair_sequence": 6}, predecessor_database=predecessor)
    assert len(calls) == int(execute)
    assert result["status"] == ("passed" if execute else "not_run_user_acceptance")


@pytest.mark.parametrize("phase", ["after_replace", "after_receipt_write", "after_manifest_write"])
def test_real_migration_fault_restores_profile_accepted_by_full_old_startup(coordinator, tmp_path, monkeypatch, phase):
    from test_database_release_migration import _fake_identity, _fixture_database
    from test_database_release_migration import _manifest as migration_manifest
    from test_database_startup_gate import (
        _APP_ARTIFACT_MANIFEST,
        _APP_ARTIFACT_MANIFEST_SHA256,
        _APP_PAYLOAD,
    )

    from genshin_app import database_bootstrap as startup
    from genshin_db import release_profile
    from genshin_db.release_integrity import build_mutable_layer_fingerprint

    profile, accepted, target = tmp_path / "profile", tmp_path / "old-accepted", tmp_path / "next.db"
    accepted.mkdir()
    old = accepted / "genshin.db"
    _fixture_database(old, upstream_commit="c" * 40, annotation_prefix="old", semantic_marker="old")
    _fixture_database(target, upstream_commit="d" * 40, annotation_prefix="new", semantic_marker="new")
    old_manifest = migration_manifest(old)
    coordinator(profile, old, old_manifest)
    old_pair = create_release_pair_manifest(old_manifest, release_pair_id="pair-old-app",
        pair_sequence=6, pair_policy="exact", app_release_id="app-old", app_version="0.45.0",
        app_artifact_manifest_sha256=_APP_ARTIFACT_MANIFEST_SHA256)
    write_database_release_manifest(accepted / "database-release-manifest.json", old_manifest)
    write_release_pair_manifest(accepted / "release-pair-manifest.json", old_pair)
    write_release_pair_manifest(profile / "release-pair-live-manifest.json", old_pair)
    before = {p.name: p.read_bytes() for p in accepted.iterdir()}
    old_sidecars = {name: (profile / name).read_bytes() for name in (
        "database-live-manifest.json", "database-live-receipt.json", "release-pair-live-manifest.json")}
    next_manifest = migration_manifest(target, release_id="db-7.0.0-r2", release_sequence=2)
    def fail(selected):
        if selected == phase: raise RuntimeError("injected pre-acceptance failure")
    with pytest.raises(RuntimeError, match="pre-acceptance"):
        coordinator(profile, target, next_manifest, fault_hook=fail)
    assert {p.name: p.read_bytes() for p in accepted.iterdir()} == before
    assert {name: (profile / name).read_bytes() for name in old_sidecars} == old_sidecars

    resources, package = tmp_path / "old-resources", tmp_path / "old-package"
    package.mkdir()
    (package / "app.bin").write_bytes(_APP_PAYLOAD)
    (package / "PACKAGE-FILES.sha256").write_bytes(_APP_ARTIFACT_MANIFEST)
    write_database_release_manifest(resources / "bundled/database-release-manifest.json", old_manifest)
    write_release_pair_manifest(resources / "bundled/release-pair-manifest.json", old_pair)
    for name, value in {"APP_RELEASE_ID": "app-old", "APP_VERSION": "0.45.0",
                       "DATABASE_RELEASE_ID": old_manifest["database_release_id"],
                       "RELEASE_PAIR_ID": "pair-old-app", "DATABASE_LOGIC_PROFILE": "logic-v1",
                       "DATABASE_DATA_CONTRACT_VERSION": 1}.items():
        monkeypatch.setattr(startup, name, value)
    monkeypatch.setattr(startup, "is_frozen", lambda: False)
    def identity(path):
        result = _fake_identity(path)
        result["mutable_layer_fingerprint"] = build_mutable_layer_fingerprint(path)
        return result
    monkeypatch.setattr(release_profile, "read_database_identity", identity)
    result = startup.prepare_database_startup(profile, resource_root=resources,
        app_package_root=package, pair_policy="exact")
    assert result["can_start"] is True and result["state"] == "current_pair_ready"
    assert result["release_pair_id"] == old_pair["release_pair_id"]
    actual = identity(profile / "genshin.db")
    receipt = json.loads((profile / "database-live-receipt.json").read_text())
    assert release_profile.database_live_receipt_matches(receipt, actual, old_manifest)
    assert {p.name: p.read_bytes() for p in accepted.iterdir()} == before
