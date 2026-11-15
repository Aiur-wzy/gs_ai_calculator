from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import genshin_app.agent_chat.native_workbench_ledger as ledger_module
from genshin_app.agent_chat.native_workbench_ledger import (
    CONTRACT,
    LEDGER_FILE,
    PROJECTION_CONTRACT,
    NativeWorkbenchLedger,
    NativeWorkbenchLedgerError,
)
from genshin_app.equipment.schema import PlanError
from tests.legacy_native_agent_fixtures import (
    open_legacy_question,
    open_legacy_question_batch,
    submit_legacy_question_answer,
    submit_legacy_question_batch_answer,
    wait_and_open_legacy_question_batch,
)


class SequenceFactory:
    def __init__(self, prefix: str, width: int) -> None:
        self.prefix = prefix
        self.width = width
        self.value = 0

    def __call__(self) -> str:
        self.value += 1
        return f"{self.prefix}{self.value:0{self.width}d}"


def binding(revision: int = 0) -> dict:
    return {
        "document_id": "board-one",
        "document_lineage_epoch": 0,
        "document_revision": revision,
        "candidate_revision": 2,
        "stage_revision": 3,
    }


def target() -> dict:
    return {
        "turn_id": "turn-one",
        "segment_id": "segment-two",
        "epoch_id": "epoch-two",
        "stage_epoch": 4,
        "runtime_incarnation": "runtime-two",
    }


def test_legacy_question_content_writers_are_not_production_apis():
    assert not hasattr(NativeWorkbenchLedger, "open_question")
    assert not hasattr(NativeWorkbenchLedger, "open_question_batch")
    assert not hasattr(NativeWorkbenchLedger, "wait_and_open_question_batch")
    assert not hasattr(NativeWorkbenchLedger, "submit_question_answer")
    assert not hasattr(NativeWorkbenchLedger, "submit_question_batch_answer")
    assert not hasattr(NativeWorkbenchLedger, "answer_question")
    assert not hasattr(NativeWorkbenchLedger, "classify_question_batch_answer")


@pytest.fixture
def ledger(tmp_path):
    return NativeWorkbenchLedger(
        tmp_path,
        conversation_id="conversation-one",
        clock=lambda: "2026-10-24T00:00:00Z",
        event_id_factory=SequenceFactory("event_", 32),
        receipt_factory=SequenceFactory("receipt_", 43),
    )


def append_turn_and_segment(ledger: NativeWorkbenchLedger) -> int:
    ledger.create_turn(
        expected_revision=0, mutation_id="mutation-turn", turn_id="turn-one"
    )
    ledger.start_segment(
        expected_revision=1,
        mutation_id="mutation-segment",
        turn_id="turn-one",
        segment_id="segment-one",
        epoch_id="epoch-one",
        runtime_incarnation="runtime-one",
        stage_epoch=3,
    )
    ledger.transition_turn(
        expected_revision=2,
        mutation_id="mutation-turn-streaming",
        turn_id="turn-one",
        to_state="streaming",
    )
    ledger.transition_segment(
        expected_revision=3,
        mutation_id="mutation-segment-streaming",
        segment_id="segment-one",
        to_state="streaming",
    )
    return 4


def open_live_question(ledger: NativeWorkbenchLedger, revision: int) -> int:
    ledger.transition_turn(
        expected_revision=revision,
        mutation_id="mutation-turn-waiting",
        turn_id="turn-one",
        to_state="awaiting_input_live",
    )
    ledger.transition_segment(
        expected_revision=revision + 1,
        mutation_id="mutation-segment-waiting",
        segment_id="segment-one",
        to_state="awaiting_input_live",
    )
    open_legacy_question(
        ledger,
        expected_revision=revision + 2,
        mutation_id="mutation-question",
        question_id="question-one",
        turn_id="turn-one",
        segment_id="segment-one",
        kind="single",
        prompt="选择计算口径",
        options=[
            {"value_id": "option-a", "label": "采用口径甲"},
            {"value_id": "option-b", "label": "采用口径乙"},
        ],
        source="workflow.numeric_model",
    )
    return revision + 3


def test_turn_outline_is_derived_and_event_window_rebuilds_one_projection(ledger):
    revision = append_turn_and_segment(ledger)
    projection = ledger.projection()
    assert projection["contract"] == PROJECTION_CONTRACT
    assert projection["source_cursor"] == projection["projection_revision"] == 4
    # DSH is the durable authority for the generic transcript.  The
    # workbench projection now describes only App-domain overlays, so the
    # absence of duplicate Host timeline nodes is complete rather than a
    # partially reconstructed transcript.
    assert projection["completeness"] == {
        "state": "complete",
        "partial_fields": [],
        "reason": None,
    }
    assert projection["turns"][0]["segment_ids"] == ["segment-one"]
    assert projection["turn_outline"] == [
        {
            "turn_id": "turn-one",
            "state": "streaming",
            "seq_start": 1,
            "seq_end": revision,
            "segment_count": 1,
            "node_count": 0,
            "markers": ["turn:streaming"],
            "excerpts_completeness": "partial",
            "excerpts": [],
        }
    ]
    window = ledger.events_after(2, limit=1)
    assert window["requested_after_seq"] == 2
    assert window["next_seq"] == 3
    assert window["source_cursor"] == revision
    assert window["events"][0]["type"] == "turn.transitioned"

    rebuilt = NativeWorkbenchLedger(
        ledger.root, conversation_id="conversation-one"
    ).projection()
    assert rebuilt == projection


def test_durable_timeline_node_completes_outline_without_exposing_payload(ledger):
    revision = append_turn_and_segment(ledger)
    receipt = ledger.record_timeline_node(
        expected_revision=revision,
        mutation_id="mutation-user-message",
        node={
            "node_id": "message-user-one",
            "turn_id": "turn-one",
            "segment_id": None,
            "operation_id": None,
            "kind": "message",
            "category": "provider",
            "role": "user",
            "label": "用户消息",
            "text": "请完成排轴",
            "state": "accepted",
            "result_code": None,
            "resource_ref": None,
            "workflow_stage": "build_setup",
        },
    )
    assert receipt["event_type"] == "timeline.node_recorded"
    assert ledger.projection()["completeness"]["state"] == "complete"
    assert ledger.projection()["turn_outline"][0]["node_count"] == 1
    assert ledger.timeline_nodes()[0]["text"] == "请完成排轴"
    with pytest.raises(NativeWorkbenchLedgerError, match="timeline_text_sensitive"):
        ledger.record_timeline_node(
            expected_revision=revision + 1,
            mutation_id="mutation-path-leak",
            node={
                "node_id": "message-path-leak",
                "turn_id": "turn-one",
                "segment_id": None,
                "operation_id": None,
                "kind": "notice",
                "category": "runtime",
                "role": "notice",
                "label": "错误",
                "text": "C:\\private\\secret.json",
                "state": "failed",
                "result_code": None,
                "resource_ref": None,
                "workflow_stage": None,
            },
        )


