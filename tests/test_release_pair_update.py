from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from genshin_app import release_pair_update as updater


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _app(root: Path, payload: bytes) -> str:
    root.mkdir(parents=True)
    member = root / "app.bin"
    member.write_bytes(payload)
    raw = f"{_sha256(member)}  app.bin\n".encode()
    (root / "PACKAGE-FILES.sha256").write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _database(path: Path, payload: bytes) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {"sha256": _sha256(path), "size_bytes": len(payload)}


def _detached_pair(root: Path, pair: dict) -> None:
    path = root / "_internal" / "bundled" / "release-pair-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pair), encoding="utf-8")


def _contracts(name: str, sequence: int, app_sha: str, database: dict):
    pair_id = f"pair-{name}"
    database_id = f"database-{name}"
    manifest = {
        "database_release_id": database_id,
        "release_sequence": sequence,
        "channel": "dev",
        "release_pair_id": pair_id,
        "app_release_id": f"app-{name}",
        "database": copy.deepcopy(database),
    }
    pair = {
        "release_pair_id": pair_id,
        "pair_sequence": sequence,
        "channel": "dev",
        "pair_policy": "current_only",
        "app": {"artifact_manifest": {"sha256": app_sha}},
        "database": {"database_release_id": database_id},
    }
    return pair, manifest


def _trust(pair: dict, manifest: dict) -> dict:
    return updater.create_local_review_trust_assertion(
        pair,
        manifest,
        assertion_id="review-1",
        verified_at="2026-10-26T00:00:00Z",
    )


def _receipt(manifest: dict, path: Path) -> dict:
    return {
        "database_release_id": manifest["database_release_id"],
        "release_pair_id": manifest["release_pair_id"],
        "database_sha256": _sha256(path),
    }


@pytest.fixture(autouse=True)
def _contract_stubs(monkeypatch):
    monkeypatch.setattr(updater, "validate_release_pair_manifest", copy.deepcopy)
    monkeypatch.setattr(updater, "validate_database_release_manifest", copy.deepcopy)
    monkeypatch.setattr(updater, "validate_database_live_receipt", copy.deepcopy)
    monkeypatch.setattr(
        updater,
        "release_pair_matches_database_manifest",
        lambda pair, manifest: (
            pair["release_pair_id"] == manifest["release_pair_id"]
            and pair["database"]["database_release_id"]
            == manifest["database_release_id"]
        ),
    )
    monkeypatch.setattr(
        updater,
        "read_database_identity",
        lambda path: {"sha256": _sha256(Path(path))},
    )
    monkeypatch.setattr(
        updater,
        "database_live_receipt_matches",
        lambda receipt, identity, manifest: (
            receipt["database_release_id"] == manifest["database_release_id"]
            and receipt["release_pair_id"] == manifest["release_pair_id"]
            and receipt["database_sha256"] == identity["sha256"]
        ),
    )
    monkeypatch.setattr(
        updater,
        "database_release_semantically_equal",
        lambda current, target: current["database"] == target["database"],
    )


def _fixture(tmp_path: Path) -> dict:
    workspace = tmp_path / "updater"
    staged_app = workspace / "staging" / "app"
    staged_database = workspace / "staging" / "genshin.db"
    pair, manifest = _contracts(
        "new", 2, _app(staged_app, b"new-app"), _database(staged_database, b"new-db")
    )
    _detached_pair(staged_app, pair)
    active_app = tmp_path / "active-app"
    active_database = tmp_path / "data" / "genshin.db"
    old_pair, old_manifest = _contracts(
        "old", 1, _app(active_app, b"old-app"), _database(active_database, b"old-db")
    )
    _detached_pair(active_app, old_pair)
    current = {
        "app_root": active_app,
        "database": active_database,
        "pair_manifest": old_pair,
        "database_manifest": old_manifest,
        "live_receipt": _receipt(old_manifest, active_database),
    }
    return locals()


def _prepare(item: dict, **changes):
    options = {
        "user_confirmed": True,
        "production": False,
        "trust_assertion": _trust(item["pair"], item["manifest"]),
        "current": item["current"],
    }
    options.update(changes)
    return updater.prepare_release_pair_handoff(
        item["workspace"],
        item["staged_app"],
        item["staged_database"],
        item["manifest"],
        item["pair"],
        **options,
    )


