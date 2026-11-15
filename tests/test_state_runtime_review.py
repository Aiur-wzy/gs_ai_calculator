"""Independent pure-runtime identity, ordering, and atomicity cases."""
import copy

import pytest
from test_state_runtime import config, event, make, two_lives

from genshin_engine.state_runtime import StateRuntime, StateRuntimeError


def test_same_time_external_order_cannot_go_back_after_completed_call():
    runtime = make(first=500_000)
    runtime.start("field", event(order=30), eligible=True)
    before = runtime.trace
    with pytest.raises(StateRuntimeError):
        runtime.end("field", event(run="0:earlier", order=10), eligible=True)
    assert runtime.trace == before


def test_same_actual_event_cannot_return_from_post_to_pre():
    runtime = make(first=500_000)
    runtime.start("field", event(), eligible=True)
    with pytest.raises(StateRuntimeError):
        runtime.cancel_source("0:cast", event(phase="pre"))


def test_shared_budget_failure_does_not_commit_start_identity_or_generation():
    calls = []
    def reserve(count):
        calls.append(count)
        if len(calls) == 1:
            raise StateRuntimeError("budget", "test shared budget")
    runtime = StateRuntime(config(first=500_000), character_ids=["a"], reserve_events=reserve)
    with pytest.raises(StateRuntimeError):
        runtime.start("field", event(), eligible=True)
    assert runtime.trace == [] and runtime.pending() == []
    assert runtime.start("field", event(), eligible=True)["scheduled"]
    assert calls[0] == calls[1]


def test_boundary_distinguishes_capture_aliasing_two_old_generations():
    runtime = make(first=500_000)
    a = runtime.start("field", event(), eligible=True)["scheduled"][0]["generation_uid"]
    b = runtime.start("field", event(10, "0:b"), eligible=True)["scheduled"][0]["generation_uid"]
    runtime.start("field", event(20, "0:c"), eligible=True)
    def cap(slot, generation):
        return {"slot": slot, "lifecycle_id": "field", "generation_uid": generation, "payload": {"value": 30}}
    same = runtime.boundary(20, retained_captures=[cap("q", a), cap("dot", a)])
    different = runtime.boundary(20, retained_captures=[cap("q", a), cap("dot", b)])
    assert same != different
    same["retained_captures"][0]["payload"]["value"] = 80
    assert runtime.boundary(20, retained_captures=[cap("q", a), cap("dot", a)])["retained_captures"][0]["payload"]["value"] == 30


def test_terminal_permit_cannot_be_reused_in_new_generation():
    runtime = make(end_tick="before_expiry")
    runtime.start("field", event(), eligible=True)
    runtime.handle(runtime.pending()[0])
    expiry = runtime.pending()[0]
    terminal = runtime.handle(expiry)["dispatches"][0]
    new = runtime.start("field", event(2_000_000, "0:new"), eligible=True)
    assert terminal["terminal_permit"]["generation_uid"] != new["scheduled"][0]["generation_uid"]
    with pytest.raises(StateRuntimeError):
        runtime.handle(expiry)


def test_captured_generation_survives_cancel_while_future_grants_do_not():
    runtime = make(delay=500_000)
    start = runtime.start("field", event(), eligible=True)
    generation = start["scheduled"][0]["generation_uid"]
    runtime.cancel_source("0:cast", event(100, "0:cancel"))
    capture = {"slot": "old", "lifecycle_id": "field", "generation_uid": generation, "payload": {"value": 80}}
    boundary = runtime.boundary(100, retained_captures=[capture])
    assert boundary["lifecycles"]["field"] == {"phase": "inactive"}
    assert boundary["retained_captures"][0]["generation_relation"] == "old:0"
    assert runtime.pending() == []


def test_mutated_generation_revision_in_scheduled_event_is_refused():
    runtime = make(first=500_000)
    runtime.start("field", event(), eligible=True)
    for key, value in (("revision", 2), ("generation_uid", "invented"), ("source_event", event(run="other"))):
        changed = copy.deepcopy(runtime.pending()[0])
        changed[key] = value
        with pytest.raises(StateRuntimeError):
            runtime.handle(changed)


def test_boundary_invalid_lifecycle_reference_is_structured_domain_error():
    runtime = StateRuntime(two_lives(), character_ids=["a"])
    generation = runtime.start("field", event(), eligible=True)["scheduled"][0]["generation_uid"]
    with pytest.raises(StateRuntimeError):
        runtime.boundary(0, retained_captures=[{"slot": "q", "lifecycle_id": [], "generation_uid": generation, "payload": {}}])


def test_late_cancelled_event_cannot_jump_over_earlier_new_generation_event():
    runtime = make(first=500_000)
    initial = runtime.start("field", event(), eligible=True)
    old_expiry = next(row for row in initial["scheduled"] if row["kind"] == "state_expire")
    runtime.start("field", event(100_000, "0:new"), eligible=True)
    before = runtime.pending()
    with pytest.raises(StateRuntimeError):
        runtime.handle(old_expiry)
    assert runtime.pending() == before
    assert runtime.handle(runtime.pending()[0])["dispatches"][0]["time_us"] == 600_000
