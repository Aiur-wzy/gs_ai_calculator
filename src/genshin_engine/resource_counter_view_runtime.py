"""Current, committed counter reads for already qualified classic effects.

No caller snapshot is accepted. Reading neither activates a rule nor consumes a
pool; the mechanics runner must qualify its current instance before calling.
Source approval remains the App's obligation, independent of ledger validity.
"""
from __future__ import annotations

import copy
import math

from genshin_engine.attribute_graph import linear_value
from genshin_engine.registry import BENEFIT_TYPES
from genshin_engine.resource_counter_frame_runtime import (
    CounterFrameRuntime,
    gate_qualification,
)
from genshin_engine.resource_counter_views import CounterViewProgram
from genshin_engine.resource_counters import CounterProgram
from genshin_engine.shared_pool_runtime import ALLOWED_ZONES, issue
from genshin_engine.shared_pools import SharedPoolError, _canonical
from genshin_engine.snapshot_capture import SnapshotCapturePending

READ_LIMITS = {"reads": 40000, "trace_bytes": 8*1024*1024}


def is_counter_view(value):
    return isinstance(value, dict) and value.get("kind") == "counter_view"


def validate_bindings(model, *, characters=None):
    config = model.get("resource_counter_views")
    effects = [(r, e) for r in model["rules"] for e in r["effects"] if is_counter_view(e["value"])]
    if config is None:
        return [issue("counter_view_reference", "资源读数表达式没有对应声明", r["id"]) for r, _ in effects]
    if any(k not in model for k in ("shared_pools", "state_runtime", "resource_counters")):
        return [issue("counter_view_reference", "资源读数必须依赖实际池、生命周期和累计账", "resource_counter_views")]
    problems = []
    views = {v["id"]: v for v in config["views"]}
    for rule, effect in effects:
        expr, path = effect["value"], f"rules.{rule['id']}.effects.{effect['id']}"
        view = views.get(expr["view_id"])
        if view is None or view["owner"] != rule["owner"]:
            problems.append(issue("counter_view_reference", "效应与读数必须关联同一提供者", path))
        channel_ok = (effect["domain"] == "provider" and expr["sample"] == "current") or (
            config["schema_version"] == 2 and effect["domain"] == "self" and expr["sample"] == "self_frame")
        if (not channel_ok
                or BENEFIT_TYPES.get(effect["benefit_type_id"], {}).get("zone") not in ALLOWED_ZONES
                or effect.get("filter", {}).get("event", "hit") != "hit"):
            problems.append(issue("counter_view_scope", "当前资源读数仅用于提供者域的经典直伤收益", path))
    if characters is not None:
        try:
            counters = CounterProgram(model["resource_counters"], model["shared_pools"], model["state_runtime"], character_ids=characters)
            CounterViewProgram(config, counters)
        except SharedPoolError as exc:
            problems.append(copy.deepcopy(exc.issue))
    return problems


class CounterViewRuntime:
    """Read only the live ledger, preparing each bounded receipt before reserve."""

    def __init__(self, config, pools, *, context=None):
        self.program = CounterViewProgram(config, pools.counter_program)
        self._ledger = pools.ledger
        self._trace = []
        self._bytes = 2
        try:
            self.frames = (CounterFrameRuntime(context, config, pools, self)
                           if context is not None and config["schema_version"] == 2 and any(
                               is_counter_view(e["value"]) and e["value"]["sample"] == "self_frame"
                               for r in context.model["rules"] for e in r["effects"]) else None)
        except SnapshotCapturePending as exc:
            raise SharedPoolError('counter_view_limit', exc.issue['message'], exc.issue['path'],
                                  severity='pending', cause=exc.issue) from exc

    def read(self, ctx, expr, rule, effect, inst, recipient, event, snapshot=None):
        if event.get("event") != "hit" or event.get("damage_system") != "classic":
            raise SharedPoolError("counter_view_damage_scope", "资源读数不能用于非经典伤害", "resource_counter_views", severity="error")
        if expr["sample"] == "self_frame" and snapshot is None:
            self.frames.validate_binding()
        if expr["sample"] == "self_frame" and snapshot is not None:
            return self.frames.read(snapshot, expr, rule, effect, inst, recipient, event)
        reading = self.program.read(expr["view_id"], self._ledger.counter_snapshot(), recipient=recipient)
        try:
            raw = reading["numerator"] / reading["denominator"]
            value = linear_value(raw, expr)
            layers = len(inst["expires"])
            multiply = BENEFIT_TYPES[effect["benefit_type_id"]]["value_semantics"] == "multiply"
            combined = value**layers if multiply else value*layers
            if not math.isfinite(combined):
                raise ArithmeticError("nonfinite layer composition")
        except ArithmeticError as exc:
            raise SharedPoolError("counter_view_value", "资源读数收益乘加溢出，封顶不能掩盖溢出", "resource_counter_views", severity="pending") from exc
        reading.update(sampling_authority="mechanics_committed_ledger", qualification="current_rule_instance_accepted")
        receipt = {"type": "counter_view_read", "receipt_id": f"counter-view:{len(self._trace)+1}",
            "event_id": event["id"], "run_id": event["run_id"], "cycle": event["origin"],
            "t": ctx.time/1_000_000, "time_us": ctx.time,
            "rule_id": rule["id"], "effect_id": effect["id"], "instance_id": inst["instance_id"],
            "effect_source_id": rule["source_id"], "domain": "provider", "sample": "current",
            "character": recipient, "expression": copy.deepcopy(expr), "reading": reading,
            "source_value": raw, "value": value, "single_value": value,
            "layer_count": layers, "combined_value": combined}
        if expr["sample"] == "self_frame":
            receipt.update(domain="self", sample="self_frame", frame_selection="current_no_self_reference",
                           gate_qualification=gate_qualification(rule, inst, event))
        size = self._bytes + len(_canonical(receipt)) + bool(self._trace)
        if len(self._trace) >= READ_LIMITS["reads"] or size > READ_LIMITS["trace_bytes"]:
            raise SharedPoolError("counter_view_limit", "实际资源读数流水超过次数或字节限制", "resource_counter_views", severity="pending")
        # Both outputs are prepared before the shared budget commits. No copying,
        # serialization or caller callbacks are performed after this reserve.
        sample = copy.deepcopy(receipt)
        ctx._reserve_events(1, "resource_counter_view")
        self._bytes = size
        self._trace.append(receipt)
        ctx.samples.append(sample)
        return value

    def report(self):
        report = {"contract": "resource-counter-views-current-v1", "execution": "mechanics_event_queue",
            "scope": "current_provider_classic_damage", "trace": copy.deepcopy(self._trace),
            "limits": dict(READ_LIMITS), "reads": len(self._trace), "trace_bytes": self._bytes,
            "source_approval": "not_evaluated_by_engine", "historical_capture": "unsupported"}
        if self.frames:
            report.update(contract="resource-counter-views-self-frame-v2", scope="current_provider_or_self_frame_classic_damage",
                          historical_capture=self.frames.report())
        return report
