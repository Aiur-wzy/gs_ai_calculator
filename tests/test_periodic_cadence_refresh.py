"""Compact periodic application cadence and replacement integration."""
# ruff: noqa: F811
from __future__ import annotations

import copy

import pytest
from test_mechanics_execution import EVIDENCE
from test_periodic_actions import confirm_periodic, periodic_application_mode
from test_periodic_application_v2 import dynamic_plan
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_action_dispatch import (
    _bundle,
    _compile,
    _emission,
    _profile,
    _resolved,
)
from test_runtime_periodic_attack import periodic_db  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.action_event_schema import _check
from genshin_app.equipment.periodic_receipt import validate_periodic_receipt
from genshin_app.equipment.periodic_schema import APPLICATION_PROFILE
from genshin_app.equipment.schema import PlanError
from genshin_db.action_dispatch_schema import (
    ActionDispatchProfileError,
    validate_bundle,
)

CADENCE = {
    "kind": "every_n_landed_hits",
    "period_hits": 2,
    "first_attempt_ordinal": 1,
    "scope": "channel_generation",
    "status": "confirmed",
    "source": "synthetic reviewed cadence fixture",
}


def _cadence_plan(db, *, mode="no_icd"):
    plan = periodic_application_mode(dynamic_plan(db), mode)
    plan["action_event_profiles"][0]["channels"][0]["application_cadence"] = copy.deepcopy(CADENCE)
    return plan


def _applications(output, profile_id="slice.app"):
    return [row for row in output["result"]["elemental"]["trace"]
            if row["type"] == "elemental_application" and row["profile_id"] == profile_id]


def test_compact_every_two_landed_hits_generates_gate_without_losing_damage(periodic_db):
    plan = confirm_periodic(_cadence_plan(periodic_db), periodic_db)
    output = run_plan_calc(plan, periodic_db)
    record = output["result"]["action_event_compilations"][0]

    assert len(output["result"]["hits"]) == 15
    assert record["stats"]["generated_hits"] == 15
    # 4 slice contacts per generation: positions 1 and 3 attempt.  The
    # uncadenced terminal channel still attempts once per generation.
    assert record["stats"]["generated_application_attempts"] == 9
    assert record["stats"]["planned_application_attempts"] == 9
    assert record["stats"]["cancelled_application_attempts"] == 0
    assert record["stats"]["application_gated_hits"] == 6
    assert [row["application_attempted"] for row in _applications(output)] == [
        True, False, True, False,
    ] * 3
    assert [row["reason"] for row in _applications(output)] == [
        "no_icd", "application_schedule_blocked", "no_icd", "application_schedule_blocked",
    ] * 3


def test_miss_does_not_advance_landed_cadence_position(periodic_db):
    plan = _cadence_plan(periodic_db)
    plan["action_event_overrides"] = [{
        "kind": "calibrated_multi_application_v2", "action_id": "q0",
        "contacts": [{"channel_id": "slice", "ordinal": 2, "landed": False, **EVIDENCE}],
    }]
    output = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    first_generation = _applications(output)[:4]
    assert [row["application_attempted"] for row in first_generation] == [True, False, False, True]
    assert [row["reason"] for row in first_generation] == [
        "no_icd", "missed", "application_schedule_blocked", "no_icd",
    ]
    generated = output["result"]["action_event_compilations"][0]["generated"]
    first_slice = [row for row in generated if row["action_id"] == "q0" and row["channel_id"] == "slice"]
    assert [row["application_cadence_position"] for row in first_slice] == [1, None, 2, 3]


