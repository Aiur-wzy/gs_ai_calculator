"""Offline action-admission closure and tamper regression tests."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest
from test_action_admission_sources import admission_db as _admission_db_fixture
from test_action_admission_sources import plan

from genshin_app.equipment.action_admission_receipt import (
    FIELD,
    _proof,
    _verify_records,
    _verify_runtime,
    validate_action_admission_receipt,
    validate_action_admission_result_receipt,
)
from genshin_app.equipment.action_admissions import compile_action_admissions
from genshin_app.equipment.composer import compose_equipment_plan
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes, run_plan_calc
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_engine.rotation import check_rotation


@pytest.fixture
def admission_db():
    yield from _admission_db_fixture.__wrapped__()


def _calculated(admission_db, *, initial_energy=60):
    db, _record = admission_db
    value = plan()
    value["rotation_model"]["characters"][0]["initial_energy"] = initial_energy
    preview = compose_equipment_plan(value, db)
    source = preview["action_admission_compilations"][0]["source_fingerprint"]
    value["action_admission_sources"][0]["accepted_fingerprint"] = source
    return run_plan_calc(value, db)


def _workbook_sheets(content):
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        nodes = workbook.findall("s:sheets/s:sheet", namespace)
        return {
            node.get("name"): ET.fromstring(
                archive.read(f"xl/worksheets/sheet{index}.xml")
            )
            for index, node in enumerate(nodes, 1)
        }


def test_static_material_and_runtime_link_verify_without_database_requery(admission_db):
    db, _record = admission_db
    value = plan()
    rotation, records, _sources = compile_action_admissions(value, db, lambda *_args: None)
    value["action_admission_sources"][0]["accepted_fingerprint"] = records[0]["source_fingerprint"]
    rotation, records, sources = compile_action_admissions(value, db, lambda *_args: None)
    assert sources
    _verify_records(records, rotation)
    checked = check_rotation(value["manual_template"], rotation)
    _verify_runtime(rotation, checked, [])


def test_calculation_closes_static_marker_parent_and_runtime_receipt(admission_db):
    calculated = _calculated(admission_db)
    result, receipt = calculated["result"], calculated["result_receipt"]
    assert FIELD in result
    assert receipt["action_admissions"]["contract"] == "action-admission-result-receipt-v1"
    summary = result["rotation_check"]["action_admission_execution"]
    assert summary["committed_actions"] == summary["receipt_count"] == 1
    assert (summary["planned_actions"], summary["reported_actions"],
            summary["unreported_actions"]) == (1, 1, 0)
    parent = next(row for row in result["rotation_check"]["event_log"]
                  if "action_admission" in row)
    assert parent["action_admission"]["certificate"]["run_id"] == summary["receipts"][0]["run_id"]
    validate_action_admission_result_receipt(result, receipt)
    validate_action_admission_receipt(
        calculated["plan"], result, calculated["report"], receipt)
    exported = json.loads(result_bytes(result, receipt, "json"))
    assert exported["result_receipt"]["action_admissions"] == receipt["action_admissions"]


def test_rejected_action_has_parent_status_and_no_success_receipt(admission_db):
    db, _record = admission_db
    value = plan()
    value["rotation_model"]["characters"][0]["initial_energy"] = 0
    _rotation, records, _sources = compile_action_admissions(value, db, lambda *_args: None)
    value["action_admission_sources"][0]["accepted_fingerprint"] = records[0]["source_fingerprint"]
    rotation, _records, _sources = compile_action_admissions(value, db, lambda *_args: None)
    checked = check_rotation(value["manual_template"], rotation)
    summary = checked["action_admission_execution"]
    assert summary["rejected_actions"] == 1
    assert summary["committed_actions"] == summary["receipt_count"] == 0
    assert (summary["planned_actions"], summary["reported_actions"],
            summary["unreported_actions"]) == (1, 1, 0)
    parent = next(row for row in checked["event_log"] if "action_admission" in row)
    assert parent["action_admission"]["status"] == "rejected"
    assert "certificate" not in parent["action_admission"]
    _verify_runtime(rotation, checked, [])


def test_runtime_tamper_fails_after_local_and_result_rehash(admission_db):
    calculated = _calculated(admission_db)
    result, receipt = copy.deepcopy(calculated["result"]), copy.deepcopy(calculated["result_receipt"])
    summary = result["rotation_check"]["action_admission_execution"]
    parent = next(row for row in result["rotation_check"]["event_log"]
                  if "action_admission" in row)
    # Rewrite both sides of the local parent/child relation and every exposed
    # hash.  Only replaying the saved engine input can reject this coherent
    # sub-tree rewrite.
    for key in ("frontline_before", "frontline_after"):
        summary["receipts"][0][key] = "locally-rehashed-tamper"
    parent["frontline"] = "locally-rehashed-tamper"
    parent["frontline_after"] = "locally-rehashed-tamper"
    summary["receipts_fingerprint"] = fingerprint(summary["receipts"])
    receipt["action_admissions"] = _proof(result)
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_action_admission_result_receipt(result, receipt)
    with pytest.raises(PlanError) as raised:
        result_bytes(result, receipt, "json")
    assert raised.value.reason == "source_changed"


def test_both_xlsx_modes_preserve_readonly_source_and_execution_history(admission_db):
    calculated = _calculated(admission_db)
    books = [
        _workbook_sheets(build_formula_workbook(calculated)),
        _workbook_sheets(
            result_bytes(calculated["result"], calculated["result_receipt"], "xlsx")
        ),
    ]
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    for book in books:
        assert {"静态动作来源", "运行时动作接纳"} <= set(book)
        for name in ("静态动作来源", "运行时动作接纳"):
            assert book[name].find("s:sheetProtection", namespace) is not None
            assert book[name].findall(".//s:f", namespace) == []
        source_text = "".join(book["静态动作来源"].itertext())
        execution_text = "".join(book["运行时动作接纳"].itertext())
        assert all(value in source_text for value in ("fixture.action-admission", "15", "60"))
        assert all(value in execution_text for value in ("已接纳并提交", "计划动作", "已报告动作"))
    support = formula_workbook_support(calculated)
    assert support["editable"]["action_admission_selection_context_and_initial_cooldown"] == "regenerate_required"
    assert support["action_admission_execution"] == "readonly_actual_gate_and_commit_receipts"


def test_legacy_result_without_any_admission_signal_remains_compatible():
    result = {"summary": {"total_damage": 0}}
    validate_action_admission_result_receipt(result, {})
