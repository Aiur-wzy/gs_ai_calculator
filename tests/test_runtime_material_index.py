"""Indexer 6: exact material IDs without changing either existing relation graph."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db import runtime_store as store
from genshin_db.annotation_store import Annotation, add_annotation
from genshin_db.raw_store import RAW_SOURCES
from genshin_db.runtime_queries import query_runtime
from genshin_runtime.normalization import Profile
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_object_index import build, rows, write

# ruff: noqa: F811
ABILITY = "BinOutput/Ability/Temp/MaterialFixture.json"


@pytest.fixture
def material_repo(fake_repo):
    write(fake_repo, catalog.MATERIAL_EXCEL_PATH, [
        {"id": 2021, "itemType": "ITEM_MATERIAL", "materialType": "MATERIAL_ADSORBATE",
         "gadgetId": 70610005, "useTarget": "ITEM_USE_TARGET_CUR_TEAM",
         "itemUse": [{"useOp": "ITEM_USE_ADD_ELEM_ENERGY", "useParam": ["7", "3", "1", "", ""]},
                     {"useParam": ["", "", ""]}, {"useParam": ["", ""]}]},
        {"id": 2022, "opaque": {"abilityName": "DoNotWalk", "configID": 2021}},
    ])
    write(fake_repo, catalog.GADGET_EXCEL_PATH, [{"id": 2021, "jsonName": "FixtureGadget"}])
    write(fake_repo, "BinOutput/Gadget/MaterialFixture.json", {"FixtureGadget": {"abilities": []}})
    write(fake_repo, ABILITY, [{"Default": {
        "abilityName": "ParticleCreator", "abilitySpecials": {"guard": 0},
        "onAdded": [
            {"$type": "GenerateElemBall", "configID": 2021, "baseEnergy": 9, "ratio": 1},
            {"$type": "GenerateElemBall", "configID": 2021, "baseEnergy": 12, "ratio": 1,
             "predicates": [{"$type": "UnknownPredicate", "value": False}]},
            {"$type": "CreateGadget", "gadgetID": 2021},
            {"$type": "UnknownOperation", "configID": 2021},
        ],
        "modifiers": {"Nested": {"duration": 0.2, "onAdded": [
            {"$type": "GenerateElemBall", "configID": 2022, "baseEnergy": 3, "ratio": 1},
        ]}},
    }}])
    return fake_repo


@pytest.fixture
def material_db(tmp_path, material_repo):
    db = tmp_path / "material.db"
    build(db, material_repo)
    return db


def test_material_records_preserve_rows_and_separate_ids_and_paths(material_db):
    definitions = rows(material_db, "runtime_definition", "WHERE definition_kind='material_record' ORDER BY json_pointer")
    assert [json.loads(r["exact_key_json"]) for r in definitions] == [2021, 2022]
    assert [r["json_pointer"] for r in definitions] == ["/0", "/1"]
    assert all(r["source_path"] == catalog.MATERIAL_EXCEL_PATH and r["owner_definition_id"] is None for r in definitions)
    assert not rows(material_db, "runtime_reference", "WHERE from_definition_id IN (?,?)", tuple(r["definition_id"] for r in definitions))
    assert not rows(material_db, "runtime_object_reference", "WHERE from_definition_id IN (?,?)", tuple(r["definition_id"] for r in definitions))
    assert catalog.MATERIAL_EXCEL_PATH not in RAW_SOURCES
    assert rows(material_db, "runtime_source", "WHERE source_path=?", (catalog.MATERIAL_EXCEL_PATH,))[0]["source_family"] == "material_excel"


def test_two_same_target_leaves_and_nested_owner_never_collapse(material_db):
    links = rows(material_db, "runtime_material_reference", "ORDER BY json_pointer")
    assert len(links) == 3
    assert len({r["reference_id"] for r in links}) == len({r["json_pointer"] for r in links}) == 3
    assert all(r["resolution_status"] == "resolved" for r in links)
    assert all(json.loads(r["scope_json"])["graph_scope"] == "energy_material_v1" for r in links)
    first = [r for r in links if r["target_key_json"] == "2021"]
    assert first[0]["from_definition_id"] == first[1]["from_definition_id"]
    assert first[0]["resolved_definition_id"] == first[1]["resolved_definition_id"]
    nested = next(r for r in links if "/modifiers/" in r["json_pointer"])
    definition = rows(material_db, "runtime_definition", "WHERE definition_id=?", (nested["from_definition_id"],))[0]
    assert definition["definition_kind"] == "modifier"
    assert json.loads(nested["scope_json"])["owner_definition_id"] == definition["owner_definition_id"]
    legacy = rows(material_db, "runtime_reference", "WHERE source_path=?", (ABILITY,))
    assert {r["legacy_reference_id"] for r in links} <= {r["reference_id"] for r in legacy}
    assert not {r["reference_id"] for r in links} & {r["reference_id"] for r in legacy}


def test_old_closure_and_gadget_graph_do_not_visit_materials(material_db):
    creator = rows(material_db, "runtime_definition", "WHERE exact_key_json=?", ('"ParticleCreator"',))[0]
    closure = query_runtime(material_db, {"action": "closure", "roots": [creator["definition_id"]]})
    assert {r["definition_kind"] for r in closure["definitions"]} == {"ability", "modifier"}
    external = [r for r in closure["references"] if r["relation_kind"] == "external_asset"]
    assert len(external) == 4 and all(r["resolution_status"] == "external_asset" for r in external)
    objects = rows(material_db, "runtime_object_reference", "WHERE source_path=?", (ABILITY,))
    assert len(objects) == 1 and objects[0]["field_name"] == "gadgetID"
    assert all(r["target_kind"] != "material_record" for r in objects)


@pytest.mark.parametrize("value,expected", [(None, "null"), ("", "empty"), (True, "present"),
                                           (0, "present"), (-1, "present"), (2**53, "present"),
                                           ("2021", "present"), (2021.0, "present"), (404, "present")])
def test_invalid_or_missing_targets_are_preserved(material_repo, tmp_path, value, expected):
    write(material_repo, ABILITY, [{"Default": {"abilityName": "Bad", "onAdded": [
        {"$type": "GenerateElemBall", "configID": value}, {"$type": "GenerateElemBall"}]}}])
    db = tmp_path / "values.db"
    build(db, material_repo)
    links = rows(db, "runtime_material_reference", "ORDER BY source_order")
    assert links[0]["presence_status"] == expected
    assert json.loads(links[0]["target_key_json"]) == value
    assert links[0]["resolution_status"] == ("source_empty" if expected != "present" else
                                              "missing" if value == 404 else "invalid_reference_form")
    assert links[0]["legacy_reference_id"] is not None
    assert links[1]["presence_status"] == "missing" and links[1]["legacy_reference_id"] is None
    assert len(rows(db, "runtime_reference", "WHERE source_path=?", (ABILITY,))) == 1
    with closing(sqlite3.connect(db)) as conn:
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == "7"


@pytest.mark.parametrize("bad", [True, 0, -1, "1", 1.0, None, 2**53])
def test_invalid_material_row_ids_fail_build(bad):
    with pytest.raises(catalog.RuntimeConfigError, match="正整数ID"):
        catalog.extract_definitions(catalog.MATERIAL_EXCEL_PATH, "material_excel", [{"id": bad}], "s")


@pytest.mark.parametrize("bad", [{}, {"id": 1}, None])
def test_material_root_must_be_array(bad):
    with pytest.raises(catalog.RuntimeConfigError, match="MaterialExcel"):
        catalog.extract_definitions(catalog.MATERIAL_EXCEL_PATH, "material_excel", bad, "s")


def test_exact_path_and_duplicate_rows_required(material_db, material_repo):
    before = material_db.read_bytes()
    with pytest.raises(catalog.RuntimeConfigError, match="来源路径"):
        catalog.extract_definitions("Wrong.json", "material_excel", [], "s")
    write(material_repo, catalog.MATERIAL_EXCEL_PATH, [{"id": 2021}, {"id": 2021}])
    with pytest.raises(catalog.RuntimeConfigError, match="ID重复"):
        build(material_db, material_repo)
    assert material_db.read_bytes() == before


def test_normalized_material_paths_do_not_invent_a_missing_raw_token():
    profile = Profile("local", ("a" * 40,), {"ABIL": "abilityName", "MODS": "modifiers", "ITEM": "configID"},
                      {"BALL": "GenerateElemBall"})
    defs = catalog.extract_definitions(ABILITY, "ability", [{"Default": {"ABIL": "P", "MODS": {"ITEM": {
        "onAdded": [{"$type": "BALL", "ITEM": 2021}, {"$type": "BALL"}]}}}}], "s", normalization=profile)
    assert catalog.extract_material_references(defs[0], normalization=profile) == []
    links = catalog.extract_material_references(defs[1], normalization=profile)
    assert links[0]["json_pointer"] == "/0/Default/MODS/ITEM/onAdded/0/ITEM"
    assert links[1]["json_pointer"] == "/0/Default/MODS/ITEM/onAdded/1/configID"
    assert links[1]["legacy_reference_id"] is None


@pytest.mark.parametrize("version", ["2", "3", "4", "5"])
def test_old_versions_read_and_deep_check_without_material_layer(material_repo, tmp_path, monkeypatch, version):
    db = tmp_path / f"old-{version}.db"
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "INDEXER_VERSION", version)
        patch.setattr(store, "INDEXER_VERSION", version)
        build(db, material_repo)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("DROP TABLE runtime_material_reference")
        if version != "5":
            conn.execute("DROP TABLE runtime_object_reference")
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == version
        assert conn.execute("SELECT COUNT(*) FROM runtime_source WHERE source_path=?", (catalog.MATERIAL_EXCEL_PATH,)).fetchone()[0] == 0
        definition = {"definition_id": "absent"}
        with pytest.raises(catalog.RuntimeConfigError) as exc:
            store.validate_definition_material_references(conn, "release", definition, {})
        assert exc.value.reason == "material_index_upgrade_required"


@pytest.mark.parametrize("field,value", [("legacy_reference_id", None), ("scope_json", "{}"),
                                         ("candidate_ids_json", "[]"), ("source_order", 42),
                                         ("target_key_json", "999999"), ("json_pointer", "/bad")])
def test_deep_detects_forged_material_edges(material_db, field, value):
    with closing(sqlite3.connect(material_db)) as conn, conn:
        ident = conn.execute("SELECT reference_id FROM runtime_material_reference LIMIT 1").fetchone()[0]
        conn.execute(f"UPDATE runtime_material_reference SET {field}=? WHERE reference_id=?", (value, ident))
    with closing(sqlite3.connect(material_db)) as conn, pytest.raises(catalog.RuntimeConfigError):
        store.validate_runtime(conn, "release", deep=True)


def test_index6_requires_material_table_and_manifest(material_db):
    with closing(sqlite3.connect(material_db)) as conn, conn:
        conn.execute("DROP TABLE runtime_material_reference")
        assert store.runtime_datasets(conn)["runtime_config"]["lines"]["release"]["state"] == "invalid"
        with pytest.raises(catalog.RuntimeConfigError):
            store.validate_runtime(conn, "release")


def test_new_material_lookup_indexes_are_exact(material_db):
    with closing(sqlite3.connect(material_db)) as conn:
        defs = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM runtime_definition WHERE version_line=? AND definition_kind=? AND exact_key_json=?", ("release", "material_record", "2021")).fetchall()
        refs = conn.execute("EXPLAIN QUERY PLAN SELECT * FROM runtime_material_reference WHERE version_line=? AND legacy_reference_id=?", ("release", "origin")).fetchall()
    assert "exact_key_json=?" in repr(defs)
    assert "legacy_reference_id=?" in repr(refs)


def test_same_commit_b_upgrade_once_preserves_seventeen_manual_and_other_line(tmp_path, material_repo, stub_fetcher, monkeypatch):
    folder = tmp_path / "upgrade"
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "INDEXER_VERSION", "5")
        patch.setattr(store, "INDEXER_VERSION", "5")
        fixture_builder.build_database(folder)
    db = folder / "genshin.db"
    store.build_runtime_layer(db, material_repo, version_line="beta", upstream_commit="b" * 40)
    beta = {t: rows(db, t, "WHERE version_line='beta' ORDER BY 1") for t in store.tables_for_indexer("7")}
    for i in range(17):
        add_annotation(db, Annotation(target_type="character", target_id=90000001, content=f"人工{i}", status="approved"))
    manual = rows(db, "annotation", "ORDER BY id")
    with closing(sqlite3.connect(db)) as conn:
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == "5"
    report = fixture_builder.build_database(folder)
    assert report.updated and report.old_version == report.new_version
    assert rows(db, "annotation", "ORDER BY id") == manual
    assert {t: rows(db, t, "WHERE version_line='beta' ORDER BY 1") for t in store.tables_for_indexer("7")} == beta
    with closing(sqlite3.connect(db)) as conn:
        assert store.validate_runtime(conn, "release", deep=True)["indexer_version"] == "7"
    assert not fixture_builder.build_database(folder).updated
    assert not rows(db, "raw_table", "WHERE source_file=?", (catalog.MATERIAL_EXCEL_PATH,))
