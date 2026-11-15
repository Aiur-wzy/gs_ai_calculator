"""R2a: one committed counter key, not timer dispatch or game-clock proof."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_counters import CounterProgram
from genshin_engine.resource_thresholds import ThresholdObserverProgram
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
from genshin_engine.state_runtime import StateRuntime
from tests.test_resource_counter_atomic import Budget, controls
from tests.test_resource_counters import apply_controls, definitions, wrap
from tests.test_resource_thresholds import declaration, image
from tests.test_shared_pool_v2 import spend
from tests.test_state_runtime import event


def setup(*, scope="pool_total", multi=False, selected=None, reset="create"):
    cfg, pools, lives = definitions(scope=scope, reset=reset)
    pools["pools"][0]["initial"] = {"a": 19, "b": 19} if multi else {"team": 19}
    if multi: pools["pools"][0]["scope"] = "per_character"
    if selected:
        other = copy.deepcopy(pools["operations"][0])
        other.update(id="other", order=2)
        pools["operations"].append(other)
        cfg["counters"][0]["operation_ids"].append("other")
    conf = declaration(pools)
    conf["schema_version"] = 2
    conf["observers"][0].update(input={"kind": "counter_value_key", "counter_id": "count", "key": "a" if scope == "pool_key" else "total"}, threshold_atoms=16)
    counter = CounterProgram(cfg, pools, lives, character_ids=["a", "b"])
    observer = ThresholdObserverProgram(conf, pools, character_ids=["a", "b"], counter_program=counter)
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    return observer, counter, pools, clock


def real(**kwargs):
    observer, counter, pools, clock = setup(**kwargs)
    budget = Budget()
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], observer_program=observer,
                              counter_program=counter, reserve_events=budget.reserve)
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    return ledger, budget, clock


def pure():
    observer, counter, pools, clock = setup()
    before, _ = apply_controls(counter, counter.initial_state(), clock, clock.start("field", event(), eligible=True))
    raw = SharedPoolLedger(pools, character_ids=["a", "b"])
    rows = spend(raw)
    after = counter.plan_spend(before, wrap(before, rows))
    candidate = {"receipt_start": 1, "receipt_end": 1, "receipts": rows,
                 "counter_span": {"before": before, "after": after["state"], "receipts": after["counter_receipts"]}}
    return observer, candidate


def test_actual_counter_crossing_does_not_truncate_raw_or_consume_again(monkeypatch):
    ledger, budget, _ = real()
    original = CounterProgram.plan_spend
    calls = []
    def tracked(program, state, receipts):
        calls.append(len(receipts))
        return original(program, state, receipts)
    monkeypatch.setattr(CounterProgram, "plan_spend", tracked)
    start = budget.used
    for i in range(3):
        spend(ledger, t=i*100_000, run=f"0:s{i}")
    assert calls == [1, 1, 1]
    rows = ledger.observer_receipts()
    assert [(r["before"], r["after"], r["crossed"]) for r in rows] == [(0, 8, False), (8, 16, True), (16, 19, False)]
    assert rows[1]["input_kind"] == "counter_value_key" and rows[1]["counter_id"] == "count"
    assert ledger.counter_snapshot()["values"]["count"]["total"] == 19
    assert ledger.snapshot()["balances"]["pool"]["team"] == 0 and budget.used-start == 9


def test_one_multikey_total_transaction_is_one_observation_and_not_per_receipt():
    ledger, budget, _ = real(multi=True)
    start = budget.used
    spend(ledger, keys=["b", "a"])
    rows = ledger.observer_receipts()
    assert [(r["before"], r["after"], r["crossed"]) for r in rows] == [(0, 16, True)]
    assert rows[0]["receipt_span"] == [1, 2]
    assert budget.used-start == 4  # one command, two counter rows, one observer


def test_pool_key_counter_does_not_accidentally_sum_other_keys():
    ledger, _, _ = real(scope="pool_key", multi=True)
    spend(ledger, keys=["a", "b"])
    assert [(r["before"], r["after"], r["crossed"]) for r in ledger.observer_receipts()] == [(0, 8, False)]


def test_unselected_cause_changes_real_baseline_without_cached_crossing():
    ledger, _, _ = real(selected=True)
    for i in range(2):
        ledger.apply_operation("other", event_id="tick", run_id=f"0:o{i}", t_us=i*100_000,
            phase="post", event_kind="elemental_reaction", keys=["team"], eligible=True)
    assert ledger.observer_receipts() == []
    spend(ledger, t=200_000, run="0:selected")
    assert [(r["before"], r["after"], r["crossed"]) for r in ledger.observer_receipts()] == [(16, 19, False)]


@pytest.mark.parametrize("change", ["accepted", "key", "generation", "after", "control", "missing", "duplicated", "bool"])
def test_detached_counter_span_requires_complete_consistent_receipts(change):
    observer, candidate = pure()
    span = candidate["counter_span"]
    if change == "accepted": span["receipts"][0]["accepted"] -= 1
    elif change == "key": span["receipts"][0]["key"] = "missing"
    elif change == "generation": span["receipts"][0]["generation_uid"] = "forged"
    elif change == "after": span["after"]["values"]["count"]["total"] += 1
    elif change == "control": span["after"]["last_control_sequence"] += 1
    elif change == "missing": span["receipts"] = []
    elif change == "duplicated": span["receipts"].append(copy.deepcopy(span["receipts"][0]))
    else: span["receipts"][0]["accepted"] = True
    state = observer.initial_state()
    original = copy.deepcopy((state, candidate))
    with pytest.raises(SharedPoolError): observer.plan_commit(state, candidate)
    assert (state, candidate) == original


def test_joint_budget_failure_preserves_balance_counter_observer_and_retry_identity():
    ledger, budget, _ = real()
    budget.limit = budget.used+2
    before = image(ledger, budget)
    with pytest.raises(SharedPoolError): spend(ledger)
    assert image(ledger, budget) == before
    budget.limit += 1
    spend(ledger)
    assert ledger.counter_snapshot()["values"]["count"]["total"] == 8


def test_counter_declaration_cannot_bind_a_different_actual_ledger_program():
    observer, counter, pools, _ = setup()
    _, other, _, _ = setup(reset="never")
    with pytest.raises(SharedPoolError):
        SharedPoolLedger(pools, character_ids=["a", "b"], counter_program=other,
            observer_program=observer, reserve_events=Budget().reserve)
    assert counter._fingerprint != other._fingerprint


@pytest.mark.parametrize("reset,expected", [("create", (0, 3)), ("never", (16, 19))])
def test_control_reset_is_not_an_observation_and_next_spend_reads_actual_baseline(reset, expected):
    ledger, _, clock = real(reset=reset)
    spend(ledger)
    spend(ledger, t=100_000, run="0:second")
    rows = copy.deepcopy(ledger.observer_receipts())
    controls(ledger, clock, clock.start("field", event(200_000, "0:new-generation"), eligible=True))
    assert ledger.observer_receipts() == rows
    spend(ledger, t=200_000, run="0:third")
    row = ledger.observer_receipts()[-1]
    assert (row["before"], row["after"]) == expected and not row["crossed"]
    assert row["history_scope"] == ("lifetime_total" if reset == "never" else "since_last_reset")


def test_outside_unstarted_window_keeps_counter_zero_despite_real_spend():
    observer, counter, pools, _ = setup()
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], counter_program=counter,
        observer_program=observer, reserve_events=Budget().reserve)
    spend(ledger)
    row = ledger.observer_receipts()[0]
    assert row["before"] == row["after"] == 0 and not row["crossed"]
    assert ledger.snapshot()["balances"]["pool"]["team"] == 11


def test_counter_candidate_rejects_hidden_reset_even_if_after_values_stay_valid():
    observer, candidate = pure()
    candidate["counter_span"]["after"]["values"]["count"]["total"] = 0
    candidate["counter_span"]["receipts"][0].update(after=0, accepted=0, reason="outside_window")
    with pytest.raises(SharedPoolError): observer.plan_commit(observer.initial_state(), candidate)


def test_second_key_receipt_tampering_rejects_whole_detached_candidate():
    observer, counter, pools, clock = setup(multi=True)
    before, _ = apply_controls(counter, counter.initial_state(), clock, clock.start("field", event(), eligible=True))
    raw = SharedPoolLedger(pools, character_ids=["a", "b"])
    rows = spend(raw, keys=["a", "b"])
    after = counter.plan_spend(before, wrap(before, rows))
    candidate = {"receipt_start": 1, "receipt_end": 2, "receipts": rows,
        "counter_span": {"before": before, "after": after["state"], "receipts": after["counter_receipts"]}}
    candidate["counter_span"]["receipts"][-1]["resource_receipt_id"] = "wrong"
    original = copy.deepcopy(candidate)
    with pytest.raises(SharedPoolError): observer.plan_commit(observer.initial_state(), candidate)
    assert candidate == original
