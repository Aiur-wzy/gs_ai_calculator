"""Optional wall-clock measurements without inspecting or copying work payloads."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter

_active = ContextVar("app_operation_timings", default=None)


@contextmanager
def collect_operation_timings():
    rows = []
    token = _active.set(rows)
    try:
        yield rows
    finally:
        _active.reset(token)


def timed_call(stage, operation, *args, **kwargs):
    rows = _active.get()
    if rows is None:
        return operation(*args, **kwargs)
    started = perf_counter()
    try:
        return operation(*args, **kwargs)
    finally:
        rows.append({"stage": stage, "started": started, "elapsed_seconds": perf_counter() - started})
