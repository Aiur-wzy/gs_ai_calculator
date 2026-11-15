"""Reporting excludes tail values without deleting execution or review evidence."""
from __future__ import annotations

import copy
import json
import subprocess
import sys

import pytest

from genshin_app.calculation_integrity import validate_result_binding
from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.calculation_overrides.computed_values import (
    SCOPED_CONTRACT,
    _aggregation_projection,
    _audit_projection,
    validate_computed_value_result_receipt,
    validate_report_numeric_baselines,
)
from genshin_app.equipment.damage_report_scope import scoped_hits
from genshin_app.equipment.external_damage import (
    validate_external_damage_result_receipt,
)
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.formula_fixed_overrides import fixed_override_projection
from genshin_app.formula_workbook import (
    build_formula_workbook,
    validate_formula_calculation,
)
from tests.test_calculation_overrides_execution import (
    accept_from_compose,
    add_mechanics_hit_relation,
    declaration,
    native_plan,
    override_item,
    target,
)
from tests.test_external_damage import confirm, supplement
from tests.test_formula_workbook import read_sheet


def scoped_plan(end=.2):
    plan = native_plan(dormant_hit=True)
    plan["mechanics_model"]["hit_events"].append({
        "hit_id": "h1", "action_id": "cast", "order": 11,
    })
    add_mechanics_hit_relation(plan, "h1")
    plan["rotation_model"]["damage_report"] = {
        "contract": "damage-report-window-v1", "mode": "explicit", "end": end,
    }
    return plan


def fixed(hit, *, approximate=False):
    return override_item(
        "replace_computed_value", target("computed_event", hit, "result.hits"),
        {"expected": 999, "precision": {
            "kind": "approximate" if approximate else "exact",
            "method": "仅供报告边界工程验收", "uncertainty": "不对应游戏事实",
            "state_effects": "none",
        }}, suffix=hit,
    )


def roundtrip(calculated):
    result = json.loads(json_bytes(calculated))["result"]
    validate_result_binding(result, calculated["result_receipt"])
    return result


def assert_workbook_report(calculated):
    workbook = build_formula_workbook(calculated)
    summary = read_sheet(workbook, 1)
    totals = calculated["result"].get("calculation_totals") or calculated["result"]["summary"]
    labels = {"总期望伤害": "total_damage", "纸面 DPS": "dps"}
    found = set()
    for cell, data in summary.items():
        if cell.startswith("A") and data["value"] in labels:
            key = labels[data["value"]]
            assert summary["B" + cell[1:]]["value"] == pytest.approx(totals[key])
            found.add(key)
    assert found == {"total_damage", "dps"}


def test_external_tail_keeps_full_review_and_exact_cutoff_is_inclusive(tmp_path):
    plan = scoped_plan()
    plan["external_damage_supplements"] = [
        supplement(id="extra.cutoff", event_id="external.cutoff", coverage_key="coverage.cutoff", t=.2),
        supplement(id="extra.tail", event_id="external.tail", coverage_key="coverage.tail", t=.3,
                   precision={"kind": "approximate", "method": "工程尾段估算",
                              "uncertainty": "工程假设", "state_effects": "none"}),
    ]
    database = tmp_path / "absent.db"
    fresh = run_plan_calc(confirm(plan, database), database)
    result = roundtrip(fresh)
    assert len(result["external_damage"]["items"]) == 2
    assert result["summary"]["total_damage"] == 45
    assert result["calculation_totals"]["external_count"] == 1
    assert result["calculation_totals"]["external_excluded_count"] == 1
    assert result["calculation_totals"]["total_damage"] == 57
    assert result["calculation_totals"]["dps"] == 285
    assert result["calculation_totals"].get("precision", "exact") == "exact"
    assert fresh["result_receipt"]["external_damage"]["item_count"] == 2
    assert_workbook_report(fresh)


def test_external_tail_still_requires_source_acceptance(tmp_path):
    plan = scoped_plan()
    plan["external_damage_supplements"] = [supplement(t=.3)]
    pending = compose_equipment_plan(plan, tmp_path / "absent.db")
    assert "external_damage_confirmation_required" in {
        issue["code"] for issue in pending["missing_inputs"]
    }
    with pytest.raises(ValueError):
        run_plan_calc(plan, tmp_path / "absent.db")


