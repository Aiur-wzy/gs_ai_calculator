"""Finite state clock contract; invented facts, no engine/source qualification."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.state_runtime import (
    StateRuntime,
    StateRuntimeError,
    validate_state_runtime,
)


def config(*, delay=0, duration=2_000_000, first=0, interval=1_000_000, end_tick="exclude"):
    evidence = {"status": "assumed", "source": "Invented timer for engineering tests"}
    life = {"id": "field", "name_zh": "工程领域", "owner": "a", "source_id": "source:field",
            "start": {"event": "action_start", "phase": "post"}, "start_order": 1,
            "establishment_order": 10, "end_order": 1,
            "establishment_delay_us": delay, "duration_us": duration,
            "restart": "replace", "pending_cancel": "source_action_cancel_before_establishment",
            "hooks": {"create": [], "established": [], "end": []}, **evidence}
    timer = {"id": "refresh", "source_id": "source:field", "lifecycle_id": "field",
             "anchor": "established", "first_offset_us": first, "interval_us": interval,
             "max_ticks": 10, "order": 20, "end_tick": end_tick,
             "payload": {"kind": "dispatch_rule", "ref_id": "buff"}, **evidence}
    if end_tick == "before_expiry": timer["terminal_order"] = 1
    return {"schema_version": 1, "lifecycles": [life], "timers": [timer]}


def event(t=0, run="0:cast", *, phase="post", order=0):
    return {"event_id": "cast", "run_id": run, "t_us": t, "order": order, "phase": phase, "causal": []}


def make(**kwargs):
    return StateRuntime(config(**kwargs), character_ids=["a"])


def drain(runtime):
    batches = []
    while runtime.pending():
        batches.append(runtime.handle(runtime.pending()[0]))
    return batches


def test_zero_offset_is_a_post_parent_child_and_normal_ticks_are_finite():
    runtime = make()
    out = runtime.start("field", event(), eligible=True)
    first = out["dispatches"][0]
    assert first["event"] == "timer_tick" and first["ordinal"] == 1
    assert first["causal_parent"]["run_id"] == "0:cast" and first["causal_parent"]["phase"] == "post"
    assert first["frame"] == "after_parent" and first["time_us"] == 0
    later = drain(runtime)
    ticks = [d for batch in later for d in batch["dispatches"] if d["event"] == "timer_tick"]
    assert [d["time_us"] for d in ticks] == [1_000_000]
    assert runtime.active("field", 2_000_000) is False


def test_terminal_tick_is_special_once_and_never_relaxes_half_open_active():
    runtime = make(end_tick="before_expiry")
    runtime.start("field", event(), eligible=True)
    normal = runtime.pending()[0]
    runtime.handle(normal)
    expiry = runtime.pending()[0]
    assert expiry["time_us"] == 2_000_000 and expiry["system_priority"] < 0
    assert runtime.active("field", 2_000_000) is False
    out = runtime.handle(expiry)
    ticks = [d for d in out["dispatches"] if d["event"] == "timer_tick"]
    assert len(ticks) == 1 and ticks[0]["frame"] == "terminal_preclose"
    assert ticks[0]["terminal_permit"]["generation_uid"] == expiry["generation_uid"]
    with pytest.raises(StateRuntimeError): runtime.handle(expiry)


def test_delay_cancel_retains_emitted_create_spend_without_early_establishment():
    cfg = config(delay=500_000)
    cfg["lifecycles"][0]["hooks"]["create"] = [{"kind": "resource_operation", "ref_id": "spend3", "order": 1}]
    cfg["lifecycles"][0]["hooks"]["established"] = [{"kind": "resource_operation", "ref_id": "grant8", "order": 1}]
    runtime = StateRuntime(cfg, character_ids=["a"])
    start = runtime.start("field", event(), eligible=True)
    assert [d["payload"]["ref_id"] for d in start["dispatches"]] == ["spend3"]
    old = runtime.pending()[0]
    out = runtime.cancel_source("0:cast", event(100_000, "0:switch"))
    assert out["dispatches"] == [] and runtime.pending() == []
    assert any(r["type"] == "lifecycle_end" and r["reason"] == "cancelled_before_establishment" for r in runtime.trace)
    assert not any(r.get("payload", {}).get("ref_id") == "grant8" for r in runtime.trace)
    assert any(r.get("payload", {}).get("ref_id") == "spend3" for r in runtime.trace)
    cancelled = runtime.handle(old)
    assert cancelled["dispatches"] == [] and cancelled["trace"][0]["type"] == "stale_event"


def test_successful_detached_lifecycle_survives_switch():
    runtime = make()
    runtime.start("field", event(), eligible=True)
    assert runtime.cancel_source("0:cast", event(100_000, "0:switch"))["dispatches"] == []
    assert runtime.active("field", 100_000)


def test_replace_invalidates_old_generation_but_not_already_returned_data():
    runtime = make(first=500_000)
    start = runtime.start("field", event(), eligible=True)
    original = copy.deepcopy(start)
    old = runtime.pending()[0]
    runtime.start("field", event(100_000, "0:recast"), eligible=True)
    assert start == original
    assert runtime.handle(old)["dispatches"] == []
    assert [d["time_us"] for d in runtime.handle(runtime.pending()[0])["dispatches"]] == [600_000]


def test_keep_does_not_repeat_initialization():
    cfg = config()
    cfg["lifecycles"][0]["restart"] = "keep"
    cfg["lifecycles"][0]["hooks"]["create"] = [{"kind": "resource_operation", "ref_id": "set10", "order": 1}]
    runtime = StateRuntime(cfg, character_ids=["a"])
    runtime.start("field", event(), eligible=True)
    out = runtime.start("field", event(100_000, "0:recast"), eligible=True)
    assert out["dispatches"] == [] and out["trace"][0]["type"] == "lifecycle_keep"
    assert sum(r.get("payload", {}).get("ref_id") == "set10" for r in runtime.trace) == 1


def test_failed_qualification_does_not_initialize():
    runtime = make()
    out = runtime.start("field", event(), eligible=False)
    assert out["scheduled"] == out["dispatches"] == []
    assert runtime.pending() == [] and not runtime.active("field", 0)


def test_exact_expiry_requires_closing_old_generation_before_recast():
    runtime = make(duration=1_000_000, first=500_000)
    runtime.start("field", event(), eligible=True)
    runtime.handle(runtime.pending()[0])
    with pytest.raises(StateRuntimeError, match="先执行"):
        runtime.start("field", event(1_000_000, "0:new"), eligible=True)
    runtime.handle(runtime.pending()[0])
    runtime.start("field", event(1_000_000, "0:new"), eligible=True)
    assert runtime.active("field", 1_000_000)


def test_two_real_starts_same_axis_and_same_named_starts_next_axis_are_distinct():
    runtime = make(first=500_000)
    one = runtime.start("field", event(), eligible=True)["scheduled"][0]["generation_uid"]
    two = runtime.start("field", event(100_000, "0:cast2"), eligible=True)["scheduled"][0]["generation_uid"]
    three = runtime.start("field", event(200_000, "1:cast"), eligible=True)["scheduled"][0]["generation_uid"]
    assert len({one, two, three}) == 3
    with pytest.raises(StateRuntimeError): runtime.start("field", event(200_000, "1:cast"), eligible=True)


@pytest.mark.parametrize("mutation", ["extend", "zero_interval", "unknown", "terminal_missing", "false_time", "future_version", "unknown_life", "duplicate_timer", "duplicate_source", "order_conflict"])
def test_closed_schema_and_ambiguous_definition_rejected(mutation):
    cfg = config(end_tick="before_expiry")
    if mutation == "extend": cfg["lifecycles"][0]["restart"] = "extend"
    elif mutation == "zero_interval": cfg["timers"][0]["interval_us"] = 0
    elif mutation == "unknown": cfg["timers"][0]["silently_guess"] = True
    elif mutation == "terminal_missing": cfg["timers"][0].pop("terminal_order")
    elif mutation == "false_time": cfg["lifecycles"][0]["duration_us"] = True
    elif mutation == "future_version": cfg["schema_version"] = 3
    elif mutation == "unknown_life": cfg["timers"][0]["lifecycle_id"] = "missing"
    elif mutation == "duplicate_timer": cfg["timers"].append(copy.deepcopy(cfg["timers"][0]))
    elif mutation == "duplicate_source":
        cfg["lifecycles"].append({**copy.deepcopy(cfg["lifecycles"][0]), "id": "second", "start_order": 2, "establishment_order": 11, "end_order": 2})
    else: cfg["timers"].append({**copy.deepcopy(cfg["timers"][0]), "id": "second", "terminal_order": 2})
    assert validate_state_runtime(cfg, character_ids=["a"])
    with pytest.raises(StateRuntimeError): StateRuntime(cfg, character_ids=["a"])


def test_forged_queued_event_never_dispatches():
    runtime = make(first=500_000)
    runtime.start("field", event(), eligible=True)
    forged = copy.deepcopy(runtime.pending()[0]); forged["time_us"] = 1
    with pytest.raises(StateRuntimeError): runtime.handle(forged)
    assert runtime.pending()[0]["time_us"] == 500_000


def test_external_same_time_order_conflict_rejected_before_start_mutation():
    runtime = StateRuntime(config(first=500_000), character_ids=["a"],
                           external_events=[{"event_id": "switch", "t_us": 500_000, "order": 20}])
    with pytest.raises(StateRuntimeError): runtime.start("field", event(), eligible=True)
    assert runtime.pending() == [] and runtime.trace == []


def test_boundary_uses_relative_times_and_preserves_old_capture_value_and_links():
    first, second = make(first=500_000), make(first=500_000)
    a = first.start("field", event(), eligible=True)["scheduled"][0]["generation_uid"]
    b = second.start("field", event(10_000_000, "7:cast"), eligible=True)["scheduled"][0]["generation_uid"]
    captured_a = [{"slot": "burst", "lifecycle_id": "field", "generation_uid": a, "payload": {"counter_atoms": 30, "unit_scale": 10}}]
    captured_b = [{**captured_a[0], "generation_uid": b}]
    assert first.boundary(100_000, retained_captures=captured_a) == second.boundary(10_100_000, retained_captures=captured_b)
    captured_b[0]["payload"] = {"counter_atoms": 80, "unit_scale": 10}
    assert first.boundary(100_000, retained_captures=captured_a) != second.boundary(10_100_000, retained_captures=captured_b)
    first.start("field", event(200_000, "0:new"), eligible=True)
    old_boundary = first.boundary(200_000, retained_captures=captured_a)
    assert old_boundary["retained_captures"][0]["generation_relation"] == "old:0"
    assert a not in str(old_boundary) and "counter_atoms" in str(old_boundary)


def test_external_budget_failure_keeps_completed_prefix_and_does_not_refund_cancel():
    spent = []
    def reserve(count):
        if sum(spent) + count > 8:
            raise StateRuntimeError("state_event_limit", "预算耗尽", severity="pending")
        spent.append(count)
    runtime = StateRuntime(config(first=500_000), character_ids=["a"], reserve_events=reserve)
    runtime.start("field", event(), eligible=True)
    before = runtime.trace
    with pytest.raises(StateRuntimeError):
        for i in range(1, 8): runtime.start("field", event(i*10_000, f"0:cast{i}"), eligible=True)
    assert runtime.trace[:len(before)] == before and sum(spent) > 0
    assert any(r["type"] == "lifecycle_start" for r in runtime.trace)


def test_input_outputs_and_pending_payloads_are_deeply_isolated():
    cfg = config(first=500_000); original = copy.deepcopy(cfg)
    runtime = StateRuntime(cfg, character_ids=["a"])
    out = runtime.start("field", event(), eligible=True)
    cfg["timers"][0]["payload"]["ref_id"] = "attacker"
    out["scheduled"][0]["time_us"] = 0
    pending = runtime.pending(); pending[0]["time_us"] = 0
    dispatched = runtime.handle(runtime.pending()[0])
    assert dispatched["dispatches"][0]["payload"]["ref_id"] == original["timers"][0]["payload"]["ref_id"]
    assert dispatched["dispatches"][0]["time_us"] == 500_000


def test_capabilities_do_not_claim_filter_source_or_engine_integration():
    from genshin_engine.state_runtime_schema import state_runtime_capabilities
    caps = state_runtime_capabilities()
    assert caps["upper_layer_qualification"] == "not_evaluated"
    assert caps["integration"] == "pure_service_only"


def two_lives():
    cfg = config(first=500_000)
    cfg["lifecycles"].append({**copy.deepcopy(cfg["lifecycles"][0]), "id": "second", "source_id": "source:second",
                              "start_order": 2, "establishment_order": 11, "end_order": 2})
    return cfg


def test_runtime_start_order_checks_same_actual_parent_not_dictionary_order():
    runtime = StateRuntime(two_lives(), character_ids=["a"])
    runtime.start("second", event(), eligible=True)
    with pytest.raises(StateRuntimeError): runtime.start("field", event(), eligible=True)


def test_same_run_cannot_change_actual_source_identity_for_another_lifecycle():
    runtime = StateRuntime(two_lives(), character_ids=["a"])
    runtime.start("field", event(), eligible=True)
    with pytest.raises(StateRuntimeError): runtime.start("second", event(100_000), eligible=True)


def test_capture_cannot_claim_unknown_or_other_lifecycle_generation():
    runtime = StateRuntime(two_lives(), character_ids=["a"])
    uid = runtime.start("field", event(), eligible=True)["scheduled"][0]["generation_uid"]
    for lid, generation in (("field", "invented"), ("second", uid)):
        with pytest.raises(StateRuntimeError):
            runtime.boundary(0, retained_captures=[{"slot": "snapshot", "lifecycle_id": lid,
                                                 "generation_uid": generation, "payload": {"counter": 30}}])


def test_declared_start_phase_cannot_be_called_as_parent_pre():
    with pytest.raises(StateRuntimeError): make().start("field", event(phase="pre"), eligible=True)


def test_two_natural_ends_are_globally_ordered_and_do_not_depend_on_ids():
    cfg = two_lives()
    cfg["timers"] = []
    for life in cfg["lifecycles"]:
        life["hooks"]["end"] = [{"kind": "resource_operation", "ref_id": life["id"], "order": 1}]
    runtime = StateRuntime(cfg, character_ids=["a"])
    runtime.start("field", event(), eligible=True)
    runtime.start("second", event(), eligible=True)
    assert [p["lifecycle_id"] for p in runtime.pending()] == ["field", "second"]
    with pytest.raises(StateRuntimeError): runtime.handle(runtime.pending()[1])
    out = drain(runtime)
    assert [d["payload"]["ref_id"] for batch in out for d in batch["dispatches"]] == ["field", "second"]


def test_pending_without_cancel_permission_still_establishes():
    cfg = config(delay=500_000)
    cfg["lifecycles"][0]["pending_cancel"] = "never_after_success"
    runtime = StateRuntime(cfg, character_ids=["a"])
    runtime.start("field", event(), eligible=True)
    runtime.cancel_source("0:cast", event(100_000, "0:switch"))
    runtime.handle(runtime.pending()[0])
    assert runtime.active("field", 500_000)


def test_explicit_end_runs_cleanup_once_and_keeps_existing_dispatch_prefix():
    cfg = config()
    cfg["lifecycles"][0]["hooks"]["end"] = [{"kind": "resource_operation", "ref_id": "clear", "order": 1}]
    runtime = StateRuntime(cfg, character_ids=["a"])
    first = runtime.start("field", event(), eligible=True)
    out = runtime.end("field", event(100_000, "0:leave"), eligible=True)
    assert out["dispatches"][0]["payload"]["ref_id"] == "clear"
    assert out["trace"][-1]["reason"] == "explicit_end"
    assert runtime.pending() == [] and first["dispatches"][0]["payload"]["ref_id"] == "buff"
    assert runtime.end("field", event(100_000, "0:leave"), eligible=True)["dispatches"] == []


def test_generation_identity_is_a_tuple_not_ambiguous_string_concatenation():
    cfg = two_lives()
    cfg["timers"] = []
    cfg["lifecycles"][0]["id"] = "x"
    cfg["lifecycles"][1]["id"] = "x@y"
    runtime = StateRuntime(cfg, character_ids=["a"])
    a = runtime.start("x", event(run="y@z"), eligible=True)["scheduled"][0]["generation_uid"]
    b = runtime.start("x@y", event(run="z", order=1), eligible=True)["scheduled"][0]["generation_uid"]
    assert a != b


@pytest.mark.parametrize("argument", ["source_parts", "operation_ids", "rule_ids", "external_events"])
def test_reference_collections_are_not_accepted_as_strings(argument):
    with pytest.raises(StateRuntimeError): StateRuntime(config(), character_ids=["a"], **{argument: "source:field"})


def test_completed_timer_prevents_same_time_earlier_external_event():
    runtime = make(first=500_000)
    runtime.start("field", event(), eligible=True)
    runtime.handle(runtime.pending()[0])
    before = runtime.trace
    with pytest.raises(StateRuntimeError):
        runtime.end("field", event(500_000, "0:early", order=10), eligible=True)
    assert runtime.trace == before
    runtime.end("field", event(500_000, "0:late", order=30), eligible=True)


def test_completed_external_event_prevents_same_time_earlier_cancelled_timer():
    runtime = make(first=500_000)
    old = runtime.start("field", event(), eligible=True)["scheduled"][0]
    runtime.end("field", event(100_000, "0:end"), eligible=True)
    runtime.cancel_source("0:cast", event(500_000, "0:later", order=30))
    before = runtime.trace
    with pytest.raises(StateRuntimeError): runtime.handle(old)
    assert runtime.trace == before


def test_same_actual_event_allows_phase_progress_and_multiple_lifecycles():
    runtime = StateRuntime(two_lives(), character_ids=["a"])
    runtime.cancel_source("not-started", event(phase="pre"))
    runtime.start("field", event(), eligible=True)
    runtime.start("second", event(), eligible=True)
    assert runtime.active("field", 0) and runtime.active("second", 0)


def test_different_actual_events_require_distinct_queue_positions():
    runtime = StateRuntime(two_lives(), character_ids=["a"])
    runtime.start("field", event(), eligible=True)
    with pytest.raises(StateRuntimeError): runtime.start("second", event(run="another"), eligible=True)
    runtime.start("second", event(run="another", order=1), eligible=True)


@pytest.mark.parametrize("operation", ["start", "end", "active", "handle", "captures"])
def test_unhashable_public_references_are_structured_errors(operation):
    runtime = make()
    with pytest.raises(StateRuntimeError):
        if operation == "start": runtime.start([], event(), eligible=True)
        elif operation == "end": runtime.end([], event(), eligible=True)
        elif operation == "active": runtime.active([], 0)
        elif operation == "handle": runtime.handle({"run_id": []})
        else: runtime.boundary(0, retained_captures={})
