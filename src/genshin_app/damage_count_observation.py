"""Counts at named stages; never equate declared components with damage rows."""
from __future__ import annotations

from collections import Counter

from genshin_engine.damage_report_window import tick


def _window_observation(manual, end):
    outside, unknown = [], []
    if type(end) not in (int, float):
        return "unknown", outside, unknown
    try:
        end_tick = tick(end)
        if end_tick <= 0:
            raise ValueError("统计窗必须为正")
    except (ValueError, ArithmeticError):
        return "invalid_time", outside, unknown
    for index, hit in enumerate(manual):
        row = {"id": hit.get("id"), "t": hit.get("t"), "input_ref": f"/manual_template/hits/{index}"}
        try:
            if type(hit.get("t")) not in (int, float):
                raise ValueError("缺少数值时间")
            hit_tick = tick(hit["t"])
        except (ValueError, ArithmeticError):
            unknown.append({**row, "reason": "invalid_time", "interpretation": "时间未满足引擎微秒精度，未判定窗内或窗外。"})
            continue
        if not 0 <= hit_tick <= end_tick:
            outside.append({**row, "reason": "declared_time_outside_report_window"})
    return "partial" if unknown else "classified", outside, unknown


def observe_damage_counts(plan, result, report=None):
    report = report or {}
    manual = (plan.get("manual_template") or {}).get("hits", [])
    composed = report.get("mechanics_input_template")
    if not isinstance(composed, dict):
        composed = (report.get("plan") or {}).get("resolved_template")
    saved = result.get("hits")
    window = result.get("damage_report_window") or {}
    end = window.get("end")
    if end is None and not window:
        end = (composed or {}).get("loop_duration")
    if end is None and not window:
        end = (plan.get("manual_template") or {}).get("loop_duration")
    window_state, outside, unclassified = _window_observation(manual, end)
    # These typed lifecycle receipts are compiled by the existing generic
    # materializer. Unknown older reports remain unknown, not zero.
    life = []
    for index, item in enumerate(report.get("periodic_attack_compilations", [])):
        receipt = item.get("lifecycle_projection") or {}
        for row in receipt.get("cancelled", []):
            life.append({**row, "compilation_index": index})
    runtime_channels = []
    for entry in (result.get("agent_execution_runtime_receipt") or {}).get("entries", []):
        for installed in entry.get("installed", []):
            if str(installed.get("target_identity", "")).startswith("periodic_lifecycle:"):
                runtime_channels.append({"entry_id": entry.get("entry_id"),
                    "scope": "channel_occurrences_not_damage_hits", **installed.get("summary", {})})
    execution = result.get("damage_execution_counts")
    return {"contract": "damage-stage-counts-v1", "authority": "observation_only",
        "declaration": {"manual_rows": len(manual),
            "manual_count_sum": sum(h.get("count", 1) for h in manual if type(h.get("count", 1)) in (int, float)),
            "mechanics_hit_events": len((plan.get("mechanics_model") or {}).get("hit_events", [])),
            "mechanics_patterns": len((plan.get("mechanics_model") or {}).get("hit_patterns", []))},
        "composition": {"template_rows": len(composed.get("hits", [])) if composed is not None else None},
        "lifecycle": {"recorded_cancellations": len(life), "reasons": dict(Counter(r.get("reason", "unknown") for r in life)),
                      "agent_channels": runtime_channels,
                      "scope": "saved_periodic_cancellation_receipts_only; not_all_input_exclusions"},
        "execution": execution or {"state": "not_recorded"},
        "report": {"saved_damage_rows": len(saved) if isinstance(saved, list) else None, "end": end,
                   "declared_manual_outside_window": len(outside) if window_state in ("classified", "partial") else None,
                   "outside_samples": outside[:8], "window_classification": window_state,
                   "unclassified_manual_count": len(unclassified) if window_state in ("classified", "partial") else len(manual),
                   "unclassified_samples": unclassified[:8]},
        "interpretation": "每层单独计数；count是数值次数，不能当独立事件。窗口外输入、生命周期取消和执行排除不可混为同一个数量。"}
