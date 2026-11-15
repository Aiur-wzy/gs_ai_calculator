"""Strict App boundary for DSH-owned generic conversation read models.

DSH owns model messages, raw reasoning, tool history, whole-session
projections and search.  This module validates the detached values returned by
the local runtime before the service or Renderer can consume them.  It never
opens DSH storage and never reconstructs history from the App workbench.

The validators intentionally accept only the reviewed, path-free bridge
contracts.  Domain overlays (candidate, whiteboard, database and App-action
state) remain separate App-owned values and are not admitted here.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from genshin_app.equipment.schema import PlanError

PROJECTION_CONTRACT = "genshin-native-dsh-projection-v1"
HISTORY_CONTRACT = "genshin-native-dsh-history-v1"
SEARCH_CONTRACT = "genshin-native-dsh-search-v1"

MAX_SAFE_INTEGER = 2**53 - 1
MAX_HISTORY_ITEMS = 500
MAX_SEARCH_ITEMS = 200
MAX_TEXT_CHARS = 128 * 1024
MAX_SNIPPET_CHARS = 2_048
MAX_LABEL_CHARS = 512
MAX_CURSOR_CHARS = 4_096
MAX_QUESTION_BATCH_ITEMS = 3
MAX_QUESTION_OPTIONS = 20
MAX_QUESTION_TEXT_CHARS = 4_096
MAX_QUESTION_HEADER_CHARS = 256
MAX_QUESTION_LABEL_CHARS = 1_000
MAX_QUESTION_DESCRIPTION_CHARS = 2_000
MAX_QUESTION_CUSTOM_CHARS = 16_384

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_EVENT_TYPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_PROJECTION_KEYS = frozenset({
    "contextBreakdown",
    "contextPressure",
    "sessionStats",
    "tokenUsage",
    "turnOutline",
    "userQuestions",
})
_HISTORY_KINDS = frozenset({
    "message",
    "reasoning",
    "tool_call",
    "tool_result",
    "turn",
    "step",
    "status",
})
_ROLES = frozenset({"user", "assistant", "system", "developer", "tool"})


def _fail(code: str) -> None:
    raise PlanError("DSH会话读取结果未通过核验", code)


def _strict_mapping(value: object, fields: set[str], code: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(code)
    return copy.deepcopy(dict(value))


def _identity(value: object, code: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(code)
    return value


def _optional_identity(value: object, code: str) -> str | None:
    return None if value is None else _identity(value, code)


def _counter(value: object, code: str, *, nullable: bool = False) -> int | None:
    if nullable and value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER:
        _fail(code)
    return value


def _bounded_text(value: object, maximum: int, code: str) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        _fail(code)
    return value


def _optional_text(value: object, maximum: int, code: str) -> str | None:
    return None if value is None else _bounded_text(value, maximum, code)


def _plain_json(value: object, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    """Bound a detached JSON projection without interpreting DSH domain fields."""

    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if depth > 32 or nodes[0] > 50_000:
        _fail("native_dsh_projection_too_large")
    if value is None or isinstance(value, (str, bool)):
        if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
            _fail("native_dsh_projection_too_large")
        return
    if type(value) is int:
        if not -MAX_SAFE_INTEGER <= value <= MAX_SAFE_INTEGER:
            _fail("native_dsh_projection_invalid")
        return
    if isinstance(value, float):
        _fail("native_dsh_projection_invalid")
    if isinstance(value, list):
        for item in value:
            _plain_json(item, depth=depth + 1, nodes=nodes)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 256:
                _fail("native_dsh_projection_invalid")
            _plain_json(item, depth=depth + 1, nodes=nodes)
        return
    _fail("native_dsh_projection_invalid")


def _question_option(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        _fail("native_dsh_question_projection_invalid")
    keys = set(value)
    if keys not in ({"label"}, {"label", "description"}):
        _fail("native_dsh_question_projection_invalid")
    option = {
        "label": _bounded_text(
            value.get("label"),
            MAX_QUESTION_LABEL_CHARS,
            "native_dsh_question_projection_invalid",
        )
    }
    if not option["label"]:
        _fail("native_dsh_question_projection_invalid")
    if "description" in value:
        description = _bounded_text(
            value.get("description"),
            MAX_QUESTION_DESCRIPTION_CHARS,
            "native_dsh_question_projection_invalid",
        )
        if not description:
            _fail("native_dsh_question_projection_invalid")
        option["description"] = description
    return option


def _question_item(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("native_dsh_question_projection_invalid")
    required = {"id", "question"}
    optional = {"detail", "header", "options", "multiSelect"}
    if not required.issubset(value) or not set(value).issubset(required | optional):
        _fail("native_dsh_question_projection_invalid")
    row: dict[str, Any] = {
        "id": _identity(value.get("id"), "native_dsh_question_projection_invalid"),
        "question": _bounded_text(
            value.get("question"),
            MAX_QUESTION_TEXT_CHARS,
            "native_dsh_question_projection_invalid",
        ),
    }
    if not row["question"]:
        _fail("native_dsh_question_projection_invalid")
    for name, maximum in (
        ("detail", MAX_QUESTION_TEXT_CHARS),
        ("header", MAX_QUESTION_HEADER_CHARS),
    ):
        if name in value:
            text = _bounded_text(
                value.get(name),
                maximum,
                "native_dsh_question_projection_invalid",
            )
            if not text:
                _fail("native_dsh_question_projection_invalid")
            row[name] = text
    if "options" in value:
        options = value.get("options")
        if (
            not isinstance(options, Sequence)
            or isinstance(options, (str, bytes))
            or not 1 <= len(options) <= MAX_QUESTION_OPTIONS
        ):
            _fail("native_dsh_question_projection_invalid")
        checked = [_question_option(item) for item in options]
        labels = [item["label"] for item in checked]
        if len(set(labels)) != len(labels):
            _fail("native_dsh_question_projection_invalid")
        row["options"] = checked
    if "multiSelect" in value:
        if type(value.get("multiSelect")) is not bool:
            _fail("native_dsh_question_projection_invalid")
        if value.get("multiSelect") and "options" not in row:
            _fail("native_dsh_question_projection_invalid")
        row["multiSelect"] = value.get("multiSelect")
    return row


def _question_answer(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail("native_dsh_question_projection_invalid")
    keys = set(value)
    if keys not in ({"id", "selected"}, {"id", "selected", "custom"}):
        _fail("native_dsh_question_projection_invalid")
    selected = value.get("selected")
    if (
        not isinstance(selected, Sequence)
        or isinstance(selected, (str, bytes))
        or len(selected) > MAX_QUESTION_OPTIONS
        or any(not isinstance(item, str) or len(item) > MAX_QUESTION_LABEL_CHARS for item in selected)
        or len(set(selected)) != len(selected)
    ):
        _fail("native_dsh_question_projection_invalid")
    row: dict[str, Any] = {
        "id": _identity(value.get("id"), "native_dsh_question_projection_invalid"),
        "selected": list(selected),
    }
    if "custom" in value:
        custom = _bounded_text(
            value.get("custom"),
            MAX_QUESTION_CUSTOM_CHARS,
            "native_dsh_question_projection_invalid",
        )
        if not custom:
            _fail("native_dsh_question_projection_invalid")
        row["custom"] = custom
    return row


def normalize_question_projection(value: object) -> dict[str, list[dict[str, Any]]]:
    """Validate the exact durable DSH user-question projection."""

    row = _strict_mapping(
        value,
        {"active", "settled"},
        "native_dsh_question_projection_invalid",
    )
    if not isinstance(row["active"], list) or not isinstance(row["settled"], list):
        _fail("native_dsh_question_projection_invalid")
    active: list[dict[str, Any]] = []
    settled: list[dict[str, Any]] = []
    call_ids: set[str] = set()
    for raw in row["active"]:
        item = _strict_mapping(
            raw,
            {"callId", "questions", "state"},
            "native_dsh_question_projection_invalid",
        )
        call_id = _identity(
            item["callId"], "native_dsh_question_projection_invalid"
        )
        questions = item["questions"]
        if (
            call_id in call_ids
            or item["state"] not in {"open", "continued"}
            or not isinstance(questions, list)
            or not 1 <= len(questions) <= MAX_QUESTION_BATCH_ITEMS
        ):
            _fail("native_dsh_question_projection_invalid")
        checked_questions = [_question_item(question) for question in questions]
        if len({question["id"] for question in checked_questions}) != len(
            checked_questions
        ):
            _fail("native_dsh_question_projection_invalid")
        call_ids.add(call_id)
        active.append({
            "callId": call_id,
            "questions": checked_questions,
            "state": item["state"],
        })
    for raw in row["settled"]:
        item = _strict_mapping(
            raw,
            {"callId", "answers"},
            "native_dsh_question_projection_invalid",
        )
        call_id = _identity(
            item["callId"], "native_dsh_question_projection_invalid"
        )
        answers = item["answers"]
        if (
            call_id in call_ids
            or not isinstance(answers, list)
            or len(answers) > MAX_QUESTION_BATCH_ITEMS
        ):
            _fail("native_dsh_question_projection_invalid")
        checked_answers = [_question_answer(answer) for answer in answers]
        if len({answer["id"] for answer in checked_answers}) != len(
            checked_answers
        ):
            _fail("native_dsh_question_projection_invalid")
        call_ids.add(call_id)
        settled.append({"callId": call_id, "answers": checked_answers})
    return {"active": active, "settled": settled}


def normalize_projection_payload(
    value: object,
    *,
    expected_session_id: str,
) -> dict[str, Any]:
    """Validate one cold/live projection snapshot from DSH."""

    expected = _identity(expected_session_id, "native_dsh_session_invalid")
    row = _strict_mapping(
        value,
        {"contract", "session_id", "as_of_seq", "values"},
        "native_dsh_projection_invalid",
    )
    if row["contract"] != PROJECTION_CONTRACT or row["session_id"] != expected:
        _fail("native_dsh_projection_binding_mismatch")
    row["as_of_seq"] = _counter(
        row["as_of_seq"], "native_dsh_projection_invalid", nullable=True
    )
    values = row["values"]
    if not isinstance(values, Mapping) or set(values) != _PROJECTION_KEYS:
        _fail("native_dsh_projection_invalid")
    _plain_json(values)
    normalized_values = copy.deepcopy(dict(values))
    normalized_values["userQuestions"] = normalize_question_projection(
        values["userQuestions"]
    )
    row["values"] = normalized_values
    return row


def _history_item(value: object) -> dict[str, Any]:
    fields = {
        "stable_id",
        "seq",
        "time_ms",
        "event_type",
        "kind",
        "turn",
        "step",
        "role",
        "label",
        "text",
        "call_id",
        "name",
        "state",
        "raw_reasoning_available",
        "raw_reasoning_included",
    }
    row = _strict_mapping(value, fields, "native_dsh_history_invalid")
    row["stable_id"] = _identity(row["stable_id"], "native_dsh_history_invalid")
    row["seq"] = _counter(row["seq"], "native_dsh_history_invalid")
    row["time_ms"] = _counter(row["time_ms"], "native_dsh_history_invalid")
    if not isinstance(row["event_type"], str) or _EVENT_TYPE.fullmatch(
        row["event_type"]
    ) is None:
        _fail("native_dsh_history_invalid")
    if row["kind"] not in _HISTORY_KINDS:
        _fail("native_dsh_history_invalid")
    row["turn"] = _counter(
        row["turn"], "native_dsh_history_invalid", nullable=True
    )
    row["step"] = _counter(
        row["step"], "native_dsh_history_invalid", nullable=True
    )
    if row["role"] is not None and row["role"] not in _ROLES:
        _fail("native_dsh_history_invalid")
    row["label"] = _bounded_text(
        row["label"], MAX_LABEL_CHARS, "native_dsh_history_invalid"
    )
    row["text"] = _optional_text(
        row["text"], MAX_TEXT_CHARS, "native_dsh_history_invalid"
    )
    row["call_id"] = _optional_identity(
        row["call_id"], "native_dsh_history_invalid"
    )
    row["name"] = _optional_text(
        row["name"], MAX_LABEL_CHARS, "native_dsh_history_invalid"
    )
    row["state"] = _optional_identity(
        row["state"], "native_dsh_history_invalid"
    )
    if (
        type(row["raw_reasoning_available"]) is not bool
        or type(row["raw_reasoning_included"]) is not bool
        or row["raw_reasoning_included"]
        and (
            row["kind"] != "reasoning"
            or not row["raw_reasoning_available"]
            or row["text"] is None
        )
        or row["kind"] == "reasoning"
        and row["text"] is not None
        and not row["raw_reasoning_included"]
    ):
        _fail("native_dsh_history_invalid")
    return row


def normalize_history_payload(
    value: object,
    *,
    expected_session_id: str,
    raw_reasoning_authorized: bool,
) -> dict[str, Any]:
    """Validate one bounded event page.

    Several items may share a DSH event sequence (for example one assistant
    settlement projects a reasoning block and visible answer).  Items must be
    stable-id unique and non-decreasing by raw event sequence; one event may
    never be split across pages by the runtime.
    """

    expected = _identity(expected_session_id, "native_dsh_session_invalid")
    row = _strict_mapping(
        value,
        {
            "contract",
            "session_id",
            "as_of_seq",
            "items",
            "has_older",
            "has_newer",
            "older_cursor",
            "newer_cursor",
            "total_items",
        },
        "native_dsh_history_invalid",
    )
    if row["contract"] != HISTORY_CONTRACT or row["session_id"] != expected:
        _fail("native_dsh_history_binding_mismatch")
    row["as_of_seq"] = _counter(
        row["as_of_seq"], "native_dsh_history_invalid", nullable=True
    )
    if (
        not isinstance(row["items"], Sequence)
        or isinstance(row["items"], (str, bytes))
        or len(row["items"]) > MAX_HISTORY_ITEMS
    ):
        _fail("native_dsh_history_invalid")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous_seq = -1
    for raw in row["items"]:
        item = _history_item(raw)
        if item["stable_id"] in seen or item["seq"] < previous_seq:
            _fail("native_dsh_history_invalid")
        if item["raw_reasoning_included"] and not raw_reasoning_authorized:
            _fail("native_dsh_reasoning_not_authorized")
        seen.add(item["stable_id"])
        previous_seq = item["seq"]
        items.append(item)
    row["items"] = items
    for name in ("has_older", "has_newer"):
        if type(row[name]) is not bool:
            _fail("native_dsh_history_invalid")
    row["older_cursor"] = _counter(
        row["older_cursor"], "native_dsh_history_invalid", nullable=True
    )
    row["newer_cursor"] = _counter(
        row["newer_cursor"], "native_dsh_history_invalid", nullable=True
    )
    row["total_items"] = _counter(
        row["total_items"], "native_dsh_history_invalid"
    )
    if (
        row["total_items"] < len(items)
        or row["has_older"] != (row["older_cursor"] is not None)
        or row["has_newer"] != (row["newer_cursor"] is not None)
    ):
        _fail("native_dsh_history_invalid")
    return row


def normalize_search_payload(
    value: object,
    *,
    expected_session_id: str,
) -> dict[str, Any]:
    """Validate one DSH FTS page; search snippets may never contain reasoning."""

    expected = _identity(expected_session_id, "native_dsh_session_invalid")
    row = _strict_mapping(
        value,
        {"contract", "session_id", "as_of_seq", "items", "next_cursor"},
        "native_dsh_search_invalid",
    )
    if row["contract"] != SEARCH_CONTRACT or row["session_id"] != expected:
        _fail("native_dsh_search_binding_mismatch")
    row["as_of_seq"] = _counter(
        row["as_of_seq"], "native_dsh_search_invalid", nullable=True
    )
    if (
        not isinstance(row["items"], Sequence)
        or isinstance(row["items"], (str, bytes))
        or len(row["items"]) > MAX_SEARCH_ITEMS
    ):
        _fail("native_dsh_search_invalid")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in row["items"]:
        item = _strict_mapping(
            raw,
            {"stable_id", "seq", "time_ms", "event_type", "turn", "kind", "snippet"},
            "native_dsh_search_invalid",
        )
        item["stable_id"] = _identity(
            item["stable_id"], "native_dsh_search_invalid"
        )
        item["seq"] = _counter(item["seq"], "native_dsh_search_invalid")
        item["time_ms"] = _counter(
            item["time_ms"], "native_dsh_search_invalid"
        )
        if (
            not isinstance(item["event_type"], str)
            or _EVENT_TYPE.fullmatch(item["event_type"]) is None
            or item["kind"] not in _HISTORY_KINDS - {"reasoning"}
        ):
            _fail("native_dsh_search_invalid")
        item["turn"] = _counter(
            item["turn"], "native_dsh_search_invalid", nullable=True
        )
        item["snippet"] = _bounded_text(
            item["snippet"], MAX_SNIPPET_CHARS, "native_dsh_search_invalid"
        )
        if item["stable_id"] in seen:
            _fail("native_dsh_search_invalid")
        seen.add(item["stable_id"])
        items.append(item)
    row["items"] = items
    cursor = row["next_cursor"]
    if cursor is not None:
        row["next_cursor"] = _bounded_text(
            cursor, MAX_CURSOR_CHARS, "native_dsh_search_invalid"
        )
    return row


def _turn_id(value: int) -> str:
    return f"dsh-turn-{value}"


def _as_cursor(value: object) -> int:
    return value if type(value) is int and value >= 0 else 0


def _as_timestamp(value: int) -> str:
    return datetime.fromtimestamp(value / 1000, tz=UTC).isoformat().replace(
        "+00:00", "Z"
    )


def turn_outline_view(
    projection: Mapping[str, Any],
    *,
    conversation_id: str,
    before_seq: int | None = None,
    after_seq: int | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    """Project DSH's whole-log turn outline into the existing Renderer contract."""

    values = projection["values"]
    raw = values["turnOutline"]
    if not isinstance(raw, list):
        _fail("native_dsh_projection_invalid")
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping) or set(item) != {
            "turn", "seq", "prompt", "response"
        }:
            _fail("native_dsh_projection_invalid")
        turn = _counter(item["turn"], "native_dsh_projection_invalid")
        start = _counter(item["seq"], "native_dsh_projection_invalid")
        prompt = _bounded_text(
            item["prompt"], MAX_SNIPPET_CHARS, "native_dsh_projection_invalid"
        )
        response = _bounded_text(
            item["response"], MAX_SNIPPET_CHARS, "native_dsh_projection_invalid"
        )
        next_start = None
        if index + 1 < len(raw) and isinstance(raw[index + 1], Mapping):
            next_start = raw[index + 1].get("seq")
        end = (
            next_start - 1
            if type(next_start) is int and next_start > start
            else projection["as_of_seq"]
            if type(projection["as_of_seq"]) is int
            else start
        )
        excerpt = prompt or response
        rows.append({
            "turn_id": _turn_id(turn),
            "start_seq": start,
            "end_seq": max(start, end),
            "status": "completed" if response else "running",
            "excerpt": excerpt,
            "markers": [],
            "unread": False,
        })
    eligible = [
        row
        for row in rows
        if (before_seq is None or row["start_seq"] < before_seq)
        and (after_seq is None or row["start_seq"] > after_seq)
    ]
    page = (
        eligible[-limit:]
        if before_seq is not None or after_seq is None
        else eligible[:limit]
    )
    cursor = _as_cursor(projection["as_of_seq"])
    return {
        "contract": "agent-turn-outline-v1",
        "session_id": conversation_id,
        "projection_revision": cursor,
        "source_cursor": cursor,
        "completeness": "complete",
        "partial_reasons": [],
        "items": page,
        "total_turns": len(rows),
        "has_older": bool(page and rows and page[0]["start_seq"] > rows[0]["start_seq"]),
        "has_newer": bool(page and rows and page[-1]["start_seq"] < rows[-1]["start_seq"]),
        "older_cursor": page[0]["start_seq"] if page else None,
        "newer_cursor": page[-1]["start_seq"] if page else None,
    }


