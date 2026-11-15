"""Offline safety core for coordinated App/database pair updates.

This module is intentionally not a downloader and is never called from the
running GUI.  It verifies already-staged artifacts, records a bounded handoff
for an external launcher, and commits the live pair identity only after the
launcher has replaced the App and the database subtransaction has published a
matching accepted database manifest and receipt.

The database subtransaction remains owned by
``genshin_db.release_migration.install_database_release``.  In particular,
preparing a handoff never writes ``genshin.db`` and never marks a release pair
operational.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from genshin_app.database_transaction import serialized_profile_writer
from genshin_app.package_integrity import (
    PackageIntegrityError,
    verify_package_artifact,
)
from genshin_app.product_release_index import (
    LIVE_PAIR_PROOF_FILENAME,
    PER_USER_UPDATER_RELATIVE,
    first_install_option,
    release_options,
    validate_live_pair_proof,
    validate_product_release_index,
)
from genshin_app.release_pair_trust import ALGORITHM as REMOTE_TRUST_ALGORITHM
from genshin_app.release_pair_trust import VERIFIER_CONTRACT as REMOTE_VERIFIER_CONTRACT
from genshin_app.release_pair_trust import build_trust_verifier
from genshin_db.release_profile import (
    DatabaseReleaseError,
    database_live_receipt_matches,
    database_release_semantically_equal,
    read_database_identity,
    release_pair_matches_database_manifest,
    validate_database_live_receipt,
    validate_database_release_manifest,
    validate_release_pair_manifest,
)

PAIR_UPDATE_JOURNAL_CONTRACT = "genshin-release-pair-update-journal-v1"
PAIR_UPDATE_HANDOFF_CONTRACT = "genshin-release-pair-launcher-handoff-v1"
PAIR_UPDATE_TRUST_CONTRACT = "genshin-release-pair-trust-assertion-v1"
PAIR_UPDATE_REMOTE_TRUST_CONTRACT = (
    "genshin-release-pair-signed-discovery-trust-v1"
)
LOCAL_REVIEW_VERIFIER_CONTRACT = "genshin-release-pair-local-review-v1"
PAIR_UPDATE_SCHEMA_VERSION = 1
PAIR_UPDATE_JOURNAL_FILENAME = "release-pair-update-journal.json"
PAIR_UPDATE_HANDOFF_FILENAME = "release-pair-launcher-handoff.json"
LIVE_PAIR_MANIFEST_FILENAME = "release-pair-live-manifest.json"
LIVE_DATABASE_MANIFEST_FILENAME = "database-live-manifest.json"
LIVE_DATABASE_RECEIPT_FILENAME = "database-live-receipt.json"
MAX_CONTROL_FILE_BYTES = 4 * 1024 * 1024

_STATES = frozenset(
    {
        "handoff_ready",
        "activation_started",
        "postchecked",
        "committed",
        "operational",
        "rolled_back",
        "cleanup_complete",
    }
)
# A rolled-back transaction still owns ``staging`` and ``rollback``.  Reusing
# the workspace before cleanup would let a later transaction collide with the
# previous rollback boundary, so only cleanup completion permits a new handoff.
_TERMINAL_STATES = frozenset({"cleanup_complete"})


class ReleasePairUpdateError(RuntimeError):
    """Stable, renderer-safe release-pair update failure."""

    def __init__(self, reason: str, message: str | None = None, details=None):
        self.reason = reason
        self.details = copy.deepcopy(details)
        super().__init__(message or reason)


def _fail(reason: str, message: str | None = None, details=None) -> None:
    raise ReleasePairUpdateError(reason, message, details)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc_timestamp(value: Any) -> str:
    if type(value) is not str or not value.endswith("Z") or len(value) > 64:
        _fail("release_pair_trust_assertion_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ReleasePairUpdateError("release_pair_trust_assertion_invalid") from error
    if parsed.tzinfo != UTC:
        _fail("release_pair_trust_assertion_invalid")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(block)
    except OSError as error:
        raise ReleasePairUpdateError("release_pair_artifact_unreadable") from error
    return digest.hexdigest()


def _is_reparse_point(path: Path) -> bool:
    """Reject symlinks and Windows junctions at updater trust boundaries."""

    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if callable(is_junction) and is_junction():
            return True
        return bool(getattr(path.lstat(), "st_file_attributes", 0) & 0x400)
    except OSError as error:
        raise ReleasePairUpdateError("release_pair_artifact_unreadable") from error


def _reject_reparse_chain(root: Path, target: Path) -> None:
    try:
        relative = target.relative_to(root)
    except ValueError:
        _fail("release_pair_staging_path_outside_workspace")
    current = root
    if _is_reparse_point(current):
        _fail("release_pair_reparse_point_unsupported")
    for part in relative.parts:
        current /= part
        if _is_reparse_point(current):
            _fail("release_pair_reparse_point_unsupported")


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"
    if len(raw) > MAX_CONTROL_FILE_BYTES:
        _fail("release_pair_control_file_too_large")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path, reason: str) -> dict[str, Any]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CONTROL_FILE_BYTES + 1)
        if not raw or len(raw) > MAX_CONTROL_FILE_BYTES:
            _fail(reason)
        value = json.loads(raw.decode("utf-8"))
    except ReleasePairUpdateError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleasePairUpdateError(reason) from error
    if type(value) is not dict:
        _fail(reason)
    return value


def _controlled_relative(root: Path, supplied: Path, *, directory: bool) -> str:
    try:
        canonical_root = root.resolve(strict=True)
        canonical = supplied.resolve(strict=True)
    except OSError as error:
        raise ReleasePairUpdateError("release_pair_staging_path_invalid") from error
    if canonical_root == canonical or canonical_root not in canonical.parents:
        _fail("release_pair_staging_path_outside_workspace")
    _reject_reparse_chain(root, supplied)
    if (directory and not canonical.is_dir()) or (
        not directory and not canonical.is_file()
    ):
        _fail("release_pair_staging_path_invalid")
    return canonical.relative_to(canonical_root).as_posix()


def _workspace_path(workspace: Path, relative: str, *, directory: bool) -> Path:
    if type(relative) is not str or not relative or "\\" in relative:
        _fail("release_pair_handoff_invalid")
    try:
        root = workspace.resolve(strict=True)
        target = (root / relative).resolve(strict=True)
    except OSError as error:
        raise ReleasePairUpdateError("release_pair_handoff_artifact_missing") from error
    if root == target or root not in target.parents:
        _fail("release_pair_handoff_path_invalid")
    _reject_reparse_chain(workspace, workspace / relative)
    if (directory and not target.is_dir()) or (
        not directory and not target.is_file()
    ):
        _fail("release_pair_handoff_artifact_missing")
    return target


def _verify_app(root: Path, pair: Mapping[str, Any], *, independent: bool = False) -> dict[str, Any]:
    try:
        verification = verify_package_artifact(root)
    except PackageIntegrityError as error:
        raise ReleasePairUpdateError(error.code) from error
    expected = pair["app"]["artifact_manifest"]["sha256"]
    if verification.get("manifest_sha256") != expected:
        _fail(
            "release_pair_app_artifact_mismatch",
            details={
                "expected_manifest_sha256": expected,
                "actual_manifest_sha256": verification.get("manifest_sha256"),
            },
        )
    detached_path = Path(root) / "_internal" / "bundled" / "release-pair-manifest.json"
    if independent:
        if detached_path.parent.exists():
            _fail("release_pair_independent_app_contains_bundled_data")
        if not (root / PER_USER_UPDATER_RELATIVE).is_file():
            _fail("release_pair_updater_current_user_missing")
        return verification
    detached_value = _read_json(
        detached_path,
        "release_pair_detached_manifest_invalid",
    )
    try:
        detached = validate_release_pair_manifest(detached_value)
    except DatabaseReleaseError as error:
        raise ReleasePairUpdateError(error.reason) from error
    if detached != pair:
        _fail("release_pair_detached_manifest_mismatch")
    return verification


def _independent_app(trust: Mapping[str, Any]) -> bool:
    return trust.get("signed_document", {}).get("artifacts", {}).get("app", {}).get("format") == "app-zip"


def _verify_product_selection(trust: Mapping[str, Any], current: dict | None, *, reuse_app: bool,
                              reuse_database: bool, for_install: bool = True) -> None:
    selection = trust.get("product_selection")
    if not isinstance(selection, dict) or set(selection) != {"signed_index", "confirmation_digest"}:
        _fail("product_release_selection_required")
    from genshin_app.version import DATABASE_RELEASE_TRUST_KEYS

    try:
        channel = current["pair_manifest"]["channel"] if current is not None else trust["signed_document"]["channel"]
        index = validate_product_release_index(
            selection["signed_index"], channel=channel,
            trust_verifier=build_trust_verifier(DATABASE_RELEASE_TRUST_KEYS),
            for_install=for_install,
        )
        if current is None:
            option = first_install_option(index)
            if (reuse_app or reuse_database or option["confirmation_digest"] != selection["confirmation_digest"]
                    or option["document"] != trust["signed_document"]):
                _fail("product_release_selection_invalid")
            return
    except ValueError as error:
        raise ReleasePairUpdateError(getattr(error, "reason", "product_release_selection_invalid")) from error
    matches = [option for option in release_options(index, current["pair_manifest"], current["database_manifest"])
               if option["confirmation_digest"] == selection["confirmation_digest"]]
    if (len(matches) != 1 or matches[0]["document"] != trust["signed_document"]
            or matches[0]["reuse_app"] != reuse_app or matches[0]["reuse_database"] != reuse_database):
        _fail("product_release_selection_invalid")


def read_live_product_proof(data_dir: Path, pair: dict, database: dict) -> dict | None:
    path = Path(data_dir) / LIVE_PAIR_PROOF_FILENAME
    if not path.exists():
        return None
    from genshin_app.version import DATABASE_RELEASE_TRUST_KEYS

    try:
        document = validate_live_pair_proof(
            _read_json(path, "product_release_live_pair_proof_invalid"), pair, database,
            trust_verifier=build_trust_verifier(DATABASE_RELEASE_TRUST_KEYS),
        )
    except ValueError as error:
        raise ReleasePairUpdateError("product_release_live_pair_proof_invalid") from error
    if document["artifacts"]["app"]["format"] != "app-zip":
        _fail("product_release_live_pair_proof_invalid")
    return document


def _verify_database_package(
    path: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    database = manifest["database"]
    try:
        size = path.stat().st_size
    except OSError as error:
        raise ReleasePairUpdateError("release_pair_database_artifact_unreadable") from error
    digest = _sha256(path)
    if size != database["size_bytes"] or digest != database["sha256"]:
        _fail(
            "release_pair_database_artifact_mismatch",
            details={
                "expected_sha256": database["sha256"],
                "actual_sha256": digest,
                "expected_size_bytes": database["size_bytes"],
                "actual_size_bytes": size,
            },
        )
    return {"sha256": digest, "size_bytes": size}


def _validated_pair(
    pair_manifest: Mapping[str, Any],
    database_manifest: Mapping[str, Any],
    *,
    production: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        pair = validate_release_pair_manifest(dict(pair_manifest))
        database = validate_database_release_manifest(dict(database_manifest))
        linked = release_pair_matches_database_manifest(pair, database)
    except DatabaseReleaseError as error:
        raise ReleasePairUpdateError(error.reason) from error
    if not linked:
        _fail("release_pair_database_manifest_mismatch")
    expected = ("stable", "exact") if production else ("dev", "current_only")
    if (pair["channel"], pair["pair_policy"]) != expected:
        _fail(
            "release_pair_update_flavor_mismatch",
            details={
                "expected_channel": expected[0],
                "expected_pair_policy": expected[1],
                "actual_channel": pair["channel"],
                "actual_pair_policy": pair["pair_policy"],
            },
        )
    return pair, database


def _validated_trust_assertion(
    trust_assertion: Mapping[str, Any] | None,
    pair: Mapping[str, Any],
    database: Mapping[str, Any],
    *,
    production: bool,
) -> dict[str, Any]:
    """Bind candidates to an independently established trust decision.

    A production verifier and pinned public key do not exist yet, so stable
    installation is deliberately unavailable.  Development current-only
    pairs require an explicit local-review assertion; user confirmation is a
    separate product decision and never substitutes for this trust input.
    """

    if type(trust_assertion) is not dict:
        _fail("release_pair_trust_assertion_required")
    if trust_assertion.get("contract") == PAIR_UPDATE_REMOTE_TRUST_CONTRACT:
        required = {
            "contract",
            "schema_version",
            "source",
            "verifier_contract",
            "authoritative",
            "scope",
            "assertion_id",
            "verified_at",
            "release_pair_id",
            "pair_manifest_sha256",
            "database_manifest_sha256",
            "document_sha256",
            "algorithm",
            "key_id",
            "signed_document",
        }
        expected_scope = "stable_exact" if production else "development_current_only"
        if (
            set(trust_assertion) not in (required, required | {"product_selection"})
            or trust_assertion["schema_version"] != PAIR_UPDATE_SCHEMA_VERSION
            or trust_assertion["source"] != "signed_discovery"
            or trust_assertion["verifier_contract"] != REMOTE_VERIFIER_CONTRACT
            or trust_assertion["authoritative"] is not True
            or trust_assertion["scope"] != expected_scope
            or trust_assertion["release_pair_id"] != pair["release_pair_id"]
            or trust_assertion["pair_manifest_sha256"] != _fingerprint(pair)
            or trust_assertion["database_manifest_sha256"] != _fingerprint(database)
            or trust_assertion["algorithm"] != REMOTE_TRUST_ALGORITHM
        ):
            _fail("release_pair_trust_assertion_mismatch")
        for field in ("assertion_id", "key_id"):
            item = trust_assertion[field]
            if type(item) is not str or not item.strip() or len(item) > 256:
                _fail("release_pair_trust_assertion_invalid")
        digest = trust_assertion["document_sha256"]
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            _fail("release_pair_trust_assertion_invalid")
        signed_document = trust_assertion["signed_document"]
        try:
            from genshin_app.release_pair_discovery import validate_latest_document
            from genshin_app.version import DATABASE_RELEASE_TRUST_KEYS

            verifier = build_trust_verifier(DATABASE_RELEASE_TRUST_KEYS)
            checked_document, trusted = validate_latest_document(
                signed_document,
                expected_channel=pair["channel"],
                trust_verifier=verifier,
                allow_unsigned_dev=False,
            )
        except Exception as error:
            raise ReleasePairUpdateError(
                "release_pair_signed_discovery_invalid"
            ) from error
        if (
            trusted is not True
            or checked_document.get("pair_manifest") != pair
            or checked_document.get("database_manifest") != database
            or _fingerprint(checked_document) != digest
            or type(checked_document.get("signature")) is not dict
            or checked_document["signature"].get("algorithm")
            != trust_assertion["algorithm"]
            or checked_document["signature"].get("key_id")
            != trust_assertion["key_id"]
        ):
            _fail("release_pair_signed_discovery_invalid")
        _utc_timestamp(trust_assertion["verified_at"])
        return copy.deepcopy(dict(trust_assertion))

    if production:
        # A local-review receipt is not a production signature and must never
        # become one merely because its manifest fingerprints happen to match.
        _fail("release_pair_production_trust_unavailable")
    required = {
        "contract",
        "schema_version",
        "source",
        "verifier_contract",
        "authoritative",
        "scope",
        "assertion_id",
        "verified_at",
        "release_pair_id",
        "pair_manifest_sha256",
        "database_manifest_sha256",
    }
    if set(trust_assertion) != required:
        _fail("release_pair_trust_assertion_invalid")
    if (
        trust_assertion["contract"] != PAIR_UPDATE_TRUST_CONTRACT
        or trust_assertion["schema_version"] != PAIR_UPDATE_SCHEMA_VERSION
        or trust_assertion["source"] != "local_review"
        or trust_assertion["verifier_contract"]
        != LOCAL_REVIEW_VERIFIER_CONTRACT
        or trust_assertion["authoritative"] is not True
        or trust_assertion["scope"] != "development_current_only"
        or trust_assertion["release_pair_id"] != pair["release_pair_id"]
        or trust_assertion["pair_manifest_sha256"] != _fingerprint(pair)
        or trust_assertion["database_manifest_sha256"]
        != _fingerprint(database)
    ):
        _fail("release_pair_trust_assertion_mismatch")
    assertion_id = trust_assertion["assertion_id"]
    if type(assertion_id) is not str or not assertion_id.strip() or len(assertion_id) > 256:
        _fail("release_pair_trust_assertion_invalid")
    _utc_timestamp(trust_assertion["verified_at"])
    return copy.deepcopy(dict(trust_assertion))


def create_signed_discovery_trust_assertion(
    discovery_result: Mapping[str, Any],
    pair_manifest: Mapping[str, Any],
    database_manifest: Mapping[str, Any],
    *,
    production: bool,
    verified_at: str | None = None,
    product_selection: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind one already verified discovery document to the durable handoff.

    Signature verification belongs to ``release_pair_discovery`` and its
    pinned verifier.  This function refuses a boolean-only or unsigned result;
    it records the exact signed document digest and key identity so the
    external process can revalidate the immutable handoff without converting
    user confirmation into trust.
    """

    pair, database = _validated_pair(
        pair_manifest,
        database_manifest,
        production=production,
    )
    trust = discovery_result.get("trust") if isinstance(discovery_result, Mapping) else None
    signed_document = discovery_result.get("signed_document") if isinstance(
        discovery_result, Mapping
    ) else None
    document_digest = discovery_result.get("document_sha256") if isinstance(
        discovery_result, Mapping
    ) else None
    if (
        discovery_result.get("authoritative") is not True
        or discovery_result.get("pair_manifest") != pair
        or discovery_result.get("database_manifest") != database
        or type(trust) is not dict
        or trust.get("source") != "signed_discovery"
        or trust.get("algorithm") != REMOTE_TRUST_ALGORITHM
        or type(trust.get("key_id")) is not str
        or not trust["key_id"]
        or type(document_digest) is not str
        or len(document_digest) != 64
        or any(character not in "0123456789abcdef" for character in document_digest)
        or type(signed_document) is not dict
        or _fingerprint(signed_document) != document_digest
    ):
        _fail("release_pair_signed_discovery_required")
    stamp = verified_at or _now()
    assertion = {
        "contract": PAIR_UPDATE_REMOTE_TRUST_CONTRACT,
        "schema_version": PAIR_UPDATE_SCHEMA_VERSION,
        "source": "signed_discovery",
        "verifier_contract": REMOTE_VERIFIER_CONTRACT,
        "authoritative": True,
        "scope": "stable_exact" if production else "development_current_only",
        "assertion_id": document_digest,
        "verified_at": stamp,
        "release_pair_id": pair["release_pair_id"],
        "pair_manifest_sha256": _fingerprint(pair),
        "database_manifest_sha256": _fingerprint(database),
        "document_sha256": document_digest,
        "algorithm": REMOTE_TRUST_ALGORITHM,
        "key_id": trust["key_id"],
        "signed_document": copy.deepcopy(signed_document),
    }
    if product_selection is not None:
        assertion["product_selection"] = copy.deepcopy(dict(product_selection))
    return _validated_trust_assertion(
        assertion,
        pair,
        database,
        production=production,
    )


