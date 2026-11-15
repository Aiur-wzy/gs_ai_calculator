"""Independent source bridge and actual calculation counterexamples."""
import copy
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.schema import PlanError
from tests.test_attribute_app_sources import attribute_plan, known_classification
from tests.test_mechanics_execution import add
from tests.test_plan_mechanics import review


def add_reader(plan, basis="total"):
    row = copy.deepcopy(plan["mechanics_model"]["rules"][0])
    row.update(id="reader", source_id="binding:reader")
    row["effects"][0].update(benefit_type_id="benefit.base_add.all", domain="provider",
        value={"kind": "stat", "subject": "owner", "stat": "em", "basis": basis, "sample": "current", "factor": 1})
    add(plan["mechanics_model"], row)
    part = copy.deepcopy(plan["effect_bindings"][0]["parts"][0])
    part.update(part_id="reader", label="Engineering reader")
    part["rule"]["rule_id"] = "reader"
    plan["effect_bindings"][0]["parts"].append(part)


def adopt(plan, db):
    review(plan, db)
    out = prepare_attribute_review(plan, "Explicit engineering attribute facts reviewed", db)
    assert out["state"] == "ready", out["missing_inputs"]
    return out["plan"]


def test_unknown_writer_total_real_calculation_and_reload_are_equal(tmp_path):
    p = attribute_plan()
    add_reader(p)
    before = copy.deepcopy(p)
    db = tmp_path / "absent.db"
    adopted = adopt(p, db)
    assert p["mechanics_model"]["attribute_model"] == before["mechanics_model"]["attribute_model"]
    assert adopted["mechanics_model"]["attribute_model"]["effect_classifications"] == []
    first = run_plan_calc(adopted, db)
    second = run_plan_calc(json.loads(json.dumps(first["plan"])), db)
    assert first["result"] == second["result"]
    assert [h["expected"] for h in first["result"]["hits"]] == pytest.approx([112.5]*3)
    assert first["result"]["attribute_ledger"]["receipts"][0]["value"] == 150
    assert not db.exists()


def test_unknown_writer_cannot_read_nonextra_or_fall_back_to_total(tmp_path):
    p = attribute_plan()
    add_reader(p, "nonextra")
    db = tmp_path / "absent.db"
    adopted = adopt(p, db)
    with pytest.raises(PlanError) as err:
        run_plan_calc(adopted, db)
    assert err.value.reason == "mechanics_pending"
    assert err.value.details["result"] is None
    assert any(i["code"] == "attribute_classification_unknown" for i in err.value.details["issues"])


def test_same_seed_and_dynamic_source_actual_read_is_rejected_not_added_twice(tmp_path):
    p = attribute_plan()
    add_reader(p)
    row = known_classification(p)
    m = p["mechanics_model"]["attribute_model"]
    m["effect_classifications"] = [row]
    m["initial_basis"][0]["included_components"] = [
        {"component": copy.deepcopy(row["component"]), "value": 50, "nonextra_inclusion": "included"}]
    db = tmp_path / "absent.db"
    adopted = adopt(p, db)
    with pytest.raises(PlanError) as err:
        run_plan_calc(adopted, db)
    assert err.value.reason == "mechanics_pending"
    assert any(i["code"] == "attribute_duplicate_delivery" for i in err.value.details["issues"])


def test_reconfirm_cannot_promote_explicit_pending_classification(tmp_path):
    p = attribute_plan()
    add_reader(p, "nonextra")
    row = known_classification(p)
    row["status"] = "pending"
    p["mechanics_model"]["attribute_model"]["effect_classifications"] = [row]
    db = tmp_path / "absent.db"
    adopted = adopt(p, db)
    assert adopted["mechanics_model"]["attribute_model"]["effect_classifications"][0]["status"] == "pending"
    with pytest.raises(PlanError) as err:
        run_plan_calc(adopted, db)
    assert any(i["code"] == "attribute_classification_unknown" for i in err.value.details["issues"])


