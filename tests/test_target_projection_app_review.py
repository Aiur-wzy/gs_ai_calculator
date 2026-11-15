"""EC08 independent native App/source/tuning/export integration checks."""
from __future__ import annotations

import copy
import sqlite3

import pytest
from test_fixed_source_plan import native, tuning
from test_freeze_export_review import workbook_sheets
from test_plan_target_projection import accept
from test_plan_target_projection import (
    target_plan as target_plan,  # noqa: PLC0414 -- pytest fixture re-export
)
from test_reaction_sources_review import evaluate_numeric_cell

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook


def confirm_match(plan, db, entry):
    plan["rotation_tuning"]["buff_hit_matches"] = [entry]
    preview = compose_equipment_plan(plan, db)
    row = next(r for r in preview["match_previews"] if r["id"] == entry["id"])
    entry.update(state="confirmed", basis_fingerprint=row["basis_fingerprint"])


def test_review_fixed_replace_mode_only_replaces_selected_effect_not_other_condition_stage(tmp_path):
    plan, db = native(tmp_path)
    entry = tuning()
    entry.pop("include_hit_ids")
    entry.pop("exclude_hit_ids")
    entry.update(mode="replace", hit_ids=["h0"])
    confirm_match(plan, db, entry)
    out = run_plan_calc(plan, db)
    # Only the first +.2 source effect is replaced. The separate frozen +.2 remains on h2.
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([54, 45, 54])
    rows = [r for r in out["result"]["target_condition_trace"] if r["cycle"] == 0]
    assert [(r["event_id"], r["automatic_value"], r["override"], r["applied"])
            for r in rows if r["effect_id"] == "ice"] == [
        ("h0", False, "include", True), ("h1", True, "exclude", False), ("h2", True, "exclude", False)]


@pytest.mark.parametrize("operation", ["include", "exclude"])
def test_review_confirmed_native_match_cannot_turn_missing_state_model_into_valid_damage(tmp_path, operation):
    plan, db = native(tmp_path)
    plan["mechanics_model"].pop("elemental_model")
    for desc in plan["mechanics_model"]["hit_events"]:
        desc.pop("application")
        desc.pop("contact")
    entry = tuning()
    entry.update(include_hit_ids=["h0"] if operation == "include" else [],
                 exclude_hit_ids=["h0"] if operation == "exclude" else [])
    confirm_match(plan, db, entry)
    with pytest.raises(PlanError) as caught:
        run_plan_calc(plan, db)
    assert caught.value.reason == "mechanics_pending"
    assert any(row["code"] == "unknown_target_state" for row in caught.value.details["issues"])


@pytest.mark.parametrize("change", ["target", "fixed_value", "requested_hits", "hit_timing"])
def test_review_fixed_match_basis_covers_actual_inputs_not_only_source_identifier(tmp_path, change):
    plan, db = native(tmp_path)
    entry = tuning()
    confirm_match(plan, db, entry)
    assert compose_equipment_plan(plan, db)["state"] == "ready"
    if change == "target":
        plan["mechanics_model"]["elemental_model"]["target"]["source"] += " changed"
    elif change == "fixed_value":
        plan["mechanics_model"]["fixed_effect_sources"][0]["effects"][0]["value"] = .3
    elif change == "requested_hits":
        entry["include_hit_ids"] = ["h2"]
    else:
        plan["manual_template"]["hits"][0]["t"] = .05
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert report["mechanics_matches"] == []
    assert next(row for row in report["match_previews"] if row["id"] == entry["id"])["basis_fingerprint"] != entry["basis_fingerprint"]


def test_review_native_output_cache_and_claimed_complete_coverage_cannot_replace_fresh_partial_source(target_plan):
    plan, db, _bundle = target_plan
    accept(plan, db)
    prior = run_plan_calc(plan, db)
    plan["resolved_template"] = copy.deepcopy(prior["plan"]["resolved_template"])
    plan["resolved_template"]["hits"][0]["multiplier"] = 999
    plan["resolution"] = copy.deepcopy(prior["plan"]["resolution"])
    plan["resolution"]["source_fingerprint"] = "fake-complete-source"
    plan["effect_bindings"][0]["coverage"]["state"] = "complete"
    out = run_plan_calc(plan, db)
    raw = .20000000298023224
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([45, 45*(1+raw), 45*(1+2*raw)])
    compiled = out["report"]["upstream_compilations"][0]
    assert compiled["whole_source_status"] == "partial"
    assert compiled["coverage_certificate"]["source_complete"] is False
    assert out["plan"]["effect_bindings"][0]["parts"] == []


