"""Pure finite energy-credit plans; publication belongs to Rotation's clock."""
from __future__ import annotations

import math

from genshin_engine.snapshot_capture import SnapshotCapturePending


def _finite(value, field):
    valid = not isinstance(value, bool) and isinstance(value, (int, float))
    try:
        valid = valid and math.isfinite(value)
    except OverflowError:
        valid = False
    if not valid:
        raise SnapshotCapturePending("energy_credit_value_invalid",
            "能量到账计划包含非有限值，未提交粒子消费或任何角色回能。", field)
    return value


def plan_energy_gain(character, before, capacity, raw, *, frontline, energy_recharge=None):
    """Return one fully validated row without mutating balances or resources."""
    before, capacity, raw = (_finite(value, key) for value, key in (
        (before, "before"), (capacity, "energy_capacity"), (raw, "raw_gain")))
    if before < 0 or capacity < before or raw < 0:
        raise SnapshotCapturePending("energy_credit_value_invalid",
            "能量到账计划的余额、容量或回能值无效，未提交到账。", "energy_credit")
    if energy_recharge is not None:
        _finite(energy_recharge, "energy_recharge")
        if energy_recharge <= 0:
            raise SnapshotCapturePending("energy_credit_value_invalid",
                "实际充能效率必须有限且大于0，未提交到账。", "energy_recharge")
    actual = min(raw, max(0, capacity - before))
    after, overflow = before + actual, raw - actual
    _finite(actual, "gain")
    _finite(after, "after")
    _finite(overflow, "overflow")
    return {"character": character, "before": before, "raw": raw,
            "gain": actual, "overflow": overflow, "after": after,
            "energy_recharge": energy_recharge, "on_field": character == frontline}
