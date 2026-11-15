"""Frozen Composer envelopes and terminal admission ordering during C1-2.

The complete-report digests were captured from the accepted pre-extraction
Composer, then compared against both implementations before deleting the old
branch. They include optional keys, ordered diagnostics and nested receipts.
"""

from __future__ import annotations

import copy
import importlib
from contextlib import contextmanager
from dataclasses import replace

import pytest

from genshin_app.equipment import composer
from genshin_app.equipment.compose_admission import finalize_compose_admission
from genshin_app.equipment.schema import PlanError
from tests.compose_report_oracle import assert_pre_extraction_envelope
from tests.test_equipment_determinism_regression import _plan
from tests.test_equipment_plan import database as database  # noqa: PLC0414
from tests.test_equipment_plan import plan_fixture


@pytest.mark.parametrize(("case", "state", "expected"), [
    ("manual", "ready", "43939df0e19fee53a51af03fb428c44bd778c306d067d979d5c6c31b3c912e9a"),
    ("database", "ready", "3df59b219b404168e96e3d93119cd6662cddb87f59e4f0c3d14feab4394bbc6d"),
    ("pending", "pending", "fd540fdee34e083b111588034f685bdf8c214393b77c9cbe67e445c7828e4b43"),
])
def test_complete_report_matches_accepted_pre_extraction_envelope(database, case, state, expected, monkeypatch):
    db, _ = database
    plan = plan_fixture() if case == "database" else _plan()
    if case == "pending":
        plan["manual_template"]["hits"][0]["damage_type_ids"] = None
    before = copy.deepcopy(plan)
    report = composer.compose_equipment_plan(plan, db)
    assert report["state"] == state
    assert_pre_extraction_envelope(report, plan, db, expected, monkeypatch)
    assert plan == before
    assert (report["plan"]["resolved_template"] is None) == (state == "pending")


@pytest.mark.parametrize("change", ["policy", "numeric", "derived_identity"])
def test_historical_envelope_projection_rejects_unapproved_changes(database, monkeypatch, change):
    plan = _plan()
    report = composer.compose_equipment_plan(plan, database[0])
    if change == "policy":
        report["plan"]["direct_energy_policy"] = "unknown-policy"
    elif change == "numeric":
        report["plan"]["resolved_template"]["hits"][0]["multiplier"] = 99
    else:
        report["calculation_override_basis"]["engine_contract_fingerprint"] = "0" * 64
    with pytest.raises(AssertionError):
        assert_pre_extraction_envelope(
            report, plan, database[0],
            "43939df0e19fee53a51af03fb428c44bd778c306d067d979d5c6c31b3c912e9a", monkeypatch)


@pytest.fixture
def admission_inputs(database, monkeypatch):
    captured = []

    def capture(inputs):
        captured.append(inputs)
        return finalize_compose_admission(inputs)

    monkeypatch.setattr(composer, "finalize_compose_admission", capture)
    composer.compose_equipment_plan(_plan(), database[0])
    assert len(captured) == 1
    return captured[0]


def test_admission_does_not_mutate_the_compiled_graph(admission_inputs):
    inputs = admission_inputs
    before = copy.deepcopy((inputs.original_plan, inputs.plan, inputs.template,
                           inputs.mechanics_model, inputs.mechanics_matches,
                           inputs.issues, inputs.sources, inputs.versions))
    decision = finalize_compose_admission(inputs)
    assert decision.state == "ready"
    assert (inputs.original_plan, inputs.plan, inputs.template,
            inputs.mechanics_model, inputs.mechanics_matches,
            inputs.issues, inputs.sources, inputs.versions) == before


def test_consumer_conflict_precedes_later_capacity_or_audit_failures(admission_inputs, monkeypatch):
    from genshin_app.equipment import action_automation_audit, action_capacities

    def unexpected(*_args, **_kwargs):
        pytest.fail("later finalizers must not run after an earlier admission error")

    monkeypatch.setattr(action_capacities, "verify_final_pools", unexpected)
    monkeypatch.setattr(action_automation_audit, "build_action_automation_audit", unexpected)
    common = {"code": "missing_input", "path": "hit.a", "message": "fixture",
              "consumer": "same", "required": True}
    inputs = replace(admission_inputs, capacity_compilations=[{}], issues=[
        {**common, "consumed": True}, {**common, "consumed": False},
    ])
    with pytest.raises(PlanError) as caught:
        finalize_compose_admission(inputs)
    assert caught.value.reason == "task_capability_consumer_conflict"
    assert caught.value.details == {"consumer": "same", "states": [(True, True), (True, False)]}


def test_materialization_verification_precedes_capacity(admission_inputs, monkeypatch):
    from genshin_app.equipment import action_capacities

    def unexpected(*_args, **_kwargs):
        pytest.fail("capacity validation must follow execution receipt validation")

    monkeypatch.setattr(action_capacities, "verify_final_pools", unexpected)
    inputs = replace(admission_inputs, capacity_compilations=[{}],
                     agent_materialization_receipt={"status": "ready"})
    with pytest.raises(PlanError) as caught:
        finalize_compose_admission(inputs)
    assert caught.value.reason == "agent_materialization_receipt_incomplete"


