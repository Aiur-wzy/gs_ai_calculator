"""Independent numeric SUMIFS domain audit; no author oracle or DB."""
import copy
import io
import re
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.formula_workbook import build_formula_workbook

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
IDS = ["A", "a", "Ａ", "A ", "é", "e\u0301", "零", "1", "01", "empty"]


@pytest.fixture(autouse=True)
def forbid_db(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Independent formula cases must not read any DB")
    monkeypatch.setattr(sqlite3, "connect", denied)


def fixture(tmp_path, *, external=False, count=1, reverse=False, only_external=False):
    plan = new_plan()
    template = plan["manual_template"]
    template.update(loop_duration=2, characters=[], hits=[])
    for i, cid in enumerate(IDS):
        template["characters"].append({"id": cid, "name": "同名", "level": 90,
            "stats": {"atk": 100 * (i + 1), "crit_rate": 0, "crit_dmg": .5}})
        if cid == "empty" or (only_external and i != 0):
            continue
        for j in range(count):
            template["hits"].append({"id": f"h.{i}.{j}", "t": 1, "character": cid,
                "skill": "有限工程伤害", "multiplier": 1, "damage_type_ids": ["damage.elemental_skill"],
                "damage_system": "classic", "element": "cryo"})
    if external:
        plan["external_damage_supplements"] = [{"id": f"e.{i}", "label": "固定外部值",
            "character": cid, "skill": "独立事件", "t": 1.2, "expected": 17 * (i + 1),
            "action_id": None, "coverage_key": f"c.{i}", "event_id": f"x.{i}",
            "reason": "测试", "calculation_note": "独立给定常量", "non_overlap_note": "独立于引擎命中",
            "source_refs": ["工程常量"], "accepted_fingerprint": None} for i, cid in enumerate(IDS[:-1])]
    if reverse:
        template["characters"].reverse()
        template["hits"].reverse()
        if external:
            plan["external_damage_supplements"].reverse()
    absent = tmp_path / "absent.db"
    if external:
        report = compose_equipment_plan(plan, absent)
        for item, row in zip(plan["external_damage_supplements"], report["external_damage_review"]["items"], strict=True):
            item.update(row["required_confirmation"])
    result = run_plan_calc(plan, absent)
    before = copy.deepcopy(result)
    raw = build_formula_workbook(result)
    assert result == before and not absent.exists()
    (tmp_path / "independent.xlsx").write_bytes(raw)
    return result, raw


def sheets(raw):
    out = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert z.testzip() is None
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        for index, name in enumerate(wb.findall("s:sheets/s:sheet", NS), 1):
            root = ET.fromstring(z.read(f"xl/worksheets/sheet{index}.xml"))
            cells = {}
            for c in root.findall(".//s:c", NS):
                v, f = c.find("s:v", NS), c.find("s:f", NS)
                value = "".join(c.itertext()) if c.attrib.get("t") == "inlineStr" else float(v.text) if v is not None and c.attrib.get("t") != "str" else v.text if v is not None else ""
                cells[c.attrib["r"]] = (value, f.text if f is not None else None, c.attrib.get("s"))
            out[name.attrib["name"]] = (root, cells)
    return out


@pytest.mark.parametrize("external,reverse,only_external", [(False, False, False), (True, False, False),
    (False, True, False), (True, True, False), (True, True, True)])
def test_exact_domain_and_full_strings(tmp_path, external, reverse, only_external):
    calculated, raw = fixture(tmp_path, external=external, reverse=reverse, only_external=only_external)
    pages = sheets(raw)
    chars = calculated["plan"]["resolved_template"]["characters"]
    hits = calculated["plan"]["resolved_template"]["hits"]
    key_owner = {i + 1: c["id"] for i, c in enumerate(chars)}
    numeric_rows = {}
    for row, hit in enumerate(hits, 5):
        cells = pages["逐事件"][1]
        cid = hit["character"]
        assert cells[f"AB{row}"][0] == cells[f"AE{row}"][0] == cid
        key = cells[f"AD{row}"][0]
        assert key_owner[key] == cid
        assert cells[f"AD{row}"][2] == cells[f"AE{row}"][2] == "5"
        # Independent classic formula for this fixture, not the workbook cache.
        numeric_rows["逐事件", row] = .45 * 100 * (IDS.index(cid) + 1)
        guard = cells[f"AC{row}"][1]
        for clause in (f"ISTEXT(AB{row})", f"ISTEXT(AE{row})", f"EXACT(AB{row},AE{row})", f"ISNUMBER(AD{row})", f"AD{row}<>{int(key)}"):
            assert clause in guard
    if external:
        for row, item in enumerate(calculated["result"]["external_damage"]["items"], 5):
            cells = pages["外部补充伤害"][1]
            assert cells[f"K{row}"][0] == key_owner[cells[f"L{row}"][0]] == item["character"]
            numeric_rows["外部补充伤害", row] = 17 * (IDS.index(item["character"]) + 1)
    # Independently resolve each actual SUMIFS range against raw keys and known
    # fixture damage; never use an event formula cache as the expected answer.
    pattern = r"SUMIFS\('([^']+)'!([A-Z]+)(\d+):\2(\d+),'\1'!([A-Z]+)(\d+):\5(\d+),(\d+)\)"
    for row, char in enumerate(chars, 12):
        value, formula, _ = pages["汇总"][1][f"B{row}"]
        matches = re.findall(pattern, formula)
        assert len(matches) == (bool(hits) + external)
        actual = 0
        for sheet, sumcol, start, end, keycol, kstart, kend, key in matches:
            assert (start, end) == (kstart, kend)
            assert (sumcol, keycol) == (("V", "AD") if sheet == "逐事件" else ("F", "L"))
            assert key_owner[int(key)] == char["id"]
            actual += sum(numeric_rows[sheet, r] for r in range(int(start), int(end) + 1)
                if pages[sheet][1][f"{keycol}{r}"][0] == int(key))
        i = IDS.index(char["id"]) + 1
        engine_amount = 0 if only_external and char["id"] != IDS[0] else 45
        expected = 0 if char["id"] == "empty" else i * (engine_amount + (17 if external else 0))
        assert actual == pytest.approx(expected) and value == pytest.approx(expected)
        assert len(formula) < 512


def test_large_external_formula_does_not_enumerate_event_addresses(tmp_path):
    _, raw = fixture(tmp_path, external=True, count=112)
    pages = sheets(raw)
    formulas = [c[1] for ref, c in pages["汇总"][1].items() if ref.startswith("B") and c[1]]
    assert all(len(f) < 512 for f in formulas)
    assert all(not re.search(r"SUM\('逐事件'!V\d+,", f) for f in formulas)


def test_owner_columns_are_hidden_and_locked(tmp_path):
    _, raw = fixture(tmp_path, external=True)
    pages = sheets(raw)
    for name, expected in (("逐事件", {30, 31}), ("外部补充伤害", {12})):
        root, _ = pages[name]
        assert root.find("s:sheetProtection", NS) is not None
        hidden = {int(c.attrib["min"]) for c in root.findall("s:cols/s:col", NS) if c.attrib.get("hidden") == "1"}
        assert hidden == expected
