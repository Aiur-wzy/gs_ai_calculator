"""Candidate pass ordering, complete artifact failures, and shared fixture parity."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing

import pytest

from genshin_db import pipeline
from genshin_db.release_integrity import build_release_core
from genshin_db.runtime_physical_index import (
    INDEX_NAME,
    missing_physical_indexes,
    preservation_manifest,
)
from tests import fixture_candidate_builder as fixture_builder
from tests.test_incremental_candidate import _accepted, _evidence, _write_repo
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811 - imported pytest fixtures

STAGES = (
    "admit_candidate_request", "capture_candidate_sources", "plan_candidate_changes",
    "prepare_candidate_workspace", "build_candidate_components",
    "verify_candidate_components", "optimize_candidate_physical_layout",
    "publish_candidate_artifacts",
)


def _build(root, **kw):
    repo = kw.pop("repo", None)
    if repo is None:
        repo = _write_repo(root)
    return pipeline.build_candidate_database(
        base_db=kw.pop("base_db", None), output_db=root / "candidate.db",
        repo_dir=repo, target_commit="a" * 40,
        version_evidence=_evidence(), verify_sources=False, **kw,
    )


def test_formal_candidate_calls_fixed_passes_once_in_order(tmp_path, monkeypatch):
    observed = []
    for name in STAGES:
        original = getattr(pipeline, name)
        def invoke(run, ports, *, _original=original, _name=name):
            observed.append(_name)
            return _original(run, ports)
        monkeypatch.setattr(pipeline, name, invoke)
    _build(tmp_path)
    assert observed == list(STAGES)


def test_admission_refusal_precedes_source_reads_and_leaves_files_intact(tmp_path, monkeypatch):
    output = tmp_path / "candidate.db"
    output.write_bytes(b"untouched")
    monkeypatch.setattr(pipeline, "capture_candidate_sources", lambda *_: pytest.fail("source read"))
    with pytest.raises(pipeline.PipelineError, match="已存在"):
        _build(tmp_path)
    assert output.read_bytes() == b"untouched"
    assert not list(tmp_path.glob(".*.candidate"))


@pytest.mark.parametrize("failure", ["verify", "receipt"])
def test_no_publishable_orphan_survives_a_pass_failure(tmp_path, monkeypatch, failure):
    def fail(*_a, **_kw):
        raise RuntimeError("controlled-stage-failure")
    monkeypatch.setattr(
        pipeline, "_validate" if failure == "verify" else "_write_json_atomic", fail,
    )
    with pytest.raises(RuntimeError, match="controlled-stage-failure"):
        _build(tmp_path)
    assert not (tmp_path / "candidate.db").exists()
    assert not list(tmp_path.glob("*.incremental-receipt.json"))
    assert not list(tmp_path.glob(".*.candidate"))


def test_shared_components_match_formal_full_candidate(tmp_path, stub_fetcher, fake_repo):
    fixture_dir = tmp_path / "isolated-fixture"
    fixture_builder.build_database(fixture_dir)
    result = _build(tmp_path, repo=fake_repo)
    fixture = fixture_dir / "genshin.db"
    assert build_release_core(fixture) == build_release_core(tmp_path / "candidate.db")
    assert pipeline._datasets(fixture) == result.datasets


def test_physical_optimization_is_a_new_complete_candidate_and_keeps_base_readonly(tmp_path, monkeypatch):
    repo = _write_repo(tmp_path)
    base = tmp_path / "base.db"
    pipeline.build_candidate_database(
        base_db=None, output_db=base, repo_dir=repo,
        target_commit="a" * 40, version_evidence=_evidence(), verify_sources=False,
    )
    with closing(sqlite3.connect(base)) as connection, connection:
        connection.execute(f'DROP INDEX "{INDEX_NAME}"')
        before_content = preservation_manifest(connection)
    accepted = _accepted(base)
    before_bytes = hashlib.sha256(base.read_bytes()).hexdigest()
    before_stat = base.stat()
    for name in ("build_curated_layer", "build_runtime_layer", "build_equipment_layer"):
        monkeypatch.setattr(pipeline, name, lambda *_a, **_kw: pytest.fail("must reuse components"))
    monkeypatch.setattr(pipeline.raw_store, "build_raw_layer", lambda *_a, **_kw: pytest.fail("must reuse raw"))
    result = _build(tmp_path, base_db=base, repo=repo, **accepted)
    assert result.plan["contract"] == "genshin-database-incremental-plan-v2"
    assert result.plan["schema_version"] == 2
    assert result.plan["mode"] == "physical_optimization"
    assert result.plan["rebuilt_components"] == []
    assert result.receipt["physical_maintenance_required"] is True
    assert result.stats["physical_indexes_created"] == 1
    assert hashlib.sha256(base.read_bytes()).hexdigest() == before_bytes
    assert base.stat().st_mtime_ns == before_stat.st_mtime_ns
    with closing(sqlite3.connect(base)) as original, closing(sqlite3.connect(tmp_path / "candidate.db")) as candidate:
        assert missing_physical_indexes(original) == (INDEX_NAME,)
        assert missing_physical_indexes(candidate) == ()
        assert preservation_manifest(candidate) == before_content


def test_retired_entrypoint_never_fetches_or_builds_even_marker_free(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.fetcher, "ensure_upstream", lambda *_: pytest.fail("retired fetch"))
    with pytest.raises(pipeline.PipelineError, match="已退役"):
        pipeline.run_update(tmp_path)
    assert not list(tmp_path.iterdir())
