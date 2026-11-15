from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from genshin_app import release_pair_feed as feed


def _fixture(tmp_path: Path, monkeypatch):
    app = tmp_path / "app"
    (app / "_internal" / "bundled").mkdir(parents=True)
    (app / "GenshinCalcGUI.exe").write_bytes(b"app-binary")
    (app / "PACKAGE-FILES.sha256").write_text("fixture\n", encoding="utf-8")
    (app / "_internal" / "bundled" / "release-pair-manifest.json").write_text(
        "{}", encoding="utf-8"
    )
    (app / "_internal" / "bundled" / "database-release-manifest.json").write_text(
        "{}", encoding="utf-8"
    )
    database = tmp_path / "genshin.db"
    database.write_bytes(b"complete sqlite fixture")
    database_sha = hashlib.sha256(database.read_bytes()).hexdigest()
    pair = {
        "release_pair_id": "pair-0.46.0-db-7.2-r1",
        "channel": "dev",
        "app": {"artifact_manifest": {"sha256": "a" * 64}},
    }
    manifest = {
        "database_release_id": "db-7.2-r1",
        "database": {
            "sha256": database_sha,
            "size_bytes": database.stat().st_size,
            "meta_fingerprint": "b" * 64,
        },
    }
    verification = {"manifest_sha256": "a" * 64, "files": 4, "bytes": 10}
    monkeypatch.setattr(
        feed,
        "_validate_inputs",
        lambda *_args: (app, database, pair, manifest, verification),
    )
    monkeypatch.setattr(feed, "verify_package_artifact", lambda _root: verification)
    monkeypatch.setattr(
        feed,
        "validate_latest_document",
        lambda document, **_kwargs: (document, False),
    )
    return app, database, pair, manifest


def test_prepare_writes_deterministic_artifacts_and_exact_payload(tmp_path, monkeypatch):
    _app, _database, pair, manifest = _fixture(tmp_path, monkeypatch)
    first = tmp_path / "feed-one"
    second = tmp_path / "feed-two"
    receipt = feed.prepare_release_pair_feed(
        app_root=tmp_path / "ignored-app",
        database=tmp_path / "ignored-db",
        pair_manifest_path=tmp_path / "pair.json",
        database_manifest_path=tmp_path / "database.json",
        base_url="https://updates.example/releases/0.46.0",
        output_directory=first,
    )
    repeated = feed.prepare_release_pair_feed(
        app_root=tmp_path / "ignored-app",
        database=tmp_path / "ignored-db",
        pair_manifest_path=tmp_path / "pair.json",
        database_manifest_path=tmp_path / "database.json",
        base_url="https://updates.example/releases/0.46.0",
        output_directory=second,
    )
    app_name = f"{pair['release_pair_id']}.app.zip"
    database_name = f"{manifest['database_release_id']}.sqlite"
    assert hashlib.sha256((first / app_name).read_bytes()).hexdigest() == hashlib.sha256(
        (second / app_name).read_bytes()
    ).hexdigest()
    assert (first / database_name).read_bytes() == b"complete sqlite fixture"
    unsigned = json.loads((first / feed.UNSIGNED_DOCUMENT_FILENAME).read_text("utf-8"))
    assert unsigned["signature"] is None
    assert unsigned["artifacts"]["app"]["url"].endswith(app_name)
    assert unsigned["artifacts"]["app"]["unpacked_size_bytes"] > 0
    assert unsigned["artifacts"]["app"]["unpacked_files"] == 4
    assert unsigned["artifacts"]["database"]["sha256"] == manifest["database"]["sha256"]
    payload = {key: value for key, value in unsigned.items() if key != "signature"}
    assert (first / feed.SIGNING_PAYLOAD_FILENAME).read_bytes() == feed._canonical(payload)
    assert receipt["signing_payload_sha256"] == repeated["signing_payload_sha256"]
    with zipfile.ZipFile(first / app_name) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert "GenshinCalcGUI.exe" in archive.namelist()
        assert all(info.date_time == feed.ZIP_TIMESTAMP for info in archive.infolist())


