"""Independent EC16a indexing counterexamples; never opens the installed DB."""

import copy
import sqlite3
from contextlib import closing

import pytest

from genshin_db import runtime_catalog as catalog
from genshin_db import runtime_store as store
from genshin_db.runtime_queries import query_runtime
from tests.test_pipeline import fake_repo  # noqa: F401
from tests.test_runtime_object_index import build, rows, write

# ruff: noqa: F811
MATERIAL = "ExcelBinOutput/MaterialExcelConfigData.json"
ABILITY = "BinOutput/Ability/Temp/IndependentEnergy.json"
GADGET = "BinOutput/Gadget/IndependentEnergy.json"


def material(ident=2021, element="7", same="3", different="1"):
    return {
        "id": ident, "itemType": "ITEM_MATERIAL", "materialType": "MATERIAL_ADSORBATE",
        "useTarget": "ITEM_USE_TARGET_CUR_TEAM", "gadgetId": 700001,
        "itemUse": [
            {"useOp": "ITEM_USE_ADD_ELEM_ENERGY", "useParam": [element, same, different, "", ""]},
            {"useParam": ["", "", ""]}, {"useParam": ["", ""]},
        ],
    }


def ability():
    return [{"Default": {
        "abilityName": "ReviewEnergyOwner",
        "onAdded": [
            {"$type": "GenerateElemBall", "configID": 2021, "baseEnergy": 9, "ratio": 1},
            {"$type": "GenerateElemBall", "configID": 2021, "baseEnergy": 12, "ratio": 1},
            {"$type": "CreateGadget", "gadgetID": 101},
            {"$type": "OtherOperation", "configID": 2021},
            {"$type": "GenerateElemBall", "baseEnergy": 9, "ratio": 1},
            {"$type": "GenerateElemBall", "configID": None, "baseEnergy": 9, "ratio": 1},
        ],
        "modifiers": {"ConditionalEnergy": {"onAdded": [{
            "$type": "Randomed", "chance": 0.5,
            "successActions": [{"$type": "GenerateElemBall", "configID": 2022, "baseEnergy": 3, "ratio": 4}],
            "failActions": [{"$type": "GenerateElemBall", "configID": 2022, "baseEnergy": 3, "ratio": 5}],
        }]}},
    }}]


@pytest.fixture
def energy_review_repo(fake_repo):
    write(fake_repo, MATERIAL, [material(), material(2022, "5")])
    write(fake_repo, ABILITY, ability())
    write(fake_repo, catalog.GADGET_EXCEL_PATH, [{"id": 101, "jsonName": "ReviewObject"}])
    write(fake_repo, GADGET, {"ReviewObject": {"abilities": []}})
    return fake_repo


@pytest.fixture
def energy_review_db(tmp_path, energy_review_repo):
    db = tmp_path / "energy-review.db"
    build(db, energy_review_repo)
    return db


def origin(db, suffix="/onAdded/0/configID"):
    candidates = [r for r in rows(db, "runtime_reference")
                  if r["source_path"] == ABILITY and r["json_pointer"].endswith(suffix)]
    assert len(candidates) == 1, candidates
    return candidates[0]["reference_id"]


def _without_extraction_version(items):
    return [{k: v for k, v in item.items() if k != "extraction_rule_id"} for item in items]


def test_review_material_edges_preserve_two_same_id_leaves_and_modifier_ownership(energy_review_db):
    edges = rows(energy_review_db, "runtime_material_reference", "WHERE source_path=?", (ABILITY,))
    matched = [r for r in edges if r["target_key_json"] == "2021"]
    assert len(matched) == 2
    assert len({r["reference_id"] for r in matched}) == 2
    assert len({r["legacy_reference_id"] for r in matched}) == 2
    assert len({r["resolved_definition_id"] for r in matched}) == 1
    conditional = [r for r in edges if r["target_key_json"] == "2022"]
    assert len(conditional) == 2
    definitions = {r["definition_id"]: r for r in rows(energy_review_db, "runtime_definition")}
    assert all(definitions[r["from_definition_id"]]["definition_kind"] == "modifier" for r in conditional)
    assert not any("/onAdded/3/" in r["json_pointer"] for r in edges)
    assert not any(r["json_pointer"].endswith("/onAdded/4/configID")
                   for r in rows(energy_review_db, "runtime_reference"))


