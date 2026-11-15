"""One capture resolver serves calibrated single and source-backed multi dispatch."""
import copy

import pytest
from test_action_events import (  # noqa: F401
    action_event_plan,
    action_events_db,
    confirm_action_events,
)
from test_periodic_actions import confirm_periodic, periodic_plan
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_periodic_attack import periodic_db  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.snapshot_capture_receipt import (
    validate_snapshot_capture_receipt,
    validate_snapshot_capture_result_receipt,
)

# ruff: noqa: F811


def with_captures(plan, offset=.1):
    plan["mechanics_model"]["snapshot_captures"] = [
        {"id": f"capture.{a['id']}", "action_id": a["id"], "offset": offset,
         "precision": .000001, "status": "assumed", "source": "Synthetic axis calibration; not measured game timing"}
        for a in plan["rotation_model"]["actions"]
    ]
    for profile in plan["action_event_profiles"]:
        for unit in profile.get("channels", [profile]):
            unit["self_sampling"] = "action_capture"
    return plan


def verify_capture_output(out, count):
    record = out["report"]["action_event_compilations"][0]
    assert len(record["generated"]) == count
    assert all(e["self_snapshot_event"] == f"capture.{e['action_id']}" for e in record["generated"])
    assert len([t for t in out["result"]["snapshot_captures"]["trace"] if t["type"] == "snapshot_capture"]) == 3
    validate_snapshot_capture_receipt(out["plan"], out["result"], out["report"], out["result_receipt"])
    validate_snapshot_capture_result_receipt(out["result"], out["result_receipt"])


def test_single_three_actions_share_calibration_but_capture_per_action(action_events_db):
    plan = confirm_action_events(with_captures(action_event_plan(action_events_db)), action_events_db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, action_events_db)
    assert plan == before
    assert [h["expected"] for h in out["result"]["hits"]] == [22.5] * 3
    verify_capture_output(out, 3)
    assert out["plan"]["mechanics_model"]["hit_patterns"] == []


def test_periodic_two_channels_share_existing_action_capture(periodic_db):
    plan = confirm_periodic(with_captures(periodic_plan(periodic_db)), periodic_db)
    out = run_plan_calc(plan, periodic_db)
    assert out["result"]["summary"]["total_damage"] == pytest.approx(1500.889481306076)
    verify_capture_output(out, 15)


@pytest.mark.parametrize("change", ["missing", "pending", "blank", "late", "same_order"])
def test_unready_or_misordered_capture_never_falls_back(action_events_db, change):
    plan = with_captures(action_event_plan(action_events_db))
    captures = plan["mechanics_model"]["snapshot_captures"]
    if change == "missing":
        captures.pop()
    elif change == "pending":
        captures[0]["status"] = "pending"
    elif change == "blank":
        captures[0]["source"] = " "
    elif change == "late":
        captures[0]["offset"] = .3
    else:
        captures[0]["offset"] = .2
        plan["action_event_overrides"] = [{"action_id": "cast0", "order": 0,
            "status": "assumed", "source": "Explicit same-order test"}]
    confirm_action_events(plan, action_events_db)
    report = compose_equipment_plan(plan, action_events_db)
    assert report["state"] == "pending"
    assert report["action_event_compilations"][0]["generated"] == []
    with pytest.raises(PlanError):
        run_plan_calc(plan, action_events_db)


def test_capture_calibration_changes_acceptance_but_axis_move_does_not(action_events_db):
    plan = confirm_action_events(with_captures(action_event_plan(action_events_db)), action_events_db)
    old = compose_equipment_plan(plan, action_events_db)["action_event_compilations"][0]
    plan["rotation_model"]["actions"][1]["start"] = 2.01
    moved = compose_equipment_plan(plan, action_events_db)["action_event_compilations"][0]
    assert moved["status"] == "ready"
    assert moved["profile_fingerprint"] == old["profile_fingerprint"]
    assert moved["instance_fingerprint"] != old["instance_fingerprint"]
    plan["mechanics_model"]["snapshot_captures"][0]["offset"] = .11
    changed = compose_equipment_plan(plan, action_events_db)["action_event_compilations"][0]
    assert changed["status"] == "pending"
    assert changed["profile_fingerprint"] != old["profile_fingerprint"]


def test_periodic_current_channel_does_not_acquire_snapshot_reference(periodic_db):
    plan = with_captures(periodic_plan(periodic_db))
    plan["action_event_profiles"][0]["channels"][1]["self_sampling"] = "current"
    out = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    generated = out["report"]["action_event_compilations"][0]["generated"]
    assert all(("self_snapshot_event" in e) == (e["channel_id"] == "slice") for e in generated)


@pytest.mark.parametrize("offset", [0, .2])
def test_zero_offset_and_same_tick_explicit_later_order_are_executable(action_events_db, offset):
    plan = with_captures(action_event_plan(action_events_db), offset)
    if offset:
        plan["action_event_overrides"] = [{"action_id": a["id"], "order": 1,
            "status": "assumed", "source": "Explicit after-capture order"} for a in plan["rotation_model"]["actions"]]
    out = run_plan_calc(confirm_action_events(plan, action_events_db), action_events_db)
    verify_capture_output(out, 3)
