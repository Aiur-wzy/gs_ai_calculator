"""Public completion proof tests; actual queue replay preserves unknown facts."""
import copy
from types import SimpleNamespace

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.energy_completion_receipt import FIELD
from genshin_app.equipment.energy_delivery_receipt import (
    make_automatic_energy_receipt,
    validate_automatic_energy_receipt,
    validate_automatic_energy_result_receipt,
)
from genshin_app.equipment.execution_verification import (
    ExecutionVerificationError,
    ExecutionVerificationSession,
    verification_diagnostic,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import build_formula_workbook
from genshin_engine.mechanics import execute_mechanics
from tests.automatic_energy_completion_helpers import energy_case
from tests.test_mechanics_execution import condition, fixture
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)

# ruff: noqa: F811


def calculate(db, proof=False):
    plan, _, _ = energy_case(db, proof=proof)
    return run_plan_calc(plan, db)


def resign(data):
    result, receipt = data["result"], data["result_receipt"]
    proof = receipt["action_resources"]["automatic_energy_delivery"]
    check = result["rotation_check"]
    proof["audit_fingerprint"] = fingerprint([
        row for row in check["event_log"] if row.get("delivery") == "automatic_action_start"])
    proof["summary_fingerprint"] = fingerprint(check["automatic_energy_delivery"])
    receipt["result_fingerprint"] = fingerprint(result)


@pytest.mark.parametrize("early", [False, True])
def test_fresh_saved_and_full_completed_pending_remains_pending(resource_db, early, monkeypatch):
    data = calculate(resource_db, early)
    result, receipt = data["result"], data["result_receipt"]
    original = copy.deepcopy(data)
    check = result["rotation_check"]
    assert result["mechanics_status"] == check["status"] == "pending"
    assert check["energy_check"]["proven_cycle"] is None
    assert any(row["code"] == "unknown_condition" for row in result["mechanics_issues"])
    assert FIELD in result and receipt["result_fingerprint"] == fingerprint(result)
    assert "mechanism_compilations" not in result and "snapshot_capture_execution" not in result
    assert check["automatic_energy_delivery"].get("termination", {}).get("kind") == (
        "cycle_proof" if early else None)
    monkeypatch.setattr("sqlite3.connect", lambda *a, **kw: pytest.fail("offline completion read DB"))
    with ExecutionVerificationSession() as session:
        validate_automatic_energy_result_receipt(result, receipt, _session=session)
        validate_automatic_energy_receipt(data["plan"], result, data["report"], receipt, _session=session)
        assert result_bytes(result, receipt, "json", _session=session)
        assert session.report()["usage"]["executions"] == 1
        assert session.report()["usage"]["cache_hits"] == 2
    review_calculation({"result": result, "result_receipt": receipt}, {"view": "actions"})
    assert build_formula_workbook(data)
    assert result_bytes(result, receipt, "xlsx")
    assert data == original


@pytest.mark.parametrize("kind", [
    "missing_execution", "missing_proof", "unknown_version", "extra_execution_key", "extra_proof_key",
    "execution_list", "wrong_fingerprint", "empty_cycles", "checked_bool", "empty_boundaries",
    "changed_boundary", "missing_action", "missing_audit", "status_promotion", "diagnostics_removed",
    "changed_input", "changed_matches", "changed_rotation", "removed_termination", "changed_termination",
    "missing_status", "missing_issues",
])
def test_rehashed_false_completion_is_rejected_by_export_and_saved_review(resource_db, kind):
    data = calculate(resource_db, True)
    result, receipt = data["result"], data["result_receipt"]
    check = result["rotation_check"]
    proof = receipt["action_resources"]["automatic_energy_delivery"]
    if kind == "missing_execution":
        result.pop(FIELD)
    elif kind == "missing_proof":
        proof.pop("completion")
    elif kind == "unknown_version":
        proof["completion"]["contract"] = "unknown"
    elif kind == "extra_execution_key":
        result[FIELD]["trusted"] = True
    elif kind == "extra_proof_key":
        proof["completion"]["trusted"] = True
    elif kind == "execution_list":
        result[FIELD] = []
    elif kind == "wrong_fingerprint":
        proof["completion"]["execution_fingerprint"] = "0" * 64
    elif kind == "empty_cycles":
        check["cycles"] = []
    elif kind == "checked_bool":
        check["energy_check"]["checked_cycles"] = True
    elif kind == "empty_boundaries":
        for cycle in check["cycles"]:
            cycle["boundary_state"] = {}
    elif kind == "changed_boundary":
        check["cycles"][-1]["boundary_state"]["frontline"] = "fake"
    elif kind == "missing_action":
        check["event_log"] = [row for row in check["event_log"] if row["type"] != "action"]
    elif kind == "missing_audit":
        check["event_log"] = [row for row in check["event_log"] if row.get("delivery") != "automatic_action_start"]
    elif kind == "status_promotion":
        result["mechanics_status"] = "passed"
    elif kind == "diagnostics_removed":
        result["mechanics_issues"] = []
    elif kind == "missing_status":
        result.pop("mechanics_status")
    elif kind == "missing_issues":
        result.pop("mechanics_issues")
    elif kind in ("changed_input", "changed_matches", "changed_rotation"):
        execution = result[FIELD]
        if kind == "changed_input":
            execution["mechanics_model"]["condition_events"][0]["value"] = False
        elif kind == "changed_matches":
            execution["matches"] = [{"rule_id": "binding:part", "effect_id": "value", "hit_id": "h0", "operation": "exclude"}]
        else:
            execution["rotation_model"]["initial_frontline"] = "fake"
        proof["completion"]["execution_fingerprint"] = fingerprint(execution)
        check["input_fingerprint"] = fingerprint(execution)
    elif kind == "removed_termination":
        check["automatic_energy_delivery"].pop("termination")
    else:
        check["automatic_energy_delivery"]["termination"]["checked_cycles"] = 2
    resign(data)
    with pytest.raises(ValueError):
        validate_automatic_energy_result_receipt(result, receipt)
    with pytest.raises(ValueError):
        result_bytes(result, receipt, "json")
    with pytest.raises(ValueError):
        review_calculation({"result": result, "result_receipt": receipt}, {"view": "summary"})


