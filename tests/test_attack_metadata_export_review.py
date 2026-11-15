"""Independent EC10 metadata receipt corruption boundaries for formula exports."""
from __future__ import annotations

import copy
import sqlite3

import pytest
from test_attack_metadata_review import EXPECTED_HIT, confirmed_metadata_output
from test_attack_metadata_review import (
    review_metadata_db as review_metadata_db,  # noqa: PLC0414
)
from test_freeze_export_review import workbook_sheets
from test_reaction_sources_review import evaluate_numeric_cell
from test_runtime_attack_metadata import metadata_db as metadata_db  # noqa: PLC0414

from genshin_app import formula_workbook
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_pipeline import fake_repo as fake_repo  # noqa: PLC0414
from tests.test_pipeline import stub_fetcher as stub_fetcher  # noqa: PLC0414


@pytest.fixture
def review_metadata_output(review_metadata_db):
    return confirmed_metadata_output(review_metadata_db)


def test_review_metadata_workbook_recalculates_formulas_and_keeps_source_values_fixed_without_db(review_metadata_output, monkeypatch):
    original = copy.deepcopy(review_metadata_output)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("metadata export attempted DB read"))
    support = formula_workbook_support(review_metadata_output)
    assert support["editable"]["attack_metadata_sources"] == "regenerate_required"
    assert support["editable"]["attack_metadata_provenance"] == "readonly_snapshot"
    sheets = workbook_sheets(build_formula_workbook(review_metadata_output))
    values = [evaluate_numeric_cell(sheets, "逐事件", f"V{i}") for i in range(5, 8)]
    assert values == pytest.approx([EXPECTED_HIT]*3)
    assert sum(values) == pytest.approx(581.2560224533081)
    assert "攻击元信息来源" in sheets
    assert all(not cell["formula"] for cell in sheets["攻击元信息来源"].values())
    parameters = sheets["参数"]
    multiplier_rows = [cell[1:] for cell, value in parameters.items() if cell.startswith("A")
                       and isinstance(value["value"], str) and value["value"].startswith("hits[")
                       and value["value"].endswith(".multiplier")]
    assert len(multiplier_rows) == 3
    assert all(parameters[f"E{row}"]["value"] == "回 App 重新生成" for row in multiplier_rows)
    assert all(parameters[f"I{row}"]["formula"] not in (None, "0") for row in multiplier_rows)
    for fmt in ("json", "csv", "xlsx"):
        assert result_bytes(review_metadata_output["result"], review_metadata_output["result_receipt"], fmt,
                            equipment_summary=review_metadata_output["equipment_summary"])
    assert review_metadata_output == original


@pytest.mark.parametrize("part", ["report-record", "result-record", "receipt-compiler", "receipt-input",
                                  "query-dependency", "source-fingerprint", "native-source",
                                  "compiled-application", "action-link", "record-unit"])
def test_review_metadata_export_never_mixes_stale_or_single_layer_changed_sources(review_metadata_output, part):
    out = copy.deepcopy(review_metadata_output)
    record = out["report"]["attack_metadata_compilations"][0]
    if part == "report-record":
        record["whole_source_status"] = "complete"
    elif part == "result-record":
        out["result"]["attack_metadata_compilations"][0]["status"] = "pending"
    elif part == "receipt-compiler":
        out["result_receipt"]["attack_metadata"]["compiler"] = "unreviewed-compiler"
    elif part == "receipt-input":
        out["result_receipt"]["attack_metadata"]["input_sources_fingerprint"] = "0" * 64
    elif part == "query-dependency":
        record["query"]["semantic_dependency"]["forged_unit_policy"] = "tax_twice"
    elif part == "source-fingerprint":
        record["source_fingerprint"] = "0" * 64
    elif part == "native-source":
        out["plan"]["attack_metadata_sources"][0]["counter_tag"] = "different-shared-state"
    elif part == "compiled-application":
        app_id = record["application_profile_id"]
        app = next(p for p in out["report"]["mechanics_model"]["elemental_model"]["profiles"] if p["id"] == app_id)
        app["gauge_units"] = 4
    elif part == "action-link":
        out["report"]["action_event_compilations"][0]["metadata_source_fingerprint"] = "0" * 64
    else:
        record["projection"]["gauge"]["tax_applied"] = True
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(out)


def _refresh_attack_metadata_test_receipts(calculated):
    """Keep coarse hashes coherent so tests reach the semantic contract guard."""
    report = calculated["report"]
    result = calculated["result"]
    receipt = calculated["result_receipt"]
    result_records = result["attack_metadata_compilations"]
    report_records = report["attack_metadata_compilations"]
    assert result_records == report_records
    records_by_profile = {row["action_event_profile_id"]: row for row in report_records}
    for event in report["action_event_compilations"]:
        record = records_by_profile[event["profile_id"]]
        event["metadata_dependency_fingerprint"] = record["metadata_dependency_fingerprint"]
    compilation_fingerprint = fingerprint(result_records)
    report["attack_metadata_compilation_fingerprint"] = compilation_fingerprint
    receipt["attack_metadata"]["compilations_fingerprint"] = compilation_fingerprint
    bindings = formula_workbook.action_dispatch_execution_plan(
        calculated["plan"], report).get("attack_metadata_sources") or []
    receipt["attack_metadata"]["input_sources_fingerprint"] = fingerprint(bindings)
    receipt["input_fingerprint"] = fingerprint(input_view(calculated["plan"]))
    receipt["result_fingerprint"] = fingerprint(result)


def _remove_stored_application_contract(record):
    interpretation = record["semantic_dependency"]["interpretation"]
    for field in tuple(interpretation):
        if field not in {"purpose", "status", "source", "shared_group"}:
            interpretation.pop(field)
    record["metadata_dependency_fingerprint"] = fingerprint(record["semantic_dependency"])


def test_modern_metadata_cannot_be_downgraded_by_stripping_only_stored_interpretation(
        review_metadata_output):
    out = copy.deepcopy(review_metadata_output)
    for records in (out["report"]["attack_metadata_compilations"],
                    out["result"]["attack_metadata_compilations"]):
        _remove_stored_application_contract(records[0])
    _refresh_attack_metadata_test_receipts(out)

    with pytest.raises(FormulaWorkbookError):
        formula_workbook._validate_attack_metadata_receipt(
            out["plan"], out["result"], out["report"], out["result_receipt"])


def test_legacy_shape_cannot_hide_record_level_application_adapter_audit(
        review_metadata_output):
    out = copy.deepcopy(review_metadata_output)
    binding = out["plan"]["attack_metadata_sources"][0]
    binding["shared_group"] = binding.pop("counter_tag")
    binding.pop("application_mode")
    binding.pop("verification")
    retained = {
        "application_adapter": {"adapter_fingerprint": "retained-modern-adapter"},
        "application_rule_audit": {"effective_rule": {"mode": "sequence"}},
    }
    for records in (out["report"]["attack_metadata_compilations"],
                    out["result"]["attack_metadata_compilations"]):
        record = records[0]
        record.pop("application_mode")
        record["compiled_fields"].pop("application_mode")
        _remove_stored_application_contract(record)
        record["semantic_dependency"]["interpretation"]["shared_group"] = binding[
            "shared_group"]
        record.update(copy.deepcopy(retained))
        record["metadata_dependency_fingerprint"] = fingerprint(record["semantic_dependency"])
    _refresh_attack_metadata_test_receipts(out)

    with pytest.raises(FormulaWorkbookError):
        formula_workbook._validate_attack_metadata_receipt(
            out["plan"], out["result"], out["report"], out["result_receipt"])
