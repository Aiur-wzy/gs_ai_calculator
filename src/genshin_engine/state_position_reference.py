"""Isolated R3 position candidate, with no queue or dispatch authority.

All input state/catalogues are caller facts. This module checks their bounded
mathematical consistency. A future clock owns those facts; a future bridge must
authenticate the actual queue, resource batch and applied control barrier. The
result is a prepared JSON candidate, never a permit or a committed transition.
"""
from __future__ import annotations

import copy
import json


class PositionError(ValueError):
    pass


def _integer(value, minimum=0, maximum=10**15):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PositionError("integer/range")


def _text(value):
    if type(value) is not str or not value.strip() or len(value) > 512:
        raise PositionError("text")


def _shape(value, fields):
    if type(value) is not dict or set(value) != set(fields.split()):
        raise PositionError("shape")


def _bytes(value):
    try:
        return len(json.dumps(value, ensure_ascii=False, allow_nan=False,
            sort_keys=True, separators=(",", ":")).encode("utf8"))
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise PositionError("finite JSON") from exc


def _anchor(value):
    _shape(value, "kind queue_event_id queue_run_id time_us system_priority order causal")
    if value["kind"] not in ("native", "scheduled"): raise PositionError("anchor kind")
    _text(value["queue_event_id"]); _text(value["queue_run_id"])
    _integer(value["time_us"])
    for key in ("system_priority", "order"):
        _integer(value[key], -10**6, 10**6)
    if type(value["causal"]) is not list or len(value["causal"]) > 32:
        raise PositionError("causal path")
    for part in value["causal"]: _integer(part, -10**6, 10**6)


def _frame(value):
    if type(value) is not dict: raise PositionError("frame")
    continuation = value.get("stage") in ("resource_tail", "batch_tail")
    _shape(value, "anchor phase stage" + (" resource_span binding_id binding_order continuation_sequence source" if continuation else ""))
    _anchor(value["anchor"])
    if value["phase"] not in ("pre", "post"): raise PositionError("phase")
    kind, stage = value["anchor"]["kind"], value["stage"]
    if (kind == "native" and stage not in ("resource_tail", "native_observe")
            or kind == "scheduled" and (value["phase"] != "post" or stage not in ("scheduled_handle", "batch_tail"))):
        raise PositionError("phase/stage combination")
    if continuation:
        span = value["resource_span"]
        if type(span) is not list or len(span) != 2: raise PositionError("span")
        for sequence in span: _integer(sequence, 1)
        if span[0] > span[1]: raise PositionError("span order")
        _text(value["binding_id"])
        _integer(value["binding_order"], 0, 10**6)
        _integer(value["continuation_sequence"], 1, 320000)
        source = value["source"]
        _shape(source, "dispatch_run_id operation_run_id operation_id phase")
        for field in ("dispatch_run_id", "operation_run_id", "operation_id"): _text(source[field])
        if source["phase"] not in ("pre", "post"): raise PositionError("source phase")
        if kind == "native" and source["phase"] != value["phase"]:
            raise PositionError("native source phase")
    return continuation


def _slot(anchor):
    return anchor["time_us"], anchor["system_priority"], anchor["order"], tuple(anchor["causal"])


def _position(frame):
    stage = {"scheduled_handle": 0, "resource_tail": 1, "native_observe": 2, "batch_tail": 3}[frame["stage"]]
    detail = (frame["resource_span"][1], frame["binding_order"], frame["continuation_sequence"]) if "resource_span" in frame else (0, 0, 0)
    return (*_slot(frame["anchor"]), frame["phase"] == "post", stage, *detail)


def _identity(frame):
    return (frame["anchor"]["queue_run_id"], frame["source"]["operation_run_id"],
        *frame["resource_span"], frame["binding_id"])


def _span_successor(old, new):
    """Check the same transaction's ordered targets, or its later ledger span."""
    if old["resource_span"] == new["resource_span"]:
        if old["anchor"]["queue_run_id"] != new["anchor"]["queue_run_id"]:
            raise PositionError("span borrowed by another anchor")
        fields = "operation_run_id", "operation_id", "phase"
        if tuple(old["source"][key] for key in fields) != tuple(new["source"][key] for key in fields):
            raise PositionError("span source changed")
        if new["binding_order"] <= old["binding_order"]:
            raise PositionError("binding order conflict/regression")
    elif new["resource_span"][0] <= old["resource_span"][1]:
        raise PositionError("span overlap/regression")


