from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from genshin_app import cli
from genshin_app import database_bootstrap as bootstrap
from genshin_app.version import (
    APP_RELEASE_ID,
    APP_VERSION,
    DATABASE_DATA_CONTRACT_VERSION,
    DATABASE_LOGIC_PROFILE,
    DATABASE_RELEASE_ID,
    RELEASE_PAIR_ID,
)
from genshin_db import release_profile as profile
from genshin_db.release_integrity import DEFAULT_MUTABLE_TABLES
from genshin_db.upstream_version import evidence_fingerprint

# Synthetic content and its declared current release must describe the same
# game version; mismatch cases below still alter their explicit contracts.
_FIXTURE_GAME_VERSION = DATABASE_RELEASE_ID.removeprefix("db-").rsplit("-r", 1)[0]

_APP_PAYLOAD = b"fixture application payload\n"
_APP_ARTIFACT_MANIFEST = (
    f"{hashlib.sha256(_APP_PAYLOAD).hexdigest()}  app.bin\n".encode()
)
_APP_ARTIFACT_MANIFEST_SHA256 = hashlib.sha256(_APP_ARTIFACT_MANIFEST).hexdigest()


def _core(marker: str = "core") -> dict:
    body = {
        "contract": "genshin-database-release-core-v1",
        "mutable_tables": sorted(DEFAULT_MUTABLE_TABLES),
        "tables": {
            "character": {
                "schema_sha256": hashlib.sha256(
                    (marker + "-schema").encode()
                ).hexdigest(),
                "row_count": 1,
                "content_sha256": hashlib.sha256(
                    (marker + "-content").encode()
                ).hexdigest(),
            }
        },
    }
    body["root_sha256"] = hashlib.sha256(
        json.dumps(
            body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return body


def _identity(
    *,
    ready: bool = True,
    marker: str = "core",
    sha: str = "a" * 64,
    meta: str = "b" * 64,
) -> dict:
    state = "ready" if ready else "missing"
    identity = {
        "sha256": sha,
        "size_bytes": 1234,
        "meta_fingerprint": meta,
        "upstream_commit": "c" * 40,
        "game_version": _FIXTURE_GAME_VERSION,
        "upstream_track": "OSCBWin",
        "upstream_updated_at": "2026-10-26T10:00:00+08:00",
        "upstream_version_evidence": {
            "resolver_version": 1,
            "status": "resolved",
            "version_evidence_ready": True,
            "version": _FIXTURE_GAME_VERSION,
            "track": "OSCBWin",
            "raw_version": "OSCBWin" + _FIXTURE_GAME_VERSION,
            "snapshot_commit": "c" * 40,
            "snapshot_updated_at": "2026-10-26T10:00:00+08:00",
            "evidence_kind": "snapshot_commit_subject",
            "evidence_commit": "c" * 40,
            "review_id": None,
            "configuration_trees": {
                "BinOutput": "1" * 40,
                "ExcelBinOutput": "2" * 40,
            },
            "source_trees": {
                "BinOutput": "1" * 40,
                "ExcelBinOutput": "2" * 40,
                "TextMap": "3" * 40,
            },
            "readme_version": "OSCBWin" + _FIXTURE_GAME_VERSION,
            "issues": [],
        },
        "version_lines": ["release"],
        "indexer_version": "7",
        "normalization_version": "2",
        "manual_content_fingerprint": "d" * 64,
        "mutable_layer_fingerprint": "e" * 64,
        "release_core": _core(marker),
        "datasets": {
            name: {"lines": {"release": {"state": state}}}
            for name in profile.REQUIRED_RELEASE_COMPONENTS
        },
    }
    evidence = identity["upstream_version_evidence"]
    evidence["evidence_fingerprint"] = evidence_fingerprint(evidence)
    return identity


def _manifest(identity: dict, *, channel: str) -> dict:
    return profile.validate_database_release_manifest(
        {
            "contract": profile.MANIFEST_CONTRACT,
            "schema_version": profile.MANIFEST_SCHEMA_VERSION,
            "database_release_id": DATABASE_RELEASE_ID,
            "release_sequence": 1,
            "channel": channel,
            "published_at": "2026-10-26T10:00:00+08:00",
            "released_at": "2026-10-26T11:00:00+08:00",
            "logic_profile": DATABASE_LOGIC_PROFILE,
            "data_contract_version": DATABASE_DATA_CONTRACT_VERSION,
            "database": {
                key: copy.deepcopy(identity[key])
                for key in (
                    "sha256",
                    "size_bytes",
                    "meta_fingerprint",
                    "upstream_commit",
                    "game_version",
                    "upstream_track",
                    "upstream_updated_at",
                    "upstream_version_evidence",
                    "version_lines",
                    "indexer_version",
                    "normalization_version",
                    "datasets",
                    "release_core",
                )
            },
            "download": None,
        }
    )


def _artifacts(
    root,
    identity,
    *,
    policy: str,
    component_policy: bool = False,
    pair_sequence: int = 1,
):
    channel = "stable" if policy == "exact" else "dev"
    manifest = _manifest(identity, channel=channel)
    if component_policy:
        manifest["component_policy"] = profile._component_policy()
        manifest = profile.validate_database_release_manifest(manifest)
    pair = profile.create_release_pair_manifest(
        manifest,
        release_pair_id=RELEASE_PAIR_ID,
        pair_sequence=pair_sequence,
        pair_policy=policy,
        app_release_id=APP_RELEASE_ID,
        app_version=APP_VERSION,
        app_artifact_manifest_sha256=_APP_ARTIFACT_MANIFEST_SHA256,
    )
    root.mkdir(parents=True, exist_ok=True)
    package_root = root / "package"
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "app.bin").write_bytes(_APP_PAYLOAD)
    (package_root / "PACKAGE-FILES.sha256").write_bytes(_APP_ARTIFACT_MANIFEST)
    directory = root / ("bundled" if policy == "exact" else "build-py")
    profile.write_database_release_manifest(
        directory / profile.MANIFEST_FILENAME,
        manifest,
    )
    profile.write_release_pair_manifest(
        directory / profile.PAIR_MANIFEST_FILENAME,
        pair,
    )
    return manifest, pair


def _seed_live_profile(data_dir, identity, manifest, pair):
    receipt = profile.create_database_live_receipt(
        manifest,
        identity,
        installed_at="2026-10-26T12:00:00+08:00",
    )
    profile.write_database_release_manifest(
        data_dir / bootstrap.LIVE_MANIFEST_FILENAME,
        manifest,
    )
    profile.write_release_pair_manifest(
        data_dir / bootstrap.LIVE_PAIR_MANIFEST_FILENAME,
        pair,
    )
    (data_dir / profile.LIVE_RECEIPT_FILENAME).write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )


