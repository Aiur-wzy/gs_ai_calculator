"""External additions stay fixed and distinct from recalculable engine hits."""
from __future__ import annotations

import ast
import copy
import io
import json
import operator
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import enable_mechanics, new_plan, run_plan_calc
from genshin_app.equipment.delivery import build_calculation_delivery
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result, external_damage_rows, summary_rows
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_external_damage import confirm, external_plan, supplement
from tests.test_freeze_export_review import workbook_sheets


def evaluate(sheets, sheet, address, edits=None):
    """Restricted Excel arithmetic interpreter; never read cached formula answers."""
    edits = edits or {}
    memo = {}

    def cell(name, addr):
        addr = addr.replace("$", "")
        if (name, addr) in edits:
            return edits[name, addr]
        if (name, addr) in memo:
            return memo[name, addr]
        data = sheets[name].get(addr, {"value": "", "formula": None})
        if not data["formula"]:
            return data["value"]
        literals, refs = [], []

        def string(match):
            literals.append(match[0][1:-1].replace('""', '"'))
            return f"_literal_{len(literals) - 1}"

        formula = re.sub(r'"(?:[^"]|"")*"', string, data["formula"])

        def ref(match):
            target = match[1] or name
            start, end = match[2].replace("$", ""), match[3]
            if end:
                end = end.replace("$", "")
                first = re.fullmatch(r"([A-Z]+)(\d+)", start)
                last = re.fullmatch(r"([A-Z]+)(\d+)", end)
                assert first[1] == last[1]
                refs.append([cell(target, first[1] + str(i)) for i in range(int(first[2]), int(last[2]) + 1)])
            else:
                refs.append(cell(target, start))
            return f"_reference_{len(refs) - 1}"

        formula = re.sub(r"(?<![A-Za-z0-9_])(?:'([^']+)'!)?(\$?[A-Z]+\$?\d+)(?::(\$?[A-Z]+\$?\d+))?", ref, formula)
        formula = formula.replace("<>", "!=").replace("^", "**")
        formula = re.sub(r"(?<![<>=!])=(?!=)", "==", formula)
        ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
               ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Lt: operator.lt,
               ast.Gt: operator.gt, ast.LtE: operator.le, ast.GtE: operator.ge,
               ast.Eq: operator.eq, ast.NotEq: operator.ne}

        def visit(node):
            if isinstance(node, ast.Constant):
                return node.value
            if isinstance(node, ast.Name):
                if node.id.startswith("_literal_"):
                    return literals[int(node.id.rsplit("_", 1)[1])]
                if node.id.startswith("_reference_"):
                    return refs[int(node.id.rsplit("_", 1)[1])]
                return {"TRUE": True, "FALSE": False}[node.id]
            if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
                return -visit(node.operand)
            if isinstance(node, ast.BinOp):
                return ops[type(node.op)](visit(node.left), visit(node.right))
            if isinstance(node, ast.Compare):
                assert len(node.ops) == 1
                return ops[type(node.ops[0])](visit(node.left), visit(node.comparators[0]))
            if isinstance(node, ast.Call):
                kind = node.func.id
                if kind == "IF":
                    return visit(node.args[1] if visit(node.args[0]) else node.args[2])
                args = [visit(a) for a in node.args]
                if kind == "SUM":
                    return sum(v for a in args for v in (a if isinstance(a, list) else [a])
                               if type(v) in (int, float))
                if kind == "SUMIFS":
                    assert len(args) == 3
                    if type(args[2]) in (int, float):
                        return sum(v for v, key in zip(args[0], args[1], strict=True)
                                   if type(key) in (int, float) and key == args[2])
                    assert not any(c in args[2] for c in "~*?")
                    return sum(v for v, key in zip(args[0], args[1], strict=True)
                               if str(key).casefold() == str(args[2]).casefold())
                return {"OR": lambda *a: any(a), "AND": lambda *a: all(a), "NOT": operator.not_,
                        "EXACT": lambda a, b: type(a) is type(b) and a == b,
                        "ISNUMBER": lambda a: type(a) in (int, float), "ISTEXT": lambda a: isinstance(a, str),
                        "MIN": min, "MAX": max,
                        "MOD": operator.mod, "ABS": abs}[kind](*args)
            raise AssertionError(ast.dump(node))

        answer = visit(ast.parse(formula, mode="eval").body)
        memo[name, addr] = answer
        return answer

    return cell(sheet, address)


@pytest.fixture
def calculated(tmp_path):
    db = tmp_path / "absent.db"
    plan = confirm(external_plan(), db)
    return run_plan_calc(plan, db, capture_display=True)


def row_named(sheet, name):
    return next(address[1:] for address, cell in sheet.items() if address.startswith("A") and cell["value"] == name)


