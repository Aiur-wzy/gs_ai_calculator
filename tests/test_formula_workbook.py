"""Real OOXML contracts and independent engine baselines for live-formula mode."""
import copy
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment.action_automation_audit import (
    _receipt_projection,
    validate_action_materialization_receipt,
)
from genshin_app.equipment.files import fingerprint
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


from formula_workbook_fixtures import (
    envelope,
    mechanics_envelope,
    mechanics_probe_inputs,
    probe_template,
)

from tests.test_action_automation_audit import (
    _coverage,
    _dispatch,
    _sealed_materialization,
    _single_action_plan,
    _trusted_bundle,
)


def read_sheet(content, number):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
    cells = {}
    for cell in root.findall(".//s:c", NS):
        formula, value, text = cell.find("s:f", NS), cell.find("s:v", NS), cell.find("s:is/s:t", NS)
        cells[cell.attrib["r"]] = {
            "formula": formula.text if formula is not None else None,
            "value": (float(value.text) if cell.attrib.get("t") != "str" else value.text) if value is not None else (text.text if text is not None else None),
        }
    return cells


def materialization_envelope(monkeypatch):
    import genshin_db.runtime_gameplay_action_manifest as manifest_runtime

    source_plan = _single_action_plan()
    coverage = _coverage(["primary"])
    dispatch = _dispatch(["primary"], coverage_marker=coverage)
    public, result_projection, receipt_projection = _sealed_materialization(
        source_plan, [], [dispatch])
    assert public["status"] == "incomplete"
    calculated = envelope()
    calculated["plan"]["rotation_model"] = copy.deepcopy(
        source_plan["rotation_model"])
    calculated["result"].update(copy.deepcopy(result_projection))
    calculated["result_receipt"].update(copy.deepcopy(receipt_projection))
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(
        calculated["result"])
    monkeypatch.setattr(manifest_runtime, "load_gameplay_action_manifest_bundle",
        lambda: _trusted_bundle(coverage))
    return calculated


def test_four_system_formulas_and_cached_baseline_reconcile():
    calculated = envelope()
    content = build_formula_workbook(calculated)
    events, summary = read_sheet(content, 3), read_sheet(content, 1)
    for i, hit in enumerate(calculated["result"]["hits"], 5):
        assert events[f"V{i}"]["formula"] and "逐事件" in events[f"V{i}"]["formula"]
        assert events[f"V{i}"]["value"] == pytest.approx(hit["expected"], rel=1e-10)
        assert events[f"Y{i}"]["value"] == pytest.approx(0, abs=1e-7)
        assert "参数" in events[f"G{i}"]["formula"]
    assert summary["B6"]["value"] == pytest.approx(calculated["result"]["summary"]["total_damage"])
    assert "SUM(" in summary["B6"]["formula"]
    assert "*" in events["V5"]["formula"]  # no total-ratio scaling
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        assert 'fullCalcOnLoad="1"' in archive.read("xl/workbook.xml").decode()
        assert "source_fingerprint" in archive.read("xl/worksheets/sheet4.xml").decode()


def test_stale_receipt_and_mismatched_event_rejected():
    stale = envelope()
    stale["plan"]["resolved_template"]["characters"][0]["stats"]["atk"] += 100
    with pytest.raises(FormulaWorkbookError, match="指纹"):
        build_formula_workbook(stale)
    stale = envelope()
    stale["result"]["hits"][0]["expected"] += 1
    with pytest.raises(FormulaWorkbookError, match="指纹"):
        build_formula_workbook(stale)
    with pytest.raises(FormulaWorkbookError, match="plan 或 result"):
        build_formula_workbook({"result": {}})


