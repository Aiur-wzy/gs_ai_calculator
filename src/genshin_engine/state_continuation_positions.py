"""Private monotone clock index; prepared deltas carry no dispatch authority."""
from __future__ import annotations

import copy

from genshin_engine.execution_control import check_execution_cancelled
from genshin_engine.resource_budget import normalize_resource_budget
from genshin_engine.state_position_reference import (
    PositionError,
    _bytes,
    _frame,
    _identity,
    _position,
    _slot,
    _span_successor,
)


class PositionLimitError(PositionError):
    """An internal bounded-work limit; malformed positions remain PositionError."""


class PositionIndex:
    def __init__(self, *, limits=None):
        self.policy = normalize_resource_budget(limits)
        self.limits = self.policy['effective']
        self.cursor, self.continuation = None, None
        self.contexts, self.handled, self.consumed, self.dispatches = {}, set(), set(), set()
        self.steps, self.bytes = 0, 2

    def prepare(self, frame, *, issued):
        check_execution_cancelled()
        continuation = _frame(frame)
        anchor, uid = frame["anchor"], frame["anchor"]["queue_run_id"]
        known = self.contexts.get(uid)
        if known is not None and known != anchor: raise PositionError("context identity changed")
        if anchor["kind"] == "scheduled":
            expected = None if issued is None else {"kind": "scheduled", "queue_event_id": issued["id"],
                "queue_run_id": issued["run_id"], "time_us": issued["time_us"],
                "system_priority": issued["system_priority"], "order": issued["order"], "causal": issued["causal"]}
            if expected != anchor: raise PositionError("unknown scheduled anchor")
            if frame["stage"] == "scheduled_handle" and uid in self.handled: raise PositionError("scheduled handle replay")
            if frame["stage"] == "batch_tail" and uid not in self.handled: raise PositionError("handle position missing")
        elif issued is not None:
            raise PositionError("native borrowed scheduled identity")
        if self.cursor is not None:
            if _slot(self.cursor["anchor"]) == _slot(anchor) and self.cursor["anchor"]["queue_run_id"] != uid:
                raise PositionError("same slot different run")
            if _position(frame) < _position(self.cursor): raise PositionError("position regression")
        identity = None
        if continuation:
            identity = _identity(frame)
            if identity in self.consumed: raise PositionError("continuation replay")
            if frame["source"]["dispatch_run_id"] in self.dispatches: raise PositionError("dispatch identity reused")
            previous_sequence = self.continuation["continuation_sequence"] if self.continuation is not None else 0
            if frame["continuation_sequence"] != previous_sequence+1: raise PositionError("continuation sequence")
            if self.continuation is not None: _span_successor(self.continuation, frame)
        if self.steps >= self.limits['position_work']: raise PositionLimitError("position work limit")
        # Upper bound is intentionally append-only; no deletion earns more space.
        extra = _bytes(frame)+_bytes(identity)+32
        if self.bytes+extra > self.limits['position_bytes']: raise PositionLimitError("position byte limit")
        return {"frame": copy.deepcopy(frame), "anchor": copy.deepcopy(anchor), "uid": uid,
            "identity": identity, "extra_bytes": extra, "continuation": continuation}

    def publish(self, prepared):
        """No validation, copying, callbacks or reads of caller data after reserve."""
        self.cursor = prepared["frame"]
        self.contexts[prepared["uid"]] = prepared["anchor"]
        if prepared["continuation"]:
            self.continuation = prepared["frame"]
            self.consumed.add(prepared["identity"])
            self.dispatches.add(prepared["frame"]["source"]["dispatch_run_id"])
        if prepared["frame"]["stage"] == "scheduled_handle": self.handled.add(prepared["uid"])
        self.steps += 1
        self.bytes += prepared["extra_bytes"]
