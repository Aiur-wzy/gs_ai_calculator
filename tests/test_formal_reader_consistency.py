"""Saved-reader scope, typed capacity failures and dedicated v3 evidence."""
from __future__ import annotations

import copy
import csv
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.calculation_integrity import result_transport_parts
from genshin_app.calculation_review import review_calculation
from genshin_app.domain_errors import classify_domain_failure, tool_failure_guidance
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.attribute_receipt import (
    MAX_EVIDENCE_BYTES,
    validate_attribute_result_receipt,
)
from genshin_app.equipment.delivery import validate_delivery_calculated
from genshin_app.equipment.files import fingerprint, json_bytes
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result, formal_calculation_audit_payload
from genshin_app.formula_workbook import build_formula_workbook
from genshin_engine.execution_control import ExecutionCancelled
from tests.test_attribute_runtime import enable
from tests.test_attribute_runtime import fixture as attribute_fixture
from tests.test_attribute_stat_app import adopt, stat_plan
from tests.test_formula_workbook import read_sheet


@pytest.fixture
def v3_calculated(tmp_path):
    database = tmp_path / "absent.db"
    return run_plan_calc(adopt(stat_plan(percent=True), database), database, capture_display=True)


def result_only(calculated):
    return {key: calculated[key] for key in ("result", "result_receipt")}


@pytest.mark.parametrize("mutation", ["read_value", "sample", "hit", "drop_receipt", "drop_ledger"])
@pytest.mark.parametrize("shape", ["nested", "root"])
def test_result_review_uses_v3_specific_saved_proof(v3_calculated, mutation, shape):
    value = result_only(v3_calculated)
    result, receipt = value["result"], value["result_receipt"]
    if mutation == "read_value":
        result["attribute_ledger"]["receipts"][0]["value"] += 1
    elif mutation == "sample":
        result["sampling_trace"][0]["value"] += 1
    elif mutation == "hit":
        result["hits"][0]["expected"] += 1
    elif mutation == "drop_receipt":
        receipt.pop("attribute_ledger")
    else:
        result.pop("attribute_ledger")
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_attribute_result_receipt(result, receipt)
    saved = {**result, "result_receipt": receipt} if shape == "root" else value
    with pytest.raises(PlanError) as rejected:
        review_calculation(saved, {"view": "attributes"})
    assert rejected.value.reason == "invalid_review_input"


def test_full_inner_snapshot_cannot_silently_downgrade_to_result_only(v3_calculated):
    assert review_calculation(v3_calculated)["validation"] == "full_calculation_package"
    changed = copy.deepcopy(v3_calculated)
    changed["plan"]["revision"] += 1
    with pytest.raises(PlanError):
        review_calculation(changed)
    incomplete = result_only(v3_calculated) | {"report": v3_calculated["report"]}
    with pytest.raises(PlanError):
        review_calculation(incomplete)
    with pytest.raises(ValueError, match="完整计算包"):
        result_transport_parts(incomplete)
    # Explicitly choosing the published result-only shape gives exactly the
    # saved-result scope; it never claims to have verified absent plan/report.
    assert review_calculation(result_only(v3_calculated))["validation"] == "saved_result_fingerprint"


def test_standalone_attribute_result_keeps_verified_receipt_in_all_exports(tmp_path):
    from genshin_engine.mechanics import execute_mechanics

    template, rotation, mechanics = attribute_fixture()
    enable(template, mechanics)
    result = execute_mechanics(template, rotation, mechanics)["result"]
    receipt = {"result_fingerprint": fingerprint(result),
               **{key: result[key] for key in ("calculation_producer_contract", "attribute_execution_budget")}}
    assert formal_calculation_audit_payload(result, receipt) is None
    target = tmp_path / "standalone.json"
    export_result(result, "json", target, receipt=receipt)
    saved = json.loads(target.read_text(encoding="utf-8"))
    actual, bound = result_transport_parts(saved)
    assert actual == result and bound == receipt
    assert review_calculation(saved)["validation"] == "saved_result_fingerprint"
    target = tmp_path / "standalone.csv"
    export_result(result, "csv", target, receipt=receipt)
    rows = list(csv.reader(io.StringIO(target.read_text(encoding="utf-8-sig"))))
    column = rows[0].index("result_receipt_json")
    assert json.loads(rows[1][column]) == receipt
    target = tmp_path / "standalone.xlsx"
    export_result(result, "xlsx", target, receipt=receipt)
    with zipfile.ZipFile(target) as book:
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        sheets = ET.fromstring(book.read("xl/workbook.xml")).findall("s:sheets/s:sheet", ns)
        index = next(i+1 for i, sheet in enumerate(sheets) if sheet.get("name") == "结果回执")
        sheet = ET.fromstring(book.read(f"xl/worksheets/sheet{index}.xml"))
        assert sheet.find("s:sheetProtection", ns) is not None
    cells = read_sheet(target.read_bytes(), index)
    chunks = [(int(cell[1:]), cells["B"+cell[1:]]["value"]) for cell, row in cells.items()
              if cell.startswith("A") and str(row["value"]).startswith("原文块 ")]
    assert json.loads("".join(value for _, value in sorted(chunks))) == receipt


