"""Finite heterogeneous reads sharing one validated runtime snapshot."""
from __future__ import annotations

import copy
import json
import re

from genshin_db.runtime_catalog import RuntimeConfigError, dumps, fingerprint
from genshin_db.runtime_request_contracts import RuntimeRequestError
from genshin_db.validation import version_line as validate_line

BATCH_ACTIONS = frozenset({"definition", "fragment", "action_capacity_profile", "action_activation_catalog", "action_capacity_source_profile","action_facts", "action_parameter", "attenuation_profile",
                           "talent_level_profile", "action_parameter_at_level", "action_level_context",
                           "mechanism_catalog", "mechanism_profile",
                           "reaction_damage_profile", "action_resource_profile", "action_dispatch_profile", "attack_metadata_profile", "passive_source_profile", "gadget_source_profile", "periodic_attack_profile", "energy_generation_profile", "limited_hit_benefit_profile", "assemble"})
BATCH_LIMITS = {"max_items": 8, "request_bytes": 65536, "min_bytes": 65536,
                "max_bytes": 4 * 1024 * 1024, "delivery_bytes": 8 * 1024 * 1024,
                "error_details_bytes": 4096, "error_message_chars": 512}
ITEM_ERRORS = frozenset({"not_found", "ambiguous_reference", "skill_depot_selection_required",
                         "empty_skill_slot", "unsupported_source", "response_too_large",
                         "bundle_too_large", "fragment_too_large", "gadget_index_upgrade_required", "material_index_upgrade_required"})


def batch_capabilities():
    return {"batch_version": 1, "actions": sorted(BATCH_ACTIONS), "limits": dict(BATCH_LIMITS),
            "items": "1..8个{id,request}；id唯一；子请求禁止batch/version_line/snapshot_id",
            "snapshot": "只读取顶层单一版本线与预期快照；每项完整data保留原语义状态",
            "status": "complete仅表示全部查询成功；partial含error/skipped，均不代表游戏机制ready",
            "budget": "max_bytes计完整payload规范紧凑UTF8；HTTP/CLI实际交付另限8MiB；超预算不截断data",
            "failure": "外层结构错误整体400；单项选择/参数错误继续；数据库损坏或快照错误整体失败",
            "execution_support": "not_evaluated"}