def test_terminal_sibling_does_not_hide_an_unsettled_operation_component(ledger):
    revision = append_turn_and_segment(ledger)

    def record(
        node_id: str,
        *,
        kind: str,
        category: str,
        state: str,
        operation_id: str | None = "call-one",
        role: str | None = None,
    ) -> None:
        nonlocal revision
        ledger.record_timeline_node(
            expected_revision=revision,
            mutation_id=f"mutation-{node_id}",
            node={
                "node_id": node_id,
                "turn_id": "turn-one",
                "segment_id": "segment-one",
                "operation_id": operation_id,
                "kind": kind,
                "category": category,
                "role": role,
                "label": node_id,
                "text": "开始" if role == "user" else "完成" if role == "assistant" else None,
                "state": state,
                "result_code": None,
                "resource_ref": None,
                "workflow_stage": "event_topology",
            },
        )
        revision += 1

    record(
        "message-user-one",
        kind="message",
        category="provider",
        state="accepted",
        operation_id=None,
        role="user",
    )
    record("tool-running", kind="tool", category="app_tool", state="running")
    record("tool-completed", kind="tool", category="app_tool", state="completed")
    record("candidate-running", kind="candidate", category="candidate", state="running")
    record(
        "message-assistant-one",
        kind="message",
        category="provider",
        state="completed",
        operation_id=None,
        role="assistant",
    )
    ledger.transition_segment(
        expected_revision=revision,
        mutation_id="mutation-segment-completed",
        segment_id="segment-one",
        to_state="completed",
    )
    revision += 1
    ledger.transition_turn(
        expected_revision=revision,
        mutation_id="mutation-turn-completed",
        turn_id="turn-one",
        to_state="completed",
    )
    revision += 1

    assert ledger.projection()["completeness"] == {
        "state": "partial",
        "partial_fields": ["timeline"],
        "reason": "durable_timeline_not_available_for_legacy_turn",
    }

    record(
        "candidate-completed",
        kind="candidate",
        category="candidate",
        state="completed",
    )
    assert ledger.projection()["completeness"]["state"] == "complete"


def test_host_replays_ten_thousand_display_events_without_renderer_materialization(tmp_path):
    events = [{
        "seq": 1,
        "event_id": f"event_{1:032d}",
        "mutation_id": "mutation-turn-large",
        "type": "turn.created",
        "occurred_at": "2026-10-25T00:00:00Z",
        "payload": {"turn_id": "turn-large", "predecessor_inbox_id": None},
    }]
    for index in range(10_000):
        seq = index + 2
        events.append({
            "seq": seq,
            "event_id": f"event_{seq:032d}",
            "mutation_id": f"mutation-timeline-{index:05d}",
            "type": "timeline.node_recorded",
            "occurred_at": "2026-10-25T00:00:01Z",
            "payload": {"node": {
                "node_id": f"node-{index:05d}",
                "turn_id": "turn-large",
                "segment_id": None,
                "operation_id": f"call-{index:05d}" if index else None,
                "kind": "message" if index == 0 else "tool",
                "category": "provider" if index == 0 else "engine",
                "role": "user" if index == 0 else None,
                "label": "用户请求" if index == 0 else "计算事件",
                "text": "开始计算" if index == 0 else None,
                "state": "completed",
                "result_code": None,
                "resource_ref": None,
                "workflow_stage": "numeric_closure",
            }},
        })
    document = {
        "contract": CONTRACT,
        "conversation_id": "conversation-large",
        "revision": len(events),
        "source_cursor": len(events),
        "events": events,
    }
    path = tmp_path / LEDGER_FILE
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    store = NativeWorkbenchLedger(tmp_path, conversation_id="conversation-large")
    snapshot = store.display_snapshot()
    assert len(snapshot["timeline"]) == 10_000
    assert snapshot["timeline"][0]["node_id"] == "node-00000"
    assert snapshot["timeline"][-1]["node_id"] == "node-09999"
    assert snapshot["projection"]["turn_outline"][0]["node_count"] == 10_000


def test_host_projection_retains_one_thousand_complete_turns(tmp_path):
    events = []

    def append(event_type, mutation_id, payload):
        seq = len(events) + 1
        events.append({
            "seq": seq,
            "event_id": f"event_{seq:032d}",
            "mutation_id": mutation_id,
            "type": event_type,
            "occurred_at": "2026-10-25T00:00:00Z",
            "payload": payload,
        })

    for index in range(1_000):
        turn_id, segment_id = f"turn-{index:04d}", f"segment-{index:04d}"
        append("turn.created", f"mutation-turn-{index:04d}", {"turn_id": turn_id, "predecessor_inbox_id": None})
        for suffix, role, text in (("user", "user", "开始"),):
            append("timeline.node_recorded", f"mutation-{suffix}-{index:04d}", {"node": {"node_id": f"node-{suffix}-{index:04d}", "turn_id": turn_id, "segment_id": None, "operation_id": None, "kind": "message", "category": "provider", "role": role, "label": "用户消息", "text": text, "state": "accepted", "result_code": None, "resource_ref": None, "workflow_stage": "build_setup"}})
        append("segment.started", f"mutation-segment-{index:04d}", {"turn_id": turn_id, "segment_id": segment_id, "epoch_id": f"epoch-{index:04d}", "runtime_incarnation": f"runtime-{index:04d}", "stage_epoch": 0})
        append("turn.transitioned", f"mutation-turn-live-{index:04d}", {"turn_id": turn_id, "from_state": "preparing", "to_state": "streaming", "reason": None})
        append("segment.transitioned", f"mutation-segment-live-{index:04d}", {"segment_id": segment_id, "from_state": "preparing", "to_state": "streaming", "reason": None})
        append("timeline.node_recorded", f"mutation-assistant-{index:04d}", {"node": {"node_id": f"node-assistant-{index:04d}", "turn_id": turn_id, "segment_id": segment_id, "operation_id": None, "kind": "message", "category": "provider", "role": "assistant", "label": "Agent 回复", "text": "完成", "state": "completed", "result_code": None, "resource_ref": None, "workflow_stage": "numeric_closure"}})
        append("segment.transitioned", f"mutation-segment-done-{index:04d}", {"segment_id": segment_id, "from_state": "streaming", "to_state": "completed", "reason": None})
        append("turn.transitioned", f"mutation-turn-done-{index:04d}", {"turn_id": turn_id, "from_state": "streaming", "to_state": "completed", "reason": None})
    document = {"contract": CONTRACT, "conversation_id": "conversation-turns", "revision": len(events), "source_cursor": len(events), "events": events}
    (tmp_path / LEDGER_FILE).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    projection = NativeWorkbenchLedger(tmp_path, conversation_id="conversation-turns").projection()
    assert len(projection["turns"]) == 1_000
    assert len(projection["turn_outline"]) == 1_000
    assert projection["completeness"]["state"] == "complete"


def test_question_suspend_answer_and_new_segment_are_durable_and_idempotent(ledger):
    revision = open_live_question(ledger, append_turn_and_segment(ledger))
    ledger.transition_turn(
        expected_revision=revision,
        mutation_id="mutation-turn-suspended",
        turn_id="turn-one",
        to_state="awaiting_input_suspended",
    )
    ledger.transition_segment(
        expected_revision=revision + 1,
        mutation_id="mutation-segment-suspended",
        segment_id="segment-one",
        to_state="suspended",
    )
    ledger.suspend_question(
        expected_revision=revision + 2,
        mutation_id="mutation-question-suspended",
        question_id="question-one",
    )
    answer = submit_legacy_question_answer(
        ledger,
        expected_revision=revision + 3,
        mutation_id="mutation-question-answer",
        question_id="question-one",
        answer={"text": None, "value_ids": ["option-b"]},
    )
    retried = submit_legacy_question_answer(
        ledger,
        expected_revision=revision + 3,
        mutation_id="mutation-question-answer",
        question_id="question-one",
        answer={"text": None, "value_ids": ["option-b"]},
    )
    assert answer["answer_receipt"] == retried["answer_receipt"]
    assert retried["status"] == "already_accepted"
    assert retried["ledger_revision"] == answer["ledger_revision"]

    ledger.transition_question(
        expected_revision=revision + 4,
        mutation_id="mutation-question-answered",
        question_id="question-one",
        to_state="answered",
    )
    ledger.transition_question(
        expected_revision=revision + 5,
        mutation_id="mutation-question-resuming",
        question_id="question-one",
        to_state="resuming",
    )
    ledger.transition_turn(
        expected_revision=revision + 6,
        mutation_id="mutation-turn-resuming",
        turn_id="turn-one",
        to_state="resuming",
    )
    ledger.start_segment(
        expected_revision=revision + 7,
        mutation_id="mutation-segment-two",
        turn_id="turn-one",
        segment_id="segment-two",
        epoch_id="epoch-two",
        runtime_incarnation="runtime-two",
        stage_epoch=4,
    )
    ledger.transition_question(
        expected_revision=revision + 8,
        mutation_id="mutation-question-settled",
        question_id="question-one",
        to_state="settled",
    )

    projection = ledger.projection()
    question = projection["questions"][0]
    assert question["mode"] == "suspended"
    assert question["state"] == "settled"
    assert question["answer"]["value_ids"] == ["option-b"]
    assert [item["segment_id"] for item in projection["segments"]] == [
        "segment-one",
        "segment-two",
    ]


