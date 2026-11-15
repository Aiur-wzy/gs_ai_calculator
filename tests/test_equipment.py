"""Equipment provenance, growth, annotation boundaries and atomic publication."""

# ruff: noqa: F811 - pytest uses imported fixture names as test parameters.

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from genshin_db import pipeline, queries
from genshin_db.annotation_store import Annotation, add_annotation
from genshin_db.equipment import (
    EquipmentError,
    build_equipment_layer,
    equipment_datasets,
)
from genshin_db.raw_store import build_raw_layer
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401


@pytest.fixture
def equipment_db(tmp_path, stub_fetcher):
    fixture_builder.build_database(tmp_path)
    return tmp_path / "genshin.db"


def test_growth_has_explicit_boundary_and_no_passive_double_count(equipment_db):
    before = queries.get_weapon_stats(equipment_db, 11401, 20, 0)
    after = queries.get_weapon_stats(equipment_db, 11401, 20, 1)
    assert [p["value"] for p in before["properties"]] == [120, 0.2]
    assert [p["value"] for p in after["properties"]] == [145, 0.2]
    assert [p["prop_type"] for p in after["properties"]] == ["FIGHT_PROP_BASE_ATTACK", "FIGHT_PROP_CHARGE_EFFICIENCY"]
    with pytest.raises(EquipmentError, match="不匹配"):
        queries.get_weapon_stats(equipment_db, 11401, 19, 1)
    with pytest.raises(EquipmentError, match="不匹配"):
        queries.get_weapon_stats(equipment_db, 11401, 41, 1)


def test_four_growth_families_consume_selected_cumulative_promotion_once(fake_repo, stub_fetcher, tmp_path):
    base = json.loads((fake_repo / "ExcelBinOutput/WeaponExcelConfigData.json").read_text("utf8"))[0]
    expected = (674, 674, 454, 564)
    weapons, curves = [], []
    for i, final in enumerate(expected):
        weapon = {**base, "id": 11401 + i, "weaponProp": [
            {"propType": "FIGHT_PROP_BASE_ATTACK", "type": f"ATK{i}", "initValue": 40}]}
        weapons.append(weapon)
        curves.append({"type": f"ATK{i}", "arith": "ARITH_MULTI", "value": (final - 100) / 40})
    sources = {"Weapon": weapons, "WeaponCurve": [{"level": level,
        "curveInfos": [{**curve, "value": curve["value"] * level / 90} for curve in curves]}
        for level in range(1, 91)],
        "WeaponPromote": [{"weaponPromoteId": 1, "promoteLevel": i, "unlockMaxLevel": max_level,
            "addProps": [{"propType": "FIGHT_PROP_BASE_ATTACK", "value": cumulative}]}
            for i, (max_level, cumulative) in enumerate(zip((20, 40, 50, 60, 70, 80, 90), (0, 25, 40, 55, 70, 85, 100)))]}
    for name, rows in sources.items():
        (fake_repo / f"ExcelBinOutput/{name}ExcelConfigData.json").write_text(json.dumps(rows), encoding="utf8")
    fixture_builder.build_database(tmp_path)
    for i, final in enumerate(expected):
        prop = queries.get_weapon_stats(tmp_path / "genshin.db", 11401 + i, 90, 6)["properties"][0]
        assert prop["value"] == pytest.approx(final)
        assert prop["calculation"]["promotion_add"] == 100


def test_precise_affix_id_sources_and_zero_slots(equipment_db):
    data = queries.get_weapon_detail(equipment_db, 11401)
    effect = data["effects"][0]["refinements"][0]
    assert effect["affix_id"] == 917 and effect["refinement"] == 1
    assert effect["param_list"] == [0.2, 0, 2, 0]
    assert effect["add_props"] == [{}, {"propType": "FIGHT_PROP_HP_PERCENT", "value": 0.2}, {}]
    assert effect["desc_source"]["record_id"] == 917
    assert effect["desc_source"]["upstream_commit"] == "a" * 40
    assert effect["desc_template"] == "造成{param1:F1P}伤害"
    assert len(queries.get_equip_affix_detail(equipment_db, 917)["context_refs"]) == 2
    assert data["weapon"]["available_states"] == [
        {"ascension": 0, "min_level": 1, "max_level": 20},
        {"ascension": 1, "min_level": 20, "max_level": 40}]


