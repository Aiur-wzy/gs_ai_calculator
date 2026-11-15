"""Independent offline export checks of EC13b source/dispatch audit boundaries."""
from __future__ import annotations

import copy
import sqlite3

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook
from tests.test_freeze_export_review import workbook_sheets
from tests.test_periodic_actions_review import accept_periodic, source_bound_plan
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_reaction_sources_review import evaluate_numeric_cell
from tests.test_runtime_periodic_attack import periodic_db  # noqa: F401

# ruff: noqa: F811


@pytest.fixture
def calculated(periodic_db):
    return run_plan_calc(accept_periodic(source_bound_plan(periodic_db), periodic_db), periodic_db)


def test_review_exported_formulas_recompute_15_hit_oracle_without_cached_answers_or_database(calculated, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("offline export opened SQLite"))
    sheets = workbook_sheets(build_formula_workbook(calculated))
    values = [evaluate_numeric_cell(sheets, "逐事件", f"V{i}") for i in range(5, 20)]
    assert sum(values) == pytest.approx(1500.889481306076015, rel=1e-13)
    assert values == pytest.approx(([90.96299886703491] * 4 + [136.44449830055237]) * 3)
    assert all(not cell["formula"] for cell in sheets["周期攻击来源"].values())
    for kind in ("json", "csv", "xlsx"):
        assert result_bytes(calculated["result"], calculated["result_receipt"], kind)


@pytest.mark.parametrize("part", ["native_timing", "source_record", "raw_parent", "generated_time",
                                  "actual_pattern", "input_multiplier", "receipt_only"])
def test_review_workbook_rejects_single_layer_stale_source_dispatch_or_native(calculated, part):
    data = copy.deepcopy(calculated)
    if part == "native_timing":
        data["plan"]["action_event_profiles"][0]["lifecycle"]["instance_end_offset_seconds"] = 1.3
    elif part == "source_record":
        data["report"]["periodic_attack_compilations"][0]["channels"][0]["compiled_fields"]["multiplier"] = 999
    elif part == "raw_parent":
        data["result"]["periodic_attack_compilations"][0]["query"]["source_graph"]["creation_bundle"]["origin"]["operation"]["raw"]["unknown"] = True
    elif part == "generated_time":
        data["report"]["action_event_compilations"][0]["generated"][0]["t"] += .001
    elif part == "actual_pattern":
        data["report"]["mechanics_model"]["hit_patterns"][0]["application"]["landed"] = False
    elif part == "input_multiplier":
        data["report"]["mechanics_input_template"]["hits"][0]["multiplier"] = 999
    else:
        data["result_receipt"]["periodic_attacks"]["compiled_mechanics_model_fingerprint"] = "0" * 64
    with pytest.raises((FormulaWorkbookError, ValueError)):
        build_formula_workbook(data)


@pytest.mark.parametrize("kind", ["json", "csv", "xlsx"])
@pytest.mark.parametrize("part", ["both_source_and_receipt_removed", "raw_parent_rehash_outer_result", "dispatch_rehash_outer_result"])
def test_review_result_only_exports_keep_inner_provenance_checks_after_outer_rehash(calculated, kind, part):
    data = copy.deepcopy(calculated)
    result, receipt = data["result"], data["result_receipt"]
    if part == "both_source_and_receipt_removed":
        result.pop("periodic_attack_compilations")
        receipt.pop("periodic_attacks")
    elif part == "raw_parent_rehash_outer_result":
        result["periodic_attack_compilations"][0]["query"]["source_graph"]["creation_bundle"]["origin"]["operation"]["raw"]["unknown"] = True
        receipt["periodic_attacks"]["compilations_fingerprint"] = fingerprint(result["periodic_attack_compilations"])
    else:
        result["action_event_compilations"][0]["generated"][0]["landed"] = False
        dispatch_fp = fingerprint(result["action_event_compilations"])
        receipt["action_events"]["compilations_fingerprint"] = dispatch_fp
        receipt["periodic_attacks"]["dispatch_compilations_fingerprint"] = dispatch_fp
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(PlanError):
        result_bytes(result, receipt, kind)
