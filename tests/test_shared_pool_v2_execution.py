"""Scaled spending on the real event queue; not proof of a game-native clock."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime import StateRuntimeError
from genshin_engine.state_runtime_mechanics import route_event_id
from tests.test_mechanics_execution import action, condition, fixture
from tests.test_shared_pool_execution import setup as gated_setup
from tests.test_shared_pool_v2 import scaled
from tests.test_state_runtime import config
from tests.test_state_runtime_execution import field_case


def periodic_case(times=(.35,), initial=19):
    template, rotation, model = fixture(times)
    template["loop_duration"] = rotation["period"] = 1
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    pools = scaled(initial=initial)
    pools["consumers"] = []
    pools["operations"][0].update(source_id="source:field", trigger={"event": "condition", "phase": "post",
        "event_ids": [route_event_id("resource_operation", "refill")]})
    state = config(duration=400_000, first=100_000, interval=100_000)
    state["lifecycles"][0]["start"]["action_ids"] = ["cast"]
    state["timers"][0].update(max_ticks=3, payload={"kind": "resource_operation", "ref_id": "refill"})
    model.update(shared_pools=pools, state_runtime=state)
    return template, rotation, model


def operation_rows(out):
    return [r for r in out.get("shared_pool_trace", []) if r["type"] == "operation" and r["cycle"] == 0]


def test_real_timer_spends_at_each_tick_with_atom_report_and_no_input_mutation():
    args = periodic_case()
    before = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    rows = operation_rows(out)
    assert [(r["t_us"], r["accepted"], r["after"]) for r in rows] == [(100_000, 8, 11), (200_000, 8, 3), (300_000, 3, 0)]
    assert all(r["unit_scale"] == 10 and r["quantity_unit"] == "atoms" for r in rows)
    assert out["result"]["shared_pools"]["schema_version"] == 2
    assert out["result"]["shared_pools"]["state"]["unit_scales"] == {"pool": 10}
    assert args == before


def test_scaled_post_grant_cannot_retroactively_qualify_parent_consumer():
    template, rotation, model = gated_setup(times=(.2, .3), initial=3)
    pool = model["shared_pools"]
    pool["schema_version"] = 2
    pool["pools"][0]["unit_scale"] = 10
    pool["consumers"][0]["amount"] = 5
    pool["operations"][0]["amount"] = 8
    out = execute_mechanics(template, rotation, model)
    assert out["result"] is not None, out["issues"]
    assert [r["expected"] for r in out["result"]["hits"]] == [45, 90]
    rows = [r for r in out["shared_pool_trace"] if r["type"] == "consume" and r["cycle"] == 0]
    assert [(r["accepted"], r["after"]) for r in rows] == [(0, 3), (5, 6)]


def test_replacement_cancels_old_ticks_before_they_can_spend_new_balance():
    template, rotation, model = periodic_case(times=(.6,), initial=100)
    rotation["actions"].append(action("again", start=.15, end=.2, order=5, repeat={"mode": "once"}))
    model["state_runtime"]["lifecycles"][0]["start"]["action_ids"].append("again")
    out = execute_mechanics(template, rotation, model)
    assert out["result"] is not None, out["issues"]
    assert [r["t_us"] for r in operation_rows(out)] == [100_000, 250_000, 350_000, 450_000]
    assert operation_rows(out)[-1]["after"] == 68


@pytest.mark.parametrize("terminal, expected", [(False, [8, 8]), (True, [8, 8, 3])])
def test_terminal_spend_needs_explicit_preclose_permission(terminal, expected):
    args = periodic_case()
    state = args[2]["state_runtime"]
    state["lifecycles"][0]["duration_us"] = 300_000
    if terminal: state["timers"][0].update(end_tick="before_expiry", terminal_order=1)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [r["accepted"] for r in operation_rows(out)] == expected


def test_unknown_periodic_filter_preserves_earlier_debit_without_total():
    template, rotation, model = periodic_case()
    model["shared_pools"]["operations"][0]["trigger"]["condition"] = "inside"
    condition(model, "inside", 0, 2)
    condition(model, "inside-unknown", .15, 3)
    model["condition_events"][-1].update(condition="inside", value=None)
    out = execute_mechanics(template, rotation, model)
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert [r["accepted"] for r in operation_rows(out)] == [8]
    assert [r["outcome"] for r in out["state_runtime_trace"]] == ["resource_operation", "pending", "pending"]


def test_controlled_limit_retains_actual_debit_and_hit_prefix(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    original = _Mechanics._reserve_events
    def reserve(ctx, count, kind=None):
        if kind == "state_runtime" and ctx.time >= 200_000:
            raise StateRuntimeError("state_event_limit", "工程预算限制", severity="pending")
        return original(ctx, count, kind)
    monkeypatch.setattr(_Mechanics, "_reserve_events", reserve)
    out = execute_mechanics(*periodic_case(times=(.15, .35)))
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert [r["accepted"] for r in operation_rows(out)] == [8]
    assert any(r["id"] == "h0" for r in out["rotation_check"]["event_log"])


def test_check_boundary_retains_atom_units_and_future_ticks():
    template, rotation, model = periodic_case(times=(.15,))
    template["loop_duration"] = rotation["period"] = .2
    out = execute_mechanics(template, rotation, model)
    assert out["result"] is not None, out["issues"]
    boundary = out["rotation_check"]["cycles"][0]["boundary_state"]["mechanics"]
    assert boundary["shared_pools"]["balances"] == {"pool": {"team": 11}}
    assert boundary["shared_pools"]["unit_scales"] == {"pool": 10}
    assert boundary["state_runtime"]["lifecycles"]["field"]["timers"]["refresh"]["next_in_us"] == 0
    assert "@state_dispatch" not in str(boundary) and "cooldown_until_us" not in str(boundary)


@pytest.mark.parametrize("grant_order, second_balance", [(5, 19), (30, 3)])
def test_same_time_refill_and_tick_follow_declared_order(grant_order, second_balance):
    args = periodic_case()
    model = args[2]
    grant = copy.deepcopy(model["shared_pools"]["operations"][0])
    grant.update(id="grant", operation="grant", amount=16, order=1,
                 trigger={"event": "condition", "phase": "post", "event_ids": ["gain"]})
    grant.pop("spend_policy")
    model["shared_pools"]["operations"].append(grant)
    condition(model, "gain", .2, grant_order)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    spend_rows = [r for r in operation_rows(out) if r["operation"] == "spend"]
    assert spend_rows[1]["after"] == second_balance
    assert spend_rows[-1]["after"] == 11


def test_cancel_before_establishment_retains_completed_create_spend():
    template, rotation, model = field_case()
    rotation["actions"][0].update(occupation_end=1.5, switch_allowed_at=.5)
    life = model["state_runtime"]["lifecycles"][0]
    life["establishment_delay_us"] = 1_200_000
    life["hooks"]["create"] = [{"kind": "resource_operation", "ref_id": "refill", "order": 1}]
    pools = scaled(initial=3)
    pools["consumers"] = []
    pools["operations"][0].update(source_id="source:field", trigger={"event": "condition", "phase": "post",
        "event_ids": [route_event_id("resource_operation", "refill")]})
    model["shared_pools"] = pools
    out = execute_mechanics(template, rotation, model)
    assert out["result"] is not None, out["issues"]
    assert [(r["accepted"], r["after"]) for r in operation_rows(out)] == [(3, 0)]
    assert len(out["state_runtime_trace"]) == 1
    assert [r["expected"] for r in out["result"]["hits"]] == [45]*5


def test_pool_receipt_limit_keeps_only_successfully_applied_debits(monkeypatch):
    from genshin_engine import shared_pools
    monkeypatch.setitem(shared_pools.LIMITS, "receipts", 1)
    out = execute_mechanics(*periodic_case(times=(.15, .35)))
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert [(r["accepted"], r["after"]) for r in operation_rows(out)] == [(8, 11)]
    assert out["state_runtime_trace"][-1]["type"] == "state_dispatch_failed"
    assert any(r["id"] == "h0" for r in out["rotation_check"]["event_log"])
