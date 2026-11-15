from __future__ import annotations

import hashlib

import pytest

from genshin_app.agent_chat.native_workbench_timeline_telemetry import (
    telemetry_bundle,
)
from genshin_app.agent_chat.native_workbench_timeline_views import (
    build_history_window,
    build_search_results,
    build_turn_outline,
)
from genshin_app.equipment.schema import PlanError


def _large_facts(turns: int = 1_000, events_per_turn: int = 10):
    outline = []
    timeline = []
    seq = 1
    for turn_index in range(turns):
        start = seq
        turn_id = f"turn-{turn_index:04d}"
        for event_index in range(events_per_turn):
            kind = "message" if event_index in {0, events_per_turn - 1} else "tool"
            role = "user" if event_index == 0 else "assistant" if event_index == events_per_turn - 1 else None
            timeline.append({
                "node_id": f"node-{turn_index:04d}-{event_index:02d}",
                "turn_id": turn_id,
                "segment_id": f"segment-{turn_index:04d}",
                "operation_id": f"call-{turn_index:04d}-{event_index:02d}" if kind == "tool" else None,
                "source_seq": seq,
                "occurred_at": f"2026-10-25T00:{turn_index % 60:02d}:{event_index:02d}Z",
                "kind": kind,
                "category": "provider" if kind == "message" else "engine",
                "role": role,
                "label": f"事件 {turn_index}-{event_index}",
                "text": f"可检索内容 {turn_index}-{event_index}",
                "state": "completed",
                "result_code": None,
                "resource_ref": None,
                "workflow_stage": "numeric_closure",
            })
            seq += 1
        outline.append({
            "turn_id": turn_id,
            "state": "completed",
            "seq_start": start,
            "seq_end": seq - 1,
            "segment_count": 1,
            "node_count": events_per_turn,
            "markers": ["turn:completed"],
            "excerpts_completeness": "complete",
            "excerpts": [{"node_id": f"node-{turn_index:04d}-00", "kind": "message", "text": f"可检索内容 {turn_index}-0"}],
        })
    foundation = {
        "conversation_id": "conversation-large",
        "source_cursor": seq - 1,
        "projection_revision": seq - 1,
        "completeness": {"state": "complete", "partial_fields": [], "reason": None},
        "turns": [{"turn_id": row["turn_id"], "state": "completed", "created_seq": row["seq_start"], "updated_seq": row["seq_end"]} for row in outline],
        "segments": [],
        "questions": [],
        "turn_outline": outline,
    }
    record = {"conversation_id": "conversation-large", "visible_messages": [], "visible_message_evicted_count": 9_000, "tool_settlement_refs": [], "usage_records": []}
    ledger = {"conversation_id": "conversation-large", "revision": seq - 1, "source_cursor": seq - 1, "events": [{"seq": row["source_seq"], "occurred_at": row["occurred_at"]} for row in timeline]}
    return record, foundation, timeline, ledger


def test_1000_turn_outline_is_bounded_and_bidirectionally_pageable():
    record, foundation, timeline, _ledger = _large_facts()
    first = build_turn_outline(conversation_id="conversation-large", record=record, foundation=foundation, timeline=timeline, limit=200)
    assert len(first["items"]) == 200
    assert first["total_turns"] == 1_000
    assert first["has_older"] is True
    second = build_turn_outline(conversation_id="conversation-large", record=record, foundation=foundation, timeline=timeline, before_seq=first["older_cursor"], limit=200)
    assert first["items"][-1]["turn_id"] != second["items"][0]["turn_id"]
    backwards = build_turn_outline(conversation_id="conversation-large", record=record, foundation=foundation, timeline=timeline, after_seq=second["newer_cursor"], limit=200)
    assert backwards["items"] == first["items"]


