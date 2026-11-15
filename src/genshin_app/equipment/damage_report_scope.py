"""Shared read-only scope checks for post-formula event ledgers.

The execution period remains authoritative for validation. This module only
selects already calculated members for an explicit versioned reporting scope.
"""
from __future__ import annotations

import math

from genshin_engine.damage_report_window import CONTRACT, tick


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("统计及审计伤害时间必须为有限数值。")
    try:
        return tick(value)
    except ArithmeticError as exc:
        raise ValueError("统计及审计伤害时间精度无效。") from exc


def reporting_window(result, *, require_complete=False):
    if "damage_report_window" not in result:
        return None
    window = result["damage_report_window"]
    required = window.get("required_validation") if isinstance(window, dict) else None
    if (not isinstance(window, dict) or window.get("contract") != CONTRACT
            or window.get("boundary") != "inclusive_time"
            or window.get("start") != 0
            or window.get("status") not in {"complete", "pending"}
            or not isinstance(required, dict)
            or required.get("status") not in {"complete", "failed", "pending"}):
        raise ValueError("统计窗口或完整必需动作验证记录无效。")
    if require_complete and (window["status"] != "complete" or required["status"] != "complete"):
        raise ValueError("统计窗口或完整必需动作验证尚未完成，不能接受补充或数值接管结果。")
    period = _time(window.get("execution_period"))
    end = _time(window["end"]) if window.get("end") is not None else None
    if (period <= 0 or end is not None and not 0 <= end <= period
            or window["status"] == "complete" and (end is None or end == 0)):
        raise ValueError("统计截止必须位于完整执行窗口内。")
    return window


def report_includes(window, time):
    return 0 <= _time(time) <= _time(window["end"])


def scoped_hits(result, *, require_complete=True):
    """Return unique real members and their report ownership, never new events."""
    window = reporting_window(result, require_complete=require_complete)
    has_cutoff = window is not None and window.get("end") is not None and _time(window["end"]) > 0
    collections = [(result.get("hits"), True)]
    if window is not None:
        collections.append((result.get("damage_report_audit_hits"), False))
    members = {}
    for rows, included in collections:
        if not isinstance(rows, list):
            raise ValueError("统计或审计伤害账缺失。")  # noqa: TRY004 -- invalid serialized evidence
        for hit in rows:
            identity = hit.get("id") if isinstance(hit, dict) else None
            if not isinstance(identity, str) or identity in members:
                raise ValueError("统计与审计伤害事件身份缺失或重复。")
            if window is not None and (
                    has_cutoff and report_includes(window, hit.get("t")) != included
                    or not has_cutoff and not included
                    or not 0 <= _time(hit.get("t")) <= _time(window["execution_period"])):
                raise ValueError("伤害事件与统计及审计窗口归属不一致。")
            members[identity] = (hit, included)
    return window, members
