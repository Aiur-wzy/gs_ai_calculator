"""追加描述的对象关联；旧 extra_info 没有来源列时兼容读取已生成的参数组标识。"""

from __future__ import annotations

import json
import re
import sqlite3

_GROUP = re.compile(r"参数组 (\d+)")


def _legacy_subskills(conn: sqlite3.Connection, character_id: int | None,
                      version_line: str | None) -> set[tuple[str, int, str]]:
    """旧单源库升级前，使用仍保留的 raw depot 关联补齐附属技能 id。"""
    if version_line not in (None, "release"):
        return set()
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='raw_table'").fetchone():
        return set()
    row = conn.execute(
        "SELECT data_json FROM raw_table WHERE source_file='AvatarSkillDepotExcelConfigData'"
    ).fetchone()
    if not row:
        return set()
    args = [character_id] if character_id is not None else []
    chars = conn.execute(
        "SELECT id,skill_depot_id FROM character WHERE version_line='release'"
        + (" AND id=?" if character_id is not None else ""), args,
    ).fetchall()
    depot_ids = {r[1] for r in chars}
    travelers = {r[0] for r in chars} & {10000005, 10000007}
    targets = set()
    for depot in json.loads(row[0]):
        did = depot.get("id", 0)
        if did not in depot_ids and not (
                did // 100 == 5 and 10000005 in travelers or
                did // 100 == 7 and 10000007 in travelers):
            continue
        for sid in [*depot.get("subSkills", []), *depot.get("skills", [])[2:]]:
            if sid:
                targets.add(("skill", sid, "release"))
    return targets


def all_targets(conn: sqlite3.Connection) -> set[tuple[str, int, str]]:
    """All stored lines and domains for orphan processing; never character aggregation."""
    targets = related_targets(conn)
    for table in ("weapon", "artifact_set", "artifact", "equip_affix"):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            key = "affix_id" if table == "equip_affix" else "id"
            targets.update((table, row[0], row[1]) for row in conn.execute(f"SELECT {key},version_line FROM {table}"))
    return targets


def related_targets(conn: sqlite3.Connection, character_id: int | None = None,
                    version_line: str | None = None) -> set[tuple[str, int, str]]:
    targets: set[tuple[str, int, str]] = set()
    for table in ("character", "skill", "talent"):
        where, args = [], []
        if character_id is not None:
            where.append(("id" if table == "character" else "character_id") + "=?")
            args.append(character_id)
        if version_line is not None:
            where.append("version_line=?")
            args.append(version_line)
        suffix = " WHERE " + " AND ".join(where) if where else ""
        targets.update((table, row[0], row[1]) for row in conn.execute(
            f"SELECT id, version_line FROM {table}{suffix}", args))
        if table == "skill":
            targets.update(("skill_param", row[0], row[1]) for row in conn.execute(
                "SELECT proud_skill_group_id, version_line FROM skill" + suffix, args)
                if row[0])

    where, args = ["target_type='character'"], []
    if character_id is not None:
        where.append("target_id=?")
        args.append(character_id)
    if version_line is not None:
        where.append("version_line=?")
        args.append(version_line)
    columns = {r[1] for r in conn.execute("PRAGMA table_info(extra_info)")}
    if columns:
        legacy = conn.execute(
            "SELECT 1 FROM extra_info WHERE " + " AND ".join(where)
            + (" AND source_id IS NULL" if "source_id" in columns else "") + " LIMIT 1", args,
        ).fetchone()
        if legacy:
            targets.update(_legacy_subskills(conn, character_id, version_line))
        source = "source_id" if "source_id" in columns else "NULL"
        group = "proud_skill_group_id" if "proud_skill_group_id" in columns else "NULL"
        for kind, content, line, source_id, gid in conn.execute(
            f"SELECT info_type,content,version_line,{source},{group} FROM extra_info WHERE "
            + " AND ".join(where), args,
        ):
            match = _GROUP.search(content or "") if gid is None else None
            gid = gid or (int(match.group(1)) if match else None)
            if gid:
                targets.add(("skill_param", gid, line))
                if kind == "inherent_skill":
                    targets.add(("inherent_skill", gid, line))
            if source_id and kind == "sub_skill":
                targets.add(("skill", source_id, line))
    if character_id is None:
        suffix = " WHERE version_line=?" if version_line else ""
        args = [version_line] if version_line else []
        targets.update(("skill_param", row[0], row[1]) for row in conn.execute(
            "SELECT proud_skill_group_id,version_line FROM skill_param" + suffix, args))
    return targets
