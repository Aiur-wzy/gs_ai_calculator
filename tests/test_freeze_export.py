"""Experimental state snapshots remain read-only and explicitly qualified."""
from __future__ import annotations

import copy
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest
from formula_workbook_fixtures import envelope

from genshin_app.equipment.files import fingerprint
from genshin_app.export import elemental_state_rows, export_result
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_engine.elemental_schema import FREEZE_RULESET, REFERENCE_CONTRACT

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def state(*, cryo=0, hydro=0, frozen=0, history=0):
    return {"ordinary_aura": {"element": "cryo" if cryo else "hydro", "remaining_gu": cryo or hydro,
                              "decay_gu_per_second": .1, "source": "工程初态"} if cryo or hydro else None,
            "frozen": {"remaining_gu": frozen, "source": "工程冻结"} if frozen else None,
            "freeze_history_seconds": history, "freeze_decay_gu_per_second": .4 + .1 * history,
            "target_id": "enemy", "t": 1}


def calculated():
    """Synthetic export-only receipt; numerical mechanics are tested separately."""
    result = envelope()
    acceptance = {"contract_id": REFERENCE_CONTRACT["contract_id"],
                  "contract_fingerprint": REFERENCE_CONTRACT["contract_fingerprint"],
                  "acknowledged": True, "source": '=HYPERLINK("https://example.invalid","测试文本")'}
    model = {"ruleset": FREEZE_RULESET, "target": {"initial_state": state(),
        "freeze": {"freezable": True, "resistance": 0}}, "reference_acceptance": acceptance,
        "contact_profiles": [{"id": "safe", "freeze_interaction": "none"}]}
    result["plan"]["mechanics_model"] = {"elemental_model": model, "rules": [],
        "hit_events": [{"hit_id": "h0", "contact": {"kind": "profile", "profile_id": "safe"}}]}
    result["result"]["elemental"] = {"ruleset": FREEZE_RULESET, "evidence_tier": "experimental_reference",
        "reference_contract": copy.deepcopy(REFERENCE_CONTRACT), "reference_acceptance": copy.deepcopy(acceptance),
        "input_window_state": state(), "final_checked_state": state(), "trace": [
            {"type": "elemental_state_transition", "reason": "frozen_expired", "t": .5,
             "event_id": "expiry", "before": state(frozen=.1, history=1), "after": state(history=1.2), "source": "自然推进"},
            {"type": "elemental_application", "t": .5, "event_id": "hit", "action_id": "cast", "character": "a",
             "target_id": "enemy", "before": state(cryo=.2, frozen=.4, history=1), "after": state(history=1),
             "package_id": "hit:reaction_package", "allowed": True, "status": "executed", "source": "实验参考测试",
             "reactions": [{"id": "hit:reaction:swirl:cryo", "type": "swirl", "reaction_element": "cryo"}],
             "layer_consumptions": [{"layer": "cryo", "reaction_element": "cryo", "before_gu": .2,
                                      "after_gu": 0, "consumed_gu": .2, "remaining_trigger_gu": .6},
                                     {"layer": "frozen", "reaction_element": "cryo", "before_gu": .4,
                                      "after_gu": .1, "consumed_gu": .3, "remaining_trigger_gu": 0}]},
            {"type": "reaction_damage_attempt", "t": .5, "event_id": "damage", "reaction_type": "swirl",
             "reaction_id": "hit:reaction:swirl:cryo", "reaction_element": "cryo", "allowed": False,
             "status": "executed", "reason": "damage_icd_blocked", "source": "伤害序列"}]}
    result["result_receipt"]["result_fingerprint"] = fingerprint(result["result"])
    return result


def sheet_cells(content, number):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = ET.fromstring(archive.read(f"xl/worksheets/sheet{number}.xml"))
    cells = {}
    for c in root.findall(".//s:c", NS):
        formula, value, text = c.find("s:f", NS), c.find("s:v", NS), c.find("s:is/s:t", NS)
        cells[c.attrib["r"]] = {"style": c.get("s"), "formula": formula.text if formula is not None else None,
                                "value": value.text if value is not None else text.text if text is not None else None}
    return root, cells


def test_rows_preserve_order_layers_packages_zero_and_unknown():
    receipt = calculated()
    before = copy.deepcopy(receipt)
    rows = elemental_state_rows(receipt["result"])
    header_index = next(i for i, r in enumerate(rows) if r and r[0] == "记录类型")
    table = [dict(zip(rows[header_index], row, strict=True)) for row in rows[header_index + 1:]]
    assert [r["记录类型"] for r in table[:5]] == ["自然状态推进", "元素附着判定", "层消费（不另计伤害）", "层消费（不另计伤害）", "反应伤害判定"]
    assert table[0]["后F（GU）"] == 0 and table[0]["原因或结果"] == "冻结层自然到期"
    assert table[1]["前C（GU）"] == .2 and table[1]["前H（GU）"] == 0
    assert [table[i]["层消费（GU）"] for i in (2, 3)] == [.2, .3]
    assert all(table[i]["反应包"] == "hit:reaction_package" for i in (1, 2, 3))
    assert table[4]["前C（GU）"] == "" and table[4]["允许"] is False
    assert receipt == before