def test_replacement_restarts_gate_but_keeps_shared_icd_continuous(periodic_db):
    plan = dynamic_plan(periodic_db)
    rotation = plan["rotation_model"]
    rotation["period"] = 3
    plan["manual_template"]["loop_duration"] = 3
    rotation["actions"] = rotation["actions"][:2]
    rotation["actions"][1].update(start=.6, occupation_end=.75)
    rotation["events"] = rotation["events"][:2]
    rotation["events"][1].update(t=.6)
    profile = plan["action_event_profiles"][0]
    profile["lifecycle"].update(
        no_refresh_replacement_or_early_despawn=False,
        replacement_policy="replace_previous",
        no_early_despawn=True,
    )
    profile["channels"][0]["application_cadence"] = copy.deepcopy(CADENCE)

    try:
        output = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    except PlanError as exc:  # keep the actual engine reason visible on regressions
        pytest.fail(repr(exc.details.get("issues", exc.details)))
    record = output["result"]["action_event_compilations"][0]
    assert record["replacements"][0]["replacement_time_ticks"] == 700000
    assert record["replacements"][0]["cancelled_hits"] == 3
    assert record["replacements"][0]["cancelled_application_attempts"] == 2
    assert record["stats"]["cancelled_application_attempts"] == 2
    old_slice = [row for row in record["generated"]
                 if row["action_id"] == "q0" and row["channel_id"] == "slice"]
    new_slice = [row for row in record["generated"]
                 if row["action_id"] == "q1" and row["channel_id"] == "slice"]
    assert [row["application_cadence_position"] for row in old_slice] == [1, 2]
    assert new_slice[0]["application_cadence_position"] == 1
    assert new_slice[0]["application_attempted"] is True

    rows = _applications(output)
    first_old = next(row for row in rows if row["action_id"] == "q0" and row["application_attempted"])
    first_new = next(row for row in rows if row["action_id"] == "q1" and row["application_attempted"])
    assert first_old["icd_audit"]["attempt_number"] == 1
    assert first_new["icd_audit"]["attempt_number"] == 2
    assert first_new["reason"] == "icd_blocked"


def test_dispatch_profile_compiles_one_compact_cadence_into_native_channel():
    emission = _emission("storm", kind="periodic", source_kind="periodic_attack", application="no_icd")
    emission["application"]["cadence"] = {
        "kind": "every_n_landed_hits", "period_hits": 2,
        "first_attempt_ordinal": 1, "scope": "channel_generation",
    }
    profile = _profile(emission)
    validate_bundle(_bundle(profile))
    compiled = _compile(_resolved(profile))
    assert compiled["status"] == "ready", compiled["issues"]
    native = compiled["fragments"]["action_event_profiles"][0]
    assert not _check(native, APPLICATION_PROFILE, "profile")
    assert native["channels"][0]["application_cadence"] == {
        **emission["application"]["cadence"],
        "status": "confirmed", "source": "action_dispatch_profile:fixture.dispatch",
    }


@pytest.mark.parametrize("mutation", ["phase", "single", "no_application", "pending"])
def test_invalid_or_unverified_compact_cadence_is_rejected(periodic_db, mutation):
    if mutation in ("phase", "single"):
        emission = _emission("storm", kind="periodic", source_kind="periodic_attack", application="no_icd")
        emission["application"]["cadence"] = {
            "kind": "every_n_landed_hits", "period_hits": 2,
            "first_attempt_ordinal": 3 if mutation == "phase" else 1,
            "scope": "channel_generation",
        }
        if mutation == "single":
            emission["kind"] = "fixed"
            emission["schedule"] = {"offset_seconds": .2, "precision_seconds": .01}
        with pytest.raises(ActionDispatchProfileError):
            validate_bundle(_bundle(_profile(emission)))
        return

    plan = _cadence_plan(periodic_db, mode="no_application" if mutation == "no_application" else "no_icd")
    if mutation == "pending":
        plan["action_event_profiles"][0]["channels"][0]["application_cadence"]["status"] = "pending"
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, periodic_db)


def test_cadence_materialization_tamper_invalidates_offline_receipt(periodic_db):
    output = run_plan_calc(confirm_periodic(_cadence_plan(periodic_db), periodic_db), periodic_db)
    output["result"]["action_event_compilations"][0]["generated"][0]["application_attempted"] = False
    with pytest.raises(ValueError):
        validate_periodic_receipt(output["plan"], output["result"], output["report"], output["result_receipt"])