def create_local_review_trust_assertion(
    pair_manifest: Mapping[str, Any],
    database_manifest: Mapping[str, Any],
    *,
    assertion_id: str,
    verified_at: str,
) -> dict[str, Any]:
    """Issue a dev-only assertion after the caller completes local review.

    This helper canonicalizes fingerprints; it does not perform or replace the
    review itself and cannot create production trust.
    """

    pair, database = _validated_pair(
        pair_manifest,
        database_manifest,
        production=False,
    )
    assertion = {
        "contract": PAIR_UPDATE_TRUST_CONTRACT,
        "schema_version": PAIR_UPDATE_SCHEMA_VERSION,
        "source": "local_review",
        "verifier_contract": LOCAL_REVIEW_VERIFIER_CONTRACT,
        "authoritative": True,
        "scope": "development_current_only",
        "assertion_id": assertion_id,
        "verified_at": verified_at,
        "release_pair_id": pair["release_pair_id"],
        "pair_manifest_sha256": _fingerprint(pair),
        "database_manifest_sha256": _fingerprint(database),
    }
    return _validated_trust_assertion(
        assertion,
        pair,
        database,
        production=False,
    )


def _validate_current(
    current: Mapping[str, Any],
    *,
    production: bool,
    proof_directory: Path | None = None,
) -> dict[str, Any]:
    required = {
        "app_root",
        "database",
        "pair_manifest",
        "database_manifest",
        "live_receipt",
    }
    if type(current) is not dict or set(current) != required:
        _fail("release_pair_current_identity_invalid")
    pair, manifest = _validated_pair(
        current["pair_manifest"],
        current["database_manifest"],
        production=production,
    )
    app_root = Path(current["app_root"])
    database_path = Path(current["database"])
    proof_root = database_path.parent if proof_directory is None else proof_directory
    independent = read_live_product_proof(proof_root, pair, manifest) is not None
    app_verification = _verify_app(app_root, pair, independent=independent)
    try:
        receipt = validate_database_live_receipt(dict(current["live_receipt"]))
        identity = read_database_identity(database_path)
        receipt_matches = database_live_receipt_matches(receipt, identity, manifest)
    except DatabaseReleaseError as error:
        raise ReleasePairUpdateError(error.reason) from error
    if not receipt_matches or (independent and identity["sha256"] != manifest["database"]["sha256"]):
        _fail("release_pair_current_database_identity_mismatch")
    return {
        "pair_manifest": pair,
        "database_manifest": manifest,
        "live_receipt": receipt,
        "app_manifest_sha256": app_verification["manifest_sha256"],
        "active_database_sha256": identity["sha256"],
        "identity_fingerprint": _fingerprint(
            {
                "pair": pair,
                "database_manifest": manifest,
                "receipt": receipt,
                "app_manifest_sha256": app_verification["manifest_sha256"],
                "active_database_sha256": identity["sha256"],
            }
        ),
    }


