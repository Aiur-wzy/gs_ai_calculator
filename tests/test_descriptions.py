"""描述升级验证：来源、缺文、参数槽位与事务性，不写正式库。"""

import hashlib
import json
import sqlite3

import pytest

from genshin_db import descriptions, extractor
from genshin_db.annotation_store import create_annotation_tables
from tests.test_extractor import _insert_raw
from tests.test_extractor import (
    fake_db as fake_db,  # noqa: PLC0414 - pytest 夹具显式导出
)


def rows(db, sql, args=()):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def raw(db, name):
    return json.loads(rows(db, "SELECT data_json FROM raw_table WHERE source_file=?", (name,))[0]
                      ["data_json"])


def execute(db, sql, args=()):
    conn = sqlite3.connect(db)
    try:
        with conn:
            conn.execute(sql, args)
    finally:
        conn.close()


def add_level_variants(db):
    proud = raw(db, "ProudSkillExcelConfigData")
    first = next(e for e in proud if e["proudSkillGroupId"] == 92002)
    first["proudSkillId"] = 9200201
    first["paramList"] = [0, 0, 15, 0]
    proud.append({**first, "proudSkillId": 9200202, "level": 2, "descTextMapHash": 603})
    texts = raw(db, "TextMap_MediumCHS")
    texts["603"] = "第二级：恢复20点燃素"
    _insert_raw(db, "ProudSkillExcelConfigData", proud)
    _insert_raw(db, "TextMap_MediumCHS", texts)


def make_legacy(db):
    add_level_variants(db)
    extractor.build_curated_layer(db, "7.0", "release", upstream_commit="a" * 40)
    execute(db, "INSERT OR REPLACE INTO meta VALUES ('upstream_commit',?)", ("a" * 40,))
    for table, columns in descriptions._COLUMNS.items():
        for column in columns:
            execute(db, f"ALTER TABLE {table} DROP COLUMN {column}")
    execute(db, "DELETE FROM meta WHERE key IN "
            "('raw_version_line','raw_upstream_commit','description_schema_version')")
    # 仿照旧版实际输出，前置零槽曾被压缩；不用新回填逻辑生成测试输入。
    execute(db, "UPDATE extra_info SET content=replace(content,'0, 0, 15, 0','15')")
    execute(db, "UPDATE extra_info SET content=replace(content,'0.8, 0, 0','0.8')")
    execute(db, "UPDATE extra_info SET content=replace(content,'0.86, 0, 0','0.86')")
    execute(db, "UPDATE extra_info SET content=replace(content,'按 paramN 引用参数序号',"
            "'按序与参数对应')")


def test_new_build_sources_missing_text_and_rich_body(fake_db):
    texts = raw(fake_db, "TextMap_MediumCHS")
    texts["201"] = "<color=#FF0000>完整描述</color>\\n<i>附注</i>{LINK#S123}引用{/LINK}"
    texts["501"] = ""
    _insert_raw(fake_db, "TextMap_MediumCHS", texts)
    skills = raw(fake_db, "AvatarSkillExcelConfigData")
    skills[1]["descTextMapHash"] = 0
    skills[2]["descTextMapHash"] = 987654321
    _insert_raw(fake_db, "AvatarSkillExcelConfigData", skills)
    extractor.build_curated_layer(fake_db, "7.0", "release", upstream_commit="b" * 40)
    actual = rows(fake_db, "SELECT * FROM skill ORDER BY id")
    assert [r["desc_status"] for r in actual] == ["present", "source_empty", "textmap_missing"]
    assert actual[0]["desc_template"] == "完整描述\\n<i>附注</i>{LINK#S123}引用{/LINK}"
    assert json.loads(actual[0]["desc_source"]) == {
        "table": "AvatarSkillExcelConfigData", "record_id": 90011, "text_map_hash": "201",
        "language": "CHS", "upstream_commit": "b" * 40,
    }
    assert json.loads(actual[1]["desc_source"])["text_map_hash"] is None
    sub = rows(fake_db, "SELECT * FROM extra_info WHERE info_type='sub_skill'")[0]
    assert sub["desc_status"] == "source_empty"
    assert json.loads(sub["desc_source"])["text_map_hash"] == "501"
    assert rows(fake_db, "SELECT text FROM text_map WHERE hash='201'")[0]["text"] == texts["201"]
    talent = rows(fake_db, "SELECT * FROM talent")[0]
    assert talent["desc_status"] == "present"
    assert json.loads(talent["desc_source"])["record_id"] == 9001


def test_inherent_level_descriptions_and_zero_positions(fake_db):
    add_level_variants(fake_db)
    extractor.build_curated_layer(fake_db, "7.0", "release", upstream_commit="c" * 40)
    item = rows(fake_db, "SELECT * FROM extra_info WHERE info_type='inherent_skill'")[0]
    assert item["source_id"] == item["proud_skill_group_id"] == 92002
    assert "Lv1: 0, 0, 15, 0" in item["content"]
    assert item["name"] == "夜域赐礼·测试"
    variants = json.loads(item["description_variants"])
    assert [v["level"] for v in variants] == [1, 2]
    assert variants[1]["desc_template"] == "第二级：恢复20点燃素"
    assert variants[1]["desc_source"]["record_id"] == 9200202
    assert all(v["desc_status"] == "present" for v in variants)
    assert json.loads(rows(fake_db, "SELECT param_list FROM skill_param WHERE "
                          "proud_skill_group_id=92002 AND level=1")[0]["param_list"]) == [0, 0, 15, 0]


