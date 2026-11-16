"""Portable smoke must exercise its actual admission policy, not invent an upgrade."""
from __future__ import annotations

import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from genshin_db import release_profile
from scripts import build_dist
from tests.test_database_startup_gate import (
    _artifacts,
    _database,
    _exact_startup_kwargs,
    _identity,
    _manifest,
    _seed_live_profile,
)


@pytest.fixture
def package(tmp_path, monkeypatch):
    app = tmp_path / "app"
    bundled = app / "_internal/bundled"
    bundled.mkdir(parents=True)
    data = b"exact previously verified database fixture"
    database = bundled / "genshin.db"
    database.write_bytes(data)
    (app / "GenshinCalc.exe").write_bytes(b"inert fixture: never executed")
    identity = _identity(sha=hashlib.sha256(data).hexdigest())
    identity["size_bytes"] = len(data)
    manifest = _manifest(identity, channel="stable")
    release_profile.write_database_release_manifest(bundled / "database-release-manifest.json", manifest)
    predecessor = release_profile.create_release_pair_manifest(manifest, release_pair_id="pair-old",
        pair_sequence=2, pair_policy="exact", app_release_id="app-old", app_version="0.46.0",
        app_artifact_manifest_sha256="1" * 64)
    current = release_profile.create_release_pair_manifest(manifest, release_pair_id="pair-new",
        pair_sequence=3, pair_policy="exact", app_release_id="app-new", app_version="0.46.0",
        app_artifact_manifest_sha256="2" * 64)
    monkeypatch.setattr(build_dist, "ROOT", tmp_path)
    monkeypatch.setattr(build_dist, "read_database_identity", lambda *_: pytest.fail("Do not repeat deep identity parsing"))
    return app, database, identity, manifest, predecessor, current


@pytest.mark.parametrize("predecessor_database", [None, object()])
def test_exact_qualifies_clean_startup_and_old_pair_rejection_not_automatic_upgrade(monkeypatch, predecessor_database):
    calls = []
    monkeypatch.setattr(build_dist, "_run_packaged_clean_startup_smoke", lambda **_: calls.append("clean") or {"status": "passed"})
    monkeypatch.setattr(build_dist, "_run_packaged_exact_predecessor_rejection", lambda **_: calls.append("reject-old") or {"status": "passed"})
    monkeypatch.setattr(build_dist, "_run_packaged_startup_rebind_smoke", lambda **_: pytest.fail("Exact cannot rebind"))
    monkeypatch.setattr(build_dist, "run_packaged_database_upgrade_smoke", lambda **_: pytest.fail("Use signed installation upgrade"))
    result = build_dist._qualify_packaged_app_startup(execute_app=True, package_root="unused",
        database_manifest={}, pair_manifest={"pair_policy": "exact"}, predecessor_pair={"pair_sequence": 2},
        predecessor_database=predecessor_database)
    assert calls == ["clean", "reject-old"]
    assert result["signed_installation_upgrade"] == "separate_qualification_required"


@pytest.mark.parametrize("mutation", [None, "database", "manifest", "receipt", "pair", "output", "wrong-reason", "accepted"])
def test_exact_predecessor_rejection_requires_exact_error_and_untouched_profile(package, monkeypatch, mutation):
    app, _database, identity, manifest, predecessor, _current = package
    def invoke(command, **kwargs):
        assert kwargs["timeout"] == 900
        profile = app.parent / command[2]
        output = app.parent / command[-1]
        if mutation == "database":
            target = profile / "genshin.db"
            original_stat = target.stat()
            target.write_bytes(b"!" + target.read_bytes()[1:])
            os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        elif mutation in {"manifest", "receipt", "pair"}:
            name = {"manifest": "database-live-manifest.json", "receipt": "database-live-receipt.json",
                    "pair": "release-pair-live-manifest.json"}[mutation]
            with (profile / name).open("a", encoding="utf-8") as stream:
                stream.write(" ")
        elif mutation == "output":
            output.write_text("{}", "utf-8")
        reason = "different_error" if mutation == "wrong-reason" else "database_live_pair_not_bundled_release"
        return SimpleNamespace(returncode=0 if mutation == "accepted" else 1, stdout="", stderr=json.dumps({"reason": reason}))
    monkeypatch.setattr(build_dist.subprocess, "run", invoke)
    def qualify():
        return build_dist._run_packaged_exact_predecessor_rejection(package_root=app,
            database_manifest=manifest, predecessor_pair=predecessor, validated_database_identity=identity)
    if mutation is None:
        assert qualify()["profile_unchanged"] is True
    else:
        with pytest.raises(ValueError):
            qualify()
    reports = list((app.parent / "build-py/startup-observations").glob("*.json"))
    report = json.loads(reports[0].read_text("utf-8"))
    assert report["status"] == ("passed" if mutation is None else "failed")
    assert "frozen_rejection" in report["phases"]


