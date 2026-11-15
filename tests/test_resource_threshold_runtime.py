"""Adapter protocol with a real ledger; these do not replace real queue tests."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from genshin_engine.resource_threshold_dispatch import ThresholdTriggerProgram
from genshin_engine.resource_threshold_runtime import ThresholdRuntime
from genshin_engine.resource_thresholds import ThresholdObserverProgram
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
from tests.test_resource_counter_atomic import Budget
from tests.test_resource_threshold_authority import apply
from tests.test_resource_threshold_dispatch import empty, model


def setup(*, qualification=None, attributes=False, config=None):
    cfg = copy.deepcopy(config) if config is not None else model()
    if qualification is not None:
        cfg["resource_threshold_triggers"]["bindings"][0]["qualification"] = qualification
    program = ThresholdTriggerProgram(cfg, character_ids=["a", "b"])
    observer = ThresholdObserverProgram(cfg["resource_threshold_observers"], cfg["shared_pools"], character_ids=["a", "b"])
    budget = Budget()
    ledger = SharedPoolLedger(cfg["shared_pools"], character_ids=["a", "b"], observer_program=observer,
                              dispatch_program=program, reserve_events=budget.reserve)
    shared = SimpleNamespace(ledger=ledger)
    runtime = ThresholdRuntime(program, shared)
    ctx = SimpleNamespace(time=0, frontline="a", conditions={}, rules=copy.deepcopy(program._rules),
        state={rid: empty() for rid in program._rules}, trace=[], attribute_runtime=object() if attributes else None,
        attribute_layer_serial=7, beneficiaries=lambda rule, event: ["a"],
        _reserve_events=lambda count, kind=None: budget.reserve(count))
    event = {"id": "hit", "run_id": "0:hit", "event": "hit", "origin": 0}
    parent = {"id": "hit", "run_id": "0:hit", "time": 0, "order": 10, "causal": (), "origin": 0}
    return runtime, ctx, ledger, budget, event, parent


def dispatch(runtime, ctx, event, parent):
    runtime.after_operation(ctx, event, "pre", parent, "refill", "0:hit")


def test_actual_signed_operation_can_commit_one_target_without_any_panel_read():
    runtime, ctx, ledger, budget, event, parent = setup(attributes=True)
    apply(ledger)
    dispatch(runtime, ctx, event, parent)
    assert budget.used == 4
    assert ctx.attribute_layer_serial == 8
    assert ctx.state["boost"]["instances"][0]["layer_snapshots"] == [None]
    assert ctx.trace[0]["type"] == "activation"
    assert runtime.report()["trace"][0]["outcome"] == "activated"


def test_target_budget_failure_keeps_joint_resource_prefix_and_no_target_state():
    runtime, ctx, ledger, budget, event, parent = setup(attributes=True)
    apply(ledger)
    before = copy.deepcopy((ctx.state, ctx.trace, ctx.attribute_layer_serial))
    budget.limit = budget.used
    with pytest.raises(SharedPoolError): dispatch(runtime, ctx, event, parent)
    assert (ctx.state, ctx.trace, ctx.attribute_layer_serial) == before
    assert ledger.snapshot()["balances"]["pool"]["team"] == 50
    report = runtime.report()
    assert report["status"] == "failed" and len(report["authority"]["claimed"]) == 1
    assert report["trace"][0]["type"] == "threshold_dispatch_failed"


def test_rejected_qualification_reserves_work_but_does_not_activate_or_capture():
    runtime, ctx, ledger, budget, event, parent = setup(qualification={"condition": "inside"}, attributes=True)
    ctx.conditions["inside"] = False
    apply(ledger)
    dispatch(runtime, ctx, event, parent)
    assert ctx.state["boost"] == empty() and ctx.attribute_layer_serial == 7
    assert budget.used == 4
    assert runtime.report()["trace"][0]["outcome"] == "not_activated"


def test_unknown_condition_has_occurrence_failure_and_cannot_resume_same_execution():
    runtime, ctx, ledger, _, event, parent = setup(qualification={"condition": "inside"})
    apply(ledger)
    with pytest.raises(SharedPoolError) as error: dispatch(runtime, ctx, event, parent)
    assert error.value.issue["code"] == "threshold_condition_unknown"
    assert runtime.report()["trace"][0]["outcome"] == "pending"
    ctx.conditions["inside"] = True
    with pytest.raises(SharedPoolError): dispatch(runtime, ctx, event, parent)
    assert ctx.state["boost"] == empty() and len(runtime.report()["trace"]) == 1


def test_rule_edited_after_validation_cannot_use_issued_crossing():
    runtime, ctx, ledger, _, event, parent = setup()
    apply(ledger)
    ctx.rules["boost"]["timing"]["duration"] = 100
    with pytest.raises(SharedPoolError) as error: dispatch(runtime, ctx, event, parent)
    assert error.value.issue["code"] == "threshold_dispatch_target_changed"
    assert ctx.state["boost"] == empty()


@pytest.mark.parametrize("field,value", [("time", 1), ("origin", 1), ("run_id", "different")])
def test_runtime_rejects_wrong_actual_parent_context(field, value):
    runtime, ctx, ledger, _, event, parent = setup()
    apply(ledger)
    parent[field] = value
    with pytest.raises(SharedPoolError): dispatch(runtime, ctx, event, parent)
    assert ledger.threshold_dispatch_report()["claimed"] == []
    assert ctx.state["boost"] == empty()


def test_native_looking_state_dispatch_is_not_an_allowed_first_slice_origin():
    runtime, ctx, ledger, _, event, parent = setup()
    apply(ledger)
    event["state_event"] = "timer_tick"
    with pytest.raises(SharedPoolError): dispatch(runtime, ctx, event, parent)
    assert ctx.state["boost"] == empty()


def test_successful_reserve_is_followed_only_by_publication(monkeypatch):
    import genshin_engine.resource_threshold_runtime as runtime_module
    runtime, ctx, ledger, budget, event, parent = setup(attributes=True)
    apply(ledger)
    def forbidden(*args, **kwargs):
        raise AssertionError("fallible work after target reserve")
    def reserve(count, kind=None):
        assert kind == "resource_threshold_dispatch" and count == 1
        budget.reserve(count)
        monkeypatch.setattr(copy, "deepcopy", forbidden)
        monkeypatch.setattr(runtime_module, "_canonical", forbidden)
    ctx._reserve_events = reserve
    dispatch(runtime, ctx, event, parent)
    assert ctx.attribute_layer_serial == 8 and ctx.trace[0]["type"] == "activation"


def test_equivalent_independently_checked_program_can_bind_existing_issuer():
    runtime, ctx, ledger, _, event, parent = setup()
    equivalent = ThresholdTriggerProgram(model(), character_ids=["b", "a"])
    replacement = ThresholdRuntime(equivalent, runtime.shared_pools)
    apply(ledger)
    dispatch(replacement, ctx, event, parent)
    assert replacement.report()["trace"][0]["outcome"] == "activated"


def test_program_qualification_mutation_after_construction_cannot_claim():
    runtime, ctx, ledger, _, event, parent = setup()
    apply(ledger)
    before = ledger.threshold_dispatch_report()
    runtime.program._by_id["raise"]["qualification"] = {"owner_presence": "frontline"}
    with pytest.raises(SharedPoolError) as error:
        dispatch(runtime, ctx, event, parent)
    assert error.value.issue["code"] == "threshold_dispatch_binding"
    assert ledger.threshold_dispatch_report() == before and ctx.state["boost"] == empty()


def test_second_target_budget_failure_preserves_first_target_and_resource_prefix():
    cfg = model()
    second = copy.deepcopy(cfg["rules"][0])
    second.update(id="second", trigger={"kind": "resource_threshold", "binding_id": "raise2"})
    cfg["rules"].append(second)
    binding = copy.deepcopy(cfg["resource_threshold_triggers"]["bindings"][0])
    binding.update(id="raise2", order=2, payload={"kind": "activate_rule", "ref_id": "second"})
    cfg["resource_threshold_triggers"]["bindings"].append(binding)
    runtime, ctx, ledger, budget, event, parent = setup(config=cfg, attributes=True)
    apply(ledger)
    budget.limit = budget.used+1
    with pytest.raises(SharedPoolError):
        dispatch(runtime, ctx, event, parent)
    assert ledger.snapshot()["balances"]["pool"]["team"] == 50
    assert len(ctx.state["boost"]["instances"]) == 1 and ctx.state["second"] == empty()
    assert ctx.attribute_layer_serial == 8 and len(ctx.trace) == 1
    rows = runtime.report()["trace"]
    assert [(r["binding_id"], r["type"]) for r in rows] == [
        ("raise", "threshold_dispatch_completed"), ("raise2", "threshold_dispatch_failed")]
    assert len(ledger.threshold_dispatch_report()["claimed"]) == 1


def test_typed_target_without_trigger_config_is_rejected_even_with_observer_config():
    from genshin_engine.shared_pool_runtime import validate_bindings
    cfg = model()
    cfg.pop("resource_threshold_triggers")
    assert validate_bindings(cfg, characters=["a", "b"])[0]["code"] == "threshold_reference"
