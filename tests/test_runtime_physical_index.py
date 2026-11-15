"""Author checks for a physical-only optional index and B maintenance."""
# ruff: noqa: F811 - imported pytest fixtures are intentionally requested by name
from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from genshin_db import pipeline
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_physical_index import (
    INDEX_COLUMNS,
    INDEX_NAME,
    INDEX_SQL,
    ensure_physical_indexes,
    missing_physical_indexes,
    preservation_manifest,
)
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401


@pytest.fixture
def bare():
    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute("CREATE TABLE runtime_reference(version_line TEXT,resolved_definition_id TEXT,resolution_status TEXT,target_kind TEXT,target_key_json TEXT)")
        yield conn


def test_index_shape_idempotence_and_read_only_inspection(bare):
    before = bare.total_changes
    assert missing_physical_indexes(bare) == (INDEX_NAME,)
    assert bare.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall() == []
    assert ensure_physical_indexes(bare) == (INDEX_NAME,)
    assert ensure_physical_indexes(bare) == ()
    assert bare.total_changes == before
    actual = bare.execute(f'PRAGMA index_xinfo("{INDEX_NAME}")').fetchall()
    assert tuple(row[2] for row in actual if row[5]) == INDEX_COLUMNS
    assert all(row[3:5] == (0, "BINARY") for row in actual if row[5])


@pytest.mark.parametrize("ddl", [
    INDEX_SQL.replace("CREATE INDEX", "CREATE UNIQUE INDEX"),
    INDEX_SQL + " WHERE resolution_status='resolved'",
    INDEX_SQL.replace("target_key_json)", "target_key_json DESC)"),
    INDEX_SQL.replace("target_key_json)", "target_key_json COLLATE NOCASE)"),
    INDEX_SQL.replace("target_key_json)", "lower(target_key_json))"),
    INDEX_SQL.replace("version_line,resolved_definition_id", "resolved_definition_id,version_line"),
    f"CREATE VIEW {INDEX_NAME} AS SELECT 1",
    INDEX_SQL.replace(INDEX_NAME, INDEX_NAME.upper()),
])
def test_same_name_wrong_shape_is_not_repaired(bare, ddl):
    bare.execute(ddl)
    before = list(bare.execute("SELECT type,name,sql FROM sqlite_master"))
    with pytest.raises(RuntimeConfigError, match="不符"):
        ensure_physical_indexes(bare)
    assert list(bare.execute("SELECT type,name,sql FROM sqlite_master")) == before


def test_manifest_retains_types_order_blobs_and_without_rowid(bare):
    bare.execute("CREATE TABLE mixed(value)")
    bare.executemany("INSERT INTO mixed VALUES (?)", [(1,), (1.0,), ("1",), (b"1",), (None,)])
    bare.execute('CREATE TABLE "no rowid"(id TEXT PRIMARY KEY,value BLOB) WITHOUT ROWID')
    bare.execute('INSERT INTO "no rowid" VALUES (?,?)', ("a", b"\0"))
    before = preservation_manifest(bare)
    ensure_physical_indexes(bare)
    assert preservation_manifest(bare) == before
    bare.execute("UPDATE mixed SET value=1.0 WHERE rowid=1")
    assert preservation_manifest(bare) != before


def old_physical_db(tmp_path):
    fixture_builder.build_database(tmp_path)
    path = tmp_path / "genshin.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(f'DROP INDEX "{INDEX_NAME}"')
    return path


def content(path):
    with closing(sqlite3.connect(path)) as conn:
        value = preservation_manifest(conn)
    # Logs and their autoincrement counter are the only publication exception.
    value["tables"].pop("import_log", None)
    value["tables"].pop("sqlite_sequence", None)
    return value


