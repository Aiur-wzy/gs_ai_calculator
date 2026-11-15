"""Shared single/multi-contact binding to an existing action capture declaration."""
from __future__ import annotations

import copy
import math
from decimal import Decimal

from .files import fingerprint


def ticks(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("面板取样时点必须是有限数字，不能使用布尔或空值")
    exact = Decimal(str(value)) * 1000000
    if exact != exact.to_integral_value():
        raise ValueError("面板取样只接受精确整数微秒")
    return int(exact)


def prepare_action_captures(actions, rotation, model):
    """Return unique per-action inputs and portable calibration semantics.

    IDs and axis positions stay in generated instance receipts, while changing
    an actual calibration invalidates profile acceptance. Repeating an identical
    calibration on another explicit action does not invent a new game fact.
    """
    captures, semantics = {}, {}
    for action in actions:
        found = [c for c in model.get("snapshot_captures", []) if c["action_id"] == action["id"]]
        if len(found) != 1:
            raise ValueError("采用面板取样的每个实际动作必须关联唯一已声明捕获")
        capture = found[0]
        if (capture["status"] not in ("assumed", "confirmed") or not capture["source"].strip()
                or ticks(capture["offset"]) < 0 or ticks(capture["precision"]) <= 0):
            raise ValueError("面板取样的偏移、记录精度或校准依据尚未就绪")
        absolute = ticks(action["start"]) + ticks(capture["offset"])
        if absolute > ticks(action["occupation_end"]) or absolute >= ticks(rotation["period"]):
            raise ValueError("面板取样必须在实际动作占用内，且严格早于输入窗口右端")
        captures[action["id"]] = capture
        semantic = {k: copy.deepcopy(capture[k]) for k in ("offset", "precision", "status", "source")}
        semantics[fingerprint(semantic)] = semantic
    return captures, [semantics[k] for k in sorted(semantics)]


def resolve_action_capture(action, hit_tick, hit_order, captures):
    """No fallback and no queue rescheduling; same-tick original hit must follow."""
    capture = captures[action["id"]]
    tick = ticks(action["start"]) + ticks(capture["offset"])
    if hit_tick < tick or hit_tick == tick and hit_order <= action["order"]:
        raise ValueError("实际命中必须排在面板取样之后；同刻需明确更晚的order")
    return capture
