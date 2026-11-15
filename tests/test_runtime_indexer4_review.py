"""Independent compatibility and exact-identity checks for indexer 4."""
from __future__ import annotations

import copy
import json
import sqlite3
import subprocess
from contextlib import closing

import pytest

from genshin_db import fetcher, pipeline, queries, runtime_catalog, runtime_store
from genshin_db.runtime_catalog import RuntimeConfigError, dumps, extract_definitions
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811

SOURCE = "ExcelBinOutput/AttackAttenuationExcelConfigData.json"
ROWS = [
    {"group": "case", "resetCycle": 2.5, "durabilitySequence": [1, 0]},
    {"group": "Case", "resetCycle": 1, "durabilitySequence": [1]},
    {"group": "x' OR 1=1 --", "resetCycle": 0.5, "durabilitySequence": [1, 1]},
]


def _write(repo, rows):
    (repo / SOURCE).write_text(json.dumps(rows), encoding="utf8")


def _query(db, group="case"):
    return query_runtime(db, {"action": "attenuation_profile", "group": group})


@pytest.mark.parametrize("version", ["2", "3"])
def test_legacy_key_precedence_is_preserved_exactly(version):
    rows = [dict(ROWS[0], attenuationGroup="historical"), dict(ROWS[1], id=99), ROWS[2]]
    result = extract_definitions(SOURCE, "excel", rows, "snapshot", indexer_version=version)
    assert [json.loads(row["exact_key_json"]) for row in result] == [
        {"attenuationGroup": "historical"}, {"id": 99}, {"source_index": 2}]
    current = extract_definitions(SOURCE, "excel", rows, "snapshot", indexer_version="4")
    assert [json.loads(row["exact_key_json"]) for row in current] == [
        {"group": row["group"]} for row in rows]
    assert [row["node_sha256"] for row in result] == [row["node_sha256"] for row in current]


@pytest.mark.parametrize("root", [{}, {"group": "case"}, {"0": ROWS[0]}])
def test_fixed_attenuation_source_rejects_non_array_roots(root):
    with pytest.raises(RuntimeConfigError):
        extract_definitions(SOURCE, "excel", root, "snapshot", indexer_version="4")


@pytest.mark.parametrize("version", ["1", "8", 4, True])
def test_unknown_version_cannot_choose_a_default_interpretation(version):
    with pytest.raises(RuntimeConfigError) as exc:
        extract_definitions(SOURCE, "excel", ROWS, "snapshot", indexer_version=version)
    assert exc.value.reason == "unsupported_schema"