def test_artifact_levels_pools_null_and_substat_identity(equipment_db):
    assert queries.get_artifact_main_stats(equipment_db, 71543, 0, 14001)["stat"]["value"] == 701
    assert queries.get_artifact_main_stats(equipment_db, 71543, 20, 14001)["stat"]["value"] == 721
    missing = queries.get_artifact_main_stats(equipment_db, 71543, 0, 14002)
    assert missing["stat"]["value"] is None and missing["stat"]["value_status"] == "source_missing"
    assert missing["warnings"][0]["code"] == "source_missing"
    detail = queries.get_artifact_detail(equipment_db, 71543)
    assert detail["artifact"]["add_prop_levels"] == [5, 9, 13, 17, 21, 25]
    assert detail["artifact"]["add_prop_display_levels"] == [4, 8, 12, 16, 20]
    assert queries.list_artifact_substats(equipment_db, 501)["items"][0]["value"] == 0.0272
    for level, prop in [(21, 14001), (0, 999), (0, 0)]:
        with pytest.raises(ValueError):
            queries.get_artifact_main_stats(equipment_db, 71543, level, prop)


def test_pagination_catalog_and_literal_name(equipment_db):
    assert queries.list_weapons(equipment_db)["total"] == 1
    page = queries.list_weapons(equipment_db, page=2, page_size=1)
    assert page["total"] == 1 and page["items"] == []
    assert queries.list_weapons(equipment_db, name="%")["items"] == []
    assert queries.list_artifact_sets(equipment_db, rarity=5)["total"] == 1
    assert queries.list_artifacts(equipment_db, rarity=4)["total"] == 0
    assert queries.list_artifact_main_props(equipment_db, 4000, prop_type="FIGHT_PROP_DEFENSE")["total"] == 0
    with pytest.raises(EquipmentError) as error:
        queries.list_artifact_substats(equipment_db, 999)
    assert error.value.status == 404


@pytest.mark.parametrize("value", [False, True, 1.0, -1, 0, 2**53, "١"])
def test_public_ids_are_strict(equipment_db, value):
    with pytest.raises(ValueError):
        queries.get_weapon_detail(equipment_db, value)


def test_readiness_legacy_incompatible_and_damage(equipment_db):
    assert queries.get_meta(equipment_db)["datasets"]["equipment"]["lines"]["release"]["state"] == "ready"
    with pytest.raises(EquipmentError) as missing:
        queries.get_weapon_detail(equipment_db, 11401, "beta")
    assert missing.value.reason == "dataset_unavailable"
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        conn.execute("UPDATE meta SET value='999' WHERE key='equipment_schema_version'")
    with pytest.raises(EquipmentError) as newer:
        queries.get_weapon_detail(equipment_db, 11401)
    assert newer.value.reason == "unsupported_schema"
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        conn.execute("UPDATE meta SET value='1' WHERE key='equipment_schema_version'")
        conn.execute("DELETE FROM weapon_effect")
    with pytest.raises(EquipmentError) as invalid:
        queries.get_weapon_detail(equipment_db, 11401)
    assert invalid.value.reason == "dataset_invalid"


def test_annotations_exact_targets_lines_and_character_isolation(equipment_db):
    for kind, object_id, content in [("weapon", 11401, "weapon"), ("equip_affix", 917, "effect"),
                                      ("artifact_set", 15, "set"), ("artifact", 71543, "piece")]:
        add_annotation(equipment_db, Annotation(kind, object_id, content, status="approved"))
    assert {a["content"] for a in queries.get_weapon_detail(equipment_db, 11401)["annotations"]} == {"weapon", "effect"}
    assert {a["content"] for a in queries.get_artifact_set_detail(equipment_db, 15)["annotations"]} == {"set", "effect"}
    assert {a["content"] for a in queries.get_artifact_detail(equipment_db, 71543)["annotations"]} == {"piece"}
    assert queries.get_character_detail(equipment_db, 90000001)["annotations"] == []
    with pytest.raises(ValueError, match="目标不存在"):
        queries.create_annotation(equipment_db, "weapon", 917, "wrong domain")
    with pytest.raises(ValueError, match="目标不存在"):
        queries.create_annotation(equipment_db, "weapon", 11401, "wrong line", version_line="beta")
    report = fixture_builder.build_database(equipment_db.parent, force=True)
    assert report.orphan_annotations == 0


def test_old_equipment_fixture_rebuilds_by_shared_components(equipment_db):
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        conn.execute("DELETE FROM meta WHERE key='equipment_schema_version'")
        conn.execute("DELETE FROM data_component")
        conn.execute("UPDATE character SET name='old extraction'")
    original = hashlib.sha256(equipment_db.read_bytes()).hexdigest()
    report = fixture_builder.build_database(equipment_db.parent)
    assert report.updated and report.action == "rebuilt" and report.old_version == report.new_version
    assert queries.get_character_detail(equipment_db, 90000001)["character"]["name"] == "甲"
    unchanged = fixture_builder.build_database(equipment_db.parent)
    assert not unchanged.updated and unchanged.datasets["equipment"]["lines"]["release"]["state"] == "ready"
    assert original != hashlib.sha256(equipment_db.read_bytes()).hexdigest()


