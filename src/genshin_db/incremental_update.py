"""Conservative, auditable planning for complete database candidates.

This module never mutates an accepted database.  It compares the source
identity recorded by a base release with one pinned target snapshot and emits
a deterministic component rebuild plan.  The plan deliberately expands to a
full rebuild whenever provenance cannot be rebound safely; incremental is an
implementation optimisation, never a weaker publication contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from genshin_db.raw_store import RAW_SOURCES

PLAN_CONTRACT = "genshin-database-incremental-plan-v1"
PHYSICAL_PLAN_CONTRACT = "genshin-database-incremental-plan-v2"
RECEIPT_CONTRACT = "genshin-database-incremental-build-receipt-v1"
PHYSICAL_RECEIPT_CONTRACT = "genshin-database-incremental-build-receipt-v2"
SCHEMA_VERSION = 1
COMPONENTS = ("curated", "equipment", "runtime_config")
BUILD_CONTRACT_META_KEY = "incremental_build_contract"

CURATED_SOURCES = frozenset(
    {
        "ExcelBinOutput/AvatarExcelConfigData.json",
        "ExcelBinOutput/AvatarSkillExcelConfigData.json",
        "ExcelBinOutput/AvatarTalentExcelConfigData.json",
        "ExcelBinOutput/AvatarSkillDepotExcelConfigData.json",
        "ExcelBinOutput/ProudSkillExcelConfigData.json",
        "ExcelBinOutput/FetterInfoExcelConfigData.json",
        "ExcelBinOutput/AvatarCurveExcelConfigData.json",
        "ExcelBinOutput/AvatarPromoteExcelConfigData.json",
        "TextMap/TextMap_MediumCHS.json",
    }
)
HIGH_FANOUT_SOURCES = frozenset(
    {
        "TextMap/TextMap_MediumCHS.json",
        "BinOutput/Common/ConfigGlobalCombat.json",
        "BinOutput/AbilityPath/AbilityPathData.json",
        "BinOutput/AnimPattern/AnimPatternData.json",
    }
)
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")


class IncrementalUpdateError(ValueError):
    """A stable planning or receipt validation failure."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _meta(connection: sqlite3.Connection) -> dict[str, str]:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone():
        return {}
    return dict(connection.execute("SELECT key,value FROM meta"))


def read_base_source_state(base_db: Path | None) -> dict[str, Any]:
    """Read only the provenance needed for planning from an accepted base."""

    if base_db is None or not Path(base_db).is_file():
        return {
            "present": False,
            "upstream_commit": None,
            "raw_manifest": {},
            "runtime_manifest": {},
            "build_contract": None,
            "complete": False,
        }
    path = Path(base_db).resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "source_manifest" not in tables:
            return {
                "present": True,
                "upstream_commit": _meta(connection).get("upstream_commit"),
                "raw_manifest": {},
                "runtime_manifest": {},
                "build_contract": None,
                "complete": False,
            }
        meta = _meta(connection)
        serialized_contract = meta.get(BUILD_CONTRACT_META_KEY)
        try:
            build_contract = (
                json.loads(serialized_contract)
                if serialized_contract is not None
                else None
            )
        except json.JSONDecodeError:
            build_contract = None
        if type(build_contract) is not dict:
            build_contract = None
        raw = {
            row[0]: {"sha256": row[1], "row_count": row[2]}
            for row in connection.execute(
                """SELECT source_file,sha256,row_count FROM source_manifest
                   WHERE component='equipment' AND version_line='release'"""
            )
        }
        runtime = {
            row[0]: {"sha256": row[1], "row_count": row[2]}
            for row in connection.execute(
                """SELECT source_file,sha256,row_count FROM source_manifest
                   WHERE component='runtime_config' AND version_line='release'"""
            )
        }
        return {
            "present": True,
            "upstream_commit": meta.get("upstream_commit"),
            "raw_manifest": raw,
            "runtime_manifest": runtime,
            "build_contract": build_contract,
            "complete": set(raw) == set(RAW_SOURCES) and bool(runtime),
        }
    finally:
        connection.close()


