"""Phase adapters use an explicit activation port, not a growing Context API."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_state_runtime_mechanics import Context, model, setup, start_event

from genshin_engine.mechanics_rule_execution import commit_rule_callback


def test_real_phase_adapter_keeps_its_existing_context_contract():
    context = setup()
    assert not hasattr(Context, "activate_or_delay")
    context.use_real_phase = True
    event, parent = start_event()
    queued = context.bridge.observe(event, "post", parent, successful=True)
    assert queued
    assert len(context.state["buff"]["instances"]) == 1
    assert context.bridge.report()["execution_trace"][-1]["outcome"] == "activated"


def inputs():
    data = model()
    selected = data["rules"][0]
    selected["timing"]["cooldown"] = .3
    selected["timing"]["cooldown_anchor"] = "success"
    event, parent = start_event()
    context = SimpleNamespace(time=1_000_000, state={selected["id"]: {"cooldown": 0, "pending": []}})
    return context, selected, event, parent


def unexpected_selection(*_args):
    pytest.fail("ordinary beneficiary scope must not invoke selection")


def test_rule_leaf_calls_only_injected_activation_and_preserves_order_and_identity():
    context, selected, event, parent = inputs()
    snapshot, beneficiaries, calls = {"frozen": True}, ["a"], []

    def activate(*args):
        assert context.state[selected["id"]]["cooldown"] == 1_300_000
        calls.append(args)

    commit_rule_callback(context, selected, event, beneficiaries, snapshot, parent,
                         tick=lambda value: round(value * 1_000_000),
                         selection_factory=unexpected_selection, activate_or_delay=activate)
    assert len(calls) == 1
    assert all(actual is expected for actual, expected in zip(calls[0][:5],
        (selected, event, beneficiaries, snapshot, parent), strict=True))
    assert calls[0][5] is None
    assert not hasattr(context, "activate_or_delay")


def test_next_switch_waits_without_entering_activation_port():
    context, selected, event, parent = inputs()
    selected["beneficiaries"] = {"mode": "next_switch", "scope": "locked"}
    snapshot = {"frozen": True}

    def unexpected(*_args):
        pytest.fail("next-switch pending state must not activate")

    commit_rule_callback(context, selected, event, ["a"], snapshot, parent,
                         tick=lambda value: round(value * 1_000_000),
                         selection_factory=unexpected_selection, activate_or_delay=unexpected)
    waiting = context.state[selected["id"]]["pending"]
    assert waiting == [{"event": event, "snapshot": snapshot}]
    assert waiting[0]["event"] is not event
    assert waiting[0]["snapshot"] is snapshot
    assert context.state[selected["id"]]["cooldown"] == 0


def test_activation_port_failure_retains_original_exception():
    context, selected, event, parent = inputs()
    failure = RuntimeError("activation fixture")

    def fail(*_args):
        raise failure

    with pytest.raises(RuntimeError) as caught:
        commit_rule_callback(context, selected, event, ["a"], {}, parent,
                             tick=lambda value: round(value * 1_000_000),
                             selection_factory=unexpected_selection, activate_or_delay=fail)
    assert caught.value is failure
