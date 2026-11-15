"""Fresh App integration using an isolated source database."""
from __future__ import annotations

import copy

import pytest
from test_freeze_runtime import model as freeze_model
from test_mechanics_execution import EVIDENCE, ability, action, fixture, hit
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_limited_hit_benefit import benefit_db, benefit_repo  # noqa: F401

from genshin_app.equipment import (
    compose_equipment_plan,
    enable_mechanics,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.limited_hit_benefit_receipt import (
    validate_limited_hit_benefit_receipt,
    validate_limited_hit_benefit_result_receipt,
)
from genshin_app.equipment.limited_hit_benefit_schema import DEFAULT_SOURCE
from genshin_app.equipment.service import result_bytes
from genshin_db.runtime_queries import query_runtime

# ruff: noqa: F811


def benefit_plan(db, *, variant="hold", param_index=1):
    template, rotation, mechanics = fixture((0,))
    template["characters"][0]["stats"].update(atk=2000, energy_recharge=1)
    rotation["abilities"][0]["variant"] = variant
    rotation["actions"] = [action("cast", start=0, end=1, repeat={"mode": "once"})]
    plan = enable_mechanics(new_plan(template))
    prototype = plan["manual_template"]["hits"][0]
    prototype.pop("multiplier")
    prototype["scaling_stat"] = "atk"
    mechanics["hit_events"] = []
    mechanics["elemental_model"] = freeze_model()
    mechanics["elemental_model"]["contact_profiles"] = [{"id": "safe", "freeze_interaction": "none", **EVIDENCE}]
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    selection = {"character_id": 10000063, "slot": "skill", "talent_level": 10, "constellation": 0}
    source = query_runtime(db, {"action": "action_parameter", "param_index": param_index, **selection})
    plan["hit_parameter_sources"] = [{"hit_id": "h0", "version_line": "release", "selection": selection,
        "param_index": param_index, "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier", **EVIDENCE,
        "expected_snapshot_id": source["snapshot"]["snapshot_id"], "expected_node_sha256": source["source"]["node_sha256"]}]
    plan["action_event_profiles"] = [{"id": "contact", "kind": "calibrated_single_contact_v1", "prototype_hit_id": "h0",
        "variant": variant, "calibration": {"offset_seconds": .2, "precision_seconds": .01, **EVIDENCE},
        "application_profile_id": "cryo", "contact_profile_id": "safe", "default_landed": True,
        "self_sampling": "current", "accepted_profile_fingerprint": None}]
    plan["action_event_bindings"] = [{"ability_id": "e", "profile_id": "contact"}]
    plan["action_event_overrides"] = []
    preview = compose_equipment_plan(plan, db)
    parent = preview["action_event_compilations"][0]
    assert parent["required_confirmation"], parent
    plan["action_event_profiles"][0].update(parent["required_confirmation"])
    binding = copy.deepcopy(DEFAULT_SOURCE)
    binding.update(id="quill", action_event_profile_id="contact")
    binding["scenario"].update(target_kind="ordinary_non_animal_monster", party_mode="single_player_fixed",
        all_party_alive=True, all_party_in_apply_predicate=True, all_party_in_retention_predicate=True,
        initial_effect="none", no_external_quill_changes=True, hitlag_policy="zero_hitlag_reference", **EVIDENCE)
    binding["calibration"].update(activation_offset_seconds=.1, precision_seconds=.01, **EVIDENCE)
    plan["limited_hit_benefit_sources"] = [binding]
    return plan


def confirm_benefit(plan, db):
    report = compose_equipment_plan(plan, db)
    record = report["limited_hit_benefit_compilations"][0]
    assert record["required_confirmation"], (record.get("issues"), report["missing_inputs"])
    plan["limited_hit_benefit_sources"][0].update(record["required_confirmation"])
    return plan


def test_fresh_action_to_quota_and_damage(benefit_db):
    plan = confirm_benefit(benefit_plan(benefit_db), benefit_db)
    before = copy.deepcopy(plan)
    calculated = run_plan_calc(plan, benefit_db)
    assert plan == before
    assert calculated["plan"]["mechanics_model"]["rules"] == []
    result = calculated["result"]
    assert result["limited_hit_benefit_summary"]["activations"] == 1
    assert result["limited_hit_benefit_summary"]["consumed_hits"] == 1
    assert result["sampling_trace"][0]["source_value"] == 2000
    assert result["sampling_trace"][0]["value"] == pytest.approx(2000 * .8218079805374146)
    assert result["hits"][0]["matched_effect_refs"][0]["benefit_type_id"] == "benefit.base_add.all"
    validate_limited_hit_benefit_receipt(calculated["plan"], result, calculated["report"], calculated["result_receipt"])
    assert result_bytes(result, calculated["result_receipt"], "json")


def test_actual_miss_still_applies_without_consumption(benefit_db):
    plan = benefit_plan(benefit_db)
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": False, "order": 20, **EVIDENCE}]
    plan["rotation_model"]["abilities"].append(ability("n", kind="action.normal_attack"))
    plan["rotation_model"]["actions"].append(action("normal", "n", 1.1, 1.6, repeat={"mode": "once"}))
    plan["manual_template"]["hits"].append(hit("normal_hit", 1.3, kind="damage.normal_attack"))
    plan["mechanics_model"]["hit_events"].append({"hit_id": "normal_hit", "action_id": "normal", "order": 20,
        "application": {"profile_id": "cryo", "target_id": "enemy", "landed": True},
        "contact": {"kind": "profile", "profile_id": "safe"}})
    calculated = run_plan_calc(confirm_benefit(plan, benefit_db), benefit_db)
    summary = calculated["result"]["limited_hit_benefit_summary"]
    assert summary["activations"] == 1 and summary["consumed_hits"] == 1
    assert [h["id"] for h in calculated["result"]["hits"]] == ["normal_hit"]
    quota = [row for row in calculated["result"]["mechanics_trace"] if row["type"] == "quota"]
    assert [(row["event_id"], row["before"], row["after"]) for row in quota] == [("normal_hit", 7, 6)]


def test_verified_all_miss_retains_mechanism_execution_without_fake_damage(benefit_db):
    plan = benefit_plan(benefit_db)
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": False, "order": 20, **EVIDENCE}]
    calculated = run_plan_calc(confirm_benefit(plan, benefit_db), benefit_db)
    result = calculated["result"]
    assert calculated["plan"]["resolved_template"]["hits"] == result["hits"] == []
    assert result["summary"]["total_damage"] == result["summary"]["dps"] == 0
    assert result["damage_reporting"]["planned_contacts"] == 1
    assert sum(row["type"] == "activation" for row in result["mechanics_trace"]) == 1
    assert not any(row["type"] == "quota" for row in result["mechanics_trace"])
    assert result_bytes(result, calculated["result_receipt"], "json")


@pytest.mark.parametrize("offset,order,confirmable", [(.1, 0, True), (.2, 0, False), (.2, 1, True), (.200001, 5, False)])
def test_same_tick_queue_order_is_guarded(benefit_db, offset, order, confirmable):
    plan = benefit_plan(benefit_db)
    plan["limited_hit_benefit_sources"][0]["calibration"]["activation_offset_seconds"] = offset
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": True, "order": order, **EVIDENCE}]
    record = compose_equipment_plan(plan, benefit_db)["limited_hit_benefit_compilations"][0]
    assert bool(record["required_confirmation"]) == confirmable
    if confirmable:
        plan["limited_hit_benefit_sources"][0].update(record["required_confirmation"])
        assert run_plan_calc(plan, benefit_db)["result"]["limited_hit_benefit_summary"]["consumed_hits"] == 1


def test_axis_move_preserves_source_acceptance_but_changes_execution(benefit_db):
    plan = confirm_benefit(benefit_plan(benefit_db), benefit_db)
    before = compose_equipment_plan(plan, benefit_db)["limited_hit_benefit_compilations"][0]
    plan["rotation_model"]["actions"][0].update(start=1, occupation_end=2)
    after = compose_equipment_plan(plan, benefit_db)["limited_hit_benefit_compilations"][0]
    assert after["status"] == "ready" and before["acceptance_fingerprint"] == after["acceptance_fingerprint"]
    assert before["execution_fingerprint"] != after["execution_fingerprint"]
    assert after["parent_links"][0]["activation_tick"] == 1100000


@pytest.mark.parametrize("where", ["quota", "sample", "activation", "source", "rule", "projected_value"])
def test_offline_single_layer_tamper_is_rejected(benefit_db, where):
    out = run_plan_calc(confirm_benefit(benefit_plan(benefit_db), benefit_db), benefit_db)
    if where == "quota":
        next(r for r in out["result"]["mechanics_trace"] if r["type"] == "quota")["after"] = 7
    elif where == "sample":
        out["result"]["sampling_trace"][0]["source_value"] += 1
    elif where == "activation":
        next(r for r in out["result"]["mechanics_trace"] if r["type"] == "activation")["quota"]["a"] = 8
    elif where == "source":
        out["result"]["limited_hit_benefit_compilations"][0]["benefit_certificate"]["capacity"] = 8
    elif where == "rule":
        out["report"]["mechanics_model"]["rules"][0]["quota"]["capacity"] = 8
    else:
        out["report"]["plan"]["resolved_template"]["buffs"][0]["effects"][0]["value"] += 1
    out["result_receipt"]["result_fingerprint"] = fingerprint(out["result"])
    with pytest.raises(ValueError):
        validate_limited_hit_benefit_receipt(out["plan"], out["result"], out["report"], out["result_receipt"])


def test_receipt_arithmetic_survives_rehash_attack(benefit_db):
    from genshin_app.equipment.limited_hit_benefit_receipt import (
        limited_hit_benefit_ledger_fingerprint,
    )
    out = run_plan_calc(confirm_benefit(benefit_plan(benefit_db), benefit_db), benefit_db)
    next(r for r in out["result"]["mechanics_trace"] if r["type"] == "quota")["after"] = 7
    out["result_receipt"]["result_fingerprint"] = fingerprint(out["result"])
    out["result_receipt"]["limited_hit_benefit"]["ledger_fingerprint"] = limited_hit_benefit_ledger_fingerprint(out["result"])
    with pytest.raises(ValueError):
        validate_limited_hit_benefit_result_receipt(out["result"], out["result_receipt"])


def test_axis_match_can_exclude_benefit_without_recovering_quota(benefit_db):
    plan = confirm_benefit(benefit_plan(benefit_db), benefit_db)
    record = compose_equipment_plan(plan, benefit_db)["limited_hit_benefit_compilations"][0]
    hid = record["parent_links"][0]["hit_id"]
    match = {"id": "exception", "effect_ref": {"buff_id": "auto.hit-benefit.quill", "effect_id": "current_attack_flat"},
        "mode": "adjust", "include_hit_ids": [], "exclude_hit_ids": [hid], "state": "pending",
        "reason": "工程反例：排除收益仍消耗次数", "source_refs": [{"kind": "engineering_test"}], "basis_fingerprint": None}
    plan["rotation_tuning"] = {"buff_hit_matches": [match]}
    preview = compose_equipment_plan(plan, benefit_db)
    match.update(state="confirmed", basis_fingerprint=preview["match_previews"][0]["basis_fingerprint"])
    out = run_plan_calc(plan, benefit_db)
    assert out["result"]["limited_hit_benefit_summary"]["consumed_hits"] == 1
    assert out["result"]["limited_hit_benefit_summary"]["applied_benefits"] == 0
    assert out["result"]["hits"][0]["matched_effect_refs"] == []
