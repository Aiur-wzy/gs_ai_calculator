from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from genshin_app import product_release_index
from genshin_app.product_release_index import (
    canonical,
    release_options,
    validate_product_release_index,
)
from genshin_app.release_pair_discovery import (
    QUALIFIED_DISCOVERY_CONTRACT,
    QUALIFIED_DISCOVERY_SCHEMA_VERSION,
    ReleasePairDiscoveryError,
)
from genshin_app.release_pair_feed import ReleasePairFeedError
from genshin_app.release_pair_trust import build_trust_verifier
from genshin_db.release_qualification import (
    QUALIFICATION_CONTRACT,
    manifest_fingerprint,
)
from tests.test_product_release_index import _signed, _verify, releases  # noqa: F401


@pytest.fixture
def publisher(monkeypatch):
    from scripts import build_release_pair_feed

    monkeypatch.setattr(build_release_pair_feed, "build_trust_verifier", lambda _keys: _verify)
    return build_release_pair_feed


def test_publisher_imports_in_fresh_offline_signing_process(tmp_path):
    root = Path(__file__).resolve().parents[1]
    load = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "from scripts.build_release_pair_feed import prepare_update_transition, finalize_product_site")
    result = subprocess.run([sys.executable, "-I", "-c", load, str(root)], cwd=tmp_path,
                            capture_output=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr.decode(errors="replace")


def _write(path, document):
    path.write_bytes(canonical(document))
    return path


def _prior(payload, pair, *, expired=False):
    expiry = datetime.now(UTC) - timedelta(minutes=1) if expired else datetime.fromisoformat(payload["expires_at"])
    return _signed({**payload, "index_sequence": 12, "apps": payload["apps"][:1], "pairs": [pair],
                    "latest_app_release_id": pair["pair_manifest"]["app"]["app_release_id"],
                    "latest_database_release_id": pair["database_manifest"]["database_release_id"],
                    "expires_at": expiry.isoformat()})


def _qualified(pair, *, sequence=3):
    manifest = pair["database_manifest"]
    database = manifest["database"]
    payload = {key: value for key, value in pair.items() if key != "signature"}
    payload["pair_manifest"] = {**pair["pair_manifest"], "pair_sequence": sequence}
    return _signed({**payload, "contract": QUALIFIED_DISCOVERY_CONTRACT,
                    "schema_version": QUALIFIED_DISCOVERY_SCHEMA_VERSION, "database_qualification": {
                        "contract": QUALIFICATION_CONTRACT,
                        "database_release_id": manifest["database_release_id"],
                        "manifest_fingerprint": manifest_fingerprint(manifest),
                        "sha256": database["sha256"],
                        "size_bytes": database["size_bytes"],
                        "release_core_sha256": database["release_core"]["root_sha256"],
                        "meta_fingerprint": database["meta_fingerprint"],
                        "mutable_layer_fingerprint": "a" * 64,
                        "manual_content_fingerprint": None,
                    }})


def test_prepare_update_transition_keeps_exact_signed_pairs_and_routes_only_one_asset(
    tmp_path, publisher, releases,  # noqa: F811
):
    _, _, documents, _, payload = releases
    installed, bridge, final = documents[1, 1], documents[2, 1], _qualified(documents[2, 2])
    past = _prior(payload, installed)
    paths = [_write(tmp_path / f"{name}.json", document)
             for name, document in (("previous", past), ("bridge", bridge), ("final", final))]
    released = datetime.now(UTC)
    expires = released + timedelta(days=30)
    output = tmp_path / "transition"
    report = publisher.prepare_update_transition(
        *paths, released.isoformat(), expires.isoformat(), output, (), index_sequence_floor=15, app_sequence_floor=5,
    )
    assert report["legacy_index_sequence"] == 16 and report["current_index_sequence"] == 17
    assert report["bridge_pair_id"] == bridge["pair_manifest"]["release_pair_id"]
    assert report["final_pair_id"] == final["pair_manifest"]["release_pair_id"]
    assert paths[0].read_bytes() == canonical(past)
    confirmed = {}
    for name, expected_pairs in (("legacy", [installed, bridge]), ("current", [installed, bridge, final])):
        unsigned = json.loads((output / f"{name}-index.unsigned.json").read_bytes())
        assert unsigned.pop("signature") is None
        assert unsigned["pairs"] == expected_pairs
        assert unsigned["apps"][:-1] == past["apps"]
        assert unsigned["apps"][-1]["release_sequence"] == 6
        assert (output / f"{name}-index-signing-payload.canonical.json").read_bytes() == canonical(unsigned)
        confirmed[name] = validate_product_release_index(_signed(unsigned), channel="stable",
                                                         trust_verifier=_verify, now=released)
    (app_only,) = release_options(confirmed["legacy"], installed["pair_manifest"], installed["database_manifest"])
    assert (app_only["mode"], app_only["reuse_database"], app_only["reuse_app"]) == ("app_only", True, False)
    (database_only,) = release_options(confirmed["current"], bridge["pair_manifest"], bridge["database_manifest"])
    assert (database_only["mode"], database_only["reuse_database"], database_only["reuse_app"]) == (
        "database_only", False, True,
    )
    assert not release_options(confirmed["current"], final["pair_manifest"], final["database_manifest"])


@pytest.mark.parametrize(("case", "reason"), [
    ("v3_bridge", "transition_legacy_requires_v2"),
    ("v2_final", "transition_final_requires_forward_v3"),
    ("pair_sequence_gap", "transition_final_requires_forward_v3"),
    ("database_first", "transition_invalid"),
    ("different_app_archive", "product_release_app_artifact_conflict"),
])
def test_prepare_update_transition_rejects_wrong_formats_and_routes(
    tmp_path, publisher, releases, case, reason,  # noqa: F811
):
    _, _, documents, _, payload = releases
    installed, bridge, final = documents[1, 1], documents[2, 1], _qualified(documents[2, 2])
    if case == "v3_bridge":
        bridge = _qualified(bridge, sequence=2)
    elif case == "v2_final":
        final = documents[2, 2]
    elif case == "pair_sequence_gap":
        final = _qualified(documents[2, 2], sequence=4)
    elif case == "database_first":
        bridge = documents[1, 2]
        final = _qualified(documents[2, 2], sequence=4)
    else:
        final["artifacts"]["app"]["sha256"] = "f" * 64
        final.pop("signature")
        final = _signed(final)
    paths = [_write(tmp_path / f"{name}.json", document) for name, document in (
        ("previous", _prior(payload, installed)), ("bridge", bridge), ("final", final),
    )]
    output = tmp_path / "transition"
    released = datetime.now(UTC)
    expires = released + timedelta(days=30)
    with pytest.raises((ReleasePairDiscoveryError, ReleasePairFeedError), match=reason):
        publisher.prepare_update_transition(*paths, released.isoformat(), expires.isoformat(), output, ())
    assert not output.exists()


@pytest.mark.parametrize(("addition_ids", "latest", "modes"), [
    ([(2, 1)], (2, 1), ["app_only"]),
    ([(1, 2)], (1, 2), ["database_only"]),
    ([(2, 2), (1, 2), (2, 1)], (2, 2), ["app_only", "database_only", "both"]),
])
def test_index_next_keeps_signed_compatibility_and_unchanged_artifacts(
    tmp_path, publisher, releases, addition_ids, latest, modes,  # noqa: F811
):
    _, _, documents, _, payload = releases
    old = documents[1, 1]
    prior = _prior(payload, old)
    prior_path = _write(tmp_path / "latest.json", prior)
    inputs = [_write(tmp_path / f"pair-{a}-{d}.json", documents[a, d]) for a, d in addition_ids]
    now = datetime.now(UTC)
    output = tmp_path / "next.unsigned.json"
    report = publisher.prepare_next_index(prior_path, inputs, now.isoformat(), (now + timedelta(days=30)).isoformat(),
                                          output, ())
    next_index = json.loads(output.read_bytes())
    assert next_index.pop("signature") is None
    assert report["index_sequence"] == next_index["index_sequence"] == 13
    assert report["added_pairs"] == len(inputs)
    assert next_index["pairs"][0] == old and next_index["apps"][0] == prior["apps"][0]
    assert prior_path.read_bytes() == canonical(prior)
    checked = validate_product_release_index(_signed(next_index), channel="stable", trust_verifier=_verify, now=now)
    a, d = latest
    assert checked["latest"] == (f"app-product-{a}", f"db-7.0.0-r{d}")
    assert checked["app_sequences"] == {f"app-product-{number}": number for number in range(1, a + 1)}
    assert [row["mode"] for row in release_options(checked, old["pair_manifest"], old["database_manifest"])] == modes
    if d == 1:
        next_pair = checked["pairs"][report["release_pair_id"]]
        assert next_pair["database_manifest"] == old["database_manifest"]
        assert next_pair["artifacts"]["database"] == old["artifacts"]["database"]


@pytest.mark.parametrize(("case", "reason"), [
    ("pair_sequence", "next_index_pair_sequence_invalid"),
    ("db_sequence", "next_index_database_sequence_invalid"),
    ("artifact", "database_artifact_conflict"),
    ("pair_signature", "pair_signature_untrusted"),
    ("outer_signature", "index_signature_untrusted"),
    ("missing_bridge", "latest_pair_missing"),
])
def test_index_next_rejects_incorrect_or_incomplete_release_proofs(tmp_path, publisher, releases, case, reason):  # noqa: F811
    _, _, documents, _, payload = releases
    prior = _prior(payload, documents[1, 2] if case == "db_sequence" else documents[1, 1])
    candidate = copy.deepcopy(documents[1, 1] if case == "db_sequence" else documents[2, 1])
    if case == "pair_sequence":
        candidate["pair_manifest"]["pair_sequence"] = 1
    elif case == "db_sequence":
        candidate["pair_manifest"]["pair_sequence"] = 4
    elif case == "artifact":
        candidate["artifacts"]["database"]["sha256"] = "f" * 64
    elif case == "pair_signature":
        candidate["signature"] = None
    elif case == "outer_signature":
        prior["index_sequence"] += 1
    if case in {"pair_sequence", "db_sequence", "artifact"}:
        candidate.pop("signature")
        candidate = _signed(candidate)
    prior_path = _write(tmp_path / "latest.json", prior)
    paths = [_write(tmp_path / "pair.json", candidate)]
    if case == "missing_bridge":
        paths.append(_write(tmp_path / "db-new.json", documents[1, 2]))
    output = tmp_path / "next.unsigned.json"
    now = datetime.now(UTC)
    with pytest.raises((ReleasePairDiscoveryError, ReleasePairFeedError), match=reason):
        publisher.prepare_next_index(prior_path, paths, now.isoformat(), (now + timedelta(days=1)).isoformat(), output, ())
    assert not output.exists()


def test_index_next_renews_an_expired_index_only_with_explicit_renewal(tmp_path, publisher, releases):  # noqa: F811
    _, _, documents, _, payload = releases
    prior = _prior(payload, documents[1, 1], expired=True)
    prior_path = _write(tmp_path / "latest.json", prior)
    output = tmp_path / "next.unsigned.json"
    now = datetime.now(UTC)
    with pytest.raises(ReleasePairFeedError, match="next_index_inputs_invalid"):
        publisher.prepare_next_index(prior_path, (), now.isoformat(), (now + timedelta(days=1)).isoformat(), output, ())
    report = publisher.prepare_next_index(prior_path, (), now.isoformat(), (now + timedelta(days=1)).isoformat(),
                                          output, (), renew=True)
    assert report["renewed"] and report["added_pairs"] == 0
    refreshed = json.loads(output.read_bytes())
    assert refreshed["pairs"] == prior["pairs"] and refreshed["apps"] == prior["apps"]
    assert refreshed["latest_app_release_id"] == prior["latest_app_release_id"]
    assert refreshed["latest_database_release_id"] == prior["latest_database_release_id"]
    assert refreshed["index_sequence"] == prior["index_sequence"] + 1


def test_index_next_rejects_candidate_outside_wire_budget(tmp_path, monkeypatch, publisher, releases):  # noqa: F811
    _, _, documents, _, payload = releases
    prior_path = _write(tmp_path / "latest.json", _prior(payload, documents[1, 1]))
    pair_path = _write(tmp_path / "pair.json", documents[2, 1])
    output = tmp_path / "next.unsigned.json"
    now = datetime.now(UTC)
    monkeypatch.setattr(product_release_index, "MAX_DOCUMENT_BYTES", prior_path.stat().st_size + 100)
    with pytest.raises(ReleasePairDiscoveryError, match="discovery_too_large"):
        publisher.prepare_next_index(prior_path, [pair_path], now.isoformat(), (now + timedelta(days=1)).isoformat(),
                                     output, ())
    assert not output.exists()


def test_index_next_verifies_real_nested_pair_signatures(tmp_path, monkeypatch, releases):  # noqa: F811
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable for RSA-PSS interoperability fixture")
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from scripts import build_release_pair_feed

    _, _, documents, _, payload = releases
    script = """
const crypto = require('node:crypto');
const fs = require('node:fs');
const { privateKey, publicKey } = crypto.generateKeyPairSync('rsa', { modulusLength: 2048 });
const { n, e } = publicKey.export({ format: 'jwk' });
const key = { key_id: 'local-rsa-fixture', algorithm: 'rsa-pss-sha256',
  modulus_hex: Buffer.from(n, 'base64url').toString('hex'), exponent: Buffer.from(e, 'base64url').readUIntBE(0, 3) };
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map(k => JSON.stringify(k) + ':' + canonical(value[k])).join(',')}}`;
  }
  return JSON.stringify(value);
}
function signed({ signature, ...payload }) {
  const value = crypto.sign('sha256', Buffer.from(canonical(payload)), {
    key: privateKey, padding: crypto.constants.RSA_PKCS1_PSS_PADDING, saltLength: 32,
  }).toString('base64url');
  return { ...payload, signature: { algorithm: key.algorithm, key_id: key.key_id, value } };
}
const { previous, next } = JSON.parse(fs.readFileSync(0, 'utf8'));
previous.pairs = previous.pairs.map(signed);
process.stdout.write(JSON.stringify({ previous: signed(previous), next: signed(next), key }));
"""
    signed = json.loads(subprocess.run([node, "-e", script], input=canonical({
        "previous": _prior(payload, documents[1, 1], expired=True), "next": documents[2, 1],
    }), capture_output=True, check=True).stdout)
    keys = (signed["key"],)
    assert validate_product_release_index(signed["previous"], channel="stable", for_install=False,
                                          trust_verifier=build_trust_verifier(keys))["latest"] == (
                                              "app-product-1", "db-7.0.0-r1")
    prior_path = _write(tmp_path / "index.json", signed["previous"])
    pair_path = _write(tmp_path / "pair.json", signed["next"])
    now = datetime.now(UTC)
    args = (prior_path, [pair_path], now.isoformat(), (now + timedelta(days=1)).isoformat())
    result = build_release_pair_feed.prepare_next_index(*args, tmp_path / "next.unsigned.json", keys)
    assert result["release_pair_id"] == signed["next"]["pair_manifest"]["release_pair_id"]
    original = signed["next"]["signature"]["value"]
    signed["next"]["signature"]["value"] = ("A" if original[0] != "A" else "B") + original[1:]
    _write(pair_path, signed["next"])
    with pytest.raises(ReleasePairFeedError, match="pair_signature_untrusted"):
        build_release_pair_feed.prepare_next_index(*args, tmp_path / "invalid.unsigned.json", keys)