def test_trust_and_production_fail_closed_before_control_files(tmp_path):
    item = _fixture(tmp_path)
    with pytest.raises(updater.ReleasePairUpdateError) as missing:
        _prepare(item, trust_assertion=None)
    assert missing.value.reason == "release_pair_trust_assertion_required"

    wrong = _trust(item["pair"], item["manifest"])
    wrong["pair_manifest_sha256"] = "0" * 64
    with pytest.raises(updater.ReleasePairUpdateError) as mismatch:
        _prepare(item, trust_assertion=wrong)
    assert mismatch.value.reason == "release_pair_trust_assertion_mismatch"

    stable_pair = copy.deepcopy(item["pair"])
    stable_manifest = copy.deepcopy(item["manifest"])
    stable_pair.update(channel="stable", pair_policy="exact")
    stable_manifest["channel"] = "stable"
    _detached_pair(item["staged_app"], stable_pair)
    forged_production_trust = _trust(item["pair"], item["manifest"])
    forged_production_trust["release_pair_id"] = stable_pair["release_pair_id"]
    forged_production_trust["pair_manifest_sha256"] = updater._fingerprint(
        stable_pair
    )
    forged_production_trust["database_manifest_sha256"] = updater._fingerprint(
        stable_manifest
    )
    with pytest.raises(updater.ReleasePairUpdateError) as production:
        updater.prepare_release_pair_handoff(
            item["workspace"],
            item["staged_app"],
            item["staged_database"],
            stable_manifest,
            stable_pair,
            user_confirmed=True,
            production=True,
            trust_assertion=forged_production_trust,
        )
    assert production.value.reason == "release_pair_production_trust_unavailable"
    assert not (item["workspace"] / updater.PAIR_UPDATE_JOURNAL_FILENAME).exists()
    assert not (item["workspace"] / updater.PAIR_UPDATE_HANDOFF_FILENAME).exists()


def test_signed_discovery_assertion_is_the_only_production_trust_path(
    tmp_path, monkeypatch
):
    item = _fixture(tmp_path)
    stable_pair = copy.deepcopy(item["pair"])
    stable_manifest = copy.deepcopy(item["manifest"])
    stable_pair.update(channel="stable", pair_policy="exact")
    stable_manifest["channel"] = "stable"
    _detached_pair(item["staged_app"], stable_pair)
    signed_document = {
        "pair_manifest": stable_pair,
        "database_manifest": stable_manifest,
        "signature": {
            "algorithm": "rsa-pss-sha256",
            "key_id": "release-2026",
            "value": "fixture",
        },
    }
    document_sha256 = updater._fingerprint(signed_document)
    discovery = {
        "authoritative": True,
        "pair_manifest": stable_pair,
        "database_manifest": stable_manifest,
        "document_sha256": document_sha256,
        "signed_document": signed_document,
        "trust": {
            "source": "signed_discovery",
            "algorithm": "rsa-pss-sha256",
            "key_id": "release-2026",
        },
    }
    monkeypatch.setattr(updater, "build_trust_verifier", lambda _keys: object())
    monkeypatch.setattr(
        "genshin_app.release_pair_discovery.validate_latest_document",
        lambda document, **_kwargs: (copy.deepcopy(document), True),
    )
    assertion = updater.create_signed_discovery_trust_assertion(
        discovery,
        stable_pair,
        stable_manifest,
        production=True,
        verified_at="2026-10-28T00:00:00Z",
    )
    prepared = updater.prepare_release_pair_handoff(
        item["workspace"],
        item["staged_app"],
        item["staged_database"],
        stable_manifest,
        stable_pair,
        user_confirmed=True,
        production=True,
        trust_assertion=assertion,
    )
    assert prepared["status"] == "handoff_ready"

    crossed = copy.deepcopy(assertion)
    crossed["document_sha256"] = "8" * 64
    crossed["release_pair_id"] = "other"
    with pytest.raises(updater.ReleasePairUpdateError) as mismatch:
        updater._validated_trust_assertion(
            crossed,
            stable_pair,
            stable_manifest,
            production=True,
        )
    assert mismatch.value.reason == "release_pair_trust_assertion_mismatch"


