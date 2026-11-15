"""Effective fixed events remain fixed; automatic formulas are comparison only."""
from __future__ import annotations

import copy
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.delivery import (
    build_calculation_delivery,
    validate_delivery_calculated,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_engine.damage_explanations import ExplanationBuilder
from tests.test_calculation_overrides_execution import (
    accept_from_compose,
    add_mechanics_hit_relation,
    declaration,
    native_plan,
    override_item,
    target,
)
from tests.test_external_damage import confirm, supplement
from tests.test_formula_workbook import NS, read_sheet


def fixed_calculation(tmp_path, *, precision="approximate", mixed=False, value=999, system="classic"):
    database = tmp_path / "absent.db"
    plan = native_plan(dormant_hit=mixed)
    if system != "classic":
        hit = plan["manual_template"]["hits"][0]
        # Arithmetic-only engineering inputs, not a character/world scenario.
        hit.update(damage_system=system, element="pyro", reaction={
            "transformative": {"type": "overload"}, "lunar": {"type": "lunar_electro"},
            "stellar": {"type": "stellar_superconduct", "layers": 3}}[system])
        plan["extensions"]["event_topology_bindings"]["bindings"][0]["target_fingerprint"] = fingerprint(hit)
    if mixed:
        plan["mechanics_model"]["hit_events"].append({"hit_id": "h1", "action_id": "cast", "order": 11})
        add_mechanics_hit_relation(plan, "h1")
        plan["external_damage_supplements"] = [supplement()]
    plan["calculation_event_overrides"] = declaration(override_item(
        "replace_computed_value", target("computed_event", "h0", "result.hits"),
        {"expected": value, "precision": {"kind": precision, "method": "单事件独立工程值",
                                       "uncertainty": "工程夹具声明的精度范围", "state_effects": "none"}}))
    if mixed:
        confirm(plan, database)
    accept_from_compose(plan, database)
    if mixed:
        confirm(plan, database)  # External review binds the now-accepted override input.
    assert compose_equipment_plan(plan, database)["state"] == "ready"
    return run_plan_calc(plan, database, capture_display=True)


def sheet(content, name):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    index = next(i for i, row in enumerate(workbook.findall("s:sheets/s:sheet", NS), 1)
                 if row.attrib["name"] == name)
    return read_sheet(content, index)


@pytest.mark.parametrize("precision", ["exact", "approximate"])
@pytest.mark.parametrize("partial", [False, True])
def test_fixed_event_total_and_baseline_have_different_ownership(tmp_path, monkeypatch, precision, partial):
    if partial:
        monkeypatch.setattr("genshin_engine.core.ExplanationBuilder", lambda *_: ExplanationBuilder(0))
    calculated = fixed_calculation(tmp_path, precision=precision)
    original = copy.deepcopy(calculated)
    content = build_formula_workbook(calculated)
    events, summary, audit = sheet(content, "逐事件"), sheet(content, "汇总"), sheet(content, "固定事件补正")
    assert events["V5"]["value"] == summary["B6"]["value"] == 999
    assert events["W5"]["value"] == "不适用（固定期望）"
    assert events["F5"]["value"] == 1  # Never backfill a multiplier to obtain the fixed value.
    assert events["X5"]["value"] == 999 and events["Y5"]["value"] == 0
    assert "'固定事件补正'!$E$5" in events["V5"]["formula"]
    assert "SUM('参数'!G5:G" in events["V5"]["formula"]
    assert "SUM('参数'!G5:G" in summary["B6"]["formula"]
    assert audit["C5"]["value"] == 45 and audit["C5"]["formula"]
    assert audit["D5"]["value"] == 67.5 and audit["D5"]["formula"]
    assert audit["E5"] == {"value": 999, "formula": None}
    assert audit["F5"]["value"] == ("近似" if precision == "approximate" else "精确")
    assert audit["G5"]["value"] == "单事件独立工程值"
    assert audit["H5"]["value"] == "工程夹具声明的精度范围"
    assert audit["B5"]["value"] == "override.one" and audit["I5"]["value"] == "coverage.one"
    assert len(audit["J5"]["value"]) == 64
    sources = sheet(content, "来源与范围")
    assert any("tests/test_calculation_overrides_execution.py" in str(cell["value"]) for cell in sources.values())
    support = formula_workbook_support(calculated)
    assert support["computed_value_overrides"]["precision"] == precision
    assert support["computed_value_overrides"]["automatic_formulas"] == "comparison_baseline_only"
    assert support["editable"]["computed_event_fixed_values"] == "regenerate_required"
    assert calculated == original
    validate_delivery_calculated(calculated)
    delivery = build_calculation_delivery(calculated, input_file_sha="a" * 64)
    assert delivery["artifacts"]["formula_workbook"]
    if partial:
        assert calculated["result"]["damage_explanations"]["status"] == "partial"


def test_fixed_normal_and_external_total_each_value_exactly_once(tmp_path):
    calculated = fixed_calculation(tmp_path, mixed=True)
    assert [row["expected"] for row in calculated["result"]["hits"]] == [999, 45]
    assert calculated["result"]["calculation_totals"]["total_damage"] == 1056
    content = build_formula_workbook(calculated)
    summary, events = sheet(content, "汇总"), sheet(content, "逐事件")
    assert summary["B6"]["value"] == 1056
    assert summary["B7"]["value"] == 528
    assert events["V5"]["value"] == 999 and events["V6"]["value"] == 45
    assert events["W6"]["value"] == 67.5
    assert sheet(content, "外部补充伤害")["F5"]["value"] == 12
    assert "SUM('固定事件补正'!L5:L5)" in summary["B6"]["formula"]
    assert "'外部补充伤害'!F5:F5" in summary["B6"]["formula"]
    assert "'逐事件'!V5:V6" in summary["B6"]["formula"]


def test_zero_fixed_value_is_an_effective_event_not_a_missing_formula(tmp_path):
    calculated = fixed_calculation(tmp_path, precision="exact", value=0)
    content = build_formula_workbook(calculated)
    assert sheet(content, "逐事件")["V5"]["value"] == 0
    assert sheet(content, "汇总")["B6"]["value"] == 0
    assert sheet(content, "固定事件补正")["C5"]["value"] == 45
    assert sheet(content, "固定事件补正")["E5"]["value"] == 0


@pytest.mark.parametrize("system", ["transformative", "lunar", "stellar"])
def test_other_formula_systems_keep_their_own_automatic_baseline(tmp_path, system):
    calculated = fixed_calculation(tmp_path, system=system)
    original = copy.deepcopy(calculated)
    content = build_formula_workbook(calculated)
    proof = calculated["result"]["calculation_computed_value_overrides"]["rows"][0]
    assert sheet(content, "逐事件")["V5"]["value"] == 999
    assert sheet(content, "固定事件补正")["C5"]["value"] == pytest.approx(proof["automatic_expected"])
    assert sheet(content, "逐事件")["W5"]["value"] == "不适用（固定期望）"
    if system == "transformative":
        assert sheet(content, "固定事件补正")["D5"]["value"] == "不适用"
    assert calculated == original


@pytest.mark.parametrize("mixed", [False, True])
def test_summary_inserted_disclosure_keeps_dps_bound_to_actual_total_row(tmp_path, monkeypatch, mixed):
    calculated = fixed_calculation(tmp_path, mixed=mixed)
    # Presentation-only insertion fixture. The actual calculated input has no
    # provisional entity; no approval, usage proof or result validator is waived.
    monkeypatch.setattr("genshin_app.formula_workbook.provisional_usage_receipt",
                        lambda *_args: {"fixture": "disclosure_row_only"})
    summary = sheet(build_formula_workbook(calculated), "汇总")
    total = 1056 if mixed else 999
    assert summary["B7"]["value"] == total
    assert summary["B8"]["value"] == total / 2
    assert "B7/'参数'!C5" in summary["B8"]["formula"]
    assert "B6/'参数'!C5" not in summary["B8"]["formula"]


@pytest.mark.parametrize("field", ["automatic_expected", "automatic_crit"])
def test_rehashed_fixed_proof_cannot_hide_a_false_automatic_baseline(tmp_path, field):
    calculated = fixed_calculation(tmp_path)
    proof = calculated["result"]["calculation_computed_value_overrides"]
    proof["rows"][0][field] += 1
    if field == "automatic_expected":
        calculated["result"]["hits"][0]["calculation_override"][field] += 1
    proof["ledger_fingerprint"] = fingerprint(proof["rows"])
    proof["proof_fingerprint"] = fingerprint({key: value for key, value in proof.items() if key != "proof_fingerprint"})
    calculated["result_receipt"]["calculation_computed_value_overrides"] = copy.deepcopy(proof)
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    with pytest.raises(FormulaWorkbookError, match="公式基线|逐击解释"):
        build_formula_workbook(calculated)


def test_missing_fixed_proof_cannot_be_treated_as_a_normal_formula(tmp_path):
    calculated = fixed_calculation(tmp_path)
    for value in (calculated["result"], calculated["result_receipt"]):
        value.pop("calculation_computed_value_overrides")
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    with pytest.raises((FormulaWorkbookError, ValueError)):
        build_formula_workbook(calculated)
