"""Independent EC16b boundaries; toy timing guards are not real skill rotations."""
from __future__ import annotations

import copy

import pytest
from test_energy_generation_sources import (
    confirm_energy,
    energy_plan,
    source_declaration,
)
from test_freeze_runtime import model as freeze_model
from test_mechanics_execution import EVIDENCE, ability, action, fixture
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_energy_dispatch import trigger_db, trigger_repo  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.energy_generation_receipt import (
    _energy_arithmetic,
    energy_generation_resource_ledger_fingerprint,
    validate_energy_generation_result_receipt,
)
from genshin_app.equipment.energy_generation_sources import (
    _delivery,
    _migration,
    _parent_links,
    _Pending,
)
from genshin_engine.mechanics import execute_mechanics

# ruff: noqa: F811


@pytest.mark.parametrize("local,delta,accepted", [
    (False, 200000, False), (False, 200001, False), (False, 200002, True),
    (True, 499999, False), (True, 500000, False), (True, 500001, True),
])
def test_review_isolation_uses_actual_absolute_hits_strict_integer_ticks(trigger_db, local, delta, accepted):
    # Inspect only the source admission helper, not a fictitious short-CD game axis.
    plan = confirm_energy(energy_plan(trigger_db, variant="hold" if local else "tap", local=local), trigger_db)
    report = compose_equipment_plan(plan, trigger_db)
    record = report["energy_generation_compilations"][0]
    prepared = copy.deepcopy(plan)
    prepared["mechanics_model"] = copy.deepcopy(report["mechanics_model"])
    parent = copy.deepcopy(report["action_event_compilations"][0])
    profile = prepared["action_event_profiles"][0]
    # Keep the audited application/contact materialized by the actual compiler.
    prepared["rotation_model"]["actions"] = prepared["rotation_model"]["actions"][:2]
    parent["generated"] = parent["generated"][:2]
    keep = {r["pattern_id"] for r in parent["generated"]}
    prepared["mechanics_model"]["hit_patterns"] = [p for p in prepared["mechanics_model"]["hit_patterns"] if p["id"] in keep]
    prepared["rotation_model"]["actions"][1].update(start=delta / 1_000_000, occupation_end=delta / 1_000_000 + 1)
    parent["generated"][1]["t"] = (200000 + delta) / 1_000_000
    args = (prepared, report["plan"]["resolved_template"], "a", profile, parent, record["trigger_certificate"])
    if accepted:
        links = _parent_links(*args)
        assert [r["absolute_tick"] for r in links] == [200000, 200000 + delta]
    else:
        with pytest.raises(_Pending, match="隔离"):
            _parent_links(*args)


@pytest.mark.parametrize("change", ["scenario", "delay", "travel", "precision"])
def test_review_semantic_change_rejects_old_acceptance(trigger_db, change):
    plan = confirm_energy(energy_plan(trigger_db), trigger_db)
    old = plan["energy_generation_sources"][0]["accepted_fingerprint"]
    binding = plan["energy_generation_sources"][0]
    if change == "scenario":
        binding["scenario"]["source"] += " revised scenario evidence"
    else:
        key = {"delay": "spawn_delay_seconds", "travel": "travel_seconds", "precision": "precision_seconds"}[change]
        binding["calibration"][key] = {"delay": .11, "travel": .21, "precision": .02}[change]
    report = compose_equipment_plan(plan, trigger_db)
    row = report["energy_generation_compilations"][0]
    assert report["state"] == row["status"] == "pending"
    assert row["required_confirmation"]["accepted_fingerprint"] != old
    assert row["rules"] == row["initial_rules"] == row["resource_ownership"] == []


