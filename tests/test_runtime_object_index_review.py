"""Independent EC13a counterexamples; synthetic sources, never the formal DB."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db import runtime_store as store
from genshin_db.runtime_queries import query_runtime
from tests.test_pipeline import fake_repo  # noqa: F401

# Fixture imports are registered by pytest.
# ruff: noqa: F811

GADGET_SOURCE = "BinOutput/Gadget/Review.json"
ABILITY_SOURCE = "BinOutput/Ability/Temp/Review.json"
NAME = "shared/~' OR 1=1--"
MOUNT = "Review_Mounted"
MAX_ID = 2**53 - 1


def write_source(repo, path, payload):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf8")


@pytest.fixture
def object_repo(fake_repo):
    # Two same-parent leaves select the same ID under different predicates.
    # A nested modifier is a distinct origin and retains its owner raw context.
    write_source(fake_repo, ABILITY_SOURCE, [{"Default": {
        "abilityName": "Review_Creator", "unknown_parent": {"must_keep": False},
        "onAdded": [
            {"$type": "CreateGadget", "gadgetID": 101, "predicate": "branch-one"},
            {"$type": "CreateGadget", "gadgetID": 101, "predicate": "branch-two"},
            {"$type": "GenerateElemBall", "configID": 101},
        ],
        "modifiers": {"Review_Modifier": {"onAdded": [
            {"$type": "TriggerBullet", "bulletID": 101, "unknown_delay": 9}
        ]}},
    }}, {"Default": {
        "abilityName": MOUNT,
        "onAdded": [{"$type": "AddAbility", "abilityName": "Missing_Inside_Mount"}],
    }}, {"Default": {"abilityName": "Review_Sibling_Only"}}])
    write_source(fake_repo, catalog.GADGET_EXCEL_PATH, [
        {"id": 101, "jsonName": NAME, "campID": 7},
        {"id": 102, "jsonName": NAME, "campID": 8},
        {"id": 103},
    ])
    write_source(fake_repo, GADGET_SOURCE, {
        NAME: {"abilities": [{"abilityName": MOUNT}], "unknown_control": {"active": False}},
        "Sibling": {"abilities": [{"abilityName": "Review_Sibling_Only"}]},
    })
    write_source(fake_repo, "ExcelBinOutput/AttackAttenuationExcelConfigData.json", [
        {"group": "default", "resetCycle": 2.5, "durabilitySequence": [1, 0, 0]},
        {"group": "review-alternate", "resetCycle": 1.25, "durabilitySequence": [1, 0]},
    ])
    return fake_repo


def build_index(repo, db, monkeypatch, version="5", line="release"):
    # Direct runtime builder requires the application's base metadata table.
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "INDEXER_VERSION", version)
        patch.setattr(store, "INDEXER_VERSION", version)
        store.build_runtime_layer(db, repo, version_line=line, upstream_commit="review-fixed-commit")
    return db


@pytest.fixture
def object_db(tmp_path, object_repo, monkeypatch):
    return build_index(object_repo, tmp_path / "isolated-objects.db", monkeypatch)


def sql_rows(db, sql, args=()):
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(sql, args)]


def origin_refs(db):
    return sql_rows(db, "SELECT * FROM runtime_reference WHERE source_path=? AND relation_kind='external_asset' ORDER BY json_pointer", (ABILITY_SOURCE,))


def test_review_two_same_target_leaves_keep_distinct_edges_and_particle_namespace(object_db):
    legacy = origin_refs(object_db)
    objects = sql_rows(object_db, "SELECT * FROM runtime_object_reference WHERE source_path=? AND relation_kind='gadget_id_reference' ORDER BY json_pointer", (ABILITY_SOURCE,))
    assert len(legacy) == 4 and len(objects) == 3
    assert {row["resolution_status"] for row in legacy} == {"external_asset"}
    assert {row["resolution_status"] for row in objects} == {"resolved"}
    assert {row["legacy_reference_id"] for row in objects} < {row["reference_id"] for row in legacy}
    assert not ({row["reference_id"] for row in objects} & {row["reference_id"] for row in legacy})
    assert len({row["reference_id"] for row in objects}) == 3
    assert not any(row["json_pointer"].endswith("/configID") for row in objects)


def test_review_shared_name_ids_are_not_deduplicated_and_pointer_escapes(object_db):
    records = sql_rows(object_db, "SELECT * FROM runtime_definition WHERE definition_kind='gadget_record' ORDER BY json_pointer")
    assert [json.loads(row["exact_key_json"]) for row in records] == [101, 102, 103]
    names = sql_rows(object_db, "SELECT * FROM runtime_object_reference WHERE relation_kind='gadget_config_name' ORDER BY json_pointer")
    assert len(names) == 3 and names[0]["resolved_definition_id"] == names[1]["resolved_definition_id"]
    assert names[0]["reference_id"] != names[1]["reference_id"]
    entry = sql_rows(object_db, "SELECT * FROM runtime_definition WHERE definition_kind='gadget_config_entry' AND exact_key_json=?", (json.dumps(NAME, ensure_ascii=False),))[0]
    assert entry["json_pointer"] == "/shared~1~0' OR 1=1--"


def test_review_object_mounts_only_from_entries_never_duplicate_root_or_sibling(object_db):
    mounts = sql_rows(object_db, "SELECT o.*,d.definition_kind FROM runtime_object_reference o JOIN runtime_definition d ON d.definition_id=o.from_definition_id AND d.version_line=o.version_line WHERE o.source_path=? AND o.relation_kind='gadget_ability_mount'", (GADGET_SOURCE,))
    assert len(mounts) == 2
    assert {row["definition_kind"] for row in mounts} == {"gadget_config_entry"}
    assert {json.loads(row["target_key_json"]) for row in mounts} == {MOUNT, "Review_Sibling_Only"}
    assert not sql_rows(object_db, "SELECT r.* FROM runtime_reference r JOIN runtime_definition d ON r.from_definition_id=d.definition_id WHERE d.definition_kind IN ('gadget_record','gadget_config_entry')")


def natural_closure(db):
    creator = sql_rows(db, "SELECT definition_id FROM runtime_definition WHERE definition_kind='ability' AND exact_key_json=?", ('"Review_Creator"',))[0]["definition_id"]
    value = query_runtime(db, {"action": "closure", "roots": [creator]})
    return {
        "definitions": sorted((d["definition_kind"], d["source_path"], d["json_pointer"], d["node_sha256"]) for d in value["definitions"]),
        "refs": sorted((r["source_path"], r["json_pointer"], r["relation_kind"], r["resolution_status"], r["target_key"]) for r in value["references"]),
        "closed": value["reference_closed"], "truncated": value["truncated"], "metrics": value["metrics"],
    }


@pytest.mark.parametrize("old_version", ["2", "3", "4"])
def test_review_old_closure_exact_range_survives_v5_without_object_table_requirement(tmp_path, object_repo, monkeypatch, old_version):
    old = build_index(object_repo, tmp_path / "old.db", monkeypatch, old_version)
    new = build_index(object_repo, tmp_path / "new.db", monkeypatch)
    # A real old snapshot never needed the additive table; reading must not create it.
    with closing(sqlite3.connect(old)) as conn, conn:
        conn.execute("DROP TABLE runtime_object_reference")
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == old_version
    assert natural_closure(old) == natural_closure(new)
    assert natural_closure(new)["metrics"] == {"definitions": 2, "references": 4}
    assert not sql_rows(old, "SELECT name FROM sqlite_master WHERE name='runtime_object_reference'")


def test_review_v5_retains_v4_exact_attenuation_identity(object_db):
    for group, reset in [("default", 2.5), ("review-alternate", 1.25)]:
        value = query_runtime(object_db, {"action": "attenuation_profile", "group": group})
        assert value["status"] == "ready"
        assert value["source"]["json_pointer"] in ("/0", "/1")
        assert value["raw_record"]["resetCycle"] == reset
        assert value["source"]["definition"]["exact_key"] == {"group": group}


@pytest.mark.parametrize("rows", [[{"id": True}], [{"id": 0}], [{"id": -1}], [{"id": 1.0}], [{"id": 1}, {"id": 1}], [{"id": MAX_ID + 1}]])
def test_review_invalid_or_duplicate_identity_rebuild_preserves_previous_db(object_db, object_repo, rows):
    before = sql_rows(object_db, "SELECT * FROM runtime_source ORDER BY source_path")
    write_source(object_repo, catalog.GADGET_EXCEL_PATH, rows)
    with pytest.raises(catalog.RuntimeConfigError):
        store.build_runtime_layer(object_db, object_repo, version_line="release", upstream_commit="review-fixed-commit")
    assert sql_rows(object_db, "SELECT * FROM runtime_source ORDER BY source_path") == before
    with closing(sqlite3.connect(object_db)) as conn:
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == "5"


def test_review_exact_sql_indices_cover_name_and_both_object_directions(object_db):
    with closing(sqlite3.connect(object_db)) as conn:
        statements = [
            ("SELECT * FROM runtime_definition WHERE version_line=? AND definition_kind=? AND exact_key_json=? ORDER BY source_path,json_pointer", ("release", "gadget_config_entry", json.dumps(NAME))),
            ("SELECT * FROM runtime_object_reference WHERE version_line=? AND from_definition_id=?", ("release", "id")),
            ("SELECT * FROM runtime_object_reference WHERE version_line=? AND target_kind=? AND target_key_json=?", ("release", "gadget_record", "101")),
        ]
        plans = [" ".join(str(row[3]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, args)) for sql, args in statements]
    assert all("SEARCH" in plan and "INDEX" in plan and "SCAN" not in plan for plan in plans)
    assert "exact_key_json=?" in plans[0] and "target_key_json=?" in plans[2]
