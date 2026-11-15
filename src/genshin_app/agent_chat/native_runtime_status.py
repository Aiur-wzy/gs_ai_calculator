"""Process-global runtime status broker for the native Agent workbench.

The conversation event stream is deliberately not used for these facts: a
runtime may cool, fail a local heartbeat, or wake from system suspend while no
conversation is open.  This broker keeps a bounded, secret-free sequence that
can be long-polled independently and invalidates old waits after resume.
"""

from __future__ import annotations

import copy
import math
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any

MAX_RUNTIME_STATUS_EVENTS = 128
MAX_RUNTIME_STATUS_WAIT_MS = 30_000
MAX_SAFE_INTEGER = 2**53 - 1

_RUNTIME_STATES = frozenset(
    {"cold", "starting", "ready", "reconnecting", "unhealthy", "stopping", "stopped", "failed"}
)
_RUNTIME_HEALTH = frozenset({"unknown", "healthy", "degraded", "unhealthy", "stopped"})
_CREDENTIAL_MODES = frozenset({"saved", "memory_only", "required"})
_PHASES = frozenset(
    {
        "idle",
        "checking_runtime",
        "reading_credential",
        "checking_provider_catalog",
        "stopping_unhealthy_runtime",
        "starting_runtime",
        "named_pipe_handshake",
        "injecting_credential",
        "preparing_request",
        "ready",
        "cooling",
        "cold",
        "resume_detected",
        "resume_health_check",
        "cancelled",
        "failed",
        "closed",
    }
)


class NativeRuntimeStatusError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> None:
    raise NativeRuntimeStatusError(code)


def _safe_text(value: object, *, limit: int = 128) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        _fail("native_runtime_status_text_invalid")
    if any(char in value for char in ("/", "\\", "\r", "\n", "\x00")):
        _fail("native_runtime_status_text_invalid")
    return value


def _projection(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "runtime_state",
        "runtime_health",
        "runtime_incarnation",
        "failure_code",
        "credential_mode",
        "phase",
        "operation_id",
        "purpose",
        "warm_lease_ms_remaining",
        "system_resume_generation",
    }
    if set(value) - allowed:
        _fail("native_runtime_status_field_invalid")
    state = value.get("runtime_state")
    health = value.get("runtime_health")
    phase = value.get("phase", "idle")
    credential = value.get("credential_mode", "required")
    if state not in _RUNTIME_STATES:
        _fail("native_runtime_status_state_invalid")
    if health not in _RUNTIME_HEALTH:
        _fail("native_runtime_status_health_invalid")
    if phase not in _PHASES:
        _fail("native_runtime_status_phase_invalid")
    if credential not in _CREDENTIAL_MODES:
        _fail("native_runtime_status_credential_invalid")
    lease = value.get("warm_lease_ms_remaining")
    if lease is not None and (type(lease) is not int or not 0 <= lease <= MAX_SAFE_INTEGER):
        _fail("native_runtime_status_lease_invalid")
    resume_generation = value.get("system_resume_generation", 0)
    if type(resume_generation) is not int or not 0 <= resume_generation <= MAX_SAFE_INTEGER:
        _fail("native_runtime_status_generation_invalid")
    purpose = value.get("purpose")
    if purpose is not None and purpose not in {"activation", "send", "resume", "create"}:
        _fail("native_runtime_status_purpose_invalid")
    return {
        "runtime_state": state,
        "runtime_health": health,
        "runtime_incarnation": _safe_text(value.get("runtime_incarnation"), limit=256),
        "failure_code": _safe_text(value.get("failure_code"), limit=128),
        "credential_mode": credential,
        "phase": phase,
        "operation_id": _safe_text(value.get("operation_id"), limit=128),
        "purpose": purpose,
        "warm_lease_ms_remaining": lease,
        "system_resume_generation": resume_generation,
    }


