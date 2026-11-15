from __future__ import annotations

import copy
import subprocess
from pathlib import Path

import pytest

from genshin_db import upstream_version


def _git(repo: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return process.stdout.strip()


def _repo(tmp_path: Path, subject: str, readme_version: str = "OSCBWin7.1.51") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    for root in upstream_version.SOURCE_TREE_ROOTS:
        path = repo / root / "fixture.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    (repo / "README.md").write_text(
        f"Current version: {readme_version}\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", subject)
    return repo


@pytest.mark.parametrize("track", ["CNCBWin", "CNRELWin"])
def test_regional_version_keeps_upstream_track_and_same_snapshot_proof(tmp_path, track):
    raw = track + "7.2.50"
    repo = _repo(tmp_path, "'" + raw + "_R49211690_D49247333_S49223112'", raw)
    result = upstream_version.inspect(repo)
    assert result["version"] == "7.2.50"
    assert result["track"] == track
    assert result["raw_version"] == raw
    assert result["evidence_kind"] == "snapshot_commit_subject"
    assert upstream_version.validate_resolved_evidence(result) == result


def test_direct_commit_preserves_full_version_and_track(tmp_path):
    repo = _repo(tmp_path, "OSCBWin7.1.51_R1_S1_D1")
    result = upstream_version.inspect(repo)
    assert result["status"] == "resolved"
    assert result["version_evidence_ready"] is True
    assert result["version"] == "7.1.51"
    assert result["track"] == "OSCBWin"
    assert result["snapshot_updated_at"]
    assert result["evidence_kind"] == "snapshot_commit_subject"
    assert len(result["evidence_fingerprint"]) == 64
    assert upstream_version.validate_resolved_evidence(result) == result


@pytest.mark.parametrize(
    "mutation",
    [
        "track",
        "raw_version",
        "snapshot_commit",
        "missing_source_tree",
        "invalid_tree_oid",
        "contradictory_configuration_tree",
        "subject_evidence_commit",
    ],
)
def test_semantically_invalid_evidence_is_rejected_even_with_fresh_fingerprint(
    tmp_path,
    mutation,
):
    evidence = copy.deepcopy(
        upstream_version.inspect(_repo(tmp_path, "OSCBWin7.1.51_R1_S1_D1"))
    )
    if mutation == "track":
        evidence["track"] = "release"
        evidence["raw_version"] = "release7.1.51"
    elif mutation == "raw_version":
        evidence["raw_version"] = "OSCBWin7.1.52"
    elif mutation == "snapshot_commit":
        evidence["snapshot_commit"] = "not-a-commit"
        evidence["evidence_commit"] = "not-a-commit"
    elif mutation == "missing_source_tree":
        evidence["source_trees"].pop("TextMap")
    elif mutation == "invalid_tree_oid":
        evidence["source_trees"]["TextMap"] = "not-an-oid"
    elif mutation == "contradictory_configuration_tree":
        evidence["configuration_trees"]["BinOutput"] = "f" * 40
    elif mutation == "subject_evidence_commit":
        evidence["evidence_commit"] = "f" * 40
    evidence["evidence_fingerprint"] = upstream_version.evidence_fingerprint(
        evidence
    )

    with pytest.raises(ValueError):
        upstream_version.validate_resolved_evidence(evidence)


def test_text_only_head_inherits_tree_equivalent_version_commit(tmp_path):
    repo = _repo(tmp_path, "OSRELWin7.0.0_R1_S1_D1", "OSRELWin7.0.0")
    version_commit = _git(repo, "rev-parse", "HEAD")
    (repo / "TextMap" / "fixture.json").write_text('{"1":"new"}', encoding="utf-8")
    _git(repo, "add", "TextMap/fixture.json")
    _git(repo, "commit", "-m", "TextMap")

    result = upstream_version.inspect(repo)
    assert result["version_evidence_ready"] is True
    assert result["version"] == "7.0.0"
    assert result["track"] == "OSRELWin"
    assert result["evidence_kind"] == "source_tree_equivalent_ancestor"
    assert result["evidence_commit"] == version_commit


def test_unreviewed_readme_conflict_requires_review(tmp_path):
    repo = _repo(tmp_path, "OSCBWin7.1.51_R1", "OSCBWin6.7.54")
    result = upstream_version.inspect(repo)
    assert result["status"] == "review_required"
    assert result["version_evidence_ready"] is False
    assert result["version"] == "7.1.51"
    assert result["issues"] == ["readme_version_conflict"]


def test_readme_alone_is_not_primary_version_evidence(tmp_path):
    repo = _repo(tmp_path, "TextMap", "OSCBWin7.1.51")
    result = upstream_version.inspect(repo)
    assert result["status"] == "unresolved"
    assert result["version_evidence_ready"] is False
    assert result["version"] is None
    assert "readme_is_auxiliary_only" in result["issues"]


def test_reviewed_exact_snapshot_requires_matching_configuration_trees(tmp_path, monkeypatch):
    repo = _repo(tmp_path, "TextMap", "OSCBWin6.7.54")
    commit = _git(repo, "rev-parse", "HEAD")
    trees = {
        root: _git(repo, "rev-parse", f"HEAD:{root}")
        for root in upstream_version.CONFIGURATION_ROOTS
    }
    monkeypatch.setitem(
        upstream_version._REVIEWED_SNAPSHOTS,
        commit,
        {
            "review_id": "fixture-review-v1",
            "track": "OSCBWin",
            "version": "7.1.51",
            "version_commit": "b" * 40,
            "trees": trees,
            "allowed_auxiliary_versions": ["OSCBWin6.7.54"],
        },
    )
    result = upstream_version.inspect(repo, commit)
    assert result["version_evidence_ready"] is True
    assert result["version"] == "7.1.51"
    assert result["review_id"] == "fixture-review-v1"
    assert result["issues"] == ["reviewed_stale_readme"]


def test_non_git_directory_returns_structured_unresolved_result(tmp_path):
    result = upstream_version.inspect(tmp_path, "a" * 40)
    assert result["status"] == "unresolved"
    assert result["issues"] == ["git_metadata_unavailable"]
    assert result["snapshot_commit"] == "a" * 40


def test_missing_consumed_source_tree_is_structured_and_not_version_evidence_ready(tmp_path):
    repo = _repo(tmp_path, "OSCBWin7.1.51_R1")
    (repo / "TextMap" / "fixture.json").unlink()
    (repo / "TextMap").rmdir()
    (repo / "README.md").write_text(
        "Current version: OSCBWin7.1.52\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "OSCBWin7.1.52_R2")

    result = upstream_version.inspect(repo)
    assert result["status"] == "review_required"
    assert result["version_evidence_ready"] is False
    assert result["version"] == "7.1.52"
    assert result["source_trees"] == result["configuration_trees"]
    assert result["issues"] == ["source_tree_missing:TextMap"]