def test_real_evidence_byte_boundary_keeps_capacity_reason_and_never_publishes(v3_calculated, tmp_path, capsys):
    # Exercise the actual 32 MiB boundary, not a monkeypatched magic limit. A
    # captured v3 run supplies the schema and identity; this deliberately huge
    # saved sampling collection is rejected for capacity before interpreting
    # its repeated observations or allowing any new authority.
    result, receipt = v3_calculated["result"], v3_calculated["result_receipt"]
    sample = result["sampling_trace"][0]
    sample_bytes = len(json.dumps(sample, ensure_ascii=False).encode("utf-8"))
    result["sampling_trace"] = [sample] * (MAX_EVIDENCE_BYTES // sample_bytes + 2)
    receipt["attribute_ledger"]["sampling_fingerprint"] = fingerprint(result["sampling_trace"])
    receipt["result_fingerprint"] = fingerprint(result)
    assert len(json_bytes(result)) < 64 * 1024 * 1024
    target = tmp_path / "existing.json"
    target.write_bytes(b"previous artifact")
    calls = [lambda: validate_attribute_result_receipt(result, receipt),
             lambda: formal_calculation_audit_payload(result, receipt),
             lambda: result_bytes(result, receipt, "json"),
             lambda: export_result(result, "json", target, receipt=receipt),
             lambda: review_calculation(result_only(v3_calculated)),
             lambda: build_formula_workbook(v3_calculated),
             lambda: validate_delivery_calculated(v3_calculated)]
    for call in calls:
        with pytest.raises(PlanError) as rejected:
            call()
        error = rejected.value
        assert error.reason == "attribute_evidence_limit"
        assert error.details["actual_bytes"] > error.details["limit"] == MAX_EVIDENCE_BYTES
        assert classify_domain_failure(error.reason, details=error.details)["category"] == "engine_capability_missing"
        assert tool_failure_guidance(error.reason, details=error.details)["recovery"]["retry_same_request"] is False
        assert target.read_bytes() == b"previous artifact"
    from genshin_app.cli import main
    saved = tmp_path / "oversized-proof.json"
    saved.write_bytes(json_bytes(result_only(v3_calculated)))
    assert main(["--data-dir", str(tmp_path), "equipment", "review", "--calculated", str(saved),
                 "--output", str(target), "--overwrite"]) == 1
    error = json.loads(capsys.readouterr().err)
    assert error["reason"] == "attribute_evidence_limit"
    assert error["category"] == "engine_capability_missing"
    assert error["recovery"]["retry_same_request"] is False
    assert target.read_bytes() == b"previous artifact"


@pytest.mark.parametrize("reader", ["service", "formula", "review"])
def test_formal_adapter_preserves_explicit_cancellation(v3_calculated, monkeypatch, reader):
    def cancelled(*args, **kwargs):
        raise ExecutionCancelled()

    module = "genshin_app.equipment.attribute_receipt"
    monkeypatch.setattr(module + ".validate_attribute_result_receipt", cancelled)
    monkeypatch.setattr(module + ".validate_attribute_receipt", cancelled)
    result, receipt = v3_calculated["result"], v3_calculated["result_receipt"]
    call = {"service": lambda: result_bytes(result, receipt, "json"),
            "formula": lambda: build_formula_workbook(v3_calculated),
            "review": lambda: review_calculation(result_only(v3_calculated))}[reader]
    with pytest.raises(ExecutionCancelled) as rejected:
        call()
    assert rejected.value.reason == "calculation_cancelled"
