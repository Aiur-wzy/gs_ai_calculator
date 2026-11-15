"""Independent arithmetic and stale-layer checks for EC11 materialized exports."""
from __future__ import annotations

import copy
import sqlite3

import pytest
from test_freeze_export_review import workbook_sheets
from test_reaction_benefits_app_review import accept
from test_reaction_benefits_app_review import swirl_plan as swirl_plan  # noqa: PLC0414
from test_reaction_benefits_review import SWIRL
from test_reaction_sources_review import evaluate_numeric_cell

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)


@pytest.fixture
def benefit_output(swirl_plan):
    plan, db, _ = swirl_plan
    accept(plan, db)
    return run_plan_calc(plan, db)


def test_review_double_reaction_workbook_recalculates_all_five_rows_without_db(benefit_output, monkeypatch):
    before = copy.deepcopy(benefit_output)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("export opened a DB"))
    monkeypatch.setattr(sqlite3.dbapi2, "connect", lambda *a, **k: pytest.fail("export opened a DB"))
    assert formula_workbook_support(benefit_output)["editable"]["reaction_benefit_sources"] == "regenerate_required"
    sheets = workbook_sheets(build_formula_workbook(benefit_output))
    values = [evaluate_numeric_cell(sheets, "逐事件", f"V{i}") for i in range(5, 10)]
    assert values == pytest.approx([45, 45, 45, SWIRL, SWIRL])
    assert sum(values) == pytest.approx(3329.644855882859)
    assert "反应收益来源" in sheets
    assert all(not cell["formula"] for cell in sheets["反应收益来源"].values())
    cells = sheets["参数"]
    source_rows = [key[1:] for key, cell in cells.items() if key.startswith("A") and isinstance(cell["value"], str)
        and cell["value"].endswith(".value") and cell["value"].startswith(("mechanics_input.buffs[", "mechanics_model.rules["))]
    assert len(source_rows) == 3
    assert all(cells["E" + row]["value"] == "回 App 重新生成" for row in source_rows)
    assert all(cells["I" + row]["formula"] not in (None, "0") for row in source_rows)
    for fmt in ("json", "csv", "xlsx"):
        assert result_bytes(benefit_output["result"], benefit_output["result_receipt"], fmt)
    assert benefit_output == before


@pytest.mark.parametrize("change", ["shared-receipt-provenance", "shared-receipt-coverage", "shared-receipt-certificate",
    "shared-receipt-branches", "projection-coverage",
    "branch-omission", "static-owner", "static-domain", "rule-source", "rule-timer", "initial-state",
    "source-acceptance", "native-initial", "result-compiled", "receipt-model"])
def test_review_formula_rejects_one_stale_layer_even_when_numeric_results_are_unchanged(benefit_output, change):
    # deepcopy of the whole tree preserves cross-layer aliases. Detach layers so
    # each mutation here is genuinely confined to the field under review.
    out = {key: copy.deepcopy(value) for key, value in benefit_output.items()}
    compiled = out["report"]["upstream_compilations"][0]
    if change == "shared-receipt-provenance":
        out["result_receipt"]["upstream_sources"][0]["provenance"]["unreviewed"] = True
    elif change == "shared-receipt-coverage":
        out["result_receipt"]["upstream_sources"][0]["whole_source_status"] = "complete"
    elif change == "shared-receipt-certificate":
        out["result_receipt"]["upstream_sources"][0]["coverage_certificate"]["source_complete"] = True
    elif change == "shared-receipt-branches":
        out["result_receipt"]["upstream_sources"][0]["branches"] = []
    elif change == "projection-coverage":
        compiled["coverage_certificate"]["source_complete"] = True
    elif change == "branch-omission":
        compiled["branches"].pop()
    elif change == "static-owner":
        out["report"]["mechanics_input_template"]["buffs"][0]["target"] = None
    elif change == "static-domain":
        out["report"]["mechanics_input_template"]["buffs"][0]["effects"][0]["zone"] = "dmg_bonus"
    elif change == "rule-source":
        out["report"]["mechanics_model"]["rules"][0]["source_id"] = "unrelated:part"
    elif change == "rule-timer":
        out["report"]["mechanics_model"]["rules"][0]["timing"]["duration"] = 12
    elif change == "initial-state":
        out["report"]["mechanics_model"]["initial_state"]["rules"][0]["cooldown_remaining"] = 1
    elif change == "source-acceptance":
        compiled["required_confirmation"]["accepted_assumptions_fingerprint"] = "0" * 64
    elif change == "native-initial":
        out["plan"]["effect_bindings"][0]["upstream_recipe"]["scenario"]["initial_source_effects"] = "unknown"
    elif change == "result-compiled":
        out["result"]["reaction_benefit_compilations"][0]["status"] = "pending"
    else:
        out["result_receipt"]["reaction_benefits"]["compiled_mechanics_model_fingerprint"] = "0" * 64
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(out)


@pytest.mark.parametrize("fmt", ["json", "csv", "xlsx"])
def test_review_snapshot_formats_reject_changed_compiled_branch(benefit_output, fmt):
    out = {key: copy.deepcopy(value) for key, value in benefit_output.items()}
    out["result"]["reaction_benefit_compilations"][0]["branches"].pop()
    with pytest.raises(PlanError):
        result_bytes(out["result"], out["result_receipt"], fmt)


@pytest.mark.parametrize("fmt", ["json", "csv", "xlsx"])
def test_review_snapshot_receipt_cannot_claim_complete_source_for_partial_records(benefit_output, fmt):
    out = {key: copy.deepcopy(value) for key, value in benefit_output.items()}
    out["result_receipt"]["upstream_sources"][0]["whole_source_status"] = "complete"
    with pytest.raises(PlanError):
        result_bytes(out["result"], out["result_receipt"], fmt)


def test_review_source_recipe_left_inactive_after_equipment_removal_can_export_baseline(swirl_plan):
    plan, db, _ = swirl_plan
    accept(plan, db)
    plan["equipment_inputs"]["a"]["artifact_sets"] = []
    out = run_plan_calc(plan, db)
    assert out["report"]["upstream_compilations"][0]["status"] == "inactive"
    sheets = workbook_sheets(build_formula_workbook(out))
    assert "反应收益来源" not in sheets
    assert [evaluate_numeric_cell(sheets, "逐事件", f"V{i}") for i in range(5, 10)] == pytest.approx(
        [45, 45, 45, 781.299, 781.299])
