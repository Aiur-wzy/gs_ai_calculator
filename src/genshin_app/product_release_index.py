"""Signed product discovery and update choices for independently released artifacts."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError

from genshin_db.locking import LockBusyError, file_lock

from .release_pair_discovery import (
    CACHE_CONTRACT,
    CACHE_SCHEMA_VERSION,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_DOCUMENT_BYTES,
    ReleasePairDiscoveryError,
    _fetch_https,
    _load_cache,
    _write_cache,
    validate_latest_document,
)

INDEX_CONTRACT = "genshin-product-release-index-v1"
LIVE_PAIR_PROOF_FILENAME = "release-pair-live-signed.json"
PER_USER_UPDATER_RELATIVE = "updater/GenshinCalcUpdater.exe"
LEGACY_STABLE_INDEX = "https://aiur-wzy.github.io/gs_ai_calculator/latest.json"
STABLE_INDEX = "https://aiur-wzy.github.io/gs_ai_calculator/stable/latest.json"
STABLE_INDEX_V2 = "https://aiur-wzy.github.io/gs_ai_calculator/stable/v2/latest.json"
QUALIFIED_STABLE_INDEX = "https://aiur-wzy.github.io/gs_ai_calculator/stable/v3/latest.json"
_PRIOR_STABLE_INDEXES = {
    STABLE_INDEX: (LEGACY_STABLE_INDEX,),
    STABLE_INDEX_V2: (STABLE_INDEX, LEGACY_STABLE_INDEX),
    QUALIFIED_STABLE_INDEX: (STABLE_INDEX_V2, STABLE_INDEX, LEGACY_STABLE_INDEX),
}


def _invalid(reason: str = "product_release_index_invalid") -> None:
    raise ReleasePairDiscoveryError(reason)


def canonical(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _stamp(value: Any) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        _invalid()
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        _invalid()
    if stamp.utcoffset() is None:
        _invalid()
    return stamp.astimezone(UTC)


def _sequence(value: Any) -> int:
    if type(value) is not int or not 0 < value < 2**53:
        _invalid()
    return value


def _signature(value: Any) -> dict:
    if (not isinstance(value, dict) or set(value) != {"algorithm", "key_id", "value"}
            or any(not isinstance(item, str) or not 0 < len(item) <= 4096 for item in value.values())):
        _invalid("product_release_index_signature_invalid")
    return value


def validate_product_release_index(value: Any, *, channel: str, trust_verifier,
                                   now: datetime | None = None, for_install: bool = True) -> dict:
    """Validate a complete bounded index and all of its immutable signed choices."""
    if not isinstance(value, dict) or set(value) != {
        "contract", "schema_version", "channel", "index_sequence", "released_at", "expires_at",
        "latest_app_release_id", "latest_database_release_id", "apps", "pairs", "signature",
    } or value["contract"] != INDEX_CONTRACT or type(value["schema_version"]) is not int or value["schema_version"] != 1:
        _invalid()
    if value["channel"] != channel or channel not in {"stable", "dev"}:
        _invalid("product_release_index_channel_mismatch")
    _sequence(value["index_sequence"])
    released, expires = _stamp(value["released_at"]), _stamp(value["expires_at"])
    if released >= expires:
        _invalid()
    payload = {key: item for key, item in value.items() if key != "signature"}
    try:
        encoded = canonical(value)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ReleasePairDiscoveryError("product_release_index_invalid") from error
    if len(encoded) > MAX_DOCUMENT_BYTES:
        _invalid("release_pair_discovery_too_large")
    signature = _signature(value["signature"])
    if trust_verifier is None or trust_verifier(payload, signature) is not True:
        _invalid("product_release_index_signature_untrusted")
    if for_install and not released <= (now or datetime.now(UTC)) < expires:
        _invalid("product_release_index_not_current")

    if type(value["apps"]) is not list or not value["apps"]:
        _invalid()
    app_releases = {}
    for app in value["apps"]:
        if type(app) is not dict or set(app) != {"app_release_id", "release_sequence", "released_at"}:
            _invalid()
        app_id = app["app_release_id"]
        if not isinstance(app_id, str) or not 0 < len(app_id) <= 256 or app_id in app_releases:
            _invalid()
        _stamp(app["released_at"])
        app_releases[app_id] = _sequence(app["release_sequence"])
    if len(set(app_releases.values())) != len(app_releases):
        _invalid("product_release_app_sequence_conflict")

    if type(value["pairs"]) is not list or not value["pairs"]:
        _invalid()
    combinations, pairs, databases, app_artifacts = {}, {}, {}, {}
    database_qualifications = {}
    pair_sequences = set()
    for entry in value["pairs"]:
        document, verified = validate_latest_document(
            entry, expected_channel=channel, trust_verifier=trust_verifier, allow_unsigned_dev=False,
        )
        if not verified:
            _invalid("product_release_pair_signature_untrusted")
        pair, database = document["pair_manifest"], document["database_manifest"]
        app_id, db_id = pair["app"]["app_release_id"], database["database_release_id"]
        pair_id, sequence = pair["release_pair_id"], pair["pair_sequence"]
        if app_id not in app_releases or (app_id, db_id) in combinations or pair_id in pairs or sequence in pair_sequences:
            _invalid("product_release_pair_conflict")
        _stamp(pair["released_at"])
        _stamp(database["released_at"])
        app_artifact = (pair["app"]["app_version"], pair["app"]["artifact_manifest"], document["artifacts"]["app"])
        if app_artifacts.setdefault(app_id, app_artifact) != app_artifact:
            _invalid("product_release_app_artifact_conflict")
        database_artifact = (database, document["artifacts"]["database"])
        if databases.setdefault(db_id, database_artifact) != database_artifact:
            _invalid("product_release_database_artifact_conflict")
        qualification = document.get("database_qualification")
        if qualification is not None and database_qualifications.setdefault(db_id, qualification) != qualification:
            _invalid("product_release_database_qualification_conflict")
        combinations[app_id, db_id] = pair_id
        pairs[pair_id] = document
        pair_sequences.add(sequence)
    db_sequences = {identifier: item[0]["release_sequence"] for identifier, item in databases.items()}
    if len(set(db_sequences.values())) != len(db_sequences):
        _invalid("product_release_database_sequence_conflict")
    newest = (value["latest_app_release_id"], value["latest_database_release_id"])
    if any(not isinstance(identifier, str) for identifier in newest) or (
        newest != (max(app_releases, key=app_releases.get), max(db_sequences, key=db_sequences.get))
        or newest not in combinations
    ):
        _invalid("product_release_latest_pair_missing")
    return {"signed_index": value, "index_sha256": digest(value), "pairs": pairs,
            "combinations": combinations, "app_sequences": app_releases,
            "database_sequences": db_sequences, "latest": newest}


def first_install_option(index: dict, *, app_only: bool = False) -> dict:
    """Select the current signed App and database for a new profile."""
    pair_id = index["combinations"][index["latest"]]
    document = index["pairs"][pair_id]
    if document["artifacts"]["app"]["format"] != "app-zip":
        _invalid("product_release_first_install_artifact_unsupported")
    return {
        "document": document,
        "confirmation_digest": digest({"index_sha256": index["index_sha256"],
                                       "selected_pair": pair_id,
                                       "mode": "first_install_app_only" if app_only else "first_install"}),
    }


def release_options(index: dict, current_pair: dict, current_database: dict) -> list[dict]:
    """Offer only explicit, strictly forward, published compatibility combinations."""
    current = (current_pair["app"]["app_release_id"], current_database["database_release_id"])
    if current[0] not in index["app_sequences"]:
        return []
    installed_app_sequence = index["app_sequences"][current[0]]
    installed_db_sequence = current_database["release_sequence"]
    latest_app, latest_db = index["latest"]
    options = []
    for mode, combination in (("app_only", (latest_app, current[1])),
                              ("database_only", (current[0], latest_db)),
                              ("both", (latest_app, latest_db))):
        if combination == current:
            continue
        changes_app, changes_database = (left != right for left, right in zip(combination, current, strict=True))
        if mode == "both" and not (changes_app and changes_database):
            continue
        pair_id = index["combinations"].get(combination)
        if pair_id is None:
            continue
        document = index["pairs"][pair_id]
        target_pair = document["pair_manifest"]
        if not changes_app and (
            document["artifacts"]["app"]["format"] != "app-zip"
            or target_pair["app"]["app_version"] != current_pair["app"]["app_version"]
            or target_pair["app"]["artifact_manifest"] != current_pair["app"]["artifact_manifest"]
        ):
            continue
        if not changes_database and document["database_manifest"] != current_database:
            continue
        if (target_pair["pair_sequence"] <= current_pair["pair_sequence"]
                or (changes_app and index["app_sequences"][combination[0]] <= installed_app_sequence)
                or (changes_database and index["database_sequences"][combination[1]] <= installed_db_sequence)):
            continue
        selected = {"index_sha256": index["index_sha256"], "current_pair": current_pair,
                    "current_database": current_database, "selected_pair": pair_id, "mode": mode}
        options.append({"mode": mode, "confirmation_digest": digest(selected), "document": document,
                        "reuse_app": not changes_app, "reuse_database": not changes_database})
    return options


def _release_identities(index: dict) -> dict:
    identities: dict[str, dict] = {"app": {}, "database": {}, "pair": {}}
    for identifier, document in index["pairs"].items():
        pair = document["pair_manifest"]
        app = pair["app"]
        database = document["database_manifest"]
        app_bytes, database_bytes = ({key: value for key, value in document["artifacts"][kind].items() if key != "url"}
                                     for kind in ("app", "database"))
        identities["app"][app["app_release_id"]] = app["app_version"], app["artifact_manifest"], app_bytes
        identities["database"][database["database_release_id"]] = database, database_bytes
        identities["pair"][identifier] = pair
    return identities


def _check_index_progress(index: dict, previous: dict) -> None:
    sequence = index["signed_index"]["index_sequence"]
    previous_sequence = previous["signed_index"]["index_sequence"]
    if sequence < previous_sequence:
        _invalid("product_release_index_replay")
    if sequence == previous_sequence:
        if index["index_sha256"] != previous["index_sha256"]:
            _invalid("product_release_index_sequence_conflict")
        return

    for key in ("app_sequences", "database_sequences"):
        current, retained = index[key], previous[key]
        if max(current.values()) < max(retained.values()):
            _invalid("product_release_index_artifact_replay")
        sequence_owners = {number: identifier for identifier, number in retained.items()}
        if any(retained.get(identifier, number) != number or sequence_owners.get(number, identifier) != identifier
               for identifier, number in current.items()):
            _invalid("product_release_artifact_sequence_conflict")

    retained = _release_identities(previous)
    current = _release_identities(index)
    for kind, records in current.items():
        if any(retained[kind].get(identifier, record) != record for identifier, record in records.items()):
            _invalid("product_release_artifact_identity_conflict")
    pair_sequence_owners = {pair["pair_sequence"]: identifier for identifier, pair in retained["pair"].items()}
    if any(pair_sequence_owners.get(pair["pair_sequence"], identifier) != identifier
           for identifier, pair in current["pair"].items()):
        _invalid("product_release_artifact_sequence_conflict")


def discover_product_index(url: str, cache_path: Path, *, channel: str, trust_verifier) -> dict:
    """Keep the last verified index as a monotonic, re-verifiable replay guard."""
    cache = _load_cache(cache_path, expected_url=url, expected_channel=channel)
    try:
        status, body, etag = _fetch_https(url, etag=cache["etag"] if cache else None,
                                        timeout=DEFAULT_TIMEOUT_SECONDS)
    except (OSError, URLError, TimeoutError) as error:
        raise ReleasePairDiscoveryError("release_pair_discovery_network_unavailable") from error
    if status not in (200, 304) or (status == 304 and (cache is None or not cache["etag"])):
        _invalid("release_pair_discovery_http_status")
    index = None
    if status == 200:
        if len(body) > MAX_DOCUMENT_BYTES:
            _invalid("release_pair_discovery_too_large")
        try:
            value = json.loads(body)
        except (ValueError, RecursionError) as error:
            raise ReleasePairDiscoveryError("product_release_index_invalid") from error
        if not isinstance(value, dict) or value.get("contract") != INDEX_CONTRACT:
            if cache is not None:
                _invalid("product_release_index_legacy_replay")
            _invalid("product_release_index_not_published")
        index = validate_product_release_index(value, channel=channel, trust_verifier=trust_verifier)
    try:
        with file_lock(cache_path.with_suffix(".lock"), timeout=5):
            current = _load_cache(cache_path, expected_url=url, expected_channel=channel)
            if index is None:
                if current is None:
                    _invalid("release_pair_discovery_304_without_cache")
                return validate_product_release_index(current["document"], channel=channel,
                                                      trust_verifier=trust_verifier)
            # The legacy updater kept the bridge receipt in this same file.
            # Its ETag belongs to another URL, but its signed history still
            # constrains the first response from the successor endpoint.
            if current is None and channel == "stable":
                for previous_url in _PRIOR_STABLE_INDEXES.get(url, ()):
                    current = _load_cache(cache_path, expected_url=previous_url, expected_channel=channel)
                    if current is not None:
                        break
            if current is not None:
                previous = validate_product_release_index(current["document"], channel=channel,
                                                          trust_verifier=trust_verifier, for_install=False)
                _check_index_progress(index, previous)
            _write_cache(cache_path, {"contract": CACHE_CONTRACT, "schema_version": CACHE_SCHEMA_VERSION,
                                     "url": url, "channel": channel, "etag": etag,
                                     "fetched_at": datetime.now(UTC).isoformat(), "document": index["signed_index"]})
    except LockBusyError as error:
        raise ReleasePairDiscoveryError("product_release_index_cache_busy") from error
    except OSError as error:
        raise ReleasePairDiscoveryError("product_release_index_cache_unavailable") from error
    return index


def validate_live_pair_proof(value: Any, pair: dict, database: dict, *, trust_verifier) -> dict:
    """The installed pair is authorized by its detached signed envelope offline."""
    document, verified = validate_latest_document(value, expected_channel=pair["channel"],
                                                  trust_verifier=trust_verifier, allow_unsigned_dev=False)
    if not verified or document["pair_manifest"] != pair or document["database_manifest"] != database:
        _invalid("product_release_live_pair_proof_invalid")
    return document
