"""Finite-axis external refresh controls for a whole periodic profile."""
# ruff: noqa: F811
from __future__ import annotations

import copy

import pytest
from test_action_dispatch_sources import _install, _multi_plan, _multi_profile
from test_mechanics_execution import ability, action
from test_periodic_actions import confirm_periodic, periodic_plan
from test_periodic_cadence_refresh import _applications
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_periodic_attack import periodic_db  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.periodic_receipt import validate_periodic_receipt
from genshin_app.equipment.schema import PlanError


def _replace_profile(plan):
    plan["action_event_profiles"][0]["lifecycle"].update(
        no_refresh_replacement_or_early_despawn=False,
        replacement_policy="replace_previous",
        no_early_despawn=True,
    )
    return plan


def _trigger_action(plan, *, action_id="refresh.cast", start=.6, end=.65, order=5):
    rotation = plan["rotation_model"]
    source_ability = ability("refresh.ability", kind="action.wait")
    source_ability.update(status="confirmed", source="confirmed refresh trigger fixture")
    rotation["abilities"].append(source_ability)
    source_action = action(
        action_id, "refresh.ability", start=start, end=end, order=order,
        repeat={"mode": "once"})
    source_action.update(status="confirmed", source="confirmed refresh trigger fixture")
    rotation["actions"].append(source_action)
    return rotation["actions"][-1]


def _refresh(plan, *, trigger_id="refresh.control", action_id="refresh.cast",
             kind="action_start", phase="pre", order=None,
             target_profile_id="q.profile", dispatch_ability_id=None):
    trigger = {"kind": kind, "action_id": action_id, "phase": phase}
    if order is not None:
        trigger["order"] = order
    target = ({"action_dispatch_ability_id": dispatch_ability_id}
              if dispatch_ability_id is not None
              else {"action_event_profile_id": target_profile_id})
    plan["periodic_refresh_triggers"] = [{
        "id": trigger_id,
        **target,
        "operation": "replace_active",
        "trigger": trigger,
        "snapshot_policy": "preserve_active_generation",
        "status": "confirmed",
        "source": "synthetic finite-axis refresh fixture",
    }]
    return plan


def _ready_refresh_plan(db, **kwargs):
    plan = _replace_profile(periodic_plan(db))
    _trigger_action(plan, start=kwargs.pop("start", .6), end=kwargs.pop("end", .65),
                    order=kwargs.pop("action_order", 5))
    return _refresh(plan, **kwargs)


def test_action_start_refresh_replaces_whole_profile_and_reopens_every_channel(periodic_db):
    plan = _ready_refresh_plan(periodic_db)
    for channel in plan["action_event_profiles"][0]["channels"]:
        channel["self_sampling"] = "action_start"
    output = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    record = output["result"]["action_event_compilations"][0]

    assert record["status"] == "ready"
    assert record["refresh_contract"]["profile_scope"] == "all_channels"
    assert record["refresh_contract"]["execution"] == "compile_time_planned"
    assert record["refresh_triggers"] == [{
        "trigger_id": "refresh.control", "profile_id": "q.profile",
        "operation": "replace_active",
        "declared_target": {"kind": "action_event_profile",
            "action_event_profile_id": "q.profile"},
        "resolved_profile_id": "q.profile",
        "source_event": {"kind": "action_start", "action_id": "refresh.cast",
            "ability_id": "refresh.ability", "time_ticks": 600000,
            "native_order": 5, "order": 5, "phase": "pre", "repeat": {"mode": "once"},
            "evidence": {
                "action": {"status": "confirmed", "source": "confirmed refresh trigger fixture"},
                "ability": {"status": "confirmed", "source": "confirmed refresh trigger fixture"},
            }},
        "source_event_fingerprint": record["refresh_triggers"][0]["source_event_fingerprint"],
        "snapshot_policy": "preserve_active_generation", "outcome": "replaced_active",
        "generation": 2, "root_action_id": "q0",
    }]
    assert record["replacements"][0]["replacing_trigger_id"] == "refresh.control"
    assert record["replacements"][0]["replacement_order"] == 5
    assert record["replacements"][0]["cancelled_hits"] == 3
    assert record["stats"] == {
        "matched_actions": 3, "planned_hits": 20, "generated_hits": 17,
        "landed_hits": 17, "missed_hits": 0, "cancelled_hits": 3,
        "refresh_triggers": 1, "refreshes_applied": 1,
        "refreshes_inactive_noop": 0, "generations_created": 4,
        "instances_replaced": 1,
    }
    refreshed = [row for row in record["generated"]
                 if row.get("instance_trigger_id") == "refresh.control"]
    assert {row["channel_id"] for row in refreshed} == {"slice", "blast"}
    assert [row["t"] for row in refreshed] == [.7, .95, 1.2, 1.45, 1.6]
    assert all(row["action_id"] == "q0" and row["instance_generation"] == 2
               for row in refreshed)
    refreshed_patterns = {row["pattern_id"] for row in refreshed}
    assert all(pattern["self_snapshot_event"] == "q0"
               for pattern in output["report"]["mechanics_model"]["hit_patterns"]
               if pattern["id"] in refreshed_patterns)
    assert output["result_receipt"]["periodic_attacks"][
        "input_refresh_triggers_fingerprint"]


