"""curated 抽取器：从 raw 层 JSON 抽取生成五张 curated 表（character/skill/talent/skill_param/text_map）。"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from genshin_db.descriptions import (
    ensure_description_columns,
    populate_descriptions,
    resolve_text,
)
from genshin_db.descriptions import (
    extra_content as _extra_content,
)
from genshin_db.display_names import populate_display_aliases
from genshin_db.locking import database_write
from genshin_db.validation import version_line as validate_line

ELEMENT_MAP = {
    "Fire": "火",
    "Water": "水",
    "Electric": "雷",
    "Ice": "冰",
    "Wind": "风",
    "Rock": "岩",
    "Grass": "草",
}

RARITY_MAP = {
    "QUALITY_PURPLE": 4,
    "QUALITY_ORANGE": 5,
    "QUALITY_ORANGE_SP": 5,
}

GROW_CURVE_KEY = {
    "FIGHT_PROP_BASE_HP": "HP",
    "FIGHT_PROP_BASE_ATTACK": "ATK",
    "FIGHT_PROP_BASE_DEFENSE": "DEF",
}
# 基础属性 → curve 类型对应的基础 prop（面板 = base × 曲线 + 突破加值[prop]）
BASE_PROP_OF_CURVE_KEY = {
    "HP": "FIGHT_PROP_BASE_HP",
    "ATK": "FIGHT_PROP_BASE_ATTACK",
    "DEF": "FIGHT_PROP_BASE_DEFENSE",
}

# effect_type 启发式关键词（一期粗糙标注，允许人工修正；优先级自上而下）
# T9 调整为 shield > buff > heal > damage > utility：班尼特Q 这类「伤害+治疗+加攻」
# 复合技能应归 buff，纯治疗归 heal
EFFECT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("shield", ("护盾",)),
    ("buff", ("提升", "提高", "加成", "降低", "减免", "抗打断", "恢复元素能量", "恢复能量")),
    ("heal", ("治疗", "恢复生命", "回复生命")),
    ("damage", ("伤害",)),
    ("utility", ("聚集", "牵引", "嘲讽", "位移", "冲刺", "攀爬", "滑翔", "游")),
]

# 突破属性 FIGHT_PROP → 中文名（T8）
ASCENSION_PROP_CN = {
    "FIGHT_PROP_CRITICAL": "暴击率",
    "FIGHT_PROP_CRITICAL_HURT": "暴击伤害",
    "FIGHT_PROP_ATTACK_PERCENT": "攻击力%",
    "FIGHT_PROP_HP_PERCENT": "生命值%",
    "FIGHT_PROP_DEFENSE_PERCENT": "防御力%",
    "FIGHT_PROP_ELEMENT_MASTERY": "元素精通",
    "FIGHT_PROP_CHARGE_EFFICIENCY": "元素充能效率",
    "FIGHT_PROP_HEAL_ADD": "治疗加成",
    "FIGHT_PROP_PHYSICAL_ADD_HURT": "物理伤害加成",
    "FIGHT_PROP_FIRE_ADD_HURT": "火元素伤害加成",
    "FIGHT_PROP_ELEC_ADD_HURT": "雷元素伤害加成",
    "FIGHT_PROP_WATER_ADD_HURT": "水元素伤害加成",
    "FIGHT_PROP_ICE_ADD_HURT": "冰元素伤害加成",
    "FIGHT_PROP_WIND_ADD_HURT": "风元素伤害加成",
    "FIGHT_PROP_ROCK_ADD_HURT": "岩元素伤害加成",
    "FIGHT_PROP_GRASS_ADD_HURT": "草元素伤害加成",
}
BASE_PROPS = {"FIGHT_PROP_BASE_HP", "FIGHT_PROP_BASE_ATTACK", "FIGHT_PROP_BASE_DEFENSE"}
PANEL_LEVELS = (90, 100)
# 旅行者多元素 depot 前缀 → 角色 id（T9；501/502… → 空，701/702… → 荧）
TRAVELER_PREFIX = {5: 10000005, 7: 10000007}
SKILL_PARAM_KEEP_LEVELS = (10, 13)

DDL = """
CREATE TABLE character (
    id INTEGER NOT NULL,
    version_line TEXT NOT NULL,
    name TEXT, desc TEXT,
    rarity INTEGER, weapon_type TEXT,
    element TEXT,
    hp_90 REAL,
    atk_90 REAL,
    def_90 REAL,
    hp_100 REAL,
    atk_100 REAL,
    def_100 REAL,
    ascension_prop TEXT,
    ascension_value REAL,
    crit_rate REAL,
    crit_dmg REAL,
    charge_efficiency REAL,
    grow_curves TEXT,
    skill_depot_id INTEGER,
    promote_id INTEGER,
    PRIMARY KEY (id, version_line)
);
CREATE INDEX idx_character_name ON character (name);

