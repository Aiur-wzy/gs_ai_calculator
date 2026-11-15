"""EC19 actual native calculation → offline zero-damage workbook projection."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from formula_workbook_fixtures import envelope
from test_action_resource_export import NS, sheets
from test_freeze_export_review import workbook_sheets
from test_limited_hit_benefit_exports import input_row
from test_limited_hit_benefit_sources import benefit_plan, confirm_benefit
from test_mechanics_execution import EVIDENCE
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_limited_hit_benefit import benefit_db, benefit_repo  # noqa: F401

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.service import result_bytes
from genshin_app.export import damage_reporting_rows, export_result
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)

# ruff: noqa: F811


@pytest.fixture
def calculated(benefit_db):
    plan = benefit_plan(benefit_db)
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": False, "order": 20, **EVIDENCE}]
    return run_plan_calc(confirm_benefit(plan, benefit_db), benefit_db)


def test_actual_full_miss_exports_zero_without_fake_hit_and_keep_source_history(calculated, monkeypatch, tmp_path):
    before = copy.deepcopy(calculated)
    result, receipt = calculated["result"], calculated["result_receipt"]
    assert result["summary"]["total_damage"] == result["summary"]["dps"] == 0
    assert result["hits"] == [] and calculated["plan"]["resolved_template"]["hits"] == []
    assert result["limited_hit_benefit_summary"]["consumed_hits"] == 0
    assert any(row["type"] == "activation" for row in result["mechanics_trace"])
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("export opened DB"))
    monkeypatch.setattr(sqlite3.dbapi2, "connect", lambda *a, **k: pytest.fail("export opened DB"))
    native = result_bytes(result, receipt, "xlsx")
    direct = export_result(result, "xlsx", tmp_path / "direct.xlsx", receipt=receipt).read_bytes()
    formula = build_formula_workbook(calculated)
    for blob in (native, direct, formula):
        pages = sheets(blob)
        proof = pages["伤害报告证明"]
        assert proof.find("s:sheetProtection", NS) is not None
        assert not proof.findall(".//s:f", NS)
        text = "".join(proof.itertext())
        assert "verified-empty-damage-v1" in text and "全部未命中" in text
        assert "有限次数收益" in pages
        assert not any(c.get("t") == "e" for p in pages.values() for c in p.findall(".//s:c", NS))
    for blob in (native, direct):
        assert len(sheets(blob)["逐事件明细"].findall("s:sheetData/s:row", NS)) == 1
    cells = workbook_sheets(formula)
    assert cells["汇总"]["B6"]["value"] == cells["汇总"]["B7"]["value"] == 0
    assert cells["汇总"]["B6"]["formula"].endswith(",0)")
    assert "B6/'参数'!C5" in cells["汇总"]["B7"]["formula"]
    assert "V5" not in cells["逐事件"]
    assert len(sheets(formula)["逐事件"].findall("s:sheetData/s:row", NS)) == 4
    assert "尚未证明" in cells["汇总"]["B9"]["value"]
    assert json.loads(result_bytes(result, receipt, "json"))["result"]["hits"] == []
    assert calculated == before


def test_fixed_proof_and_provider_atk_preserve_other_panel_edits(calculated):
    support = formula_workbook_support(calculated)
    assert support["zero_damage_projection"] == "readonly_verified_execution"
    assert support["editable"]["damage_reporting_contacts_and_outcomes"] == "regenerate_required"
    assert support["editable"]["limited_hit_provider_attack"] == "regenerate_required"
    cells = workbook_sheets(build_formula_workbook(calculated))["参数"]
    for path in ("characters[0].stats.atk", "damage_reporting.contacts.chunk[0]",
                 "damage_reporting.scope.chunk[0]", "damage_reporting.status.chunk[0]"):
        row = input_row(cells, path)
        assert cells["E" + row]["value"] == "回 App 重新生成"
        assert cells["I" + row]["formula"] == f"IF(C{row}<>D{row},1,0)"
    row = input_row(cells, "characters[0].stats.hp")
    assert cells["E" + row]["value"] == "数值公式重算"
    assert cells["I" + row]["formula"] == "0"


def test_empty_projection_without_limited_source_keeps_ordinary_atk_editable(benefit_db):
    plan = benefit_plan(benefit_db)
    plan["limited_hit_benefit_sources"] = []
    plan["action_event_overrides"] = [{"action_id": "cast", "landed": False, "order": 20, **EVIDENCE}]
    out = run_plan_calc(plan, benefit_db)
    cells = workbook_sheets(build_formula_workbook(out))["参数"]
    row = input_row(cells, "characters[0].stats.atk")
    assert cells["E" + row]["value"] == "数值公式重算"
    assert cells["I" + row]["formula"] == "0"


@pytest.mark.parametrize("part", ["proof", "contacts", "result", "receipt", "report", "native", "marker_removed"])
def test_formula_rejects_stale_or_partially_erased_zero_evidence(calculated, part):
    out = copy.deepcopy(calculated)
    if part == "proof":
        out["result"]["damage_reporting"]["planned_contacts"] += 1
    elif part == "contacts":
        out["result"]["damage_reporting"]["contacts"][0]["landed"] = True
    elif part == "result":
        out["result"]["summary"]["dps"] = None
    elif part == "receipt":
        out["result_receipt"].pop("damage_reporting")
    elif part == "report":
        out["report"].pop("damage_reporting")
    elif part == "native":
        out["plan"]["action_event_overrides"][0]["landed"] = True
    else:
        out["result"].pop("damage_reporting")
        out["result"].pop("mechanics")
        out["result_receipt"].pop("damage_reporting")
    out["result_receipt"]["result_fingerprint"] = fingerprint(out["result"])
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(out)


def test_csv_and_receiptless_bare_exports_reject_before_writing(calculated, tmp_path):
    result, receipt = calculated["result"], calculated["result_receipt"]
    with pytest.raises(ValueError, match="CSV"):
        result_bytes(result, receipt, "csv")
    path = tmp_path / "not-created" / "result.csv"
    with pytest.raises(ValueError, match="CSV"):
        export_result(result, "csv", path, receipt=receipt)
    assert not path.parent.exists()
    with pytest.raises(ValueError):
        export_result(result, "xlsx", path)
    erased = copy.deepcopy(result)
    erased.pop("mechanics")
    erased.pop("damage_reporting")
    with pytest.raises(ValueError):
        export_result(erased, "json", path)
    assert not path.exists()


def test_old_nonempty_export_unchanged(tmp_path):
    old = envelope()
    assert damage_reporting_rows(old["result"]) == []
    assert "zero_damage_projection" not in formula_workbook_support(old)
    assert "伤害报告证明" not in sheets(build_formula_workbook(old))
    export_result(old["result"], "csv", tmp_path / "old.csv")
    assert (tmp_path / "old.csv").exists()
