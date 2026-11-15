"""Independent source-to-execution cases, with literal arithmetic oracles."""
from __future__ import annotations

import copy
from decimal import Decimal

import pytest
from test_action_events_review import attach_mechanics_rules, review_manual_sources
from test_energy_generation_sources import confirm_energy, source_declaration
from test_limited_hit_benefit_sources import benefit_plan, confirm_benefit
from test_mechanics_execution import (
    ability,
    action,
    add,
    condition,
    hit,
    rule,
    second_character,
)
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_energy_dispatch import origin, trigger_db, trigger_repo  # noqa: F401
from test_runtime_limited_hit_benefit import benefit_db, benefit_repo  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.limited_hit_benefit_receipt import (
    validate_limited_hit_benefit_receipt,
)
from genshin_app.equipment.service import result_bytes

# ruff: noqa: F811
ONCE = {"mode": "once"}
FACTOR = Decimal("0.8218079805374146")


def normal_hits(plan, actor, aid, times, *, snapshot=None):
    template, model = plan["manual_template"], plan["mechanics_model"]
    for i, when in enumerate(times):
        hid = f"{aid}.{i}"
        template["hits"].append(hit(hid, round(when, 6), actor, "damage.normal_attack"))
        desc = {"hit_id": hid, "action_id": aid, "order": 20 + i,
                "application": {"profile_id": "cryo", "target_id": "enemy", "landed": True},
                "contact": {"kind": "profile", "profile_id": "safe"}}
        if snapshot:
            desc["self_snapshot_event"] = snapshot
        model["hit_events"].append(desc)


def add_recipient(plan):
    template, rotation = plan["manual_template"], plan["rotation_model"]
    second_character(template, rotation)
    template["characters"][1]["stats"].update(atk=100, energy_recharge=1)
    rotation["abilities"].extend([ability("switch", "a", "action.switch"),
                                   ability("bn", "b", "action.normal_attack")])
    rotation["actions"].extend([
        action("switch-b", "switch", 2, 2.01, repeat=dict(ONCE), switch_to="b"),
        action("bn", "bn", 2.1, 3.1, repeat=dict(ONCE))])
    template["switches"] = [{"t": 2, "character": "b"}]


def test_review_source_delivers_seven_separate_hits_to_each_recipient(benefit_db):
    plan = benefit_plan(benefit_db)
    add_recipient(plan)
    plan["rotation_model"]["abilities"].append(ability("an", "a", "action.normal_attack"))
    plan["rotation_model"]["actions"].append(action("an", "an", 1.05, 1.9, repeat=dict(ONCE)))
    normal_hits(plan, "a", "an", [1.1 + i / 10 for i in range(7)])
    normal_hits(plan, "b", "bn", [2.2 + i / 10 for i in range(8)])
    out = run_plan_calc(confirm_benefit(plan, benefit_db), benefit_db)
    result = out["result"]
    assert result["limited_hit_benefit_summary"]["consumed_by_character"] == {"a": 7, "b": 7}
    quota = [r for r in result["mechanics_trace"] if r["type"] == "quota"]
    assert [r["after"] for r in quota if r["pool"] == "a"] == [6, 5, 4, 3, 2, 1, 0, 0]
    assert [r["after"] for r in quota if r["pool"] == "b"] == [6, 5, 4, 3, 2, 1, 0, 0]
    expected = float((Decimal(100) + Decimal(2000) * FACTOR) * Decimal(".45"))
    assert [h["expected"] for h in result["hits"] if h["id"].startswith("bn.")] == pytest.approx([expected] * 7 + [45])
    validate_limited_hit_benefit_receipt(out["plan"], result, out["report"], out["result_receipt"])


def test_review_second_actual_cast_resets_pool_even_when_instance_identifier_is_reused(benefit_db):
    plan = confirm_benefit(benefit_plan(benefit_db), benefit_db)
    accepted = plan["limited_hit_benefit_sources"][0]["accepted_fingerprint"]
    plan["rotation_model"]["actions"].append(action("recast", "e", 2, 3, repeat=dict(ONCE)))
    out = run_plan_calc(plan, benefit_db)
    assert out["plan"]["limited_hit_benefit_sources"][0]["accepted_fingerprint"] == accepted
    rows = out["result"]["mechanics_trace"]
    activations = [r for r in rows if r["type"] == "activation"]
    quotas = [r for r in rows if r["type"] == "quota"]
    assert [r["event_id"] for r in activations] == ["cast", "recast"]
    assert activations[0]["instance_id"] == activations[1]["instance_id"]
    assert [(r["before"], r["after"]) for r in quotas] == [(7, 6), (7, 6)]
    assert [r["expires_at"] for r in activations] == [[15.1], [17.1]]


