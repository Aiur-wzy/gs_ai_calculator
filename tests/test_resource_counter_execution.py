"""Actual mechanics counter execution, separate from pure planner acceptance."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
from genshin_engine.state_runtime_mechanics import route_event_id
from tests.test_mechanics_execution import action, condition
from tests.test_resource_counters import definitions
from tests.test_shared_pool_v2_execution import operation_rows, periodic_case


def case(*, reset="create", window="created_until_end", initial=19, times=(.35,)):
    args = periodic_case(times=times, initial=initial)
    args[2]["resource_counters"] = definitions(reset=reset, window=window)[0]
    return args


def count_rows(out):
    return [r for r in out["resource_counters"]["trace"] if r["type"] == "counter_accumulation"]


def test_real_timer_and_counter_share_atomic_debit_and_report():
    args = case()
    before = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [r["accepted"] for r in operation_rows(out)] == [8, 8, 3]
    assert [r["after"] for r in count_rows(out)] == [8, 16, 19]
    assert out["resource_counters"]["state"]["values"]["count"]["total"] == 19
    assert out["result"]["resource_counters"] == out["resource_counters"]
    assert args == before


def replace_case():
    args = case(initial=100, times=(.3,))
    start_op = args[2]["shared_pools"]["operations"][0]
    end_op = copy.deepcopy(start_op)
    end_op.update(id="end_spend", order=2)
    end_op["trigger"]["event_ids"] = [route_event_id("resource_operation", "end_spend")]
    args[2]["shared_pools"]["operations"].append(end_op)
    args[2]["resource_counters"]["counters"][0]["operation_ids"].append("end_spend")
    life = args[2]["state_runtime"]["lifecycles"][0]
    life["hooks"] = {"create": [{"kind": "resource_operation", "ref_id": "refill", "order": 1}],
                     "established": [], "end": [{"kind": "resource_operation", "ref_id": "end_spend", "order": 1}]}
    args[2]["state_runtime"]["timers"] = []
    args[1]["actions"].append(action("again", start=.15, end=.2, order=5, repeat={"mode": "once"}))
    life["start"]["action_ids"].append("again")
    return args


def test_replace_old_end_and_new_create_are_woven_before_new_generation():
    args = replace_case()
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [r["after"] for r in count_rows(out)[:3]] == [8, 16, 8]
    rows = count_rows(out)
    assert rows[0]["generation_uid"] == rows[1]["generation_uid"] != rows[2]["generation_uid"]


def test_duration_refresh_keeps_ticks_and_accumulation_until_actual_expiry():
    args = case(initial=100, times=(.35, .7), reset="end")
    life = args[2]["state_runtime"]["lifecycles"][0]
    life["restart"] = "refresh_duration"
    args[2]["state_runtime"]["timers"][0]["max_ticks"] = 5
    args[1]["actions"].extend([
        action("recast", start=.15, end=.2, order=5, repeat={"mode": "once"}),
        action("fresh", start=.6, end=.65, order=5, repeat={"mode": "once"}),
    ])
    life["start"]["action_ids"].extend(["recast", "fresh"])
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    rows = count_rows(out)
    assert [(row["time_us"], row["after"]) for row in rows] == [
        (100_000, 8), (200_000, 16), (300_000, 24), (400_000, 32), (500_000, 40),
        (700_000, 8), (800_000, 16), (900_000, 24),
    ]
    assert len({row["generation_uid"] for row in rows[:5]}) == 1
    assert rows[4]["generation_uid"] != rows[5]["generation_uid"]
    runtime_trace = out["state_runtime"]["issued_trace"]
    refreshed = next(row for row in runtime_trace if row["type"] == "lifecycle_duration_refresh")
    assert (refreshed["previous_end_us"], refreshed["end_us"]) == (400_000, 550_000)
    assert [row["time_us"] for row in runtime_trace if row["type"] == "lifecycle_end"] == [550_000, 1_000_000]


def test_unknown_filter_does_not_invent_counter_updates_or_leak_total():
    args = case()
    args[2]["shared_pools"]["operations"][0]["trigger"]["condition"] = "inside"
    condition(args[2], "inside", 0, 2)
    condition(args[2], "unknown", .15, 3)
    args[2]["condition_events"][-1].update(condition="inside", value=None)
    out = execute_mechanics(*args)
    assert out["status"] == "pending" and out["result"] is None
    assert [r["accepted"] for r in count_rows(out)] == [8]


def test_counter_requires_v2_pool_and_lifecycle_without_silent_omission():
    for missing in ("state_runtime", "shared_pools"):
        args = case()
        args[2].pop(missing)
        out = execute_mechanics(*args)
        assert out["result"] is None and out["status"] == "invalid"
        assert any(i["code"].startswith("counter_") for i in out["issues"])


def test_counter_public_budget_failure_keeps_actual_hit_and_debit_prefix(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    original = _Mechanics._reserve_events
    def reserve(ctx, count, kind=None):
        if kind == "resource_counters" and ctx.time == 200_000 and count == 2:
            raise SharedPoolError("counter_limit", "合成联合提交预算限制", severity="pending")
        return original(ctx, count, kind)
    monkeypatch.setattr(_Mechanics, "_reserve_events", reserve)
    out = execute_mechanics(*case(times=(.15, .35)))
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert [r["accepted"] for r in count_rows(out)] == [8]
    assert [r["after"] for r in operation_rows(out)] == [11]
    assert out["resource_counters"]["applied_state"]["last_ledger_sequence"] == 1
    assert any(r["id"] == "h0" for r in out["rotation_check"]["event_log"])
    assert any(r["type"] == "state_dispatch_failed" for r in out["state_runtime_trace"])


def test_old_end_commit_survives_new_create_failure_and_reports_applied_cursor(monkeypatch):
    original = SharedPoolLedger._prepare_counter
    def prepare(ledger, plan, **kwargs):
        rows = plan["counter_receipts"]
        # Initial create is first resource receipt; replacement old end is next.
        # Reject only replacement create candidate, after its legal reset.
        if kwargs.get("resource_count") and len(ledger._receipts) == 2:
            assert rows[0]["after"] == 8
            raise SharedPoolError("counter_limit", "合成第三次资源事务拒绝", severity="pending")
        return original(ledger, plan, **kwargs)
    monkeypatch.setattr(SharedPoolLedger, "_prepare_counter", prepare)
    out = execute_mechanics(*replace_case())
    assert out["status"] == "pending" and out["result"] is None
    assert [r["after"] for r in count_rows(out)] == [8, 16]
    assert [r["after"] for r in out["shared_pool_trace"]] == [92, 84]
    applied = out["resource_counters"]["applied_state"]
    assert applied["values"]["count"]["total"] == 0  # new create reset already completed
    assert applied["lifecycles"]["field"]["phase"] == "created"  # new establishment not applied
    assert applied["last_ledger_sequence"] == 2
    assert any(r["type"] == "state_control_completed" for r in out["state_runtime_trace"])


def test_counter_boundary_keeps_values_and_future_window_without_absolute_identity():
    args = case(times=(.15,))
    args[0]["loop_duration"] = args[1]["period"] = .2
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    first = out["rotation_check"]["cycles"][0]["boundary_state"]["mechanics"]["shared_pools"]["resource_counters"]
    assert first["values"]["count"]["total"] == 8
    assert first["lifecycles"]["field"]["end_remaining_us"] == 200_000
    assert "generation_uid" not in repr(first) and "sequence" not in repr(first) and "last_time_us" not in repr(first)


def test_no_counter_model_does_not_add_counter_reports_or_controls():
    out = execute_mechanics(*periodic_case())
    assert out["result"] is not None, out["issues"]
    assert "resource_counters" not in out and "resource_counters" not in out["result"]
    assert all(r["type"] != "state_control_completed" for r in out["state_runtime_trace"])


@pytest.mark.parametrize("terminal,expected", [(False, [8, 16]), (True, [8, 16, 19])])
def test_terminal_counter_credit_requires_declared_preclose_frame(terminal, expected):
    args = case()
    state = args[2]["state_runtime"]
    state["lifecycles"][0]["duration_us"] = 300_000
    if terminal: state["timers"][0].update(end_tick="before_expiry", terminal_order=1)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert [r["after"] for r in count_rows(out)] == expected


def test_never_reset_preserves_actual_total_across_axis_check_cycles():
    args = case(reset="never", initial=1000)
    args[1]["actions"][0]["repeat"] = {"mode": "each_cycle"}
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    rows = count_rows(out)
    assert [r["after"] for r in rows] == list(range(8, 8*(len(rows)+1), 8))
    assert len(rows) > 3 and len({r["generation_uid"] for r in rows}) > 1
    assert all(r["history_scope"] == "lifetime_total" for r in rows)
    boundaries = [c["boundary_state"]["mechanics"]["shared_pools"]["resource_counters"]["values"]["count"]["total"]
                  for c in out["rotation_check"]["cycles"]]
    assert boundaries == [24, 48, 72]


def test_copied_clock_batch_cannot_authorize_actual_counter_mutation(monkeypatch):
    from genshin_engine.state_runtime import StateRuntime
    finish = StateRuntime._finish
    def copied(clock, *args, **kwargs):
        out = finish(clock, *args, **kwargs)
        if "ordered_steps" in out: out["ordered_steps"] = copy.deepcopy(out["ordered_steps"])
        return out
    monkeypatch.setattr(StateRuntime, "_finish", copied)
    out = execute_mechanics(*case())
    assert out["status"] == "invalid" and out["result"] is None
    assert any(i["code"] == "state_control_authority" for i in out["issues"])
    assert out["resource_counters"]["applied_state"]["last_control_sequence"] == 0
    assert not out["resource_counters"]["trace"] and not out["shared_pool_trace"]


def test_failed_control_trace_identifies_exact_unapplied_step(monkeypatch):
    original = SharedPoolLedger.apply_counter_control
    def apply(ledger, step):
        if step["kind"] == "lifecycle_established":
            raise SharedPoolError("counter_limit", "合成建立控制拒绝", severity="pending")
        return original(ledger, step)
    monkeypatch.setattr(SharedPoolLedger, "apply_counter_control", apply)
    out = execute_mechanics(*case())
    assert out["status"] == "pending" and out["result"] is None
    row = next(r for r in out["state_runtime_trace"] if r["type"] == "state_control_failed")
    assert row["time_us"] == 0 and row["lifecycle_id"] == "field" and row["control_sequence"] == 2
    assert row["generation_uid"] and row["event_run_id"] == "0:cast"
    assert out["resource_counters"]["applied_state"]["last_control_sequence"] == 1