@pytest.mark.parametrize("ruleset", [None, "gauge-basic-v1", "gauge-swirl-v2"])
def test_older_rulesets_have_no_new_sheet_or_contract(ruleset):
    receipt = envelope()
    if ruleset:
        receipt["result"]["elemental"] = {"ruleset": ruleset}
        receipt["result_receipt"]["result_fingerprint"] = fingerprint(receipt["result"])
    assert elemental_state_rows(receipt["result"]) == []
    assert "elemental_reference" not in formula_workbook_support(receipt)
    with zipfile.ZipFile(io.BytesIO(build_formula_workbook(receipt))) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        # A12 adds the lossless source sheet for every ruleset; older rulesets
        # still do not gain the freeze-only state contract or state sheet.
        assert [sheet.attrib["name"] for sheet in workbook.findall("s:sheets/s:sheet", NS)] == [
            "汇总", "参数", "逐事件", "来源与范围", "完整来源JSON"]
        assert len([n for n in archive.namelist() if n.startswith("xl/worksheets/")]) == 5


def test_snapshot_xlsx_readonly_and_literal_sources(tmp_path):
    receipt = calculated()
    receipt["result"].pop("rotation_check")
    p = export_result(receipt["result"], "xlsx", tmp_path / "freeze.xlsx")
    root, cells = sheet_cells(p.read_bytes(), 4)
    assert root.find("s:sheetProtection", NS) is not None
    assert all(c["formula"] is None for c in cells.values())
    assert any((c["value"] or "").startswith("=HYPERLINK") for c in cells.values())
    assert any(c["value"] == "experimental_reference" for c in cells.values())


def test_formula_freeze_state_is_readonly_with_fixed_input_guards():
    receipt = calculated()
    original = copy.deepcopy(receipt)
    support = formula_workbook_support(receipt)
    assert support["editable"]["elemental_initial_state_contact_reference"] == "regenerate_required"
    assert support["elemental_reference"]["state_integration"] == "not_replayed_in_workbook"
    content = build_formula_workbook(receipt)
    _, params = sheet_cells(content, 2)
    paths = {c["value"]: ref[1:] for ref, c in params.items() if ref.startswith("A")}
    for path in ("mechanics_model.elemental_model.target.initial_state.freeze_history_seconds",
                 "mechanics_model.elemental_model.reference_acceptance.contract_fingerprint",
                 "mechanics_model.hit_events[0].contact.profile_id"):
        row = paths[path]
        assert params["C" + row]["style"] == "5"
        assert params["I" + row]["formula"] == f"IF(C{row}<>D{row},1,0)"
    root, trace = sheet_cells(content, 5)
    assert root.find("s:sheetProtection", NS) is not None
    assert all(cell["formula"] is None and cell["style"] in ("1", "5") for cell in trace.values())
    _, sources = sheet_cells(content, 4)
    assert any("不是游戏真值" in (c["value"] or "") for c in sources.values())
    assert receipt == original


def test_source_compilation_confirmation_is_retained_without_raw_query_dump():
    result = calculated()["result"]
    result["elemental"]["reaction_damage_source_compilations"] = [{
        "reaction_type": "swirl", "status": "ready", "binding_basis": "工程范围已核对",
        "snapshot": {"snapshot_id": "snapshot-A"}, "compilation_fingerprint": "compiled-A",
        "required_confirmation": {"expected_profile_fingerprint": "profile-A"},
        "profiles": [{"sources": {"origin_attack": {"node_sha256": "node-A", "json_pointer": "/31/Default"}},
                      "content_fingerprint": "query-A", "raw_nodes": ["must not dump raw configuration"]}]}]
    rows = elemental_state_rows(result)
    flattened = " ".join(str(cell) for row in rows for cell in row)
    for value in ("snapshot-A", "profile-A", "node-A", "/31/Default", "工程范围已核对"):
        assert value in flattened
    assert "must not dump" not in flattened


@pytest.mark.parametrize("case", ["ordinary", "double"])
def test_actual_freeze_engine_trace_and_formula_baseline(case):
    from formula_workbook_fixtures import mechanics_envelope
    from test_freeze_runtime import double_inputs, inputs

    receipt = mechanics_envelope(inputs() if case == "ordinary" else double_inputs())
    content = build_formula_workbook(receipt)
    _, trace = sheet_cells(content, 5)
    text = [cell["value"] for cell in trace.values()]
    assert "experimental_reference" in text
    assert "冻结层自然到期" in text
    assert "层消费（不另计伤害）" in text
    for attempt in receipt["result"]["elemental"]["trace"]:
        assert attempt["event_id"] in text
        if "package_id" in attempt:
            assert attempt["package_id"] in text
    _, events = sheet_cells(content, 3)
    assert [float(events[f"V{i}"]["value"]) for i in range(5, 5 + len(receipt["result"]["hits"]))] == pytest.approx(
        [hit["expected"] for hit in receipt["result"]["hits"]])
    assert all(cell["formula"] is None for cell in trace.values())


def test_changed_native_reference_input_rejects_stale_formula_export():
    from genshin_app.equipment.files import input_view

    receipt = calculated()
    receipt["plan"]["manual_template"] = copy.deepcopy(receipt["plan"]["resolved_template"])
    receipt["result_receipt"]["input_fingerprint"] = fingerprint(input_view(receipt["plan"]))
    build_formula_workbook(receipt)
    receipt["plan"]["mechanics_model"]["elemental_model"]["reference_acceptance"]["acknowledged"] = False
    with pytest.raises(FormulaWorkbookError, match="参考输入.*指纹"):
        build_formula_workbook(receipt)