def inspect_active_release_pair(
    active_app_root: Path,
    data_dir: Path,
    *,
    production: bool,
) -> dict[str, Any]:
    """Rebuild current identity from the protected App package and live DB.

    The external elevated helper calls this after the GUI exits.  It must not
    trust the rollback identity copied from the user-writable handoff alone.
    The packaged pair/database manifests live below the admin-installed App
    root; the live sidecars must equal those protected identities and bind the
    actual SQLite bytes through the installation receipt.
    """

    app_root = Path(active_app_root).resolve(strict=True)
    data = Path(data_dir).resolve(strict=True)
    bundled = app_root / "_internal" / "bundled"
    live_pair_value = _read_json(
        data / LIVE_PAIR_MANIFEST_FILENAME,
        "release_pair_live_manifest_invalid",
    )
    live_database_value = _read_json(
        data / LIVE_DATABASE_MANIFEST_FILENAME,
        "release_pair_live_database_manifest_invalid",
    )
    receipt_value = _read_json(
        data / LIVE_DATABASE_RECEIPT_FILENAME,
        "release_pair_live_database_receipt_invalid",
    )
    try:
        live_pair = validate_release_pair_manifest(live_pair_value)
        live_database = validate_database_release_manifest(live_database_value)
        receipt = validate_database_live_receipt(receipt_value)
    except DatabaseReleaseError as error:
        raise ReleasePairUpdateError(error.reason) from error
    if read_live_product_proof(data, live_pair, live_database) is None:
        try:
            protected_pair = validate_release_pair_manifest(_read_json(
                bundled / "release-pair-manifest.json", "release_pair_protected_manifest_invalid",
            ))
            protected_database = validate_database_release_manifest(_read_json(
                bundled / "database-release-manifest.json", "release_pair_protected_manifest_invalid",
            ))
        except DatabaseReleaseError as error:
            raise ReleasePairUpdateError(error.reason) from error
        if live_pair != protected_pair or live_database != protected_database:
            _fail("release_pair_active_identity_mismatch")
    return _validate_current(
        {
            "app_root": app_root,
            "database": data / "genshin.db",
            "pair_manifest": live_pair,
            "database_manifest": live_database,
            "live_receipt": receipt,
        },
        production=production,
    )


