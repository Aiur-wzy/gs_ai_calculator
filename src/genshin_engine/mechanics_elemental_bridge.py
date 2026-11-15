"""Elemental/special generated-event integration at the mechanics observer port.

The bridge reads the current runtime through ports and owns only projection
deduplication/cursors. It does not keep another elemental state, queue or clock.
The rotation observer supplies each already-selected advance boundary.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MechanicsElementalPorts:
    get_elemental: Callable[[], Any]
    get_special_runtime: Callable[[], Any]
    get_time: Callable[[], int]
    append_trace: Callable[[dict], None]
    count_event: Callable[..., None]
    dispatch_generated: Callable[[dict, int, dict], bool]


class MechanicsElementalBridge:
    def __init__(self, ports: MechanicsElementalPorts):
        self.ports = ports
        self.trace: list[dict] = []
        self.seen: set[tuple] = set()
        self.cursor = 0

    def record_special_event(self, row, cycle):
        if not isinstance(row, dict) or not str(row.get("type", "")).startswith("special_reaction_"):
            return False
        key = (row.get("type"), row.get("event_id"), row.get("time_tick"), row.get("t"),
               row.get("operation"), row.get("reaction_type"),
               row.get("special_reaction_type"), row.get("component"))
        if key in self.seen:
            return False
        self.seen.add(key)
        clone = copy.deepcopy(row)
        clone["cycle"] = cycle
        self.trace.append(clone)
        self.ports.append_trace(copy.deepcopy(clone))
        return True

    def sync_trace(self, cycle):
        runtime = self.ports.get_special_runtime()
        if runtime is None:
            return
        rows = runtime.trace[self.cursor:]
        self.cursor = len(runtime.trace)
        for row in rows:
            if self.record_special_event(row, cycle):
                self.ports.count_event("special_reaction_event")

    def drain_generated(self, cycle=0, parent=None, events=None):
        runtime = self.ports.get_special_runtime()
        if runtime is None:
            return
        parent = parent or {"id": "@special_runtime", "run_id": f"{cycle}:@special_runtime",
                            "time": self.ports.get_time(), "order": 0, "origin": cycle, "causal": ()}
        if events is None:
            drain = getattr(self.ports.get_elemental(), "drain_generated_events", None)
            events = drain() if callable(drain) else []
        if isinstance(events, dict):
            events = events.get("generated_events", [])
        for row in events or []:
            new = self.record_special_event(row, cycle)
            handled = self.ports.dispatch_generated(row, cycle, parent)
            if not handled and new:
                self.ports.count_event("special_reaction_event")
        self.sync_trace(cycle)

    def advance_to(self, time, cycle=0, parent=None):
        """Apply only the boundary chosen by the root rotation observer."""

        before = len(self.ports.get_elemental().trace)
        self.ports.get_elemental().advance(time)
        for transition in self.ports.get_elemental().trace[before:]:
            transition["cycle"] = cycle
            self.ports.append_trace(copy.deepcopy(transition))
            self.ports.count_event("state_transition")
        if self.ports.get_special_runtime() is not None:
            if callable(getattr(self.ports.get_elemental(), "drain_generated_events", None)):
                self.drain_generated(cycle, parent)
            else:
                # Preserve the existing older-runtime adapter during this
                # behavior-preserving slice; its removal is a separate change.
                generated = self.ports.get_special_runtime().advance(time)
                self.drain_generated(cycle, parent, generated)


__all__ = ["MechanicsElementalBridge", "MechanicsElementalPorts"]