def validate_batch_request(request):
    from genshin_db.runtime_queries import ACTION_FIELDS

    if not isinstance(request, dict) or request.keys() - {"action", "version_line", "snapshot_id", "max_bytes", "items"}:
        raise RuntimeRequestError("batch请求包含未知字段或不是对象", "", "batch")
    if request.get("action") != "batch" or any(value is None for value in request.values()):
        raise RuntimeRequestError("batch请求action无效或包含null", "/action", "batch")
    validate_line(request.get("version_line", "release"))
    snapshot = request.get("snapshot_id")
    if snapshot is not None and (not isinstance(snapshot, str) or not snapshot or len(snapshot) > 128):
        raise RuntimeRequestError("snapshot_id必须为1..128字符字符串", "/snapshot_id", "batch")
    limit = request.get("max_bytes", BATCH_LIMITS["max_bytes"])
    if type(limit) is not int or not BATCH_LIMITS["min_bytes"] <= limit <= BATCH_LIMITS["max_bytes"]:
        raise RuntimeRequestError("batch max_bytes必须为65536..4194304的整数", "/max_bytes", "batch",
                                  actual=limit, minimum=BATCH_LIMITS["min_bytes"], maximum=BATCH_LIMITS["max_bytes"])
    items = request.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= BATCH_LIMITS["max_items"]:
        raise RuntimeRequestError("batch items必须为1..8项列表", "/items", "batch")
    seen = set()
    for index, item in enumerate(items):
        path = "/items/" + str(index)
        if not isinstance(item, dict) or set(item) != {"id", "request"}:
            raise RuntimeRequestError("batch每项只允许id/request", path, "batch")
        ident, query = item["id"], item["request"]
        if not isinstance(ident, str) or re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", ident) is None or ident in seen:
            raise RuntimeRequestError("batch id必须唯一且为1..64个ASCII字母数字或._-字符", path + "/id", "batch")
        seen.add(ident)
        if (not isinstance(query, dict) or not isinstance(query.get("action"), str)
                or query["action"] not in BATCH_ACTIONS):
            raise RuntimeRequestError(
                "此查询不能放入批次。请选择受支持的子查询，或单独提交当前查询。",
                path + "/request/action", "batch",
                allowed_actions=sorted(BATCH_ACTIONS),
            )
        if query.keys() - (ACTION_FIELDS[query["action"]] | {"action"}) or any(v is None for v in query.values()):
            raise RuntimeRequestError("batch子请求含未知字段、版本覆盖或null", path + "/request", "batch")
    try:
        size = len(dumps(request).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise RuntimeRequestError("batch请求必须为有限JSON值", "", "batch") from exc
    if size > BATCH_LIMITS["request_bytes"]:
        raise RuntimeConfigError("batch请求超过64KiB", "request_too_large", 413)
    return copy.deepcopy(request)


def _error(exc):
    from genshin_db.runtime_request_contracts import RuntimeRequestError

    # Batch's item error family is stable; retain the precise new request
    # pointer and repair contract in details rather than changing that family.
    reason = "invalid_parameter" if isinstance(exc, RuntimeRequestError) else getattr(exc, "reason", "invalid_parameter")
    error = {"code": getattr(exc, "status", 400), "reason": str(reason)[:128],
             "message": str(exc)[:BATCH_LIMITS["error_message_chars"]]}
    details = getattr(exc, "details", None)
    if details is not None:
        details_bytes = len(dumps(details).encode("utf-8"))
        if details_bytes <= BATCH_LIMITS["error_details_bytes"]:
            error["details"] = copy.deepcopy(details)
        else:
            error.update(details_omitted=True, details_fingerprint=fingerprint(details), details_bytes=details_bytes)
    return error


def _seal(snapshot, items, limit):
    counts = {"requested": len(items), **{status: sum(item["status"] == status for item in items)
                                         for status in ("ok", "error", "skipped")}}
    result = {"batch_version": 1, "status": "complete" if counts["ok"] == len(items) else "partial",
              "snapshot": snapshot, "items": items, "counts": counts,
              "limits": {**BATCH_LIMITS, "max_bytes": limit}}
    result["content_fingerprint"] = fingerprint(result)
    return result


def _skip(item):
    return {**item, "status": "skipped", "error": {"code": 413, "reason": "batch_budget_exhausted",
                                                    "message": "批次总预算已用尽，本项未执行"}}


def query_runtime_batch(db_path, request, *, public=False):
    from genshin_db.runtime_queries import _read, query_runtime, query_runtime_public

    request = validate_batch_request(request)
    query = query_runtime_public if public else query_runtime
    line, limit = request.get("version_line", "release"), request.get("max_bytes", BATCH_LIMITS["max_bytes"])
    # Reserve all item envelopes, including worst-case bounded errors, before item reads.
    # JSON escaping of control characters takes six bytes, exceeding UTF-8's four.
    worst = {"code": 500, "reason": "\0" * 128, "message": "\0" * BATCH_LIMITS["error_message_chars"],
             "details": "x" * BATCH_LIMITS["error_details_bytes"], "details_omitted": True,
             "details_fingerprint": "f" * 64, "details_bytes": 2**64-1}
    work = [{**item, "status": "error", "error": copy.deepcopy(worst)} for item in request["items"]]
    with _read(db_path, line, request.get("snapshot_id")) as (_, snapshot):
        if len(dumps(_seal(snapshot, work, limit)).encode("utf-8")) > limit:
            raise RuntimeConfigError("batch预算不足以保留全部项的错误回执", "response_too_large", 413)
        for position, item in enumerate(request["items"]):
            try:
                data = query(db_path, {**item["request"], "version_line": line,
                                     "snapshot_id": snapshot["snapshot_id"]})
                identity = ("version_line", "snapshot_id", "upstream_commit", "manifest_fingerprint", "indexer_version")
                if not isinstance(data, dict) or any(data.get("snapshot", {}).get(k) != snapshot.get(k) for k in identity):
                    raise RuntimeConfigError("batch子查询返回不同快照", "mixed_snapshot", 500, {"id": item["id"]})
                value = {**item, "status": "ok", "data": data}
            except RuntimeConfigError as exc:
                if exc.status >= 500 or exc.reason not in ITEM_ERRORS:
                    raise
                value = {**item, "status": "error", "error": _error(exc)}
            except json.JSONDecodeError:
                raise  # Source parse corruption is never a recoverable input error.
            except ValueError as exc:
                value = {**item, "status": "error", "error": _error(exc)}
            work[position] = value
            if len(dumps(_seal(snapshot, work, limit)).encode("utf-8")) > limit:
                work[position] = {**item, "status": "error", "error": {
                    "code": 413, "reason": "response_budget_exceeded", "message": "完整单项响应无法放入批次预算；未截断原文"}}
                work[position + 1:] = [_skip(tail) for tail in request["items"][position + 1:]]
                break
        result = _seal(snapshot, work, limit)
        if len(dumps(result).encode("utf-8")) > limit:
            raise RuntimeConfigError("batch完整回执仍超过总预算", "response_too_large", 413)
        return result
