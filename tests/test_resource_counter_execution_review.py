"""Independent real queue accounting tests, with invented source facts only."""
from __future__ import annotations

import copy

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
from genshin_engine.state_runtime_mechanics import route_event_id
from tests.test_mechanics_execution import condition
from tests.test_resource_counter_execution import case, count_rows, replace_case


def two_lives(*, at=150_000, target_duration=400_000):
    args = case(initial=100, reset="never")
    model = args[2]
    own_life = model["state_runtime"]["lifecycles"][0]
    own_life["duration_us"] = target_duration
    other = copy.deepcopy(own_life)
    other.update(id="other", source_id="source:other", start_order=2,
                 establishment_order=11, end_order=2, duration_us=400_000)
    op = copy.deepcopy(model["shared_pools"]["operations"][0])
    op.update(id="other_spend", source_id="source:other", order=2)
    op["trigger"]["event_ids"] = [route_event_id("resource_operation", "other_spend")]
    timer = copy.deepcopy(model["state_runtime"]["timers"][0])
    timer.update(id="other_tick", lifecycle_id="other", source_id="source:other",
                 first_offset_us=at, max_ticks=1, order=30,
                 payload={"kind": "resource_operation", "ref_id": "other_spend"})
    model["shared_pools"]["operations"].append(op)
    model["state_runtime"]["lifecycles"].append(other)
    model["state_runtime"]["timers"].append(timer)
    model["resource_counters"]["counters"][0]["operation_ids"].append("other_spend")
    return args


def test_other_source_spend_counts_in_target_lifecycle_without_borrowing_generation():
    args = two_lives()
    native = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    receipts = {r["id"]: r for r in out["shared_pool_trace"]}
    rows = count_rows(out)
    assert [r["after"] for r in rows] == [8, 16, 24, 32]
    other = next(r for r in rows if receipts[r["resource_receipt_id"]]["operation_id"] == "other_spend")
    assert other["accepted"] == 8
    assert other["generation_uid"] == rows[0]["generation_uid"]
    assert other["lifecycle_id"] == "field" and args == native


def test_exact_expiry_other_source_still_spends_without_target_counter_credit():
    out = execute_mechanics(*two_lives(at=200_000, target_duration=200_000))
    assert out["result"] is not None, out["issues"]
    assert [r["accepted"] for r in out["shared_pool_trace"]] == [8, 8]
    assert [(r["resource_accepted"], r["accepted"]) for r in count_rows(out)] == [(8, 8), (8, 0)]
    assert count_rows(out)[-1]["reason"] == "outside_window"
    assert out["resource_counters"]["state"]["values"]["count"]["total"] == 8


def test_known_false_filter_advances_only_control_without_resource_or_counter_credit():
    args = case()
    args[2]["shared_pools"]["operations"][0]["trigger"]["condition"] = "inside"
    condition(args[2], "inside", 0, 2)
    args[2]["condition_events"][-1]["value"] = False
    out = execute_mechanics(*args)
    assert out["result"] is not None, out["issues"]
    assert out["shared_pool_trace"] == [] and count_rows(out) == []
    state = out["resource_counters"]["applied_state"]
    assert state["last_ledger_sequence"] == 0 and state["last_control_sequence"] > 0
    assert state["values"]["count"]["total"] == 0


def test_new_establishment_control_failure_preserves_all_committed_hook_prefix(monkeypatch):
    apply = SharedPoolLedger.apply_counter_control

    def fail(ledger, step):
        if step["kind"] == "lifecycle_established" and len(ledger.receipts()) == 3:
            raise SharedPoolError("counter_limit", "Independent establishment budget rejection", severity="pending")
        return apply(ledger, step)

    monkeypatch.setattr(SharedPoolLedger, "apply_counter_control", fail)
    out = execute_mechanics(*replace_case())
    assert out["status"] == "pending" and out["result"] is None and out["resolved_template"] is None
    assert [r["after"] for r in count_rows(out)] == [8, 16, 8]
    state = out["resource_counters"]["applied_state"]
    assert state["last_ledger_sequence"] == 3 and state["lifecycles"]["field"]["phase"] == "created"
    assert state["values"]["count"]["total"] == 8
    assert [r["after"] for r in out["shared_pool_trace"]] == [92, 84, 76]
    failure = next(r for r in out["state_runtime_trace"] if r["type"] == "state_control_failed")
    assert failure["lifecycle_id"] == "field"
    assert failure["control_sequence"] == state["last_control_sequence"] + 1
    assert failure["generation_uid"] == state["lifecycles"]["field"]["generation_uid"]
    assert failure["time_us"] == state["last_time_us"]
    assert failure["event_run_id"] and failure["message"] == "Independent establishment budget rejection"


def test_relative_counter_boundary_is_stable_across_actual_source_occurrence_identity():
    args = case(times=(.15,))
    args[0]["loop_duration"] = args[1]["period"] = .2
    original = execute_mechanics(*args)
    renamed = copy.deepcopy(args)
    renamed[1]["actions"][0]["id"] = "renamed"
    renamed[2]["state_runtime"]["lifecycles"][0]["start"]["action_ids"] = ["renamed"]
    for row in renamed[2]["hit_events"]:
        row["action_id"] = "renamed"
    changed = execute_mechanics(*renamed)
    assert original["result"] is not None and changed["result"] is not None, changed["issues"]
    left = original["rotation_check"]["cycles"][0]["boundary_state"]["mechanics"]["shared_pools"]["resource_counters"]
    right = changed["rotation_check"]["cycles"][0]["boundary_state"]["mechanics"]["shared_pools"]["resource_counters"]
    assert left == right
    assert original["resource_counters"]["applied_state"]["lifecycles"]["field"]["generation_uid"] != changed["resource_counters"]["applied_state"]["lifecycles"]["field"]["generation_uid"]
