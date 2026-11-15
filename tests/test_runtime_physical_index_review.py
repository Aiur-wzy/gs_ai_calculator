"""Independent EC17b publication tests; all databases are tiny local fixtures."""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from genshin_db import pipeline, queries
from genshin_db import runtime_physical_index as physical
from genshin_db.equipment import build_equipment_layer
from genshin_db.extractor import build_curated_layer
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import get_runtime_status, query_runtime
from genshin_db.runtime_store import build_runtime_layer
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_indexer4 import pinned_repo, update  # noqa: F401

# ruff: noqa: F811


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def snapshot(db, *, omit_performance=True):
    """Our own row oracle: ignore only the documented administrative exception."""
    with closing(sqlite3.connect(db)) as conn:
        schema = conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name,sql"
        ).fetchall()
        if omit_performance:
            schema = [row for row in schema if row[1] != physical.INDEX_NAME]
        tables = {}
        for kind, name, _, _ in schema:
            if kind != "table" or name == "import_log":
                continue
            rows = conn.execute(f"SELECT * FROM {quote(name)}").fetchall()
            if name == "sqlite_sequence":
                rows = [row for row in rows if row[0] != "import_log"]
            # Preserve SQLite storage types as well as values, without using the
            # production digest or relying on index-dependent SELECT ordering.
            typed = [tuple((type(v).__name__, repr(v)) for v in row) for row in rows]
            tables[name] = sorted(typed)
        return schema, tables