def test_review_delete_and_order_keep_acceptance_but_rebuild_real_parents(trigger_db):
    plan = confirm_energy(energy_plan(trigger_db), trigger_db)
    old = compose_equipment_plan(plan, trigger_db)["energy_generation_compilations"][0]
    plan["rotation_model"]["actions"].pop(1)
    plan["action_event_overrides"] = [{"action_id": "cast2", "landed": True, "order": 777, **EVIDENCE}]
    result = run_plan_calc(plan, trigger_db)["result"]
    new = result["energy_generation_compilations"][0]
    assert new["accepted_fingerprint"] == old["accepted_fingerprint"]
    assert new["execution_fingerprint"] != old["execution_fingerprint"]
    assert [(r["action_id"], r["order"]) for r in new["parent_links"]] == [("cast0", 0), ("cast2", 777)]
    assert result["energy_generation_summary"]["credited_particles"] == 6


def test_review_complete_migration_is_reversible_and_preserves_unrelated_event(trigger_db):
    plan = energy_plan(trigger_db)
    original = [
        {"id": "old", "type": "energy_spawn", "t": .3, "order": 30, "action_id": "cast0", "count": 3,
         "element": "anemo", "particle_type": "particle", "source_kind": "skill", "repeat": {"mode": "once"}, **EVIDENCE},
        {"id": "old-credit", "type": "energy_credit", "credit_at": .5, "order": 31, "action_id": "cast0", "spawn_id": "old",
         "count": 3, "repeat": {"mode": "once"}, **EVIDENCE},
        {"id": "refund", "type": "energy_direct", "t": .6, "order": 32, "action_id": "cast0", "amounts": {"a": 7},
         "repeat": {"mode": "once"}, **EVIDENCE},
    ]
    plan["rotation_model"]["events"] = copy.deepcopy(original)
    plan["energy_generation_sources"][0]["replaces_spawn_ids"] = ["old"]
    calculated = run_plan_calc(confirm_energy(plan, trigger_db), trigger_db)
    assert calculated["plan"]["rotation_model"]["events"] == original
    rows = calculated["result"]["rotation_check"]["event_log"]
    assert not {"old", "old-credit"} & {r["id"] for r in rows}
    assert any(r["id"] == "refund" and r["status"] == "passed" for r in rows)
    plan.pop("energy_generation_sources")
    old_result = run_plan_calc(plan, trigger_db)["result"]
    old_rows = old_result["rotation_check"]["event_log"]
    assert {"old", "old-credit", "refund"} <= {r["id"] for r in old_rows}
    assert "energy_generation_compilations" not in old_result


def _direct_with_source(db, *, initial=0, capacity=100, er_delta=0):
    plan = energy_plan(db)
    plan["manual_template"]["characters"][0]["stats"]["energy_recharge"] = 2
    rotation = plan["rotation_model"]
    rotation["characters"][0].update(
        energy_recharge=2, initial_energy=initial, energy_capacity=capacity)
    rotation["events"] = [{
        "id": "direct", "type": "energy_direct", "t": .1, "order": 32,
        "action_id": "cast0", "amounts": {"a": 7},
        "repeat": {"mode": "once"}, **EVIDENCE,
    }]
    if er_delta:
        rotation["energy_recharge_windows"] = [{
            "id": "temporary.er", "character": "a", "start": 0, "end": .3,
            "delta": er_delta, "repeat": {"mode": "once"}, **EVIDENCE,
        }]
    return run_plan_calc(confirm_energy(plan, db), db, capture_display=True)


@pytest.mark.parametrize("initial,capacity,delta,gain,overflow", [
    (0, 100, 0, 14, 0), (90, 100, 0, 10, 4), (100, 100, 0, 0, 14),
    (0, 100, 1, 21, 0),
])
def test_source_energy_receipt_accepts_direct_base_times_actual_er_once(
        trigger_db, initial, capacity, delta, gain, overflow):
    from genshin_app.equipment.delivery import validate_delivery_calculated
    from genshin_app.equipment.service import result_bytes

    calculated = _direct_with_source(
        trigger_db, initial=initial, capacity=capacity, er_delta=delta)
    result = calculated["result"]
    direct = next(row for row in result["rotation_check"]["event_log"]
                  if row["id"] == "direct")
    value = direct["energy"][0]
    assert value["base_amount"] == 7
    assert value["energy_recharge"] == 2 + delta
    assert value["raw"] == value["theoretical_amount"] == 7 * (2 + delta)
    assert value["gain"] == gain and value["overflow"] == overflow
    assert value["after"] == initial + gain
    validate_delivery_calculated(calculated)
    assert result_bytes(result, calculated["result_receipt"], "xlsx")


