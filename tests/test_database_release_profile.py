from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import release_profile as profile
from genshin_db.release_integrity import DEFAULT_MUTABLE_TABLES
from genshin_db.upstream_version import evidence_fingerprint


def test_read_database_identity_escapes_sqlite_uri_path(tmp_path):
    database = tmp_path / "数据库 #验收%25" / "genshin.db"
    database.parent.mkdir()
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO meta VALUES ('game_version','7.2.50')")
        connection.execute("CREATE TABLE character (version_line TEXT)")
        connection.execute("INSERT INTO character VALUES ('release')")

    before = database.read_bytes()
    identity = profile.read_database_identity(database)
    assert identity["sha256"] == hashlib.sha256(before).hexdigest()
    assert identity["game_version"] == "7.2.50"
    assert identity["version_lines"] == ["release"]
    assert database.read_bytes() == before


def _core(marker="core"):
    body = {
        "contract": "genshin-database-release-core-v1",
        "mutable_tables": sorted(DEFAULT_MUTABLE_TABLES),
        "tables": {
            "character": {
                "schema_sha256": hashlib.sha256((marker + "-schema").encode()).hexdigest(),
                "row_count": 1,
                "content_sha256": hashlib.sha256((marker + "-content").encode()).hexdigest(),
            }
        },
    }
    body["root_sha256"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return body


def _identity(*, ready=True, sha="a" * 64, meta="b" * 64, core_marker="core"):
    state = "ready" if ready else "missing"
    identity = {
        "sha256": sha,
        "size_bytes": 1234,
        "meta_fingerprint": meta,
        "upstream_commit": "c" * 40,
        "game_version": "7.1.51",
        "upstream_track": "OSCBWin",
        "upstream_updated_at": "2026-10-26T10:00:00+08:00",
        "upstream_version_evidence": {
            "resolver_version": 1,
            "status": "resolved",
            "version_evidence_ready": True,
            "version": "7.1.51",
            "track": "OSCBWin",
            "raw_version": "OSCBWin7.1.51",
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
            "readme_version": "OSCBWin7.1.51",
            "issues": [],
        },
        "version_lines": ["release"],
        "indexer_version": "7",
        "normalization_version": "2",
        "manual_content_fingerprint": "d" * 64,
        "mutable_layer_fingerprint": "e" * 64,
        "release_core": _core(core_marker),
        "datasets": {
            name: {"lines": {"release": {"state": state}}}
            for name in profile.REQUIRED_RELEASE_COMPONENTS
        },
    }
    return _with_evidence_fingerprint(identity)


def _with_evidence_fingerprint(identity):
    evidence = identity["upstream_version_evidence"]
    evidence["evidence_fingerprint"] = evidence_fingerprint(evidence)
    return identity


def _manifest(*, ident=None, release_id="db-7.1.51-r1", sequence=1,
              logic="logic-v1", pair_id="pair-1", app_id="app-0.45.0"):
    ident = ident or _identity()
    del pair_id, app_id
    return profile.validate_database_release_manifest({
        "contract": profile.MANIFEST_CONTRACT,
        "schema_version": profile.MANIFEST_SCHEMA_VERSION,
        "database_release_id": release_id,
        "release_sequence": sequence,
        "channel": "stable",
        "published_at": "2026-10-26T10:00:00+08:00",
        "released_at": "2026-10-26T11:00:00+08:00",
        "logic_profile": logic,
        "data_contract_version": 1,
        "database": {
            key: copy.deepcopy(ident[key])
            for key in (
                "sha256", "size_bytes", "meta_fingerprint", "upstream_commit",
                "game_version", "upstream_track", "upstream_updated_at",
                "upstream_version_evidence", "version_lines", "datasets",
                "indexer_version", "normalization_version", "release_core",
            )
        },
        "download": None,
    })


def _legacy_manifest(
    *,
    ident=None,
    release_id="db-7.1.51-r1",
    sequence=1,
    logic="logic-v1",
    pair_id="pair-1",
    app_id="app-0.45.0",
):
    value = _manifest(
        ident=ident,
        release_id=release_id,
        sequence=sequence,
        logic=logic,
    )
    value.update(
        contract=profile.LEGACY_MANIFEST_CONTRACT,
        schema_version=profile.LEGACY_MANIFEST_SCHEMA_VERSION,
        release_pair_id=pair_id,
        app_release_id=app_id,
    )
    return profile.validate_database_release_manifest(value)


def _inspect(path, **kwargs):
    kwargs.pop("app_version", None)
    return profile.inspect_database_profile(
        path,
        app_release_id="app-0.45.0",
        release_pair_id="pair-1",
        required_database_release_id="db-7.1.51-r1",
        pair_policy="exact",
        **kwargs,
    )


def test_manifest_roundtrip_and_download_identity(tmp_path):
    value = _manifest()
    target = profile.write_database_release_manifest(tmp_path / profile.MANIFEST_FILENAME, value)
    assert profile.load_database_release_manifest(target) == value
    bad = copy.deepcopy(value)
    bad["download"] = {
        "url": "https://example.invalid/db",
        "sha256": "f" * 64,
        "size_bytes": 1234,
    }
    with pytest.raises(profile.DatabaseReleaseError, match="identity_mismatch"):
        profile.validate_database_release_manifest(bad)


def test_v2_manifest_is_readable_but_does_not_control_new_pair():
    legacy = _legacy_manifest(pair_id="pair-old", app_id="app-0.45.0")
    normalized = profile.validate_database_release_manifest(legacy)

    assert normalized["contract"] == profile.LEGACY_MANIFEST_CONTRACT
    assert normalized["release_pair_id"] == "pair-old"
    pair = profile.create_release_pair_manifest(
        normalized,
        release_pair_id="pair-new",
        pair_sequence=2,
        pair_policy="exact",
        app_release_id="app-0.46.0",
        app_version="0.46.0",
        app_artifact_manifest_sha256="f" * 64,
    )
    assert pair["release_pair_id"] == "pair-new"
    assert pair["app"]["app_release_id"] == "app-0.46.0"
    assert profile.release_pair_matches_database_manifest(pair, normalized)


def test_database_semantic_equality_ignores_wrapper_pair_and_artifact_bytes():
    current = _manifest()
    legacy = _legacy_manifest()
    legacy["database"]["sha256"] = "9" * 64
    legacy["database"]["size_bytes"] = 9876

    assert profile.database_release_semantically_equal(current, legacy)

    changed_core = copy.deepcopy(legacy)
    changed_core["database"]["release_core"] = _core("changed")
    assert not profile.database_release_semantically_equal(current, changed_core)


def test_manifest_rejects_tampered_upstream_evidence_fingerprint():
    value = _manifest()
    tampered = copy.deepcopy(value)
    tampered["database"]["upstream_version_evidence"]["evidence_kind"] = "tampered"
    with pytest.raises(profile.DatabaseReleaseError) as failure:
        profile.validate_database_release_manifest(tampered)
    assert failure.value.reason == "database_release_upstream_evidence_invalid"


def test_manifest_rejects_self_consistent_but_semantically_fake_evidence():
    value = _manifest()
    tampered = copy.deepcopy(value)
    evidence = tampered["database"]["upstream_version_evidence"]
    evidence["track"] = "release"
    evidence["raw_version"] = "release7.1.51"
    tampered["database"]["upstream_track"] = "release"
    evidence["evidence_fingerprint"] = evidence_fingerprint(evidence)

    with pytest.raises(profile.DatabaseReleaseError) as failure:
        profile.validate_database_release_manifest(tampered)

    assert failure.value.reason == "database_release_upstream_evidence_invalid"


def test_live_receipt_allows_diagnostic_count_changes_but_binds_knowledge_fingerprint():
    packaged = _identity()
    packaged["datasets"]["mechanic_semantics"] = {
        "schema_version": 1,
        "lines": {
            "release": {
                "state": "ready",
                "schema_version": 1,
                "counts": {"facts": 0, "descriptors": 0, "heads": 0},
                "execution_support": "not_evaluated",
            }
        },
    }
    manifest = _manifest(ident=packaged)
    receipt = profile.create_database_live_receipt(
        manifest,
        packaged,
        installed_at="2026-10-26T12:00:00+08:00",
    )

    edited = copy.deepcopy(packaged)
    edited["datasets"]["mechanic_semantics"]["lines"]["release"]["counts"] = {
        "facts": 3,
        "descriptors": 2,
        "heads": 5,
    }
    edited["datasets"]["mechanic_semantics"]["lines"]["release"][
        "execution_support"
    ] = "reviewed_locally"
    assert profile.database_live_receipt_matches(receipt, edited, manifest)

    changed_knowledge = copy.deepcopy(edited)
    changed_knowledge["mutable_layer_fingerprint"] = "7" * 64
    assert not profile.database_live_receipt_matches(receipt, changed_knowledge, manifest)

    incompatible = copy.deepcopy(edited)
    incompatible["datasets"]["mechanic_semantics"]["lines"]["release"][
        "schema_version"
    ] = 2
    assert not profile.database_live_receipt_matches(receipt, incompatible, manifest)

    wrong_source = copy.deepcopy(edited)
    wrong_source["datasets"]["runtime_config"]["lines"]["release"][
        "upstream_commit"
    ] = "9" * 40
    assert not profile.database_live_receipt_matches(receipt, wrong_source, manifest)


def test_v2_live_receipt_remains_readable_without_granting_pair_authority():
    identity = _identity()
    manifest = _legacy_manifest(ident=identity)
    receipt = profile.create_database_live_receipt(
        manifest,
        identity,
        installed_at="2026-10-26T12:00:00+08:00",
    )
    receipt.update(
        contract=profile.LEGACY_LIVE_RECEIPT_CONTRACT,
        schema_version=2,
        release_pair_id="pair-1",
        app_release_id="app-0.45.0",
    )
    receipt_without_fingerprint = {
        key: value for key, value in receipt.items() if key != "receipt_fingerprint"
    }
    receipt["receipt_fingerprint"] = profile._fingerprint(receipt_without_fingerprint)

    normalized = profile.validate_database_live_receipt(receipt)
    assert normalized["release_pair_id"] == "pair-1"
    assert profile.database_live_receipt_matches(normalized, identity, manifest)


def test_create_manifest_requires_release_components(monkeypatch, tmp_path):
    database = tmp_path / "genshin.db"
    database.write_bytes(b"fixture")
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: _identity(ready=False))
    with pytest.raises(profile.DatabaseReleaseError) as failure:
        profile.create_database_release_manifest(
            database,
            database_release_id="db-7.1.51-r1",
            release_sequence=1,
            channel="dev",
            release_pair_id="pair-1",
            app_release_id="app-0.45.0",
            published_at="2026-10-26T10:00:00+08:00",
            logic_profile="logic-v1",
            data_contract_version=1,
        )
    assert failure.value.reason == "database_release_components_not_ready"