def test_prepare_rechecks_candidate_and_requires_exact_rollback(tmp_path):
    item = _fixture(tmp_path)
    before = _sha256(item["active_database"])
    prepared = _prepare(item)
    assert prepared["status"] == "handoff_ready"
    assert prepared["pair_operational"] is False
    assert _sha256(item["active_database"]) == before
    handoff_text = (
        item["workspace"] / updater.PAIR_UPDATE_HANDOFF_FILENAME
    ).read_text(encoding="utf-8")
    assert str(item["workspace"]) not in handoff_text

    rollback = item["workspace"] / "rollback"
    shutil.copytree(item["active_app"], rollback / "app")
    shutil.copy2(item["active_database"], rollback / "genshin.db")
    item["staged_database"].write_bytes(b"tampered")
    with pytest.raises(updater.ReleasePairUpdateError) as tampered:
        updater.begin_pair_update_activation(
            item["workspace"], operation_id=prepared["operation_id"]
        )
    assert tampered.value.reason == "release_pair_database_artifact_mismatch"
    item["staged_database"].write_bytes(b"new-db")

    (rollback / "genshin.db").write_bytes(b"wrong")
    with pytest.raises(updater.ReleasePairUpdateError) as wrong:
        updater.begin_pair_update_activation(
            item["workspace"], operation_id=prepared["operation_id"]
        )
    assert wrong.value.reason == "release_pair_update_rollback_identity_mismatch"
    shutil.copy2(item["active_database"], rollback / "genshin.db")
    started = updater.begin_pair_update_activation(
        item["workspace"], operation_id=prepared["operation_id"]
    )
    assert started["status"] == "activation_started"
    assert updater.begin_pair_update_activation(
        item["workspace"], operation_id=prepared["operation_id"]
    ) == started


def test_prepare_app_only_handoff_binds_and_rechecks_installed_database(tmp_path):
    item = _fixture(tmp_path)
    item["manifest"]["database"] = copy.deepcopy(
        item["old_manifest"]["database"]
    )
    prepared = updater.prepare_release_pair_handoff(
        item["workspace"],
        item["staged_app"],
        None,
        item["manifest"],
        item["pair"],
        user_confirmed=True,
        production=False,
        trust_assertion=_trust(item["pair"], item["manifest"]),
        current=item["current"],
        reuse_installed_database=True,
    )
    inspected = updater.inspect_pair_update_handoff(
        item["workspace"], operation_id=prepared["operation_id"]
    )
    assert inspected["database_mode"] == "reuse_installed"
    assert inspected["staged_database"] is None

    item["active_database"].write_bytes(b"changed-after-consent")
    with pytest.raises(updater.ReleasePairUpdateError) as failure:
        updater.begin_pair_update_activation(
            item["workspace"],
            operation_id=prepared["operation_id"],
            active_app_root=item["active_app"],
            active_database=item["active_database"],
        )
    assert failure.value.reason in {
        "release_pair_current_database_identity_mismatch",
        "release_pair_update_rollback_identity_mismatch",
    }


def test_product_activation_revalidates_active_pair_without_workspace_copy(tmp_path):
    item = _fixture(tmp_path)
    prepared = _prepare(item)
    rollback = item["workspace"] / "rollback"
    assert not (rollback / "app").exists()
    assert not (rollback / "genshin.db").exists()

    started = updater.begin_pair_update_activation(
        item["workspace"],
        operation_id=prepared["operation_id"],
        active_app_root=item["active_app"],
        active_database=item["active_database"],
    )
    assert started["status"] == "activation_started"


def test_prepare_rejects_downgrade_and_same_sequence_split_brain(tmp_path):
    downgrade = _fixture(tmp_path / "downgrade")
    downgrade["pair"]["pair_sequence"] = 1
    downgrade["current"]["pair_manifest"]["pair_sequence"] = 2
    _detached_pair(downgrade["staged_app"], downgrade["pair"])
    _detached_pair(
        downgrade["current"]["app_root"],
        downgrade["current"]["pair_manifest"],
    )
    with pytest.raises(updater.ReleasePairUpdateError) as lower:
        _prepare(downgrade)
    assert lower.value.reason == "release_pair_update_rollback_forbidden"

    split = _fixture(tmp_path / "split")
    split["pair"]["pair_sequence"] = 1
    _detached_pair(split["staged_app"], split["pair"])
    with pytest.raises(updater.ReleasePairUpdateError) as crossed:
        _prepare(split)
    assert crossed.value.reason == "release_pair_update_split_brain"


