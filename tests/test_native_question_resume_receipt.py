from __future__ import annotations

import hashlib
import json

import pytest

from genshin_app.agent_chat.native_question_resume_receipt import (
    APP_APPLIED,
    PREPARED,
    RUNTIME_SETTLED,
    NativeQuestionResumeReceiptStore,
)
from genshin_app.equipment.schema import PlanError


def _checkpoint(event_count: int) -> dict:
    row = {
        "contract": "genshin-native-dsh-checkpoint-v1",
        "session_id": "dsh-session-1",
        "storage_format": "dsh-jsonl-zstd-v1",
        "composition_hash": "a" * 64,
        "revision": f"revision-{event_count}",
        "last_event_seq": event_count - 1,
        "event_count": event_count,
        "size_bytes": event_count * 128,
    }
    return {
        **row,
        "flush_digest": hashlib.sha256(
            json.dumps(row, separators=(",", ":")).encode()
        ).hexdigest(),
    }


def _identity() -> dict:
    return {
        "conversation_id": "conversation-1",
        "dsh_session_id": "dsh-session-1",
        "request_id": "request-1",
        "message_id": "8590a7f2-668a-43f4-8755-910e85d53e3f",
        "runtime_turn_id": "turn-2",
        "epoch_id": "epoch-2",
        "source_turn_id": "turn-1",
        "source_epoch_id": "epoch-1",
        "call_id": "call-1",
        "answer_receipt": "answer-1",
        "question_fingerprint": "b" * 64,
        "answers_fingerprint": "c" * 64,
        "prompt_profile_hash": "d" * 64,
        "runtime_context_hash": "e" * 64,
        "tool_manifest_hash": "f" * 64,
        "workflow_stage": "action_checkpoint",
        "provider": "deepseek-official",
        "model": "DeepSeek-V4.1-Flash",
        "reasoning_effort": "high",
        "credential_generation": 2,
        "pre_checkpoint_binding": "1" * 64,
    }


def _ack() -> dict:
    return {
        "status": "accepted",
        "call_id": "call-1",
        "answer_receipt": "answer-1",
        "starts_turn": True,
        "reason": None,
    }


def _outcome() -> dict:
    return {
        "terminal_type": "turn.completed",
        "event_frames": 12,
        "acknowledged_event_frames": 12,
        "provider_request_possible": True,
        "terminal_reason": None,
        "segment_restart_reason": None,
        "segment_budget_usage": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
        "settled_tool_call_ids": ["call-tool-1"],
    }


def test_receipt_is_three_phase_atomic_and_idempotent(tmp_path):
    store = NativeQuestionResumeReceiptStore(
        tmp_path, conversation_id="conversation-1"
    )
    prepared = store.prepare(
        identity=_identity(), baseline_dsh_checkpoint=_checkpoint(4)
    )
    assert prepared["state"] == PREPARED
    assert store.prepare(
        identity=_identity(), baseline_dsh_checkpoint=_checkpoint(4)
    ) == prepared

    settled = store.settle_runtime(
        identity=_identity(),
        resume_ack=_ack(),
        terminal_dsh_checkpoint=_checkpoint(10),
        terminal_outcome=_outcome(),
    )
    assert settled["state"] == RUNTIME_SETTLED
    assert store.settle_runtime(
        identity=_identity(),
        resume_ack=_ack(),
        terminal_dsh_checkpoint=_checkpoint(10),
        terminal_outcome=_outcome(),
    ) == settled

    applied = store.mark_app_applied(
        identity=_identity(),
        app_result={
            "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
        post_checkpoint_binding="2" * 64,
    )
    assert applied["state"] == APP_APPLIED
    assert store.read(request_id="request-1") == applied
    raw = next((tmp_path / ".native-question-resume-receipts-v1").glob("*.json"))
    text = raw.read_text(encoding="utf-8")
    assert "raw_reasoning" not in text
    assert "assistant_text" not in text
    assert "assistant_messages" not in text
    assert "projected_events" not in text


def test_receipt_rejects_identity_conflict_and_nonadvancing_terminal(tmp_path):
    store = NativeQuestionResumeReceiptStore(
        tmp_path, conversation_id="conversation-1"
    )
    store.prepare(identity=_identity(), baseline_dsh_checkpoint=_checkpoint(4))
    changed = {**_identity(), "answers_fingerprint": "9" * 64}
    with pytest.raises(PlanError, match="冲突"):
        store.prepare(identity=changed, baseline_dsh_checkpoint=_checkpoint(4))
    with pytest.raises(PlanError, match="没有"):
        store.settle_runtime(
            identity=_identity(),
            resume_ack=_ack(),
            terminal_dsh_checkpoint=_checkpoint(4),
            terminal_outcome=_outcome(),
        )


def test_receipt_corruption_and_duplicate_keys_fail_closed(tmp_path):
    store = NativeQuestionResumeReceiptStore(
        tmp_path, conversation_id="conversation-1"
    )
    store.prepare(identity=_identity(), baseline_dsh_checkpoint=_checkpoint(4))
    path = next((tmp_path / ".native-question-resume-receipts-v1").glob("*.json"))
    path.write_text('{"contract":"x","contract":"y"}', encoding="utf-8")
    with pytest.raises(PlanError, match="严格JSON"):
        store.read(request_id="request-1")


@pytest.mark.parametrize("status", ["unavailable", "conflict"])
def test_receipt_persists_terminal_no_turn_acknowledgement(tmp_path, status):
    store = NativeQuestionResumeReceiptStore(
        tmp_path, conversation_id="conversation-1"
    )
    store.prepare(identity=_identity(), baseline_dsh_checkpoint=_checkpoint(4))
    settled = store.settle_runtime(
        identity=_identity(),
        resume_ack={
            "status": status,
            "call_id": "call-1",
            "answer_receipt": "answer-1",
            "starts_turn": False,
            "reason": "question_not_continued",
        },
        terminal_dsh_checkpoint=_checkpoint(4),
        terminal_outcome=None,
    )
    assert settled["state"] == RUNTIME_SETTLED
    applied = store.mark_app_applied(
        identity=_identity(),
        app_result={"status": "no_turn", "usage": None},
        post_checkpoint_binding="2" * 64,
    )
    assert applied["state"] == APP_APPLIED
    assert applied["resume_ack"]["status"] == status
