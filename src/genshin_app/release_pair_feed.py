"""Offline publisher for App/database release-pair feed artifacts.

The publisher has two deliberately separate phases:

``prepare`` validates the complete App/database pair, emits deterministic
download artifacts and writes the exact canonical payload to be signed by an
external release-signing system.  It never owns a private key.

``finalize`` accepts a detached signature object, verifies it with the public
keys compiled into the App, and only then publishes ``latest.json`` locally.
Uploading remains an operator/deployment concern outside this module.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import shutil
import stat
import urllib.parse
import zipfile
import zlib
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from genshin_app.package_integrity import verify_package_artifact
from genshin_app.product_release_index import PER_USER_UPDATER_RELATIVE
from genshin_app.release_pair_discovery import (
    DISCOVERY_CONTRACT,
    DISCOVERY_SCHEMA_VERSION,
    MAX_DOCUMENT_BYTES,
    ReleasePairDiscoveryError,
    validate_latest_document,
)
from genshin_app.release_pair_trust import build_trust_verifier
from genshin_db.release_profile import (
    PAIR_MANIFEST_FILENAME,
    DatabaseReleaseError,
    load_database_release_manifest,
    load_release_pair_manifest,
    read_database_identity,
    release_pair_matches_database_manifest,
)

FEED_RECEIPT_CONTRACT = "genshin-release-pair-feed-receipt-v1"
FEED_RECEIPT_SCHEMA_VERSION = 1
UNSIGNED_DOCUMENT_FILENAME = "latest.unsigned.json"
SIGNING_PAYLOAD_FILENAME = "release-pair-signing-payload.canonical.json"
FINAL_DOCUMENT_FILENAME = "latest.json"
FEED_RECEIPT_FILENAME = "release-pair-feed-receipt.json"
MAX_SIGNATURE_BYTES = 64 * 1024
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


class ReleasePairFeedError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _fail(reason: str) -> None:
    raise ReleasePairFeedError(reason)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_atomic(path: Path, raw: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    _write_atomic(
        path,
        (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        ),
    )


def _reparse(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        junction = getattr(path, "is_junction", None)
        if callable(junction) and junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError:
        return True


def _safe_members(root: Path) -> list[tuple[str, Path]]:
    members: list[tuple[str, Path]] = []
    folded: set[str] = set()
    for directory, names, files in os.walk(root, followlinks=False):
        current = Path(directory)
        if _reparse(current):
            _fail("release_pair_feed_app_link_forbidden")
        for name in names:
            if _reparse(current / name):
                _fail("release_pair_feed_app_link_forbidden")
        for name in files:
            path = current / name
            if _reparse(path) or not path.is_file():
                _fail("release_pair_feed_app_file_invalid")
            relative = path.relative_to(root).as_posix()
            parsed = PurePosixPath(relative)
            if (
                parsed.is_absolute()
                or any(part in {"", ".", ".."} or ":" in part for part in parsed.parts)
                or "\\" in relative
                or relative.casefold() in folded
            ):
                _fail("release_pair_feed_app_path_invalid")
            folded.add(relative.casefold())
            members.append((relative, path))
    return sorted(members)


def _deterministic_zip(app_root: Path, destination: Path) -> None:
    members = _safe_members(app_root)
    if not members:
        _fail("release_pair_feed_app_empty")
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(
            temporary,
            "x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            strict_timestamps=True,
        ) as archive:
            for relative, path in members:
                info = zipfile.ZipInfo(relative, date_time=ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.flag_bits = 0x800
                with path.open("rb") as source, archive.open(info, "w") as output:
                    shutil.copyfileobj(source, output, 4 * 1024 * 1024)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _filename(value: str, suffix: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 128
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for character in value)
    ):
        _fail("release_pair_feed_identifier_invalid")
    return f"{value}{suffix}"


def _base_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme.lower() != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        _fail("release_pair_feed_base_url_invalid")
    return value.rstrip("/")


def _artifact_url(base: str, filename: str) -> str:
    return f"{base}/{urllib.parse.quote(filename, safe='._-')}"


def _read_json(path: Path, reason: str, *, maximum: int = MAX_DOCUMENT_BYTES) -> dict:
    try:
        with path.open("rb") as source:
            raw = source.read(maximum + 1)
        if not 2 <= len(raw) <= maximum:
            _fail(reason)
        value = json.loads(raw.decode("utf-8"))
    except ReleasePairFeedError:
        raise
    except (OSError, ValueError, RecursionError) as error:
        raise ReleasePairFeedError(reason) from error
    if type(value) is not dict:
        _fail(reason)
    return value


def _validate_feed_receipt(feed: Path) -> dict[str, Any]:
    receipt = _read_json(
        feed / FEED_RECEIPT_FILENAME,
        "release_pair_feed_receipt_invalid",
    )
    if (
        receipt.get("contract") != FEED_RECEIPT_CONTRACT
        or receipt.get("schema_version") != FEED_RECEIPT_SCHEMA_VERSION
        or type(receipt.get("files")) is not dict
        or type(receipt.get("receipt_sha256")) is not str
        or type(receipt.get("signing_payload_sha256")) is not str
    ):
        _fail("release_pair_feed_receipt_invalid")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if hashlib.sha256(_canonical(body)).hexdigest() != receipt["receipt_sha256"]:
        _fail("release_pair_feed_receipt_invalid")
    expected = receipt["files"]
    actual_names = {path.name for path in feed.iterdir() if path.name != FEED_RECEIPT_FILENAME}
    if set(expected) != actual_names:
        _fail("release_pair_feed_file_set_mismatch")
    for name, identity in expected.items():
        if (
            type(name) is not str
            or Path(name).name != name
            or "\\" in name
            or "/" in name
            or ":" in name
            or type(identity) is not dict
            or set(identity) != {"sha256", "size_bytes"}
            or type(identity["sha256"]) is not str
            or len(identity["sha256"]) != 64
            or type(identity["size_bytes"]) is not int
            or identity["size_bytes"] < 0
        ):
            _fail("release_pair_feed_receipt_invalid")
        path = feed / name
        if (
            _reparse(path)
            or not path.is_file()
            or path.stat().st_size != identity["size_bytes"]
            or _sha256(path) != identity["sha256"]
        ):
            _fail("release_pair_feed_file_identity_mismatch")
    return receipt


def _check_signed_artifact_binding(receipt: dict, document: dict) -> None:
    pair = document["pair_manifest"]
    database = document["database_manifest"]
    artifacts = document["artifacts"]
    independent_app = artifacts["app"]["format"] == "app-zip"
    app_id = pair["app"]["app_release_id"] if independent_app else pair["release_pair_id"]
    suffix = ".sqlite.gz" if artifacts["database"]["format"] == "sqlite-gzip" else ".sqlite"
    filenames = {"app": _filename(app_id, ".app.zip"),
                 "database": _filename(database["database_release_id"], suffix)}
    if (receipt.get("artifacts") != artifacts or receipt.get("channel") != document["channel"]
            or receipt.get("release_pair_id") != pair["release_pair_id"]
            or receipt.get("database_release_id") != database["database_release_id"]
            or set(receipt["files"]) != {*filenames.values(), UNSIGNED_DOCUMENT_FILENAME, SIGNING_PAYLOAD_FILENAME}):
        _fail("release_pair_feed_signed_artifact_mismatch")
    for kind, filename in filenames.items():
        signed = artifacts[kind]
        if receipt["files"][filename] != {key: signed[key] for key in ("sha256", "size_bytes")}:
            _fail("release_pair_feed_signed_artifact_mismatch")


def _validate_inputs(
    app_root: Path,
    database: Path,
    pair_manifest_path: Path,
    database_manifest_path: Path,
    *,
    independent_app: bool = False,
) -> tuple[Path, Path, dict, dict, dict]:
    try:
        app = app_root.resolve(strict=True)
        db = database.resolve(strict=True)
    except OSError as error:
        raise ReleasePairFeedError("release_pair_feed_input_missing") from error
    if (
        not app.is_dir()
        or _reparse(app)
        or not db.is_file()
        or _reparse(db)
        or app == db
        or app in db.parents
    ):
        _fail("release_pair_feed_input_invalid")
    try:
        pair = load_release_pair_manifest(pair_manifest_path)
        database_manifest = load_database_release_manifest(database_manifest_path)
        if not release_pair_matches_database_manifest(pair, database_manifest):
            _fail("release_pair_feed_manifest_binding_mismatch")
        app_verification = verify_package_artifact(app)
        identity = read_database_identity(db)
    except ReleasePairFeedError:
        raise
    except DatabaseReleaseError as error:
        raise ReleasePairFeedError(error.reason) from error
    except Exception as error:
        reason = getattr(error, "code", "release_pair_feed_app_verification_failed")
        raise ReleasePairFeedError(reason) from error
    if (
        app_verification["manifest_sha256"]
        != pair["app"]["artifact_manifest"]["sha256"]
        or identity["sha256"] != database_manifest["database"]["sha256"]
        or identity["size_bytes"] != database_manifest["database"]["size_bytes"]
        or identity["meta_fingerprint"]
        != database_manifest["database"]["meta_fingerprint"]
    ):
        _fail("release_pair_feed_artifact_binding_mismatch")

    if independent_app:
        if (app / "_internal" / "bundled").exists():
            _fail("release_pair_independent_app_contains_bundled_data")
        if not (app / PER_USER_UPDATER_RELATIVE).is_file():
            _fail("release_pair_updater_current_user_missing")
        return app, db, pair, database_manifest, app_verification

    # The detached pair descriptor is excluded from PACKAGE-FILES by design,
    # so validate it separately before archiving the whole App tree.
    packaged_pair = app / "_internal" / "bundled" / PAIR_MANIFEST_FILENAME
    packaged_database_manifest = (
        app / "_internal" / "bundled" / database_manifest_path.name
    )
    try:
        if (
            load_release_pair_manifest(packaged_pair) != pair
            or load_database_release_manifest(packaged_database_manifest)
            != database_manifest
        ):
            _fail("release_pair_feed_packaged_manifest_mismatch")
    except DatabaseReleaseError as error:
        raise ReleasePairFeedError(error.reason) from error
    return app, db, pair, database_manifest, app_verification


def _accepted_database_artifact(
    pair_path: Path,
    archive: Path,
    *,
    pair: dict,
    database_manifest: dict,
    trust_keys: Sequence[Mapping[str, Any]],
    requested_url: str | None,
) -> dict:
    if _reparse(pair_path) or not pair_path.is_file() or _reparse(archive) or not archive.is_file():
        _fail("release_pair_feed_database_reuse_input_invalid")
    verifier = build_trust_verifier(trust_keys)
    if verifier is None:
        _fail("release_pair_feed_trust_keys_unconfigured")
    proof = _read_json(pair_path, "release_pair_feed_database_reuse_proof_invalid")
    try:
        signed, trusted = validate_latest_document(
            proof, expected_channel=pair["channel"], trust_verifier=verifier, allow_unsigned_dev=False,
        )
    except ReleasePairDiscoveryError as error:
        raise ReleasePairFeedError("release_pair_feed_database_reuse_proof_invalid") from error
    if not trusted:
        _fail("release_pair_feed_database_reuse_proof_untrusted")
    artifact = signed["artifacts"]["database"]
    if (signed["database_manifest"] != database_manifest
            or signed["pair_manifest"]["pair_sequence"] >= pair["pair_sequence"]
            or artifact["format"] != "sqlite-gzip"):
        _fail("release_pair_feed_database_reuse_manifest_mismatch")
    if requested_url is not None and requested_url != artifact["url"]:
        _fail("release_pair_feed_database_reuse_url_mismatch")
    if archive.stat().st_size != artifact["size_bytes"] or _sha256(archive) != artifact["sha256"]:
        _fail("release_pair_feed_database_reuse_archive_mismatch")

    expanded = hashlib.sha256()
    remaining = artifact["unpacked_size_bytes"]
    try:
        with gzip.open(archive, "rb") as stream:
            while chunk := stream.read(min(4 * 1024 * 1024, remaining + 1)):
                remaining -= len(chunk)
                if remaining < 0:
                    _fail("release_pair_feed_database_reuse_expanded_mismatch")
                expanded.update(chunk)
    except (OSError, EOFError, zlib.error) as error:
        raise ReleasePairFeedError("release_pair_feed_database_reuse_archive_invalid") from error
    if remaining != 0 or expanded.hexdigest() != artifact["unpacked_sha256"]:
        _fail("release_pair_feed_database_reuse_expanded_mismatch")
    return artifact


def prepare_release_pair_feed(
    *,
    app_root: Path,
    database: Path,
    pair_manifest_path: Path,
    database_manifest_path: Path,
    base_url: str,
    output_directory: Path,
    independent_app: bool = False,
    database_base_url: str | None = None,
    accepted_database_source_pair: Path | None = None,
    accepted_database_archive: Path | None = None,
    trust_keys: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Create an exact signing payload, retaining an accepted database gzip when supplied."""

    reuse = accepted_database_source_pair is not None or accepted_database_archive is not None
    if reuse and (not independent_app or accepted_database_source_pair is None or accepted_database_archive is None):
        _fail("release_pair_feed_database_reuse_inputs_required")
    output = Path(output_directory)
    if output.exists():
        _fail("release_pair_feed_output_exists")
    parent = output.parent.resolve(strict=True)
    temporary = parent / f".{output.name}.{uuid4().hex}.candidate"
    temporary.mkdir()
    try:
        app, db, pair, database_manifest, before_verification = _validate_inputs(
            Path(app_root),
            Path(database),
            Path(pair_manifest_path),
            Path(database_manifest_path),
            **({"independent_app": True} if independent_app else {}),
        )
        base = _base_url(base_url)
        database_base = _base_url(database_base_url) if database_base_url is not None else base
        app_id = pair["app"]["app_release_id"] if independent_app else pair["release_pair_id"]
        app_name = _filename(app_id, ".app.zip")
        database_name = _filename(database_manifest["database_release_id"], ".sqlite.gz" if independent_app else ".sqlite")
        app_artifact = temporary / app_name
        database_artifact = temporary / database_name
        reused_descriptor = None
        if reuse:
            reused_descriptor = _accepted_database_artifact(
                Path(accepted_database_source_pair), Path(accepted_database_archive),
                pair=pair, database_manifest=database_manifest, trust_keys=trust_keys,
                requested_url=_artifact_url(database_base, database_name) if database_base_url is not None else None,
            )
        _deterministic_zip(app, app_artifact)
        if reused_descriptor is not None:
            shutil.copyfile(Path(accepted_database_archive), database_artifact)
            if (database_artifact.stat().st_size != reused_descriptor["size_bytes"]
                    or _sha256(database_artifact) != reused_descriptor["sha256"]):
                _fail("release_pair_feed_database_reuse_archive_mismatch")
        elif independent_app:
            copied_sha = hashlib.sha256()
            copied_size = 0
            with (db.open("rb") as source, database_artifact.open("xb") as packed,
                  gzip.GzipFile(filename="", mode="wb", fileobj=packed, mtime=0) as compressed):
                while chunk := source.read(4 * 1024 * 1024):
                    compressed.write(chunk)
                    copied_sha.update(chunk)
                    copied_size += len(chunk)
            if (copied_sha.hexdigest() != database_manifest["database"]["sha256"]
                    or copied_size != database_manifest["database"]["size_bytes"]):
                _fail("release_pair_feed_database_copy_mismatch")
        else:
            shutil.copyfile(db, database_artifact)

        # Detect source drift during the potentially long ZIP/database copy.
        after = verify_package_artifact(app)
        if after != before_verification or _sha256(db) != database_manifest["database"]["sha256"]:
            _fail("release_pair_feed_source_changed")
        if not independent_app and (
            _sha256(database_artifact) != database_manifest["database"]["sha256"]
            or database_artifact.stat().st_size
            != database_manifest["database"]["size_bytes"]
        ):
            _fail("release_pair_feed_database_copy_mismatch")

        unpacked_members = _safe_members(app)
        unpacked_size = sum(path.stat().st_size for _relative, path in unpacked_members)

        artifacts = {
            "app": {
                "url": _artifact_url(base, app_name),
                "sha256": _sha256(app_artifact),
                "size_bytes": app_artifact.stat().st_size,
                "format": "app-zip" if independent_app else "zip",
                "unpacked_size_bytes": unpacked_size,
                "unpacked_files": len(unpacked_members),
            },
            "database": reused_descriptor if reused_descriptor is not None else {
                "url": _artifact_url(database_base, database_name),
                "sha256": _sha256(database_artifact),
                "size_bytes": database_artifact.stat().st_size,
                "format": "sqlite-gzip" if independent_app else "sqlite",
            },
        }
        if independent_app and reused_descriptor is None:
            artifacts["database"].update(unpacked_sha256=database_manifest["database"]["sha256"],
                                         unpacked_size_bytes=database_manifest["database"]["size_bytes"])
        payload = {
            "contract": DISCOVERY_CONTRACT,
            "schema_version": DISCOVERY_SCHEMA_VERSION,
            "channel": pair["channel"],
            "pair_manifest": pair,
            "database_manifest": database_manifest,
            "artifacts": artifacts,
        }
        unsigned = {**copy.deepcopy(payload), "signature": None}
        # The discovery validator is also the publisher schema oracle.  An
        # unsigned stable document is expected to be untrusted, not invalid.
        checked, _trusted = validate_latest_document(
            unsigned,
            expected_channel=pair["channel"],
            trust_verifier=None,
            allow_unsigned_dev=(pair["channel"] == "dev"),
        )
        if checked != unsigned:
            _fail("release_pair_feed_document_normalization_drift")
        canonical = _canonical(payload)
        _write_json(temporary / UNSIGNED_DOCUMENT_FILENAME, unsigned)
        _write_atomic(temporary / SIGNING_PAYLOAD_FILENAME, canonical)
        receipt_body = {
            "contract": FEED_RECEIPT_CONTRACT,
            "schema_version": FEED_RECEIPT_SCHEMA_VERSION,
            "release_pair_id": pair["release_pair_id"],
            "database_release_id": database_manifest["database_release_id"],
            "channel": pair["channel"],
            "artifacts": artifacts,
            "files": {
                app_name: {
                    "sha256": artifacts["app"]["sha256"],
                    "size_bytes": artifacts["app"]["size_bytes"],
                },
                database_name: {
                    "sha256": artifacts["database"]["sha256"],
                    "size_bytes": artifacts["database"]["size_bytes"],
                },
                UNSIGNED_DOCUMENT_FILENAME: {
                    "sha256": _sha256(temporary / UNSIGNED_DOCUMENT_FILENAME),
                    "size_bytes": (temporary / UNSIGNED_DOCUMENT_FILENAME).stat().st_size,
                },
                SIGNING_PAYLOAD_FILENAME: {
                    "sha256": hashlib.sha256(canonical).hexdigest(),
                    "size_bytes": len(canonical),
                },
            },
            "signing_payload_sha256": hashlib.sha256(canonical).hexdigest(),
        }
        receipt = {
            **receipt_body,
            "receipt_sha256": hashlib.sha256(_canonical(receipt_body)).hexdigest(),
        }
        _write_json(temporary / FEED_RECEIPT_FILENAME, receipt)
        os.replace(temporary, output)
        return copy.deepcopy(receipt)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def finalize_release_pair_feed(
    *,
    feed_directory: Path,
    signature_path: Path,
    trust_keys: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Verify an external signature and write ``latest.json`` exactly once."""

    feed = Path(feed_directory).resolve(strict=True)
    if not feed.is_dir() or _reparse(feed):
        _fail("release_pair_feed_directory_invalid")
    target = feed / FINAL_DOCUMENT_FILENAME
    if target.exists():
        _fail("release_pair_feed_final_exists")
    receipt = _validate_feed_receipt(feed)
    unsigned = _read_json(
        feed / UNSIGNED_DOCUMENT_FILENAME,
        "release_pair_feed_unsigned_document_invalid",
    )
    if unsigned.get("signature") is not None:
        _fail("release_pair_feed_unsigned_document_invalid")
    signature = _read_json(
        Path(signature_path),
        "release_pair_feed_signature_invalid",
        maximum=MAX_SIGNATURE_BYTES,
    )
    if set(signature) != {"algorithm", "key_id", "value"}:
        _fail("release_pair_feed_signature_invalid")
    payload = {key: copy.deepcopy(value) for key, value in unsigned.items() if key != "signature"}
    canonical_path = feed / SIGNING_PAYLOAD_FILENAME
    try:
        with canonical_path.open("rb") as source:
            canonical = source.read(MAX_DOCUMENT_BYTES + 1)
    except OSError as error:
        raise ReleasePairFeedError("release_pair_feed_signing_payload_missing") from error
    if len(canonical) > MAX_DOCUMENT_BYTES or canonical != _canonical(payload):
        _fail("release_pair_feed_signing_payload_mismatch")
    if hashlib.sha256(canonical).hexdigest() != receipt["signing_payload_sha256"]:
        _fail("release_pair_feed_signing_payload_mismatch")
    verifier = build_trust_verifier(trust_keys)
    if verifier is None:
        _fail("release_pair_feed_trust_keys_unconfigured")
    document = {**payload, "signature": signature}
    try:
        checked, trusted = validate_latest_document(
            document,
            expected_channel=payload["channel"],
            trust_verifier=verifier,
            allow_unsigned_dev=False,
        )
    except ReleasePairDiscoveryError as error:
        raise ReleasePairFeedError(error.reason) from error
    if trusted is not True or checked != document:
        _fail("release_pair_feed_signature_untrusted")
    _check_signed_artifact_binding(receipt, checked)
    _write_json(target, checked)
    return {
        "status": "finalized",
        "path": str(target),
        "release_pair_id": checked["pair_manifest"]["release_pair_id"],
        "document_sha256": hashlib.sha256(_canonical(checked)).hexdigest(),
        "signature": {
            "algorithm": signature["algorithm"],
            "key_id": signature["key_id"],
        },
    }


__all__ = [
    "FINAL_DOCUMENT_FILENAME",
    "SIGNING_PAYLOAD_FILENAME",
    "UNSIGNED_DOCUMENT_FILENAME",
    "ReleasePairFeedError",
    "finalize_release_pair_feed",
    "prepare_release_pair_feed",
]