@pytest.mark.parametrize("old_version", ["2", "3"])
def test_old_deep_then_same_commit_upgrade_once(tmp_path, fake_repo, stub_fetcher, monkeypatch, old_version):
    # Construct the historical fixture using both historical writer dispatches,
    # then use the new reader without altering its stored manifest or rows.
    _write(fake_repo, ROWS)
    with monkeypatch.context() as patch:
        patch.setattr(runtime_catalog, "INDEXER_VERSION", old_version)
        patch.setattr(runtime_store, "INDEXER_VERSION", old_version)
        fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    with closing(sqlite3.connect(db)) as conn:
        old = runtime_store.validate_runtime(conn, "release", deep=True)
        raw_hashes = conn.execute(
            "SELECT source_path,source_sha256 FROM runtime_source ORDER BY source_path").fetchall()
        node_hashes = conn.execute(
            "SELECT source_path,json_pointer,node_sha256 FROM runtime_definition "
            "ORDER BY source_path,json_pointer,definition_kind").fetchall()
    assert old["indexer_version"] == old_version
    assert _query(db)["source"]["definition"]["exact_key"] == {"source_index": 0}
    upgraded = fixture_builder.build_database(tmp_path)
    assert upgraded.updated and upgraded.action == "rebuilt"
    current = _query(db)
    assert current["snapshot"]["upstream_commit"] == old["upstream_commit"]
    assert current["snapshot"]["snapshot_id"] != old["snapshot_id"]
    assert current["source"]["definition"]["exact_key"] == {"group": "case"}
    with closing(sqlite3.connect(db)) as conn:
        runtime_store.validate_runtime(conn, "release", deep=True)
        current_hashes = dict(conn.execute(
            "SELECT source_path,source_sha256 FROM runtime_source ORDER BY source_path").fetchall())
        assert {path: current_hashes[path] for path, _ in raw_hashes} == dict(raw_hashes)
        assert set(current_hashes) - dict(raw_hashes).keys() == {
            runtime_catalog.GADGET_EXCEL_PATH, runtime_catalog.MATERIAL_EXCEL_PATH,
            runtime_catalog.GLOBAL_COMBAT_PATH}
        current_nodes = conn.execute(
            "SELECT source_path,json_pointer,node_sha256 FROM runtime_definition "
            "ORDER BY source_path,json_pointer,definition_kind").fetchall()
        assert [row for row in current_nodes if row[0] in dict(raw_hashes)] == node_hashes
        extra_nodes = [row for row in current_nodes if row[0] not in dict(raw_hashes)]
        assert len(extra_nodes) == 1
        assert extra_nodes[0][:2] == (runtime_catalog.GLOBAL_COMBAT_PATH, "")
        global_root = conn.execute(
            "SELECT definition_kind,definition_id FROM runtime_definition WHERE source_path=?",
            (runtime_catalog.GLOBAL_COMBAT_PATH,)).fetchall()
        assert len(global_root) == 1 and global_root[0][0] == "global_combat_config"
        public = query_runtime(db, {"action": "definition", "definition_id": global_root[0][1],
                                    "sections": ["summary", "references", "payload"]})
        assert json.loads(public["payload_json"]) == json.loads(
            (fake_repo / runtime_catalog.GLOBAL_COMBAT_PATH).read_text(encoding="utf8"))
        assert public["references"] == []
        assert conn.execute("SELECT COUNT(*) FROM runtime_reference WHERE source_path=?",
                            (runtime_catalog.GLOBAL_COMBAT_PATH,)).fetchone() == (0,)
    assert fixture_builder.build_database(tmp_path).updated is False
    with pytest.raises(RuntimeConfigError) as exc:
        query_runtime(db, {"action": "attenuation_profile", "group": "case", "snapshot_id": old["snapshot_id"]})
    assert exc.value.reason == "snapshot_changed"


def test_exact_case_and_sql_literal_lookup_uses_identity_index(tmp_path, fake_repo, stub_fetcher):
    _write(fake_repo, ROWS)
    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    for index, row in enumerate(ROWS):
        result = _query(db, row["group"])
        assert result["source"]["json_pointer"] == f"/{index}"
        assert result["raw_record"] == row
    with closing(sqlite3.connect(db)) as conn:
        explanation = conn.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM runtime_definition WHERE version_line=? "
            "AND definition_kind=? AND exact_key_json=? AND source_path=? ORDER BY source_path,json_pointer",
            ("release", "excel_record", dumps({"group": "case"}), SOURCE)).fetchall()
    assert any("SEARCH" in row[3] and "exact_key_json=?" in row[3] for row in explanation), explanation


def test_forged_exact_key_cannot_redirect_to_another_raw_group(tmp_path, fake_repo, stub_fetcher):
    _write(fake_repo, ROWS)
    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE runtime_definition SET exact_key_json=? WHERE source_path=? AND json_pointer='/1'",
                     (dumps({"group": "forged"}), SOURCE))
    with pytest.raises(RuntimeConfigError) as exc:
        _query(db, "forged")
    assert exc.value.reason == "dataset_invalid"


def test_duplicate_group_failed_build_preserves_existing_snapshot(tmp_path, fake_repo, stub_fetcher):
    _write(fake_repo, ROWS)
    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    before = _query(db)
    _write(fake_repo, [*ROWS, copy.deepcopy(ROWS[0])])
    with pytest.raises(RuntimeConfigError):
        fixture_builder.build_database(tmp_path)
    after = _query(db)
    assert after == before  # Failure logging may change DB bytes, never these data.
    with closing(sqlite3.connect(db)) as conn:
        runtime_store.validate_runtime(conn, "release", deep=True)


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True,
                          check=True, encoding="utf8").stdout.strip()


def _commit_fixture(repo):
    _git(repo, "init", "-q")
    _git(repo, "config", "core.autocrlf", "false")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=Review Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "-qm", "OSCBWin7.0.54 local fixture")
    return _git(repo, "rev-parse", "HEAD")