def test_10000_event_search_and_history_use_stable_seq_cursors():
    record, foundation, timeline, _ledger = _large_facts()
    digest = hashlib.sha256("可检索内容".encode()).hexdigest()
    seen = set()
    cursor = None
    while True:
        page = build_search_results(conversation_id="conversation-large", record=record, foundation=foundation, timeline=timeline, query="可检索内容", expected_digest=digest, limit=200, after_seq=cursor)
        ids = {row["stable_node_id"] for row in page["results"]}
        assert not seen.intersection(ids)
        seen.update(ids)
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert len(seen) == 10_000

    window = build_history_window(conversation_id="conversation-large", record=record, foundation=foundation, timeline=timeline, turn_id="turn-0500", source_seq=5_001, limit=4)
    assert len(window["nodes"]) == 4 and window["has_newer"]
    assert window["nodes"][0]["category"] in {"provider", "engine"}
    assert "segment_id" in window["nodes"][0]
    assert "operation_id" in window["nodes"][0]
    assert "occurred_at" in window["nodes"][0]
    newer = build_history_window(conversation_id="conversation-large", record=record, foundation=foundation, timeline=timeline, turn_id="turn-0500", source_seq=5_001, after_seq=window["newer_cursor"], limit=4)
    older = build_history_window(conversation_id="conversation-large", record=record, foundation=foundation, timeline=timeline, turn_id="turn-0500", source_seq=5_001, before_seq=newer["older_cursor"], limit=4)
    assert older["nodes"] == window["nodes"]


def test_telemetry_pages_timeline_without_loading_all_spans():
    record, foundation, timeline, ledger = _large_facts()
    bundle = telemetry_bundle(ledger, foundation, record, timeline, limit=300)
    trajectory = bundle["trajectory"]
    assert trajectory["total_spans"] == 11_000
    assert len(trajectory["spans"]) <= 300
    assert trajectory["has_older"] is True
    assert trajectory["completeness"] == "complete"
    assert all("arguments" not in repr(row) for row in trajectory["spans"])


def test_telemetry_adds_bounded_turn_and_segment_envelopes_with_stage_metadata():
    record, foundation, timeline, ledger = _large_facts(turns=1, events_per_turn=3)
    foundation["segments"] = [{
        "segment_id": "segment-0000",
        "turn_id": "turn-0000",
        "state": "completed",
        "created_seq": 1,
        "updated_seq": 3,
    }]

    trajectory = telemetry_bundle(
        ledger,
        foundation,
        record,
        timeline,
        limit=9,
    )["trajectory"]

    spans = {row["span_id"]: row for row in trajectory["spans"]}
    assert len(spans) <= 9
    assert trajectory["total_spans"] == 5
    assert spans["segment:segment-0000"]["parent_span_id"] == "turn:turn-0000"
    for index in range(3):
        event = spans[f"timeline:node-0000-{index:02d}"]
        assert event["parent_span_id"] == "segment:segment-0000"
        assert event["workflow_stage"] == "numeric_closure"
        assert spans["segment:segment-0000"]["seq_start"] <= event["seq_start"]
        assert event["seq_end"] <= spans["segment:segment-0000"]["seq_end"]


def test_telemetry_falls_back_to_turn_parent_when_segment_bounds_do_not_contain_event():
    record, foundation, timeline, ledger = _large_facts(turns=1, events_per_turn=3)
    foundation["segments"] = [{
        "segment_id": "segment-0000",
        "turn_id": "turn-0000",
        "state": "completed",
        "created_seq": 1,
        "updated_seq": 1,
    }]

    trajectory = telemetry_bundle(
        ledger,
        foundation,
        record,
        timeline,
        limit=9,
    )["trajectory"]
    spans = {row["span_id"]: row for row in trajectory["spans"]}
    assert spans["timeline:node-0000-00"]["parent_span_id"] == "segment:segment-0000"
    assert spans["timeline:node-0000-01"]["parent_span_id"] == "turn:turn-0000"
    assert spans["timeline:node-0000-02"]["parent_span_id"] == "turn:turn-0000"


