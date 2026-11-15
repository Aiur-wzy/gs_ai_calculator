"""Durable, path-free Host facts for the native Agent workbench.

This module is intentionally independent from the runtime and Renderer.  It
stores a single append-only fact stream for logical turns, execution segments,
question delivery/resume receipts and the busy-turn inbox.  DSH owns generic
question prose, options and answers; this ledger retains only opaque
identities, fingerprints and App-domain lifecycle facts.  The bounded turn outline is
derived from those facts; no projection may become a second authority.

The first integration slice deliberately contains no provider calls, process
handles, filesystem capabilities, raw tool arguments or document bodies.  A
Router/Service adapter can therefore be added later without weakening the
existing candidate and App-action ledgers.
"""

from __future__ import annotations

import copy
import json
import re
import secrets
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from genshin_app.equipment.files import json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_db.locking import file_lock

from .native_session_store import redact_visible_text
from .sessions import safe_path, write_owned

CONTRACT = "genshin-native-workbench-ledger-v1"
PROJECTION_CONTRACT = "genshin-agent-ui-projection-v2-foundation"
LEDGER_FILE = "native-workbench-ledger-v1.json"
LOCK_FILE = ".native-workbench-ledger-v1.lock"

MAX_LEDGER_BYTES = 512 * 1024 * 1024
# 1,000 turns with tool/search activity routinely exceed the original 8,192
# lifecycle rows.  This is still a hard per-conversation bound; readers page
# timeline nodes and never ship this whole file to the Renderer.
MAX_EVENTS = 262_144
MAX_EVENT_BYTES = 32 * 1024
MAX_TEXT_CHARS = 16_000
MAX_SUMMARY_CHARS = 1_000
MAX_OPTIONS = 32
MAX_QUESTION_BATCH_ITEMS = 3
MAX_ANSWER_VALUES = 32
MAX_MARKERS = 32
MAX_PROJECTION_ROWS = 2048
MAX_TIMELINE_NODES = 20_000
MAX_TIMELINE_PAGE = 500
JSON_SAFE_INTEGER_MAX = 2**53 - 1

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_EVENT_ID = re.compile(r"event_[A-Za-z0-9_-]{32}\Z")
_RECEIPT = re.compile(r"receipt_[A-Za-z0-9_-]{43}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")
_SENSITIVE_PREFIXES = (
    "access-token:",
    "api-key:",
    "api_key:",
    "apikey:",
    "authorization:",
    "basic:",
    "bearer:",
    "cookie:",
    "credential:",
    "password:",
    "proxy-authorization:",
    "secret:",
    "sk-",
    "token:",
    "wf_",
    "x-api-key:",
)

TIMELINE_KINDS = frozenset(
    {
        "message",
        "tool",
        "activity",
        "research",
        "question",
        "candidate",
        "app_action",
        "checkpoint",
        "notice",
    }
)
TIMELINE_ROLES = frozenset({"user", "assistant", "notice", "system"})
TIMELINE_CATEGORIES = frozenset(
    {
        "runtime",
        "provider",
        "web",
        "app_tool",
        "engine",
        "database",
        "skill",
        "user_wait",
        "candidate",
        "app_action",
        "checkpoint",
    }
)