@pytest.mark.parametrize("mutation", [
    "base", "er", "double_er", "theoretical", "overflow", "gain", "after",
    "contract", "missing_base", "false_er", "final",
])
def test_source_direct_arithmetic_rejects_after_both_outer_hashes_recomputed(
        trigger_db, mutation):
    from genshin_app.equipment.files import fingerprint

    calculated = _direct_with_source(trigger_db, initial=90)
    result, receipt = calculated["result"], calculated["result_receipt"]
    direct = next(row for row in result["rotation_check"]["event_log"]
                  if row["id"] == "direct")
    value = direct["energy"][0]
    if mutation == "double_er":
        value.update(raw=28, theoretical_amount=28, overflow=18)
    elif mutation == "contract":
        value["settlement_contract"] = "unsupported"
    elif mutation == "missing_base":
        value.pop("base_amount")
    elif mutation == "false_er":
        value["energy_recharge"] = False
    elif mutation == "final":
        result["rotation_check"]["characters"][0]["final_energy"] = 99
    else:
        field = {"base": "base_amount", "er": "energy_recharge",
                 "theoretical": "theoretical_amount"}.get(mutation, mutation)
        value[field] += 1
    receipt["energy_generation"]["resource_ledger_fingerprint"] = (
        energy_generation_resource_ledger_fingerprint(result))
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_energy_generation_result_receipt(result, receipt)


def test_historical_direct_final_amount_arithmetic_remains_readonly():
    result = {"rotation_check": {
        "resolved_rotation_model": {"characters": [{
            "id": "a", "initial_energy": 0, "energy_capacity": 10, "element": "cryo"}]},
        "event_log": [{"id": "legacy", "type": "energy_direct", "status": "passed",
                       "frontline": "a", "energy": [{"character": "a", "on_field": True,
                           "before": 0, "raw": 7, "gain": 7, "overflow": 0, "after": 7,
                           "energy_recharge": None}]}],
        "characters": [{"id": "a", "final_energy": 7}],
    }}
    before = copy.deepcopy(result)
    _energy_arithmetic(result)
    assert result == before
    result["rotation_check"]["event_log"][0]["energy"][0]["energy_recharge"] = 2
    with pytest.raises(ValueError):
        _energy_arithmetic(result)


def test_review_shared_legacy_rule_cannot_be_globally_removed_for_one_action():
    _, rotation, mechanics = fixture()
    rotation["actions"] = [action("cast"), action("other", start=2, end=3)]
    rotation["events"] = [{"id": "old", "type": "energy_spawn", "action_id": "cast"},
                          {"id": "other-event", "type": "energy_spawn", "action_id": "other"}]
    for act, event_id in zip(rotation["actions"], ("old", "other-event"), strict=True):
        act["rule_outcomes"] = [{"rule_id": "legacy", "event_ids": [event_id]}]
    binding = source_declaration()
    binding["replaces_spawn_ids"] = ["old"]
    with pytest.raises(_Pending, match="其他动作"):
        _migration({"rotation_model": rotation, "mechanics_model": mechanics}, binding, [{"action_id": "cast", "actor": "a"}])