def test_current_context_hash_changes_with_note_and_source_value_but_not_dict_order(tmp_path):
    p = attribute_plan()
    p["mechanics_model"]["attribute_model"]["effect_classifications"] = [known_classification(p)]
    db = tmp_path / "absent.db"
    adopted = adopt(p, db)
    fp = compose_equipment_plan(adopted, db)["attribute_source_adoption"]["components"][0]["expected_component"]
    reordered = dict(reversed(list(adopted.items())))
    assert compose_equipment_plan(reordered, db)["attribute_source_adoption"]["components"][0]["expected_component"] == fp
    for change in ("note", "value"):
        changed = copy.deepcopy(adopted)
        if change == "note":
            changed["attribute_review"]["note"] += " new assumption"
        else:
            changed["mechanics_model"]["rules"][0]["effects"][0]["value"] = 60
        out = compose_equipment_plan(changed, db)
        assert out["state"] == "pending"
        assert {"attribute_scope_unreviewed", "attribute_source_stale"} <= {i["code"] for i in out["missing_inputs"]}


def test_unready_binding_is_not_reported_as_explicitly_excluded(tmp_path):
    p = attribute_plan()
    p["mechanics_model"]["attribute_model"]["effect_classifications"] = [known_classification(p)]
    p["effect_bindings"][0]["parts"][0].update(state="pending", reason="Unresolved actual source")
    db = tmp_path / "absent.db"
    review(p, db)
    report = prepare_attribute_review(p, "Reviewed attributes, actual source remains unresolved", db)
    assert report["state"] == "pending" and report["missing_inputs"]
    assert report["attribute_source_adoption"]["components"][0]["status"] != "excluded"
    with pytest.raises(PlanError):
        run_plan_calc(report["plan"], db)


@pytest.mark.parametrize("kind", ["rule_self", "manual_target"])
def test_initial_component_cannot_claim_an_impossible_fixed_recipient(tmp_path, kind):
    p = attribute_plan()
    other = copy.deepcopy(p["manual_template"]["characters"][0])
    other["id"] = "b"
    p["manual_template"]["characters"].append(other)
    component = known_classification(p)["component"]
    if kind == "rule_self":
        p["mechanics_model"]["rules"][0]["beneficiaries"] = {"mode": "self", "scope": "locked"}
    else:
        p["manual_template"]["buffs"].append({"id": "manual-em", "start": 0, "end": 1,
            "target": "a", "scope": "team", "extensions": {"owner": "a", "source_id": "manual:em"},
            "effects": [{"id": "value", "zone": "em", "stat": "all", "benefit_type_id": "benefit.em.all",
                         "value": 50, "applies_to": {"mode": "all"}}]})
        component = {**component, "part_id": "manual:em"}
    p["mechanics_model"]["attribute_model"]["initial_basis"].append({"actor": "b",
        "included_components": [{"component": component, "value": 50, "nonextra_inclusion": "included"}]})
    with pytest.raises(PlanError, match="受益"):
        prepare_attribute_review(p, "Cannot attribute actor-a-only source to actor b", tmp_path / "absent.db")


