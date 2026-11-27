"""The Pages publication includes and re-reads every retained signed combination."""

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest
import test_product_release_index as release_fixtures
from test_product_release_index import _signature, _signed, _verify

from genshin_app.product_release_index import canonical
from scripts import verify_public_release_downloads as readback
from scripts.product_site_publication import (
    OFFLINE_PAIR_CATALOG,
    OFFLINE_PAIR_DIRECTORY,
)

releases = release_fixtures.releases


@pytest.fixture
def publish_site(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from scripts import build_release_pair_feed

    monkeypatch.setattr(build_release_pair_feed, "build_trust_verifier", lambda _keys: _verify)
    monkeypatch.setattr(readback, "build_trust_verifier", lambda _keys: _verify)

    def publish(payload, *, setup=None, offline_bundle=None):
        unsigned, signature, site = (tmp_path / name for name in ("unsigned", "signature", "site"))
        unsigned.write_bytes(canonical({**payload, "signature": None}))
        signature.write_bytes(canonical(_signature(payload)))
        setup_asset = None
        if setup is not None:
            setup_asset = tmp_path / "setup.json"
            setup_asset.write_bytes(canonical(setup))
        offline_asset = None
        if offline_bundle is not None:
            offline_asset = tmp_path / "offline.json"
            offline_asset.write_bytes(canonical(offline_bundle))
        receipt = build_release_pair_feed.finalize_product_site(unsigned, signature, site, (),
                                                               setup_asset_path=setup_asset,
                                                               offline_bundle_asset_path=offline_asset)
        assert receipt["offline_pair_documents"] == len(payload["pairs"])
        return site

    return publish


def _local_public_http(monkeypatch, public_bytes):
    calls = []

    def get(url, *, maximum, reference=None):
        calls.append(url)
        assert url in public_bytes
        raw = public_bytes[url]
        if reference is not None and reference != raw:
            raise ValueError("published document bytes differ")
        assert len(raw) == maximum
        return {"url": url, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    monkeypatch.setattr(readback, "read_public_bytes", get)
    return calls


def test_public_readback_verifies_every_retained_combination_and_unique_asset(publish_site, releases, monkeypatch):
    _, _, documents, sources, payload = releases
    setup_raw = b"downloaded setup archive"
    setup = {"url": "https://example.invalid/setup.zip", "size_bytes": len(setup_raw),
             "sha256": hashlib.sha256(setup_raw).hexdigest()}
    offline_raw = b"complete offline installation archive"
    offline = {"url": "https://example.invalid/offline.zip", "size_bytes": len(offline_raw),
               "sha256": hashlib.sha256(offline_raw).hexdigest()}
    site = publish_site(payload, setup=setup, offline_bundle=offline)
    root_url = "https://example.invalid/pages"
    catalog = json.loads((site / OFFLINE_PAIR_CATALOG).read_bytes())
    assert catalog["current_pair_id"] == "pair-product-2-2"
    assert (site / "database-install-pair.json").read_bytes() == canonical(documents[2, 2])
    assert [row["pair_sequence"] for row in catalog["pairs"]] == [4, 3, 2, 1]
    for row in catalog["pairs"]:
        assert row["document"] == f'{OFFLINE_PAIR_DIRECTORY}/pair-{row["pair_sequence"]}.json'
        expected = next(document for document in documents.values()
                        if document["pair_manifest"]["release_pair_id"] == row["release_pair_id"])
        assert (site / row["document"]).read_bytes() == canonical(expected)
    site_files = {f"{root_url}/{member.relative_to(site).as_posix()}": member.read_bytes()
                  for member in site.rglob("*.json")}
    site_files[f"{root_url}/index.html"] = (site / "index.html").read_bytes()
    public = {**site_files, **{url: path.read_bytes() for url, path in sources.items()},
              setup["url"]: setup_raw, offline["url"]: offline_raw}
    calls = _local_public_http(monkeypatch, public)
    result = readback.verify_public_site(site, {}, base_url=root_url, setup=setup, offline_bundle=offline)
    assert result["status"] == "verified" and result["verified_offline_pairs"] == 4
    assert result["verified_asset_urls"] == 6
    assert set(calls) == set(public) and len(calls) == len(public)
    assert result["readback"]["app"]["url"] == documents[2, 2]["artifacts"]["app"]["url"]
    assert result["readback"]["database"]["url"] == documents[2, 2]["artifacts"]["database"]["url"]
    assert result["readback"]["setup"]["url"] == setup["url"]
    assert result["readback"]["offline_bundle"]["url"] == offline["url"]

    historic_doc = f"{root_url}/{OFFLINE_PAIR_DIRECTORY}/pair-1.json"
    raw = public[historic_doc]
    public[historic_doc] = b"X" + raw[1:]
    with pytest.raises(ValueError, match="published document bytes differ"):
        readback.verify_public_site(site, {}, base_url=root_url, setup=setup)
    public[historic_doc] = raw

    # A retained asset missing integrity breaks readback even when the latest
    # public installer and its two default downloads are unchanged.
    historic_url = documents[1, 1]["artifacts"]["app"]["url"]
    public[historic_url] = b"X" + public[historic_url][1:]
    with pytest.raises(ValueError, match="published download digest differs"):
        readback.verify_public_site(site, {}, setup=setup)


@pytest.mark.parametrize("damage", [
    "changed_document", "unlisted_file", "missing_link", "missing_catalogue", "wrong_group", "extra_link",
])
def test_public_readback_rejects_incomplete_or_changed_history_before_network(
    publish_site, releases, monkeypatch, damage,
):
    _, _, _documents, _sources, payload = releases
    site = publish_site(payload)
    calls = _local_public_http(monkeypatch, {})
    history, page, catalog = site / OFFLINE_PAIR_DIRECTORY, site / "index.html", site / OFFLINE_PAIR_CATALOG
    if damage == "changed_document":
        (history / "pair-1.json").write_bytes((history / "pair-2.json").read_bytes())
    elif damage == "unlisted_file":
        (history / "unexpected.json").write_bytes(b"{}")
    elif damage == "missing_link":
        page.write_bytes(page.read_bytes().replace(b'href="database-install-pairs/pair-1.json"', b'href="elsewhere"'))
    elif damage == "wrong_group":
        text = page.read_bytes()
        first = b"database-install-pairs/pair-1.json"
        second = b"database-install-pairs/pair-2.json"
        page.write_bytes(text.replace(first, b"temporary-link").replace(second, first).replace(b"temporary-link", second))
    elif damage == "extra_link":
        page.write_bytes(page.read_bytes().replace(b"</main>", b'<a href="database-install-pairs/arbitrary.json">extra</a></main>'))
    else:
        catalog.unlink()
    with pytest.raises(ValueError, match="document|inventory|missing published links"):
        readback.verify_public_site(site, {})
    assert not calls


def test_legacy_single_pair_pages_still_verify_without_history(publish_site, releases, monkeypatch):
    _, _, documents, sources, payload = releases
    latest = documents[2, 2]
    site = publish_site({**payload, "pairs": [latest], "apps": [payload["apps"][-1]]})
    (site / OFFLINE_PAIR_CATALOG).unlink()
    history = site / OFFLINE_PAIR_DIRECTORY
    (history / "pair-4.json").unlink()
    history.rmdir()
    page = site / "index.html"
    page.write_bytes(re.sub(rb"<section>.*?</section>", b"", page.read_bytes()))
    references = {locator["url"]: sources[locator["url"]].read_bytes() for locator in latest["artifacts"].values()}
    calls = _local_public_http(monkeypatch, references)
    result = readback.verify_public_site(site, {})
    assert result["verified_offline_pairs"] == 1
    assert set(calls) == set(references) and len(calls) == len(references)


def test_pages_cannot_bind_one_public_url_to_two_signed_assets(publish_site, releases):
    _, _, documents, _, payload = releases
    app_url = documents[2, 2]["artifacts"]["app"]["url"]
    altered = copy.deepcopy(payload)
    for pair in altered["pairs"]:
        if pair["pair_manifest"]["app"]["app_release_id"] == "app-product-1":
            pair["artifacts"]["app"]["url"] = app_url
            replacement = _signed({key: value for key, value in pair.items() if key != "signature"})
            pair.update(replacement)
    with pytest.raises(ValueError, match="signed asset URL identifies different bytes"):
        publish_site(altered)