def _public_history_node(item: Mapping[str, Any]) -> dict[str, Any]:
    kind = item["kind"]
    role = item["role"]
    if kind in {"message", "reasoning"}:
        public_kind = "message"
        category = "provider"
        public_role = (
            role
            if role in {"user", "assistant", "system"}
            else "system"
            if role == "developer"
            else None
        )
    elif kind in {"tool_call", "tool_result"}:
        public_kind = "tool"
        category = "app_tool"
        public_role = None
    else:
        public_kind = "activity"
        category = "runtime"
        public_role = None
    stable = f"dsh:{item['stable_id']}"
    operation = (
        f"reasoning:{item['stable_id']}"
        if kind == "reasoning"
        else item["call_id"] or item["stable_id"]
    )
    return {
        "stable_node_id": stable,
        "turn_id": _turn_id(item["turn"]) if item["turn"] is not None else "dsh-turn-0",
        "segment_id": None,
        "operation_id": operation,
        "source_seq": item["seq"],
        "kind": public_kind,
        "category": category,
        "role": public_role,
        "label": item["label"],
        "text": item["text"],
        "state": item["state"],
        "result_code": None,
        "resource_ref": None,
        "workflow_stage": None,
        "occurred_at": _as_timestamp(item["time_ms"]),
    }