def test_prepare_rejects_insecure_url_and_existing_output(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    with pytest.raises(feed.ReleasePairFeedError) as insecure:
        feed.prepare_release_pair_feed(
            app_root=tmp_path / "app",
            database=tmp_path / "genshin.db",
            pair_manifest_path=tmp_path / "pair.json",
            database_manifest_path=tmp_path / "database.json",
            base_url="http://updates.example/releases",
            output_directory=tmp_path / "feed",
        )
    assert insecure.value.reason == "release_pair_feed_base_url_invalid"
    (tmp_path / "exists").mkdir()
    with pytest.raises(feed.ReleasePairFeedError) as existing:
        feed.prepare_release_pair_feed(
            app_root=tmp_path / "app",
            database=tmp_path / "genshin.db",
            pair_manifest_path=tmp_path / "pair.json",
            database_manifest_path=tmp_path / "database.json",
            base_url="https://updates.example/releases",
            output_directory=tmp_path / "exists",
        )
    assert existing.value.reason == "release_pair_feed_output_exists"


def test_finalize_requires_compiled_key_verification(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    directory = tmp_path / "feed"
    feed.prepare_release_pair_feed(
        app_root=tmp_path / "app",
        database=tmp_path / "genshin.db",
        pair_manifest_path=tmp_path / "pair.json",
        database_manifest_path=tmp_path / "database.json",
        base_url="https://updates.example/releases",
        output_directory=directory,
    )
    signature = tmp_path / "signature.json"
    signature.write_text(
        json.dumps(
            {
                "algorithm": "rsa-pss-sha256",
                "key_id": "release-key",
                "value": "signed-value",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(feed, "build_trust_verifier", lambda _keys: lambda *_args: True)

    def validate(document, *, trust_verifier, **_kwargs):
        payload = {key: value for key, value in document.items() if key != "signature"}
        return document, trust_verifier(payload, document["signature"])

    monkeypatch.setattr(feed, "validate_latest_document", validate)
    result = feed.finalize_release_pair_feed(
        feed_directory=directory,
        signature_path=signature,
        trust_keys=({"fixture": True},),
    )
    assert result["status"] == "finalized"
    latest = json.loads((directory / feed.FINAL_DOCUMENT_FILENAME).read_text("utf-8"))
    assert latest["signature"]["key_id"] == "release-key"
    with pytest.raises(feed.ReleasePairFeedError) as duplicate:
        feed.finalize_release_pair_feed(
            feed_directory=directory,
            signature_path=signature,
            trust_keys=({"fixture": True},),
        )
    assert duplicate.value.reason == "release_pair_feed_final_exists"


def test_finalize_refuses_unconfigured_or_untrusted_signature(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    directory = tmp_path / "feed"
    feed.prepare_release_pair_feed(
        app_root=tmp_path / "app",
        database=tmp_path / "genshin.db",
        pair_manifest_path=tmp_path / "pair.json",
        database_manifest_path=tmp_path / "database.json",
        base_url="https://updates.example/releases",
        output_directory=directory,
    )
    signature = tmp_path / "signature.json"
    signature.write_text(
        '{"algorithm":"rsa-pss-sha256","key_id":"k","value":"bad"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(feed, "build_trust_verifier", lambda _keys: None)
    with pytest.raises(feed.ReleasePairFeedError) as missing:
        feed.finalize_release_pair_feed(
            feed_directory=directory,
            signature_path=signature,
            trust_keys=(),
        )
    assert missing.value.reason == "release_pair_feed_trust_keys_unconfigured"


def test_finalize_rehashes_every_prepared_artifact_before_publishing(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    directory = tmp_path / "feed"
    receipt = feed.prepare_release_pair_feed(
        app_root=tmp_path / "app",
        database=tmp_path / "genshin.db",
        pair_manifest_path=tmp_path / "pair.json",
        database_manifest_path=tmp_path / "database.json",
        base_url="https://updates.example/releases",
        output_directory=directory,
    )
    artifact = next(
        directory / name for name in receipt["files"] if name.endswith(".sqlite")
    )
    artifact.write_bytes(b"tampered after prepare")
    signature = tmp_path / "signature.json"
    signature.write_text(
        '{"algorithm":"rsa-pss-sha256","key_id":"k","value":"unused"}',
        encoding="utf-8",
    )
    with pytest.raises(feed.ReleasePairFeedError) as tampered:
        feed.finalize_release_pair_feed(
            feed_directory=directory,
            signature_path=signature,
            trust_keys=(),
        )
    assert tampered.value.reason == "release_pair_feed_file_identity_mismatch"
