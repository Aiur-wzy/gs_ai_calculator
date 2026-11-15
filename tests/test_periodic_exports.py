"""Periodic source evidence, formula values and fixed-edit boundaries survive export."""
import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest
from test_periodic_actions import confirm_periodic, periodic_plan
from test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from test_runtime_periodic_attack import periodic_db  # noqa: F401

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.service import result_bytes
from genshin_app.export import periodic_attack_rows
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)

# ruff: noqa: F811
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
EXPECTED = [45 * multiplier for _ in range(3) for multiplier in
            [2.021399974822998] * 4 + [3.032099962234497]]


def pages(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        return {name.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
                for i, name in enumerate(names, 1)}


def numbers(sheet, column, first, length):
    cells = {cell.get("r"): cell for cell in sheet.findall(".//s:c", NS)}
    return [float(cells[f"{column}{row}"].find("s:v", NS).text) for row in range(first, first+length)]


@pytest.fixture
def periodic_calculated(periodic_db):
    return run_plan_calc(confirm_periodic(periodic_plan(periodic_db), periodic_db), periodic_db)


def test_complete_periodic_source_round_trips_through_chunked_literal_rows(periodic_calculated):
    result = periodic_calculated["result"]
    rows = periodic_attack_rows(result)
    raw = "".join(row[1] for row in rows if row and row[0] in ("完整创建、挂载、参数与未支持分支", "续"))
    restored = json.loads(raw)
    assert restored == result["periodic_attack_compilations"][0]
    assert restored["query"]["source_graph"]["creation_bundle"]
    assert "不证明角色动作原生启动" in rows[0][1]
    assert all(len(row[1]) <= 15000 for row in rows if row and row[0] in ("完整创建、挂载、参数与未支持分支", "续"))


def test_result_and_formula_exports_keep_all_contacts_and_readonly_sources(periodic_calculated):
    data = periodic_calculated
    formula = pages(build_formula_workbook(data))
    snapshot = pages(result_bytes(data["result"], data["result_receipt"], "xlsx"))
    assert numbers(formula["逐事件"], "V", 5, 15) == pytest.approx(EXPECTED)
    assert numbers(snapshot["逐事件明细"], "G", 2, 15) == pytest.approx(EXPECTED)
    assert len(formula["逐事件"].findall(".//s:c[@r='V5']/s:f", NS)) == 1
    for workbook in (formula, snapshot):
        for name in ("周期攻击来源", "动作派发来源"):
            assert workbook[name].find("s:sheetProtection", NS) is not None
            assert not workbook[name].findall(".//s:f", NS)
        assert not any(cell.get("t") == "e" for sheet in workbook.values() for cell in sheet.findall(".//s:c", NS))
    support = formula_workbook_support(data)["editable"]
    assert support["periodic_attack_sources"] == "regenerate_required"
    assert support["periodic_attack_provenance"] == "readonly_snapshot"
    rows = formula["参数"].findall("s:sheetData/s:row", NS)
    fixed = [row for row in rows if any(text in "".join(row.itertext()) for text in
             ("periodic_attack_sources[", "action_event_profiles[", "].multiplier"))]
    assert len(fixed) > 15
    assert all(row.findall("s:c", NS)[2].get("s") == "5" and row.findall("s:c", NS)[8].find("s:f", NS) is not None for row in fixed)


def test_panel_change_recalculates_damage_but_preserves_periodic_structure(periodic_db, periodic_calculated):
    p = copy.deepcopy(periodic_calculated["plan"])
    p["manual_template"]["characters"][0]["stats"]["atk"] *= 2
    recalculated = run_plan_calc(p, periodic_db)
    sheet = pages(build_formula_workbook(recalculated))["逐事件"]
    assert numbers(sheet, "V", 5, 15) == pytest.approx([2 * n for n in EXPECTED])
    assert [h["id"] for h in recalculated["result"]["hits"]] == [h["id"] for h in periodic_calculated["result"]["hits"]]
    assert recalculated["plan"]["action_event_profiles"] == periodic_calculated["plan"]["action_event_profiles"]