def test_both_snapshots_and_formula_keep_external_values_and_provenance(calculated, tmp_path, monkeypatch):
    original = copy.deepcopy(calculated)
    result, receipt = calculated["result"], calculated["result_receipt"]
    assert result["summary"]["total_damage"] == 45
    assert result["calculation_totals"]["total_damage"] == 57
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("offline export opened DB"))
    snapshots = [result_bytes(result, receipt, "xlsx"),
                 export_result(result, "xlsx", tmp_path / "snapshot.xlsx", receipt=receipt).read_bytes()]
    for raw in snapshots:
        sheets = workbook_sheets(raw)
        assert sheets["汇总"]["B2"]["value"] == 57
        assert sheets["外部补充伤害"]["F5"]["value"] == 12
        assert sheets["外部补充伤害"]["F5"]["formula"] is None
        assert "引擎计算" in [c["value"] for c in sheets["汇总"].values()]
    raw = build_formula_workbook(calculated)
    sheets = workbook_sheets(raw)
    assert sheets["汇总"]["B6"]["value"] == 57
    assert evaluate(sheets, "汇总", "B6") == 57
    assert evaluate(sheets, "汇总", "B7") == pytest.approx(result["calculation_totals"]["dps"])
    assert evaluate(sheets, "逐事件", "V5") == 45
    actor = calculated["plan"]["resolved_template"]["characters"][0]
    name = actor.get("name") or actor["id"]
    assert evaluate(sheets, "汇总", "B" + row_named(sheets["汇总"], name)) == 57
    assert sheets["外部补充伤害"]["F5"]["formula"] is None
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        index = [s.attrib["name"] for s in workbook.findall("s:sheets/s:sheet", ns)].index("外部补充伤害") + 1
        page = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
        assert page.find("s:sheetProtection", ns) is not None
        assert page.find(".//s:c[@r='F5']", ns).attrib["s"] == "5"
    assert calculated == original


@pytest.mark.parametrize("change", ["atk", "resistance", "external", "external_blank", "external_actor"])
def test_edit_invalidates_combined_but_preserves_engine_local_formula(calculated, change):
    sheets = workbook_sheets(build_formula_workbook(calculated))
    paths = {c["value"]: "C" + a[1:] for a, c in sheets["参数"].items() if a.startswith("A")}
    edits, engine = {}, 45
    if change == "atk":
        edits["参数", paths["characters[0].stats.atk"]] = 200
        engine = 90
    elif change == "resistance":
        key = next(k for k in paths if k.startswith("enemy.resistance."))
        edits["参数", paths[key]] = .2
        engine = 40
    elif change == "external_actor":
        edits["外部补充伤害", "K5"] = "different"
    else:
        edits["外部补充伤害", "F5"] = 24 if change == "external" else ""
    assert evaluate(sheets, "汇总", "B6", edits) == "需重新复核/生成"
    assert evaluate(sheets, "汇总", "B7", edits) == "需重新复核/生成"
    engine_row = row_named(sheets["汇总"], "引擎计算（局部公式）")
    assert evaluate(sheets, "汇总", "B" + engine_row, edits) == pytest.approx(engine)
    support = formula_workbook_support(calculated)
    assert support["editable"]["external_damage_supplements"] == "regenerate_required"
    assert support["editable"]["character_stats"] == "supported"


@pytest.mark.parametrize("mutation", ["total", "external", "delete_proof", "delete_basis", "delete_items"])
def test_outer_resign_cannot_hide_external_tampering(calculated, mutation, tmp_path):
    changed = copy.deepcopy(calculated)
    result, receipt = changed["result"], changed["result_receipt"]
    if mutation == "total":
        result["calculation_totals"]["total_damage"] += 1
    elif mutation == "external":
        result["external_damage"]["items"][0]["expected"] += 1
    elif mutation == "delete_proof":
        receipt.pop("external_damage")
    elif mutation == "delete_basis":
        result["external_damage"].pop("basis")
    else:
        result["external_damage"]["items"] = []
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(changed)
    with pytest.raises(ValueError):
        export_result(result, "xlsx", tmp_path / "forged.xlsx", receipt=receipt)
    assert not (tmp_path / "forged.xlsx").exists()


def test_csv_rejects_before_writing(calculated, tmp_path):
    with pytest.raises(ValueError, match="CSV"):
        export_result(calculated["result"], "csv", tmp_path / "result.csv", receipt=calculated["result_receipt"])
    assert not (tmp_path / "result.csv").exists()


def test_four_artifacts_keep_same_external_ledger_and_combined_summary(calculated, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("delivery reopened DB"))
    built = build_calculation_delivery(calculated, input_file_sha="c" * 64)
    assert built["summary"]["total_damage"] == 57
    artifacts = built["artifacts"]
    native = json.loads(artifacts["native_plan"])
    saved = json.loads(artifacts["calculated"])["calculated"]
    assert saved == calculated
    assert native["external_damage_supplements"] == saved["result"]["external_damage"]["items"]
    assert workbook_sheets(artifacts["result_workbook"])["汇总"]["B2"]["value"] == 57
    formula = workbook_sheets(artifacts["formula_workbook"])
    assert evaluate(formula, "汇总", "B6") == 57