def test_action_materialization_diagnostic_is_visible_and_coordinated_status_rewrite_fails(
        monkeypatch):
    calculated = materialization_envelope(monkeypatch)
    content = build_formula_workbook(calculated)
    sources = read_sheet(content, 4)
    texts = {str(cell["value"]) for cell in sources.values()}
    assert "动作物化状态" in texts
    assert any("不完整" in value and "诊断结果" in value for value in texts)
    assert "动作缺项：e1" in texts
    assert any("未解决实例：primary" in value for value in texts)

    forged = copy.deepcopy(calculated)
    public = forged["result"]["action_materialization_receipt"]
    row = public["actions"][0]
    row.update(coverage_status="unknown", topology_kind=None,
        expected_components=None, expected_occurrences=None,
        unresolved_components=[], unresolved_occurrences=[],
        cancelled_occurrences=[], unexpected_occurrences=[], issues=[])
    public["status"], public["calculation_class"], public["summary"] = (
        _receipt_projection(public["actions"]))
    forged["result_receipt"]["action_materialization_receipt"] = copy.deepcopy(public)
    forged["result_receipt"]["action_materialization_proof"][
        "public_receipt_fingerprint"] = fingerprint(public)
    forged["result_receipt"]["result_fingerprint"] = fingerprint(forged["result"])
    validate_action_materialization_receipt(public)

    with pytest.raises(FormulaWorkbookError, match="不能由封存的独立清单和编译事件重建"):
        build_formula_workbook(forged)


def test_structure_er_and_local_scenario_status_use_live_formulas():
    calculated = envelope()
    content = build_formula_workbook(calculated)
    summary, parameters = read_sheet(content, 1), read_sheet(content, 2)
    paths = {cell["value"]: ref[1:] for ref, cell in parameters.items() if ref.startswith("A")}
    count_row = paths["hits[0].count"]
    er_row = paths["characters[0].stats.energy_recharge"]
    assert f"C{count_row}<>" in parameters[f"I{count_row}"]["formula"]
    assert "参数'!I5:" in summary["B6"]["formula"]
    assert f"C{er_row}<>" in parameters[f"J{er_row}"]["formula"]
    assert "参数'!J5:" in summary["B9"]["formula"]
    assert "循环结论失效" in summary["B9"]["formula"]
    assert "未回写 App" in summary["B8"]["formula"]
    support = formula_workbook_support(calculated)
    assert support["editable"]["actions_hit_count_timing"] == "regenerate_required"


def test_snapshot_and_frontline_membership_are_preserved():
    calculated = envelope()
    event = read_sheet(build_formula_workbook(calculated), 3)
    assert "buff-a" in event["AA9"]["value"]  # late snapshot still samples early frontline
    assert event["AA10"]["value"] is None  # end-exclusive / switched-away event


def test_declared_dependencies_are_not_claimed_as_fully_live():
    calculated = envelope()
    calculated["plan"]["mechanics_model"] = {"rules": [{"id": "quota", "capacity": 7, "effects": []}]}
    support = formula_workbook_support(calculated)
    assert support["status"] == "partial"
    assert support["editable"]["character_stats"] == "regenerate_required"
    content = build_formula_workbook(calculated)
    summary = read_sheet(content, 1)
    assert "需重新生成" in summary["B6"]["formula"]
    assert "局部支持" == summary["B5"]["value"]


def test_resistance_and_crit_caps_formula_boundaries():
    for resistance in (-.2, 0, .75, 1.0):
        template = probe_template()
        template["enemy"]["resistance"]["default"] = resistance
        template["characters"][0]["stats"]["crit_rate"] = 1.5
        calculated = envelope(template)
        result = read_sheet(build_formula_workbook(calculated), 3)
        assert result["V5"]["value"] == pytest.approx(calculated["result"]["hits"][0]["expected"])
        assert "MIN(" in result["N5"]["formula"]
        assert "<0.75" in result["P5"]["formula"]


def test_immutable_input_and_literal_formula_strings():
    calculated = envelope()
    calculated["plan"]["name"] = '=HYPERLINK("https://example.invalid","不可执行来源")'
    before = copy.deepcopy(calculated)
    content = build_formula_workbook(calculated)
    assert calculated == before
    assert read_sheet(content, 1)["A2"]["formula"] is None


