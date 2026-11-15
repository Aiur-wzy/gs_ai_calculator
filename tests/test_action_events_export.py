"""EC09 product export integration: fixed dispatch inputs, literal sources and summaries."""
from __future__ import annotations

import copy
import io
import json
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.cli import _calculation_output_summary
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.service import result_bytes
from genshin_app.export import action_event_rows, export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_action_events import action_event_plan, confirm_action_events
from tests.test_action_events import (
    action_events_db as action_events_db,  # noqa: PLC0414 -- pytest fixture
)
from tests.test_pipeline import (
    fake_repo as fake_repo,  # noqa: PLC0414 -- fixture dependency
)
from tests.test_pipeline import (
    stub_fetcher as stub_fetcher,  # noqa: PLC0414 -- fixture dependency
)

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def sheets(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        return {item.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml")) for i, item in enumerate(names, 1)}


def test_dispatch_export_actual_source_no_late_query_and_fixed_guards(action_events_db, monkeypatch, tmp_path):
    plan = action_event_plan(action_events_db)
    confirm_action_events(plan, action_events_db)
    output = run_plan_calc(plan, action_events_db)
    original = copy.deepcopy(output)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("export must not reopen database"))
    formula = sheets(build_formula_workbook(output))
    snapshot = sheets(result_bytes(output["result"], output["result_receipt"], "xlsx"))
    generic = sheets(export_result(
        output["result"], "xlsx", tmp_path / "result.xlsx",
        receipt=output["result_receipt"],
    ).read_bytes())
    for book in (formula, snapshot, generic):
        page = book["动作派发来源"]
        assert page.find("s:sheetProtection", NS) is not None
        assert not page.findall(".//s:f", NS)
        assert all(text in "".join(page.itertext()) for text in ("校准", "partial", "not_generated"))
        assert "auto.action." in "".join(page.itertext())
    fixed = [row for row in formula["参数"].findall("s:sheetData/s:row", NS)
             if "action_event_profiles[" in "".join(row.itertext())]
    assert fixed
    for row in fixed:
        cells = row.findall("s:c", NS)
        assert cells[2].get("s") == "5"
        assert "<>" in cells[8].find("s:f", NS).text
    support = formula_workbook_support(output)
    assert support["editable"]["action_event_profiles_bindings_overrides"] == "regenerate_required"
    assert support["editable"]["action_event_provenance"] == "readonly_snapshot"
    assert support["action_events"]["state_machine_replay"] is False
    assert output == original


def test_dispatch_summary_distinguishes_planned_generated_and_missed(action_events_db):
    plan = action_event_plan(action_events_db)
    confirm_action_events(plan, action_events_db)
    second = plan["rotation_model"]["actions"][1]["id"]
    plan["action_event_overrides"] = [{"action_id": second, "landed": False, "status": "assumed", "source": "工程第二次落空"}]
    output = run_plan_calc(plan, action_events_db)
    summary = _calculation_output_summary(output)["action_events"]
    assert summary["generated_hit_count"] == 3
    assert summary["reported_hit_count"] == 2
    assert summary["declared_missed_count"] == 1
    assert summary["skill_coverage"] == "partial"
    assert len(json.dumps(summary, ensure_ascii=False).encode("utf8")) < 1500
    mappings = [r for r in action_event_rows(output["result"]) if len(r) == 8 and r[0] == second]
    assert len(mappings) == 1 and mappings[0][5:7] == ["落空", "否"]


def test_long_shared_dispatch_evidence_is_literal_and_not_truncated():
    text = '来源💧=HYPERLINK("x")' * 2500
    record = {"profile_id": "p", "prototype_hit_id": "h", "status": "ready", "evidence": text, "generated": []}
    rows = action_event_rows({"action_event_compilations": [record], "hits": []})
    encoded = "".join(row[1] for row in rows if row and row[0] in ("共享档案与来源", "续"))
    assert json.loads(encoded)["evidence"] == text
    assert all(len(row[1].encode("utf-16-le")) // 2 <= 30000 for row in rows if row and row[0] in ("共享档案与来源", "续"))


def test_empty_dispatch_does_not_add_export_pages():
    assert action_event_rows({"hits": []}) == []
