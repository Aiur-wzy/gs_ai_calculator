"""Independent source and exported-counter boundary checks, invented facts."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
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
from tests.test_mechanics_execution import add, rule
from tests.test_resource_counter_app import counter_plan, raw_invoke
from tests.test_shared_pool_app_delivery import authorize


def test_full_context_confirmation_cannot_be_replaced_by_current_source_only(tmp_path):
    db = tmp_path/"absent.db"
    plan = authorize(counter_plan(), db)
    plan["effect_bindings"][0]["parts"][0]["dependencies"]["mode"] = "source_only"
    report = compose_equipment_plan(plan, db)
    assert report["state"] == "pending"
    assert any(i["code"] == "counter_source_scope_unreviewed" for i in report["missing_inputs"])
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)
    assert not db.exists()


def test_actual_native_partial_counter_exclusion_preserves_independent_spend(tmp_path):
    db = tmp_path/"absent.db"
    plan = counter_plan()
    add(plan["mechanics_model"], rule("counter_only", value=0, source_id="binding:counter"))
    second = copy.deepcopy(plan["mechanics_model"]["resource_counters"]["counters"][0])
    second.update(id="other", source_id="binding:counter")
    plan["mechanics_model"]["resource_counters"]["counters"].append(second)
    part = copy.deepcopy(plan["effect_bindings"][0]["parts"][0])
    part.update(part_id="counter", state="excluded", reason="Counter only is outside this declared scenario")
    part["rule"]["rule_id"] = "counter_only"
    plan["effect_bindings"][0]["parts"].append(part)
    authorize(plan, db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert [r["accepted"] for r in out["result"]["shared_pools"]["trace"]] == [8, 8, 3]
    assert out["result"]["resource_counters"]["state"]["values"] == {"count": {"total": 19}}
    records = out["report"]["resource_counter_source_adoptions"]
    assert [(r["id"], r["effective_status"]) for r in records] == [("count", "ready"), ("other", "excluded")]
    assert plan == before


def test_pending_original_dependency_stays_pending_after_structure_only_validation():
    kept, records, issues, original = raw_invoke(
        change=lambda m: m["shared_pools"]["operations"][0].update(status="pending"))
    assert kept is None and records[0]["effective_status"] == "dependency_pending"
    assert issues and original["shared_pools"]["operations"][0]["status"] == "pending"


def test_all_three_export_routes_keep_actual_debit_separate_from_zero_credit(tmp_path):
    db = tmp_path/"absent.db"
    plan = counter_plan()
    model = plan["mechanics_model"]
    model["resource_counters"]["counters"][0].update(accumulate_window="established_until_end", reset_on="never")
    model["state_runtime"]["lifecycles"][0].update(establishment_delay_us=500_000)
    model["state_runtime"]["lifecycles"][0]["hooks"]["create"] = [{"kind": "resource_operation", "ref_id": "refill", "order": 1}]
    model["state_runtime"]["timers"] = []
    out = run_plan_calc(authorize(plan, db), db)
    ledger = out["result"]["resource_counters"]
    rows = [r for r in ledger["trace"] if r["type"] == "counter_accumulation"]
    assert [(r["requested"], r["resource_accepted"], r["accepted"], r["after"]) for r in rows] == [(8, 8, 0, 0)]
    assert rows[0]["reason"] == "outside_window"
    assert formula_workbook_support(out)["editable"]["resource_counter_windows_and_sources"] == "regenerate_required"
    target = tmp_path/"counter.xlsx"
    export_result(out["result"], "xlsx", target, receipt=out["result_receipt"])
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    for payload in (target.read_bytes(), result_bytes(out["result"], out["result_receipt"], "xlsx"), build_formula_workbook(out)):
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            sheets = ET.fromstring(z.read("xl/workbook.xml")).findall("s:sheets/s:sheet", ns)
            index = next(i+1 for i, sheet in enumerate(sheets) if sheet.attrib["name"] == "资源累计流水")
        cells = read_sheet(payload, index)
        chunks = sorted((int(c["value"].split()[-1]), cells["B"+ref[1:]]["value"]) for ref, c in cells.items()
                        if ref.startswith("A") and isinstance(c["value"], str) and c["value"].startswith("原文块 "))
        assert json.loads("".join(text for _, text in chunks)) == ledger
        assert all(c["formula"] is None for c in cells.values())
    assert not db.exists()


def test_control_failure_export_preserves_type_and_actual_message():
    rows = state_runtime_rows({"state_runtime": {"execution_trace": [{
        "type": "state_control_failed", "time_us": 200_000, "lifecycle_id": "field",
        "payload": {"kind": "lifecycle_established"}, "message": "Independent exact failed control"}]}})
    actual = next(r for r in rows if len(r) == 10 and r[1] == "累计控制失败")
    assert actual[0] == .2 and actual[5] == "失败" and actual[9] == "Independent exact failed control"