def test_new_manifest_separates_required_core_from_optional_capabilities(
    monkeypatch, tmp_path
):
    database = tmp_path / "genshin.db"
    database.write_bytes(b"fixture")
    identity = _identity()
    identity["datasets"]["runtime_config"]["lines"]["release"]["state"] = "missing"
    identity["datasets"]["mechanic_semantics"]["lines"]["release"]["state"] = "missing"
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)

    manifest = profile.create_database_release_manifest(
        database,
        database_release_id="db-7.1.51-r1",
        release_sequence=1,
        channel="dev",
        release_pair_id="pair-1",
        app_release_id="app-0.45.0",
        published_at="2026-10-26T10:00:00+08:00",
        logic_profile="logic-v1",
        data_contract_version=1,
    )
    policy = manifest["component_policy"]
    assert policy["components"]["equipment"]["requirement"] == "required"
    assert policy["components"]["runtime_config"]["requirement"] == "optional"
    assert policy["components"]["mechanic_semantics"] == {
        "requirement": "optional",
        "role": "knowledge",
        "execution_authority": "none",
    }
    result = _inspect(
        database,
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=manifest,
    )
    assert result["state"] == "current_pair_ready"
    assert result["can_continue_current"] is True
    assert result["capability_state"] == "degraded"
    assert result["degraded_components"] == [
        "runtime_config", "mechanic_semantics"
    ]


