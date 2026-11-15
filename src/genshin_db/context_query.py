"""Bounded public context queries and lossless, explicit detail projections.

Selections never interpret descriptions or generate a rotation. Omitted sections are
reported separately from requested values absent from the database.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3

from genshin_db.equipment import MAX_PUBLIC_ID, EquipmentError
from genshin_db.validation import version_line as validate_line

LIMITS = {"characters": 4, "weapons": 4, "artifact_sets": 8}
SECTIONS = {
    "character": ("character", "skills", "talents", "extra_info", "annotations"),
    "skill": ("description", "params", "annotations", "context_ref"),
    "weapon": ("weapon", "effects", "annotations"),
    "artifact_set": ("artifact_set", "effects", "piece_refs", "annotations"),
}
SELECTORS = {
    "character": ("sections", "levels"), "skill": ("sections", "levels"),
    "weapon": ("sections", "refinement"), "artifact_set": ("sections", "set_thresholds"),
}
SELECTION_MAXIMUMS = {"levels": 15, "refinement": 5, "set_thresholds": 5}


def context_request_schema():
    """Return a model/API schema generated from the validator constants."""
    identity = {"type": "integer", "minimum": 1, "maximum": MAX_PUBLIC_ID}

    def text_array(values):
        return {"type": "array", "minItems": 1, "maxItems": len(values),
                "items": {"type": "string", "enum": list(values)}}

    def integer_array(maximum):
        return {"type": "array", "minItems": 1, "maxItems": maximum,
                "items": {"type": "integer", "minimum": 1, "maximum": maximum}}

    rows = {
        "characters": {"type": "object", "properties": {
            "id": identity, "sections": text_array(SECTIONS["character"]),
            "levels": integer_array(SELECTION_MAXIMUMS["levels"])},
            "required": ["id"], "additionalProperties": False},
        "weapons": {"type": "object", "properties": {
            "id": identity, "sections": text_array(SECTIONS["weapon"]),
            "refinement": {"type": "integer", "minimum": 1,
                           "maximum": SELECTION_MAXIMUMS["refinement"]}},
            "required": ["id"], "additionalProperties": False},
        "artifact_sets": {"type": "object", "properties": {
            "id": identity, "sections": text_array(SECTIONS["artifact_set"]),
            "set_thresholds": integer_array(SELECTION_MAXIMUMS["set_thresholds"])},
            "required": ["id"],
            "additionalProperties": False},
    }
    collections = {name: {"type": "array", "maxItems": LIMITS[name], "items": row}
                   for name, row in rows.items()}
    schema = {
        "type": "object",
        "properties": {"version_line": {"type": "string", "enum": ["release", "beta"],
                                           "default": "release"}, **collections},
        "additionalProperties": False,
        # The DSH runtime accepts a deliberately small JSON-schema subset.  It
        # does not accept an ``anyOf`` next to object keywords, so the
        # authoritative Python validator keeps the cross-field rule that at
        # least one collection must be non-empty.  Keeping the transport
        # schema representable is more important than advertising a condition
        # that would prevent the entire native-tool snapshot from loading.
        "examples": [{"version_line": "release", "characters": [{
            "id": 10000133,
            "sections": ["character", "skills", "talents", "extra_info", "annotations"],
            "levels": [10, 13],
        }]}],
    }
    return copy.deepcopy(schema)


def fingerprint(value):
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def manual_content_fingerprint(conn):
    """Hash identities AND actual contents, including deletion and review changes."""
    content = {}
    for table, order in (("annotation", "id"), ("character_note", "character_id,version_line")):
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name=? AND type='table'", (table,)).fetchone()
        content[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}")] if exists else []
    return fingerprint(content)


def _integer(value, name, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} 必须为 1–{maximum} 的整数")
    return value


def _array(value, name, maximum, choices=None):
    if not isinstance(value, list) or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name} 必须为非空数组，至多 {maximum} 项")
    if choices is None:
        result = [_integer(v, name, maximum) for v in value]
    else:
        if any(not isinstance(v, str) or v not in choices for v in value):
            raise ValueError(f"非法 {name}；可选 {list(choices)}")
        result = list(value)
    if len(set(result)) != len(result):
        raise ValueError(f"{name} 不得重复")
    return result


def validate_selection(kind, *, sections=None, levels=None, refinement=None, set_thresholds=None):
    selection = {}
    if sections is not None:
        selection["sections"] = _array(sections, "sections", len(SECTIONS[kind]), SECTIONS[kind])
    if levels is not None:
        selection["levels"] = _array(levels, "levels", SELECTION_MAXIMUMS["levels"])
    if refinement is not None:
        selection["refinement"] = _integer(
            refinement, "refinement", SELECTION_MAXIMUMS["refinement"])
    if set_thresholds is not None:
        selection["set_thresholds"] = _array(
            set_thresholds, "set_thresholds", SELECTION_MAXIMUMS["set_thresholds"])
    if selection.keys() - set(SELECTORS[kind]):
        raise ValueError(f"{kind} 不支持该选择字段")
    return selection


def http_selection(qs, kind):
    result = {}
    for key in SELECTORS[kind]:
        if key not in qs:
            continue
        raw = qs[key][0]
        if key == "sections":
            result[key] = raw.split(",")
        else:
            values = raw.split(",")
            if any(not v.isascii() or not v.isdecimal() for v in values):
                raise ValueError(f"{key} 必须为十进制整数" + ("列表" if key != "refinement" else ""))
            result[key] = int(raw) if key == "refinement" else [int(v) for v in values]
    return validate_selection(kind, **result)


def _select_levels(item, levels, missing, path):
    params = item.get("params", {})
    for level in levels:
        if str(level) not in params:
            missing.append({"path": path + ".params", "value": level, "reason": "source_missing"})
    item["params"] = {k: v for k, v in params.items() if int(k) in levels}


def project_detail(detail, kind, selection):
    from genshin_db.parameter_references import add_context_parameter_references
    if not selection:
        return add_context_parameter_references(detail, kind)
    # The source query result belongs to this call; do not change database values.
    requested = selection.get("sections", list(SECTIONS[kind]))
    missing = []
    if kind == "character":
        if "levels" in selection:
            for index, item in enumerate(detail["skills"]):
                _select_levels(item, selection["levels"], missing, f"skills[{index}]")
            for index, item in enumerate(detail["extra_info"]):
                if item["info_type"] == "sub_skill":
                    _select_levels(item, selection["levels"], missing, f"extra_info[{index}]")
        result = {k: v for k, v in detail.items() if k in requested or k in ("character", "annotations")}
        missing = [v for v in missing if v["path"].split("[")[0] in result]
    elif kind == "skill":
        result = detail
        for index, item in enumerate(result["items"]):
            if "levels" in selection:
                _select_levels(item, selection["levels"], missing, f"items[{index}]")
            if "params" not in requested:
                item.pop("params", None)
        if "params" not in requested:
            missing = []
    else:
        result = {k: v for k, v in detail.items() if k in requested or k in (kind, "annotations", "warnings", "data_version")}
        if "piece_refs" in requested:
            for key in ("piece_refs_total", "piece_refs_truncated"):
                if key in detail:
                    result[key] = detail[key]
        selected_affixes = set()
        if "effects" in result:
            if kind == "weapon":
                for group in result["effects"]:
                    if "refinement" in selection:
                        group["refinements"] = [r for r in group["refinements"] if r["refinement"] == selection["refinement"]]
                        if not group["refinements"]:
                            missing.append({"path": f"effects[source_index={group['source_index']}].refinements", "value": selection["refinement"], "reason": "source_missing"})
                    selected_affixes.update(r["affix_id"] for r in group["refinements"])
            else:
                if "set_thresholds" in selection:
                    present = {e["required_count"] for e in result["effects"]}
                    missing.extend({"path": "effects", "value": v, "reason": "source_missing"} for v in selection["set_thresholds"] if v not in present)
                    result["effects"] = [e for e in result["effects"] if e["required_count"] in selection["set_thresholds"]]
                selected_affixes.update(e["effect"]["affix_id"] for e in result["effects"] if e["effect"])
            result["annotations"] = [a for a in result["annotations"] if a["target_type"] != "equip_affix" or a["target_id"] in selected_affixes]
    retained = {"character": ["character", "annotations"], "skill": ["description", "annotations", "context_ref"],
                "weapon": ["weapon", "annotations"], "artifact_set": ["artifact_set", "annotations"]}[kind]
    result["selection"] = {"requested": selection, "retained_sections": retained,
                           "not_requested": [s for s in SECTIONS[kind] if s not in requested and s not in retained],
                           "missing": missing}
    return add_context_parameter_references(result, kind)


def validate_context_request(request):
    if not isinstance(request, dict) or request.keys() - {"version_line", *LIMITS}:
        raise ValueError("上下文请求只允许 version_line、characters、weapons、artifact_sets")
    line = request.get("version_line", "release")
    validate_line(line)
    normalized = {"version_line": line}
    for collection, limit in LIMITS.items():
        values = request.get(collection, [])
        if not isinstance(values, list) or len(values) > limit:
            raise ValueError(f"{collection} 必须为数组，至多 {limit} 项")
        kind = {"characters": "character", "weapons": "weapon", "artifact_sets": "artifact_set"}[collection]
        rows = []
        for item in values:
            if not isinstance(item, dict) or item.keys() - {"id", *SELECTORS[kind]} or "id" not in item:
                raise ValueError(f"非法 {collection} 对象字段")
            ident = _integer(item["id"], "id", MAX_PUBLIC_ID)
            if any(value is None for value in item.values()):
                raise ValueError("选择字段不得为 null；未请求请省略")
            rows.append({"id": ident, **validate_selection(kind, **{k: v for k, v in item.items() if k != "id"})})
        normalized[collection] = rows
    if not any(normalized[k] for k in LIMITS):
        raise ValueError("上下文请求至少包含一个显式实体")
    return normalized


def query_context(db_path, request):
    from genshin_db import queries
    from genshin_db.equipment_queries import equipment_read_session

    request = validate_context_request(request)
    items, line = [], request["version_line"]
    with equipment_read_session(db_path):
        meta = queries.get_meta(db_path)
        for collection, kind, query in (("characters", "character", queries.get_character_detail),
                                        ("weapons", "weapon", queries.get_weapon_detail),
                                        ("artifact_sets", "artifact_set", queries.get_artifact_set_detail)):
            for spec in request[collection]:
                item = {"entity_type": kind, "id": spec["id"], "request": spec}
                try:
                    data = query(db_path, spec["id"], version_line=line, **{k: v for k, v in spec.items() if k != "id"})
                    item.update(status="ok", data=data, content_fingerprint=fingerprint(data))
                except queries.NotFoundError as exc:
                    item.update(status="error", error={"code": 404, "reason": "not_found", "message": str(exc)})
                except EquipmentError as exc:
                    item.update(status="error", error={"code": exc.status, "reason": exc.reason, "message": str(exc), "details": exc.details})
                except (sqlite3.DatabaseError, json.JSONDecodeError) as exc:
                    item.update(status="error", error={"code": 500, "reason": "dataset_invalid", "message": str(exc)})
                items.append(item)
        snapshot = {**meta, "version_line": line}
        snapshot["fingerprint"] = fingerprint({"meta": meta, "request": request, "items": items})
    return {"version_line": line, "snapshot": snapshot, "items": items, "limits": dict(LIMITS)}


CONTEXT_CAPABILITIES = {
    "path": "/v1/context/query", "method": "POST", "auth": "none", "read_only": True,
    "desc": "有界实体资料上下文；同一只读快照、显式选择、逐项错误及内容指纹，不生成队伍轴",
    "limits": LIMITS, "sections": SECTIONS, "selectors": SELECTORS,
    "input": {"version_line": "release/beta；默认release", "characters": "[{id,sections?,levels?}]",
              "weapons": "[{id,sections?,refinement?}]", "artifact_sets": "[{id,sections?,set_thresholds?}]"},
    "validation": "未知字段、重复/空选择、越界和null整体400；实体/组件缺失逐项error；无任意SQL或raw导出",
    "output": ["version_line", "snapshot", "items", "limits"],
    "selection_policy": "省略选择保留旧完整返回；投影保留身份、正文来源和approved注释。levels=1..15只筛战斗技能和附属技能，固有天赋等级不裁剪；refinement=1..5；set_thresholds=1..5。",
    "cache_policy": "snapshot来自同一只读事务；manual_content_fingerprint覆盖全部人工内容和身份，感知新增/修改/删除/审核变化；每项content_fingerprint对应实际投影。not_requested与missing分开；不是游戏机制完整性证明。",
}
