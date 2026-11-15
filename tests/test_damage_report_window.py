import copy

import pytest
from test_mechanics_execution import action, fixture

from genshin_engine.damage_report_window import CONTRACT
from genshin_engine.mechanics import execute_mechanics


def explicit(data, end):
    data[1]["damage_report"] = {"contract": CONTRACT, "mode": "explicit", "end": end}
    return data


def test_explicit_report_includes_cutoff_and_leaves_period_and_audit_untouched():
    data = explicit(fixture((.2, .3, .4)), .3)
    before = copy.deepcopy(data)
    out = execute_mechanics(*data)
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert [h["id"] for h in out["result"]["hits"]] == ["h0", "h1"]
    assert out["result"]["summary"]["dps"] == pytest.approx(90 / .3)
    assert out["resolved_template"]["loop_duration"] == .3
    report = out["result"]["damage_report_window"]
    assert report["execution_period"] == 10
    assert report["required_validation"]["status"] == "complete"
    assert data == before


def test_default_cutoff_uses_explicit_active_component_not_off_field_last_hit():
    data = fixture((.2, .3, 9))
    data[1]["damage_report"] = {"contract": CONTRACT, "mode": "last_active_damage", "active_hit_ids": ["h0", "h1"]}
    out = execute_mechanics(*data)
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert out["result"]["damage_report_window"]["end"] == .3
    assert out["result"]["summary"]["total_damage"] == pytest.approx(90)
    assert any(r.get("event_id") == "h2" for r in out["mechanics_trace"])


def test_missing_direct_anchor_remains_repairable_pending():
    data = fixture((.2,))
    data[1]["damage_report"] = {"contract": CONTRACT, "mode": "last_active_damage"}
    out = execute_mechanics(*data)
    assert out["status"] == "pending"
    assert out["result"]["summary"]["dps"] is None
    assert any(r["code"] == "damage_report_anchor_missing" for r in out["issues"])


def test_refused_final_required_action_cannot_fall_back_to_previous_damage():
    data = fixture((.2,))
    data[1]["actions"].append(action("late", start=1, end=1.1, order=3))
    data[1]["abilities"][0]["cooldown"] = {"group": "e", "duration": 10, "start": "action_start", "charges": 1, "recovery": "parallel"}
    data[1]["initial_cooldowns"] = [{"character": "a", "group": "e", "ready_in": []}]
    data[1]["damage_report"] = {"contract": CONTRACT, "mode": "last_active_damage", "active_hit_ids": ["h0"]}
    out = execute_mechanics(*data)
    assert out["status"] in ("failed", "pending"), out["issues"]
    assert out["result"]["damage_report_window"]["required_validation"]["status"] != "complete"
    assert out["result"]["summary"]["dps"] is None


def test_legacy_declared_duration_is_not_silently_reinterpreted():
    out = execute_mechanics(*fixture((.2,)))
    assert "damage_report_window" not in out["result"]
    assert out["result"]["summary"]["dps"] == pytest.approx(4.5)


def test_public_native_calculation_and_fresh_read_keep_report_window(tmp_path):
    import json

    from test_calculation_overrides_execution import native_plan

    from genshin_app.calculation_integrity import validate_result_binding
    from genshin_app.equipment.files import json_bytes
    from genshin_app.equipment.service import run_plan_calc

    plan = native_plan()
    plan["rotation_model"]["damage_report"] = {"contract": CONTRACT, "mode": "explicit", "end": .2}
    calculated = run_plan_calc(plan, tmp_path / "absent.db")
    assert calculated["result"]["summary"]["dps"] == pytest.approx(225)
    output = tmp_path / "window.calculated.json"
    output.write_bytes(json_bytes(calculated))
    restored = json.loads(output.read_text(encoding="utf-8"))
    validate_result_binding(restored["result"], restored["result_receipt"])
    assert restored["result"]["damage_report_window"] == calculated["result"]["damage_report_window"]