def test_offline_refresh_keeps_beta_runtime_and_latest_manual_edit(tmp_path, fake_repo, monkeypatch):
    _write(fake_repo, ROWS)
    commit = _commit_fixture(fake_repo)
    monkeypatch.setattr(fetcher, "ensure_upstream", lambda _: pytest.fail("offline update attempted fetch"))
    fixture_builder.build_database(tmp_path, offline_repo=fake_repo, expected_commit=commit)
    db = tmp_path / "genshin.db"
    runtime_store.build_runtime_layer(db, fake_repo, version_line="beta", upstream_commit=commit)
    beta_before = query_runtime(db, {"action": "attenuation_profile", "group": "Case", "version_line": "beta"})
    annotation = queries.create_annotation(db, "character", 90000001, "before rebuild")
    original_validate = pipeline._validate

    def late_edit(build_db):
        queries.update_annotation(db, annotation["id"], {"content": "saved while building"})
        original_validate(build_db)

    monkeypatch.setattr(pipeline, "_validate", late_edit)
    with pytest.raises(pipeline.PipelineError, match="base_db"):
        fixture_builder.build_database(tmp_path, force=True, offline_repo=fake_repo, expected_commit=commit)
    beta_after = query_runtime(db, {"action": "attenuation_profile", "group": "Case", "version_line": "beta"})
    assert beta_before == beta_after
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT content FROM annotation WHERE id=?", (annotation["id"],)).fetchone()[0] == "saved while building"
        runtime_store.validate_runtime(conn, "beta", deep=True)
    assert not fixture_builder.build_database(tmp_path, offline_repo=fake_repo, expected_commit=commit).updated


def test_clean_status_skip_worktree_cannot_forge_pinned_source(tmp_path, fake_repo, monkeypatch):
    _write(fake_repo, ROWS)
    commit = _commit_fixture(fake_repo)
    monkeypatch.setattr(fetcher, "ensure_upstream", lambda _: pytest.fail("offline update attempted fetch"))
    fixture_builder.build_database(tmp_path, offline_repo=fake_repo, expected_commit=commit)
    db = tmp_path / "genshin.db"
    before = _query(db)
    _git(fake_repo, "update-index", "--skip-worktree", SOURCE)
    _write(fake_repo, [dict(ROWS[0], resetCycle=99), *ROWS[1:]])
    assert _git(fake_repo, "status", "--porcelain", "--untracked-files=all") == ""
    with pytest.raises(fetcher.FetcherError, match="固定commit"):
        fixture_builder.build_database(tmp_path, force=True, offline_repo=fake_repo, expected_commit=commit)
    assert _query(db) == before


@pytest.mark.parametrize("missing", ["commit", "repo"])
def test_partial_offline_request_does_not_initialize_database(tmp_path, missing):
    kwargs = {"offline_repo": tmp_path} if missing == "commit" else {"expected_commit": "a" * 40}
    with pytest.raises(ValueError):
        fixture_builder.build_database(tmp_path, **kwargs)
    assert not (tmp_path / "genshin.db").exists()
    assert not (tmp_path / "build").exists()


@pytest.mark.parametrize("semantic_change", [False, True])
def test_crlf_equivalence_is_narrow_and_does_not_rewrite_checkout(fake_repo, semantic_change):
    path = fake_repo / SOURCE
    path.write_bytes(json.dumps(ROWS, indent=2).encode("utf8"))
    commit = _commit_fixture(fake_repo)
    # Hide the checkout change from status; blob equivalence remains decisive.
    _git(fake_repo, "update-index", "--skip-worktree", SOURCE)
    payload = path.read_bytes().replace(b"\n", b"\r\n")
    if semantic_change:
        payload = payload.replace(b'"resetCycle": 2.5', b'"resetCycle": 9.5')
    path.write_bytes(payload)
    manifest = runtime_catalog.enumerate_runtime_sources(fake_repo)
    if semantic_change:
        with pytest.raises(fetcher.FetcherError, match="CRLF"):
            fetcher.verify_pinned_sources(fake_repo, commit, manifest)
    else:
        receipt = fetcher.verify_pinned_sources(fake_repo, commit, manifest)
        assert receipt["crlf_equivalent_files"] == 1
        assert receipt["crlf_examples"] == [SOURCE]
        assert receipt["stored_source_bytes"] == "unchanged_checkout_bytes"
        assert receipt["manifest_fingerprint"] == manifest["fingerprint"]
    assert path.read_bytes() == payload
