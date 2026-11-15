import copy

import pytest

from genshin_app.calculation_scope import validation_scope
from genshin_app.energy_trials import trial_energy_recharge
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_db.runtime_energy_generation import get_runtime_energy_generation_profile
from genshin_db.validation import QueryValidationError
from tests.test_calculation_overrides_execution import native_plan
from tests.test_energy_trials import plan as energy_plan


def test_action_status_is_not_steady_energy_summary():
    result = {"rotation_check": {"status": "unproven", "action_check": {"status": "passed"},
        "energy_check": {"status": "unproven", "opening_status": "passed"},
        "damage_interpretation": {"complete_first_cycle": True}}}
    scope = validation_scope({}, result)
    assert scope["actions"] == "passed"
    assert scope["energy_scope"]["supplied_window"] == "passed"
    assert scope["energy_scope"]["sustainable_loop"] == "unproven"
    result["rotation_check"]["damage_interpretation"] = {"kind": "executed_prefix", "complete_first_cycle": False}
    assert validation_scope({}, result)["energy_scope"]["supplied_window"] == "not_verified"
    assert validation_scope({})["actions"] == "not_checked"


@pytest.mark.parametrize("bad", [10000133, "11331", "ABC" * 21, {}, None])
def test_invalid_origin_is_request_error_before_database_read(tmp_path, bad):
    db = tmp_path / "does-not-exist.db"
    with pytest.raises(QueryValidationError) as error:
        get_runtime_energy_generation_profile(db, origin_reference_id=bad)
    assert error.value.reason == "invalid_query_request"
    assert "不是角色ID、技能ID" in str(error.value)
    assert not db.exists()


def test_v2_plain_calculates_but_window_is_explicit_preexecution_migration(tmp_path):
    plan = native_plan()
    plan["plan_version"] = 2
    del plan["mechanics_model"]
    result = run_plan_calc(plan, tmp_path / "absent.db")
    assert result["result"]["summary"]["total_damage"] == 45
    plan["rotation_model"]["damage_report"] = {"contract": "damage-report-window-v1", "mode": "explicit", "end": .2}
    before = copy.deepcopy(plan)
    with pytest.raises(PlanError) as error:
        run_plan_calc(plan, tmp_path / "absent.db")
    assert error.value.reason == "damage_report_requires_causal_execution"
    assert error.value.details["execution_state"] == "not_started"
    assert "enable-mechanics --plan" in error.value.details["recovery"]["cli"]
    assert plan == before
    migrated = enable_mechanics(plan)
    migrated["mechanics_model"] = native_plan()["mechanics_model"]
    actual = run_plan_calc(migrated, tmp_path / "absent.db")
    assert actual["result"]["damage_report_window"]["end"] == .2
    assert actual["result"]["summary"]["dps"] == 225


def test_trial_shape_error_provides_correct_envelope_before_execution(tmp_path):
    with pytest.raises(PlanError) as caught:
        trial_energy_recharge(energy_plan(), tmp_path / "absent.db", {"a": {"energy_recharge": 2}})
    assert caught.value.details["path"] == "/trials"
    assert caught.value.details["recovery"]["example"]["trials"][0]["stats"]
    assert not (tmp_path / "absent.db").exists()


def test_input_variants_execute_real_copies_and_keep_original(tmp_path):
    plan = energy_plan()
    before = copy.deepcopy(plan)
    result = trial_energy_recharge(plan, tmp_path / "absent.db", {"observations": [
        {"id": "hits", "source": {"resource": "result", "pointer": "/hits"}, "check": {"kind": "count", "min": 2}}], "trials": [
        {"id": "more-resource", "operations": [{"op": "replace", "path": "/rotation_model/events/0/amounts/a", "value": 5}]},
        {"id": "too-late", "operations": [{"op": "replace", "path": "/rotation_model/events/0/t", "value": 5}]}]})
    baseline, more, late = result["trials"]
    assert baseline["energy"]["bursts"][1]["before"] == 8
    assert more["energy"]["bursts"][1]["before"] == 10
    assert late["energy"]["bursts"][1]["before"] == 0
    assert more["observations"]["items"][0]["status"] == "observed_match"
    assert result["acceptance_effect"] == "none" and plan == before


def test_input_variant_cannot_write_derived_result_or_identity(tmp_path):
    result = trial_energy_recharge(energy_plan(), tmp_path / "absent.db", {"include_baseline": False, "trials": [
        {"id": "forged", "operations": [{"op": "replace", "path": "/revision", "value": 99}]}]})
    assert result["trials"][0]["status"] == "incomplete"
    assert result["trials"][0]["failure"]["reason"] == "agent_patch_forbidden"


def test_trial_observation_failure_cannot_erase_actual_energy_result(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("synthetic unavailable observation")
    monkeypatch.setattr("genshin_app.energy_trials.inspect_calculation_assistance", broken)
    result = trial_energy_recharge(energy_plan(), tmp_path / "absent.db", {"include_baseline": False,
        "trials": [{"id": "more", "stats": {"a": {"energy_recharge": 2}}}],
        "observations": [{"id": "hits", "source": {"resource": "result", "pointer": "/hits"},
                          "check": {"kind": "count", "min": 1}}]})
    assert result["trials"][0]["status"] == "tested"
    assert result["trials"][0]["energy"]["bursts"][1]["before"] == 10
    assert result["trials"][0]["observations"]["state"] == "observation_unavailable"