class NativeRuntimeStatusBroker:
    """Bounded condition-backed projection and delta stream."""

    def __init__(self) -> None:
        self._condition = threading.Condition(threading.RLock())
        self._events: deque[dict[str, Any]] = deque(maxlen=MAX_RUNTIME_STATUS_EVENTS)
        self._seq = 0
        self._closed = False
        self._projection = _projection(
            {
                "runtime_state": "cold",
                "runtime_health": "stopped",
                "credential_mode": "required",
                "phase": "cold",
            }
        )

    def publish(self, value: Mapping[str, Any], *, force: bool = False) -> dict[str, Any]:
        next_projection = _projection(value)
        with self._condition:
            if self._closed:
                _fail("native_runtime_status_closed")
            if (
                next_projection["system_resume_generation"]
                < self._projection["system_resume_generation"]
            ):
                # Projection construction happens outside the broker lock. A
                # thread that sampled the pre-resume generation may arrive
                # after the forced resume event; it must not roll the process
                # stream back to an older system epoch, even when its caller
                # requested a force publish.
                return self._snapshot_locked(self._seq)
            if not force and next_projection == self._projection:
                return self._snapshot_locked(self._seq)
            if not force and all(
                next_projection[key] == current
                for key, current in self._projection.items()
                if key != "warm_lease_ms_remaining"
            ):
                # The lease countdown is sampled status, not a lifecycle
                # transition.  Publishing every millisecond of drift makes a
                # long-poll return immediately and can hide the later wake,
                # failure or resume event the caller is actually waiting for.
                # Refresh the current projection without consuming sequence
                # space or waking subscribers; expiry itself still publishes
                # the material cold/stopped transition.
                self._projection = next_projection
                return self._snapshot_locked(self._seq)
            self._seq += 1
            self._projection = next_projection
            self._events.append({"seq": self._seq, **copy.deepcopy(next_projection)})
            self._condition.notify_all()
            return copy.deepcopy(self._events[-1])

    def _snapshot_locked(self, after_seq: int) -> dict[str, Any]:
        first = self._events[0]["seq"] if self._events else self._seq + 1
        gap = after_seq < first - 1
        events = [] if gap else [copy.deepcopy(row) for row in self._events if row["seq"] > after_seq]
        return {
            "contract": "native-runtime-status-v1",
            "requested_after_seq": after_seq,
            "next_seq": self._seq,
            "gap": gap,
            "baseline": copy.deepcopy(self._projection) if gap or after_seq == 0 else None,
            "events": events,
            "closed": self._closed,
            "system_resume_generation": self._projection["system_resume_generation"],
        }

    def snapshot(self, after_seq: int = 0) -> dict[str, Any]:
        if type(after_seq) is not int or not 0 <= after_seq <= MAX_SAFE_INTEGER:
            _fail("native_runtime_status_cursor_invalid")
        with self._condition:
            if after_seq > self._seq:
                _fail("native_runtime_status_cursor_ahead")
            return self._snapshot_locked(after_seq)

    def wait(self, after_seq: int = 0, *, timeout_ms: int = 12_000) -> dict[str, Any]:
        if type(after_seq) is not int or not 0 <= after_seq <= MAX_SAFE_INTEGER:
            _fail("native_runtime_status_cursor_invalid")
        if type(timeout_ms) is not int or not 0 <= timeout_ms <= MAX_RUNTIME_STATUS_WAIT_MS:
            _fail("native_runtime_status_timeout_invalid")
        deadline = time.monotonic() + timeout_ms / 1000
        timed_out = False
        with self._condition:
            if after_seq > self._seq:
                _fail("native_runtime_status_cursor_ahead")
            while not self._closed and after_seq == self._seq:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                self._condition.wait(remaining)
            result = self._snapshot_locked(after_seq)
        result["timed_out"] = bool(timed_out and not result["events"] and not result["gap"])
        return result

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._seq += 1
            final = {**self._projection, "runtime_state": "stopped", "runtime_health": "stopped", "phase": "closed"}
            self._projection = _projection(final)
            self._events.append({"seq": self._seq, **copy.deepcopy(self._projection)})
            self._condition.notify_all()


class NativeSuspendDetector:
    """Detect a suspend-sized wall/monotonic discontinuity without OS hooks."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        threshold_seconds: float = 5.0,
    ) -> None:
        if not callable(monotonic) or not callable(wall_clock):
            raise TypeError("native_suspend_clock_invalid")
        if (
            not isinstance(threshold_seconds, (int, float))
            or isinstance(threshold_seconds, bool)
            or not math.isfinite(float(threshold_seconds))
            or float(threshold_seconds) <= 0
        ):
            raise ValueError("native_suspend_threshold_invalid")
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._threshold = float(threshold_seconds)
        self._last_mono = float(monotonic())
        self._last_wall = float(wall_clock())

    def sample(self) -> bool:
        now_mono = float(self._monotonic())
        now_wall = float(self._wall_clock())
        values = (now_mono, now_wall, self._last_mono, self._last_wall)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("native_suspend_clock_invalid")
        mono_elapsed = max(0.0, now_mono - self._last_mono)
        wall_elapsed = max(0.0, now_wall - self._last_wall)
        self._last_mono, self._last_wall = now_mono, now_wall
        return wall_elapsed - mono_elapsed >= self._threshold


__all__ = [
    "MAX_RUNTIME_STATUS_EVENTS",
    "MAX_RUNTIME_STATUS_WAIT_MS",
    "NativeRuntimeStatusBroker",
    "NativeRuntimeStatusError",
    "NativeSuspendDetector",
]