def test_review_new_source_does_not_change_legacy_or_gadget_extraction_rules():
    for path, family, raw in ((ABILITY, "ability", ability()), (GADGET, "gadget", {"ReviewObject": {"abilities": []}})):
        previous = catalog.extract_definitions(path, family, raw, "same-snapshot", indexer_version="5")
        current = catalog.extract_definitions(path, family, raw, "same-snapshot", indexer_version="6")
        assert current == previous
        for old, new in zip(previous, current, strict=True):
            assert _without_extraction_version(catalog.extract_references(old, indexer_version="5")) == _without_extraction_version(
                catalog.extract_references(new, indexer_version="6"))
            assert _without_extraction_version(catalog.extract_object_references(old, indexer_version="5")) == _without_extraction_version(
                catalog.extract_object_references(new, indexer_version="6"))


def test_review_materials_do_not_enter_default_closure_or_gadget_graph(energy_review_db):
    root = next(r for r in rows(energy_review_db, "runtime_definition")
                if r["source_path"] == ABILITY and r["definition_kind"] == "ability")
    closure = query_runtime(energy_review_db, {"action": "closure", "roots": [root["definition_id"]]})
    assert "material_record" not in {r["definition_kind"] for r in closure["definitions"]}
    particles = [r for r in closure["references"] if r["field_name"] == "configID"]
    assert particles and all(r["resolution_status"] in {"external_asset", "source_empty"} for r in particles)
    assert not any(r["field_name"] == "configID" for r in rows(energy_review_db, "runtime_object_reference"))
    assert rows(energy_review_db, "runtime_source", "WHERE source_path=? AND source_family='material_excel'", (MATERIAL,))


def test_review_stored_index5_deep_validates_without_material_table_or_source(energy_review_repo, tmp_path, monkeypatch):
    db = tmp_path / "index5.db"
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "INDEXER_VERSION", "5")
        patch.setattr(store, "INDEXER_VERSION", "5")
        build(db, energy_review_repo)
    assert not rows(db, "runtime_source", "WHERE source_path=?", (MATERIAL,))
    with closing(sqlite3.connect(db)) as connection, connection:
        connection.execute("DROP TABLE runtime_material_reference")
        assert store.validate_runtime(connection, "release", deep=True)["indexer_version"] == "5"
    assert query_runtime(db, {"action": "gadget_source_profile", "gadget_id": 101})["status"] == "ready"


@pytest.mark.parametrize("bad_id", [True, 0, -1, "2021", None, 2**53])
def test_review_invalid_material_id_rebuild_is_atomic(energy_review_repo, energy_review_db, bad_id):
    before = energy_review_db.read_bytes()
    invalid = material()
    invalid["id"] = bad_id
    write(energy_review_repo, MATERIAL, [invalid])
    with pytest.raises(catalog.RuntimeConfigError):
        build(energy_review_db, energy_review_repo)
    assert energy_review_db.read_bytes() == before


def test_review_duplicate_material_id_rebuild_preserves_existing_database(energy_review_repo, energy_review_db):
    before = energy_review_db.read_bytes()
    other = copy.deepcopy(material())
    other["gadgetId"] += 1
    write(energy_review_repo, MATERIAL, [material(), other])
    with pytest.raises(catalog.RuntimeConfigError):
        build(energy_review_db, energy_review_repo)
    assert energy_review_db.read_bytes() == before


def test_review_missing_material_relation_table_is_invalid_index6(energy_review_db):
    with closing(sqlite3.connect(energy_review_db)) as connection, connection:
        connection.execute("DROP TABLE runtime_material_reference")
        with pytest.raises(catalog.RuntimeConfigError):
            store.validate_runtime(connection, "release", deep=True)


def test_review_exact_material_lookup_and_origin_query_have_sql_indexes(energy_review_db):
    with closing(sqlite3.connect(energy_review_db)) as connection:
        materials = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM runtime_definition WHERE version_line=? AND definition_kind=? AND exact_key_json=?",
            ("release", "material_record", "2021"),
        ).fetchall()
        origins = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM runtime_material_reference WHERE version_line=? AND legacy_reference_id=?",
            ("release", "anything"),
        ).fetchall()
    assert "exact_key_json=?" in repr(materials)
    assert "legacy_reference_id=?" in repr(origins)
