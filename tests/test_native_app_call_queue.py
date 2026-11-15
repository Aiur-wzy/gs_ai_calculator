"""App resource admission, not a replacement for DSH's tool scheduler."""
from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from genshin_app.agent_chat.native_app_call_queue import (
    AppCallDeferred,
    AppQueueProtocolError,
    NativeAppCallQueue,
)


def request(session="one", **changes):
    value = {"request_id": str(uuid.uuid4()), "session_id": session,
             "call_id": str(uuid.uuid4()), "raw_arguments_hash": "a" * 64}
    return value | changes


def cancel(value):
    return {"contract": "genshin-native-dsh-domain-cancel-v1", "request_id": str(uuid.uuid4()),
            "session_id": value["session_id"], "target_request_id": value["request_id"],
            "target_kind": "dsh_app_call_request", "call_id": value["call_id"],
            "raw_arguments_hash": value["raw_arguments_hash"], "reason": "user_cancel"}


def test_safe_group_serializes_actual_resource_and_cancel_never_executes():
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    calls, finished, cancelled = [], [], []
    with ThreadPoolExecutor(4) as workers, ThreadPoolExecutor(1) as controls:
        def execute(_kind, value):
            calls.append(value["request_id"])
            started.set()
            assert release.wait(3)
            return {"status": "settled_success"}

        queue = NativeAppCallQueue(executor=workers, control_executor=controls,
            route_cached=lambda _sid: "same-candidate", route_resolve=lambda _sid: "same-candidate",
            execute=execute, finished=lambda rid: finished.append(rid) or done.set(),
            cancelled=lambda value: cancelled.append(value["request_id"]))
        first, second = request(), request()
        queue.submit("dsh_app_call_request", first)
        assert started.wait(1)
        queue.submit("dsh_app_call_request", second)
        result = queue.cancel(cancel(second))
        assert result["execution_state"] == "did_not_execute"
        assert result["app_call_admission"] == "not_recorded"
        assert queue.snapshot()["running"] == 1
        release.set()
        queue.wait_idle()
        assert calls == [first["request_id"]]
        assert cancelled == [second["request_id"]]
        assert sorted(finished) == sorted([first["request_id"], second["request_id"]])
        queue.close()


def test_unresolved_alias_watermark_prevents_late_known_scope_overtaking():
    resolving, resolve, started = threading.Event(), threading.Event(), threading.Event()
    order = []
    with ThreadPoolExecutor(4) as workers, ThreadPoolExecutor(1) as controls:
        def route(_sid):
            resolving.set()
            assert resolve.wait(3)
            return "same"

        queue = NativeAppCallQueue(executor=workers, control_executor=controls,
            route_cached=lambda sid: None if sid == "unresolved" else "same", route_resolve=route,
            execute=lambda _kind, value: order.append(value["session_id"]) or started.set() or {},
            finished=lambda _rid: None, cancelled=lambda _value: None)
        queue.submit("dsh_app_call_request", request("unresolved"))
        assert resolving.wait(1)
        queue.submit("dsh_app_call_request", request("known"))
        assert not started.is_set()
        assert queue.snapshot()["running"] == 0
        resolve.set()
        queue.wait_idle()
        assert order == ["unresolved", "known"]
        queue.close()


def test_running_cancel_is_not_proof_of_no_side_effect_and_replays_same_ack():
    started, release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(1) as workers, ThreadPoolExecutor(1) as controls:
        def execute(_kind, _value):
            started.set()
            assert release.wait(3)
            return {"status": "settled_success"}

        queue = NativeAppCallQueue(executor=workers, control_executor=controls,
            route_cached=lambda _sid: "same", route_resolve=lambda _sid: "same",
            execute=execute, finished=lambda _rid: None, cancelled=lambda _value: None)
        value = request()
        queue.submit("dsh_app_call_request", value)
        assert started.wait(1)
        cancellation = cancel(value)
        ack = queue.cancel(cancellation)
        assert ack["disposition"] == "running" and ack["outcome_unknown"]
        release.set()
        queue.wait_idle()
        assert queue.cancel(cancellation) == ack
        assert queue.cancel(cancel(value))["execution_state"] == "settled_success"
        queue.close()