def logs(db):
    with closing(sqlite3.connect(db)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='import_log'").fetchone():
            return []
        return conn.execute("SELECT * FROM import_log ORDER BY id").fetchall()


def assert_history_unchanged(db, before):
    assert logs(db) == before


def drop_index(db):
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(f"DROP INDEX {quote(physical.INDEX_NAME)}")


@pytest.fixture
def old_db(tmp_path, stub_fetcher):
    report = fixture_builder.build_database(tmp_path)
    assert report.updated and report.action == "rebuilt"
    db = tmp_path / "genshin.db"
    drop_index(db)
    return db


def test_review_helper_is_inspection_only_and_ddl_rolls_back(tmp_path):
    db = tmp_path / "narrow.db"
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("CREATE TABLE runtime_reference(" + ",".join(physical.INDEX_COLUMNS) + ")")
        conn.commit()
        original = db.read_bytes()
        assert physical.missing_physical_indexes(conn) == (physical.INDEX_NAME,)
        assert db.read_bytes() == original
        conn.execute("BEGIN")
        assert physical.ensure_physical_indexes(conn) == (physical.INDEX_NAME,)
        assert physical.ensure_physical_indexes(conn) == ()
        conn.rollback()
        assert physical.missing_physical_indexes(conn) == (physical.INDEX_NAME,)
        with conn:
            physical.ensure_physical_indexes(conn)
        assert physical.missing_physical_indexes(conn) == ()


@pytest.mark.parametrize("ddl", [
    "CREATE INDEX {name} ON runtime_reference(resolved_definition_id,version_line,resolution_status,target_kind,target_key_json)",
    "CREATE UNIQUE INDEX {name} ON runtime_reference(version_line,resolved_definition_id,resolution_status,target_kind,target_key_json)",
    "CREATE INDEX {name} ON runtime_reference(version_line DESC,resolved_definition_id,resolution_status,target_kind,target_key_json)",
    "CREATE INDEX {name} ON runtime_reference(version_line COLLATE NOCASE,resolved_definition_id,resolution_status,target_kind,target_key_json)",
    "CREATE INDEX {name} ON runtime_reference(version_line,resolved_definition_id,resolution_status,target_kind,target_key_json) WHERE version_line IS NOT NULL",
    "CREATE INDEX {name} ON runtime_reference(version_line,resolved_definition_id,resolution_status,lower(target_kind),target_key_json)",
    "CREATE INDEX {name} ON other_table(version_line,resolved_definition_id,resolution_status,target_kind,target_key_json)",
    "CREATE TABLE {name}(unrelated)",
])
def test_review_same_name_wrong_shape_is_not_repaired_or_accepted(ddl):
    with closing(sqlite3.connect(":memory:")) as conn:
        for table in ("runtime_reference", "other_table"):
            conn.execute(f"CREATE TABLE {table}(" + ",".join(physical.INDEX_COLUMNS) + ")")
        conn.execute(ddl.format(name=physical.INDEX_NAME))
        before = conn.execute("SELECT * FROM sqlite_master ORDER BY name").fetchall()
        for operation in (physical.missing_physical_indexes, physical.ensure_physical_indexes):
            with pytest.raises(RuntimeConfigError) as caught:
                operation(conn)
            assert caught.value.reason == "physical_index_invalid"
            assert conn.execute("SELECT * FROM sqlite_master ORDER BY name").fetchall() == before


@pytest.mark.parametrize("indexer_version", ["6", "7"])
def test_review_old_indexless_database_reads_without_schema_or_source_change(
        tmp_path, stub_fetcher, monkeypatch, indexer_version):
    from genshin_db import runtime_catalog, runtime_store
    with monkeypatch.context() as patch:
        patch.setattr(runtime_catalog, "INDEXER_VERSION", indexer_version)
        patch.setattr(runtime_store, "INDEXER_VERSION", indexer_version)
        report = fixture_builder.build_database(tmp_path)
    assert report.updated and report.action == "rebuilt"
    old_db = tmp_path / "genshin.db"
    drop_index(old_db)
    with closing(sqlite3.connect(old_db)) as conn:
        ident = conn.execute(
            "SELECT definition_id FROM runtime_definition WHERE definition_kind='ability' ORDER BY definition_id"
        ).fetchone()[0]
    before = hashlib.sha256(old_db.read_bytes()).hexdigest()
    status = get_runtime_status(old_db)
    definition = query_runtime(old_db, {"action": "definition", "definition_id": ident})
    assert status["state"] == "ready" and status["indexer_version"] == indexer_version
    assert definition["definition"]["definition_id"] == ident
    assert hashlib.sha256(old_db.read_bytes()).hexdigest() == before
    with closing(sqlite3.connect(old_db)) as conn:
        assert physical.missing_physical_indexes(conn) == (physical.INDEX_NAME,)


def test_review_maintenance_preserves_every_nonlog_row_manual_beta_and_identity(old_db, fake_repo, monkeypatch):
    commit = "a" * 40
    build_curated_layer(old_db, "beta-test", "beta", upstream_commit=commit)
    build_equipment_layer(old_db, "beta", upstream_commit=commit,
                          manifest=pipeline._source_manifest(fake_repo))
    build_runtime_layer(old_db, fake_repo, version_line="beta", upstream_commit=commit)
    # Building the second line also creates the new physical index; model the
    # real old database only after both logical snapshots are complete.
    drop_index(old_db)
    queries.create_annotation(old_db, "character", 90000001, "人工原稿")
    with closing(sqlite3.connect(old_db)) as conn, conn:
        conn.execute("INSERT INTO entity_alias VALUES (?,?,?,?,?,?,?,?,?)",
                     ("character", 90000001, "release", "zh-CN", "人工称呼", "人工称呼", "manual", '{}', None))
        conn.execute("INSERT INTO character_note VALUES (?,?,?,?)",
                     (90000001, "beta", "手工跨线注释", "2001-02-03T04:05:06Z"))
        conn.execute("INSERT INTO text_map VALUES ('beta-only','CHS','共享文本保留')")
        conn.execute("CREATE TABLE user_aux(id INTEGER PRIMARY KEY, value, payload BLOB)")
        conn.executemany("INSERT INTO user_aux VALUES(?,?,?)", [
            (1, 1, b"\x00\xff"), (2, 1.0, b""), (3, "1", None), (4, None, b"\x01")])
    before, before_logs = snapshot(old_db), logs(old_db)
    states = {line: get_runtime_status(old_db, line) for line in ("release", "beta")}
    checked = []
    validate = pipeline.validate_runtime

    def observed(conn, line, **options):
        checked.append((line, options.get("deep", False)))
        return validate(conn, line, **options)

    monkeypatch.setattr(pipeline, "validate_runtime", observed)

    def forbidden(*args, **kwargs):
        pytest.fail("physical maintenance must not rebuild, relabel or migrate logical data")

    for name in ("build_curated_layer", "build_equipment_layer", "build_runtime_layer",
                 "apply_labels"):
        monkeypatch.setattr(pipeline, name, forbidden)
    monkeypatch.setattr(pipeline.raw_store, "build_raw_layer", forbidden)
    report = fixture_builder.build_database(old_db.parent)
    assert report.updated and report.action == "optimized"
    assert report.old_version == report.new_version == "a" * 12
    assert report.orphan_annotations == 0
    assert report.stats["physical_indexes_created"] == 1
    assert {("release", True), ("beta", True)} <= set(checked)
    assert snapshot(old_db) == before
    assert {line: get_runtime_status(old_db, line) for line in states} == states
    assert_history_unchanged(old_db, before_logs)
    after_logs = logs(old_db)
    second = fixture_builder.build_database(old_db.parent)
    assert second.action == "unchanged" and not second.updated and second.backup_path is None
    assert snapshot(old_db) == before
    assert_history_unchanged(old_db, after_logs)


def test_review_pinned_offline_maintenance_retains_source_verification(tmp_path, pinned_repo, monkeypatch):
    folder = tmp_path / "offline-stage"
    update(folder, pinned_repo)
    db = folder / "genshin.db"
    drop_index(db)
    before = snapshot(db)
    monkeypatch.setattr(pipeline.fetcher, "ensure_upstream", lambda *_: pytest.fail("unexpected network"))
    report = update(folder, pinned_repo)
    assert report.action == "optimized" and report.source_verification
    assert snapshot(db) == before
    assert update(folder, pinned_repo).action == "unchanged"


def test_review_same_name_wrong_index_in_live_is_failed_not_rebuilt(old_db):
    with closing(sqlite3.connect(old_db)) as conn, conn:
        conn.execute(f"CREATE INDEX {physical.INDEX_NAME} ON runtime_reference(version_line)")
    before, history = snapshot(old_db, omit_performance=False), logs(old_db)
    with pytest.raises(RuntimeConfigError, match="规格不符"):
        fixture_builder.build_database(old_db.parent)
    assert snapshot(old_db, omit_performance=False) == before
    assert_history_unchanged(old_db, history)
    assert not list((old_db.parent / "backups").glob("*.db"))


def test_review_case_variant_reserved_name_is_controlled_error_without_repair(old_db):
    upper = physical.INDEX_NAME.upper()
    with closing(sqlite3.connect(old_db)) as conn, conn:
        conn.execute(f"CREATE INDEX {upper} ON runtime_reference({','.join(physical.INDEX_COLUMNS)})")
    before, history = snapshot(old_db, omit_performance=False), logs(old_db)
    with pytest.raises(RuntimeConfigError) as caught:
        fixture_builder.build_database(old_db.parent)
    assert caught.value.reason == "physical_index_invalid"
    assert snapshot(old_db, omit_performance=False) == before
    assert_history_unchanged(old_db, history)


def test_review_active_live_wal_is_rejected_without_checkpoint_or_removing_sidecars(old_db, monkeypatch):
    history, before = logs(old_db), snapshot(old_db, omit_performance=False)

    def forbidden(*args, **kwargs):
        pytest.fail("live WAL must be rejected before candidate DDL")

    monkeypatch.setattr(pipeline, "ensure_physical_indexes", forbidden)
    with closing(sqlite3.connect(old_db)) as owner:
        assert owner.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        with owner:
            owner.execute("UPDATE text_map SET text=text")
        owner.execute("BEGIN")
        owner.execute("SELECT * FROM text_map").fetchall()
        wal, shm = [Path(str(old_db) + suffix) for suffix in ("-wal", "-shm")]
        assert wal.is_file() and shm.is_file()
        with pytest.raises(pipeline.PipelineError, match="WAL"):
            fixture_builder.build_database(old_db.parent)
        # Our pre-existing reader transaction remains usable; the maintenance
        # path must not unlink/checkpoint its active files to make replace pass.
        assert wal.is_file() and shm.is_file()
        assert owner.execute("SELECT COUNT(*) FROM text_map").fetchone()[0] > 0
        assert owner.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert snapshot(old_db, omit_performance=False) == before
        assert_history_unchanged(old_db, history)


def test_review_active_candidate_wal_is_retained_and_never_replaces_live(old_db, monkeypatch):
    before = snapshot(old_db, omit_performance=False)
    original, holders = pipeline.optimize_candidate_physical_layout, []
    def hold_wal_candidate(run, ports):
        original(run, ports)
        connection = sqlite3.connect(run.temporary)
        holders.append((run.temporary, connection))
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        with connection:
            connection.execute("UPDATE text_map SET text=text")
        connection.execute("BEGIN")
        connection.execute("SELECT * FROM text_map").fetchall()
    monkeypatch.setattr(pipeline, "optimize_candidate_physical_layout", hold_wal_candidate)
    # Fixture composition calls the production pass object too.
    monkeypatch.setattr(fixture_builder, "optimize_candidate_physical_layout", hold_wal_candidate)
    try:
        with pytest.raises(pipeline.PipelineError, match="WAL"):
            fixture_builder.build_database(old_db.parent)
        assert len(holders) == 1
        candidate, reader = holders[0]
        assert candidate.is_file()
        assert Path(str(candidate) + "-wal").is_file()
        assert Path(str(candidate) + "-shm").is_file()
        assert reader.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        assert snapshot(old_db, omit_performance=False) == before
        with closing(sqlite3.connect(old_db)) as connection:
            assert physical.missing_physical_indexes(connection) == (physical.INDEX_NAME,)
    finally:
        for _, connection in holders:
            connection.close()


@pytest.mark.parametrize("failure", ["ddl", "receipt", "replace", "row_change", "type_change", "deep"])
def test_review_failure_never_publishes_candidate_and_only_appends_failure_log(old_db, monkeypatch, failure):
    with closing(sqlite3.connect(old_db)) as conn, conn:
        conn.execute("CREATE TABLE user_aux(value)")
        conn.execute("INSERT INTO user_aux VALUES(1)")
    before, history = snapshot(old_db, omit_performance=False), logs(old_db)
    ensure = pipeline.ensure_physical_indexes

    def broken_ddl(conn):
        if failure == "ddl":
            raise sqlite3.OperationalError("injected index build failure")
        result = ensure(conn)
        conn.execute("UPDATE user_aux SET value=?", (1.0 if failure == "type_change" else 2,))
        return result

    def broken(*args, **kwargs):
        raise OSError("injected publication failure")

    validate = pipeline.validate_runtime

    def deep_failure(conn, line, **options):
        if options.get("deep"):
            raise RuntimeConfigError("injected candidate deep validation failure")
        return validate(conn, line, **options)

    with monkeypatch.context() as patch:
        if failure in ("ddl", "row_change", "type_change"):
            patch.setattr(pipeline, "ensure_physical_indexes", broken_ddl)
        elif failure == "receipt":
            patch.setattr(pipeline, "_write_json_atomic", broken)
        elif failure == "replace":
            patch.setattr(pipeline.os, "replace", broken)
        else:
            patch.setattr(pipeline, "validate_runtime", deep_failure)
        with pytest.raises((OSError, sqlite3.OperationalError, RuntimeConfigError, pipeline.PipelineError)):
            fixture_builder.build_database(old_db.parent)
    assert snapshot(old_db, omit_performance=False) == before
    assert_history_unchanged(old_db, history)
    with closing(sqlite3.connect(old_db)) as conn:
        assert physical.missing_physical_indexes(conn) == (physical.INDEX_NAME,)
    # A failed fixture candidate must leave the base reusable.
    assert fixture_builder.build_database(old_db.parent).action == "optimized"
