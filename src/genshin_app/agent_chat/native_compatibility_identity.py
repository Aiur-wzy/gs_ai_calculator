"""Stable App/release/Skill identities for native-Agent session admission.

The runtime manifest is not the whole application.  A persisted Agent session
must also stop when the App/database release pair, the executable/source
artifact, or any Markdown resource exposed by ``gs_read_skill`` changes.
This module computes those identities without publishing local paths or file
contents into the session record.
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import fingerprint
from genshin_app.package_integrity import verify_package_manifest_descriptor
from genshin_app.paths import default_data_dir, is_frozen, resource_dir, skill_dir
from genshin_app.version import (
    APP_RELEASE_ID,
    APP_VERSION,
    DATABASE_DATA_CONTRACT_VERSION,
    DATABASE_LOGIC_PROFILE,
    DATABASE_RELEASE_CHANNEL,
    DATABASE_RELEASE_ID,
    DATABASE_RELEASE_SEQUENCE,
    NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256,
    RELEASE_PAIR_ID,
    RELEASE_PAIR_SEQUENCE,
)

from .native_text_safety import sanitize_public_text

IDENTITY_CONTRACT = "genshin-native-session-compatibility-identity-v2"
APP_ARTIFACT_IDENTITY_CONTRACT = "genshin-native-app-artifact-identity-v2"
SHELL_RELEASE_IDENTITY_CONTRACT = "genshin-native-dsh-shell-release-identity-v1"
SKILL_MANIFEST_CONTRACT = "genshin-native-skill-bundle-manifest-v1"
SOURCE_ARTIFACT_CONTRACT = "genshin-native-source-artifact-manifest-v1"
_SOURCE_SUFFIXES = frozenset({".py", ".js", ".css", ".html", ".json"})


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_skill_bundle_manifest(root: str | Path | None = None) -> dict[str, Any]:
    """Hash the exact sanitized Markdown content available to ``gs_read_skill``."""

    base = Path(root) if root is not None else skill_dir()
    base = base.resolve(strict=True)
    resources: list[dict[str, Any]] = []
    paths = sorted(
        base.rglob("*.md"),
        key=lambda path: path.relative_to(base).as_posix(),
    )
    for path in paths:
        if path.is_symlink():
            raise RuntimeError("native_skill_bundle_link_unsupported")
        resolved = path.resolve(strict=True)
        if base not in resolved.parents:
            raise RuntimeError("native_skill_bundle_path_invalid")
        text = sanitize_public_text(resolved.read_text(encoding="utf-8"))
        resources.append({
            "resource": resolved.relative_to(base).as_posix(),
            "content_hash": _sha256_bytes(text.encode("utf-8")),
            "characters": len(text),
        })
    if not resources:
        raise RuntimeError("native_skill_bundle_empty")
    return {
        "contract": SKILL_MANIFEST_CONTRACT,
        "resources": resources,
    }


def skill_bundle_hash(root: str | Path | None = None) -> str:
    return fingerprint(build_skill_bundle_manifest(root))


def _active_product_pair(data_dir: Path | None) -> tuple[dict, dict] | None:
    if not is_frozen() or DATABASE_RELEASE_CHANNEL != "stable":
        return None
    from genshin_app.release_pair_update import (
        LIVE_DATABASE_MANIFEST_FILENAME,
        LIVE_PAIR_MANIFEST_FILENAME,
        LIVE_PAIR_PROOF_FILENAME,
        read_live_product_proof,
    )
    from genshin_db.release_profile import (
        load_database_release_manifest,
        load_release_pair_manifest,
    )

    directory = data_dir if data_dir is not None else default_data_dir()
    bundled = (resource_dir() / "bundled" / "release-pair-manifest.json").is_file()
    if bundled:
        if (directory / LIVE_PAIR_PROOF_FILENAME).exists():
            raise RuntimeError("native_active_release_pair_invalid")
        return None
    pair = load_release_pair_manifest(directory / LIVE_PAIR_MANIFEST_FILENAME)
    database = load_database_release_manifest(directory / LIVE_DATABASE_MANIFEST_FILENAME)
    if (read_live_product_proof(directory, pair, database) is None
            or (pair["app"]["app_release_id"], pair["app"]["app_version"], pair["app"]["logic_profile"],
                pair["app"]["data_contract_version"]) != (APP_RELEASE_ID, APP_VERSION, DATABASE_LOGIC_PROFILE,
                                                          DATABASE_DATA_CONTRACT_VERSION)
            or pair["pair_policy"] != "exact" or pair["channel"] != DATABASE_RELEASE_CHANNEL):
        raise RuntimeError("native_active_release_pair_invalid")
    return pair, database


def active_database_release_id(data_dir: Path | None = None) -> str:
    product = _active_product_pair(data_dir)
    return DATABASE_RELEASE_ID if product is None else product[1]["database_release_id"]


def release_pair_identity_hash(data_dir: Path | None = None) -> str:
    """Bind the exact App/database pair rather than only the SQLite digest."""

    product = _active_product_pair(data_dir)
    if product is not None:
        pair, database = product
        return fingerprint({"contract": IDENTITY_CONTRACT, "pair": pair, "database": database})

    return fingerprint({
        "contract": IDENTITY_CONTRACT,
        "app_release_id": APP_RELEASE_ID,
        "app_version": APP_VERSION,
        "release_pair_id": RELEASE_PAIR_ID,
        "release_pair_sequence": RELEASE_PAIR_SEQUENCE,
        "database_release_id": DATABASE_RELEASE_ID,
        "database_release_sequence": DATABASE_RELEASE_SEQUENCE,
        "database_logic_profile": DATABASE_LOGIC_PROFILE,
        "database_data_contract_version": DATABASE_DATA_CONTRACT_VERSION,
    })


def _source_manifest(
    roots: Iterable[tuple[str, str | Path]],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for namespace, supplied in roots:
        base = Path(supplied).resolve(strict=True)
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            if path.is_symlink():
                raise RuntimeError("native_app_artifact_link_unsupported")
            resolved = path.resolve(strict=True)
            if base not in resolved.parents:
                raise RuntimeError("native_app_artifact_path_invalid")
            rows.append({
                "resource": f"{namespace}/{resolved.relative_to(base).as_posix()}",
                "content_hash": _sha256_bytes(resolved.read_bytes()),
            })
    if not rows:
        raise RuntimeError("native_app_artifact_empty")
    rows.sort(key=lambda row: row["resource"])
    return {"contract": SOURCE_ARTIFACT_CONTRACT, "files": rows}


def app_artifact_identity_hash(
    *,
    source_roots: Iterable[tuple[str, str | Path]] | None = None,
    package_root: str | Path | None = None,
) -> str:
    """Return one content identity for a source checkout or portable package."""

    if package_root is not None or (source_roots is None and is_frozen()):
        root = (
            Path(package_root)
            if package_root is not None
            else Path(sys.executable).resolve(strict=True).parent
        )
        descriptor = verify_package_manifest_descriptor(root)
        artifact = {
            "contract": "genshin-native-packaged-app-artifact-v1",
            "app_release_id": APP_RELEASE_ID,
            "package_manifest_sha256": descriptor["manifest_sha256"],
            "package_file_count": descriptor["files"],
        }
    else:
        root = resource_dir().resolve(strict=True)
        selected = source_roots or (
            ("genshin_app", root / "src" / "genshin_app"),
            ("genshin_db", root / "src" / "genshin_db"),
            ("genshin_engine", root / "src" / "genshin_engine"),
            # Runtime semantics and reviewed JSON profiles are imported by all
            # three packages above.  Omitting this root would let a source/dev
            # session survive changes to action, energy, periodic or reaction
            # execution rules even though a packaged build's file manifest
            # correctly binds the same files.
            ("genshin_runtime", root / "src" / "genshin_runtime"),
        )
        artifact = _source_manifest(selected)
    return fingerprint({
        "contract": APP_ARTIFACT_IDENTITY_CONTRACT,
        "artifact": artifact,
        "app_release_id": APP_RELEASE_ID,
        "native_shell_release": shell_release_identity_descriptor(),
    })


def shell_release_identity_descriptor() -> dict[str, Any]:
    """Describe the independent release anchor; a dev identity is not trust.

    Source hashes identify editable App content only. They never manufacture a
    trusted runtime pin while the reviewed release manifest is still pending.
    """
    pin = NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256
    if pin is not None and (not isinstance(pin, str)
                            or re.fullmatch(r"[0-9a-f]{64}", pin) is None):
        raise RuntimeError("native_shell_release_pin_invalid")
    return {
        "contract": SHELL_RELEASE_IDENTITY_CONTRACT,
        "runtime_generation": "dsh_client_shell",
        "state": "pending" if pin is None else "pinned",
        "manifest_sha256": pin,
    }


@lru_cache(maxsize=4)
def current_native_compatibility_identity(data_dir: Path | None = None) -> dict[str, Any]:
    """Compute once per process; a restart is required after installed files change."""

    return {
        "contract": IDENTITY_CONTRACT,
        "native_shell_release": shell_release_identity_descriptor(),
        "release_pair_identity_hash": release_pair_identity_hash(data_dir) if data_dir is not None else release_pair_identity_hash(),
        "app_artifact_identity_hash": app_artifact_identity_hash(),
        "skill_bundle_hash": skill_bundle_hash(),
    }


__all__ = [
    "APP_ARTIFACT_IDENTITY_CONTRACT",
    "IDENTITY_CONTRACT",
    "SHELL_RELEASE_IDENTITY_CONTRACT",
    "SKILL_MANIFEST_CONTRACT",
    "SOURCE_ARTIFACT_CONTRACT",
    "app_artifact_identity_hash",
    "build_skill_bundle_manifest",
    "current_native_compatibility_identity",
    "release_pair_identity_hash",
    "shell_release_identity_descriptor",
    "skill_bundle_hash",
]
