"""Isolated synthetic databases built by the production component passes.

This is a test-fixture adapter, not a release/install API.  Fixtures do not
pretend to own an accepted manifest or approval, acquire live writer fences,
merge concurrent edits, make backups, or maintain the legacy import log.
Production candidate/installer tests call the actual APIs directly.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from genshin_db import fetcher, pipeline
from genshin_db.candidate_build_context import CandidateBuildContext
from genshin_db.candidate_build_stages import (
    build_candidate_components,
    capture_candidate_sources,
    optimize_candidate_physical_layout,
    plan_candidate_changes,
    prepare_candidate_workspace,
    publish_candidate_artifacts,
    verify_candidate_components,
)
from genshin_db.candidate_physical_layout import discard_unpublished_database
from genshin_db.live_mutation_policy import assert_candidate_data_dir
from tests.fixture_candidate_validation import _complete, _preflight


@dataclass
class UpdateReport:
    updated: bool
    old_version: str | None
    new_version: str | None
    stats: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    orphan_annotations: int = 0
    action: str = "unchanged"
    datasets: dict = field(default_factory=dict)
    equipment_warnings: list[dict] = field(default_factory=list)
    equipment_warnings_total: int = 0
    backup_path: str | None = None
    source_verification: dict | None = None
    upstream_version_evidence: dict | None = None
    incremental_plan: dict | None = None
    incremental_receipt: dict | None = None

def build_database(
    data_dir: Path,
    force: bool = False,
    *,
    offline_repo: Path | None = None,
    expected_commit: str | None = None,
) -> UpdateReport:
    if (offline_repo is None) != (expected_commit is None):
        raise ValueError("offline_repo与expected_commit必须同时提供")
    folder = assert_candidate_data_dir(data_dir, "test.fixture_candidate_builder")
    database = folder / "genshin.db"
    _preflight(database)
    fetched = (
        fetcher.ensure_upstream(folder / "upstream")
        if offline_repo is None
        else fetcher.inspect_pinned_upstream(offline_repo, expected_commit)
    )
    evidence = pipeline._version_evidence(fetched)
    pipeline._require_version_evidence(evidence, fetched.commit)
    old_commit = pipeline._read_meta_value(database, "upstream_commit")
    destination = folder / "fixture-build" / "candidate.db"
    run = CandidateBuildContext(
        base_db=database if database.exists() else None,
        output_db=destination,
        repo_dir=fetched.repo_dir,
        target_commit=fetched.commit,
        version_evidence=evidence,
        force_full=force,
    )
    # Synthetic fixture admission is intentionally separate.  There is no
    # production bypass: none of these helpers is shipped in the application.
    run.base = run.base_db
    run.output = destination
    run.repo = Path(fetched.repo_dir).resolve()
    run.destination_receipt = destination.with_suffix(".receipt.json")
    if run.base is not None:
        run.checked_base_manifest = {"database_release_id": "synthetic-fixture-base"}
    ports = pipeline._candidate_build_ports()
    capture_candidate_sources(run, ports)
    if run.base is not None and not _complete(
        run.base, run.target_commit, run.raw_manifest,
        run.runtime_manifest, evidence,
    ):
        run.force_full = True
    try:
        plan_candidate_changes(run, ports)
    except pipeline.CandidateNoOpError:
        return UpdateReport(
            updated=False, old_version=old_commit[:12] if old_commit else None,
            new_version=fetched.commit[:12], datasets=pipeline._datasets(database),
            source_verification=run.source_verification,
            upstream_version_evidence=evidence,
        )
    prepare_candidate_workspace(run, ports)
    try:
        build_candidate_components(run, ports)
        verify_candidate_components(run, ports)
        optimize_candidate_physical_layout(run, ports)
        result = publish_candidate_artifacts(run, ports)
        os.replace(destination, database)
    finally:
        discard_unpublished_database(run.temporary)
        destination.unlink(missing_ok=True)
        run.destination_receipt.unlink(missing_ok=True)
    equipment = run.component_reports.get("equipment", {})
    curated = run.component_reports.get("curated")
    return UpdateReport(
        updated=True, old_version=old_commit[:12] if old_commit else None,
        new_version=fetched.commit[:12], stats=result.stats,
        warnings=list(curated.warnings) if curated is not None else [],
        action="optimized" if result.plan["mode"] == "physical_optimization" else "rebuilt",
        datasets=result.datasets,
        equipment_warnings=equipment.get("warnings", [])[:100],
        equipment_warnings_total=len(equipment.get("warnings", [])),
        source_verification=result.source_verification,
        upstream_version_evidence=evidence,
        incremental_plan=result.plan, incremental_receipt=result.receipt,
    )