def test_migration_is_idempotent_and_preserves_existing_data(fake_db):
    make_legacy(fake_db)
    create_annotation_tables(fake_db)
    execute(fake_db, "UPDATE talent SET buff_zone='[\"manual-tag\"]'")
    execute(fake_db, "INSERT INTO character_note (character_id,version_line,note) "
            "VALUES (90000001,'release','用户笔记')")
    execute(fake_db, "INSERT INTO annotation (target_type,target_id,content,status,created_at,"
            "updated_at) VALUES ('character',90000001,'人工机制','approved','before','before')")
    before = {t: rows(fake_db, f"SELECT * FROM {t}")
              for t in ("character", "skill", "talent", "skill_param", "annotation", "character_note")}
    old_meta = rows(fake_db, "SELECT * FROM meta")
    report = descriptions.migrate_descriptions(fake_db)
    assert report["updated_rows"] == 6
    assert report["status_counts"]["extra_info"] == {"present": 2}
    for table, old_rows in before.items():
        actual = rows(fake_db, f"SELECT * FROM {table}")
        if old_rows:
            assert [{k: r[k] for k in old_rows[0]} for r in actual] == old_rows
        else:
            assert not actual
    assert all(r in rows(fake_db, "SELECT * FROM meta") for r in old_meta)
    assert "Lv1: 0, 0, 15, 0" in rows(fake_db, "SELECT content FROM extra_info "
                                     "WHERE info_type='inherent_skill'")[0]["content"]
    before_second = hashlib.sha256(fake_db.read_bytes()).hexdigest()
    assert descriptions.migrate_descriptions(fake_db)["updated_rows"] == 0
    assert hashlib.sha256(fake_db.read_bytes()).hexdigest() == before_second


def test_failed_migration_rolls_back_columns_and_rows(fake_db, monkeypatch):
    make_legacy(fake_db)
    before = hashlib.sha256(fake_db.read_bytes()).hexdigest()
    original = descriptions._update

    def fail_after_write(*args):
        original(*args)
        raise RuntimeError("故障注入：写入后失败")

    monkeypatch.setattr(descriptions, "_update", fail_after_write)
    with pytest.raises(RuntimeError, match="故障注入"):
        descriptions.migrate_descriptions(fake_db)
    assert hashlib.sha256(fake_db.read_bytes()).hexdigest() == before
    assert "desc_source" not in rows(fake_db, "SELECT * FROM skill")[0]


@pytest.mark.parametrize("mismatch", ["raw_commit", "text_map", "body", "owner"])
def test_migration_does_not_guess_mismatching_source(fake_db, mismatch):
    make_legacy(fake_db)
    if mismatch == "raw_commit":
        execute(fake_db, "INSERT OR REPLACE INTO meta VALUES ('raw_upstream_commit','wrong')")
    elif mismatch == "text_map":
        execute(fake_db, "UPDATE text_map SET text='另一个快照' WHERE hash='201'")
    elif mismatch == "body":
        execute(fake_db, "UPDATE skill SET desc_template='原有人工文本' WHERE id=90011")
    else:
        execute(fake_db, "UPDATE skill SET character_id=123 WHERE id=90011")
    original_body = rows(fake_db, "SELECT desc_template FROM skill WHERE id=90011")[0]
    descriptions.migrate_descriptions(fake_db)
    item = rows(fake_db, "SELECT * FROM skill WHERE id=90011")[0]
    assert item["desc_status"] == "legacy_unverified" and item["desc_source"] is None
    assert item["desc_template"] == original_body["desc_template"]


def test_migration_does_not_use_release_raw_for_beta(fake_db):
    make_legacy(fake_db)
    execute(fake_db, "INSERT INTO skill SELECT id,'beta',character_id,slot,name,desc_template,"
            "cooldown,energy_cost,duration,cost_stamina,max_charge_num,effect_type,"
            "proud_skill_group_id FROM skill")
    descriptions.migrate_descriptions(fake_db)
    assert all(r["desc_source"] is None for r in rows(fake_db, "SELECT * FROM skill "
                                                     "WHERE version_line='beta'"))
    descriptions.migrate_descriptions(fake_db, "beta")
    assert all(r["desc_status"] == "legacy_unverified" for r in rows(fake_db, "SELECT * FROM skill "
                                                                    "WHERE version_line='beta'"))
    assert all(r["desc_status"] == "present" for r in rows(fake_db, "SELECT * FROM skill "
                                                         "WHERE version_line='release'"))


def test_ambiguous_legacy_extra_keeps_content_without_guessing_id(fake_db):
    skills = raw(fake_db, "AvatarSkillExcelConfigData")
    original = next(s for s in skills if s["id"] == 90014)
    skills.append({**original, "id": 90015})
    _insert_raw(fake_db, "AvatarSkillExcelConfigData", skills)
    depots = raw(fake_db, "AvatarSkillDepotExcelConfigData")
    depots[0]["subSkills"].append(90015)
    _insert_raw(fake_db, "AvatarSkillDepotExcelConfigData", depots)
    extractor.build_curated_layer(fake_db, "7.0", "release", upstream_commit="a" * 40)
    fresh = rows(fake_db, "SELECT * FROM extra_info WHERE info_type='sub_skill'")
    assert {r["source_id"] for r in fresh} == {90014, 90015}
    assert all(r["desc_status"] == "present" for r in fresh)
    make_legacy(fake_db)
    old = rows(fake_db, "SELECT content FROM extra_info WHERE info_type='sub_skill'")
    descriptions.migrate_descriptions(fake_db)
    migrated = rows(fake_db, "SELECT * FROM extra_info WHERE info_type='sub_skill'")
    assert [r["content"] for r in migrated] == [r["content"] for r in old]
    assert all(r["source_id"] is None and r["desc_source"] is None
               and r["desc_status"] == "legacy_unverified" for r in migrated)
