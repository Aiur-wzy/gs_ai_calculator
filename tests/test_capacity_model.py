# ruff: noqa: F811 - pytest fixture injection uses imported names.
import copy
import sqlite3
from contextlib import closing

import pytest
from capacity_fixture import context, model_record, selection

from genshin_db import runtime_action_capacity as capacity
from genshin_db.runtime_queries import query_runtime
from genshin_db.semantic_store import HEAD_TABLE, read_record, review_record
from genshin_runtime.action_capacity_model import validate_model
from genshin_runtime.mechanism_semantics import validate_record
from tests.action_resource_fixtures import (
    action_resource_synthetic_db_only,  # noqa: F401
)
from tests.test_action_resource_sources import (  # noqa: F401
    fixture_record,
    install,
    source_db,
)
from tests.test_pipeline import fake_repo  # noqa: F401

pytestmark = pytest.mark.usefixtures("action_resource_synthetic_db_only")


@pytest.fixture
def ready(source_db, fake_repo):
    import json

    from genshin_db.equipment import build_equipment_layer
    from genshin_db.raw_store import build_raw_layer
    from tests.test_action_resource_sources import COMMIT
    from tests.test_runtime_object_index import build
    weapon_path = fake_repo / "ExcelBinOutput/WeaponExcelConfigData.json"
    weapon_rows = json.loads(weapon_path.read_text("utf8"))
    weapon_rows.append({**copy.deepcopy(weapon_rows[0]), "id": 11402})
    weapon_rows[0]["skillAffix"] = [0, 0]
    weapon_rows[0]["weaponType"] = "WEAPON_POLE"
    weapon_path.write_text(json.dumps(weapon_rows), "utf8")
    build(source_db, fake_repo, upstream_commit=COMMIT)
    raw = build_raw_layer(fake_repo, source_db)
    build_equipment_layer(source_db, "release", upstream_commit=COMMIT, manifest=raw.manifest)
    from genshin_db.annotation_store import create_annotation_tables
    from genshin_db.extractor import build_curated_layer
    build_curated_layer(source_db, "engineering", "release", upstream_commit=COMMIT)
    create_annotation_tables(source_db)
    delta = fixture_record(source_db)
    install(source_db, delta)
    model = model_record(source_db, delta)
    install(source_db, model)
    return source_db, model, delta


def query(ready, constellation=1):
    db, model, _ = ready
    return query_runtime(db, {"action": "action_capacity_profile", "selection": selection(constellation),
                              "context": context(), "model_ref": {"id": model["id"], "revision": model["revision"]}})


@pytest.mark.parametrize("constellation,expected", [(0, 1), (1, 2)])
def test_actual_sqlite_full_model(ready, constellation, expected):
    before = ready[0].read_bytes()
    output = query(ready, constellation)
    assert output["status"] == "ready" and output["capacity"] == expected
    assert output["native_dispatch"] == "unproven" and output["execution_authority"] is False
    assert len(output["competitor_inventory"]["entries"]) == 2
    assert ready[0].read_bytes() == before


@pytest.mark.parametrize("where", ["model", "delta"])
def test_same_revision_revocation_blocks_whole_model(ready, where):
    db, model, delta = ready
    old = query(ready)
    record = model if where == "model" else delta
    with closing(sqlite3.connect(db)) as conn:
        stored = read_record(conn, "release", "fact", record["id"], 1)
    review_record(db, version_line="release", record_kind="fact", record_id=record["id"], revision=1,
                  status="revoked", reviewer="engineering", reviewed_at="2026-10-08T01:00:00+00:00",
                  expected_review_fingerprint=stored["review_fingerprint"])
    current = query(ready, 0)
    assert current["status"] == "pending" and current["capacity"] is None
    assert old["model_receipt"] != current["model_receipt"] or old["competitor_inventory"] != current["competitor_inventory"]


def test_current_higher_pending_model_cannot_borrow_old(ready):
    db, old, _ = ready
    new = copy.deepcopy(old)
    new["revision"] = new["payload"]["revision"] = 2
    new["review"] = {"status": "pending", "reviewer": None, "reviewed_at": None}
    install(db, new, expected=1)
    assert query(ready)["reason"] == "head_changed"


@pytest.mark.parametrize("change", ["bool_base", "float_capacity", "variant", "unknown", "missing_case", "duplicate_source", "missing_text_sha"])
def test_strict_model_contract(ready, change):
    c = copy.deepcopy(ready[1]["payload"]["claim"])
    if change == "bool_base": c["base"]["value"] = True
    elif change == "float_capacity": c["reviewed_cases"][0]["capacity"] = 1.0
    elif change == "variant": c["domain"]["variant"] = "press"
    elif change == "unknown": c["allow_unknown"] = True
    elif change == "missing_case": c["reviewed_cases"].pop()
    elif change == "duplicate_source": c["materials"]["sources"].append(c["materials"]["sources"][0])
    else: c["text_inventory"][0]["text_sha256"] = None
    with pytest.raises(ValueError): validate_model(c)


@pytest.mark.parametrize("path", ["node", "parent", "text", "source"])
def test_full_material_mismatch_is_rejected(ready, monkeypatch, path):
    original = capacity.fresh_model_material
    def corrupted(*args):
        material = original(*args)
        if path == "node": material["nodes"][0]["raw"]["extra"] = 1
        elif path == "parent": material["talent_parent"]["unexpected"] = []
        elif path == "text": material["texts"][0]["text"] += "changed"
        else: material["sources"][0]["source_sha256"] = "0" * 64
        capacity.verify_material(args[2]["payload"]["claim"], material)
        return material
    monkeypatch.setattr(capacity, "fresh_model_material", corrupted)
    with pytest.raises(ValueError): query(ready)


def test_new_unreviewed_competitor_blocks_and_changes_inventory(ready):
    before = query(ready)
    other = copy.deepcopy(ready[2])
    other["id"] = other["payload"]["id"] = "fixture.extra.pending"
    other["review"] = {"status": "pending", "reviewer": None, "reviewed_at": None}
    install(ready[0], other)
    current = query(ready)
    assert current["reason"] == "capacity_competitor_unresolved" and current["capacity"] is None
    assert current["capacity_competitor_inventory_fingerprint"] != before["capacity_competitor_inventory_fingerprint"]


def test_duplicate_operation_not_added_as_another_use(ready):
    other = copy.deepcopy(ready[2])
    other["id"] = other["payload"]["id"] = "fixture.extra.approved"
    install(ready[0], other)
    assert query(ready)["status"] == "pending"


def test_orphan_current_head_cannot_disappear_in_join(ready):
    with closing(sqlite3.connect(ready[0])) as conn, conn:
        conn.execute(f"INSERT INTO {HEAD_TABLE} VALUES (?,?,?,?,?)", ("release", "fact", "fixture.orphan", 1, "now"))
    with pytest.raises(ValueError, match="缺少完整记录|指向不存在的修订"): query(ready)


def test_fact_limit_is_before_material_loading(ready, monkeypatch):
    for i in range(31):
        row = copy.deepcopy(ready[2]); row["id"] = row["payload"]["id"] = f"fixture.extra.{i}"
        install(ready[0], row)
    monkeypatch.setattr(capacity, "fresh_model_material", lambda *a: pytest.fail("excess inventory entered full material"))
    with pytest.raises(ValueError, match="32条"): query(ready)


def test_model_requires_exact_source_evidence_wrapper(ready):
    record = copy.deepcopy(ready[1]); record["evidence"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError): validate_record(record, "fact")
