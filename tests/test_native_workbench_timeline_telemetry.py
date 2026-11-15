from __future__ import annotations

from genshin_app.agent_chat.native_workbench_timeline_telemetry import (
    context_metrics_projection,
)


def test_context_metrics_keeps_unknown_provider_usage_visible():
    projection = context_metrics_projection(
        {
            "conversation_id": "conversation-1",
            "usage_records": [{
                "turn_id": "turn-1",
                "usage_state": "unknown",
                "input_tokens": None,
                "output_tokens": None,
                "reasoning_tokens": None,
                "cached_input_tokens": None,
                "total_tokens": None,
            }],
        },
        source_cursor=3,
        projection_revision=4,
    )

    assert "provider_usage_incomplete" in projection["partial_reasons"]
    metric = next(
        row
        for row in projection["metrics"]
        if row["metric_id"] == "provider_usage_incomplete"
    )
    assert metric["value"] is None
    assert metric["precision"] == "unknown"
    assert "不能解释为完整或零用量" in metric["note"]
