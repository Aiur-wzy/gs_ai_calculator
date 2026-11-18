"""Execution identity and immutable event-route boundaries after E5 migration."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from genshin_engine import mechanics
from genshin_engine.mechanics_event_execution import (
    _GAMEPLAY_PREPARERS,
    _SYSTEM_HANDLERS,
    dispatch_mechanics_event,
)
from genshin_engine.mechanics_scheduling import ObserverEventEmitter
from genshin_engine.shared_pool_runtime import SharedPoolRuntime
from genshin_engine.state_runtime import StateRuntimeError
from tests.test_shared_pool_execution import setup


def _context(**overrides):
    return SimpleNamespace(queue=[], conditions={}, shared_pools=None,
                           attribute_runtime=None, state_runtime=None, counter_views=None,
                           cancelled_actions=set(), time=0, trace=[], **overrides)


def _event(kind="future_event", event_id="event", data=None):
    return {"id": event_id, "run_id": "0:" + event_id, "kind": kind,
            "origin": 0, "time": 0, "order": 0, "data": data or {}}


def test_routes_are_frozen_and_only_register_generic_event_kinds():
    assert set(_SYSTEM_HANDLERS) == {
        "state_runtime_event", "elemental_state_operation", "special_reaction_operation",
        "mechanics_snapshot_capture", "mechanics_activate",
        "target_status_operation", "health_operation",
    }
    assert set(_GAMEPLAY_PREPARERS) == {
        "action", "mechanics_end", "mechanics_hit", "elemental_application",
        "mechanics_condition", "elemental_state_deadline",
    }
    with pytest.raises(TypeError):
        _SYSTEM_HANDLERS["runtime_plugin"] = lambda *_args: None
    with pytest.raises(TypeError):
        _GAMEPLAY_PREPARERS["runtime_plugin"] = lambda *_args: None


def test_unknown_event_still_binds_the_frame_and_returns_the_existing_batch():
    bound = []
    ctx = _context()
    ctx.counter_views = SimpleNamespace(frames=SimpleNamespace(bind=bound.append))
    ctx.queue.append({"id": "already_emitted"})
    result = dispatch_mechanics_event(ctx, _event(), {}, "a")
    assert result is ctx.queue and len(bound) == 1
    assert bound[0] == {"id": "event", "origin": 0, "conditions": {}}


def test_internal_state_event_routes_before_reserved_identity_guard():
    ctx = _context()
    internal = _event("state_runtime_event", "@state_route:internal")
    handled = []
    ctx.state_runtime = SimpleNamespace(handle=lambda ev: handled.append(ev) or [{"id": "child"}])
    assert dispatch_mechanics_event(ctx, internal, {}, "a") is ctx.queue
    assert handled == [internal] and ctx.queue == [{"id": "child"}]
    with pytest.raises(StateRuntimeError) as caught:
        dispatch_mechanics_event(ctx, _event(event_id="@state_route:authored"), {}, "a")
    assert caught.value.issue["code"] == "state_reserved_identity"


def test_cancelled_end_returns_before_frame_binding_and_preserves_old_batch():
    ctx = _context()
    ctx.cancelled_actions.add((0, "cast"))
    ctx.queue.append({"id": "existing"})

    def unexpected(_event):
        pytest.fail("a superseded action end must stop before binding a frame")

    ctx.counter_views = SimpleNamespace(frames=SimpleNamespace(bind=unexpected))
    row = {}
    assert dispatch_mechanics_event(ctx, _event("mechanics_end", data={"action_id": "cast"}), row, "a") == []
    assert row == {"status": "cancelled"}
    assert ctx.queue == [{"id": "existing"}]
    assert ctx.trace[0]["type"] == "action_end_superseded"


def test_false_condition_keeps_explicit_empty_return_and_observes_requested_snapshot():
    ctx = _context()
    ctx.snapshot_ids, ctx.snapshots, ctx.issues = {"event"}, {}, []
    seen = []
    snapshot = {"marker": True}
    ctx.observed_snapshot = lambda event: seen.append(event) or snapshot
    ctx.queue.append({"id": "existing"})
    assert dispatch_mechanics_event(ctx, _event("mechanics_condition", data={"condition": "flag", "value": False}), {}, "a") == []
    assert ctx.snapshots[(0, "event")] is snapshot
    assert len(seen) == 1 and ctx.conditions == {"flag": False}
    assert ctx.queue == [{"id": "existing"}]


def test_root_prelude_remains_the_only_batch_time_frontline_sequence():
    runtime = object.__new__(mechanics._Mechanics)
    calls = []
    runtime.event_emitter = ObserverEventEmitter(reserve_event=lambda: None, to_ticks=int)
    runtime.event_emitter.emit({"id": "old", "origin": 0, "time": 0, "order": 0}, "old", 0, {}, "old")
    old_batch = runtime.queue
    runtime.attribute_runtime = SimpleNamespace(stage=lambda ev, phase: calls.append(("attribute", phase)))
    runtime.elemental = object()
    runtime.frontline = "before"

    def expire(time):
        assert runtime.queue == [] and runtime.queue is not old_batch
        calls.append(("expire", time))
        runtime.time = time

    def advance(time, cycle, parent):
        assert runtime.time == time and runtime.frontline == "before"
        calls.append(("elemental", time))

    runtime.expire, runtime._advance_elemental = expire, advance
    runtime.conditions, runtime.shared_pools, runtime.state_runtime = {}, None, None
    runtime.health_runtime = None
    runtime.cancelled_actions = set()
    runtime.counter_views = SimpleNamespace(frames=SimpleNamespace(bind=lambda event: calls.append(("bind", runtime.frontline))))
    result = runtime._on_event(_event(), {}, "after", {})
    assert result is runtime.queue
    assert calls == [("attribute", "pre"), ("expire", 0), ("elemental", 0), ("bind", "after")]
    assert len(old_batch) == 1


def test_hit_projection_passes_the_same_runtime_to_shared_pool_consumption(monkeypatch):
    owners, consumers = [], []
    original_hit, original_consume = mechanics._Mechanics.hit, SharedPoolRuntime.consume

    def hit(ctx, event, payload):
        owners.append(ctx)
        return original_hit(ctx, event, payload)

    def consume(pool, ctx, event, damage_hit):
        assert ctx is owners[-1]
        assert ctx.queue is ctx.event_emitter.batch
        consumers.append(ctx)
        return original_consume(pool, ctx, event, damage_hit)

    monkeypatch.setattr(mechanics._Mechanics, "hit", hit)
    monkeypatch.setattr(SharedPoolRuntime, "consume", consume)
    output = mechanics.execute_mechanics(*setup())
    assert output["result"] is not None, output["issues"]
    assert consumers and len({id(ctx) for ctx in [*owners, *consumers]}) == 1


def test_emission_port_honors_a_later_scoped_budget_hook_without_rebinding_state():
    runtime = object.__new__(mechanics._Mechanics)
    runtime.event_emitter = ObserverEventEmitter(reserve_event=runtime.count_event, to_ticks=int)
    seen = []
    runtime._count_event = lambda kind=None: seen.append(kind)
    parent = {"id": "p", "origin": 0, "time": 0, "order": 0}
    row = runtime._enqueue(parent, "hit", 0, {}, "h")
    assert seen == [None] and runtime.queue == [row]
    failure = ValueError("scoped budget fixture")

    def fail(kind=None):
        raise failure

    runtime._count_event = fail
    with pytest.raises(ValueError) as caught:
        runtime._enqueue(parent, "hit", 0, {}, "h2")
    assert caught.value is failure
    assert runtime.serial == 1 and runtime.queue == [row]


def test_rule_phase_keeps_class_level_callback_authority_and_original_root(monkeypatch):
    runtime = object.__new__(mechanics._Mechanics)
    runtime.state_runtime = runtime.shared_pools = None
    runtime.attribute_callbacks = runtime.attribute_runtime = None
    runtime.time, runtime.state = 0, {"rule": {"cooldown": 0}}
    runtime.rules = {"rule": {"id": "rule", "trigger": {"phase": "pre"}}}
    runtime.filter = lambda *_args: True
    runtime.snapshot = lambda: {"snapshot": True}
    runtime.beneficiaries = lambda *_args: ["a"]
    seen = []

    def class_authorized(owner, rule, event, beneficiaries, snapshot, parent):
        seen.append((owner, rule["id"], beneficiaries, snapshot))

    def instance_untrusted(*_args):
        pytest.fail("rule phase must retain its existing class-level callback dispatch")

    runtime._commit_callback = instance_untrusted
    monkeypatch.setattr(mechanics._Mechanics, "_commit_callback", class_authorized)
    runtime.phase({"id": "event"}, "pre", {})
    assert seen == [(runtime, "rule", ["a"], {"snapshot": True})]


def test_capture_reference_honors_late_capacity_fault_before_publishing_trace():
    runtime = object.__new__(mechanics._Mechanics)
    runtime.time = 100
    runtime.capture_receipts = {(0, "capture"): {"run_id": "0:capture", "snapshot_fingerprint": "known"}}
    runtime.capture_trace, runtime.trace = [], []
    failure = ValueError("scoped capacity fixture")

    def fail(_entry):
        raise failure

    runtime._reserve_capture = fail
    with pytest.raises(ValueError) as caught:
        runtime._capture_reference({"origin": 0, "id": "consumer"}, "capture", "hit")
    assert caught.value is failure
    assert runtime.capture_trace == [] and runtime.trace == []


def test_expiry_facade_sets_the_only_clock_before_rule_leaf_failure(monkeypatch):
    runtime = object.__new__(mechanics._Mechanics)
    runtime.time = 0

    def fail(owner, time):
        assert owner is runtime and owner.time == time == 123
        raise ValueError("expiry leaf fault")

    monkeypatch.setattr(mechanics, "expire_rule_instances", fail)
    with pytest.raises(ValueError, match="expiry leaf"):
        runtime.expire(123)
    assert runtime.time == 123
