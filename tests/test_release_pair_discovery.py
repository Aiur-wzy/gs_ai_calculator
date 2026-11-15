from __future__ import annotations

import copy
import hashlib
import json
import urllib.error

import pytest

from genshin_app import release_pair_discovery as discovery
from genshin_db import release_profile as profile
from genshin_db.release_integrity import DEFAULT_MUTABLE_TABLES
from genshin_db.upstream_version import evidence_fingerprint


def _core():
    value = {
        "contract": "genshin-database-release-core-v1",
        "mutable_tables": sorted(DEFAULT_MUTABLE_TABLES),
        "tables": {
            "character": {
                "schema_sha256": "a" * 64,
                "row_count": 1,
                "content_sha256": "b" * 64,
            }
        },
    }
    value["root_sha256"] = hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return value


def _database_manifest(*, channel="stable"):
    evidence = {
        "resolver_version": 1,
        "status": "resolved",
        "version_evidence_ready": True,
        "version": "7.1.53",
        "track": "OSCBWin",
        "raw_version": "OSCBWin7.1.53",
        "snapshot_commit": "e" * 40,
        "snapshot_updated_at": "2026-10-16T00:06:03-03:00",
        "evidence_kind": "snapshot_commit_subject",
        "evidence_commit": "e" * 40,
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
        "readme_version": "OSCBWin7.1.53",
        "issues": [],
    }
    evidence["evidence_fingerprint"] = evidence_fingerprint(evidence)
    database = {
        "sha256": "c" * 64,
        "size_bytes": 1234,
        "meta_fingerprint": "d" * 64,
        "upstream_commit": "e" * 40,
        "game_version": "7.1.53",
        "upstream_track": "OSCBWin",
        "upstream_updated_at": "2026-10-16T00:06:03-03:00",
        "upstream_version_evidence": evidence,
        "version_lines": ["release"],
        "indexer_version": "7",
        "normalization_version": "2",
        "datasets": {
            name: {"lines": {"release": {"state": "ready"}}}
            for name in profile.REQUIRED_RELEASE_COMPONENTS
        },
        "release_core": _core(),
    }
    return profile.validate_database_release_manifest(
        {
            "contract": profile.MANIFEST_CONTRACT,
            "schema_version": profile.MANIFEST_SCHEMA_VERSION,
            "database_release_id": "db-7.1.53-r1",
            "release_sequence": 2,
            "channel": channel,
            "published_at": "2026-10-16T00:06:03-03:00",
            "released_at": "2026-10-26T12:00:00+08:00",
            "logic_profile": "logic-v1",
            "data_contract_version": 1,
            "database": database,
            "download": None,
        }
    )


def _document(*, channel="stable", signature=True):
    database = _database_manifest(channel=channel)
    pair = profile.create_release_pair_manifest(
        database,
        release_pair_id="pair-2",
        pair_sequence=2,
        pair_policy="exact" if channel == "stable" else "current_only",
        app_release_id="app-0.46.0",
        app_version="0.46.0",
        app_artifact_manifest_sha256="f" * 64,
    )
    return {
        "contract": discovery.DISCOVERY_CONTRACT,
        "schema_version": discovery.DISCOVERY_SCHEMA_VERSION,
        "channel": channel,
        "pair_manifest": pair,
        "database_manifest": database,
        "artifacts": {
            "app": {
                "url": "https://updates.example/app-0.46.0.zip",
                "sha256": "a" * 64,
                "size_bytes": 2345,
                "format": "zip",
                "unpacked_size_bytes": 12_345,
                "unpacked_files": 42,
            },
            "database": {
                "url": "https://updates.example/genshin.db",
                "sha256": database["database"]["sha256"],
                "size_bytes": database["database"]["size_bytes"],
                "format": "sqlite",
            },
        },
        "signature": (
            {"algorithm": "test", "key_id": "test-key", "value": "signed"}
            if signature
            else None
        ),
    }