def test_action_start_post_uses_real_post_order_and_is_audited(periodic_db):
    plan = _ready_refresh_plan(periodic_db, phase="post", action_order=5)
    output = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    source = output["result"]["action_event_compilations"][0]["refresh_triggers"][0]["source_event"]
    assert source["native_order"] == 5
    assert source["order"] == 6
    assert source["phase"] == "post"


def test_action_end_requires_explicit_nonconflicting_post_order(periodic_db):
    plan = _ready_refresh_plan(periodic_db, kind="action_end", phase="post", order=7,
                               start=.5, end=.6, action_order=5)
    output = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    source = output["result"]["action_event_compilations"][0]["refresh_triggers"][0]["source_event"]
    assert source["kind"] == "action_end" and source["time_ticks"] == 600000
    assert source["native_order"] == 5 and source["order"] == 7

    invalid = _ready_refresh_plan(periodic_db, kind="action_end", phase="post", order=5,
                                  start=.5, end=.6, action_order=5)
    preview = compose_equipment_plan(confirm_periodic(invalid, periodic_db), periodic_db)
    record = preview["action_event_compilations"][0]
    assert record["status"] == "pending" and record["generated"] == []
    assert "严格晚于" in record["issues"][0]["message"]


def test_inactive_replace_is_audited_noop_and_does_not_start_an_instance(periodic_db):
    plan = _ready_refresh_plan(periodic_db, start=2, end=2.05)
    output = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    record = output["result"]["action_event_compilations"][0]
    assert record["stats"]["generated_hits"] == 15
    assert record["stats"]["refreshes_inactive_noop"] == 1
    assert record["stats"]["refreshes_applied"] == 0
    assert record["refresh_triggers"][0]["outcome"] == "inactive_noop"
    assert not any(row.get("instance_trigger_id") == "refresh.control"
                   for row in record["generated"])


def test_refresh_source_and_generation_identities_are_closed(periodic_db):
    collision = _ready_refresh_plan(periodic_db, trigger_id="q0")
    with pytest.raises(PlanError, match="不能与动作"):
        compose_equipment_plan(collision, periodic_db)

    implicit = _replace_profile(periodic_plan(periodic_db))
    _refresh(implicit, action_id="q0")
    preview = compose_equipment_plan(confirm_periodic(implicit, periodic_db), periodic_db)
    record = preview["action_event_compilations"][0]
    assert record["status"] == "pending" and "不能同时声明" in record["issues"][0]["message"]

    assumed = _ready_refresh_plan(periodic_db)
    next(row for row in assumed["rotation_model"]["actions"]
         if row["id"] == "refresh.cast")["status"] = "assumed"
    preview = compose_equipment_plan(confirm_periodic(assumed, periodic_db), periodic_db)
    assert "须为confirmed" in preview["action_event_compilations"][0]["issues"][0]["message"]

    assumed_ability = _ready_refresh_plan(periodic_db)
    next(row for row in assumed_ability["rotation_model"]["abilities"]
         if row["id"] == "refresh.ability")["status"] = "assumed"
    preview = compose_equipment_plan(confirm_periodic(assumed_ability, periodic_db), periodic_db)
    assert "须为confirmed" in preview["action_event_compilations"][0]["issues"][0]["message"]


def test_refresh_contract_and_window_fail_closed(periodic_db):
    no_replace = periodic_plan(periodic_db)
    _trigger_action(no_replace)
    _refresh(no_replace)
    with pytest.raises(PlanError, match="replace_previous"):
        compose_equipment_plan(no_replace, periodic_db)

    assumed = _ready_refresh_plan(periodic_db)
    assumed["periodic_refresh_triggers"][0]["status"] = "assumed"
    with pytest.raises(PlanError, match="结构不合法"):
        compose_equipment_plan(assumed, periodic_db)

    outside = _ready_refresh_plan(periodic_db, start=21.1, end=21.15)
    preview = compose_equipment_plan(confirm_periodic(outside, periodic_db), periodic_db)
    record = preview["action_event_compilations"][0]
    assert record["status"] == "pending" and record["generated"] == []
    assert "结束越过输入窗口" in record["issues"][0]["message"]


