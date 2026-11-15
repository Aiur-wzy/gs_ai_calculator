"""G4 查询层测试：genshin_db.queries（service 与 GUI 桥层的共用口径）。"""

import json

import pytest

from genshin_db import queries
from genshin_db.annotation_store import create_annotation_tables
from genshin_db.extractor import build_curated_layer
from genshin_db.raw_store import build_raw_layer


@pytest.fixture
def db_path(tmp_path):
    """用 fake 上游建库（与 test_service 同一假数据）。"""
    from tests.test_extractor import FAKE_CURVE, FAKE_PROMOTE, TEXTS

    repo = tmp_path / "repo"
    (repo / "ExcelBinOutput").mkdir(parents=True)
    (repo / "TextMap").mkdir()
    files = {
        "AvatarExcelConfigData": [{
            "id": 90000001, "nameTextMapHash": 100, "descTextMapHash": 101,
            "qualityType": "QUALITY_ORANGE", "weaponType": "WEAPON_SWORD_ONE_HAND",
            "hpBase": 12000.0, "attackBase": 300.0, "defenseBase": 700.0,
            "critical": 0.05, "criticalHurt": 0.5, "chargeEfficiency": 1.0,
            "propGrowCurves": [
                {"type": "FIGHT_PROP_BASE_HP", "growCurve": "GROW_CURVE_HP_S5"},
                {"type": "FIGHT_PROP_BASE_ATTACK", "growCurve": "GROW_CURVE_ATTACK_S5"},
                {"type": "FIGHT_PROP_BASE_DEFENSE", "growCurve": "GROW_CURVE_DEFENSE_S5"},
            ],
            "skillDepotId": 9001, "avatarPromoteId": 900,
        }],
        "AvatarSkillExcelConfigData": [
            {"id": 90011, "nameTextMapHash": 200, "descTextMapHash": 201,
             "proudSkillGroupId": 91001},
            {"id": 90013, "nameTextMapHash": 220, "descTextMapHash": 221,
             "cdTime": 15, "costElemVal": 60, "costElemType": "Rock",
             "proudSkillGroupId": 91003},
        ],
        "AvatarTalentExcelConfigData": [
            {"talentId": 9001, "nameTextMapHash": 300, "descTextMapHash": 301,
             "paramList": [0.1], "addProps": []},
        ],
        "AvatarSkillDepotExcelConfigData": [{
            "id": 9001, "skills": [90011, 0, 0, 0], "subSkills": [],
            "inherentProudSkillOpens": [], "energySkill": 90013,
            "talents": [9001, 0, 0, 0, 0, 0],
        }],
        "ProudSkillExcelConfigData": [
            {"proudSkillGroupId": 91001, "level": 1, "paramList": [0.5]},
            {"proudSkillGroupId": 91003, "level": 10, "paramList": [3.0]},
            {"proudSkillGroupId": 91003, "level": 13, "paramList": [3.5]},
        ],
        "AvatarCurveExcelConfigData": FAKE_CURVE,
        "AvatarPromoteExcelConfigData": FAKE_PROMOTE,
        "FetterInfoExcelConfigData": [],
    }
    for name, data in files.items():
        (repo / "ExcelBinOutput" / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    (repo / "TextMap" / "TextMap_MediumCHS.json").write_text(
        json.dumps(TEXTS, ensure_ascii=False), encoding="utf-8"
    )
    db = tmp_path / "genshin.db"
    from tests.equipment_fixtures import equipment_files
    for rel, data in equipment_files().items():
        (repo / rel).write_text(json.dumps(data, ensure_ascii=False), encoding="utf8")
    raw = build_raw_layer(repo, db)
    build_curated_layer(db, "7.0", "release")
    from genshin_db.equipment import build_equipment_layer
    build_equipment_layer(
        db,
        "release",
        upstream_commit="query-fixture",
        manifest=raw.manifest,
    )
    create_annotation_tables(db)
    return db


# ---------- 查询 ----------

def test_get_meta(db_path):
    meta = queries.get_meta(db_path)
    assert meta["game_version"] == "7.0"
    assert meta["version_lines"] == ["release"]


def test_list_characters_default(db_path):
    r = queries.list_characters(db_path)
    assert r["total"] == 1
    assert r["items"][0]["id"] == 90000001


def test_character_detail_aggregate(db_path):
    d = queries.get_character_detail(db_path, 90000001)
    assert d["character"]["name"]
    assert len(d["skills"]) == 2  # 普攻位 + 元素爆发（energySkill）
    assert len(d["talents"]) == 1
    burst = next(s for s in d["skills"] if s["id"] == 90013)
    assert burst["cooldown"] == 15 and burst["energy_cost"] == 60
    assert set(burst["params"].keys()) == {"10", "13"}  # T8 只存 10/13 级


def test_character_detail_404(db_path):
    with pytest.raises(queries.NotFoundError):
        queries.get_character_detail(db_path, 999)


def test_skill_and_params(db_path):
    r = queries.get_skill(db_path, 90013)
    assert r["items"][0]["character_id"] == 90000001
    with pytest.raises(queries.NotFoundError):
        queries.get_skill(db_path, 999)
    p = queries.get_skill_params(db_path, 91003, level=10)
    assert p["items"][0]["param_list"] == [3.0]
    with pytest.raises(queries.NotFoundError):
        queries.get_skill_params(db_path, 999)


def test_search(db_path):
    r = queries.search(db_path, "角")  # TEXTS[100] = 测试角色
    assert r["characters"] and r["characters"][0]["id"] == 90000001
    with pytest.raises(ValueError, match="检索词"):
        queries.search(db_path, "  ")


# ---------- 追加描述（ADR-0006 全状态可编辑） ----------

def test_annotation_crud_full_cycle(db_path):
    # create → draft
    r = queries.create_annotation(db_path, "character", 90000001, "测试内容",
                                  category="快照", title="测试标题")
    ann_id = r["id"]
    assert r["status"] == "draft"
    # list：status=None 全部 / approved 过滤
    all_items = queries.list_annotations(db_path, status=None)["items"]
    assert len(all_items) == 1 and all_items[0]["status"] == "draft"
    assert queries.list_annotations(db_path, status="approved")["total"] == 0
    # 目标过滤
    by_target = queries.list_annotations(
        db_path, status=None, target_type="character", target_id=90000001)
    assert by_target["total"] == 1
    # update：内容 + 状态提升 approved
    r2 = queries.update_annotation(db_path, ann_id, {"status": "approved", "content": "改后"})
    assert r2["status"] == "approved"
    assert queries.list_annotations(db_path, status="approved")["total"] == 1
    # update approved（ADR-0006：不限状态可再编辑）
    r3 = queries.update_annotation(db_path, ann_id, {"title": "再改"})
    assert r3["status"] == "approved"
    # delete（approved 也可删）
    assert queries.delete_annotation(db_path, ann_id)["deleted"] == ann_id
    assert queries.list_annotations(db_path, status=None)["total"] == 0


def test_annotation_errors(db_path):
    with pytest.raises(ValueError, match="缺少字段"):
        queries.create_annotation(db_path, "character", 0, "")
    with pytest.raises(queries.NotFoundError):
        queries.update_annotation(db_path, 999, {"content": "x"})
    with pytest.raises(queries.NotFoundError):
        queries.delete_annotation(db_path, 999)
    r = queries.create_annotation(db_path, "skill", 90013, "内容")
    with pytest.raises(ValueError, match="非法 status"):
        queries.update_annotation(db_path, r["id"], {"status": "unknown"})
    with pytest.raises(ValueError, match="没有可更新"):
        queries.update_annotation(db_path, r["id"], {})


def test_list_annotation_categories_returns_counts_without_bodies(db_path):
    rows = [
        ("action_dispatch", "release", "第一条"),
        ("action_dispatch", None, "跨版本条目"),
        ("hit_pattern", "beta", "测试服条目"),
    ]
    for category, version_line, content in rows:
        created = queries.create_annotation(
            db_path, "skill", 90013, content,
            category=category, version_line=version_line)
        queries.update_annotation(db_path, created["id"], {"status": "approved"})
    pending = queries.create_annotation(
        db_path, "skill", 90013, "待审内容",
        category="action_dispatch", version_line="release")
    queries.update_annotation(db_path, pending["id"], {"status": "pending"})

    result = queries.list_annotation_categories(
        db_path, "skill", 90013, "approved", "release")

    assert result == {
        "target": {"target_type": "skill", "target_id": 90013},
        "status": "approved",
        "version_line": "release",
        "total": 2,
        "categories": [{"category": "action_dispatch", "count": 2}],
    }
    assert "content" not in json.dumps(result, ensure_ascii=False)
    assert queries.list_annotation_categories(
        db_path, "skill", 90013, "pending", "release")["total"] == 1


def test_annotation_target_scope_resolves_character_children(db_path):
    for target_type, target_id in (
            ("character", 90000001), ("skill", 90013),
            ("talent", 9001), ("skill_param", 91003)):
        scope = queries.annotation_target_scope(
            db_path, target_type, target_id, "release")
        assert scope["target"] == {
            "target_type": target_type, "target_id": target_id}
        assert scope["character_ids"] == [90000001]
        assert scope["weapon_ids"] == []
        assert scope["artifact_set_ids"] == []

    assert queries.annotation_target_scope(
        db_path, "weapon", 11401)["weapon_ids"] == [11401]
    assert queries.annotation_target_scope(
        db_path, "artifact_set", 15)["artifact_set_ids"] == [15]
    assert queries.annotation_target_scope(
        db_path, "artifact", 71543)["artifact_set_ids"] == [15]
    affix = queries.annotation_target_scope(db_path, "equip_affix", 917)
    assert affix["weapon_ids"] == [11401]
    assert affix["artifact_set_ids"] == [15]


def test_service_gui_same_source(db_path):
    """口径一致冒烟：查询层结果与 service 端点处理结果结构相同（同函数即同口径）。"""
    d = queries.get_character_detail(db_path, 90000001)
    assert set(d) == {"character", "skills", "talents", "extra_info", "annotations"}
