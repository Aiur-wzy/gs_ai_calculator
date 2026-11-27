"""Fixed candidate stages; only the installation coordinator mutates live paths.

The component pass is shared with isolated fixtures.  There is no legacy
builder selection, installation lock, or active database write in these passes.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any
from uuid import uuid4

from genshin_db import fetcher, raw_store
from genshin_db.annotation_store import create_annotation_tables
from genshin_db.capability_catalog_build import publish_capability_catalog
from genshin_db.candidate_build_context import CandidateBuildContext
from genshin_db.candidate_build_ports import CandidateBuildPorts
from genshin_db.candidate_physical_layout import assert_rollback_database
from genshin_db.incremental_update import (
    BUILD_CONTRACT_META_KEY,
    build_incremental_plan,
    read_base_source_state,
)
from genshin_db.incremental_update import build_receipt as build_incremental_receipt
from genshin_db.release_integrity import build_release_core
from genshin_db.runtime_catalog import enumerate_runtime_sources
from genshin_db.runtime_store import runtime_datasets
from genshin_db.semantic_store import ensure_schema as ensure_semantic_schema
from genshin_runtime.diagnostics import diagnosed, diagnostic_stage


def _candidate_diagnostic_metadata(arguments):
    run = arguments["run"]
    return {"source_commit": run.target_commit,
            "change_set_id": (run.content_change_set or {}).get("change_set_id")}


def _observe_component(run, component, operation, *args, **kwargs):
    with diagnostic_stage(
        "database-build", "build-component-v1", source_commit=run.target_commit, component=component,
        inputs=lambda: {"plan": run.plan, "source_manifest": run.selected_manifest},
        change_set_id=lambda: (run.content_change_set or {}).get("change_set_id"),
    ) as audit:
        result = operation(*args, **kwargs)
        audit.result(outputs={"result": result}, changed_paths=(f"components.{component}",))
        return result


@diagnosed('database-build', 'admit-candidate-request-v1', metadata=_candidate_diagnostic_metadata)
def admit_candidate_request(
    run: CandidateBuildContext, ports: CandidateBuildPorts
) -> None:
    from genshin_db.live_mutation_policy import assert_candidate_database

    run.output = assert_candidate_database(
        run.output_db, "pipeline.build_candidate_database"
    )
    run.base = Path(run.base_db).resolve() if run.base_db is not None else None
    run.repo = Path(run.repo_dir).resolve()
    if run.base is not None and (not run.base.is_file()):
        raise ports.PipelineError("候选构建base_db不存在")
    if run.base is not None and run.base == run.output:
        raise ports.PipelineError("候选输出不得覆盖base_db")
    if run.output.exists():
        raise ports.PipelineError("候选输出已存在，拒绝覆盖")
    run.checked_base_manifest = (
        ports.validate_accepted_base(
            run.base, manifest=run.base_release_manifest, receipt=run.base_live_receipt
        )
        if run.base is not None
        else None
    )
    run.content_requested = any(
        value is not None
        for value in (
            run.content_change_set,
            run.content_approval,
            run.base_database_release_id,
            run.target_database_release_id,
            run.content_receipt_path,
        )
    )
    if run.content_requested and (
        run.base is None
        or run.content_change_set is None
        or run.content_approval is None
        or (not run.base_database_release_id)
        or (not run.target_database_release_id)
    ):
        raise ports.PipelineError("项目内容变更必须完整绑定base/target发行与用户批准")
    if (
        run.content_requested
        and run.checked_base_manifest is not None
        and (
            run.checked_base_manifest["database_release_id"]
            != run.base_database_release_id
        )
    ):
        raise ports.PipelineError("项目内容变更的base发行与accepted manifest不一致")
    run.destination_receipt = run.receipt_path or run.output.with_suffix(
        run.output.suffix + ".incremental-receipt.json"
    )
    run.destination_content_receipt = (
        run.content_receipt_path
        or run.output.with_suffix(run.output.suffix + ".content-receipt.json")
        if run.content_requested
        else None
    )
    control_paths = [run.output.resolve(), Path(run.destination_receipt).resolve()]
    if run.destination_content_receipt is not None:
        control_paths.append(Path(run.destination_content_receipt).resolve())
    if len(control_paths) != len(set(control_paths)):
        raise ports.PipelineError("候选数据库与两类回执必须使用不同路径")
    run.target_commit = str(run.target_commit).lower()
    if run.version_evidence is None:
        run.version_evidence = fetcher.inspect_upstream_version(
            run.repo, run.target_commit
        )
    ports.require_version_evidence(run.version_evidence, run.target_commit)


@diagnosed('database-build', 'capture-candidate-sources-v1', metadata=_candidate_diagnostic_metadata)
def capture_candidate_sources(
    run: CandidateBuildContext, ports: CandidateBuildPorts
) -> None:
    run.raw_manifest = ports.source_manifest(run.repo)
    run.runtime_manifest = enumerate_runtime_sources(run.repo)
    run.selected_manifest = ports.selected_source_manifest(
        run.repo, run.raw_manifest, run.runtime_manifest
    )
    run.source_verification = (
        fetcher.verify_pinned_sources(
            run.repo, run.target_commit, run.selected_manifest
        )
        if run.verify_sources
        else {
            "mode": "caller-reviewed-fixture",
            "commit": run.target_commit,
            "manifest_fingerprint": run.selected_manifest["fingerprint"],
            "checked_files": len(run.selected_manifest["files"]),
        }
    )
    run.metadata_changed = ports.candidate_metadata_changed(
        run.base, run.version_evidence, run.target_commit
    )
    run.build_contract = ports.candidate_build_contract(run.target_commit)
    if run.base is not None:
        with closing(sqlite3.connect(run.base.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            run.physical_maintenance_required = bool(ports.missing_physical_indexes(connection))


@diagnosed('database-build', 'plan-candidate-changes-v1', metadata=_candidate_diagnostic_metadata)
def plan_candidate_changes(
    run: CandidateBuildContext, ports: CandidateBuildPorts
) -> None:
    run.plan = build_incremental_plan(
        base_db=run.base,
        base_database_release_id=run.checked_base_manifest["database_release_id"]
        if run.checked_base_manifest is not None
        else None,
        target_commit=run.target_commit,
        raw_manifest=run.raw_manifest,
        runtime_manifest=run.runtime_manifest,
        build_contract=run.build_contract,
        metadata_changed=run.metadata_changed,
        project_content_changed=bool(
            run.content_change_set and run.content_change_set.get("operations")
        ),
        force_full=run.force_full,
        physical_maintenance_required=run.physical_maintenance_required,
    )
    if run.plan["mode"] == "no_op":
        raise ports.CandidateNoOpError(run.plan)


@diagnosed('database-build', 'prepare-candidate-workspace-v1', metadata=_candidate_diagnostic_metadata)
def prepare_candidate_workspace(
    run: CandidateBuildContext, ports: CandidateBuildPorts
) -> None:
    if run.physical_maintenance_required and run.base is not None:
        assert_rollback_database(run.base, ports.PipelineError)
    run.before_stat = run.base.stat() if run.base is not None else None
    run.before_core = (
        build_release_core(run.base)["root_sha256"] if run.base is not None else None
    )
    run.temporary = run.output.with_name(f".{run.output.name}.{uuid4().hex}.candidate")
    run.temporary.parent.mkdir(parents=True, exist_ok=True)
    if run.temporary.exists():
        run.temporary.unlink()
    run.raw_stats = None
    run.content_application_receipt = None
    run.stats: dict[str, Any] = {}


@diagnosed('database-build', 'build-candidate-components-v1', metadata=_candidate_diagnostic_metadata)
def build_candidate_components(
    run: CandidateBuildContext, ports: CandidateBuildPorts
) -> None:
    if run.base is not None:
        ports.sqlite_backup_readonly(run.base, run.temporary)
    if run.plan["mode"] == "physical_optimization":
        # A physical-only candidate preserves every table value and rowid.
        # Replacing identical meta rows would still move SQLite rowids.
        return
    run.content_timestamp = run.version_evidence["snapshot_updated_at"]
    run.rebuilt = set(run.plan["rebuilt_components"])
    if run.plan["mode"] in {"full_rebuild", "component_rebuild"}:
        base_state = read_base_source_state(run.base)
        run.raw_stats = _observe_component(run, "raw", raw_store.build_raw_layer,
            run.repo,
            run.temporary,
            content_timestamp=run.content_timestamp,
            reuse_from=run.base if run.base is not None else None,
            base_manifest=base_state["raw_manifest"] if run.base is not None else None,
        )
        if run.raw_stats.manifest != run.raw_manifest:
            raise ports.PipelineError("候选构建期间raw来源改变")
        run.stats.update(
            raw_files=run.raw_stats.files,
            raw_rows=run.raw_stats.rows,
            raw_reused_files=run.raw_stats.reused_files,
            raw_rebuilt_files=run.raw_stats.rebuilt_files,
        )
    run.game_version = run.version_evidence.get("version") or "unknown"
    if "curated" in run.rebuilt:
        extract_stats = _observe_component(run, "curated", ports.build_curated_layer,
            run.temporary,
            run.game_version,
            "release",
            upstream_commit=run.target_commit,
            content_timestamp=run.content_timestamp,
        )
        labels = ports.apply_labels(run.temporary, "release")
        run.component_reports["curated"] = extract_stats
        run.stats.update(
            {f"curated_{key}": value for key, value in extract_stats.counts.items()}
        )
        run.stats["labeled_talent"] = labels["talent"]["labeled"]
        run.stats["labeled_skill_param"] = labels["skill_param"]["labeled"]
    if "equipment" in run.rebuilt:
        equipment_report = _observe_component(run, "equipment", ports.build_equipment_layer,
            run.temporary,
            "release",
            upstream_commit=run.target_commit,
            manifest=run.raw_manifest,
            content_timestamp=run.content_timestamp,
        )
        run.component_reports["equipment"] = equipment_report
        run.stats.update(
            {
                f"equipment_{key}": value
                for key, value in equipment_report["counts"].items()
            }
        )
    if "runtime_config" in run.rebuilt:
        runtime_report = _observe_component(run, "runtime_config", ports.build_runtime_layer,
            run.temporary,
            run.repo,
            version_line="release",
            upstream_commit=run.target_commit,
            manifest=run.runtime_manifest,
            content_timestamp=run.content_timestamp,
        )
        run.stats.update(runtime_report["table_counts"])
    create_annotation_tables(run.temporary)
    with closing(sqlite3.connect(run.temporary)) as connection, connection:
        ensure_semantic_schema(connection, version_line="release")
        runtime_state = runtime_datasets(connection)["runtime_config"]["lines"][
            "release"
        ]
        normalization = runtime_state.get("normalization")
        normalization_version = (
            str(normalization.get("normalization_version"))
            if isinstance(normalization, dict)
            else "unavailable"
        )
        version_meta = {
            "game_version": run.game_version,
            "upstream_commit": run.target_commit,
            "upstream_version_resolver_version": str(
                run.version_evidence.get("resolver_version", 0)
            ),
            "upstream_version_status": str(
                run.version_evidence.get("status", "unresolved")
            ),
            "upstream_track": run.version_evidence.get("track") or "unknown",
            "upstream_updated_at": run.version_evidence.get("snapshot_updated_at")
            or "unknown",
            "upstream_version_evidence": ports.evidence_json(run.version_evidence),
            "indexer_version": str(runtime_state.get("indexer_version")),
            "normalization_version": normalization_version,
        }
        for key, value in version_meta.items():
            connection.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
            connection.execute(
                "INSERT OR REPLACE INTO meta VALUES (?,?)", (f"{key}:release", value)
            )
        connection.execute(
            "INSERT OR REPLACE INTO meta VALUES (?,?)",
            (
                BUILD_CONTRACT_META_KEY,
                json.dumps(
                    run.build_contract,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            ),
        )
        run.stats["special_reaction_capability_catalog"] = publish_capability_catalog(
            connection, run.base, run.game_version, run.selected_manifest["files"],
        )
    if run.content_requested:
        from genshin_db.content_change_set import apply_content_change_set

        run.content_application_receipt = _observe_component(run, "project_content", apply_content_change_set,
            base_database=run.base,
            candidate_database=run.temporary,
            change_set=run.content_change_set,
            approval=run.content_approval,
            base_database_release_id=run.base_database_release_id,
            target_database_release_id=run.target_database_release_id,
        )


@diagnosed('database-build', 'verify-candidate-components-v1', metadata=_candidate_diagnostic_metadata)
def verify_candidate_components(
    run: CandidateBuildContext, ports: CandidateBuildPorts
) -> None:
    ports.validate(run.temporary)
    if enumerate_runtime_sources(run.repo) != run.runtime_manifest:
        raise ports.PipelineError("候选构建期间runtime来源改变")
    if run.verify_sources:
        run.source_verification = fetcher.verify_pinned_sources(
            run.repo, run.target_commit, run.selected_manifest
        )
    with closing(sqlite3.connect(run.temporary)) as connection:
        from genshin_db.publication_evidence import (
            table_exists,
            validate_publication_inventory,
        )

        connection.row_factory = sqlite3.Row
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ports.PipelineError("候选完整性检查失败")
        ports.validate_equipment(connection, "release")
        ports.validate_runtime(connection, "release", deep=True)
        ports.validate_semantic_component(connection)
        if table_exists(connection):
            run.stats["entity_publication_counts"] = validate_publication_inventory(connection)
    if run.content_application_receipt is not None:
        from genshin_db.content_change_set import project_content_fingerprint

        if (
            project_content_fingerprint(run.temporary)
            != run.content_application_receipt["after_project_content_fingerprint"]
        ):
            raise ports.PipelineError("项目内容在批准change set应用后发生未申报漂移")


@diagnosed('database-build', 'optimize-candidate-physical-layout-v1', metadata=_candidate_diagnostic_metadata)
def optimize_candidate_physical_layout(
    run: CandidateBuildContext, ports: CandidateBuildPorts
) -> None:
    if not run.physical_maintenance_required:
        return
    with closing(sqlite3.connect(run.temporary)) as connection, connection:
        before = ports.preservation_manifest(connection)
        created = _observe_component(run, "physical_indexes", ports.ensure_physical_indexes, connection)
        for line, state in runtime_datasets(connection)["runtime_config"]["lines"].items():
            if state["state"] == "ready":
                ports.validate_runtime(connection, line, deep=True)
        if ports.preservation_manifest(connection) != before:
            raise ports.PipelineError("候选读取优化改变了非物理索引内容")
        run.stats["physical_indexes_created"] = len(created)


@diagnosed('database-build', 'publish-candidate-artifacts-v1', metadata=_candidate_diagnostic_metadata)
def publish_candidate_artifacts(
    run: CandidateBuildContext, ports: CandidateBuildPorts
) -> Any:
    if run.physical_maintenance_required:
        if run.base is not None:
            assert_rollback_database(run.base, ports.PipelineError)
        assert_rollback_database(run.temporary, ports.PipelineError)
    run.candidate_core = build_release_core(run.temporary)["root_sha256"]
    run.receipt = build_incremental_receipt(
        plan=run.plan,
        base_release_core_sha256=run.before_core,
        candidate_release_core_sha256=run.candidate_core,
        raw_reused_files=run.raw_stats.reused_files
        if run.raw_stats is not None
        else len(raw_store.RAW_SOURCES),
        raw_rebuilt_files=run.raw_stats.rebuilt_files
        if run.raw_stats is not None
        else 0,
    )
    if run.base is not None:
        after_stat = run.base.stat()
        if (
            after_stat.st_size != run.before_stat.st_size
            or after_stat.st_mtime_ns != run.before_stat.st_mtime_ns
            or build_release_core(run.base)["root_sha256"] != run.before_core
        ):
            raise ports.PipelineError("候选构建期间base_db发生变化")
    os.replace(run.temporary, run.output)
    if run.destination_receipt.exists() or (
        run.destination_content_receipt is not None
        and run.destination_content_receipt.exists()
    ):
        run.output.unlink(missing_ok=True)
        raise ports.PipelineError("候选构建回执已存在，拒绝覆盖")
    try:
        ports.write_json_atomic(run.destination_receipt, run.receipt)
        if run.destination_content_receipt is not None:
            ports.write_json_atomic(
                run.destination_content_receipt, run.content_application_receipt
            )
    except Exception:
        run.output.unlink(missing_ok=True)
        run.destination_receipt.unlink(missing_ok=True)
        if run.destination_content_receipt is not None:
            run.destination_content_receipt.unlink(missing_ok=True)
        raise
    return ports.CandidateBuildReport(
        output_db=str(run.output),
        plan=run.plan,
        receipt=run.receipt,
        datasets=ports.datasets(run.output),
        stats=run.stats,
        source_verification=run.source_verification,
        receipt_path=str(run.destination_receipt),
        content_application_receipt=run.content_application_receipt,
        content_receipt_path=str(run.destination_content_receipt)
        if run.destination_content_receipt is not None
        else None,
    )
