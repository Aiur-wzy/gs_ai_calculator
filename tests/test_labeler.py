"""labeler（T10 buff_zone 机器标注）测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from genshin_db.labeler import apply_labels, zones_in_text
from genshin_engine.constants import BUFF_ZONES


class TestZonesInText:
    def test_empty_and_none(self):
        assert zones_in_text(None) == []
        assert zones_in_text("") == []
        assert zones_in_text("普通攻击，进行至多五段连续剑击。") == []

    def test_stat_add_by_convert_word(self):
        # 班尼特Q 标杆：「基于…基础攻击力」转化来源 → 面板固定加值
        zones = zones_in_text("基于班尼特的基础攻击力，以一定比例获得攻击力加成")
        assert zones == ["stat_add"]

    def test_stat_add_by_fixed_value(self):
        zones = zones_in_text("获得100点攻击力提升")
        assert zones == ["stat_add"]

    def test_stat_pct_by_percent(self):
        zones = zones_in_text("队伍中所有角色的攻击力提升{param1:percent}")
        assert zones == ["stat_pct"]

    def test_crit_and_em(self):
        zones = zones_in_text("暴击率提升{param1:percent}，暴击伤害提高{param2:percent}。"
                              "元素精通提升80。")
        assert zones == ["crit_rate", "crit_dmg", "em"]

    def test_def_and_res(self):
        assert zones_in_text("使敌人的防御力降低{param1:percent}") == ["def_reduction"]
        assert zones_in_text("无视敌人60%的防御力") == ["def_ignore"]
        assert zones_in_text("敌人的冰元素抗性降低{param1:percent}") == ["res_shred"]

    def test_generic_dmg_bonus(self):
        assert zones_in_text("造成的伤害提升{param1:percent}") == ["dmg_bonus"]

    def test_reaction_zones(self):
        # 反应词 + 伤害提升 → reaction_bonus；固定点数 → reaction_flat
        assert zones_in_text("蒸发反应造成的伤害提升15%") == ["reaction_bonus"]
        assert zones_in_text("超载反应造成的伤害提高100点") == ["reaction_flat"]

    def test_lunar_zones(self):
        assert zones_in_text("月感电反应造成的伤害提升20%") == ["lunar_mult"]
        assert zones_in_text("月曜反应造成的基础伤害提高") == ["lunar_base"]
        assert zones_in_text("触发月绽放时，获得擢升效果") == ["lunar_elevate"]

    def test_stellar_zones(self):
        assert zones_in_text("星超导造成的伤害提升15%") == ["stellar_mult"]
        assert zones_in_text("星烁反应的基础伤害提高{param1:percent}") == ["stellar_base"]

    def test_bare_elevate_not_labeled(self):
        # 裸「擢升」无月曜/星烁上下文 → 保守不标
        assert zones_in_text("获得一层擢升，持续20秒") == []

    def test_multi_zone_and_order(self):
        # 按引擎枚举序输出，面板阶段在伤害阶段之前。
        zones = zones_in_text("暴击率提升10%。攻击力提升{param1:percent}。无视敌人防御")
        assert zones == ["stat_pct", "crit_rate", "def_ignore"]
        assert zones == [z for z in BUFF_ZONES if z in set(zones)]

    def test_stat_add_by_attr_of_percent(self):
        # 班尼特C1 标杆：「追加…基础攻击力的20%」无转化词，但「属性的N%」= 来源转化
        zones = zones_in_text(
            "美妙旅程的攻击力提升效果不再有生命值限制，"
            "数值上追加班尼特基础攻击力的20%。")
        assert zones == ["stat_add"]

    def test_damage_multiplier_not_stat_add(self):
        # 「攻击力700%的草元素伤害」是倍率写法（无 的），不得误判 stat_add
        assert zones_in_text("造成基尼奇的攻击力700%的草元素伤害") == []

    def test_heal_text_no_zone(self):
        assert zones_in_text("基于生命值上限，恢复队伍中所有角色的生命值") == []


def _make_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    with conn:
        conn.executescript("""
        CREATE TABLE skill (
            id INTEGER, version_line TEXT, character_id INTEGER,
            desc_template TEXT, proud_skill_group_id INTEGER
        );
        CREATE TABLE talent (
            id INTEGER NOT NULL, version_line TEXT NOT NULL,
            character_id INTEGER, level INTEGER, name TEXT, desc_template TEXT,
            buff_zone TEXT, PRIMARY KEY (id, character_id, version_line)
        );
        CREATE TABLE skill_param (
            proud_skill_group_id INTEGER NOT NULL, level INTEGER NOT NULL,
            version_line TEXT NOT NULL, param_list TEXT, param_desc TEXT,
            buff_zone TEXT,
            PRIMARY KEY (proud_skill_group_id, level, version_line)
        );
        """)
        conn.execute(
            "INSERT INTO skill VALUES (101, 'release', 1001, ?, 9001)",
            ("元素爆发：基于班尼特的基础攻击力，获得攻击力加成",),
        )
        conn.executemany(
            "INSERT INTO talent VALUES (?, 'release', 1001, ?, ?, ?, NULL)",
            [
                (201, 1, "命座1", "造成的伤害提升20%"),
                (202, 2, "命座2", "普通攻击命中后恢复生命值"),
            ],
        )
        conn.executemany(
            "INSERT INTO skill_param VALUES (9001, ?, 'release', '[]', ?, NULL)",
            [(1, "技能伤害"), (10, "攻击力加成比例")],
        )
    conn.close()


class TestApplyLabels:
    def test_default_relabel_keeps_version_lines_separate(self, tmp_path):
        db = tmp_path / "t.db"
        _make_db(db)
        conn = sqlite3.connect(db)
        with conn:
            conn.execute("INSERT INTO skill VALUES(101,'beta',1001,'暴击率提高20%',9001)")
            conn.execute("INSERT INTO skill_param VALUES(9001,10,'beta','[]','[]',NULL)")
        conn.close()
        apply_labels(db)
        conn = sqlite3.connect(db)
        try:
            rows = dict(conn.execute("SELECT version_line,buff_zone FROM skill_param WHERE level=10"))
            assert json.loads(rows["release"]) == ["stat_add"]
            assert json.loads(rows["beta"]) == ["crit_rate"]
        finally:
            conn.close()

    def test_labels_and_report(self, tmp_path):
        db = tmp_path / "t.db"
        _make_db(db)
        report = apply_labels(db)

        assert report["talent"] == {"labeled": 1, "total": 2}
        assert report["skill_param"]["total"] == 2
        assert "dmg_bonus" in report["zone_distribution"]

        conn = sqlite3.connect(db)
        try:
            rows = dict(conn.execute(
                "SELECT id, buff_zone FROM talent ORDER BY id").fetchall())
            assert json.loads(rows[201]) == ["dmg_bonus"]
            assert rows[202] is None

            sp = dict(conn.execute(
                "SELECT level, buff_zone FROM skill_param").fetchall())
            # level 1 信号 = 技能 desc（基于…攻击力 → stat_add）
            assert json.loads(sp[1]) == ["stat_add"]
            # level 10 的 param_desc「攻击力加成比例」被 desc 转化上下文抑制 → 不双标 stat_pct
            assert json.loads(sp[10]) == ["stat_add"]
        finally:
            conn.close()

    def test_version_line_filter(self, tmp_path):
        db = tmp_path / "t.db"
        _make_db(db)
        conn = sqlite3.connect(db)
        with conn:
            conn.execute(
                "INSERT INTO talent VALUES (301, 'beta', 1001, 1, 'x', "
                "'暴击伤害提升60%', NULL)")
        conn.close()

        report = apply_labels(db, "release")
        assert report["talent"]["total"] == 2  # 只数 release

        conn = sqlite3.connect(db)
        try:
            beta_row = conn.execute(
                "SELECT buff_zone FROM talent WHERE version_line='beta'").fetchone()
            assert beta_row[0] is None  # beta 行未被触碰
        finally:
            conn.close()