def test_candidate_receipt_failure_never_replaces_fixture(equipment_db, monkeypatch):
    def fail(*args):
        raise OSError("receipt unavailable")
    monkeypatch.setattr(pipeline, "_write_json_atomic", fail)
    before = queries.get_weapon_detail(equipment_db, 11401)
    with pytest.raises(OSError, match="receipt unavailable"):
        fixture_builder.build_database(equipment_db.parent, force=True)
    assert queries.get_weapon_detail(equipment_db, 11401) == before


def test_concurrent_fixture_edit_is_refused_and_other_line_stays_intact(equipment_db, fake_repo, monkeypatch):
    manifest = pipeline._source_manifest(fake_repo)
    build_equipment_layer(equipment_db, "beta", upstream_commit="beta-snapshot", manifest=manifest)
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        beta = conn.execute("SELECT * FROM weapon WHERE version_line='beta'").fetchall()
        component = conn.execute("SELECT * FROM data_component WHERE version_line='beta'").fetchall()
    original = pipeline.build_equipment_layer
    def during_build(*args, **kwargs):
        result = original(*args, **kwargs)
        queries.create_annotation(equipment_db, "weapon", 11401, "latest manual")
        return result
    monkeypatch.setattr(pipeline, "build_equipment_layer", during_build)
    with pytest.raises(pipeline.PipelineError, match="base_db"):
        fixture_builder.build_database(equipment_db.parent, force=True)
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        assert conn.execute("SELECT * FROM weapon WHERE version_line='beta'").fetchall() == beta
        assert conn.execute("SELECT * FROM data_component WHERE version_line='beta'").fetchall() == component
        assert conn.execute("SELECT content FROM annotation").fetchone()[0] == "latest manual"


@pytest.mark.parametrize("mutation", ["bad_affix", "bad_curve", "bad_codex", "nan", "duplicate"])
def test_invalid_source_rolls_back_equipment(equipment_db, fake_repo, mutation):
    names = {"bad_affix": "Weapon", "bad_curve": "WeaponCurve", "bad_codex": "ReliquaryCodex", "nan": "Weapon", "duplicate": "EquipAffix"}
    file = fake_repo / f"ExcelBinOutput/{names[mutation]}ExcelConfigData.json"
    rows = json.loads(file.read_text("utf8"))
    if mutation == "bad_affix":
        rows[0]["skillAffix"] = [999]
    elif mutation == "bad_curve":
        rows[0]["curveInfos"][0]["arith"] = "ARITH_UNKNOWN"
    elif mutation == "bad_codex":
        rows[0]["level"] = 4
    elif mutation == "nan":
        rows[0]["weaponProp"][0]["initValue"] = float("nan")
    else:
        rows.append(rows[0])
    file.write_text(json.dumps(rows), "utf8")
    with pytest.raises((ValueError, sqlite3.IntegrityError)):
        fixture_builder.build_database(equipment_db.parent, force=True)
    assert queries.get_weapon_stats(equipment_db, 11401, 20, 1)["properties"][0]["value"] == 145


def test_original_file_hash_and_all_required_sources(equipment_db, fake_repo):
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        rows = conn.execute("SELECT source_file,sha256 FROM source_manifest WHERE component='equipment' AND version_line='release'").fetchall()
        assert len(rows) == 20
        assert all(digest == hashlib.sha256((fake_repo / path).read_bytes()).hexdigest() for path, digest in rows)
        assert equipment_datasets(conn)["equipment"]["lines"]["release"]["state"] == "ready"
    (fake_repo / "ExcelBinOutput/WeaponCodexExcelConfigData.json").unlink()
    with pytest.raises(pipeline.PipelineError, match="缺失"):
        fixture_builder.build_database(equipment_db.parent)


def test_partial_direct_build_is_transactional(equipment_db, fake_repo):
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        before = conn.execute("SELECT * FROM weapon").fetchall()
    file = fake_repo / "ExcelBinOutput/ReliquarySetExcelConfigData.json"
    file.write_text('[{"setId":999,"setNeedNum":[2]}]', "utf8")
    stats = build_raw_layer(fake_repo, equipment_db)
    with pytest.raises(EquipmentError):
        build_equipment_layer(equipment_db, "release", upstream_commit="new", manifest=stats.manifest)
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        assert conn.execute("SELECT * FROM weapon").fetchall() == before


