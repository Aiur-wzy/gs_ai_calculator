"""Off-tree R0 contract and atomic ledger acceptance; no production imports patched."""
from __future__ import annotations

import copy
import importlib

import pytest

from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
from tests.test_resource_counter_atomic import Budget, controls
from tests.test_resource_counters import definitions
from tests.test_shared_pool_v2 import spend
from tests.test_state_runtime import event


def declaration(pools=None, *, down=False, key="team"):
    if pools is None:
        _, pools, _ = definitions()
    return {"schema_version": 1, "observers": [{
        "id": "cross", "name_zh": "工程阈值", "owner": "a", "source_id": "source:threshold",
        "input": {"kind": "pool_balance_key", "pool_id": "pool", "key": key},
        "comparison": "enter_lte" if down else "enter_gte", "threshold_atoms": 50,
        "causes": {"operation_ids": ["refill"], "consumer_ids": []},
        "status": "assumed", "source": "工程假设，非游戏证据"}]}


def program(conf, pools):
    mod = importlib.import_module("genshin_engine.resource_thresholds")
    return mod.ThresholdObserverProgram(conf, pools, character_ids=["a", "b"])


def make(*, counter=False, initial=58, down=True):
    cfg, pools, lives = definitions()
    pools["pools"][0]["initial"] = {"team": initial}
    budget = Budget()
    kwargs = {}
    clock = None
    if counter:
        from genshin_engine.resource_counters import CounterProgram
        from genshin_engine.state_runtime import StateRuntime
        kwargs["counter_program"] = CounterProgram(cfg, pools, lives, character_ids=["a", "b"])
        clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    obs = program(declaration(pools, down=down), pools)
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], observer_program=obs,
                              reserve_events=budget.reserve, **kwargs)
    if counter:
        controls(ledger, clock, clock.start("field", event(), eligible=True))
    return ledger, budget, clock


def image(ledger, budget):
    return copy.deepcopy((ledger.snapshot(), ledger.receipts(), ledger.counter_snapshot(), ledger.counter_receipts(),
        ledger.observer_snapshot(), ledger.observer_receipts(), budget.used, ledger._time, ledger._event_identity,
        ledger._event_stage, ledger._operation_seen, ledger._consumption, ledger._phase_order,
        ledger._receipt_bytes, ledger._counter_work, ledger._observer_work, ledger._observer_receipt_bytes))


def test_real_spend_and_observer_publish_jointly_without_new_public_snapshot_fields():
    ledger, budget, _ = make()
    rows = spend(ledger)
    observed = ledger.observer_receipts()
    assert rows[0]["after"] == 50
    assert [(r["before"], r["after"], r["crossed"]) for r in observed] == [(58, 50, True)]
    assert observed[0]["authority"] == "audit_no_dispatch_authority"
    assert observed[0]["execution"] == "ledger_committed_audit"
    assert budget.used == 2
    assert ledger.observer_snapshot()["last_ledger_sequence"] == 1
    assert "observers" not in ledger.snapshot()


def test_unbounded_pool_observer_uses_safety_limit_not_a_gameplay_cap():
    _, pools, _ = definitions()
    pools["pools"][0].update(capacity="unbounded", initial={"team": 58})
    observer = program(declaration(pools, down=True), pools)
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], observer_program=observer)
    row = spend(ledger)[0]
    assert row["capacity_mode"] == "unbounded" and row["after"] == 50
    assert ledger.observer_receipts()[0]["crossed"] is True


def test_counter_resource_observer_share_one_reserve_and_three_outputs():
    ledger, budget, _ = make(counter=True)
    used = budget.used
    spend(ledger)
    assert budget.used-used == 3
    assert ledger.counter_snapshot()["values"]["count"]["total"] == 8
    assert ledger.observer_receipts()[0]["crossed"] is True


@pytest.mark.parametrize("counter", [False, True])
def test_failed_shared_budget_is_atomic_and_same_identity_can_retry(counter):
    ledger, budget, _ = make(counter=counter)
    budget.limit = budget.used + (2 if counter else 1)
    before = image(ledger, budget)
    with pytest.raises(SharedPoolError):
        spend(ledger)
    assert image(ledger, budget) == before
    budget.limit += 1
    spend(ledger)
    assert ledger.observer_snapshot()["last_ledger_sequence"] == 1


def test_observer_byte_failure_does_not_commit_counter_or_balance(monkeypatch):
    mod = importlib.import_module("genshin_engine.resource_threshold_schema")
    ledger, budget, _ = make(counter=True)
    before = image(ledger, budget)
    monkeypatch.setitem(mod.THRESHOLD_LIMITS, "trace_bytes", 2)
    with pytest.raises(SharedPoolError):
        spend(ledger)
    assert image(ledger, budget) == before


