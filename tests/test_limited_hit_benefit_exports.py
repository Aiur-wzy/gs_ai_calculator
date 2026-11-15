"""Author integration: offline evidence and formula guards for limited-hit sources."""
from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from test_action_events_review import attach_mechanics_rules, review_manual_sources
from test_action_resource_export import NS, sheets
from test_freeze_export_review import workbook_sheets
from test_limited_hit_benefit_sources import benefit_plan, confirm_benefit
from test_mechanics_execution import rule
from test_pipeline import fake_repo  # noqa: F401
from test_reaction_sources_review import evaluate_numeric_cell
from test_runtime_limited_hit_benefit import benefit_db, benefit_repo  # noqa: F401

from genshin_app.cli import _calculation_output_summary
from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result, limited_hit_benefit_rows
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)

# ruff: noqa: F811


@pytest.fixture
def calculated(benefit_db):
    return run_plan_calc(confirm_benefit(benefit_plan(benefit_db), benefit_db), benefit_db)


def input_row(cells, path):
    return next(address[1:] for address, cell in cells.items() if address.startswith("A") and cell["value"] == path)


def test_books_export_offline_complete_source_and_actual_history(calculated, monkeypatch, tmp_path):
    before = copy.deepcopy(calculated)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("export opened DB"))
    monkeypatch.setattr(sqlite3.dbapi2, "connect", lambda *a, **k: pytest.fail("export opened DB"))
    formula = build_formula_workbook(calculated)
    snapshot = result_bytes(calculated["result"], calculated["result_receipt"], "xlsx")
    target = tmp_path / "direct.xlsx"
    export_result(
        calculated["result"], "xlsx", target,
        receipt=calculated["result_receipt"],
    )
    for content in (formula, snapshot, target.read_bytes()):
        pages = sheets(content)
        page = pages["有限次数收益"]
        assert page.find("s:sheetProtection", NS) is not None
        assert not page.findall(".//s:f", NS)
        text = "".join(page.itertext())
        assert all(value in text for value in ("次数检查与消费", "攻击力取样", "partial", "reference_adapted_not_decoded", "source_value", "2000", "0.8218079805374146"))
        assert not any(cell.get("t") == "e" for sheet in pages.values() for cell in sheet.findall(".//s:c", NS))
    cells = workbook_sheets(formula)
    assert evaluate_numeric_cell(cells, "逐事件", "J5") == pytest.approx(1643.6159610748292)
    assert cells["逐事件"]["J5"]["value"] == pytest.approx(1643.6159610748292)
    assert evaluate_numeric_cell(cells, "逐事件", "V5") == pytest.approx(calculated["result"]["hits"][0]["expected"])
    assert cells["逐事件"]["V5"]["value"] == pytest.approx(calculated["result"]["hits"][0]["expected"])
    assert calculated == before


def test_provider_atk_and_source_values_fixed_but_unrelated_stats_remain_editable(calculated):
    support = formula_workbook_support(calculated)
    assert support["editable"]["limited_hit_provider_attack"] == "regenerate_required"
    assert support["editable"]["character_stats"] == "supported_except_limited_hit_provider_atk"
    assert support["editable"]["limited_hit_benefit_provenance_and_ledger"] == "readonly_snapshot"
    pages = workbook_sheets(build_formula_workbook(calculated))
    cells = pages["参数"]
    for path in ("characters[0].stats.atk", "characters[0].stats.base_atk",
                 "mechanics_model.rules[0].effects[0].value.factor",
                 "limited_hit_benefit_sources[0].calibration.activation_offset_seconds",
                 "limited_hit_benefit_compilations[0].benefit_certificate.capacity",
                 "limited_hit_benefit_compilations[0].initial_rules[0].instances"):
        row = input_row(cells, path)
        assert cells["E" + row]["value"] == "回 App 重新生成"
        assert cells["I" + row]["formula"] == f"IF(C{row}<>D{row},1,0)"
    for path in ("characters[0].stats.hp", "characters[0].stats.em"):
        row = input_row(cells, path)
        assert cells["E" + row]["value"] == "数值公式重算"
        assert cells["I" + row]["formula"] == "0"
    row = input_row(cells, "characters[0].stats.atk")
    # Inspect formula dependency only. This edit invalidates the source sampling
    # contract; it does not constitute a valid new workbook calculation.
    assert evaluate_numeric_cell(pages, "逐事件", "J5", {("参数", "C" + row): 2500}) == pytest.approx(2054.5199513435365)