def test_no_loop_duration_is_not_fabricated_dps():
    template = probe_template()
    del template["loop_duration"]
    calculated = envelope(template)
    result = read_sheet(build_formula_workbook(calculated), 1)
    assert result["B7"]["value"] == "未设置循环时长"


def test_real_mechanics_projection_uses_dynamic_source_formulas():
    calculated = mechanics_envelope()
    assert formula_workbook_support(calculated)["status"] == "supported"
    content = build_formula_workbook(calculated)
    params, events = read_sheet(content, 2), read_sheet(content, 3)
    paths = {cell["value"]: ref[1:] for ref, cell in params.items() if ref.startswith("A")}
    source = paths["characters[1].stats.atk"]
    factor = paths["mechanics_model.rules[2].effects[0].value.factor"]
    numeric = paths["buffs[1].effects[0].value"]
    formula = params[f"C{numeric}"]["formula"]
    assert f"$C${source}" in formula
    assert f"$C${factor}" in formula
    assert "MIN(" in formula and "MAX(" in formula
    manual_rows = [ref[1:] for ref, cell in params.items() if ref.startswith("A") and cell["value"] == "mechanics_input.buffs[1].effects[0].value"]
    assert manual_rows
    assert any(f"$C${manual_rows[0]}" in (cell["formula"] or "") for ref, cell in params.items() if ref.startswith("C"))
    for index, hit in enumerate(calculated["result"]["hits"], 5):
        assert events[f"V{index}"]["value"] == pytest.approx(hit["expected"])


def test_missing_mechanics_instance_or_dependency_is_explicit():
    calculated = mechanics_envelope()
    del calculated["result"]["sampling_trace"][0]["projected_effect_id"]
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    support = formula_workbook_support(calculated)
    assert support["status"] == "partial"
    assert support["limitations"][0]["code"] == "mechanics_dependency_unavailable"


def test_zero_percent_without_base_stat_does_not_promise_nonzero_edit():
    template = probe_template()
    del template["characters"][0]["stats"]["base_atk"]
    template["buffs"][0]["effects"][0]["value"] = 0
    calculated = envelope(template)
    support = formula_workbook_support(calculated)
    assert any(item["code"] == "base_stat_missing" for item in support["limitations"])
    assert support["editable"]["numeric_buff_values"] == "regenerate_required"


def test_formula_cells_and_structure_are_protected_but_numeric_inputs_unlock():
    with zipfile.ZipFile(io.BytesIO(build_formula_workbook(envelope()))) as archive:
        assert '<sheetProtection sheet="1"' in archive.read("xl/worksheets/sheet2.xml").decode()
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        formats = styles.find("s:cellXfs", NS)
        assert formats[2].find("s:protection", NS).attrib["locked"] == "0"
        assert formats[3].find("s:protection", NS) is None


def test_energy_window_dependency_is_a_source_parameter_not_a_trace_constant():
    template, rotation, model = mechanics_probe_inputs()
    model["rules"][2]["effects"][0]["value"] = {"kind": "stat", "subject": "owner", "stat": "energy_recharge", "factor": 100, "sample": "current"}
    rotation["energy_recharge_windows"] = [{"id": "er-window", "character": "b", "start": 1, "end": 2, "delta": .5,
                                            "repeat": {"mode": "each_cycle"}, "status": "assumed", "source": "工程输入"}]
    calculated = mechanics_envelope((template, rotation, model))
    assert formula_workbook_support(calculated)["status"] == "supported"
    params = read_sheet(build_formula_workbook(calculated), 2)
    paths = {cell["value"]: ref[1:] for ref, cell in params.items() if ref.startswith("A")}
    row = paths["rotation_model.energy_recharge_windows[0].delta"]
    assert any(f"$C${row}" in (cell["formula"] or "") for ref, cell in params.items() if ref.startswith("C"))
