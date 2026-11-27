"""Read-only discovery of a project-published App/database release pair.

This module deliberately stops at discovery.  It never downloads an App or
database artifact, never replaces the active installation, and never treats a
raw game-data upstream commit as a project release.  A caller may pass the
validated manifests to the profile inspector after applying its own update
policy.
"""

from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any
from uuid import uuid4

from genshin_db.release_profile import (
    DatabaseReleaseError,
    release_pair_matches_database_manifest,
    validate_database_release_manifest,
    validate_release_pair_manifest,
)

DISCOVERY_CONTRACT = "genshin-release-pair-latest-v2"
DISCOVERY_SCHEMA_VERSION = 2
QUALIFIED_DISCOVERY_CONTRACT = "genshin-release-pair-latest-v3"
QUALIFIED_DISCOVERY_SCHEMA_VERSION = 3
CACHE_CONTRACT = "genshin-release-pair-discovery-cache-v1"
CACHE_SCHEMA_VERSION = 1
RESULT_CONTRACT = "genshin-release-pair-discovery-result-v1"
DEFAULT_TIMEOUT_SECONDS = 3.0
MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
# The cache adds local metadata to a document that may fill the wire budget.
MAX_CACHE_BYTES = MAX_DOCUMENT_BYTES + 128 * 1024
MAX_DECLARED_ARTIFACT_BYTES = 8 * 1024 * 1024 * 1024
MAX_DECLARED_APP_UNPACKED_BYTES = 16 * 1024 * 1024 * 1024
MAX_DECLARED_APP_FILES = 100_000

TrustVerifier = Callable[[Mapping[str, Any], Mapping[str, Any]], bool]