def _trusted(_payload, signature):
    return signature["key_id"] == "test-key" and signature["value"] == "signed"


def test_https_discovery_validates_binding_and_writes_only_cache(monkeypatch, tmp_path):
    document = _document()
    calls = []

    def fetch(url, *, etag, timeout):
        calls.append((url, etag, timeout))
        return 200, json.dumps(document).encode(), '"pair-2"'

    monkeypatch.setattr(discovery, "_fetch_https", fetch)
    cache = tmp_path / "release-cache.json"
    result = discovery.discover_latest_release_pair(
        url="https://updates.example/latest.json",
        cache_path=cache,
        channel="stable",
        trust_verifier=_trusted,
        checked_at="2026-10-26T12:30:00+08:00",
    )
    assert result["status"] == "available"
    assert result["authoritative"] is True
    assert result["can_offer_update"] is True
    assert result["release_pair_id"] == "pair-2"
    assert result["database_release_id"] == "db-7.1.53-r1"
    assert result["artifacts"]["app"]["format"] == "zip"
    assert result["document_sha256"] == result["confirmation_digest"]
    assert result["trust"] == {
        "source": "signed_discovery",
        "algorithm": "test",
        "key_id": "test-key",
    }
    assert calls == [("https://updates.example/latest.json", None, 3.0)]
    assert cache.is_file()
    assert list(tmp_path.iterdir()) == [cache]


def test_etag_304_and_network_failure_use_validated_cache(monkeypatch, tmp_path):
    document = _document()
    cache = tmp_path / "release-cache.json"
    monkeypatch.setattr(
        discovery,
        "_fetch_https",
        lambda *_args, **_kwargs: (200, json.dumps(document).encode(), '"pair-2"'),
    )
    first = discovery.discover_latest_release_pair(
        url="https://updates.example/latest.json",
        cache_path=cache,
        channel="stable",
        trust_verifier=_trusted,
        checked_at="2026-10-26T12:30:00+08:00",
    )
    assert first["status"] == "available"

    observed = {}

    def not_modified(_url, *, etag, timeout):
        observed.update(etag=etag, timeout=timeout)
        return 304, b"", None

    monkeypatch.setattr(discovery, "_fetch_https", not_modified)
    second = discovery.discover_latest_release_pair(
        url="https://updates.example/latest.json",
        cache_path=cache,
        channel="stable",
        trust_verifier=_trusted,
        checked_at="2026-10-26T12:31:00+08:00",
    )
    assert observed["etag"] == '"pair-2"'
    assert second["status"] == "not_modified"
    assert second["source"] == "not_modified_cache"
    assert second["fetched_at"] == first["fetched_at"]

    monkeypatch.setattr(
        discovery,
        "_fetch_https",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            urllib.error.URLError("offline")
        ),
    )
    offline = discovery.discover_latest_release_pair(
        url="https://updates.example/latest.json",
        cache_path=cache,
        channel="stable",
        trust_verifier=_trusted,
        checked_at="2026-10-26T12:32:00+08:00",
    )
    assert offline["status"] == "offline_cache"
    assert offline["authoritative"] is True
    assert offline["reason"] == "release_pair_discovery_network_unavailable"


def test_invalid_live_document_does_not_hide_behind_cache(monkeypatch, tmp_path):
    cache = tmp_path / "release-cache.json"
    valid = _document()
    monkeypatch.setattr(
        discovery,
        "_fetch_https",
        lambda *_args, **_kwargs: (200, json.dumps(valid).encode(), '"good"'),
    )
    discovery.discover_latest_release_pair(
        url="https://updates.example/latest.json",
        cache_path=cache,
        channel="stable",
        trust_verifier=_trusted,
    )
    invalid = copy.deepcopy(valid)
    invalid["database_manifest"]["release_pair_id"] = "crossed"
    monkeypatch.setattr(
        discovery,
        "_fetch_https",
        lambda *_args, **_kwargs: (200, json.dumps(invalid).encode(), '"bad"'),
    )
    result = discovery.discover_latest_release_pair(
        url="https://updates.example/latest.json",
        cache_path=cache,
        channel="stable",
        trust_verifier=_trusted,
    )
    assert result["status"] == "rejected"
    assert result["source"] == "remote"
    assert result["can_offer_update"] is False


