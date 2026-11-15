"""Full entry receipts, error priority and lifecycle scopes against the old entry."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest
from test_freeze_runtime import inputs as freeze_inputs
from test_mechanics_execution import add, fixture, rule
from test_mechanics_swirl import setup as swirl_inputs

from genshin_engine import mechanics
from genshin_engine import mechanics_execution_pipeline as pipeline
from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.elemental import ElementalPending
from genshin_engine.mechanics_entry_reference import (
    execute_mechanics as reference_execute,
)
from genshin_engine.mechanics_execution_context import MechanicsExecutionRequest
from genshin_engine.snapshot_capture import SnapshotCapturePending
from genshin_engine.special_reactions import SpecialReactionPending
from genshin_engine.target_state import TargetStatePending


def _run(values, matches=None, *, trace=None):
    return pipeline.execute_mechanics_pipeline(
        MechanicsExecutionRequest(*values, matches), mechanics._EXECUTION_SERVICES,
        pass_trace=trace)


def legacy_receipt(value):
    """The frozen entry predates R16's additive, separately tested count view.

    Retain every numerical, elemental, resource, source and error field in the
    independent comparison; only this new report is not in the old contract.
    """
    result = copy.deepcopy(value)
    result.pop('damage_execution_counts', None)
    if isinstance(result.get('result'), dict):
        result['result'].pop('damage_execution_counts', None)
    return result


@pytest.mark.parametrize("case", ["ordinary", "delayed", "freeze", "swirl", "invalid_schema", "unknown_owner", "bad_match"])
def test_complete_entry_projection_and_input_isolation_match_frozen_reference(case):
    values = (freeze_inputs() if case == "freeze" else swirl_inputs((.2, .3))
              if case == "swirl" else fixture((.2, .2, .4)))
    matches = None
    if case == "delayed":
        boost = rule(duration=.2)
        boost["timing"]["delay"] = .15
        add(values[2], boost)
    elif case == "invalid_schema":
        values[2]["schema_version"] = 999
    elif case == "unknown_owner":
        boost = rule()
        boost["owner"] = "not-in-party"
        add(values[2], boost)
    elif case == "bad_match":
        matches = [{"rule_id": "unknown", "effect_id": "unknown", "hit_id": "unknown", "operation": "include"}]
    original = copy.deepcopy((values, matches))
    expected = reference_execute(*copy.deepcopy(values), copy.deepcopy(matches))
    actual = _run(values, matches)
    assert legacy_receipt(actual) == expected
    assert (values, matches) == original


def test_actual_entry_has_one_ordered_pass_chain_and_no_product_debug_fields():
    values, trace = fixture(), []
    result = _run(values, trace=trace)
    assert legacy_receipt(result) == reference_execute(*values)
    assert [receipt.pass_id for receipt in trace] == [
        "basic-admission-v1", "source-admission-v1", "input-projection-v1",
        "binding-admission-v1", "rotation-semantics-v1", "event-expansion-v1",
        "event-reference-admission-v1", "runtime-construction-v1", "simulation-v1",
        "result-projection-v1",
    ]
    assert all(receipt.status == "completed" for receipt in trace)
    assert "pass_trace" not in result and "execution_pipeline" not in result


def test_invalid_basic_contract_returns_before_sources_or_tail_reports():
    values, trace = fixture(), []
    values[2]["schema_version"] = 999
    _run(values, trace=trace)
    assert [(receipt.pass_id, receipt.status) for receipt in trace] == [("basic-admission-v1", "returned")]


@pytest.mark.parametrize("stage", ["constructor", "simulation"])
def test_partial_owner_construction_and_runtime_fault_receipt_match_old_entry(monkeypatch, stage):
    values = fixture()

    def fault(*_args, **_kwargs):
        if stage == "constructor":
            raise ValueError("constructor fault")
        raise AttributeLedgerError("fixture_attribute_fault", "runtime fault")

    name = "_Mechanics" if stage == "constructor" else "_simulate"
    monkeypatch.setattr(mechanics, name, fault)
    monkeypatch.setitem(reference_execute.__globals__, name, fault)
    assert legacy_receipt(_run(copy.deepcopy(values))) == reference_execute(*copy.deepcopy(values))


@pytest.mark.parametrize("pending", [
    SnapshotCapturePending("fixture", "constructor pending", "capture"),
    ElementalPending("fixture", "constructor pending", "application"),
    SpecialReactionPending("fixture", "constructor pending", "reaction"),
    TargetStatePending("source.fixture"),
])
def test_preexisting_pending_constructor_fault_now_reports_original_issue_without_fake_state(monkeypatch, pending):
    values = fixture()
    original_issue = copy.deepcopy(pending.issue)

    def fault(*_args, **_kwargs):
        raise pending

    monkeypatch.setattr(mechanics, "_Mechanics", fault)
    monkeypatch.setitem(reference_execute.__globals__, "_Mechanics", fault)
    with pytest.raises(UnboundLocalError):
        reference_execute(*copy.deepcopy(values))
    result = _run(copy.deepcopy(values))
    assert result["status"] == "pending" and result["result"] is None
    assert result["resolved_template"] is None
    assert result["mechanics_trace"] == [] and result["sampling_trace"] == []
    assert "elemental_trace" not in result and "snapshot_capture_trace" not in result
    assert result["rotation_check"]["status"] == "not_checked"
    issue = result["issues"][0]
    assert {key: value for key, value in issue.items() if key != "details"} == {
        key: value for key, value in original_issue.items() if key != "details"}
    diagnostic = issue["details"].pop("mechanics_execution")
    assert issue.get("details", {}) == original_issue.get("details", {})
    assert diagnostic == {
        "phase": "preparation", "pass_id": "runtime-construction-v1",
        "executor_state": "not_initialized",
        "source": f"{type(pending).__module__}.{type(pending).__qualname__}",
    }
    assert pending.issue == original_issue


def test_unknown_constructor_program_error_is_still_raised(monkeypatch):
    def fault(*_args, **_kwargs):
        raise RuntimeError("program defect")

    monkeypatch.setattr(mechanics, "_Mechanics", fault)
    with pytest.raises(RuntimeError, match="program defect"):
        _run(fixture())


@pytest.mark.parametrize("name", ["validate_mechanics_model", "_attach_shared_pool_report"])
def test_pre_and_post_try_faults_are_not_absorbed_by_the_result_projector(monkeypatch, name):
    values = fixture()

    def fault(*_args, **_kwargs):
        raise ValueError("outside the former try scope")

    monkeypatch.setattr(mechanics, name, fault)
    monkeypatch.setitem(reference_execute.__globals__, name, fault)
    for execute in (_run, lambda values: reference_execute(*values)):
        with pytest.raises(ValueError, match="former try"):
            execute(copy.deepcopy(values))


def test_schema_diagnostics_are_collected_in_the_same_order_before_return(monkeypatch):
    seen = []
    values = fixture()

    def mechanics_validation(_model):
        seen.append("mechanics")
        return {"issues": [{"severity": "error", "code": "mechanics.first"}]}

    def rotation_validation(_model):
        seen.append("rotation")
        return {"issues": [{"severity": "error", "code": "rotation.second"}]}

    monkeypatch.setattr(mechanics, "validate_mechanics_model", mechanics_validation)
    monkeypatch.setattr(mechanics, "validate_rotation_model", rotation_validation)
    result = _run(values)
    assert seen == ["mechanics", "rotation"]
    assert [issue["code"] for issue in result["issues"]] == ["mechanics.first", "rotation.second"]


def test_reference_entry_does_not_use_a_corrupted_actual_pass(monkeypatch):
    values = fixture()
    expected = reference_execute(*copy.deepcopy(values))
    damaged = replace(pipeline._EXECUTION_PASSES[0], operation=lambda *_args: False)
    monkeypatch.setattr(pipeline, "_EXECUTION_PASSES", (damaged, *pipeline._EXECUTION_PASSES[1:]))
    assert _run(copy.deepcopy(values)) != expected
    assert reference_execute(*copy.deepcopy(values)) == expected
    assert reference_execute.__globals__ is not mechanics.execute_mechanics.__globals__
    assert "execute_mechanics_pipeline" not in reference_execute.__code__.co_names


def test_named_services_keep_late_owner_report_hooks(monkeypatch):
    values = fixture()
    original_simulate = mechanics._simulate
    seen = []

    def report(*_args):
        seen.append("report")

    def simulate(*args, **kwargs):
        result = original_simulate(*args, **kwargs)
        monkeypatch.setattr(mechanics, "_attach_shared_pool_report", report)
        return result

    monkeypatch.setattr(mechanics, "_simulate", simulate)
    _run(values)
    assert seen == ["report"]
