"""Per-build request and artifacts; never an installed database owner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from os import stat_result
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genshin_db.raw_store import RawStats


@dataclass(slots=True, kw_only=True)
class CandidateBuildContext:
    base_db: Path | None
    output_db: Path
    repo_dir: Path
    target_commit: str
    version_evidence: dict[str, Any] | None = None
    force_full: bool = False
    verify_sources: bool = True
    receipt_path: Path | None = None
    content_change_set: dict[str, Any] | None = None
    content_approval: dict[str, Any] | None = None
    base_database_release_id: str | None = None
    target_database_release_id: str | None = None
    content_receipt_path: Path | None = None
    base_release_manifest: Mapping[str, Any] | Path | None = None
    base_live_receipt: Mapping[str, Any] | Path | None = None
    base: Path | None = None
    before_core: str | None = None
    before_stat: stat_result | None = None
    build_contract: dict[str, Any] | None = None
    candidate_core: str | None = None
    checked_base_manifest: dict[str, Any] | None = None
    content_application_receipt: dict[str, Any] | None = None
    content_requested: bool = False
    content_timestamp: str | None = None
    destination_content_receipt: Path | None = None
    destination_receipt: Path | None = None
    game_version: str | None = None
    metadata_changed: bool = False
    physical_maintenance_required: bool = False
    output: Path | None = None
    plan: dict[str, Any] | None = None
    raw_manifest: dict[str, Any] | None = None
    raw_stats: RawStats | None = None
    rebuilt: set[str] = field(default_factory=set)
    receipt: dict[str, Any] | None = None
    repo: Path | None = None
    runtime_manifest: dict[str, Any] | None = None
    selected_manifest: dict[str, Any] | None = None
    source_verification: dict[str, Any] | None = None
    stats: dict[str, Any] = field(default_factory=dict)
    component_reports: dict[str, Any] = field(default_factory=dict)
    temporary: Path | None = None
