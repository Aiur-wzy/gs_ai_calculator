"""Local App/database startup admission.

The release-profile module owns database identity and pair classification.  This
module is the deliberately small App adapter which turns that classification
into a fail-closed startup decision.  It never performs network I/O, downloads
an update, migrates a database, or opens an explicitly supplied acceptance
database.

Admission is synchronous here so that callers can decide how to present
progress. The GUI launcher runs it from pywebview's
startup callback while a small startup page is visible; engine, data service
and Agent construction happen only after this function returns ``can_start``.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from genshin_app.database_legacy_processes import packaged_peer_processes
from genshin_app.database_transaction import (
    journal_pending,
    owns_activation,
    profile_writer,
    read_activation_journal,
)
from genshin_app.package_integrity import (
    PackageIntegrityError,
    read_signed_package_root_file,
    verify_package_artifact,
    verify_signed_package_startup,
)
from genshin_app.paths import is_frozen, resource_dir
from genshin_app.version import (
    APP_RELEASE_ID,
    APP_VERSION,
    DATABASE_DATA_CONTRACT_VERSION,
    DATABASE_LOGIC_PROFILE,
    DATABASE_PAIR_POLICY,
    DATABASE_RELEASE_CHANNEL,
    DATABASE_RELEASE_ID,
    RELEASE_PAIR_ID,
)
from genshin_db.locking import LockBusyError
from genshin_db.release_profile import (
    LIVE_RECEIPT_FILENAME,
    MANIFEST_FILENAME,
    PAIR_MANIFEST_FILENAME,
    DatabaseReleaseError,
    create_database_live_receipt,
    current_only_combination_fingerprint,
    database_live_receipt_matches,
    database_release_semantically_equal,
    inspect_database_profile,
    load_database_release_manifest,
    load_release_pair_manifest,
    release_pair_matches_database_manifest,
    validate_database_live_receipt,
    validate_database_release_manifest,
    validate_release_pair_manifest,
)

STARTUP_GATE_CONTRACT = "genshin-database-startup-gate-v1"
LIVE_MANIFEST_FILENAME = "database-live-manifest.json"
LIVE_PAIR_MANIFEST_FILENAME = "release-pair-live-manifest.json"
MIGRATION_JOURNAL_FILENAME = "database-release-migration-journal.json"
PACKAGED_DATABASE_QUALIFICATION_FILENAME = "release-database-qualification.json"
_MAX_CONTROL_FILE_BYTES = 4 * 1024 * 1024
_READY_STATES = {
    "exact": frozenset({"current_pair_ready"}),
    "current_only": frozenset({"current_dev_ready"}),
}
_VERIFIED_BUNDLED_CATALOG_SOURCE: tuple[Path, dict, dict] | None = None

ProgressCallback = Callable[[dict[str, str]], None]


class DatabaseStartupError(RuntimeError):
    """Structured, public-safe startup refusal."""

    def __init__(
        self,
        reason: str,
        message: str | None = None,
        details: Mapping[str, Any] | None = None,
    ):
        self.reason = reason
        self.details = copy.deepcopy(dict(details)) if details is not None else None
        super().__init__(message or reason)


def _fail(
    reason: str,
    message: str | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    raise DatabaseStartupError(reason, message, details)


def _progress(callback: ProgressCallback | None, stage: str, message: str) -> None:
    if callback is None:
        return
    callback(
        {
            "contract": STARTUP_GATE_CONTRACT,
            "status": "checking",
            "stage": stage,
            "message": message,
        }
    )


def _bounded_json_object(path: Path, reason: str) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        if size < 2 or size > _MAX_CONTROL_FILE_BYTES:
            _fail(reason)
        value = json.loads(path.read_text(encoding="utf-8"))
    except DatabaseStartupError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatabaseStartupError(reason) from error
    if type(value) is not dict:
        _fail(reason)
    return value


def _optional_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    _bounded_json_object(path, "database_release_manifest_unreadable")
    try:
        return load_database_release_manifest(path)
    except DatabaseReleaseError as error:
        raise DatabaseStartupError(error.reason) from error


def _optional_pair_manifest(path: Path) -> dict | None:
    if not path.exists():
        return None
    _bounded_json_object(path, "release_pair_manifest_unreadable")
    try:
        return load_release_pair_manifest(path)
    except DatabaseReleaseError as error:
        raise DatabaseStartupError(error.reason) from error


def _optional_receipt(path: Path) -> dict | None:
    if not path.exists():
        return None
    value = _bounded_json_object(path, "database_live_receipt_unreadable")
    try:
        return validate_database_live_receipt(value)
    except DatabaseReleaseError as error:
        raise DatabaseStartupError(error.reason) from error


def _journal_state(data_dir: Path) -> str | None:
    path = data_dir / MIGRATION_JOURNAL_FILENAME
    if not path.exists():
        return None
    value = _bounded_json_object(path, "database_migration_journal_unreadable")
    state = value.get("state")
    if type(state) is not str or not state:
        _fail("database_migration_journal_state_invalid")
    return state


def _artifact_directory(root: Path) -> Path | None:
    """Locate reviewed local artifacts without consulting a network source."""

    candidates = (root / "bundled", root / "build-py")
    for directory in candidates:
        if (directory / MANIFEST_FILENAME).is_file():
            return directory
    return None


def _validate_pair_for_app(
    pair: Mapping[str, Any],
    database_manifest: Mapping[str, Any],
    *,
    pair_policy: str,
) -> dict:
    try:
        normalized = validate_release_pair_manifest(dict(pair))
        database = validate_database_release_manifest(dict(database_manifest))
        matches = release_pair_matches_database_manifest(normalized, database)
    except DatabaseReleaseError as error:
        raise DatabaseStartupError(error.reason) from error
    if not matches:
        _fail("release_pair_database_manifest_mismatch")
    app = normalized["app"]
    expected = {
        "app_release_id": APP_RELEASE_ID,
        "app_version": APP_VERSION,
        "required_database_release_id": DATABASE_RELEASE_ID,
        "logic_profile": DATABASE_LOGIC_PROFILE,
        "data_contract_version": DATABASE_DATA_CONTRACT_VERSION,
    }
    actual = {field: app.get(field) for field in expected}
    if (
        normalized["release_pair_id"] != RELEASE_PAIR_ID
        or normalized["pair_policy"] != pair_policy
        or actual != expected
    ):
        _fail(
            "release_pair_app_contract_mismatch",
            details={
                "expected_release_pair_id": RELEASE_PAIR_ID,
                "actual_release_pair_id": normalized.get("release_pair_id"),
                "expected_pair_policy": pair_policy,
                "actual_pair_policy": normalized.get("pair_policy"),
                "expected_app": expected,
                "actual_app": actual,
            },
        )
    return normalized


def _validate_pair_for_database(
    pair: Mapping[str, Any],
    database_manifest: Mapping[str, Any],
    *,
    pair_policy: str,
) -> dict:
    """Validate a prior App pair without requiring the current App identity.

    A newer App may deliberately reuse one immutable database release. The old
    live pair is still evidence that the existing sidecars formed a coherent
    commit, but its App fields must not be compared with the new App until the
    package pair has been admitted as the successor.
    """

    try:
        normalized = validate_release_pair_manifest(dict(pair))
        database = validate_database_release_manifest(dict(database_manifest))
        matches = release_pair_matches_database_manifest(normalized, database)
    except DatabaseReleaseError as error:
        raise DatabaseStartupError(error.reason) from error
    if not matches:
        _fail("release_pair_database_manifest_mismatch")
    if normalized["pair_policy"] != pair_policy:
        _fail(
            "release_pair_app_contract_mismatch",
            details={
                "expected_pair_policy": pair_policy,
                "actual_pair_policy": normalized.get("pair_policy"),
            },
        )
    return normalized


def _admit_packaged_pair_successor(
    live_pair: Mapping[str, Any],
    bundled_pair: Mapping[str, Any],
) -> None:
    """Reject rollback and same-sequence forks before rebinding a live pair."""

    live_sequence = live_pair["pair_sequence"]
    bundled_sequence = bundled_pair["pair_sequence"]
    if live_sequence > bundled_sequence:
        _fail(
            "bundled_release_pair_downgrade_forbidden",
            details={
                "current_sequence": live_sequence,
                "target_sequence": bundled_sequence,
            },
        )
    if live_sequence == bundled_sequence and dict(live_pair) != dict(bundled_pair):
        _fail(
            "bundled_release_pair_sequence_conflict",
            details={"sequence": live_sequence},
        )


def _verify_app_artifact_manifest(
    pair: Mapping[str, Any],
    package_root: Path,
    *,
    signed_startup: bool = False,
) -> str:
    """Bind this running package to the content inventory named by PairManifest."""

    artifact = pair["app"]["artifact_manifest"]
    try:
        if signed_startup:
            verified = verify_signed_package_startup(Path(package_root), artifact["sha256"])
        else:
            verified = verify_package_artifact(Path(package_root))
    except PackageIntegrityError as error:
        raise DatabaseStartupError(error.code) from error
    digest = verified["manifest_sha256"]
    if digest != artifact["sha256"]:
        _fail("release_pair_app_artifact_mismatch")
    return digest


def _remember_bundled_catalog_source(package_root: Path, pair: dict, database: dict) -> None:
    """Retain the identity already verified by this process's bundled admission."""
    global _VERIFIED_BUNDLED_CATALOG_SOURCE
    previous = _VERIFIED_BUNDLED_CATALOG_SOURCE
    source = (package_root, pair, database)
    if previous is not None and previous[0] == package_root:
        if previous != source:
            _fail("database_bundled_catalog_source_changed")
        return
    _VERIFIED_BUNDLED_CATALOG_SOURCE = copy.deepcopy(source)


