import copy
import json
import re
import sqlite3

import pytest

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    _Cell,
    _Sheet,
    _worksheet_xml,
    build_formula_workbook,
)
from tests.formula_owner_oracle import NS, evaluator, read


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("This synthetic manual case opened a database"))


def plan_for(ids=("a", "A"), *, count=1, external=False, external_count=None):
    plan = new_plan()
    template = plan["manual_template"]
    template.update(loop_duration=2, characters=[], hits=[])
    for index, cid in enumerate(ids):
        template["characters"].append({"id": cid, "name": "相同展示名", "level": 90,
            "stats": {"atk": 100 * (index + 1), "crit_rate": 0, "crit_dmg": .5}})
        if cid in ("no-hit", "external-only"):
            continue
        template["hits"].extend({"id": f"h.{index}.{j}", "t": 1, "character": cid,
            "skill": "虚构工程命中", "multiplier": 1, "damage_type_ids": ["damage.elemental_skill"],
            "damage_system": "classic", "element": "cryo"} for j in range(count))
    if external:
        owners = [cid for cid in ids if cid != "no-hit"]
        count_items = external_count if external_count is not None else len(owners)
        plan["external_damage_supplements"] = [{"id": f"extra.{i}", "label": "工程外部项",
            "character": owners[i % len(owners)], "skill": "独立外部项", "t": 1.5, "expected": i + 2,
            "action_id": None, "coverage_key": f"coverage.{i}", "event_id": f"external.{i}",
            "reason": "工程输入", "calculation_note": "固定工程常量", "non_overlap_note": "独立事件",
            "source_refs": ["合成材料"], "accepted_fingerprint": None} for i in range(count_items)]
    return plan


def calculated(plan, tmp_path):
    absent = tmp_path / "absent.db"
    if plan.get("external_damage_supplements"):
        review = compose_equipment_plan(plan, absent)
        for item, row in zip(plan["external_damage_supplements"], review["external_damage_review"]["items"], strict=True):
            assert row["required_confirmation"] is not None, row
            item.update(row["required_confirmation"])
    result = run_plan_calc(plan, absent)
    assert not absent.exists()
    return result


def validate_export(result, tmp_path):
    before = copy.deepcopy(result)
    raw = build_formula_workbook(result)
    assert result == before
    pages, roots = read(raw)
    evaluate = evaluator(pages)
    ids = [c["id"] for c in result["plan"]["resolved_template"]["characters"]]
    totals = result["result"].get("calculation_totals", result["result"]["summary"])
    selected = {}
    for index, cid in enumerate(ids, 12):
        cell = f"B{index}"
        expected = totals["by_character"].get(cid, 0)
        assert pages["汇总"][cell]["value"] == pytest.approx(expected)
        assert evaluate("汇总", cell) == pytest.approx(expected)
        selected[cid] = evaluate("汇总", cell)
        formula = pages["汇总"][cell]["formula"]
        assert len(formula) < 512
        assert not re.search(r'SUMIFS\([^)]*,"', formula)
    assert evaluate("汇总", "B6") == pytest.approx(totals["total_damage"])
    assert sum(selected.values()) == pytest.approx(totals["total_damage"])
    (tmp_path / "workbook.xlsx").write_bytes(raw)
    (tmp_path / "observations.json").write_text(json.dumps({"totals": selected,
        "max_formula_characters": max(len(c["formula"] or "") for p in pages.values() for c in p.values()),
        "excel_execution": False}, ensure_ascii=False, indent=2), "utf-8")
    return pages, roots, selected