def test_saved_report_cutoff_energy_uses_real_ledger_not_execution_tail(tmp_path):
    from test_calculation_overrides_execution import EVIDENCE, ONCE, native_plan

    from genshin_app.calculation_assistance import inspect_calculation_assistance
    from genshin_app.calculation_integrity import validate_result_binding
    from genshin_app.equipment import run_plan_calc

    plan = native_plan()
    rotation = plan["rotation_model"]
    rotation["damage_report"] = {"contract": CONTRACT, "mode": "explicit", "end": .2}
    rotation["characters"][0].update(energy_capacity=20, initial_energy=8)
    rotation["abilities"][0]["energy"] = {"required": 5, "amount": 5}
    rotation["events"] = [
        {"id": "cast-cost", "type": "energy_spend", "t": 0, "order": 1, "repeat": ONCE,
         "action_id": "cast", "character": "a", "required": 5, "amount": 5, **EVIDENCE},
        {"id": "on-cutoff", "type": "energy_direct", "t": .2, "order": 20, "repeat": ONCE,
         "amounts": {"a": 2}, **EVIDENCE},
        {"id": "after-cutoff", "type": "energy_direct", "t": .4, "order": 21, "repeat": ONCE,
         "amounts": {"a": 7}, **EVIDENCE},
    ]
    saved = run_plan_calc(plan, tmp_path / "absent.db")
    before = copy.deepcopy(saved)
    feedback = inspect_calculation_assistance(saved, {"view": "energy"})["energy"]
    assert feedback["report_cutoff"]["t"] == .2
    assert feedback["report_cutoff"]["balances"] == {"a": 5}
    assert feedback["characters"][0]["final_energy"] == 12
    assert saved == before
    validate_result_binding(saved["result"], saved["result_receipt"])
    saved["result"]["damage_report_window"]["status"] = "pending"
    assert inspect_calculation_assistance(saved, {"view": "energy"})["energy"]["report_cutoff"]["balances"] is None


def test_explicit_empty_report_window_has_zero_damage_not_missing_execution(tmp_path):
    from test_calculation_overrides_execution import native_plan

    from genshin_app.equipment.service import run_plan_calc

    plan = native_plan()
    plan["rotation_model"]["damage_report"] = {"contract": CONTRACT, "mode": "explicit", "end": .1}
    calculated = run_plan_calc(plan, tmp_path / "absent.db")
    assert calculated["result"]["summary"]["total_damage"] == 0
    assert calculated["result"]["summary"]["dps"] == 0


@pytest.mark.parametrize("end", [.1, .2])
def test_window_proof_survives_export_and_rejects_self_resigned_scope_tamper(tmp_path, end):
    from test_calculation_overrides_execution import native_plan

    from genshin_app.equipment.damage_report_window_receipt import (
        validate_window_calculation,
    )
    from genshin_app.equipment.files import fingerprint
    from genshin_app.equipment.service import run_plan_calc
    from genshin_app.formula_workbook import validate_formula_calculation

    plan = native_plan()
    plan["rotation_model"]["damage_report"] = {"contract": CONTRACT, "mode": "explicit", "end": end}
    fresh = run_plan_calc(plan, tmp_path / "absent.db")
    validate_formula_calculation(fresh)
    modified = copy.deepcopy(fresh)
    modified["result"]["damage_report_window"]["end"] += .01
    modified["result_receipt"]["result_fingerprint"] = fingerprint(modified["result"])
    with pytest.raises(ValueError, match="统计截止"):
        validate_window_calculation(modified["plan"], modified["result"], modified["report"], modified["result_receipt"])


def test_report_at_execution_boundary_includes_real_hit_without_adding_time():
    data = explicit(fixture((10,)), 10)
    out = execute_mechanics(*data)
    assert out["status"] in ("passed", "unproven"), out["issues"]
    assert [h["t"] for h in out["result"]["hits"]] == [10]
    assert out["result"]["summary"]["dps"] == pytest.approx(4.5)


