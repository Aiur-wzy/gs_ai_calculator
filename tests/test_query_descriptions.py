"""原始描述上下文：旧库只读兼容、来源分层、共享归属及真实 HTTP 返回。"""

import hashlib
import json
import sqlite3
import threading
import urllib.request

import pytest

from genshin_db import queries
from genshin_db.annotation_store import create_annotation_tables
from genshin_db.service import GenshinHTTPServer


@pytest.fixture
def legacy_db(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    path = data_dir / "genshin.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE character (id INTEGER, version_line TEXT, name TEXT,
                                    skill_depot_id INTEGER);
            CREATE TABLE skill (id INTEGER, character_id INTEGER, version_line TEXT,
                                slot TEXT, name TEXT, desc_template TEXT,
                                proud_skill_group_id INTEGER);
            CREATE TABLE talent (id INTEGER, character_id INTEGER, version_line TEXT,
                                 level INTEGER, name TEXT, desc_template TEXT,
                                 param_list TEXT, add_props TEXT);
            CREATE TABLE skill_param (proud_skill_group_id INTEGER, version_line TEXT,
                                      level INTEGER, param_list TEXT, param_desc TEXT,
                                      add_props TEXT, buff_zone TEXT);
            CREATE TABLE extra_info (id INTEGER PRIMARY KEY, target_type TEXT,
                                     target_id INTEGER, info_type TEXT, content TEXT,
                                     version_line TEXT);
            INSERT INTO character VALUES (1,'release','角色一',1),(2,'release','角色二',2),
                                         (1,'beta','角色一测试',1);
            INSERT INTO skill VALUES (10,1,'release','skill','技能','原始技能描述',100),
                                     (10,2,'release','skill','共享技能','原始技能描述',100),
                                     (10,1,'beta','skill','技能测试','测试原文',100);
            INSERT INTO talent VALUES (20,1,'release',1,'命座','命座原文','[0,0.2]','{}');
            INSERT INTO skill_param VALUES
                (100,'release',1,'[0,0,15,0]','["冷却|{param3:I}"]','{}','["stat_add"]'),
                (100,'beta',1,'[0,0,20,0]','["冷却|{param3:I}"]','{}',NULL),
                (777,'release',1,'[0,7]','[]','{}',NULL);
            INSERT INTO extra_info VALUES
                (1,'character',1,'sub_skill','旧补充文本，不作为独立描述解析','release'),
                (2,'character',1,'inherent_skill','旧固有天赋（参数组 100）','release'),
                (3,'character',2,'sub_skill','另一角色共享的附属技能','release'),
                (4,'character',1,'sub_skill','重复关系由其他动作集引用','release'),
                (5,'character',1,'sub_skill','无参数组的附属技能','release'),
                (6,'character',1,'sub_skill','测试线附属技能','beta');
        """)
    create_annotation_tables(path)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            "INSERT INTO annotation (id,target_type,target_id,version_line,content,status,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?, 'created','updated')",
            [(1, "skill", 10, None, "共享机制", "approved"),
             (2, "skill", 10, "release", "正式机制", "approved"),
             (3, "skill", 10, "beta", "测试机制", "approved"),
             (4, "skill", 10, "release", "未确认机制", "draft"),
             (5, "skill_param", 100, "release", "参数组机制", "approved"),
             (6, "character", 1, "release", "角色级条件", "approved"),
             (7, "talent", 20, "release", "命座条件", "approved"),
             (8, "skill_param", 101, "release", "无关机制", "approved"),
             (9, "skill", 500, "release", "附属机制", "approved"),
             (10, "inherent_skill", 100, "release", "固有机制", "approved")],
        )
    return path


def _source(table, record_id, text_hash):
    return {"table": table, "record_id": record_id, "text_map_hash": str(text_hash),
            "language": "CHS", "upstream_commit": "description-test-commit"}


@pytest.fixture
def description_db(legacy_db):
    with sqlite3.connect(legacy_db) as conn:
        for table in ("skill", "talent", "extra_info"):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN desc_source TEXT")
            conn.execute(f"ALTER TABLE {table} ADD COLUMN desc_status TEXT")
        for column, kind in (("name", "TEXT"), ("desc_template", "TEXT"),
                             ("source_id", "INTEGER"), ("proud_skill_group_id", "INTEGER"),
                             ("description_variants", "TEXT")):
            conn.execute(f"ALTER TABLE extra_info ADD COLUMN {column} {kind}")
        conn.execute("UPDATE skill SET desc_source=?,desc_status='present'",
                     (json.dumps(_source("AvatarSkillExcelConfigData", 10, 110)),))
        conn.execute("UPDATE talent SET desc_source=?,desc_status='present'",
                     (json.dumps(_source("AvatarTalentExcelConfigData", 20, 120)),))
        conn.execute(
            "UPDATE extra_info SET name='附属技能',desc_template='独立附属原文',"
            "source_id=500,proud_skill_group_id=100,desc_source=?,desc_status='present'",
            (json.dumps(_source("AvatarSkillExcelConfigData", 500, 1500)),),
        )
        variants = [
            {"level": level, "desc_template": text,
             "desc_source": _source("ProudSkillExcelConfigData", 10000 + level, 1600 + level),
             "desc_status": "present"}
            for level, text in ((1, "一级固有原文"), (2, "二级固有原文"))
        ]
        conn.execute(
            "UPDATE extra_info SET name='固有天赋',desc_template='一级固有原文',source_id=100,"
            "desc_source=?,description_variants=? WHERE id=2",
            (json.dumps(variants[0]["desc_source"]), json.dumps(variants)),
        )
        conn.execute(
            "UPDATE extra_info SET name='无参数附属',source_id=501,proud_skill_group_id=NULL,"
            "desc_template='',desc_status='source_empty',desc_source=? WHERE id=5",
            (json.dumps(_source("AvatarSkillExcelConfigData", 501, 1501)),),
        )
    return legacy_db


def test_old_database_queries_preserve_description_and_do_not_migrate(legacy_db):
    before = hashlib.sha256(legacy_db.read_bytes()).hexdigest()
    detail = queries.get_character_detail(legacy_db, 1)
    assert set(detail) == {"character", "skills", "talents", "extra_info", "annotations"}
    for item in [*detail["skills"], *detail["talents"]]:
        assert item["desc_template"]
        assert item["desc_source"] is None and item["desc_status"] == "legacy_unverified"
    extra = detail["extra_info"][0]
    assert extra["content"] == "旧补充文本，不作为独立描述解析"
    assert extra["desc_template"] is None and extra["name"] is None
    assert extra["desc_source"] is None and extra["desc_status"] == "legacy_unverified"
    assert extra["source_id"] is None and extra["proud_skill_group_id"] is None
    assert extra["params"] == {} and extra["description_variants"] == []
    assert queries.get_skill(legacy_db, 10)["items"][0]["proud_skill_group_id"] == 100
    assert len(queries.get_skill_params(legacy_db, 100)["context_refs"]) == 2
    assert hashlib.sha256(legacy_db.read_bytes()).hexdigest() == before


def test_description_sources_are_decoded_and_machine_labels_stay_separate(description_db):
    detail = queries.get_character_detail(description_db, 1)
    skill = detail["skills"][0]
    assert skill["desc_template"] == "原始技能描述"
    assert skill["desc_source"] == _source("AvatarSkillExcelConfigData", 10, 110)
    assert skill["desc_status"] == "present" and skill["proud_skill_group_id"] == 100
    assert skill["params"]["1"]["param_list"] == [0, 0, 15, 0]
    assert skill["params"]["1"]["buff_zone"] == '["stat_add"]'  # 旧数值返回兼容
    assert detail["talents"][0]["desc_source"] == _source("AvatarTalentExcelConfigData", 20, 120)
    assert detail["talents"][0]["param_list"] == [0, 0.2]


def test_extra_descriptions_have_stable_identity_variants_and_exact_params(description_db):
    detail = queries.get_character_detail(description_db, 1)
    sub = next(item for item in detail["extra_info"] if item["id"] == 1)
    assert sub["source_id"] == 500 and sub["proud_skill_group_id"] == 100
    assert sub["desc_template"] == "独立附属原文" and sub["content"].startswith("旧补充文本")
    assert sub["params"]["1"]["param_list"] == [0, 0, 15, 0]
    innate = next(item for item in detail["extra_info"] if item["info_type"] == "inherent_skill")
    assert innate["source_id"] == 100
    assert [(item["level"], item["desc_template"]) for item in innate["description_variants"]] == [
        (1, "一级固有原文"), (2, "二级固有原文")]
    assert innate["description_variants"][1]["desc_source"]["record_id"] == 10002
    no_group = next(item for item in detail["extra_info"] if item["source_id"] == 501)
    assert no_group["params"] == {} and no_group["desc_status"] == "source_empty"
    assert no_group["desc_template"] == ""  # 不用content或机器标签冒充缺失原文


@pytest.mark.parametrize("line,expected", [("release", [1, 2, 5]), ("beta", [1, 3])])
def test_single_skill_annotations_filter_status_line_and_exact_targets(description_db, line, expected):
    items = queries.get_skill(description_db, 10, line)["items"]
    assert len(items) == (2 if line == "release" else 1)
    for item in items:
        assert [ann["id"] for ann in item["annotations"]] == expected
        context = item["context_ref"]
        assert {key: value for key, value in context.items() if key != "publication"} == {
            "character_id": item["character_id"], "version_line": line,
            "path": f"/v1/characters/{item['character_id']}?version_line={line}"}
        publication = context["publication"]
        assert publication["entity"] == {"type": "character", "id": item["character_id"]}
        assert publication["version_line"] == line
        assert publication["entity_status"] == "unknown" and publication["evidence_state"] == "no_evidence"
        assert publication["requires_usage_confirmation"] is True
    assert len(queries.get_skill(description_db, 10, line, character_id=1)["items"]) == 1


def test_character_context_still_contains_related_conditional_annotations(description_db):
    detail = queries.get_character_detail(description_db, 1)
    assert [ann["id"] for ann in detail["annotations"]] == [1, 2, 5, 6, 7, 9, 10]


def test_parameter_refs_keep_all_owners_deduplicate_and_use_real_extra_paths(description_db):
    result = queries.get_skill_params(description_db, 100, level=1)
    assert result["version_line"] == "release" and result["context_note"] is None
    assert result["items"][0]["param_list"] == [0, 0, 15, 0]
    refs = result["context_refs"]
    assert {(r["kind"], r["source_id"], r["character_id"]) for r in refs} == {
        ("skill", 10, 1), ("skill", 10, 2),
        ("sub_skill", 500, 1), ("sub_skill", 500, 2), ("inherent_skill", 100, 1)}
    assert len(refs) == 5
    for ref in refs:
        assert ref["version_line"] == "release" and ref["proud_skill_group_id"] == 100
        if ref["kind"] == "skill":
            assert f"character_id={ref['character_id']}" in ref["path"]
        else:
            assert ref["path"] == f"/v1/characters/{ref['character_id']}?version_line=release"
            assert ref["selector"]["collection"] == "extra_info"
            assert ref["selector"]["source_id"] == ref["source_id"]
    beta = queries.get_skill_params(description_db, 100, "beta")["context_refs"]
    assert {(r["kind"], r["character_id"]) for r in beta} == {("skill", 1), ("sub_skill", 1)}


def test_unowned_parameter_group_keeps_values_with_explicit_context_note(description_db):
    result = queries.get_skill_params(description_db, 777)
    assert result["items"][0]["param_list"] == [0, 7]
    assert result["context_refs"] == [] and "不表示没有技能描述" in result["context_note"]


def test_http_exposes_decoded_description_context_and_capabilities(description_db):
    server = GenshinHTTPServer(description_db.parent, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def get(path):
            with urllib.request.urlopen(server.base_url + path, timeout=5) as response:
                payload = json.load(response)
                assert payload["code"] == 0
                return payload["data"]

        skill = get("/v1/skills/10?character_id=1")["items"][0]
        assert skill["desc_source"]["text_map_hash"] == "110"
        assert [ann["id"] for ann in skill["annotations"]] == [1, 2, 5]
        params = get("/v1/skill-params/100?level=1")
        assert len(params["context_refs"]) == 5
        for ref in params["context_refs"]:
            context = get(ref["path"])
            assert "items" in context if ref["kind"] == "skill" else "extra_info" in context
        descriptions = {entry["path"]: entry["desc"]
                        for entry in get("/v1/meta/capabilities")["endpoints"]}
        assert "legacy_unverified" in descriptions["/v1/characters/{id}"]
        assert "annotations" in descriptions["/v1/skills/{id}"]
        assert "context_refs" in descriptions["/v1/skill-params/{groupId}"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
