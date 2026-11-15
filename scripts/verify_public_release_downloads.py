"""Verify a deployed product index and stream its public assets through SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from genshin_app.product_release_index import canonical, validate_product_release_index
from genshin_app.release_pair_discovery import (
    MAX_DOCUMENT_BYTES,
    _HttpsOnlyRedirectHandler,
)
from genshin_app.release_pair_trust import build_trust_verifier
from genshin_app.windows_known_folders import is_reparse_point

if __package__:
    from .product_site_publication import (
        OFFLINE_PAIR_CATALOG,
        OFFLINE_PAIR_DIRECTORY,
        offline_pair_catalog,
        site_downloads,
    )
else:
    from product_site_publication import (
        OFFLINE_PAIR_CATALOG,
        OFFLINE_PAIR_DIRECTORY,
        offline_pair_catalog,
        site_downloads,
    )


def read_public_bytes(url: str, *, maximum: int, reference: bytes | None = None) -> dict:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.fragment:
        raise ValueError("expected an HTTPS URL without credentials or fragments")
    digest = hashlib.sha256()
    size = 0
    opener = build_opener(_HttpsOnlyRedirectHandler())
    request = Request(url, headers={"Accept-Encoding": "identity", "User-Agent": "GenshinCalc-release-readback"})
    with opener.open(request, timeout=60) as response:
        if response.status != 200:
            raise ValueError("expected a complete HTTP 200 response")
        while block := response.read(1024 * 1024):
            if reference is not None and reference[size:size + len(block)] != block:
                raise ValueError("published document bytes differ")
            size += len(block)
            if size > maximum:
                raise ValueError("published response exceeds expected length")
            digest.update(block)
    if size != maximum:
        raise ValueError("published response has unexpected length")
    return {"url": url, "sha256": digest.hexdigest(), "size_bytes": size}


def _read_document(path: Path, *, maximum: int = MAX_DOCUMENT_BYTES) -> bytes:
    if not path.is_file() or is_reparse_point(path):
        raise ValueError(f"local publication document is not a regular file: {path.name}")
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if not 0 < len(raw) <= maximum:
        raise ValueError(f"local publication document size invalid: {path.name}")
    return raw


class _PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = set()
        self.items = []
        self._open_items = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self._open_items.append(set())
        elif tag == "a":
            hrefs = {value for name, value in attrs if name == "href" and value}
            self.links.update(hrefs)
            if self._open_items:
                self._open_items[-1].update(hrefs)

    def handle_endtag(self, tag):
        if tag == "li" and self._open_items:
            self.items.append(self._open_items.pop())


def _website_documents(site, raw, verified, selected):
    documents = {"latest.json": raw, "index.html": _read_document(site / "index.html"),
                 "database-install-pair.json": _read_document(site / "database-install-pair.json")}
    if documents["database-install-pair.json"] != canonical(selected):
        raise ValueError("offline database document differs from signed index pair")
    catalog_path = site / OFFLINE_PAIR_CATALOG
    directory = site / OFFLINE_PAIR_DIRECTORY
    # Existing single-pair Pages predate the catalogue; the default alias
    # already covers their complete signed index.
    if len(verified["pairs"]) == 1 and not os.path.lexists(catalog_path) and not os.path.lexists(directory):
        return documents
    catalog = offline_pair_catalog(verified)
    documents[OFFLINE_PAIR_CATALOG] = _read_document(catalog_path)
    if documents[OFFLINE_PAIR_CATALOG] != canonical(catalog):
        raise ValueError("published compatibility catalogue differs from signed index")
    names = {Path(row["document"]).name for row in catalog["pairs"]}
    if not directory.is_dir() or is_reparse_point(directory) or {entry.name for entry in directory.iterdir()} != names:
        raise ValueError("local offline pair documents differ from signed index inventory")
    for row in catalog["pairs"]:
        name = row["document"]
        document = _read_document(site / name)
        if document != canonical(verified["pairs"][row["release_pair_id"]]):
            raise ValueError(f"offline pair document differs from signed index: {name}")
        documents[name] = document
    return documents


def verify_public_site(site: Path, public_key_record: dict, *, base_url: str | None = None, setup=None):
    if not site.is_dir() or is_reparse_point(site):
        raise ValueError("local publication site is not a regular directory")
    raw = _read_document(site / "latest.json")
    verified = validate_product_release_index(json.loads(raw), channel="stable",
                                               trust_verifier=build_trust_verifier((public_key_record,)))
    if raw != canonical(verified["signed_index"]):
        raise ValueError("local published index is not canonical")
    selected = verified["pairs"][verified["combinations"][verified["latest"]]]
    documents = _website_documents(site, raw, verified, selected)
    downloads = site_downloads(verified, selected, setup)
    links = _PageLinks()
    links.feed(documents["index.html"].decode("utf-8"))
    required = (set(documents) - {"index.html"}) | {locator["url"] for locator in downloads.values()}
    if missing := sorted(required - links.links):
        raise ValueError(f"local download page is missing published links: {missing}")
    history_links = {href for href in links.links if href.startswith(OFFLINE_PAIR_DIRECTORY + "/")}
    if history_links != {name for name in documents if name.startswith(OFFLINE_PAIR_DIRECTORY + "/")}:
        raise ValueError("local download page links unexpected offline pair documents")
    for row in offline_pair_catalog(verified)["pairs"] if OFFLINE_PAIR_CATALOG in documents else ():
        name = row["document"]
        locators = verified["pairs"][row["release_pair_id"]]["artifacts"]
        expected = {name, *(locator["url"] for locator in locators.values())}
        if [item for item in links.items if name in item] != [expected]:
            raise ValueError(f"local download page groups the wrong assets for offline pair document: {name}")

    def readback(url, **kwargs):
        try:
            return read_public_bytes(url, **kwargs)
        except (OSError, ValueError, URLError) as error:
            raise ValueError(f"{url}: {error}") from error

    receipts = {}
    if base_url:
        parsed = urlsplit(base_url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.query or parsed.fragment):
            raise ValueError("expected an HTTPS Pages root URL")
        for name, document in documents.items():
            path = f"{base_url.rstrip('/')}/{name}"
            receipts[name] = readback(path, maximum=len(document), reference=document)
    downloaded = {}
    for name, locator in downloads.items():
        url = locator["url"]
        if url not in downloaded:
            actual = readback(url, maximum=locator["size_bytes"])
            if actual["sha256"] != locator["sha256"]:
                raise ValueError(f"{url}: published download digest differs")
            downloaded[url] = actual
        receipts[name] = downloaded[url]
    return {"status": "verified", "pages_readback": bool(base_url), "index_sha256": verified["index_sha256"],
            "latest": list(verified["latest"]), "readback": receipts,
            "verified_asset_urls": len(downloaded), "verified_offline_pairs": len(verified["pairs"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True, help="本地验签后发布的 Pages 目录")
    parser.add_argument("--base-url", help="公开 Pages 根地址。附件先上线时可省略，先验签并读回附件。")
    parser.add_argument("--public-key-record", type=Path, required=True)
    parser.add_argument("--setup-asset", type=Path, help="小安装器的 HTTPS 地址、长度和 SHA-256 JSON")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        record = json.loads(_read_document(args.public_key_record, maximum=64 * 1024))
        setup = json.loads(_read_document(args.setup_asset, maximum=64 * 1024)) if args.setup_asset else None
        receipt = verify_public_site(args.site, record, base_url=args.base_url, setup=setup)
        with args.report.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (OSError, ValueError, URLError) as error:
        parser.exit(1, f"公开读回失败。{error}\n")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
