"""Independent EC09A receipt tampering and emitted-formula checks."""
from __future__ import annotations

import copy
import sqlite3

import pytest
from test_action_events_review import accepted, review_action_plan
from test_action_events_review import (
    review_action_db as review_action_db,  # noqa: PLC0414 -- pytest fixture
)
from test_freeze_export_review import workbook_sheets
from test_reaction_sources_review import evaluate_numeric_cell

from genshin_app.equipment import run_plan_calc
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook
from tests.test_pipeline import (
    fake_repo as fake_repo,  # noqa: PLC0414 -- pytest fixture dependency
)
from tests.test_pipeline import (
    stub_fetcher as stub_fetcher,  # noqa: PLC0414 -- pytest fixture dependency
)


@pytest.fixture
def review_dispatch_output(review_action_db):
    plan = review_action_plan(review_action_db)
    accepted(plan, review_action_db)
    return run_plan_calc(plan, review_action_db)


def test_review_action_formulas_recalculate_from_fixed_scalar_and_never_query_db(review_dispatch_output, monkeypatch):
    untouched = copy.deepcopy(review_dispatch_output)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("export must use materialized sources"))
    content = build_formula_workbook(review_dispatch_output)
    sheets = workbook_sheets(content)
    # Independent oracle: 100 * 1.25 * .5 * .9 for each real input cast.
    values = [evaluate_numeric_cell(sheets, "逐事件", f"V{i}") for i in range(5, 8)]
    assert values == pytest.approx([56.25, 56.25, 56.25])
    assert sum(values) == pytest.approx(168.75)
    assert review_dispatch_output == untouched


@pytest.mark.parametrize("part", ["report-record", "result-record", "receipt-only", "profile-input",
                                  "compiled-model", "execution-model", "prototype-template",
                                  "parameter-record", "elemental-record", "reaction-record",
                                  "parameter-receipt-only"])
def test_review_single_layer_corruption_never_exports_a_mixed_source_workbook(review_dispatch_output, part):
    out = copy.deepcopy(review_dispatch_output)
    if part == "report-record":
        out["report"]["action_event_compilations"][0]["generated"][0]["landed"] = False
    elif part == "result-record":
        out["result"]["action_event_compilations"][0]["skill_coverage"] = "complete"
    elif part == "receipt-only":
        out["result_receipt"]["action_events"]["resource_coverage"] = "complete"
    elif part == "profile-input":
        out["plan"]["action_event_profiles"][0]["default_landed"] = False
    elif part == "compiled-model":
        out["report"]["mechanics_model"]["hit_patterns"][0]["orders"] = [123]
    elif part == "execution-model":
        out["report"]["action_event_execution_rotation_model"]["characters"][0]["energy_recharge"] = 7
    elif part == "prototype-template":
        out["report"]["mechanics_input_template"]["hits"][0]["multiplier"] = 999
    elif part == "parameter-record":
        out["report"]["hit_parameter_compilations"][0]["source"]["node_sha256"] = "0" * 64
    elif part == "elemental-record":
        out["report"].setdefault("elemental_compilations", []).append({"profile_id": "forged"})
    elif part == "reaction-record":
        out["report"].setdefault("reaction_damage_compilations", []).append({"profile_id": "forged"})
    else:
        out["result_receipt"]["hit_parameter_sources"][0]["source"]["node_sha256"] = "0" * 64
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(out)


def test_review_generated_cache_cannot_replace_fresh_native_dispatch(review_dispatch_output, review_action_db):
    plan = copy.deepcopy(review_dispatch_output["plan"])
    plan["resolved_template"]["hits"][0]["multiplier"] = 999
    plan["resolution"]["action_event_compilations"][0]["generated"][0]["landed"] = False
    output = run_plan_calc(plan, review_action_db)
    assert [hit["expected"] for hit in output["result"]["hits"]] == pytest.approx([56.25] * 3)
    assert output["plan"]["mechanics_model"]["hit_patterns"] == []