def test_duplicate_position_and_same_tick_first_hit_without_order_are_atomic_pending(periodic_db):
    duplicate = _ready_refresh_plan(periodic_db)
    duplicate["periodic_refresh_triggers"].append({
        **copy.deepcopy(duplicate["periodic_refresh_triggers"][0]), "id": "refresh.control.2"})
    preview = compose_equipment_plan(confirm_periodic(duplicate, periodic_db), periodic_db)
    record = preview["action_event_compilations"][0]
    assert record["status"] == "pending" and record["generated"] == []
    assert "同一时点和order" in record["issues"][0]["message"]

    same_tick = _ready_refresh_plan(periodic_db)
    same_tick["action_event_profiles"][0]["channels"][0]["schedule"][
        "first_offset_seconds"] = .1
    preview = compose_equipment_plan(confirm_periodic(same_tick, periodic_db), periodic_db)
    record = preview["action_event_compilations"][0]
    assert record["status"] == "pending" and record["generated"] == []
    assert "同刻首击" in record["issues"][0]["message"]


def test_refresh_receipt_detects_source_event_or_transition_tamper(periodic_db):
    output = run_plan_calc(confirm_periodic(_ready_refresh_plan(periodic_db), periodic_db), periodic_db)
    output["result"]["action_event_compilations"][0]["refresh_triggers"][0][
        "source_event"]["order"] = 99
    with pytest.raises(ValueError):
        validate_periodic_receipt(
            output["plan"], output["result"], output["report"], output["result_receipt"])


def test_external_refresh_preserves_shared_hit_counter_and_timer_icd(periodic_db):
    plan = _ready_refresh_plan(periodic_db)
    output = run_plan_calc(confirm_periodic(plan, periodic_db), periodic_db)
    record = output["result"]["action_event_compilations"][0]
    refreshed = [row for row in record["generated"]
                 if row.get("instance_trigger_id") == "refresh.control"
                 and row["channel_id"] == "slice"]
    assert "application_cadence_position" not in refreshed[0]
    applications = _applications(output)
    first_old = next(row for row in applications if row["t"] == .2)
    second_old = next(row for row in applications if row["t"] == .45)
    first_refreshed = next(row for row in applications if row["t"] == .7)
    assert first_old["icd_audit"]["attempt_number"] == 1
    assert second_old["icd_audit"]["attempt_number"] == 2
    assert first_refreshed["icd_audit"]["attempt_number"] == 3
    assert first_refreshed["icd_audit"]["anchor_t"] == first_old["icd_audit"]["anchor_t"]
    assert first_refreshed["icd_audit"]["reset_at"] == first_old["icd_audit"]["reset_at"]
    assert first_refreshed["reason"] == "icd_blocked"


def test_public_dispatch_ability_target_resolves_after_private_materialization(
        periodic_db, monkeypatch):
    profile = _multi_profile(periodic_db)
    profile["lifecycle"].update(
        no_refresh_replacement_or_early_despawn=False,
        replacement_policy="replace_previous",
        no_early_despawn=True,
    )
    _install(monkeypatch, profile)
    plan = _multi_plan(periodic_db, profile)
    _trigger_action(plan)
    _refresh(plan, dispatch_ability_id="e")

    output = run_plan_calc(plan, periodic_db)
    dispatch = output["report"]["action_dispatch_compilations"][0]
    resolved_profile_id = dispatch["materialized"]["profile_ids"][0]
    event = next(row for row in output["result"]["action_event_compilations"]
                 if row["profile_id"] == resolved_profile_id)
    refresh = event["refresh_triggers"][0]
    assert refresh["declared_target"] == {
        "kind": "action_dispatch_ability", "action_dispatch_ability_id": "e"}
    assert refresh["resolved_profile_id"] == resolved_profile_id
    assert output["plan"]["periodic_refresh_triggers"][0]["action_dispatch_ability_id"] == "e"
    assert "action_event_profile_id" not in output["plan"]["periodic_refresh_triggers"][0]
    assert not output["plan"].get("action_event_profiles")


def test_dispatch_refresh_target_is_closed_before_and_after_materialization(periodic_db):
    missing = _replace_profile(periodic_plan(periodic_db))
    _trigger_action(missing)
    _refresh(missing, dispatch_ability_id="missing")
    with pytest.raises(PlanError, match="没有公开声明"):
        compose_equipment_plan(missing, periodic_db)

    both = _ready_refresh_plan(periodic_db)
    both["periodic_refresh_triggers"][0]["action_dispatch_ability_id"] = "e"
    with pytest.raises(PlanError, match="结构不合法"):
        compose_equipment_plan(both, periodic_db)


def test_absent_or_empty_refresh_field_preserves_periodic_instance_payload(periodic_db):
    base_plan = confirm_periodic(_replace_profile(periodic_plan(periodic_db)), periodic_db)
    base = run_plan_calc(base_plan, periodic_db)
    empty_plan = copy.deepcopy(base_plan)
    empty_plan["periodic_refresh_triggers"] = []
    empty = run_plan_calc(empty_plan, periodic_db)
    left = base["result"]["action_event_compilations"][0]
    right = empty["result"]["action_event_compilations"][0]
    assert left == right
    assert left["instance_fingerprint"] == right["instance_fingerprint"]
    assert "input_refresh_triggers_fingerprint" not in base["result_receipt"]["periodic_attacks"]
    assert "input_refresh_triggers_fingerprint" not in empty["result_receipt"]["periodic_attacks"]
