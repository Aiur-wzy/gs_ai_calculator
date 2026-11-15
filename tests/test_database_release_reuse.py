from __future__ import annotations

import hashlib
import json

import pytest

from genshin_app import bundled_database_activation as activation
from genshin_app import database_release_reuse as reuse


def test_verified_reusable_database_accepts_only_valid_semantic_match(
    tmp_path,
    monkeypatch,
):
    data = tmp_path / "data"
    data.mkdir()
    (data / "genshin.db").write_bytes(b"live")
    (data / reuse.LIVE_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    (data / reuse.LIVE_RECEIPT_FILENAME).write_text("{}", encoding="utf-8")
    current = {"database_release_id": "db-same"}
    target = {"database_release_id": "db-same", "artifact": "repacked"}
    identity = {"sha256": "a" * 64}
    monkeypatch.setattr(reuse, "load_database_release_manifest", lambda _path: current)
    monkeypatch.setattr(reuse, "validate_database_live_receipt", lambda _value: {})
    monkeypatch.setattr(reuse, "read_database_identity", lambda _path: identity)
    monkeypatch.setattr(reuse, "database_live_receipt_matches", lambda *_args: True)
    monkeypatch.setattr(
        reuse,
        "database_release_semantically_equal",
        lambda left, right: left is current and right is target,
    )

    assert reuse.verified_reusable_database(data, target) == identity

    monkeypatch.setattr(reuse, "database_live_receipt_matches", lambda *_args: False)
    with pytest.raises(reuse.DatabaseReleaseReuseError) as failure:
        reuse.verified_reusable_database(data, target)
    assert failure.value.reason == "database_reuse_live_identity_mismatch"


def test_rebind_database_sidecars_never_modifies_sqlite(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    database = data / "genshin.db"
    database.write_bytes(b"unchanged database")
    old_manifest = data / reuse.LIVE_MANIFEST_FILENAME
    old_receipt = data / reuse.LIVE_RECEIPT_FILENAME
    old_manifest.write_text('{"old":"manifest"}', encoding="utf-8")
    old_receipt.write_text('{"old":"receipt"}', encoding="utf-8")
    target = {"database_release_id": "db-same"}
    receipt = {"database_release_id": "db-same", "receipt": "new"}
    monkeypatch.setattr(
        reuse,
        "validate_database_release_manifest",
        lambda value: value,
    )
    monkeypatch.setattr(
        reuse,
        "create_database_live_receipt",
        lambda *_args, **_kwargs: receipt,
    )

    reuse.rebind_database_sidecars(data, target, {"sha256": hashlib.sha256(database.read_bytes()).hexdigest()})

    assert database.read_bytes() == b"unchanged database"
    assert json.loads(old_manifest.read_text(encoding="utf-8")) == target
    assert json.loads(old_receipt.read_text(encoding="utf-8")) == receipt


def test_bundled_activation_reuses_database_and_commits_pair_last(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(activation, "packaged_peer_processes", list)
    data = tmp_path / "data"
    data.mkdir()
    package = tmp_path / "bundle" / "genshin.db"
    package.parent.mkdir()
    package.write_bytes(b"package")
    manifest = {
        "database_release_id": "db-same",
        "release_sequence": 2,
    }
    pair = {"release_pair_id": "pair-new", "pair_sequence": 3}
    identity = {"sha256": "b" * 64}
    calls: list[str] = []
    monkeypatch.setattr(activation, "is_frozen", lambda: True)
    monkeypatch.setattr(
        activation,
        "_load_verified_bundled_release",
        lambda: (package, manifest, pair, "a" * 64),
    )
    monkeypatch.setattr(
        activation,
        "verified_reusable_database",
        lambda *_args: identity,
    )
    monkeypatch.setattr(
        activation,
        "rebind_database_sidecars",
        lambda *_args: calls.append("rebind"),
    )
    monkeypatch.setattr(
        activation,
        "install_database_release",
        lambda *_args, **_kwargs: pytest.fail("semantic reuse must not swap SQLite"),
    )
    monkeypatch.setattr(
        activation,
        "prepare_database_startup",
        lambda _directory: {"can_start": True, "state": "current_dev_ready"},
    )

    result = activation.activate_bundled_database(
        data,
        consent_source="user_repair",
    )

    assert calls == ["rebind"]
    assert result["migration_status"] == "database_reused"
    assert result["rollback_retained"] is False
    assert json.loads(
        (data / activation.LIVE_PAIR_MANIFEST_FILENAME).read_text(encoding="utf-8")
    ) == pair


def test_same_database_sequence_allows_semantic_repack_but_not_changed_content(
    tmp_path,
    monkeypatch,
):
    data = tmp_path / "data"
    data.mkdir()
    (data / activation.LIVE_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
    current = {"database_release_id": "db-same", "release_sequence": 2}
    target = {
        "database_release_id": "db-same",
        "release_sequence": 2,
        "artifact": "repacked",
    }
    pair = {"release_pair_id": "pair-new", "pair_sequence": 3}
    monkeypatch.setattr(
        activation,
        "load_database_release_manifest",
        lambda _path: current,
    )
    monkeypatch.setattr(
        activation,
        "database_release_semantically_equal",
        lambda left, right: left is current and right is target,
    )

    activation._refuse_downgrade_or_split(
        data,
        target_manifest=target,
        target_pair=pair,
    )

    monkeypatch.setattr(
        activation,
        "database_release_semantically_equal",
        lambda *_args: False,
    )
    with pytest.raises(activation.BundledDatabaseActivationError) as failure:
        activation._refuse_downgrade_or_split(
            data,
            target_manifest=target,
            target_pair=pair,
        )
    assert failure.value.reason == "bundled_database_release_sequence_conflict"