def test_external_execution_period_boundary_is_new_contract_only(tmp_path):
    plan = scoped_plan(end=2)
    plan["external_damage_supplements"] = [supplement(t=2)]
    database = tmp_path / "absent.db"
    fresh = run_plan_calc(confirm(plan, database), database)
    assert fresh["result"]["calculation_totals"]["total_damage"] == 102
    assert fresh["result"]["calculation_totals"]["external_count"] == 1
    roundtrip(fresh)


@pytest.mark.parametrize("end,reported_ids", [(.1, []), (.2, ["h0"]), (.3, ["h0", "h1"])])
def test_fixed_values_preserve_audit_targets_without_changing_report_precision(tmp_path, end, reported_ids):
    plan = scoped_plan(end)
    plan["calculation_event_overrides"] = declaration(fixed("h0"), fixed("h1", approximate=True))
    database = tmp_path / "absent.db"
    accept_from_compose(plan, database)
    fresh = run_plan_calc(plan, database)
    result = roundtrip(fresh)
    assert [h["id"] for h in result["hits"]] == reported_ids
    assert result["summary"]["total_damage"] == 999 * len(reported_ids)
    assert result["summary"]["dps"] == 999 * len(reported_ids) / end
    assert result["summary"]["precision"] == ("approximate" if "h1" in reported_ids else "exact")
    proof = result["calculation_computed_value_overrides"]
    assert proof["contract"] == SCOPED_CONTRACT
    assert len(proof["rows"]) == 2
    assert proof["audit_aggregation"]["event_ids"] == [h for h in ("h0", "h1") if h not in reported_ids]
    assert all(row["automatic_expected"] == 45 for row in proof["rows"])
    assert proof["aggregation"]["window"]["source"] == "damage_report_window.end"
    projected = fixed_override_projection(result, fresh["result_receipt"])
    assert set(projected["rows"]) == set(reported_ids)
    assert set(projected["audit_rows"]) == {"h0", "h1"} - set(reported_ids)
    assert_workbook_report(fresh)


def _resign_computed(result, receipt):
    proof = result["calculation_computed_value_overrides"]
    proof["ledger_fingerprint"] = fingerprint(proof["rows"])
    proof["proof_fingerprint"] = fingerprint({k: v for k, v in proof.items() if k != "proof_fingerprint"})
    receipt["calculation_computed_value_overrides"] = copy.deepcopy(proof)
    receipt["result_fingerprint"] = fingerprint(result)


def test_tail_automatic_baseline_cannot_be_forged_even_if_local_hashes_are_rewritten(tmp_path):
    plan = scoped_plan()
    plan["calculation_event_overrides"] = declaration(fixed("h1", approximate=True))
    database = tmp_path / "absent.db"
    accept_from_compose(plan, database)
    fresh = run_plan_calc(plan, database)
    result, receipt = copy.deepcopy(fresh["result"]), copy.deepcopy(fresh["result_receipt"])
    result["damage_report_audit_hits"][0]["calculation_override"]["automatic_expected"] = 888
    result["calculation_computed_value_overrides"]["rows"][0]["automatic_expected"] = 888
    _resign_computed(result, receipt)
    with pytest.raises(ValueError, match="基线"):
        validate_computed_value_result_receipt(result, receipt)


@pytest.mark.parametrize("ledger", ["hits", "damage_report_audit_hits"])
def test_unowned_automatic_number_tamper_is_detected_for_both_ledgers(tmp_path, ledger):
    fresh = run_plan_calc(scoped_plan(), tmp_path / "absent.db")
    result = copy.deepcopy(fresh["result"])
    result[ledger][0]["expected"] += 1
    with pytest.raises(ValueError, match="自动数值"):
        validate_report_numeric_baselines(result, fresh["result"])