def test_question_requires_matching_live_waiting_turn_and_segment(ledger):
    revision = append_turn_and_segment(ledger)
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        open_legacy_question(
            ledger,
            expected_revision=revision,
            mutation_id="mutation-question-too-early",
            question_id="question-one",
            turn_id="turn-one",
            segment_id="segment-one",
            kind="text",
            prompt="补充说明",
            options=None,
            source="workflow.event_topology",
        )
    assert error.value.code == "workbench_question_owner_not_waiting"
    assert ledger.read()["revision"] == revision


def test_question_batch_preserves_one_to_three_items_and_one_atomic_receipt(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.transition_turn(
        expected_revision=revision,
        mutation_id="mutation-batch-turn-waiting",
        turn_id="turn-one",
        to_state="awaiting_input_live",
    )
    ledger.transition_segment(
        expected_revision=revision + 1,
        mutation_id="mutation-batch-segment-waiting",
        segment_id="segment-one",
        to_state="awaiting_input_live",
    )
    items = [
        {
            "question_id": "choice-one",
            "kind": "single",
            "prompt": "采用哪个口径？",
            "detail": "只影响本轮候选。",
            "header": "计算口径",
            "intent": "decision",
            "options": [
                {"value_id": "a", "label": "口径甲", "description": None},
                {"value_id": "b", "label": "口径乙", "description": "更保守"},
            ],
            "allow_skip": False,
        },
        {
            "question_id": "note-two",
            "kind": "text",
            "prompt": "是否补充约束？",
            "detail": None,
            "header": None,
            "intent": None,
            "options": [],
            "allow_skip": True,
        },
    ]
    open_legacy_question_batch(
        ledger,
        expected_revision=revision + 2,
        mutation_id="mutation-question-batch",
        batch_id="batch-one",
        turn_id="turn-one",
        segment_id="segment-one",
        call_id="call-one",
        items=items,
        source="dsh.ask_user_question",
    )
    answer = {
        "items": [
            {
                "question_id": "choice-one",
                "text": None,
                "value_ids": ["b"],
                "skipped": False,
            },
            {
                "question_id": "note-two",
                "text": None,
                "value_ids": [],
                "skipped": True,
            },
        ]
    }
    accepted = submit_legacy_question_batch_answer(
        ledger,
        expected_revision=revision + 3,
        mutation_id="mutation-question-batch-answer",
        batch_id="batch-one",
        answer=answer,
    )
    retried = submit_legacy_question_batch_answer(
        ledger,
        expected_revision=revision + 3,
        mutation_id="mutation-question-batch-answer",
        batch_id="batch-one",
        answer=answer,
    )
    projected = ledger.projection()["questions"][0]
    assert projected["kind"] == "batch"
    assert projected["call_id"] == "call-one"
    assert [row["question_id"] for row in projected["items"]] == [
        "choice-one",
        "note-two",
    ]
    assert projected["answer"] == answer
    assert accepted["answer_receipt"] == retried["answer_receipt"]
    assert retried["status"] == "already_accepted"


def test_dsh_question_reference_persists_only_ids_hashes_and_delivery_receipt(ledger):
    revision = append_turn_and_segment(ledger)
    opened = ledger.wait_and_open_question_reference(
        expected_revision=revision,
        mutation_id="mutation-question-reference",
        batch_id="batch-reference",
        turn_id="turn-one",
        segment_id="segment-one",
        dsh_turn_id="runtime-turn-one",
        dsh_epoch_id="runtime-epoch-one",
        call_id="call-reference",
        question_ids=["choice-one", "note-two"],
        question_option_ids=[["option-a", "option-b"], []],
        question_fingerprint="a" * 64,
        approval_scope=None,
        source="dsh.ask_user_question",
    )
    assert opened["status"] == "accepted"
    question = ledger.projection()["questions"][0]
    assert question["content_authority"] == "dsh.userQuestions"
    assert question["items"] == []
    assert question["answer"] is None
    assert question["question_ids"] == ["choice-one", "note-two"]

    submitted = ledger.submit_question_batch_answer_receipt(
        expected_revision=opened["ledger_revision"],
        mutation_id="mutation-question-reference-answer",
        batch_id="batch-reference",
        answers_fingerprint="b" * 64,
    )
    assert submitted["answer_receipt"].startswith("receipt_")
    classified = ledger.classify_question_answer_fingerprint(
        batch_id="batch-reference",
        answers_fingerprint="b" * 64,
    )
    assert classified["classification"] == "exact_replay"
    stored = ledger.read()["events"]
    opened_payload = next(
        row["payload"]
        for row in stored
        if row["type"] == "question.batch_reference_wait_opened"
    )
    answer_payload = next(
        row["payload"]
        for row in stored
        if row["type"] == "question.batch_answer_receipt_recorded"
    )
    assert "items" not in opened_payload
    assert set(answer_payload) == {
        "batch_id",
        "answers_fingerprint",
        "answer_receipt",
    }


def test_question_batch_preserves_dsh_option_custom_answer(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.transition_turn(
        expected_revision=revision,
        mutation_id="mutation-custom-turn-waiting",
        turn_id="turn-one",
        to_state="awaiting_input_live",
    )
    ledger.transition_segment(
        expected_revision=revision + 1,
        mutation_id="mutation-custom-segment-waiting",
        segment_id="segment-one",
        to_state="awaiting_input_live",
    )
    open_legacy_question_batch(
        ledger,
        expected_revision=revision + 2,
        mutation_id="mutation-custom-question-batch",
        batch_id="batch-custom",
        turn_id="turn-one",
        segment_id="segment-one",
        call_id="call-custom",
        items=[{
            "question_id": "choice-custom",
            "kind": "single",
            "prompt": "选择或补充一个口径",
            "detail": None,
            "header": None,
            "intent": None,
            "options": [
                {"value_id": "known", "label": "已知口径", "description": None}
            ],
            "allow_skip": False,
        }],
        source="dsh.ask_user_question",
    )
    answer = {
        "items": [{
            "question_id": "choice-custom",
            "text": "用户补充口径",
            "value_ids": [],
            "skipped": False,
        }]
    }
    submit_legacy_question_batch_answer(
        ledger,
        expected_revision=revision + 3,
        mutation_id="mutation-custom-question-answer",
        batch_id="batch-custom",
        answer=answer,
    )
    assert ledger.projection()["questions"][0]["answer"] == answer


def test_question_batch_rejects_partial_reordered_or_unapproved_skip(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.transition_turn(
        expected_revision=revision,
        mutation_id="mutation-invalid-batch-turn-waiting",
        turn_id="turn-one",
        to_state="awaiting_input_live",
    )
    ledger.transition_segment(
        expected_revision=revision + 1,
        mutation_id="mutation-invalid-batch-segment-waiting",
        segment_id="segment-one",
        to_state="awaiting_input_live",
    )
    item = {
        "question_id": "confirm-one",
        "kind": "confirmation",
        "prompt": "确认继续？",
        "detail": None,
        "header": "确认",
        "intent": "confirmation",
        "options": [],
        "allow_skip": False,
    }
    open_legacy_question_batch(
        ledger,
        expected_revision=revision + 2,
        mutation_id="mutation-invalid-question-batch",
        batch_id="batch-one",
        turn_id="turn-one",
        segment_id="segment-one",
        call_id="call-one",
        items=[item],
        source="dsh.ask_user_question",
    )
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        submit_legacy_question_batch_answer(
            ledger,
            expected_revision=revision + 3,
            mutation_id="mutation-invalid-question-batch-answer",
            batch_id="batch-one",
            answer={
                "items": [
                    {
                        "question_id": "confirm-one",
                        "text": None,
                        "value_ids": [],
                        "skipped": True,
                    }
                ]
            },
        )
    assert error.value.code == "workbench_question_batch_answer_invalid"
    assert ledger.read()["revision"] == revision + 3


def test_question_batch_wait_open_is_one_atomic_fact_and_exact_retry(ledger):
    revision = append_turn_and_segment(ledger)
    items = [{
        "question_id": "text-one",
        "kind": "text",
        "prompt": "请补充说明",
        "detail": None,
        "header": None,
        "intent": None,
        "options": [],
        "allow_skip": False,
    }]
    opened = wait_and_open_legacy_question_batch(
        ledger,
        expected_revision=revision,
        mutation_id="mutation-atomic-wait-open",
        batch_id="batch-atomic",
        turn_id="turn-one",
        segment_id="segment-one",
        call_id="call-atomic",
        items=items,
        source="dsh.ask_user_question",
    )
    retried = wait_and_open_legacy_question_batch(
        ledger,
        expected_revision=revision,
        mutation_id="mutation-atomic-wait-open",
        batch_id="batch-atomic",
        turn_id="turn-one",
        segment_id="segment-one",
        call_id="call-atomic",
        items=items,
        source="dsh.ask_user_question",
    )
    projection = ledger.projection()
    assert opened["ledger_revision"] == revision + 1
    assert retried["status"] == "already_accepted"
    assert projection["turns"][0]["state"] == "awaiting_input_live"
    assert projection["segments"][0]["state"] == "awaiting_input_live"
    assert projection["questions"][0]["state"] == "open"
    assert projection["questions"][0]["call_id"] == "call-atomic"


def test_legacy_single_question_api_rejects_batch_kind(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.transition_turn(
        expected_revision=revision,
        mutation_id="mutation-legacy-turn-waiting",
        turn_id="turn-one",
        to_state="awaiting_input_live",
    )
    ledger.transition_segment(
        expected_revision=revision + 1,
        mutation_id="mutation-legacy-segment-waiting",
        segment_id="segment-one",
        to_state="awaiting_input_live",
    )
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        open_legacy_question(
            ledger,
            expected_revision=revision + 2,
            mutation_id="mutation-invalid-legacy-batch",
            question_id="batch-one",
            turn_id="turn-one",
            segment_id="segment-one",
            kind="batch",
            prompt="非法批次",
            options=[],
            source="test",
        )
    assert error.value.code == "workbench_question_kind_invalid"


def test_queue_steer_reorder_and_admission_receipt_follow_cas(ledger):
    revision = append_turn_and_segment(ledger)
    queue = ledger.enqueue(
        expected_revision=revision,
        mutation_id="mutation-queue",
        inbox_id="inbox-queue",
        kind="queue",
        message="下一轮继续核对能量",
        document_binding=binding(),
        target=None,
        client_request_id="request-queue",
    )
    steer = ledger.enqueue(
        expected_revision=revision + 1,
        mutation_id="mutation-steer",
        inbox_id="inbox-steer",
        kind="steer",
        message="当前步骤先检查元素底",
        document_binding=binding(),
        target={
            **target(),
            "segment_id": "segment-one",
            "epoch_id": "epoch-one",
            "runtime_incarnation": "runtime-one",
            "stage_epoch": 3,
        },
        client_request_id="request-steer",
    )
    assert queue["ledger_revision"] == revision + 1
    assert steer["ledger_revision"] == revision + 2
    ledger.reorder_inbox(
        expected_revision=revision + 2,
        mutation_id="mutation-reorder",
        inbox_ids=["inbox-steer", "inbox-queue"],
    )
    promoting = ledger.transition_inbox(
        expected_revision=revision + 3,
        mutation_id="mutation-promoting",
        inbox_id="inbox-steer",
        expected_item_revision=0,
        to_state="promoting",
    )
    admitted = ledger.transition_inbox(
        expected_revision=revision + 4,
        mutation_id="mutation-admitted",
        inbox_id="inbox-steer",
        expected_item_revision=1,
        to_state="admitted",
    )
    retried = ledger.transition_inbox(
        expected_revision=revision + 4,
        mutation_id="mutation-admitted",
        inbox_id="inbox-steer",
        expected_item_revision=1,
        to_state="admitted",
    )
    assert promoting["status"] == "accepted"
    assert admitted["admission_receipt"] == retried["admission_receipt"]
    assert retried["ledger_revision"] == admitted["ledger_revision"]
    projection = ledger.projection()
    assert [item["inbox_id"] for item in projection["inbox"]] == [
        "inbox-steer",
        "inbox-queue",
    ]
    assert projection["inbox"][0]["state"] == "admitted"
    outline = projection["turn_outline"][0]
    assert outline["seq_end"] == admitted["source_cursor"]
    assert outline["markers"] == ["turn:streaming", "inbox:admitted"]


def test_steer_ack_preserves_external_runtime_evidence(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.enqueue(
        expected_revision=revision,
        mutation_id="mutation-steer-with-ack",
        inbox_id="inbox-steer-with-ack",
        kind="steer",
        message="立即调整当前步骤",
        document_binding=binding(),
        target={
            **target(),
            "segment_id": "segment-one",
            "epoch_id": "epoch-one",
            "runtime_incarnation": "runtime-one",
            "stage_epoch": 3,
        },
        client_request_id="request-steer-with-ack",
    )
    ledger.transition_inbox(
        expected_revision=revision + 1,
        mutation_id="mutation-steer-with-ack-promoting",
        inbox_id="inbox-steer-with-ack",
        expected_item_revision=0,
        to_state="promoting",
    )
    accepted = ledger.acknowledge_steer(
        expected_revision=revision + 2,
        mutation_id="mutation-steer-with-ack-admitted",
        inbox_id="inbox-steer-with-ack",
        expected_item_revision=1,
        ack_status="accepted",
        message_id="message-steer-with-ack",
        dsh_message_id="dsh-message-with-ack",
    )
    retried = ledger.acknowledge_steer(
        expected_revision=revision + 2,
        mutation_id="mutation-steer-with-ack-admitted",
        inbox_id="inbox-steer-with-ack",
        expected_item_revision=1,
        ack_status="accepted",
        message_id="message-steer-with-ack",
        dsh_message_id="dsh-message-with-ack",
    )
    item = ledger.projection()["inbox"][0]
    assert accepted["admission_receipt"] == retried["admission_receipt"]
    assert item["state"] == "admitted"
    assert item["runtime_ack"] == {
        "status": "accepted",
        "message_id": "message-steer-with-ack",
        "dsh_message_id": "dsh-message-with-ack",
    }


def test_same_mutation_with_different_request_fails_closed(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.enqueue(
        expected_revision=revision,
        mutation_id="mutation-queue",
        inbox_id="inbox-queue",
        kind="queue",
        message="保留原消息",
        document_binding=binding(),
        target=None,
        client_request_id="request-queue",
    )
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.enqueue(
            expected_revision=revision,
            mutation_id="mutation-queue",
            inbox_id="inbox-queue",
            kind="queue",
            message="偷偷替换消息",
            document_binding=binding(),
            target=None,
            client_request_id="request-queue",
        )
    assert error.value.code == "workbench_mutation_conflict"
    assert ledger.projection()["inbox"][0]["message"] == "保留原消息"


def test_queue_edit_and_lineage_rebind_are_item_revision_cas(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.enqueue(
        expected_revision=revision,
        mutation_id="mutation-queue",
        inbox_id="inbox-queue",
        kind="queue",
        message="旧消息",
        document_binding=binding(),
        target=None,
        client_request_id="request-queue",
    )
    ledger.transition_inbox(
        expected_revision=revision + 1,
        mutation_id="mutation-needs-rebind",
        inbox_id="inbox-queue",
        expected_item_revision=0,
        to_state="needs_rebind",
        reason="document_lineage_changed",
    )
    revised = ledger.revise_inbox(
        expected_revision=revision + 2,
        mutation_id="mutation-revise",
        inbox_id="inbox-queue",
        expected_item_revision=1,
        message="按新文档继续",
        document_binding={
            **binding(5),
            "document_lineage_epoch": 1,
        },
        target=None,
    )
    retried = ledger.revise_inbox(
        expected_revision=revision + 2,
        mutation_id="mutation-revise",
        inbox_id="inbox-queue",
        expected_item_revision=1,
        message="按新文档继续",
        document_binding={
            **binding(5),
            "document_lineage_epoch": 1,
        },
        target=None,
    )
    assert retried["ledger_revision"] == revised["ledger_revision"]
    item = ledger.projection()["inbox"][0]
    assert item["revision"] == 2
    assert item["message"] == "按新文档继续"
    assert item["document_binding"]["document_lineage_epoch"] == 1
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.revise_inbox(
            expected_revision=revision + 3,
            mutation_id="mutation-revise-stale",
            inbox_id="inbox-queue",
            expected_item_revision=1,
            message="过期修改",
            document_binding=binding(),
            target=None,
        )
    assert error.value.code == "workbench_inbox_item_revision_conflict"


def test_checkpoint_batch_rebind_is_atomic_idempotent_and_receipted(ledger):
    revision = append_turn_and_segment(ledger)
    for suffix in ("one", "two"):
        ledger.enqueue(
            expected_revision=revision,
            mutation_id=f"mutation-queue-{suffix}",
            inbox_id=f"inbox-queue-{suffix}",
            kind="queue",
            message=f"队列消息 {suffix}",
            document_binding=binding(),
            target=None,
            client_request_id=f"request-queue-{suffix}",
        )
        revision += 1
    items = [
        {
            "inbox_id": f"inbox-queue-{suffix}",
            "expected_item_revision": 0,
            "old_document_binding": binding(),
            "new_document_binding": {
                **binding(8),
                "document_lineage_epoch": 1,
            },
        }
        for suffix in ("one", "two")
    ]
    rebound = ledger.rebind_inbox_batch(
        expected_revision=revision,
        mutation_id="mutation-checkpoint-rebind",
        items=items,
    )
    retried = ledger.rebind_inbox_batch(
        expected_revision=revision,
        mutation_id="mutation-checkpoint-rebind",
        items=items,
    )
    assert rebound["rebind_receipt"] == retried["rebind_receipt"]
    assert retried["status"] == "already_accepted"
    projected = ledger.projection()["inbox"]
    assert [row["state"] for row in projected] == [
        "needs_rebind",
        "needs_rebind",
    ]
    assert [row["revision"] for row in projected] == [1, 1]
    assert all(
        row["document_binding"]["document_lineage_epoch"] == 1
        for row in projected
    )


def test_checkpoint_batch_rebind_remains_idempotent_at_locked_append_boundary(
    ledger, monkeypatch
):
    """A concurrent retry can miss the optimistic pre-read.

    The locked append boundary must still recognise the Host-generated
    rebind receipt in the already committed event instead of reporting a
    mutation conflict.
    """

    revision = append_turn_and_segment(ledger)
    ledger.enqueue(
        expected_revision=revision,
        mutation_id="mutation-queue-race",
        inbox_id="inbox-queue-race",
        kind="queue",
        message="并发重绑",
        document_binding=binding(),
        target=None,
        client_request_id="request-queue-race",
    )
    revision += 1
    items = [{
        "inbox_id": "inbox-queue-race",
        "expected_item_revision": 0,
        "old_document_binding": binding(),
        "new_document_binding": {
            **binding(9),
            "document_lineage_epoch": 1,
        },
    }]
    monkeypatch.setattr(ledger, "_idempotent_receipt", lambda **_kwargs: None)

    accepted = ledger.rebind_inbox_batch(
        expected_revision=revision,
        mutation_id="mutation-rebind-race",
        items=items,
    )
    retried = ledger.rebind_inbox_batch(
        expected_revision=revision,
        mutation_id="mutation-rebind-race",
        items=items,
    )

    assert retried["status"] == "already_accepted"
    assert retried["rebind_receipt"] == accepted["rebind_receipt"]


def test_checkpoint_batch_rebind_rejects_one_stale_item_without_partial_write(ledger):
    revision = append_turn_and_segment(ledger)
    for suffix in ("one", "two"):
        ledger.enqueue(
            expected_revision=revision,
            mutation_id=f"mutation-queue-{suffix}",
            inbox_id=f"inbox-queue-{suffix}",
            kind="queue",
            message=f"队列消息 {suffix}",
            document_binding=binding(),
            target=None,
            client_request_id=f"request-queue-{suffix}",
        )
        revision += 1
    ledger.revise_inbox(
        expected_revision=revision,
        mutation_id="mutation-queue-two-revise",
        inbox_id="inbox-queue-two",
        expected_item_revision=0,
        message="已并发修改",
        document_binding=binding(),
        target=None,
    )
    revision += 1
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.rebind_inbox_batch(
            expected_revision=revision,
            mutation_id="mutation-stale-checkpoint-rebind",
            items=[
                {
                    "inbox_id": f"inbox-queue-{suffix}",
                    "expected_item_revision": 0,
                    "old_document_binding": binding(),
                    "new_document_binding": {
                        **binding(8),
                        "document_lineage_epoch": 1,
                    },
                }
                for suffix in ("one", "two")
            ],
        )
    assert error.value.code == "workbench_inbox_item_revision_conflict"
    assert [row["state"] for row in ledger.projection()["inbox"]] == [
        "queued",
        "queued",
    ]


@pytest.mark.parametrize(
    "message",
    [
        "api_key: sk-not-allowed-12345678",
        r"打开 C:\\Users\\private\\answer.json",
        "读取 /home/user/private.json",
    ],
)
def test_visible_text_rejects_credentials_and_local_paths(ledger, message):
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.enqueue(
            expected_revision=0,
            mutation_id="mutation-sensitive",
            inbox_id="inbox-sensitive",
            kind="queue",
            message=message,
            document_binding=binding(),
            target=None,
            client_request_id="request-sensitive",
        )
    assert error.value.code == "workbench_inbox_message_sensitive"
    assert not (ledger.root / LEDGER_FILE).exists()


def test_stale_revision_and_invalid_transition_do_not_write(ledger):
    ledger.create_turn(
        expected_revision=0, mutation_id="mutation-turn", turn_id="turn-one"
    )
    with pytest.raises(NativeWorkbenchLedgerError) as stale:
        ledger.start_segment(
            expected_revision=0,
            mutation_id="mutation-segment-stale",
            turn_id="turn-one",
            segment_id="segment-one",
            epoch_id="epoch-one",
            runtime_incarnation="runtime-one",
            stage_epoch=0,
        )
    assert stale.value.code == "workbench_stale_revision"
    with pytest.raises(NativeWorkbenchLedgerError) as invalid:
        ledger.transition_turn(
            expected_revision=1,
            mutation_id="mutation-turn-invalid",
            turn_id="turn-one",
            to_state="completed",
        )
    assert invalid.value.code == "workbench_turn_transition_invalid"
    assert ledger.read()["revision"] == 1


def test_turn_cannot_run_without_one_live_execution_segment(ledger):
    ledger.create_turn(
        expected_revision=0,
        mutation_id="mutation-turn",
        turn_id="turn-one",
    )
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.transition_turn(
            expected_revision=1,
            mutation_id="mutation-segmentless-stream",
            turn_id="turn-one",
            to_state="streaming",
        )
    assert error.value.code == "workbench_turn_live_segment_missing"
    assert ledger.read()["revision"] == 1


def test_corrupt_or_duplicate_key_storage_fails_closed(ledger):
    ledger.root.mkdir(parents=True, exist_ok=True)
    (ledger.root / LEDGER_FILE).write_text(
        f'{{"contract":"{CONTRACT}","contract":"{CONTRACT}"}}',
        encoding="utf-8",
    )
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.read()
    assert error.value.code == "workbench_storage_invalid"


def test_tampered_event_stream_cannot_be_replayed(ledger):
    ledger.create_turn(
        expected_revision=0, mutation_id="mutation-turn", turn_id="turn-one"
    )
    path = ledger.root / LEDGER_FILE
    value = json.loads(path.read_text(encoding="utf-8"))
    value["events"][0]["seq"] = 2
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.projection()
    assert error.value.code == "workbench_event_sequence_invalid"


def test_unreviewed_payload_fields_and_raw_reasoning_fail_closed(ledger):
    ledger.create_turn(
        expected_revision=0, mutation_id="mutation-turn", turn_id="turn-one"
    )
    path = ledger.root / LEDGER_FILE
    value = json.loads(path.read_text(encoding="utf-8"))
    value["events"][0]["payload"]["raw_reasoning"] = "private chain"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.read()
    assert error.value.code == "workbench_turn_created_payload_invalid"


def test_legacy_writable_outline_event_is_rejected_as_second_authority(ledger):
    ledger.root.mkdir(parents=True, exist_ok=True)
    value = {
        "contract": CONTRACT,
        "conversation_id": "conversation-one",
        "revision": 1,
        "source_cursor": 1,
        "events": [
            {
                "seq": 1,
                "event_id": "event_" + "a" * 32,
                "mutation_id": "mutation-legacy-outline",
                "type": "outline.node",
                "occurred_at": "2026-10-24T00:00:00Z",
                "payload": {
                    "node_id": "node-one",
                    "turn_id": "turn-one",
                    "segment_id": None,
                    "kind": "assistant_message",
                    "summary": "独立摘要不能成为事实",
                    "markers": [],
                },
            }
        ],
    }
    (ledger.root / LEDGER_FILE).write_text(
        json.dumps(value, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.read()
    assert error.value.code == "workbench_legacy_outline_event_unsupported"
    assert not hasattr(ledger, "append_outline_node")


def test_failed_atomic_publication_preserves_previous_authority(ledger, monkeypatch):
    ledger.create_turn(
        expected_revision=0, mutation_id="mutation-turn", turn_id="turn-one"
    )
    before = (ledger.root / LEDGER_FILE).read_bytes()

    def fail_write(*_args, **_kwargs):
        raise PlanError("simulated write failure")

    monkeypatch.setattr(ledger_module, "write_owned", fail_write)
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.start_segment(
            expected_revision=1,
            mutation_id="mutation-segment",
            turn_id="turn-one",
            segment_id="segment-one",
            epoch_id="epoch-one",
            runtime_incarnation="runtime-one",
            stage_epoch=0,
        )
    assert error.value.code == "workbench_storage_invalid"
    assert (ledger.root / LEDGER_FILE).read_bytes() == before


def test_two_instances_share_file_lock_and_only_one_cas_wins(tmp_path):
    first = NativeWorkbenchLedger(tmp_path, conversation_id="conversation-one")
    second = NativeWorkbenchLedger(tmp_path, conversation_id="conversation-one")

    def create(instance, suffix):
        try:
            return instance.create_turn(
                expected_revision=0,
                mutation_id=f"mutation-{suffix}",
                turn_id=f"turn-{suffix}",
            )["status"]
        except NativeWorkbenchLedgerError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(create, first, "one"),
            pool.submit(create, second, "two"),
        ]
        results = sorted(future.result() for future in futures)
    assert results == ["accepted", "workbench_stale_revision"]
    assert first.read()["revision"] == 1


def test_cursor_bounds_and_exact_root_contract(ledger):
    ledger.create_turn(
        expected_revision=0, mutation_id="mutation-turn", turn_id="turn-one"
    )
    with pytest.raises(NativeWorkbenchLedgerError) as ahead:
        ledger.events_after(2)
    assert ahead.value.code == "workbench_after_seq_ahead"
    raw = ledger.read()
    assert set(raw) == {
        "contract",
        "conversation_id",
        "revision",
        "source_cursor",
        "events",
    }
    assert raw["contract"] == CONTRACT


def test_only_one_live_turn_and_terminal_requires_settled_children(ledger):
    revision = append_turn_and_segment(ledger)
    with pytest.raises(NativeWorkbenchLedgerError) as live_turn:
        ledger.create_turn(
            expected_revision=revision,
            mutation_id="mutation-turn-two-too-early",
            turn_id="turn-two",
        )
    assert live_turn.value.code == "workbench_live_turn_exists"

    with pytest.raises(NativeWorkbenchLedgerError) as live_segment:
        ledger.transition_turn(
            expected_revision=revision,
            mutation_id="mutation-turn-complete-too-early",
            turn_id="turn-one",
            to_state="completed",
        )
    assert live_segment.value.code == "workbench_turn_segment_not_terminal"
    assert ledger.read()["revision"] == revision

    ledger.transition_segment(
        expected_revision=revision,
        mutation_id="mutation-segment-completed",
        segment_id="segment-one",
        to_state="completed",
    )
    ledger.transition_turn(
        expected_revision=revision + 1,
        mutation_id="mutation-turn-completed",
        turn_id="turn-one",
        to_state="completed",
    )
    accepted = ledger.create_turn(
        expected_revision=revision + 2,
        mutation_id="mutation-turn-two",
        turn_id="turn-two",
    )
    assert accepted["status"] == "accepted"


def test_provider_boundary_unknown_can_converge_to_interrupted(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.transition_segment(
        expected_revision=revision,
        mutation_id="mutation-segment-interrupted",
        segment_id="segment-one",
        to_state="interrupted",
        reason="provider_outcome_unknown",
    )
    ledger.transition_turn(
        expected_revision=revision + 1,
        mutation_id="mutation-turn-interrupted",
        turn_id="turn-one",
        to_state="interrupted",
        reason="provider_outcome_unknown",
    )
    projection = ledger.projection()
    assert projection["segments"][0]["state"] == "interrupted"
    assert projection["turns"][0]["state"] == "interrupted"


def test_suspended_turn_cannot_resume_before_answer_receipt(ledger):
    revision = open_live_question(ledger, append_turn_and_segment(ledger))
    ledger.transition_turn(
        expected_revision=revision,
        mutation_id="mutation-turn-suspended",
        turn_id="turn-one",
        to_state="awaiting_input_suspended",
    )
    ledger.transition_segment(
        expected_revision=revision + 1,
        mutation_id="mutation-segment-suspended",
        segment_id="segment-one",
        to_state="suspended",
    )
    ledger.suspend_question(
        expected_revision=revision + 2,
        mutation_id="mutation-question-suspended",
        question_id="question-one",
    )

    with pytest.raises(NativeWorkbenchLedgerError) as error:
        ledger.transition_turn(
            expected_revision=revision + 3,
            mutation_id="mutation-premature-resume",
            turn_id="turn-one",
            to_state="resuming",
        )
    assert error.value.code == "workbench_turn_question_not_resumable"
    assert ledger.read()["revision"] == revision + 3


def test_inbox_targets_and_kind_specific_promotion_fail_closed(ledger):
    revision = append_turn_and_segment(ledger)
    with pytest.raises(NativeWorkbenchLedgerError) as stale:
        ledger.enqueue(
            expected_revision=revision,
            mutation_id="mutation-stale-steer",
            inbox_id="inbox-stale-steer",
            kind="steer",
            message="投递到不存在的执行段",
            document_binding=binding(),
            target=target(),
            client_request_id="request-stale-steer",
        )
    assert stale.value.code == "workbench_inbox_target_stale"

    ledger.enqueue(
        expected_revision=revision,
        mutation_id="mutation-queue",
        inbox_id="inbox-queue",
        kind="queue",
        message="下一轮再处理",
        document_binding=binding(),
        target=None,
        client_request_id="request-queue",
    )
    with pytest.raises(NativeWorkbenchLedgerError) as active:
        ledger.transition_inbox(
            expected_revision=revision + 1,
            mutation_id="mutation-queue-promote-too-early",
            inbox_id="inbox-queue",
            expected_item_revision=0,
            to_state="promoting",
        )
    assert active.value.code == "workbench_queue_turn_still_active"

    ledger.enqueue(
        expected_revision=revision + 1,
        mutation_id="mutation-stop-and-send",
        inbox_id="inbox-stop-and-send",
        kind="stop_and_send",
        message="停止后发送",
        document_binding=binding(),
        target={
            **target(),
            "segment_id": "segment-one",
            "epoch_id": "epoch-one",
            "runtime_incarnation": "runtime-one",
            "stage_epoch": 3,
        },
        client_request_id="request-stop-and-send",
    )
    with pytest.raises(NativeWorkbenchLedgerError) as unsettled:
        ledger.transition_inbox(
            expected_revision=revision + 2,
            mutation_id="mutation-stop-promote-too-early",
            inbox_id="inbox-stop-and-send",
            expected_item_revision=0,
            to_state="promoting",
        )
    assert unsettled.value.code == "workbench_stop_and_send_not_settled"
    assert ledger.read()["revision"] == revision + 2


def test_promoting_can_record_unknown_ack_without_replay(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.enqueue(
        expected_revision=revision,
        mutation_id="mutation-steer",
        inbox_id="inbox-steer",
        kind="steer",
        message="立即调整当前步骤",
        document_binding=binding(),
        target={
            **target(),
            "segment_id": "segment-one",
            "epoch_id": "epoch-one",
            "runtime_incarnation": "runtime-one",
            "stage_epoch": 3,
        },
        client_request_id="request-steer",
    )
    ledger.transition_inbox(
        expected_revision=revision + 1,
        mutation_id="mutation-steer-promoting",
        inbox_id="inbox-steer",
        expected_item_revision=0,
        to_state="promoting",
    )
    receipt = ledger.transition_inbox(
        expected_revision=revision + 2,
        mutation_id="mutation-steer-unknown",
        inbox_id="inbox-steer",
        expected_item_revision=1,
        to_state="outcome_unknown",
        reason="runtime_ack_unknown",
    )
    assert receipt["status"] == "accepted"
    assert ledger.projection()["inbox"][0]["state"] == "outcome_unknown"


def test_queue_admission_requires_one_durable_predecessor_turn(ledger):
    revision = append_turn_and_segment(ledger)
    ledger.enqueue(
        expected_revision=revision,
        mutation_id="mutation-queue",
        inbox_id="inbox-queue",
        kind="queue",
        message="下一轮继续",
        document_binding=binding(),
        target=None,
        client_request_id="request-queue",
    )
    ledger.transition_segment(
        expected_revision=revision + 1,
        mutation_id="mutation-segment-completed",
        segment_id="segment-one",
        to_state="completed",
    )
    ledger.transition_turn(
        expected_revision=revision + 2,
        mutation_id="mutation-turn-completed",
        turn_id="turn-one",
        to_state="completed",
    )
    ledger.transition_inbox(
        expected_revision=revision + 3,
        mutation_id="mutation-queue-promoting",
        inbox_id="inbox-queue",
        expected_item_revision=0,
        to_state="promoting",
    )

    with pytest.raises(NativeWorkbenchLedgerError) as missing_turn:
        ledger.transition_inbox(
            expected_revision=revision + 4,
            mutation_id="mutation-queue-admitted-too-early",
            inbox_id="inbox-queue",
            expected_item_revision=1,
            to_state="admitted",
        )
    assert missing_turn.value.code == "workbench_inbox_admission_turn_missing"

    with pytest.raises(NativeWorkbenchLedgerError) as missing_predecessor:
        ledger.create_turn(
            expected_revision=revision + 4,
            mutation_id="mutation-unlinked-turn-two",
            turn_id="turn-two",
        )
    assert missing_predecessor.value.code == "workbench_turn_predecessor_required"

    ledger.create_turn(
        expected_revision=revision + 4,
        mutation_id="mutation-turn-two",
        turn_id="turn-two",
        predecessor_inbox_id="inbox-queue",
    )
    ledger.start_segment(
        expected_revision=revision + 5,
        mutation_id="mutation-segment-two",
        turn_id="turn-two",
        segment_id="segment-two",
        epoch_id="epoch-two",
        runtime_incarnation="runtime-two",
        stage_epoch=4,
    )
    with pytest.raises(NativeWorkbenchLedgerError) as not_admitted:
        ledger.transition_turn(
            expected_revision=revision + 6,
            mutation_id="mutation-turn-two-streaming-too-early",
            turn_id="turn-two",
            to_state="streaming",
        )
    assert not_admitted.value.code == "workbench_turn_predecessor_not_admitted"
    admitted = ledger.transition_inbox(
        expected_revision=revision + 6,
        mutation_id="mutation-queue-admitted",
        inbox_id="inbox-queue",
        expected_item_revision=1,
        to_state="admitted",
    )
    ledger.transition_turn(
        expected_revision=revision + 7,
        mutation_id="mutation-turn-two-streaming",
        turn_id="turn-two",
        to_state="streaming",
    )
    projection = ledger.projection()
    assert admitted["admission_receipt"]
    assert projection["turns"][1]["predecessor_inbox_id"] == "inbox-queue"
    assert projection["inbox"][0]["state"] == "admitted"
    assert projection["turn_outline"][1]["markers"] == [
        "turn:streaming",
        "inbox:admitted",
    ]


def test_generated_receipts_must_be_unique(tmp_path):
    duplicate_receipt = "receipt_" + "x" * 43
    subject = NativeWorkbenchLedger(
        tmp_path,
        conversation_id="conversation-one",
        receipt_factory=lambda: duplicate_receipt,
    )
    revision = append_turn_and_segment(subject)
    exact_target = {
        **target(),
        "segment_id": "segment-one",
        "epoch_id": "epoch-one",
        "runtime_incarnation": "runtime-one",
        "stage_epoch": 3,
    }
    for suffix in ("one", "two"):
        subject.enqueue(
            expected_revision=revision,
            mutation_id=f"mutation-steer-{suffix}",
            inbox_id=f"inbox-steer-{suffix}",
            kind="steer",
            message=f"调整 {suffix}",
            document_binding=binding(),
            target=exact_target,
            client_request_id=f"request-steer-{suffix}",
        )
        revision += 1
    subject.transition_inbox(
        expected_revision=revision,
        mutation_id="mutation-promote-one",
        inbox_id="inbox-steer-one",
        expected_item_revision=0,
        to_state="promoting",
    )
    subject.transition_inbox(
        expected_revision=revision + 1,
        mutation_id="mutation-admit-one",
        inbox_id="inbox-steer-one",
        expected_item_revision=1,
        to_state="admitted",
    )
    subject.transition_inbox(
        expected_revision=revision + 2,
        mutation_id="mutation-promote-two",
        inbox_id="inbox-steer-two",
        expected_item_revision=0,
        to_state="promoting",
    )
    with pytest.raises(NativeWorkbenchLedgerError) as duplicate:
        subject.transition_inbox(
            expected_revision=revision + 3,
            mutation_id="mutation-admit-two",
            inbox_id="inbox-steer-two",
            expected_item_revision=1,
            to_state="admitted",
        )
    assert duplicate.value.code == "workbench_inbox_admission_receipt_conflict"
    assert subject.read()["revision"] == revision + 3


def test_events_after_uses_host_sequence_not_list_offset(ledger, monkeypatch):
    monkeypatch.setattr(
        ledger,
        "_read_unlocked",
        lambda: {
            "contract": CONTRACT,
            "conversation_id": "conversation-one",
            "revision": 7,
            "source_cursor": 7,
            "events": [
                {"seq": 5, "type": "retained"},
                {"seq": 6, "type": "retained"},
                {"seq": 7, "type": "retained"},
            ],
        },
    )
    window = ledger.events_after(5, limit=2)
    assert [item["seq"] for item in window["events"]] == [6, 7]
    assert window["first_available_seq"] == 5
    with pytest.raises(NativeWorkbenchLedgerError) as trimmed:
        ledger.events_after(3)
    assert trimmed.value.code == "workbench_cursor_trimmed"


def test_outline_seq_end_tracks_question_and_segment_facts(ledger):
    revision = open_live_question(ledger, append_turn_and_segment(ledger))
    outline = ledger.projection()["turn_outline"][0]
    assert outline["seq_start"] == 1
    assert outline["seq_end"] == revision
    assert outline["markers"] == ["turn:awaiting_input_live", "question:open"]
    assert outline["excerpts_completeness"] == "partial"
    assert outline["excerpts"] == []


def test_projection_limit_is_rejected_before_persisting_unreadable_state(
    ledger, monkeypatch
):
    monkeypatch.setattr(ledger_module, "MAX_PROJECTION_ROWS", 1)
    ledger.create_turn(
        expected_revision=0,
        mutation_id="mutation-turn-one",
        turn_id="turn-one",
    )
    ledger.transition_turn(
        expected_revision=1,
        mutation_id="mutation-turn-one-failed",
        turn_id="turn-one",
        to_state="failed",
    )
    with pytest.raises(NativeWorkbenchLedgerError) as limit:
        ledger.create_turn(
            expected_revision=2,
            mutation_id="mutation-turn-two",
            turn_id="turn-two",
        )
    assert limit.value.code == "workbench_projection_size_limit"
    assert ledger.projection()["turns"][0]["turn_id"] == "turn-one"


def test_concurrent_same_mutation_is_exactly_once(tmp_path):
    first = NativeWorkbenchLedger(tmp_path, conversation_id="conversation-one")
    second = NativeWorkbenchLedger(tmp_path, conversation_id="conversation-one")

    def create(instance):
        return instance.create_turn(
            expected_revision=0,
            mutation_id="mutation-shared",
            turn_id="turn-one",
        )["status"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(create, (first, second)))
    assert results == ["accepted", "already_accepted"]
    assert first.read()["revision"] == 1


def test_answer_race_accepts_only_one_question_answer(ledger):
    revision = open_live_question(ledger, append_turn_and_segment(ledger))
    first = NativeWorkbenchLedger(ledger.root, conversation_id="conversation-one")
    second = NativeWorkbenchLedger(ledger.root, conversation_id="conversation-one")

    def answer(instance, suffix, option):
        try:
            return submit_legacy_question_answer(
                instance,
                expected_revision=revision,
                mutation_id=f"mutation-answer-{suffix}",
                question_id="question-one",
                answer={"text": None, "value_ids": [option]},
            )["status"]
        except NativeWorkbenchLedgerError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(answer, first, "one", "option-a"),
            pool.submit(answer, second, "two", "option-b"),
        ]
        results = sorted(future.result() for future in futures)
    assert results == ["accepted", "workbench_stale_revision"]
    question = first.projection()["questions"][0]
    assert question["state"] == "answer_submitting"
    assert question["answer"]["value_ids"] in (["option-a"], ["option-b"])


def test_ack_lost_after_atomic_publication_recovers_by_mutation_id(ledger, monkeypatch):
    ledger.create_turn(
        expected_revision=0,
        mutation_id="mutation-turn",
        turn_id="turn-one",
    )
    real_write = ledger_module.write_owned

    def write_then_lose_ack(*args, **kwargs):
        real_write(*args, **kwargs)
        raise PlanError("simulated lost acknowledgement")

    monkeypatch.setattr(ledger_module, "write_owned", write_then_lose_ack)
    with pytest.raises(NativeWorkbenchLedgerError) as lost:
        ledger.start_segment(
            expected_revision=1,
            mutation_id="mutation-segment",
            turn_id="turn-one",
            segment_id="segment-one",
            epoch_id="epoch-one",
            runtime_incarnation="runtime-one",
            stage_epoch=0,
        )
    assert lost.value.code == "workbench_storage_invalid"
    monkeypatch.setattr(ledger_module, "write_owned", real_write)

    recovered = ledger.start_segment(
        expected_revision=1,
        mutation_id="mutation-segment",
        turn_id="turn-one",
        segment_id="segment-one",
        epoch_id="epoch-one",
        runtime_incarnation="runtime-one",
        stage_epoch=0,
    )
    assert recovered["status"] == "already_accepted"
    assert ledger.read()["revision"] == 2


def test_oversized_event_is_rejected_before_publication(ledger):
    revision = open_live_question(ledger, append_turn_and_segment(ledger))
    before = ledger.read()
    with pytest.raises(NativeWorkbenchLedgerError) as error:
        open_legacy_question(
            ledger,
            expected_revision=revision,
            mutation_id="mutation-oversized-question",
            question_id="question-two",
            turn_id="turn-one",
            segment_id="segment-one",
            kind="single",
            prompt="选择一项",
            options=[
                {"value_id": f"option-{index}", "label": "字" * 1000}
                for index in range(32)
            ],
            source="workflow.numeric_model",
        )
    assert error.value.code == "workbench_event_size_limit"
    assert ledger.read() == before
