"""Root-observer integration semantics for elemental generated-event projection."""

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from genshin_engine.mechanics_elemental_bridge import (
    MechanicsElementalBridge,
    MechanicsElementalPorts,
)


def _bridge(*, elemental=None, special=None, dispatch=None, count=None):
    trace, counts, calls = [], [], []
    current = {"elemental": elemental, "special": special, "time": 42}
    bridge = MechanicsElementalBridge(MechanicsElementalPorts(
        get_elemental=lambda: current["elemental"],
        get_special_runtime=lambda: current["special"],
        get_time=lambda: current["time"],
        append_trace=trace.append,
        count_event=count or counts.append,
        dispatch_generated=dispatch or (lambda row, cycle, parent: calls.append((row, cycle, parent)) or False),
    ))
    return bridge, current, trace, counts, calls


def _event(event_id="e", **extra):
    return {"type": "special_reaction_damage_request", "event_id": event_id,
            "time_tick": 42, "t": .000042, "reaction_type": "lunar_bloom", **extra}


def test_record_deduplication_and_both_trace_copies_remain_independent():
    bridge, _, trace, _, _ = _bridge()
    original = _event(detail={"value": [1]})
    assert not bridge.record_special_event(None, 0)
    assert not bridge.record_special_event({"type": "ordinary"}, 0)
    assert bridge.record_special_event(original, 2)
    assert not bridge.record_special_event(copy.deepcopy(original), 3)
    assert bridge.record_special_event(_event(component="another"), 2)
    assert original.get("cycle") is None
    assert bridge.trace[0]["cycle"] == trace[0]["cycle"] == 2
    original["detail"]["value"].append(2)
    bridge.trace[0]["detail"]["value"].append(3)
    assert trace[0]["detail"] == {"value": [1]}


def test_drain_preserves_record_dispatch_count_and_final_sync_order():
    generated = [_event("handled"), _event("unhandled")]
    special = SimpleNamespace(trace=[*generated, _event("trace_only")])
    bridge, _, trace, counts, _ = _bridge(special=special)
    dispatched = []

    def dispatch(row, cycle, parent):
        # The event's first projection is already visible before dispatch.
        assert trace[-1]["event_id"] == row["event_id"]
        dispatched.append((row["event_id"], cycle, parent))
        return row["event_id"] == "handled"

    bridge.ports = MechanicsElementalPorts(
        **{field: getattr(bridge.ports, field) for field in (
            "get_elemental", "get_special_runtime", "get_time", "append_trace", "count_event")},
        dispatch_generated=dispatch,
    )
    bridge.drain_generated(2, events={"generated_events": generated})
    assert [row["event_id"] for row in trace] == ["handled", "unhandled", "trace_only"]
    assert [name for name, _, _ in dispatched] == ["handled", "unhandled"]
    assert dispatched[0][1:] == (2, {"id": "@special_runtime", "run_id": "2:@special_runtime",
                                   "time": 42, "order": 0, "origin": 2, "causal": ()})
    assert counts == ["special_reaction_event", "special_reaction_event"]
    assert bridge.cursor == len(special.trace)
    bridge.sync_trace(2)
    assert len(trace) == 3 and len(counts) == 2


def test_advance_applies_only_the_root_supplied_boundary_then_drains():
    elemental = SimpleNamespace(trace=[])
    special = SimpleNamespace(trace=[])
    bridge, current, trace, counts, calls = _bridge(elemental=elemental, special=special)
    advanced, drained = [], []

    def advance(time):
        advanced.append(time)
        elemental.trace.append({"type": "elemental_state_transition", "t": time})

    def drain():
        drained.append(True)
        return [_event()]

    elemental.advance = advance
    elemental.drain_generated_events = drain
    parent = {"id": "root", "time": 42}
    bridge.advance_to(42, 3, parent)
    assert advanced == [42] and drained == [True]
    assert current["time"] == 42
    assert counts == ["state_transition", "special_reaction_event"]
    assert elemental.trace[0]["cycle"] == trace[0]["cycle"] == 3
    assert trace[0] is not elemental.trace[0]
    assert calls[0][2] is parent
    assert not hasattr(bridge, "time") and not hasattr(bridge, "queue")


def test_older_runtime_adapter_keeps_its_explicit_generated_batch():
    events = [_event()]
    calls = []
    elemental = SimpleNamespace(trace=[], advance=lambda time: calls.append(("elemental", time)))
    special = SimpleNamespace(trace=[], advance=lambda time: calls.append(("special", time)) or events)
    bridge, _, trace, counts, _ = _bridge(elemental=elemental, special=special)
    bridge.advance_to(42)
    assert calls == [("elemental", 42), ("special", 42)]
    assert len(trace) == 1 and counts == ["special_reaction_event"]


def test_trace_cursor_and_record_precede_the_existing_budget_failure():
    special = SimpleNamespace(trace=[_event()])
    failure = ValueError("budget fixture")

    def fail(_kind):
        raise failure

    bridge, _, trace, _, _ = _bridge(special=special, count=fail)
    with pytest.raises(ValueError) as caught:
        bridge.sync_trace(2)
    assert caught.value is failure
    assert bridge.cursor == 1 and len(trace) == 1 and len(bridge.seen) == 1


def test_no_special_runtime_does_not_read_clock_or_elemental_or_dispatch():
    def unexpected(*_args):
        pytest.fail("an absent special runtime must short-circuit the integration port")

    bridge = MechanicsElementalBridge(MechanicsElementalPorts(
        get_elemental=unexpected, get_special_runtime=lambda: None, get_time=unexpected,
        append_trace=unexpected, count_event=unexpected, dispatch_generated=unexpected,
    ))
    bridge.drain_generated()
    bridge.sync_trace(0)
    assert bridge.trace == [] and bridge.cursor == 0