def test_tail_supplement_does_not_turn_failed_required_execution_into_report(tmp_path):
    plan = scoped_plan()
    plan["external_damage_supplements"] = [supplement(t=.3)]
    database = tmp_path / "absent.db"
    fresh = run_plan_calc(confirm(plan, database), database)
    result = copy.deepcopy(fresh["result"])
    result["damage_report_window"]["required_validation"]["status"] = "failed"
    with pytest.raises(ValueError, match="必需动作"):
        validate_external_damage_result_receipt(result, fresh["result_receipt"])


def test_report_cannot_hide_unowned_audit_identity(tmp_path):
    fresh = run_plan_calc(scoped_plan(), tmp_path / "absent.db")
    result = copy.deepcopy(fresh["result"])
    result["damage_report_audit_hits"][0]["id"] = "h0"
    with pytest.raises(ValueError, match="重复"):
        validate_report_numeric_baselines(result, fresh["result"])


def test_optional_later_cycle_failure_does_not_block_complete_required_origin(tmp_path):
    plan = scoped_plan()
    rotation = plan["rotation_model"]
    rotation["actions"][0]["repeat"] = {"mode": "each_cycle"}
    rotation["abilities"][0]["cooldown"] = {
        "group": "e", "duration": 3, "charges": 1,
        "start": "action_start", "recovery": "sequential",
    }
    rotation["initial_cooldowns"] = [{"character": "a", "group": "e", "ready_in": []}]
    plan["external_damage_supplements"] = [supplement(t=.2)]
    database = tmp_path / "absent.db"
    fresh = run_plan_calc(confirm(plan, database), database)
    result = roundtrip(fresh)
    assert result["damage_report_window"]["required_validation"]["status"] == "complete"
    assert result["damage_report_window"]["optional_audit"]["status"] == "failed"
    assert result["calculation_totals"]["total_damage"] == 57


def test_required_tail_action_failure_cannot_be_hidden_by_shorter_report(tmp_path):
    plan = scoped_plan()
    rotation = plan["rotation_model"]
    rotation["actions"].append({**rotation["actions"][0], "id": "required.tail",
                                "start": .5, "occupation_end": .6, "order": 2})
    rotation["abilities"][0]["cooldown"] = {
        "group": "e", "duration": 3, "charges": 1,
        "start": "action_start", "recovery": "sequential",
    }
    rotation["initial_cooldowns"] = [{"character": "a", "group": "e", "ready_in": []}]
    plan["external_damage_supplements"] = [supplement(t=.2)]
    database = tmp_path / "absent.db"
    confirm(plan, database)
    with pytest.raises(ValueError):
        run_plan_calc(plan, database)


def test_fixed_tail_and_external_tail_share_one_report_without_double_count(tmp_path):
    plan = scoped_plan()
    plan["calculation_event_overrides"] = declaration(fixed("h1", approximate=True))
    plan["external_damage_supplements"] = [supplement(t=.3)]
    database = tmp_path / "absent.db"
    accept_from_compose(plan, database)
    confirm(plan, database)
    fresh = run_plan_calc(plan, database)
    result = roundtrip(fresh)
    assert result["summary"]["total_damage"] == 45
    assert result["calculation_totals"]["total_damage"] == 45
    assert result["calculation_totals"]["external_count"] == 0
    assert result["damage_report_audit_hits"][0]["expected"] == 999
    assert result["summary"]["precision"] == "exact"
    assert_workbook_report(fresh)


def test_missing_anchor_diagnostic_can_be_saved_fresh_read_and_explained_without_dps(tmp_path):
    plan = scoped_plan()
    plan["rotation_model"]["damage_report"] = {
        "contract": "damage-report-window-v1", "mode": "last_active_damage",
    }
    fresh = run_plan_calc(plan, tmp_path / "absent.db")
    assert fresh["completion"]["task_admission"] == "pending"
    assert fresh["result"]["damage_report_window"]["end"] is None
    assert fresh["result"]["summary"]["dps"] is None
    source = tmp_path / "diagnostic.calculated.json"
    source.write_bytes(json_bytes(fresh))
    probe = subprocess.run([sys.executable, "-c", (
        "import json,sys; from pathlib import Path; "
        "from genshin_app.calculation_integrity import validate_result_binding; "
        "r=json.loads(Path(sys.argv[1]).read_text(encoding='utf8')); "
        "validate_result_binding(r['result'],r['result_receipt']); "
        "assert r['result']['summary']['dps'] is None; print('diagnostic-readable')"
    ), str(source)], check=False, capture_output=True, text=True, timeout=30)
    assert probe.returncode == 0, probe.stderr
    assert probe.stdout.strip() == "diagnostic-readable"
    summary = read_sheet(build_formula_workbook(fresh), 1)
    dps_cells = [summary["B" + cell[1:]]["value"] for cell, value in summary.items()
                 if cell.startswith("A") and value["value"] == "纸面 DPS"]
    assert len(dps_cells) == 1 and not isinstance(dps_cells[0], (int, float))


