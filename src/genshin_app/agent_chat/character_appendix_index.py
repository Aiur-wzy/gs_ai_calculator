"""Thin Agent-facing adapter for the read-only character appendix navigator."""
from __future__ import annotations

import copy

from genshin_app.equipment.files import fingerprint
from genshin_db import queries
from genshin_db.appendix_navigation import CONTRACT, character_appendix_index
from genshin_db.validation import positive_int
from genshin_db.validation import version_line as validate_line

TOOL_DESCRIPTION = (
    "读取当前会话固定数据库中一个角色的附录导航索引，含已存技能/附属技能/天赋/命座的"
    "approved与pending分类及数量；不含正文、不执行知识。完整索引保存为会话resource，"
    "用gs_read_context分页；正文仍用gs_query_appendix。complete只指索引枚举，不表示已读正文。"
)
TOOL_SCHEMA = {"type": "object", "properties": {
    # Tool schemas themselves cross the JavaScript-safe JSON boundary.
    "db_character_id": {"type": "integer", "minimum": 1, "maximum": 2**53 - 1}},
    "required": ["db_character_id"], "additionalProperties": False}


def appendix_navigation_capabilities():
    return {"contract_id": CONTRACT, "schema_version": 1,
            "tool": "gs_get_character_appendix_index", "input_schema": copy.deepcopy(TOOL_SCHEMA),
            "version_binding": "session_database_snapshot_and_version_line",
            "execution_authority": "none", "body_included": False,
            "result_statuses": ["complete", "partial", "not_found"],
            "default_page": "/nonempty_targets", "complete_targets": "/targets",
            "issues": "/issues", "body_tool": "gs_query_appendix",
            "resource_read_tool": "gs_read_context",
            "fallback_tools": ["gs_query_context", "gs_list_appendix_categories", "gs_query_appendix"],
            "completeness": "索引枚举完成不等于正文已读、知识正确、机制完整或可执行。"}


def query_character_appendix_index(db_path, request):
    character_id = positive_int(request["db_character_id"], "db_character_id")
    validate_line(request["version_line"])
    connection = queries._connect(db_path)
    try:
        # Borrowed connections already belong to the session's read snapshot.
        # Standalone callers also receive one coherent SQLite read transaction.
        if not connection.in_transaction:
            connection.execute("BEGIN")
        result = character_appendix_index(connection, character_id, request["version_line"])
        result["database_fingerprint"] = request["database_fingerprint"]
        return result
    finally:
        connection.close()


def get_character_appendix_index(session, args, *, query, page, version_line):
    request = {"db_character_id": args["db_character_id"],
               "version_line": version_line(session),
               "database_fingerprint": fingerprint(session.database_snapshot)}
    saved = query(session, {"request": request}, query_character_appendix_index, "appendix_index")
    resource = saved["resource"]
    value = session.resources[resource]
    response = page(value, resource, "/nonempty_targets")
    response.update({key: value[key] for key in (
        "contract_id", "schema_version", "db_character_id", "version_line",
        "database_fingerprint", "character_name", "status", "coverage",
        "execution_authority", "body_read")})
    response["targets_ref"] = {"resource": resource, "pointer": "/targets"}
    response["issues_ref"] = {"resource": resource, "pointer": "/issues"}
    response["issue_count"] = len(value["issues"])
    if "source_ref" in saved:
        response["source_ref"] = saved["source_ref"]
    assert response["contract_id"] == CONTRACT
    return response