def four_person_execution(*, hit_offfield=False, travel=.2):
    template, rotation, mechanics = fixture((.2,))
    rotation["actions"][0]["repeat"] = {"mode": "once"}
    template["hits"][0]["element"] = "anemo"
    template["characters"][0]["stats"]["energy_recharge"] = 2
    rotation["characters"][0].update(element="anemo", energy_recharge=2, initial_energy=90, energy_capacity=100)
    for cid, element, er in (("b", "cryo", 1.5), ("c", "cryo", 1), ("d", "hydro", 1)):
        template["characters"].append({"id": cid, "level": 90, "stats": {"atk": 1, "em": 0, "energy_recharge": er}})
        rotation["characters"].append({"id": cid, "element": element, "energy_recharge": er,
            "initial_energy": 0, "energy_capacity": 100, **EVIDENCE})
    template["switches"] = [{"t": .1 if hit_offfield else .4, "character": "b"}]
    switch_t = .1 if hit_offfield else .4
    rotation["abilities"].append(ability("switch", "a", "action.switch"))
    rotation["actions"].append(action("switch-to-b", "switch", start=switch_t, end=switch_t,
        order=20, repeat={"mode": "once"}, switch_to="b"))
    mechanics["elemental_model"] = freeze_model()
    mechanics["hit_events"][0].update(application={"profile_id": "anemo", "target_id": "enemy", "landed": True},
        contact={"kind": "none", **EVIDENCE})
    binding = source_declaration()
    binding["calibration"]["travel_seconds"] = travel
    links = [{"action_id": "cast", "hit_id": "h0", "order": 10, "absolute_tick": 200000, "landed": True}]
    stage = _delivery(binding, "a", links, {"count": 3, "element": "anemo", "particle_type": "particle"}, {"events": []})
    mechanics["rules"] = stage["rules"]
    mechanics["initial_state"]["rules"] = stage["initial_rules"]
    mechanics["resource_ownership"] = stage["resource_ownership"]
    return execute_mechanics(template, rotation, mechanics)


def test_review_credit_samples_actual_frontline_er_and_capacity_independently():
    result = four_person_execution()
    assert result["result"] is not None, result["issues"]
    credits = [r for r in result["rotation_check"]["event_log"] if r["type"] == "energy_credit"]
    assert len(credits) == 1
    assert credits[0]["t"] == .5
    values = {row["character"]: row for row in credits[0]["energy"]}
    # 3 particles × (3 same / 1 different) × (.6 off / 1 on) × ER.
    assert [values[c]["raw"] for c in "abcd"] == pytest.approx([10.8, 4.5, 1.8, 1.8])
    assert [values[c]["gain"] for c in "abcd"] == pytest.approx([10, 4.5, 1.8, 1.8])
    assert values["a"]["overflow"] == pytest.approx(.8)
    assert [values[c]["after"] for c in "abcd"] == pytest.approx([100, 4.5, 1.8, 1.8])


def test_review_offfield_at_hit_blocks_batch_despite_frontline_action_start():
    result = four_person_execution(hit_offfield=True)
    assert result["result"] is not None, result["issues"]
    assert not any(r["type"] in ("energy_spawn", "energy_credit") for r in result["rotation_check"]["event_log"])


def test_review_failed_burst_does_not_count_in_flight_or_tail_as_credited(trigger_db):
    plan = energy_plan(trigger_db)
    plan["energy_generation_sources"][0]["calibration"]["travel_seconds"] = 2.2
    plan["rotation_model"]["abilities"].append(ability("q", "a", "action.elemental_burst", {"required": 10, "amount": 10}))
    plan["rotation_model"]["actions"].append(action("burst", "q", start=1.1, end=1.2, repeat={"mode": "once"}))
    plan["rotation_model"]["events"].append({"id": "spend", "type": "energy_spend", "t": 1.1, "order": 1,
        "action_id": "burst", "character": "a", "required": 10, "amount": 10, "repeat": {"mode": "once"}, **EVIDENCE})
    result = run_plan_calc(confirm_energy(plan, trigger_db), trigger_db)["result"]
    summary = result["energy_generation_summary"]
    assert result["rotation_check"]["energy_check"]["status"] == "failed"
    assert summary["spawned_particles"] == summary["in_flight_particles"] == 3
    assert summary["credited_particles"] == 0
    assert summary["eligible_parent_hits"] == 1 and summary["unverified_parent_hits"] == 2
