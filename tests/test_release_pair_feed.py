from __future__ import annotations

import copy
import gzip
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from genshin_app import release_pair_feed as feed
from genshin_app.product_release_index import validate_product_release_index
from tests import test_product_release_index as product_fixtures
from tests.test_product_release_index import _signed, _verify

releases = product_fixtures.releases


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


def test_finalize_rejects_artifact_even_if_unsigned_receipt_matches_it(tmp_path, monkeypatch):
    _fixture(tmp_path, monkeypatch)
    directory = tmp_path / "feed"
    receipt = feed.prepare_release_pair_feed(
        app_root=tmp_path / "app", database=tmp_path / "genshin.db", pair_manifest_path=tmp_path / "pair.json",
        database_manifest_path=tmp_path / "database.json", base_url="https://updates.example/releases",
        output_directory=directory,
    )
    name = next(name for name in receipt["files"] if name.endswith(".sqlite"))
    (directory / name).write_bytes(b"changed staged bytes")
    receipt["files"][name] = {"sha256": feed._sha256(directory / name), "size_bytes": (directory / name).stat().st_size}
    receipt.pop("receipt_sha256")
    receipt["receipt_sha256"] = hashlib.sha256(feed._canonical(receipt)).hexdigest()
    (directory / feed.FEED_RECEIPT_FILENAME).write_text(json.dumps(receipt), encoding="utf-8")
    signature = tmp_path / "signature.json"
    signature.write_text('{"algorithm":"rsa-pss-sha256","key_id":"k","value":"fixture"}', encoding="utf-8")
    monkeypatch.setattr(feed, "build_trust_verifier", lambda _keys: lambda *_args: True)
    monkeypatch.setattr(feed, "validate_latest_document", lambda document, **_kwargs: (document, True))

    with pytest.raises(feed.ReleasePairFeedError, match="signed_artifact_mismatch"):
        feed.finalize_release_pair_feed(feed_directory=directory, signature_path=signature, trust_keys=())
    assert not (directory / feed.FINAL_DOCUMENT_FILENAME).exists()


@pytest.fixture
def accepted_database_archive(tmp_path, monkeypatch, releases):
    apps, databases, documents, _, index = releases
    database, _ = databases[1]
    archive = tmp_path / "original-database.sqlite.gz"
    archive.write_bytes(gzip.compress(database.read_bytes(), compresslevel=1, mtime=42))
    previous = copy.deepcopy(documents[1, 1])
    old_descriptor = previous["artifacts"]["database"]
    old_descriptor.update(sha256=feed._sha256(archive), size_bytes=archive.stat().st_size)
    proof_path = tmp_path / "accepted-source-pair.json"

    def write_proof(proof, *, sign=True):
        if sign:
            proof = _signed({key: value for key, value in proof.items() if key != "signature"})
        proof_path.write_text(json.dumps(proof), encoding="utf-8")
        return proof

    previous = write_proof(previous)
    monkeypatch.setattr(feed, "build_trust_verifier", lambda keys: _verify if keys else None)
    parameters = {
        "app_root": apps[2][0],
        "database": database,
        "pair_manifest_path": tmp_path / "pair-2-1.json",
        "database_manifest_path": tmp_path / "database-1.json",
        "base_url": "https://example.invalid/app-2",
        "output_directory": tmp_path / "new-feed",
        "independent_app": True,
        "accepted_database_source_pair": proof_path,
        "accepted_database_archive": archive,
        "trust_keys": ({"fixture": True},),
    }
    return parameters, previous, write_proof, index


def test_successor_index_retains_exact_signed_database_asset(accepted_database_archive):
    parameters, previous, _, index = accepted_database_archive
    receipt = feed.prepare_release_pair_feed(**parameters)
    output = parameters["output_directory"]
    old_archive = parameters["accepted_database_archive"]
    db_manifest = previous["database_manifest"]
    output_archive = output / f'{db_manifest["database_release_id"]}.sqlite.gz'
    assert output_archive.read_bytes() == old_archive.read_bytes()
    assert receipt["artifacts"]["database"] == previous["artifacts"]["database"]
    unsigned = json.loads((output / feed.UNSIGNED_DOCUMENT_FILENAME).read_bytes())
    signature = _signed({k: v for k, v in unsigned.items() if k != "signature"})["signature"]
    signature_path = output.parent / "new-signature.json"
    signature_path.write_text(json.dumps(signature), encoding="utf-8")
    assert feed.finalize_release_pair_feed(
        feed_directory=output, signature_path=signature_path, trust_keys=parameters["trust_keys"],
    )["status"] == "finalized"
    successor = json.loads((output / feed.FINAL_DOCUMENT_FILENAME).read_bytes())
    assert successor["database_manifest"] == db_manifest
    assert successor["artifacts"]["database"] == previous["artifacts"]["database"]
    assert successor["artifacts"]["app"] != previous["artifacts"]["app"]
    index = _signed({**index, "pairs": [previous, successor], "latest_database_release_id": db_manifest["database_release_id"]})
    checked = validate_product_release_index(index, channel="stable", trust_verifier=_verify)
    assert checked["latest"] == ("app-product-2", db_manifest["database_release_id"])


@pytest.mark.parametrize("fault, error", [
    ("missing_proof", "database_reuse_inputs_required"),
    ("unsigned", "database_reuse_proof_untrusted"),
    ("tampered_proof", "database_reuse_proof_untrusted"),
    ("url_override", "database_reuse_url_mismatch"),
    ("tampered_archive", "database_reuse_archive_mismatch"),
    ("wrong_expanded_bytes", "database_reuse_expanded_mismatch"),
])
def test_reused_database_requires_signed_archive_identity(accepted_database_archive, fault, error):
    parameters, previous, write_proof, _ = accepted_database_archive
    archive = parameters["accepted_database_archive"]
    if fault == "missing_proof":
        del parameters["accepted_database_source_pair"]
    elif fault == "unsigned":
        write_proof({**previous, "signature": None}, sign=False)
    elif fault == "tampered_proof":
        previous["artifacts"]["database"]["url"] = "https://example.invalid/different.sqlite.gz"
        write_proof(previous, sign=False)
    elif fault == "url_override":
        parameters["database_base_url"] = "https://example.invalid/different"
    elif fault == "tampered_archive":
        original = archive.read_bytes()
        archive.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
    else:
        archive.write_bytes(gzip.compress(b"wrong database"))
        previous["artifacts"]["database"].update(sha256=feed._sha256(archive), size_bytes=archive.stat().st_size)
        write_proof(previous)
    with pytest.raises(feed.ReleasePairFeedError, match=error):
        feed.prepare_release_pair_feed(**parameters)
    assert not parameters["output_directory"].exists()