def test_ready_runtime_dataset_preserves_nested_source_degradation():
    identity = _identity()
    runtime = identity["datasets"]["runtime_config"]["lines"]["release"]
    runtime.update(
        source_capability_state="degraded",
        source_capabilities={
            "animator": {"state": "unavailable", "requirement": "optional"},
        },
    )

    result = profile.database_capability_projection(identity)

    assert result["components"]["runtime_config"]["available"] is True
    assert result["state"] == "degraded"
    assert result["partial_components"] == ["runtime_config"]
    assert result["degraded_components"] == ["runtime_config"]


def test_explicit_component_commitment_still_fails_closed(monkeypatch, tmp_path):
    database = tmp_path / "genshin.db"
    database.write_bytes(b"fixture")
    identity = _identity()
    identity["datasets"]["runtime_config"]["lines"]["release"]["state"] = "missing"
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)

    with pytest.raises(profile.DatabaseReleaseError) as failure:
        profile.create_database_release_manifest(
            database,
            database_release_id="db-7.1.51-r1",
            release_sequence=1,
            channel="dev",
            release_pair_id="pair-1",
            app_release_id="app-0.45.0",
            published_at="2026-10-26T10:00:00+08:00",
            logic_profile="logic-v1",
            data_contract_version=1,
            required_components=("equipment", "runtime_config"),
        )

    assert failure.value.reason == "database_release_components_not_ready"


