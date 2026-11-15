"""Lifecycle controls are signed execution input, separate from display trace."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.state_runtime import StateRuntime, StateRuntimeError
from tests.test_state_runtime import config, event


def ordered_runtime(**kwargs):
    data = config(first=1_000_000, **kwargs)
    life = data["lifecycles"][0]
    life["hooks"]["create"] = [{"kind": "resource_operation", "ref_id": "create-spend", "order": 1}]
    life["hooks"]["end"] = [{"kind": "resource_operation", "ref_id": "end-spend", "order": 1}]
    return StateRuntime(data, character_ids=["a"], emit_ordered_steps=True)


def test_old_clock_does_not_emit_new_control_batches():
    runtime = StateRuntime(config(), character_ids=["a"])
    assert "ordered_steps" not in runtime.start("field", event(), eligible=True)


def test_replace_preserves_old_closing_end_then_new_create_order():
    runtime = ordered_runtime()
    initial = runtime.start("field", event(), eligible=True)
    first = runtime.consume_ordered_steps(initial["ordered_steps"])
    later = runtime.start("field", event(500_000, "0:again"), eligible=True)
    steps = runtime.consume_ordered_steps(later["ordered_steps"])
    assert [r["kind"] for r in steps] == ["lifecycle_closing", "dispatch", "lifecycle_end", "lifecycle_start", "dispatch", "lifecycle_established"]
    assert all(r["generation_uid"] == first[0]["generation_uid"] for r in steps[:3])
    assert all(r["generation_uid"] == steps[-1]["generation_uid"] for r in steps[3:])
    assert steps[0]["generation_uid"] != steps[-1]["generation_uid"]
    assert steps[1]["dispatch"]["payload"]["ref_id"] == "end-spend"
    assert steps[4]["dispatch"]["payload"]["ref_id"] == "create-spend"
    assert [r["sequence"] for r in first+steps] == list(range(1, len(first+steps)+1))
    assert {r["event_run_id"] for r in steps} == {"0:again"}


@pytest.mark.parametrize("mutation", ["copy", "reverse", "generation", "payload", "sequence"])
def test_control_batch_requires_original_unchanged_ordered_authority(mutation):
    runtime = ordered_runtime()
    batch = runtime.start("field", event(), eligible=True)["ordered_steps"]
    if mutation == "copy": batch = copy.deepcopy(batch)
    if mutation == "reverse": batch["steps"].reverse()
    if mutation == "generation": batch["steps"][0]["generation_uid"] = "wrong"
    if mutation == "payload": batch["steps"][1]["dispatch"]["payload"]["ref_id"] = "wrong"
    if mutation == "sequence": batch["steps"][0]["sequence"] += 100
    with pytest.raises(StateRuntimeError) as exc: runtime.consume_ordered_steps(batch)
    assert exc.value.issue["code"] == "state_control_authority"


def test_batch_can_only_be_claimed_once_and_view_is_detached():
    runtime = ordered_runtime()
    batch = runtime.start("field", event(), eligible=True)["ordered_steps"]
    claimed = runtime.consume_ordered_steps(batch)
    claimed[0]["generation_uid"] = "changed"
    assert batch["steps"][0]["generation_uid"] != "changed"
    with pytest.raises(StateRuntimeError): runtime.consume_ordered_steps(batch)


def test_signed_batches_cannot_be_claimed_out_of_order():
    runtime = ordered_runtime()
    first = runtime.start("field", event(), eligible=True)["ordered_steps"]
    second = runtime.start("field", event(500_000, "0:again"), eligible=True)["ordered_steps"]
    with pytest.raises(StateRuntimeError) as exc: runtime.consume_ordered_steps(second)
    assert exc.value.issue["code"] == "state_control_order"
    assert runtime.consume_ordered_steps(first)
    assert runtime.consume_ordered_steps(second)


def test_display_trace_never_becomes_counter_control_authority():
    runtime = ordered_runtime()
    out = runtime.start("field", event(), eligible=True)
    out["trace"].clear()
    out["dispatches"][0]["payload"]["ref_id"] = "display-copy-was-edited"
    steps = runtime.consume_ordered_steps(out["ordered_steps"])
    assert steps[1]["dispatch"]["payload"]["ref_id"] == "create-spend"


def test_ordered_control_opt_in_requires_explicit_boolean():
    with pytest.raises(StateRuntimeError): StateRuntime(config(), character_ids=["a"], emit_ordered_steps="yes")


def test_terminal_is_inside_closing_transaction_without_extending_active():
    runtime = ordered_runtime(end_tick="before_expiry")
    runtime.consume_ordered_steps(runtime.start("field", event(), eligible=True)["ordered_steps"])
    batches = []
    while runtime.pending():
        out = runtime.handle(runtime.pending()[0])
        batches.append(runtime.consume_ordered_steps(out["ordered_steps"]))
    final = batches[-1]
    assert [r["kind"] for r in final] == ["lifecycle_closing", "dispatch", "dispatch", "lifecycle_end"]
    assert final[1]["dispatch"]["frame"] == "terminal_preclose"
    assert runtime.active("field", 2_000_000) is False


def test_failed_atomic_reservation_does_not_claim_control_sequence():
    allowed = [False]
    def reserve(count):
        if not allowed[0]: raise StateRuntimeError("state_event_limit", "test budget", severity="pending")
    runtime = StateRuntime(config(), character_ids=["a"], emit_ordered_steps=True, reserve_events=reserve)
    with pytest.raises(StateRuntimeError): runtime.start("field", event(), eligible=True)
    allowed[0] = True
    steps = runtime.consume_ordered_steps(runtime.start("field", event(), eligible=True)["ordered_steps"])
    assert steps[0]["sequence"] == 1