def _freeze_startup(monkeypatch, resources):
    monkeypatch.setattr(bootstrap, "is_frozen", lambda: True)
    monkeypatch.setattr(bootstrap, "resource_dir", lambda: resources)
    monkeypatch.setattr(
        bootstrap.sys,
        "executable",
        str(resources / "package" / "GenshinCalcGUI.exe"),
    )


def _exact_startup_kwargs(resource_root):
    return {
        "resource_root": resource_root,
        "pair_policy": "exact",
        "app_package_root": resource_root / "package",
    }


def _database(data_dir):
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "genshin.db"
    path.write_bytes(b"fixture")
    return path


def test_exact_pair_seeds_reviewed_local_sidecars_and_reuses_them(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    identity = _identity()
    _artifacts(tmp_path / "resources", identity, policy="exact")
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)
    stages = []

    first = bootstrap.prepare_database_startup(
        data_dir,
        **_exact_startup_kwargs(tmp_path / "resources"),
        progress=lambda value: stages.append(value["stage"]),
    )

    assert first["can_start"] is True
    assert first["state"] == "current_pair_ready"
    assert first["initialized_sidecars"] is True
    assert stages == ["control_files", "database_integrity", "ready"]
    assert (data_dir / bootstrap.LIVE_MANIFEST_FILENAME).is_file()
    assert (data_dir / profile.LIVE_RECEIPT_FILENAME).is_file()
    assert (data_dir / bootstrap.LIVE_PAIR_MANIFEST_FILENAME).is_file()

    second = bootstrap.prepare_database_startup(
        data_dir,
        **_exact_startup_kwargs(tmp_path / "resources"),
    )
    assert second["initialized_sidecars"] is False
    assert second["state"] == "current_pair_ready"


