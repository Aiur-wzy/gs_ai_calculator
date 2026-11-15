"""Shared, paginated equipment reads and deterministic growth evaluation."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from genshin_db.equipment import (
    EQUIP_FIELDS,
    MAX_PUBLIC_ID,
    PROPERTIES,
    WEAPON_TYPES,
    EquipmentError,
    equipment_capabilities,
    equipment_datasets,
    validate_equipment,
)
from genshin_db.locking import database_lock
from genshin_db.validation import positive_int, text_field
from genshin_db.validation import version_line as validate_line

__all__ = [
    "equipment_capabilities",
    "equipment_read_session",
    "get_artifact_detail",
    "get_artifact_main_stat_for_slot",
    "get_artifact_main_stats",
    "get_artifact_set_detail",
    "get_equip_affix",
    "get_equip_affix_detail",
    "get_weapon_detail",
    "get_weapon_stats",
    "list_artifact_main_props",
    "list_artifact_sets",
    "list_artifact_substats",
    "list_artifacts",
    "list_weapons",
]

_SESSION = ContextVar("equipment_read_session", default=None)


class _BorrowedConnection:
    """Shared readers may close their handle without closing the batch snapshot."""

    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def close(self):
        pass


def borrow_read_connection(db_path):
    active = _SESSION.get()
    if active is not None and active["path"] == Path(db_path).resolve():
        return _BorrowedConnection(active["conn"])
    return None


def borrow_read_cache(db_path, namespace):
    """Return component-local state whose lifetime is exactly the locked read transaction."""
    active = _SESSION.get()
    if active is not None and active["path"] == Path(db_path).resolve():
        return active.setdefault("component_cache", {}).setdefault(namespace, {})
    return None


def _integer(value, name, zero=False):
    if zero and (value == 0 or value == "0") and not isinstance(value, (bool, float)):
        return 0
    result = positive_int(value, name)
    if result > MAX_PUBLIC_ID:
        raise ValueError(f"{name} 超过安全整数范围")
    return result


def _enum(value, name, choices, *, optional=False):
    if optional and value in (None, ""):
        return None
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"非法 {name}: {value!r}")
    return value


def _page(page, page_size):
    page, size = _integer(page, "page"), min(_integer(page_size, "page_size"), 100)
    if (page - 1) * size > 2**63 - 1:
        raise ValueError("分页偏移超出范围")
    return page, size


def _rarity(value):
    if value in (None, ""):
        return None
    value = _integer(value, "rarity")
    if value > 5:
        raise ValueError("rarity 必须为 1–5")
    return value


def _row(row):
    if row is None:
        return None
    out = dict(row)
    for key in list(out):
        if key.endswith("_json"):
            out[key[:-5]] = json.loads(out.pop(key))
        elif key in ("name_source", "desc_source") and out[key]:
            out[key] = json.loads(out[key])
    return out


def _one(conn, table, key, object_id, line):
    row = conn.execute(f"SELECT * FROM {table} WHERE {key}=? AND version_line=?", (object_id, line)).fetchone()
    if row is None:
        raise EquipmentError(f"未收录 {table} #{object_id}", "not_found", 404)
    return _row(row)


@contextmanager
def equipment_read_session(db_path):
    """A bounded read transaction; validations are reused only inside this locked session."""
    path = Path(db_path).resolve()
    active = _SESSION.get()
    if active is not None and active["path"] == path:
        yield
        return
    with database_lock(path):
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        token = _SESSION.set({"path": path, "conn": conn, "versions": {}})
        try:
            yield
        finally:
            _SESSION.reset(token)
            conn.close()


@contextmanager
def _read(db_path, line):
    validate_line(line)
    with equipment_read_session(db_path):
        active = _SESSION.get()
        conn = active["conn"]
        try:
            if line in active["versions"]:
                yield conn, active["versions"][line]
                return
            state = equipment_datasets(conn)["equipment"]["lines"][line]
            if state["state"] != "ready":
                reason, status = {"missing": ("dataset_unavailable", 409),
                                  "incompatible": ("unsupported_schema", 409),
                                  "invalid": ("dataset_invalid", 500)}[state["state"]]
                raise EquipmentError(f"装备资料状态: {state['state']}（{line}）", reason, status, state)
            # Do not conceal broken references behind a previously published marker.
            validate_equipment(conn, line)
            version = {"version_line": line, "upstream_commit": state["upstream_commit"],
                       "equipment_schema_version": state["schema_version"], "built_at": state["built_at"]}
            active["versions"][line] = version
            yield conn, version
        except (sqlite3.DatabaseError, json.JSONDecodeError, KeyError, TypeError, IndexError, OverflowError) as exc:
            raise EquipmentError(f"装备资料结构损坏: {exc}") from exc


def _annotations(conn, targets, line):
    if not targets or not conn.execute("SELECT 1 FROM sqlite_master WHERE name='annotation'").fetchone():
        return []
    rows = conn.execute("SELECT * FROM annotation WHERE status='approved' "
                        "AND (version_line=? OR version_line IS NULL) ORDER BY id", (line,))
    return [dict(r) for r in rows if (r["target_type"], r["target_id"]) in targets]


def _warnings(obj, kind, object_id):
    result = []
    for field in ("name", "desc"):
        status = obj.get(field + "_status")
        if status and status != "present":
            result.append({"code": status, "message": f"{'名称' if field == 'name' else '原文'}未提供或未命中映射",
                           "target": {"type": kind, "id": object_id}, "field": field,
                           "source_refs": obj.get("source_refs", [])})
    return result


def _value(prop_type, value, refs):
    info = PROPERTIES.get(prop_type, {"name": prop_type, "unit": "unknown"})
    return {"prop_type": prop_type, "prop_name": info["name"], "value": value, "unit": info["unit"],
            "value_status": "source_missing" if value is None else "present", "source_refs": refs}


def _value_warnings(stat, kind, object_id):
    result = []
    if stat["value"] is None:
        result.append({"code": "source_missing", "message": "源配置未提供可求值数值",
                       "target": {"type": kind, "id": object_id}, "field": stat["prop_type"], "source_refs": stat["source_refs"]})
    if stat["unit"] == "unknown":
        result.append({"code": "unmapped_prop_type", "message": "属性单位尚未标定",
                       "target": {"type": kind, "id": object_id}, "field": stat["prop_type"], "source_refs": stat["source_refs"]})
    return result


def _catalog(conn, table, field, object_id, line):
    return [_row(r) for r in conn.execute(f"SELECT * FROM {table} WHERE {field}=? AND version_line=? ORDER BY id", (object_id, line))]


def _states(conn, weapon, line):
    rows = conn.execute("SELECT promote_level,unlock_max_level FROM weapon_promote WHERE "
                        "promote_group_id=? AND version_line=? ORDER BY promote_level", (weapon["promote_group_id"], line)).fetchall()
    return [{"ascension": r[0], "min_level": rows[i-1][1] if i else 1, "max_level": r[1]} for i, r in enumerate(rows)]


def _effect(conn, affix_id, line):
    effect = _one(conn, "equip_affix", "affix_id", affix_id, line)
    effect["annotations"] = _annotations(conn, {("equip_affix", affix_id)}, line)
    return effect


def _listing(conn, table, where, args, page, size):
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM {table} x WHERE {clause}", args).fetchone()[0]
    rows = conn.execute(f"SELECT x.* FROM {table} x WHERE {clause} ORDER BY x.id LIMIT ? OFFSET ?",
                        [*args, size, (page - 1) * size])
    return [_row(row) for row in rows], total


def _filters(line, name, rarity):
    where, args = ["x.version_line=?"], [line]
    if name not in (None, ""):
        text_field(name, "name")
        # Validate Unicode before SQLite binding so malformed requests remain 400.
        name.encode("utf-8")
        escaped = name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("x.name LIKE ? ESCAPE '\\'")
        args.append(f"%{escaped}%")
    if rarity is not None:
        where.append("x.rarity=?")
        args.append(rarity)
    return where, args


def list_weapons(db_path, version_line="release", *, name=None, weapon_type=None, rarity=None,
                 catalog_scope="codex", page=1, page_size=20):
    page, size = _page(page, page_size)
    rarity = _rarity(rarity)
    weapon_type = _enum(weapon_type, "weapon_type", WEAPON_TYPES, optional=True)
    _enum(catalog_scope, "catalog_scope", ("codex", "all"))
    where, args = _filters(version_line, name, rarity)
    if weapon_type:
        where.append("x.weapon_type=?")
        args.append(weapon_type)
    if catalog_scope == "codex":
        where.append("EXISTS(SELECT 1 FROM weapon_codex c WHERE c.weapon_id=x.id AND c.version_line=x.version_line)")
    with _read(db_path, version_line) as (conn, version):
        rows, total = _listing(conn, "weapon", where, args, page, size)
        items = []
        for row in rows:
            item = {k: row[k] for k in ("id", "version_line", "name", "name_status", "rarity", "weapon_type")}
            item.update(in_codex=bool(_catalog(conn, "weapon_codex", "weapon_id", row["id"], version_line)),
                        max_level=max(s["max_level"] for s in _states(conn, row, version_line)),
                        has_effect=any(row["skill_affix_ids"]))
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": size,
                "catalog_scope": catalog_scope, "data_version": version}


def get_weapon_detail(db_path, weapon_id, version_line="release", *, sections=None, refinement=None):
    from genshin_db.context_query import project_detail, validate_selection
    from genshin_db.publication_evidence import publication_by_id
    selection = validate_selection("weapon", sections=sections, refinement=refinement)
    weapon_id = _integer(weapon_id, "weapon_id")
    with _read(db_path, version_line) as (conn, version):
        weapon = _one(conn, "weapon", "id", weapon_id, version_line)
        weapon["publication"] = publication_by_id(conn, "weapon", [weapon_id], version_line,
                                                  detailed=True)[weapon_id]
        weapon["catalog_refs"] = _catalog(conn, "weapon_codex", "weapon_id", weapon_id, version_line)
        weapon["in_codex"] = bool(weapon["catalog_refs"])
        weapon["properties"] = [_row(r) for r in conn.execute("SELECT * FROM weapon_property WHERE weapon_id=? AND version_line=? ORDER BY source_index", (weapon_id, version_line))]
        weapon["available_states"] = _states(conn, weapon, version_line)
        groups, targets = {}, {("weapon", weapon_id)}
        warnings = _warnings(weapon, "weapon", weapon_id)
        for row in conn.execute("SELECT * FROM weapon_effect WHERE weapon_id=? AND version_line=? ORDER BY source_index,source_level", (weapon_id, version_line)):
            effect = _effect(conn, row["affix_id"], version_line)
            group = groups.setdefault(row["source_index"], {"source_index": row["source_index"], "affix_group_id": row["affix_group_id"], "refinements": []})
            group["refinements"].append({"refinement": row["refinement"], **effect})
            targets.add(("equip_affix", row["affix_id"]))
            warnings.extend(_warnings(effect, "equip_affix", row["affix_id"]))
        return project_detail({"weapon": weapon, "effects": list(groups.values()), "annotations": _annotations(conn, targets, version_line),
                "warnings": warnings, "data_version": version}, "weapon", selection)


def get_weapon_stats(db_path, weapon_id, level, ascension, version_line="release"):
    weapon_id, level = _integer(weapon_id, "weapon_id"), _integer(level, "level")
    ascension = _integer(ascension, "ascension", True)
    with _read(db_path, version_line) as (conn, version):
        weapon = _one(conn, "weapon", "id", weapon_id, version_line)
        states = _states(conn, weapon, version_line)
        if not any(s["ascension"] == ascension and s["min_level"] <= level <= s["max_level"] for s in states):
            raise EquipmentError("武器等级与突破阶段不匹配", "invalid_equipment_state", 400, {"available_states": states})
        promote = _row(conn.execute("SELECT * FROM weapon_promote WHERE promote_group_id=? AND promote_level=? AND version_line=?", (weapon["promote_group_id"], ascension, version_line)).fetchone())
        additions = {}
        for prop in promote["add_props"]:
            if prop.get("propType"):
                additions[prop["propType"]] = additions.get(prop["propType"], 0) + prop.get("value", 0)
        properties, warnings, seen = [], [], set()
        for raw in conn.execute("SELECT * FROM weapon_property WHERE weapon_id=? AND version_line=? ORDER BY source_index", (weapon_id, version_line)):
            prop = _row(raw)
            if not prop["prop_type"]:
                continue
            curve = _row(conn.execute("SELECT * FROM weapon_curve WHERE curve_type=? AND level=? AND version_line=?", (prop["curve_type"], level, version_line)).fetchone())
            if curve is None or curve["arith"] != "ARITH_MULTI":
                raise EquipmentError("成长曲线缺失或算法不支持")
            code = prop["prop_type"]
            addition = additions.get(code, 0)
            value = None if prop["init_value"] is None else prop["init_value"] * curve["value"] + addition
            stat = _value(code, value, prop["source_refs"] + curve["source_refs"] + promote["source_refs"])
            stat["calculation"] = {"init_value": prop["init_value"], "curve_type": prop["curve_type"],
                                   "curve_value": curve["value"], "arith": curve["arith"], "promotion_add": addition}
            properties.append(stat)
            warnings.extend(_value_warnings(stat, "weapon", weapon_id))
            seen.add(code)
        for code, value in additions.items():
            if code not in seen and value:
                stat = _value(code, value, promote["source_refs"])
                stat["calculation"] = {"init_value": None, "curve_type": None, "curve_value": None, "arith": None, "promotion_add": value}
                properties.append(stat)
                warnings.extend(_value_warnings(stat, "weapon", weapon_id))
        return {"weapon_id": weapon_id, "version_line": version_line, "level": level, "ascension": ascension,
                "properties": properties, "formula_version": "weapon-growth-v1", "warnings": warnings, "data_version": version}


def _set_info(conn, row, line):
    row["catalog_rarities"] = [r[0] for r in conn.execute("SELECT DISTINCT rarity FROM artifact_codex WHERE set_id=? AND version_line=? ORDER BY rarity", (row["id"], line))]
    row["configured_rarities"] = [r[0] for r in conn.execute("SELECT DISTINCT rarity FROM artifact WHERE set_id=? AND version_line=? ORDER BY rarity", (row["id"], line))]
    row["in_codex"] = bool(row["catalog_rarities"])
    return row


def list_artifact_sets(db_path, version_line="release", *, name=None, rarity=None,
                      catalog_scope="codex", page=1, page_size=20):
    page, size = _page(page, page_size)
    rarity = _rarity(rarity)
    _enum(catalog_scope, "catalog_scope", ("codex", "all"))
    where, args = _filters(version_line, name, None)
    if catalog_scope == "codex" or rarity is not None:
        table = "artifact_codex" if catalog_scope == "codex" else "artifact"
        where.append(f"EXISTS(SELECT 1 FROM {table} c WHERE c.set_id=x.id AND c.version_line=x.version_line" + (" AND c.rarity=?" if rarity else "") + ")")
        if rarity:
            args.append(rarity)
    with _read(db_path, version_line) as (conn, version):
        rows, total = _listing(conn, "artifact_set", where, args, page, size)
        items = []
        for row in rows:
            info = _set_info(conn, row, version_line)
            items.append({k: info[k] for k in ("id", "version_line", "name", "name_status", "in_codex", "catalog_rarities", "configured_rarities", "required_counts")})
        return {"items": items, "total": total, "page": page, "page_size": size, "catalog_scope": catalog_scope, "data_version": version}


def get_artifact_set_detail(db_path, set_id, version_line="release", *, sections=None, set_thresholds=None):
    from genshin_db.context_query import project_detail, validate_selection
    from genshin_db.publication_evidence import publication_by_id
    selection = validate_selection("artifact_set", sections=sections, set_thresholds=set_thresholds)
    set_id = _integer(set_id, "set_id")
    with _read(db_path, version_line) as (conn, version):
        artifact_set = _set_info(conn, _one(conn, "artifact_set", "id", set_id, version_line), version_line)
        artifact_set["publication"] = publication_by_id(conn, "artifact_set", [set_id], version_line,
                                                        detailed=True)[set_id]
        effects, targets = [], {("artifact_set", set_id)}
        warnings = _warnings(artifact_set, "artifact_set", set_id)
        for raw in conn.execute("SELECT * FROM artifact_set_effect WHERE set_id=? AND version_line=? ORDER BY source_index", (set_id, version_line)):
            row = _row(raw)
            effect = _effect(conn, row["affix_id"], version_line) if row["affix_id"] else None
            effects.append({"required_count": row["required_count"], "source_index": row["source_index"], "relation_status": row["relation_status"], "effect": effect})
            if effect:
                targets.add(("equip_affix", row["affix_id"]))
                warnings.extend(_warnings(effect, "equip_affix", row["affix_id"]))
            else:
                warnings.append({"code": "source_empty", "message": "源套装未提供效果组", "target": {"type": "artifact_set", "id": set_id}, "field": f"effects[{row['source_index']}]", "source_refs": row["source_refs"]})
        pieces = [dict(r) for r in conn.execute("SELECT i.artifact_id,i.equip_type,c.rarity,i.codex_id FROM artifact_codex_item i JOIN artifact_codex c ON c.id=i.codex_id AND c.version_line=i.version_line WHERE c.set_id=? AND c.version_line=? ORDER BY i.artifact_id,i.codex_id", (set_id, version_line))]
        return project_detail({"artifact_set": artifact_set, "effects": effects, "piece_refs": pieces[:25],
                "piece_refs_total": len(pieces), "piece_refs_truncated": len(pieces) > 25,
                "annotations": _annotations(conn, targets, version_line), "warnings": warnings, "data_version": version}, "artifact_set", selection)


def list_artifacts(db_path, version_line="release", *, name=None, set_id=None, equip_type=None,
                   rarity=None, catalog_scope="codex", page=1, page_size=20):
    page, size = _page(page, page_size)
    rarity = _rarity(rarity)
    equip_type = _enum(equip_type, "equip_type", EQUIP_FIELDS, optional=True)
    _enum(catalog_scope, "catalog_scope", ("codex", "all"))
    if set_id not in (None, ""):
        set_id = _integer(set_id, "set_id")
    where, args = _filters(version_line, name, rarity)
    for field, value in (("set_id", set_id), ("equip_type", equip_type)):
        if value not in (None, ""):
            where.append(f"x.{field}=?")
            args.append(value)
    if catalog_scope == "codex":
        where.append("EXISTS(SELECT 1 FROM artifact_codex_item c WHERE c.artifact_id=x.id AND c.version_line=x.version_line)")
    with _read(db_path, version_line) as (conn, version):
        rows, total = _listing(conn, "artifact", where, args, page, size)
        items = []
        for row in rows:
            item = {k: row[k] for k in ("id", "version_line", "name", "name_status", "set_id", "equip_type", "rarity", "append_prop_num")}
            item.update(max_level=row["max_source_level"] - 1,
                        in_codex=bool(conn.execute("SELECT 1 FROM artifact_codex_item WHERE artifact_id=? AND version_line=?", (row["id"], version_line)).fetchone()),
                        set_name=_one(conn, "artifact_set", "id", row["set_id"], version_line)["name"] if row["set_id"] else None)
            items.append(item)
        return {"items": items, "total": total, "page": page, "page_size": size, "catalog_scope": catalog_scope, "data_version": version}


def get_artifact_detail(db_path, artifact_id, version_line="release"):
    artifact_id = _integer(artifact_id, "artifact_id")
    with _read(db_path, version_line) as (conn, version):
        artifact = _one(conn, "artifact", "id", artifact_id, version_line)
        artifact["max_level"] = artifact["max_source_level"] - 1
        artifact["add_prop_display_levels"] = [x - 1 for x in artifact["add_prop_levels"] if 1 <= x <= artifact["max_source_level"]]
        artifact["catalog_refs"] = [dict(r) for r in conn.execute("SELECT * FROM artifact_codex_item WHERE artifact_id=? AND version_line=? ORDER BY codex_id", (artifact_id, version_line))]
        artifact["in_codex"] = bool(artifact["catalog_refs"])
        sid = artifact["set_id"]
        set_ref = {"id": sid, "name": _one(conn, "artifact_set", "id", sid, version_line)["name"], "path": f"/v1/artifact-sets/{sid}?version_line={version_line}"} if sid else None
        return {"artifact": artifact, "set_ref": set_ref,
                "main_prop_query": {"depot_id": artifact["main_prop_depot_id"], "path": f"/v1/artifact-main-props?depot_id={artifact['main_prop_depot_id']}&version_line={version_line}"},
                "substat_query": {"depot_id": artifact["append_prop_depot_id"], "path": f"/v1/artifact-substats?depot_id={artifact['append_prop_depot_id']}&version_line={version_line}"},
                "annotations": _annotations(conn, {("artifact", artifact_id)}, version_line),
                "warnings": _warnings(artifact, "artifact", artifact_id), "data_version": version}


def _pool(db_path, table, depot_id, version_line, prop_type, page, page_size):
    depot_id = _integer(depot_id, "depot_id")
    page, size = _page(page, page_size)
    if prop_type not in (None, ""):
        text_field(prop_type, "prop_type")
        prop_type.encode("utf-8")
    with _read(db_path, version_line) as (conn, version):
        known = set(PROPERTIES) | {r[0] for r in conn.execute(f"SELECT DISTINCT prop_type FROM {table} WHERE version_line=?", (version_line,))}
        _enum(prop_type, "prop_type", known, optional=True)
        if not conn.execute(f"SELECT 1 FROM {table} WHERE depot_id=? AND version_line=?", (depot_id, version_line)).fetchone():
            raise EquipmentError(f"词条池未收录: {depot_id}", "not_found", 404)
        where, args = ["x.version_line=?", "x.depot_id=?"], [version_line, depot_id]
        if prop_type:
            where.append("x.prop_type=?")
            args.append(prop_type)
        rows, total = _listing(conn, table, where, args, page, size)
        items = []
        for row in rows:
            stat = _value(row["prop_type"], row.get("value"), row["source_refs"])
            if table == "artifact_main_prop":
                item = {k: row[k] for k in ("id", "depot_id", "prop_type", "affix_name", "source_refs")}
                item.update(prop_name=stat["prop_name"], unit=stat["unit"])
            else:
                item = {"id": row["id"], "depot_id": depot_id, "source_group_id": row["source_group_id"], **stat}
            items.append(item)
        return {"depot_id": depot_id, "items": items, "total": total, "page": page, "page_size": size, "data_version": version}


def list_artifact_main_props(db_path, depot_id, version_line="release", *, prop_type=None, page=1, page_size=20):
    return _pool(db_path, "artifact_main_prop", depot_id, version_line, prop_type, page, page_size)


def list_artifact_substats(db_path, depot_id, version_line="release", *, prop_type=None, page=1, page_size=20):
    return _pool(db_path, "artifact_substat", depot_id, version_line, prop_type, page, page_size)


def get_artifact_main_stats(db_path, artifact_id, level, main_prop_id, version_line="release"):
    artifact_id = _integer(artifact_id, "artifact_id")
    level, main_prop_id = _integer(level, "level", True), _integer(main_prop_id, "main_prop_id")
    with _read(db_path, version_line) as (conn, version):
        artifact = _one(conn, "artifact", "id", artifact_id, version_line)
        prop = _row(conn.execute("SELECT * FROM artifact_main_prop WHERE id=? AND version_line=?", (main_prop_id, version_line)).fetchone())
        if level >= artifact["max_source_level"] or prop is None or prop["depot_id"] != artifact["main_prop_depot_id"]:
            raise EquipmentError("圣遗物等级越界或主词条不属于当前池", "invalid_equipment_state", 400)
        source = _row(conn.execute("SELECT * FROM artifact_level WHERE source_rank=? AND source_level=? AND version_line=?", (artifact["rarity"], level + 1, version_line)).fetchone())
        if source is None:
            raise EquipmentError("圣遗物成长等级行缺失")
        values = [p.get("value", 0) for p in source["add_props"] if p.get("propType") == prop["prop_type"]]
        stat = _value(prop["prop_type"], sum(values) if values else None, artifact["source_refs"] + prop["source_refs"] + source["source_refs"])
        return {"artifact_id": artifact_id, "version_line": version_line, "level": level, "source_level": level + 1,
                "rarity": artifact["rarity"], "main_prop_id": main_prop_id, "depot_id": prop["depot_id"], "stat": stat,
                "warnings": _value_warnings(stat, "artifact", artifact_id), "data_version": version}


def get_artifact_main_stat_for_slot(db_path, rarity, equip_type, level, prop_type, version_line="release"):
    """Resolve an abstract main stat without inventing/selecting a piece ID.

    Every configured depot matching the exact slot/rank is considered. An
    absent or conflicting source is returned explicitly, never first-match.
    """
    rarity, level = _integer(rarity, "rarity"), _integer(level, "level", True)
    _enum(equip_type, "equip_type", tuple(EQUIP_FIELDS))
    _enum(prop_type, "prop_type", tuple(PROPERTIES))
    with _read(db_path, version_line) as (conn, version):
        rows = conn.execute("SELECT DISTINCT main_prop_depot_id,max_source_level FROM artifact "
                            "WHERE rarity=? AND equip_type=? AND version_line=?", (rarity, equip_type, version_line)).fetchall()
        depots = sorted({r[0] for r in rows if r[0] is not None and level < r[1]})
        allowed = [depot for depot in depots if conn.execute(
            "SELECT 1 FROM artifact_main_prop WHERE depot_id=? AND prop_type=? AND version_line=? LIMIT 1",
            (depot, prop_type, version_line)).fetchone()]
        sources = [_row(row) for row in conn.execute(
            "SELECT * FROM artifact_level WHERE source_rank=? AND source_level=? AND version_line=?",
            (rarity, level + 1, version_line))]
        values, unknown = set(), False
        for row in sources:
            matching = [p.get("value") for p in row["add_props"] if p.get("propType") == prop_type]
            if not matching:
                continue
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in matching):
                unknown = True
                continue
            total = sum(matching)
            if not math.isfinite(total):
                unknown = True
                continue
            values.add(total)
        values = sorted(values)
        state = ("source_missing" if unknown else "ready" if allowed and len(values) == 1
                 else "ambiguous" if allowed and len(values) > 1 else "source_missing")
        refs = [ref for row in sources for ref in row.get("source_refs", [])]
        return {"state": state, "selector": {"rarity": rarity, "equip_type": equip_type, "level": level, "prop_type": prop_type},
                "value": values[0] if state == "ready" else None, "candidate_values": values,
                "eligible_depot_ids": allowed, "source_refs": refs, "data_version": version}


def get_equip_affix_detail(db_path, affix_id, version_line="release"):
    from genshin_db.publication_evidence import publication_by_id

    affix_id = _integer(affix_id, "affix_id")
    with _read(db_path, version_line) as (conn, version):
        effect = _effect(conn, affix_id, version_line)
        refs = [{"kind": "weapon", "id": r[0], "source_index": r[1], "refinement": r[2], "path": f"/v1/weapons/{r[0]}?version_line={version_line}", "version_line": version_line}
                for r in conn.execute("SELECT weapon_id,source_index,refinement FROM weapon_effect WHERE affix_id=? AND version_line=? ORDER BY weapon_id,source_index", (affix_id, version_line))]
        refs += [{"kind": "artifact_set", "id": r[0], "required_count": r[1], "path": f"/v1/artifact-sets/{r[0]}?version_line={version_line}", "version_line": version_line}
                 for r in conn.execute("SELECT set_id,required_count FROM artifact_set_effect WHERE affix_id=? AND version_line=? ORDER BY set_id,source_index", (affix_id, version_line))]
        for kind in ("weapon", "artifact_set"):
            selected = [ref for ref in refs if ref["kind"] == kind]
            states = publication_by_id(conn, kind, [ref["id"] for ref in selected], version_line)
            for ref in selected:
                ref["publication"] = states[ref["id"]]
        return {"effect": effect, "context_refs": refs, "context_note": None if refs else "当前资料未收录该效果归属", "data_version": version}


get_equip_affix = get_equip_affix_detail