def test_no_matching_cause_advances_global_span_but_no_evaluation():
    ledger, budget, _ = make()
    ledger.try_consume("consumer", event_id="hit", run_id="0:hit", t_us=0, actor="a", eligible=True)
    assert ledger.observer_receipts() == []
    assert ledger.observer_snapshot()["last_ledger_sequence"] == 1
    spend(ledger)
    assert ledger.observer_snapshot()["last_ledger_sequence"] == 2
    assert len(ledger.observer_receipts()) == 1
    assert budget.used == 3


def test_consumer_reread_does_not_repeat_observer_or_cost():
    _, pools, _ = definitions()
    conf = declaration(pools, down=True)
    conf["observers"][0]["causes"] = {"operation_ids": [], "consumer_ids": ["consumer"]}
    budget = Budget()
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], observer_program=program(conf, pools), reserve_events=budget.reserve)
    first = ledger.try_consume("consumer", event_id="hit", run_id="0:hit", t_us=0, actor="a", eligible=True)
    before = image(ledger, budget)
    assert ledger.try_consume("consumer", event_id="hit", run_id="0:hit", t_us=0, actor="a", eligible=True) == first
    assert image(ledger, budget) == before


def test_equal_threshold_and_ineligible_are_non_crossings():
    ledger, _, _ = make(initial=50)
    assert ledger.observer_receipts() == []
    spend(ledger, eligible=False)
    assert ledger.observer_receipts()[0]["crossed"] is False
    spend(ledger, run="0:next", t=1)
    assert [r["crossed"] for r in ledger.observer_receipts()] == [False, False]


@pytest.mark.parametrize("bad", [True, 50.0, -1, 1001])
def test_threshold_requires_bounded_integer_atoms(bad):
    _, pools, _ = definitions()
    cfg = declaration(pools)
    cfg["observers"][0]["threshold_atoms"] = bad
    with pytest.raises(SharedPoolError):
        program(cfg, pools)


def test_configuration_and_reports_are_detached():
    _, pools, _ = definitions()
    cfg = declaration(pools, down=True)
    prog = program(cfg, pools)
    cfg["observers"][0]["threshold_atoms"] = 1
    pools["pools"][0]["initial"]["team"] = 1
    assert prog.initial_state()["execution"] == "candidate_only"
    ledger, _, _ = make()
    spend(ledger)
    result = ledger.observer_receipts()
    result[0]["crossed"] = False
    state = ledger.observer_snapshot()
    state["last_ledger_sequence"] = 999
    assert ledger.observer_receipts()[0]["crossed"] is True
    assert ledger.observer_snapshot()["last_ledger_sequence"] == 1


def test_plain_legacy_ledger_keeps_outputs_and_does_not_reserve():
    _, pools, _ = definitions()
    budget = Budget()
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], reserve_events=budget.reserve)
    spend(ledger)
    assert budget.used == 0
    assert ledger.observer_snapshot() is None and ledger.observer_receipts() == []
    assert set(ledger.receipts()[0]).isdisjoint({"observer", "crossed", "authority"})


def test_other_pool_span_is_seen_without_index_scan_or_evaluation():
    _, pools, _ = definitions()
    other = copy.deepcopy(pools["pools"][0])
    other.update(id="other", source_id="source:other")
    pools["pools"].append(other)
    op = copy.deepcopy(pools["operations"][0])
    op.update(id="other-spend", pool_id="other")
    pools["operations"].append(op)
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], observer_program=program(declaration(), pools))
    ledger.apply_operation("other-spend", event_id="other", run_id="0:other", t_us=0,
        phase="post", event_kind="elemental_reaction", keys=["team"], eligible=True)
    assert ledger.observer_snapshot()["last_ledger_sequence"] == 1
    assert ledger.observer_snapshot()["index_visits"] == 0
    spend(ledger)
    assert ledger.observer_snapshot()["last_ledger_sequence"] == 2
    assert ledger.observer_snapshot()["index_visits"] == 1


def test_multikey_fixed_key_evaluates_once_and_key_order_is_irrelevant():
    _, pools, _ = definitions()
    pools["pools"][0].update(scope="per_character", initial={"a": 58, "b": 0})
    conf = declaration(pools, down=True, key="a")
    left = SharedPoolLedger(pools, character_ids=["a", "b"], observer_program=program(conf, pools))
    right = SharedPoolLedger(pools, character_ids=["a", "b"], observer_program=program(conf, pools))
    spend(left, keys=["a", "b"])
    spend(right, keys=["b", "a"])
    assert left.observer_receipts() == right.observer_receipts()
    assert len(left.observer_receipts()) == 1
    assert left.observer_receipts()[0]["crossed"] is True
    assert left.observer_snapshot()["last_ledger_sequence"] == 2