def test_legacy_manifest_retains_strict_all_component_promise(monkeypatch, tmp_path):
    database = tmp_path / "genshin.db"
    database.write_bytes(b"fixture")
    identity = _identity()
    identity["datasets"]["runtime_config"]["lines"]["release"]["state"] = "missing"
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)
    manifest = _legacy_manifest(ident=identity)

    result = _inspect(
        database,
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=manifest,
    )

    assert result["state"] == "incompatible_legacy"
    assert result["can_continue_current"] is False
    assert result["missing_components"] == ["runtime_config"]


def test_component_policy_cannot_grant_knowledge_execution_authority():
    value = _manifest()
    value["component_policy"] = profile._component_policy()
    value["component_policy"]["components"]["mechanic_semantics"][
        "execution_authority"
    ] = "direct"

    with pytest.raises(profile.DatabaseReleaseError) as failure:
        profile.validate_database_release_manifest(value)

    assert failure.value.reason == "database_release_component_policy_invalid"


def test_profile_first_install_is_read_only(tmp_path):
    result = _inspect(
        tmp_path / "missing.db",
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        bundled_manifest=_manifest(),
    )
    assert result["state"] == "first_install"
    assert result["available_release_id"] == "db-7.1.51-r1"
    assert not (tmp_path / "missing.db").exists()


def test_complete_fixed_profile_may_decline_compatible_update(monkeypatch, tmp_path):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"live")
    current_identity = _identity()
    remote_identity = _identity(sha="e" * 64, meta="f" * 64)
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: current_identity)
    result = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=_manifest(ident=current_identity),
        remote_manifest=_manifest(
            ident=remote_identity, release_id="db-7.1.51-r2", sequence=2,
            pair_id="pair-2",
        ),
    )
    assert result["state"] == "pair_update_available"
    assert result["can_continue_current"] is True


def test_update_requiring_new_logic_is_pair_update(monkeypatch, tmp_path):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"live")
    current_identity = _identity()
    remote_identity = _identity(sha="e" * 64, meta="f" * 64)
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: current_identity)
    result = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=_manifest(ident=current_identity),
        remote_manifest=_manifest(
            ident=remote_identity,
            release_id="db-7.1.51-r2",
            sequence=2,
            logic="logic-v2",
            pair_id="pair-2",
            app_id="app-0.46.0",
        ),
    )
    assert result["state"] == "pair_update_available"
    assert result["can_continue_current"] is True


def test_incomplete_old_database_is_not_a_fixed_offline_release(monkeypatch, tmp_path):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"legacy")
    legacy = _identity(ready=False)
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: legacy)
    result = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        remote_manifest=_manifest(
            ident=_identity(sha="8" * 64, meta="9" * 64),
            release_id="db-7.1.51-r2", sequence=2, pair_id="pair-2"
        ),
    )
    assert result["state"] == "incompatible_legacy"
    assert result["can_continue_current"] is False
    assert set(result["missing_components"]) == set(profile.REQUIRED_RELEASE_COMPONENTS)