def test_unknown_schema_refuses_all_writes_before_fetch(equipment_db, monkeypatch):
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        conn.execute("UPDATE meta SET value='2' WHERE key='equipment_schema_version'")
    digest = hashlib.sha256(equipment_db.read_bytes()).hexdigest()
    monkeypatch.setattr(pipeline.fetcher, "ensure_upstream", lambda _: pytest.fail("must not fetch"))
    with pytest.raises(pipeline.PipelineError, match="版本不受支持"):
        fixture_builder.build_database(equipment_db.parent, force=True)
    assert digest == hashlib.sha256(equipment_db.read_bytes()).hexdigest()


@pytest.mark.parametrize("field,value", [("param_list_json", "bad JSON"), ("add_props_json", '{"wrong":"shape"}')])
def test_published_malformed_arrays_report_dataset_invalid(equipment_db, field, value):
    with closing(sqlite3.connect(equipment_db)) as conn, conn:
        conn.execute(f"UPDATE equip_affix SET {field}=?", (value,))
    with pytest.raises(EquipmentError) as error:
        queries.get_equip_affix_detail(equipment_db, 917)
    assert error.value.reason == "dataset_invalid" and error.value.status == 500


def test_source_change_same_commit_rebuilds(equipment_db, fake_repo):
    path = fake_repo / "ExcelBinOutput/WeaponExcelConfigData.json"
    rows = json.loads(path.read_text("utf8"))
    rows[0]["weaponProp"][0]["initValue"] = 50
    path.write_text(json.dumps(rows), "utf8")
    report = fixture_builder.build_database(equipment_db.parent)
    assert report.updated
    assert queries.get_weapon_stats(equipment_db, 11401, 20, 1)["properties"][0]["value"] == 175


def test_manual_fixture_edits_are_not_silently_merged_into_candidate(equipment_db, monkeypatch):
    first = queries.create_annotation(equipment_db, "weapon", 11401, "change")
    second = queries.create_annotation(equipment_db, "artifact", 71543, "delete")
    original = pipeline.build_equipment_layer
    def during_build(*args, **kwargs):
        result = original(*args, **kwargs)
        queries.update_annotation(equipment_db, first["id"], {"content": "changed"})
        queries.delete_annotation(equipment_db, second["id"])
        return result
    monkeypatch.setattr(pipeline, "build_equipment_layer", during_build)
    with pytest.raises(pipeline.PipelineError, match="base_db"):
        fixture_builder.build_database(equipment_db.parent, force=True)
    assert [a["content"] for a in queries.list_annotations(equipment_db, status=None)["items"]] == ["changed"]


def test_shared_equipment_annotation_survives_when_only_other_line_remains(equipment_db, fake_repo):
    build_equipment_layer(equipment_db, "beta", upstream_commit="beta", manifest=pipeline._source_manifest(fake_repo))
    shared = queries.create_annotation(equipment_db, "weapon", 11401, "shared")
    release = queries.create_annotation(equipment_db, "weapon", 11401, "release", version_line="release")
    for ann in (shared, release):
        queries.update_annotation(equipment_db, ann["id"], {"status": "approved"})
    source = fake_repo / "ExcelBinOutput/WeaponExcelConfigData.json"
    records = json.loads(source.read_text("utf8")); records[0]["id"] = 11402
    source.write_text(json.dumps(records), "utf8")
    codex = fake_repo / "ExcelBinOutput/WeaponCodexExcelConfigData.json"
    rows = json.loads(codex.read_text("utf8")); rows[0]["weaponId"] = 11402
    codex.write_text(json.dumps(rows), "utf8")
    report = fixture_builder.build_database(equipment_db.parent, force=True)
    assert report.orphan_annotations == 0
    annotations = {a["content"]: a["status"] for a in queries.list_annotations(equipment_db, status=None)["items"]}
    assert annotations == {"shared": "approved", "release": "approved"}


def test_read_session_validates_once_and_never_caches_across_updates(equipment_db, monkeypatch):
    from genshin_db import equipment_queries
    original = equipment_queries.validate_equipment
    calls = []
    def counted(conn, line):
        calls.append(line)
        return original(conn, line)
    monkeypatch.setattr(equipment_queries, "validate_equipment", counted)
    with queries.equipment_read_session(equipment_db):
        first = queries.get_weapon_detail(equipment_db, 11401)
        queries.get_weapon_stats(equipment_db, 11401, 20, 1)
        queries.get_artifact_detail(equipment_db, 71543)
        queries.get_artifact_main_stats(equipment_db, 71543, 20, 14001)
    assert calls == ["release"]
    fixture_builder.build_database(equipment_db.parent, force=True)
    second = queries.get_weapon_detail(equipment_db, 11401)
    assert calls == ["release", "release"]
    # ``built_at`` is source-bound so a forced rebuild of the same snapshot is
    # deterministic; the second validation call proves the read session did
    # not cache state across updates.
    assert first["data_version"]["built_at"] == second["data_version"]["built_at"]