def test_same_time_cross_back_cross_again_uses_each_actual_span():
    _, pools, _ = definitions()
    pools["pools"][0]["initial"] = {"team": 49}
    debit = pools["operations"][0]
    debit["amount"] = 1
    grant = copy.deepcopy(debit)
    grant.update(id="grant", operation="grant", amount=1, order=1)
    grant.pop("spend_policy")
    pools["operations"].append(grant)
    conf = declaration(pools)
    conf["observers"][0]["causes"]["operation_ids"].append("grant")
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], observer_program=program(conf, pools))
    for number, operation in enumerate(("grant", "refill", "grant")):
        ledger.apply_operation(operation, event_id="tick", run_id=f"0:hit-{number}", t_us=0,
            phase="post", event_kind="elemental_reaction", keys=["team"], eligible=True)
    assert [r["crossed"] for r in ledger.observer_receipts()] == [True, False, True]
    assert ledger.observer_snapshot()["last_ledger_sequence"] == 3


def test_set_to_zero_crosses_without_claiming_accepted_spend():
    _, pools, _ = definitions()
    pools["operations"][0].update(operation="set", amount=0)
    pools["operations"][0].pop("spend_policy")
    conf = declaration(pools, down=True)
    conf["observers"][0]["threshold_atoms"] = 0
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"], observer_program=program(conf, pools))
    receipts = spend(ledger)
    assert receipts[0]["accepted"] == 0 and receipts[0]["delta"] == -100
    assert ledger.observer_receipts()[0]["crossed"] is True
    assert ledger.counter_snapshot() is None


def test_counter_overflow_prevents_observer_cursor_and_audit():
    ledger, budget, _ = make(counter=True)
    ledger._counter_state["values"]["count"]["total"] = 10**15
    before = image(ledger, budget)
    with pytest.raises(SharedPoolError):
        spend(ledger)
    assert image(ledger, budget) == before


def test_later_counter_control_budget_includes_existing_observer_rows(monkeypatch):
    from genshin_engine.shared_pool_schema import LIMITS
    ledger, budget, clock = make(counter=True)
    spend(ledger)
    steps = clock.consume_ordered_steps(clock.end("field", event(t=1, run="0:end"), eligible=True)["ordered_steps"])
    count = len(ledger.receipts()) + len(ledger.counter_receipts())
    # One new counter window would fit without the already committed observer.
    monkeypatch.setitem(LIMITS, "receipts", count+1)
    before = image(ledger, budget)
    with pytest.raises(SharedPoolError):
        ledger.apply_counter_control(steps[0])
    assert image(ledger, budget) == before


def test_all_copies_serialization_and_candidate_validation_precede_reserve(monkeypatch):
    import genshin_engine.resource_thresholds as observer_module
    import genshin_engine.shared_pools as pool_module
    ledger, _, _ = make(counter=True)
    def forbidden(*args, **kwargs):
        raise AssertionError("fallible work after reserve")
    def reserve(count):
        assert count == 3
        monkeypatch.setattr(pool_module, "_canonical", forbidden)
        monkeypatch.setattr(observer_module, "_canonical", forbidden)
        monkeypatch.setattr(copy, "deepcopy", forbidden)
    ledger._reserve_counter_events = reserve
    result = spend(ledger)
    assert result[0]["after"] == 50
    assert ledger._observer_receipts[0]["crossed"] is True


@pytest.mark.parametrize("mutation", ["bool_scale", "delta", "span", "duplicate_key", "foreign_run", "before_bool"])
def test_pure_candidate_rejects_malformed_receipts(mutation):
    _, pools, _ = definitions()
    prog = program(declaration(pools, down=True), pools)
    raw = SharedPoolLedger(pools, character_ids=["a", "b"])
    rows = spend(raw)
    cand = {"receipt_start": 1, "receipt_end": 1, "receipts": rows}
    if mutation == "bool_scale": rows[0]["unit_scale"] = True
    elif mutation == "delta": rows[0]["delta"] = 0
    elif mutation == "span": cand["receipt_start"] = 2
    elif mutation == "duplicate_key":
        cand["receipt_end"] = 2
        rows.append(copy.deepcopy(rows[0]))
    elif mutation == "foreign_run": rows[0]["run_id"] = []
    else: rows[0]["before"] = True
    initial = prog.initial_state()
    before = copy.deepcopy(initial)
    with pytest.raises(SharedPoolError):
        prog.plan_commit(initial, cand)
    assert initial == before


def test_pure_finite_cursor_upper_bound_does_not_publish_unusable_state():
    _, pools, _ = definitions()
    prog = program(declaration(pools), pools)
    state = prog.initial_state()
    state["last_ledger_sequence"] = 10**15
    ledger = SharedPoolLedger(pools, character_ids=["a", "b"])
    with pytest.raises(SharedPoolError):
        prog.plan_commit(state, {"receipt_start": 10**15+1, "receipt_end": 10**15+1, "receipts": spend(ledger)})


def test_pending_source_and_unknown_zero_key_are_not_approved():
    _, pools, _ = definitions()
    conf = declaration(pools)
    conf["observers"][0]["status"] = "pending"
    with pytest.raises(SharedPoolError):
        program(conf, pools)
    conf["observers"][0]["status"] = "assumed"
    conf["observers"][0]["input"]["key"] = "missing"
    with pytest.raises(SharedPoolError):
        program(conf, pools)