def matches_verified_bundled_catalog_source(database: dict, *, live_pair: dict | None = None) -> bool:
    """Verify portable catalog authority from this exact package, without opening SQLite."""
    if not is_frozen() or DATABASE_PAIR_POLICY != "exact":
        return False
    from genshin_app.windows_known_folders import reject_reparse_chain

    resources, policy, app_root, _ = _resolve_startup_flavor(
        resource_root=None, pair_policy=None, app_package_root=None,
    )
    bundle = resources / "bundled"
    pair_path = bundle / PAIR_MANIFEST_FILENAME
    manifest_path = bundle / MANIFEST_FILENAME
    if not (os.path.lexists(pair_path) or os.path.lexists(manifest_path)):
        return False

    def read_source() -> tuple[dict, dict]:
        pair = _optional_pair_manifest(reject_reparse_chain(pair_path, reason="database_bundled_pair_invalid"))
        manifest = _optional_manifest(reject_reparse_chain(manifest_path, reason="database_bundled_pair_invalid"))
        if pair is None or manifest is None:
            _fail("database_bundled_pair_missing")
        return _validate_pair_for_app(pair, manifest, pair_policy=policy), manifest

    pair, manifest = read_source()
    if pair["channel"] != DATABASE_RELEASE_CHANNEL or database != manifest or (live_pair is not None and live_pair != pair):
        return False
    previous = _VERIFIED_BUNDLED_CATALOG_SOURCE
    if previous is None or previous[0] != app_root:
        # A cold review can run without DB startup. Verify the packaged
        # anchor once, using the same inventory check as portable startup.
        _verify_app_artifact_manifest(pair, app_root)
        if read_source() != (pair, manifest):
            _fail("database_bundled_catalog_source_changed")
    _remember_bundled_catalog_source(app_root, pair, manifest)
    return True