def _normalized_target(
    raw_manifest: Mapping[str, Mapping[str, Any]],
    runtime_manifest: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if set(raw_manifest) != set(RAW_SOURCES):
        raise IncrementalUpdateError("incremental_target_raw_manifest_incomplete")
    raw: dict[str, dict[str, Any]] = {}
    for path, item in raw_manifest.items():
        if (
            type(path) is not str
            or type(item) is not dict
            or not _SHA_RE.fullmatch(str(item.get("sha256", "")))
            or type(item.get("row_count")) is not int
            or item["row_count"] < 0
        ):
            raise IncrementalUpdateError("incremental_target_raw_manifest_invalid")
        raw[path] = {
            "sha256": item["sha256"],
            "row_count": item["row_count"],
        }
    files = runtime_manifest.get("files") if isinstance(runtime_manifest, Mapping) else None
    if type(files) is not dict or not files:
        raise IncrementalUpdateError("incremental_target_runtime_manifest_invalid")
    runtime: dict[str, dict[str, Any]] = {}
    for path, item in files.items():
        if (
            type(path) is not str
            or type(item) is not dict
            or not _SHA_RE.fullmatch(str(item.get("sha256", "")))
        ):
            raise IncrementalUpdateError("incremental_target_runtime_manifest_invalid")
        runtime[path] = {"sha256": item["sha256"]}
    return dict(sorted(raw.items())), dict(sorted(runtime.items()))


def _diff(
    before: Mapping[str, Mapping[str, Any]],
    after: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        old = before.get(path)
        new = after.get(path)
        old_sha = old.get("sha256") if old else None
        new_sha = new.get("sha256") if new else None
        if old_sha == new_sha and old is not None and new is not None:
            continue
        kind = "added" if old is None else "deleted" if new is None else "modified"
        changes.append(
            {
                "path": path,
                "change": kind,
                "before_sha256": old_sha,
                "after_sha256": new_sha,
            }
        )
    return changes


def _components_for(path: str) -> set[str]:
    components = {"runtime_config"}
    if path in RAW_SOURCES:
        components.add("equipment")
    if path in CURATED_SOURCES:
        components.add("curated")
    return components


def build_incremental_plan(
    *,
    base_db: Path | None,
    base_database_release_id: str | None,
    target_commit: str,
    raw_manifest: Mapping[str, Mapping[str, Any]],
    runtime_manifest: Mapping[str, Any],
    build_contract: Mapping[str, Any],
    metadata_changed: bool = False,
    project_content_changed: bool = False,
    force_full: bool = False,
    physical_maintenance_required: bool = False,
) -> dict[str, Any]:
    """Return a deterministic conservative plan for one complete candidate."""

    target_commit = str(target_commit).lower()
    if not _COMMIT_RE.fullmatch(target_commit):
        raise IncrementalUpdateError("incremental_target_commit_invalid")
    target_raw, target_runtime = _normalized_target(raw_manifest, runtime_manifest)
    if (
        type(build_contract) is not dict
        or build_contract.get("contract")
        != "genshin-database-candidate-build-contract-v1"
        or type(build_contract.get("schema_version")) is not int
        or build_contract["schema_version"] < 1
        or type(build_contract.get("dependency_map_version")) is not int
        or build_contract["dependency_map_version"] < 1
    ):
        raise IncrementalUpdateError("incremental_build_contract_invalid")
    target_build_contract = json.loads(_canonical(build_contract))
    base = read_base_source_state(base_db)
    if base["present"]:
        if type(base_database_release_id) is not str or not base_database_release_id:
            raise IncrementalUpdateError("incremental_base_release_identity_required")
    elif base_database_release_id is not None:
        raise IncrementalUpdateError("incremental_base_release_identity_unexpected")
    raw_changes = _diff(base["raw_manifest"], target_raw)
    runtime_changes = _diff(base["runtime_manifest"], target_runtime)
    by_path: dict[str, dict[str, Any]] = {}
    for row in [*raw_changes, *runtime_changes]:
        prior = by_path.get(row["path"])
        if prior is not None and prior != row:
            raise IncrementalUpdateError("incremental_source_identity_conflict")
        by_path[row["path"]] = row
    changed = []
    affected: set[str] = set()
    for path, row in sorted(by_path.items()):
        components = sorted(_components_for(path))
        affected.update(components)
        changed.append({**row, "components": components})

    fallback: list[str] = []
    base_commit = base["upstream_commit"]
    if not base["present"]:
        fallback.append("base_database_missing")
    elif not base["complete"]:
        fallback.append("base_source_ownership_incomplete")
    if base["build_contract"] is None:
        fallback.append("base_build_contract_missing")
    elif base["build_contract"] != target_build_contract:
        fallback.append("build_contract_changed")
    if force_full:
        fallback.append("full_rebuild_requested")
    if base_commit is not None and base_commit != target_commit:
        # Runtime definition IDs and several source references embed the
        # accepted snapshot.  Until provenance is separated from business
        # rows, reusing those projections would publish contradictory source
        # claims, so a new upstream commit deliberately rebuilds all.
        fallback.append("accepted_snapshot_provenance_rebind_required")
    if any(row["path"] in HIGH_FANOUT_SOURCES for row in changed):
        fallback.append("high_fanout_source_changed")
    if any(not row["components"] for row in changed):
        fallback.append("source_dependency_unknown")

    if fallback:
        mode = "full_rebuild"
        rebuilt = list(COMPONENTS)
    elif changed:
        rebuilt = sorted(affected)
        mode = "full_rebuild" if set(rebuilt) == set(COMPONENTS) else "component_rebuild"
    elif project_content_changed:
        mode = "incremental"
        rebuilt = []
    elif metadata_changed:
        mode = "metadata_only"
        rebuilt = []
    elif physical_maintenance_required:
        mode = "physical_optimization"
        rebuilt = []
    else:
        mode = "no_op"
        rebuilt = []
    reused = [component for component in COMPONENTS if component not in rebuilt]
    body = {
        "contract": PLAN_CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "base_database_present": base["present"],
        "base_database_release_id": base_database_release_id,
        "base_source_ownership_complete": base["complete"],
        "base_commit": base_commit,
        "target_commit": target_commit,
        "base_build_contract_sha256": (
            _fingerprint(base["build_contract"])
            if base["build_contract"] is not None
            else None
        ),
        "target_build_contract": target_build_contract,
        "target_build_contract_sha256": _fingerprint(target_build_contract),
        "mode": mode,
        "changed_sources": changed,
        "rebuilt_components": rebuilt,
        "reused_components": reused,
        "fallback_reasons": sorted(set(fallback)),
        "metadata_changed": metadata_changed,
        "project_content_changed": project_content_changed,
        "target_raw_manifest_sha256": _fingerprint(target_raw),
        "target_runtime_manifest_sha256": _fingerprint(target_runtime),
    }
    if physical_maintenance_required:
        body.update(
            contract=PHYSICAL_PLAN_CONTRACT,
            schema_version=2,
            physical_maintenance_required=True,
        )
    return {**body, "plan_sha256": _fingerprint(body)}


def build_receipt(
    *,
    plan: Mapping[str, Any],
    base_release_core_sha256: str | None,
    candidate_release_core_sha256: str,
    raw_reused_files: int,
    raw_rebuilt_files: int,
) -> dict[str, Any]:
    """Build a deterministic receipt from an executed, validated plan."""

    if (
        plan.get("contract") not in {PLAN_CONTRACT, PHYSICAL_PLAN_CONTRACT}
        or plan.get("plan_sha256") is None
    ):
        raise IncrementalUpdateError("incremental_plan_invalid")
    plan_body = {key: value for key, value in plan.items() if key != "plan_sha256"}
    if plan["plan_sha256"] != _fingerprint(plan_body):
        raise IncrementalUpdateError("incremental_plan_fingerprint_mismatch")
    for value in (candidate_release_core_sha256,):
        if not _SHA_RE.fullmatch(str(value)):
            raise IncrementalUpdateError("incremental_receipt_digest_invalid")
    if base_release_core_sha256 is not None and not _SHA_RE.fullmatch(
        str(base_release_core_sha256)
    ):
        raise IncrementalUpdateError("incremental_receipt_digest_invalid")
    if any(type(value) is not int or value < 0 for value in (raw_reused_files, raw_rebuilt_files)):
        raise IncrementalUpdateError("incremental_receipt_counts_invalid")
    body = {
        "contract": RECEIPT_CONTRACT,
        "schema_version": SCHEMA_VERSION,
        "plan_sha256": plan["plan_sha256"],
        "mode": plan["mode"],
        "base_commit": plan["base_commit"],
        "base_database_release_id": plan["base_database_release_id"],
        "target_commit": plan["target_commit"],
        "target_build_contract_sha256": plan["target_build_contract_sha256"],
        "metadata_changed": plan["metadata_changed"],
        "project_content_changed": plan["project_content_changed"],
        "changed_sources": plan["changed_sources"],
        "rebuilt_components": plan["rebuilt_components"],
        "reused_components": plan["reused_components"],
        "fallback_reasons": plan["fallback_reasons"],
        "base_release_core_sha256": base_release_core_sha256,
        "candidate_release_core_sha256": candidate_release_core_sha256,
        "raw_reused_files": raw_reused_files,
        "raw_rebuilt_files": raw_rebuilt_files,
    }
    if plan.get("contract") == PHYSICAL_PLAN_CONTRACT:
        body["contract"] = PHYSICAL_RECEIPT_CONTRACT
        body["schema_version"] = 2
        body["physical_maintenance_required"] = plan["physical_maintenance_required"]
    return {**body, "receipt_sha256": _fingerprint(body)}


__all__ = [
    "COMPONENTS",
    "PLAN_CONTRACT",
    "RECEIPT_CONTRACT",
    "IncrementalUpdateError",
    "build_incremental_plan",
    "build_receipt",
    "read_base_source_state",
]
