"""Independent detached-planner tests; no joint balance commit is claimed."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_counters import CounterProgram, ResourceCounterError
from genshin_engine.shared_pools import SharedPoolLedger
from genshin_engine.state_runtime import StateRuntime
from tests.test_resource_counters import apply_controls, definitions, setup, wrap
from tests.test_shared_pool_v2 import spend
from tests.test_state_runtime import event


def test_real_receipt_same_time_old_control_position_is_not_reassigned():
    program, state, clock, ledger = setup(delay=500_000)
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    raw = wrap(state, spend(ledger, t=500_000))
    state, _ = apply_controls(program, state, clock, clock.handle(clock.pending()[0]))
    frozen = copy.deepcopy(state)
    with pytest.raises(ResourceCounterError) as exc:
        program.plan_spend(state, raw)
    assert exc.value.issue["code"] == "counter_receipt_order"
    assert state == frozen


def test_backward_closing_control_is_atomic_and_legitimate_retry_succeeds():
    program, state, clock, ledger = setup()
    state, _ = apply_controls(program, state, clock, clock.start("field", event(), eligible=True))
    state = program.plan_spend(state, wrap(state, spend(ledger, t=150_000)))["state"]
    closing = clock.consume_ordered_steps(clock.end("field", event(150_000, "0:end"), eligible=True)["ordered_steps"])[0]
    old = copy.deepcopy(state)
    malformed = copy.deepcopy(closing)
    malformed["time_us"] = malformed["context"]["t_us"] = 100_000
    with pytest.raises(ResourceCounterError):
        program.plan_control(state, malformed)
    assert state == old
    result = program.plan_control(state, closing)
    assert result["state"]["lifecycles"]["field"]["closing_us"] == 150_000


def test_second_key_overflow_discards_all_candidate_updates():
    conf, pools, lives = definitions()
    pools["pools"][0].update(scope="per_character", initial={"a": 16, "b": 16})
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    state, _ = apply_controls(program, program.initial_state(), clock, clock.start("field", event(), eligible=True))
    state["values"]["count"]["total"] = 10**15-8
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"])
    raw = wrap(state, spend(ledger, keys=["a", "b"]))
    old = copy.deepcopy((state, raw))
    with pytest.raises(ResourceCounterError) as exc:
        program.plan_spend(state, raw)
    assert exc.value.issue["code"] == "counter_limit"
    assert (state, raw) == old
    # The pure planner never owns or rolls back this independently used ledger.
    state["values"]["count"]["total"] = 0
    assert program.plan_spend(state, raw)["state"]["values"]["count"]["total"] == 16


def test_definition_mutation_and_later_reset_do_not_rewrite_existing_candidates():
    conf, pools, lives = definitions()
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    state, _ = apply_controls(program, program.initial_state(), clock, clock.start("field", event(), eligible=True))
    raw = spend(SharedPoolLedger(pools, character_ids=["a", "b"]))
    conf["counters"][0]["reset_on"] = "never"
    pools["pools"][0]["unit_scale"] = 1000
    result = program.plan_spend(state, wrap(state, raw))
    frozen = copy.deepcopy(result)
    fresh, _ = apply_controls(program, result["state"], clock, clock.start("field", event(100_000, "0:again"), eligible=True))
    assert fresh["values"]["count"]["total"] == 0
    assert result == frozen
    assert result["counter_receipts"][0]["unit_scale"] == 10


def test_receipt_boolean_cannot_impersonate_numeric_unit_scale():
    conf, pools, lives = definitions()
    pools["pools"][0]["unit_scale"] = 1
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    raw = spend(SharedPoolLedger(pools, character_ids=["a", "b"]))
    raw[0]["unit_scale"] = True
    with pytest.raises(ResourceCounterError):
        program.plan_spend(program.initial_state(), wrap(program.initial_state(), raw))


def test_ledger_sequence_limit_rejects_candidate_that_cannot_be_revalidated():
    program, state, _, ledger = setup()
    state["last_ledger_sequence"] = 10**15
    raw = wrap(state, spend(ledger))
    with pytest.raises(ResourceCounterError):
        program.plan_spend(state, raw)
