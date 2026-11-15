"""Component-level candidate planning/building never mutates the accepted base."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from genshin_db import content_change_set, pipeline, upstream_version
from genshin_db.incremental_update import build_incremental_plan
from genshin_db.release_profile import (
    create_database_live_receipt,
    create_database_release_manifest,
    read_database_identity,
)
from genshin_db.runtime_catalog import enumerate_runtime_sources
from tests.test_pipeline import MINIMAL_FILES


def _write_repo(root: Path) -> Path:
    repo = root / "repo"
    for relative, value in copy.deepcopy(MINIMAL_FILES).items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return repo


def _evidence(commit: str = "a" * 40) -> dict:
    value = {
        "resolver_version": 1,
        "status": "resolved",
        "version_evidence_ready": True,
        "snapshot_commit": commit,
        "snapshot_updated_at": "2026-10-26T00:00:00+00:00",
        "track": "OSCBWin",
        "version": "7.1.51",
        "raw_version": "OSCBWin7.1.51",
        "evidence_kind": "snapshot_commit_subject",
        "evidence_commit": commit,
        "review_id": None,
        "configuration_trees": {
            "BinOutput": "1" * 40,
            "ExcelBinOutput": "2" * 40,
        },
        "source_trees": {
            "BinOutput": "1" * 40,
            "ExcelBinOutput": "2" * 40,
            "TextMap": "3" * 40,
        },
        "readme_version": "OSCBWin7.1.51",
        "issues": [],
    }
    value["evidence_fingerprint"] = upstream_version.evidence_fingerprint(value)
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _accepted(database: Path, release_id: str = "db-7.1.51-r1") -> dict:
    manifest = create_database_release_manifest(
        database,
        database_release_id=release_id,
        release_sequence=1,
        channel="dev",
        release_pair_id="pair-base",
        app_release_id="app-base",
        logic_profile="engine-v2",
        data_contract_version=1,
    )
    receipt = create_database_live_receipt(
        manifest,
        read_database_identity(database),
        installed_at="2026-10-28T00:00:00Z",
    )
    return {
        "base_release_manifest": manifest,
        "base_live_receipt": receipt,
    }


def _contract(commit: str = "a" * 40) -> dict:
    return pipeline._candidate_build_contract(commit)


def test_fresh_and_noop_candidates_keep_base_byte_for_byte(tmp_path):
    repo = _write_repo(tmp_path)
    first = tmp_path / "candidate-1.db"
    fresh = pipeline.build_candidate_database(
        base_db=None,
        output_db=first,
        repo_dir=repo,
        target_commit="a" * 40,
        version_evidence=_evidence(),
        verify_sources=False,
    )
    assert fresh.plan["mode"] == "full_rebuild"
    assert "base_database_missing" in fresh.plan["fallback_reasons"]

    before_sha = _sha(first)
    before_mtime = first.stat().st_mtime_ns
    second = tmp_path / "candidate-2.db"
    with pytest.raises(pipeline.CandidateNoOpError) as stopped:
        pipeline.build_candidate_database(
            base_db=first,
            output_db=second,
            repo_dir=repo,
            target_commit="a" * 40,
            version_evidence=_evidence(),
            verify_sources=False,
            **_accepted(first),
        )

    assert stopped.value.plan["mode"] == "no_op"
    assert stopped.value.plan["rebuilt_components"] == []
    assert not second.exists()
    assert _sha(first) == before_sha
    assert first.stat().st_mtime_ns == before_mtime


def test_runtime_only_change_reuses_raw_and_rebuilds_only_runtime(tmp_path):
    repo = _write_repo(tmp_path)
    base = tmp_path / "base.db"
    pipeline.build_candidate_database(
        base_db=None,
        output_db=base,
        repo_dir=repo,
        target_commit="a" * 40,
        version_evidence=_evidence(),
        verify_sources=False,
    )
    before = _sha(base)
    talent = repo / "BinOutput/Talent/EquipTalents/Fixture.json"
    value = json.loads(talent.read_text(encoding="utf-8"))
    value["Fixture_Additional"] = []
    talent.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    result = pipeline.build_candidate_database(
        base_db=base,
        output_db=tmp_path / "runtime-only.db",
        repo_dir=repo,
        target_commit="a" * 40,
        version_evidence=_evidence(),
        verify_sources=False,
        **_accepted(base),
    )

    assert result.plan["mode"] == "component_rebuild"
    assert result.plan["rebuilt_components"] == ["runtime_config"]
    assert result.plan["reused_components"] == ["curated", "equipment"]
    assert result.receipt["raw_reused_files"] == len(pipeline.raw_store.RAW_SOURCES)
    assert result.receipt["raw_rebuilt_files"] == 0
    assert _sha(base) == before


def test_high_fanout_and_new_snapshot_expand_to_full_rebuild(tmp_path):
    repo = _write_repo(tmp_path)
    base = tmp_path / "base.db"
    pipeline.build_candidate_database(
        base_db=None,
        output_db=base,
        repo_dir=repo,
        target_commit="a" * 40,
        version_evidence=_evidence(),
        verify_sources=False,
    )
    text_map = repo / "TextMap/TextMap_MediumCHS.json"
    value = json.loads(text_map.read_text(encoding="utf-8"))
    value["999"] = "新增"
    text_map.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    plan = build_incremental_plan(
        base_db=base,
        base_database_release_id="db-7.1.51-r1",
        target_commit="a" * 40,
        raw_manifest=pipeline._source_manifest(repo),
        runtime_manifest=enumerate_runtime_sources(repo),
        build_contract=_contract(),
    )
    assert plan["mode"] == "full_rebuild"
    assert "high_fanout_source_changed" in plan["fallback_reasons"]
    assert plan["rebuilt_components"] == ["curated", "equipment", "runtime_config"]

    snapshot = build_incremental_plan(
        base_db=base,
        base_database_release_id="db-7.1.51-r1",
        target_commit="b" * 40,
        raw_manifest=pipeline._source_manifest(repo),
        runtime_manifest=enumerate_runtime_sources(repo),
        build_contract=_contract("b" * 40),
    )
    assert snapshot["mode"] == "full_rebuild"
    assert "accepted_snapshot_provenance_rebind_required" in snapshot["fallback_reasons"]


def test_plan_detects_add_modify_and_delete_deterministically(tmp_path):
    repo = _write_repo(tmp_path)
    base = tmp_path / "base.db"
    pipeline.build_candidate_database(
        base_db=None,
        output_db=base,
        repo_dir=repo,
        target_commit="a" * 40,
        version_evidence=_evidence(),
        verify_sources=False,
    )
    modified = repo / "BinOutput/Talent/EquipTalents/Fixture.json"
    modified.write_text('{"Changed": []}', encoding="utf-8")
    deleted = repo / "BinOutput/Avatar/Fixture.json"
    (repo / "BinOutput/Avatar/Keep.json").write_text("{}", encoding="utf-8")
    deleted.unlink()
    added = repo / "BinOutput/Ability/Temp/EquipAbilities/Added.json"
    added.write_text("[]", encoding="utf-8")

    plan = build_incremental_plan(
        base_db=base,
        base_database_release_id="db-7.1.51-r1",
        target_commit="a" * 40,
        raw_manifest=pipeline._source_manifest(repo),
        runtime_manifest=enumerate_runtime_sources(repo),
        build_contract=_contract(),
    )
    kinds = {(row["path"], row["change"]) for row in plan["changed_sources"]}
    assert ("BinOutput/Talent/EquipTalents/Fixture.json", "modified") in kinds
    assert ("BinOutput/Avatar/Fixture.json", "deleted") in kinds
    assert ("BinOutput/Avatar/Keep.json", "added") in kinds
    assert ("BinOutput/Ability/Temp/EquipAbilities/Added.json", "added") in kinds
    # Replanning identical evidence yields an identical approval identity.
    repeated = build_incremental_plan(
        base_db=base,
        base_database_release_id="db-7.1.51-r1",
        target_commit="a" * 40,
        raw_manifest=pipeline._source_manifest(repo),
        runtime_manifest=enumerate_runtime_sources(repo),
        build_contract=_contract(),
    )
    assert repeated == plan


def test_build_contract_change_forces_safe_rebuild(tmp_path):
    repo = _write_repo(tmp_path)
    base = tmp_path / "base.db"
    pipeline.build_candidate_database(
        base_db=None,
        output_db=base,
        repo_dir=repo,
        target_commit="a" * 40,
        version_evidence=_evidence(),
        verify_sources=False,
    )
    changed_contract = _contract()
    changed_contract["dependency_map_version"] += 1
    plan = build_incremental_plan(
        base_db=base,
        base_database_release_id="db-7.1.51-r1",
        target_commit="a" * 40,
        raw_manifest=pipeline._source_manifest(repo),
        runtime_manifest=enumerate_runtime_sources(repo),
        build_contract=changed_contract,
    )
    assert plan["mode"] == "full_rebuild"
    assert "build_contract_changed" in plan["fallback_reasons"]


def test_candidate_requires_real_accepted_base_identity(tmp_path):
    repo = _write_repo(tmp_path)
    base = tmp_path / "base.db"
    pipeline.build_candidate_database(
        base_db=None,
        output_db=base,
        repo_dir=repo,
        target_commit="a" * 40,
        version_evidence=_evidence(),
        verify_sources=False,
    )
    changed = repo / "BinOutput/Talent/EquipTalents/Fixture.json"
    changed.write_text('{"Changed": []}', encoding="utf-8")
    with pytest.raises(pipeline.PipelineError, match="accepted manifest"):
        pipeline.build_candidate_database(
            base_db=base,
            output_db=tmp_path / "missing-identity.db",
            repo_dir=repo,
            target_commit="a" * 40,
            version_evidence=_evidence(),
            verify_sources=False,
        )
    accepted = _accepted(base)
    accepted["base_live_receipt"] = {
        **accepted["base_live_receipt"],
        "package_sha256": "0" * 64,
    }
    with pytest.raises(pipeline.PipelineError, match="manifest/receipt"):
        pipeline.build_candidate_database(
            base_db=base,
            output_db=tmp_path / "wrong-identity.db",
            repo_dir=repo,
            target_commit="a" * 40,
            version_evidence=_evidence(),
            verify_sources=False,
            **accepted,
        )


def test_reviewed_project_content_is_applied_inside_candidate_and_receipted(tmp_path):
    repo = _write_repo(tmp_path)
    base = tmp_path / "base.db"
    pipeline.build_candidate_database(
        base_db=None,
        output_db=base,
        repo_dir=repo,
        target_commit="a" * 40,
        version_evidence=_evidence(),
        verify_sources=False,
    )
    row = {
        "id": 9001,
        "target_type": "character",
        "target_id": 10000002,
        "version_line": "release",
        "category": "mechanic",
        "title": "受审动作说明",
        "content": "由Agent整理、用户批准后随完整数据库版本发布。",
        "status": "approved",
        "created_at": "2026-10-28T00:00:00Z",
        "updated_at": "2026-10-28T00:00:00Z",
        "author": "agent",
        "reviewer": "user",
        "reviewed_at": "2026-10-28T00:00:00Z",
        "game_version_range": "7.1.53",
    }
    change = content_change_set.create_change_set(
        base_database_release_id="db-7.1.51-r1",
        target_database_release_id="db-7.1.51-r2",
        base_project_content_fingerprint=(
            content_change_set.project_content_fingerprint(base)
        ),
        sources=[{
            "source_id": "appendix-ayaka-1",
            "kind": "appendix",
            "locator": "skill/appendix/ayaka.md",
            "evidence_sha256": "f" * 64,
            "summary": "角色动作资料",
        }],
        operations=[{
            "operation_id": "add-ayaka-note",
            "source_id": "appendix-ayaka-1",
            "table": "annotation",
            "action": "add",
            "key": {"id": 9001},
            "before_sha256": None,
            "row": row,
        }],
        impact={"components": ["mechanic_semantics"], "summary": "新增角色动作说明"},
    )
    approval = content_change_set.create_approval(
        change,
        approved_by="user-explicit-confirmation",
        approved_at="2026-10-28T00:01:00Z",
    )
    same_receipt = tmp_path / "same-receipt.json"
    with pytest.raises(pipeline.PipelineError, match="不同路径"):
        pipeline.build_candidate_database(
            base_db=base,
            output_db=tmp_path / "bad-candidate.db",
            repo_dir=repo,
            target_commit="a" * 40,
            version_evidence=_evidence(),
            verify_sources=False,
            content_change_set=change,
            content_approval=approval,
            base_database_release_id="db-7.1.51-r1",
            target_database_release_id="db-7.1.51-r2",
            receipt_path=same_receipt,
            content_receipt_path=same_receipt,
            **_accepted(base, "db-7.1.51-r1"),
        )
    candidate = tmp_path / "candidate.db"
    result = pipeline.build_candidate_database(
        base_db=base,
        output_db=candidate,
        repo_dir=repo,
        target_commit="a" * 40,
        version_evidence=_evidence(),
        verify_sources=False,
        content_change_set=change,
        content_approval=approval,
        base_database_release_id="db-7.1.51-r1",
        target_database_release_id="db-7.1.51-r2",
        **_accepted(base, "db-7.1.51-r1"),
    )
    assert result.content_application_receipt["operation_counts"] == {
        "add": 1,
        "update": 0,
        "delete": 0,
    }
    assert Path(result.content_receipt_path).is_file()
    assert result.content_application_receipt[
        "after_project_content_fingerprint"
    ] == content_change_set.project_content_fingerprint(candidate)
    with sqlite3.connect(candidate) as connection:
        assert connection.execute(
            "SELECT content FROM annotation WHERE id=9001"
        ).fetchone()[0] == row["content"]
