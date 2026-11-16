from __future__ import annotations

import ast
import json
import shutil
from pathlib import Path

import pytest

from genshin_app.product_release_index import canonical, validate_product_release_index
from genshin_app.release_pair_trust import build_trust_verifier
from scripts import installation_qa_fixture as qa
from tests.test_product_release_index import releases  # noqa: F401


@pytest.fixture
def qa_root(tmp_path):
    openssl = shutil.which("openssl") or r"C:\Program Files\Git\usr\bin\openssl.exe"
    if not Path(openssl).is_file():
        pytest.skip("OpenSSL is required for the real isolated RSA-PSS signer")
    root = tmp_path / "new-qa-only"
    receipt = qa.initialize(root, openssl)
    assert receipt["production_authority"] is False
    return root, openssl


def test_qa_signer_matches_its_public_key_and_never_prints_private_key(qa_root, capsys):
    root, openssl = qa_root
    _, row = qa._root(root)
    signature = qa._sign(root, row, {"fact": "test only"}, openssl, "test")
    verifier = build_trust_verifier(tuple(row["trust_keys"]))
    assert verifier({"fact": "test only"}, signature)
    assert not verifier({"fact": "changed"}, signature)
    assert "PRIVATE KEY" not in capsys.readouterr().out
    with pytest.raises(ValueError, match="must_be_new"):
        qa.initialize(root, openssl)


def test_overlay_is_new_file_and_source_remains_unchanged(qa_root, tmp_path):
    root, _ = qa_root
    source = Path("src/genshin_app/version.py")
    before = source.read_bytes()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"channel": "stable", "database_release_id": "db-qa", "release_sequence": 9}))
    predecessor = tmp_path / "predecessor.json"
    predecessor.write_text("{}")
    result = qa.version_overlay(root, source, manifest, predecessor, "app-0.46.0-r21-qa", "pair-r21-qa", 3)
    assert source.read_bytes() == before
    assert Path(result["version_overlay"]).is_relative_to(root)
    tree = ast.parse(Path(result["version_overlay"]).read_text("utf-8"))
    values = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id in {"APP_RELEASE_ID", "DATABASE_RELEASE_TRUST_KEYS", "APP_DIR_NAME", "INSTALLATION_QA_RUN_ID"}}
    assert values["APP_RELEASE_ID"].endswith("-qa") and values["APP_DIR_NAME"] == "GenshinCalcQA"
    assert build_trust_verifier(values["DATABASE_RELEASE_TRUST_KEYS"]) is not None
    assert len(values["INSTALLATION_QA_RUN_ID"]) == 32
    from scripts.verify_windows_installation_registration import qa_registration
    _, _, registration = qa_registration(root)
    assert registration.qa_run_id == values["INSTALLATION_QA_RUN_ID"]


def test_qa_cannot_sign_a_feed_outside_its_new_workspace(qa_root, tmp_path):
    root, openssl = qa_root
    with pytest.raises(ValueError, match="stay_inside"):
        qa.sign_feed(root, tmp_path, tmp_path / "index.json", openssl)


def test_real_qa_signatures_complete_feed_and_preserve_predecessor(qa_root, releases):  # noqa: F811
    root, openssl = qa_root
    _, row = qa._root(root)
    apps, databases, documents, sources, original_index = releases
    old_payload = {key: value for key, value in documents[1, 1].items() if key != "signature"}
    old_signed = {**old_payload, "signature": qa._sign(root, row, old_payload, openssl, "old-pair")}
    past_payload = {**original_index, "latest_app_release_id": "app-product-1", "latest_database_release_id": "db-7.0.0-r1",
                    "apps": original_index["apps"][:1], "pairs": [old_signed]}
    past_payload.pop("signature", None)
    previous = root / "previous-index.json"
    previous.write_bytes(canonical({**past_payload, "signature": qa._sign(root, row, past_payload, openssl, "old-index")}))
    old_pair_file = root / "old-pair-manifest.json"
    old_pair_file.write_bytes(canonical(old_signed["pair_manifest"]))
    database, manifest = databases[1]
    manifest_file = root / "database.json"
    manifest_file.write_text(json.dumps(manifest))
    qa.version_overlay(root, Path("src/genshin_app/version.py"), manifest_file, old_pair_file,
                       "app-0.46.0-r21-qa", "pair-app-qa", 2)
    prepared = qa.prepare_feed(root, apps[2][0], database, manifest_file, previous,
                              sources[old_signed["artifacts"]["database"]["url"]])
    assert prepared["production_authority"] is False
    feed = root / "feed"
    result = qa.sign_feed(root, feed, previous, openssl)
    index = json.loads(Path(result["index"]).read_text("utf-8"))
    checked = validate_product_release_index(index, channel="stable", trust_verifier=build_trust_verifier(tuple(row["trust_keys"])))
    assert result["production_authority"] is False
    assert checked["latest"] == ("app-0.46.0-r21-qa", "db-7.0.0-r1")
    assert index["pairs"][0] == old_signed
