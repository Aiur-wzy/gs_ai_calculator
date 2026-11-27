"""Read-only database release identity and App/database pair classification.

This module deliberately does not download, migrate, or replace a database.
It provides the stable contracts consumed by the release builder and, in a
later slice, by the desktop update coordinator.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from genshin_db.queries import get_meta
from genshin_db.release_integrity import (
    ReleaseIntegrityError,
    build_mutable_layer_fingerprint,
    build_release_core,
    validate_release_core,
)
from genshin_db.upstream_version import validate_resolved_evidence

MANIFEST_CONTRACT = "genshin-database-release-manifest-v3"
LEGACY_MANIFEST_CONTRACT = "genshin-database-release-manifest-v2"
PROFILE_CONTRACT = "genshin-database-profile-inspection-v2"
MANIFEST_SCHEMA_VERSION = 3
LEGACY_MANIFEST_SCHEMA_VERSION = 2
MANIFEST_FILENAME = "database-release-manifest.json"
PAIR_MANIFEST_CONTRACT = "genshin-release-pair-manifest-v2"
PAIR_MANIFEST_SCHEMA_VERSION = 2
PAIR_MANIFEST_FILENAME = "release-pair-manifest.json"
LIVE_RECEIPT_CONTRACT = "genshin-database-live-receipt-v3"
LEGACY_LIVE_RECEIPT_CONTRACT = "genshin-database-live-receipt-v2"
LIVE_RECEIPT_FILENAME = "database-live-receipt.json"
# ``REQUIRED_RELEASE_COMPONENTS`` is retained as the historical complete
# dataset list because older manifests implicitly promised all three.  New
# manifests carry an explicit component policy and only require core data at
# startup; optional task/knowledge capabilities may be unavailable without
# making a valid App/database pair corrupt.
REQUIRED_RELEASE_COMPONENTS = ("equipment", "runtime_config", "mechanic_semantics")
RELEASE_COMPONENTS = REQUIRED_RELEASE_COMPONENTS
DEFAULT_REQUIRED_COMPONENTS = ("equipment",)
DEFAULT_OPTIONAL_COMPONENTS = ("runtime_config", "mechanic_semantics")
COMPONENT_POLICY_CONTRACT = "genshin-database-component-policy-v1"
COMPONENT_ROLES = {
    "equipment": ("core_data", "validated_projection"),
    "runtime_config": ("task_capability", "agent_materialization"),
    "mechanic_semantics": ("knowledge", "none"),
}
PAIR_POLICIES = frozenset({"exact", "current_only"})
PROFILE_STATES = frozenset(
    {
        "first_install",
        "current_pair_ready",
        "current_dev_ready",
        "current_dev_stale",
        "pair_update_available",
        "pair_mismatch",
        "incompatible_legacy",
        "corrupt",
        "recovery_available",
    }
)

_RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_DATABASE_RELEASE_ID = re.compile(
    r"db-(?P<version>\d+(?:\.\d+){2,})-r(?P<revision>[1-9]\d*)\Z"
)
_GAME_VERSION = re.compile(r"\d+(?:\.\d+){2,}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_LOGIC_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}\Z")
_CHANNELS = frozenset({"stable", "dev"})
_JOURNAL_STATES = frozenset(
    {
        "prepared",
        "validated",
        "swap_started",
        "postchecked",
        "committed",
        "operational",
        "cleanup_complete",
        "rolled_back",
    }
)
_JOURNAL_RECOVERY_STATES = frozenset(
    {"prepared", "validated", "swap_started", "postchecked", "committed"}
)

# Dataset responses intentionally contain both contract identity and diagnostic
# presentation fields.  Only these keys describe the installed
# engine/data contract.  Counts, quality summaries, timestamps, diagnostics and
# execution observations remain visible in ``datasets`` but do not become an
# immutable identity merely because they share the same response object.
_DATASET_TOP_CONTRACT_FIELDS = (
    "schema_version",
    "supported_schema_versions",
    "profile_id",
    "profile_version",
    "indexer_version",
    "supported_indexer_versions",
)
_DATASET_LINE_CONTRACT_FIELDS = (
    "state",
    "index_state",
    "schema_version",
    "upstream_commit",
    "snapshot_id",
    "profile_id",
    "profile_version",
    "indexer_version",
    "manifest_fingerprint",
    "normalization",
    "source_capability_contract",
    "source_capability_state",
    "source_capabilities",
)


class DatabaseReleaseError(ValueError):
    """Stable validation failure used by build and runtime callers."""

    def __init__(self, reason: str, message: str | None = None):
        self.reason = reason
        super().__init__(message or reason)


def _object(value: Any, reason: str) -> dict:
    if type(value) is not dict:
        raise DatabaseReleaseError(reason)
    return value


def _text(value: Any, reason: str, *, maximum: int = 256) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise DatabaseReleaseError(reason)
    return value


def _identifier(value: Any, reason: str) -> str:
    text = _text(value, reason, maximum=96)
    if not _RELEASE_ID.fullmatch(text):
        raise DatabaseReleaseError(reason)
    return text


def _positive_int(value: Any, reason: str) -> int:
    if type(value) is not int or value < 1:
        raise DatabaseReleaseError(reason)
    return value


def _component_policy(
    required_components: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    required = (
        list(DEFAULT_REQUIRED_COMPONENTS)
        if required_components is None
        else list(required_components)
    )
    if (
        not required
        or any(type(name) is not str or name not in RELEASE_COMPONENTS for name in required)
        or len(set(required)) != len(required)
        or "equipment" not in required
    ):
        raise DatabaseReleaseError("database_release_component_policy_invalid")
    components: dict[str, dict[str, str]] = {}
    for name in RELEASE_COMPONENTS:
        role, authority = COMPONENT_ROLES[name]
        components[name] = {
            "requirement": "required" if name in required else "optional",
            "role": role,
            "execution_authority": authority,
        }
    return {
        "contract": COMPONENT_POLICY_CONTRACT,
        "components": components,
    }


def validate_component_policy(value: Any) -> dict[str, Any]:
    policy = _object(value, "database_release_component_policy_invalid")
    if set(policy) != {"contract", "components"} or policy.get("contract") != COMPONENT_POLICY_CONTRACT:
        raise DatabaseReleaseError("database_release_component_policy_invalid")
    components = _object(
        policy.get("components"), "database_release_component_policy_invalid"
    )
    if set(components) != set(RELEASE_COMPONENTS):
        raise DatabaseReleaseError("database_release_component_policy_invalid")
    normalized: dict[str, dict[str, str]] = {}
    for name in RELEASE_COMPONENTS:
        row = _object(components[name], "database_release_component_policy_invalid")
        if set(row) != {"requirement", "role", "execution_authority"}:
            raise DatabaseReleaseError("database_release_component_policy_invalid")
        role, authority = COMPONENT_ROLES[name]
        if (
            row.get("requirement") not in ("required", "optional")
            or row.get("role") != role
            or row.get("execution_authority") != authority
        ):
            raise DatabaseReleaseError("database_release_component_policy_invalid")
        normalized[name] = {
            "requirement": row["requirement"],
            "role": role,
            "execution_authority": authority,
        }
    if normalized["equipment"]["requirement"] != "required":
        raise DatabaseReleaseError("database_release_component_policy_invalid")
    # Knowledge can be required as package content, but never gains execution
    # authority from that packaging promise.
    if normalized["mechanic_semantics"]["execution_authority"] != "none":
        raise DatabaseReleaseError("database_release_component_policy_invalid")
    return {"contract": COMPONENT_POLICY_CONTRACT, "components": normalized}


def manifest_component_policy(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return an explicit policy, preserving strict legacy-manifest behavior."""

    value = manifest.get("component_policy")
    if value is not None:
        return validate_component_policy(value)
    # Legacy v2 manifests had no role metadata and treated all three dataset
    # rows as required.  Keeping that inference is necessary for exact replay.
    return _component_policy(list(RELEASE_COMPONENTS))


