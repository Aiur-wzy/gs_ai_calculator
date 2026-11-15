"""Indexer upgrade through the real fixed-commit, offline B pipeline."""
import json
import sqlite3
import subprocess
from contextlib import closing

import pytest

from genshin_app.cli import main
from genshin_db import fetcher, pipeline, runtime_catalog, runtime_store
from genshin_db.annotation_store import Annotation, add_annotation
from genshin_db.extractor import build_curated_layer
from genshin_db.runtime_queries import get_runtime_status, query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo  # noqa: F401

# ruff: noqa: F811
SOURCE = "ExcelBinOutput/AttackAttenuationExcelConfigData.json"


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          encoding="utf8").stdout.strip()


@pytest.fixture
def pinned_repo(fake_repo):
    (fake_repo / SOURCE).write_text(json.dumps([
        {"group": "default", "resetCycle": 2.5, "durabilitySequence": [1, 0, 0]}]), encoding="utf8")
    (fake_repo / ".gitignore").write_text("BinOutput/Ability/Temp/Ignored.json\n", encoding="utf8")
    git(fake_repo, "init")
    git(fake_repo, "config", "core.autocrlf", "false")
    git(fake_repo, "add", "--all")
    git(fake_repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", "OSCBWin7.0.54 fixture")
    return fake_repo, git(fake_repo, "rev-parse", "HEAD")


def update(folder, source, **kw):
    repo, commit = source
    return fixture_builder.build_database(folder, offline_repo=repo, expected_commit=commit, **kw)


def beta_rows(db):
    with closing(sqlite3.connect(db)) as conn:
        result = {}
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if "version_line" in {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}:
                result[table] = sorted(conn.execute(f'SELECT * FROM "{table}" WHERE version_line=?', ("beta",)).fetchall(), key=repr)
        return result


def test_offline_same_commit_upgrade_keeps_seventeen_manual_rows_and_beta(tmp_path, pinned_repo, monkeypatch):
    folder = tmp_path / "stage"
    with monkeypatch.context() as patch:
        patch.setattr(runtime_catalog, "INDEXER_VERSION", "3")
        patch.setattr(runtime_store, "INDEXER_VERSION", "3")
        update(folder, pinned_repo)
    db = folder / "genshin.db"
    original = query_runtime(db, {"action": "attenuation_profile", "group": "default"})
    for i in range(17):
        add_annotation(db, Annotation(target_type="character", target_id=90000001,
                                      content=f"人工保留{i}", status="approved"))
    build_curated_layer(db, "beta-fixture", "beta")
    before_beta = beta_rows(db)
    with closing(sqlite3.connect(db)) as conn:
        manual = conn.execute("SELECT * FROM annotation ORDER BY id").fetchall()
    monkeypatch.setattr(fetcher, "ensure_upstream", lambda *_: pytest.fail("offline must never fetch"))
    report = update(folder, pinned_repo)
    assert report.updated and report.old_version == report.new_version
    assert report.orphan_annotations == 0
    assert beta_rows(db) == before_beta
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT * FROM annotation ORDER BY id").fetchall() == manual
        assert runtime_store.validate_runtime(conn, "release", deep=True)["indexer_version"] == runtime_store.INDEXER_VERSION
    current = query_runtime(db, {"action": "attenuation_profile", "group": "default"})
    assert current["snapshot"]["snapshot_id"] != original["snapshot"]["snapshot_id"]
    assert current["source"]["node_sha256"] == original["source"]["node_sha256"]
    assert update(folder, pinned_repo).updated is False


def test_deprecated_cli_cannot_replace_internal_candidate_pipeline(tmp_path, pinned_repo, capsys):
    repo, commit = pinned_repo
    folder = tmp_path / "cli"
    args = ["--data-dir", str(folder), "db", "update", "--offline-repo", str(repo), "--expected-commit", commit]
    assert main(args) == 1
    rejected = json.loads(capsys.readouterr().err)
    assert rejected["reason"] == "accepted_database_is_immutable"
    assert not (folder / "genshin.db").exists()

    first = update(folder, pinned_repo)
    second = update(folder, pinned_repo)
    assert first.updated is True and second.updated is False
    assert git(repo, "rev-parse", "HEAD") == commit
    assert not git(repo, "status", "--porcelain", "--untracked-files=all")


@pytest.mark.parametrize("options", [{"offline_repo": "."}, {"expected_commit": "a" * 40}])
def test_offline_pair_required_before_write(tmp_path, options):
    with pytest.raises(ValueError, match="同时提供"):
        fixture_builder.build_database(tmp_path / "absent", **options)
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("commit", ["HEAD", "a" * 39, "a" * 41, "g" * 40, "a" * 40 + ";bad"])
def test_pinned_commit_requires_exact_sha(pinned_repo, commit):
    with pytest.raises(ValueError, match="40位"):
        fetcher.inspect_pinned_upstream(pinned_repo[0], commit)


def test_wrong_head_and_subdirectory_rejected(pinned_repo):
    repo, commit = pinned_repo
    with pytest.raises(fetcher.FetcherError, match="HEAD"):
        fetcher.inspect_pinned_upstream(repo, "b" * 40)
    with pytest.raises(fetcher.FetcherError, match="根目录"):
        fetcher.inspect_pinned_upstream(repo / "ExcelBinOutput", commit)


@pytest.mark.parametrize("relative", [SOURCE, "unexpected.txt"])
def test_dirty_worktree_rejected(tmp_path, pinned_repo, relative):
    repo, _ = pinned_repo
    (repo / relative).write_text("changed", encoding="utf8")
    with pytest.raises(fetcher.FetcherError, match="工作树"):
        update(tmp_path / "stage", pinned_repo)
    assert not (tmp_path / "stage/genshin.db").exists()


def test_ignored_selected_source_cannot_enter_pinned_build(tmp_path, pinned_repo):
    repo, _ = pinned_repo
    (repo / "BinOutput/Ability/Temp/Ignored.json").write_text('[{"Default":{"abilityName":"Ignored"}}]', encoding="utf8")
    assert not git(repo, "status", "--porcelain", "--untracked-files=all")
    with pytest.raises(fetcher.FetcherError, match="不是固定commit"):
        update(tmp_path / "stage", pinned_repo)
    assert not (tmp_path / "stage/genshin.db").exists()


def test_skip_worktree_changed_bytes_cannot_pass_git_clean(tmp_path, pinned_repo):
    repo, _ = pinned_repo
    git(repo, "update-index", "--skip-worktree", SOURCE)
    (repo / SOURCE).write_text("[]", encoding="utf8")
    assert not git(repo, "status", "--porcelain", "--untracked-files=all")
    with pytest.raises(fetcher.FetcherError, match="字节与固定commit不符"):
        update(tmp_path / "stage", pinned_repo)
    assert not (tmp_path / "stage/genshin.db").exists()


@pytest.mark.parametrize("semantic_change", [False, True])
def test_crlf_equivalence_does_not_allow_other_content_changes(tmp_path, pinned_repo, semantic_change):
    repo, commit = pinned_repo
    original = (repo / SOURCE).read_bytes()
    # Ensure this source has a line ending committed as LF.
    (repo / SOURCE).write_bytes(original + b"\n")
    git(repo, "add", SOURCE)
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-m", "OSCBWin7.0.54 LF source")
    commit = git(repo, "rev-parse", "HEAD")
    git(repo, "config", "core.autocrlf", "true")
    git(repo, "update-index", "--skip-worktree", SOURCE)
    checkout = (original + b"\n").replace(b"\n", b"\r\n")
    if semantic_change:
        checkout = checkout.replace(b'"resetCycle": 2.5', b'"resetCycle": 3.5')
    (repo / SOURCE).write_bytes(checkout)
    assert not git(repo, "status", "--porcelain", "--untracked-files=all")
    if semantic_change:
        with pytest.raises(fetcher.FetcherError, match="字节与固定commit不符"):
            update(tmp_path / "stage", (repo, commit))
    else:
        import hashlib
        report = update(tmp_path / "stage", (repo, commit))
        assert report.source_verification["core_autocrlf"] == "true"
        assert report.source_verification["crlf_equivalent_files"] == 1
        result = query_runtime(tmp_path / "stage/genshin.db", {"action": "attenuation_profile", "group": "default"})
        assert result["source"]["source_sha256"] == hashlib.sha256(checkout).hexdigest()


def test_source_change_during_build_does_not_replace_previous_database(tmp_path, pinned_repo, monkeypatch):
    folder = tmp_path / "stage"
    update(folder, pinned_repo)
    db = folder / "genshin.db"
    original = get_runtime_status(db)
    validate = pipeline._validate

    def mutate_after_validation(path):
        validate(path)
        (pinned_repo[0] / "BinOutput/Ability/Temp/Ignored.json").write_text(
            '[{"Default":{"abilityName":"Ignored"}}]', encoding="utf8")

    monkeypatch.setattr(pipeline, "_validate", mutate_after_validation)
    with pytest.raises(pipeline.PipelineError, match="runtime来源改变"):
        update(folder, pinned_repo, force=True)
    assert get_runtime_status(db) == original
    assert not list((folder / "backups").glob("*.db"))