def _read_packaged_database_qualification(package_root, manifest_sha256, live_manifest):
    from genshin_db.release_qualification import load_database_install_qualification

    reason = "database_install_qualification_invalid"
    try:
        raw = read_signed_package_root_file(
            package_root, manifest_sha256, PACKAGED_DATABASE_QUALIFICATION_FILENAME,
            maximum_bytes=16 * 1024,
        )
        if raw is None:
            return None
        proof = json.loads(raw)
        if (
            type(proof) is not dict
            or type(proof.get("database_release_id")) is not str
            or type(proof.get("sha256")) is not str
        ):
            _fail(reason)
        if (
            proof["database_release_id"] != live_manifest["database_release_id"]
            or proof["sha256"] != live_manifest["database"]["sha256"]
        ):
            return None
        return load_database_install_qualification(proof, live_manifest)
    except PackageIntegrityError as error:
        raise DatabaseStartupError(error.code) from error
    except DatabaseReleaseError as error:
        raise DatabaseStartupError(error.reason) from error
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise DatabaseStartupError(reason) from error


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        raw = (
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _can_seed_exact_sidecars(
    identity: Mapping[str, Any],
    bundled_manifest: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
) -> bool:
    database = bundled_manifest["database"]
    exact_package = (
        identity.get("sha256") == database.get("sha256")
        and identity.get("meta_fingerprint") == database.get("meta_fingerprint")
    )
    if exact_package:
        return True
    if receipt is None:
        return False
    try:
        return database_live_receipt_matches(receipt, identity, bundled_manifest)
    except DatabaseReleaseError:
        return False


def _seed_exact_live_sidecars(
    data_dir: Path,
    *,
    bundled_manifest: Mapping[str, Any],
    bundled_pair: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> dict:
    """Publish local identities; pair is last and therefore the commit marker."""

    _require_quiescent_identity_writer()
    try:
        receipt = create_database_live_receipt(
            bundled_manifest,
            identity,
            installed_at=_now(),
        )
        manifest = validate_database_release_manifest(dict(bundled_manifest))
        pair = validate_release_pair_manifest(dict(bundled_pair))
        _write_json_atomic(data_dir / LIVE_MANIFEST_FILENAME, manifest)
        _write_json_atomic(data_dir / LIVE_RECEIPT_FILENAME, receipt)
        _write_json_atomic(data_dir / LIVE_PAIR_MANIFEST_FILENAME, pair)
    except (DatabaseReleaseError, OSError) as error:
        reason = getattr(error, "reason", "database_startup_sidecar_write_failed")
        raise DatabaseStartupError(reason) from error
    return receipt


def _profile_failure(profile: Mapping[str, Any]) -> None:
    reasons = profile.get("reasons")
    reason = (
        reasons[0]
        if type(reasons) is list and reasons and type(reasons[0]) is str
        else "database_startup_not_ready"
    )
    _fail(
        reason,
        details={
            "state": profile.get("state"),
            "reasons": copy.deepcopy(reasons),
            "current_release_id": profile.get("current_release_id"),
            "available_release_id": profile.get("available_release_id"),
            "missing_components": copy.deepcopy(profile.get("missing_components")),
        },
    )


def _require_quiescent_identity_writer():
    if not is_frozen():
        return
    try:
        peers = packaged_peer_processes()
    except OSError as error:
        raise DatabaseStartupError("database_legacy_instance_check_failed", "无法核对其他应用实例，请关闭其他版本后重试。") from error
    if peers:
        _fail("database_legacy_instance_running", "请先自行关闭其他原神拉表窗口，再重新检查。")


def _resolve_startup_flavor(
    *,
    resource_root: Path | None,
    pair_policy: str | None,
    app_package_root: Path | None,
) -> tuple[Path, str, Path, bool]:
    """Resolve immutable build pins without permitting frozen call-site overrides."""

    frozen = is_frozen()
    if frozen:
        if any(
            value is not None
            for value in (resource_root, pair_policy, app_package_root)
        ):
            _fail("frozen_database_startup_override_forbidden")
        allowed = {
            "stable": "exact",
            "dev": "current_only",
        }
        expected = allowed.get(DATABASE_RELEASE_CHANNEL)
        if expected is None or DATABASE_PAIR_POLICY != expected:
            _fail(
                "frozen_release_flavor_invalid",
                details={
                    "database_release_channel": DATABASE_RELEASE_CHANNEL,
                    "database_pair_policy": DATABASE_PAIR_POLICY,
                },
            )
        return (
            resource_dir(),
            expected,
            Path(sys.executable).resolve().parent,
            True,
        )

    root = Path(resource_root) if resource_root is not None else resource_dir()
    policy = pair_policy or DATABASE_PAIR_POLICY
    package_root = Path(app_package_root) if app_package_root is not None else root
    return root, policy, package_root, False


def _require_frozen_artifact_flavor(
    *,
    frozen: bool,
    bundled_manifest: Mapping[str, Any] | None,
    bundled_pair: Mapping[str, Any] | None,
    live_manifest: Mapping[str, Any] | None,
    live_pair: Mapping[str, Any] | None,
) -> None:
    if not frozen:
        return
    artifacts = {
        "bundled_manifest": bundled_manifest,
        "bundled_pair": bundled_pair,
        "live_manifest": live_manifest,
        "live_pair": live_pair,
    }
    mismatched = sorted(
        name
        for name, value in artifacts.items()
        if value is not None and value.get("channel") != DATABASE_RELEASE_CHANNEL
    )
    if mismatched:
        _fail(
            "frozen_release_artifact_flavor_mismatch",
            details={
                "expected_channel": DATABASE_RELEASE_CHANNEL,
                "mismatched_artifacts": mismatched,
            },
        )


def _prepare_database_startup_locked(
    data_dir: Path,
    *,
    resource_root: Path | None = None,
    pair_policy: str | None = None,
    app_package_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Validate the local App/database contract before runtime startup.

    The explicit external-database acceptance route must not call this function:
    that route is intentionally read-only and is labelled as a bypass by the GUI
    launcher.  Normal startup has no bypass.
    """

    started = perf_counter()
    directory = Path(data_dir)
    try:
        pending = read_activation_journal(directory)
    except (OSError, ValueError) as error:
        raise DatabaseStartupError("database_activation_journal_invalid") from error
    if journal_pending(pending) and not owns_activation(directory):
        _fail("database_activation_recovery_required", details={"activation_state": pending["state"]})
    database = directory / "genshin.db"
    root, policy, package_root, frozen = _resolve_startup_flavor(
        resource_root=resource_root,
        pair_policy=pair_policy,
        app_package_root=app_package_root,
    )
    if policy not in _READY_STATES:
        _fail("release_pair_policy_invalid")

    _progress(progress, "control_files", "正在读取本地数据库版本凭据")
    artifact_dir = _artifact_directory(root)
    bundled_manifest = (
        _optional_manifest(artifact_dir / MANIFEST_FILENAME)
        if artifact_dir is not None
        else None
    )
    bundled_pair = (
        _optional_pair_manifest(artifact_dir / PAIR_MANIFEST_FILENAME)
        if artifact_dir is not None
        else None
    )
    live_manifest = _optional_manifest(directory / LIVE_MANIFEST_FILENAME)
    live_receipt = _optional_receipt(directory / LIVE_RECEIPT_FILENAME)
    live_pair = _optional_pair_manifest(directory / LIVE_PAIR_MANIFEST_FILENAME)
    journal_state = _journal_state(directory)
    app_check_started = perf_counter()
    signed_product_pair = False
    qualification = None
    if policy == "exact":
        from genshin_app.release_pair_update import (
            LIVE_PAIR_PROOF_FILENAME,
            ReleasePairUpdateError,
            qualified_database_from_signed_document,
            read_live_product_proof,
        )

        if (directory / LIVE_PAIR_PROOF_FILENAME).exists():
            if live_pair is None or live_manifest is None or live_receipt is None:
                _fail("database_live_pair_incomplete")
            try:
                signed_document = read_live_product_proof(directory, live_pair, live_manifest)
                if signed_document is None:
                    _fail("product_release_live_pair_proof_invalid")
                qualification = qualified_database_from_signed_document(signed_document)
            except (DatabaseReleaseError, ReleasePairUpdateError) as error:
                raise DatabaseStartupError(error.reason) from error
            app = live_pair["app"]
            if (app["app_release_id"], app["app_version"], app["logic_profile"], app["data_contract_version"]) != (
                APP_RELEASE_ID, APP_VERSION, DATABASE_LOGIC_PROFILE, DATABASE_DATA_CONTRACT_VERSION,
            ) or live_pair["pair_policy"] != policy or live_pair["channel"] != DATABASE_RELEASE_CHANNEL:
                _fail("release_pair_app_contract_mismatch")
            if bundled_pair is not None or bundled_manifest is not None:
                _fail("release_pair_independent_app_contains_bundled_data")
            _progress(progress, "app_integrity", "正在校验应用程序")
            manifest_digest = _verify_app_artifact_manifest(live_pair, package_root, signed_startup=True)
            if qualification is None:
                qualification = _read_packaged_database_qualification(
                    package_root, manifest_digest, live_manifest,
                )
            signed_product_pair = True
    _require_frozen_artifact_flavor(
        frozen=frozen,
        bundled_manifest=bundled_manifest,
        bundled_pair=bundled_pair,
        live_manifest=live_manifest,
        live_pair=live_pair,
    )

    packaged_pair_required = policy == "exact" or frozen
    if packaged_pair_required and not signed_product_pair:
        if bundled_manifest is None or bundled_pair is None:
            _fail("database_bundled_pair_missing")
        bundled_pair = _validate_pair_for_app(
            bundled_pair,
            bundled_manifest,
            pair_policy=policy,
        )
        _progress(progress, "app_integrity", "正在完整校验应用程序")
        _verify_app_artifact_manifest(bundled_pair, package_root)
    app_seconds = perf_counter() - app_check_started

    if policy == "exact" and not signed_product_pair:
        # The packaged PairManifest is the App/database compatibility trust
        # anchor.  Database manifests describe database content only, so an
        # older manifest schema or another reviewed SQLite packaging of the
        # same semantic release must not force a fake database release bump.
        if live_manifest is not None and not database_release_semantically_equal(
            live_manifest,
            bundled_manifest,
        ):
            _fail("database_live_manifest_not_bundled_release")
        if live_pair is not None and live_pair != bundled_pair:
            _fail("database_live_pair_not_bundled_release")

    inspection_options = {}
    if qualification is not None:
        from genshin_db.release_qualification import read_qualified_database_identity

        def read_identity(path: Path) -> dict:
            return read_qualified_database_identity(path, live_manifest, qualification)

        inspection_options["identity_reader"] = read_identity

    _progress(
        progress,
        "database_integrity",
        (
            "正在核对已签名数据库并检查能否打开"
            if qualification is not None
            else "正在校验数据库结构、必需资料与发行核心"
        ),
    )
    database_check_started = perf_counter()
    try:
        profile = inspect_database_profile(
            database,
            logic_profile=DATABASE_LOGIC_PROFILE,
            data_contract_version=DATABASE_DATA_CONTRACT_VERSION,
            app_release_id=APP_RELEASE_ID,
            release_pair_id=live_pair["release_pair_id"] if signed_product_pair else RELEASE_PAIR_ID,
            required_database_release_id=live_manifest["database_release_id"] if signed_product_pair else DATABASE_RELEASE_ID,
            pair_policy=policy,
            app_version=APP_VERSION,
            live_manifest=live_manifest,
            bundled_manifest=bundled_manifest,
            live_receipt=live_receipt,
            journal_state=journal_state,
            **inspection_options,
        )
    except DatabaseReleaseError as error:
        raise DatabaseStartupError(error.reason) from error
    database_seconds = perf_counter() - database_check_started

    initialized_sidecars = False
    rebound_live_pair = False
    selected_pair: dict[str, Any] | None = None
    pending_pair_rebind = False
    if policy == "exact":
        presence = (live_manifest is not None, live_receipt is not None, live_pair is not None)
        if not all(presence):
            identity = profile.get("live")
            if (
                type(identity) is not dict
                or not _can_seed_exact_sidecars(
                    identity,
                    bundled_manifest,
                    live_receipt,
                )
            ):
                _fail(
                    "database_live_pair_incomplete",
                    details={
                        "live_manifest": presence[0],
                        "live_receipt": presence[1],
                        "live_pair_manifest": presence[2],
                    },
                )
            live_receipt = _seed_exact_live_sidecars(
                directory,
                bundled_manifest=bundled_manifest,
                bundled_pair=bundled_pair,
                identity=identity,
            )
            live_manifest = copy.deepcopy(bundled_manifest)
            live_pair = copy.deepcopy(bundled_pair)
            initialized_sidecars = True

        if not signed_product_pair:
            live_pair = _validate_pair_for_app(live_pair, live_manifest, pair_policy=policy)
        if not signed_product_pair and (
            not database_release_semantically_equal(
                live_manifest,
                bundled_manifest,
            )
            or live_pair != bundled_pair
        ):
            _fail("database_live_pair_not_bundled_release")
        selected_pair = live_pair
        identity = profile.get("live")
        if signed_product_pair and (not isinstance(identity, dict)
                or identity.get("sha256") != live_manifest["database"]["sha256"]):
            _fail(
                "database_live_signed_artifact_mismatch",
                details={"state": profile.get("state"), "reasons": profile.get("reasons")},
            )
        try:
            receipt_matches = (
                type(identity) is dict
                and database_live_receipt_matches(
                    live_receipt,
                    identity,
                    live_manifest,
                )
            )
        except DatabaseReleaseError:
            receipt_matches = False
        if not receipt_matches:
            _fail("database_live_receipt_manifest_mismatch")
    else:
        # PairManifest is optional for repository current-only runs, but any
        # present pair is authoritative and therefore must validate completely.
        selected_manifest = live_manifest or bundled_manifest
        if frozen:
            # The frozen package pair was already bound to the complete App
            # inventory above. When only the App/pair advanced, a valid prior
            # live pair is a predecessor marker, not a reason to reject an
            # otherwise identical immutable database release.
            if (
                selected_manifest is None
                or bundled_manifest is None
                or bundled_pair is None
            ):
                _fail("database_bundled_pair_missing")
            if not database_release_semantically_equal(
                selected_manifest,
                bundled_manifest,
            ):
                _fail("database_live_manifest_not_bundled_release")
            if live_pair is not None:
                if live_manifest is None:
                    _fail("database_live_pair_incomplete")
                live_pair = _validate_pair_for_database(
                    live_pair,
                    live_manifest,
                    pair_policy=policy,
                )
                _admit_packaged_pair_successor(live_pair, bundled_pair)
            selected_pair = bundled_pair
            pending_pair_rebind = (
                live_manifest is not None
                and (
                    live_pair is None
                    or live_pair != bundled_pair
                    or live_manifest != bundled_manifest
                )
            )
        else:
            selected_pair = live_pair
            if selected_manifest is not None:
                try:
                    if (
                        selected_pair is None
                        and bundled_pair is not None
                        and release_pair_matches_database_manifest(
                            bundled_pair,
                            selected_manifest,
                        )
                    ):
                        selected_pair = bundled_pair
                except DatabaseReleaseError as error:
                    raise DatabaseStartupError(error.reason) from error
            if selected_pair is not None:
                if selected_manifest is None:
                    _fail("release_pair_database_manifest_missing")
                selected_pair = _validate_pair_for_app(
                    selected_pair,
                    selected_manifest,
                    pair_policy=policy,
                )

    state = profile.get("state")
    if state not in _READY_STATES[policy] or profile.get("can_continue_current") is not True:
        _profile_failure(profile)

    if pending_pair_rebind:
        # Automatic rebinding is deliberately narrower than a database update:
        # the accepted manifest bytes and SQLite identity must already match.
        # A changed database manifest continues through the explicit activation
        # or update transaction instead of being rewritten during startup.
        if live_manifest != bundled_manifest or live_receipt is None:
            _fail("database_live_manifest_rebind_required")
        identity = profile.get("live")
        try:
            receipt_matches = (
                type(identity) is dict
                and database_live_receipt_matches(
                    live_receipt,
                    identity,
                    live_manifest,
                )
            )
        except DatabaseReleaseError:
            receipt_matches = False
        if not receipt_matches:
            _fail("database_reuse_live_identity_mismatch")
        try:
            _require_quiescent_identity_writer()
            _write_json_atomic(
                directory / LIVE_PAIR_MANIFEST_FILENAME,
                selected_pair,
            )
        except OSError as error:
            raise DatabaseStartupError(
                "database_startup_sidecar_write_failed"
            ) from error
        live_pair = copy.deepcopy(selected_pair)
        rebound_live_pair = True

    if frozen and live_manifest is None and live_receipt is None and live_pair is None:
        identity = profile.get("live")
        if type(identity) is not dict or not _can_seed_exact_sidecars(identity, bundled_manifest, None):
            _fail("database_live_pair_incomplete")
        _seed_exact_live_sidecars(directory, bundled_manifest=bundled_manifest,
            bundled_pair=bundled_pair, identity=identity)
        initialized_sidecars = True

    active_manifest = live_manifest or bundled_manifest
    if active_manifest is None:
        _fail("database_release_identity_unmanifested")
    if frozen and policy == "exact" and not signed_product_pair:
        _remember_bundled_catalog_source(package_root, bundled_pair, bundled_manifest)
    combination = (
        current_only_combination_fingerprint(active_manifest)
        if policy == "current_only"
        else None
    )
    result = {
        "contract": STARTUP_GATE_CONTRACT,
        "status": "ready",
        "state": state,
        "can_start": True,
        "pair_policy": policy,
        "app_release_id": APP_RELEASE_ID,
        # Pair identity belongs to the PairManifest/App contract, never to the
        # database manifest.  ``selected_pair`` may be absent only for an
        # unfrozen current-only repository run, where the compiled current
        # pair is the authority.
        "release_pair_id": (
            (selected_pair or {}).get("release_pair_id") or RELEASE_PAIR_ID
        ),
        "database_release_id": active_manifest["database_release_id"],
        "app_integrity_scope": "startup_members" if signed_product_pair else "full_or_source",
        "database_integrity_scope": "signed_exact_bytes" if qualification is not None else "full",
        "startup_timings_seconds": {
            "app_admission": round(app_seconds, 3),
            "database_admission": round(database_seconds, 3),
            "total": round(perf_counter() - started, 3),
        },
        "current_combination_fingerprint": combination,
        "initialized_sidecars": initialized_sidecars,
        "rebound_live_pair": rebound_live_pair,
        "capability_state": profile.get("capability_state", "unavailable"),
        "capabilities": copy.deepcopy(profile.get("capabilities")),
        "degraded_components": copy.deepcopy(
            profile.get("degraded_components") or []
        ),
    }
    if progress is not None:
        progress(
            {
                "contract": STARTUP_GATE_CONTRACT,
                "status": "ready",
                "stage": "ready",
                "message": "数据库校验完成，正在启动应用",
            }
        )
    return result


def prepare_database_startup(
    data_dir: Path, *, resource_root: Path | None = None,
    pair_policy: str | None = None, app_package_root: Path | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Serialize admission and every automatic identity write with activation.

    A previous read outside this fence is never permission to rebind a pair.
    The existing migration lock is thread-reentrant, including postchecks.
    """
    try:
        with profile_writer(data_dir):
            return _prepare_database_startup_locked(
                data_dir, resource_root=resource_root, pair_policy=pair_policy,
                app_package_root=app_package_root, progress=progress,
            )
    except LockBusyError as error:
        raise DatabaseStartupError("database_migration_busy", "数据库正在更新，请稍后重新检查。") from error