def _journal_path(workspace: Path) -> Path:
    return workspace / PAIR_UPDATE_JOURNAL_FILENAME


def _handoff_path(workspace: Path) -> Path:
    return workspace / PAIR_UPDATE_HANDOFF_FILENAME


def _validate_journal(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "contract",
        "schema_version",
        "operation_id",
        "state",
        "production",
        "target_release_pair_id",
        "target_pair_sequence",
        "target_fingerprint",
        "rollback_release_pair_id",
        "rollback_fingerprint",
        "handoff_fingerprint",
        "history",
        "created_at",
        "updated_at",
    }:
        _fail("release_pair_update_journal_invalid")
    if (
        value["contract"] != PAIR_UPDATE_JOURNAL_CONTRACT
        or value["schema_version"] != PAIR_UPDATE_SCHEMA_VERSION
        or type(value["operation_id"]) is not str
        or len(value["operation_id"]) != 32
        or any(character not in "0123456789abcdef" for character in value["operation_id"])
        or value["state"] not in _STATES
        or type(value["production"]) is not bool
        or type(value["target_release_pair_id"]) is not str
        or not value["target_release_pair_id"]
        or type(value["target_pair_sequence"]) is not int
        or value["target_pair_sequence"] < 1
        or (
            value["rollback_release_pair_id"] is not None
            and (
                type(value["rollback_release_pair_id"]) is not str
                or not value["rollback_release_pair_id"]
            )
        )
        or type(value["history"]) is not list
        or not value["history"]
        or type(value["created_at"]) is not str
        or not value["created_at"]
        or type(value["updated_at"]) is not str
        or not value["updated_at"]
    ):
        _fail("release_pair_update_journal_invalid")
    for field in (
        "target_fingerprint",
        "rollback_fingerprint",
        "handoff_fingerprint",
    ):
        item = value[field]
        if item is not None and (
            type(item) is not str
            or len(item) != 64
            or any(character not in "0123456789abcdef" for character in item)
        ):
            _fail("release_pair_update_journal_invalid")
    if value["target_fingerprint"] is None or value["handoff_fingerprint"] is None:
        _fail("release_pair_update_journal_invalid")
    for row in value["history"]:
        if (
            type(row) is not dict
            or set(row) != {"state", "at"}
            or row["state"] not in _STATES
            or type(row["at"]) is not str
            or not row["at"]
        ):
            _fail("release_pair_update_journal_invalid")
    if value["history"][-1]["state"] != value["state"]:
        _fail("release_pair_update_journal_invalid")
    return copy.deepcopy(value)