def required_manifest_components(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    policy = manifest_component_policy(manifest)
    return tuple(
        name
        for name in RELEASE_COMPONENTS
        if policy["components"][name]["requirement"] == "required"
    )


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def database_release_core(identity: Mapping[str, Any]) -> dict:
    if type(identity) is not dict:
        raise DatabaseReleaseError("database_release_core_invalid")
    try:
        return validate_release_core(copy.deepcopy(identity.get("release_core")))
    except ReleaseIntegrityError as error:
        raise DatabaseReleaseError(error.reason) from error


def database_release_core_fingerprint(identity: Mapping[str, Any]) -> str:
    return database_release_core(identity)["root_sha256"]


def dataset_contract_projection(datasets: Mapping[str, Any]) -> dict:
    """Project immutable capability/source identity from a dataset report.

    In particular, ``mechanic_semantics.lines.*.counts`` is projected separately
    from the dataset contract.  The live receipt binds the complete knowledge
    layer fingerprint, while schema/state remain in this projection.
    """

    if type(datasets) is not dict:
        raise DatabaseReleaseError("database_release_datasets_invalid")
    projected: dict[str, Any] = {}
    for component in REQUIRED_RELEASE_COMPONENTS:
        value = datasets.get(component)
        if type(value) is not dict:
            raise DatabaseReleaseError("database_release_datasets_invalid")
        lines = value.get("lines")
        if type(lines) is not dict:
            raise DatabaseReleaseError("database_release_datasets_invalid")
        component_projection = {
            key: copy.deepcopy(value[key])
            for key in _DATASET_TOP_CONTRACT_FIELDS
            if key in value
        }
        line_projection: dict[str, dict[str, Any]] = {}
        for line, state in sorted(lines.items()):
            if type(line) is not str or type(state) is not dict:
                raise DatabaseReleaseError("database_release_datasets_invalid")
            line_projection[line] = {
                key: copy.deepcopy(state[key])
                for key in _DATASET_LINE_CONTRACT_FIELDS
                if key in state
            }
        component_projection["lines"] = line_projection
        projected[component] = component_projection
    return projected


def database_matches_manifest_core(
    identity: Mapping[str, Any], manifest_database: Mapping[str, Any]
) -> bool:
    """Compare a recomputed immutable core, never self-reported component rows."""

    try:
        if any(
            identity.get(field) != manifest_database.get(field)
            for field in (
                "upstream_commit",
                "game_version",
                "upstream_track",
                "upstream_updated_at",
                "version_lines",
                "indexer_version",
                "normalization_version",
            )
        ):
            return False
        if dataset_contract_projection(identity.get("datasets")) != (
            dataset_contract_projection(manifest_database.get("datasets"))
        ):
            return False
        expected = validate_release_core(
            copy.deepcopy(manifest_database.get("release_core"))
        )
        return database_release_core(identity) == expected
    except (DatabaseReleaseError, ReleaseIntegrityError, AttributeError):
        return False


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _published_at(value: Any) -> str:
    text = _text(value, "database_release_published_at_invalid", maximum=64)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as error:
        raise DatabaseReleaseError("database_release_published_at_invalid") from error
    if parsed.tzinfo is None:
        raise DatabaseReleaseError("database_release_published_at_invalid")
    return text


def _parse_json_object(value: Any) -> dict:
    if type(value) is dict:
        return copy.deepcopy(value)
    if type(value) is str:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if type(parsed) is dict else {}
    return {}


def _dataset_projection(meta: Mapping[str, Any]) -> dict:
    datasets = meta.get("datasets")
    if type(datasets) is not dict:
        return {}
    return copy.deepcopy(datasets)


def _meta_identity(meta: Mapping[str, Any]) -> dict:
    evidence = _parse_json_object(
        meta.get("upstream_version_evidence:release")
        or meta.get("upstream_version_evidence")
    )
    return {
        "upstream_commit": meta.get("upstream_commit:release")
        or meta.get("upstream_commit"),
        "game_version": meta.get("game_version:release")
        or meta.get("game_version")
        or "unknown",
        "upstream_track": meta.get("upstream_track:release")
        or meta.get("upstream_track"),
        "upstream_updated_at": meta.get("upstream_updated_at:release")
        or meta.get("upstream_updated_at"),
        "upstream_version_evidence": evidence,
        "version_lines": list(meta.get("version_lines") or []),
        "indexer_version": meta.get("indexer_version"),
        "normalization_version": meta.get("normalization_version"),
        "datasets": _dataset_projection(meta),
        "manual_content_fingerprint": meta.get("manual_content_fingerprint"),
    }


def read_database_identity(database: Path, *, include_file_hash: bool = True) -> dict:
    """Inspect one SQLite file without writing it or inferring release order."""

    path = Path(database)
    if not path.is_file():
        raise DatabaseReleaseError("database_profile_missing")
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            connection.close()
        if quick_check != "ok":
            raise DatabaseReleaseError("database_profile_integrity_failed")
        meta = get_meta(path)
        release_core = build_release_core(path)
        mutable_layer_fingerprint = build_mutable_layer_fingerprint(path)
    except DatabaseReleaseError:
        raise
    except (
        OSError,
        sqlite3.Error,
        TypeError,
        ValueError,
        KeyError,
        ReleaseIntegrityError,
    ) as error:
        raise DatabaseReleaseError("database_profile_unreadable") from error
    identity = _meta_identity(meta)
    result = {
        "size_bytes": path.stat().st_size,
        "sha256": _file_sha256(path) if include_file_hash else None,
        "meta_fingerprint": _fingerprint(identity),
        "release_core": release_core,
        "mutable_layer_fingerprint": mutable_layer_fingerprint,
        **identity,
    }
    return result


def _validate_database(value: Any) -> dict:
    database = _object(value, "database_release_database_invalid")
    required = {
        "sha256",
        "size_bytes",
        "meta_fingerprint",
        "upstream_commit",
        "game_version",
        "upstream_track",
        "upstream_updated_at",
        "upstream_version_evidence",
        "version_lines",
        "indexer_version",
        "normalization_version",
        "datasets",
        "release_core",
    }
    if set(database) != required:
        raise DatabaseReleaseError("database_release_database_invalid")
    sha256 = _text(database["sha256"], "database_release_sha256_invalid", maximum=64)
    meta_fingerprint = _text(
        database["meta_fingerprint"], "database_release_meta_fingerprint_invalid", maximum=64
    )
    if not _SHA256.fullmatch(sha256) or not _SHA256.fullmatch(meta_fingerprint):
        raise DatabaseReleaseError("database_release_sha256_invalid")
    size_bytes = _positive_int(database["size_bytes"], "database_release_size_invalid")
    commit = _text(database["upstream_commit"], "database_release_commit_invalid", maximum=40)
    if not _COMMIT.fullmatch(commit):
        raise DatabaseReleaseError("database_release_commit_invalid")
    game_version = _text(database["game_version"], "database_release_game_version_invalid", maximum=64)
    if not _GAME_VERSION.fullmatch(game_version):
        raise DatabaseReleaseError("database_release_game_version_invalid")
    upstream_track = _text(
        database["upstream_track"],
        "database_release_upstream_track_invalid",
        maximum=32,
    )
    upstream_updated_at = _published_at(database["upstream_updated_at"])
    evidence = _object(
        database["upstream_version_evidence"],
        "database_release_upstream_evidence_invalid",
    )
    try:
        evidence = validate_resolved_evidence(
            evidence,
            expected_commit=commit,
            expected_version=game_version,
            expected_track=upstream_track,
            expected_snapshot_updated_at=upstream_updated_at,
        )
    except ValueError:
        raise DatabaseReleaseError("database_release_upstream_evidence_invalid")
    lines = database["version_lines"]
    if (
        type(lines) is not list
        or not lines
        or any(type(line) is not str or line not in ("release", "beta") for line in lines)
        or len(set(lines)) != len(lines)
    ):
        raise DatabaseReleaseError("database_release_version_lines_invalid")
    indexer_version = _text(
        database["indexer_version"], "database_release_indexer_version_invalid", maximum=64
    )
    normalization_version = _text(
        database["normalization_version"],
        "database_release_normalization_version_invalid",
        maximum=64,
    )
    datasets = _object(database["datasets"], "database_release_datasets_invalid")
    dataset_contract_projection(datasets)
    try:
        release_core = validate_release_core(copy.deepcopy(database["release_core"]))
    except ReleaseIntegrityError as error:
        raise DatabaseReleaseError(error.reason) from error
    return {
        "sha256": sha256,
        "size_bytes": size_bytes,
        "meta_fingerprint": meta_fingerprint,
        "upstream_commit": commit,
        "game_version": game_version,
        "upstream_track": upstream_track,
        "upstream_updated_at": upstream_updated_at,
        "upstream_version_evidence": copy.deepcopy(evidence),
        "version_lines": list(lines),
        "indexer_version": indexer_version,
        "normalization_version": normalization_version,
        "datasets": copy.deepcopy(datasets),
        "release_core": release_core,
    }


def validate_database_release_manifest(value: Any) -> dict:
    """Validate and normalize a local or remote database release manifest.

    Version 3 makes the database release identity independent from any App or
    App/database pair.  A database release can therefore be reused by a newer
    compatible App without reissuing an otherwise identical database.  Version
    2 remains readable so existing installed releases and their fingerprints
    keep validating, but its historical reverse-binding fields are not granted
    authority over a new :class:`ReleasePairManifest`.
    """

    manifest = _object(value, "database_release_manifest_invalid")
    required = {
        "contract",
        "schema_version",
        "database_release_id",
        "release_sequence",
        "channel",
        "published_at",
        "released_at",
        "logic_profile",
        "data_contract_version",
        "database",
        "download",
    }
    contract = manifest.get("contract")
    schema_version = manifest.get("schema_version")
    legacy = (
        contract == LEGACY_MANIFEST_CONTRACT
        and schema_version == LEGACY_MANIFEST_SCHEMA_VERSION
    )
    current = contract == MANIFEST_CONTRACT and schema_version == MANIFEST_SCHEMA_VERSION
    if not (legacy or current):
        raise DatabaseReleaseError("database_release_manifest_unsupported")
    legacy_binding = {"release_pair_id", "app_release_id"} if legacy else set()
    expected = required | legacy_binding
    if set(manifest) not in (expected, expected | {"component_policy"}):
        raise DatabaseReleaseError("database_release_manifest_invalid")
    release_id = _text(
        manifest["database_release_id"], "database_release_id_invalid", maximum=96
    )
    if not _RELEASE_ID.fullmatch(release_id):
        raise DatabaseReleaseError("database_release_id_invalid")
    channel = _text(manifest["channel"], "database_release_channel_invalid", maximum=16)
    if channel not in _CHANNELS:
        raise DatabaseReleaseError("database_release_channel_invalid")
    logic_profile = _text(
        manifest["logic_profile"], "database_release_logic_profile_invalid", maximum=96
    )
    if not _LOGIC_PROFILE.fullmatch(logic_profile):
        raise DatabaseReleaseError("database_release_logic_profile_invalid")
    download = manifest["download"]
    normalized_download = None
    if download is not None:
        download = _object(download, "database_release_download_invalid")
        if set(download) != {"url", "sha256", "size_bytes"}:
            raise DatabaseReleaseError("database_release_download_invalid")
        url = _text(download["url"], "database_release_download_invalid", maximum=2048)
        if not url.startswith("https://"):
            raise DatabaseReleaseError("database_release_download_invalid")
        download_sha = _text(download["sha256"], "database_release_download_invalid", maximum=64)
        if not _SHA256.fullmatch(download_sha):
            raise DatabaseReleaseError("database_release_download_invalid")
        normalized_download = {
            "url": url,
            "sha256": download_sha,
            "size_bytes": _positive_int(
                download["size_bytes"], "database_release_download_invalid"
            ),
        }
    database = _validate_database(manifest["database"])
    release_match = _DATABASE_RELEASE_ID.fullmatch(release_id)
    if (
        release_match is None
        or release_match.group("version") != database["game_version"]
    ):
        raise DatabaseReleaseError("database_release_id_game_version_mismatch")
    if normalized_download is not None and (
        normalized_download["sha256"] != database["sha256"]
        or normalized_download["size_bytes"] != database["size_bytes"]
    ):
        raise DatabaseReleaseError("database_release_download_identity_mismatch")
    published_at = _published_at(manifest["published_at"])
    if published_at != database["upstream_updated_at"]:
        raise DatabaseReleaseError("database_release_published_at_evidence_mismatch")
    normalized = {
        "contract": contract,
        "schema_version": schema_version,
        "database_release_id": release_id,
        "release_sequence": _positive_int(
            manifest["release_sequence"], "database_release_sequence_invalid"
        ),
        "channel": channel,
        "published_at": published_at,
        "released_at": _published_at(manifest["released_at"]),
        "logic_profile": logic_profile,
        "data_contract_version": _positive_int(
            manifest["data_contract_version"], "database_release_data_contract_invalid"
        ),
        "database": database,
        "download": normalized_download,
    }
    if legacy:
        # Preserve the exact v2 normalized shape so old manifest fingerprints,
        # pair manifests and live receipts remain verifiable.  These values are
        # historical provenance only; compatibility is decided by PairManifest.
        normalized["release_pair_id"] = _identifier(
            manifest["release_pair_id"], "database_release_pair_id_invalid"
        )
        normalized["app_release_id"] = _identifier(
            manifest["app_release_id"], "database_release_app_id_invalid"
        )
    if "component_policy" in manifest:
        normalized["component_policy"] = validate_component_policy(
            manifest["component_policy"]
        )
    return normalized


def _database_release_semantic_projection(manifest: Mapping[str, Any]) -> dict:
    normalized = validate_database_release_manifest(dict(manifest))
    database = normalized["database"]
    return {
        "database_release_id": normalized["database_release_id"],
        "release_sequence": normalized["release_sequence"],
        "channel": normalized["channel"],
        "published_at": normalized["published_at"],
        "logic_profile": normalized["logic_profile"],
        "data_contract_version": normalized["data_contract_version"],
        "component_policy": manifest_component_policy(normalized),
        "meta_fingerprint": database["meta_fingerprint"],
        "upstream_commit": database["upstream_commit"],
        "game_version": database["game_version"],
        "upstream_track": database["upstream_track"],
        "upstream_updated_at": database["upstream_updated_at"],
        "upstream_version_evidence": database["upstream_version_evidence"],
        "version_lines": database["version_lines"],
        "indexer_version": database["indexer_version"],
        "normalization_version": database["normalization_version"],
        "datasets": dataset_contract_projection(database["datasets"]),
        "release_core": database["release_core"],
    }


def database_release_semantically_equal(
    left: Mapping[str, Any], right: Mapping[str, Any]
) -> bool:
    """Return whether two manifests describe the same database release.

    This comparison intentionally ignores App/pair reverse bindings retained
    by legacy v2 manifests, the manifest schema wrapper, download location,
    publication packaging bytes and release timestamp.  Those values may
    differ when an installed SQLite file has a mutable local layer or when a
    legacy manifest is read by the v3 contract.  It does *not* weaken database
    identity: release ID/sequence, logic contract, upstream evidence, dataset
    contract, metadata fingerprint and immutable release core must all match.
    """

    try:
        return _database_release_semantic_projection(
            left
        ) == _database_release_semantic_projection(right)
    except DatabaseReleaseError:
        return False


def create_database_live_receipt(
    manifest: Mapping[str, Any],
    installed_identity: Mapping[str, Any],
    *,
    installed_at: str,
) -> dict:
    """Bind an exact release package to its immutable installed content."""

    normalized = validate_database_release_manifest(dict(manifest))
    if not database_matches_manifest_core(installed_identity, normalized["database"]):
        raise DatabaseReleaseError("database_live_receipt_core_mismatch")
    ready, _missing = _release_ready(
        installed_identity, required_manifest_components(normalized)
    )
    if not ready:
        raise DatabaseReleaseError("database_live_receipt_core_mismatch")
    receipt = {
        "contract": LIVE_RECEIPT_CONTRACT,
        "schema_version": 3,
        "database_release_id": normalized["database_release_id"],
        "release_sequence": normalized["release_sequence"],
        "logic_profile": normalized["logic_profile"],
        "data_contract_version": normalized["data_contract_version"],
        "installed_at": _published_at(installed_at),
        "manifest_fingerprint": _fingerprint(normalized),
        "package_sha256": normalized["database"]["sha256"],
        "active_sha256_at_install": installed_identity.get("sha256"),
        "release_core_sha256": database_release_core_fingerprint(installed_identity),
        "mutable_layer_fingerprint_at_install": installed_identity.get(
            "mutable_layer_fingerprint"
        ),
    }
    return {**receipt, "receipt_fingerprint": _fingerprint(receipt)}


def validate_database_live_receipt(value: Any) -> dict:
    receipt = _object(value, "database_live_receipt_invalid")
    fields = {
        "contract",
        "schema_version",
        "database_release_id",
        "release_sequence",
        "logic_profile",
        "data_contract_version",
        "installed_at",
        "manifest_fingerprint",
        "package_sha256",
        "active_sha256_at_install",
        "release_core_sha256",
        "mutable_layer_fingerprint_at_install",
        "receipt_fingerprint",
    }
    legacy = (
        receipt.get("contract") == LEGACY_LIVE_RECEIPT_CONTRACT
        and receipt.get("schema_version") == 2
    )
    current = (
        receipt.get("contract") == LIVE_RECEIPT_CONTRACT
        and receipt.get("schema_version") == 3
    )
    legacy_binding = {"release_pair_id", "app_release_id"} if legacy else set()
    if not (legacy or current) or set(receipt) != fields | legacy_binding:
        raise DatabaseReleaseError("database_live_receipt_invalid")
    normalized = {
        "contract": receipt["contract"],
        "schema_version": receipt["schema_version"],
        "database_release_id": _text(
            receipt["database_release_id"], "database_live_receipt_invalid", maximum=96
        ),
        "release_sequence": _positive_int(
            receipt["release_sequence"], "database_live_receipt_invalid"
        ),
        "logic_profile": _text(
            receipt["logic_profile"], "database_live_receipt_invalid", maximum=96
        ),
        "data_contract_version": _positive_int(
            receipt["data_contract_version"], "database_live_receipt_invalid"
        ),
        "installed_at": _published_at(receipt["installed_at"]),
    }
    if legacy:
        normalized["release_pair_id"] = _identifier(
            receipt["release_pair_id"], "database_live_receipt_invalid"
        )
        normalized["app_release_id"] = _identifier(
            receipt["app_release_id"], "database_live_receipt_invalid"
        )
    for field in (
        "manifest_fingerprint",
        "package_sha256",
        "active_sha256_at_install",
        "release_core_sha256",
        "mutable_layer_fingerprint_at_install",
    ):
        sha256 = _text(receipt[field], "database_live_receipt_invalid", maximum=64)
        if not _SHA256.fullmatch(sha256):
            raise DatabaseReleaseError("database_live_receipt_invalid")
        normalized[field] = sha256
    fingerprint = _text(
        receipt["receipt_fingerprint"], "database_live_receipt_invalid", maximum=64
    )
    if not _SHA256.fullmatch(fingerprint) or fingerprint != _fingerprint(normalized):
        raise DatabaseReleaseError("database_live_receipt_invalid")
    normalized["receipt_fingerprint"] = fingerprint
    return normalized


def database_live_receipt_matches(
    receipt: Mapping[str, Any],
    identity: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> bool:
    normalized_receipt = validate_database_live_receipt(dict(receipt))
    normalized_manifest = validate_database_release_manifest(dict(manifest))
    return (
        normalized_receipt["database_release_id"]
        == normalized_manifest["database_release_id"]
        and normalized_receipt["release_sequence"]
        == normalized_manifest["release_sequence"]
        and normalized_receipt["logic_profile"] == normalized_manifest["logic_profile"]
        and normalized_receipt["data_contract_version"]
        == normalized_manifest["data_contract_version"]
        and normalized_receipt["manifest_fingerprint"]
        == _fingerprint(normalized_manifest)
        and normalized_receipt["package_sha256"]
        == normalized_manifest["database"]["sha256"]
        and normalized_receipt["release_core_sha256"]
        == database_release_core_fingerprint(identity)
        and normalized_receipt["mutable_layer_fingerprint_at_install"]
        == identity.get("mutable_layer_fingerprint")
        and database_matches_manifest_core(identity, normalized_manifest["database"])
    )


def load_database_release_manifest(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatabaseReleaseError("database_release_manifest_unreadable") from error
    return validate_database_release_manifest(value)


def create_database_release_manifest(
    database: Path,
    *,
    database_release_id: str,
    release_sequence: int,
    channel: str,
    logic_profile: str,
    data_contract_version: int,
    release_pair_id: str | None = None,
    app_release_id: str | None = None,
    released_at: str | None = None,
    published_at: str | None = None,
    download_url: str | None = None,
    require_release_ready: bool = True,
    required_components: tuple[str, ...] | list[str] | None = None,
) -> dict:
    # ``release_pair_id`` and ``app_release_id`` are accepted temporarily for
    # source compatibility with v2 callers.  They deliberately do not enter a
    # v3 database release: App compatibility belongs to ReleasePairManifest.
    del release_pair_id, app_release_id
    identity = read_database_identity(database)
    source_time = identity.get("upstream_updated_at")
    if published_at is not None and published_at != source_time:
        raise DatabaseReleaseError("database_release_published_at_evidence_mismatch")
    if released_at is None:
        if channel != "dev":
            raise DatabaseReleaseError("database_release_released_at_required")
        released_at = source_time
    manifest = {
        "contract": MANIFEST_CONTRACT,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "database_release_id": database_release_id,
        "release_sequence": release_sequence,
        "channel": channel,
        "published_at": source_time,
        "released_at": released_at,
        "logic_profile": logic_profile,
        "data_contract_version": data_contract_version,
        "component_policy": _component_policy(required_components),
        "database": {
            key: identity[key]
            for key in (
                "sha256",
                "size_bytes",
                "meta_fingerprint",
                "upstream_commit",
                "game_version",
                "upstream_track",
                "upstream_updated_at",
                "upstream_version_evidence",
                "version_lines",
                "indexer_version",
                "normalization_version",
                "datasets",
                "release_core",
            )
        },
        "download": (
            {
                "url": download_url,
                "sha256": identity["sha256"],
                "size_bytes": identity["size_bytes"],
            }
            if download_url is not None
            else None
        ),
    }
    normalized = validate_database_release_manifest(manifest)
    ready, missing = _release_ready(
        identity, required_manifest_components(normalized)
    )
    if require_release_ready and not ready:
        raise DatabaseReleaseError(
            "database_release_components_not_ready",
            "release components not ready: " + ",".join(missing),
        )
    return normalized


def write_database_release_manifest(path: Path, manifest: Mapping[str, Any]) -> Path:
    normalized = validate_database_release_manifest(dict(manifest))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Canonical fingerprint, independent of pretty-printed JSON bytes."""

    return _fingerprint(validate_database_release_manifest(dict(manifest)))


def _app_contract(
    *,
    app_release_id: str,
    app_version: str,
    required_database_release_id: str,
    logic_profile: str,
    data_contract_version: int,
    artifact_manifest_sha256: str,
) -> dict:
    body = {
        "app_release_id": _identifier(app_release_id, "release_pair_app_invalid"),
        "app_version": _text(app_version, "release_pair_app_invalid", maximum=32),
        "required_database_release_id": _identifier(
            required_database_release_id, "release_pair_app_invalid"
        ),
        "logic_profile": _text(
            logic_profile, "release_pair_app_invalid", maximum=96
        ),
        "data_contract_version": _positive_int(
            data_contract_version, "release_pair_app_invalid"
        ),
        "artifact_manifest": {
            "contract": "genshin-package-file-manifest-v1",
            "path": "PACKAGE-FILES.sha256",
            "sha256": _text(
                artifact_manifest_sha256,
                "release_pair_app_artifact_invalid",
                maximum=64,
            ),
        },
    }
    if (
        not _LOGIC_PROFILE.fullmatch(body["logic_profile"])
        or not _SHA256.fullmatch(body["artifact_manifest"]["sha256"])
    ):
        raise DatabaseReleaseError("release_pair_app_invalid")
    return {**body, "contract_sha256": _fingerprint(body)}


def validate_release_pair_manifest(value: Any) -> dict:
    manifest = _object(value, "release_pair_manifest_invalid")
    required = {
        "contract",
        "schema_version",
        "release_pair_id",
        "pair_sequence",
        "pair_policy",
        "channel",
        "released_at",
        "app",
        "database",
    }
    if set(manifest) != required:
        raise DatabaseReleaseError("release_pair_manifest_invalid")
    if (
        manifest["contract"] != PAIR_MANIFEST_CONTRACT
        or manifest["schema_version"] != PAIR_MANIFEST_SCHEMA_VERSION
    ):
        raise DatabaseReleaseError("release_pair_manifest_unsupported")
    channel = _text(manifest["channel"], "release_pair_channel_invalid", maximum=16)
    policy = _text(manifest["pair_policy"], "release_pair_policy_invalid", maximum=16)
    if channel not in _CHANNELS or policy not in PAIR_POLICIES:
        raise DatabaseReleaseError("release_pair_policy_invalid")
    if channel == "stable" and policy != "exact":
        raise DatabaseReleaseError("release_pair_stable_requires_exact")
    app = _object(manifest["app"], "release_pair_app_invalid")
    if set(app) != {
        "app_release_id",
        "app_version",
        "required_database_release_id",
        "logic_profile",
        "data_contract_version",
        "artifact_manifest",
        "contract_sha256",
    }:
        raise DatabaseReleaseError("release_pair_app_invalid")
    artifact = _object(app["artifact_manifest"], "release_pair_app_artifact_invalid")
    normalized_app = _app_contract(
        app_release_id=app["app_release_id"],
        app_version=app["app_version"],
        required_database_release_id=app["required_database_release_id"],
        logic_profile=app["logic_profile"],
        data_contract_version=app["data_contract_version"],
        artifact_manifest_sha256=artifact.get("sha256"),
    )
    if artifact != normalized_app["artifact_manifest"]:
        raise DatabaseReleaseError("release_pair_app_artifact_invalid")
    if app["contract_sha256"] != normalized_app["contract_sha256"]:
        raise DatabaseReleaseError("release_pair_app_contract_mismatch")
    database = _object(manifest["database"], "release_pair_database_invalid")
    if set(database) != {
        "database_release_id",
        "manifest_sha256",
        "release_core_sha256",
    }:
        raise DatabaseReleaseError("release_pair_database_invalid")
    normalized_database = {
        "database_release_id": _identifier(
            database["database_release_id"], "release_pair_database_invalid"
        ),
        "manifest_sha256": _text(
            database["manifest_sha256"],
            "release_pair_database_invalid",
            maximum=64,
        ),
        "release_core_sha256": _text(
            database["release_core_sha256"],
            "release_pair_database_invalid",
            maximum=64,
        ),
    }
    if not all(
        _SHA256.fullmatch(normalized_database[field])
        for field in ("manifest_sha256", "release_core_sha256")
    ):
        raise DatabaseReleaseError("release_pair_database_invalid")
    if (
        normalized_database["database_release_id"]
        != normalized_app["required_database_release_id"]
    ):
        raise DatabaseReleaseError("release_pair_database_requirement_mismatch")
    return {
        "contract": PAIR_MANIFEST_CONTRACT,
        "schema_version": PAIR_MANIFEST_SCHEMA_VERSION,
        "release_pair_id": _identifier(
            manifest["release_pair_id"], "release_pair_id_invalid"
        ),
        "pair_sequence": _positive_int(
            manifest["pair_sequence"], "release_pair_sequence_invalid"
        ),
        "pair_policy": policy,
        "channel": channel,
        "released_at": _published_at(manifest["released_at"]),
        "app": normalized_app,
        "database": normalized_database,
    }


def create_release_pair_manifest(
    database_manifest: Mapping[str, Any],
    *,
    release_pair_id: str,
    pair_sequence: int,
    pair_policy: str,
    app_release_id: str,
    app_version: str,
    app_artifact_manifest_sha256: str,
    released_at: str | None = None,
) -> dict:
    database = validate_database_release_manifest(dict(database_manifest))
    return validate_release_pair_manifest(
        {
            "contract": PAIR_MANIFEST_CONTRACT,
            "schema_version": PAIR_MANIFEST_SCHEMA_VERSION,
            "release_pair_id": release_pair_id,
            "pair_sequence": pair_sequence,
            "pair_policy": pair_policy,
            "channel": database["channel"],
            "released_at": database["released_at"] if released_at is None else released_at,
            "app": _app_contract(
                app_release_id=app_release_id,
                app_version=app_version,
                required_database_release_id=database["database_release_id"],
                logic_profile=database["logic_profile"],
                data_contract_version=database["data_contract_version"],
                artifact_manifest_sha256=app_artifact_manifest_sha256,
            ),
            "database": {
                "database_release_id": database["database_release_id"],
                "manifest_sha256": _fingerprint(database),
                "release_core_sha256": database["database"]["release_core"][
                    "root_sha256"
                ],
            },
        }
    )


def release_pair_matches_database_manifest(
    pair_manifest: Mapping[str, Any], database_manifest: Mapping[str, Any]
) -> bool:
    pair = validate_release_pair_manifest(dict(pair_manifest))
    database = validate_database_release_manifest(dict(database_manifest))
    return (
        pair["channel"] == database["channel"]
        and pair["app"]["logic_profile"] == database["logic_profile"]
        and pair["app"]["data_contract_version"]
        == database["data_contract_version"]
        and pair["database"]["database_release_id"]
        == database["database_release_id"]
        and pair["database"]["manifest_sha256"] == _fingerprint(database)
        and pair["database"]["release_core_sha256"]
        == database["database"]["release_core"]["root_sha256"]
    )


def write_release_pair_manifest(path: Path, manifest: Mapping[str, Any]) -> Path:
    normalized = validate_release_pair_manifest(dict(manifest))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def load_release_pair_manifest(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DatabaseReleaseError("release_pair_manifest_unreadable") from error
    return validate_release_pair_manifest(value)


def database_capability_projection(
    identity: Mapping[str, Any],
    *,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project per-component availability without granting execution rights."""

    normalized_policy = (
        validate_component_policy(policy)
        if policy is not None
        else _component_policy()
    )
    datasets = identity.get("datasets")
    components: dict[str, dict[str, Any]] = {}
    required_missing: list[str] = []
    optional_unavailable: list[str] = []
    partial_components: list[str] = []
    for component in RELEASE_COMPONENTS:
        declared = normalized_policy["components"][component]
        value = datasets.get(component) if type(datasets) is dict else None
        lines = value.get("lines") if type(value) is dict else None
        release = lines.get("release") if type(lines) is dict else None
        source_state = release.get("state") if type(release) is dict else "missing"
        available = source_state == "ready"
        row = {
            **copy.deepcopy(declared),
            "available": available,
            "dataset_state": source_state,
        }
        if component == "runtime_config" and type(release) is dict:
            row["source_capability_state"] = release.get("source_capability_state")
            row["source_capabilities"] = copy.deepcopy(
                release.get("source_capabilities")
            )
            # A queryable runtime index may intentionally omit optional
            # upstream source families.  Keep ``available`` about the dataset
            # itself, but do not flatten its nested capability loss into
            # ``ready`` at the App boundary.
            if available and row["source_capability_state"] == "degraded":
                partial_components.append(component)
        components[component] = row
        if not available:
            if declared["requirement"] == "required":
                required_missing.append(component)
            else:
                optional_unavailable.append(component)
    result = {
        "contract": "genshin-database-capabilities-v1",
        "state": "unavailable" if required_missing else (
            "degraded" if optional_unavailable or partial_components else "ready"
        ),
        "components": components,
        "required_missing": required_missing,
        "optional_unavailable": optional_unavailable,
        "partial_components": partial_components,
        # Preserve the public component-policy order instead of leaking an
        # incidental alphabetical sort into App/GUI projections.
        "degraded_components": [
            name for name in RELEASE_COMPONENTS
            if name in set(optional_unavailable + partial_components)
        ],
    }
    return {**result, "fingerprint": _fingerprint(result)}


def _release_ready(
    identity: Mapping[str, Any],
    required_components: tuple[str, ...] | list[str] = REQUIRED_RELEASE_COMPONENTS,
) -> tuple[bool, list[str]]:
    missing: list[str] = []
    datasets = identity.get("datasets")
    if type(datasets) is not dict:
        return False, list(required_components)
    for component in required_components:
        value = datasets.get(component)
        lines = value.get("lines") if type(value) is dict else None
        release = lines.get("release") if type(lines) is dict else None
        if type(release) is not dict or release.get("state") != "ready":
            missing.append(component)
    return not missing, missing


def manifest_supports_app(
    manifest: Mapping[str, Any],
    *,
    logic_profile: str,
    data_contract_version: int,
    app_release_id: str | None = None,
    release_pair_id: str | None = None,
    required_database_release_id: str | None = None,
    pair_policy: str = "exact",
    app_version: str | None = None,
) -> bool:
    """Check whether one database release satisfies an App data contract.

    App and pair identities are intentionally ignored here.  They are checked
    against ReleasePairManifest by the startup/activation layer.  Keeping that
    decision out of DatabaseReleaseManifest allows a compatible new App to
    reuse the same immutable database release.
    """

    normalized = validate_database_release_manifest(dict(manifest))
    if pair_policy not in PAIR_POLICIES:
        raise DatabaseReleaseError("release_pair_policy_invalid")
    del app_release_id, release_pair_id, app_version
    common = (
        normalized["logic_profile"] == logic_profile
        and normalized["data_contract_version"] == data_contract_version
    )
    if not common:
        return False
    return (
        required_database_release_id is None
        or normalized["database_release_id"] == required_database_release_id
    )


def current_only_combination_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Bind the current source contract to one immutable database release."""

    normalized = validate_database_release_manifest(dict(manifest))
    database = normalized["database"]
    return _fingerprint(
        {
            "policy": "current_only",
            "database_release_id": normalized["database_release_id"],
            "database_semantic_fingerprint": _fingerprint(
                _database_release_semantic_projection(normalized)
            ),
            "logic_profile": normalized["logic_profile"],
            "data_contract_version": normalized["data_contract_version"],
            "game_version": database["game_version"],
            "indexer_version": database.get("indexer_version"),
            "normalization_version": database.get("normalization_version"),
            "release_core_sha256": database["release_core"]["root_sha256"],
        }
    )


def inspect_database_profile(
    live_database: Path,
    *,
    logic_profile: str,
    data_contract_version: int,
    app_release_id: str | None = None,
    release_pair_id: str | None = None,
    required_database_release_id: str | None = None,
    pair_policy: str = "exact",
    app_version: str | None = None,
    live_manifest: Mapping[str, Any] | None = None,
    bundled_manifest: Mapping[str, Any] | None = None,
    remote_manifest: Mapping[str, Any] | None = None,
    live_receipt: Mapping[str, Any] | None = None,
    journal_state: str | None = None,
    identity_reader: Callable[[Path], dict] | None = None,
) -> dict:
    """Classify a local profile from explicit, already-fetched manifests.

    Passing ``remote_manifest`` never performs network I/O. An alternate
    identity reader must validate the actual local database before returning
    its identity, just as the default reader does.
    """

    if pair_policy not in PAIR_POLICIES:
        raise DatabaseReleaseError("release_pair_policy_invalid")
    if app_release_id is None and app_version is not None:
        app_release_id = f"app-{app_version}"

    path = Path(live_database)
    manifests = {
        name: validate_database_release_manifest(dict(value)) if value is not None else None
        for name, value in (
            ("live", live_manifest),
            ("bundled", bundled_manifest),
            ("remote", remote_manifest),
        )
    }
    if journal_state is not None:
        if journal_state not in _JOURNAL_STATES:
            raise DatabaseReleaseError("database_migration_journal_state_invalid")
        if journal_state in _JOURNAL_RECOVERY_STATES:
            return {
                "contract": PROFILE_CONTRACT,
                "state": "recovery_available",
                "can_continue_current": False,
                "reasons": ["database_migration_incomplete"],
                "live": None,
                "current_release_id": None,
                "available_release_id": None,
            }
    if not path.exists():
        target = manifests["bundled"] or manifests["remote"]
        return {
            "contract": PROFILE_CONTRACT,
            "state": "first_install",
            "can_continue_current": False,
            "reasons": ["database_profile_missing"],
            "live": None,
            "current_release_id": None,
            "available_release_id": target["database_release_id"] if target else None,
        }
    try:
        identity = (identity_reader or read_database_identity)(path)
    except DatabaseReleaseError as error:
        return {
            "contract": PROFILE_CONTRACT,
            "state": "corrupt",
            "can_continue_current": False,
            "reasons": [error.reason],
            "live": None,
            "current_release_id": None,
            "available_release_id": None,
        }
    receipt = None
    if live_receipt is not None:
        try:
            receipt = validate_database_live_receipt(dict(live_receipt))
        except DatabaseReleaseError as error:
            return {
                "contract": PROFILE_CONTRACT,
                "state": "corrupt",
                "can_continue_current": False,
                "reasons": [error.reason],
                "live": identity,
                "current_release_id": None,
                "available_release_id": None,
            }
    current = manifests["live"]
    if current is not None:
        exact_package = (
            current["database"]["sha256"] == identity["sha256"]
            and current["database"]["meta_fingerprint"] == identity["meta_fingerprint"]
        )
        try:
            installed_copy = receipt is not None and database_live_receipt_matches(
                receipt, identity, current
            )
        except DatabaseReleaseError:
            installed_copy = False
        if (not exact_package and not installed_copy) or (
            receipt is not None and not installed_copy
        ):
            return {
                "contract": PROFILE_CONTRACT,
                "state": "corrupt",
                "can_continue_current": False,
                "reasons": ["database_live_manifest_mismatch"],
                "live": identity,
                "current_release_id": current["database_release_id"],
                "available_release_id": None,
            }
    if current is None:
        candidates = tuple(
            manifest
            for manifest in (manifests["bundled"], manifests["remote"])
            if manifest is not None
        )
        if receipt is not None:
            try:
                current = next(
                    (
                        manifest
                        for manifest in candidates
                        if database_live_receipt_matches(receipt, identity, manifest)
                    ),
                    None,
                )
            except DatabaseReleaseError:
                current = None
            if current is None:
                return {
                    "contract": PROFILE_CONTRACT,
                    "state": "corrupt",
                    "can_continue_current": False,
                    "reasons": ["database_live_receipt_manifest_mismatch"],
                    "live": identity,
                    "current_release_id": receipt["database_release_id"],
                    "available_release_id": None,
                }
        else:
            current = next(
                (
                    manifest
                    for manifest in candidates
                    if (
                        manifest["database"]["sha256"] == identity["sha256"]
                        and manifest["database"]["meta_fingerprint"]
                        == identity["meta_fingerprint"]
                    )
                ),
                None,
            )
    if current is not None and not manifest_supports_app(
        current,
        app_release_id=app_release_id,
        release_pair_id=release_pair_id,
        required_database_release_id=required_database_release_id,
        pair_policy=pair_policy,
        logic_profile=logic_profile,
        data_contract_version=data_contract_version,
    ):
        return {
            "contract": PROFILE_CONTRACT,
            "state": "pair_mismatch",
            "can_continue_current": False,
            "reasons": ["database_release_pair_mismatch"],
            "live": identity,
            "current_release_id": current["database_release_id"],
            "current_release_pair_id": current.get("release_pair_id"),
            "available_release_id": (
                manifests["remote"] or manifests["bundled"] or {}
            ).get("database_release_id"),
        }
    current_policy = (
        manifest_component_policy(current)
        if current is not None
        else _component_policy(list(RELEASE_COMPONENTS))
    )
    capabilities = database_capability_projection(
        identity, policy=current_policy
    )
    ready, missing = _release_ready(
        identity,
        [
            name
            for name, row in current_policy["components"].items()
            if row["requirement"] == "required"
        ],
    )
    if not ready:
        return {
            "contract": PROFILE_CONTRACT,
            "state": "incompatible_legacy",
            "can_continue_current": False,
            "reasons": ["database_required_components_not_ready"],
            "missing_components": missing,
            "capabilities": capabilities,
            "live": identity,
            "current_release_id": current["database_release_id"] if current else None,
            "available_release_id": (
                manifests["remote"] or manifests["bundled"] or {}
            ).get("database_release_id"),
        }
    if current is None:
        available = manifests["remote"] or manifests["bundled"]
        return {
            "contract": PROFILE_CONTRACT,
            "state": "incompatible_legacy",
            "can_continue_current": False,
            "reasons": ["database_release_identity_unmanifested"],
            "live": identity,
            "current_release_id": None,
            "available_release_id": (
                available["database_release_id"] if available else None
            ),
        }
    available = manifests["remote"] or manifests["bundled"]
    if pair_policy == "current_only":
        target = available or current
        stale = (
            target["database_release_id"] != current["database_release_id"]
            or not database_release_semantically_equal(target, current)
        )
        state = "current_dev_stale" if stale else "current_dev_ready"
        reasons = [
            "database_current_dev_stale" if stale else "database_current_dev_ready"
        ]
        can_continue = not stale
        current_combination = current_only_combination_fingerprint(current)
    else:
        update = (
            available is not None
            and available["release_sequence"] > current["release_sequence"]
            and not database_release_semantically_equal(available, current)
        )
        state = "pair_update_available" if update else "current_pair_ready"
        reasons = [
            "database_pair_update_available" if update else "database_current_pair_ready"
        ]
        can_continue = True
        current_combination = None
    if capabilities["state"] == "degraded":
        reasons.append("database_optional_capabilities_degraded")
    return {
        "contract": PROFILE_CONTRACT,
        "state": state,
        "can_continue_current": can_continue,
        "reasons": reasons,
        "live": identity,
        "current_release_id": current["database_release_id"] if current else None,
        "current_release_pair_id": current.get("release_pair_id") if current else None,
        "available_release_id": available["database_release_id"] if available else None,
        "available_release_pair_id": available.get("release_pair_id") if available else None,
        "current_combination_fingerprint": current_combination,
        "capability_state": capabilities["state"],
        "capabilities": capabilities,
        "degraded_components": capabilities["degraded_components"],
    }