def test_public_automatic_window_exports_formula_without_changing_execution_period(tmp_path):
    from test_calculation_overrides_execution import native_plan

    from genshin_app.equipment.service import run_plan_calc
    from genshin_app.formula_workbook import build_formula_workbook

    plan = native_plan()
    plan["rotation_model"]["damage_report"] = {"contract": CONTRACT, "mode": "last_active_damage", "active_hit_ids": ["h0"]}
    fresh = run_plan_calc(plan, tmp_path / "absent.db")
    assert fresh["plan"]["resolved_template"]["loop_duration"] == .2
    assert fresh["plan"]["rotation_model"]["period"] == 2
    assert build_formula_workbook(fresh).startswith(b"PK")


@pytest.mark.parametrize("field", ["total_damage", "dps", "by_character", "by_skill"])
def test_window_summary_cannot_be_self_resigned_away_from_real_hits(tmp_path, field):
    from test_calculation_overrides_execution import native_plan

    from genshin_app.equipment.files import fingerprint
    from genshin_app.equipment.service import run_plan_calc
    from genshin_app.formula_workbook import validate_formula_calculation

    plan = native_plan()
    plan["rotation_model"]["damage_report"] = {"contract": CONTRACT, "mode": "explicit", "end": .2}
    fresh = run_plan_calc(plan, tmp_path / "absent.db")
    if isinstance(fresh["result"]["summary"][field], dict):
        fresh["result"]["summary"][field]["fabricated"] = 100
    else:
        fresh["result"]["summary"][field] += 100
    fresh["result_receipt"]["result_fingerprint"] = fingerprint(fresh["result"])
    with pytest.raises(ValueError, match="统计截止"):
        validate_formula_calculation(fresh)


def test_reporting_contract_does_not_turn_an_empty_event_draft_into_zero_result():
    data = explicit(fixture(()), 1)
    out = execute_mechanics(*data)
    assert out["resolved_template"] is None
    assert out["result"] is None or out["result"]["summary"]["dps"] is None


def test_explicit_window_can_report_actual_all_missed_contacts_as_zero():
    from test_verified_empty_damage_engine import missed_fixture
    data = explicit(missed_fixture(), .1)
    out = execute_mechanics(*data)
    assert out["resolved_template"]["loop_duration"] == .1
    assert out["result"]["damage_report_window"]["status"] == "complete"
    assert out["result"]["summary"]["dps"] == 0
    assert "damage_reporting" not in out["result"]


def test_changing_report_end_does_not_shift_delayed_source_or_snapshot_cycle():
    from test_mechanics_execution import add, rule

    data = fixture((12, 21))
    data[0]["loop_duration"] = data[1]["period"] = 20
    data[1]["characters"][0].update(initial_energy=0, energy_capacity=100, energy_recharge=2)
    data[2]["hit_events"][1]["self_snapshot_event"] = "cast"
    add(data[2], rule(resources=[{"id": "refund", "kind": "energy_direct", "delay": 21,
        "amounts": {"a": 2}, "source_part": "refund"}]))
    data[2]["resource_ownership"] = [{"rule_id": "boost", "action_id": "cast", "source_part": "refund", "replaces_event_ids": []}]
    data[1]["damage_report"] = {"contract": CONTRACT, "mode": "explicit", "end": 12}
    first = execute_mechanics(*data)
    data[1]["damage_report"]["end"] = 15
    second = execute_mechanics(*data)
    assert first["status"] in ("passed", "unproven"), first["issues"]
    assert first["rotation_check"]["event_log"] == second["rotation_check"]["event_log"]
    assert first["sampling_trace"] == second["sampling_trace"]
    credit = next(row for row in first["rotation_check"]["event_log"] if row["type"] == "energy_direct" and row["t"] == 21)
    assert credit["source_cycle"] == 0
    assert first["result"]["damage_report_window"]["execution_period"] == 20
    assert first["result"]["summary"]["total_damage"] == second["result"]["summary"]["total_damage"]