def test_live_manifest_mismatch_and_future_contract_fail_closed(monkeypatch, tmp_path):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"live")
    identity = _identity()
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)
    mismatch = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=_manifest(ident=_identity(sha="e" * 64)),
    )
    assert mismatch["state"] == "corrupt"

    future = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=_manifest(
            ident=identity,
            logic="logic-v2",
        ),
    )
    assert future["state"] == "pair_mismatch"

    reusable_database = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=_manifest(
            ident=identity,
            pair_id="pair-old",
            app_id="app-0.44.0",
        ),
    )
    assert reusable_database["state"] == "current_pair_ready"


def test_incomplete_migration_journal_wins_before_opening_database(monkeypatch, tmp_path):
    monkeypatch.setattr(
        profile,
        "read_database_identity",
        lambda _path: pytest.fail("recovery classification must not open live database"),
    )
    result = _inspect(
        tmp_path / "genshin.db",
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        journal_state="swap_started",
    )
    assert result["state"] == "recovery_available"


def test_installed_receipt_separates_package_bytes_from_mutable_manual_layer(
    monkeypatch, tmp_path
):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"installed")
    package_identity = _identity()
    manifest = _manifest(ident=package_identity)
    installed_identity = copy.deepcopy(package_identity)
    installed_identity.update(
        sha256="e" * 64,
        meta_fingerprint="f" * 64,
        manual_content_fingerprint="1" * 64,
    )
    receipt = profile.create_database_live_receipt(
        manifest,
        installed_identity,
        installed_at="2026-10-26T10:30:00+08:00",
    )
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: installed_identity)
    result = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=manifest,
        live_receipt=receipt,
    )
    assert result["state"] == "current_pair_ready"
    assert result["current_release_id"] == manifest["database_release_id"]

    without_receipt = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=manifest,
    )
    assert without_receipt["state"] == "corrupt"


def test_receipt_fails_closed_on_core_change_and_unknown_ready_db(monkeypatch, tmp_path):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"installed")
    identity = _identity()
    manifest = _manifest(ident=identity)
    receipt = profile.create_database_live_receipt(
        manifest,
        identity,
        installed_at="2026-10-26T10:30:00+08:00",
    )
    changed = copy.deepcopy(identity)
    changed["release_core"] = _core("changed")
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: changed)
    corrupt = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=manifest,
        live_receipt=receipt,
    )
    assert corrupt["state"] == "corrupt"

    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)
    unknown = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
    )
    assert unknown["state"] == "incompatible_legacy"
    assert unknown["reasons"] == ["database_release_identity_unmanifested"]


@pytest.mark.parametrize("state", ["operational", "cleanup_complete", "rolled_back"])
def test_successful_or_rolled_back_journal_states_are_not_recovery(
    monkeypatch, tmp_path, state
):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"live")
    identity = _identity()
    manifest = _manifest(ident=identity)
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)
    result = _inspect(
        live,
        app_version="0.45.0",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=manifest,
        journal_state=state,
    )
    assert result["state"] == "current_pair_ready"


def test_manifest_json_has_no_machine_path(tmp_path):
    value = _manifest()
    target = profile.write_database_release_manifest(tmp_path / profile.MANIFEST_FILENAME, value)
    body = json.loads(target.read_text(encoding="utf-8"))
    assert set(body["database"]) == {
        "sha256", "size_bytes", "meta_fingerprint", "upstream_commit",
        "game_version", "upstream_track", "upstream_updated_at",
        "upstream_version_evidence", "version_lines", "indexer_version",
        "normalization_version", "datasets",
        "release_core",
    }


