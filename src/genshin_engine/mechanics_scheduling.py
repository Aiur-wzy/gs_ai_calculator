"""Derived-event emission ports for the single rotation scheduler.

The emitter owns only the observer's current output batch, its causal serial,
and the last offered deadline identity. It has no clock, global priority queue,
or authority to advance any elemental/reaction runtime. Rotation consumes each
returned batch and remains the sole owner of global ordering and time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class ElementalDeadlineSource(Protocol):
    def next_state_deadline(self, horizon: int) -> dict[str, Any] | None: ...


class SpecialReactionDeadlineSource(Protocol):
    def next_deadline(self) -> int | None: ...


def select_elemental_deadline(
    elemental: ElementalDeadlineSource,
    get_special_runtime: Callable[[], SpecialReactionDeadlineSource | None],
    horizon: int,
) -> dict[str, Any] | None:
    """Read deadline proposals in their established order without advancing time."""

    deadline = elemental.next_state_deadline(horizon)
    runtime = get_special_runtime()
    if runtime is not None:
        special_time = runtime.next_deadline()
        if special_time is not None and special_time <= horizon:
            special = {"time": special_time, "revision": special_time,
                       "state_kind": "special_reaction_runtime"}
            if deadline is None or (special["time"], special["state_kind"]) < (
                    deadline["time"], deadline.get("state_kind", "")):
                deadline = special
    return deadline


def deadline_is_current(event: dict[str, Any], deadline: dict[str, Any] | None) -> bool:
    return bool(deadline and
                (event["time"], event["data"]["revision"], event["data"].get("state_kind")) ==
                (deadline["time"], deadline["revision"], deadline.get("state_kind")))


class ObserverEventEmitter:
    """One emission buffer shared with the mechanics observer's existing users."""

    def __init__(self, *, reserve_event: Callable[[], None], to_ticks: Callable[[Any], int]):
        self.batch: list[dict[str, Any]] = []
        self.serial = 0
        self.deadline_queued: tuple[int, Any, Any] | None = None
        self._reserve_event = reserve_event
        self._to_ticks = to_ticks

    def begin_batch(self) -> None:
        # Replace the list, as the prior observer did. A root scheduler retaining
        # the previous return value must never see that older batch cleared.
        self.batch = []

    def emit(self, parent, kind, delay, payload, suffix):
        self._reserve_event()
        self.serial += 1
        time = parent["time"] + self._to_ticks(delay)
        eid = f"{parent['id']}~{suffix}"
        row = {"id": eid, "kind": kind, "time": time, "order": parent["order"],
               "causal": (*parent.get("causal", ()), self.serial), "origin": parent["origin"],
               "run_id": f"{parent['origin']}:{eid}", "data": payload}
        self.batch.append(row)
        return row

    def offer_deadline(self, deadline, *, target, origin=0):
        key = ((deadline["time"], deadline["revision"], deadline.get("state_kind"))
               if deadline else None)
        if key is None or key == self.deadline_queued:
            self.deadline_queued = key
            return None
        self.deadline_queued = key
        self._reserve_event()
        eid = f"@elemental_state:{target}:{key[0]}:{key[1]}"
        return {"id": eid, "run_id": eid, "time": key[0], "origin": origin, "order": 0,
                "system_priority": -1, "kind": "elemental_state_deadline",
                "data": {"revision": key[1], "state_kind": key[2]}}


__all__ = [
    "ElementalDeadlineSource",
    "ObserverEventEmitter",
    "SpecialReactionDeadlineSource",
    "deadline_is_current",
    "select_elemental_deadline",
]
