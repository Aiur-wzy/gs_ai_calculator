"""Stable filenames and metadata shared by the product Pages publisher and verifier."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

OFFLINE_PAIR_DIRECTORY = "database-install-pairs"
OFFLINE_PAIR_CATALOG = f"{OFFLINE_PAIR_DIRECTORY}.json"


def offline_pair_catalog(index):
    """Project only previously verified index entries to bounded numeric paths."""
    rows = []
    for pair_id, document in index["pairs"].items():
        pair = document["pair_manifest"]
        sequence = pair["pair_sequence"]
        if type(sequence) is not int or not 0 < sequence < 2**63:
            raise ValueError("product_site_pair_sequence_invalid")
        rows.append({
            "release_pair_id": pair_id,
            "pair_sequence": sequence,
            "app_release_id": pair["app"]["app_release_id"],
            "database_release_id": document["database_manifest"]["database_release_id"],
            "document": f"{OFFLINE_PAIR_DIRECTORY}/pair-{sequence}.json",
        })
    rows.sort(key=lambda item: item["pair_sequence"], reverse=True)
    return {"contract": "genshin-product-offline-pair-catalog-v1",
            "index_sha256": index["index_sha256"],
            "current_pair_id": index["combinations"][index["latest"]],
            "pairs": rows}


def validate_setup_locator(value):
    if type(value) is not dict or set(value) != {"url", "sha256", "size_bytes"}:
        raise ValueError("product_release_setup_asset_invalid")
    url = urlsplit(value["url"] if isinstance(value["url"], str) else "")
    if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
            or url.fragment or not isinstance(value["sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", value["sha256"]) is None
            or type(value["size_bytes"]) is not int or not 0 < value["size_bytes"] < 2 * 1024**3):
        raise ValueError("product_release_setup_asset_invalid")
    return value


def site_downloads(index, latest, setup=None, *, offline_bundle=None):
    """Keep the latest public receipt keys and bind each retained URL once."""
    downloads = dict(latest["artifacts"])
    if setup is not None:
        downloads["setup"] = validate_setup_locator(setup)
    if offline_bundle is not None:
        downloads["offline_bundle"] = validate_setup_locator(offline_bundle)
    by_url = {}

    def bind(locator):
        url, sha256, size = locator["url"], locator["sha256"], locator["size_bytes"]
        if not 0 < size < 2 * 1024**3:
            raise ValueError(f"local GitHub asset size invalid: {url}")
        identity = (sha256, size)
        if url in by_url:
            if by_url[url] != identity:
                raise ValueError(f"signed asset URL identifies different bytes: {url}")
            return False
        by_url[url] = identity
        return True

    for locator in downloads.values():
        bind(locator)
    for row in offline_pair_catalog(index)["pairs"]:
        for kind, locator in index["pairs"][row["release_pair_id"]]["artifacts"].items():
            if bind(locator):
                downloads[f'retained-pair-{row["pair_sequence"]}-{kind}'] = locator
    return downloads