def read_pair_update_journal(workspace: Path) -> dict[str, Any] | None:
    path = _journal_path(Path(workspace))
    if not path.exists():
        return None
    return _validate_journal(
        _read_json(path, "release_pair_update_journal_invalid")
    )


def _read_handoff(workspace: Path, journal: Mapping[str, Any]) -> dict[str, Any]:
    value = _read_json(
        _handoff_path(workspace),
        "release_pair_update_handoff_invalid",
    )
    required = {
        "contract",
        "schema_version",
        "operation_id",
        "production",
        "staging",
        "rollback_directory",
        "rollback_artifacts",
        "target",
        "rollback",
        "database_subtransaction",
    }
    if (
        set(value) != required
        or value.get("contract") != PAIR_UPDATE_HANDOFF_CONTRACT
        or value.get("schema_version") != PAIR_UPDATE_SCHEMA_VERSION
        or value.get("operation_id") != journal["operation_id"]
        or value.get("production") is not journal["production"]
        or _fingerprint(value) != journal["handoff_fingerprint"]
    ):
        _fail("release_pair_update_handoff_invalid")
    return value


def _validated_handoff_target(
    handoff: Mapping[str, Any], journal: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    target = handoff.get("target")
    if type(target) is not dict or set(target) != {
        "pair_manifest",
        "database_manifest",
        "trust_assertion",
        "app_verification",
        "database_verification",
    }:
        _fail("release_pair_update_handoff_invalid")
    pair, database = _validated_pair(
        target["pair_manifest"],
        target["database_manifest"],
        production=journal["production"],
    )
    trust = _validated_trust_assertion(
        target["trust_assertion"],
        pair,
        database,
        production=journal["production"],
    )
    normalized = {
        "pair_manifest": pair,
        "database_manifest": database,
        "trust_assertion": trust,
        "app_verification": target["app_verification"],
        "database_verification": target["database_verification"],
    }
    if normalized != target or _fingerprint(target) != journal["target_fingerprint"]:
        _fail("release_pair_update_target_identity_mismatch")
    return pair, database


def _write_transition(
    workspace: Path,
    journal: Mapping[str, Any],
    state: str,
) -> dict[str, Any]:
    if state not in _STATES:
        _fail("release_pair_update_state_invalid")
    value = copy.deepcopy(dict(journal))
    value["state"] = state
    value["updated_at"] = _now()
    value["history"].append({"state": state, "at": value["updated_at"]})
    _write_json_atomic(_journal_path(workspace), value)
    return value


def prepare_release_pair_handoff(
    workspace: Path,
    staged_app_root: Path | None,
    staged_database: Path | None,
    database_manifest: Mapping[str, Any],
    pair_manifest: Mapping[str, Any],
    *,
    user_confirmed: bool,
    production: bool,
    trust_assertion: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None = None,
    reuse_installed_database: bool = False,
) -> dict[str, Any]:
    """Verify staged artifacts and create one external-launcher handoff.

    Both staged artifacts must already be located below ``workspace/staging``.
    The function performs no active App/database replacement.  Stable updates
    require an exact pair; repository development updates require current-only.
    """

    if user_confirmed is not True:
        _fail("release_pair_update_confirmation_required")
    if type(production) is not bool:
        _fail("release_pair_update_mode_invalid")
    if type(reuse_installed_database) is not bool:
        _fail("release_pair_update_database_mode_invalid")
    root = Path(workspace)
    try:
        root = root.resolve(strict=True)
        staging_path = root / "staging"
        if _is_reparse_point(staging_path):
            _fail("release_pair_reparse_point_unsupported")
        staging = staging_path.resolve(strict=True)
    except OSError as error:
        raise ReleasePairUpdateError("release_pair_staging_missing") from error
    if root == staging or root not in staging.parents or not staging.is_dir():
        _fail("release_pair_staging_invalid")
    reuse_installed_app = staged_app_root is None
    if reuse_installed_app and reuse_installed_database:
        _fail("release_pair_update_no_artifact_changes")
    app_relative = None if reuse_installed_app else _controlled_relative(staging, Path(staged_app_root), directory=True)
    if reuse_installed_database:
        if staged_database is not None:
            _fail("release_pair_update_database_mode_invalid")
        database_relative = None
    else:
        if staged_database is None:
            _fail("release_pair_staged_database_missing")
        database_relative = _controlled_relative(
            staging, Path(staged_database), directory=False
        )
    existing = read_pair_update_journal(root)
    if existing is not None and existing["state"] not in _TERMINAL_STATES:
        _fail(
            "release_pair_update_recovery_required",
            details={"state": existing["state"]},
        )

    pair, manifest = _validated_pair(
        pair_manifest,
        database_manifest,
        production=production,
    )
    trust = _validated_trust_assertion(
        trust_assertion,
        pair,
        manifest,
        production=production,
    )
    rollback = _validate_current(current, production=production) if current else None
    independent = _independent_app(trust)
    if independent:
        _verify_product_selection(trust, rollback, reuse_app=reuse_installed_app,
                                  reuse_database=reuse_installed_database)
    if reuse_installed_app:
        if (not independent or rollback is None or current is None
                or pair["app"]["app_release_id"] != rollback["pair_manifest"]["app"]["app_release_id"]
                or read_live_product_proof(Path(current["database"]).parent, rollback["pair_manifest"],
                                           rollback["database_manifest"]) is None):
            _fail("release_pair_app_reuse_not_verified")
        app_verification = _verify_app(Path(current["app_root"]), pair, independent=True)
    else:
        app_verification = _verify_app(Path(staged_app_root), pair, independent=independent)
    if reuse_installed_database:
        if rollback is None or not database_release_semantically_equal(
            rollback["database_manifest"], manifest
        ):
            _fail("release_pair_database_reuse_not_verified")
        database_verification = {
            "mode": "reuse_installed",
            "active_database_sha256": rollback["active_database_sha256"],
        }
    else:
        database_verification = _verify_database_package(
            Path(staged_database), manifest
        )
    if rollback is not None:
        current_pair = rollback["pair_manifest"]
        if current_pair["channel"] != pair["channel"]:
            _fail("release_pair_update_channel_mismatch")
        if pair["pair_sequence"] < current_pair["pair_sequence"]:
            _fail("release_pair_update_rollback_forbidden")
        if pair["pair_sequence"] == current_pair["pair_sequence"]:
            if pair == current_pair and manifest == rollback["database_manifest"]:
                return {
                    "contract": PAIR_UPDATE_HANDOFF_CONTRACT,
                    "status": "already_current",
                    "release_pair_id": pair["release_pair_id"],
                    "pair_sequence": pair["pair_sequence"],
                    "pair_operational": True,
                }
            _fail("release_pair_update_split_brain")

    operation_id = uuid4().hex
    target = {
        "pair_manifest": pair,
        "database_manifest": manifest,
        "trust_assertion": trust,
        "app_verification": app_verification,
        "database_verification": database_verification,
    }
    target_fingerprint = _fingerprint(target)
    handoff = {
        "contract": PAIR_UPDATE_HANDOFF_CONTRACT,
        "schema_version": PAIR_UPDATE_SCHEMA_VERSION,
        "operation_id": operation_id,
        "production": production,
        "staging": {
            "app_relative": app_relative,
            "database_relative": database_relative,
        },
        "rollback_directory": "rollback",
        "rollback_artifacts": (
            {"app_relative": "app", "database_relative": "genshin.db"}
            if rollback is not None
            else None
        ),
        "target": target,
        "rollback": rollback,
        "database_subtransaction": {
            "entrypoint": "genshin_db.release_migration.install_database_release",
            "mode": "reuse_installed" if reuse_installed_database else "install",
            "pair_operational_after_return": False,
            "requires_external_launcher": True,
        },
    }
    handoff_fingerprint = _fingerprint(handoff)
    stamp = _now()
    journal = {
        "contract": PAIR_UPDATE_JOURNAL_CONTRACT,
        "schema_version": PAIR_UPDATE_SCHEMA_VERSION,
        "operation_id": operation_id,
        "state": "handoff_ready",
        "production": production,
        "target_release_pair_id": pair["release_pair_id"],
        "target_pair_sequence": pair["pair_sequence"],
        "target_fingerprint": target_fingerprint,
        "rollback_release_pair_id": (
            rollback["pair_manifest"]["release_pair_id"] if rollback else None
        ),
        "rollback_fingerprint": (
            rollback["identity_fingerprint"] if rollback else None
        ),
        "handoff_fingerprint": handoff_fingerprint,
        "history": [{"state": "handoff_ready", "at": stamp}],
        "created_at": stamp,
        "updated_at": stamp,
    }
    # Create the fixed rollback boundary before the journal makes the handoff
    # actionable.  The launcher must populate and verify these paths before it
    # may start a swap.
    if rollback is not None:
        (root / "rollback").mkdir(exist_ok=True)
    # Handoff is durable before the journal makes it actionable.
    _write_json_atomic(_handoff_path(root), handoff)
    _write_json_atomic(_journal_path(root), journal)
    return {
        "contract": PAIR_UPDATE_HANDOFF_CONTRACT,
        "status": "handoff_ready",
        "operation_id": operation_id,
        "release_pair_id": pair["release_pair_id"],
        "pair_sequence": pair["pair_sequence"],
        "handoff_fingerprint": handoff_fingerprint,
        "pair_operational": False,
    }


def begin_pair_update_activation(
    workspace: Path,
    *,
    operation_id: str,
    active_app_root: Path | None = None,
    active_database: Path | None = None,
) -> dict[str, Any]:
    """Authorize the external swap only after an independent rollback exists."""

    root = Path(workspace).resolve(strict=True)
    journal = read_pair_update_journal(root)
    if journal is None or journal["operation_id"] != operation_id:
        _fail("release_pair_update_operation_mismatch")
    if journal["state"] == "activation_started":
        return {
            "contract": PAIR_UPDATE_JOURNAL_CONTRACT,
            "status": journal["state"],
            "operation_id": operation_id,
            "pair_operational": False,
        }
    if journal["state"] != "handoff_ready":
        _fail("release_pair_update_state_conflict", details={"state": journal["state"]})
    handoff = _read_handoff(root, journal)
    pair, database_manifest = _validated_handoff_target(handoff, journal)
    database_mode = handoff["database_subtransaction"].get("mode", "install")
    if database_mode not in {"install", "reuse_installed"}:
        _fail("release_pair_update_database_mode_invalid")
    # Re-verify candidates immediately before the external launcher may swap.
    app_relative = handoff["staging"]["app_relative"]
    independent = _independent_app(handoff["target"]["trust_assertion"])
    if app_relative is None:
        if not independent or active_app_root is None or database_mode != "install":
            _fail("release_pair_app_reuse_not_verified")
        app_root = Path(active_app_root)
    else:
        app_root = _workspace_path(root / "staging", app_relative, directory=True)
    app_verification = _verify_app(app_root, pair, independent=independent)
    if app_verification != handoff["target"]["app_verification"]:
        _fail("release_pair_update_target_identity_mismatch")
    if database_mode == "install":
        staged_database = _workspace_path(
            root / "staging",
            handoff["staging"]["database_relative"],
            directory=False,
        )
        database_verification = _verify_database_package(
            staged_database, database_manifest
        )
        if database_verification != handoff["target"]["database_verification"]:
            _fail("release_pair_update_target_identity_mismatch")
    rollback = handoff.get("rollback")
    if independent:
        _verify_product_selection(handoff["target"]["trust_assertion"], rollback,
                                  reuse_app=app_relative is None,
                                  reuse_database=database_mode == "reuse_installed")
    if rollback is not None:
        if (active_app_root is None) is not (active_database is None):
            _fail("release_pair_update_rollback_identity_mismatch")
        if active_app_root is not None and active_database is not None:
            active_identity = _validate_current(
                {
                    "app_root": Path(active_app_root),
                    "database": Path(active_database),
                    "pair_manifest": rollback["pair_manifest"],
                    "database_manifest": rollback["database_manifest"],
                    "live_receipt": rollback["live_receipt"],
                },
                production=journal["production"],
            )
            if active_identity["identity_fingerprint"] != journal["rollback_fingerprint"]:
                _fail("release_pair_update_rollback_identity_mismatch")
            if database_mode == "reuse_installed":
                expected_verification = {
                    "mode": "reuse_installed",
                    "active_database_sha256": active_identity[
                        "active_database_sha256"
                    ],
                }
                if (
                    not database_release_semantically_equal(
                        active_identity["database_manifest"], database_manifest
                    )
                    or expected_verification
                    != handoff["target"]["database_verification"]
                ):
                    _fail("release_pair_database_reuse_not_verified")
        elif database_mode == "install":
            # Legacy/offline callers may still present a complete workspace
            # snapshot.  The product helper uses the active protected pair
            # above and never treats this user-writable copy as authority.
            rollback_artifacts = handoff.get("rollback_artifacts")
            if rollback_artifacts != {
                "app_relative": "app",
                "database_relative": "genshin.db",
            }:
                _fail("release_pair_update_handoff_invalid")
            try:
                rollback_root = (root / handoff["rollback_directory"]).resolve(
                    strict=True
                )
            except OSError as error:
                raise ReleasePairUpdateError(
                    "release_pair_update_rollback_missing"
                ) from error
            if root == rollback_root or root not in rollback_root.parents:
                _fail("release_pair_update_rollback_missing")
            rollback_app_root = _workspace_path(
                rollback_root, rollback_artifacts["app_relative"], directory=True
            )
            rollback_database = _workspace_path(
                rollback_root, rollback_artifacts["database_relative"], directory=False
            )
            if not rollback_app_root.exists() or not rollback_database.exists():
                _fail("release_pair_update_rollback_missing")
            app_verification = _verify_app(
                rollback_app_root, rollback["pair_manifest"]
            )
            database_sha256 = _sha256(rollback_database)
            if (
                app_verification["manifest_sha256"]
                != rollback["app_manifest_sha256"]
                or database_sha256 != rollback["active_database_sha256"]
            ):
                _fail("release_pair_update_rollback_identity_mismatch")
        else:
            _fail("release_pair_database_reuse_not_verified")
    elif database_mode == "reuse_installed":
        _fail("release_pair_database_reuse_not_verified")
    transitioned = _write_transition(root, journal, "activation_started")
    return {
        "contract": PAIR_UPDATE_JOURNAL_CONTRACT,
        "status": transitioned["state"],
        "operation_id": operation_id,
        "pair_operational": False,
    }


def inspect_pair_update_handoff(
    workspace: Path,
    *,
    operation_id: str,
) -> dict[str, Any]:
    """Return one validated handoff to the external updater process.

    The returned absolute paths are derived from already-bounded relative
    paths only after revalidating the journal, handoff fingerprint and staged
    artifacts.  This function is read-only and does not authorize activation.
    """

    root = Path(workspace).resolve(strict=True)
    journal = read_pair_update_journal(root)
    if journal is None or journal["operation_id"] != operation_id:
        _fail("release_pair_update_operation_mismatch")
    if journal["state"] not in {"handoff_ready", "activation_started"}:
        _fail("release_pair_update_state_conflict", details={"state": journal["state"]})
    handoff = _read_handoff(root, journal)
    pair, database = _validated_handoff_target(handoff, journal)
    database_mode = handoff["database_subtransaction"].get("mode", "install")
    if database_mode not in {"install", "reuse_installed"}:
        _fail("release_pair_update_database_mode_invalid")
    app_relative = handoff["staging"]["app_relative"]
    independent = _independent_app(handoff["target"]["trust_assertion"])
    staged_app = None
    # Read-only inspection still verifies bytes so a helper cannot receive a
    # stale path after consent and silently swap a different artifact.
    if app_relative is not None:
        staged_app = _workspace_path(root / "staging", app_relative, directory=True)
        _verify_app(staged_app, pair, independent=independent)
    elif not independent or database_mode != "install" or handoff.get("rollback") is None:
        _fail("release_pair_app_reuse_not_verified")
    staged_database = None
    if database_mode == "install":
        staged_database = _workspace_path(
            root / "staging",
            handoff["staging"]["database_relative"],
            directory=False,
        )
        _verify_database_package(staged_database, database)
    elif handoff["staging"]["database_relative"] is not None:
        _fail("release_pair_update_database_mode_invalid")
    return {
        "operation_id": operation_id,
        "state": journal["state"],
        "production": journal["production"],
        "staged_app_root": staged_app,
        "independent_app": independent,
        "staged_database": staged_database,
        "database_mode": database_mode,
        "pair_manifest": pair,
        "database_manifest": database,
        "rollback_required": handoff.get("rollback") is not None,
        "expected_current_fingerprint": journal["rollback_fingerprint"],
    }


def _load_accepted_database(data_dir: Path) -> tuple[dict, dict, dict]:
    manifest_value = _read_json(
        data_dir / LIVE_DATABASE_MANIFEST_FILENAME,
        "release_pair_live_database_manifest_invalid",
    )
    receipt_value = _read_json(
        data_dir / LIVE_DATABASE_RECEIPT_FILENAME,
        "release_pair_live_database_receipt_invalid",
    )
    try:
        manifest = validate_database_release_manifest(manifest_value)
        receipt = validate_database_live_receipt(receipt_value)
        identity = read_database_identity(data_dir / "genshin.db")
        matches = database_live_receipt_matches(receipt, identity, manifest)
    except DatabaseReleaseError as error:
        raise ReleasePairUpdateError(error.reason) from error
    if not matches:
        _fail("release_pair_live_database_identity_mismatch")
    return manifest, receipt, identity


@serialized_profile_writer()
def postcheck_pair_update(
    workspace: Path,
    active_app_root: Path,
    data_dir: Path,
    *,
    operation_id: str,
) -> dict[str, Any]:
    """Verify active paths and publish the pair identity as the final marker."""

    root = Path(workspace).resolve(strict=True)
    journal = read_pair_update_journal(root)
    if journal is None or journal["operation_id"] != operation_id:
        _fail("release_pair_update_operation_mismatch")
    if journal["state"] == "operational":
        handoff = _read_handoff(root, journal)
        return {
            "contract": PAIR_UPDATE_JOURNAL_CONTRACT,
            "status": journal["state"],
            "operation_id": operation_id,
            "release_pair_id": handoff["target"]["pair_manifest"][
                "release_pair_id"
            ],
            "pair_operational": True,
        }
    if journal["state"] not in {"activation_started", "postchecked", "committed"}:
        _fail("release_pair_update_state_conflict", details={"state": journal["state"]})
    handoff = _read_handoff(root, journal)
    pair, target_database = _validated_handoff_target(handoff, journal)
    trust = handoff["target"]["trust_assertion"]
    independent = _independent_app(trust)
    _verify_app(Path(active_app_root), pair, independent=independent)
    accepted_manifest, _receipt, identity = _load_accepted_database(
        Path(data_dir).resolve(strict=True)
    )
    if (accepted_manifest != target_database
            or (independent and identity["sha256"] != target_database["database"]["sha256"])):
        _fail("release_pair_postcheck_database_manifest_mismatch")
    if journal["state"] == "activation_started":
        journal = _write_transition(root, journal, "postchecked")
    # The complete pair is committed only after both active paths passed their
    # own checks.  Database migration deliberately cannot write this marker.
    if independent:
        _write_json_atomic(Path(data_dir) / LIVE_PAIR_PROOF_FILENAME, trust["signed_document"])
    _write_json_atomic(Path(data_dir) / LIVE_PAIR_MANIFEST_FILENAME, pair)
    if journal["state"] == "postchecked":
        journal = _write_transition(root, journal, "committed")
    journal = _write_transition(root, journal, "operational")
    return {
        "contract": PAIR_UPDATE_JOURNAL_CONTRACT,
        "status": journal["state"],
        "operation_id": operation_id,
        "release_pair_id": pair["release_pair_id"],
        "pair_operational": True,
    }


@serialized_profile_writer()
def confirm_pair_update_rollback(
    workspace: Path,
    active_app_root: Path,
    data_dir: Path,
    *,
    operation_id: str,
) -> dict[str, Any]:
    """Accept an externally restored old pair only after exact verification."""

    root = Path(workspace).resolve(strict=True)
    journal = read_pair_update_journal(root)
    if journal is None or journal["operation_id"] != operation_id:
        _fail("release_pair_update_operation_mismatch")
    if journal["state"] not in {"activation_started", "postchecked", "committed"}:
        _fail("release_pair_update_state_conflict", details={"state": journal["state"]})
    handoff = _read_handoff(root, journal)
    _validated_handoff_target(handoff, journal)
    rollback = handoff.get("rollback")
    if rollback is None:
        _fail("release_pair_update_rollback_unavailable")
    pair = rollback["pair_manifest"]
    independent = read_live_product_proof(Path(data_dir), pair, rollback["database_manifest"]) is not None
    _verify_app(Path(active_app_root), pair, independent=independent)
    accepted_manifest, receipt, identity = _load_accepted_database(
        Path(data_dir).resolve(strict=True)
    )
    if (
        accepted_manifest != rollback["database_manifest"]
        or receipt != rollback["live_receipt"]
        or identity["sha256"] != rollback["active_database_sha256"]
    ):
        _fail("release_pair_update_rollback_identity_mismatch")
    _write_json_atomic(Path(data_dir) / LIVE_PAIR_MANIFEST_FILENAME, pair)
    journal = _write_transition(root, journal, "rolled_back")
    return {
        "contract": PAIR_UPDATE_JOURNAL_CONTRACT,
        "status": journal["state"],
        "operation_id": operation_id,
        "release_pair_id": pair["release_pair_id"],
        "pair_operational": True,
    }


def complete_pair_update_cleanup(
    workspace: Path,
    *,
    operation_id: str,
) -> dict[str, Any]:
    """Remove only coordinator-owned transient trees after a terminal outcome."""

    root = Path(workspace).resolve(strict=True)
    journal = read_pair_update_journal(root)
    if journal is None or journal["operation_id"] != operation_id:
        _fail("release_pair_update_operation_mismatch")
    if journal["state"] == "cleanup_complete":
        return {
            "contract": PAIR_UPDATE_JOURNAL_CONTRACT,
            "status": journal["state"],
            "operation_id": operation_id,
            "diagnostic_artifacts_retained": [],
            "pair_operational": True,
        }
    if journal["state"] not in {"operational", "rolled_back"}:
        _fail("release_pair_update_state_conflict", details={"state": journal["state"]})
    terminal_state = journal["state"]
    retained: list[str] = []
    diagnostic_rollback = (
        root / "diagnostics" / f"{journal['operation_id']}-rollback"
    )
    for name in ("staging", "rollback"):
        path = root / name
        if not path.exists():
            # A crash can happen after the development rollback was archived
            # but before the journal transition.  Treat that move as durable
            # and finish cleanup idempotently on the next launch.
            if (
                name == "rollback"
                and not journal["production"]
                and diagnostic_rollback.exists()
            ):
                resolved_diagnostic = diagnostic_rollback.resolve(strict=True)
                if (
                    root == resolved_diagnostic
                    or root not in resolved_diagnostic.parents
                    or _is_reparse_point(diagnostic_rollback)
                ):
                    _fail("release_pair_cleanup_path_invalid")
                retained.append(resolved_diagnostic.relative_to(root).as_posix())
            continue
        resolved = path.resolve(strict=True)
        if root == resolved or root not in resolved.parents or _is_reparse_point(path):
            _fail("release_pair_cleanup_path_invalid")
        if journal["production"] or name == "staging":
            shutil.rmtree(resolved)
        else:
            diagnostics = root / "diagnostics"
            if diagnostics.exists() and _is_reparse_point(diagnostics):
                _fail("release_pair_cleanup_path_invalid")
            diagnostics.mkdir(exist_ok=True)
            if diagnostic_rollback.exists():
                _fail("release_pair_cleanup_destination_exists")
            try:
                os.replace(resolved, diagnostic_rollback)
            except OSError as error:
                raise ReleasePairUpdateError("release_pair_cleanup_failed") from error
            retained.append(diagnostic_rollback.relative_to(root).as_posix())
    journal = _write_transition(root, journal, "cleanup_complete")
    return {
        "contract": PAIR_UPDATE_JOURNAL_CONTRACT,
        "status": journal["state"],
        "operation_id": operation_id,
        "diagnostic_artifacts_retained": retained,
        "pair_operational": terminal_state in {"operational", "rolled_back"},
    }


__all__ = [
    "LIVE_PAIR_MANIFEST_FILENAME",
    "PAIR_UPDATE_HANDOFF_CONTRACT",
    "PAIR_UPDATE_HANDOFF_FILENAME",
    "PAIR_UPDATE_JOURNAL_CONTRACT",
    "PAIR_UPDATE_JOURNAL_FILENAME",
    "PAIR_UPDATE_REMOTE_TRUST_CONTRACT",
    "PAIR_UPDATE_TRUST_CONTRACT",
    "ReleasePairUpdateError",
    "begin_pair_update_activation",
    "complete_pair_update_cleanup",
    "confirm_pair_update_rollback",
    "create_local_review_trust_assertion",
    "create_signed_discovery_trust_assertion",
    "inspect_active_release_pair",
    "inspect_pair_update_handoff",
    "postcheck_pair_update",
    "prepare_release_pair_handoff",
    "read_pair_update_journal",
]
