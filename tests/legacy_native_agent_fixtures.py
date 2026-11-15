"""Writers for historical native-Agent compatibility fixtures.

Production code must never import this module.  DSH owns durable transcript
and question content now.  These helpers create records written by the old
App-owned stores so read-only migration, projection, and crash-recovery tests
can continue to prove compatibility without leaving a callable production
write path behind.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from genshin_app.agent_chat import native_session_store as session_store
from genshin_app.agent_chat import native_workbench_ledger as workbench_ledger
from genshin_app.agent_chat.native_workbench_ledger import NativeWorkbenchLedger


def append_legacy_visible_message(
    record: dict,
    *,
    message_id: str,
    role: str,
    text: str,
    workflow_stage: str | None,
    user_turn: int,
) -> dict:
    """Create one pre-DSH visible-message record for compatibility tests."""

    snapshot = session_store._snapshot(record, "原生会话记录")
    session_store.normalize_native_session(snapshot)
    if any(row["message_id"] == message_id for row in snapshot["visible_messages"]):
        session_store._fail("可见消息身份已经存在", "call_id_conflict")
    row = {
        "message_id": message_id,
        "role": role,
        "text": session_store.redact_visible_text(text),
        "workflow_stage": workflow_stage,
        "user_turn": user_turn,
    }
    session_store._validate_message(row)
    snapshot["visible_messages"].append(row)
    snapshot["accepted_user_turn_count"] = max(
        snapshot["accepted_user_turn_count"],
        user_turn,
    )
    if workflow_stage is not None:
        snapshot["current_workflow_stage"] = workflow_stage
    while (
        len(snapshot["visible_messages"]) > session_store.MAX_VISIBLE_MESSAGES
        or sum(len(item["text"]) for item in snapshot["visible_messages"])
        > session_store.MAX_VISIBLE_TEXT_CHARS
    ):
        snapshot["visible_messages"].pop(0)
        snapshot["visible_message_evicted_count"] += 1
    session_store.refresh_checkpoint_binding(snapshot)
    session_store.normalize_native_session(snapshot)
    session_store._commit_record(record, snapshot)
    return copy.deepcopy(row)


def open_legacy_question(
    ledger: NativeWorkbenchLedger,
    *,
    expected_revision: int,
    mutation_id: str,
    question_id: str,
    turn_id: str,
    segment_id: str,
    kind: str,
    prompt: str,
    options: Sequence[Mapping[str, str]] | None,
    source: str,
) -> dict[str, Any]:
    """Create one pre-DSH question-content event for compatibility tests."""

    if kind not in workbench_ledger.QUESTION_KINDS - {"batch"}:
        workbench_ledger._fail("workbench_question_kind_invalid")
    return ledger._append(
        expected_revision=expected_revision,
        mutation_id=mutation_id,
        event_type="question.opened",
        payload={
            "question_id": workbench_ledger._identity(question_id, "question_id"),
            "turn_id": workbench_ledger._identity(turn_id, "turn_id"),
            "segment_id": workbench_ledger._identity(segment_id, "segment_id"),
            "kind": kind,
            "prompt": workbench_ledger._visible_text(
                prompt,
                "question_prompt",
                workbench_ledger.MAX_TEXT_CHARS,
            ),
            "options": workbench_ledger._question_options(
                list(options or []), kind
            ),
            "source": workbench_ledger._identity(source, "question_source"),
        },
    )


def open_legacy_question_batch(
    ledger: NativeWorkbenchLedger,
    *,
    expected_revision: int,
    mutation_id: str,
    batch_id: str,
    turn_id: str,
    segment_id: str,
    call_id: str,
    items: Sequence[Mapping[str, Any]],
    source: str,
) -> dict[str, Any]:
    """Create one pre-DSH full question-batch event for compatibility tests."""

    return ledger._append(
        expected_revision=expected_revision,
        mutation_id=mutation_id,
        event_type="question.batch_opened",
        payload={
            "batch_id": workbench_ledger._identity(batch_id, "question_id"),
            "turn_id": workbench_ledger._identity(turn_id, "turn_id"),
            "segment_id": workbench_ledger._identity(segment_id, "segment_id"),
            "call_id": workbench_ledger._identity(call_id, "question_call_id"),
            "items": workbench_ledger._question_batch_items(list(items)),
            "source": workbench_ledger._identity(source, "question_source"),
        },
    )


def wait_and_open_legacy_question_batch(
    ledger: NativeWorkbenchLedger,
    *,
    expected_revision: int,
    mutation_id: str,
    batch_id: str,
    turn_id: str,
    segment_id: str,
    call_id: str,
    items: Sequence[Mapping[str, Any]],
    source: str,
) -> dict[str, Any]:
    """Create one atomic pre-DSH wait-and-full-batch compatibility event."""

    return ledger._append(
        expected_revision=expected_revision,
        mutation_id=mutation_id,
        event_type="question.batch_wait_opened",
        payload={
            "batch_id": workbench_ledger._identity(batch_id, "question_id"),
            "turn_id": workbench_ledger._identity(turn_id, "turn_id"),
            "segment_id": workbench_ledger._identity(segment_id, "segment_id"),
            "call_id": workbench_ledger._identity(call_id, "question_call_id"),
            "items": workbench_ledger._question_batch_items(list(items)),
            "source": workbench_ledger._identity(source, "question_source"),
        },
    )


def submit_legacy_question_answer(
    ledger: NativeWorkbenchLedger,
    *,
    expected_revision: int,
    mutation_id: str,
    question_id: str,
    answer: Mapping[str, Any],
) -> dict[str, Any]:
    """Write one historical App-owned single-question answer event."""

    checked_question_id = workbench_ledger._identity(question_id, "question_id")
    question = next(
        (
            item
            for item in ledger.projection()["questions"]
            if item["question_id"] == checked_question_id
        ),
        None,
    )
    if question is None:
        workbench_ledger._fail("workbench_question_missing")
    checked_answer = workbench_ledger._question_answer(
        answer,
        kind=question["kind"],
        options=question["options"],
    )
    retried = ledger._idempotent_receipt(
        mutation_id=mutation_id,
        event_type="question.answer_submitted",
        expected_payload={
            "question_id": checked_question_id,
            "answer": checked_answer,
        },
    )
    if retried is not None:
        return retried
    return ledger._append(
        expected_revision=expected_revision,
        mutation_id=mutation_id,
        event_type="question.answer_submitted",
        payload={
            "question_id": checked_question_id,
            "answer": checked_answer,
            "answer_receipt": None,
        },
    )


def submit_legacy_question_batch_answer(
    ledger: NativeWorkbenchLedger,
    *,
    expected_revision: int,
    mutation_id: str,
    batch_id: str,
    answer: Mapping[str, Any],
) -> dict[str, Any]:
    """Write one historical App-owned batch-answer event."""

    checked_batch_id = workbench_ledger._identity(batch_id, "question_id")
    question = next(
        (
            item
            for item in ledger.projection()["questions"]
            if item["question_id"] == checked_batch_id
        ),
        None,
    )
    if question is None or question.get("kind") != "batch":
        workbench_ledger._fail("workbench_question_missing")
    checked_answer = workbench_ledger._question_batch_answer(
        answer,
        items=question["items"],
    )
    retried = ledger._idempotent_receipt(
        mutation_id=mutation_id,
        event_type="question.batch_answer_submitted",
        expected_payload={
            "batch_id": checked_batch_id,
            "answer": checked_answer,
        },
    )
    if retried is not None:
        return retried
    return ledger._append(
        expected_revision=expected_revision,
        mutation_id=mutation_id,
        event_type="question.batch_answer_submitted",
        payload={
            "batch_id": checked_batch_id,
            "answer": checked_answer,
            "answer_receipt": None,
        },
    )


__all__ = [
    "append_legacy_visible_message",
    "open_legacy_question",
    "open_legacy_question_batch",
    "submit_legacy_question_answer",
    "submit_legacy_question_batch_answer",
    "wait_and_open_legacy_question_batch",
]
