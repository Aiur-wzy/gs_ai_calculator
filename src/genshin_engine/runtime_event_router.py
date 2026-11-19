"""Extensible dispatch for engine-generated runtime events.

The router owns no gameplay state and performs no scheduling.  It only maps a
stable event type to one reviewed handler, so new reaction families do not add
another branch to the mechanics coordinator.  Handler registration is frozen
before execution and duplicate ownership is rejected.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import Any

RuntimeEventHandler = Callable[[dict[str, Any], int, dict[str, Any]], None]


class RuntimeEventRouter:
    def __init__(self, handlers: Mapping[str, RuntimeEventHandler]):
        normalized: dict[str, RuntimeEventHandler] = {}
        for event_type, handler in handlers.items():
            if not isinstance(event_type, str) or not event_type:
                raise ValueError("runtime event type must be a non-empty string")
            if not callable(handler):
                raise TypeError(f"runtime event handler is not callable: {event_type}")
            if event_type in normalized:
                raise ValueError(f"runtime event type has more than one owner: {event_type}")
            normalized[event_type] = handler
        self._handlers = MappingProxyType(normalized)

    def __deepcopy__(self, memo):
        # Retain immutable registration, but rebind bound handlers to the
        # copied owner. Reusing the original proxy would cross branch state.
        clone = type(self).__new__(type(self))
        memo[id(self)] = clone
        clone._handlers = MappingProxyType(copy.deepcopy(dict(self._handlers), memo))
        return clone

    @property
    def event_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))

    def dispatch(self, event: dict[str, Any], cycle: int, parent: dict[str, Any]) -> bool:
        handler = self._handlers.get(event.get("type"))
        if handler is None:
            return False
        handler(event, cycle, parent)
        return True


__all__ = ["RuntimeEventHandler", "RuntimeEventRouter"]