def test_file_release_before_waiter_recheck_does_not_lose_wakeup():
    calls, subscriptions = [], []
    state = {"busy": True, "generation": 1}
    with ThreadPoolExecutor(1) as workers, ThreadPoolExecutor(1) as controls:
        def subscribe(_key, notify):
            subscriptions.append(notify)
            # Release between the original occupation and subscribe/recheck.
            state.update(busy=False, generation=2)
            return lambda: None

        def execute(_kind, _value):
            calls.append(1)
            if len(calls) == 1:
                # The gate was occupied, then released before safe defer gets
                # back to the queue. Its current snapshot must be rechecked.
                state.update(busy=False, generation=4)
                raise AppCallDeferred
            return {"status": "settled_success"}

        queue = NativeAppCallQueue(executor=workers, control_executor=controls,
            route_cached=lambda _sid: "same", route_resolve=lambda _sid: "same", execute=execute,
            finished=lambda _rid: None, cancelled=lambda _value: None,
            subscribe_resource=subscribe, resource_snapshot=lambda _key: dict(state))
        queue.submit("dsh_app_call_request", request())
        queue.wait_idle()
        assert calls == [1, 1] and len(subscriptions) == 1
        queue.close()


def test_early_cancel_tombstone_prevents_late_business_frame_execution():
    executed, cancelled = [], []
    with ThreadPoolExecutor(1) as workers, ThreadPoolExecutor(1) as controls:
        queue = NativeAppCallQueue(executor=workers, control_executor=controls,
            route_cached=lambda _sid: "same", route_resolve=lambda _sid: "same",
            execute=lambda *_: executed.append(1), finished=lambda _rid: None,
            cancelled=lambda value: cancelled.append(value["request_id"]))
        value = request()
        ack = queue.cancel(cancel(value))
        assert ack["disposition"] == "not_observed" and ack["outcome_unknown"]
        queue.submit("dsh_app_call_request", value)
        queue.wait_idle()
        assert executed == [] and cancelled == [value["request_id"]]
        with pytest.raises(AppQueueProtocolError, match="target_mismatch"):
            queue.cancel(cancel(value) | {"session_id": "different-owner"})
        queue.close()


def test_cancel_unresolved_ticket_advances_watermark_without_waiting_for_its_route():
    resolving, resolve, known_started = threading.Event(), threading.Event(), threading.Event()
    with ThreadPoolExecutor(2) as workers, ThreadPoolExecutor(1) as controls:
        def route(_sid):
            resolving.set()
            assert resolve.wait(3)
            return "same"

        queue = NativeAppCallQueue(executor=workers, control_executor=controls,
            route_cached=lambda sid: None if sid == "unknown" else "same", route_resolve=route,
            execute=lambda *_: known_started.set() or {}, finished=lambda _rid: None,
            cancelled=lambda _value: None)
        unknown = request("unknown")
        queue.submit("dsh_app_call_request", unknown)
        assert resolving.wait(1)
        queue.submit("dsh_app_call_request", request("known"))
        assert not known_started.is_set()
        queue.cancel(cancel(unknown))
        assert known_started.wait(1)
        resolve.set()
        queue.wait_idle()
        queue.close()


def test_receipt_retention_releases_large_parameters_and_uses_owner_for_aged_cancel():
    calls, lookups = [], []
    with ThreadPoolExecutor(1) as workers, ThreadPoolExecutor(1) as controls:
        def receipt(value):
            lookups.append(value["call_id"])
            return {"state": "settled_success", "settlement_ref": "appcall.verified"}

        queue = NativeAppCallQueue(executor=workers, control_executor=controls,
            route_cached=lambda _sid: "same", route_resolve=lambda _sid: "same",
            execute=lambda *_: {"status": "settled_success", "large": "x" * 100_000},
            finished=lambda rid: calls.append(rid), cancelled=lambda _value: None,
            retained_receipts=2, receipt_lookup=receipt)
        first = None
        for _ in range(8):
            value = request(arguments_json="x" * 100_000)
            first = first or value
            queue.submit("dsh_app_call_request", value)
            queue.wait_idle()
        assert queue.snapshot()["active_tickets"] == 0
        assert queue.snapshot()["retained_receipts"] == 2
        assert queue.snapshot()["receive_seq"] == 8
        assert "arguments_json" not in repr(queue._completed)
        assert "large" not in repr(queue._completed)
        ack = queue.cancel(cancel(first))
        assert ack["execution_state"] == "settled_success"
        assert ack["settlement_ref"] == "appcall.verified"
        assert queue.snapshot()["unmatched_cancellations"] == 0
        assert lookups == [first["call_id"]] and len(calls) == 8
        queue.close()
