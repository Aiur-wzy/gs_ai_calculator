"""数据查询层：db-service HTTP 端点与 GUI 桥层共用的唯一查询实现（口径一致保证）。

纯函数，参数为 db_path，返回 JSON 兼容 dict；不碰 HTTP 也不碰 pywebview。
未找到抛 NotFoundError，参数非法抛 ValueError，由调用方各自翻译（404 / err 包络）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from genshin_db.annotation_store import Annotation, add_annotation
from genshin_db.display_names import (  # noqa: F401
    get_display_catalog,
    resolve_display_entity,
)
from genshin_db.locking import database_write
from genshin_db.targets import related_targets
from genshin_db.validation import (
    ALL_STATUS,
    MAX_SQLITE_INT,
    TARGET_TYPES,
    QueryValidationError,
    enum_value,
    positive_int,
    text_field,
)
from genshin_db.validation import version_line as validate_line

MAX_PAGE_SIZE = 100
SEARCH_CAP_PER_TYPE = 20


class NotFoundError(Exception):
    reason = "entity_not_found"


def _connect(db_path: Path) -> sqlite3.Connection:
    """查询层默认使用物理只读连接，避免查询意外创建 journal/WAL。"""
    from genshin_db.equipment_queries import borrow_read_connection
    borrowed = borrow_read_connection(db_path)
    if borrowed is not None:
        return borrowed
    path = Path(db_path).resolve()
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_write(db_path: Path) -> sqlite3.Connection:
    """仅供隔离候选构建器和测试夹具使用的低层写连接。"""

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_character(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("grow_curves"):
        d["grow_curves"] = json.loads(d["grow_curves"])
    return d


def _load_params(conn: sqlite3.Connection, gid: int | None, version_line: str) -> dict:
    params = {}
    if gid:
        for p in conn.execute(
            "SELECT level, param_list, param_desc, add_props, buff_zone FROM skill_param"
            " WHERE proud_skill_group_id=? AND version_line=? ORDER BY level",
            (gid, version_line),
        ).fetchall():
            params[str(p["level"])] = {
                "param_list": json.loads(p["param_list"]),
                "param_desc": json.loads(p["param_desc"]) if p["param_desc"] else [],
                "add_props": json.loads(p["add_props"]) if p["add_props"] else {},
                "buff_zone": p["buff_zone"],
            }
    return params


def _skill_to_dict(conn: sqlite3.Connection, row: sqlite3.Row, version_line: str) -> dict:
    sd = _description_to_dict(row)
    gid = sd["proud_skill_group_id"]
    sd["params"] = _load_params(conn, gid, version_line)
    return sd


def _description_to_dict(row: sqlite3.Row) -> dict:
    """来源与展示文字分离；读取旧库不写迁移、不从拼接文本猜原始描述。"""
    item = dict(row)
    item.setdefault("desc_template", None)
    item["desc_source"] = json.loads(item["desc_source"]) if item.get("desc_source") else None
    item["desc_status"] = item.get("desc_status") or "legacy_unverified"
    return item


def _extra_to_dict(conn: sqlite3.Connection, row: sqlite3.Row, version_line: str) -> dict:
    item = _description_to_dict(row)
    for key in ("name", "source_id", "proud_skill_group_id"):
        item.setdefault(key, None)
    item["description_variants"] = (
        json.loads(item["description_variants"]) if item.get("description_variants") else []
    )
    item["params"] = _load_params(conn, item["proud_skill_group_id"], version_line)
    return item


def _approved_annotations(conn: sqlite3.Connection, targets: set[tuple[str, int]],
                          version_line: str) -> list[dict]:
    if not targets or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='annotation'"
    ).fetchone():
        return []
    ordered = sorted(targets)
    bindings = [value for target in ordered for value in target]
    return [dict(row) for row in conn.execute(
        "SELECT * FROM annotation WHERE (target_type,target_id) IN (VALUES "
        + ",".join("(?,?)" for _ in ordered) + ")"
        " AND status='approved' AND (version_line IS NULL OR version_line=?)"
        " ORDER BY id", [*bindings, version_line],
    )]


def _character_ref(character_id: int, version_line: str) -> dict:
    return {"character_id": character_id, "version_line": version_line,
            "path": f"/v1/characters/{character_id}?version_line={version_line}"}


def _group_context_refs(conn: sqlite3.Connection, group_id: int, version_line: str) -> list[dict]:
    """参数组可被多个角色/附属对象共享；去重归属关系，保留每个合法 owner。"""
    refs = {}
    for row in conn.execute(
        "SELECT id,character_id FROM skill WHERE proud_skill_group_id=? AND version_line=?"
        " ORDER BY character_id,id", (group_id, version_line),
    ):
        sid, cid = row["id"], row["character_id"]
        key = ("skill", cid, sid)
        refs[key] = {
            "kind": "skill", "source_id": sid, "character_id": cid,
            "proud_skill_group_id": group_id, "version_line": version_line,
            "path": f"/v1/skills/{sid}?character_id={cid}&version_line={version_line}",
        }
    columns = {row[1] for row in conn.execute("PRAGMA table_info(extra_info)")}
    if "proud_skill_group_id" in columns:
        source = "source_id" if "source_id" in columns else "NULL AS source_id"
        for row in conn.execute(
            f"SELECT info_type,target_id,{source} FROM extra_info"
            " WHERE target_type='character' AND proud_skill_group_id=? AND version_line=?"
            " AND info_type IN ('sub_skill','inherent_skill') ORDER BY target_id,info_type,id",
            (group_id, version_line),
        ):
            kind, cid, sid = row["info_type"], row["target_id"], row["source_id"]
            key = (kind, cid, sid)
            refs[key] = {
                "kind": kind, "source_id": sid, "character_id": cid,
                "proud_skill_group_id": group_id, **_character_ref(cid, version_line),
                "selector": {"collection": "extra_info", "info_type": kind,
                             "source_id": sid, "proud_skill_group_id": group_id},
            }
    return list(refs.values())


def _page_args(page: int, page_size: int) -> tuple[int, int]:
    page = positive_int(page, "page")
    page_size = min(MAX_PAGE_SIZE, positive_int(page_size, "page_size"))
    if (page - 1) * page_size > MAX_SQLITE_INT:
        raise QueryValidationError("page 超出分页范围")
    return page, page_size


# ---------- 元信息 ----------

def get_meta(db_path: Path) -> dict:
    conn = _connect(db_path)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        lines = conn.execute(
            "SELECT DISTINCT version_line FROM character ORDER BY version_line"
        ).fetchall()
        meta["version_lines"] = [r[0] for r in lines]
        from genshin_db.equipment import equipment_datasets
        from genshin_db.runtime_store import runtime_datasets
        from genshin_db.semantic_store import semantic_datasets
        meta["datasets"] = {**equipment_datasets(conn), **runtime_datasets(conn),
                            **semantic_datasets(conn)}
        from genshin_db.context_query import manual_content_fingerprint
        from genshin_db.publication_evidence import publication_fingerprint
        meta["manual_content_fingerprint"] = manual_content_fingerprint(conn)
        meta["entity_publication_fingerprint"] = publication_fingerprint(conn)
        return meta
    finally:
        conn.close()


# ---------- 角色/技能/倍率 ----------

def list_characters(
    db_path: Path,
    version_line: str = "release",
    name: str | None = None,
    element: str | None = None,
    rarity: int | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    validate_line(version_line)
    if name is not None:
        text_field(name, "name")
    if element is not None:
        enum_value(element, "element", ("火", "水", "雷", "冰", "风", "岩", "草"))
    if rarity is not None:
        rarity = positive_int(rarity, "rarity")
        if rarity not in (4, 5):
            raise QueryValidationError("rarity 必须为 4 或 5")
    page, page_size = _page_args(page, page_size)
    where, args = ["version_line = ?"], [version_line]
    if name:
        where.append("name LIKE ?")
        args.append(f"%{name}%")
    if element:
        where.append("element = ?")
        args.append(element)
    if rarity:
        where.append("rarity = ?")
        args.append(rarity)
    sql_where = "WHERE " + " AND ".join(where)
    conn = _connect(db_path)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM character {sql_where}", args
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT id, name, element, rarity, weapon_type, hp_90, atk_90, def_90,"
            " ascension_prop, ascension_value"
            f" FROM character {sql_where} ORDER BY id LIMIT ? OFFSET ?",
            [*args, page_size, (page - 1) * page_size],
        ).fetchall()
        return {
            "total": total, "page": page, "page_size": page_size,
            "items": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def get_character_detail(db_path: Path, char_id: int, version_line: str = "release", *,
                         sections=None, levels=None) -> dict:
    from genshin_db.context_query import project_detail, validate_selection
    selection = validate_selection("character", sections=sections, levels=levels)
    char_id = positive_int(char_id, "character_id")
    validate_line(version_line)
    conn = _connect(db_path)
    try:
        char = conn.execute(
            "SELECT * FROM character WHERE id=? AND version_line=?", (char_id, version_line)
        ).fetchone()
        if char is None:
            raise NotFoundError(f"角色不存在: {char_id}（version_line={version_line}）")
        skills = [
            _skill_to_dict(conn, s, version_line)
            for s in conn.execute(
                "SELECT * FROM skill WHERE character_id=? AND version_line=? ORDER BY"
                " CASE slot WHEN 'normal' THEN 0 WHEN 'skill' THEN 1 ELSE 2 END",
                (char_id, version_line),
            ).fetchall()
        ]
        talents = [
            {
                **_description_to_dict(t),
                "param_list": json.loads(t["param_list"]) if t["param_list"] else [],
                "add_props": json.loads(t["add_props"]) if t["add_props"] else {},
            }
            for t in conn.execute(
                "SELECT * FROM talent WHERE character_id=? AND version_line=? ORDER BY level",
                (char_id, version_line),
            ).fetchall()
        ]
        extra_info = [
            _extra_to_dict(conn, r, version_line) for r in conn.execute(
                "SELECT * FROM extra_info"
                " WHERE target_type='character' AND target_id=? AND version_line=?",
                (char_id, version_line),
            ).fetchall()
        ]
        targets = {(kind, target_id) for kind, target_id, _ in
                   related_targets(conn, char_id, version_line)}
        annotations = _approved_annotations(conn, targets, version_line)
        from genshin_db.publication_evidence import publication_by_id

        character = _row_to_character(char)
        character["publication"] = publication_by_id(conn, "character", [char_id], version_line,
                                                     detailed=True)[char_id]
        return project_detail({
            "character": character,
            "skills": skills,
            "talents": talents,
            "extra_info": extra_info,
            "annotations": annotations,
        }, "character", selection)
    finally:
        conn.close()


def get_skill(
    db_path: Path,
    skill_id: int,
    version_line: str = "release",
    character_id: int | None = None,
    *, sections=None, levels=None,
) -> dict:
    from genshin_db.context_query import project_detail, validate_selection
    selection = validate_selection("skill", sections=sections, levels=levels)
    skill_id = positive_int(skill_id, "skill_id")
    validate_line(version_line)
    if character_id is not None:
        character_id = positive_int(character_id, "character_id")
    where, args = ["id = ?", "version_line = ?"], [skill_id, version_line]
    if character_id:
        where.append("character_id = ?")
        args.append(character_id)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            f"SELECT * FROM skill WHERE {' AND '.join(where)}", args
        ).fetchall()
        if not rows:
            raise NotFoundError(f"技能不存在: {skill_id}（version_line={version_line}）")
        from genshin_db.publication_evidence import publication_by_id

        states = publication_by_id(conn, "character", [row["character_id"] for row in rows
                                                       if row["character_id"] is not None], version_line)
        items = []
        for row in rows:
            item = _skill_to_dict(conn, row, version_line)
            targets = {("skill", item["id"])}
            if item["proud_skill_group_id"]:
                targets.add(("skill_param", item["proud_skill_group_id"]))
            item["annotations"] = _approved_annotations(conn, targets, version_line)
            item["context_ref"] = _character_ref(item["character_id"], version_line)
            if item["character_id"] in states:
                item["context_ref"]["publication"] = states[item["character_id"]]
            items.append(item)
        return project_detail({"items": items}, "skill", selection)
    finally:
        conn.close()


def get_skill_params(
    db_path: Path,
    group_id: int,
    version_line: str = "release",
    level: int | None = None,
) -> dict:
    group_id = positive_int(group_id, "group_id")
    validate_line(version_line)
    if level is not None:
        level = positive_int(level, "level")
    where, args = ["proud_skill_group_id = ?", "version_line = ?"], [group_id, version_line]
    if level:
        where.append("level = ?")
        args.append(level)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM skill_param"
            f" WHERE {' AND '.join(where)} ORDER BY level", args
        ).fetchall()
        if not rows:
            raise NotFoundError(f"参数组不存在: {group_id}（version_line={version_line}）")
        items = [
            {
                "level": r["level"],
                "param_list": json.loads(r["param_list"]),
                "param_desc": json.loads(r["param_desc"]) if r["param_desc"] else [],
                "add_props": json.loads(r["add_props"]) if r["add_props"] else {},
                "buff_zone": r["buff_zone"],
            }
            for r in rows
        ]
        context_refs = _group_context_refs(conn, group_id, version_line)
        from genshin_db.publication_evidence import publication_by_id

        states = publication_by_id(conn, "character", [ref["character_id"] for ref in context_refs], version_line)
        for ref in context_refs:
            ref["publication"] = states[ref["character_id"]]
        return {"proud_skill_group_id": group_id, "version_line": version_line, "items": items,
                "context_refs": context_refs,
                "context_note": None if context_refs else
                    "未找到已结构化的归属关系；参数仍可用，不表示没有技能描述。旧库可重建补齐来源。"}
    finally:
        conn.close()


def search(db_path: Path, q: str, version_line: str = "release") -> dict:
    text_field(q, "检索词 q", required=True)
    validate_line(version_line)
    q = q.strip()
    if not q:
        raise QueryValidationError("缺少检索词 q")
    like = f"%{q}%"
    conn = _connect(db_path)
    try:
        characters = conn.execute(
            "SELECT id, name, element, rarity, weapon_type FROM character"
            " WHERE version_line=? AND name LIKE ? ORDER BY id LIMIT ?",
            (version_line, like, SEARCH_CAP_PER_TYPE),
        ).fetchall()
        skills = conn.execute(
            "SELECT id, character_id, slot, name FROM skill"
            " WHERE version_line=? AND name LIKE ? ORDER BY id LIMIT ?",
            (version_line, like, SEARCH_CAP_PER_TYPE),
        ).fetchall()
        talents = conn.execute(
            "SELECT id, character_id, level, name FROM talent"
            " WHERE version_line=? AND name LIKE ? ORDER BY id LIMIT ?",
            (version_line, like, SEARCH_CAP_PER_TYPE),
        ).fetchall()
        return {
            "q": q,
            "characters": [dict(r) for r in characters],
            "skills": [dict(r) for r in skills],
            "talents": [dict(r) for r in talents],
        }
    finally:
        conn.close()


# ---------- 已发布追加描述只读查询（ADR-0017） ----------

def list_annotations(
    db_path: Path,
    status: str | None = "approved",
    target_type: str | None = None,
    target_id: int | None = None,
    category: str | None = None,
    version_line: str | None = "release",
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """status=None 表示只读查看全部已发布状态；否则按状态过滤。"""
    validate_line(version_line, nullable=True)
    enum_value(status, "status", ALL_STATUS, nullable=True)
    enum_value(target_type, "target_type", TARGET_TYPES, nullable=True)
    if target_id is not None:
        target_id = positive_int(target_id, "target_id")
    if category is not None:
        text_field(category, "category")
    page, page_size = _page_args(page, page_size)
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if target_type:
        where.append("target_type = ?")
        args.append(target_type)
    if target_id:
        where.append("target_id = ?")
        args.append(target_id)
    if category:
        where.append("category = ?")
        args.append(category)
    if version_line:
        where.append("(version_line IS NULL OR version_line = ?)")
        args.append(version_line)
    sql_where = ("WHERE " + " AND ".join(where)) if where else ""
    conn = _connect(db_path)
    try:
        total = conn.execute(
            f"SELECT COUNT(*) FROM annotation {sql_where}", args
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM annotation {sql_where} ORDER BY id LIMIT ? OFFSET ?",
            [*args, page_size, (page - 1) * page_size],
        ).fetchall()
        return {
            "total": total, "page": page, "page_size": page_size,
            "items": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def list_annotation_categories(
    db_path: Path,
    target_type: str,
    target_id: int,
    status: str,
    version_line: str = "release",
) -> dict:
    """List appendix category counts without disclosing annotation text.

    This is the discovery surface used before an Agent is allowed to read an
    appendix body.  All identity selectors are exact; version-neutral rows are
    included because they apply to both public version lines.
    """
    target_type = enum_value(target_type, "target_type", TARGET_TYPES)
    target_id = positive_int(target_id, "target_id")
    status = enum_value(status, "status", ALL_STATUS)
    validate_line(version_line)
    conn = _connect(db_path)
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='annotation'"
        ).fetchone()
        rows = [] if not exists else conn.execute(
            """SELECT COALESCE(category, '') AS category, COUNT(*) AS count
               FROM annotation
               WHERE target_type=? AND target_id=? AND status=?
                 AND (version_line IS NULL OR version_line=?)
               GROUP BY COALESCE(category, '')
               ORDER BY category""",
            (target_type, target_id, status, version_line),
        ).fetchall()
        categories = [
            {"category": row["category"], "count": row["count"]}
            for row in rows
        ]
        return {
            "target": {"target_type": target_type, "target_id": target_id},
            "status": status,
            "version_line": version_line,
            "total": sum(row["count"] for row in rows),
            "categories": categories,
        }
    finally:
        conn.close()


def annotation_target_scope(
    db_path: Path,
    target_type: str,
    target_id: int,
    version_line: str = "release",
) -> dict:
    """Resolve one annotation target to its owning public game entities.

    The result is deliberately small: it proves that a skill/talent/affix
    belongs to a selected character or equipment item without disclosing the
    surrounding database record.
    """
    target_type = enum_value(target_type, "target_type", TARGET_TYPES)
    target_id = positive_int(target_id, "target_id")
    validate_line(version_line)
    from genshin_db.appendix_navigation import annotation_owner_scope

    conn = _connect(db_path)
    try:
        from genshin_db.publication_evidence import owner_publication

        scope = annotation_owner_scope(conn, target_type, target_id, version_line)
        return {**scope, "publication": owner_publication(conn, scope, version_line)}
    finally:
        conn.close()


def create_annotation(
    db_path: Path,
    target_type: str,
    target_id: int,
    content: str,
    category: str = "",
    title: str = "",
    version_line: str | None = None,
) -> dict:
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "queries.create_annotation")
    missing = [k for k, v in (("target_type", target_type), ("target_id", target_id),
                              ("content", content)) if not v]
    if missing:
        raise QueryValidationError(f"缺少字段: {missing}")
    ann_id = add_annotation(
        db_path,
        Annotation(target_type=target_type, target_id=target_id,
                   category=category, title=title, content=content, status="draft",
                   version_line=version_line),
    )
    return {"id": ann_id, "status": "draft"}


def _require_annotation(conn: sqlite3.Connection, ann_id: int) -> None:
    row = conn.execute("SELECT id FROM annotation WHERE id=?", (ann_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"追加描述不存在: {ann_id}")


@database_write
def update_annotation(db_path: Path, ann_id: int, fields: dict) -> dict:
    """修改 category/title/content/status（status 可选值见 ALL_STATUS）。无有效字段抛 ValueError。"""
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "queries.update_annotation")
    ann_id = positive_int(ann_id, "annotation_id")
    if not isinstance(fields, dict):
        raise QueryValidationError("请求体必须为 JSON 对象")
    if fields.keys() - {"category", "title", "content", "status"}:
        raise QueryValidationError("包含不可更新的字段")
    sets, args = [], []
    for key in ("category", "title", "content"):
        if key in fields:
            text_field(fields[key], key, required=key == "content")
            sets.append(f"{key} = ?")
            args.append(fields[key])
    if "status" in fields:
        enum_value(fields["status"], "status", ALL_STATUS)
        sets.append("status = ?")
        args.append(fields["status"])
    if not sets:
        raise QueryValidationError("没有可更新的字段（category/title/content/status）")
    sets.append("updated_at = ?")
    args.append(datetime.now(UTC).isoformat())
    args.append(ann_id)
    conn = _connect_write(db_path)
    try:
        _require_annotation(conn, ann_id)
        with conn:
            conn.execute(f"UPDATE annotation SET {', '.join(sets)} WHERE id=?", args)
        new_status = conn.execute(
            "SELECT status FROM annotation WHERE id=?", (ann_id,)
        ).fetchone()["status"]
        return {"id": ann_id, "status": new_status}
    finally:
        conn.close()


@database_write
def delete_annotation(db_path: Path, ann_id: int) -> dict:
    from genshin_db.live_mutation_policy import assert_candidate_database

    db_path = assert_candidate_database(db_path, "queries.delete_annotation")
    ann_id = positive_int(ann_id, "annotation_id")
    conn = _connect_write(db_path)
    try:
        _require_annotation(conn, ann_id)
        with conn:
            conn.execute("DELETE FROM annotation WHERE id=?", (ann_id,))
        return {"deleted": ann_id}
    finally:
        conn.close()


# Shared equipment API (same functions for HTTP, GUI and application composition).
from genshin_db.equipment_queries import (  # noqa: F401
    equipment_capabilities,
    equipment_read_session,
    get_artifact_detail,
    get_artifact_main_stats,
    get_artifact_set_detail,
    get_equip_affix,
    get_equip_affix_detail,
    get_weapon_detail,
    get_weapon_stats,
    list_artifact_main_props,
    list_artifact_sets,
    list_artifact_substats,
    list_artifacts,
    list_weapons,
)
