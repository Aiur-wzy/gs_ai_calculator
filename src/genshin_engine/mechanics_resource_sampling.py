"""Energy observations over the existing Mechanics state and source clock."""
from __future__ import annotations

from genshin_engine.rotation_port import TICKS
from genshin_engine.snapshot_capture import SnapshotCapturePending


def total_energy_recharge(runtime, cid, time, frontline, event=None):
    """Read one pre-credit immutable TOTAL; callers multiply the gain once."""
    runtime.frontline = frontline
    runtime.expire(time)
    sample_event = event or {"id": "energy_credit", "origin": time // runtime.data["period"]}
    key = (sample_event['id'], sample_event['origin'], time, frontline)
    cached = getattr(runtime, '_energy_credit_attribute_snapshot', None) if getattr(runtime, '_energy_credit_attempt', None) else None
    if cached is None or cached[0] != key:
        runtime.attribute_runtime.stage(sample_event, "pre")
        snapshot = runtime.snapshot()
        if getattr(runtime, '_energy_credit_attempt', None):
            runtime._energy_credit_attribute_snapshot = (key, snapshot)
    else:
        snapshot = cached[1]
    receipt = runtime.attribute_runtime.read(cid, "total", snapshot, stat="energy_recharge")
    runtime.samples.append({"event_id": sample_event["id"], "cycle": sample_event["origin"], "t": time / TICKS,
        "character": cid, "stat": "energy_recharge", "domain": "provider", "sample_t": time / TICKS,
        "source_value": receipt["value"], "value": receipt["value"], "attribute_receipt_id": receipt["receipt_id"],
        "consumer": "energy_credit", "unit": "ratio"})
    return receipt["value"]


def begin_energy_credit(runtime, event):
    """Start a bounded observation attempt, never a second energy ledger."""
    if getattr(runtime, "_energy_credit_failed", False):
        raise SnapshotCapturePending("energy_credit_attempt_failed",
            "上一次到账取样未提交；请从原输入重新计算，不能复用失败执行器。", "energy_credit")
    runtime.health_event_context = event
    runtime._energy_credit_attempt = {
        "type": "energy_credit_sampling", "event_id": event["id"],
        "run_id": event["run_id"], "source_cycle": event["origin"],
        "t": event["time"]/TICKS, "sample_start": len(runtime.samples),
        "capture_start": len(runtime.capture_trace),
    }
    runtime._energy_credit_attribute_snapshot = None


def finish_energy_credit(runtime, event, *, committed):
    attempt = runtime._energy_credit_attempt
    sample_count = len(runtime.samples)-attempt["sample_start"]
    capture_count = len(runtime.capture_trace)-attempt["capture_start"]
    # Constant-only credits need no extra sampling record; Rotation already
    # reports their amounts. Failed observations never become committed proof.
    if sample_count or capture_count or not committed:
        runtime.trace.append({**attempt, "status": "committed" if committed else "not_committed",
                              "sample_count": sample_count, "capture_count": capture_count})
    if not committed:
        runtime._energy_credit_failed = True
    runtime._energy_credit_attempt = None
    runtime._energy_credit_attribute_snapshot = None


def energy_recharge(runtime, cid, time, frontline, event=None):
    if runtime.attribute_runtime and runtime.attribute_runtime.five_stats:
        total = total_energy_recharge(runtime, cid, time, frontline, event)
        # The established Rotation port already adds its initial ER and windows.
        # Return only their complement, not a second full ER multiplier.
        baseline = runtime.data["chars"][cid]["energy_recharge"]
        windows = sum(w["delta"] for w in runtime.windows
                      if w["character"] == cid and w["start"] <= time < w["end"])
        return total - (baseline + windows)
    if runtime.attribute_runtime:
        runtime.attribute_runtime.stage(event or {"id": "energy_credit", "origin": time//runtime.data["period"]}, "pre")
    runtime.expire(time)
    runtime.frontline = frontline
    sample_event = ({"id": event["id"], "origin": event["origin"]} if event is not None
                    else {"id": "energy_credit", "origin": time//runtime.data["period"]})
    value = 0
    for rule in runtime.rules.values():
        if rule.get("resource_gate"):
            continue
        for inst in runtime.state[rule["id"]]["instances"]:
            if cid not in inst["beneficiaries"]:
                continue
            for effect in rule["effects"]:
                if effect["benefit_type_id"] != "benefit.energy_recharge.all":
                    continue
                if rule["beneficiaries"]["scope"] == "current_frontline" and frontline != cid:
                    continue
                if isinstance(effect["value"], dict) and effect["value"]["sample"] == "trigger":
                    value += sum(runtime.value(effect, rule, {**inst, "trigger_snapshot": sample}, cid,
                                               sample_event) for sample in inst["layer_snapshots"])
                else:
                    value += runtime.value(effect, rule, inst, cid, sample_event)*len(inst["expires"])
    return value


def pending_energy_sample_refs(runtime):
    """Conservative source references used by delayed-credit boundary proof."""
    return [effect["value"]["event_id"] for rule in runtime.rules.values() for effect in rule["effects"]
            if effect["benefit_type_id"] == "benefit.energy_recharge.all"
            and isinstance(effect["value"], dict) and effect["value"]["sample"] == "event"]
