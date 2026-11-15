"""extractor 模块测试：假数据验证字段映射、槽位/元素推断、effect_type 启发式与 text_map 灌入。"""

import json
import sqlite3

import pytest

from genshin_db import extractor
from genshin_db.raw_store import CREATE_RAW_TABLE

TEXTS = {
    "100": "测试角色",
    "101": "测试角色描述",
    "200": "普通攻击·测试",
    "201": "造成{param1:F1P}的岩元素伤害",
    "210": "测试战技",
    "211": "恢复生命值并造成{param1:P}伤害",
    "220": "测试爆发",
    "221": "提升队伍中所有角色的攻击力，并恢复生命值",
    "300": "一命·测试",
    "301": "暴击率提升10%",
    "400": "固有天赋描述",
    "500": "陀螺·冲天转转",
    "501": "搭乘陀螺冲撞敌人，造成{param1:P}岩元素伤害",
    "600": "夜域赐礼·测试",
    "601": "在夜魂状态下移动速度提升",
    "602": "冲撞伤害",
    "800": "空",
    "801": "异邦铁风",
    "802": "风涡剑",
    "803": "岩潮奔涌",
}

FAKE_CURVE = [
    {"level": 90, "curveInfos": [
        {"type": "GROW_CURVE_HP_S5", "value": 10.0, "arith": "ARITH_MULTI"},
        {"type": "GROW_CURVE_ATTACK_S5", "value": 2.0, "arith": "ARITH_MULTI"},
        {"type": "GROW_CURVE_DEFENSE_S5", "value": 1.5, "arith": "ARITH_MULTI"},
    ]},
    {"level": 100, "curveInfos": [
        {"type": "GROW_CURVE_HP_S5", "value": 11.0, "arith": "ARITH_MULTI"},
        {"type": "GROW_CURVE_ATTACK_S5", "value": 2.2, "arith": "ARITH_MULTI"},
        {"type": "GROW_CURVE_DEFENSE_S5", "value": 1.65, "arith": "ARITH_MULTI"},
    ]},
]

FAKE_PROMOTE = [
    {"avatarPromoteId": 900, "unlockMaxLevel": 80, "addProps": [
        {"propType": "FIGHT_PROP_BASE_HP", "value": 1500.0},
        {"propType": "FIGHT_PROP_BASE_ATTACK", "value": 30.0},
        {"propType": "FIGHT_PROP_BASE_DEFENSE", "value": 80.0},
        {"propType": "FIGHT_PROP_CRITICAL_HURT", "value": 0.288},
    ]},
    {"avatarPromoteId": 900, "unlockMaxLevel": 90, "addProps": [
        {"propType": "FIGHT_PROP_BASE_HP", "value": 2000.0},
        {"propType": "FIGHT_PROP_BASE_ATTACK", "value": 40.0},
        {"propType": "FIGHT_PROP_BASE_DEFENSE", "value": 100.0},
        {"propType": "FIGHT_PROP_CRITICAL_HURT", "value": 0.384},
    ]},
]


def _insert_raw(db_path, name, data):
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO raw_table VALUES (?,?,?)",
                (name, json.dumps(data, ensure_ascii=False), "2026-09-02T00:00:00+00:00"),
            )
    finally:
        conn.close()


