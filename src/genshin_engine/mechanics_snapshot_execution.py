"""Snapshot capture and references over the existing Mechanics state graph."""

from __future__ import annotations

import copy

from genshin_engine import snapshot_capture as capture_contract
from genshin_engine.mechanics_execution_port import MechanicsExecutionPort
from genshin_engine.rotation_port import TICKS
from genshin_engine.snapshot_capture import SnapshotCapturePending


def snapshot_runtime(runtime: MechanicsExecutionPort):
    states = {}
    for rid, state in runtime.state.items():
        instances = []
        for instance in state["instances"]:
            # A captured panel is created once by freeze_runtime_panel and
            # only read afterwards. A refresh replaces the panel reference.
            # Copy the mutable instance and its containers, not the panels.
            saved = {**instance,
                     "beneficiaries": list(instance["beneficiaries"]),
                     "expires": list(instance["expires"]),
                     "quota": dict(instance["quota"]),
                     "layer_snapshots": list(instance["layer_snapshots"])}
            if "attribute_layers" in instance:
                saved["attribute_layers"] = list(instance["attribute_layers"])
            instances.append(saved)
        states[rid] = {"cooldown": state["cooldown"], "instances": instances}
    snapshot = {"time": runtime.time, "frontline": runtime.frontline, "state": states}
    if getattr(runtime, 'health_runtime', None):
        snapshot['health'] = runtime.health_runtime.snapshot()
    if runtime.attribute_runtime:
        snapshot["attribute_frame"] = runtime.attribute_runtime.capture(snapshot)
    return snapshot


def capture_live_attribute_frame(runtime: MechanicsExecutionPort):
    """Read current facts synchronously without freezing the entire rule graph."""
    live = {"time": runtime.time, "frontline": runtime.frontline, "state": runtime.state}
    if getattr(runtime, 'health_runtime', None):
        live['health'] = runtime.health_runtime.snapshot()
    return {"time": runtime.time, "attribute_frame": runtime.attribute_runtime.capture(live)}


def reserve_snapshot_capacity(runtime: MechanicsExecutionPort, value):
    size = len(capture_contract.canonical(value))
    attempted = runtime.capture_bytes + size + bool(runtime.capture_entries)
    if attempted > capture_contract.MAX_SNAPSHOT_CAPTURE_BYTES:
        raise SnapshotCapturePending("snapshot_capture_limit", "捕获与引用的累计存储字节超过限制。",
                                     "mechanics_model.snapshot_captures", limit=capture_contract.MAX_SNAPSHOT_CAPTURE_BYTES,
                                     bytes=runtime.capture_bytes, attempted=attempted)
    runtime.capture_bytes = attempted
    runtime.capture_entries += 1


def capture_runtime_snapshot(runtime: MechanicsExecutionPort, ev, row):
    capture = runtime.captures[ev["id"]]
    key = (ev["origin"], ev["id"])
    parent = runtime.pending_captures[key]
    receipt = {"type": "snapshot_capture", "capture_id": ev["id"], "event_id": ev["id"],
               "run_id": ev["run_id"], "origin": ev["origin"], "cycle": ev["origin"],
               "action_id": capture["action_id"], "parent_run_id": parent["parent_run_id"],
               "actor": parent["actor"], "t": runtime.time/TICKS, "time_ticks": runtime.time,
               "order": ev["order"], "causal": list(ev["causal"]),
               "declaration_fingerprint": capture_contract.fingerprint(capture),
               "status": "captured", "snapshot_fingerprint": None}
    if parent.get("interrupted_by"):
        receipt.update(status="cancelled", reason="successful_successor_before_capture",
                       interrupted_by=parent["interrupted_by"])
        runtime.reserve_capture(receipt)
        runtime.capture_trace.append(receipt)
        runtime.trace.append(copy.deepcopy(receipt))
        row.update(status="pending", snapshot_capture=copy.deepcopy(receipt))
        raise SnapshotCapturePending("snapshot_cancelled", "成功后继动作先于捕获；本有限模型不推测取消后的取样。",
                                     capture["id"], action_id=capture["action_id"], interrupted_by=parent["interrupted_by"])
    if (runtime.counter_views and runtime.counter_views.frames
            and ev['id'] in runtime.counter_views.frames.references):
        runtime.counter_views.frames.capture(ev, row, receipt)
        return
    # Measure the same state as snapshot(), before its deep copy is allocated.
    preview = {"time": runtime.time, "frontline": runtime.frontline,
               "state": {rid: {"cooldown": s["cooldown"], "instances": s["instances"]} for rid, s in runtime.state.items()}}
    if getattr(runtime, 'health_runtime', None):
        preview['health'] = runtime.health_runtime.snapshot()
    receipt["snapshot_fingerprint"] = capture_contract.fingerprint(preview)
    runtime.reserve_capture({"snapshot": preview, "receipt": receipt})
    runtime.snapshots[key] = runtime.observed_snapshot(ev)
    runtime.capture_receipts[key] = receipt
    runtime.pending_captures.pop(key)
    runtime.capture_trace.append(receipt)
    runtime.trace.append(copy.deepcopy(receipt))
    row["snapshot_capture"] = copy.deepcopy(receipt)


def observe_runtime_snapshot(runtime: MechanicsExecutionPort, event):
    if runtime.attribute_runtime and runtime.attribute_runtime.graph:
        runtime.attribute_runtime.stage(event, "capture", observation=True)
    snapshot = runtime.snapshot()
    if runtime.counter_views and runtime.counter_views.frames:
        runtime.counter_views.frames.observe(event, snapshot)
    return snapshot


def reference_runtime_capture(runtime: MechanicsExecutionPort, event, ref, usage):
    key = (event["origin"], ref)
    receipt = runtime.capture_receipts.get(key)
    if receipt is None:
        raise SnapshotCapturePending("snapshot_event_unexecuted", "引用的动作从属捕获尚未执行，不能借用旧轮快照。",
                                     ref, consumer_id=event["id"], origin=event["origin"])
    entry = {"type": "snapshot_capture_reference", "capture_id": ref, "capture_run_id": receipt["run_id"],
             "event_id": event["id"], "origin": event["origin"], "cycle": event["origin"],
             "t": runtime.time/TICKS, "usage": usage, "snapshot_fingerprint": receipt["snapshot_fingerprint"]}
    runtime.reserve_capture(entry)
    runtime.capture_trace.append(entry)
    runtime.trace.append(copy.deepcopy(entry))

