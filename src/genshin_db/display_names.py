"""中文显示身份与旧模板别名。名称和原文来自 curated，别名不参与计算。"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

from genshin_db.locking import database_write
from genshin_db.validation import version_line as validate_line

SCHEMA_VERSION = 1
GENERATOR_REVISION = "zh-display-1"
LOCALE = "zh-CN"
GROUPS = {
    "characters": ("character", "id", "角色"),
    "weapons": ("weapon", "id", "武器"),
    "artifact_sets": ("artifact_set", "id", "圣遗物套装"),
    "skills": ("skill", "id", "技能"),
    "talents": ("talent", "id", "命之座"),
    "artifacts": ("artifact", "id", "圣遗物"),
}
ALIAS_COLUMNS = ("entity_type", "entity_id", "version_line", "locale", "alias_key", "alias",
                 "source_kind", "source_json", "generator_revision")


def normalize_alias(value: str) -> str:
    """仅折叠大小写、空白、横线和下划线；不截断姓名、不推断角色/旅行者形态。"""
    return re.sub(r"[\s_-]+", "", unicodedata.normalize("NFKC", str(value)).casefold())


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _meta(conn):
    return dict(conn.execute("SELECT key,value FROM meta")) if "meta" in _tables(conn) else {}


def _check_version(conn):
    version = _meta(conn).get("display_schema_version")
    if version is not None and version != str(SCHEMA_VERSION):
        raise ValueError("中文显示数据结构版本不受支持，禁止降级写入")


def ensure_display_schema(conn):
    """调用者拥有事务；人工别名与生成别名分别保存，不自动改写已有名字。"""
    _check_version(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS entity_alias (entity_type TEXT NOT NULL,"
                 "entity_id INTEGER NOT NULL,version_line TEXT NOT NULL,locale TEXT NOT NULL,"
                 "alias_key TEXT NOT NULL,alias TEXT NOT NULL,source_kind TEXT NOT NULL,"
                 "source_json TEXT NOT NULL,generator_revision TEXT,"
                 "PRIMARY KEY(entity_type,entity_id,version_line,locale,alias_key,source_kind))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_entity_alias_lookup ON "
                 "entity_alias(version_line,locale,entity_type,alias_key)")


@lru_cache(maxsize=1)
def _reference():
    return json.loads(Path(__file__).with_name("display_alias_reference.json").read_text("utf-8"))


def _entities(conn, line, selected=None):
    tables = _tables(conn)
    groups = {}
    for group, (table, _, _) in GROUPS.items():
        if selected is not None and group not in selected:
            continue
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        groups[group] = [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE version_line=? ORDER BY id", (line,)
        )] if table in tables and {"id", "name", "version_line"} <= columns else []
    return groups


def _generated_aliases(conn, line, groups):
    records = {}

    def add(kind, rid, alias, source_kind, source):
        if not isinstance(alias, str) or not alias.strip():
            return
        key = normalize_alias(alias)
        values = (kind, rid, line, LOCALE, key, alias, source_kind,
                  _json(source), GENERATOR_REVISION)
        records[(kind, rid, key, source_kind)] = values

    # The pinned resource contains only identity strings, with original EN/CHS file hashes.
    # It does not assert that this historical snapshot is the current beta/release source.
    reference = _reference()
    seeds = {(r["entity_type"], r["id"]): r for r in reference["entities"]}
    for group, items in groups.items():
        kind = GROUPS[group][0]
        for row in items:
            seed = seeds.get((kind, row["id"]))
            if seed and row["name"] == seed["name_zh"]:
                for alias in seed["aliases"]:
                    add(kind, row["id"], alias, "pinned_reference", {
                        "resource": "display_alias_reference.json",
                        "upstream_commit": reference["upstream_commit"],
                        "table": seed["source_table"], "record_id": seed["source_record_id"],
                        "entity_id": row["id"], "identity_name": row["name"],
                        "name_text_map_hash": seed["name_text_map_hash"],
                        "source_files": reference["source_files"],
                        "identity_check": "same_id_and_chinese_name",
                    })
    # Raw has one shared snapshot. Never borrow its icon aliases for another version line.
    meta = _meta(conn)
    commit = meta.get("raw_upstream_commit")
    line_commit = meta.get(f"upstream_commit:{line}", meta.get("upstream_commit")
                           if line == "release" else None)
    if ("raw_table" not in _tables(conn) or "text_map" not in _tables(conn)
            or not commit or meta.get("raw_version_line") != line or commit != line_commit):
        return list(records.values())
    raw = conn.execute("SELECT data_json FROM raw_table WHERE source_file=?",
                       ("AvatarExcelConfigData",)).fetchone()
    characters = {r["id"]: r for r in groups.get("characters", [])}
    for avatar in json.loads(raw[0]) if raw else []:
        row = characters.get(avatar.get("id"))
        text_hash = str(avatar.get("nameTextMapHash", ""))
        name = conn.execute("SELECT text FROM text_map WHERE hash=? AND locale='CHS'",
                            (text_hash,)).fetchone()
        icon = avatar.get("iconName", "")
        if (row and name and row["name"] == name[0] and icon.startswith("UI_AvatarIcon_")
                and avatar.get("useType") == "AVATAR_FORMAL"):
            add("character", row["id"], icon.removeprefix("UI_AvatarIcon_"), "upstream", {
                "table": "AvatarExcelConfigData", "record_id": row["id"],
                "field": "iconName", "value": icon, "name_text_map_hash": text_hash,
                "identity_name": row["name"],
                "use_type": "AVATAR_FORMAL",
                "upstream_commit": commit, "version_line": line,
            })
    return list(records.values())


def populate_display_aliases(conn, version_line):
    """增量补齐当前线；原角色、装备、数值、原文、人工层及别名均不改写。"""
    ensure_display_schema(conn)
    groups = _entities(conn, version_line, ("characters", "weapons", "artifact_sets"))
    desired = {tuple(row[:5]) + (row[6],): row
               for row in _generated_aliases(conn, version_line, groups)}
    existing = {tuple(row[:5]) + (row[6],): tuple(row) for row in conn.execute(
        "SELECT " + ",".join(ALIAS_COLUMNS) + " FROM entity_alias WHERE version_line=?"
        " AND locale=? AND source_kind IN ('upstream','pinned_reference')",
        (version_line, LOCALE))}
    changed = 0
    for key in existing.keys() - desired.keys():
        conn.execute("DELETE FROM entity_alias WHERE entity_type=? AND entity_id=?"
                     " AND version_line=? AND locale=? AND alias_key=? AND source_kind=?", key)
        changed += 1
    for key, row in desired.items():
        if existing.get(key) != row:
            conn.execute("INSERT OR REPLACE INTO entity_alias (" + ",".join(ALIAS_COLUMNS)
                         + ") VALUES (" + ",".join("?" for _ in row) + ")", row)
            changed += 1
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT)")
    for key, value in (("display_schema_version", str(SCHEMA_VERSION)),
                       (f"display_generator:{version_line}", GENERATOR_REVISION),
                       (f"display_alias_count:{version_line}", str(len(desired)))):
        old = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if old is None or old[0] != value:
            conn.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))
    return {"schema_version": SCHEMA_VERSION, "version_line": version_line,
            "changed_rows": changed, "generated_alias_rows": len(desired),
            "entity_counts": {key: len(value) for key, value in groups.items()},
            "source_counts": dict(Counter(row[6] for row in desired.values())),
            "validation": {"valid": True, "other_lines_preserved": True,
                           "manual_aliases_preserved": True}}


def display_complete(conn, version_line):
    """本地结构/生成版本检查。名称不全仍可展示明确占位，不伪造完整性。"""
    meta = _meta(conn)
    if (meta.get("display_schema_version") != str(SCHEMA_VERSION)
            or meta.get(f"display_generator:{version_line}") != GENERATOR_REVISION):
        return False
    columns = {r[1] for r in conn.execute("PRAGMA table_info(entity_alias)")}
    if not set(ALIAS_COLUMNS) <= columns:
        return False
    count = conn.execute("SELECT COUNT(*) FROM entity_alias WHERE version_line=? AND locale=?"
                         " AND source_kind IN ('upstream','pinned_reference')",
                         (version_line, LOCALE)).fetchone()[0]
    return meta.get(f"display_alias_count:{version_line}") == str(count)


@database_write
def migrate_display_names(db_path: Path, version_line: str = "release") -> dict:
    """显式离线迁移；调用前由管理入口备份。只读查询从不调用此方法。"""
    validate_line(version_line)
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=rw", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            result = populate_display_aliases(conn, version_line)
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("中文显示资料迁移后数据库完整性检查失败")
            result["validation"]["integrity_check"] = "ok"
            return result
    finally:
        conn.close()


def _decode(value):
    return json.loads(value) if isinstance(value, str) and value else None


def get_display_catalog(db_path: Path, version_line: str = "release", *,
                        entity_types: list[str] | None = None,
                        include_descriptions: bool = False) -> dict:
    """所有公开名字使用中文；旧库只读派生同契约，缺失路径不会创建 SQLite 文件。"""
    validate_line(version_line)
    if entity_types is None:
        entity_types = ["character", "weapon", "artifact_set"]
    if (not isinstance(entity_types, list) or not entity_types
            or any(kind not in {v[0] for v in GROUPS.values()} for kind in entity_types)):
        raise ValueError("不支持的显示资料对象类型")
    if not isinstance(include_descriptions, bool):
        raise ValueError("include_descriptions 必须为布尔值")  # noqa: TRY004 - 查询层统一参数错误契约
    from genshin_db.equipment_queries import (
        borrow_read_connection,
        equipment_read_session,
    )

    with equipment_read_session(db_path):
        conn = borrow_read_connection(db_path)
        _check_version(conn)
        groups = _entities(conn, version_line, {key for key, value in GROUPS.items()
                                              if value[0] in entity_types})
        aliases = defaultdict(dict)
        generated = _generated_aliases(conn, version_line, groups)
        rows = [dict(zip(ALIAS_COLUMNS, row, strict=True)) for row in generated]
        current_names = {(GROUPS[key][0], item["id"]): item["name"]
                         for key, items in groups.items() for item in items}
        if "entity_alias" in _tables(conn):
            rows += [dict(row) for row in conn.execute(
                "SELECT * FROM entity_alias WHERE version_line=? AND locale=?",
                (version_line, LOCALE))]
        for row in rows:
            source = _decode(row["source_json"])
            if (row["source_kind"] != "manual" and
                    source.get("identity_name") != current_names.get((row["entity_type"], row["entity_id"]))):
                continue
            aliases[row["entity_type"], row["entity_id"]][row["alias_key"]] = {
                "alias": row["alias"], "source_kind": row["source_kind"],
                "source": source,
            }
        result = {"locale": LOCALE, "version_line": version_line,
                  "schema_version": SCHEMA_VERSION,
                  "storage_status": "ready" if display_complete(conn, version_line) else "legacy_read_only",
                  "alias_normalization": "NFKC + casefold + remove whitespace/underscore/hyphen",
                  "alias_policy": "精确规范化匹配；仅唯一身份可自动解析；同名及旅行者保留原始编号"}
        for group, items in groups.items():
            kind, _, label = GROUPS[group]
            output = []
            for row in items:
                name = row.get("name") or ""
                name_cn = bool(re.search(r"[\u3400-\u9fff]", name))
                a = list(aliases[kind, row["id"]].values())
                output.append({
                    "entity_type": kind, "id": row["id"], "name": name,
                    "display_name": name if name_cn else f"未命名{label}（{row['id']}）",
                    "display_name_status": "present" if name_cn else "chinese_name_missing",
                    "name_source": _decode(row.get("name_source")) or {
                        "table": kind, "field": "name", "version_line": version_line,
                        "verification": "curated_chinese_text" if name_cn else "unverified",
                    },
                    "aliases": sorted({name, str(row["id"]), *(v["alias"] for v in a)} - {""}),
                    "alias_sources": a,
                    **({"description": row.get("desc_template", row.get("desc")),
                        "description_status": row.get("desc_status") or "legacy_unverified",
                        "description_source": _decode(row.get("desc_source"))}
                       if include_descriptions else {}),
                    **{key: row[key] for key in ("character_id", "slot", "level", "set_id",
                                               "element", "weapon_type") if key in row},
                })
            result[group] = output
        return result


def resolve_display_entity(db_path: Path, reference: str | int, entity_type: str = "character",
                           version_line: str = "release") -> dict:
    """不以首个结果消歧；共享技能编号和同名角色必须由调用者显式指定身份。"""
    group = next((key for key, value in GROUPS.items() if value[0] == entity_type), None)
    if group is None:
        raise ValueError("不支持的显示资料对象类型")
    if isinstance(reference, bool) or not isinstance(reference, (str, int)) or not str(reference).strip():
        raise ValueError("查询名称或编号不能为空")
    key = normalize_alias(str(reference))
    items = get_display_catalog(db_path, version_line, entity_types=[entity_type])[group]
    found = [item for item in items if any(normalize_alias(a) == key for a in item["aliases"])]
    return {"status": "resolved" if len(found) == 1 else "ambiguous" if found else "missing",
            "entity_type": entity_type, "reference": reference, "version_line": version_line,
            "items": found}
