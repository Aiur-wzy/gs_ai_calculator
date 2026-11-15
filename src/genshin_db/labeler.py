"""数据标注器（T10）：buff_zone 机器标注。

- 信号源 = desc_template + param_desc 文本（add_props 上游实测几乎全空，不可用）。
- 值域 = genshin_engine.constants.BUFF_ZONES（引擎枚举唯一源，不另建编号映射）。
- 存储 = TEXT 列 JSON 数组；无拐力信号则为 NULL。
- 调用时机 = 隔离候选库的 curated 构建后二次 UPDATE；历史 `db relabel` 公开入口已经弃用。
- 规则可迭代；持久修正必须形成带来源的内容 change set，经用户批准后随完整新数据库版本发布，不得写活动库。

方法：按句切分（。；换行为界），逐句匹配信号。关键语义区分：
- 面板区二分：「基于/相当于…」转化来源或固定点数 → stat_add；
  直接百分比（% 或 {paramN:..P} 占位符）→ stat_pct。伤害基础区 base_add/base_mult 独立。
- 增伤区互斥：同句出现反应词 → reaction_*；出现月曜/星烁词 → lunar_*/stellar_*；
  否则 → dmg_bonus。月曜/星烁句内再按 擢升/基础伤害/倍率或泛化增伤 细分
  elevate/base/mult；裸「擢升」无月曜/星烁上下文时不标（保守，留待人工校订）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from genshin_db.locking import database_write
from genshin_db.validation import version_line as validate_line
from genshin_engine.constants import BUFF_ZONES

_SENT_SPLIT = re.compile(r"[。；\n]")

# ---- 句子级信号 ----
_ATTR_GRANT = re.compile(
    r"(?:攻击力|生命值|防御力)[^。；\n]{0,6}(?:提高|提升|加成|增加)"
    r"|获得[^。；\n]{0,8}(?:攻击力|生命值|防御力)[^。；\n]{0,4}(?:加成|提升|提高)"
)
_CONVERT_WORD = re.compile(r"基于|相当于|转化")
# 「（基础）属性的N%」以属性为来源 → stat_add，区别于 攻击力700% 的伤害倍率写法。
_ATTR_OF_PERCENT = re.compile(r"(?:基础)?(?:攻击力|生命值|防御力)的\s*\d+(?:\.\d+)?\s*%"
                              r"|(?:基础)?(?:攻击力|生命值|防御力)的\s*\{param\d+:[^}]*P\}")
_ATTR_WORD = re.compile(r"攻击力|生命值|防御力")
_PERCENT_HINT = re.compile(r"%|\{param\d+:[^}]*P\}|比例")
_FIXED_HINT = re.compile(r"\d+\s*点|\{param\d+:[^}]*[IF]\}\s*点|固定值")
_LUNAR = re.compile(r"月曜|月感电|月绽放|月结晶")
_STELLAR = re.compile(r"星烁|星超导|星扩散")
_DMG_UP = re.compile(r"(?<!暴击)伤害[^。；\n]{0,4}(?:提升|提高|加成)|增伤")
_MULT_UP = re.compile(r"倍率[^。；\n]{0,4}(?:提升|提高)")
_BASE_DMG_UP = re.compile(r"基础伤害[^。；\n]{0,6}(?:提高|提升|加成)")
_ELEVATE = re.compile(r"擢升")

# ---- 逐句规则表（无互斥需求的 zone） ----
_RULES: list[tuple[str, re.Pattern]] = [
    ("def_ignore", re.compile(r"无视[^。；\n]{0,10}防御")),
    ("def_reduction", re.compile(
        r"防御力[^。；\n]{0,6}降低|降低[^。；\n]{0,8}(?:敌人|敌方)[^。；\n]{0,6}防御")),
    ("res_shred", re.compile(r"抗性[^。；\n]{0,6}降低|降低[^。；\n]{0,10}抗性|减[^。；\n]{0,2}抗")),
    ("crit_rate", re.compile(r"暴击率[^。；\n]{0,4}(?:提升|提高|加成|固定|\+)")),
    ("crit_dmg", re.compile(r"暴击伤害[^。；\n]{0,4}(?:提升|提高|加成|固定|\+)")),
    ("em", re.compile(r"元素精通[^。；\n]{0,4}(?:提升|提高|加成|\+)|获得[^。；\n]{0,6}元素精通")),
]
_REACTION_WORD = re.compile(
    r"蒸发|融化|扩散|感电|超载|超导|碎冰|燃烧|绽放|激化|剧变|增幅|反应"
)
_REACTION_FLAT = re.compile(r"伤害[^。；\n]{0,6}(?:提高|提升)[^。；\n]{0,8}(?:点|固定值)")


def zones_in_text(text: str | None) -> list[str]:
    """从文本标注拐力乘区，输出按引擎 BUFF_ZONES 枚举序去重。"""
    if not text:
        return []
    sentences = [s for s in _SENT_SPLIT.split(text) if s.strip()]

    # 预扫描：转化来源（基于/相当于/转化/属性的N%）命中的属性 → 抑制其他句对同属性的
    # stat_pct 直判（班尼特Q：desc 句「基于基础攻击力」+ param 列「攻击力加成比例」
    # 属同一 buff，不可双标）
    suppressed: set[str] = set()
    for sent in sentences:
        if _ATTR_GRANT.search(sent) and (_CONVERT_WORD.search(sent)
                                         or _ATTR_OF_PERCENT.search(sent)):
            suppressed.update(_ATTR_WORD.findall(sent))

    found: set[str] = set()
    for sent in sentences:
        lunar = bool(_LUNAR.search(sent))
        stellar = bool(_STELLAR.search(sent))

        # 面板属性：转化/固定 → stat_add；百分比 → stat_pct。
        if _ATTR_GRANT.search(sent):
            if _CONVERT_WORD.search(sent) or _ATTR_OF_PERCENT.search(sent) or (
                    _FIXED_HINT.search(sent) and not _PERCENT_HINT.search(sent)):
                found.add("stat_add")
            elif not set(_ATTR_WORD.findall(sent)) & suppressed:
                found.add("stat_pct")

        for zone, pat in _RULES:
            if pat.search(sent):
                found.add(zone)

        # 增伤区互斥分支
        if lunar or stellar:
            pre = "stellar" if stellar else "lunar"
            if _ELEVATE.search(sent):
                found.add(f"{pre}_elevate")
            if _BASE_DMG_UP.search(sent):
                found.add(f"{pre}_base")
            elif _DMG_UP.search(sent) or _MULT_UP.search(sent):
                found.add(f"{pre}_mult")
        elif _REACTION_WORD.search(sent):
            if _DMG_UP.search(sent):
                found.add("reaction_flat" if _REACTION_FLAT.search(sent)
                          else "reaction_bonus")
        elif _DMG_UP.search(sent):
            found.add("dmg_bonus")

    return [z for z in BUFF_ZONES if z in found]


def _label_rows(conn: sqlite3.Connection, select_sql: str, update_sql: str,
                text_of, args: tuple) -> tuple[int, int]:
    labeled = total = 0
    with conn:
        for row in conn.execute(select_sql, args):
            zones = zones_in_text(text_of(row))
            total += 1
            labeled += bool(zones)
            conn.execute(update_sql,
                         (json.dumps(zones, ensure_ascii=False) if zones else None,
                          *row[:3]))
    return labeled, total


@database_write
def apply_labels(db_path: Path, version_line: str | None = None) -> dict:
    """对 talent 与 skill_param 两表标注 buff_zone，返回覆盖率报告。

    version_line=None 时全表标注。
    """
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "labeler.apply_labels")
    validate_line(version_line, nullable=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        where = " WHERE version_line=?" if version_line else ""
        args = (version_line,) if version_line else ()

        # talent：信号 = desc_template
        t_labeled, t_total = _label_rows(
            conn,
            f"SELECT id, character_id, version_line, desc_template FROM talent{where}",
            "UPDATE talent SET buff_zone=? WHERE id=? AND character_id=? AND version_line=?",
            lambda r: r["desc_template"],
            args,
        )

        # skill_param：信号 = param_desc + 所属技能 desc_template
        group_desc = {
            (r["proud_skill_group_id"], r["version_line"]): r["desc_template"] or ""
            for r in conn.execute(
                "SELECT proud_skill_group_id, version_line, MAX(desc_template) AS desc_template "
                "FROM skill WHERE proud_skill_group_id IS NOT NULL"
                + (" AND version_line=?" if version_line else "")
                + " GROUP BY proud_skill_group_id, version_line",
                args,
            )
        }
        sp_labeled, sp_total = _label_rows(
            conn,
            "SELECT proud_skill_group_id, level, version_line, param_desc "
            f"FROM skill_param{where}",
            "UPDATE skill_param SET buff_zone=? "
            "WHERE proud_skill_group_id=? AND level=? AND version_line=?",
            lambda r: f"{group_desc.get((r['proud_skill_group_id'], r['version_line']), '')}\n"
                      f"{r['param_desc'] or ''}",
            args,
        )

        dist: dict[str, int] = {}
        for table in ("talent", "skill_param"):
            for (bz,) in conn.execute(
                f"SELECT buff_zone FROM {table} WHERE buff_zone IS NOT NULL"
                + (" AND version_line=?" if version_line else ""), args,
            ):
                for z in json.loads(bz):
                    dist[z] = dist.get(z, 0) + 1
        return {
            "talent": {"labeled": t_labeled, "total": t_total},
            "skill_param": {"labeled": sp_labeled, "total": sp_total},
            "zone_distribution": dict(sorted(dist.items(), key=lambda kv: -kv[1])),
        }
    finally:
        conn.close()
