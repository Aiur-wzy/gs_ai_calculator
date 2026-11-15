"""Soft conditions use actual rows, retain unknowns and never change input."""
import copy

import pytest

from genshin_app.calculation_assistance import inspect_calculation_assistance
from genshin_app.declared_observations import observe_declared
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.schema import PlanError
from tests.test_calculation_overrides_execution import native_plan


def request(check, **extra):
    return {"id": "declared", "source": {"resource": "result", "pointer": "/rows"}, "check": check, **extra}


def observe(row, values, complete=True):
    return observe_declared([row], plan={}, result={"rows": values,
        "rotation_check": {"damage_interpretation": {"complete_first_cycle": complete}}})["items"][0]


def test_group_count_finds_missing_group_and_returns_actual_evidence():
    row = request({"kind": "count", "min": 1}, group_by=["/group"], expected_groups=[["one"], ["two"]],
        where=[{"path": "/reaction", "op": "eq", "value": "cryo-swirl"}])
    result = observe(row, [{"group": "one", "reaction": "cryo-swirl"}, {"group": "two", "reaction": "none"}])
    assert result["status"] == "observed_difference"
    assert result["groups"][0]["actual_count"] == 1
    assert result["groups"][0]["samples"] == ["/rows/0"]
    assert result["groups"][1]["status"] == "not_observed_in_complete_window"
    assert observe(row, [], complete=False)["status"] == "unknown"


def test_future_settlement_and_recipient_condition_are_declared_not_inferred():
    result = observe(request({"kind": "compare", "left": "/settled_at", "op": "lte", "right": "/damage_at"}),
                     [{"settled_at": 4, "damage_at": 3}, {"settled_at": 4, "damage_at": 5}])
    assert result["status"] == "observed_difference"
    assert result["groups"][0]["counterexamples"][0]["left"] == 4
    assert result["groups"][0]["samples"][0]["source_ref"] == "/rows/1"
    recipient = request({"kind": "compare", "left": "/recipient", "op": "ne", "right": "/provider"})
    assert observe(recipient, [{"recipient": "owner", "provider": "owner"}])["status"] == "observed_difference"
    assert observe(recipient, [{"recipient": "other"}])["status"] == "unknown"


def test_shared_cd_observation_groups_exact_sources_and_skips_no_game_rules():
    row = request({"kind": "min_gap", "time": "/t", "seconds": 1}, group_by=["/source"])
    result = observe(row, [{"source": "first", "t": 1}, {"source": "second", "t": 1.05},
                          {"source": "first", "t": 1.1}, {"source": "second", "t": 3}])
    assert result["groups"][0]["minimum_observed_gap"] == pytest.approx(.1)
    assert result["groups"][0]["status"] == "observed_difference"
    assert result["groups"][1]["status"] == "observed_match"
    assert result["acceptance_effect"] == "none"


def test_replacement_duplicate_uses_explicit_identity_components():
    row = request({"kind": "unique", "fields": ["/action", "/component", "/target", "/t"]})
    actual = [{"action": "a", "component": "ray", "target": "enemy", "t": 1, "kind": kind}
              for kind in ["normal", "special"]]
    result = observe(row, actual)
    assert result["groups"][0]["violation_count"] == 1
    assert result["groups"][0]["counterexamples"][0]["previous_ref"] == "/rows/0"


def test_missing_fields_are_not_false_or_vacuous_success():
    row = request({"kind": "count", "min": 1}, where=[{"path": "/frontline", "op": "eq", "value": True}])
    result = observe(row, [{"t": 1}])
    assert result["status"] == "unknown" and result["unknown_rows"] == 1
    assert observe(request({"kind": "unique", "fields": ["/id"]}), [])["status"] == "unknown"
    # An upper bound is not proved from only a prefix. A known excess, however,
    # remains a counterexample even when other rows are incomplete.
    row["check"] = {"kind": "count", "max": 1}
    assert observe(row, [{"frontline": True}], complete=False)["status"] == "unknown"
    assert observe(row, [{"frontline": True}, {"frontline": True}, {}], complete=False)["status"] == "observed_difference"


def test_group_pagination_does_not_hide_overall_counterexample():
    row = request({"kind": "count", "min": 1}, group_by=["/group"], expected_groups=[["ok"], ["missing"]])
    result = observe_declared([row], plan={}, result={"rows": [{"group": "ok"}],
        "rotation_check": {"damage_interpretation": {"complete_first_cycle": True}}}, limit=1)["items"][0]
    assert result["status"] == "observed_difference" and result["group_count"] == 2
    assert result["next_offset"] == 1 and len(result["groups"]) == 1


def test_declared_input_empty_list_is_known_zero_not_missing_execution():
    row = request({"kind": "count", "min": 0, "max": 0})
    row["source"]["resource"] = "plan"
    result = observe_declared([row], plan={"rows": []}, result={})["items"][0]
    assert result["status"] == "observed_match"
    assert result["groups"][0]["actual_count"] == 0
    assert result["evidence_scope"] == "declared_input_only"
    assert result["complete_execution_window"] is False
    assert result["source_scope_complete"] is True


def test_real_calculation_observation_leaves_receipts_and_admission_unchanged(tmp_path):
    plan = native_plan()
    actual = run_plan_calc(plan, tmp_path / "absent.db")
    before = copy.deepcopy(actual)
    row = request({"kind": "count", "min": 2})
    row["source"]["pointer"] = "/hits"
    output = inspect_calculation_assistance(actual, {"observations": [row]})
    assert output["declared_observations"]["items"][0]["status"] == "observed_difference"
    assert actual == before
    # A soft unmet author expectation never changes formal calculation evidence.
    assert actual["result_receipt"] == before["result_receipt"]


@pytest.mark.parametrize("check", [
    {"kind": "python", "code": "raise Exception()"},
    {"kind": "compare", "left": "/x", "op": "eq", "right": "/y", "value": 1},
    {"kind": "min_gap", "time": "/t", "seconds": -1},
])
def test_unknown_observation_language_is_request_error(check):
    with pytest.raises(PlanError) as caught:
        observe(request(check), [])
    assert caught.value.reason == "invalid_observation_request"
    assert caught.value.details["execution_state"] == "not_started"


@pytest.mark.parametrize("mutate", [
    lambda row: row["check"].update(kind=[]),
    lambda row: row["source"].update(resource={}),
    lambda row: row.update(where=[{"path": "/x", "op": [], "value": 1}]),
    lambda row: row.update(where=[{"path": "/x", "op": "eq", "value": float("nan")}]),
])
def test_malformed_values_never_escape_as_raw_type_errors(mutate):
    row = request({"kind": "count", "min": 1})
    mutate(row)
    with pytest.raises(PlanError) as caught:
        observe(row, [])
    assert caught.value.reason == "invalid_observation_request"
