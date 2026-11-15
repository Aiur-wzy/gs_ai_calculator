"""装备规范层、来源校验与组件发布状态。成长数值和被动效果独立保存。"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from genshin_db.descriptions import DescriptionIndex
from genshin_db.display_names import populate_display_aliases
from genshin_db.locking import database_write
from genshin_db.raw_store import RAW_SOURCES
from genshin_db.validation import version_line as validate_line

SCHEMA_VERSION = 1
MAX_PUBLIC_ID = 2**53 - 1
WEAPON_TYPES = ("WEAPON_SWORD_ONE_HAND", "WEAPON_CLAYMORE", "WEAPON_POLE",
                "WEAPON_BOW", "WEAPON_CATALYST", "WEAPON_CROSSBOW")
EQUIP_FIELDS = {"EQUIP_BRACER": "flowerId", "EQUIP_NECKLACE": "leatherId",
                "EQUIP_SHOES": "sandId", "EQUIP_RING": "cupId", "EQUIP_DRESS": "capId"}
PROPERTIES = {
    "FIGHT_PROP_" + key: {"name": name, "unit": unit}
    for key, name, unit in (
        ("BASE_ATTACK", "基础攻击力", "flat"), ("HP", "生命值", "flat"),
        ("ATTACK", "攻击力", "flat"), ("DEFENSE", "防御力", "flat"),
        ("ELEMENT_MASTERY", "元素精通", "flat"), ("HP_PERCENT", "生命值", "ratio"),
        ("ATTACK_PERCENT", "攻击力", "ratio"), ("DEFENSE_PERCENT", "防御力", "ratio"),
        ("CRITICAL", "暴击率", "ratio"), ("CRITICAL_HURT", "暴击伤害", "ratio"),
        ("CHARGE_EFFICIENCY", "元素充能效率", "ratio"), ("HEAL_ADD", "治疗加成", "ratio"),
        ("PHYSICAL_ADD_HURT", "物理伤害加成", "ratio"), ("ADD_HURT", "伤害加成", "ratio"),
        ("SHIELD_COST_MINUS_RATIO", "护盾强效", "ratio"),
    )
}
for _code, _name in (("FIRE", "火"), ("ELEC", "雷"), ("WATER", "水"), ("WIND", "风"),
                     ("ROCK", "岩"), ("GRASS", "草"), ("ICE", "冰")):
    PROPERTIES[f"FIGHT_PROP_{_code}_ADD_HURT"] = {"name": f"{_name}元素伤害加成", "unit": "ratio"}
    PROPERTIES[f"FIGHT_PROP_{_code}_SUB_HURT"] = {"name": f"{_name}元素抗性", "unit": "ratio"}
for _code in ("HEALED_ADD", "PHYSICAL_SUB_HURT", "SKILL_CD_MINUS_RATIO", "SPEED_PERCENT", "SUB_HURT"):
    PROPERTIES[f"FIGHT_PROP_{_code}"] = {"name": f"FIGHT_PROP_{_code}", "unit": "unknown"}

_NAME = "name TEXT,name_text_map_hash TEXT,name_source TEXT,name_status TEXT"
_DESC = "desc_template TEXT,desc_source TEXT,desc_status TEXT"
_SOURCE = "source_missing_fields_json TEXT NOT NULL,source_refs_json TEXT NOT NULL"
# Explicit relational columns, plus immutable field provenance. JSON retains ordered raw arrays.
TABLES = {
    "weapon": ("id", (f"id INTEGER,{_NAME},{_DESC},rarity INTEGER,weapon_type TEXT,"
               "promote_group_id INTEGER,icon TEXT,awaken_icon TEXT,skill_affix_ids_json TEXT")),
    "weapon_property": ("weapon_id,source_index", ("weapon_id INTEGER,source_index INTEGER,"
                        "prop_type TEXT,curve_type TEXT,init_value REAL")),
    "weapon_curve": ("curve_type,level", "curve_type TEXT,level INTEGER,arith TEXT,value REAL"),
    "weapon_promote": ("promote_group_id,promote_level", ("promote_group_id INTEGER,"
                       "promote_level INTEGER,unlock_max_level INTEGER,add_props_json TEXT")),
    "weapon_effect": ("weapon_id,source_index,source_level", ("weapon_id INTEGER,"
                      "source_index INTEGER,source_level INTEGER,affix_group_id INTEGER,"
                      "affix_id INTEGER,refinement INTEGER")),
    "weapon_codex": ("id", ("id INTEGER,weapon_id INTEGER,sort_order INTEGER,"
                     "is_disuse INTEGER,show_only_unlocked INTEGER")),
    "equip_affix": ("affix_id", (f"affix_id INTEGER,affix_group_id INTEGER,source_level INTEGER,"
                    f"{_NAME},{_DESC},param_list_json TEXT,add_props_json TEXT,open_config TEXT")),
    "artifact_set": ("id", ("id INTEGER,affix_group_id INTEGER,required_counts_json TEXT,"
                     "contains_ids_json TEXT,source_rank_level INTEGER,icon TEXT,"
                     f"disable_filter INTEGER,bag_sort_value INTEGER,{_NAME}")),
    "artifact_set_effect": ("set_id,source_index", ("set_id INTEGER,source_index INTEGER,"
                            "required_count INTEGER,source_level INTEGER,affix_id INTEGER,"
                            "relation_status TEXT")),
    "artifact": ("id", (f"id INTEGER,set_id INTEGER,equip_type TEXT,rarity INTEGER,{_NAME},"
                 f"{_DESC},icon TEXT,main_prop_depot_id INTEGER,append_prop_depot_id INTEGER,"
                 "append_prop_num INTEGER,max_source_level INTEGER,add_prop_levels_json TEXT")),
    "artifact_codex": ("id", "id INTEGER,set_id INTEGER,rarity INTEGER,sort_order INTEGER"),
    "artifact_codex_item": ("codex_id,equip_type", ("codex_id INTEGER,equip_type TEXT,"
                           "artifact_id INTEGER,source_field TEXT")),
    "artifact_main_prop": ("id", "id INTEGER,depot_id INTEGER,prop_type TEXT,affix_name TEXT"),
    "artifact_substat": ("id", ("id INTEGER,depot_id INTEGER,prop_type TEXT,"
                         "source_group_id INTEGER,value REAL")),
    "artifact_level": ("rarity_key,source_level", ("rarity_key INTEGER,source_rank INTEGER,"
                       "source_level INTEGER,add_props_json TEXT,exp REAL")),
}
REQUIRED_TABLES = {*TABLES, "data_component", "source_manifest"}


class EquipmentError(ValueError):
    def __init__(self, message, reason="dataset_invalid", status=500, details=None):
        super().__init__(message)
        self.reason, self.status, self.details = reason, status, details


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def source_ref(table, key, field, commit):
    return {"table": table, "key": key, "field": field, "upstream_commit": commit}


def equipment_capabilities():
    return {"supported_schema_versions": [SCHEMA_VERSION], "weapon_types": list(WEAPON_TYPES),
            "equip_types": list(EQUIP_FIELDS), "properties": PROPERTIES,
            "catalog_scopes": ["codex", "all"], "value_statuses": ["present", "source_missing"],
            "annotation_targets": ["weapon", "artifact_set", "artifact", "equip_affix"],
            "formula_version": "weapon-growth-v1"}


def ensure_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS data_component (component TEXT,version_line TEXT,"
                 "schema_version INTEGER,upstream_commit TEXT,built_at TEXT,validation_json TEXT,"
                 "PRIMARY KEY(component,version_line))")
    conn.execute("CREATE TABLE IF NOT EXISTS source_manifest (component TEXT,version_line TEXT,"
                 "source_file TEXT,upstream_commit TEXT,sha256 TEXT,row_count INTEGER,imported_at TEXT,"
                 "PRIMARY KEY(component,version_line,source_file))")
    for table, (keys, columns) in TABLES.items():
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({columns},{_SOURCE},"
                     "version_line TEXT NOT NULL CHECK(version_line IN ('release','beta')),"
                     f"PRIMARY KEY({keys},version_line))")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_equipment_affix_group ON "
                 "equip_affix(affix_group_id,source_level,version_line)")
    for table, fields in {"weapon": "weapon_type,rarity,id", "artifact": "set_id,equip_type,rarity,id",
                          "equip_affix": "affix_group_id,source_level", "weapon_effect": "affix_id",
                          "artifact_set_effect": "affix_id", "artifact_main_prop": "depot_id,id",
                          "artifact_substat": "depot_id,id", "artifact_codex_item": "artifact_id"}.items():
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_equipment_{table} ON {table}(version_line,{fields})")
    for table in ("weapon", "artifact", "artifact_set"):
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_equipment_{table}_name ON {table}(version_line,name,id)")


def _safe_numbers(value, path=""):
    if isinstance(value, float) and not math.isfinite(value):
        raise EquipmentError(f"非有限数值: {path}")
    if isinstance(value, dict):
        for key, child in value.items():
            _safe_numbers(child, f"{path}.{key}")
    elif isinstance(value, list):
        for i, child in enumerate(value):
            _safe_numbers(child, f"{path}[{i}]")


def _id(value, field, *, zero=False):
    if isinstance(value, bool) or not isinstance(value, int) or not (0 if zero else 1) <= value <= MAX_PUBLIC_ID:
        raise EquipmentError(f"源整数不合法: {field}={value!r}")
    return value


def _missing(record, fields):
    result = [field for field in fields if field not in record]
    for array in ("addProps", "weaponProp"):
        for i, item in enumerate(record.get(array, [])):
            if item.get("propType") and ("value" if array == "addProps" else "initValue") not in item:
                result.append(f"{array}[{i}]." + ("value" if array == "addProps" else "initValue"))
    return result


def _rows(conn, table, line):
    return [dict(r) for r in conn.execute(f"SELECT * FROM {table} WHERE version_line=?", (line,))]


@database_write
def build_equipment_layer(
    db_path: Path,
    version_line: str,
    *,
    upstream_commit: str,
    manifest: dict,
    content_timestamp: str | None = None,
) -> dict:
    """Build/publish one line in a transaction. Caller supplies hashes of frozen original bytes."""
    validate_line(version_line)
    if set(manifest) != set(RAW_SOURCES):
        raise EquipmentError("装备构建需要完整 20 文件来源清单")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if any(state["state"] == "incompatible" for state in equipment_datasets(conn)["equipment"]["lines"].values()):
            raise EquipmentError("装备结构版本不受支持，禁止重建降级", "unsupported_schema", 409)
        raw = {r[0]: json.loads(r[1]) for r in conn.execute("SELECT source_file,data_json FROM raw_table")}
        for rel in RAW_SOURCES:
            stem = Path(rel).stem
            if stem not in raw or len(raw[stem]) != manifest[rel]["row_count"]:
                raise EquipmentError(f"raw 来源不完整: {rel}")
        index = DescriptionIndex(raw, upstream_commit)
        timestamp = content_timestamp or datetime.now(UTC).isoformat()
        warnings = []

        def insert(table, record, source_table, key, values, missing=(), field=None):
            for value in key.values():
                if isinstance(value, (int, float, bool)):
                    _id(value, str(key), zero=True)
            _safe_numbers(values, source_table)
            for column, value in values.items():
                if value is None or column in ("is_disuse", "show_only_unlocked", "disable_filter"):
                    continue
                if (column in ("id", "rarity", "level", "source_index", "source_level", "refinement",
                               "promote_level", "unlock_max_level", "required_count", "source_rank",
                               "rarity_key", "max_source_level", "append_prop_num", "source_rank_level")
                        or column.endswith("_id")):
                    _id(value, f"{table}.{column}", zero=True)
                if column in ("value", "init_value", "exp") and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    raise EquipmentError(f"源数值类型不合法: {table}.{column}")
                if column == "param_list_json" and any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value):
                    raise EquipmentError(f"源参数类型不合法: {table}")
                if column == "add_props_json" and (not isinstance(value, list) or any(not isinstance(v, dict) or ("value" in v and (isinstance(v['value'], bool) or not isinstance(v['value'], (int, float)))) for v in value)):
                    raise EquipmentError(f"源属性数组不合法: {table}")
            values = {**values, "version_line": version_line,
                      "source_missing_fields_json": dumps(_missing(record, missing)),
                      "source_refs_json": dumps([source_ref(source_table, key, field, upstream_commit)])}
            for k, v in list(values.items()):
                if isinstance(v, (dict, list)):
                    values[k] = dumps(v)
            conn.execute(f"INSERT INTO {table} ({','.join(values)}) VALUES "
                         f"({','.join('?' for _ in values)})", list(values.values()))

        def texts(record, table, key):
            name_info = index.describe({**record, "descTextMapHash": record.get("nameTextMapHash")}, table, key)
            return {"name": name_info["desc_template"] or None,
                    "name_text_map_hash": str(record["nameTextMapHash"]) if record.get("nameTextMapHash") else None,
                    "name_source": name_info["desc_source"], "name_status": name_info["desc_status"],
                    **index.describe(record, table, key)}

        with conn:
            ensure_schema(conn)
            for table in TABLES:
                conn.execute(f"DELETE FROM {table} WHERE version_line=?", (version_line,))
            conn.execute("DELETE FROM data_component WHERE component='equipment' AND version_line=?", (version_line,))
            conn.execute("DELETE FROM source_manifest WHERE component='equipment' AND version_line=?", (version_line,))
            affixes = defaultdict(dict)
            for record in raw["EquipAffixExcelConfigData"]:
                rid = _id(record.get("affixId"), "affixId")
                gid = _id(record.get("id"), "EquipAffix.id")
                level = _id(record.get("level", 0), "EquipAffix.level", zero=True)
                if level in affixes[gid]:
                    raise EquipmentError(f"重复效果组等级: {gid}/{level}")
                affixes[gid][level] = rid
                insert("equip_affix", record, "EquipAffixExcelConfigData", {"affixId": rid},
                       {"affix_id": rid, "affix_group_id": gid, "source_level": level,
                        **texts(record, "EquipAffixExcelConfigData", "affixId"),
                        "param_list_json": record.get("paramList", []),
                        "add_props_json": record.get("addProps", []), "open_config": record.get("openConfig")},
                       ("level", "paramList", "addProps", "openConfig"))
            for record in raw["WeaponExcelConfigData"]:
                rid = _id(record.get("id"), "Weapon.id")
                insert("weapon", record, "WeaponExcelConfigData", {"id": rid},
                       {"id": rid, **texts(record, "WeaponExcelConfigData", "id"),
                        "rarity": record["rankLevel"], "weapon_type": record["weaponType"],
                        "promote_group_id": record["weaponPromoteId"], "icon": record.get("icon"),
                        "awaken_icon": record.get("awakenIcon"), "skill_affix_ids_json": record.get("skillAffix", [])},
                       ("icon", "awakenIcon", "skillAffix"))
                for i, prop in enumerate(record.get("weaponProp", [])):
                    insert("weapon_property", prop, "WeaponExcelConfigData", {"id": rid},
                           {"weapon_id": rid, "source_index": i, "prop_type": prop.get("propType"),
                            "curve_type": prop.get("type"), "init_value": prop.get("initValue")},
                           ("propType", "type", "initValue"), f"weaponProp[{i}]")
                for i, gid in enumerate(record.get("skillAffix", [])):
                    if not gid:
                        continue
                    if gid not in affixes:
                        raise EquipmentError(f"武器 {rid} 效果组断链: {gid}")
                    for level, aid in sorted(affixes[gid].items()):
                        insert("weapon_effect", record, "WeaponExcelConfigData", {"id": rid},
                               {"weapon_id": rid, "source_index": i, "source_level": level,
                                "affix_group_id": gid, "affix_id": aid, "refinement": level + 1},
                               field=f"skillAffix[{i}]")
            for record in raw["WeaponCurveExcelConfigData"]:
                for curve in record["curveInfos"]:
                    if curve.get("arith") != "ARITH_MULTI":
                        raise EquipmentError(f"不支持成长算法: {curve}")
                    insert("weapon_curve", curve, "WeaponCurveExcelConfigData",
                           {"level": record["level"], "type": curve["type"]},
                           {"curve_type": curve["type"], "level": record["level"],
                            "arith": curve["arith"], "value": curve["value"]}, field="curveInfos")
            for record in raw["WeaponPromoteExcelConfigData"]:
                gid, level = record["weaponPromoteId"], record.get("promoteLevel", 0)
                insert("weapon_promote", record, "WeaponPromoteExcelConfigData",
                       {"weaponPromoteId": gid, "promoteLevel": level},
                       {"promote_group_id": gid, "promote_level": level,
                        "unlock_max_level": record["unlockMaxLevel"], "add_props_json": record.get("addProps", [])},
                       ("promoteLevel", "addProps"), "addProps")
            for record in raw["WeaponCodexExcelConfigData"]:
                insert("weapon_codex", record, "WeaponCodexExcelConfigData", {"Id": record["Id"]},
                       {"id": record["Id"], "weapon_id": record["weaponId"],
                        "sort_order": record.get("sortOrder"), "is_disuse": record.get("isDisuse"),
                        "show_only_unlocked": record.get("showOnlyUnlocked")},
                       ("sortOrder", "isDisuse", "showOnlyUnlocked"))
            for record in raw["ReliquarySetExcelConfigData"]:
                rid, gid = record["setId"], record.get("EquipAffixId") or None
                if gid and gid not in affixes:
                    raise EquipmentError(f"套装 {rid} 效果组断链: {gid}")
                if not gid and (rid != 15000 or record.get("setNeedNum") != [3, 5]):
                    raise EquipmentError(f"未核实的空套装效果组: {rid}")
                names = [dict(r) for r in conn.execute("SELECT name,name_text_map_hash,name_source,name_status "
                         "FROM equip_affix WHERE version_line=? AND affix_group_id=? ORDER BY source_level,affix_id",
                         (version_line, gid)) if r["name"]]
                name = names[0] if names else {"name": None, "name_text_map_hash": None,
                                             "name_source": None, "name_status": "source_empty"}
                if len({n["name"] for n in names}) > 1:
                    warnings.append({"code": "name_conflict", "message": "同套装效果名称不同",
                                     "target": {"type": "artifact_set", "id": rid}, "field": "name",
                                     "source_refs": [source_ref("ReliquarySetExcelConfigData", {"setId": rid}, "EquipAffixId", upstream_commit)]})
                insert("artifact_set", record, "ReliquarySetExcelConfigData", {"setId": rid},
                       {"id": rid, "affix_group_id": gid, "required_counts_json": record["setNeedNum"],
                        "contains_ids_json": record.get("containsList", []), "source_rank_level": record.get("rankLevel"),
                        "icon": record.get("setIcon"), "disable_filter": record.get("disableFilter"),
                        "bag_sort_value": record.get("bagSortValue"), **name},
                       ("EquipAffixId", "containsList", "rankLevel", "setIcon", "disableFilter", "bagSortValue"))
                for i, count in enumerate(record["setNeedNum"]):
                    aid = affixes.get(gid, {}).get(i)
                    if gid and aid is None:
                        raise EquipmentError(f"套装 {rid} 件数索引 {i} 无效果")
                    insert("artifact_set_effect", record, "ReliquarySetExcelConfigData", {"setId": rid},
                           {"set_id": rid, "source_index": i, "required_count": count, "source_level": i,
                            "affix_id": aid, "relation_status": "present" if aid else "source_empty"}, field=f"setNeedNum[{i}]")
            for record in raw["ReliquaryExcelConfigData"]:
                rid = _id(record.get("id"), "Reliquary.id")
                insert("artifact", record, "ReliquaryExcelConfigData", {"id": rid},
                       {"id": rid, "set_id": record.get("setId") or None, "equip_type": record["equipType"],
                        "rarity": record["rankLevel"], **texts(record, "ReliquaryExcelConfigData", "id"),
                        "icon": record.get("icon"), "main_prop_depot_id": record["mainPropDepotId"],
                        "append_prop_depot_id": record["appendPropDepotId"], "append_prop_num": record.get("appendPropNum"),
                        "max_source_level": record["maxLevel"], "add_prop_levels_json": record.get("addPropLevels", [])},
                       ("setId", "icon", "appendPropNum", "addPropLevels"))
            for record in raw["ReliquaryCodexExcelConfigData"]:
                rid = record["Id"]
                insert("artifact_codex", record, "ReliquaryCodexExcelConfigData", {"Id": rid},
                       {"id": rid, "set_id": record["suitId"], "rarity": record["level"], "sort_order": record.get("sortOrder")}, ("sortOrder",))
                for equip, field in EQUIP_FIELDS.items():
                    if record.get(field):
                        insert("artifact_codex_item", record, "ReliquaryCodexExcelConfigData", {"Id": rid},
                               {"codex_id": rid, "equip_type": equip, "artifact_id": record[field], "source_field": field}, field=field)
            for table, source, mapping in (
                ("artifact_main_prop", "ReliquaryMainPropExcelConfigData", {"id": "id", "depot_id": "propDepotId", "prop_type": "propType", "affix_name": "affixName"}),
                ("artifact_substat", "ReliquaryAffixExcelConfigData", {"id": "id", "depot_id": "depotId", "prop_type": "propType", "source_group_id": "group_id", "value": "propValue"}),
            ):
                for record in raw[source]:
                    insert(table, record, source, {"id": record["id"]}, {k: record[v] for k, v in mapping.items()})
            for record in raw["ReliquaryLevelExcelConfigData"]:
                insert("artifact_level", record, "ReliquaryLevelExcelConfigData",
                       {"rank": record.get("rank", 0), "level": record["level"]},
                       {"rarity_key": record.get("rank", 0), "source_rank": record.get("rank"),
                        "source_level": record["level"], "add_props_json": record["addProps"], "exp": record.get("exp")},
                       ("rank", "exp"), "addProps")
            for rel, item in manifest.items():
                conn.execute("INSERT INTO source_manifest VALUES ('equipment',?,?,?,?,?,?)",
                             (version_line, rel, upstream_commit, item["sha256"], item["row_count"], timestamp))
            validation = validate_equipment(conn, version_line)
            for kind, rid in validation.pop("missing_value_targets"):
                row = conn.execute(f"SELECT source_refs_json FROM {kind} WHERE id=? AND version_line=?", (rid, version_line)).fetchone()
                warnings.append({"code": "source_missing", "message": "此配置存在合法状态下源数值缺失",
                                 "target": {"type": kind, "id": rid}, "field": "growth",
                                 "source_refs": json.loads(row[0])})
            for row in _rows(conn, "artifact_set_effect", version_line):
                if row["relation_status"] != "present":
                    warnings.append({"code": "source_empty", "message": "源套装未提供效果组",
                                     "target": {"type": "artifact_set", "id": row["set_id"]}, "field": f"effects[{row['source_index']}]",
                                     "source_refs": json.loads(row["source_refs_json"])})
            for table, identity in (("weapon", "id"), ("artifact", "id"), ("artifact_set", "id"), ("equip_affix", "affix_id")):
                for row in _rows(conn, table, version_line):
                    for field in ("name", "desc"):
                        status = row.get(field + "_status")
                        if status and status != "present":
                            warnings.append({"code": status, "message": "源文本未提供或未命中映射",
                                             "target": {"type": table, "id": row[identity]}, "field": field,
                                             "source_refs": json.loads(row["source_refs_json"])})
            validation["warnings"] = warnings
            conn.execute("INSERT INTO data_component VALUES ('equipment',?,?,?,?,?)",
                         (version_line, SCHEMA_VERSION, upstream_commit, timestamp, dumps(validation)))
            conn.execute("INSERT OR REPLACE INTO meta VALUES ('equipment_schema_version','1')")
            populate_display_aliases(conn, version_line)
        return validation
    finally:
        conn.close()


def validate_equipment(conn, line):
    """Validate all numeric states and nonzero references before publishing ready."""
    data = {table: _rows(conn, table, line) for table in TABLES}
    for table, rows in data.items():
        if not rows:
            raise EquipmentError(f"装备表为空: {table}/{line}")
        for row in rows:
            _safe_numbers(row, table)
            for field, value in row.items():
                if field.endswith("_json"):
                    decoded = json.loads(value)
                    if not isinstance(decoded, list):
                        raise EquipmentError(f"装备数组字段损坏: {table}.{field}")
                    _safe_numbers(decoded, f"{table}.{field}")
            for key in TABLES[table][0].split(","):
                if key != "curve_type" and key != "equip_type":
                    _id(row[key], f"{table}.{key}", zero=key in ("source_index", "source_level", "promote_level", "rarity_key"))
    weapons = {r["id"]: r for r in data["weapon"]}
    artifacts = {r["id"]: r for r in data["artifact"]}
    sets = {r["id"]: r for r in data["artifact_set"]}
    affixes = {r["affix_id"]: r for r in data["equip_affix"]}
    curves = {(r["curve_type"], r["level"]): r for r in data["weapon_curve"]}
    promotes = defaultdict(dict)
    for row in data["weapon_promote"]:
        promotes[row["promote_group_id"]][row["promote_level"]] = row
    missing_configs = set()
    for weapon in weapons.values():
        if weapon["weapon_type"] not in WEAPON_TYPES or weapon["rarity"] not in range(1, 6):
            raise EquipmentError(f"武器类型或星级未登记: {weapon['id']}")
        for source_index, gid in enumerate(json.loads(weapon["skill_affix_ids_json"])):
            expected = {a["affix_id"] for a in affixes.values() if a["affix_group_id"] == gid} if gid else set()
            actual = {r["affix_id"] for r in data["weapon_effect"] if r["weapon_id"] == weapon["id"] and r["source_index"] == source_index}
            if expected != actual or gid and not expected:
                raise EquipmentError(f"武器效果关系不完整: {weapon['id']}/{source_index}")
        stages = promotes.get(weapon["promote_group_id"], {})
        if sorted(stages) != list(range(len(stages))) or not stages:
            raise EquipmentError(f"武器突破组断链: {weapon['id']}")
        for stage, record in stages.items():
            minimum = 1 if stage == 0 else stages[stage - 1]["unlock_max_level"]
            maximum = record["unlock_max_level"]
            if maximum < minimum:
                raise EquipmentError(f"突破等级范围错误: {weapon['id']}/{stage}")
            for prop in [r for r in data["weapon_property"] if r["weapon_id"] == weapon["id"]]:
                if not prop["prop_type"]:
                    continue
                if prop["init_value"] is None:
                    expected_missing = ({"FIGHT_PROP_BASE_ATTACK"} if weapon["id"] in (10009, 10010, 10011, 10012)
                                        else {"FIGHT_PROP_ATTACK_PERCENT"} if weapon["id"] == 11420 else set())
                    if prop["prop_type"] not in expected_missing:
                        raise EquipmentError(f"未核实的武器初值缺失: {weapon['id']}")
                    missing_configs.add(("weapon", weapon["id"]))
                for level in range(minimum, maximum + 1):
                    curve = curves.get((prop["curve_type"], level))
                    if curve is None or curve["arith"] != "ARITH_MULTI" or curve["value"] is None:
                        raise EquipmentError(f"武器曲线缺失/算法不支持: {weapon['id']}/{level}")
                    if prop["init_value"] is not None:
                        addition = sum(p.get("value", 0) for p in json.loads(record["add_props_json"]) if p.get("propType") == prop["prop_type"])
                        if not math.isfinite(prop["init_value"] * curve["value"] + addition):
                            raise EquipmentError(f"武器成长计算溢出: {weapon['id']}/{level}")
    for row in data["weapon_property"]:
        if row["weapon_id"] not in weapons:
            raise EquipmentError("武器属性 owner 断链")
    for row in data["weapon_effect"]:
        effect = affixes.get(row["affix_id"])
        if row["weapon_id"] not in weapons or effect is None or (effect["affix_group_id"], effect["source_level"]) != (row["affix_group_id"], row["source_level"]):
            raise EquipmentError("武器效果关联断链")
    for row in data["weapon_codex"]:
        if row["weapon_id"] not in weapons:
            raise EquipmentError("武器图鉴断链")
    for row in data["artifact_set_effect"]:
        owner, effect = sets.get(row["set_id"]), affixes.get(row["affix_id"])
        if owner is None or (effect is None and not (owner["id"] == 15000 and owner["affix_group_id"] is None and row["relation_status"] == "source_empty")):
            raise EquipmentError("套装效果关联断链")
        if effect and (effect["affix_group_id"] != owner["affix_group_id"] or effect["source_level"] != row["source_index"]):
            raise EquipmentError("套装效果等级不匹配")
    main = defaultdict(list)
    for row in data["artifact_main_prop"]:
        main[row["depot_id"]].append(row)
    subdepots = {r["depot_id"] for r in data["artifact_substat"]}
    levels = {(r["source_rank"], r["source_level"]): json.loads(r["add_props_json"]) for r in data["artifact_level"]}
    for artifact in artifacts.values():
        aid = artifact["id"]
        if artifact["equip_type"] not in EQUIP_FIELDS or artifact["rarity"] not in range(1, 6) or artifact["max_source_level"] < 1:
            raise EquipmentError(f"圣遗物类型/星级/等级不合法: {aid}")
        if artifact["set_id"] is not None and artifact["set_id"] not in sets:
            raise EquipmentError(f"圣遗物 {aid} 套装断链")
        if artifact["main_prop_depot_id"] not in main or artifact["append_prop_depot_id"] not in subdepots:
            raise EquipmentError(f"圣遗物 {aid} 词条池断链")
        for level in range(1, artifact["max_source_level"] + 1):
            props = levels.get((artifact["rarity"], level))
            if props is None:
                raise EquipmentError(f"圣遗物 {aid} 等级行缺失: {level}")
            available = {p.get("propType") for p in props}
            for prop in main[artifact["main_prop_depot_id"]]:
                if prop["prop_type"] not in available:
                    if not prop["prop_type"].endswith("_SUB_HURT"):
                        raise EquipmentError(f"未核实的主词条成长缺值: {aid}/{prop['prop_type']}")
                    missing_configs.add(("artifact", aid))
    codex = {r["id"]: r for r in data["artifact_codex"]}
    for row in codex.values():
        if row["set_id"] not in sets:
            raise EquipmentError("圣遗物图鉴套装断链")
    for row in data["artifact_codex_item"]:
        owner, artifact = codex.get(row["codex_id"]), artifacts.get(row["artifact_id"])
        if owner is None or artifact is None or (owner["set_id"], owner["rarity"], row["equip_type"]) != (artifact["set_id"], artifact["rarity"], artifact["equip_type"]):
            raise EquipmentError("圣遗物图鉴单件/部位/星级断链")
    for row in sets.values():
        relations = [r for r in data["artifact_set_effect"] if r["set_id"] == row["id"]]
        if [(r["source_index"], r["required_count"]) for r in sorted(relations, key=lambda r: r["source_index"])] != list(enumerate(json.loads(row["required_counts_json"]))):
            raise EquipmentError(f"套装效果件数关系不完整: {row['id']}")
        for aid in json.loads(row["contains_ids_json"]):
            if aid and (aid not in artifacts or artifacts[aid]["set_id"] != row["id"]):
                raise EquipmentError(f"套装 containsList 断链: {row['id']}/{aid}")
    entities = [*weapons.values(), *artifacts.values(), *sets.values(), *affixes.values()]
    prop_types = {r["prop_type"] for t in ("weapon_property", "artifact_main_prop", "artifact_substat") for r in data[t] if r["prop_type"]}
    for table in ("weapon_promote", "artifact_level", "equip_affix"):
        for row in data[table]:
            prop_types.update(p["propType"] for p in json.loads(row["add_props_json"]) if p.get("propType"))
    return {"counts": {"weapons": len(weapons), "artifact_sets": len(sets), "artifacts": len(artifacts),
                       "equip_affixes": len(affixes), "codex_weapons": len({r['weapon_id'] for r in data['weapon_codex']}),
                       "codex_artifact_sets": len({r['set_id'] for r in data['artifact_codex']}),
                       "codex_artifacts": len({r['artifact_id'] for r in data['artifact_codex_item']})},
            "quality_summary": {"missing_names": sum(r.get("name_status") != "present" for r in entities),
                                "missing_descriptions": sum(r["desc_status"] != "present" for r in entities if "desc_status" in r),
                                "missing_effect_relations": sum(r["relation_status"] != "present" for r in data["artifact_set_effect"]),
                                "missing_value_configs": len(missing_configs),
                                "unmapped_prop_types": sum(PROPERTIES.get(p, {}).get("unit", "unknown") == "unknown" for p in prop_types)},
            "table_counts": {table: len(rows) for table, rows in data.items()},
            "missing_value_targets": sorted(missing_configs),
            "prop_types": sorted(prop_types)}


def equipment_datasets(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    marker = conn.execute("SELECT value FROM meta WHERE key='equipment_schema_version'").fetchone() if "meta" in tables else None
    result = {"equipment": {"supported_schema_versions": [SCHEMA_VERSION], "lines": {}}}
    for line in ("release", "beta"):
        state = {"state": "missing", "schema_version": None, "upstream_commit": None,
                 "built_at": None, "counts": None, "quality_summary": None}
        if marker and str(marker[0]) != str(SCHEMA_VERSION):
            state.update(state="incompatible", schema_version=marker[0])
        elif marker and not REQUIRED_TABLES <= tables:
            state["state"] = "invalid"
        elif REQUIRED_TABLES <= tables:
            try:
                row = conn.execute("SELECT schema_version,upstream_commit,built_at,validation_json "
                                   "FROM data_component WHERE component='equipment' AND version_line=?", (line,)).fetchone()
                if row:
                    state.update(schema_version=row[0], upstream_commit=row[1], built_at=row[2])
                    if row[0] != SCHEMA_VERSION:
                        state["state"] = "incompatible"
                    else:
                        summary = json.loads(row[3])
                        manifest = conn.execute("SELECT source_file,upstream_commit,sha256 FROM source_manifest "
                                                "WHERE component='equipment' AND version_line=?", (line,)).fetchall()
                        valid = (marker is not None and bool(row[1]) and bool(row[2])
                                 and {r[0] for r in manifest} == set(RAW_SOURCES)
                                 and all(r[1] == row[1] and len(r[2]) == 64 for r in manifest))
                        valid = valid and all(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE version_line=?", (line,)).fetchone()[0] == summary.get("table_counts", {}).get(table)
                                              for table in TABLES)
                        state.update(state="ready" if valid else "invalid", counts=summary["counts"], quality_summary=summary["quality_summary"])
            except (sqlite3.DatabaseError, TypeError, ValueError, KeyError):
                state["state"] = "invalid"
        result["equipment"]["lines"][line] = state
    return result
