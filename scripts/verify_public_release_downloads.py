"""Verify a deployed product index and stream its public assets through SHA-256."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True, help="本地验签后发布的 Pages 目录")
    parser.add_argument("--base-url", help="公开 Pages 根地址。附件先上线时可省略，先验签并读回附件。")
    parser.add_argument("--public-key-record", type=Path, required=True)
    parser.add_argument("--setup-asset", type=Path, help="小安装器的 HTTPS 地址、长度和 SHA-256 JSON")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    current_url = ""
    try:
        index = (args.site / "latest.json").read_bytes()
        record = json.loads(args.public_key_record.read_bytes())
        result = validate_product_release_index(json.loads(index), channel="stable",
                                                trust_verifier=build_trust_verifier((record,)))
        selected = result["pairs"][result["combinations"][result["latest"]]]
        setup = json.loads(args.setup_asset.read_bytes()) if args.setup_asset else None
        page = (args.site / "index.html").read_bytes()
        pair = (args.site / "database-install-pair.json").read_bytes()
        if pair != canonical(selected):
            raise ValueError("offline database document differs from signed index pair")
        documents = {"latest.json": index, "index.html": page, "database-install-pair.json": pair}
        readback = {}
        for name, raw in (documents.items() if args.base_url else ()):
            if not 0 < len(raw) <= MAX_DOCUMENT_BYTES:
                raise ValueError("local publication document size invalid")
            current_url = f"{args.base_url.rstrip('/')}/{name}"
            readback[name] = read_public_bytes(current_url, maximum=len(raw), reference=raw)
        downloads = {**selected["artifacts"], **({"setup": setup} if setup is not None else {})}
        for name, expected in downloads.items():
            if not 0 < expected["size_bytes"] < 2 * 1024**3:
                raise ValueError("local GitHub asset size invalid")
            current_url = expected["url"]
            actual = read_public_bytes(current_url, maximum=expected["size_bytes"])
            if actual["sha256"] != expected["sha256"]:
                raise ValueError("published download digest differs")
            readback[name] = actual
        receipt = {"status": "verified", "pages_readback": bool(args.base_url), "index_sha256": result["index_sha256"],
                   "latest": list(result["latest"]), "readback": readback}
        with args.report.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(receipt, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
    except (OSError, ValueError, URLError) as error:
        parser.exit(1, f"公开读回失败（{current_url}）。{error}\n")
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