@pytest.fixture
def fake_db(tmp_path):
    db_path = tmp_path / "t.db"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(CREATE_RAW_TABLE)
    conn.close()
    _insert_raw(db_path, "TextMap_MediumCHS", TEXTS)
    _insert_raw(db_path, "AvatarExcelConfigData", [{
        "id": 90000001,
        "nameTextMapHash": 100,
        "descTextMapHash": 101,
        "qualityType": "QUALITY_ORANGE",
        "weaponType": "WEAPON_SWORD_ONE_HAND",
        "hpBase": 12000.0, "attackBase": 300.0, "defenseBase": 700.0,
        "critical": 0.05, "criticalHurt": 0.5, "chargeEfficiency": 1.0,
        "propGrowCurves": [
            {"type": "FIGHT_PROP_BASE_HP", "growCurve": "GROW_CURVE_HP_S5"},
            {"type": "FIGHT_PROP_BASE_ATTACK", "growCurve": "GROW_CURVE_ATTACK_S5"},
            {"type": "FIGHT_PROP_BASE_DEFENSE", "growCurve": "GROW_CURVE_DEFENSE_S5"},
        ],
        "skillDepotId": 9001, "avatarPromoteId": 900,
    }])
    _insert_raw(db_path, "AvatarCurveExcelConfigData", FAKE_CURVE)
    _insert_raw(db_path, "AvatarPromoteExcelConfigData", FAKE_PROMOTE)
    _insert_raw(db_path, "AvatarSkillExcelConfigData", [
        {"id": 90011, "nameTextMapHash": 200, "descTextMapHash": 201, "proudSkillGroupId": 91001},
        {"id": 90012, "nameTextMapHash": 210, "descTextMapHash": 211,
         "cdTime": 12, "proudSkillGroupId": 91002},
        {"id": 90013, "nameTextMapHash": 220, "descTextMapHash": 221,
         "cdTime": 15, "costElemVal": 60, "costElemType": "Rock",
         "proudSkillGroupId": 91003, "maxChargeNum": 1},
        {"id": 90014, "nameTextMapHash": 500, "descTextMapHash": 501, "proudSkillGroupId": 92001},
        {"id": 99999, "nameTextMapHash": 0, "descTextMapHash": 0},
    ])
    _insert_raw(db_path, "AvatarSkillDepotExcelConfigData", [{
        "id": 9001,
        "skills": [90011, 90012, 0, 0],
        "subSkills": [90014],
        "inherentProudSkillOpens": [{"proudSkillGroupId": 92002, "needAvatarPromoteLevel": 2}, {}],
        "energySkill": 90013,
        "talents": [9001, 9002, 9003, 9004, 9005, 9006],
    }])
    _insert_raw(db_path, "AvatarTalentExcelConfigData", [
        {"talentId": 9001, "nameTextMapHash": 300, "descTextMapHash": 301,
         "paramList": [0.1, 0, 0], "addProps": [{"propType": "FIGHT_PROP_CRITICAL", "value": 0.1}]},
    ])
    _insert_raw(db_path, "ProudSkillExcelConfigData", [
        {"proudSkillGroupId": 91001, "level": 1, "paramList": [0.5, 0.6],
         "paramDescList": [201, 0], "addProps": [{}]},
        {"proudSkillGroupId": 91001, "level": 2, "paramList": [0.55, 0.66],
         "paramDescList": [201, 0], "addProps": [{"propType": "FIGHT_PROP_ATTACK_PERCENT", "value": 0.2}]},
        {"proudSkillGroupId": 91003, "level": 9, "paramList": [2.9]},
        {"proudSkillGroupId": 91003, "level": 10, "paramList": [3.0]},
        {"proudSkillGroupId": 91003, "level": 13, "paramList": [3.5]},
        {"proudSkillGroupId": 91003, "level": 15, "paramList": [3.8]},
        {"proudSkillGroupId": 92001, "level": 1, "paramList": [0.8, 0, 0],
         "paramDescList": [602, 0, 0]},
        {"proudSkillGroupId": 92001, "level": 2, "paramList": [0.86, 0, 0],
         "paramDescList": [602, 0, 0]},
        {"proudSkillGroupId": 92002, "level": 1, "nameTextMapHash": 600,
         "descTextMapHash": 601, "paramList": [0.1], "paramDescList": []},
    ])
    return db_path


def test_character_mapping(fake_db):
    extractor.build_curated_layer(fake_db, "6.5", "release")
    conn = sqlite3.connect(fake_db)
    row = conn.execute(
        "SELECT name, rarity, element, grow_curves, hp_90, atk_90, def_90, hp_100,"
        " ascension_prop, ascension_value FROM character WHERE id=90000001"
    ).fetchone()
    conn.close()
    assert row[0] == "测试角色"
    assert row[1] == 5
    assert row[2] == "岩"  # 由 energySkill.costElemType=Rock 推断
    assert json.loads(row[3]) == {
        "HP": "GROW_CURVE_HP_S5", "ATK": "GROW_CURVE_ATTACK_S5", "DEF": "GROW_CURVE_DEFENSE_S5",
    }
    # T8 面板：base × 曲线 + 突破加值（最高档累计值）
    assert row[4] == 12000.0 * 10.0 + 2000.0   # hp_90
    assert row[5] == 300.0 * 2.0 + 40.0        # atk_90
    assert row[6] == 700.0 * 1.5 + 100.0       # def_90
    assert row[7] == 12000.0 * 11.0 + 2000.0   # hp_100
    assert row[8] == "暴击伤害"
    assert row[9] == pytest.approx(0.384)