class ReleasePairDiscoveryError(ValueError):
    """Stable, non-secret error raised for invalid discovery inputs."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlparse(newurl)
        if target.scheme.lower() != "https":
            raise ReleasePairDiscoveryError("release_pair_redirect_not_https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _read_limited(handle, *, limit: int = MAX_DOCUMENT_BYTES) -> bytes:
    body = handle.read(limit + 1)
    if len(body) > limit:
        raise ReleasePairDiscoveryError("release_pair_discovery_too_large")
    return body


def _validate_signature(value: Any) -> dict | None:
    if value is None:
        return None
    if type(value) is not dict or set(value) != {"algorithm", "key_id", "value"}:
        raise ReleasePairDiscoveryError("release_pair_signature_invalid")
    if any(type(value[field]) is not str or not value[field] for field in value):
        raise ReleasePairDiscoveryError("release_pair_signature_invalid")
    if any(len(value[field]) > 4096 for field in value):
        raise ReleasePairDiscoveryError("release_pair_signature_invalid")
    return copy.deepcopy(value)


def _validate_artifact(value: Any, *, kind: str) -> dict[str, Any]:
    """Validate a signed download locator without dereferencing it.

    App packages are zip archives containing the complete application tree;
    database packages are complete SQLite files.  The archive digest is
    intentionally distinct from the App's internal PACKAGE-FILES manifest
    digest: both identities are rechecked by the transport/updater boundary.
    """

    required = {"url", "sha256", "size_bytes", "format"}
    artifact_format = value.get("format") if isinstance(value, dict) else None
    if kind == "app":
        required |= {"unpacked_size_bytes", "unpacked_files"}
    elif artifact_format == "sqlite-gzip":
        required |= {"unpacked_size_bytes", "unpacked_sha256"}
    if type(value) is not dict or set(value) != required:
        raise ReleasePairDiscoveryError("release_pair_artifact_locator_invalid")
    allowed_formats = {"zip", "app-zip"} if kind == "app" else {"sqlite", "sqlite-gzip"}
    parsed = urllib.parse.urlparse(value["url"] if type(value["url"]) is str else "")
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or type(artifact_format) is not str
        or artifact_format not in allowed_formats
    ):
        raise ReleasePairDiscoveryError("release_pair_artifact_locator_invalid")
    digest = value["sha256"]
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise ReleasePairDiscoveryError("release_pair_artifact_locator_invalid")
    size = value["size_bytes"]
    if type(size) is not int or not 0 < size <= MAX_DECLARED_ARTIFACT_BYTES:
        raise ReleasePairDiscoveryError("release_pair_artifact_locator_invalid")
    if kind == "app" and (
        type(value["unpacked_size_bytes"]) is not int
        or not 0 < value["unpacked_size_bytes"] <= MAX_DECLARED_APP_UNPACKED_BYTES
        or type(value["unpacked_files"]) is not int
        or not 0 < value["unpacked_files"] <= MAX_DECLARED_APP_FILES
    ):
        raise ReleasePairDiscoveryError("release_pair_artifact_locator_invalid")
    if artifact_format == "sqlite-gzip" and (
        type(value["unpacked_size_bytes"]) is not int
        or not 0 < value["unpacked_size_bytes"] <= MAX_DECLARED_ARTIFACT_BYTES
        or type(value["unpacked_sha256"]) is not str
        or len(value["unpacked_sha256"]) != 64
        or any(character not in "0123456789abcdef" for character in value["unpacked_sha256"])
    ):
        raise ReleasePairDiscoveryError("release_pair_artifact_locator_invalid")
    return copy.deepcopy(value)


def _document_sha256(value: Mapping[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(_canonical(value)).hexdigest()


def validate_latest_document(
    value: Any,
    *,
    expected_channel: str,
    trust_verifier: TrustVerifier | None,
    allow_unsigned_dev: bool,
) -> tuple[dict, bool]:
    """Validate a project release envelope and return its trust decision.

    Cryptographic policy intentionally remains injected: no algorithm is
    silently accepted merely because the envelope names it.  Stable discovery
    therefore remains informational until the App supplies a pinned verifier.
    """

    fields = {
        "contract",
        "schema_version",
        "channel",
        "pair_manifest",
        "database_manifest",
        "artifacts",
        "signature",
    }
    if type(value) is not dict or type(value.get("schema_version")) is not int:
        raise ReleasePairDiscoveryError("release_pair_discovery_document_invalid")
    envelope = (value.get("contract"), value.get("schema_version"))
    qualified = envelope == (QUALIFIED_DISCOVERY_CONTRACT, QUALIFIED_DISCOVERY_SCHEMA_VERSION)
    if not qualified and envelope != (DISCOVERY_CONTRACT, DISCOVERY_SCHEMA_VERSION):
        raise ReleasePairDiscoveryError("release_pair_discovery_document_unsupported")
    if set(value) != (fields | {"database_qualification"} if qualified else fields):
        raise ReleasePairDiscoveryError("release_pair_discovery_document_invalid")
    if expected_channel not in {"stable", "dev"} or value["channel"] != expected_channel:
        raise ReleasePairDiscoveryError("release_pair_discovery_channel_mismatch")
    try:
        pair = validate_release_pair_manifest(value["pair_manifest"])
        database = validate_database_release_manifest(value["database_manifest"])
    except DatabaseReleaseError as error:
        raise ReleasePairDiscoveryError(error.reason) from error
    if pair["channel"] != expected_channel or database["channel"] != expected_channel:
        raise ReleasePairDiscoveryError("release_pair_discovery_channel_mismatch")
    try:
        linked = release_pair_matches_database_manifest(pair, database)
    except DatabaseReleaseError as error:
        raise ReleasePairDiscoveryError(error.reason) from error
    if not linked:
        raise ReleasePairDiscoveryError("release_pair_discovery_binding_mismatch")

    artifacts_value = value["artifacts"]
    if type(artifacts_value) is not dict or set(artifacts_value) != {"app", "database"}:
        raise ReleasePairDiscoveryError("release_pair_artifacts_invalid")
    artifacts = {
        "app": _validate_artifact(artifacts_value["app"], kind="app"),
        "database": _validate_artifact(
            artifacts_value["database"], kind="database"
        ),
    }
    database_identity = database["database"]
    artifact = artifacts["database"]
    database_hash = artifact.get("unpacked_sha256", artifact["sha256"])
    database_bytes = artifact.get("unpacked_size_bytes", artifact["size_bytes"])
    if database_hash != database_identity["sha256"] or database_bytes != database_identity["size_bytes"]:
        raise ReleasePairDiscoveryError("release_pair_database_artifact_binding_mismatch")

    signature = _validate_signature(value["signature"])
    signed_payload = {
        "contract": envelope[0],
        "schema_version": envelope[1],
        "channel": expected_channel,
        "pair_manifest": pair,
        "database_manifest": database,
        "artifacts": artifacts,
    }
    if qualified:
        from genshin_db.release_qualification import load_database_install_qualification

        if expected_channel != "stable" or artifacts["app"]["format"] != "app-zip":
            raise ReleasePairDiscoveryError("release_pair_database_qualification_unsupported")
        try:
            load_database_install_qualification(value["database_qualification"], database)
        except (ValueError, DatabaseReleaseError) as error:
            raise ReleasePairDiscoveryError("release_pair_database_qualification_invalid") from error
        signed_payload["database_qualification"] = copy.deepcopy(value["database_qualification"])
    trusted = False
    if signature is not None and trust_verifier is not None:
        try:
            trusted = trust_verifier(copy.deepcopy(signed_payload), signature) is True
        except Exception:  # noqa: BLE001 - verifier failures are untrusted, not fatal
            trusted = False
    elif expected_channel == "dev" and allow_unsigned_dev and signature is None:
        trusted = True
    return {
        **signed_payload,
        "signature": signature,
    }, trusted


def _validate_cache(value: Any, *, expected_url: str, expected_channel: str) -> dict:
    if type(value) is not dict or set(value) != {
        "contract",
        "schema_version",
        "url",
        "channel",
        "etag",
        "fetched_at",
        "document",
    }:
        raise ReleasePairDiscoveryError("release_pair_discovery_cache_invalid")
    if (
        value["contract"] != CACHE_CONTRACT
        or value["schema_version"] != CACHE_SCHEMA_VERSION
        or value["url"] != expected_url
        or value["channel"] != expected_channel
        or type(value["fetched_at"]) is not str
        or not value["fetched_at"]
        or (value["etag"] is not None and type(value["etag"]) is not str)
        or type(value["document"]) is not dict
    ):
        raise ReleasePairDiscoveryError("release_pair_discovery_cache_invalid")
    return copy.deepcopy(value)


def _load_cache(path: Path, *, expected_url: str, expected_channel: str) -> dict | None:
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            raw = _read_limited(handle, limit=MAX_CACHE_BYTES)
        return _validate_cache(
            json.loads(raw.decode("utf-8")),
            expected_url=expected_url,
            expected_channel=expected_channel,
        )
    except (OSError, ValueError, RecursionError):
        return None


def _write_cache(path: Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    raw = _canonical(value)
    if len(raw) > MAX_CACHE_BYTES:
        raise ReleasePairDiscoveryError("release_pair_discovery_cache_too_large")
    try:
        with temporary.open("xb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def _fetch_https(url: str, *, etag: str | None, timeout: float) -> tuple[int, bytes, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username is not None:
        raise ReleasePairDiscoveryError("release_pair_discovery_url_invalid")
    headers = {
        "Accept": "application/json",
        "User-Agent": "GenshinCalc-release-pair-discovery/1",
    }
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers, method="GET")
    opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:
            final_url = response.geturl()
            if urllib.parse.urlparse(final_url).scheme.lower() != "https":
                raise ReleasePairDiscoveryError("release_pair_redirect_not_https")
            return response.status, _read_limited(response), response.headers.get("ETag")
    except urllib.error.HTTPError as error:
        if error.code == 304:
            with error:
                return 304, b"", error.headers.get("ETag")
        raise
    except HTTPException as error:
        raise OSError("invalid release metadata response") from error


def _result(
    *,
    status: str,
    source: str,
    checked_at: str,
    fetched_at: str | None = None,
    etag: str | None = None,
    document: Mapping[str, Any] | None = None,
    trusted: bool = False,
    reason: str | None = None,
) -> dict:
    pair = document.get("pair_manifest") if document is not None else None
    database = document.get("database_manifest") if document is not None else None
    signature = document.get("signature") if document is not None else None
    document_digest = _document_sha256(document) if document is not None else None
    return {
        "contract": RESULT_CONTRACT,
        "status": status,
        "source": source,
        "checked_at": checked_at,
        "fetched_at": fetched_at,
        "etag": etag,
        "authoritative": trusted,
        "can_offer_update": trusted and document is not None,
        "reason": reason,
        "release_pair_id": pair.get("release_pair_id") if pair else None,
        "pair_sequence": pair.get("pair_sequence") if pair else None,
        "database_release_id": (
            database.get("database_release_id") if database else None
        ),
        "pair_manifest": copy.deepcopy(pair),
        "database_manifest": copy.deepcopy(database),
        "artifacts": copy.deepcopy(document.get("artifacts")) if document else None,
        # Kept for the privileged external helper to re-verify after the GUI
        # exits.  GUI bridges expose only the summary fields, never this blob.
        "signed_document": copy.deepcopy(document) if document else None,
        "trust": (
            {
                "source": "signed_discovery",
                "algorithm": signature.get("algorithm"),
                "key_id": signature.get("key_id"),
            }
            if trusted and isinstance(signature, dict)
            else None
        ),
        # This exact digest is the confirmation token used by the downloader.
        # Re-fetching a different signed document invalidates prior consent.
        "document_sha256": document_digest,
        "confirmation_digest": document_digest,
    }


def discover_latest_release_pair(
    *,
    url: str | None,
    cache_path: Path,
    channel: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    trust_verifier: TrustVerifier | None = None,
    allow_unsigned_dev: bool = False,
    test_fixture_path: Path | None = None,
    checked_at: str | None = None,
) -> dict:
    """Discover the latest published pair without downloading its artifacts.

    ``test_fixture_path`` is the only non-HTTPS source and is deliberately a
    separate argument.  It cannot be combined with a URL and is accepted only
    for the dev channel with ``allow_unsigned_dev=True``.
    """

    check_time = checked_at or _now()
    if type(timeout) not in (int, float) or not 0 < float(timeout) <= 15:
        raise ReleasePairDiscoveryError("release_pair_discovery_timeout_invalid")
    if channel not in {"stable", "dev"}:
        raise ReleasePairDiscoveryError("release_pair_discovery_channel_invalid")
    if test_fixture_path is not None:
        if url is not None or channel != "dev" or not allow_unsigned_dev:
            raise ReleasePairDiscoveryError("release_pair_discovery_test_source_forbidden")
        try:
            raw = Path(test_fixture_path).read_bytes()
            if len(raw) > MAX_DOCUMENT_BYTES:
                raise ReleasePairDiscoveryError("release_pair_discovery_too_large")
            value = json.loads(raw.decode("utf-8"))
            document, trusted = validate_latest_document(
                value,
                expected_channel=channel,
                trust_verifier=trust_verifier,
                allow_unsigned_dev=allow_unsigned_dev,
            )
        except ReleasePairDiscoveryError:
            raise
        except (OSError, ValueError, RecursionError) as error:
            raise ReleasePairDiscoveryError(
                "release_pair_discovery_test_source_invalid"
            ) from error
        return _result(
            status="available" if trusted else "untrusted",
            source="test_fixture",
            checked_at=check_time,
            fetched_at=check_time,
            document=document,
            trusted=trusted,
            reason=None if trusted else "release_pair_signature_untrusted",
        )

    if not url:
        return _result(
            status="disabled",
            source="none",
            checked_at=check_time,
            reason="release_pair_discovery_url_unconfigured",
        )
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username is not None:
        raise ReleasePairDiscoveryError("release_pair_discovery_url_invalid")

    cache = _load_cache(Path(cache_path), expected_url=url, expected_channel=channel)
    etag = cache.get("etag") if cache else None
    try:
        status, body, response_etag = _fetch_https(
            url,
            etag=etag,
            timeout=float(timeout),
        )
        if status == 304:
            if cache is None:
                return _result(
                    status="rejected",
                    source="remote",
                    checked_at=check_time,
                    reason="release_pair_discovery_304_without_cache",
                )
            document, trusted = validate_latest_document(
                cache["document"],
                expected_channel=channel,
                trust_verifier=trust_verifier,
                allow_unsigned_dev=False,
            )
            return _result(
                status="not_modified" if trusted else "untrusted",
                source="not_modified_cache",
                checked_at=check_time,
                fetched_at=cache["fetched_at"],
                etag=response_etag or cache["etag"],
                document=document,
                trusted=trusted,
                reason=None if trusted else "release_pair_signature_untrusted",
            )
        if status != 200:
            raise ReleasePairDiscoveryError("release_pair_discovery_http_status")
        try:
            value = json.loads(body.decode("utf-8"))
        except (ValueError, RecursionError) as error:
            raise ReleasePairDiscoveryError(
                "release_pair_discovery_document_invalid"
            ) from error
        document, trusted = validate_latest_document(
            value,
            expected_channel=channel,
            trust_verifier=trust_verifier,
            allow_unsigned_dev=False,
        )
        cache_value = {
            "contract": CACHE_CONTRACT,
            "schema_version": CACHE_SCHEMA_VERSION,
            "url": url,
            "channel": channel,
            "etag": response_etag,
            "fetched_at": check_time,
            "document": document,
        }
        if trusted:
            _write_cache(Path(cache_path), cache_value)
        return _result(
            status="available" if trusted else "untrusted",
            source="remote",
            checked_at=check_time,
            fetched_at=check_time,
            etag=response_etag,
            document=document,
            trusted=trusted,
            reason=None if trusted else "release_pair_signature_untrusted",
        )
    except ReleasePairDiscoveryError as error:
        # Invalid or untrusted live content is an active rejection.  Falling
        # back to a cache here would hide a server-side integrity failure.
        return _result(
            status="rejected",
            source="remote",
            checked_at=check_time,
            reason=error.reason,
        )
    except (OSError, TimeoutError, urllib.error.URLError, urllib.error.HTTPError):
        if cache is None:
            return _result(
                status="unavailable",
                source="none",
                checked_at=check_time,
                reason="release_pair_discovery_network_unavailable",
            )
        try:
            document, trusted = validate_latest_document(
                cache["document"],
                expected_channel=channel,
                trust_verifier=trust_verifier,
                allow_unsigned_dev=False,
            )
        except ReleasePairDiscoveryError as error:
            return _result(
                status="rejected",
                source="cache",
                checked_at=check_time,
                reason=error.reason,
            )
        return _result(
            status="offline_cache" if trusted else "untrusted",
            source="offline_cache",
            checked_at=check_time,
            fetched_at=cache["fetched_at"],
            etag=cache["etag"],
            document=document,
            trusted=trusted,
            reason=(
                "release_pair_discovery_network_unavailable"
                if trusted
                else "release_pair_signature_untrusted"
            ),
        )
