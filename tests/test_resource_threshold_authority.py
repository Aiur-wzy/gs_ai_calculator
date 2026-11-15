"""Real ledger issuance/claim authority; actual rule execution is not wired here."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_threshold_dispatch import (
    DISPATCH_LIMITS,
    ThresholdTriggerProgram,
)
from genshin_engine.resource_thresholds import ThresholdObserverProgram
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger, _canonical
from tests.test_resource_counter_atomic import Budget
from tests.test_resource_threshold_dispatch import model


def make(config=None):
    cfg = config or model()
    program = ThresholdTriggerProgram(cfg, character_ids=["a", "b"])
    observer = ThresholdObserverProgram(cfg["resource_threshold_observers"], cfg["shared_pools"], character_ids=["a", "b"])
    budget = Budget()
    ledger = SharedPoolLedger(cfg["shared_pools"], character_ids=["a", "b"], observer_program=observer,
                              dispatch_program=program, reserve_events=budget.reserve)
    return ledger, budget


def apply(ledger, run="0:hit"):
    return ledger.apply_operation("refill", event_id="hit", run_id=run, t_us=0,
        phase="pre", event_kind="hit", keys=["team"], eligible=True)


def claim(ledger, batch=None, **kw):
    authority = ledger._dispatch_authority
    return authority.claim(batch if batch is not None else authority._last_batch, event_id="hit",
        run_id=kw.get("run", "0:hit"), t_us=kw.get("t", 0), phase=kw.get("phase", "pre"), command_id="refill")


def image(ledger, budget):
    return copy.deepcopy((ledger.snapshot(), ledger.receipts(), ledger.observer_snapshot(), ledger.observer_receipts(),
        ledger.threshold_dispatch_report(), ledger._event_identity, ledger._event_stage, ledger._operation_seen,
        ledger._phase_order, ledger._observer_work, budget.used, ledger._dispatch_authority._next_claim))


def test_resource_observer_and_batch_joint_commit_then_claim_is_not_activation():
    ledger, budget = make()
    rows = apply(ledger)
    assert rows[0]["after"] == 50 and budget.used == 3
    report = ledger.threshold_dispatch_report()
    assert len(report["issued"]) == 1 and report["claimed"] == []
    assert report["applied"] == "reported_by_runtime_not_authority"
    commands = claim(ledger)
    assert len(commands) == 1 and commands[0]["binding_id"] == "raise"
    assert len(ledger.threshold_dispatch_report()["claimed"]) == 1
    assert budget.used == 3


@pytest.mark.parametrize("forgery", ["copied", "display", "other_ledger", "changed", "wrong_time", "wrong_phase"])
def test_only_original_unchanged_object_and_exact_current_occurrence_can_be_claimed(forgery):
    ledger, budget = make()
    apply(ledger)
    authority = ledger._dispatch_authority
    original = authority._last_batch
    candidate = original
    kwargs = {}
    if forgery == "copied": candidate = copy.deepcopy(original)
    elif forgery == "display": candidate = ledger.threshold_dispatch_report()["issued"][0]
    elif forgery == "other_ledger":
        other, _ = make()
        apply(other)
        candidate = other._dispatch_authority._last_batch
    elif forgery == "changed": original["commands"][0]["threshold_atoms"] += 1
    elif forgery == "wrong_time": kwargs["t"] = True
    elif forgery == "wrong_phase": kwargs["phase"] = "post"
    before = image(ledger, budget)
    with pytest.raises(SharedPoolError): claim(ledger, candidate, **kwargs)
    assert image(ledger, budget) == before
    if forgery == "changed": original["commands"][0]["threshold_atoms"] -= 1
    assert len(claim(ledger)) == 1
    with pytest.raises(SharedPoolError): claim(ledger)


def test_signing_public_budget_failure_is_atomic_and_can_retry_actual_identity():
    ledger, budget = make()
    budget.limit = 2
    before = image(ledger, budget)
    with pytest.raises(SharedPoolError): apply(ledger)
    assert image(ledger, budget) == before
    budget.limit = 3
    apply(ledger)
    assert ledger._dispatch_authority._issued_sequence == 1


def test_signing_byte_failure_does_not_commit_resource_or_observer(monkeypatch):
    ledger, budget = make()
    before = image(ledger, budget)
    monkeypatch.setitem(DISPATCH_LIMITS, "trace_bytes", 4)
    with pytest.raises(SharedPoolError): apply(ledger)
    assert image(ledger, budget) == before


def test_replaying_signed_batch_or_modifying_report_never_reapplies():
    ledger, _ = make()
    apply(ledger)
    batch = ledger._dispatch_authority._last_batch
    commands = claim(ledger)
    commands[0]["payload"]["ref_id"] = "forged"
    assert ledger.threshold_dispatch_report()["issued"][0]["commands"][0]["payload"]["ref_id"] == "boost"
    with pytest.raises(SharedPoolError): claim(ledger, batch)


def test_declared_rule_graph_cannot_be_rebound_to_a_changed_pool_registry():
    cfg = model()
    program = ThresholdTriggerProgram(cfg, character_ids=["a", "b"])
    cfg["shared_pools"]["operations"][0]["amount"] = 2
    obs = ThresholdObserverProgram(cfg["resource_threshold_observers"], cfg["shared_pools"], character_ids=["a", "b"])
    with pytest.raises(SharedPoolError):
        SharedPoolLedger(cfg["shared_pools"], character_ids=["a", "b"], observer_program=obs, dispatch_program=program)


def test_future_claim_row_bytes_are_charged_at_joint_issue_commit():
    ledger, _ = make()
    apply(ledger)
    before = ledger.threshold_dispatch_report()["reserved_bytes"]
    claim(ledger)
    report = ledger.threshold_dispatch_report()
    assert before == report["reserved_bytes"] == len(_canonical(report["issued"]))+len(_canonical(report["claimed"]))


def test_unclaimed_crossing_prevents_skipping_to_next_resource_transaction():
    ledger, budget = make()
    apply(ledger)
    before = image(ledger, budget)
    with pytest.raises(SharedPoolError) as error:
        apply(ledger, "0:next")
    assert error.value.issue["code"] == "threshold_dispatch_unclaimed"
    assert image(ledger, budget) == before
    claim(ledger)
    apply(ledger, "0:next")
    assert ledger.snapshot()["balances"]["pool"]["team"] == 51
    assert ledger._dispatch_authority._last_batch is None


def test_publishing_signed_batch_does_not_copy_or_serialize_after_joint_reserve(monkeypatch):
    import genshin_engine.resource_threshold_authority as authority_module
    import genshin_engine.resource_thresholds as observer_module
    import genshin_engine.shared_pools as pool_module
    ledger, _ = make()
    def forbidden(*args, **kwargs):
        raise AssertionError("fallible action after joint reserve")
    def reserve(count):
        assert count == 3
        monkeypatch.setattr(authority_module, "_canonical", forbidden)
        monkeypatch.setattr(observer_module, "_canonical", forbidden)
        monkeypatch.setattr(pool_module, "_canonical", forbidden)
        monkeypatch.setattr(copy, "deepcopy", forbidden)
    ledger._reserve_counter_events = reserve
    assert apply(ledger)[0]["after"] == 50
    assert ledger._dispatch_authority._last_batch["sequence"] == 1


def test_counter_observer_and_signing_are_atomic_in_one_actual_resource_commit():
    from genshin_engine.resource_counters import CounterProgram
    from genshin_engine.state_runtime import StateRuntime
    from tests.test_resource_counter_atomic import controls
    from tests.test_resource_counters import definitions
    from tests.test_state_runtime import event
    cfg = model()
    counter_config, _, lives = definitions()
    cfg["shared_pools"]["pools"][0]["initial"]["team"] = 58
    cfg["shared_pools"]["operations"][0].update(operation="spend", amount=8, spend_policy="up_to_available")
    cfg["resource_threshold_observers"]["observers"][0]["comparison"] = "enter_lte"
    cfg["state_runtime"] = lives
    trigger = ThresholdTriggerProgram(cfg, character_ids=["a", "b"])
    observer = ThresholdObserverProgram(cfg["resource_threshold_observers"], cfg["shared_pools"], character_ids=["a", "b"])
    counter = CounterProgram(counter_config, cfg["shared_pools"], lives, character_ids=["a", "b"])
    budget = Budget()
    ledger = SharedPoolLedger(cfg["shared_pools"], character_ids=["a", "b"], counter_program=counter,
        observer_program=observer, dispatch_program=trigger, reserve_events=budget.reserve)
    clock = StateRuntime(lives, character_ids=["a", "b"], emit_ordered_steps=True)
    controls(ledger, clock, clock.start("field", event(), eligible=True))
    before = image(ledger, budget), ledger.counter_snapshot(), ledger.counter_receipts()
    used = budget.used
    budget.limit = used+3
    with pytest.raises(SharedPoolError): apply(ledger)
    assert (image(ledger, budget), ledger.counter_snapshot(), ledger.counter_receipts()) == before
    budget.limit += 1
    apply(ledger)
    assert budget.used-used == 4
    assert ledger.counter_snapshot()["values"]["count"]["total"] == 8
    assert ledger.observer_receipts()[0]["crossed"] is True
    assert len(ledger.threshold_dispatch_report()["issued"]) == 1
