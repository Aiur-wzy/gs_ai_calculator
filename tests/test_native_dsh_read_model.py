"""Strict tests for the DSH-owned generic conversation read boundary."""

from __future__ import annotations

import copy

import pytest

from genshin_app.agent_chat.native_dsh_read_model import (
    history_window_view,
    normalize_history_payload,
    normalize_projection_payload,
    normalize_search_payload,
    search_results_view,
)
from genshin_app.equipment.schema import PlanError

SESSION_ID = "dsh-session-read-model"


def _projection() -> dict:
    return {
        "contract": "genshin-native-dsh-projection-v1",
        "session_id": SESSION_ID,
        "as_of_seq": 12,
        "values": {
            "contextBreakdown": {
                "systemTokens": 1,
                "toolsTokens": 2,
                "messageTokens": 3,
            },
            "contextPressure": {
                "projectedTokens": 6,
                "pressureTokens": 5,
                "contextWindow": 64_000,
            },
            "sessionStats": {"turns": 1, "steps": 1},
            "tokenUsage": {
                "uncachedInputTokens": 3,
                "outputTokens": 2,
                "cacheReadTokens": 0,
                "cacheWriteTokens": 0,
            },
            "turnOutline": [
                {
                    "turn": 1,
                    "seq": 1,
                    "prompt": "检查动作轴",
                    "response": "完成",
                }
            ],
            "userQuestions": {"active": [], "settled": []},
        },
    }


def _history_item(
    stable_id: str,
    *,
    seq: int,
    kind: str = "message",
    text: str | None = "完成",
    raw_available: bool = False,
    raw_included: bool = False,
) -> dict:
    return {
        "stable_id": stable_id,
        "seq": seq,
        "time_ms": 1_700_000_000_000 + seq,
        "event_type": "assistant/message",
        "kind": kind,
        "turn": 1,
        "step": 1,
        "role": "assistant",
        "label": "推理过程" if kind == "reasoning" else "Agent 回复",
        "text": text,
        "call_id": None,
        "name": None,
        "state": "completed",
        "raw_reasoning_available": raw_available,
        "raw_reasoning_included": raw_included,
    }


def _history(*items: dict) -> dict:
    return {
        "contract": "genshin-native-dsh-history-v1",
        "session_id": SESSION_ID,
        "as_of_seq": 12,
        "items": list(items),
        "has_older": False,
        "has_newer": False,
        "older_cursor": None,
        "newer_cursor": None,
        "total_items": len(items),
    }


def test_projection_binding_is_exact_and_detached() -> None:
    source = _projection()
    checked = normalize_projection_payload(
        source, expected_session_id=SESSION_ID
    )
    source["values"]["turnOutline"][0]["prompt"] = "changed"

    assert checked["values"]["turnOutline"][0]["prompt"] == "检查动作轴"
    with pytest.raises(PlanError) as caught:
        normalize_projection_payload(checked, expected_session_id="other-session")
    assert caught.value.reason == "native_dsh_projection_binding_mismatch"