def test_ordinary_provider_atk_contribution_is_fixed_as_part_of_sampling(benefit_db):
    plan = benefit_plan(benefit_db)
    attach_mechanics_rules(plan, rule("attack", benefit="benefit.stat_add.atk", value=500))
    review_manual_sources(plan, benefit_db)
    preview = compose_equipment_plan(plan, benefit_db)
    plan["action_event_profiles"][0].update(preview["action_event_compilations"][0]["required_confirmation"])
    plan = confirm_benefit(plan, benefit_db)
    report = compose_equipment_plan(plan, benefit_db)
    assert report["state"] == "ready", report["missing_inputs"]
    out = run_plan_calc(plan, benefit_db)
    cells = workbook_sheets(build_formula_workbook(out))
    row = input_row(cells["参数"], "mechanics_model.rules[0].effects[0].value")
    assert cells["参数"]["E" + row]["value"] == "回 App 重新生成"
    assert cells["参数"]["I" + row]["formula"] != "0"
    assert evaluate_numeric_cell(cells, "逐事件", "J5") == pytest.approx(2054.5199513435365)


@pytest.mark.parametrize("field", ["source", "calibration", "activation", "quota", "sample", "receipt", "report"])
def test_changed_source_or_ledger_cannot_use_formula_export(calculated, field):
    out = {k: copy.deepcopy(v) for k, v in calculated.items()}
    if field == "source":
        out["result"]["limited_hit_benefit_compilations"][0]["benefit_certificate"]["capacity"] = 8
    elif field == "calibration":
        out["plan"]["limited_hit_benefit_sources"][0]["calibration"]["activation_offset_seconds"] = .11
    elif field == "activation":
        next(row for row in out["result"]["mechanics_trace"] if row["type"] == "activation")["t"] += .01
    elif field == "quota":
        next(row for row in out["result"]["mechanics_trace"] if row["type"] == "quota")["after"] += 1
    elif field == "sample":
        out["result"]["sampling_trace"][0]["source_value"] += 1
    elif field == "receipt":
        out["result_receipt"].pop("limited_hit_benefit")
    else:
        out["report"]["limited_hit_benefit_compilations"] = []
    out["result_receipt"]["result_fingerprint"] = fingerprint(out["result"])
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(out)


def test_full_unicode_record_chunking_and_compact_cli(calculated):
    record = copy.deepcopy(calculated["result"]["limited_hit_benefit_compilations"][0])
    record["description"] = '=HYPERLINK("https://example.invalid","原文仅文本")' + "冰翎❄💧" * 18000
    rows = limited_hit_benefit_rows({"limited_hit_benefit_compilations": [record]})
    parts = [row[1] for row in rows if row and row[0] in ("完整来源、确认、校准与执行证书", "续")]
    assert json.loads("".join(parts)) == record
    assert all(len(value.encode("utf-16-le")) // 2 <= 30000 for value in parts)
    summary = _calculation_output_summary(calculated)["limited_hit_benefit"]
    assert summary["activations"] == summary["consumed_hits"] == summary["sampled_hits"] == 1
    assert summary["whole_source_status"] == "partial"
    assert len(json.dumps(summary, ensure_ascii=False).encode("utf8")) < 1024
    assert "source_bundle" not in summary and "rules" not in summary