def test_review_provider_current_remains_current_when_recipient_is_snapshotted(benefit_db):
    plan = benefit_plan(benefit_db)
    add_recipient(plan)
    plan["rotation_model"]["actions"][-1]["occupation_end"] = 4
    normal_hits(plan, "b", "bn", [2.2, 3.2], snapshot="bn")
    model = plan["mechanics_model"]
    condition(model, "change", 3)
    model["condition_events"][0]["repeat"] = dict(ONCE)
    trigger = {"event": "condition", "phase": "pre", "event_ids": ["change"]}
    for cid, value in (("a", 500), ("b", 900)):
        attach_mechanics_rules(plan, rule("panel-" + cid, "benefit.stat_add.atk", value, trigger=trigger,
            beneficiaries={"mode": "characters", "characters": [cid], "scope": "locked"}))
    review_manual_sources(plan, benefit_db)
    out = run_plan_calc(confirm_benefit(plan, benefit_db), benefit_db)
    result = out["result"]
    assert [h["expected"] for h in result["hits"] if h["id"].startswith("bn.")] == pytest.approx(
        [784.62718248367314, 969.533978104591425])
    samples = [r for r in result["sampling_trace"] if r["event_id"].startswith("bn.")]
    assert [r["source_value"] for r in samples] == [2000, 2500]
    assert [r["sample_t"] for r in samples] == [2.2, 3.2]
    assert result_bytes(result, out["result_receipt"], "json")


@pytest.mark.parametrize("field,value", [
    ("initial_effect", "unknown"), ("all_party_alive", False),
    ("all_party_in_apply_predicate", None), ("all_party_in_retention_predicate", False),
    ("no_external_quill_changes", None), ("hitlag_policy", "unknown"),
])
def test_review_unknown_scene_atomically_removes_delivery(benefit_db, field, value):
    plan = confirm_benefit(benefit_plan(benefit_db), benefit_db)
    plan["limited_hit_benefit_sources"][0]["scenario"][field] = value
    report = compose_equipment_plan(plan, benefit_db)
    row = report["limited_hit_benefit_compilations"][0]
    assert row["status"] == "pending" and row["required_confirmation"] is None
    assert row["rules"] == row["parts"] == row["initial_rules"] == []


@pytest.mark.parametrize("style", ["filtered", "quota", "conversion"])
def test_review_actual_provider_unsupported_contribution_prevents_source_confirmation(benefit_db, style):
    plan = benefit_plan(benefit_db)
    model = plan["mechanics_model"]
    value = {"kind": "stat", "subject": "owner", "stat": "hp", "factor": .1,
             "sample": "current"} if style == "conversion" else 500
    panel = rule("filtered-panel", "benefit.stat_add.atk", value)
    if style == "filtered":
        panel["effects"][0]["filter"] = {"event": "hit", "phase": "pre"}
    elif style == "quota":
        panel["quota"] = {"capacity": 1, "scope": "per_beneficiary", "refresh": "reset",
                          "consume": {"event": "hit", "phase": "pre"}}
    add(model, panel)
    row = compose_equipment_plan(plan, benefit_db)["limited_hit_benefit_compilations"][0]
    assert row["status"] == "pending" and row["required_confirmation"] is None
    assert any("攻击力" in i["message"] for i in row["issues"])


def test_review_energy_and_quill_share_actual_parent_and_export_offline(trigger_db, monkeypatch):
    plan = benefit_plan(trigger_db)
    plan["rotation_model"]["characters"][0].update(initial_energy=0, energy_capacity=100)
    source = source_declaration()
    source["origin_reference_id"] = origin(trigger_db, "hold", True)
    plan["energy_generation_sources"] = [source]
    plan = confirm_benefit(confirm_energy(plan, trigger_db), trigger_db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, trigger_db)
    assert plan == before
    result = out["result"]
    credit = [r for r in result["rotation_check"]["event_log"] if r["type"] == "energy_credit"]
    assert len(credit) == 1 and credit[0]["energy"][0]["gain"] == 12
    assert result["limited_hit_benefit_summary"]["consumed_hits"] == 1
    import sqlite3
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("Export must stay offline"))
    validate_limited_hit_benefit_receipt(out["plan"], result, out["report"], out["result_receipt"])
    assert result_bytes(result, out["result_receipt"], "xlsx")
