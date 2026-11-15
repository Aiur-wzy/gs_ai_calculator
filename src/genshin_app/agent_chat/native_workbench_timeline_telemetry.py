"""Telemetry projections derived from the Host durable display timeline."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

TRAJECTORY_CONTRACT = "agent-trajectory-v1"
CONTEXT_METRICS_CONTRACT = "agent-context-metrics-v1"
PERFORMANCE_METRICS_CONTRACT = "agent-performance-metrics-v1"
MAX_SPANS_PAGE = 500
MAX_METRICS = 128


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _elapsed_ms(start: object, end: object) -> int | None:
    left, right = _stamp(start), _stamp(end)
    if left is None or right is None or right < left:
        return None
    return round((right - left).total_seconds() * 1000)


def _state(value: object) -> str:
    if value in {"preparing", "starting", "connecting"}: return "preparing"
    if value in {"streaming", "using_tool", "resuming", "cancelling", "running", "claimed", "presented"}: return "running"
    if value in {"awaiting_input_live", "awaiting_input_suspended", "suspended", "open", "answer_submitting", "answered", "waiting"}: return "waiting"
    if value in {"completed", "settled", "consumed", "admitted", "applied", "committed", "accepted", "created", "renamed", "deleted", "recovered"}: return "completed"
    if value in {"cancelled", "expired", "aborted"}: return "cancelled"
    if value in {"failed", "orphaned", "outcome_unknown", "error"}: return "failed"
    if value == "interrupted": return "interrupted"
    return "unknown"


def _identity(ledger: Mapping[str, Any], projection: Mapping[str, Any], session: Mapping[str, Any]) -> tuple[str, int, int]:
    conversation_id = ledger.get("conversation_id")
    revision, cursor = ledger.get("revision"), ledger.get("source_cursor")
    if not isinstance(conversation_id, str) or session.get("conversation_id") != conversation_id or projection.get("conversation_id") != conversation_id:
        raise ValueError("workbench_telemetry_identity_invalid")
    if type(revision) is not int or type(cursor) is not int or projection.get("projection_revision") != revision or projection.get("source_cursor") != cursor:
        raise ValueError("workbench_telemetry_revision_invalid")
    return conversation_id, revision, cursor


def _reasons(projection: Mapping[str, Any], *, legacy: bool) -> list[str]:
    result = ["durable_timeline_not_available_for_legacy_turn"] if legacy else []
    completeness = projection.get("completeness")
    if isinstance(completeness, Mapping) and completeness.get("state") != "complete":
        fields = completeness.get("partial_fields")
        if isinstance(fields, list):
            result.extend(f"foundation_partial:{field}"[:128] for field in fields if isinstance(field, str))
        reason = completeness.get("reason")
        if isinstance(reason, str) and reason:
            result.append(f"foundation_partial:{reason}"[:128])
    return list(dict.fromkeys(result))[:32]


def trajectory_projection(ledger: Mapping[str, Any], projection: Mapping[str, Any], timeline: Sequence[Mapping[str, Any]] | None, *, after_seq: int | None = None, before_seq: int | None = None, limit: int = 300) -> dict[str, Any]:
    conversation_id, revision, cursor = _identity(ledger, projection, {"conversation_id": ledger.get("conversation_id")})
    if type(limit) is not int or not 1 <= limit <= MAX_SPANS_PAGE or after_seq is not None and before_seq is not None:
        raise ValueError("workbench_trajectory_page_invalid")
    for value in (after_seq, before_seq):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError("workbench_trajectory_page_invalid")
    rows = sorted(
        timeline or [],
        key=lambda row: (
            row.get("source_seq")
            if isinstance(row, Mapping) and type(row.get("source_seq")) is int
            else 2**63
        ),
    )
    eligible = [row for row in rows if type(row.get("source_seq")) is int and (after_seq is None or row["source_seq"] > after_seq) and (before_seq is None or row["source_seq"] < before_seq)]
    # A page also carries one lifecycle span for every represented Turn.  Keep
    # the complete envelope under the hard Renderer span bound even for the
    # adversarial case of one event per Turn.
    # One event can require both its Turn and execution-segment envelope.
    # Reserving three slots keeps the returned page within ``limit`` even for
    # the adversarial one-event-per-segment case.
    event_limit = max(1, limit // 3)
    eligible = (
        eligible[-event_limit:]
        if before_seq is not None or (before_seq is None and after_seq is None)
        else eligible[:event_limit]
    )
    operation_bounds: dict[
        tuple[str | None, str | None, str], tuple[str | None, str | None]
    ] = {}
    for row in rows:
        operation = row.get("operation_id")
        stamp = row.get("occurred_at")
        if not isinstance(operation, str):
            continue
        key = (
            row.get("turn_id") if isinstance(row.get("turn_id"), str) else None,
            row.get("segment_id")
            if isinstance(row.get("segment_id"), str)
            else None,
            operation,
        )
        first, last = operation_bounds.get(key, (None, None))
        operation_bounds[key] = (first or stamp, stamp or last)
    spans = []
    represented_turns = (
        list(dict.fromkeys(
            row.get("turn_id")
            for row in eligible
            if isinstance(row.get("turn_id"), str)
        ))
        if limit >= 2
        else []
    )
    projection_turns = {
        row.get("turn_id"): row
        for row in projection.get("turns", [])
        if isinstance(row, Mapping) and isinstance(row.get("turn_id"), str)
    }
    projection_segments = {
        row.get("segment_id"): row
        for row in projection.get("segments", [])
        if isinstance(row, Mapping) and isinstance(row.get("segment_id"), str)
    }
    event_times = {
        row.get("seq"): row.get("occurred_at")
        for row in ledger.get("events", [])
        if isinstance(row, Mapping) and type(row.get("seq")) is int
    }
    added_turn_ids: set[str] = set()
    for turn_id in represented_turns:
        turn = projection_turns.get(turn_id)
        if turn is None:
            continue
        start, end = turn.get("created_seq"), turn.get("updated_seq")
        if type(start) is not int or type(end) is not int or end < start:
            continue
        elapsed = _elapsed_ms(event_times.get(start), event_times.get(end))
        spans.append({"span_id": f"turn:{turn_id}", "parent_span_id": None, "turn_id": turn_id, "segment_id": None, "workflow_stage": None, "category": "turn", "state": _state(turn.get("state")), "label": f"任务轮次 {turn_id}", "summary": "Host 逻辑 Turn 生命周期", "result_code": None, "resource_ref": None, "seq_start": start, "seq_end": end, "elapsed_ms": elapsed, "timing_source": "host", "timing_precision": "exact" if elapsed is not None else "unknown"})
        added_turn_ids.add(turn_id)
    represented_segments = (
        list(dict.fromkeys(
            row.get("segment_id")
            for row in eligible
            if isinstance(row.get("segment_id"), str)
        ))
        if limit >= 3
        else []
    )
    added_segment_ids: set[str] = set()
    for segment_id in represented_segments:
        segment = projection_segments.get(segment_id)
        if segment is None or segment.get("turn_id") not in added_turn_ids:
            continue
        start, end = segment.get("created_seq"), segment.get("updated_seq")
        if type(start) is not int or type(end) is not int or end < start:
            continue
        turn = projection_turns[segment["turn_id"]]
        turn_start, turn_end = turn.get("created_seq"), turn.get("updated_seq")
        if (
            type(turn_start) is not int
            or type(turn_end) is not int
            or start < turn_start
            or end > turn_end
        ):
            continue
        elapsed = _elapsed_ms(event_times.get(start), event_times.get(end))
        turn_id = segment["turn_id"]
        spans.append({"span_id": f"segment:{segment_id}", "parent_span_id": f"turn:{turn_id}", "turn_id": turn_id, "segment_id": segment_id, "workflow_stage": None, "category": "segment", "state": _state(segment.get("state")), "label": f"执行段 {segment_id}", "summary": "一次可恢复的 Agent 执行段", "result_code": None, "resource_ref": None, "seq_start": start, "seq_end": end, "elapsed_ms": elapsed, "timing_source": "host", "timing_precision": "exact" if elapsed is not None else "unknown"})
        added_segment_ids.add(segment_id)
    for row in eligible:
        node_id, turn_id, seq = row.get("node_id"), row.get("turn_id"), row.get("source_seq")
        if not isinstance(node_id, str) or not isinstance(turn_id, str) or type(seq) is not int:
            raise ValueError("workbench_trajectory_node_invalid")
        operation = row.get("operation_id")
        operation_key = (
            row.get("turn_id") if isinstance(row.get("turn_id"), str) else None,
            row.get("segment_id")
            if isinstance(row.get("segment_id"), str)
            else None,
            operation,
        )
        start, end = operation_bounds.get(
            operation_key,
            (row.get("occurred_at"), row.get("occurred_at")),
        ) if isinstance(operation, str) else (row.get("occurred_at"), row.get("occurred_at"))
        elapsed = _elapsed_ms(start, end)
        category = row.get("category") if row.get("category") in {"runtime", "provider", "web", "app_tool", "engine", "database", "skill", "user_wait", "candidate", "app_action", "checkpoint"} else "other"
        source = "host"
        if category == "provider": source = "provider"
        elif category == "runtime": source = "runtime"
        elif category in {"engine", "app_tool", "app_action", "candidate", "checkpoint"}: source = "engine" if category == "engine" else "app"
        segment_id = row.get("segment_id") if isinstance(row.get("segment_id"), str) else None
        segment = projection_segments.get(segment_id)
        segment_contains_node = (
            segment_id in added_segment_ids
            and segment is not None
            and segment.get("turn_id") == turn_id
            and type(segment.get("created_seq")) is int
            and type(segment.get("updated_seq")) is int
            and segment["created_seq"] <= seq <= segment["updated_seq"]
        )
        turn = projection_turns.get(turn_id)
        turn_contains_node = (
            turn_id in added_turn_ids
            and turn is not None
            and type(turn.get("created_seq")) is int
            and type(turn.get("updated_seq")) is int
            and turn["created_seq"] <= seq <= turn["updated_seq"]
        )
        parent_span_id = (
            f"segment:{segment_id}"
            if segment_contains_node
            else f"turn:{turn_id}"
            if turn_contains_node
            else None
        )
        spans.append({"span_id": f"timeline:{node_id}", "parent_span_id": parent_span_id, "turn_id": turn_id, "segment_id": segment_id, "workflow_stage": row.get("workflow_stage") if isinstance(row.get("workflow_stage"), str) else None, "category": category, "state": _state(row.get("state")), "label": str(row.get("label") or row.get("kind") or "事件")[:1000], "summary": str(row.get("text") or "")[:1000], "result_code": row.get("result_code") if isinstance(row.get("result_code"), str) else None, "resource_ref": row.get("resource_ref") if isinstance(row.get("resource_ref"), str) else None, "seq_start": seq, "seq_end": seq, "elapsed_ms": elapsed, "timing_source": source, "timing_precision": "exact" if elapsed is not None else "unknown"})
    legacy = timeline is None
    reasons = _reasons(projection, legacy=legacy)
    if any(
        isinstance(row.get("turn_id"), str) and row["turn_id"] not in added_turn_ids
        for row in eligible
    ):
        reasons.append("trajectory_turn_envelope_unavailable")
    category_order = {"turn": 0, "segment": 1}
    spans.sort(key=lambda row: (row["seq_start"], category_order.get(row["category"], 2), row["span_id"]))
    first = eligible[0]["source_seq"] if eligible else None
    last = eligible[-1]["source_seq"] if eligible else None
    return {"contract": TRAJECTORY_CONTRACT, "session_id": conversation_id, "projection_revision": revision, "source_cursor": cursor, "completeness": "partial" if reasons else "complete", "partial_reasons": reasons, "spans": spans, "total_spans": len(rows) + len(projection.get("turns", [])) + len(projection.get("segments", [])), "has_older": bool(eligible and rows and rows[0]["source_seq"] < first), "has_newer": bool(eligible and rows and rows[-1]["source_seq"] > last), "older_cursor": first, "newer_cursor": last}


def context_metrics_projection(session_record: Mapping[str, Any], *, source_cursor: int, projection_revision: int) -> dict[str, Any]:
    conversation_id = session_record.get("conversation_id")
    if not isinstance(conversation_id, str):
        raise TypeError("workbench_telemetry_identity_invalid")
    totals: dict[str, int] = {}
    incomplete_usage_turns: list[str] = []
    for row in session_record.get("usage_records", []):
        if not isinstance(row, Mapping): continue
        if row.get("usage_state") in {"unknown", "partial"} and isinstance(row.get("turn_id"), str):
            incomplete_usage_turns.append(row["turn_id"])
        for name in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_input_tokens", "total_tokens"):
            value = row.get(name)
            if type(value) is int and value >= 0: totals[name] = totals.get(name, 0) + value
    labels = {"input_tokens": "累计输入 Token", "output_tokens": "累计输出 Token", "reasoning_tokens": "累计推理 Token", "cached_input_tokens": "累计缓存输入 Token", "total_tokens": "累计 Token"}
    metrics = [{"metric_id": name, "label": labels[name], "category": "provider_usage", "value": value, "unit": "token", "source": "provider", "precision": "exact", "note": "来自 provider 已结算 usage。"} for name, value in totals.items()]
    if incomplete_usage_turns:
        metrics.append({"metric_id": "provider_usage_incomplete", "label": "Provider 用量完整性", "category": "provider_usage", "value": None, "unit": "token", "source": "provider", "precision": "unknown", "note": f"{len(set(incomplete_usage_turns))} 轮未返回完整 usage；已知值保留，但不能解释为完整或零用量。"})
    metrics.append({"metric_id": "model_context_limit", "label": "模型上下文上限", "category": "context_limit", "value": None, "unit": "token", "source": "provider", "precision": "unknown", "note": "当前耐久事实没有可核验的模型上下文上限。"})
    partial_reasons = ["context_component_breakdown_unavailable"]
    if incomplete_usage_turns:
        partial_reasons.append("provider_usage_incomplete")
    return {"contract": CONTEXT_METRICS_CONTRACT, "session_id": conversation_id, "projection_revision": projection_revision, "source_cursor": source_cursor, "completeness": "partial", "partial_reasons": partial_reasons, "metrics": metrics[:MAX_METRICS]}


def performance_metrics_projection(ledger: Mapping[str, Any], projection: Mapping[str, Any], timeline: Sequence[Mapping[str, Any]] | None) -> dict[str, Any]:
    conversation_id, revision, cursor = _identity(ledger, projection, {"conversation_id": ledger.get("conversation_id")})
    rows = list(timeline or [])
    metrics = []
    for turn in list(projection.get("turns", []))[-64:]:
        if not isinstance(turn, Mapping) or not isinstance(turn.get("turn_id"), str): continue
        owned = [row for row in rows if row.get("turn_id") == turn["turn_id"]]
        first_user = next((row for row in owned if row.get("kind") == "message" and row.get("role") == "user"), None)
        first_assistant = next((row for row in owned if row.get("kind") == "message" and row.get("role") == "assistant"), None)
        value = _elapsed_ms(first_user.get("occurred_at") if first_user else None, first_assistant.get("occurred_at") if first_assistant else None)
        metrics.append({"metric_id": f"provider_ttft_ms:{turn['turn_id']}", "label": f"Turn {turn['turn_id']} 首条回复延迟", "category": "provider", "value": value, "unit": "ms", "source": "provider", "precision": "exact" if value is not None else "unknown", "note": "从耐久用户消息到首条耐久助手消息。"})
    operation_groups: dict[
        tuple[str, str | None, str | None, str], dict[str, Any]
    ] = {}
    for index, row in enumerate(rows):
        raw_category = row.get("category")
        category = (
            raw_category
            if raw_category
            in {
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
            else "other"
        )
        operation = row.get("operation_id")
        identity = operation if isinstance(operation, str) else f"node-{index}"
        key = (
            category,
            row.get("turn_id") if isinstance(row.get("turn_id"), str) else None,
            row.get("segment_id")
            if isinstance(row.get("segment_id"), str)
            else None,
            identity,
        )
        group = operation_groups.setdefault(
            key,
            {"category": category, "first": row.get("occurred_at"), "last": row.get("occurred_at")},
        )
        group["last"] = row.get("occurred_at")
    category_groups: dict[str, list[int | None]] = {}
    for group in operation_groups.values():
        category_groups.setdefault(group["category"], []).append(
            _elapsed_ms(group["first"], group["last"])
        )
    for category, values in sorted(category_groups.items()):
        known = [value for value in values if value is not None]
        unknown_count = len(values) - len(known)
        metrics.append({"metric_id": f"operation_count:{category}", "label": f"{category} 操作数", "category": category, "value": len(values), "unit": "count", "source": "host", "precision": "exact", "note": "按 Turn、执行段和稳定操作身份去重。"})
        metrics.append({"metric_id": f"operation_elapsed_ms:{category}", "label": f"{category} 已知操作耗时合计", "category": category, "value": sum(known) if known else None, "unit": "ms", "source": "host", "precision": "bounded" if known and unknown_count else "exact" if known else "unknown", "note": "各操作可能并行或重叠，不能据此推导总墙钟；缺失计时不会被估造。"})
        if unknown_count:
            metrics.append({"metric_id": f"operation_timing_unknown_count:{category}", "label": f"{category} 耗时未知操作数", "category": category, "value": unknown_count, "unit": "count", "source": "host", "precision": "exact", "note": "Host 没有可核验的起止时间。"})
    reasons = []
    provider_metrics = [
        row for row in metrics if row["metric_id"].startswith("provider_ttft_ms:")
    ]
    if not provider_metrics or any(row["value"] is None for row in provider_metrics):
        reasons.append("provider_phase_timings_unavailable")
    if any(
        row["metric_id"].startswith("operation_timing_unknown_count:")
        for row in metrics
    ):
        reasons.append("operation_timing_unavailable")
    return {"contract": PERFORMANCE_METRICS_CONTRACT, "session_id": conversation_id, "projection_revision": revision, "source_cursor": cursor, "completeness": "partial" if reasons else "complete", "partial_reasons": reasons, "metrics": metrics[:MAX_METRICS]}


def telemetry_bundle(ledger: Mapping[str, Any], projection: Mapping[str, Any], session_record: Mapping[str, Any], timeline: Sequence[Mapping[str, Any]] | None = None, *, after_seq: int | None = None, before_seq: int | None = None, limit: int = 300) -> dict[str, Any]:
    _conversation_id, _revision, _cursor = _identity(ledger, projection, session_record)
    trajectory = trajectory_projection(ledger, projection, timeline, after_seq=after_seq, before_seq=before_seq, limit=limit)
    return copy.deepcopy({"trajectory": trajectory, "context": context_metrics_projection(session_record, source_cursor=trajectory["source_cursor"], projection_revision=trajectory["projection_revision"]), "performance": performance_metrics_projection(ledger, projection, timeline)})


__all__ = ["CONTEXT_METRICS_CONTRACT", "PERFORMANCE_METRICS_CONTRACT", "TRAJECTORY_CONTRACT", "context_metrics_projection", "performance_metrics_projection", "telemetry_bundle", "trajectory_projection"]