@pytest.mark.parametrize("mutation", [None, "database", "pair"])
def test_clean_startup_uses_real_full_byte_and_result_pair_checks(package, monkeypatch, mutation):
    app, _database, _identity_value, _manifest_value, predecessor, current = package
    def invoke(command, **kwargs):
        assert kwargs["timeout"] == 900
        profile = app.parent / command[2]
        (app.parent / command[-1]).write_text("{}", "utf-8")
        release_profile.write_release_pair_manifest(profile / "release-pair-live-manifest.json",
                                                   predecessor if mutation == "pair" else current)
        if mutation == "database":
            path = profile / "genshin.db"
            path.write_bytes(b"!" + path.read_bytes()[1:])
        return SimpleNamespace(returncode=0, stdout="{}", stderr="")
    monkeypatch.setattr(build_dist.subprocess, "run", invoke)
    def qualify():
        return build_dist._run_packaged_clean_startup_smoke(package_root=app, pair_manifest=current)
    if mutation is None:
        assert qualify()["status"] == "passed"
    else:
        with pytest.raises(ValueError):
            qualify()


def test_cached_identity_cannot_substitute_another_database(package):
    app, _database, identity, manifest, predecessor, _current = package
    with pytest.raises(ValueError, match="validated_identity_mismatch"):
        build_dist._run_packaged_exact_predecessor_rejection(package_root=app, database_manifest=manifest,
            predecessor_pair=predecessor, validated_database_identity={**identity, "sha256": "a" * 64})


def test_bytes_proof_rejects_equal_size_same_mtime_tampering(tmp_path):
    database = tmp_path / "genshin.db"
    database.write_bytes(b"abc")
    original = build_dist._database_artifact_bytes(database)
    stamp = database.stat()
    database.write_bytes(b"abd")
    os.utime(database, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with pytest.raises(ValueError, match="database_bytes_mismatch"):
        build_dist._database_artifact_bytes(database, original)


def test_real_exact_admission_still_refuses_valid_predecessor_without_touching_profile(tmp_path, monkeypatch):
    from genshin_app import database_bootstrap

    data = tmp_path / "profile"
    _database(data)
    identity = _identity()
    manifest, current = _artifacts(tmp_path / "resources", identity, policy="exact", pair_sequence=3)
    predecessor = release_profile.create_release_pair_manifest(manifest, release_pair_id="accepted-prior-pair",
        pair_sequence=2, pair_policy="exact", app_release_id="accepted-prior-app", app_version="0.46.0",
        app_artifact_manifest_sha256=current["app"]["artifact_manifest"]["sha256"])
    _seed_live_profile(data, identity, manifest, predecessor)
    before = {path.name: path.read_bytes() for path in data.iterdir() if path.is_file()}
    monkeypatch.setattr(release_profile, "read_database_identity", lambda *_: pytest.fail("Reject mismatched pair before deep DB read"))
    with pytest.raises(database_bootstrap.DatabaseStartupError) as failure:
        database_bootstrap.prepare_database_startup(data, **_exact_startup_kwargs(tmp_path / "resources"))
    assert failure.value.reason == "database_live_pair_not_bundled_release"
    assert {name: (data / name).read_bytes() for name in before} == before