def test_formula_workbook_retains_nonextra_cache_full_ledger_and_edit_guard(tmp_path):
    from genshin_app.formula_workbook import (
        formula_workbook_support,
        write_formula_workbook,
    )
    from tests.test_formula_workbook import read_sheet
    p = attribute_plan()
    add_reader(p, "nonextra")
    row = known_classification(p)
    row["nonextra_inclusion"] = "excluded"
    p["mechanics_model"]["attribute_model"]["effect_classifications"] = [row]
    p["mechanics_model"]["attribute_model"]["initial_basis"][0]["nonextra"] = {
        "mode": "explicit", "value": 60, "status": "assumed", "source": "Independent engineered NONEXTRA basis"}
    db = tmp_path / "absent.db"
    adopted = adopt(p, db)
    out = run_plan_calc(adopted, db)
    assert [h["expected"] for h in out["result"]["hits"]] == pytest.approx([72]*3)
    path = tmp_path / "nonextra.xlsx"
    write_formula_workbook(out, path)
    before = path.read_bytes()
    support = formula_workbook_support(out)
    assert support["editable"]["attribute_model_and_basis"] == "regenerate_required"
    assert support["editable"]["character_stats"] == "regenerate_required"
    assert support["attribute_sampling"] == "readonly_execution_receipts"
    assert any(i["code"] == "attribute_ledger_sample_fixed" for i in support["limitations"])
    events, summary, params, sources = [read_sheet(before, number) for number in (3, 1, 2, 4)]
    for index in (5, 6, 7):
        assert events[f"V{index}"]["value"] == pytest.approx(72)
        assert events[f"Y{index}"]["value"] == pytest.approx(0)
    assert summary["B6"]["value"] == pytest.approx(216)
    assert "SUM('参数'!I5:" in summary["B6"]["formula"] and "需重新生成" in summary["B6"]["formula"]
    rows = {cell["value"]: int(ref[1:]) for ref, cell in params.items() if ref.startswith("A")}
    em_row = rows["characters[0].stats.em"]
    assert params[f"G{em_row}"]["formula"] == f"IF(C{em_row}<>D{em_row},1,0)"
    assert params[f"I{em_row}"]["formula"] == f"IF(C{em_row}<>D{em_row},1,0)"
    # Source sheet must reconstruct the complete ledger, not just mention its name.
    index = next(int(ref[1:]) for ref, cell in sources.items() if ref.startswith("A") and cell["value"] == "attribute_ledger")
    start, end = map(int, re.search(r"!C(\d+):C(\d+)", sources[f"B{index}"]["value"]).groups())
    with zipfile.ZipFile(io.BytesIO(before)) as workbook:
        sheets = ET.fromstring(workbook.read('xl/workbook.xml')).findall('{*}sheets/{*}sheet')
        source_number = next(i for i, sheet in enumerate(sheets, 1) if sheet.attrib['name'] == '完整来源JSON')
    detail = read_sheet(before, source_number)
    parts = [detail[f'C{row}']['value'] for row in range(start, end + 1)]
    assert all(detail[f'A{row}']['value'] == 'attribute_ledger' for row in range(start, end + 1))
    assert [detail[f'B{row}']['value'] for row in range(start, end + 1)] == list(range(1, end - start + 2))
    assert all(len(chunk.encode('utf-16-le')) // 2 <= 30000 for chunk in parts)
    raw = ''.join(parts).encode('utf-8')
    assert hashlib.sha256(raw).hexdigest() == sources[f'C{index}']['value']
    assert len(raw) == sources[f'D{index}']['value']
    assert json.loads(raw) == out["result"]["attribute_ledger"]
    changed = copy.deepcopy(adopted)
    changed["mechanics_model"]["attribute_model"]["initial_basis"][0]["nonextra"]["value"] = 80
    fresh = run_plan_calc(adopt(changed, db), db)
    assert [h["expected"] for h in fresh["result"]["hits"]] == pytest.approx([81]*3)
    assert path.read_bytes() == before  # An old workbook is not an attribute interpreter.


def test_readonly_attribute_sheet_reconstructs_actual_ledger_in_all_export_paths(tmp_path):
    import io
    import xml.etree.ElementTree as et
    import zipfile

    from genshin_app.equipment.service import result_bytes
    from genshin_app.export import export_result
    from genshin_app.formula_workbook import build_formula_workbook
    from tests.test_formula_workbook import read_sheet

    p = attribute_plan()
    add_reader(p)
    out = run_plan_calc(adopt(p, tmp_path / "absent.db"), tmp_path / "absent.db")
    original = copy.deepcopy(out)
    direct = tmp_path / "direct-attributes.xlsx"
    export_result(out["result"], "xlsx", direct, receipt=out["result_receipt"])
    contents = {
        "App": result_bytes(out["result"], out["result_receipt"], "xlsx"),
        "direct": direct.read_bytes(),
        "formula": build_formula_workbook(out),
    }
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    for route, content in contents.items():
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            assert archive.testzip() is None
            sheets = et.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", ns)
            number = next(i+1 for i, row in enumerate(sheets) if row.attrib["name"] == "属性取样账")
        cells = read_sheet(content, number)
        assert "须回App重算" in cells["B1"]["value"], route
        assert all(cell["formula"] is None for cell in cells.values()), route
        parts = sorted((int(cell["value"].split()[-1]), cells[f"B{ref[1:]}"]["value"])
            for ref, cell in cells.items() if ref.startswith("A")
            and isinstance(cell["value"], str) and cell["value"].startswith("原文块 "))
        assert [number for number, _ in parts] == list(range(1, len(parts)+1)), route
        assert json.loads("".join(value for _, value in parts)) == out["result"]["attribute_ledger"], route
        assert cells["A3"]["value"] == out["result"]["attribute_ledger"]["receipts"][0]["receipt_id"]
        assert cells["I3"]["value"] == 150
    assert out == original