def history_window_view(
    history: Mapping[str, Any],
    *,
    conversation_id: str,
    turn_id: str,
    source_seq: int,
    stable_node_id: str | None,
) -> dict[str, Any]:
    """Project one DSH history page without introducing a second transcript."""

    items = [_public_history_node(item) for item in history["items"]]
    selected = [item for item in items if item["turn_id"] == turn_id]
    reasons: list[str] = []
    if stable_node_id is not None and not any(
        row["stable_node_id"] == stable_node_id for row in selected
    ):
        reasons.append("stable_anchor_not_loaded")
    cursor = _as_cursor(history["as_of_seq"])
    return {
        "contract": "agent-history-window-v1",
        "session_id": conversation_id,
        "projection_revision": cursor,
        "source_cursor": cursor,
        "completeness": "partial" if reasons else "complete",
        "partial_reasons": reasons,
        "anchor": {
            "turn_id": turn_id,
            "source_seq": source_seq,
            "stable_node_id": stable_node_id,
        },
        "has_older": history["has_older"],
        "has_newer": history["has_newer"],
        "older_cursor": history["older_cursor"],
        "newer_cursor": history["newer_cursor"],
        "total_nodes": history["total_items"],
        "nodes": selected,
    }


def search_results_view(
    search: Mapping[str, Any],
    *,
    conversation_id: str,
    query: str,
) -> dict[str, Any]:
    cursor = _as_cursor(search["as_of_seq"])
    return {
        "contract": "agent-search-results-v1",
        "session_id": conversation_id,
        "projection_revision": cursor,
        "source_cursor": cursor,
        "query_digest": hashlib.sha256(query.strip().encode("utf-8")).hexdigest(),
        "completeness": "complete",
        "partial_reasons": [],
        "results": [
            {
                "stable_node_id": f"dsh:{item['stable_id']}",
                "turn_id": _turn_id(item["turn"] or 0),
                "source_seq": item["seq"],
                "kind": item["kind"],
                "excerpt": item["snippet"],
            }
            for item in search["items"]
        ],
        "has_more": search["next_cursor"] is not None,
        "next_cursor": search["next_cursor"],
    }