def test_unsigned_stable_is_informational_but_unsigned_dev_fixture_is_explicit(
    monkeypatch, tmp_path
):
    stable = _document(signature=False)
    monkeypatch.setattr(
        discovery,
        "_fetch_https",
        lambda *_args, **_kwargs: (200, json.dumps(stable).encode(), None),
    )
    result = discovery.discover_latest_release_pair(
        url="https://updates.example/latest.json",
        cache_path=tmp_path / "stable-cache.json",
        channel="stable",
    )
    assert result["status"] == "untrusted"
    assert result["authoritative"] is False
    assert result["can_offer_update"] is False

    fixture = tmp_path / "latest-dev.json"
    fixture.write_text(
        json.dumps(_document(channel="dev", signature=False)), encoding="utf-8"
    )
    dev = discovery.discover_latest_release_pair(
        url=None,
        cache_path=tmp_path / "unused.json",
        channel="dev",
        allow_unsigned_dev=True,
        test_fixture_path=fixture,
    )
    assert dev["status"] == "available"
    assert dev["source"] == "test_fixture"
    assert dev["authoritative"] is True


def test_disabled_non_https_and_raw_upstream_are_not_release_pairs(monkeypatch, tmp_path):
    disabled = discovery.discover_latest_release_pair(
        url=None,
        cache_path=tmp_path / "cache.json",
        channel="stable",
    )
    assert disabled["status"] == "disabled"
    with pytest.raises(discovery.ReleasePairDiscoveryError) as failure:
        discovery.discover_latest_release_pair(
            url="http://updates.example/latest.json",
            cache_path=tmp_path / "cache.json",
            channel="stable",
        )
    assert failure.value.reason == "release_pair_discovery_url_invalid"

    monkeypatch.setattr(
        discovery,
        "_fetch_https",
        lambda *_args, **_kwargs: (
            200,
            json.dumps({"upstream_commit": "f" * 40, "game_version": "7.1.53"}).encode(),
            None,
        ),
    )
    raw = discovery.discover_latest_release_pair(
        url="https://updates.example/latest.json",
        cache_path=tmp_path / "cache.json",
        channel="stable",
        trust_verifier=_trusted,
    )
    assert raw["status"] == "rejected"
    assert raw["reason"] == "release_pair_discovery_document_invalid"


def test_signed_artifact_locators_are_strict_and_database_bytes_are_bound():
    document = _document()
    checked, trusted = discovery.validate_latest_document(
        document,
        expected_channel="stable",
        trust_verifier=_trusted,
        allow_unsigned_dev=False,
    )
    assert trusted is True
    assert checked["artifacts"] == document["artifacts"]

    insecure = copy.deepcopy(document)
    insecure["artifacts"]["app"]["url"] = "http://updates.example/app.zip"
    with pytest.raises(discovery.ReleasePairDiscoveryError) as url_error:
        discovery.validate_latest_document(
            insecure,
            expected_channel="stable",
            trust_verifier=_trusted,
            allow_unsigned_dev=False,
        )
    assert url_error.value.reason == "release_pair_artifact_locator_invalid"

    crossed = copy.deepcopy(document)
    crossed["artifacts"]["database"]["sha256"] = "0" * 64
    with pytest.raises(discovery.ReleasePairDiscoveryError) as binding_error:
        discovery.validate_latest_document(
            crossed,
            expected_channel="stable",
            trust_verifier=_trusted,
            allow_unsigned_dev=False,
        )
    assert binding_error.value.reason == "release_pair_database_artifact_binding_mismatch"
