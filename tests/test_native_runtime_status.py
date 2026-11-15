from __future__ import annotations

import threading
import time

import pytest

from genshin_app.agent_chat.native_runtime_status import (
    MAX_RUNTIME_STATUS_EVENTS,
    NativeRuntimeStatusBroker,
    NativeRuntimeStatusError,
    NativeSuspendDetector,
)


def projection(**overrides):
    return {
        "runtime_state": "starting",
        "runtime_health": "unknown",
        "runtime_incarnation": None,
        "failure_code": None,
        "credential_mode": "saved",
        "phase": "reading_credential",
        "operation_id": "runtime-op-one",
        "purpose": "send",
        "warm_lease_ms_remaining": None,
        "system_resume_generation": 0,
        **overrides,
    }


def test_broker_waits_without_provider_work_and_wakes_for_status():
    broker = NativeRuntimeStatusBroker()
    observed = {}

    def wait():
        observed["result"] = broker.wait(0, timeout_ms=2_000)

    thread = threading.Thread(target=wait)
    thread.start()
    time.sleep(0.02)
    event = broker.publish(projection())
    thread.join(2)
    assert not thread.is_alive()
    assert event["seq"] == 1
    assert observed["result"]["events"] == [event]
    assert observed["result"]["timed_out"] is False


def test_broker_is_bounded_and_gap_returns_current_baseline():
    broker = NativeRuntimeStatusBroker()
    for index in range(MAX_RUNTIME_STATUS_EVENTS + 5):
        broker.publish(projection(operation_id=f"runtime-op-{index}"))
    result = broker.snapshot(0)
    assert result["gap"] is True
    assert result["baseline"]["operation_id"] == f"runtime-op-{MAX_RUNTIME_STATUS_EVENTS + 4}"
    assert result["events"] == []
    assert result["next_seq"] == MAX_RUNTIME_STATUS_EVENTS + 5


def test_duplicate_projection_does_not_advance_and_close_wakes_waiters():
    broker = NativeRuntimeStatusBroker()
    first = broker.publish(projection())
    duplicate = broker.publish(projection())
    assert duplicate["next_seq"] == first["seq"]
    observed = {}
    thread = threading.Thread(
        target=lambda: observed.setdefault("result", broker.wait(first["seq"], timeout_ms=2_000))
    )
    thread.start()
    time.sleep(0.02)
    broker.close()
    thread.join(2)
    assert observed["result"]["closed"] is True
    assert observed["result"]["events"][0]["phase"] == "closed"


def test_lease_countdown_refresh_does_not_wake_a_lifecycle_waiter():
    broker = NativeRuntimeStatusBroker()
    first = broker.publish(projection(warm_lease_ms_remaining=300_000))
    observed = {}
    thread = threading.Thread(
        target=lambda: observed.setdefault(
            "result", broker.wait(first["seq"], timeout_ms=2_000)
        )
    )
    thread.start()
    time.sleep(0.02)

    refresh = broker.publish(projection(warm_lease_ms_remaining=299_950))
    assert refresh["next_seq"] == first["seq"]
    time.sleep(0.02)
    assert thread.is_alive()

    transition = broker.publish(
        projection(
            runtime_state="ready",
            runtime_health="healthy",
            phase="ready",
            warm_lease_ms_remaining=299_900,
        )
    )
    thread.join(2)
    assert not thread.is_alive()
    assert observed["result"]["events"] == [transition]
    assert transition["warm_lease_ms_remaining"] == 299_900


def test_stale_resume_generation_cannot_overwrite_the_newer_epoch():
    broker = NativeRuntimeStatusBroker()
    current = broker.publish(
        projection(
            runtime_state="reconnecting",
            runtime_health="unknown",
            phase="resume_detected",
            system_resume_generation=1,
        ),
        force=True,
    )

    stale = broker.publish(
        projection(
            runtime_state="ready",
            runtime_health="healthy",
            phase="ready",
            system_resume_generation=0,
        ),
        force=True,
    )

    assert stale["next_seq"] == current["seq"]
    snapshot = broker.snapshot(0)
    assert snapshot["system_resume_generation"] == 1
    assert snapshot["baseline"]["phase"] == "resume_detected"
    assert snapshot["events"] == [current]


def test_same_generation_force_publish_remains_a_real_lifecycle_event():
    broker = NativeRuntimeStatusBroker()
    first = broker.publish(
        projection(system_resume_generation=1, warm_lease_ms_remaining=300_000),
        force=True,
    )

    second = broker.publish(
        projection(system_resume_generation=1, warm_lease_ms_remaining=300_000),
        force=True,
    )

    assert second["seq"] == first["seq"] + 1
    snapshot = broker.snapshot(first["seq"])
    assert snapshot["events"] == [second]


@pytest.mark.parametrize(
    "value",
    [
        projection(path="C:/private"),
        projection(runtime_state="ready/secret"),
        projection(failure_code="C:\\private"),
        projection(phase="provider_request"),
        projection(warm_lease_ms_remaining=-1),
    ],
)
def test_broker_rejects_unreviewed_or_path_bearing_status(value):
    broker = NativeRuntimeStatusBroker()
    with pytest.raises(NativeRuntimeStatusError):
        broker.publish(value)
    assert broker.snapshot(0)["next_seq"] == 0


def test_suspend_detector_uses_wall_monotonic_discontinuity():
    values = {"mono": 10.0, "wall": 100.0}
    detector = NativeSuspendDetector(
        monotonic=lambda: values["mono"],
        wall_clock=lambda: values["wall"],
        threshold_seconds=5,
    )
    values.update(mono=12.0, wall=102.0)
    assert detector.sample() is False
    values.update(mono=13.0, wall=120.0)
    assert detector.sample() is True
    values.update(mono=14.0, wall=121.0)
    assert detector.sample() is False