@pytest.mark.parametrize("field", ["template", "mechanics", "matches", "rotation", "panel"])
def test_full_report_must_bind_the_same_execution(resource_db, field):
    data = calculate(resource_db)
    report, result = data["report"], data["result"]
    if field == "template":
        report["mechanics_input_template"]["characters"][0]["stats"]["atk"] += 1
    elif field == "mechanics":
        report["mechanics_model"]["condition_events"][0]["value"] = False
    elif field == "matches":
        report["mechanics_matches"].append({"nonsense": True})
    elif field == "rotation":
        report["compiled_rotation_model"]["initial_frontline"] = "fake"
    else:
        result["rotation_check"]["panel_bindings"][0]["value"] += 1
        resign(data)
    with pytest.raises(ValueError):
        validate_automatic_energy_receipt(data["plan"], result, report, data["result_receipt"])


def test_real_future_mechanism_event_prevents_early_proof():
    template, rotation, model = fixture((.2,))
    rotation["max_cycles"] = 10
    condition(model, "late", 5, order=20)
    model["condition_events"][0]["repeat"] = {"mode": "cycles", "cycles": [8]}
    result = execute_mechanics(template, rotation, model)
    check = result["rotation_check"]
    assert check["energy_check"]["checked_cycles"] == 10
    assert any(row["id"] == "late" and row["t"] == 85 for row in check["event_log"])


@pytest.mark.parametrize("kind", ["execution", "proof", "wrong_status", "empty_execution"])
def test_completion_residues_are_not_ignored(resource_db, kind):
    data = calculate(resource_db)
    result, receipt = data["result"], data["result_receipt"]
    if kind == "execution":
        result.pop("action_resource_compilations")
        receipt.pop("action_resources")
        result["rotation_check"] = {}
    elif kind == "proof":
        result.pop(FIELD)
        result["rotation_check"]["status"] = "passed"
    elif kind == "wrong_status":
        result["rotation_check"]["status"] = "passed"
    else:
        result[FIELD] = None
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_automatic_energy_result_receipt(result, receipt)


def test_preparation_limits_precede_copy_and_leave_no_residue(resource_db, monkeypatch):
    data = calculate(resource_db)
    result = copy.deepcopy(data["result"])
    result.pop(FIELD)
    before = copy.deepcopy(result)
    monkeypatch.setattr("genshin_app.equipment.energy_completion_receipt.copy",
                        SimpleNamespace(deepcopy=lambda *a, **kw: pytest.fail("copied before input limit")))
    with ExecutionVerificationSession(budget={
            "policy": "execution-verification-budget-v2", "limits": {"input_nodes": 5}}) as session:
        with pytest.raises(ExecutionVerificationError) as caught:
            make_automatic_energy_receipt(data["plan"], data["report"], result, _session=session)
        assert caught.value.code == "verification_input_nodes"
        assert session.report()["usage"]["executions"] == 0
        assert session._cache == []
    assert result == before


@pytest.mark.parametrize("entry", ["result", "full", "export", "review", "formula"])
def test_completion_budget_cannot_silently_skip_replay(resource_db, monkeypatch, entry):
    data = calculate(resource_db)
    original = copy.deepcopy(data)
    result, receipt = data["result"], data["result_receipt"]
    with ExecutionVerificationSession(budget={
            "policy": "execution-verification-budget-v2", "limits": {"executions": 0}}) as session:
        calls = {
            "result": lambda: validate_automatic_energy_result_receipt(result, receipt, _session=session),
            "full": lambda: validate_automatic_energy_receipt(data["plan"], result, data["report"], receipt, _session=session),
            "export": lambda: result_bytes(result, receipt, "json", _session=session),
            "review": lambda: review_calculation({"result": result, "result_receipt": receipt}, {}, _session=session),
            "formula": lambda: build_formula_workbook(data, _session=session),
        }
        with pytest.raises(ValueError) as caught:
            calls[entry]()
        diagnostic = verification_diagnostic(caught.value)
        assert diagnostic["issues"][0]["dimension"] == "executions"
        assert diagnostic["issues"][0]["limit"] == 0
        assert session.report()["stopped"]
        assert session.report()["usage"]["executions"] == 0
        assert session._cache == []
    assert data == original