TURN_STATES = frozenset(
    {
        "preparing",
        "streaming",
        "using_tool",
        "awaiting_input_live",
        "awaiting_input_suspended",
        "resuming",
        "cancelling",
        "continued",
        "completed",
        "cancelled",
        "failed",
        "interrupted",
    }
)
TURN_TERMINAL = frozenset({"completed", "cancelled", "failed", "interrupted"})
TURN_TRANSITIONS = {
    "preparing": frozenset({"streaming", "cancelling", "failed", "interrupted"}),
    "streaming": frozenset(
        {
            "using_tool",
            "awaiting_input_live",
            "cancelling",
            "completed",
            "failed",
            "interrupted",
        }
    ),
    "using_tool": frozenset(
        {
            "streaming",
            "awaiting_input_live",
            "cancelling",
            "completed",
            "failed",
            "interrupted",
        }
    ),
    "awaiting_input_live": frozenset(
        {"streaming", "awaiting_input_suspended", "cancelling", "failed", "interrupted"}
    ),
    "awaiting_input_suspended": frozenset({"resuming", "cancelling", "failed"}),
    "resuming": frozenset(
        {"streaming", "cancelling", "completed", "failed", "interrupted"}
    ),
    "cancelling": frozenset({"cancelled", "interrupted"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
    "interrupted": frozenset(),
}

SEGMENT_STATES = frozenset(
    {
        "preparing",
        "streaming",
        "using_tool",
        "awaiting_input_live",
        "suspended",
        "cancelling",
        "continued",
        "completed",
        "cancelled",
        "failed",
        "interrupted",
    }
)
SEGMENT_TERMINAL = frozenset(
    {"suspended", "continued", "completed", "cancelled", "failed", "interrupted"}
)
SEGMENT_TRANSITIONS = {
    "preparing": frozenset(
        {"streaming", "cancelling", "continued", "completed", "failed", "interrupted"}
    ),
    "streaming": frozenset(
        {
            "using_tool",
            "awaiting_input_live",
            "cancelling",
            "continued",
            "completed",
            "failed",
            "interrupted",
        }
    ),
    "using_tool": frozenset(
        {
            "streaming",
            "awaiting_input_live",
            "cancelling",
            "continued",
            "completed",
            "failed",
            "interrupted",
        }
    ),
    "awaiting_input_live": frozenset(
        {"streaming", "suspended", "cancelling", "failed", "interrupted"}
    ),
    "cancelling": frozenset({"cancelled", "interrupted"}),
    "suspended": frozenset(),
    "continued": frozenset(),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
    "interrupted": frozenset(),
}

QUESTION_KINDS = frozenset({"confirmation", "single", "multiple", "text", "batch"})
QUESTION_MODES = frozenset({"live", "suspended"})
QUESTION_STATES = frozenset(
    {
        "open",
        "answer_submitting",
        "answered",
        "cancelled",
        "expired",
        "orphaned",
        "resuming",
        "settled",
    }
)
QUESTION_TERMINAL = frozenset({"cancelled", "expired", "orphaned", "settled"})
QUESTION_TRANSITIONS = {
    "open": frozenset({"answer_submitting", "cancelled", "expired", "orphaned"}),
    "answer_submitting": frozenset({"answered", "orphaned"}),
    "answered": frozenset({"resuming", "settled", "orphaned"}),
    "resuming": frozenset({"settled", "orphaned"}),
    "cancelled": frozenset(),
    "expired": frozenset(),
    "orphaned": frozenset(),
    "settled": frozenset(),
}

INBOX_KINDS = frozenset({"queue", "steer", "stop_and_send"})
INBOX_STATES = frozenset(
    {
        "queued",
        "cancelling",
        "held_action_required",
        "held_failure",
        "needs_rebind",
        "promoting",
        "admitted",
        "consumed",
        "cancelled",
        "failed",
        "outcome_unknown",
    }
)
INBOX_TERMINAL = frozenset({"consumed", "cancelled", "failed", "outcome_unknown"})
INBOX_TRANSITIONS = {
    "queued": frozenset(
        {
            "cancelling",
            "held_action_required",
            "held_failure",
            "needs_rebind",
            "promoting",
            "cancelled",
            "failed",
        }
    ),
    "cancelling": frozenset(
        {
            "promoting",
            "held_action_required",
            "held_failure",
            "needs_rebind",
            "cancelled",
            "failed",
            "outcome_unknown",
        }
    ),
    "held_action_required": frozenset(
        {"queued", "needs_rebind", "promoting", "cancelled", "failed"}
    ),
    "held_failure": frozenset(
        {"queued", "needs_rebind", "promoting", "cancelled", "failed"}
    ),
    "needs_rebind": frozenset({"queued", "cancelled", "failed"}),
    "promoting": frozenset(
        {
            "admitted",
            "held_action_required",
            "held_failure",
            "needs_rebind",
            "failed",
            "outcome_unknown",
        }
    ),
    "admitted": frozenset({"consumed", "failed", "outcome_unknown"}),
    "consumed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
    "outcome_unknown": frozenset(),
}

_STEERABLE_TURN_STATES = frozenset({"preparing", "streaming", "using_tool"})
_STEERABLE_SEGMENT_STATES = frozenset({"preparing", "streaming", "using_tool"})
_STOP_AND_SEND_SAFE_TERMINALS = frozenset({"completed", "cancelled"})


class NativeWorkbenchLedgerError(RuntimeError):
    """Stable, non-sensitive workbench ledger failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeWorkbenchLedgerError(code)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _counter(value: object, field: str) -> int:
    if type(value) is not int or not 0 <= value <= JSON_SAFE_INTEGER_MAX:
        _fail(f"workbench_{field}_invalid")
    return value


def _identity(value: object, field: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _fail(f"workbench_{field}_invalid")
    if value.casefold().startswith(_SENSITIVE_PREFIXES):
        _fail(f"workbench_{field}_invalid")
    return value


def _optional_identity(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _identity(value, field)


def _timestamp(value: object, field: str = "timestamp") -> str:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        _fail(f"workbench_{field}_invalid")
    try:
        parsed = datetime.fromisoformat(value)
    except (OverflowError, ValueError):
        _fail(f"workbench_{field}_invalid")
    if parsed.tzinfo is None:
        _fail(f"workbench_{field}_invalid")
    return value


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_event_id() -> str:
    return "event_" + secrets.token_urlsafe(24)


def _new_receipt() -> str:
    return "receipt_" + secrets.token_urlsafe(32)


def _visible_text(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _fail(f"workbench_{field}_invalid")
    if redact_visible_text(value) != value:
        _fail(f"workbench_{field}_sensitive")
    return value


def _optional_code(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _identity(value, field)


def _identity_list(
    value: object,
    field: str,
    *,
    maximum: int,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum:
        _fail(f"workbench_{field}_invalid")
    if not allow_empty and not value:
        _fail(f"workbench_{field}_invalid")
    result = [_identity(item, field) for item in value]
    if len(set(result)) != len(result):
        _fail(f"workbench_{field}_invalid")
    return result


def _question_option_id_matrix(value: object, *, question_count: int) -> list[list[str]]:
    if not isinstance(value, list) or len(value) != question_count:
        _fail("workbench_question_option_ids_invalid")
    return [
        _identity_list(
            row,
            "question_option_id",
            maximum=MAX_OPTIONS,
            allow_empty=True,
        )
        for row in value
    ]


def _document_binding(value: object) -> dict[str, Any]:
    fields = {
        "document_id",
        "document_lineage_epoch",
        "document_revision",
        "candidate_revision",
        "stage_revision",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("workbench_document_binding_invalid")
    return {
        "document_id": _identity(value.get("document_id"), "document_id"),
        "document_lineage_epoch": _counter(
            value.get("document_lineage_epoch"), "document_lineage_epoch"
        ),
        "document_revision": _counter(
            value.get("document_revision"), "document_revision"
        ),
        "candidate_revision": _counter(
            value.get("candidate_revision"), "candidate_revision"
        ),
        "stage_revision": _counter(value.get("stage_revision"), "stage_revision"),
    }


def _target(value: object, *, required: bool) -> dict[str, Any] | None:
    if value is None and not required:
        return None
    fields = {
        "turn_id",
        "segment_id",
        "epoch_id",
        "stage_epoch",
        "runtime_incarnation",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("workbench_inbox_target_invalid")
    return {
        "turn_id": _identity(value.get("turn_id"), "turn_id"),
        "segment_id": _identity(value.get("segment_id"), "segment_id"),
        "epoch_id": _identity(value.get("epoch_id"), "epoch_id"),
        "stage_epoch": _counter(value.get("stage_epoch"), "stage_epoch"),
        "runtime_incarnation": _identity(
            value.get("runtime_incarnation"), "runtime_incarnation"
        ),
    }


def _question_options(value: object, kind: str) -> list[dict[str, str]]:
    if kind in {"confirmation", "text"}:
        if value not in (None, []):
            _fail("workbench_question_options_invalid")
        return []
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_OPTIONS:
        _fail("workbench_question_options_invalid")
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"value_id", "label"}:
            _fail("workbench_question_options_invalid")
        result.append(
            {
                "value_id": _identity(item.get("value_id"), "question_value_id"),
                "label": _visible_text(
                    item.get("label"), "question_option_label", MAX_SUMMARY_CHARS
                ),
            }
        )
    if len({item["value_id"] for item in result}) != len(result):
        _fail("workbench_question_options_invalid")
    return result


def _optional_visible_text(
    value: object,
    field: str,
    maximum: int,
) -> str | None:
    if value is None:
        return None
    return _visible_text(value, field, maximum)


def _question_batch_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_QUESTION_BATCH_ITEMS:
        _fail("workbench_question_batch_items_invalid")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    fields = {
        "question_id",
        "kind",
        "prompt",
        "detail",
        "header",
        "intent",
        "options",
        "allow_skip",
    }
    for item in value:
        if not isinstance(item, Mapping) or set(item) != fields:
            _fail("workbench_question_batch_items_invalid")
        question_id = _identity(item.get("question_id"), "batch_question_id")
        kind = item.get("kind")
        if question_id in seen or kind not in QUESTION_KINDS - {"batch"}:
            _fail("workbench_question_batch_items_invalid")
        if type(item.get("allow_skip")) is not bool:
            _fail("workbench_question_batch_items_invalid")
        seen.add(question_id)
        raw_options = item.get("options")
        if kind in {"confirmation", "text"}:
            if raw_options not in (None, []):
                _fail("workbench_question_batch_items_invalid")
            options: list[dict[str, str | None]] = []
        else:
            if not isinstance(raw_options, list) or not 1 <= len(raw_options) <= MAX_OPTIONS:
                _fail("workbench_question_batch_items_invalid")
            options = []
            option_ids: set[str] = set()
            for option in raw_options:
                if not isinstance(option, Mapping) or set(option) != {
                    "value_id",
                    "label",
                    "description",
                }:
                    _fail("workbench_question_batch_items_invalid")
                value_id = _identity(option.get("value_id"), "question_value_id")
                if value_id in option_ids:
                    _fail("workbench_question_batch_items_invalid")
                option_ids.add(value_id)
                options.append(
                    {
                        "value_id": value_id,
                        "label": _visible_text(
                            option.get("label"),
                            "question_option_label",
                            MAX_SUMMARY_CHARS,
                        ),
                        "description": _optional_visible_text(
                            option.get("description"),
                            "question_option_description",
                            MAX_SUMMARY_CHARS,
                        ),
                    }
                )
        result.append(
            {
                "question_id": question_id,
                "kind": kind,
                "prompt": _visible_text(
                    item.get("prompt"), "question_prompt", MAX_TEXT_CHARS
                ),
                "detail": _optional_visible_text(
                    item.get("detail"), "question_detail", MAX_TEXT_CHARS
                ),
                "header": _optional_visible_text(
                    item.get("header"), "question_header", MAX_SUMMARY_CHARS
                ),
                "intent": _optional_identity(item.get("intent"), "question_intent"),
                "options": options,
                "allow_skip": item["allow_skip"],
            }
        )
    return result


def _question_batch_answer(
    value: object,
    *,
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"items"}:
        _fail("workbench_question_batch_answer_invalid")
    answers = value.get("items")
    if not isinstance(answers, list) or len(answers) != len(items):
        _fail("workbench_question_batch_answer_invalid")
    result: list[dict[str, Any]] = []
    for question, answer in zip(items, answers, strict=True):
        if not isinstance(answer, Mapping) or set(answer) != {
            "question_id",
            "text",
            "value_ids",
            "skipped",
        }:
            _fail("workbench_question_batch_answer_invalid")
        if answer.get("question_id") != question["question_id"]:
            _fail("workbench_question_batch_answer_invalid")
        skipped = answer.get("skipped")
        if type(skipped) is not bool:
            _fail("workbench_question_batch_answer_invalid")
        if skipped:
            if not question["allow_skip"] or answer.get("text") is not None or answer.get("value_ids") != []:
                _fail("workbench_question_batch_answer_invalid")
            checked = {"text": None, "value_ids": []}
        else:
            checked = _question_answer(
                {"text": answer.get("text"), "value_ids": answer.get("value_ids")},
                kind=question["kind"],
                options=question["options"],
            )
        result.append(
            {
                "question_id": question["question_id"],
                "text": checked["text"],
                "value_ids": checked["value_ids"],
                "skipped": skipped,
            }
        )
    return {"items": result}


def normalize_question_batch_answer(
    value: object,
    *,
    items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate one GUI answer against a DSH-owned question snapshot.

    The returned value is safe to keep only for the duration of the delivery
    transaction.  Callers that need durability must store its fingerprint,
    never the answer text itself, in the App-domain ledger.
    """

    return _question_batch_answer(value, items=items)


def _question_answer(
    value: object,
    *,
    kind: str,
    options: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"text", "value_ids"}:
        _fail("workbench_question_answer_invalid")
    text = value.get("text")
    raw_ids = value.get("value_ids")
    if kind == "text":
        answer_text = _visible_text(text, "question_answer", MAX_TEXT_CHARS)
        if raw_ids != []:
            _fail("workbench_question_answer_invalid")
        return {"text": answer_text, "value_ids": []}
    custom = (
        None
        if text is None
        else _visible_text(text, "question_answer", MAX_TEXT_CHARS)
    )
    ids = _identity_list(
        raw_ids,
        "question_answer_value_id",
        maximum=MAX_ANSWER_VALUES,
        allow_empty=kind in {"single", "multiple"} and custom is not None,
    )
    allowed = {item["value_id"] for item in options}
    if kind == "confirmation":
        allowed = {"confirm", "cancel"}
    if any(item not in allowed for item in ids):
        _fail("workbench_question_answer_invalid")
    if kind == "confirmation" and (custom is not None or len(ids) != 1):
        _fail("workbench_question_answer_invalid")
    if kind == "single" and len(ids) > 1:
        _fail("workbench_question_answer_invalid")
    if kind in {"single", "multiple"} and not ids and custom is None:
        _fail("workbench_question_answer_invalid")
    return {"text": custom, "value_ids": ids}


def _receipt(value: object, field: str = "receipt") -> str:
    if not isinstance(value, str) or _RECEIPT.fullmatch(value) is None:
        _fail(f"workbench_{field}_invalid")
    return value


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        _fail(f"workbench_{field}_invalid")
    return value


def _message_uuid(value: object) -> str:
    if not isinstance(value, str):
        _fail("workbench_resume_message_id_invalid")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        _fail("workbench_resume_message_id_invalid")
    if (
        str(parsed) != value
        or parsed.version not in {1, 2, 3, 4, 5}
        or parsed.variant != uuid.RFC_4122
    ):
        _fail("workbench_resume_message_id_invalid")
    return value


def _event_id(value: object) -> str:
    if not isinstance(value, str) or _EVENT_ID.fullmatch(value) is None:
        _fail("workbench_event_id_invalid")
    return value


def _exact_payload(value: object, fields: set[str], event_type: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail(f"workbench_{event_type.replace('.', '_')}_payload_invalid")
    return copy.deepcopy(dict(value))


def _timeline_node(value: object) -> dict[str, Any]:
    """Validate one small display fact; execution payloads never cross here."""

    fields = {
        "node_id",
        "turn_id",
        "segment_id",
        "operation_id",
        "kind",
        "category",
        "role",
        "label",
        "text",
        "state",
        "result_code",
        "resource_ref",
        "workflow_stage",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("workbench_timeline_node_invalid")
    kind = value.get("kind")
    category = value.get("category")
    role = value.get("role")
    if kind not in TIMELINE_KINDS or category not in TIMELINE_CATEGORIES:
        _fail("workbench_timeline_node_invalid")
    if role is not None and role not in TIMELINE_ROLES:
        _fail("workbench_timeline_node_invalid")
    text = value.get("text")
    if text is not None:
        text = _visible_text(text, "timeline_text", MAX_TEXT_CHARS)
    return {
        "node_id": _identity(value.get("node_id"), "timeline_node_id"),
        "turn_id": _identity(value.get("turn_id"), "turn_id"),
        "segment_id": _optional_identity(value.get("segment_id"), "segment_id"),
        "operation_id": _optional_identity(
            value.get("operation_id"), "timeline_operation_id"
        ),
        "kind": kind,
        "category": category,
        "role": role,
        "label": _visible_text(
            value.get("label"), "timeline_label", MAX_SUMMARY_CHARS
        ),
        "text": text,
        "state": _optional_identity(value.get("state"), "timeline_state"),
        "result_code": _optional_identity(
            value.get("result_code"), "timeline_result_code"
        ),
        "resource_ref": _optional_identity(
            value.get("resource_ref"), "timeline_resource_ref"
        ),
        "workflow_stage": _optional_identity(
            value.get("workflow_stage"), "workflow_stage"
        ),
    }


def _empty_state() -> dict[str, Any]:
    return {
        "turns": {},
        "segments": {},
        "questions": {},
        "inbox": {},
        "inbox_order": [],
        "timeline": [],
        "timeline_ids": set(),
        "mutations": {},
    }


def _require_transition(
    current: str,
    target: str,
    transitions: Mapping[str, frozenset[str]],
    code: str,
) -> None:
    if target not in transitions.get(current, frozenset()):
        _fail(code)


def _touch_turn(state: Mapping[str, Any], turn_id: str, seq: int) -> None:
    """Advance a turn's source range for every fact owned by that turn."""

    turn = state["turns"].get(turn_id)
    if turn is None:
        _fail("workbench_turn_missing")
    turn["updated_seq"] = max(turn["updated_seq"], seq)


def _target_segment(
    state: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    require_steerable: bool,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Resolve an exact, incarnation-bound Inbox target or fail closed."""

    turn = state["turns"].get(target["turn_id"])
    segment = state["segments"].get(target["segment_id"])
    if (
        turn is None
        or segment is None
        or segment["turn_id"] != target["turn_id"]
        or segment["epoch_id"] != target["epoch_id"]
        or segment["stage_epoch"] != target["stage_epoch"]
        or segment["runtime_incarnation"] != target["runtime_incarnation"]
    ):
        _fail("workbench_inbox_target_stale")
    if require_steerable and (
        turn["state"] not in _STEERABLE_TURN_STATES
        or segment["state"] not in _STEERABLE_SEGMENT_STATES
    ):
        _fail("workbench_inbox_target_not_live")
    return turn, segment


def _apply_event(state: dict[str, Any], event: Mapping[str, Any]) -> None:
    event_type = event["type"]
    payload = event["payload"]
    turns = state["turns"]
    segments = state["segments"]
    questions = state["questions"]
    inbox = state["inbox"]

    if event_type == "timeline.node_recorded":
        row = _exact_payload(payload, {"node"}, event_type)
        node = _timeline_node(row["node"])
        turn = turns.get(node["turn_id"])
        if turn is None:
            _fail("workbench_timeline_turn_missing")
        segment_id = node["segment_id"]
        if segment_id is not None:
            segment = segments.get(segment_id)
            if segment is None or segment["turn_id"] != node["turn_id"]:
                _fail("workbench_timeline_segment_invalid")
        if node["node_id"] in state["timeline_ids"]:
            _fail("workbench_timeline_node_exists")
        if len(state["timeline"]) >= MAX_TIMELINE_NODES:
            _fail("workbench_timeline_capacity")
        node["source_seq"] = event["seq"]
        node["occurred_at"] = event["occurred_at"]
        state["timeline"].append(node)
        state["timeline_ids"].add(node["node_id"])
        _touch_turn(state, node["turn_id"], event["seq"])
        return

    if event_type == "turn.created":
        row = _exact_payload(payload, {"turn_id", "predecessor_inbox_id"}, event_type)
        turn_id = _identity(row["turn_id"], "turn_id")
        predecessor_inbox_id = _optional_identity(
            row["predecessor_inbox_id"], "predecessor_inbox_id"
        )
        if turn_id in turns:
            _fail("workbench_turn_exists")
        if len(turns) >= MAX_PROJECTION_ROWS:
            _fail("workbench_projection_size_limit")
        if any(item["state"] not in TURN_TERMINAL for item in turns.values()):
            _fail("workbench_live_turn_exists")
        promoting_predecessors = [
            item
            for item in inbox.values()
            if item["kind"] in {"queue", "stop_and_send"}
            and item["state"] == "promoting"
        ]
        if predecessor_inbox_id is None and promoting_predecessors:
            _fail("workbench_turn_predecessor_required")
        if predecessor_inbox_id is not None:
            predecessor = inbox.get(predecessor_inbox_id)
            if (
                predecessor is None
                or predecessor["kind"] not in {"queue", "stop_and_send"}
                or predecessor["state"] != "promoting"
                or any(
                    item["predecessor_inbox_id"] == predecessor_inbox_id
                    for item in turns.values()
                )
            ):
                _fail("workbench_turn_predecessor_invalid")
        turns[turn_id] = {
            "turn_id": turn_id,
            "predecessor_inbox_id": predecessor_inbox_id,
            "state": "preparing",
            "created_seq": event["seq"],
            "updated_seq": event["seq"],
            "terminal_seq": None,
            "segment_ids": [],
        }
        return

    if event_type == "turn.transitioned":
        row = _exact_payload(
            payload, {"turn_id", "from_state", "to_state", "reason"}, event_type
        )
        turn_id = _identity(row["turn_id"], "turn_id")
        turn = turns.get(turn_id)
        if turn is None:
            _fail("workbench_turn_missing")
        previous = row["from_state"]
        target_state = row["to_state"]
        if previous not in TURN_STATES or target_state not in TURN_STATES:
            _fail("workbench_turn_state_invalid")
        if turn["state"] != previous:
            _fail("workbench_turn_state_conflict")
        _require_transition(
            previous,
            target_state,
            TURN_TRANSITIONS,
            "workbench_turn_transition_invalid",
        )
        _optional_code(row["reason"], "turn_reason")
        owned_questions = [
            item for item in questions.values() if item["turn_id"] == turn_id
        ]
        latest_question = max(
            owned_questions, key=lambda item: item["created_seq"], default=None
        )
        live_segments = [
            item
            for item in segments.values()
            if item["turn_id"] == turn_id and item["state"] not in SEGMENT_TERMINAL
        ]
        if (
            target_state
            in {
                "streaming",
                "using_tool",
                "awaiting_input_live",
            }
            and len(live_segments) != 1
        ):
            _fail("workbench_turn_live_segment_missing")
        predecessor_inbox_id = turn["predecessor_inbox_id"]
        if target_state == "streaming" and predecessor_inbox_id is not None:
            predecessor = inbox[predecessor_inbox_id]
            if predecessor["state"] not in {"admitted", "consumed"}:
                _fail("workbench_turn_predecessor_not_admitted")
        if (
            previous == "awaiting_input_live"
            and target_state == "streaming"
            and (
                latest_question is None
                or latest_question["state"]
                not in {
                    "answered",
                    "settled",
                }
            )
        ):
            _fail("workbench_turn_question_not_answered")
        if (
            previous == "awaiting_input_suspended"
            and target_state == "resuming"
            and (
                latest_question is None
                or latest_question["mode"] != "suspended"
                or latest_question["state"] not in {"answered", "resuming"}
            )
        ):
            _fail("workbench_turn_question_not_resumable")
        if target_state in TURN_TERMINAL:
            owned_segments = sorted(
                (
                    segment
                    for segment in segments.values()
                    if segment["turn_id"] == turn_id
                ),
                key=lambda item: item["created_seq"],
            )
            if any(
                segment["state"] not in SEGMENT_TERMINAL for segment in owned_segments
            ):
                _fail("workbench_turn_segment_not_terminal")
            latest_segment = owned_segments[-1] if owned_segments else None
            if target_state == "completed" and (
                latest_segment is None or latest_segment["state"] != "completed"
            ):
                _fail("workbench_turn_completion_not_proven")
            if (
                target_state == "cancelled"
                and latest_segment is not None
                and latest_segment["state"] != "cancelled"
            ):
                _fail("workbench_turn_cancellation_not_proven")
            if any(item["state"] not in QUESTION_TERMINAL for item in owned_questions):
                _fail("workbench_turn_question_not_terminal")
        turn["state"] = target_state
        turn["updated_seq"] = event["seq"]
        if target_state in TURN_TERMINAL:
            if turn["terminal_seq"] is not None:
                _fail("workbench_turn_terminal_duplicate")
            turn["terminal_seq"] = event["seq"]
        return

    if event_type == "segment.started":
        row = _exact_payload(
            payload,
            {
                "turn_id",
                "segment_id",
                "epoch_id",
                "runtime_incarnation",
                "stage_epoch",
            },
            event_type,
        )
        turn_id = _identity(row["turn_id"], "turn_id")
        segment_id = _identity(row["segment_id"], "segment_id")
        epoch_id = _identity(row["epoch_id"], "epoch_id")
        runtime_incarnation = _identity(
            row["runtime_incarnation"], "runtime_incarnation"
        )
        stage_epoch = _counter(row["stage_epoch"], "stage_epoch")
        turn = turns.get(turn_id)
        if turn is None or turn["state"] not in {
            "preparing", "resuming", "streaming",
        }:
            _fail("workbench_segment_turn_not_startable")
        if segment_id in segments:
            _fail("workbench_segment_exists")
        if len(segments) >= MAX_PROJECTION_ROWS:
            _fail("workbench_projection_size_limit")
        if any(item["state"] not in SEGMENT_TERMINAL for item in segments.values()):
            _fail("workbench_live_segment_exists")
        if turn["state"] == "streaming":
            prior = (
                segments.get(turn["segment_ids"][-1])
                if turn["segment_ids"]
                else None
            )
            if prior is None or prior["state"] != "continued":
                _fail("workbench_segment_continuation_missing")
        if turn["state"] == "resuming" and not any(
            question["turn_id"] == turn_id
            and question["mode"] == "suspended"
            and question["state"] == "resuming"
            for question in questions.values()
        ):
            _fail("workbench_segment_resume_question_missing")
        segments[segment_id] = {
            "segment_id": segment_id,
            "turn_id": turn_id,
            "epoch_id": epoch_id,
            "runtime_incarnation": runtime_incarnation,
            "stage_epoch": stage_epoch,
            "state": "preparing",
            "created_seq": event["seq"],
            "updated_seq": event["seq"],
            "terminal_seq": None,
        }
        turn["segment_ids"].append(segment_id)
        turn["updated_seq"] = event["seq"]
        return

    if event_type == "segment.transitioned":
        row = _exact_payload(
            payload,
            {"segment_id", "from_state", "to_state", "reason"},
            event_type,
        )
        segment_id = _identity(row["segment_id"], "segment_id")
        segment = segments.get(segment_id)
        if segment is None:
            _fail("workbench_segment_missing")
        previous = row["from_state"]
        target_state = row["to_state"]
        if previous not in SEGMENT_STATES or target_state not in SEGMENT_STATES:
            _fail("workbench_segment_state_invalid")
        if segment["state"] != previous:
            _fail("workbench_segment_state_conflict")
        _require_transition(
            previous,
            target_state,
            SEGMENT_TRANSITIONS,
            "workbench_segment_transition_invalid",
        )
        _optional_code(row["reason"], "segment_reason")
        owned_questions = [
            item for item in questions.values() if item["segment_id"] == segment_id
        ]
        latest_question = max(
            owned_questions, key=lambda item: item["created_seq"], default=None
        )
        if (
            previous == "awaiting_input_live"
            and target_state == "streaming"
            and (
                latest_question is None
                or latest_question["state"] not in {"answered", "settled"}
            )
        ):
            _fail("workbench_segment_question_not_answered")
        if (
            target_state in SEGMENT_TERMINAL
            and target_state != "suspended"
            and any(item["state"] not in QUESTION_TERMINAL for item in owned_questions)
        ):
            _fail("workbench_segment_question_not_terminal")
        segment["state"] = target_state
        segment["updated_seq"] = event["seq"]
        _touch_turn(state, segment["turn_id"], event["seq"])
        if target_state in SEGMENT_TERMINAL:
            if segment["terminal_seq"] is not None:
                _fail("workbench_segment_terminal_duplicate")
            segment["terminal_seq"] = event["seq"]
        return

    if event_type == "question.batch_reference_wait_opened":
        row = _exact_payload(
            payload,
            {
                "batch_id",
                "turn_id",
                "segment_id",
                "dsh_turn_id",
                "dsh_epoch_id",
                "call_id",
                "question_ids",
                "question_option_ids",
                "question_fingerprint",
                "approval_scope",
                "source",
            },
            event_type,
        )
        batch_id = _identity(row["batch_id"], "question_id")
        turn_id = _identity(row["turn_id"], "turn_id")
        segment_id = _identity(row["segment_id"], "segment_id")
        turn = turns.get(turn_id)
        segment = segments.get(segment_id)
        if (
            turn is None
            or segment is None
            or segment["turn_id"] != turn_id
            or turn["state"] not in {"streaming", "using_tool"}
            or segment["state"] not in {"streaming", "using_tool"}
        ):
            _fail("workbench_question_owner_not_live")
        if batch_id in questions:
            _fail("workbench_question_exists")
        if len(questions) >= MAX_PROJECTION_ROWS:
            _fail("workbench_projection_size_limit")
        unfinished = [
            item for item in questions.values() if item["state"] not in QUESTION_TERMINAL
        ]
        if unfinished:
            if not (
                len(unfinished) == 1
                and unfinished[0]["turn_id"] == turn_id
                and unfinished[0]["mode"] == "suspended"
                and unfinished[0]["state"] == "resuming"
            ):
                _fail("workbench_open_question_exists")
            unfinished[0]["state"] = "settled"
            unfinished[0]["revision"] += 1
            unfinished[0]["updated_seq"] = event["seq"]
        question_ids = _identity_list(
            row["question_ids"],
            "batch_question_id",
            maximum=MAX_QUESTION_BATCH_ITEMS,
            allow_empty=False,
        )
        question_option_ids = _question_option_id_matrix(
            row["question_option_ids"], question_count=len(question_ids)
        )
        turn["state"] = "awaiting_input_live"
        turn["updated_seq"] = event["seq"]
        segment["state"] = "awaiting_input_live"
        segment["updated_seq"] = event["seq"]
        questions[batch_id] = {
            "question_id": batch_id,
            "batch_id": batch_id,
            "turn_id": turn_id,
            "segment_id": segment_id,
            "dsh_turn_id": _identity(row["dsh_turn_id"], "dsh_turn_id"),
            "dsh_epoch_id": _identity(row["dsh_epoch_id"], "dsh_epoch_id"),
            "call_id": _identity(row["call_id"], "question_call_id"),
            "kind": "batch",
            "mode": "live",
            "state": "open",
            "prompt": "",
            "options": [],
            "items": [],
            "question_ids": question_ids,
            "question_option_ids": question_option_ids,
            "question_fingerprint": _digest(
                row["question_fingerprint"], "question_fingerprint"
            ),
            "content_authority": "dsh.userQuestions",
            "approval_scope": _optional_code(
                row["approval_scope"], "question_approval_scope"
            ),
            "source": _identity(row["source"], "question_source"),
            "answer": None,
            "answer_fingerprint": None,
            "answer_receipt": None,
            "suspension": None,
            "resume": None,
            "revision": 0,
            "created_seq": event["seq"],
            "updated_seq": event["seq"],
        }
        return

    if event_type == "question.batch_wait_opened":
        row = _exact_payload(
            payload,
            {
                "batch_id",
                "turn_id",
                "segment_id",
                "call_id",
                "items",
                "source",
            },
            event_type,
        )
        batch_id = _identity(row["batch_id"], "question_id")
        turn_id = _identity(row["turn_id"], "turn_id")
        segment_id = _identity(row["segment_id"], "segment_id")
        turn = turns.get(turn_id)
        segment = segments.get(segment_id)
        if (
            turn is None
            or segment is None
            or segment["turn_id"] != turn_id
            or turn["state"] not in {"streaming", "using_tool"}
            or segment["state"] not in {"streaming", "using_tool"}
        ):
            _fail("workbench_question_owner_not_live")
        if batch_id in questions:
            _fail("workbench_question_exists")
        if len(questions) >= MAX_PROJECTION_ROWS:
            _fail("workbench_projection_size_limit")
        unfinished = [
            item for item in questions.values() if item["state"] not in QUESTION_TERMINAL
        ]
        if unfinished:
            if not (
                len(unfinished) == 1
                and unfinished[0]["turn_id"] == turn_id
                and unfinished[0]["mode"] == "suspended"
                and unfinished[0]["state"] == "resuming"
            ):
                _fail("workbench_open_question_exists")
            # A second DSH question proves that the prior suspended answer was
            # accepted and execution advanced.  Settle that source question
            # inside the same append before exposing the new one.
            unfinished[0]["state"] = "settled"
            unfinished[0]["revision"] += 1
            unfinished[0]["updated_seq"] = event["seq"]
        question_items = _question_batch_items(row["items"])
        turn["state"] = "awaiting_input_live"
        turn["updated_seq"] = event["seq"]
        segment["state"] = "awaiting_input_live"
        segment["updated_seq"] = event["seq"]
        questions[batch_id] = {
            "question_id": batch_id,
            "batch_id": batch_id,
            "turn_id": turn_id,
            "segment_id": segment_id,
            "call_id": _identity(row["call_id"], "question_call_id"),
            "kind": "batch",
            "mode": "live",
            "state": "open",
            "prompt": "",
            "options": [],
            "items": question_items,
            "source": _identity(row["source"], "question_source"),
            "answer": None,
            "answer_receipt": None,
            "suspension": None,
            "resume": None,
            "revision": 0,
            "created_seq": event["seq"],
            "updated_seq": event["seq"],
        }
        return

    if event_type == "question.batch_opened":
        row = _exact_payload(
            payload,
            {
                "batch_id",
                "turn_id",
                "segment_id",
                "call_id",
                "items",
                "source",
            },
            event_type,
        )
        batch_id = _identity(row["batch_id"], "question_id")
        turn_id = _identity(row["turn_id"], "turn_id")
        segment_id = _identity(row["segment_id"], "segment_id")
        turn = turns.get(turn_id)
        segment = segments.get(segment_id)
        if (
            turn is None
            or segment is None
            or segment["turn_id"] != turn_id
            or turn["state"] != "awaiting_input_live"
            or segment["state"] != "awaiting_input_live"
        ):
            _fail("workbench_question_owner_not_waiting")
        if batch_id in questions:
            _fail("workbench_question_exists")
        if len(questions) >= MAX_PROJECTION_ROWS:
            _fail("workbench_projection_size_limit")
        if any(item["state"] not in QUESTION_TERMINAL for item in questions.values()):
            _fail("workbench_open_question_exists")
        questions[batch_id] = {
            "question_id": batch_id,
            "batch_id": batch_id,
            "turn_id": turn_id,
            "segment_id": segment_id,
            "call_id": _identity(row["call_id"], "question_call_id"),
            "kind": "batch",
            "mode": "live",
            "state": "open",
            "prompt": "",
            "options": [],
            "items": _question_batch_items(row["items"]),
            "source": _identity(row["source"], "question_source"),
            "answer": None,
            "answer_receipt": None,
            "suspension": None,
            "resume": None,
            "revision": 0,
            "created_seq": event["seq"],
            "updated_seq": event["seq"],
        }
        _touch_turn(state, turn_id, event["seq"])
        return

    if event_type == "question.opened":
        row = _exact_payload(
            payload,
            {
                "question_id",
                "turn_id",
                "segment_id",
                "kind",
                "prompt",
                "options",
                "source",
            },
            event_type,
        )
        question_id = _identity(row["question_id"], "question_id")
        turn_id = _identity(row["turn_id"], "turn_id")
        segment_id = _identity(row["segment_id"], "segment_id")
        kind = row["kind"]
        if kind not in QUESTION_KINDS - {"batch"}:
            _fail("workbench_question_kind_invalid")
        turn = turns.get(turn_id)
        segment = segments.get(segment_id)
        if (
            turn is None
            or segment is None
            or segment["turn_id"] != turn_id
            or turn["state"] != "awaiting_input_live"
            or segment["state"] != "awaiting_input_live"
        ):
            _fail("workbench_question_owner_not_waiting")
        if question_id in questions:
            _fail("workbench_question_exists")
        if len(questions) >= MAX_PROJECTION_ROWS:
            _fail("workbench_projection_size_limit")
        if any(item["state"] not in QUESTION_TERMINAL for item in questions.values()):
            _fail("workbench_open_question_exists")
        source = _identity(row["source"], "question_source")
        questions[question_id] = {
            "question_id": question_id,
            "turn_id": turn_id,
            "segment_id": segment_id,
            "kind": kind,
            "mode": "live",
            "state": "open",
            "prompt": _visible_text(row["prompt"], "question_prompt", MAX_TEXT_CHARS),
            "options": _question_options(row["options"], kind),
            "source": source,
            "answer": None,
            "answer_receipt": None,
            "suspension": None,
            "resume": None,
            "revision": 0,
            "created_seq": event["seq"],
            "updated_seq": event["seq"],
        }
        _touch_turn(state, turn_id, event["seq"])
        return

    if event_type == "question.runtime_suspended":
        row = _exact_payload(
            payload,
            {
                "question_id",
                "source_turn_id",
                "source_epoch_id",
                "source_segment_id",
                "runtime_incarnation",
                "call_id",
                "session_ref",
                "event_count",
            },
            event_type,
        )
        question_id = _identity(row["question_id"], "question_id")
        question = questions.get(question_id)
        if (
            question is None
            or question["state"] != "open"
            or question["mode"] != "live"
            or question.get("suspension") is not None
        ):
            _fail("workbench_question_not_live")
        turn = turns[question["turn_id"]]
        segment = segments[question["segment_id"]]
        source = {
            "source_turn_id": _identity(row["source_turn_id"], "source_turn_id"),
            "source_epoch_id": _identity(row["source_epoch_id"], "source_epoch_id"),
            "source_segment_id": _identity(
                row["source_segment_id"], "source_segment_id"
            ),
            "runtime_incarnation": _identity(
                row["runtime_incarnation"], "runtime_incarnation"
            ),
            "call_id": _identity(row["call_id"], "question_call_id"),
            "session_ref": _digest(row["session_ref"], "question_session_ref"),
            "event_count": _counter(row["event_count"], "question_event_count"),
        }
        if (
            source["event_count"] < 1
            or source["source_segment_id"] != question["segment_id"]
            or source["source_epoch_id"] != segment["epoch_id"]
            or source["runtime_incarnation"] != segment["runtime_incarnation"]
            or source["call_id"] != question.get("call_id")
            or turn["state"] != "awaiting_input_live"
            or segment["state"] != "awaiting_input_live"
        ):
            _fail("workbench_question_suspension_binding_invalid")
        # ``source_turn_id`` is the DSH execution identity.  The workbench
        # turn may intentionally be a longer-lived logical turn after one or
        # more question-resume segments, so it is not compared to turn_id.
        turn["state"] = "awaiting_input_suspended"
        turn["updated_seq"] = event["seq"]
        segment["state"] = "suspended"
        segment["updated_seq"] = event["seq"]
        segment["terminal_seq"] = event["seq"]
        question["mode"] = "suspended"
        question["suspension"] = source
        question["revision"] += 1
        question["updated_seq"] = event["seq"]
        return

    if event_type == "question.resume_segment_prepared":
        row = _exact_payload(
            payload,
            {
                "question_id",
                "answer_receipt",
                "answers_fingerprint",
                "request_id",
                "message_id",
                "runtime_turn_id",
                "epoch_id",
                "segment_id",
                "runtime_incarnation",
            },
            event_type,
        )
        question_id = _identity(row["question_id"], "question_id")
        question = questions.get(question_id)
        if (
            question is None
            or question["mode"] != "suspended"
            or question["state"] not in {"answer_submitting", "resuming"}
            or question.get("suspension") is None
        ):
            _fail("workbench_question_not_resumable")
        turn = turns[question["turn_id"]]
        if turn["state"] not in {"awaiting_input_suspended", "resuming"}:
            _fail("workbench_question_not_resumable")
        if any(item["state"] not in SEGMENT_TERMINAL for item in segments.values()):
            _fail("workbench_live_segment_exists")
        answer_receipt = _receipt(row["answer_receipt"], "answer_receipt")
        answers_fingerprint = _digest(
            row["answers_fingerprint"], "answers_fingerprint"
        )
        request_id = _identity(row["request_id"], "resume_request_id")
        message_id = _message_uuid(row["message_id"])
        runtime_turn_id = _identity(row["runtime_turn_id"], "runtime_turn_id")
        epoch_id = _identity(row["epoch_id"], "epoch_id")
        segment_id = _identity(row["segment_id"], "segment_id")
        runtime_incarnation = _identity(
            row["runtime_incarnation"], "runtime_incarnation"
        )
        if answer_receipt != question["answer_receipt"] or segment_id in segments:
            _fail("workbench_question_resume_binding_invalid")
        stable = {
            "answer_receipt": answer_receipt,
            "answers_fingerprint": answers_fingerprint,
            "request_id": request_id,
            "message_id": message_id,
            "runtime_turn_id": runtime_turn_id,
            "epoch_id": epoch_id,
        }
        resume = question.get("resume")
        if resume is None:
            resume = {
                **stable,
                "attempts": [],
                "ack": None,
                "result_status": None,
            }
            question["resume"] = resume
        elif any(resume.get(key) != value for key, value in stable.items()):
            _fail("workbench_question_resume_conflict")
        attempt = {
            "segment_id": segment_id,
            "runtime_incarnation": runtime_incarnation,
            "created_seq": event["seq"],
        }
        resume["attempts"].append(attempt)
        segments[segment_id] = {
            "segment_id": segment_id,
            "turn_id": question["turn_id"],
            "epoch_id": epoch_id,
            "runtime_incarnation": runtime_incarnation,
            "stage_epoch": len(resume["attempts"]) - 1,
            "state": "preparing",
            "created_seq": event["seq"],
            "updated_seq": event["seq"],
            "terminal_seq": None,
        }
        turn["segment_ids"].append(segment_id)
        turn["state"] = "resuming"
        turn["updated_seq"] = event["seq"]
        question["state"] = "resuming"
        question["revision"] += 1
        question["updated_seq"] = event["seq"]
        return

    if event_type == "question.resume_recovered":
        row = _exact_payload(payload, {"question_id", "segment_id"}, event_type)
        question_id = _identity(row["question_id"], "question_id")
        segment_id = _identity(row["segment_id"], "segment_id")
        question = questions.get(question_id)
        segment = segments.get(segment_id)
        if (
            question is None
            or question["mode"] != "suspended"
            or question["state"] != "resuming"
            or segment is None
            or segment["turn_id"] != question["turn_id"]
            or segment["state"] in SEGMENT_TERMINAL
        ):
            _fail("workbench_question_resume_recovery_invalid")
        resume = question.get("resume")
        if (
            not isinstance(resume, Mapping)
            or not resume.get("attempts")
            or resume["attempts"][-1]["segment_id"] != segment_id
        ):
            _fail("workbench_question_resume_recovery_invalid")
        segment["state"] = "interrupted"
        segment["updated_seq"] = event["seq"]
        segment["terminal_seq"] = event["seq"]
        turn = turns[question["turn_id"]]
        turn["state"] = "resuming"
        turn["updated_seq"] = event["seq"]
        question["revision"] += 1
        question["updated_seq"] = event["seq"]
        return

    if event_type == "question.resume_acknowledged":
        row = _exact_payload(
            payload,
            {
                "question_id",
                "answer_receipt",
                "status",
                "starts_turn",
                "reason",
                "result_status",
            },
            event_type,
        )
        question_id = _identity(row["question_id"], "question_id")
        question = questions.get(question_id)
        resume = None if question is None else question.get("resume")
        if (
            question is None
            or question["state"] not in {"resuming", "settled"}
            or not isinstance(resume, Mapping)
            or _receipt(row["answer_receipt"], "answer_receipt")
            != resume["answer_receipt"]
        ):
            _fail("workbench_question_resume_ack_invalid")
        status = row["status"]
        starts_turn = row["starts_turn"]
        reason = row["reason"]
        result_status = row["result_status"]
        if (
            status not in {"accepted", "already_accepted"}
            or type(starts_turn) is not bool
            or reason is not None
            or result_status not in {None, "completed", "cancelled", "error"}
            or starts_turn and result_status is None
            or not starts_turn and result_status is not None
        ):
            _fail("workbench_question_resume_ack_invalid")
        ack = {
            "status": status,
            "starts_turn": starts_turn,
            "reason": None,
        }
        if resume.get("ack") is not None and resume.get("ack") != ack:
            _fail("workbench_question_resume_ack_conflict")
        resume["ack"] = ack
        resume["result_status"] = result_status
        question["revision"] += 1
        question["updated_seq"] = event["seq"]
        _touch_turn(state, question["turn_id"], event["seq"])
        return

    if event_type == "question.suspended":
        row = _exact_payload(payload, {"question_id"}, event_type)
        question_id = _identity(row["question_id"], "question_id")
        question = questions.get(question_id)
        if (
            question is None
            or question["state"] != "open"
            or question["mode"] != "live"
        ):
            _fail("workbench_question_not_live")
        turn = turns[question["turn_id"]]
        segment = segments[question["segment_id"]]
        if (
            turn["state"] != "awaiting_input_suspended"
            or segment["state"] != "suspended"
        ):
            _fail("workbench_question_suspend_checkpoint_missing")
        question["mode"] = "suspended"
        question["revision"] += 1
        question["updated_seq"] = event["seq"]
        _touch_turn(state, question["turn_id"], event["seq"])
        return

    if event_type == "question.batch_answer_receipt_recorded":
        row = _exact_payload(
            payload,
            {"batch_id", "answers_fingerprint", "answer_receipt"},
            event_type,
        )
        batch_id = _identity(row["batch_id"], "question_id")
        question = questions.get(batch_id)
        if (
            question is None
            or question["kind"] != "batch"
            or question["state"] != "open"
            or question.get("content_authority") != "dsh.userQuestions"
        ):
            _fail("workbench_question_not_open")
        turn = turns[question["turn_id"]]
        segment = segments[question["segment_id"]]
        if question["mode"] == "live":
            if (
                turn["state"] != "awaiting_input_live"
                or segment["state"] != "awaiting_input_live"
            ):
                _fail("workbench_question_owner_not_waiting")
        elif (
            turn["state"] != "awaiting_input_suspended"
            or segment["state"] != "suspended"
        ):
            _fail("workbench_question_suspend_checkpoint_missing")
        answer_receipt = _receipt(row["answer_receipt"], "answer_receipt")
        if any(
            item["answer_receipt"] == answer_receipt
            for item in questions.values()
            if item["question_id"] != batch_id
        ):
            _fail("workbench_answer_receipt_conflict")
        question["state"] = "answer_submitting"
        question["answer_fingerprint"] = _digest(
            row["answers_fingerprint"], "answers_fingerprint"
        )
        question["answer_receipt"] = answer_receipt
        question["revision"] += 1
        question["updated_seq"] = event["seq"]
        _touch_turn(state, question["turn_id"], event["seq"])
        return

    if event_type == "question.batch_answer_submitted":
        row = _exact_payload(
            payload, {"batch_id", "answer", "answer_receipt"}, event_type
        )
        batch_id = _identity(row["batch_id"], "question_id")
        question = questions.get(batch_id)
        if question is None or question["kind"] != "batch" or question["state"] != "open":
            _fail("workbench_question_not_open")
        turn = turns[question["turn_id"]]
        segment = segments[question["segment_id"]]
        if question["mode"] == "live":
            if turn["state"] != "awaiting_input_live" or segment["state"] != "awaiting_input_live":
                _fail("workbench_question_owner_not_waiting")
        elif turn["state"] != "awaiting_input_suspended" or segment["state"] != "suspended":
            _fail("workbench_question_suspend_checkpoint_missing")
        answer = _question_batch_answer(row["answer"], items=question["items"])
        answer_receipt = _receipt(row["answer_receipt"], "answer_receipt")
        if any(
            item["answer_receipt"] == answer_receipt
            for item in questions.values()
            if item["question_id"] != batch_id
        ):
            _fail("workbench_answer_receipt_conflict")
        question["state"] = "answer_submitting"
        question["answer"] = answer
        question["answer_receipt"] = answer_receipt
        question["revision"] += 1
        question["updated_seq"] = event["seq"]
        _touch_turn(state, question["turn_id"], event["seq"])
        return

    if event_type == "question.answer_submitted":
        row = _exact_payload(
            payload, {"question_id", "answer", "answer_receipt"}, event_type
        )
        question_id = _identity(row["question_id"], "question_id")
        question = questions.get(question_id)
        if question is None or question["state"] != "open":
            _fail("workbench_question_not_open")
        turn = turns[question["turn_id"]]
        segment = segments[question["segment_id"]]
        if question["mode"] == "live":
            if (
                turn["state"] != "awaiting_input_live"
                or segment["state"] != "awaiting_input_live"
            ):
                _fail("workbench_question_owner_not_waiting")
        elif (
            turn["state"] != "awaiting_input_suspended"
            or segment["state"] != "suspended"
        ):
            _fail("workbench_question_suspend_checkpoint_missing")
        answer = _question_answer(
            row["answer"], kind=question["kind"], options=question["options"]
        )
        answer_receipt = _receipt(row["answer_receipt"], "answer_receipt")
        if any(
            item["answer_receipt"] == answer_receipt
            for item in questions.values()
            if item["question_id"] != question_id
        ):
            _fail("workbench_answer_receipt_conflict")
        question["state"] = "answer_submitting"
        question["answer"] = answer
        question["answer_receipt"] = answer_receipt
        question["revision"] += 1
        question["updated_seq"] = event["seq"]
        _touch_turn(state, question["turn_id"], event["seq"])
        return

    if event_type == "question.transitioned":
        row = _exact_payload(
            payload, {"question_id", "from_state", "to_state", "reason"}, event_type
        )
        question_id = _identity(row["question_id"], "question_id")
        question = questions.get(question_id)
        if question is None:
            _fail("workbench_question_missing")
        previous = row["from_state"]
        target_state = row["to_state"]
        if previous not in QUESTION_STATES or target_state not in QUESTION_STATES:
            _fail("workbench_question_state_invalid")
        if question["state"] != previous:
            _fail("workbench_question_state_conflict")
        _require_transition(
            previous,
            target_state,
            QUESTION_TRANSITIONS,
            "workbench_question_transition_invalid",
        )
        _optional_code(row["reason"], "question_reason")
        if target_state == "resuming" and question["mode"] != "suspended":
            _fail("workbench_question_not_suspended")
        question["state"] = target_state
        question["revision"] += 1
        question["updated_seq"] = event["seq"]
        _touch_turn(state, question["turn_id"], event["seq"])
        return

    if event_type == "inbox.enqueued":
        row = _exact_payload(
            payload,
            {
                "inbox_id",
                "kind",
                "message",
                "document_binding",
                "target",
                "client_request_id",
            },
            event_type,
        )
        inbox_id = _identity(row["inbox_id"], "inbox_id")
        kind = row["kind"]
        if kind not in INBOX_KINDS:
            _fail("workbench_inbox_kind_invalid")
        if inbox_id in inbox:
            _fail("workbench_inbox_exists")
        if len(inbox) >= MAX_PROJECTION_ROWS:
            _fail("workbench_projection_size_limit")
        target = _target(row["target"], required=kind != "queue")
        if kind == "queue" and target is not None:
            _fail("workbench_inbox_target_invalid")
        if target is not None:
            target_turn, target_segment = _target_segment(
                state,
                target,
                require_steerable=kind == "steer",
            )
            if kind == "stop_and_send" and (
                target_turn["state"] in TURN_TERMINAL
                or target_segment["state"] in SEGMENT_TERMINAL
            ):
                _fail("workbench_inbox_target_not_live")
        client_request_id = _identity(row["client_request_id"], "client_request_id")
        if any(
            item["client_request_id"] == client_request_id for item in inbox.values()
        ):
            _fail("workbench_client_request_id_conflict")
        inbox[inbox_id] = {
            "inbox_id": inbox_id,
            "kind": kind,
            "message": _visible_text(row["message"], "inbox_message", MAX_TEXT_CHARS),
            "document_binding": _document_binding(row["document_binding"]),
            "target": target,
            "client_request_id": client_request_id,
            "state": "queued",
            "revision": 0,
            "admission_receipt": None,
            "runtime_ack": None,
            "reason": None,
            "created_seq": event["seq"],
            "updated_seq": event["seq"],
        }
        state["inbox_order"].append(inbox_id)
        if target is not None:
            _touch_turn(state, target["turn_id"], event["seq"])
        return

    if event_type == "inbox.steer_acknowledged":
        row = _exact_payload(
            payload,
            {
                "inbox_id",
                "expected_item_revision",
                "ack_status",
                "message_id",
                "dsh_message_id",
                "admission_receipt",
            },
            event_type,
        )
        inbox_id = _identity(row["inbox_id"], "inbox_id")
        item = inbox.get(inbox_id)
        if item is None:
            _fail("workbench_inbox_missing")
        expected_revision = _counter(
            row["expected_item_revision"], "inbox_item_revision"
        )
        if item["revision"] != expected_revision:
            _fail("workbench_inbox_item_revision_conflict")
        if item["kind"] != "steer" or item["state"] != "promoting":
            _fail("workbench_steer_ack_state_invalid")
        ack_status = row["ack_status"]
        if ack_status not in {"accepted", "already_accepted"}:
            _fail("workbench_steer_ack_status_invalid")
        message_id = _identity(row["message_id"], "message_id")
        dsh_message_id = _identity(row["dsh_message_id"], "dsh_message_id")
        admission_receipt = _receipt(
            row["admission_receipt"], "inbox_admission_receipt"
        )
        if any(
            other["admission_receipt"] == admission_receipt
            for other in inbox.values()
            if other["inbox_id"] != inbox_id
        ):
            _fail("workbench_inbox_admission_receipt_conflict")
        if any(
            other.get("runtime_ack") is not None
            and other["runtime_ack"]["dsh_message_id"] == dsh_message_id
            for other in inbox.values()
            if other["inbox_id"] != inbox_id
        ):
            _fail("workbench_steer_ack_conflict")
        item["state"] = "admitted"
        item["revision"] += 1
        item["admission_receipt"] = admission_receipt
        item["runtime_ack"] = {
            "status": ack_status,
            "message_id": message_id,
            "dsh_message_id": dsh_message_id,
        }
        item["reason"] = None
        item["updated_seq"] = event["seq"]
        target = item["target"]
        if target is not None:
            _touch_turn(state, target["turn_id"], event["seq"])
        return

    if event_type == "inbox.transitioned":
        row = _exact_payload(
            payload,
            {
                "inbox_id",
                "expected_item_revision",
                "from_state",
                "to_state",
                "reason",
                "admission_receipt",
            },
            event_type,
        )
        inbox_id = _identity(row["inbox_id"], "inbox_id")
        item = inbox.get(inbox_id)
        if item is None:
            _fail("workbench_inbox_missing")
        expected_revision = _counter(
            row["expected_item_revision"], "inbox_item_revision"
        )
        if item["revision"] != expected_revision:
            _fail("workbench_inbox_item_revision_conflict")
        previous = row["from_state"]
        target_state = row["to_state"]
        if previous not in INBOX_STATES or target_state not in INBOX_STATES:
            _fail("workbench_inbox_state_invalid")
        if item["state"] != previous:
            _fail("workbench_inbox_state_conflict")
        _require_transition(
            previous,
            target_state,
            INBOX_TRANSITIONS,
            "workbench_inbox_transition_invalid",
        )
        reason = _optional_code(row["reason"], "inbox_reason")
        if target_state == "promoting":
            if item["kind"] == "steer":
                _target_segment(state, item["target"], require_steerable=True)
            elif item["kind"] == "stop_and_send":
                if any(
                    other["inbox_id"] != inbox_id
                    and other["kind"] in {"queue", "stop_and_send"}
                    and other["state"] == "promoting"
                    for other in inbox.values()
                ):
                    _fail("workbench_inbox_promotion_exists")
                target_turn, target_segment = _target_segment(
                    state, item["target"], require_steerable=False
                )
                if (
                    target_turn["state"] not in _STOP_AND_SEND_SAFE_TERMINALS
                    or target_segment["state"] not in SEGMENT_TERMINAL
                ):
                    _fail("workbench_stop_and_send_not_settled")
            else:
                if any(
                    other["inbox_id"] != inbox_id
                    and other["kind"] in {"queue", "stop_and_send"}
                    and other["state"] == "promoting"
                    for other in inbox.values()
                ):
                    _fail("workbench_inbox_promotion_exists")
                if any(turn["state"] not in TURN_TERMINAL for turn in turns.values()):
                    _fail("workbench_queue_turn_still_active")
        admission_receipt = row["admission_receipt"]
        if target_state == "admitted":
            admission_receipt = _receipt(admission_receipt, "inbox_admission_receipt")
            if item["kind"] in {"queue", "stop_and_send"}:
                linked_turns = [
                    turn
                    for turn in turns.values()
                    if turn["predecessor_inbox_id"] == inbox_id
                    and turn["state"] not in TURN_TERMINAL
                ]
                if len(linked_turns) != 1:
                    _fail("workbench_inbox_admission_turn_missing")
            if any(
                other["admission_receipt"] == admission_receipt
                for other in inbox.values()
                if other["inbox_id"] != inbox_id
            ):
                _fail("workbench_inbox_admission_receipt_conflict")
        elif admission_receipt is not None:
            _fail("workbench_inbox_admission_receipt_invalid")
        item["state"] = target_state
        item["revision"] += 1
        item["reason"] = reason
        if admission_receipt is not None:
            item["admission_receipt"] = admission_receipt
        item["updated_seq"] = event["seq"]
        related_turn_ids = {
            turn["turn_id"]
            for turn in turns.values()
            if turn["predecessor_inbox_id"] == inbox_id
        }
        if item["target"] is not None:
            related_turn_ids.add(item["target"]["turn_id"])
        for related_turn_id in related_turn_ids:
            _touch_turn(state, related_turn_id, event["seq"])
        return

    if event_type == "inbox.revised":
        row = _exact_payload(
            payload,
            {
                "inbox_id",
                "expected_item_revision",
                "message",
                "document_binding",
                "target",
            },
            event_type,
        )
        inbox_id = _identity(row["inbox_id"], "inbox_id")
        item = inbox.get(inbox_id)
        if item is None:
            _fail("workbench_inbox_missing")
        expected_revision = _counter(
            row["expected_item_revision"], "inbox_item_revision"
        )
        if item["revision"] != expected_revision:
            _fail("workbench_inbox_item_revision_conflict")
        if item["state"] not in {
            "queued",
            "held_action_required",
            "held_failure",
            "needs_rebind",
        }:
            _fail("workbench_inbox_not_editable")
        previous_target = item["target"]
        target = _target(row["target"], required=item["kind"] != "queue")
        if item["kind"] == "queue" and target is not None:
            _fail("workbench_inbox_target_invalid")
        if target is not None:
            _target_segment(
                state,
                target,
                require_steerable=item["kind"] == "steer",
            )
        item["message"] = _visible_text(row["message"], "inbox_message", MAX_TEXT_CHARS)
        item["document_binding"] = _document_binding(row["document_binding"])
        item["target"] = target
        item["revision"] += 1
        item["updated_seq"] = event["seq"]
        touched_turn_ids = {
            value["turn_id"] for value in (previous_target, target) if value is not None
        }
        for target_turn_id in touched_turn_ids:
            _touch_turn(state, target_turn_id, event["seq"])
        return

    if event_type == "inbox.batch_rebound":
        row = _exact_payload(payload, {"items", "rebind_receipt"}, event_type)
        raw_items = row["items"]
        if (
            not isinstance(raw_items, list)
            or not 1 <= len(raw_items) <= MAX_PROJECTION_ROWS
        ):
            _fail("workbench_inbox_rebind_items_invalid")
        checked: list[
            tuple[dict[str, Any], int, dict[str, Any], dict[str, Any]]
        ] = []
        seen: set[str] = set()
        for value in raw_items:
            if not isinstance(value, Mapping) or set(value) != {
                "inbox_id",
                "expected_item_revision",
                "old_document_binding",
                "new_document_binding",
            }:
                _fail("workbench_inbox_rebind_items_invalid")
            inbox_id = _identity(value["inbox_id"], "inbox_id")
            expected_item_revision = _counter(
                value["expected_item_revision"], "inbox_item_revision"
            )
            old_binding = _document_binding(value["old_document_binding"])
            new_binding = _document_binding(value["new_document_binding"])
            item = inbox.get(inbox_id)
            if inbox_id in seen or item is None:
                _fail("workbench_inbox_rebind_items_invalid")
            seen.add(inbox_id)
            if item["revision"] != expected_item_revision:
                _fail("workbench_inbox_item_revision_conflict")
            if item["state"] not in {
                "queued",
                "held_action_required",
                "held_failure",
                "needs_rebind",
            }:
                _fail("workbench_inbox_rebind_busy")
            if item["document_binding"] != old_binding:
                _fail("workbench_inbox_rebind_source_conflict")
            if (
                new_binding["document_id"] != old_binding["document_id"]
                or new_binding["document_lineage_epoch"]
                <= old_binding["document_lineage_epoch"]
            ):
                _fail("workbench_inbox_rebind_target_invalid")
            checked.append(
                (item, expected_item_revision, old_binding, new_binding)
            )
        rebind_receipt = _receipt(row["rebind_receipt"], "inbox_rebind_receipt")
        if any(
            event["type"] == "inbox.batch_rebound"
            and event["payload"]["rebind_receipt"] == rebind_receipt
            for event in state["mutations"].values()
        ):
            _fail("workbench_inbox_rebind_receipt_conflict")
        for item, _expected_revision, _old_binding, new_binding in checked:
            item["document_binding"] = new_binding
            item["state"] = "needs_rebind"
            item["revision"] += 1
            item["reason"] = "document_lineage_changed"
            item["updated_seq"] = event["seq"]
            target = item["target"]
            if target is not None:
                _touch_turn(state, target["turn_id"], event["seq"])
        return

    if event_type == "inbox.reordered":
        row = _exact_payload(payload, {"inbox_ids"}, event_type)
        ids = _identity_list(row["inbox_ids"], "inbox_id", maximum=MAX_PROJECTION_ROWS)
        reorderable = [
            item_id
            for item_id in state["inbox_order"]
            if inbox[item_id]["state"]
            in {"queued", "held_action_required", "held_failure", "needs_rebind"}
        ]
        if set(ids) != set(reorderable):
            _fail("workbench_inbox_reorder_incomplete")
        iterator = iter(ids)
        state["inbox_order"] = [
            next(iterator) if item_id in reorderable else item_id
            for item_id in state["inbox_order"]
        ]
        return

    _fail("workbench_event_type_invalid")


def _validate_event(value: object, *, expected_seq: int) -> dict[str, Any]:
    fields = {"seq", "event_id", "mutation_id", "type", "occurred_at", "payload"}
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("workbench_event_invalid")
    row = copy.deepcopy(dict(value))
    if _counter(row["seq"], "event_seq") != expected_seq:
        _fail("workbench_event_sequence_invalid")
    _event_id(row["event_id"])
    _identity(row["mutation_id"], "mutation_id")
    event_type = _identity(row["type"], "event_type")
    if event_type == "outline.node":
        _fail("workbench_legacy_outline_event_unsupported")
    _timestamp(row["occurred_at"])
    if len(json_bytes(row)) > MAX_EVENT_BYTES:
        _fail("workbench_event_size_limit")
    return row


def _replay(events: object) -> dict[str, Any]:
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        _fail("workbench_events_invalid")
    state = _empty_state()
    event_ids: set[str] = set()
    for expected_seq, value in enumerate(events, 1):
        event = _validate_event(value, expected_seq=expected_seq)
        if event["event_id"] in event_ids:
            _fail("workbench_event_id_conflict")
        event_ids.add(event["event_id"])
        mutation_id = event["mutation_id"]
        if mutation_id in state["mutations"]:
            _fail("workbench_mutation_id_conflict")
        _apply_event(state, event)
        state["mutations"][mutation_id] = event
    return state


def _ledger(value: object, *, conversation_id: str) -> dict[str, Any]:
    fields = {"contract", "conversation_id", "revision", "source_cursor", "events"}
    if not isinstance(value, Mapping) or set(value) != fields:
        _fail("workbench_ledger_invalid")
    row = copy.deepcopy(dict(value))
    if row["contract"] != CONTRACT:
        _fail("workbench_contract_invalid")
    if _identity(row["conversation_id"], "conversation_id") != conversation_id:
        _fail("workbench_conversation_id_mismatch")
    revision = _counter(row["revision"], "revision")
    source_cursor = _counter(row["source_cursor"], "source_cursor")
    if revision != source_cursor or revision != len(row["events"]):
        _fail("workbench_revision_invalid")
    _replay(row["events"])
    return row


def _turn_outline(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for turn in sorted(state["turns"].values(), key=lambda item: item["created_seq"]):
        turn_id = turn["turn_id"]
        markers = [f"turn:{turn['state']}"]
        related_questions = sorted(
            (
                question
                for question in state["questions"].values()
                if question["turn_id"] == turn_id
            ),
            key=lambda item: item["created_seq"],
        )
        for question in related_questions:
            marker = f"question:{question['state']}"
            if marker not in markers and len(markers) < MAX_MARKERS:
                markers.append(marker)
        predecessor_inbox_id = turn["predecessor_inbox_id"]
        if predecessor_inbox_id is not None:
            predecessor = state["inbox"][predecessor_inbox_id]
            marker = f"inbox:{predecessor['state']}"
            if marker not in markers and len(markers) < MAX_MARKERS:
                markers.append(marker)
        for inbox_id in state["inbox_order"]:
            item = state["inbox"][inbox_id]
            target = item["target"]
            if target is None or target["turn_id"] != turn_id:
                continue
            marker = f"inbox:{item['state']}"
            if marker not in markers and len(markers) < MAX_MARKERS:
                markers.append(marker)
        nodes = [
            node for node in state["timeline"] if node["turn_id"] == turn_id
        ]
        excerpts: list[dict[str, str]] = []
        for node in nodes:
            visible = node.get("text") or node.get("label")
            if not visible:
                continue
            excerpts.append(
                {
                    "node_id": node["node_id"],
                    "kind": node["kind"],
                    "text": visible[:MAX_SUMMARY_CHARS],
                }
            )
            if len(excerpts) >= 3:
                break
        result.append(
            {
                "turn_id": turn_id,
                "state": turn["state"],
                "seq_start": turn["created_seq"],
                "seq_end": turn["updated_seq"],
                "segment_count": len(turn["segment_ids"]),
                "node_count": len(nodes),
                "markers": markers,
                "excerpts_completeness": "complete" if nodes else "partial",
                "excerpts": excerpts,
            }
        )
    return result


class NativeWorkbenchLedger:
    """One conversation's append-only workbench fact ledger."""

    def __init__(
        self,
        root: str | Path,
        *,
        conversation_id: str,
        clock: Callable[[], str] = _now,
        event_id_factory: Callable[[], str] = _new_event_id,
        receipt_factory: Callable[[], str] = _new_receipt,
    ) -> None:
        self.root = Path(root).absolute()
        self.conversation_id = _identity(conversation_id, "conversation_id")
        try:
            self.path = safe_path(self.root, LEDGER_FILE)
            self.lock_path = safe_path(self.root, LOCK_FILE)
        except (OSError, PlanError):
            _fail("workbench_storage_invalid")
        self._clock = clock
        self._event_id_factory = event_id_factory
        self._receipt_factory = receipt_factory

    def _checked_path(self, name: str) -> Path:
        try:
            return safe_path(self.root, name)
        except (OSError, PlanError):
            _fail("workbench_storage_invalid")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        try:
            lock_path = self._checked_path(LOCK_FILE)
            self._checked_path(LEDGER_FILE)
            with file_lock(lock_path):
                if self._checked_path(LOCK_FILE) != lock_path:
                    _fail("workbench_storage_invalid")
                if self._checked_path(LEDGER_FILE) != self.path:
                    _fail("workbench_storage_invalid")
                yield
        except NativeWorkbenchLedgerError:
            raise
        except (OSError, PlanError):
            _fail("workbench_storage_invalid")

    def _empty_ledger(self) -> dict[str, Any]:
        return {
            "contract": CONTRACT,
            "conversation_id": self.conversation_id,
            "revision": 0,
            "source_cursor": 0,
            "events": [],
        }

    def _read_unlocked(self) -> dict[str, Any]:
        if self._checked_path(LEDGER_FILE) != self.path:
            _fail("workbench_storage_invalid")
        try:
            if not self.path.exists():
                return self._empty_ledger()
            if self.path.is_symlink() or self.path.stat().st_size > MAX_LEDGER_BYTES:
                _fail("workbench_storage_invalid")
            value = json.loads(
                self.path.read_text(encoding="utf-8"),
                object_pairs_hook=_strict_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
            )
        except NativeWorkbenchLedgerError:
            raise
        except (OSError, UnicodeError, ValueError, RecursionError):
            _fail("workbench_storage_invalid")
        return _ledger(value, conversation_id=self.conversation_id)

    def _write_unlocked(self, value: Mapping[str, Any]) -> None:
        checked = _ledger(value, conversation_id=self.conversation_id)
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            write_owned(self.root, LEDGER_FILE, checked, MAX_LEDGER_BYTES)
        except PlanError:
            _fail("workbench_storage_invalid")

    @staticmethod
    def _receipt_for(event: Mapping[str, Any], revision: int) -> dict[str, Any]:
        result = {
            "status": "accepted",
            "ledger_revision": revision,
            "source_cursor": event["seq"],
            "projection_revision": revision,
            "event_id": event["event_id"],
            "mutation_id": event["mutation_id"],
            "event_type": event["type"],
        }
        if event["type"] in {
            "question.answer_submitted",
            "question.batch_answer_submitted",
            "question.batch_answer_receipt_recorded",
        }:
            result["answer_receipt"] = event["payload"]["answer_receipt"]
        if (
            event["type"] == "inbox.transitioned"
            and event["payload"]["to_state"] == "admitted"
        ):
            result["admission_receipt"] = event["payload"]["admission_receipt"]
        if event["type"] == "inbox.steer_acknowledged":
            result["admission_receipt"] = event["payload"]["admission_receipt"]
        if event["type"] == "inbox.batch_rebound":
            result["rebind_receipt"] = event["payload"]["rebind_receipt"]
        return result

    def _idempotent_receipt(
        self,
        *,
        mutation_id: str,
        event_type: str,
        expected_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Return an accepted retry only when its immutable request matches.

        Transition payloads contain a derived ``from_state`` and some events
        contain a Host-generated receipt.  A successful retry must therefore
        be recognized before deriving those fields from the already-advanced
        projection.  Callers pass only request-owned fields here.
        """

        mutation_id = _identity(mutation_id, "mutation_id")
        event_type = _identity(event_type, "event_type")
        with self._locked():
            ledger = self._read_unlocked()
        state = _replay(ledger["events"])
        existing = state["mutations"].get(mutation_id)
        if existing is None:
            return None
        if existing["type"] != event_type or any(
            existing["payload"].get(key) != value
            for key, value in expected_payload.items()
        ):
            _fail("workbench_mutation_conflict")
        return {
            **self._receipt_for(existing, existing["seq"]),
            "status": "already_accepted",
        }

    def _append(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        _counter(expected_revision, "expected_revision")
        mutation_id = _identity(mutation_id, "mutation_id")
        event_type = _identity(event_type, "event_type")
        proposed_payload = copy.deepcopy(dict(payload))
        with self._locked():
            ledger = self._read_unlocked()
            state = _replay(ledger["events"])
            existing = state["mutations"].get(mutation_id)
            if existing is not None:
                comparable = copy.deepcopy(proposed_payload)
                for generated in (
                    "answer_receipt",
                    "admission_receipt",
                    "rebind_receipt",
                ):
                    if (
                        comparable.get(generated) is None
                        and generated in existing["payload"]
                    ):
                        comparable[generated] = existing["payload"][generated]
                if existing["type"] != event_type or existing["payload"] != comparable:
                    _fail("workbench_mutation_conflict")
                return {
                    **self._receipt_for(existing, existing["seq"]),
                    "status": "already_accepted",
                }
            if ledger["revision"] != expected_revision:
                _fail("workbench_stale_revision")
            if len(ledger["events"]) >= MAX_EVENTS:
                _fail("workbench_event_log_full")
            if (
                event_type in {
                    "question.answer_submitted",
                    "question.batch_answer_submitted",
                    "question.batch_answer_receipt_recorded",
                }
                and proposed_payload.get("answer_receipt") is None
            ):
                proposed_payload["answer_receipt"] = self._receipt_factory()
                _receipt(proposed_payload["answer_receipt"], "answer_receipt")
            if (
                event_type == "inbox.transitioned"
                and proposed_payload.get("to_state") == "admitted"
                and proposed_payload.get("admission_receipt") is None
            ):
                proposed_payload["admission_receipt"] = self._receipt_factory()
                _receipt(
                    proposed_payload["admission_receipt"],
                    "inbox_admission_receipt",
                )
            if (
                event_type == "inbox.steer_acknowledged"
                and proposed_payload.get("admission_receipt") is None
            ):
                proposed_payload["admission_receipt"] = self._receipt_factory()
                _receipt(
                    proposed_payload["admission_receipt"],
                    "inbox_admission_receipt",
                )
            if (
                event_type == "inbox.batch_rebound"
                and proposed_payload.get("rebind_receipt") is None
            ):
                proposed_payload["rebind_receipt"] = self._receipt_factory()
                _receipt(
                    proposed_payload["rebind_receipt"],
                    "inbox_rebind_receipt",
                )
            event_id = self._event_id_factory()
            _event_id(event_id)
            if any(item["event_id"] == event_id for item in ledger["events"]):
                _fail("workbench_event_id_generation_failed")
            event = {
                "seq": ledger["revision"] + 1,
                "event_id": event_id,
                "mutation_id": mutation_id,
                "type": event_type,
                "occurred_at": self._clock(),
                "payload": proposed_payload,
            }
            event = _validate_event(event, expected_seq=event["seq"])
            _apply_event(state, event)
            ledger["events"].append(event)
            ledger["revision"] = event["seq"]
            ledger["source_cursor"] = event["seq"]
            self._write_unlocked(ledger)
            return self._receipt_for(event, ledger["revision"])

    def read(self) -> dict[str, Any]:
        with self._locked():
            return copy.deepcopy(self._read_unlocked())

    def _projection_from(
        self, ledger: Mapping[str, Any], state: Mapping[str, Any]
    ) -> dict[str, Any]:
        ordered_inbox = [state["inbox"][item] for item in state["inbox_order"]]
        if any(
            len(group) > MAX_PROJECTION_ROWS
            for group in (
                state["turns"],
                state["segments"],
                state["questions"],
                state["inbox"],
            )
        ):
            _fail("workbench_projection_size_limit")
        timeline_by_turn: dict[str, list[Mapping[str, Any]]] = {}
        terminal_operation_components: dict[
            tuple[str, str, str, str], int
        ] = {}
        for node in state["timeline"]:
            timeline_by_turn.setdefault(node["turn_id"], []).append(node)
            if (
                node["operation_id"] is not None
                and node["state"]
                not in {None, "preparing", "running", "streaming"}
            ):
                component = (
                    node["turn_id"],
                    node["operation_id"],
                    node["kind"],
                    node["category"],
                )
                terminal_operation_components[component] = max(
                    terminal_operation_components.get(component, 0),
                    node["source_seq"],
                )
        # DSH is the durable authority for generic user/assistant/reasoning
        # and tool history.  Workbench completeness therefore covers only
        # App-domain overlays; absence of a duplicate transcript must never
        # downgrade an otherwise complete domain projection.
        timeline_complete = all(
            any(
                node["kind"] == "question"
                and node["operation_id"] == question_id
                for node in timeline_by_turn.get(question["turn_id"], [])
            )
            for question_id, question in state["questions"].items()
        ) and all(
            turn["state"] not in TURN_TERMINAL
            or all(
                terminal_operation_components.get((
                    node["turn_id"],
                    node["operation_id"],
                    node["kind"],
                    node["category"],
                ), 0) > node["source_seq"]
                for node in timeline_by_turn.get(turn_id, [])
                if node["operation_id"] is not None
                and node["kind"] in {"research", "candidate", "app_action"}
                and node["state"] in {"preparing", "running", "streaming"}
            )
            for turn_id, turn in state["turns"].items()
        )
        return {
                "contract": PROJECTION_CONTRACT,
                "projection_kind": "workbench_foundation",
                "conversation_id": self.conversation_id,
                "source_cursor": ledger["source_cursor"],
                "projection_revision": ledger["revision"],
                "completeness": {
                    "state": "complete" if timeline_complete else "partial",
                    "partial_fields": (
                        [] if timeline_complete else ["timeline"]
                    ),
                    "reason": (
                        None
                        if timeline_complete
                        else "durable_timeline_not_available_for_legacy_turn"
                    ),
                },
                "turns": sorted(
                    state["turns"].values(), key=lambda item: item["created_seq"]
                ),
                "segments": sorted(
                    state["segments"].values(), key=lambda item: item["created_seq"]
                ),
                "questions": sorted(
                    state["questions"].values(), key=lambda item: item["created_seq"]
                ),
                "inbox": ordered_inbox,
                "turn_outline": _turn_outline(state),
            }

    def projection(self) -> dict[str, Any]:
        with self._locked():
            ledger = self._read_unlocked()
        state = _replay(ledger["events"])
        return copy.deepcopy(self._projection_from(ledger, state))

    def display_snapshot(self) -> dict[str, Any]:
        """Read one revision-consistent projection and display timeline.

        The Renderer-facing views must never join a projection from one ledger
        revision to timeline nodes from another.  Keeping this read under one
        file lock also means readers can page a 10,000-node history without
        making the timeline a second durable authority.
        """

        with self._locked():
            ledger = self._read_unlocked()
        state = _replay(ledger["events"])
        return copy.deepcopy(
            {
                "ledger": ledger,
                "projection": self._projection_from(ledger, state),
                "timeline": state["timeline"],
            }
        )

    def events_after(self, after_seq: int, *, limit: int = 256) -> dict[str, Any]:
        after_seq = _counter(after_seq, "after_seq")
        if type(limit) is not int or not 1 <= limit <= 256:
            _fail("workbench_event_limit_invalid")
        with self._locked():
            ledger = self._read_unlocked()
        if after_seq > ledger["source_cursor"]:
            _fail("workbench_after_seq_ahead")
        events = ledger["events"]
        first_available_seq = (
            events[0]["seq"] if events else ledger["source_cursor"] + 1
        )
        if after_seq < first_available_seq - 1:
            _fail("workbench_cursor_trimmed")
        # A cursor is a Host sequence, not a Python list offset.  They happen
        # to be equal before retention trimming, but relying on that equality
        # would skip every retained row after the first compaction.
        rows = [item for item in events if item["seq"] > after_seq][:limit]
        return {
            "contract": CONTRACT,
            "conversation_id": self.conversation_id,
            "requested_after_seq": after_seq,
            "next_seq": rows[-1]["seq"] if rows else after_seq,
            "first_available_seq": first_available_seq,
            "source_cursor": ledger["source_cursor"],
            "events": copy.deepcopy(rows),
        }

    def timeline_nodes(self) -> list[dict[str, Any]]:
        """Return detached, already-redacted display facts for Host projections."""

        with self._locked():
            ledger = self._read_unlocked()
        return copy.deepcopy(_replay(ledger["events"])["timeline"])

    def record_timeline_node(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        node: Mapping[str, Any],
    ) -> dict[str, Any]:
        checked = _timeline_node(node)
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="timeline.node_recorded",
            payload={"node": checked},
        )

    def create_turn(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        turn_id: str,
        predecessor_inbox_id: str | None = None,
    ) -> dict[str, Any]:
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="turn.created",
            payload={
                "turn_id": _identity(turn_id, "turn_id"),
                "predecessor_inbox_id": _optional_identity(
                    predecessor_inbox_id, "predecessor_inbox_id"
                ),
            },
        )

    def transition_turn(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        turn_id: str,
        to_state: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        checked_turn_id = _identity(turn_id, "turn_id")
        checked_reason = _optional_code(reason, "turn_reason")
        if to_state not in TURN_STATES:
            _fail("workbench_turn_state_invalid")
        retried = self._idempotent_receipt(
            mutation_id=mutation_id,
            event_type="turn.transitioned",
            expected_payload={
                "turn_id": checked_turn_id,
                "to_state": to_state,
                "reason": checked_reason,
            },
        )
        if retried is not None:
            return retried
        projection = self.projection()
        turn = next(
            (item for item in projection["turns"] if item["turn_id"] == turn_id),
            None,
        )
        if turn is None:
            _fail("workbench_turn_missing")
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="turn.transitioned",
            payload={
                "turn_id": checked_turn_id,
                "from_state": turn["state"],
                "to_state": to_state,
                "reason": checked_reason,
            },
        )

    def start_segment(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        turn_id: str,
        segment_id: str,
        epoch_id: str,
        runtime_incarnation: str,
        stage_epoch: int,
    ) -> dict[str, Any]:
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="segment.started",
            payload={
                "turn_id": _identity(turn_id, "turn_id"),
                "segment_id": _identity(segment_id, "segment_id"),
                "epoch_id": _identity(epoch_id, "epoch_id"),
                "runtime_incarnation": _identity(
                    runtime_incarnation, "runtime_incarnation"
                ),
                "stage_epoch": _counter(stage_epoch, "stage_epoch"),
            },
        )

    def transition_segment(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        segment_id: str,
        to_state: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        checked_segment_id = _identity(segment_id, "segment_id")
        checked_reason = _optional_code(reason, "segment_reason")
        if to_state not in SEGMENT_STATES:
            _fail("workbench_segment_state_invalid")
        retried = self._idempotent_receipt(
            mutation_id=mutation_id,
            event_type="segment.transitioned",
            expected_payload={
                "segment_id": checked_segment_id,
                "to_state": to_state,
                "reason": checked_reason,
            },
        )
        if retried is not None:
            return retried
        projection = self.projection()
        segment = next(
            (
                item
                for item in projection["segments"]
                if item["segment_id"] == segment_id
            ),
            None,
        )
        if segment is None:
            _fail("workbench_segment_missing")
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="segment.transitioned",
            payload={
                "segment_id": checked_segment_id,
                "from_state": segment["state"],
                "to_state": to_state,
                "reason": checked_reason,
            },
        )

    def wait_and_open_question_reference(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        batch_id: str,
        turn_id: str,
        segment_id: str,
        dsh_turn_id: str,
        dsh_epoch_id: str,
        call_id: str,
        question_ids: Sequence[str],
        question_option_ids: Sequence[Sequence[str]],
        question_fingerprint: str,
        approval_scope: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Persist only Host delivery facts for one DSH-owned question call."""

        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="question.batch_reference_wait_opened",
            payload={
                "batch_id": _identity(batch_id, "question_id"),
                "turn_id": _identity(turn_id, "turn_id"),
                "segment_id": _identity(segment_id, "segment_id"),
                "dsh_turn_id": _identity(dsh_turn_id, "dsh_turn_id"),
                "dsh_epoch_id": _identity(dsh_epoch_id, "dsh_epoch_id"),
                "call_id": _identity(call_id, "question_call_id"),
                "question_ids": _identity_list(
                    list(question_ids),
                    "batch_question_id",
                    maximum=MAX_QUESTION_BATCH_ITEMS,
                    allow_empty=False,
                ),
                "question_option_ids": _question_option_id_matrix(
                    [list(row) for row in question_option_ids],
                    question_count=len(question_ids),
                ),
                "question_fingerprint": _digest(
                    question_fingerprint, "question_fingerprint"
                ),
                "approval_scope": _optional_code(
                    approval_scope, "question_approval_scope"
                ),
                "source": _identity(source, "question_source"),
            },
        )

    def suspend_question(
        self, *, expected_revision: int, mutation_id: str, question_id: str
    ) -> dict[str, Any]:
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="question.suspended",
            payload={"question_id": _identity(question_id, "question_id")},
        )

    def record_runtime_question_suspension(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        question_id: str,
        source_turn_id: str,
        source_epoch_id: str,
        source_segment_id: str,
        runtime_incarnation: str,
        call_id: str,
        session_ref: str,
        event_count: int,
    ) -> dict[str, Any]:
        """Atomically suspend one live segment and retain its opaque resume proof."""

        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="question.runtime_suspended",
            payload={
                "question_id": _identity(question_id, "question_id"),
                "source_turn_id": _identity(source_turn_id, "source_turn_id"),
                "source_epoch_id": _identity(source_epoch_id, "source_epoch_id"),
                "source_segment_id": _identity(
                    source_segment_id, "source_segment_id"
                ),
                "runtime_incarnation": _identity(
                    runtime_incarnation, "runtime_incarnation"
                ),
                "call_id": _identity(call_id, "question_call_id"),
                "session_ref": _digest(session_ref, "question_session_ref"),
                "event_count": _counter(event_count, "question_event_count"),
            },
        )

    def prepare_question_resume_segment(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        question_id: str,
        answer_receipt: str,
        answers_fingerprint: str,
        request_id: str,
        message_id: str,
        runtime_turn_id: str,
        epoch_id: str,
        segment_id: str,
        runtime_incarnation: str,
    ) -> dict[str, Any]:
        """Persist stable resume identity before a runtime can observe it."""

        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="question.resume_segment_prepared",
            payload={
                "question_id": _identity(question_id, "question_id"),
                "answer_receipt": _receipt(answer_receipt, "answer_receipt"),
                "answers_fingerprint": _digest(
                    answers_fingerprint, "answers_fingerprint"
                ),
                "request_id": _identity(request_id, "resume_request_id"),
                "message_id": _message_uuid(message_id),
                "runtime_turn_id": _identity(
                    runtime_turn_id, "runtime_turn_id"
                ),
                "epoch_id": _identity(epoch_id, "epoch_id"),
                "segment_id": _identity(segment_id, "segment_id"),
                "runtime_incarnation": _identity(
                    runtime_incarnation, "runtime_incarnation"
                ),
            },
        )

    def recover_question_resume_segment(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        question_id: str,
        segment_id: str,
    ) -> dict[str, Any]:
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="question.resume_recovered",
            payload={
                "question_id": _identity(question_id, "question_id"),
                "segment_id": _identity(segment_id, "segment_id"),
            },
        )

    def acknowledge_question_resume(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        question_id: str,
        answer_receipt: str,
        status: str,
        starts_turn: bool,
        reason: str | None,
        result_status: str | None,
    ) -> dict[str, Any]:
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="question.resume_acknowledged",
            payload={
                "question_id": _identity(question_id, "question_id"),
                "answer_receipt": _receipt(answer_receipt, "answer_receipt"),
                "status": _identity(status, "resume_status"),
                "starts_turn": starts_turn,
                "reason": _optional_code(reason, "resume_reason"),
                "result_status": _optional_code(
                    result_status, "resume_result_status"
                ),
            },
        )

    def classify_question_answer_fingerprint(
        self,
        *,
        batch_id: str,
        answers_fingerprint: str,
    ) -> dict[str, Any]:
        """Classify a DSH-owned answer without persisting its content."""

        checked_batch_id = _identity(batch_id, "question_id")
        checked_fingerprint = _digest(
            answers_fingerprint, "answers_fingerprint"
        )
        question = next(
            (
                item
                for item in self.projection()["questions"]
                if item["question_id"] == checked_batch_id
            ),
            None,
        )
        if (
            question is None
            or question.get("kind") != "batch"
            or question.get("content_authority") != "dsh.userQuestions"
        ):
            _fail("workbench_question_missing")
        existing = question.get("answer_fingerprint")
        return {
            "classification": (
                "new"
                if existing is None
                else "exact_replay"
                if existing == checked_fingerprint
                else "conflict"
            ),
            "question": question,
            "answers_fingerprint": checked_fingerprint,
        }

    def submit_question_batch_answer_receipt(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        batch_id: str,
        answers_fingerprint: str,
    ) -> dict[str, Any]:
        """Persist an answer delivery receipt without prompt/option/answer text."""

        checked_batch_id = _identity(batch_id, "question_id")
        checked_fingerprint = _digest(
            answers_fingerprint, "answers_fingerprint"
        )
        retried = self._idempotent_receipt(
            mutation_id=mutation_id,
            event_type="question.batch_answer_receipt_recorded",
            expected_payload={
                "batch_id": checked_batch_id,
                "answers_fingerprint": checked_fingerprint,
            },
        )
        if retried is not None:
            return retried
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="question.batch_answer_receipt_recorded",
            payload={
                "batch_id": checked_batch_id,
                "answers_fingerprint": checked_fingerprint,
                "answer_receipt": None,
            },
        )

    def transition_question(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        question_id: str,
        to_state: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        checked_question_id = _identity(question_id, "question_id")
        checked_reason = _optional_code(reason, "question_reason")
        if to_state not in QUESTION_STATES:
            _fail("workbench_question_state_invalid")
        retried = self._idempotent_receipt(
            mutation_id=mutation_id,
            event_type="question.transitioned",
            expected_payload={
                "question_id": checked_question_id,
                "to_state": to_state,
                "reason": checked_reason,
            },
        )
        if retried is not None:
            return retried
        projection = self.projection()
        question = next(
            (
                item
                for item in projection["questions"]
                if item["question_id"] == question_id
            ),
            None,
        )
        if question is None:
            _fail("workbench_question_missing")
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="question.transitioned",
            payload={
                "question_id": checked_question_id,
                "from_state": question["state"],
                "to_state": to_state,
                "reason": checked_reason,
            },
        )

    def enqueue(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        inbox_id: str,
        kind: str,
        message: str,
        document_binding: Mapping[str, Any],
        target: Mapping[str, Any] | None,
        client_request_id: str,
    ) -> dict[str, Any]:
        if kind not in INBOX_KINDS:
            _fail("workbench_inbox_kind_invalid")
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="inbox.enqueued",
            payload={
                "inbox_id": _identity(inbox_id, "inbox_id"),
                "kind": kind,
                "message": _visible_text(message, "inbox_message", MAX_TEXT_CHARS),
                "document_binding": _document_binding(document_binding),
                "target": _target(target, required=kind != "queue"),
                "client_request_id": _identity(client_request_id, "client_request_id"),
            },
        )

    def transition_inbox(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        inbox_id: str,
        expected_item_revision: int,
        to_state: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        checked_inbox_id = _identity(inbox_id, "inbox_id")
        checked_item_revision = _counter(expected_item_revision, "inbox_item_revision")
        checked_reason = _optional_code(reason, "inbox_reason")
        if to_state not in INBOX_STATES:
            _fail("workbench_inbox_state_invalid")
        retried = self._idempotent_receipt(
            mutation_id=mutation_id,
            event_type="inbox.transitioned",
            expected_payload={
                "inbox_id": checked_inbox_id,
                "expected_item_revision": checked_item_revision,
                "to_state": to_state,
                "reason": checked_reason,
            },
        )
        if retried is not None:
            return retried
        projection = self.projection()
        item = next(
            (item for item in projection["inbox"] if item["inbox_id"] == inbox_id),
            None,
        )
        if item is None:
            _fail("workbench_inbox_missing")
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="inbox.transitioned",
            payload={
                "inbox_id": checked_inbox_id,
                "expected_item_revision": checked_item_revision,
                "from_state": item["state"],
                "to_state": to_state,
                "reason": checked_reason,
                "admission_receipt": None,
            },
        )

    def acknowledge_steer(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        inbox_id: str,
        expected_item_revision: int,
        ack_status: str,
        message_id: str,
        dsh_message_id: str,
    ) -> dict[str, Any]:
        """Persist the exact external Steer ACK before consuming its item."""

        checked = {
            "inbox_id": _identity(inbox_id, "inbox_id"),
            "expected_item_revision": _counter(
                expected_item_revision, "inbox_item_revision"
            ),
            "ack_status": ack_status,
            "message_id": _identity(message_id, "message_id"),
            "dsh_message_id": _identity(dsh_message_id, "dsh_message_id"),
        }
        if ack_status not in {"accepted", "already_accepted"}:
            _fail("workbench_steer_ack_status_invalid")
        retried = self._idempotent_receipt(
            mutation_id=mutation_id,
            event_type="inbox.steer_acknowledged",
            expected_payload=checked,
        )
        if retried is not None:
            return retried
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="inbox.steer_acknowledged",
            payload={**checked, "admission_receipt": None},
        )

    def revise_inbox(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        inbox_id: str,
        expected_item_revision: int,
        message: str,
        document_binding: Mapping[str, Any],
        target: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        checked_inbox_id = _identity(inbox_id, "inbox_id")
        checked_item_revision = _counter(expected_item_revision, "inbox_item_revision")
        checked_message = _visible_text(message, "inbox_message", MAX_TEXT_CHARS)
        checked_binding = _document_binding(document_binding)
        projection = self.projection()
        item = next(
            (row for row in projection["inbox"] if row["inbox_id"] == checked_inbox_id),
            None,
        )
        if item is None:
            _fail("workbench_inbox_missing")
        checked_target = _target(target, required=item["kind"] != "queue")
        requested = {
            "inbox_id": checked_inbox_id,
            "expected_item_revision": checked_item_revision,
            "message": checked_message,
            "document_binding": checked_binding,
            "target": checked_target,
        }
        retried = self._idempotent_receipt(
            mutation_id=mutation_id,
            event_type="inbox.revised",
            expected_payload=requested,
        )
        if retried is not None:
            return retried
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="inbox.revised",
            payload=requested,
        )

    def reorder_inbox(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        inbox_ids: Sequence[str],
    ) -> dict[str, Any]:
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="inbox.reordered",
            payload={
                "inbox_ids": _identity_list(
                    list(inbox_ids), "inbox_id", maximum=MAX_PROJECTION_ROWS
                )
            },
        )

    def rebind_inbox_batch(
        self,
        *,
        expected_revision: int,
        mutation_id: str,
        items: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Atomically invalidate Queue bindings after a lineage advance.

        The checkpoint coordinator supplies exact item revisions and both
        sides of the document binding.  The generated receipt can be stored
        in its own transaction journal without either file pretending to be
        a cross-file atomic authority.
        """

        checked_items: list[dict[str, Any]] = []
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
            _fail("workbench_inbox_rebind_items_invalid")
        for value in items:
            if not isinstance(value, Mapping) or set(value) != {
                "inbox_id",
                "expected_item_revision",
                "old_document_binding",
                "new_document_binding",
            }:
                _fail("workbench_inbox_rebind_items_invalid")
            checked_items.append(
                {
                    "inbox_id": _identity(value["inbox_id"], "inbox_id"),
                    "expected_item_revision": _counter(
                        value["expected_item_revision"], "inbox_item_revision"
                    ),
                    "old_document_binding": _document_binding(
                        value["old_document_binding"]
                    ),
                    "new_document_binding": _document_binding(
                        value["new_document_binding"]
                    ),
                }
            )
        if not 1 <= len(checked_items) <= MAX_PROJECTION_ROWS:
            _fail("workbench_inbox_rebind_items_invalid")
        retried = self._idempotent_receipt(
            mutation_id=mutation_id,
            event_type="inbox.batch_rebound",
            expected_payload={"items": checked_items},
        )
        if retried is not None:
            return retried
        return self._append(
            expected_revision=expected_revision,
            mutation_id=mutation_id,
            event_type="inbox.batch_rebound",
            payload={"items": checked_items, "rebind_receipt": None},
        )


__all__ = [
    "CONTRACT",
    "INBOX_KINDS",
    "INBOX_STATES",
    "LEDGER_FILE",
    "MAX_EVENTS",
    "PROJECTION_CONTRACT",
    "QUESTION_KINDS",
    "QUESTION_STATES",
    "SEGMENT_STATES",
    "TURN_STATES",
    "NativeWorkbenchLedger",
    "NativeWorkbenchLedgerError",
]
