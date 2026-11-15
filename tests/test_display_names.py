"""显示身份契约：中文正文、来源、旧库只读、跨线隔离和可恢复离线迁移。"""

import hashlib
import json
import sqlite3
import threading
import urllib.request

import pytest

from genshin_db import queries
from genshin_db.display_names import (
    display_complete,
    get_display_catalog,
    migrate_display_names,
    normalize_alias,
    resolve_display_entity,
)
from genshin_db.service import GenshinHTTPServer


@pytest.fixture
def display_db(tmp_path):
    path = tmp_path / "genshin.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE character (id INTEGER, version_line TEXT,name TEXT,desc TEXT);
            CREATE TABLE skill (id INTEGER,character_id INTEGER,version_line TEXT,
                                name TEXT,desc_template TEXT,slot TEXT);
            CREATE TABLE weapon (id INTEGER,version_line TEXT,name TEXT,desc_template TEXT,
                                 name_source TEXT,name_status TEXT,desc_status TEXT);
            CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE raw_table (source_file TEXT PRIMARY KEY,data_json TEXT);
            CREATE TABLE text_map (hash TEXT,locale TEXT,text TEXT);
            CREATE TABLE annotation (id INTEGER PRIMARY KEY,content TEXT);
            INSERT INTO character VALUES
              (10000002,'release','神里绫华','角色原文'),
              (10000052,'release','雷电将军','雷电将军原文'),
              (10000005,'release','旅行者','旅行者原文'),
              (10000007,'release','旅行者','旅行者原文'),
              (11000028,'release','白盒少女','测试对象'),
              (90000001,'release','未来角色','扩展角色'),
              (90000001,'beta','未来角色测试','测试线原文'),
              (90000002,'release','English Only','原文保留');
            INSERT INTO skill VALUES
              (10,10000005,'release','共享技能','原始伤害描述','skill'),
              (10,10000007,'release','共享技能','原始伤害描述','skill');
            INSERT INTO weapon VALUES (11509,'release','雾切之回光','武器原文',NULL,'present','present');
            INSERT INTO meta VALUES
              ('raw_version_line','release'),('raw_upstream_commit','same-commit'),
              ('upstream_commit:release','same-commit'),('upstream_commit:beta','old-commit');
            INSERT INTO annotation VALUES (1,'人工说明不可改变');
            INSERT INTO text_map VALUES
              ('1','CHS','神里绫华'),('2','CHS','白盒少女'),('3','CHS','未来角色');
        """)
        avatars = [
            {"id": 10000002, "nameTextMapHash": 1, "iconName": "UI_AvatarIcon_Ayaka", "useType": "AVATAR_FORMAL"},
            {"id": 11000028, "nameTextMapHash": 2, "iconName": "UI_AvatarIcon_Ayaka"},
            {"id": 90000001, "nameTextMapHash": 3, "iconName": "UI_AvatarIcon_NewActor", "useType": "AVATAR_FORMAL"},
        ]
        conn.execute("INSERT INTO raw_table VALUES ('AvatarExcelConfigData',?)", (json.dumps(avatars),))
    return path


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_old_catalog_is_read_only_compact_chinese_and_resolves_formal_avatar(display_db):
    before = _sha(display_db)
    catalog = queries.get_display_catalog(display_db)
    assert catalog["storage_status"] == "legacy_read_only" and catalog["locale"] == "zh-CN"
    assert {k for k, v in catalog.items() if isinstance(v, list)} == {
        "characters", "weapons", "artifact_sets"}
    assert all("description" not in row for row in catalog["characters"])
    result = resolve_display_entity(display_db, "ayaka")
    assert result["status"] == "resolved" and result["items"][0]["id"] == 10000002
    assert result["items"][0]["display_name"] == "神里绫华"
    assert resolve_display_entity(display_db, "Raiden_Shogun")["items"][0]["id"] == 10000052
    assert resolve_display_entity(display_db, "Mistsplitter Reforged", "weapon")["status"] == "resolved"
    assert resolve_display_entity(display_db, "90000002")["items"][0]["display_name"] == "未命名角色（90000002）"
    assert _sha(display_db) == before


def test_shared_names_and_skill_ids_stay_ambiguous(display_db):
    assert normalize_alias(" Ｒａｉｄｅｎ_Shogun ") == "raidenshogun"
    assert resolve_display_entity(display_db, "旅行者")["status"] == "ambiguous"
    assert resolve_display_entity(display_db, "Traveler")["status"] == "ambiguous"
    assert resolve_display_entity(display_db, "10000005")["status"] == "resolved"
    assert resolve_display_entity(display_db, 10, "skill")["status"] == "ambiguous"
    assert resolve_display_entity(display_db, "Ay")["status"] == "missing"
    assert resolve_display_entity(display_db, "new_actor", version_line="beta")["status"] == "missing"
    assert resolve_display_entity(display_db, "new-actor")["items"][0]["id"] == 90000001


def test_description_opt_in_preserves_original_and_identity(display_db):
    result = get_display_catalog(display_db, entity_types=["skill"], include_descriptions=True)
    assert [(r["id"], r["character_id"], r["description"]) for r in result["skills"]] == [
        (10, 10000005, "原始伤害描述"), (10, 10000007, "原始伤害描述")]
    assert all(r["description_status"] == "legacy_unverified" for r in result["skills"])


def test_migration_is_idempotent_preserves_manual_data_and_other_line(display_db):
    with sqlite3.connect(display_db) as conn:
        snapshot = {table: conn.execute(f"SELECT * FROM {table}").fetchall()
                    for table in ("character", "skill", "weapon", "annotation", "raw_table", "text_map")}
    first = migrate_display_names(display_db)
    assert first["changed_rows"] > 0 and first["validation"]["integrity_check"] == "ok"
    with sqlite3.connect(display_db) as conn:
        conn.execute("INSERT INTO entity_alias VALUES (?,?,?,?,?,?,?,?,?)",
                     ("character", 10000002, "release", "zh-CN", "绫华", "绫华", "manual", '{"note":"人工确认"}', None))
        conn.execute("INSERT INTO entity_alias VALUES (?,?,?,?,?,?,?,?,?)",
                     ("character", 90000001, "beta", "zh-CN", "futuretest", "future-test", "manual", '{}', None))
        beta = conn.execute("SELECT * FROM entity_alias WHERE version_line='beta'").fetchall()
    before = _sha(display_db)
    second = migrate_display_names(display_db)
    assert second["changed_rows"] == 0 and _sha(display_db) == before
    assert resolve_display_entity(display_db, "绫华")["items"][0]["id"] == 10000002
    with sqlite3.connect(display_db) as conn:
        assert display_complete(conn, "release")
        assert not display_complete(conn, "beta")
        assert conn.execute("SELECT * FROM entity_alias WHERE version_line='beta'").fetchall() == beta
        for table, rows in snapshot.items():
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == rows


def test_current_name_change_does_not_keep_stale_alias(display_db):
    migrate_display_names(display_db)
    with sqlite3.connect(display_db) as conn:
        conn.execute("UPDATE character SET name='另一身份' WHERE id=10000002")
    assert resolve_display_entity(display_db, "ayaka")["status"] == "missing"
    assert migrate_display_names(display_db)["changed_rows"] > 0
    assert resolve_display_entity(display_db, "ayaka")["status"] == "missing"


def test_failed_migration_rolls_back_and_future_schema_is_read_only(display_db):
    with sqlite3.connect(display_db) as conn:
        conn.execute("UPDATE raw_table SET data_json='broken json'")
    before = _sha(display_db)
    with pytest.raises(json.JSONDecodeError):
        migrate_display_names(display_db)
    assert _sha(display_db) == before
    with sqlite3.connect(display_db) as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='entity_alias'").fetchone()
        conn.execute("INSERT INTO meta VALUES ('display_schema_version','2')")
    before = _sha(display_db)
    with pytest.raises(ValueError, match="不受支持"):
        migrate_display_names(display_db)
    assert _sha(display_db) == before


def test_missing_database_is_not_created_and_invalid_request_rejected(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        get_display_catalog(path)
    assert not path.exists()
    with pytest.raises(ValueError):
        get_display_catalog(path, entity_types=["unsafe; DROP TABLE character"])
    with pytest.raises(ValueError):
        get_display_catalog(path, version_line="other")


def test_http_display_contract_matches_pure_queries(display_db):
    with sqlite3.connect(display_db) as conn:
        conn.execute("DROP TABLE annotation")  # HTTP 初始化需要完整人工层，前面仅用最小保留性夹具。
    server = GenshinHTTPServer(display_db.parent, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(server.base_url + "/v1/display/catalog") as response:
            assert json.load(response)["data"] == get_display_catalog(display_db)
        with urllib.request.urlopen(server.base_url + "/v1/display/resolve?q=ayaka") as response:
            data = json.load(response)["data"]
            assert data["status"] == "resolved" and data["items"][0]["display_name"] == "神里绫华"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