def test_skill_param_level_filter(fake_db):
    """T8：组内有 10/13 级时只保留这两级；无 10/13 的组（91001）全保留。"""
    extractor.build_curated_layer(fake_db, "6.5", "release")
    conn = sqlite3.connect(fake_db)
    lv_91003 = [r[0] for r in conn.execute(
        "SELECT level FROM skill_param WHERE proud_skill_group_id=91003 ORDER BY level"
    )]
    lv_91001 = [r[0] for r in conn.execute(
        "SELECT level FROM skill_param WHERE proud_skill_group_id=91001 ORDER BY level"
    )]
    conn.close()
    assert lv_91003 == [10, 13]
    assert lv_91001 == [1, 2]


def test_traveler_multi_element_depots(fake_db):
    """T9：旅行者未被引用的元素 depot 按前缀归属，多元素时 element 置空。"""
    _insert_raw(fake_db, "AvatarExcelConfigData", [
        {"id": 90000001, "nameTextMapHash": 100, "descTextMapHash": 101,
         "qualityType": "QUALITY_ORANGE", "weaponType": "WEAPON_SWORD_ONE_HAND",
         "hpBase": 12000.0, "attackBase": 300.0, "defenseBase": 700.0,
         "skillDepotId": 9001, "avatarPromoteId": 900},
        {"id": 10000005, "nameTextMapHash": 800, "descTextMapHash": 101,
         "qualityType": "QUALITY_ORANGE", "weaponType": "WEAPON_SWORD_ONE_HAND",
         "hpBase": 10000.0, "attackBase": 200.0, "defenseBase": 600.0,
         "skillDepotId": 501, "avatarPromoteId": 900},
        {"id": 10000007, "nameTextMapHash": 800, "descTextMapHash": 101,
         "qualityType": "QUALITY_ORANGE", "weaponType": "WEAPON_SWORD_ONE_HAND",
         "hpBase": 10000.0, "attackBase": 200.0, "defenseBase": 600.0,
         "skillDepotId": 701, "avatarPromoteId": 900},
    ])
    # 男女主的 E/爆发/命座共享同一 id（真实数据如此：10097/51 等）
    _insert_raw(fake_db, "AvatarSkillDepotExcelConfigData", [
        {"id": 9001, "skills": [90011, 90012, 0, 0], "subSkills": [90014],
         "inherentProudSkillOpens": [], "energySkill": 90013,
         "talents": [9001, 0, 0, 0, 0, 0]},
        {"id": 501, "skills": [0, 0], "energySkill": 0, "talents": []},  # 占位 depot
        {"id": 502, "skills": [95011, 0], "energySkill": 95013, "talents": [9501, 0, 0, 0, 0, 0]},
        {"id": 503, "skills": [95021, 0], "energySkill": 95023, "talents": [9601, 0, 0, 0, 0, 0]},
        {"id": 701, "skills": [0, 0], "energySkill": 0, "talents": []},  # 荧占位 depot
        {"id": 702, "skills": [95051, 0], "energySkill": 95013, "talents": [9501, 0, 0, 0, 0, 0]},
    ])
    _insert_raw(fake_db, "AvatarSkillExcelConfigData", [
        {"id": 90011, "nameTextMapHash": 200, "descTextMapHash": 201, "proudSkillGroupId": 91001},
        {"id": 90012, "nameTextMapHash": 210, "descTextMapHash": 211, "cdTime": 12},
        {"id": 90013, "nameTextMapHash": 220, "descTextMapHash": 221, "costElemType": "Rock"},
        {"id": 95011, "nameTextMapHash": 801, "descTextMapHash": 201},
        {"id": 95013, "nameTextMapHash": 802, "descTextMapHash": 201, "costElemType": "Wind"},
        {"id": 95021, "nameTextMapHash": 801, "descTextMapHash": 201},
        {"id": 95023, "nameTextMapHash": 803, "descTextMapHash": 201, "costElemType": "Rock"},
        {"id": 95051, "nameTextMapHash": 801, "descTextMapHash": 201},
    ])
    _insert_raw(fake_db, "AvatarTalentExcelConfigData", [
        {"talentId": 9501, "nameTextMapHash": 300, "descTextMapHash": 301},
        {"talentId": 9601, "nameTextMapHash": 300, "descTextMapHash": 301},
    ])
    extractor.build_curated_layer(fake_db, "6.5", "release")
    conn = sqlite3.connect(fake_db)
    elem = conn.execute("SELECT element FROM character WHERE id=10000005").fetchone()[0]
    owned = {r[0] for r in conn.execute(
        "SELECT id FROM skill WHERE character_id=10000005"
    )}
    # 共享技能/命座：男女主都应有（荧 depot 702 与空 depot 502 共用 95013/9501）
    lumine_skill = {r[0] for r in conn.execute(
        "SELECT id FROM skill WHERE character_id=10000007"
    )}
    aether_talents = conn.execute(
        "SELECT COUNT(*) FROM talent WHERE character_id=10000005"
    ).fetchone()[0]
    lumine_talents = conn.execute(
        "SELECT COUNT(*) FROM talent WHERE character_id=10000007"
    ).fetchone()[0]
    conn.close()
    assert elem == ""  # 风+岩冲突 → 多元素置空
    assert {95011, 95013, 95021, 95023} <= owned  # 两个元素 depot 的技能都归属旅行者
    assert 95013 in lumine_skill and 95051 in lumine_skill
    assert aether_talents == 2 and lumine_talents == 1  # 共享命座双挂