def test_capacity_failure_precedes_automation_audit(admission_inputs, monkeypatch):
    from genshin_app.equipment import action_automation_audit, action_capacities
    from genshin_app.equipment.energy_policy import compile_current_energy_rotation

    failure = PlanError("capacity fixture", "capacity_fixture_failure", {"stage": "capacity"})
    seen = []

    def fail_capacity(original_plan, rotation, records):
        seen.append((original_plan, rotation, records))
        raise failure

    def unexpected(*_args, **_kwargs):
        pytest.fail("automation audit must not run after capacity validation fails")

    monkeypatch.setattr(action_capacities, "verify_final_pools", fail_capacity)
    monkeypatch.setattr(action_automation_audit, "build_action_automation_audit", unexpected)
    rotation = compile_current_energy_rotation({"fixture": True})
    inputs = replace(admission_inputs, capacity_compilations=[{"fixture": True}],
                     plan={**admission_inputs.plan, "rotation_model": rotation})
    with pytest.raises(PlanError) as caught:
        finalize_compose_admission(inputs)
    assert caught.value is failure
    assert len(seen) == 1
    assert seen[0][0] is inputs.original_plan
    assert seen[0][1] is inputs.plan["rotation_model"]
    assert seen[0][2] is inputs.capacity_compilations


def test_report_projector_consumes_the_same_finalized_decision_once(database, monkeypatch):
    events = []
    decision = None
    real_projector = composer.project_equipment_report

    def finalize(inputs):
        nonlocal decision
        events.append("admission")
        decision = finalize_compose_admission(inputs)
        return decision

    def project(**inputs):
        events.append("projection")
        assert inputs["state"] == decision.state
        assert inputs["resolution"] is decision.resolution
        return real_projector(**inputs)

    monkeypatch.setattr(composer, "finalize_compose_admission", finalize)
    monkeypatch.setattr(composer, "project_equipment_report", project)
    report = composer.compose_equipment_plan(_plan(), database[0])
    assert report["state"] == "ready"
    assert events == ["admission", "projection"]


def test_production_pipeline_records_a_separate_admission_pass(database, monkeypatch):
    real_runner = composer.run_compose_pipeline
    traces = []

    def run(request, passes):
        report, trace = real_runner(request, passes)
        traces.append(trace)
        return report, trace

    monkeypatch.setattr(composer, "run_compose_pipeline", run)
    report = composer.compose_equipment_plan(_plan(), database[0])
    assert len(traces) == 1
    assert [receipt.pass_id for receipt in traces[0].receipts] == [
        "input-context-v1", "action-preparation-v1", "loadout-resolution-v1",
        "source-compilation-v1", "mechanics-baseline-v1", "agent-execution-overlay-v1",
        "post-materialization-finalizers-v1", "calculation-override-v1",
        "effective-graph-validation-v1", "compilation-envelope-v1",
        "admission-audit-finalizer-v1", "report-projector-v1",
    ]
    assert traces[0].receipts[10].required_artifacts == ("compiled_artifacts",)
    assert traces[0].receipts[10].produced_artifacts == ("report_projection_inputs",)
    assert "trace" not in report


def test_failed_admission_never_runs_the_projector_or_changes_caller_input(database, monkeypatch):
    failure = PlanError("admission fixture", "admission_fixture_failure")
    plan = _plan()
    before = copy.deepcopy(plan)

    def fail(_inputs):
        raise failure

    def unexpected(**_inputs):
        pytest.fail("a failed admission cannot produce a public report")

    monkeypatch.setattr(composer, "finalize_compose_admission", fail)
    monkeypatch.setattr(composer, "project_equipment_report", unexpected)
    with pytest.raises(PlanError) as caught:
        composer.compose_equipment_plan(plan, database[0])
    assert caught.value is failure
    assert plan == before


def test_action_preparation_keeps_exact_compiler_order_and_runs_each_once(database, monkeypatch):
    calls = []
    compilers = [
        ("action_admissions", "compile_action_admissions"),
        ("action_resources", "compile_action_resources"),
        ("action_capacities", "compile_action_capacities"),
        ("action_dispatch_sources", "prepare_action_dispatch_sources"),
        ("attack_metadata", "prepare_attack_metadata"),
        ("periodic_sources", "prepare_periodic_sources"),
        ("hit_parameters", "compile_hit_parameters"),
    ]
    for module_name, name in compilers:
        module = importlib.import_module(f"genshin_app.equipment.{module_name}")
        real = getattr(module, name)

        def wrap(*args, _real=real, _name=name, **kwargs):
            calls.append(_name)
            return _real(*args, **kwargs)

        monkeypatch.setattr(module, name, wrap)
    composer.compose_equipment_plan(_plan(), database[0])
    assert calls == [name for _, name in compilers]


@pytest.mark.parametrize("operation", ["compose", "refresh"])
def test_all_passes_reuse_the_single_facade_database_read_scope(database, monkeypatch, operation):
    from genshin_db import queries

    real_scope = queries.equipment_read_session
    entered, depth = [], []

    @contextmanager
    def read_scope(db_path):
        entered.append(db_path)
        assert not depth, "a pass must not open a second live database transaction"
        depth.append(db_path)
        try:
            with real_scope(db_path) as session:
                yield session
        finally:
            depth.pop()

    monkeypatch.setattr(queries, "equipment_read_session", read_scope)
    fn = composer.compose_equipment_plan if operation == "compose" else composer.apply_data_update
    report = fn(plan_fixture(), database[0])
    assert report["state"] == "ready"
    assert entered == [database[0]]
    assert depth == []
