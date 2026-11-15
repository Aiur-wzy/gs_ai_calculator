"""Independent EC06 emitted-formula arithmetic and native structural input guards."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import PurePosixPath

import pytest
from formula_workbook_fixtures import mechanics_envelope
from test_formula_workbook import read_sheet
from test_freeze_mechanics_review import setup
from test_freeze_review import ORACLE
from test_reaction_sources_review import evaluate_numeric_cell

from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook


def long_envelope():
    expected = json.loads(ORACLE.read_text("utf8"))
    t, r, m = setup([row["element"] for row in expected["rows"]], [row["gu"] for row in expected["rows"]], [row["t"] for row in expected["rows"]])
    t["loop_duration"] = r["period"] = 14
    receipt = mechanics_envelope((t, r, m))
    receipt["plan"]["manual_template"] = copy.deepcopy(t)
    receipt["result_receipt"]["input_fingerprint"] = fingerprint(input_view(receipt["plan"]))
    return receipt


def workbook_sheets(content):
    """Use emitted sheet identities, never a test-maintained translated alias."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {row.attrib["Id"]: row.attrib["Target"] for row in relations}
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rid = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    sheets = {}
    for sheet in workbook.findall("s:sheets/s:sheet", ns):
        path = PurePosixPath(targets[sheet.attrib[rid]])
        assert path.stem.startswith("sheet"), path
        sheets[sheet.attrib["name"]] = read_sheet(content, int(path.stem[5:]))
    return sheets


def test_review_exported_formulas_reproduce_independent_whole_chain_not_cached_answers():
    receipt = long_envelope()
    original = copy.deepcopy(receipt)
    content = build_formula_workbook(receipt)
    sheets = workbook_sheets(content)
    actual = [evaluate_numeric_cell(sheets, "逐事件", f"V{row}") for row in range(5, 33)]
    assert len([v for v in actual if v == pytest.approx(45)]) == 20
    assert len([v for v in actual if v == pytest.approx(781.299)]) == 8
    assert sum(actual) == pytest.approx(7150.392)
    state_cells = sheets["元素状态流水"]
    assert all(not row["formula"] for row in state_cells.values()), "ODE/state ledger is a read-only result snapshot"
    values = [row["value"] for row in state_cells.values()]
    assert values.count("层消费（不另计伤害）") == 10
    assert "experimental_reference" in values
    assert receipt == original


@pytest.mark.parametrize("part", ["contact", "initial_state", "reference", "application"])
def test_review_changed_native_state_contact_or_rules_cannot_export_stale_structure(part):
    receipt = long_envelope()
    m = receipt["plan"]["mechanics_model"]
    if part == "contact":
        m["hit_events"][0]["contact"] = {"kind": "profile", "profile_id": "unsafe"}
    elif part == "initial_state":
        m["elemental_model"]["target"]["initial_state"]["source"] = "Changed initial-state evidence"
    elif part == "reference":
        m["elemental_model"]["reference_acceptance"]["contract_fingerprint"] = "obsolete-profile"
    else:
        m["hit_events"][1]["application"]["landed"] = False
    with pytest.raises(FormulaWorkbookError, match="参考输入.*指纹"):
        build_formula_workbook(receipt)