class PositionProgram:
    def __init__(self, *, scheduled_anchors=(), max_bytes=8*1024*1024):
        _integer(max_bytes, 1, 32*1024*1024)
        if type(scheduled_anchors) not in (list, tuple) or len(scheduled_anchors) > 40000:
            raise PositionError("scheduled catalogue limit")
        self._scheduled = {}
        for value in scheduled_anchors:
            _anchor(value)
            if value["kind"] != "scheduled" or value["queue_run_id"] in self._scheduled:
                raise PositionError("scheduled catalogue identity")
            self._scheduled[value["queue_run_id"]] = copy.deepcopy(value)
        if _bytes(self._scheduled) > max_bytes: raise PositionError("catalogue byte limit")
        self._max_bytes = max_bytes

    @staticmethod
    def initial_state():
        return {"contract": "state-position-candidate-v1", "cursor": None,
                "contexts": {}, "consumed": [], "handled": [], "position_steps": 0}

    def _state(self, state):
        _shape(state, "contract cursor contexts consumed handled position_steps")
        if state["contract"] != "state-position-candidate-v1": raise PositionError("state contract")
        if type(state["contexts"]) is not dict or type(state["consumed"]) is not list or type(state["handled"]) is not list:
            raise PositionError("state collections")
        _integer(state["position_steps"], 0, 40000)
        for name in ("contexts", "consumed", "handled"):
            if len(state[name]) > state["position_steps"]: raise PositionError("state count")
        slots = set()
        for uid, anchor in state["contexts"].items():
            _anchor(anchor)
            if uid != anchor["queue_run_id"]: raise PositionError("state context identity")
            slot = _slot(anchor)
            if slot in slots: raise PositionError("state contexts share slot")
            slots.add(slot)
            if (anchor["kind"] == "scheduled" and self._scheduled.get(uid) != anchor
                    or anchor["kind"] == "native" and uid in self._scheduled):
                raise PositionError("state context catalogue")
        identities, dispatch_ids, previous = set(), set(), None
        for sequence, frame in enumerate(state["consumed"], 1):
            if not _frame(frame) or frame["continuation_sequence"] != sequence:
                raise PositionError("state continuation sequence")
            if state["contexts"].get(frame["anchor"]["queue_run_id"]) != frame["anchor"]:
                raise PositionError("state continuation context")
            identity = _identity(frame)
            if identity in identities: raise PositionError("state replay")
            identities.add(identity)
            if frame["source"]["dispatch_run_id"] in dispatch_ids:
                raise PositionError("state dispatch identity reused")
            dispatch_ids.add(frame["source"]["dispatch_run_id"])
            if previous is not None:
                if _position(frame) <= _position(previous): raise PositionError("state continuation position order")
                _span_successor(previous, frame)
            previous = frame
        if any(type(uid) is not str for uid in state["handled"]) or len(set(state["handled"])) != len(state["handled"]):
            raise PositionError("state handled identity")
        for uid in state["handled"]:
            if uid not in self._scheduled or state["contexts"].get(uid) != self._scheduled[uid]:
                raise PositionError("state handled catalogue")
        if any(anchor["kind"] == "scheduled" and uid not in state["handled"] for uid, anchor in state["contexts"].items()):
            raise PositionError("state scheduled context lacks handle")
        if len(state["consumed"])+len(state["handled"]) > state["position_steps"]:
            raise PositionError("state position work count")
        if state["cursor"] is None:
            if state["position_steps"] or state["contexts"]: raise PositionError("state empty cursor")
        else:
            cursor_is_continuation = _frame(state["cursor"])
            if state["contexts"].get(state["cursor"]["anchor"]["queue_run_id"]) != state["cursor"]["anchor"]:
                raise PositionError("state cursor identity")
            if any(_position(old) > _position(state["cursor"]) for old in state["consumed"]):
                raise PositionError("state cursor precedes continuation")
            if any(_slot(anchor) > _slot(state["cursor"]["anchor"]) for anchor in state["contexts"].values()):
                raise PositionError("state cursor precedes known context")
            if cursor_is_continuation and (not state["consumed"] or state["cursor"] != state["consumed"][-1]):
                raise PositionError("state continuation cursor is not history tail")

    def plan(self, state, frame):
        """Prepare one position; caller reserves its combined work before publish.

        No method commits to a clock, validates game timing, or authenticates a
        source. ``requires_applied_barrier`` is an obligation, never its receipt.
        """
        if _bytes([state, frame]) > self._max_bytes: raise PositionError("input byte limit")
        self._state(state)
        continuation = _frame(frame)
        anchor, prior = frame["anchor"], state["cursor"]
        uid = anchor["queue_run_id"]
        known = state["contexts"].get(uid)
        if known is not None and known != anchor: raise PositionError("context identity changed")
        if anchor["kind"] == "scheduled":
            if self._scheduled.get(uid) != anchor: raise PositionError("unknown scheduled anchor")
            if frame["stage"] == "scheduled_handle" and uid in state["handled"]:
                raise PositionError("scheduled handle replay")
            if frame["stage"] == "batch_tail" and uid not in state["handled"]:
                raise PositionError("scheduled handle position missing")
        elif uid in self._scheduled:
            raise PositionError("native borrowed scheduled identity")
        if prior is not None:
            if _slot(prior["anchor"]) == _slot(anchor) and prior["anchor"]["queue_run_id"] != uid:
                raise PositionError("same slot different run")
            if _position(frame) < _position(prior): raise PositionError("position regression")
        if continuation:
            if _identity(frame) in {_identity(old) for old in state["consumed"]}:
                raise PositionError("continuation replay")
            if frame["continuation_sequence"] != len(state["consumed"])+1:
                raise PositionError("continuation sequence")
            if any(old["source"]["dispatch_run_id"] == frame["source"]["dispatch_run_id"] for old in state["consumed"]):
                raise PositionError("dispatch identity reused")
            if state["consumed"]: _span_successor(state["consumed"][-1], frame)
        next_state = copy.deepcopy(state)
        next_state["contexts"][uid] = copy.deepcopy(anchor)
        next_state["cursor"] = copy.deepcopy(frame)
        next_state["position_steps"] += 1
        if next_state["position_steps"] > 40000: raise PositionError("position work limit")
        if continuation: next_state["consumed"].append(copy.deepcopy(frame))
        if frame["stage"] == "scheduled_handle": next_state["handled"].append(uid)
        result = {"status": "candidate_only", "dispatch_authority": False,
            "requires_applied_barrier": frame["stage"] == "batch_tail", "state": next_state,
            "candidate_scan_units": 1+len(state["contexts"])+(4 if continuation else 2)*len(state["consumed"])+len(state["handled"])}
        if _bytes(result) > self._max_bytes: raise PositionError("candidate byte limit")
        return result