def test_exact_pair_can_start_with_explicit_optional_capability_degradation(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    identity = _identity()
    identity["datasets"]["runtime_config"]["lines"]["release"]["state"] = "missing"
    identity["datasets"]["mechanic_semantics"]["lines"]["release"]["state"] = "missing"
    _artifacts(
        tmp_path / "resources",
        identity,
        policy="exact",
        component_policy=True,
    )
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)

    result = bootstrap.prepare_database_startup(
        data_dir,
        **_exact_startup_kwargs(tmp_path / "resources"),
    )

    assert result["can_start"] is True
    assert result["capability_state"] == "degraded"
    assert result["degraded_components"] == [
        "runtime_config", "mechanic_semantics"
    ]
    assert result["capabilities"]["components"]["mechanic_semantics"][
        "execution_authority"
    ] == "none"


def test_current_only_accepts_current_reviewed_database_without_writing_sidecars(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    identity = _identity()
    _artifacts(tmp_path / "resources", identity, policy="current_only")
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)

    result = bootstrap.prepare_database_startup(
        data_dir,
        resource_root=tmp_path / "resources",
        pair_policy="current_only",
    )

    assert result["state"] == "current_dev_ready"
    assert len(result["current_combination_fingerprint"]) == 64
    assert not (data_dir / bootstrap.LIVE_MANIFEST_FILENAME).exists()
    assert not (data_dir / profile.LIVE_RECEIPT_FILENAME).exists()
    assert not (data_dir / bootstrap.LIVE_PAIR_MANIFEST_FILENAME).exists()


def test_frozen_current_only_rebinds_prior_app_pair_for_same_database(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(bootstrap, "packaged_peer_processes", list)
    data_dir = tmp_path / "profile"
    database_path = _database(data_dir)
    database_before = database_path.read_bytes()
    identity = _identity()
    resources = tmp_path / "resources"
    manifest, current_pair = _artifacts(
        resources,
        identity,
        policy="current_only",
        pair_sequence=2,
    )
    prior_pair = profile.create_release_pair_manifest(
        manifest,
        release_pair_id="pair-0.45.0-db-7.1.53-r1",
        pair_sequence=1,
        pair_policy="current_only",
        app_release_id="app-0.45.0",
        app_version="0.45.0",
        app_artifact_manifest_sha256="9" * 64,
    )
    _seed_live_profile(data_dir, identity, manifest, prior_pair)
    manifest_before = (data_dir / bootstrap.LIVE_MANIFEST_FILENAME).read_bytes()
    receipt_before = (data_dir / profile.LIVE_RECEIPT_FILENAME).read_bytes()
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)
    _freeze_startup(monkeypatch, resources)

    first = bootstrap.prepare_database_startup(data_dir)

    assert first["can_start"] is True
    assert first["release_pair_id"] == RELEASE_PAIR_ID
    assert first["rebound_live_pair"] is True
    assert database_path.read_bytes() == database_before
    assert (data_dir / bootstrap.LIVE_MANIFEST_FILENAME).read_bytes() == manifest_before
    assert (data_dir / profile.LIVE_RECEIPT_FILENAME).read_bytes() == receipt_before
    assert profile.load_release_pair_manifest(
        data_dir / bootstrap.LIVE_PAIR_MANIFEST_FILENAME
    ) == current_pair

    second = bootstrap.prepare_database_startup(data_dir)
    assert second["can_start"] is True
    assert second["rebound_live_pair"] is False


