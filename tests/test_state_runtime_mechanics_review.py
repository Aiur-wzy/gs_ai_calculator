"""Independent adapter review; these tests do not certify real queue hooks."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.state_runtime import StateRuntimeError
from tests.test_state_runtime_mechanics import model, pool_model, setup, start_event


def begin(ctx):
    ctx.use_real_phase = True
    event, parent = start_event()
    return ctx.bridge.observe(event, "post", parent, successful=True)


def next_tick(ctx, queued):
    tick = next(row for row in queued if row["data"]["kind"] == "state_tick")
    ctx.expire(tick["time"])
    return ctx.bridge.handle(tick)


@pytest.mark.parametrize("target", ["rule", "operation"])
def test_each_unknown_occurrence_stays_pending_after_issue_dedup(target):
    data = model() if target == "rule" else pool_model()
    row = data["rules"][0] if target == "rule" else data["shared_pools"]["operations"][0]
    row["trigger"]["condition"] = "inside"
    ctx = setup(data, conditions={"inside"})
    ctx.conditions["inside"] = None
    queued = begin(ctx)
    next_tick(ctx, queued)
    trace = ctx.bridge.report()["execution_trace"]
    assert len(trace) == 2
    assert len({row["run_id"] for row in trace}) == 2
    assert [row["outcome"] for row in trace] == ["pending", "pending"]
    # Deduplication is fine; it must not turn the second unknown into false.
    assert len(ctx.issues) == 1


def test_short_ttl_new_instances_have_distinct_occurrence_identity():
    data = model()
    data["rules"][0]["timing"]["duration"] = .25
    ctx = setup(data)
    queued = begin(ctx)
    old_id = ctx.state["buff"]["instances"][0]["instance_id"]
    next_tick(ctx, queued)
    new_id = ctx.state["buff"]["instances"][0]["instance_id"]
    assert old_id != new_id
    assert len(ctx.state["buff"]["instances"]) == 1
    calls = [row["event"] for row in ctx.calls if row["phase"] == "post"]
    assert calls[0]["id"] == calls[1]["id"]
    assert calls[0]["run_id"] != calls[1]["run_id"]


def test_unknown_does_not_poison_later_explicit_false_or_true():
    data = model()
    data["state_runtime"]["lifecycles"][0]["duration_us"] = 3_000_000
    data["rules"][0]["trigger"]["condition"] = "inside"
    ctx = setup(data, conditions={"inside"})
    queued = begin(ctx)
    ctx.conditions["inside"] = False
    queued = next_tick(ctx, queued)
    ctx.conditions["inside"] = True
    next_tick(ctx, queued)
    assert [row["outcome"] for row in ctx.bridge.report()["execution_trace"]] == [
        "pending", "not_activated", "activated"]


def test_completed_authority_cannot_replay_with_original_token():
    ctx = setup()
    begin(ctx)
    call = ctx.calls[-1]
    before = copy.deepcopy(ctx.bridge.report())
    with pytest.raises(StateRuntimeError):
        ctx.bridge.authorize_dispatch(call["event"], "post", call["parent"],
            allowed_rule_ids=call["rules"], allowed_operation_ids=call["operations"],
            authority=ctx.bridge._authority)
    assert ctx.bridge.report() == before


def test_replaced_generation_old_signed_tick_cannot_refresh_new_instance():
    ctx = setup()
    old_queued = begin(ctx)
    ctx.expire(100_000)
    event, parent = start_event(100_000, "0:recast")
    ctx.bridge.observe(event, "post", parent, successful=True)
    before = copy.deepcopy(ctx.state)
    old_tick = next(row for row in old_queued if row["data"]["kind"] == "state_tick")
    assert ctx.bridge.keep_pending(old_tick) is False
    ctx.expire(old_tick["time"])
    ctx.bridge.handle(old_tick)
    assert ctx.state == before
    assert len(ctx.bridge.report()["execution_trace"]) == 2
