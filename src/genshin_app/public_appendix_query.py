"""Read one explicit appendix scope without granting execution authority."""
from __future__ import annotations

import copy

from genshin_app.equipment.schema import PlanError, _check
from genshin_db import queries
from genshin_db.validation import TARGET_TYPES

_SCOPE = {
    "target_type": {"enum": list(TARGET_TYPES)},
    "target_id": {"type": "integer", "minimum": 1},
    "status": {"enum": ["approved", "pending"]},
    "version_line": {"enum": ["release", "beta"]},
}
APPENDIX_CATEGORIES_SCHEMA = {
    "type": "object", "properties": _SCOPE,
    "required": list(_SCOPE), "additionalProperties": False,
}
APPENDIX_BODY_SCHEMA = {
    "type": "object",
    "properties": {
        **_SCOPE,
        "category": {"type": "string", "minLength": 1, "maxLength": 128},
        "page": {"type": "integer", "minimum": 1, "maximum": 10000},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["target_type", "target_id", "status", "category", "version_line"],
    "additionalProperties": False,
}
_ITEM_FIELDS = (
    "id", "target_type", "target_id", "version_line", "category",
    "title", "content", "status",
)


def _owner_view(db_path, target, line):
    scope = dict(queries.annotation_target_scope(db_path, **target, version_line=line))
    publication = scope.pop("publication", [])
    return scope, {"owner_publication": publication} if publication else {}


def appendix_query_contract():
    return {
        "cli": "db appendix --input REQUEST.json",
        "request_schema": {"oneOf": [copy.deepcopy(APPENDIX_CATEGORIES_SCHEMA),
                                     copy.deepcopy(APPENDIX_BODY_SCHEMA)]},
        "selection": "省略category只列分类及计数，提供category读取正文。approved与pending分别查询。",
        "output_contracts": {"categories": "agent-appendix-category-index-v1",
                             "body": "agent-appendix-query-v1"},
        "execution_authority": "none",
    }


def read_appendix(db_path, request):
    """Validate and route a standalone request using the native tool schemas."""
    body = isinstance(request, dict) and "category" in request
    issues = []
    _check(request, APPENDIX_BODY_SCHEMA if body else APPENDIX_CATEGORIES_SCHEMA, "request", issues)
    if issues:
        raise PlanError("附录查询参数不合法", "appendix_query_schema_invalid", issues)
    query = query_appendix if body else query_appendix_categories
    with queries.equipment_read_session(db_path):
        return query(db_path, request)


def query_appendix(db_path, request):
    """Project one already validated exact body selector."""
    selection = {key: request[key] for key in (*_SCOPE, "category")}
    page = request.get("page", 1)
    page_size = request.get("page_size", 10)
    raw = queries.list_annotations(db_path, **selection, page=page, page_size=page_size)
    target = {key: selection[key] for key in ("target_type", "target_id")}
    status, line = selection["status"], selection["version_line"]
    owner_scope, publication = _owner_view(db_path, target, line)
    items = []
    for row in raw.get("items", []):
        if not isinstance(row, dict):
            raise PlanError("附录查询返回了非法条目", "agent_appendix_scope_invalid")
        if (any(row.get(key) != selection[key] for key in (*target, "status", "category"))
                or row.get("version_line") not in (None, line)):
            raise PlanError("附录查询返回范围与精确选择器不一致", "agent_appendix_scope_invalid")
        item = {key: copy.deepcopy(row[key]) for key in _ITEM_FIELDS if key in row}
        item.update(annotation_authority=status, execution_authority="none")
        items.append(item)
    return {
        "contract_id": "agent-appendix-query-v1",
        "schema_version": 1,
        "target": target,
        "owner_scope": owner_scope,
        **publication,
        "selection": {key: selection[key] for key in ("status", "category", "version_line")},
        "annotation_authority": status,
        "execution_authority": "none",
        "total": raw.get("total", 0),
        "page": raw.get("page", page),
        "page_size": raw.get("page_size", page_size),
        "items": items,
        "reading": {"delivered_items": len(items), "understood": "not_asserted",
                    "next_request": ({**selection, "page": page + 1, "page_size": page_size}
                        if page * page_size < raw.get("total", 0) else None)},
    }


def query_appendix_categories(db_path, request):
    """Project categories for one already validated target and status."""
    target = {key: request[key] for key in ("target_type", "target_id")}
    status, line = request["status"], request["version_line"]
    raw = queries.list_annotation_categories(db_path, **target, status=status, version_line=line)
    _, publication = _owner_view(db_path, target, line)
    categories = []
    for row in raw.get("categories", []):
        if (not isinstance(row, dict) or not isinstance(row.get("category"), str)
                or type(row.get("count")) is not int or row["count"] < 0):
            raise PlanError("附录分类查询返回了非法条目", "agent_appendix_scope_invalid")
        categories.append({"category": row["category"], "count": row["count"],
                           "annotation_authority": status, "execution_authority": "none"})
    return {
        "contract_id": "agent-appendix-category-index-v1",
        "schema_version": 1,
        "target": target,
        **publication,
        "status": status,
        "version_line": line,
        "annotation_authority": status,
        "execution_authority": "none",
        "total": sum(row["count"] for row in categories),
        "categories": categories,
    }
