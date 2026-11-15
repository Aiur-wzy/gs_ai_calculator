from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db.release_integrity import (
    ReleaseIntegrityError,
    build_mutable_layer_fingerprint,
    build_release_core,
    database_matches_release_core,
    validate_release_core,
)


def _database(path, *, reverse=False):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(
            """
            CREATE TABLE weapon (id INTEGER PRIMARY KEY, name TEXT, base_atk REAL, payload BLOB);
            CREATE INDEX idx_weapon_name ON weapon(name);
            CREATE TABLE annotation (id INTEGER PRIMARY KEY, content TEXT);
            CREATE TABLE import_log (id INTEGER PRIMARY KEY, status TEXT);
            CREATE TABLE data_component (
                component TEXT PRIMARY KEY, state TEXT, upstream_commit TEXT,
                validation_json TEXT
            );
            """
        )
        rows = [
            (1, "Alpha", 674.0, b"\x00\x01"),
            (2, "Beta", 608.0, b"\xff"),
        ]
        connection.executemany(
            "INSERT INTO weapon VALUES (?,?,?,?)",
            list(reversed(rows)) if reverse else rows,
        )
        connection.execute("INSERT INTO annotation VALUES (1,'local')")
        connection.execute("INSERT INTO import_log VALUES (1,'success')")
        connection.executemany(
            "INSERT INTO data_component VALUES (?,?,?,?)",
            [
                ("equipment", "ready", "a" * 40, "{}"),
                ("runtime_config", "ready", "a" * 40, "{}"),
                ("mechanic_semantics", "ready", "manual", '{"review":1}'),
            ],
        )


def test_release_core_is_deterministic_and_ignores_row_insertion_order(tmp_path):
    first = tmp_path / "first.db"
    second = tmp_path / "second.db"
    _database(first)
    _database(second, reverse=True)
    assert build_release_core(first) == build_release_core(second)


def test_mutable_changes_do_not_change_core_but_business_changes_do(tmp_path):
    database = tmp_path / "database.db"
    _database(database)
    expected = build_release_core(database)
    mutable_before = build_mutable_layer_fingerprint(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("UPDATE annotation SET content='edited'")
        connection.execute("INSERT INTO import_log VALUES (2,'later')")
        connection.execute(
            "UPDATE data_component SET validation_json=? WHERE component=?",
            ('{"review":2}', "mechanic_semantics"),
        )
    assert database_matches_release_core(database, expected)
    assert build_mutable_layer_fingerprint(database) != mutable_before
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "UPDATE data_component SET state='missing' WHERE component='equipment'"
        )
    assert not database_matches_release_core(database, expected)


def test_mutable_table_rows_are_mutable_but_its_schema_is_not(tmp_path):
    database = tmp_path / "database.db"
    _database(database)
    expected = build_release_core(database)
    assert "annotation" in expected["tables"]
    assert expected["tables"]["annotation"]["row_count"] == 0

    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO annotation VALUES (2,'another local row')")
    assert database_matches_release_core(database, expected)

    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("ALTER TABLE annotation ADD COLUMN source TEXT")
    assert not database_matches_release_core(database, expected)


def test_schema_and_new_immutable_table_drift_are_detected(tmp_path):
    database = tmp_path / "database.db"
    _database(database)
    expected = build_release_core(database)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("CREATE TABLE skill (id INTEGER PRIMARY KEY, multiplier REAL)")
    assert not database_matches_release_core(database, expected)


def test_validator_rejects_tampered_root_and_unsorted_mutable_tables(tmp_path):
    database = tmp_path / "database.db"
    _database(database)
    expected = build_release_core(database)
    assert validate_release_core(expected) == expected
    tampered = copy.deepcopy(expected)
    tampered["root_sha256"] = "0" * 64
    with pytest.raises(ReleaseIntegrityError) as failure:
        validate_release_core(tampered)
    assert failure.value.reason == "database_release_core_root_invalid"
    unsorted = copy.deepcopy(expected)
    unsorted["mutable_tables"] = list(reversed(unsorted["mutable_tables"]))
    with pytest.raises(ReleaseIntegrityError) as failure:
        validate_release_core(unsorted)
    assert failure.value.reason == "database_release_core_mutable_tables_invalid"

    expanded = copy.deepcopy(expected)
    expanded["mutable_tables"] = sorted([*expanded["mutable_tables"], "weapon"])
    expanded_body = {key: value for key, value in expanded.items() if key != "root_sha256"}
    expanded["root_sha256"] = hashlib.sha256(
        json.dumps(
            expanded_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    with pytest.raises(ReleaseIntegrityError) as failure:
        validate_release_core(expanded)
    assert failure.value.reason == "database_release_core_mutable_tables_unsupported"