def test_skill_slots_and_effect_type(fake_db):
    extractor.build_curated_layer(fake_db, "6.5", "release")
    conn = sqlite3.connect(fake_db)
    rows = {
        r[0]: r for r in conn.execute(
            "SELECT id, slot, effect_type, cooldown, energy_cost FROM skill WHERE character_id=90000001"
        )
    }
    orphan = conn.execute("SELECT COUNT(*) FROM skill WHERE id=99999").fetchone()[0]
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    conn.close()
    assert rows[90011][1] == "normal" and rows[90011][2] == "damage"
    assert rows[90012][1] == "skill" and rows[90012][2] == "heal"  # 无 buff 关键词 → heal
    # T9 优先级：buff > heal——「提升攻击力+恢复生命」的复合技能归 buff（班尼特Q 同类）
    assert rows[90013][1] == "burst" and rows[90013][2] == "buff"
    assert rows[90012][3] == 12 and rows[90013][4] == 60
    assert orphan == 0  # 无归属技能被跳过
    assert meta["game_version"] == "6.5"


def test_talent_and_skill_param(fake_db):
    stats = extractor.build_curated_layer(fake_db, "6.5", "release")
    conn = sqlite3.connect(fake_db)
    t = conn.execute("SELECT level, name, effect_type, add_props FROM talent WHERE id=9001").fetchone()
    sp = conn.execute(
        "SELECT param_list, add_props FROM skill_param WHERE proud_skill_group_id=91001 AND level=2"
    ).fetchone()
    tm = conn.execute("SELECT COUNT(*) FROM text_map").fetchone()[0]
    conn.close()
    assert t[0] == 1 and t[1] == "一命·测试" and t[2] == "buff"
    assert json.loads(t[3]) == {"FIGHT_PROP_CRITICAL": 0.1}
    assert json.loads(sp[0]) == [0.55, 0.66]
    assert json.loads(sp[1]) == {"FIGHT_PROP_ATTACK_PERCENT": 0.2}
    assert tm == len(TEXTS)
    assert stats.counts["character"] == 1 and stats.counts["skill"] == 3


def test_shared_depot_prefers_playable(fake_db):
    """测试角色复用正式角色 depot 时，技能归属可玩角色（id < 10000900）。"""
    _insert_raw(fake_db, "AvatarExcelConfigData", [
        {"id": 90000001, "nameTextMapHash": 100, "descTextMapHash": 101,
         "qualityType": "QUALITY_ORANGE", "weaponType": "WEAPON_SWORD_ONE_HAND",
         "hpBase": 1, "attackBase": 1, "defenseBase": 1,
         "skillDepotId": 9001, "avatarPromoteId": 900},
        {"id": 11000099, "nameTextMapHash": 100, "descTextMapHash": 101,
         "qualityType": "QUALITY_ORANGE", "weaponType": "WEAPON_SWORD_ONE_HAND",
         "hpBase": 1, "attackBase": 1, "defenseBase": 1,
         "skillDepotId": 9001, "avatarPromoteId": 900},
    ])
    extractor.build_curated_layer(fake_db, "6.5", "release")
    conn = sqlite3.connect(fake_db)
    owner = conn.execute(
        "SELECT DISTINCT character_id FROM skill WHERE id=90011"
    ).fetchall()
    elem = conn.execute(
        "SELECT element FROM character WHERE id=90000001"
    ).fetchone()[0]
    conn.close()
    assert [r[0] for r in owner] == [90000001]
    assert elem == "岩"


