"""Candidate clock2 positions must commit with existing clock state and budget."""
import copy

import pytest

from genshin_engine.state_runtime import StateRuntime, StateRuntimeError
from tests.test_state_runtime import config, event


def declarations():
    cfg = config(first=100_000, interval=100_000)
    cfg["schema_version"] = 2
    second = copy.deepcopy(cfg["lifecycles"][0])
    second.update(id="field_b", source_id="source:b", start_order=2, end_order=2,
        establishment_order=11, pending_cancel="never_after_success",
        start={"kind": "resource_threshold", "binding_id": "start-b"})
    cfg["lifecycles"].append(second)
    return cfg


def positioned(t=0, run="0:cast", *, phase="post", order=0):
    value = event(t, run, phase=phase, order=order)
    value["position"] = {"anchor": {"kind": "native", "queue_event_id": value["event_id"],
        "queue_run_id": run, "time_us": t, "system_priority": 0, "order": order, "causal": []},
        "phase": phase, "stage": "native_observe"}
    return value


def continuation(base, *, sequence=1, span=(1, 1), binding="start-b", binding_order=2, scheduled=False):
    value = copy.deepcopy(base)
    value["run_id"] = f"@threshold:dispatch:{sequence}"
    value["event_id"] = value["run_id"]
    value["position"].update(stage="batch_tail" if scheduled else "resource_tail",
        resource_span=list(span), binding_id=binding, binding_order=binding_order, continuation_sequence=sequence,
        source={"dispatch_run_id": value["run_id"], "operation_run_id": base["run_id"], "operation_id": "spend", "phase": base["phase"]})
    return value


def snapshot(clock):
    return copy.deepcopy((clock._states, clock._pending, clock._contexts, clock._context_phases,
        clock._starts, clock._start_orders, clock._trace, clock._reserved, clock._position_index.__dict__))


def test_clock2_native_resource_tail_can_return_to_original_observe():
    c = StateRuntime(declarations(), character_ids=["a"], emit_ordered_steps=True)
    parent = positioned(phase="pre")
    c.start("field_b", continuation(parent), eligible=True)
    parent["phase"] = parent["position"]["phase"] = "post"
    c.start("field", parent, eligible=True)
    assert c.active("field_b", 0) and c.active("field", 0)


def test_clock2_no_old_context_or_regression_after_observe():
    c = StateRuntime(declarations(), character_ids=["a"], emit_ordered_steps=True)
    with pytest.raises(StateRuntimeError): c.start("field", event(), eligible=True)
    c.start("field", positioned(), eligible=True)
    with pytest.raises(StateRuntimeError): c.start("field_b", continuation(positioned()), eligible=True)


def test_clock2_two_spans_same_parent_start_distinct_generations():
    c = StateRuntime(declarations(), character_ids=["a"], emit_ordered_steps=True)
    base = positioned(phase="pre")
    c.start("field_b", continuation(base), eligible=True)
    generation = c._states["field_b"]["generation_uid"]
    c.start("field_b", continuation(base, sequence=2, span=(2, 2)), eligible=True)
    assert c._states["field_b"]["generation_uid"] != generation
    with pytest.raises(StateRuntimeError):
        c.start("field_b", continuation(base, sequence=3, span=(2, 2)), eligible=True)


def test_clock2_joint_reserve_failure_does_not_commit_position_or_actual_identity():
    def reject(count):
        raise StateRuntimeError("state_event_limit", "candidate injected rejection", severity="pending")
    c = StateRuntime(declarations(), character_ids=["a"], emit_ordered_steps=True, reserve_events=reject)
    before = snapshot(c)
    source = continuation(positioned(phase="pre"))
    with pytest.raises(StateRuntimeError): c.start("field_b", source, eligible=True)
    assert snapshot(c) == before
    c._reserve_external = None
    c.start("field_b", source, eligible=True)
    assert c._position_index.steps == 1


def test_clock2_cannot_bypass_earlier_pending_timer_to_start_life():
    c = StateRuntime(declarations(), character_ids=["a"], emit_ordered_steps=True)
    c.start("field", positioned(), eligible=True)
    before = snapshot(c)
    with pytest.raises(StateRuntimeError, match="更早"):
        c.start("field_b", continuation(positioned(200_000, "0:later", order=2)), eligible=True)
    assert snapshot(c) == before


def test_clock2_scheduled_handle_then_tail_has_real_anchor_and_new_source_uid():
    c = StateRuntime(declarations(), character_ids=["a"], emit_ordered_steps=True)
    first = c.start("field", positioned(), eligible=True)
    c.consume_ordered_steps(first["ordered_steps"])
    queued = c.pending()[0]
    tick = c.handle(queued)
    c.consume_ordered_steps(tick["ordered_steps"])
    base = positioned(queued["time_us"], queued["run_id"], order=queued["order"])
    base["event_id"] = queued["id"]
    base["position"] = copy.deepcopy(c._position_index.cursor)
    source = continuation(base, scheduled=True)
    c.start("field_b", source, eligible=True)
    assert c._last_event_uid == queued["run_id"]
    assert c._states["field_b"]["parent"]["run_id"] == source["run_id"]
    # This pure test intentionally makes no claim about actual bridge delivery.


def test_clock2_wrong_typed_binding_cannot_reuse_endpoint_or_position():
    c = StateRuntime(declarations(), character_ids=["a"], emit_ordered_steps=True)
    before = snapshot(c)
    with pytest.raises(StateRuntimeError):
        c.start("field_b", continuation(positioned(phase="pre"), binding="other"), eligible=True)
    assert snapshot(c) == before
    c.start("field_b", continuation(positioned(phase="pre")), eligible=True)


def test_candidate_clock3_remains_closed():
    cfg = declarations()
    cfg["schema_version"] = 3
    with pytest.raises(StateRuntimeError):
        StateRuntime(cfg, character_ids=["a"], emit_ordered_steps=True)


def test_clock2_stale_expiry_cannot_advance_over_new_generation_tick():
    c = StateRuntime(declarations(), character_ids=["a"], emit_ordered_steps=True)
    c.start("field", positioned(), eligible=True)
    old_expiry = next(row for row in c.pending() if row["kind"] == "state_expire")
    c.start("field", positioned(50_000, "0:recast", order=5), eligible=True)
    before = snapshot(c)
    with pytest.raises(StateRuntimeError): c.handle(old_expiry)
    assert snapshot(c) == before
    c.handle(c.pending()[0])
    assert c._time == 150_000


def test_clock2_different_event_cannot_reopen_lower_same_time_order():
    c = StateRuntime(declarations(), character_ids=["a"], emit_ordered_steps=True)
    c.start("field", positioned(order=30), eligible=True)
    before = snapshot(c)
    with pytest.raises(StateRuntimeError):
        c.end("field", positioned(run="0:late", order=10), eligible=True)
    assert snapshot(c) == before