def test_frozen_current_only_rebinds_same_version_new_artifact_with_unique_identity(
    tmp_path,
    monkeypatch,
):
    """A rebuilt App may reuse the DB but advances identity and sequence."""
    monkeypatch.setattr(bootstrap, "packaged_peer_processes", list)

    data_dir = tmp_path / "profile"
    database_path = _database(data_dir)
    database_before = database_path.read_bytes()
    identity = _identity()
    resources = tmp_path / "resources"
    manifest, current_pair = _artifacts(
        resources,
        identity,
        policy="current_only",
        pair_sequence=4,
    )
    predecessor = profile.create_release_pair_manifest(
        manifest,
        release_pair_id="pair-0.46.0-db-7.1.53-r1",
        pair_sequence=3,
        pair_policy="current_only",
        app_release_id="app-0.46.0",
        app_version=APP_VERSION,
        app_artifact_manifest_sha256="9" * 64,
    )
    _seed_live_profile(data_dir, identity, manifest, predecessor)
    manifest_before = (data_dir / bootstrap.LIVE_MANIFEST_FILENAME).read_bytes()
    receipt_before = (data_dir / profile.LIVE_RECEIPT_FILENAME).read_bytes()
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)
    _freeze_startup(monkeypatch, resources)

    result = bootstrap.prepare_database_startup(data_dir)

    assert result["can_start"] is True
    assert result["rebound_live_pair"] is True
    assert database_path.read_bytes() == database_before
    assert (data_dir / bootstrap.LIVE_MANIFEST_FILENAME).read_bytes() == manifest_before
    assert (data_dir / profile.LIVE_RECEIPT_FILENAME).read_bytes() == receipt_before
    assert profile.load_release_pair_manifest(
        data_dir / bootstrap.LIVE_PAIR_MANIFEST_FILENAME
    ) == current_pair


@pytest.mark.parametrize(
    ("prior_sequence", "reason"),
    [
        (3, "bundled_release_pair_downgrade_forbidden"),
        (2, "bundled_release_pair_sequence_conflict"),
    ],
)
def test_frozen_current_only_refuses_prior_pair_rollback_or_split(
    tmp_path,
    monkeypatch,
    prior_sequence,
    reason,
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    identity = _identity()
    resources = tmp_path / "resources"
    manifest, _current_pair = _artifacts(
        resources,
        identity,
        policy="current_only",
        pair_sequence=2,
    )
    prior_pair = profile.create_release_pair_manifest(
        manifest,
        release_pair_id=f"pair-prior-{prior_sequence}-db-7.1.53-r1",
        pair_sequence=prior_sequence,
        pair_policy="current_only",
        app_release_id="app-prior",
        app_version="0.45.0",
        app_artifact_manifest_sha256="8" * 64,
    )
    _seed_live_profile(data_dir, identity, manifest, prior_pair)
    pair_before = (data_dir / bootstrap.LIVE_PAIR_MANIFEST_FILENAME).read_bytes()
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)
    _freeze_startup(monkeypatch, resources)

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap.prepare_database_startup(data_dir)

    assert failure.value.reason == reason
    assert (data_dir / bootstrap.LIVE_PAIR_MANIFEST_FILENAME).read_bytes() == pair_before


