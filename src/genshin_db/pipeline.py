"""Build complete isolated database candidates; installation is a separate transaction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from genshin_db import fetcher, raw_store, upstream_version
from genshin_db.candidate_build_context import CandidateBuildContext
from genshin_db.candidate_build_ports import CandidateBuildPorts
from genshin_db.candidate_build_stages import (
    admit_candidate_request,
    build_candidate_components,
    capture_candidate_sources,
    optimize_candidate_physical_layout,
    plan_candidate_changes,
    prepare_candidate_workspace,
    publish_candidate_artifacts,
    verify_candidate_components,
)
from genshin_db.candidate_physical_layout import discard_unpublished_database
from genshin_db.equipment import (
    SCHEMA_VERSION as EQUIPMENT_SCHEMA_VERSION,
)
from genshin_db.equipment import (
    build_equipment_layer,
    equipment_datasets,
    validate_equipment,
)
from genshin_db.extractor import build_curated_layer
from genshin_db.labeler import apply_labels
from genshin_db.release_profile import (
    DatabaseReleaseError,
    database_live_receipt_matches,
    read_database_identity,
    validate_database_live_receipt,
    validate_database_release_manifest,
)
from genshin_db.runtime_catalog import (
    INDEXER_VERSION,
    fingerprint,
)
from genshin_db.runtime_catalog import (
    PROFILE_ID as RUNTIME_PROFILE_ID,
)
from genshin_db.runtime_catalog import (
    PROFILE_VERSION as RUNTIME_PROFILE_VERSION,
)
from genshin_db.runtime_catalog import (
    SCHEMA_VERSION as RUNTIME_SCHEMA_VERSION,
)
from genshin_db.runtime_physical_index import (
    ensure_physical_indexes,
    missing_physical_indexes,
    preservation_manifest,
)
from genshin_db.runtime_store import (
    build_runtime_layer,
    runtime_datasets,
    validate_runtime,
)
from genshin_db.semantic_store import SCHEMA_VERSION as SEMANTIC_SCHEMA_VERSION
from genshin_db.semantic_store import semantic_datasets
from genshin_db.semantic_store import validate_component as validate_semantic_component
from genshin_runtime.normalization import NORMALIZATION_VERSION, for_commit

LIVE_DB_NAME = "genshin.db"
CURATED_TABLES = ["character", "skill", "talent", "skill_param", "text_map"]
CANDIDATE_BUILD_CONTRACT = "genshin-database-candidate-build-contract-v1"
CANDIDATE_BUILD_SCHEMA_VERSION = 1
DEPENDENCY_MAP_VERSION = 1
CURATED_SCHEMA_VERSION = 1


class PipelineError(Exception):
    """管道校验或执行失败时抛出（旧库不受影响）。"""


class CandidateNoOpError(PipelineError):
    """A pure no-op is an audit result, never a publishable DB candidate."""

    def __init__(self, plan: Mapping[str, Any]):
        self.plan = dict(plan)
        super().__init__("来源、构建合同与发行元数据均未变化；不生成新数据库版本")




@dataclass
class CandidateBuildReport:
    """Result of building one complete candidate without touching its base."""

    output_db: str
    plan: dict
    receipt: dict
    datasets: dict
    stats: dict = field(default_factory=dict)
    source_verification: dict | None = None
    receipt_path: str | None = None
    content_application_receipt: dict | None = None
    content_receipt_path: str | None = None


def _read_meta_value(db_path: Path, key: str) -> str | None:
    if not db_path.exists():
        return None
    conn = sqlite3.connect(db_path)
    try:
        table = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone()[0]
        if not table:
            return None
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _validate(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise PipelineError(f"integrity_check 失败: {integrity}")
        for table in CURATED_TABLES:
            suffix = "" if table == "text_map" else " WHERE version_line='release'"
            count = conn.execute(f"SELECT COUNT(*) FROM {table}{suffix}").fetchone()[0]
            if count == 0:
                raise PipelineError(f"表 {table} 为空")
    finally:
        conn.close()


# Backwards-compatible public matcher for callers that only inspect a subject.
# The update pipeline itself uses the source-bound resolver below.
_VERSION_RE = re.compile(r"OS(?:CB|REL)Win(\d+(?:\.\d+){2,3})(?=$|[_\s.-])")


def _game_version(repo_dir: Path, expected_commit: str | None = None) -> str:
    evidence = fetcher.inspect_upstream_version(repo_dir, expected_commit)
    return evidence.get("version") or "unknown"


def _version_evidence(fetched: fetcher.FetchResult) -> dict:
    evidence = fetched.version_evidence
    if not isinstance(evidence, dict):
        evidence = fetcher.inspect_upstream_version(fetched.repo_dir, fetched.commit)
    return evidence


def _evidence_json(evidence: dict) -> str:
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _require_version_evidence(evidence: dict, commit: str) -> None:
    """Fail before reading/building sources when the version identity is incomplete."""

    try:
        upstream_version.validate_resolved_evidence(
            evidence,
            expected_commit=commit,
        )
    except ValueError:
        issues = evidence.get("issues")
        detail = ",".join(str(issue) for issue in issues) if isinstance(issues, list) else "unknown"
        raise PipelineError(f"上游版本证据不完整，拒绝构建: {detail}")


def _sqlite_backup_readonly(source_path: Path, destination_path: Path) -> None:
    """Copy one stable SQLite snapshot without opening the source for writes."""

    source = sqlite3.connect(source_path.resolve().as_uri() + "?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.execute("PRAGMA query_only=ON")
        source.execute("BEGIN")
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _candidate_metadata_changed(base_db: Path | None, evidence: dict, commit: str) -> bool:
    if base_db is None or not base_db.is_file():
        return True
    expected = {
        "game_version": evidence.get("version") or "unknown",
        "upstream_commit": commit,
        "upstream_track": evidence.get("track") or "unknown",
        "upstream_updated_at": evidence.get("snapshot_updated_at") or "unknown",
        "upstream_version_evidence": _evidence_json(evidence),
    }
    with closing(
        sqlite3.connect(base_db.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        actual = dict(connection.execute("SELECT key,value FROM meta")) if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'"
        ).fetchone() else {}
    return any(
        actual.get(key) != value or actual.get(f"{key}:release") != value
        for key, value in expected.items()
    )


def _candidate_build_contract(target_commit: str) -> dict[str, Any]:
    """Describe the versioned build semantics, independently from its algorithm.

    The contract is deliberately data rather than a hard-coded comparison of
    individual implementation branches.  Future maintainers may replace the
    incremental algorithm or component boundaries by bumping this document;
    an accepted base built by a different contract is conservatively rebuilt.
    """

    from genshin_db.capability_catalog_build import CATALOG_BUILD_VERSION
    profile = for_commit(target_commit)
    normalization = (
        profile.receipt(target_commit)
        if profile is not None
        else {
            "state": "unavailable",
            "normalization_version": NORMALIZATION_VERSION,
            "upstream_commit": target_commit,
        }
    )
    return {
        "contract": CANDIDATE_BUILD_CONTRACT,
        "schema_version": CANDIDATE_BUILD_SCHEMA_VERSION,
        "dependency_map_version": DEPENDENCY_MAP_VERSION,
        "components": {
            "curated_schema_version": CURATED_SCHEMA_VERSION,
            "equipment_schema_version": EQUIPMENT_SCHEMA_VERSION,
            "runtime_schema_version": RUNTIME_SCHEMA_VERSION,
            "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
        },
        "runtime": {
            "profile_id": RUNTIME_PROFILE_ID,
            "profile_version": RUNTIME_PROFILE_VERSION,
            "indexer_version": INDEXER_VERSION,
        },
        "normalization": normalization,
        "special_reaction_capability_catalog": CATALOG_BUILD_VERSION,
    }


def _document(value: Mapping[str, Any] | Path, *, manifest: bool) -> dict[str, Any]:
    try:
        if isinstance(value, Mapping):
            raw = dict(value)
        else:
            raw = json.loads(Path(value).read_text(encoding="utf-8"))
        return (
            validate_database_release_manifest(raw)
            if manifest
            else validate_database_live_receipt(raw)
        )
    except (OSError, UnicodeError, json.JSONDecodeError, DatabaseReleaseError) as error:
        raise PipelineError("base accepted manifest/receipt无效") from error


def _validate_accepted_base(
    base: Path,
    *,
    manifest: Mapping[str, Any] | Path | None,
    receipt: Mapping[str, Any] | Path | None,
) -> dict[str, Any]:
    """Bind an incremental base to its accepted manifest and install receipt."""

    if manifest is None or receipt is None:
        raise PipelineError("增量候选必须提供base accepted manifest与receipt")
    checked_manifest = _document(manifest, manifest=True)
    checked_receipt = _document(receipt, manifest=False)
    try:
        identity = read_database_identity(base)
        matches = database_live_receipt_matches(
            checked_receipt,
            identity,
            checked_manifest,
        )
    except DatabaseReleaseError as error:
        raise PipelineError("base accepted身份无法核验") from error
    if not matches:
        raise PipelineError("base数据库与accepted manifest/receipt不一致")
    return checked_manifest


def _candidate_build_ports() -> CandidateBuildPorts:
    return CandidateBuildPorts(
        PipelineError=lambda *a, **kw: PipelineError(*a, **kw),
        CandidateNoOpError=lambda *a, **kw: CandidateNoOpError(*a, **kw),
        CandidateBuildReport=lambda *a, **kw: CandidateBuildReport(*a, **kw),
        validate_accepted_base=lambda *a, **kw: _validate_accepted_base(*a, **kw),
        require_version_evidence=lambda *a, **kw: _require_version_evidence(*a, **kw),
        source_manifest=lambda *a, **kw: _source_manifest(*a, **kw),
        selected_source_manifest=lambda *a, **kw: _selected_source_manifest(*a, **kw),
        candidate_metadata_changed=lambda *a, **kw: _candidate_metadata_changed(*a, **kw),
        candidate_build_contract=lambda *a, **kw: _candidate_build_contract(*a, **kw),
        sqlite_backup_readonly=lambda *a, **kw: _sqlite_backup_readonly(*a, **kw),
        build_curated_layer=lambda *a, **kw: build_curated_layer(*a, **kw),
        apply_labels=lambda *a, **kw: apply_labels(*a, **kw),
        build_equipment_layer=lambda *a, **kw: build_equipment_layer(*a, **kw),
        build_runtime_layer=lambda *a, **kw: build_runtime_layer(*a, **kw),
        evidence_json=lambda *a, **kw: _evidence_json(*a, **kw),
        validate=lambda *a, **kw: _validate(*a, **kw),
        validate_equipment=lambda *a, **kw: validate_equipment(*a, **kw),
        validate_runtime=lambda *a, **kw: validate_runtime(*a, **kw),
        validate_semantic_component=lambda *a, **kw: validate_semantic_component(*a, **kw),
        write_json_atomic=lambda *a, **kw: _write_json_atomic(*a, **kw),
        datasets=lambda *a, **kw: _datasets(*a, **kw),
        ensure_physical_indexes=lambda *a, **kw: ensure_physical_indexes(*a, **kw),
        missing_physical_indexes=lambda *a, **kw: missing_physical_indexes(*a, **kw),
        preservation_manifest=lambda *a, **kw: preservation_manifest(*a, **kw),
    )


def build_candidate_database(
    *,
    base_db: Path | None,
    output_db: Path,
    repo_dir: Path,
    target_commit: str,
    version_evidence: dict | None = None,
    force_full: bool = False,
    verify_sources: bool = True,
    receipt_path: Path | None = None,
    content_change_set: dict | None = None,
    content_approval: dict | None = None,
    base_database_release_id: str | None = None,
    target_database_release_id: str | None = None,
    content_receipt_path: Path | None = None,
    base_release_manifest: Mapping[str, Any] | Path | None = None,
    base_live_receipt: Mapping[str, Any] | Path | None = None,
) -> CandidateBuildReport:
    """Build a complete, versionable SQLite candidate from a read-only base.

    ``base_db`` is never replaced or opened for writing.  The new database is
    assembled at a private temporary path, fully validated, then published to
    the previously absent ``output_db`` path.  Installation into the active
    location remains the responsibility of the release migration transaction.
    """

    run = CandidateBuildContext(
        base_db=base_db,
        output_db=output_db,
        repo_dir=repo_dir,
        target_commit=target_commit,
        version_evidence=version_evidence,
        force_full=force_full,
        verify_sources=verify_sources,
        receipt_path=receipt_path,
        content_change_set=content_change_set,
        content_approval=content_approval,
        base_database_release_id=base_database_release_id,
        target_database_release_id=target_database_release_id,
        content_receipt_path=content_receipt_path,
        base_release_manifest=base_release_manifest,
        base_live_receipt=base_live_receipt,
    )
    ports = _candidate_build_ports()
    admit_candidate_request(run, ports)
    capture_candidate_sources(run, ports)
    plan_candidate_changes(run, ports)
    prepare_candidate_workspace(run, ports)
    try:
        build_candidate_components(run, ports)
        verify_candidate_components(run, ports)
        optimize_candidate_physical_layout(run, ports)
        return publish_candidate_artifacts(run, ports)
    except Exception:
        discard_unpublished_database(run.temporary)
        raise


def run_update(data_dir: Path, force: bool = False, *, offline_repo: Path | None = None,
               expected_commit: str | None = None) -> None:
    """Retired in-place update entry; use a complete candidate and release installer."""
    from genshin_db.live_mutation_policy import assert_candidate_data_dir

    if (offline_repo is None) != (expected_commit is None):
        raise ValueError("offline_repo与expected_commit必须同时提供")
    assert_candidate_data_dir(data_dir, "pipeline.run_update")
    raise PipelineError(
        "旧数据库更新入口已退役；请构建完整candidate并经发行迁移安装，禁止原地修补数据库"
    )




def _datasets(path):
    with closing(sqlite3.connect(path)) as conn, conn:
        return {**equipment_datasets(conn), **runtime_datasets(conn), **semantic_datasets(conn)}








def _source_manifest(repo_dir):
    result = {}
    for rel in raw_store.RAW_SOURCES:
        path = repo_dir / rel
        if not path.is_file():
            raise PipelineError(f"必需来源文件缺失: {rel}")
        content = path.read_bytes()
        result[rel] = {"sha256": hashlib.sha256(content).hexdigest(),
                       "row_count": len(json.loads(content.decode("utf-8-sig")))}
    return result


def _selected_source_manifest(repo_dir, raw_manifest, runtime_manifest):
    """Merge every source actually consumed by raw/equipment/runtime builders."""

    files = {name: dict(spec) for name, spec in runtime_manifest["files"].items()}
    for relative, raw_spec in raw_manifest.items():
        payload = (repo_dir / relative).read_bytes()
        spec = {
            "sha256": raw_spec["sha256"],
            "size_bytes": len(payload),
            "family": "raw_source",
        }
        existing = files.get(relative)
        if existing is not None and (
            existing.get("sha256") != spec["sha256"]
            or existing.get("size_bytes") != spec["size_bytes"]
        ):
            raise PipelineError(f"来源清单身份冲突: {relative}")
        files.setdefault(relative, spec)
    body = {
        "profile_id": "database-selected-source-verification-v1",
        "files": dict(sorted(files.items())),
    }
    return {**body, "fingerprint": fingerprint(body)}








if __name__ == "__main__":
    from genshin_db.live_mutation_policy import rejection_message

    raise SystemExit(rejection_message("python -m genshin_db.pipeline"))
