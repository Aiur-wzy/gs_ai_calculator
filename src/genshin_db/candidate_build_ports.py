"""Named late-bound collaborators shared by formal and fixture components."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CandidateBuildPorts:
    PipelineError: Callable[..., Any]
    CandidateNoOpError: Callable[..., Any]
    CandidateBuildReport: Callable[..., Any]
    validate_accepted_base: Callable[..., Any]
    require_version_evidence: Callable[..., Any]
    source_manifest: Callable[..., Any]
    selected_source_manifest: Callable[..., Any]
    candidate_metadata_changed: Callable[..., Any]
    candidate_build_contract: Callable[..., Any]
    sqlite_backup_readonly: Callable[..., Any]
    build_curated_layer: Callable[..., Any]
    apply_labels: Callable[..., Any]
    build_equipment_layer: Callable[..., Any]
    build_runtime_layer: Callable[..., Any]
    evidence_json: Callable[..., Any]
    validate: Callable[..., Any]
    validate_equipment: Callable[..., Any]
    validate_runtime: Callable[..., Any]
    validate_semantic_component: Callable[..., Any]
    write_json_atomic: Callable[..., Any]
    datasets: Callable[..., Any]
    ensure_physical_indexes: Callable[..., Any]
    missing_physical_indexes: Callable[..., Any]
    preservation_manifest: Callable[..., Any]