def test_same_commit_maintenance_preserves_source_and_manual_without_rebuild(tmp_path, stub_fetcher, monkeypatch):
    path = old_physical_db(tmp_path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("INSERT INTO entity_alias VALUES (?,?,?,?,?,?,?,?,?)",
                     ("character", 90000001, "release", "zh-CN", "独立人工别名", "独立人工别名", "manual", "{}", None))
    before = content(path)

    def forbidden(*args, **kwargs):
        raise AssertionError("physical maintenance must not rebuild/migrate/orphan")

    for name in ("build_curated_layer", "build_runtime_layer", "build_equipment_layer"):
        monkeypatch.setattr(pipeline, name, forbidden)
    monkeypatch.setattr(pipeline.raw_store, "build_raw_layer", forbidden)
    result = fixture_builder.build_database(tmp_path)
    assert result.updated and result.action == "optimized"
    assert result.stats["physical_indexes_created"] == 1
    assert content(path) == before
    with closing(sqlite3.connect(path)) as conn:
        assert missing_physical_indexes(conn) == ()
    assert fixture_builder.build_database(tmp_path).action == "unchanged"


@pytest.mark.parametrize("failure", ["mutation", "deep", "receipt", "replace"])
def test_failed_maintenance_keeps_game_data_and_records_failure(tmp_path, stub_fetcher, monkeypatch, failure):
    path = old_physical_db(tmp_path)
    before = content(path)
    if failure == "mutation":
        original = pipeline.ensure_physical_indexes

        def corrupt(conn):
            result = original(conn)
            conn.execute("UPDATE text_map SET text='changed'")
            return result

        monkeypatch.setattr(pipeline, "ensure_physical_indexes", corrupt)
    elif failure == "deep":
        original = pipeline.validate_runtime

        def reject(conn, line, *, deep=False):
            if conn.execute("PRAGMA database_list").fetchone()[2].endswith(".candidate"):
                raise RuntimeConfigError("deliberate candidate deep failure")
            return original(conn, line, deep=deep)

        monkeypatch.setattr(pipeline, "validate_runtime", reject)
    else:
        def fail(*args, **kwargs):
            raise OSError("deliberate " + failure)

        if failure == "receipt":
            monkeypatch.setattr(pipeline, "_write_json_atomic", fail)
        else:
            monkeypatch.setattr(pipeline.os, "replace", fail)
    with pytest.raises((OSError, RuntimeConfigError, pipeline.PipelineError)):
        fixture_builder.build_database(tmp_path)
    assert content(path) == before
    with closing(sqlite3.connect(path)) as conn:
        assert missing_physical_indexes(conn) == (INDEX_NAME,)


def test_new_build_has_optional_index(tmp_path, stub_fetcher):
    result = fixture_builder.build_database(tmp_path)
    assert result.action == "rebuilt"
    with closing(sqlite3.connect(tmp_path / "genshin.db")) as conn:
        assert missing_physical_indexes(conn) == ()


def test_all_ready_runtime_lines_deep_checked_and_opaque_data_preserved(tmp_path, stub_fetcher, fake_repo, monkeypatch):
    from genshin_db.runtime_store import build_runtime_layer

    path = old_physical_db(tmp_path)
    build_runtime_layer(path, fake_repo, version_line="beta", upstream_commit="b" * 40)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(f'DROP INDEX "{INDEX_NAME}"')
        conn.execute('CREATE TABLE "opaque state"(id TEXT PRIMARY KEY,payload BLOB) WITHOUT ROWID')
        conn.execute('INSERT INTO "opaque state" VALUES (?,?)', ("unchanged", b"\0\xff"))
    before = content(path)
    seen = []
    original = pipeline.validate_runtime

    def observe(conn, line, *, deep=False):
        seen.append((line, deep))
        return original(conn, line, deep=deep)

    monkeypatch.setattr(pipeline, "validate_runtime", observe)
    assert fixture_builder.build_database(tmp_path).action == "optimized"
    assert ("release", True) in seen and ("beta", True) in seen
    assert content(path) == before


def test_late_out_of_protocol_change_refuses_to_overwrite(tmp_path, stub_fetcher, monkeypatch):
    path = old_physical_db(tmp_path)
    original = pipeline.ensure_physical_indexes
    def change_during_optimization(connection):
        created = original(connection)
        with closing(sqlite3.connect(path)) as base, base:
            base.execute("INSERT INTO text_map VALUES ('late-only','CHS','late external state')")
        return created
    monkeypatch.setattr(pipeline, "ensure_physical_indexes", change_during_optimization)
    with pytest.raises(pipeline.PipelineError, match="base_db"):
        fixture_builder.build_database(tmp_path)
    with closing(sqlite3.connect(path)) as connection:
        assert missing_physical_indexes(connection) == (INDEX_NAME,)
        assert connection.execute("SELECT text FROM text_map WHERE hash='late-only'").fetchone()[0] == "late external state"


def test_live_wal_with_active_reader_is_not_replaced(tmp_path, stub_fetcher):
    path = old_physical_db(tmp_path)
    with closing(sqlite3.connect(path)) as reader:
        assert reader.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM runtime_reference").fetchone()
        with pytest.raises(pipeline.PipelineError, match="WAL"):
            fixture_builder.build_database(tmp_path)
        assert reader.execute(f"SELECT name FROM sqlite_master WHERE name='{INDEX_NAME}'").fetchone() is None
    with closing(sqlite3.connect(path)) as conn:
        assert missing_physical_indexes(conn) == (INDEX_NAME,)
