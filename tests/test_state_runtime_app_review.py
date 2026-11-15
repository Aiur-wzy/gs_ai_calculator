"""Independent reviewed state delivery and display contracts."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as et
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result, state_runtime_rows
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_formula_workbook import read_sheet
from tests.test_shared_pool_app_delivery import authorize
from tests.test_state_runtime_app_delivery import state_plan


def test_state_timer_edit_requires_actual_review_and_changes_receipt_not_native_implicitly(tmp_path):
    db = tmp_path / "absent.db"
    p = authorize(state_plan(), db)
    first = run_plan_calc(p, db)
    changed = copy.deepcopy(p)
    changed["mechanics_model"]["state_runtime"]["timers"][0]["interval_us"] = 500_000
    assert compose_equipment_plan(changed, db)["state"] == "pending"
    with pytest.raises(PlanError): run_plan_calc(changed, db)
    adopted = authorize(changed, db)
    original = copy.deepcopy(adopted)
    second = run_plan_calc(adopted, db)
    assert adopted == original
    assert [r["time_us"] for r in first["result"]["state_runtime"]["execution_trace"]] == [0, 1_000_000]
    assert [r["time_us"] for r in second["result"]["state_runtime"]["execution_trace"]] == [0, 500_000, 1_000_000, 1_500_000]
    assert first["result_receipt"]["input_fingerprint"] != second["result_receipt"]["input_fingerprint"]


def test_state_full_issued_and_execution_ledger_is_readonly_in_three_actual_exports(tmp_path):
    db = tmp_path / "absent.db"
    out = run_plan_calc(authorize(state_plan(), db), db)
    original = copy.deepcopy(out)
    direct = tmp_path / "direct-state.xlsx"
    export_result(out["result"], "xlsx", direct, receipt=out["result_receipt"])
    support = formula_workbook_support(out)
    assert support["editable"]["state_runtime_lifecycle_timers"] == "regenerate_required"
    ns = {"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    for route, content in {"direct":direct.read_bytes(), "App":result_bytes(out["result"], out["result_receipt"], "xlsx"),
        "formula":build_formula_workbook(out)}.items():
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            sheets = et.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet",ns)
            index = next(i+1 for i, row in enumerate(sheets) if row.attrib["name"] == "生命周期流水")
            assert archive.testzip() is None
        cells = read_sheet(content,index)
        chunks = sorted((int(cell["value"].split()[-1]),cells[f"B{ref[1:]}"]["value"])
            for ref,cell in cells.items() if ref.startswith("A") and isinstance(cell["value"],str)
            and cell["value"].startswith("原文块 "))
        assert [n for n,_ in chunks] == list(range(1,len(chunks)+1)),route
        assert json.loads("".join(value for _,value in chunks)) == out["result"]["state_runtime"],route
        assert all(cell["formula"] is None for cell in cells.values()),route
        assert sum(cell["value"] == "调度记录" for cell in cells.values()) == 1,route
        assert sum(cell["value"] == "实际执行记录" for cell in cells.values()) == 1,route
        assert sum(cell["value"] == "收益已激活" for cell in cells.values()) == 2,route
    assert out == original


def test_actual_resource_outcome_and_failed_type_have_readable_export_labels():
    record = {"execution_trace":[
        {"type":"state_dispatch_completed","outcome":"resource_operation","time_us":1_000_000,
         "payload":{"kind":"resource_operation","ref_id":"grant"}},
        {"type":"state_dispatch_failed","run_id":"signed","payload":{"kind":"dispatch_rule","ref_id":"buff"},
         "message":"工程来源已改变"}],"issued_trace":[]}
    original = copy.deepcopy(record)
    rows = state_runtime_rows({"state_runtime":record})
    assert any(row and len(row)>5 and row[5] == "资源操作已处理" for row in rows)
    assert any(row and len(row)>1 and row[1] == "指令失败" for row in rows)
    assert any(row and len(row)>9 and row[9] == "工程来源已改变" for row in rows)
    assert record == original
