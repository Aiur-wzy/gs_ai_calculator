"""Actual reaction-source calculation and immutable snapshot/formula exports."""
from __future__ import annotations

import copy
import io
import json
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

import pytest
from test_reaction_benefits_app import accept
from test_reaction_benefits_app import reaction_plan as reaction_plan  # noqa: PLC0414

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sheets(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        return {node.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
                for i, node in enumerate(names, 1)}


def calculated(reaction_plan):
    plan, db, _, _ = reaction_plan
    accept(plan, db)
    return run_plan_calc(plan, db)


def test_three_generated_parts_are_fixed_in_formula_and_all_source_sheets_are_readonly(reaction_plan, monkeypatch, tmp_path):
    data = calculated(reaction_plan)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("export must not query database"))
    formula = sheets(build_formula_workbook(data))
    source = formula["反应收益来源"]
    assert source.find("s:sheetProtection", NS) is not None
    assert not source.findall(".//s:f", NS)
    assert "ordinary_swirl_benefit_projection_certificate" in "".join(source.itertext())
    parameters = formula["参数"].findall("s:sheetData/s:row", NS)
    source_values = [row for row in parameters if any(text in "".join(row.itertext()) for text in
        ("mechanics_input.buffs[", "mechanics_model.rules[")) and ".value" in "".join(row.itertext())]
    # All three generated values are source controlled, not yellow editable cells.
    assert len(source_values) == 3
    assert all(row.findall("s:c", NS)[2].get("s") == "5" for row in source_values)
    snapshot = result_bytes(data["result"], data["result_receipt"], "xlsx")
    assert sheets(snapshot)["反应收益来源"].find("s:sheetProtection", NS) is not None
    path = tmp_path / "standalone.xlsx"
    export_result(
        data["result"], "xlsx", path, receipt=data["result_receipt"],
    )
    assert sheets(path.read_bytes())["反应收益来源"].find("s:sheetProtection", NS) is not None
    encoded = result_bytes(data["result"], data["result_receipt"], "json")
    assert json.loads(encoded)["result"]["reaction_benefit_compilations"][0]["whole_source_status"] == "partial"


@pytest.mark.parametrize("field", ("report", "result", "receipt", "scenario", "model", "initial", "static", "source_acceptance"))
def test_single_layer_tamper_cannot_export_formula(reaction_plan, field):
    data = copy.deepcopy(calculated(reaction_plan))
    if field == "report":
        data["report"]["upstream_compilations"][0]["rules"][0]["effects"][0]["value"] = .8
    elif field == "result":
        data["result"]["reaction_benefit_compilations"][0]["provenance"]["injected"] = True
    elif field == "receipt":
        data["result_receipt"]["reaction_benefits"]["compiler"] = "wrong"
    elif field == "scenario":
        data["plan"]["effect_bindings"][0]["upstream_recipe"]["scenario"]["origin_target_range"] = "unknown"
    elif field == "model":
        data["report"]["mechanics_model"]["rules"][0]["timing"]["duration"] = 12
    elif field == "initial":
        data["report"]["mechanics_model"]["initial_state"]["rules"][0]["cooldown_remaining"] = 1
    elif field == "static":
        data["report"]["mechanics_input_template"]["buffs"][0]["effects"][0]["value"] = .9
    else:
        data["report"]["upstream_compilations"][0]["assumptions_fingerprint"] = "0" * 64
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(data)


@pytest.mark.parametrize("fmt", ("xlsx", "csv", "json"))
@pytest.mark.parametrize("refresh_result_binding", (False, True),
                         ids=("original_receipt", "rehashed_generic_receipt"))
def test_result_only_exports_reject_changed_source_record(reaction_plan, fmt, refresh_result_binding):
    from genshin_app.equipment.files import fingerprint

    data = calculated(reaction_plan)
    before = copy.deepcopy(data)
    valid = result_bytes(data["result"], data["result_receipt"], fmt)
    assert valid and data == before
    if fmt == "json":
        decoded = json.loads(valid)
        assert decoded["result"] == data["result"]
        assert decoded["result_receipt"] == data["result_receipt"]
    data["result"]["reaction_benefit_compilations"][0]["rules"][0]["timing"]["duration"] = 12
    if refresh_result_binding:
        # Adversarial tamper only: a recomputed general hash must not authorize
        # a changed professional source record against its independent receipt.
        data["result_receipt"]["result_fingerprint"] = fingerprint(data["result"])
    with pytest.raises(PlanError) as caught:
        result_bytes(data["result"], data["result_receipt"], fmt)
    assert caught.value.reason == "source_changed"
    if refresh_result_binding:
        assert "反应收益来源" in str(caught.value)
    else:
        assert isinstance(caught.value.__cause__, ValueError)
        assert "指纹不一致" in str(caught.value.__cause__)