@pytest.mark.parametrize("external", [False, True])
def test_real_public_export_exact_identity_and_literal_characters(tmp_path, external):
    ids = ("a", "A", "w*", "wild", "q?", "qx", "~", '<&"', "=x", "<x", "x", "01", "1", "Ä", "ä", "no-hit", "external-only")
    result = calculated(plan_for(ids, external=external), tmp_path)
    pages, roots, selected = validate_export(result, tmp_path)
    assert selected["a"] != selected["A"]
    assert selected["no-hit"] == 0
    assert selected["external-only"] == (17 if external else 0)
    assert {int(c.attrib["min"]) for c in roots["逐事件"].findall("s:cols/s:col", NS) if c.attrib.get("hidden") == "1"} == {30, 31}
    assert all(c["style"] == "5" for ref, c in pages["逐事件"].items() if ref.startswith(("AD", "AE")) and int(re.sub("[A-Z]", "", ref)) >= 5)
    if external:
        assert roots["外部补充伤害"].find("s:cols/s:col[@min='12']", NS).attrib["hidden"] == "1"


@pytest.mark.parametrize("external", [False, True])
def test_arbitrary_native_order_preserves_exact_domain(tmp_path, external):
    first = plan_for(("a", "A", "no-hit", '<&"', "01", "1"), count=3, external=external)
    second = copy.deepcopy(first)
    second["manual_template"]["characters"].reverse()
    second["manual_template"]["hits"] = second["manual_template"]["hits"][1::2] + second["manual_template"]["hits"][::2]
    if external:
        second["external_damage_supplements"].reverse()
    totals = []
    for index, plan in enumerate((first, second)):
        path = tmp_path / str(index)
        path.mkdir()
        totals.append(validate_export(calculated(plan, path), path)[2])
    assert totals[0] == totals[1]


@pytest.mark.parametrize(("column", "value"), [("AB", "A"), ("AB", "missing"), ("AB", ""),
    ("AB", 1), ("AB", True), ("AE", "A"), ("AE", ""), ("AE", 1),
    ("AD", 2), ("AD", "1"), ("AD", ""), ("AD", True), ("AD", 1.5)])
def test_engine_identity_edits_invalidate(tmp_path, column, value):
    pages, _ = read(build_formula_workbook(calculated(plan_for(), tmp_path)))
    evaluate = evaluator(pages, {("逐事件", column + "5"): value})
    assert evaluate("逐事件", "AC5") == 1
    assert evaluate("汇总", "B6") == "需重新生成"
    assert evaluate("汇总", "B12") == "需重新生成"


@pytest.mark.parametrize(("column", "value"), [("K", "A"), ("K", "missing"), ("K", ""),
    ("K", 1), ("K", True), ("L", 2), ("L", "1"), ("L", ""), ("L", True), ("L", 1.5)])
def test_external_identity_edits_invalidate_only_combined(tmp_path, column, value):
    pages, _ = read(build_formula_workbook(calculated(plan_for(external=True), tmp_path)))
    evaluate = evaluator(pages, {("外部补充伤害", column + "5"): value})
    assert evaluate("外部补充伤害", "J5") == 1
    assert evaluate("汇总", "B6") == "需重新复核/生成"
    assert evaluate("汇总", "B12") == "需重新复核/生成"
    engine_row = next(ref[1:] for ref, c in pages["汇总"].items() if ref.startswith("A") and c["value"] == "引擎计算（局部公式）")
    assert evaluate("汇总", "B" + engine_row) == 135


def test_numeric_identity_cells_require_text_even_if_exact_coerces(tmp_path):
    pages, _ = read(build_formula_workbook(calculated(plan_for(("1", "01"), external=True), tmp_path)))
    for sheet, column in (("逐事件", "AB"), ("逐事件", "AE"), ("外部补充伤害", "K")):
        evaluate = evaluator(pages, {(sheet, column + "5"): 1})
        assert evaluate("汇总", "B6") == "需重新复核/生成"


@pytest.mark.parametrize("external", [False, True])
def test_ordinary_numeric_edit_preserves_existing_local_scope(tmp_path, external):
    pages, _ = read(build_formula_workbook(calculated(plan_for(external=external), tmp_path)))
    row = next(ref[1:] for ref, c in pages["参数"].items() if ref.startswith("A") and c["value"] == "characters[0].stats.atk")
    evaluate = evaluator(pages, {("参数", "C" + row): 200})
    assert evaluate("逐事件", "V5") == 90
    assert evaluate("逐事件", "V6") == 90
    assert evaluate("汇总", "B12") == ("需重新复核/生成" if external else 90)