def test_extra_info_generated(fake_db):
    """附属技能与固有天赋进 extra_info，内容含名称/描述/参数。"""
    stats = extractor.build_curated_layer(fake_db, "6.5", "release")
    conn = sqlite3.connect(fake_db)
    rows = conn.execute(
        "SELECT info_type, content FROM extra_info WHERE target_type='character' AND target_id=90000001"
        " ORDER BY info_type"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    by_type = {r[0]: r[1] for r in rows}
    sub = by_type["sub_skill"]
    assert "陀螺·冲天转转" in sub
    assert "搭乘陀螺冲撞敌人" in sub
    assert "冲撞伤害" in sub
    assert "Lv1: 0.8" in sub and "Lv2: 0.86" in sub
    inh = by_type["inherent_skill"]
    assert "夜域赐礼·测试" in inh
    assert "在夜魂状态下移动速度提升" in inh
    assert stats.counts["extra_info"] == 2


def test_missing_raw_source_raises(tmp_path):
    db_path = tmp_path / "empty.db"
    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute(CREATE_RAW_TABLE)
    conn.close()
    with pytest.raises(extractor.ExtractorError):
        extractor.build_curated_layer(db_path, "6.5", "release")


def test_annotation_targets_include_subskills_and_inherent_skills(fake_db):
    from genshin_db import queries
    from genshin_db.annotation_store import (
        Annotation,
        add_annotation,
        create_annotation_tables,
        mark_orphans,
    )
    from genshin_db.targets import all_targets

    extractor.build_curated_layer(fake_db, "7.0", "release")
    create_annotation_tables(fake_db)
    targets = [("character", 90000001), ("skill", 90013), ("talent", 9001),
               ("skill_param", 91003), ("skill", 90014), ("skill_param", 92001),
               ("inherent_skill", 92002)]
    expected = set()
    for kind, target in targets:
        expected.add(add_annotation(fake_db, Annotation(kind, target, "approved", status="approved")))
    beta = add_annotation(fake_db, Annotation("character", 90000001, "beta only",
                                             status="approved", version_line="beta"))
    add_annotation(fake_db, Annotation("skill", 90013, "draft"))
    aggregate = queries.get_character_detail(fake_db, 90000001)
    assert {a["id"] for a in aggregate["annotations"]} == expected
    assert beta not in {a["id"] for a in queries.list_annotations(fake_db)["items"]}
    conn = sqlite3.connect(fake_db)
    try:
        assert mark_orphans(fake_db, all_targets(conn)) == 1
        assert conn.execute("SELECT status FROM annotation WHERE id=?", (beta,)).fetchone()[0] == "orphan"
        assert conn.execute("SELECT status FROM annotation WHERE target_type='inherent_skill'").fetchone()[0] == "approved"
    finally:
        conn.close()


def test_legacy_extra_info_targets_remain_queryable(fake_db):
    from genshin_db import queries
    from genshin_db.annotation_store import (
        Annotation,
        add_annotation,
        create_annotation_tables,
    )

    extractor.build_curated_layer(fake_db, "7.0", "release")
    create_annotation_tables(fake_db)
    conn = sqlite3.connect(fake_db)
    with conn:
        conn.execute("ALTER TABLE extra_info DROP COLUMN source_id")
        conn.execute("ALTER TABLE extra_info DROP COLUMN proud_skill_group_id")
    conn.close()
    ids = {add_annotation(fake_db, Annotation(kind, target, "legacy", status="approved"))
           for kind, target in [("skill", 90014), ("inherent_skill", 92002)]}
    assert {a["id"] for a in queries.get_character_detail(fake_db, 90000001)["annotations"]} == ids
