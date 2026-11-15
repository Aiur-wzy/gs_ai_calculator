"""External declarations in NativeResearchLedger; no second storage owner.

These records describe what a CLI operator says they consulted. They are not
DSH calls and can never be settled as provider-verified Web executions.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

from genshin_app.equipment.files import fingerprint
from genshin_db.locking import file_lock

from .native_research_ledger import (
    MAX_ENTRIES,
    MAX_QUERY_CHARS,
    _counter,
    _fail,
    _hash,
    _identity,
    _resource_ids,
    _safe_external_text,
    _sources,
    _timestamp,
    _validate_usage,
)

AUTHORITY = "external_declared"
REPORT_CONTRACT = "external-research-usage-report-v1"
BINDING_KEYS = {"task_id", "candidate_id", "candidate_revision", "candidate_fingerprint"}
APPEND_FIELDS = {"contract", "request_id", "expected_ledger_revision", "candidate_revision",
                 "query", "sources", "source_observed_at", "content_hash", "resource_ids"}
APPEND_REQUIRED = {"contract", "request_id", "expected_ledger_revision", "candidate_revision"}


def binding(value):
    if not isinstance(value, Mapping) or set(value) != BINDING_KEYS:
        _fail("外部研究必须绑定当前任务和候选", "research_binding_invalid")
    result = dict(value)
    _identity(result["task_id"], "task_id")
    _identity(result["candidate_id"], "candidate_id")
    _counter(result["candidate_revision"], "candidate_revision")
    _hash(result["candidate_fingerprint"], "candidate_fingerprint")
    return result


def turn_id(context):
    # A ledger-local audit group, explicitly outside the DSH turn namespace.
    return "external." + fingerprint({k: context[k] for k in ("task_id", "candidate_id")})[:48]


def check_context(ledger, context, *, advance=False):
    context = binding(context)
    previous = ledger.get("external_context")
    if previous is None:
        if ledger["entries"] or ledger["reports"]:
            _fail("原生DSH研究账不能改绑为外部任务", "research_authority_mismatch")
        return context
    if any(previous[k] != context[k] for k in ("task_id", "candidate_id")):
        _fail("研究账属于另一个任务或候选", "research_binding_conflict")
    if context == previous:
        return context
    if (advance and context["candidate_revision"] > previous["candidate_revision"]):
        return context
    _fail("候选版本已变化，请先明确绑定当前版本", "stale_research_binding",
          {"current_binding": previous, "requested_binding": context})


def validate_entry(value):
    keys = {"research_id", "call_id", "turn_id", "authority", "task_binding",
            "request_id", "request_fingerprint", "query", "sources", "registered_at",
            "source_observed_at", "content_hash", "resource_ids", "status", "usage"}
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail("外部研究条目字段不完整", "research_external_input")
    row = copy.deepcopy(dict(value))
    if row["authority"] != AUTHORITY or row["status"] != AUTHORITY:
        _fail("外部研究不能声称已由DSH联网验证", "research_authority_mismatch")
    from .native_research_ledger import _RESEARCH_ID
    if not isinstance(row["research_id"], str) or not _RESEARCH_ID.fullmatch(row["research_id"]):
        _fail("research_id不合法")
    row["task_binding"] = binding(row["task_binding"])
    _identity(row["request_id"], "request_id")
    _safe_external_text(row["request_id"], "request_id", 128)
    if row["call_id"] != "external." + fingerprint([row["task_binding"]["task_id"], row["request_id"]]):
        _fail("外部请求身份不一致", "research_binding_invalid")
    if row["turn_id"] != turn_id(row["task_binding"]):
        _fail("外部研究分组不匹配", "research_binding_invalid")
    if row["query"] is not None:
        row["query"] = _safe_external_text(row["query"], "query", MAX_QUERY_CHARS)
    row["sources"] = _sources(row["sources"])
    if row["query"] is None and not row["sources"]:
        _fail("外部研究至少需要查询内容或公开来源", "research_external_input")
    _timestamp(row["registered_at"], "registered_at")
    if row["source_observed_at"] is not None:
        _timestamp(row["source_observed_at"], "source_observed_at")
    if row["content_hash"] is not None:
        _hash(row["content_hash"], "content_hash")
    row["resource_ids"] = _resource_ids(row["resource_ids"])
    _hash(row["request_fingerprint"], "request_fingerprint")
    canonical = {key: row[key] for key in ("task_binding", "request_id", "query", "sources",
                                         "source_observed_at", "content_hash", "resource_ids")}
    if fingerprint(canonical) != row["request_fingerprint"]:
        _fail("外部研究请求指纹不一致", "research_external_input")
    if row["usage"] is not None:
        row["usage"] = _validate_usage(row["usage"])
    return row


def validate_context_rows(ledger):
    context = binding(ledger["external_context"])
    for row in ledger["entries"]:
        previous = row.get("task_binding")
        if (row.get("authority") != AUTHORITY or not isinstance(previous, dict)
                or any(previous[k] != context[k] for k in ("task_id", "candidate_id"))
                or previous["candidate_revision"] > context["candidate_revision"]
                or previous["candidate_revision"] == context["candidate_revision"] and previous != context):
            _fail("外部条目不属于当前任务候选历史", "research_binding_invalid")
    return context


def bind(store, context, expected_revision):
    _counter(expected_revision, "expected_revision")
    with file_lock(store.lock_path):
        ledger = store._read_unlocked()
        checked = check_context(ledger, context, advance=True)
        if ledger["revision"] != expected_revision:
            _fail("研究账revision已变化", "stale_context")
        previous = ledger.get("external_context")
        if previous != checked:
            ledger["external_context"] = checked
            store._write_unlocked(ledger)
        return {"status": "bound", "authority": AUTHORITY, "binding": checked,
                "previous_binding": previous, "research_ledger_revision": ledger["revision"]}


def append(store, context, request):
    if (not isinstance(request, Mapping) or set(request) - APPEND_FIELDS or not APPEND_REQUIRED <= set(request)
            or request.get("contract") != "external-research-request-v1"):
        _fail("外部研究请求字段不合法", "research_external_input")
    context = binding(context)
    if request["candidate_revision"] != context["candidate_revision"]:
        _fail("请求不属于当前候选版本", "stale_research_binding")
    _counter(request["candidate_revision"], "candidate_revision")
    _counter(request["expected_ledger_revision"], "expected_ledger_revision")
    canonical = {"task_binding": context, "request_id": request["request_id"],
                 "query": request.get("query"), "sources": _sources(request.get("sources", [])),
                 "source_observed_at": request.get("source_observed_at"),
                 "content_hash": request.get("content_hash"), "resource_ids": request.get("resource_ids", [])}
    row = validate_entry({"research_id": store._id_factory(),
        "call_id": "external." + fingerprint([context["task_id"], request["request_id"]]),
        "turn_id": turn_id(context), "authority": AUTHORITY, "task_binding": context,
        **{k: canonical[k] for k in canonical if k != "task_binding"},
        "request_fingerprint": fingerprint(canonical), "registered_at": store._clock(),
        "status": AUTHORITY, "usage": None})
    with file_lock(store.lock_path):
        ledger = store._read_unlocked()
        check_context(ledger, context)
        previous = next((r for r in ledger["entries"] if r["request_id"] == row["request_id"]), None)
        if previous is not None:
            if previous["request_fingerprint"] != row["request_fingerprint"]:
                _fail("相同外部请求ID的内容或候选绑定不同", "call_id_conflict")
            return {"status": "already_recorded", "authority": AUTHORITY,
                    "research_id": previous["research_id"], "research_ledger_revision": ledger["revision"],
                    "binding": previous["task_binding"]}
        if ledger["revision"] != request["expected_ledger_revision"]:
            _fail("研究账revision已变化", "stale_context")
        if len(ledger["entries"]) >= MAX_ENTRIES:
            _fail("研究账达到现有容量上限", "agent_size_limit")
        ledger["external_context"] = context
        ledger["entries"].append(row)
        ledger["revision"] += 1
        store._write_unlocked(ledger)
        return {"status": "recorded", "authority": AUTHORITY, "research_id": row["research_id"],
                "research_ledger_revision": ledger["revision"], "binding": context}


def listing(store, context, offset=0, limit=50):
    context = binding(context)
    _counter(offset, "offset")
    if type(limit) is not int or not 1 <= limit <= 100:
        _fail("limit不合法", "agent_tool_input")
    ledger = store.read()
    recorded = ledger.get("external_context")
    if recorded is not None and any(context[k] != recorded[k] for k in ("task_id", "candidate_id")):
        _fail("研究账属于其他候选", "research_binding_conflict")
    if recorded is None and (ledger["entries"] or ledger["reports"]):
        _fail("原生研究账不可作为外部任务读取", "research_authority_mismatch")
    rows = [{**copy.deepcopy(row),
             "binding_state": "current" if row["task_binding"] == context else (
                 "later_revision" if row["task_binding"]["candidate_revision"] > context["candidate_revision"] else "previous_revision"),
             "usage_binding": ledger["reports"][-1].get("external_binding") if row["usage"] is not None and ledger["reports"] else None}
            for row in ledger["entries"][offset:offset + limit]]
    reports = [r for r in ledger["reports"] if r.get("external_binding") == context]
    return {"contract": "external-research-list-v1", "authority": AUTHORITY,
            "binding": context, "recorded_binding": recorded,
            "requires_candidate_rebind": recorded is not None and context != recorded,
            "research_ledger_revision": ledger["revision"], "items": rows,
            "current_binding_reports": copy.deepcopy(reports[-1:]),
            "total": len(ledger["entries"]), "offset": offset,
            "next_offset": offset + len(rows) if offset + len(rows) < len(ledger["entries"]) else None,
            "network_execution_verified": False}