def test_empty_supplements_keep_original_summary_and_formula_behavior(tmp_path):
    db = tmp_path / "absent.db"
    plan = external_plan()
    plan.pop("external_damage_supplements")
    original = run_plan_calc(plan, db)
    plan["external_damage_supplements"] = []
    empty = run_plan_calc(plan, db)
    assert empty["result"] == original["result"]
    assert external_damage_rows(empty["result"]) == []
    assert summary_rows(empty["result"]) == summary_rows(original["result"])
    a, b = [workbook_sheets(build_formula_workbook(c)) for c in (original, empty)]
    assert a["汇总"] == b["汇总"] and a["逐事件"] == b["逐事件"]
    assert "外部补充伤害" not in a and "外部补充伤害" not in b


def test_zero_and_external_only_character_are_not_lost_from_totals(tmp_path):
    db = tmp_path / "absent.db"
    plan = external_plan()
    other = copy.deepcopy(plan["manual_template"]["characters"][0])
    other.update(id="b", name="另一工程角色")
    plan["manual_template"]["characters"].append(other)
    first = plan["external_damage_supplements"][0]
    second = copy.deepcopy(first)
    second.update(id="second", event_id="external.second", coverage_key="coverage.second",
                  character="b", expected=7, accepted_fingerprint=None)
    first["expected"] = 0
    plan["external_damage_supplements"].append(second)
    calc = run_plan_calc(confirm(plan, db), db)
    sheets = workbook_sheets(build_formula_workbook(calc))
    assert evaluate(sheets, "汇总", "B6") == 52
    row = row_named(sheets["汇总"], "另一工程角色")
    assert evaluate(sheets, "汇总", "B" + row) == 7
    assert sheets["外部补充伤害"]["F5"]["value"] == 0
    assert evaluate(sheets, "汇总", "B6", {("外部补充伤害", "F5"): ""}) == "需重新复核/生成"


def test_verified_miss_zero_is_engine_only_when_external_value_is_present(tmp_path):
    from tests.test_verified_empty_damage_review import miss_case

    db = tmp_path / "absent.db"
    template, rotation, model = miss_case()
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=model, external_damage_supplements=[supplement()])
    calc = run_plan_calc(confirm(plan, db), db, capture_display=True)
    assert calc["result"]["hits"] == []
    assert calc["result"]["summary"]["total_damage"] == 0
    assert calc["result"]["calculation_totals"]["total_damage"] == 12
    books = [result_bytes(calc["result"], calc["result_receipt"], "xlsx"), build_formula_workbook(calc)]
    for raw in books:
        cells = workbook_sheets(raw)
        proof = " ".join(str(c["value"]) for c in cells["伤害报告证明"].values())
        assert "引擎总伤害" in proof and "外部补充另列" in proof
        assert cells["外部补充伤害"]["F5"]["value"] == 12
    formula = workbook_sheets(books[1])
    assert "V5" not in formula["逐事件"]
    assert evaluate(formula, "汇总", "B6") == 12
    assert evaluate(formula, "汇总", "B7") == pytest.approx(12 / rotation["period"])


@pytest.mark.parametrize("both_engine_actors", [False, True])
def test_external_actor_identity_is_case_sensitive_for_totals_and_edit_guard(tmp_path, both_engine_actors):
    db = tmp_path / "absent.db"
    plan = external_plan()
    plan["manual_template"]["characters"][0]["name"] = "小写角色"
    other = copy.deepcopy(plan["manual_template"]["characters"][0])
    other.update(id="A", name="大写角色")
    plan["manual_template"]["characters"].append(other)
    if both_engine_actors:
        other_hit = copy.deepcopy(plan["manual_template"]["hits"][0])
        other_hit.update(id="upper.hit", character="A", multiplier=2)
        plan["manual_template"]["hits"].append(other_hit)
    first = plan["external_damage_supplements"][0]
    first["expected"] = 2
    second = copy.deepcopy(first)
    second.update(id="uppercase", event_id="external.uppercase", coverage_key="coverage.uppercase",
                  character="A", expected=7)
    plan["external_damage_supplements"].append(second)
    calc = run_plan_calc(confirm(plan, db), db)
    sheets = workbook_sheets(build_formula_workbook(calc))
    upper = 97 if both_engine_actors else 7
    assert calc["result"]["summary"]["by_character"] == ({"a": 45, "A": 90} if both_engine_actors else {"a": 45})
    assert calc["result"]["calculation_totals"]["by_character"] == {"a": 47, "A": upper}
    for name, expected, external_row in (("小写角色", 47, 5), ("大写角色", upper, 6)):
        address = "B" + row_named(sheets["汇总"], name)
        assert evaluate(sheets, "汇总", address) == expected
        assert sheets["汇总"][address]["value"] == expected
        owner_key = sheets["外部补充伤害"][f"L{external_row}"]["value"]
        assert owner_key == external_row - 4
        assert f"+SUMIFS('外部补充伤害'!F5:F6,'外部补充伤害'!L5:L6,{int(owner_key)})" in sheets["汇总"][address]["formula"]
    assert evaluate(sheets, "汇总", "B6") == 47 + upper
    assert evaluate(sheets, "汇总", "B6", {("外部补充伤害", "K5"): "A"}) == "需重新复核/生成"
    assert evaluate(sheets, "汇总", "B6", {("外部补充伤害", "K6"): "a"}) == "需重新复核/生成"
