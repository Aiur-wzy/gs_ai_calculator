"""Paged Renderer views over the Host-owned durable display timeline.

The workbench ledger is the sole authority for new conversations.  A bounded
visible-message adapter remains for legacy folders and always reports partial.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from genshin_app.equipment.schema import PlanError

from .native_session_store import redact_visible_text

OUTLINE_CONTRACT = "agent-turn-outline-v1"
SEARCH_CONTRACT = "agent-search-results-v1"
HISTORY_WINDOW_CONTRACT = "agent-history-window-v1"
MAX_QUERY_CHARS = 256
MAX_SEARCH_RESULTS = 200
MAX_HISTORY_NODES = 500
MAX_OUTLINE_ITEMS = 500
MAX_TIMELINE_NODES = 20_000
MAX_EXCERPT_CHARS = 512
MAX_WINDOW_TEXT_CHARS = 32_000
MAX_PARTIAL_REASONS = 32
JSON_SAFE_INTEGER_MAX = 2**53 - 1
_KINDS = {"message", "tool", "activity", "research", "question", "candidate", "app_action", "checkpoint", "notice"}
_ROLES = {"user", "assistant", "notice", "system"}


def _fail(message: str, reason: str) -> None:
    raise PlanError(message, reason)


def _counter(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"{field}不合法", "agent_workbench_projection_invalid")
    return value


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        _fail(f"{field}不合法", "agent_workbench_projection_invalid")
    return value


def _optional_identity(value: object, field: str) -> str | None:
    return None if value is None else _identity(value, field)


def _page_limit(value: object, *, maximum: int, reason: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        _fail("分页上限不合法", reason)
    return value


def normalized_query(value: object) -> str:
    if not isinstance(value, str):
        _fail("搜索文本不合法", "agent_search_query_invalid")
    result = value[:MAX_QUERY_CHARS].strip()
    if not result:
        _fail("搜索文本不能为空", "agent_search_query_invalid")
    return result


def query_digest(value: object) -> str:
    return hashlib.sha256(normalized_query(value).encode("utf-8")).hexdigest()


def _match_text(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _foundation(value: object, *, conversation_id: str) -> dict[str, Any]:
    if value is None:
        return {"conversation_id": conversation_id, "source_cursor": 0, "projection_revision": 0, "turn_outline": [], "completeness": {"state": "partial", "partial_fields": ["timeline"], "reason": "workbench_ledger_not_created"}}
    if not isinstance(value, Mapping) or value.get("conversation_id") != conversation_id:
        _fail("工作台账本投影不合法", "agent_workbench_projection_invalid")
    result = dict(value)
    result["source_cursor"] = _counter(result.get("source_cursor"), "source_cursor")
    result["projection_revision"] = _counter(result.get("projection_revision"), "projection_revision")
    if not isinstance(result.get("turn_outline"), list) or len(result["turn_outline"]) > 2048:
        _fail("工作台Turn大纲不合法", "agent_workbench_projection_invalid")
    return result


def _turn_rows(foundation: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_start = -1
    for raw in foundation["turn_outline"]:
        if not isinstance(raw, Mapping):
            _fail("工作台Turn大纲不合法", "agent_workbench_projection_invalid")
        turn_id = _identity(raw.get("turn_id"), "turn_id")
        start = _counter(raw.get("seq_start"), "seq_start")
        end = _counter(raw.get("seq_end"), "seq_end")
        state = _identity(raw.get("state"), "turn_state")
        markers = raw.get("markers")
        if turn_id in seen or end < start or start <= previous_start or not isinstance(markers, list) or len(markers) > 32 or any(not isinstance(item, str) or len(item) > 64 for item in markers):
            _fail("工作台Turn大纲不合法", "agent_workbench_projection_invalid")
        excerpts = raw.get("excerpts", [])
        if not isinstance(excerpts, list) or len(excerpts) > 3:
            _fail("工作台Turn大纲不合法", "agent_workbench_projection_invalid")
        checked_excerpts = []
        for item in excerpts:
            if not isinstance(item, Mapping):
                _fail("工作台Turn大纲不合法", "agent_workbench_projection_invalid")
            excerpt_text = item.get("text")
            if not isinstance(excerpt_text, str):
                _fail("工作台Turn大纲不合法", "agent_workbench_projection_invalid")
            checked_excerpts.append({"node_id": _identity(item.get("node_id"), "node_id"), "kind": _identity(item.get("kind"), "kind"), "text": redact_visible_text(excerpt_text)[:MAX_EXCERPT_CHARS]})
        rows.append({"turn_id": turn_id, "state": state, "start_seq": start, "end_seq": end, "markers": list(markers), "excerpts": checked_excerpts, "node_count": _counter(raw.get("node_count", 0), "node_count")})
        seen.add(turn_id)
        previous_start = start
    return rows


def _timeline(
    value: object, *, turn_ranges: Mapping[str, tuple[int, int]]
) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) > MAX_TIMELINE_NODES:
        _fail("耐久时间线不合法", "agent_workbench_projection_invalid")
    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    previous = 0
    for raw in value:
        if not isinstance(raw, Mapping):
            _fail("耐久时间线不合法", "agent_workbench_projection_invalid")
        node_id = _identity(raw.get("node_id"), "node_id")
        turn_id = _identity(raw.get("turn_id"), "turn_id")
        source_seq = _counter(raw.get("source_seq"), "source_seq")
        kind, role = raw.get("kind"), raw.get("role")
        bounds = turn_ranges.get(turn_id)
        if node_id in ids or bounds is None or not bounds[0] <= source_seq <= bounds[1] or source_seq <= previous or kind not in _KINDS or role is not None and role not in _ROLES:
            _fail("耐久时间线不合法", "agent_workbench_projection_invalid")
        text, label = raw.get("text"), raw.get("label")
        if text is not None and not isinstance(text, str) or not isinstance(label, str):
            _fail("耐久时间线不合法", "agent_workbench_projection_invalid")
        result.append({"stable_node_id": f"{kind}:{node_id}", "turn_id": turn_id, "segment_id": _optional_identity(raw.get("segment_id"), "segment_id"), "operation_id": _optional_identity(raw.get("operation_id"), "operation_id"), "source_seq": source_seq, "kind": kind, "category": _identity(raw.get("category"), "category"), "role": role, "label": redact_visible_text(label)[:1000], "text": None if text is None else redact_visible_text(text)[:MAX_WINDOW_TEXT_CHARS], "state": _optional_identity(raw.get("state"), "state"), "result_code": _optional_identity(raw.get("result_code"), "result_code"), "resource_ref": _optional_identity(raw.get("resource_ref"), "resource_ref"), "workflow_stage": _optional_identity(raw.get("workflow_stage"), "workflow_stage"), "occurred_at": raw.get("occurred_at") if isinstance(raw.get("occurred_at"), str) else None})
        ids.add(node_id)
        previous = source_seq
    return result


def _legacy_nodes(record: Mapping[str, Any], turns: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    rows = record.get("visible_messages")
    evicted = record.get("visible_message_evicted_count")
    if not isinstance(rows, list) or type(evicted) is not int or evicted < 0:
        _fail("原生会话消息投影不合法", "agent_workbench_projection_invalid")
    groups: dict[int, list[dict[str, Any]]] = {}
    seen_messages: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping) or raw.get("role") not in {"user", "assistant", "notice"}:
            _fail("原生会话消息投影不合法", "agent_workbench_projection_invalid")
        group = _counter(raw.get("user_turn"), "user_turn")
        message_id = _identity(raw.get("message_id"), "message_id")
        message_text = raw.get("text")
        if message_id in seen_messages or not isinstance(message_text, str):
            _fail("原生会话消息投影不合法", "agent_workbench_projection_invalid")
        seen_messages.add(message_id)
        groups.setdefault(group, []).append({"message_id": message_id, "role": raw["role"], "text": redact_visible_text(message_text)[:MAX_WINDOW_TEXT_CHARS], "workflow_stage": _optional_identity(raw.get("workflow_stage"), "workflow_stage")})
    ordered = sorted(groups)
    reasons = ["legacy_visible_message_fallback"]
    if evicted:
        reasons.append("visible_messages_evicted")
    last = record.get("last_turn")
    exact = evicted == 0 and len(ordered) == len(turns) and (not turns and not rows or bool(turns) and (not isinstance(last, Mapping) or last.get("turn_id") in {None, turns[-1]["turn_id"]}))
    if not exact:
        if rows:
            reasons.append("turn_message_binding_unproven" if turns else "turn_message_binding_missing")
        return [], reasons
    if any(
        len(groups[group_id]) > turn["end_seq"] - turn["start_seq"] + 1
        for turn, group_id in zip(turns, ordered, strict=True)
    ):
        reasons.append("legacy_message_sequence_unproven")
        return [], reasons
    previous_synthetic_end = -1
    for turn, group_id in zip(turns, ordered, strict=True):
        synthetic_end = turn["start_seq"] + len(groups[group_id]) - 1
        if turn["start_seq"] <= previous_synthetic_end:
            reasons.append("legacy_message_sequence_unproven")
            return [], reasons
        previous_synthetic_end = synthetic_end
    result = []
    for turn, group_id in zip(turns, ordered, strict=True):
        for index, item in enumerate(groups[group_id]):
            result.append({"stable_node_id": f"message:{item['message_id']}", "turn_id": turn["turn_id"], "segment_id": None, "operation_id": None, "source_seq": turn["start_seq"] + index, "kind": "message", "category": "provider", "role": item["role"], "label": "消息", "text": item["text"], "state": None, "result_code": None, "resource_ref": None, "workflow_stage": item["workflow_stage"], "occurred_at": None})
    return result, reasons


def _source(
    record: Mapping[str, Any],
    base: Mapping[str, Any],
    timeline: object,
    *,
    conversation_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    if record.get("conversation_id") != conversation_id:
        _fail("原生会话身份不匹配", "agent_workbench_projection_invalid")
    turns = _turn_rows(base)
    checked = _timeline(
        timeline,
        turn_ranges={
            row["turn_id"]: (row["start_seq"], row["end_seq"])
            for row in turns
        },
    )
    if checked is not None:
        counts: dict[str, int] = {}
        for node in checked:
            counts[node["turn_id"]] = counts.get(node["turn_id"], 0) + 1
        if any(counts.get(row["turn_id"], 0) != row["node_count"] for row in turns):
            _fail("耐久时间线节点数量不匹配", "agent_workbench_projection_invalid")
        return turns, checked, []
    legacy, reasons = _legacy_nodes(record, turns)
    return turns, legacy, reasons


def _partial_reasons(base: Mapping[str, Any], extra: list[str]) -> list[str]:
    reasons = list(extra)
    completeness = base.get("completeness")
    if isinstance(completeness, Mapping) and completeness.get("state") != "complete":
        if isinstance(completeness.get("reason"), str):
            reasons.append(completeness["reason"])
        if isinstance(completeness.get("partial_fields"), list):
            reasons.extend(f"partial:{field}" for field in completeness["partial_fields"] if isinstance(field, str))
    return list(dict.fromkeys(reason[:128] for reason in reasons if reason))[:MAX_PARTIAL_REASONS]


def build_turn_outline(*, conversation_id: str, record: Mapping[str, Any], foundation: object, timeline: object = None, before_seq: object = None, after_seq: object = None, limit: object = 200) -> dict[str, Any]:
    conversation_id = _identity(conversation_id, "conversation_id")
    page_limit = _page_limit(limit, maximum=MAX_OUTLINE_ITEMS, reason="agent_outline_limit_invalid")
    if before_seq is not None and after_seq is not None:
        _fail("Turn分页游标冲突", "agent_outline_cursor_invalid")
    before = None if before_seq is None else _counter(before_seq, "before_seq")
    after = None if after_seq is None else _counter(after_seq, "after_seq")
    base = _foundation(foundation, conversation_id=conversation_id)
    turns, nodes, reasons = _source(
        record, base, timeline, conversation_id=conversation_id
    )
    eligible = [row for row in turns if (before is None or row["start_seq"] < before) and (after is None or row["start_seq"] > after)]
    eligible = (
        eligible[-page_limit:]
        if before is not None or (before is None and after is None)
        else eligible[:page_limit]
    )
    node_by_turn: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        node_by_turn.setdefault(node["turn_id"], []).append(node)
    items = []
    for turn in eligible:
        visible = node_by_turn.get(turn["turn_id"], [])
        excerpt = next(((row.get("text") or row.get("label") or "").strip()[:MAX_EXCERPT_CHARS] for row in visible if (row.get("text") or row.get("label") or "").strip()), "")
        if not excerpt and turn["excerpts"]:
            excerpt = turn["excerpts"][0]["text"]
        items.append({"turn_id": turn["turn_id"], "start_seq": turn["start_seq"], "end_seq": turn["end_seq"], "status": turn["state"], "excerpt": excerpt, "markers": turn["markers"], "unread": False})
    reasons = _partial_reasons(base, reasons)
    return {"contract": OUTLINE_CONTRACT, "session_id": conversation_id, "projection_revision": base["projection_revision"], "source_cursor": base["source_cursor"], "completeness": "partial" if reasons else "complete", "partial_reasons": reasons, "items": items, "total_turns": len(turns), "has_older": bool(items and turns and items[0]["start_seq"] > turns[0]["start_seq"]), "has_newer": bool(items and turns and items[-1]["start_seq"] < turns[-1]["start_seq"]), "older_cursor": items[0]["start_seq"] if items else None, "newer_cursor": items[-1]["start_seq"] if items else None}


def build_search_results(*, conversation_id: str, record: Mapping[str, Any], foundation: object, timeline: object = None, query: object, expected_digest: object, limit: object = 50, after_seq: object = None) -> dict[str, Any]:
    conversation_id = _identity(conversation_id, "conversation_id")
    normalized = normalized_query(query)
    page_limit = _page_limit(limit, maximum=MAX_SEARCH_RESULTS, reason="agent_search_limit_invalid")
    digest = query_digest(normalized)
    if not isinstance(expected_digest, str) or expected_digest != digest:
        _fail("搜索摘要与查询不匹配", "agent_search_digest_mismatch")
    cursor = None if after_seq is None else _counter(after_seq, "after_seq")
    base = _foundation(foundation, conversation_id=conversation_id)
    _turns, nodes, reasons = _source(
        record, base, timeline, conversation_id=conversation_id
    )
    needle, matches = _match_text(normalized), []
    for node in nodes:
        if cursor is not None and node["source_seq"] <= cursor:
            continue
        visible = "\n".join(part for part in (node.get("text"), node.get("label")) if part)
        comparable = _match_text(visible)
        position = comparable.find(needle)
        if position < 0:
            continue
        excerpt_start = max(0, position - 80)
        matches.append({"stable_node_id": node["stable_node_id"], "turn_id": node["turn_id"], "source_seq": node["source_seq"], "kind": f"{node['kind']}.{node['role']}" if node.get("role") else node["kind"], "excerpt": visible[excerpt_start:excerpt_start + MAX_EXCERPT_CHARS].strip()})
    page, has_more = matches[:page_limit], len(matches) > page_limit
    reasons = _partial_reasons(base, reasons)
    return {"contract": SEARCH_CONTRACT, "session_id": conversation_id, "projection_revision": base["projection_revision"], "source_cursor": base["source_cursor"], "query_digest": digest, "completeness": "partial" if reasons else "complete", "partial_reasons": reasons, "results": page, "has_more": has_more, "next_cursor": page[-1]["source_seq"] if has_more and page else None}


def build_history_window(*, conversation_id: str, record: Mapping[str, Any], foundation: object, timeline: object = None, turn_id: object, source_seq: object, stable_node_id: object = None, before_seq: object = None, after_seq: object = None, limit: object = 128) -> dict[str, Any]:
    conversation_id = _identity(conversation_id, "conversation_id")
    checked_turn = _identity(turn_id, "turn_id")
    anchor_seq = _counter(source_seq, "source_seq")
    page_limit = _page_limit(limit, maximum=MAX_HISTORY_NODES, reason="agent_history_limit_invalid")
    anchor_node = None if stable_node_id is None else _identity(stable_node_id, "stable_node_id")
    if before_seq is not None and after_seq is not None:
        _fail("历史分页游标冲突", "agent_history_cursor_invalid")
    before = None if before_seq is None else _counter(before_seq, "before_seq")
    after = None if after_seq is None else _counter(after_seq, "after_seq")
    base = _foundation(foundation, conversation_id=conversation_id)
    turns, all_nodes, reasons = _source(
        record, base, timeline, conversation_id=conversation_id
    )
    turn = next((row for row in turns if row["turn_id"] == checked_turn), None)
    if turn is None:
        _fail("Turn不存在", "agent_turn_not_found")
    if not turn["start_seq"] <= anchor_seq <= turn["end_seq"]:
        _fail("历史定位游标不属于目标Turn", "agent_history_anchor_invalid")
    owned = [row for row in all_nodes if row["turn_id"] == checked_turn]
    if before is not None:
        page = [row for row in owned if row["source_seq"] < before][-page_limit:]
    elif after is not None:
        page = [row for row in owned if row["source_seq"] > after][:page_limit]
    elif anchor_node is not None and any(row["stable_node_id"] == anchor_node for row in owned):
        index = next(index for index, row in enumerate(owned) if row["stable_node_id"] == anchor_node)
        start = max(0, min(index - page_limit // 2, max(0, len(owned) - page_limit)))
        page = owned[start:start + page_limit]
    else:
        page = owned[:page_limit]
    if anchor_node is not None and not any(row["stable_node_id"] == anchor_node for row in page):
        reasons.append("stable_anchor_not_loaded")
    first_seq = page[0]["source_seq"] if page else None
    last_seq = page[-1]["source_seq"] if page else None
    public = [
        {
            key: row[key]
            for key in (
                "stable_node_id",
                "turn_id",
                "segment_id",
                "operation_id",
                "source_seq",
                "kind",
                "category",
                "role",
                "label",
                "text",
                "state",
                "result_code",
                "resource_ref",
                "workflow_stage",
                "occurred_at",
            )
        }
        for row in page
    ]
    reasons = _partial_reasons(base, reasons)
    return {"contract": HISTORY_WINDOW_CONTRACT, "session_id": conversation_id, "projection_revision": base["projection_revision"], "source_cursor": base["source_cursor"], "completeness": "partial" if reasons else "complete", "partial_reasons": reasons, "anchor": {"turn_id": checked_turn, "source_seq": anchor_seq, "stable_node_id": anchor_node}, "nodes": public, "has_older": bool(page and owned and owned[0]["source_seq"] < first_seq), "has_newer": bool(page and owned and owned[-1]["source_seq"] > last_seq), "older_cursor": first_seq, "newer_cursor": last_seq, "total_nodes": len(owned)}


__all__ = ["HISTORY_WINDOW_CONTRACT", "OUTLINE_CONTRACT", "SEARCH_CONTRACT", "build_history_window", "build_search_results", "build_turn_outline", "normalized_query", "query_digest"]
