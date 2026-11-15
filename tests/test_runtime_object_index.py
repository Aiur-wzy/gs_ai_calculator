"""Indexer 5 object identities stay separate from the legacy execution graph."""
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db import runtime_store as store
from genshin_db.annotation_store import Annotation, add_annotation
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811
GADGET = "BinOutput/Gadget/ObjectFixture.json"
ABILITY = "BinOutput/Ability/Temp/ObjectFixture.json"


def write(repo, path, value):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False), "utf8")


def build(db, repo, *, version_line="release", upstream_commit="a" * 40):
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT)")
    return store.build_runtime_layer(db, repo, version_line=version_line, upstream_commit=upstream_commit)


@pytest.fixture
def object_repo(fake_repo):
    write(fake_repo, catalog.GADGET_EXCEL_PATH, [
        {"id": 101, "jsonName": "Shared", "kind": "first"},
        {"id": 102, "jsonName": "Shared", "kind": "second"},
        {"id": 103}, {"id": 104, "jsonName": None}, {"id": 105, "jsonName": ""},
        {"id": 106, "jsonName": "NotHere"},
    ])
    write(fake_repo, GADGET, {"Shared": {"abilities": [{"abilityName": "Child"}], "opaque": {"unknown": True}},
                             "Unrelated": {"abilities": []}})
    write(fake_repo, ABILITY, [{"Default": {"abilityName": "Creator", "onAdded": [
        {"$type": "CreateGadget", "gadgetID": 101, "unknown": True},
        {"$type": "CreateGadget", "gadgetID": 101, "predicates": [{"$type": "UnknownCondition"}]},
        {"$type": "GenerateElemBall", "configID": 2022},
    ], "modifiers": {"Spawn": {"onAdded": [{"$type": "TriggerBullet", "bulletID": 102}]}}}},
        {"Default": {"abilityName": "Child"}}])
    return fake_repo


@pytest.fixture
def object_db(tmp_path, object_repo):
    db = tmp_path / "objects.db"
    build(db, object_repo)
    return db


def rows(db, table, suffix="", params=()):
    with closing(sqlite3.connect(db)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(f"SELECT * FROM {table} {suffix}", params)]


def test_two_id_rows_share_one_exact_config_without_collapsing_rows(object_db):
    links = rows(object_db, "runtime_object_reference", "WHERE relation_kind='gadget_config_name'")
    by_id = {json.loads(r["exact_key_json"]): r for r in rows(object_db, "runtime_definition", "WHERE definition_kind='gadget_record'")}
    by_from = {r["from_definition_id"]: r for r in links}
    assert by_id[101]["node_sha256"] != by_id[102]["node_sha256"]
    assert by_from[by_id[101]["definition_id"]]["resolved_definition_id"] == by_from[by_id[102]["definition_id"]]["resolved_definition_id"]
    assert [by_from[by_id[i]["definition_id"]]["presence_status"] for i in (103, 104, 105)] == ["missing", "null", "empty"]
    assert by_from[by_id[106]["definition_id"]]["resolution_status"] == "missing"


def test_old_edges_and_closure_do_not_enter_new_object_graph(object_db):
    creator = next(r for r in rows(object_db, "runtime_definition") if r["definition_kind"] == "ability" and r["exact_key_json"] == '"Creator"')
    closure = query_runtime(object_db, {"action": "closure", "roots": [creator["definition_id"]]})
    assert {r["definition_kind"] for r in closure["definitions"]} <= {"ability", "modifier"}
    external = [r for r in closure["references"] if r["relation_kind"] == "external_asset"]
    assert len(external) == 4 and all(r["resolution_status"] == "external_asset" for r in external)
    links = rows(object_db, "runtime_object_reference", "WHERE relation_kind='gadget_id_reference'")
    assert len(links) == 3 and all(r["resolution_status"] == "resolved" for r in links)
    assert len({r["reference_id"] for r in links}) == 3
    assert {r["legacy_reference_id"] for r in links} < {r["reference_id"] for r in external}
    assert not ({r["reference_id"] for r in links} & {r["reference_id"] for r in external})
    assert len(rows(object_db, "runtime_object_reference", "WHERE relation_kind='gadget_ability_mount'")) == 1
    with closing(sqlite3.connect(object_db)) as conn:
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == store.INDEXER_VERSION