def test_postcheck_commit_cleanup_and_rollback_boundaries(tmp_path):
    item = _fixture(tmp_path)
    prepared = _prepare(item)
    rollback = item["workspace"] / "rollback"
    shutil.copytree(item["active_app"], rollback / "app")
    shutil.copy2(item["active_database"], rollback / "genshin.db")
    updater.begin_pair_update_activation(
        item["workspace"], operation_id=prepared["operation_id"]
    )

    shutil.rmtree(item["active_app"])
    shutil.copytree(item["staged_app"], item["active_app"])
    shutil.copy2(item["staged_database"], item["active_database"])
    data_dir = item["active_database"].parent
    (data_dir / updater.LIVE_DATABASE_MANIFEST_FILENAME).write_text(
        json.dumps(item["manifest"]), encoding="utf-8"
    )
    (data_dir / updater.LIVE_DATABASE_RECEIPT_FILENAME).write_text(
        json.dumps(_receipt(item["manifest"], item["active_database"])),
        encoding="utf-8",
    )
    result = updater.postcheck_pair_update(
        item["workspace"],
        item["active_app"],
        data_dir,
        operation_id=prepared["operation_id"],
    )
    assert result["status"] == "operational"
    assert json.loads(
        (data_dir / updater.LIVE_PAIR_MANIFEST_FILENAME).read_text(encoding="utf-8")
    ) == item["pair"]
    cleanup = updater.complete_pair_update_cleanup(
        item["workspace"], operation_id=prepared["operation_id"]
    )
    assert cleanup["pair_operational"] is True
    assert not (item["workspace"] / "staging").exists()
    assert not (item["workspace"] / "rollback").exists()
    assert cleanup["diagnostic_artifacts_retained"] == [
        f"diagnostics/{prepared['operation_id']}-rollback"
    ]
    assert (
        item["workspace"]
        / "diagnostics"
        / f"{prepared['operation_id']}-rollback"
    ).is_dir()


def test_rollback_and_first_install_are_explicit_paths(tmp_path):
    item = _fixture(tmp_path)
    prepared = _prepare(item)
    rollback = item["workspace"] / "rollback"
    shutil.copytree(item["active_app"], rollback / "app")
    shutil.copy2(item["active_database"], rollback / "genshin.db")
    updater.begin_pair_update_activation(
        item["workspace"], operation_id=prepared["operation_id"]
    )
    data_dir = item["active_database"].parent
    (data_dir / updater.LIVE_DATABASE_MANIFEST_FILENAME).write_text(
        json.dumps(item["old_manifest"]), encoding="utf-8"
    )
    (data_dir / updater.LIVE_DATABASE_RECEIPT_FILENAME).write_text(
        json.dumps(item["current"]["live_receipt"]), encoding="utf-8"
    )
    rolled_back = updater.confirm_pair_update_rollback(
        item["workspace"],
        item["active_app"],
        data_dir,
        operation_id=prepared["operation_id"],
    )
    assert rolled_back["status"] == "rolled_back"
    assert rolled_back["pair_operational"] is True
    with pytest.raises(updater.ReleasePairUpdateError) as pending_cleanup:
        _prepare(item)
    assert pending_cleanup.value.reason == "release_pair_update_recovery_required"
    cleanup = updater.complete_pair_update_cleanup(
        item["workspace"], operation_id=prepared["operation_id"]
    )
    assert cleanup["status"] == "cleanup_complete"
    assert cleanup["diagnostic_artifacts_retained"] == [
        f"diagnostics/{prepared['operation_id']}-rollback"
    ]

    fresh = _fixture(tmp_path / "fresh")
    first = _prepare(fresh, current=None)
    started = updater.begin_pair_update_activation(
        fresh["workspace"], operation_id=first["operation_id"]
    )
    assert started["status"] == "activation_started"
