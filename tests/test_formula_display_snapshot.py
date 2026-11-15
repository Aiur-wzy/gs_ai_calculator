"""Captured display names are offline presentation, not a second calculation."""
import copy
import io
import sqlite3
import zipfile

import pytest
from formula_workbook_fixtures import envelope, probe_template
from test_formula_workbook import read_sheet

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    validate_formula_calculation,
)
from genshin_app.presentation import display_names


def captured():
    template = probe_template()
    template["characters"][0]["name"] = "alias a"
    value = envelope(template)
    value["plan"]["manual_template"] = copy.deepcopy(template)
    value["plan"]["equipment_inputs"] = {"a": {"character_ref": {
        "db_character_id": 90000001, "version_line": "release"}}}
    catalog = {"version_line": "release", "characters": [{"id": 90000001,
        "name": "资料角色甲", "display_name": "冻结目录甲", "aliases": ["a"]}]}
    source = {"kind": "calculation_snapshot", "version_line": "release", "status": "ready",
              "catalog_fingerprint": fingerprint(catalog)}
    value["display_context"] = {"catalog": catalog, "warnings": [], "receipt": source}
    value["result_receipt"]["display_source"] = copy.deepcopy(source)
    return value


def test_two_workbooks_use_the_same_captured_names_offline_without_mutation(monkeypatch):
    value = captured()
    before = copy.deepcopy(value)

    def forbidden(*args, **kwargs):
        raise AssertionError("An exporter reopened a database or reran a game calculation")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr("genshin_app.equipment.service.run_plan_calc", forbidden)
    validate_formula_calculation(value)
    formula = build_formula_workbook(value)
    result = result_bytes(value["result"], value["result_receipt"], "xlsx",
        display=display_names(value["plan"], value["display_context"]["catalog"]))
    for content in (formula, result):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            texts = "\n".join(archive.read(p).decode("utf8") for p in archive.namelist() if p.endswith(".xml"))
        assert "冻结目录甲" in texts and "角色1（中文名待补）" not in texts
    for index, hit in enumerate(value["result"]["hits"], 5):
        assert read_sheet(formula, 3)[f"V{index}"]["value"] == pytest.approx(hit["expected"])
    assert value == before


@pytest.mark.parametrize("mutation", ["catalog", "receipt", "missing_context", "missing_source",
                                      "warning_type", "status", "line", "entries", "aliases"])
def test_mismatched_or_malformed_display_snapshot_rejected(mutation):
    value = captured()
    context = value["display_context"]
    if mutation == "catalog":
        context["catalog"]["characters"][0]["display_name"] = "后来目录"
    elif mutation == "receipt":
        value["result_receipt"]["display_source"]["status"] = "template_fallback"
    elif mutation == "missing_context":
        value.pop("display_context")
    elif mutation == "missing_source":
        value["result_receipt"].pop("display_source")
    elif mutation == "warning_type":
        context["warnings"] = "警告不是数组"
    elif mutation == "status":
        context["warnings"] = ["回退说明"]
    elif mutation == "line":
        context["catalog"]["version_line"] = "beta"
    elif mutation == "entries":
        context["catalog"]["characters"] = ["坏目录对象"]
    else:
        context["catalog"]["characters"][0]["aliases"] = "a"
    if mutation in {"line", "entries", "aliases"}:
        context["receipt"]["catalog_fingerprint"] = fingerprint(context["catalog"])
        value["result_receipt"]["display_source"] = copy.deepcopy(context["receipt"])
    with pytest.raises(FormulaWorkbookError, match="中文名称目录"):
        validate_formula_calculation(value)


def test_fallback_and_legacy_absence_keep_template_names():
    value = captured()
    value["plan"]["resolved_template"]["characters"][0]["name"] = "模板中文角色"
    value["result_receipt"]["output_fingerprint"] = fingerprint(value["plan"]["resolved_template"])
    value["display_context"]["catalog"] = {}
    value["display_context"]["warnings"] = ["无法读取目录，沿用模板"]
    value["display_context"]["receipt"].update(status="template_fallback", catalog_fingerprint=fingerprint({}))
    value["result_receipt"]["display_source"] = copy.deepcopy(value["display_context"]["receipt"])
    assert read_sheet(build_formula_workbook(value), 3)["B5"]["value"] == "模板中文角色"
    value.pop("display_context")
    value["result_receipt"].pop("display_source")
    validate_formula_calculation(value)
    assert read_sheet(build_formula_workbook(value), 3)["B5"]["value"] == "模板中文角色"