def test_current_only_rejects_in_place_knowledge_layer_change(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    packaged = _identity()
    edited = copy.deepcopy(packaged)
    edited.update(
        sha256="8" * 64,
        meta_fingerprint="9" * 64,
        mutable_layer_fingerprint="7" * 64,
    )
    _artifacts(tmp_path / "resources", packaged, policy="current_only")
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: edited)

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap.prepare_database_startup(
            data_dir,
            resource_root=tmp_path / "resources",
            pair_policy="current_only",
        )
    assert failure.value.reason == "database_release_identity_unmanifested"
    assert failure.value.details["state"] == "incompatible_legacy"


@pytest.mark.parametrize(
    ("with_manifest", "ready", "reason"),
    [
        (False, True, "database_release_identity_unmanifested"),
        (True, False, "database_required_components_not_ready"),
    ],
)
def test_current_only_refuses_unknown_or_incomplete_database(
    tmp_path,
    monkeypatch,
    with_manifest,
    ready,
    reason,
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    identity = _identity(ready=ready)
    if with_manifest:
        _artifacts(tmp_path / "resources", identity, policy="current_only")
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap.prepare_database_startup(
            data_dir,
            resource_root=tmp_path / "resources",
            pair_policy="current_only",
        )
    assert failure.value.reason == reason
    assert failure.value.details["state"] == "incompatible_legacy"


def test_exact_pair_app_identity_mismatch_fails_before_runtime_startup(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    identity = _identity()
    _manifest_value, pair = _artifacts(
        tmp_path / "resources",
        identity,
        policy="exact",
    )
    pair["release_pair_id"] = "pair-other"
    pair_path = (
        tmp_path
        / "resources"
        / "bundled"
        / profile.PAIR_MANIFEST_FILENAME
    )
    pair_path.write_text(json.dumps(pair), encoding="utf-8")
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap.prepare_database_startup(
            data_dir,
            **_exact_startup_kwargs(tmp_path / "resources"),
        )
    assert failure.value.reason == "release_pair_app_contract_mismatch"


def test_exact_pair_rejects_app_artifact_manifest_drift_before_database_read(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    identity = _identity()
    resources = tmp_path / "resources"
    _artifacts(resources, identity, policy="exact")
    replacement = b"different but internally consistent payload\n"
    (resources / "package" / "app.bin").write_bytes(replacement)
    replacement_manifest = (
        f"{hashlib.sha256(replacement).hexdigest()}  app.bin\n".encode()
    )
    (resources / "package" / "PACKAGE-FILES.sha256").write_bytes(
        replacement_manifest
    )
    monkeypatch.setattr(
        profile,
        "read_database_identity",
        lambda _path: pytest.fail("app artifact mismatch must fail before DB scan"),
    )

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap.prepare_database_startup(
            data_dir,
            **_exact_startup_kwargs(resources),
        )
    assert failure.value.reason == "release_pair_app_artifact_mismatch"


def test_exact_rejects_changed_app_file_before_database_read(tmp_path, monkeypatch):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    identity = _identity()
    resources = tmp_path / "resources"
    _artifacts(resources, identity, policy="exact")
    (resources / "package" / "app.bin").write_bytes(b"tampered payload\n")
    monkeypatch.setattr(
        profile,
        "read_database_identity",
        lambda _path: pytest.fail("package mismatch must fail before DB scan"),
    )

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap.prepare_database_startup(
            data_dir,
            **_exact_startup_kwargs(resources),
        )
    assert failure.value.reason == "supply_chain_package_manifest_hash_mismatch"


def test_exact_rejects_self_consistent_locally_rebuilt_live_pair(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    packaged_identity = _identity()
    _artifacts(tmp_path / "resources", packaged_identity, policy="exact")

    forged_identity = _identity(
        marker="forged",
        sha="8" * 64,
        meta="9" * 64,
    )
    forged_manifest = _manifest(forged_identity, channel="stable")
    forged_pair = profile.create_release_pair_manifest(
        forged_manifest,
        release_pair_id=RELEASE_PAIR_ID,
        pair_sequence=1,
        pair_policy="exact",
        app_release_id=APP_RELEASE_ID,
        app_version=APP_VERSION,
        app_artifact_manifest_sha256=_APP_ARTIFACT_MANIFEST_SHA256,
    )
    forged_receipt = profile.create_database_live_receipt(
        forged_manifest,
        forged_identity,
        installed_at="2026-10-26T12:00:00+08:00",
    )
    profile.write_database_release_manifest(
        data_dir / bootstrap.LIVE_MANIFEST_FILENAME,
        forged_manifest,
    )
    profile.write_release_pair_manifest(
        data_dir / bootstrap.LIVE_PAIR_MANIFEST_FILENAME,
        forged_pair,
    )
    (data_dir / profile.LIVE_RECEIPT_FILENAME).write_text(
        json.dumps(forged_receipt),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        profile,
        "read_database_identity",
        lambda _path: forged_identity,
    )

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap.prepare_database_startup(
            data_dir,
            **_exact_startup_kwargs(tmp_path / "resources"),
        )
    assert failure.value.reason == "database_live_manifest_not_bundled_release"


def test_exact_allows_mutable_live_layer_only_through_bundled_receipt_bridge(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    packaged_identity = _identity()
    packaged_manifest, packaged_pair = _artifacts(
        tmp_path / "resources",
        packaged_identity,
        policy="exact",
    )
    installed_identity = copy.deepcopy(packaged_identity)
    installed_identity.update(
        sha256="4" * 64,
        meta_fingerprint="5" * 64,
        mutable_layer_fingerprint="6" * 64,
    )
    receipt = profile.create_database_live_receipt(
        packaged_manifest,
        installed_identity,
        installed_at="2026-10-26T12:00:00+08:00",
    )
    profile.write_database_release_manifest(
        data_dir / bootstrap.LIVE_MANIFEST_FILENAME,
        packaged_manifest,
    )
    profile.write_release_pair_manifest(
        data_dir / bootstrap.LIVE_PAIR_MANIFEST_FILENAME,
        packaged_pair,
    )
    (data_dir / profile.LIVE_RECEIPT_FILENAME).write_text(
        json.dumps(receipt),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        profile,
        "read_database_identity",
        lambda _path: installed_identity,
    )

    result = bootstrap.prepare_database_startup(
        data_dir,
        **_exact_startup_kwargs(tmp_path / "resources"),
    )
    assert result["state"] == "current_pair_ready"
    assert result["initialized_sidecars"] is False


def test_incomplete_migration_journal_blocks_without_deep_database_read(
    tmp_path,
    monkeypatch,
):
    data_dir = tmp_path / "profile"
    _database(data_dir)
    identity = _identity()
    _artifacts(tmp_path / "resources", identity, policy="current_only")
    (data_dir / bootstrap.MIGRATION_JOURNAL_FILENAME).write_text(
        json.dumps({"state": "swap_started"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        profile,
        "read_database_identity",
        lambda _path: pytest.fail("recovery gate must not inspect active database"),
    )

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        bootstrap.prepare_database_startup(
            data_dir,
            resource_root=tmp_path / "resources",
            pair_policy="current_only",
        )
    assert failure.value.reason == "database_migration_incomplete"
    assert failure.value.details["state"] == "recovery_available"


def test_cli_runtime_service_applies_gate_before_importing_service(monkeypatch, tmp_path):
    monkeypatch.setattr(
        cli,
        "_require_runtime_database",
        lambda _path: (_ for _ in ()).throw(
            bootstrap.DatabaseStartupError("database_current_dev_stale")
        ),
    )
    monkeypatch.setattr(
        "genshin_db.service.serve",
        lambda *_args, **_kwargs: pytest.fail("service must not start before admission"),
    )

    with pytest.raises(bootstrap.DatabaseStartupError) as failure:
        cli._cmd_db_serve(SimpleNamespace(data_dir=str(tmp_path), port=0))
    assert failure.value.reason == "database_current_dev_stale"
