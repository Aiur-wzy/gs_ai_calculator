"""Real atomic ledger tests; distinct from pure detached-planner validation."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_counters import CounterProgram, ResourceCounterError
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
from genshin_engine.state_runtime import StateRuntime
from tests.test_resource_counters import definitions
from tests.test_shared_pool_v2 import spend
from tests.test_shared_pools import consume
from tests.test_state_runtime import event


class Budget:
    def __init__(self):
        self.used, self.limit = 0, 40000

    def reserve(self, count):
        if self.used+count > self.limit:
            raise SharedPoolError("pool_work_limit", "工程公共预算超限", severity="pending")
        self.used += count


def make(*, second=False):
    conf, pools, lives = definitions()
    if second:
        conf["counters"].append({**copy.deepcopy(conf["counters"][0]), "id": "second", "source_id": "source:second"})
    budget = Budget()
    program = CounterProgram(conf, pools, lives, character_ids=["a", "b"])
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], counter_program=program, reserve_events=budget.reserve)
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    return ledger, clock, budget


def controls(ledger, clock, out):
    for step in clock.consume_ordered_steps(out["ordered_steps"]):
        ledger.apply_counter_control(step)


def image(ledger, budget):
    return copy.deepcopy((ledger.snapshot(), ledger.receipts(), ledger.counter_snapshot(),
                          ledger.counter_receipts(), budget.used, ledger._cooldowns,
                          ledger._event_identity, ledger._event_stage, ledger._operation_seen,
                          ledger._consumption, ledger._phase_order))


def test_second_counter_overflow_is_atomic_with_resources_and_retry_identity():
    ledger, clock, budget = make(second=True)
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    ledger._counter_state["values"]["second"]["total"] = 10**15
    before = image(ledger, budget)
    with pytest.raises(ResourceCounterError): spend(ledger)
    assert image(ledger, budget) == before
    ledger._counter_state["values"]["second"]["total"] = 0
    rows = spend(ledger)
    assert rows[0]["accepted"] == 8
    assert ledger.counter_snapshot()["values"] == {"count": {"total": 8}, "second": {"total": 8}}


def test_public_work_budget_failure_preserves_both_cursors_and_retry():
    ledger, clock, budget = make()
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    budget.limit = budget.used+1  # operation + counter update needs 2
    before = image(ledger, budget)
    with pytest.raises(SharedPoolError): spend(ledger)
    assert image(ledger, budget) == before
    budget.limit += 1
    spend(ledger)
    assert ledger.counter_snapshot()["last_ledger_sequence"] == 1


def test_control_without_counter_rows_still_reserves_one_execution_work():
    ledger, clock, budget = make()
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    queued = clock.pending()[0]
    steps = clock.consume_ordered_steps(clock.handle(queued)["ordered_steps"])
    assert len(steps) == 1 and steps[0]["kind"] == "dispatch"
    budget.limit = budget.used
    before = image(ledger, budget)
    with pytest.raises(SharedPoolError): ledger.apply_counter_control(steps[0])
    assert image(ledger, budget) == before
    budget.limit += 1
    ledger.apply_counter_control(steps[0])
    assert ledger.counter_snapshot()["last_control_sequence"] == steps[0]["sequence"]


def test_consumer_reread_does_not_redeliver_receipt_or_counter_cursor():
    ledger, clock, budget = make()
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    first = consume(ledger, t=0)
    before = image(ledger, budget)
    assert consume(ledger, t=0) == first
    assert image(ledger, budget) == before
    assert ledger.counter_snapshot()["last_ledger_sequence"] == 1
    assert ledger.counter_snapshot()["values"]["count"]["total"] == 0
    spend(ledger)
    assert ledger.counter_snapshot()["last_ledger_sequence"] == 2
    assert ledger.counter_snapshot()["values"]["count"]["total"] == 8


def test_successful_previous_operation_survives_later_counter_failure():
    ledger, clock, budget = make()
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    spend(ledger)
    prefix = image(ledger, budget)
    budget.limit = budget.used
    with pytest.raises(SharedPoolError): spend(ledger, t=100_000, run="0:later")
    assert image(ledger, budget) == prefix


def test_no_counter_configuration_preserves_legacy_output_shape():
    _, pools, _ = definitions()
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"])
    spend(ledger)
    assert "resource_counters" not in ledger.snapshot()
    assert "counter_updates" not in ledger.receipts()[0]
