"""Actual B flow with tiny upstream files; only fetch transport is stubbed."""

import sqlite3
from contextlib import closing

import pytest

from genshin_db import pipeline
from genshin_db import semantic_store as store
from genshin_db.annotation_store import Annotation, add_annotation
from genshin_db.equipment import build_equipment_layer
from genshin_db.extractor import build_curated_layer
from genshin_db.runtime_store import TABLE_COLUMNS, build_runtime_layer
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_semantic_store import bundle, fact, head

# ruff: noqa: F811


def current(db, line="release"):
    with closing(sqlite3.connect(db)) as conn:
        return store.read_record(conn, line, "fact", "one", 1)


def semantic_rows(db):
    with closing(sqlite3.connect(db)) as conn:
        return {table: sorted(conn.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
                for table in (*store.TABLES.values(), store.HEAD_TABLE)}


def raw_rows(db):
    """Keep all content columns; rebuild timestamps are administrative metadata."""
    with closing(sqlite3.connect(db)) as conn:
        result = {"raw_table": conn.execute("SELECT source_file,data_json FROM raw_table ORDER BY source_file").fetchall()}
        for table in TABLE_COLUMNS:
            columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")') if row[1] != "imported_at"]
            quoted = ",".join('"' + name + '"' for name in columns)
            result[table] = sorted(conn.execute(f'SELECT {quoted} FROM "{table}"').fetchall(), key=repr)
        result["annotation"] = conn.execute("SELECT * FROM annotation ORDER BY id").fetchall()
        return result


def test_same_snapshot_remains_unchanged_and_never_seeds_approval(tmp_path, stub_fetcher):
    assert fixture_builder.build_database(tmp_path).action == "rebuilt"
    db = tmp_path / "genshin.db"
    with closing(sqlite3.connect(db)) as conn:
        assert store.read_catalog(conn)["items"] == []
    pending = fact(status="pending")
    store.import_bundle(db, bundle([pending]), dry_run=False)
    rows = semantic_rows(db)
    second = fixture_builder.build_database(tmp_path)
    assert not second.updated and second.action == "unchanged"
    assert semantic_rows(db) == rows
    assert current(db)["review"]["status"] == "pending"


@pytest.mark.parametrize("change", ["revoke", "delete_head"])
def test_candidate_refuses_late_review_or_deleted_heads_on_both_lines(
    tmp_path, fake_repo, stub_fetcher, monkeypatch, change,
):
    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    commit = "a" * 40
    build_curated_layer(db, "beta-fixture", "beta", upstream_commit=commit)
    build_equipment_layer(db, "beta", upstream_commit=commit, manifest=pipeline._source_manifest(fake_repo))
    build_runtime_layer(db, fake_repo, version_line="beta", upstream_commit=commit)
    for line in ("release", "beta"):
        store.import_bundle(db, bundle([fact(line=line)]), version_line=line, dry_run=False)
    add_annotation(db, Annotation("character", 90000001, "保留原人工", version_line="release", status="approved"))
    original_validate = pipeline._validate
    observed = {}

    def after_build(candidate):
        original_validate(candidate)
        # A synthetic fixture changed after the candidate snapshot was taken.
        # The new candidate path refuses this drift instead of merging it.
        for line in ("release", "beta"):
            old = current(db, line)
            if change == "revoke":
                store.review_record(db, version_line=line, record_kind="fact", record_id="one", revision=1,
                                    status="revoked", reviewer="late-reviewer", reviewed_at="2026-10-03T05:00:00+00:00",
                                    expected_review_fingerprint=old["review_fingerprint"])
            else:
                store.import_bundle(db, bundle([], [head(revision=None, expected=1)]), version_line=line, dry_run=False)
        add_annotation(db, Annotation("character", 90000001, "构建期间人工", version_line="release", status="approved"))
        observed["semantic"] = semantic_rows(db)
        observed["raw"] = raw_rows(db)

    monkeypatch.setattr(pipeline, "_validate", after_build)
    with pytest.raises(pipeline.PipelineError, match="base_db"):
        fixture_builder.build_database(tmp_path, force=True)
    assert semantic_rows(db) == observed["semantic"]
    assert raw_rows(db) == observed["raw"]
    for line in ("release", "beta"):
        with closing(sqlite3.connect(db)) as conn:
            selected = store.read_record(conn, line, "fact", "one")
            if change == "revoke":
                assert selected["review"]["status"] == "revoked"
            else:
                assert selected["reason"] == "head_missing"
                assert store.read_record(conn, line, "fact", "one", 1)["review"]["status"] == "approved"


def test_future_semantic_component_blocks_before_fetch_or_failure_log(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE data_component SET schema_version=2 WHERE component=?", (store.COMPONENT,))
    before, calls = db.read_bytes(), stub_fetcher["calls"]
    with pytest.raises(store.SemanticStoreError, match="版本"):
        fixture_builder.build_database(tmp_path, force=True)
    assert stub_fetcher["calls"] == calls and db.read_bytes() == before


def test_legacy_missing_semantics_is_unchanged_until_explicit_install_or_force(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    with closing(sqlite3.connect(db)) as conn, conn:
        for table in (*store.TABLES.values(), store.HEAD_TABLE):
            conn.execute(f'DROP TABLE "{table}"')
        conn.execute("DELETE FROM data_component WHERE component=?", (store.COMPONENT,))
    assert not fixture_builder.build_database(tmp_path).updated
    with closing(sqlite3.connect(db)) as conn:
        assert store.validate_component(conn)["reason"] == "component_missing"
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (store.HEAD_TABLE,)).fetchone()
    assert fixture_builder.build_database(tmp_path, force=True).updated
    with closing(sqlite3.connect(db)) as conn:
        assert store.validate_component(conn)["status"] == "ready"
        assert store.read_catalog(conn)["items"] == []
        assert conn.execute("SELECT COUNT(*) FROM mechanic_semantic_fact").fetchone()[0] == 0


def test_failed_force_does_not_revert_late_manual_revocation(tmp_path, stub_fetcher, monkeypatch):
    fixture_builder.build_database(tmp_path)
    db = tmp_path / "genshin.db"
    store.import_bundle(db, bundle(), dry_run=False)
    original_validate = pipeline._validate
    expected = {}

    def revoke(candidate):
        original_validate(candidate)
        old = current(db)
        store.review_record(db, version_line="release", record_kind="fact", record_id="one", revision=1,
                            status="revoked", reviewer="late", reviewed_at="2026-10-03",
                            expected_review_fingerprint=old["review_fingerprint"])
        expected["rows"] = semantic_rows(db)

    monkeypatch.setattr(pipeline, "_validate", revoke)
    with pytest.raises(pipeline.PipelineError, match="base_db"):
        fixture_builder.build_database(tmp_path, force=True)
    assert semantic_rows(db) == expected["rows"]
    assert current(db)["review"]["status"] == "revoked"