def test_pending_window_still_refuses_external_values(tmp_path):
    plan = scoped_plan()
    plan["rotation_model"]["damage_report"] = {
        "contract": "damage-report-window-v1", "mode": "last_active_damage",
    }
    plan["external_damage_supplements"] = [supplement(t=.3)]
    database = tmp_path / "absent.db"
    confirm(plan, database)
    with pytest.raises(ValueError, match="未完成"):
        run_plan_calc(plan, database)


@pytest.mark.parametrize("hit_id", ["h0", "h1"])
@pytest.mark.parametrize("field", ["value", "precision", "source"])
def test_locally_resigned_fixed_values_remain_bound_to_accepted_overlay(tmp_path, hit_id, field):
    plan = scoped_plan()
    plan["calculation_event_overrides"] = declaration(fixed(hit_id))
    database = tmp_path / "absent.db"
    accept_from_compose(plan, database)
    fresh = run_plan_calc(plan, database)
    result, receipt = copy.deepcopy(fresh["result"]), copy.deepcopy(fresh["result_receipt"])
    proof = result["calculation_computed_value_overrides"]
    row = proof["rows"][0]
    _, members = scoped_hits(result)
    hit = members[hit_id][0]
    if field == "value":
        row["effective_expected"] = hit["expected"] = 777
        hit["calculation_override"]["effective_expected"] = 777
    elif field == "precision":
        row["precision"]["method"] = "擅自替换受审方法"
        hit["calculation_override"]["precision"] = copy.deepcopy(row["precision"])
    else:
        row["source_fingerprint"] = "f" * 64
    # Recompute all local arithmetic and hashes, while retaining the actual
    # accepted plan, graph and execution receipt unchanged.
    proof["aggregation"] = _aggregation_projection(result, proof["rows"], proof["aggregation"]["window"])
    proof["audit_aggregation"] = _audit_projection(proof["rows"], members)
    result["summary"].update({key: copy.deepcopy(value) for key, value in proof["aggregation"].items() if key != "window"})
    _resign_computed(result, receipt)
    fresh["result"], fresh["result_receipt"] = result, receipt
    with pytest.raises(ValueError, match="受审"):
        validate_computed_value_result_receipt(result, receipt)
    with pytest.raises(ValueError):
        validate_result_binding(result, receipt)
    with pytest.raises(ValueError):
        validate_formula_calculation(fresh)


def test_deleting_tail_computed_proof_cannot_silently_restore_automatic_value(tmp_path):
    plan = scoped_plan()
    plan["calculation_event_overrides"] = declaration(fixed("h1"))
    database = tmp_path / "absent.db"
    accept_from_compose(plan, database)
    fresh = run_plan_calc(plan, database)
    result, receipt = fresh["result"], fresh["result_receipt"]
    row = result["calculation_computed_value_overrides"]["rows"][0]
    hit = result["damage_report_audit_hits"][0]
    hit["expected"], hit["crit"] = row["automatic_expected"], row["automatic_crit"]
    del hit["calculation_override"]
    del result["calculation_computed_value_overrides"]
    del receipt["calculation_computed_value_overrides"]
    del receipt["calculation_computed_value_window"]
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError, match="缺少实际执行证明"):
        validate_computed_value_result_receipt(result, receipt)
    with pytest.raises(ValueError):
        validate_result_binding(result, receipt)
    with pytest.raises(ValueError):
        validate_formula_calculation(fresh)