@pytest.mark.parametrize(("count", "external_count"), [(900, 1), (10000, 1), (1, 512)])
def test_large_real_exports_have_bounded_owner_formulas(tmp_path, count, external_count):
    result = calculated(plan_for(("a",), count=count, external=True, external_count=external_count), tmp_path)
    validate_export(result, tmp_path)


@pytest.mark.parametrize("damage", ["unknown", "duplicate", "numeric"])
def test_invalid_native_identities_are_explicitly_rejected(tmp_path, damage):
    plan = plan_for()
    if damage == "unknown":
        plan["manual_template"]["hits"][0]["character"] = "unknown"
    elif damage == "duplicate":
        plan["manual_template"]["characters"][1]["id"] = "a"
    else:
        plan["manual_template"]["characters"][0]["id"] = 1
    with pytest.raises(ValueError):
        build_formula_workbook(calculated(plan, tmp_path))


@pytest.mark.parametrize("limit", ["rows", "columns", "formula"])
def test_existing_formula_limit_and_sheet_capacity_fail_explicitly(limit):
    sheet = _Sheet("工程极限")
    sheet.rows = [[]] * 1048577 if limit == "rows" else [[_Cell(None)] * 16385] if limit == "columns" else [[_Cell(0, "1+" * 4096 + "1")]]
    with pytest.raises(FormulaWorkbookError, match="超过 Excel"):
        _worksheet_xml(sheet)




def test_complete_event_and_external_rows_can_be_reordered_without_owner_lists(tmp_path):
    result = calculated(plan_for(("a", "A", "no-hit", "01", "1"), count=2, external=True), tmp_path)
    pages, _ = read(build_formula_workbook(result))
    original = evaluator(pages)
    expected = [original("汇总", f"B{i}") for i in range(12, 17)]
    moved = copy.deepcopy(pages)
    for sheet, count in (("逐事件", 8), ("外部补充伤害", 4)):
        mapping = dict(zip(range(5, 5 + count), reversed(range(5, 5 + count)), strict=True))
        for address, record in pages[sheet].items():
            match = re.fullmatch(r"([A-Z]+)(\d+)", address)
            old_row = int(match[2])
            if old_row not in mapping:
                continue
            updated = copy.deepcopy(record)
            if updated["formula"]:
                def relocate(m):
                    target_sheet = m[1]
                    # re.sub consumes this callback synchronously in this iteration.
                    if (target_sheet is None or target_sheet == sheet) and int(m[4]) in mapping:  # noqa: B023
                        return (f"'{target_sheet}'!" if target_sheet else "") + m[2] + m[3] + str(mapping[int(m[4])])  # noqa: B023
                    return m[0]
                updated["formula"] = re.sub(r"(?<![A-Za-z0-9_])(?:'([^']+)'!)?(\$?[A-Z]+)(\$?)(\d+)", relocate, updated["formula"])
            moved[sheet][match[1] + str(mapping[old_row])] = updated
    evaluate = evaluator(moved)
    assert [evaluate("汇总", f"B{i}") for i in range(12, 17)] == expected
    assert evaluate("汇总", "B6") == original("汇总", "B6")


def test_long_common_prefix_ids_are_not_truncated_or_formula_criteria(tmp_path):
    prefix = "x" * 4096
    result = calculated(plan_for((prefix + "a", prefix + "A")), tmp_path)
    pages, _, totals = validate_export(result, tmp_path)
    assert totals == {prefix + "a": 45, prefix + "A": 90}
    assert pages["逐事件"]["AE5"]["value"] == prefix + "a"
    assert pages["逐事件"]["AE6"]["value"] == prefix + "A"
    assert len(pages["汇总"]["B12"]["formula"]) < 256
