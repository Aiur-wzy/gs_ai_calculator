"""Public v2 fixed-counter and ordinary-timer actual-queue regressions."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.shared_pools import SharedPoolError
from tests.test_mechanics_execution import add, rule
from tests.test_resource_counter_execution import case


def counter_threshold_case(*, window="created_until_end", times=(.15, .25, .35)):
    args = case(times=times, window=window)
    model = args[2]
    model["resource_threshold_observers"] = {"schema_version": 2, "observers": [{
        "id": "spent", "name_zh": "工程实际消费跨线", "owner": "a", "source_id": "source:observer",
        "input": {"kind": "counter_value_key", "counter_id": "count", "key": "total"},
        "comparison": "enter_gte", "threshold_atoms": 16,
        "causes": {"operation_ids": ["refill"], "consumer_ids": []},
        "status": "assumed", "source": "工程合成事实，非游戏客户端计时证据"}]}
    model["resource_threshold_triggers"] = {"schema_version": 2, "bindings": [{
        "id": "spent-window", "name_zh": "累计过线后的短时增伤", "owner": "a", "source_id": "source:effect",
        "observer_id": "spent", "order": 1, "payload": {"kind": "activate_rule", "ref_id": "boost"},
        "status": "assumed", "source": "工程合成事实，非游戏客户端计时证据"}]}
    add(model, rule("boost", domain="provider", duration=.25, source_id="source:effect",
        trigger={"kind": "resource_threshold", "binding_id": "spent-window"}))
    return args


def test_actual_timer_spend_and_counter_crossing_activate_without_manual_condition():
    args = counter_threshold_case()
    old = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 90, 90]
    assert out["resource_counters"]["state"]["values"]["count"]["total"] == 19
    assert not args[2]["condition_events"] and args == old
    observations = out["resource_threshold_observers"]["trace"]
    assert [(r["before"], r["after"], r["crossed"]) for r in observations] == [(0, 8, False), (8, 16, True), (16, 19, False)]
    dispatch = out["resource_threshold_triggers"]["trace"]
    assert len(dispatch) == 1 and dispatch[0]["time_us"] == 200_000
    assert dispatch[0]["source_run_id"] == observations[1]["run_id"]


def test_real_lifecycle_window_prevents_counter_crossing_despite_resource_spend():
    args = counter_threshold_case(window="established_until_end")
    args[2]["state_runtime"]["lifecycles"][0]["establishment_delay_us"] = 250_000
    args[2]["state_runtime"]["timers"][0]["anchor"] = "create"
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 45, 45]
    assert out["resource_counters"]["state"]["values"]["count"]["total"] == 3
    assert not out["resource_threshold_triggers"]["trace"]


def test_timer_target_failure_preserves_committed_spend_and_counter_prefix(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    original = _Mechanics._reserve_events
    def reserve(ctx, count, kind=None):
        if kind == "resource_threshold_dispatch":
            raise SharedPoolError("threshold_dispatch_limit", "工程阈值目标预算拒绝", severity="pending")
        return original(ctx, count, kind)
    monkeypatch.setattr(_Mechanics, "_reserve_events", reserve)
    out = execute_mechanics(*counter_threshold_case())
    assert out["status"] == "pending" and out["result"] is None
    assert out["resource_counters"]["applied_state"]["values"]["count"]["total"] == 16
    assert out["shared_pool_trace"][-1]["after"] == 3
    assert any(r["type"] == "state_dispatch_failed" for r in out["state_runtime_trace"])
    assert any(r["id"] == "h0" for r in out["rotation_check"]["event_log"])


@pytest.mark.parametrize("malformed", ["hook", "terminal", "old_trigger_contract"])
def test_ordinary_timer_permission_does_not_expand_to_unreviewed_origins(malformed):
    args = counter_threshold_case()
    if malformed == "hook":
        args[2]["state_runtime"]["lifecycles"][0]["hooks"]["create"] = [{"kind": "resource_operation", "ref_id": "refill", "order": 1}]
    elif malformed == "terminal":
        args[2]["state_runtime"]["timers"][0].update(end_tick="before_expiry", terminal_order=1)
    else:
        args[2]["resource_threshold_triggers"]["schema_version"] = 1
    out = execute_mechanics(*args)
    assert out["status"] == "invalid" and out["result"] is None
    assert any(i["code"] == "threshold_dispatch_origin" for i in out["issues"]), out["issues"]


@pytest.mark.parametrize("forgery", ["event_copy", "parent_copy", "phase", "timer", "operation"])
def test_timer_provenance_cannot_be_forged_before_claiming_real_batch(monkeypatch, forgery):
    from genshin_engine.state_runtime import StateRuntimeError
    from genshin_engine.state_runtime_mechanics import StateRuntimeMechanics
    original = StateRuntimeMechanics.authorize_threshold_operation
    checked = []
    def verify(bridge, event, phase, parent, operation_id, expected_origin):
        ledger = bridge.ctx.shared_pools.ledger
        before = copy.deepcopy((ledger.threshold_dispatch_report(), ledger.counter_snapshot(), bridge._authorization_phase))
        args = [event, phase, parent, operation_id, expected_origin]
        if forgery == "event_copy": args[0] = copy.deepcopy(event)
        elif forgery == "parent_copy": args[2] = copy.deepcopy(parent)
        elif forgery == "phase": args[1] = "pre" if phase == "post" else "post"
        elif forgery == "timer": args[4] = {**expected_origin, "timer_id": "forged"}
        else: args[3] = "other"
        with pytest.raises(StateRuntimeError): original(bridge, *args)
        assert (ledger.threshold_dispatch_report(), ledger.counter_snapshot(), bridge._authorization_phase) == before
        checked.append(event["run_id"])
        return original(bridge, event, phase, parent, operation_id, expected_origin)
    monkeypatch.setattr(StateRuntimeMechanics, "authorize_threshold_operation", verify)
    out = execute_mechanics(*counter_threshold_case())
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 90, 90]
    assert len(checked) == 1


def test_real_timer_original_objects_lose_authority_after_dispatch_returns(monkeypatch):
    from genshin_engine.state_runtime import StateRuntimeError
    from genshin_engine.state_runtime_mechanics import StateRuntimeMechanics
    original = StateRuntimeMechanics.authorize_threshold_operation
    captured = []
    def remember(bridge, *args):
        captured.append((bridge, args))
        return original(bridge, *args)
    monkeypatch.setattr(StateRuntimeMechanics, "authorize_threshold_operation", remember)
    out = execute_mechanics(*counter_threshold_case())
    assert out["result"] is not None, out["issues"]
    bridge, args = captured[0]
    with pytest.raises(StateRuntimeError): original(bridge, *args)


def test_one_real_timer_multikey_total_crosses_once_and_not_once_per_key():
    from tests.test_mechanics_execution import second_character
    args = counter_threshold_case()
    second_character(args[0], args[1])
    args[2]["shared_pools"]["pools"][0].update(scope="per_character", initial={"a": 19, "b": 19})
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert out["resource_counters"]["state"]["values"]["count"]["total"] == 38
    assert [(r["before"], r["after"]) for r in out["resource_threshold_observers"]["trace"]] == [(0, 16), (16, 32), (32, 38)]
    assert len(out["resource_threshold_triggers"]["trace"]) == 1
    assert [h["expected"] for h in out["result"]["hits"]] == [90, 90, 45]  # half-open .1+.25


def test_native_pre_spend_can_cross_real_counter_for_current_hit():
    args = counter_threshold_case()
    args[2]["state_runtime"]["timers"] = []
    args[2]["shared_pools"]["operations"][0]["trigger"] = {"event": "hit", "phase": "pre"}
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 90, 90]
    trace = out["resource_threshold_triggers"]["trace"]
    assert len(trace) == 1 and trace[0]["source_run_id"] == "0:h1"
    assert "source_origin" not in trace[0] and trace[0]["counter_observation"]["counter_id"] == "count"


def test_timer_balance_crossing_does_not_require_unrelated_counter_program():
    args = counter_threshold_case()
    args[2].pop("resource_counters")
    observer = args[2]["resource_threshold_observers"]["observers"][0]
    observer.update(input={"kind": "pool_balance_key", "pool_id": "pool", "key": "team"},
                    comparison="enter_lte", threshold_atoms=3)
    args[2]["resource_threshold_observers"]["schema_version"] = 1
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [h["expected"] for h in out["result"]["hits"]] == [45, 90, 90]
    assert "resource_counters" not in out
    assert out["resource_threshold_triggers"]["trace"][0]["source_origin"]["kind"] == "ordinary_timer"