@pytest.mark.parametrize("version", ["2", "3", "4"])
def test_old_stored_index_validates_without_new_table_or_manifest(object_repo, tmp_path, monkeypatch, version):
    db = tmp_path / "old.db"
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "INDEXER_VERSION", version)
        patch.setattr(store, "INDEXER_VERSION", version)
        build(db, object_repo)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DROP TABLE runtime_object_reference")
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == version
        assert conn.execute("SELECT COUNT(*) FROM runtime_source WHERE source_path=?", (catalog.GADGET_EXCEL_PATH,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runtime_definition WHERE definition_kind IN ('gadget_record','gadget_config_entry')").fetchone()[0] == 0


@pytest.mark.parametrize("version", ["2", "3", "4"])
def test_old_definition_and_reference_rules_not_rewritten(version):
    data = {"Shared": {"abilities": [{"abilityName": "Child"}]}}
    legacy = catalog.extract_definitions(GADGET, "gadget", data, "snapshot", indexer_version=version)
    current = catalog.extract_definitions(GADGET, "gadget", data, "snapshot", indexer_version="5")
    assert len(legacy) == 1 and current[0] == legacy[0]
    prior = catalog.extract_references(legacy[0], indexer_version=version)
    latest = catalog.extract_references(current[0], indexer_version="5")
    assert [{k: v for k, v in r.items() if k != "extraction_rule_id"} for r in prior] == [
        {k: v for k, v in r.items() if k != "extraction_rule_id"} for r in latest]


@pytest.mark.parametrize("bad", [True, 0, -1, "101", None, 2**53])
def test_invalid_gadget_ids_rejected(bad):
    with pytest.raises(catalog.RuntimeConfigError):
        catalog.extract_definitions(catalog.GADGET_EXCEL_PATH, "gadget_excel", [{"id": bad}], "snapshot")


def test_duplicate_id_failure_keeps_previous_snapshot(object_repo, object_db):
    before = object_db.read_bytes()
    write(object_repo, catalog.GADGET_EXCEL_PATH, [{"id": 101}, {"id": 101}])
    with pytest.raises(catalog.RuntimeConfigError, match="ID重复"):
        store.build_runtime_layer(object_db, object_repo, version_line="release", upstream_commit="a" * 40)
    assert object_db.read_bytes() == before


def test_ambiguous_config_and_ability_keeps_every_exact_candidate(object_repo, tmp_path):
    write(object_repo, "BinOutput/Gadget/Collision.json", {"Shared": {"abilities": []}})
    write(object_repo, "BinOutput/Ability/Temp/Collision.json", [{"Another": {"abilityName": "Child"}}])
    db = tmp_path / "ambiguous.db"
    build(db, object_repo)
    links = rows(db, "runtime_object_reference")
    for r in links:
        if r["target_key_json"] in ('"Shared"', '"Child"'):
            assert r["resolution_status"] == "ambiguous"
            assert r["resolved_definition_id"] is None and len(json.loads(r["candidate_ids_json"])) == 2


@pytest.mark.parametrize("field,value", [("scope_json", "{}"), ("candidate_ids_json", "[]"),
                                        ("legacy_reference_id", None), ("json_pointer", "/bad")])
def test_deep_rejects_forged_object_relationship(object_db, field, value):
    with closing(sqlite3.connect(object_db)) as conn, conn:
        ident = conn.execute("SELECT reference_id FROM runtime_object_reference WHERE relation_kind='gadget_id_reference' LIMIT 1").fetchone()[0]
        conn.execute(f"UPDATE runtime_object_reference SET {field}=? WHERE reference_id=?", (value, ident))
    with closing(sqlite3.connect(object_db)) as conn, pytest.raises(catalog.RuntimeConfigError):
        store.validate_runtime(conn, "release", deep=True)


def test_normalized_gadget_scope_keeps_dynamic_names_and_raw_pointers():
    from genshin_runtime.normalization import Profile
    profile = Profile("test", ("a" * 40,), {"OPAQUEARRAY": "abilities", "OPAQUENAME": "abilityName"}, {})
    root = {"OPAQUEARRAY": {"OPAQUEARRAY": [{"OPAQUENAME": "Child"}]}}
    definitions = catalog.extract_definitions(GADGET, "gadget", root, "snapshot", normalization=profile)
    child = definitions[1]
    assert json.loads(child["exact_key_json"]) == "OPAQUEARRAY"
    assert catalog.extract_references(child, normalization=profile) == []
    link = catalog.extract_object_references(child, normalization=profile)[0]
    assert link["json_pointer"] == "/OPAQUEARRAY/OPAQUEARRAY/0/OPAQUENAME"
    assert link["target_key_json"] == '"Child"'
    assert root == {"OPAQUEARRAY": {"OPAQUEARRAY": [{"OPAQUENAME": "Child"}]}}


def test_same_commit_b_upgrades_once_and_preserves_manual_and_beta(tmp_path, object_repo, stub_fetcher, monkeypatch):
    folder = tmp_path / "b-stage"
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "INDEXER_VERSION", "4")
        patch.setattr(store, "INDEXER_VERSION", "4")
        fixture_builder.build_database(folder)
    db = folder / "genshin.db"
    store.build_runtime_layer(db, object_repo, version_line="beta", upstream_commit="b" * 40)
    beta_before = rows(db, "runtime_object_reference", "WHERE version_line='beta'")
    for i in range(17):
        add_annotation(db, Annotation(target_type="character", target_id=90000001, content=f"保留{i}", status="approved"))
    manual = rows(db, "annotation", "ORDER BY id")
    with closing(sqlite3.connect(db)) as conn:
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == "4"
    result = fixture_builder.build_database(folder)
    assert result.updated and result.old_version == result.new_version
    assert rows(db, "annotation", "ORDER BY id") == manual
    assert rows(db, "runtime_object_reference", "WHERE version_line='beta'") == beta_before
    assert not fixture_builder.build_database(folder).updated
    assert rows(db, "runtime_source", "WHERE source_path=?", (catalog.GADGET_EXCEL_PATH,))
    assert not rows(db, "raw_table", "WHERE source_file=?", (catalog.GADGET_EXCEL_PATH,))


def test_exact_query_plan_uses_identity_and_object_indexes(object_db):
    with closing(sqlite3.connect(object_db)) as conn:
        identity = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM runtime_definition WHERE version_line=? AND definition_kind=? AND exact_key_json=?", ("release", "gadget_record", "101")).fetchall()
        objects = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM runtime_object_reference WHERE version_line=? AND from_definition_id=?", ("release", "anything")).fetchall()
    assert "exact_key_json=?" in repr(identity)
    assert "from_definition_id=?" in repr(objects)


def test_index5_requires_its_object_table(object_db):
    with closing(sqlite3.connect(object_db)) as conn, conn:
        conn.execute("DROP TABLE runtime_object_reference")
        assert store.runtime_datasets(conn)["runtime_config"]["lines"]["release"]["state"] == "invalid"
        with pytest.raises(catalog.RuntimeConfigError):
            store.validate_runtime(conn, "release", deep=True)


def test_index5_rejects_rehashed_manifest_without_required_gadget_source(object_db):
    with closing(sqlite3.connect(object_db)) as conn, conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT validation_json FROM data_component WHERE component='runtime_config' AND version_line='release'").fetchone()
        summary = json.loads(row[0])
        conn.execute("UPDATE runtime_source SET source_family='excel' WHERE source_path=?", (catalog.GADGET_EXCEL_PATH,))
        files = {r['source_path']: {"family": r['source_family'], "sha256": r['source_sha256'], "size_bytes": r['source_size_bytes']}
                 for r in conn.execute("SELECT * FROM runtime_source WHERE version_line='release' ORDER BY source_path")}
        body = {"profile_id": summary['profile_id'], "profile_version": summary['profile_version'], "indexer_version": "5",
                "files": files, "total_bytes": sum(v['size_bytes'] for v in files.values())}
        summary['manifest_fingerprint'] = catalog.fingerprint(body)
        conn.execute("UPDATE data_component SET validation_json=? WHERE component='runtime_config' AND version_line='release'", (catalog.dumps(summary),))
        with pytest.raises(catalog.RuntimeConfigError, match="GadgetExcel"):
            store.validate_runtime(conn, "release")