def test_release_pair_binds_one_exact_database_without_reverse_manifest(tmp_path):
    database = _manifest()
    pair = profile.create_release_pair_manifest(
        database,
        release_pair_id="pair-1",
        pair_sequence=1,
        pair_policy="exact",
        app_release_id="app-0.45.0",
        app_version="0.45.0",
        app_artifact_manifest_sha256="f" * 64,
    )
    assert pair["app"]["required_database_release_id"] == "db-7.1.51-r1"
    assert profile.release_pair_matches_database_manifest(pair, database)
    crossed_channel = copy.deepcopy(pair)
    crossed_channel["channel"] = "dev"
    assert not profile.release_pair_matches_database_manifest(
        crossed_channel,
        database,
    )
    target = profile.write_release_pair_manifest(
        tmp_path / profile.PAIR_MANIFEST_FILENAME, pair
    )
    assert profile.load_release_pair_manifest(target) == pair

    artifact_tampered = copy.deepcopy(pair)
    artifact_tampered["app"]["artifact_manifest"]["sha256"] = "0" * 64
    with pytest.raises(profile.DatabaseReleaseError) as artifact_failure:
        profile.validate_release_pair_manifest(artifact_tampered)
    assert artifact_failure.value.reason == "release_pair_app_contract_mismatch"

    reused = profile.create_release_pair_manifest(
        database,
        release_pair_id="pair-2",
        pair_sequence=2,
        pair_policy="exact",
        app_release_id="app-0.46.0",
        app_version="0.46.0",
        app_artifact_manifest_sha256="e" * 64,
    )
    assert reused["database"] == pair["database"]
    assert reused["app"]["app_release_id"] == "app-0.46.0"
    assert profile.release_pair_matches_database_manifest(reused, database)


def test_current_only_blocks_stale_database_instead_of_loading_old_adapter(
    monkeypatch, tmp_path
):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"live")
    current_identity = _identity()
    latest_identity = _identity(
        sha="8" * 64, meta="9" * 64, core_marker="latest"
    )
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: current_identity)
    result = profile.inspect_database_profile(
        live,
        app_release_id="app-0.45.0",
        pair_policy="current_only",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=_manifest(ident=current_identity),
        bundled_manifest=_manifest(
            ident=latest_identity,
            release_id="db-7.1.51-r2",
            sequence=2,
            pair_id="pair-2",
        ),
    )
    assert result["state"] == "current_dev_stale"
    assert result["can_continue_current"] is False
    assert len(result["current_combination_fingerprint"]) == 64


def test_current_only_does_not_treat_pair_neutral_wrapper_as_database_change(
    monkeypatch, tmp_path
):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"live")
    identity = _identity()
    current = _legacy_manifest(ident=identity)
    available = _manifest(ident=identity)
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: identity)

    result = profile.inspect_database_profile(
        live,
        app_release_id="app-0.46.0",
        pair_policy="current_only",
        logic_profile="logic-v1",
        data_contract_version=1,
        live_manifest=current,
        bundled_manifest=available,
    )

    assert result["state"] == "current_dev_ready"
    assert result["can_continue_current"] is True


def test_current_only_rejects_knowledge_layer_drift_without_a_live_receipt(
    monkeypatch, tmp_path
):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"locally annotated")
    packaged = _identity()
    edited = copy.deepcopy(packaged)
    edited.update(
        sha256="8" * 64,
        meta_fingerprint="9" * 64,
        mutable_layer_fingerprint="7" * 64,
    )
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: edited)

    result = profile.inspect_database_profile(
        live,
        app_release_id="app-0.45.0",
        release_pair_id="pair-1",
        required_database_release_id="db-7.1.51-r1",
        pair_policy="current_only",
        logic_profile="logic-v1",
        data_contract_version=1,
        bundled_manifest=_manifest(ident=packaged),
    )

    assert result["state"] == "incompatible_legacy"
    assert result["can_continue_current"] is False
    assert result["reasons"] == ["database_release_identity_unmanifested"]


def test_current_only_rejects_immutable_core_drift_without_a_live_receipt(
    monkeypatch, tmp_path
):
    live = tmp_path / "genshin.db"
    live.write_bytes(b"tampered immutable content")
    packaged = _identity()
    tampered = _identity(
        sha="8" * 64,
        meta="9" * 64,
        core_marker="tampered",
    )
    monkeypatch.setattr(profile, "read_database_identity", lambda _path: tampered)

    result = profile.inspect_database_profile(
        live,
        app_release_id="app-0.45.0",
        release_pair_id="pair-1",
        required_database_release_id="db-7.1.51-r1",
        pair_policy="current_only",
        logic_profile="logic-v1",
        data_contract_version=1,
        bundled_manifest=_manifest(ident=packaged),
    )

    assert result["state"] == "incompatible_legacy"
    assert result["can_continue_current"] is False
    assert result["reasons"] == ["database_release_identity_unmanifested"]