def telemetry_view(
    projection: Mapping[str, Any],
    history: Mapping[str, Any],
    *,
    conversation_id: str,
) -> dict[str, Any]:
    """Build the existing telemetry envelope exclusively from DSH generic facts."""

    cursor = _as_cursor(projection["as_of_seq"])
    values = projection["values"]
    spans = []
    for item in history["items"]:
        node = _public_history_node(item)
        spans.append({
            "span_id": node["stable_node_id"],
            "parent_span_id": None,
            "turn_id": node["turn_id"],
            "segment_id": None,
            "workflow_stage": None,
            "category": node["category"] if node["category"] in {
                "provider", "app_tool", "runtime"
            } else "other",
            "state": node["state"] if node["state"] in {
                "preparing", "running", "waiting", "completed", "cancelled",
                "failed", "interrupted", "unknown",
            } else "unknown",
            "label": node["label"],
            "summary": (node["text"] or "")[:1_000],
            "result_code": None,
            "resource_ref": None,
            "seq_start": node["source_seq"],
            "seq_end": node["source_seq"],
            "elapsed_ms": None,
            "timing_source": "provider" if node["category"] == "provider" else "runtime",
            "timing_precision": "unknown",
        })
    trajectory = {
        "contract": "agent-trajectory-v1",
        "session_id": conversation_id,
        "projection_revision": cursor,
        "source_cursor": cursor,
        "completeness": "complete",
        "partial_reasons": [],
        "spans": spans,
        "total_spans": history["total_items"],
        "has_older": history["has_older"],
        "has_newer": history["has_newer"],
        "older_cursor": history["older_cursor"],
        "newer_cursor": history["newer_cursor"],
    }
    usage = values["tokenUsage"]
    pressure = values["contextPressure"]
    breakdown = values["contextBreakdown"]
    context_rows = []
    for key, label in (
        ("uncachedInputTokens", "累计未缓存输入 Token"),
        ("outputTokens", "累计输出 Token"),
        ("cacheReadTokens", "累计缓存读取 Token"),
        ("cacheWriteTokens", "累计缓存写入 Token"),
    ):
        value = usage.get(key) if isinstance(usage, Mapping) else None
        if type(value) is int:
            context_rows.append({
                "metric_id": key,
                "label": label,
                "category": "provider_usage",
                "value": value,
                "unit": "token",
                "source": "provider",
                "precision": "exact",
                "note": "来自 DSH 耐久会话投影。",
            })
    for key, label in (
        ("projectedTokens", "下次请求预计上下文"),
        ("pressureTokens", "最近请求上下文"),
        ("contextWindow", "模型上下文上限"),
    ):
        value = pressure.get(key) if isinstance(pressure, Mapping) else None
        if type(value) is int:
            context_rows.append({
                "metric_id": key,
                "label": label,
                "category": "context_pressure",
                "value": value,
                "unit": "token",
                "source": "runtime",
                "precision": "exact",
                "note": "由 DSH 按当前模型表面重放计算。",
            })
    for key, label in (
        ("systemTokens", "系统提示估算"),
        ("toolsTokens", "工具定义估算"),
        ("messageTokens", "消息估算"),
    ):
        value = breakdown.get(key) if isinstance(breakdown, Mapping) else None
        if type(value) is int:
            context_rows.append({
                "metric_id": key,
                "label": label,
                "category": "context_breakdown",
                "value": value,
                "unit": "token",
                "source": "runtime",
                "precision": "estimated",
                "note": "DSH 固定启发式估算，不是账单值。",
            })
    stats = values["sessionStats"]
    performance_rows = []
    labels = {
        "turns": ("已完成轮次", "count"),
        "steps": ("已结束步骤", "count"),
        "llmMs": ("模型累计耗时", "ms"),
        "toolMs": ("工具累计耗时", "ms"),
        "ttftMs": ("首 Token 延迟合计", "ms"),
        "ttftSteps": ("含首 Token 计时步骤", "count"),
        "decodeMs": ("解码累计耗时", "ms"),
        "decodeTokens": ("解码输出 Token", "token"),
    }
    if isinstance(stats, Mapping):
        for key, (label, unit) in labels.items():
            value = stats.get(key)
            if type(value) is int:
                performance_rows.append({
                    "metric_id": key,
                    "label": label,
                    "category": "provider",
                    "value": value,
                    "unit": unit,
                    "source": "provider",
                    "precision": "exact",
                    "note": "由 DSH 完整事件日志折叠。",
                })
    def metrics(contract: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "contract": contract,
            "session_id": conversation_id,
            "projection_revision": cursor,
            "source_cursor": cursor,
            "completeness": "complete",
            "partial_reasons": [],
            "metrics": rows,
        }
    return {
        "trajectory": trajectory,
        "context": metrics("agent-context-metrics-v1", context_rows),
        "performance": metrics(
            "agent-performance-metrics-v1", performance_rows
        ),
    }


__all__ = [
    "HISTORY_CONTRACT",
    "MAX_HISTORY_ITEMS",
    "MAX_SEARCH_ITEMS",
    "PROJECTION_CONTRACT",
    "SEARCH_CONTRACT",
    "history_window_view",
    "normalize_history_payload",
    "normalize_projection_payload",
    "normalize_question_projection",
    "normalize_search_payload",
    "search_results_view",
    "telemetry_view",
    "turn_outline_view",
]