def test_review_typed_manual_alias_cannot_double_deliver_same_upstream_affix(target_plan):
    plan, db, _ = target_plan
    accept(plan, db)
    other = copy.deepcopy(plan["effect_bindings"][0])
    other.pop("upstream_recipe")
    other.update(binding_id="manual-alias", owner={"kind": "manual", "note": "same real affix under alias"},
                 source_refs=[{"kind": "database", "table": "EquipAffixExcelConfigData",
                               "key": {"affixId": 2140011}, "version_line": "release"}])
    plan["effect_bindings"].append(other)
    with pytest.raises(PlanError) as caught:
        compose_equipment_plan(plan, db)
    assert caught.value.reason == "duplicate_delivery"


def test_review_withdrawn_equipment_does_not_keep_compiled_source_or_confirmed_match(target_plan):
    plan, db, _ = target_plan
    accept(plan, db)
    out = run_plan_calc(plan, db)
    src = out["result"]["fixed_effect_sources"][0]
    entry = tuning()
    entry["effect_ref"] = {"fixed_source_id": src["id"], "effect_id": src["effects"][0]["id"]}
    confirm_match(plan, db, entry)
    plan["equipment_inputs"]["a"]["artifact_sets"] = []
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert not report["mechanics_model"].get("fixed_effect_sources")
    assert report["mechanics_matches"] == []
    assert report["match_previews"][0]["basis_fingerprint"] is None


def test_review_actual_manual_damage_system_wins_over_old_ordinary_output_cache(target_plan):
    plan, db, _ = target_plan
    accept(plan, db)
    out = run_plan_calc(plan, db)
    plan["resolved_template"] = out["plan"]["resolved_template"]
    plan["manual_template"]["hits"][0]["damage_system"] = "stellar"
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert not report["mechanics_model"].get("fixed_effect_sources")


def test_review_formula_recalculates_source_bound_critical_damage_and_keeps_condition_ledger_readonly(target_plan, monkeypatch):
    plan, db, _ = target_plan
    accept(plan, db)
    out = run_plan_calc(plan, db)
    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: pytest.fail("export reopened DB after materialization"))
    sheets = workbook_sheets(build_formula_workbook(out))
    actual = [evaluate_numeric_cell(sheets, "逐事件", f"V{row}") for row in range(5, 8)]
    raw = .20000000298023224
    assert actual == pytest.approx([45, 45*(1+raw), 45*(1+2*raw)], abs=1e-10)
    assert all(not cell["formula"] for cell in sheets["目标条件收益"].values())


@pytest.mark.parametrize("field", ["source_receipt", "branch_receipt", "scope_certificate", "receipt_only"])
def test_review_formula_must_not_publish_foreign_source_receipt_next_to_same_numeric_trace(target_plan, field):
    plan, db, _ = target_plan
    accept(plan, db)
    out = run_plan_calc(plan, db)
    record = out["report"]["upstream_compilations"][0]
    if field == "source_receipt":
        record["provenance"]["source"]["upstream_commit"] = "foreign-commit"
    elif field == "branch_receipt":
        record["branches"][2]["raw"]["$type"] = "foreign-branch-type"
    elif field == "scope_certificate":
        record["coverage_certificate"]["source_complete"] = True
    else:
        out["result_receipt"]["upstream_sources"][0]["provenance"]["source"]["upstream_commit"] = "foreign-commit"
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(out)


def test_review_scope_rejection_revokes_projection_coverage_certificate(target_plan):
    plan, db, _ = target_plan
    accept(plan, db)
    plan["manual_template"]["hits"][0]["damage_system"] = "stellar"
    report = compose_equipment_plan(plan, db)
    record = report["upstream_compilations"][0]
    assert record["status"] == record["projection_status"] == "pending"
    assert record["coverage_certificate"]["projection_complete"] is False
    assert record["coverage_certificate"]["projection_status"] == "pending"