def test_projection_validates_durable_user_questions_strictly() -> None:
    source = _projection()
    source["values"]["userQuestions"] = {
        "active": [
            {
                "callId": "call-question-one",
                "state": "continued",
                "questions": [
                    {
                        "id": "question-one",
                        "question": "采用哪种方案？",
                        "header": "方案",
                        "options": [
                            {"label": "A", "description": "采用方案A"},
                            {"label": "B"},
                        ],
                        "multiSelect": False,
                    }
                ],
            }
        ],
        "settled": [
            {
                "callId": "call-question-old",
                "answers": [
                    {"id": "question-old", "selected": ["A"]}
                ],
            }
        ],
    }

    checked = normalize_projection_payload(
        source, expected_session_id=SESSION_ID
    )

    assert checked["values"]["userQuestions"]["active"][0]["state"] == (
        "continued"
    )
    source["values"]["userQuestions"]["active"][0]["questions"][0][
        "question"
    ] = "changed"
    assert (
        checked["values"]["userQuestions"]["active"][0]["questions"][0][
            "question"
        ]
        == "采用哪种方案？"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["active"][0].update({"unknown": True}),
        lambda value: value["active"][0]["questions"][0].update(
            {"multiSelect": "yes"}
        ),
        lambda value: value["active"][0]["questions"][0]["options"].append(
            {"label": "A"}
        ),
        lambda value: value["settled"].append(
            {"callId": "call-question-one", "answers": []}
        ),
    ],
)
def test_projection_rejects_ambiguous_or_extended_question_state(mutation) -> None:
    source = _projection()
    source["values"]["userQuestions"] = {
        "active": [
            {
                "callId": "call-question-one",
                "state": "open",
                "questions": [
                    {
                        "id": "question-one",
                        "question": "采用哪种方案？",
                        "options": [{"label": "A"}],
                    }
                ],
            }
        ],
        "settled": [],
    }
    mutation(source["values"]["userQuestions"])

    with pytest.raises(PlanError) as caught:
        normalize_projection_payload(source, expected_session_id=SESSION_ID)
    assert caught.value.reason == "native_dsh_question_projection_invalid"


def test_history_keeps_one_event_atomic_with_multiple_stable_items() -> None:
    source = _history(
        _history_item(
            "reasoning-one",
            seq=8,
            kind="reasoning",
            text=None,
            raw_available=True,
        ),
        _history_item("answer-one", seq=8),
    )

    checked = normalize_history_payload(
        source,
        expected_session_id=SESSION_ID,
        raw_reasoning_authorized=False,
    )
    window = history_window_view(
        checked,
        conversation_id="conversation-one",
        turn_id="dsh-turn-1",
        source_seq=8,
        stable_node_id="dsh:answer-one",
    )

    assert [row["source_seq"] for row in window["nodes"]] == [8, 8]
    assert [row["stable_node_id"] for row in window["nodes"]] == [
        "dsh:reasoning-one",
        "dsh:answer-one",
    ]


def test_raw_reasoning_requires_explicit_authorization() -> None:
    payload = _history(
        _history_item(
            "reasoning-one",
            seq=8,
            kind="reasoning",
            text="内部推理",
            raw_available=True,
            raw_included=True,
        )
    )

    with pytest.raises(PlanError) as caught:
        normalize_history_payload(
            payload,
            expected_session_id=SESSION_ID,
            raw_reasoning_authorized=False,
        )
    assert caught.value.reason == "native_dsh_reasoning_not_authorized"
    checked = normalize_history_payload(
        payload,
        expected_session_id=SESSION_ID,
        raw_reasoning_authorized=True,
    )
    assert checked["items"][0]["text"] == "内部推理"


def test_search_rejects_reasoning_and_preserves_opaque_cursor() -> None:
    source = {
        "contract": "genshin-native-dsh-search-v1",
        "session_id": SESSION_ID,
        "as_of_seq": 12,
        "items": [
            {
                "stable_id": "answer-one",
                "seq": 8,
                "time_ms": 1_700_000_000_008,
                "event_type": "assistant/message",
                "turn": 1,
                "kind": "message",
                "snippet": "动作轴完成",
            }
        ],
        "next_cursor": "fts:v1:opaque-page-two",
    }
    checked = normalize_search_payload(
        source, expected_session_id=SESSION_ID
    )
    projected = search_results_view(
        checked, conversation_id="conversation-one", query="动作轴"
    )
    assert projected["next_cursor"] == "fts:v1:opaque-page-two"
    assert projected["has_more"] is True

    leaked = copy.deepcopy(source)
    leaked["items"][0]["kind"] = "reasoning"
    with pytest.raises(PlanError) as caught:
        normalize_search_payload(leaked, expected_session_id=SESSION_ID)
    assert caught.value.reason == "native_dsh_search_invalid"