CREATE TABLE skill (
    id INTEGER NOT NULL,
    version_line TEXT NOT NULL,
    character_id INTEGER, slot TEXT,
    name TEXT, desc_template TEXT,
    cooldown REAL, energy_cost REAL, duration REAL,
    cost_stamina REAL,
    max_charge_num INTEGER,
    effect_type TEXT,
    proud_skill_group_id INTEGER,
    PRIMARY KEY (id, character_id, version_line)
);
CREATE INDEX idx_skill_char ON skill (character_id);

CREATE TABLE talent (
    id INTEGER NOT NULL,
    version_line TEXT NOT NULL,
    character_id INTEGER, level INTEGER,
    name TEXT, desc_template TEXT,
    effect_type TEXT,
    param_list TEXT,
    add_props TEXT,
    buff_zone TEXT,
    PRIMARY KEY (id, character_id, version_line)
);
CREATE INDEX idx_talent_char ON talent (character_id);

CREATE TABLE skill_param (
    proud_skill_group_id INTEGER NOT NULL,
    level INTEGER NOT NULL,
    version_line TEXT NOT NULL,
    param_list TEXT, param_desc TEXT,
    add_props TEXT, buff_zone TEXT,
    PRIMARY KEY (proud_skill_group_id, level, version_line)
);

CREATE TABLE text_map (
    hash TEXT NOT NULL,
    locale TEXT NOT NULL,
    text TEXT,
    PRIMARY KEY (hash, locale)
);

CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE extra_info (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    info_type TEXT NOT NULL,
    content TEXT NOT NULL,
    version_line TEXT NOT NULL,
    source_id INTEGER,
    proud_skill_group_id INTEGER
);
CREATE INDEX idx_extra_info_target ON extra_info (target_type, target_id);
"""

CURATED_TABLES = ["character", "skill", "talent", "skill_param", "text_map", "meta", "extra_info"]


class ExtractorError(Exception):
    """raw 层缺失或结构异常到无法继续时抛出。"""


@dataclass
class ExtractStats:
    counts: dict[str, int]
    warnings: list[str]


def _classify_effect(desc: str) -> str:
    for effect_type, keywords in EFFECT_RULES:
        if effect_type == "heal":
            # 治疗判定需「恢复/回复/治疗」与「生命」同时出现——「恢复大量生命值」不含「恢复生命」子串
            if any(v in desc for v in ("治疗", "恢复", "回复", "治愈")) and "生命" in desc:
                return effect_type
        elif any(kw in desc for kw in keywords):
            return effect_type
    return "other"


def _compact_add_props(add_props: list) -> str:
    pairs = {
        p["propType"]: p.get("value", 0)
        for p in add_props
        if isinstance(p, dict) and p.get("propType")
    }
    return json.dumps(pairs, ensure_ascii=False) if pairs else ""


@database_write
def build_curated_layer(
    db_path: Path,
    version: str,
    version_line: str,
    *,
    upstream_commit: str | None = None,
    content_timestamp: str | None = None,
) -> ExtractStats:
    validate_line(version_line)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        raw = {
            name: json.loads(row[0])
            for name in ("AvatarExcelConfigData", "AvatarSkillExcelConfigData",
                         "AvatarTalentExcelConfigData", "AvatarSkillDepotExcelConfigData",
                         "ProudSkillExcelConfigData", "TextMap_MediumCHS",
                         "AvatarCurveExcelConfigData", "AvatarPromoteExcelConfigData")
            if (row := conn.execute(
                "SELECT data_json FROM raw_table WHERE source_file=?", (name,)
            ).fetchone())
        }
        missing = {"AvatarExcelConfigData", "AvatarSkillExcelConfigData",
                   "AvatarTalentExcelConfigData", "AvatarSkillDepotExcelConfigData",
                   "ProudSkillExcelConfigData", "TextMap_MediumCHS"} - raw.keys()
        if missing:
            raise ExtractorError(f"raw 层缺少来源: {sorted(missing)}")

        textmap = {str(h): t for h, t in raw["TextMap_MediumCHS"].items()}

        def resolve(h) -> str:
            return resolve_text(textmap, h)

        warn: Counter[str] = Counter()
        warn_details: dict[str, list[str]] = {}

        def warnf(kind: str, detail: object) -> None:
            warn[kind] += 1
            details = warn_details.setdefault(kind, [])
            if len(details) < 3:
                details.append(str(detail))

        # 成长曲线索引：curve_type -> level -> 倍率（T8）
        curve_mult: dict[str, dict[int, float]] = {}
        for c in raw.get("AvatarCurveExcelConfigData", []):
            for info in c.get("curveInfos", []):
                if info.get("arith") == "ARITH_MULTI" and info.get("type"):
                    curve_mult.setdefault(info["type"], {})[c["level"]] = info.get("value", 1.0)
        if not curve_mult:
            warnf("成长曲线数据缺失", "AvatarCurveExcelConfigData 未入库或为空，面板列为 NULL")

        # 突破加值索引：avatarPromoteId -> 最高档 addProps（累计值；T8）
        promote_top: dict[int, dict[str, float]] = {}
        for p in raw.get("AvatarPromoteExcelConfigData", []):
            pid = p.get("avatarPromoteId")
            if pid is None:
                continue
            # 保留 unlockMaxLevel 最高档
            cur = p.get("unlockMaxLevel", 0)
            if pid not in promote_top or cur > promote_top[pid]["__level__"]:
                props = {"__level__": cur}
                for ap in p.get("addProps", []):
                    if ap.get("propType"):
                        props[ap["propType"]] = ap.get("value", 0)
                promote_top[pid] = props
        if not promote_top:
            warnf("突破数据缺失", "AvatarPromoteExcelConfigData 未入库或为空，面板列为 NULL")

        def panel(base, curve_type: str | None, level: int, adds: dict | None,
                  base_prop: str) -> float | None:
            """面板 = 基础值 × 曲线倍率 + 突破加值；数据缺失返回 None。"""
            if base is None or not curve_type or not adds:
                return None
            mult = curve_mult.get(curve_type, {}).get(level)
            if mult is None:
                warnf("成长曲线倍率缺失", f"{curve_type} Lv{level}")
                return None
            return base * mult + adds.get(base_prop, 0)

        # depot 归属：一个 depot 可能被多个 avatar 引用（测试角色复用正式角色的 depot）。
        # 可玩角色 id < 10000900（>= 该值为试用/测试/UGC 条目），共享时优先归属可玩角色。
        PLAYABLE_ID_MAX = 10000900
        depot_owner: dict[int, int] = {}
        for av in raw["AvatarExcelConfigData"]:
            depot_id = av.get("skillDepotId")
            if not depot_id:
                continue
            existing = depot_owner.get(depot_id)
            if existing is None:
                depot_owner[depot_id] = av["id"]
            else:
                ex_playable = existing < PLAYABLE_ID_MAX
                new_playable = av["id"] < PLAYABLE_ID_MAX
                if new_playable and not ex_playable:
                    warnf("depot 被多角色引用，改归可玩角色", f"depot={depot_id}: {existing} -> {av['id']}")
                    depot_owner[depot_id] = av["id"]
                elif new_playable == ex_playable:
                    warnf("depot 被多角色引用", f"depot={depot_id}: {existing} 与 {av['id']}")

        # 旅行者多元素 depot：avatar 只引用占位 depot（501/701），各元素 depot（502-507/702-707）
        # 无人引用，按前缀规则归属（T9）
        for depot in raw["AvatarSkillDepotExcelConfigData"]:
            did = depot.get("id")
            if did and did not in depot_owner and did // 100 in TRAVELER_PREFIX:
                depot_owner[did] = TRAVELER_PREFIX[did // 100]
                warnf("旅行者多元素 depot 前缀映射", f"depot={did} -> {TRAVELER_PREFIX[did // 100]}")

        skill_by_id = {s["id"]: s for s in raw["AvatarSkillExcelConfigData"] if "id" in s}

        # 技能归属与槽位：skills[0]=normal, skills[1]=skill, energySkill=burst
        # 归属为列表：旅行者的 E/Q/命座在男女主间共享同一 id（T9 实测 10097/51 等）
        skill_owner: dict[int, list[tuple[int, str]]] = {}
        talent_owner: dict[int, list[tuple[int, int]]] = {}

        def _own(mapping: dict, key: int, value: tuple) -> None:
            owners = mapping.setdefault(key, [])
            if value not in owners:
                owners.append(value)

        char_element: dict[int, str] = {}
        multi_element: set[int] = set()  # 多元素角色（旅行者）：元素冲突时置空
        for depot in raw["AvatarSkillDepotExcelConfigData"]:
            char_id = depot_owner.get(depot.get("id"))
            if char_id is None:
                warnf("depot 无对应角色", f"id={depot.get('id')}")
                continue
            skills_arr = depot.get("skills", [])
            for idx, slot in ((0, "normal"), (1, "skill")):
                if idx < len(skills_arr) and skills_arr[idx]:
                    _own(skill_owner, skills_arr[idx], (char_id, slot))
            burst_id = depot.get("energySkill")
            if burst_id:
                _own(skill_owner, burst_id, (char_id, "burst"))
                burst = skill_by_id.get(burst_id)
                elem_raw = burst.get("costElemType") if burst else None
                if elem_raw:
                    element = ELEMENT_MAP.get(elem_raw)
                    if element is None:
                        warnf("未知元素类型", elem_raw)
                    elif char_id in char_element and char_element[char_id] != element:
                        char_element.pop(char_id)
                        multi_element.add(char_id)
                        warnf("多元素角色（元素留空）", f"avatar_id={char_id}")
                    elif char_id not in multi_element:
                        char_element[char_id] = element
                else:
                    warnf("爆发技能缺 costElemType", f"skill_id={burst_id}")
            for i, tid in enumerate(depot.get("talents", [])):
                if tid:
                    _own(talent_owner, tid, (char_id, i + 1))

        characters, skills, talents, skill_params = [], [], [], []

        for av in raw["AvatarExcelConfigData"]:
            try:
                quality = av.get("qualityType", "")
                rarity = RARITY_MAP.get(quality)
                if rarity is None:
                    warnf("未知 qualityType", f"{quality} (avatar_id={av.get('id')})")
                curves = {
                    GROW_CURVE_KEY[c["type"]]: c.get("growCurve", "")
                    for c in av.get("propGrowCurves", [])
                    if c.get("type") in GROW_CURVE_KEY
                }
                char_id = av["id"]
                adds = promote_top.get(av.get("avatarPromoteId"))
                if adds is None:
                    warnf("突破组不存在", f"promoteId={av.get('avatarPromoteId')} (avatar_id={char_id})")
                bases = {"HP": av.get("hpBase"), "ATK": av.get("attackBase"), "DEF": av.get("defenseBase")}
                panels = {
                    f"{k.lower()}_{lv}": panel(bases[k], curves.get(k), lv, adds,
                                               BASE_PROP_OF_CURVE_KEY[k])
                    for k in ("HP", "ATK", "DEF") for lv in PANEL_LEVELS
                }
                # 突破属性：最高档 addProps 中除基础三维外的非零项
                asc_prop, asc_value = None, None
                if adds:
                    for prop, val in adds.items():
                        if prop != "__level__" and prop not in BASE_PROPS and val:
                            cn = ASCENSION_PROP_CN.get(prop)
                            if cn is None:
                                warnf("未知突破属性类型", f"{prop} (avatar_id={char_id})")
                                cn = prop
                            asc_prop, asc_value = cn, val
                            break
                characters.append((
                    char_id, version_line,
                    resolve(av.get("nameTextMapHash")), resolve(av.get("descTextMapHash")),
                    rarity, av.get("weaponType", ""), char_element.get(char_id, ""),
                    panels["hp_90"], panels["atk_90"], panels["def_90"],
                    panels["hp_100"], panels["atk_100"], panels["def_100"],
                    asc_prop, asc_value,
                    av.get("critical"), av.get("criticalHurt"), av.get("chargeEfficiency"),
                    json.dumps(curves, ensure_ascii=False),
                    av.get("skillDepotId"), av.get("avatarPromoteId"),
                ))
                if char_id not in char_element and char_id not in multi_element:
                    warnf("角色元素缺失", f"avatar_id={char_id}")
            except Exception as e:  # noqa: BLE001 - 单条目失败跳过并记 warning
                warnf("角色条目解析失败", f"avatar_id={av.get('id')}: {e}")

        for sk in raw["AvatarSkillExcelConfigData"]:
            try:
                owners = skill_owner.get(sk.get("id"))
                if not owners:
                    warnf("技能无归属角色（跳过）", f"skill_id={sk.get('id')}")
                    continue
                desc = resolve(sk.get("descTextMapHash"))
                for char_id, slot in owners:
                    skills.append((
                        sk["id"], version_line,
                        char_id, slot,
                        resolve(sk.get("nameTextMapHash")), desc,
                        sk.get("cdTime"), sk.get("costElemVal"), None,
                        sk.get("costStamina"), sk.get("maxChargeNum"),
                        _classify_effect(desc), sk.get("proudSkillGroupId"),
                    ))
            except Exception as e:  # noqa: BLE001
                warnf("技能条目解析失败", f"skill_id={sk.get('id')}: {e}")

        talent_by_id = {t["talentId"]: t for t in raw["AvatarTalentExcelConfigData"] if "talentId" in t}
        for talent_id, owners in talent_owner.items():
            t = talent_by_id.get(talent_id)
            if t is None:
                warnf("命座 id 在 AvatarTalent 中不存在", talent_id)
                continue
            try:
                desc = resolve(t.get("descTextMapHash"))
                for char_id, level in owners:
                    talents.append((
                        talent_id, version_line,
                        char_id, level,
                        resolve(t.get("nameTextMapHash")), desc,
                        _classify_effect(desc),
                        json.dumps(t.get("paramList", []), ensure_ascii=False),
                        _compact_add_props(t.get("addProps", [])),
                        None,  # buff_zone：由 labeler 二阶段标注（T10）
                    ))
            except Exception as e:  # noqa: BLE001
                warnf("命座条目解析失败", f"talent_id={talent_id}: {e}")

        proud_groups: dict[int, list[dict]] = {}
        for ps in raw["ProudSkillExcelConfigData"]:
            try:
                group_id = ps.get("proudSkillGroupId")
                level = ps.get("level")
                if group_id is None or level is None:
                    warnf("ProudSkill 条目缺 group/level", ps.get("proudSkillId"))
                    continue
                proud_groups.setdefault(group_id, []).append(ps)
            except Exception as e:  # noqa: BLE001
                warnf("ProudSkill 条目解析失败", f"{ps.get('proudSkillId')}: {e}")

        # T8：只保留 10/13 级（13 级=命座+3 后）；组内两级都无则全保留
        for group_id, entries in proud_groups.items():
            levels = {e["level"] for e in entries}
            keep = [lv for lv in SKILL_PARAM_KEEP_LEVELS if lv in levels]
            for ps in entries:
                if keep and ps["level"] not in keep:
                    continue
                param_desc = [
                    resolve(h) for h in ps.get("paramDescList", []) if h
                ]
                skill_params.append((
                    group_id, ps["level"], version_line,
                    json.dumps(ps.get("paramList", []), ensure_ascii=False),
                    json.dumps(param_desc, ensure_ascii=False),
                    _compact_add_props(ps.get("addProps", [])),
                    None,
                ))

        # extra_info 补充信息层：附属技能与固有天赋（信息完整性优先，文本面向 LLM）
        extras: list[tuple] = []
        char_names = {row[0]: row[2] for row in characters}
        for depot in raw["AvatarSkillDepotExcelConfigData"]:
            char_id = depot_owner.get(depot.get("id"))
            if char_id is None:
                continue
            cname = char_names.get(char_id, "")
            sub_ids = [s for s in depot.get("subSkills", []) if s]
            sub_ids += [s for s in depot.get("skills", [])[2:] if s]
            for sid in sub_ids:
                sk = skill_by_id.get(sid)
                if sk is None:
                    warnf("附属技能 id 无对应技能条目", f"skill_id={sid}")
                    continue
                gid = sk.get("proudSkillGroupId")
                content = _extra_content(
                    "附属技能", resolve(sk.get("nameTextMapHash")), gid,
                    resolve(sk.get("descTextMapHash")),
                    proud_groups.get(gid, []), resolve,
                )
                extras.append(("character", char_id, "sub_skill",
                               f"[{cname}] " + content, version_line, sid, gid))
            for item in depot.get("inherentProudSkillOpens", []):
                # 条目为 {"proudSkillGroupId": x, "needAvatarPromoteLevel": n} 或空 dict
                gid = item.get("proudSkillGroupId") if isinstance(item, dict) else item
                if not gid:
                    continue
                entries = proud_groups.get(gid, [])
                if not entries:
                    warnf("固有天赋参数组不存在", f"group={gid}")
                    continue
                first = entries[0]
                content = _extra_content(
                    "固有天赋", resolve(first.get("nameTextMapHash")), gid,
                    resolve(first.get("descTextMapHash")), entries, resolve,
                )
                need = item.get("needAvatarPromoteLevel") if isinstance(item, dict) else None
                if need:
                    content += f"\n解锁条件：角色突破 {need}"
                extras.append(("character", char_id, "inherent_skill",
                               f"[{cname}] " + content, version_line, gid, gid))

        # 升级旧库只增列；共享文本/另一版本线/自建层不能因本线重建被清空。
        conn.executescript(DDL.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ")
                           .replace("CREATE INDEX ", "CREATE INDEX IF NOT EXISTS "))
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            ensure_description_columns(conn)
            for table in ("character", "skill", "talent", "skill_param", "extra_info"):
                conn.execute(f"DELETE FROM {table} WHERE version_line=?", (version_line,))
            conn.executemany(
                "INSERT OR REPLACE INTO character VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                characters,
            )
            conn.executemany(
                "INSERT OR REPLACE INTO skill (id,version_line,character_id,slot,name,desc_template,"
                "cooldown,energy_cost,duration,cost_stamina,max_charge_num,effect_type,"
                "proud_skill_group_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                skills,
            )
            conn.executemany(
                "INSERT OR REPLACE INTO talent (id,version_line,character_id,level,name,"
                "desc_template,effect_type,param_list,add_props,buff_zone) VALUES (?,?,?,?,?,?,?,?,?,?)",
                talents,
            )
            conn.executemany(
                "INSERT OR REPLACE INTO skill_param VALUES (?,?,?,?,?,?,?)",
                skill_params,
            )
            conn.executemany(
                "INSERT OR REPLACE INTO text_map VALUES (?,?,?)",
                ((h, "CHS", t) for h, t in textmap.items()),
            )
            conn.executemany(
                "INSERT INTO extra_info (target_type, target_id, info_type, content, version_line,"
                " source_id, proud_skill_group_id) VALUES (?,?,?,?,?,?,?)",
                extras,
            )
            built_at = content_timestamp or datetime.now(UTC).isoformat()
            meta_items = [(f"game_version:{version_line}", version),
                          (f"built_at:{version_line}", built_at),
                          ("raw_version_line", version_line),
                          ("raw_upstream_commit", upstream_commit),
                          (f"upstream_commit:{version_line}", upstream_commit),
                          ("description_schema_version", "1")]
            if version_line == "release" or not conn.execute(
                    "SELECT 1 FROM meta WHERE key='game_version'"
            ).fetchone():
                meta_items += [("game_version", version), ("built_at", built_at)]
            conn.executemany(
                "INSERT OR REPLACE INTO meta VALUES (?,?)",
                meta_items,
            )
            populate_descriptions(conn, version_line, raw, upstream_commit, trusted=True)
            populate_display_aliases(conn, version_line)

        counts = {
            "character": len(characters),
            "skill": len(skills),
            "talent": len(talents),
            "skill_param": len(skill_params),
            "text_map": len(textmap),
            "extra_info": len(extras),
        }
        warning_lines = []
        for kind, n in warn.most_common():
            samples = "; ".join(warn_details.get(kind, []))
            line = f"{kind} (x{n})"
            if samples:
                line += f"  示例: {samples}"
            warning_lines.append(line)
        return ExtractStats(counts=counts, warnings=warning_lines)
    finally:
        conn.close()


if __name__ == "__main__":
    stats = build_curated_layer(Path("data/build/raw-check.db"), "unknown", "release")
    for table, count in stats.counts.items():
        print(f"{table}: {count}")
    print(f"warnings: {len(stats.warnings)} 类")
    for w in stats.warnings[:10]:
        print(f"  {w}")