def test_telemetry_does_not_emit_dangling_or_out_of_bounds_lifecycle_parents():
    record, foundation, timeline, ledger = _large_facts(turns=1, events_per_turn=3)
    foundation["turns"][0]["updated_seq"] = 1
    foundation["segments"] = [{
        "segment_id": "segment-0000",
        "turn_id": "different-turn",
        "state": "completed",
        "created_seq": 1,
        "updated_seq": 3,
    }]

    trajectory = telemetry_bundle(
        ledger,
        foundation,
        record,
        timeline,
        limit=9,
    )["trajectory"]
    spans = {row["span_id"]: row for row in trajectory["spans"]}
    assert "segment:segment-0000" not in spans
    assert spans["timeline:node-0000-00"]["parent_span_id"] == "turn:turn-0000"
    assert spans["timeline:node-0000-01"]["parent_span_id"] is None
    assert spans["timeline:node-0000-02"]["parent_span_id"] is None


def test_telemetry_operation_timing_is_scoped_to_turn_and_segment():
    record, foundation, timeline, ledger = _large_facts(turns=2, events_per_turn=3)
    for row in timeline:
        row["category"] = "engine"
        row["operation_id"] = "shared-operation-name"

    bundle = telemetry_bundle(ledger, foundation, record, timeline, limit=30)
    spans = {
        row["span_id"]: row for row in bundle["trajectory"]["spans"]
    }
    assert spans["timeline:node-0000-00"]["elapsed_ms"] == 2_000
    assert spans["timeline:node-0001-00"]["elapsed_ms"] == 2_000
    metrics = {
        row["metric_id"]: row for row in bundle["performance"]["metrics"]
    }
    assert metrics["operation_count:engine"]["value"] == 2
    assert metrics["operation_elapsed_ms:engine"]["value"] == 4_000
    assert metrics["operation_elapsed_ms:engine"]["precision"] == "exact"


def test_telemetry_never_exceeds_tiny_requested_page_limits():
    record, foundation, timeline, ledger = _large_facts(turns=1, events_per_turn=3)
    foundation["segments"] = [{
        "segment_id": "segment-0000",
        "turn_id": "turn-0000",
        "state": "completed",
        "created_seq": 1,
        "updated_seq": 3,
    }]

    for limit in (1, 2, 3):
        trajectory = telemetry_bundle(
            ledger,
            foundation,
            record,
            timeline,
            limit=limit,
        )["trajectory"]
        assert 1 <= len(trajectory["spans"]) <= limit


def test_legacy_visible_message_adapter_is_explicitly_partial():
    record, foundation, _timeline, _ledger = _large_facts(turns=1, events_per_turn=2)
    record["visible_message_evicted_count"] = 0
    record["visible_messages"] = [
        {"message_id": "legacy-user", "role": "user", "text": "旧消息", "workflow_stage": None, "user_turn": 1},
    ]
    record["last_turn"] = {"turn_id": "turn-0000"}
    result = build_turn_outline(conversation_id="conversation-large", record=record, foundation=foundation, timeline=None)
    assert result["completeness"] == "partial"
    assert "legacy_visible_message_fallback" in result["partial_reasons"]


def test_legacy_adapter_refuses_to_invent_sequences_outside_the_turn_range():
    record, foundation, _timeline, _ledger = _large_facts(turns=1, events_per_turn=1)
    record["visible_message_evicted_count"] = 0
    record["visible_messages"] = [
        {"message_id": "legacy-user", "role": "user", "text": "问题", "workflow_stage": None, "user_turn": 1},
        {"message_id": "legacy-answer", "role": "assistant", "text": "回答", "workflow_stage": None, "user_turn": 1},
    ]
    record["last_turn"] = {"turn_id": "turn-0000"}

    result = build_turn_outline(
        conversation_id="conversation-large",
        record=record,
        foundation=foundation,
        timeline=None,
    )

    assert result["completeness"] == "partial"
    assert "legacy_message_sequence_unproven" in result["partial_reasons"]


@pytest.mark.parametrize("field_value", [{"secret": "value"}, ["not", "text"]])
def test_outline_and_legacy_adapter_reject_non_string_display_text(field_value):
    record, foundation, _timeline, _ledger = _large_facts(turns=1, events_per_turn=2)
    foundation["turn_outline"][0]["excerpts"][0]["text"] = field_value
    with pytest.raises(PlanError) as outline_error:
        build_turn_outline(
            conversation_id="conversation-large",
            record=record,
            foundation=foundation,
            timeline=None,
        )
    assert outline_error.value.reason == "agent_workbench_projection_invalid"

    foundation["turn_outline"][0]["excerpts"][0]["text"] = "安全摘要"
    record["visible_message_evicted_count"] = 0
    record["visible_messages"] = [{
        "message_id": "legacy-user",
        "role": "user",
        "text": field_value,
        "workflow_stage": None,
        "user_turn": 1,
    }]
    record["last_turn"] = {"turn_id": "turn-0000"}
    with pytest.raises(PlanError) as message_error:
        build_turn_outline(
            conversation_id="conversation-large",
            record=record,
            foundation=foundation,
            timeline=None,
        )
    assert message_error.value.reason == "agent_workbench_projection_invalid"


def test_views_reject_a_session_record_from_another_conversation():
    record, foundation, _timeline, _ledger = _large_facts(
        turns=1, events_per_turn=2
    )
    record["conversation_id"] = "conversation-other"

    with pytest.raises(PlanError) as error:
        build_turn_outline(
            conversation_id="conversation-large",
            record=record,
            foundation=foundation,
            timeline=None,
        )

    assert error.value.reason == "agent_workbench_projection_invalid"


def test_outline_accepts_overlapping_turn_lifetimes_but_keeps_creation_order():
    record, foundation, timeline, _ledger = _large_facts(
        turns=2, events_per_turn=2
    )
    # A terminal Turn can be touched by a later stop-and-send admission after
    # its successor has already been created.  Lifecycle ranges may therefore
    # overlap even though Turn creation sequence remains strictly ordered.
    foundation["turn_outline"][0]["seq_end"] = 4

    result = build_turn_outline(
        conversation_id="conversation-large",
        record=record,
        foundation=foundation,
        timeline=timeline,
    )

    assert [row["turn_id"] for row in result["items"]] == [
        "turn-0000",
        "turn-0001",
    ]


def test_timeline_node_must_belong_to_its_claimed_turn_lifecycle_range():
    record, foundation, timeline, _ledger = _large_facts(
        turns=2, events_per_turn=2
    )
    timeline[0]["turn_id"] = "turn-0001"

    with pytest.raises(PlanError) as error:
        build_turn_outline(
            conversation_id="conversation-large",
            record=record,
            foundation=foundation,
            timeline=timeline,
        )

    assert error.value.reason == "agent_workbench_projection_invalid"


def test_timeline_must_materialize_the_outline_node_counts_exactly():
    record, foundation, timeline, _ledger = _large_facts(
        turns=1, events_per_turn=2
    )

    with pytest.raises(PlanError) as error:
        build_turn_outline(
            conversation_id="conversation-large",
            record=record,
            foundation=foundation,
            timeline=timeline[:-1],
        )

    assert error.value.reason == "agent_workbench_projection_invalid"


def test_legacy_adapter_refuses_overlapping_synthetic_sequences():
    record, foundation, _timeline, _ledger = _large_facts(
        turns=2, events_per_turn=2
    )
    foundation["turn_outline"][0]["seq_end"] = 4
    record["visible_message_evicted_count"] = 0
    record["visible_messages"] = [
        {
            "message_id": f"legacy-{index}",
            "role": "user" if index in {0, 3} else "assistant",
            "text": f"消息 {index}",
            "workflow_stage": None,
            "user_turn": 1 if index < 3 else 2,
        }
        for index in range(4)
    ]
    record["last_turn"] = {"turn_id": "turn-0001"}

    result = build_turn_outline(
        conversation_id="conversation-large",
        record=record,
        foundation=foundation,
        timeline=None,
    )

    assert result["completeness"] == "partial"
    assert "legacy_message_sequence_unproven" in result["partial_reasons"]
