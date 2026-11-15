"""Independent EC14 offline proof tampering and immutable action workbook inputs."""
from __future__ import annotations

import copy
import io
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook
from tests.test_automatic_energy_review import (
    accept_resource_sources,
    audits,
    automatic_native,
)
from tests.test_freeze_export_review import workbook_sheets
from tests.test_mechanics_execution import EVIDENCE
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)

# ruff: noqa: F811
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@pytest.fixture
def automatic_result(resource_db):
    plan = automatic_native()
    plan["rotation_model"]["characters"][0]["energy_capacity"] = 90
    plan["rotation_model"]["events"] = [{"id": "refund", "type": "energy_direct",
        "t": 0, "order": 1, "amounts": {"a": 30}, "repeat": {"mode": "once"}, **EVIDENCE}]
    return run_plan_calc(accept_resource_sources(plan, resource_db), resource_db)


def worksheet_xml(content, name):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        relation_id = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        sheet = next(row for row in workbook.findall("s:sheets/s:sheet", NS) if row.get("name") == name)
        target = next(row.get("Target") for row in relations if row.get("Id") == sheet.get(relation_id))
        return ET.fromstring(archive.read(target.lstrip("/") if target.startswith("/") else "xl/" + target))


def test_review_offline_outputs_keep_historical_audit_and_do_not_query_database(automatic_result, monkeypatch):
    original = copy.deepcopy(automatic_result)
    rotation = automatic_result["result"]["rotation_check"]
    row = audits(rotation)[0]
    assert (row["before"], row["after"], row["amount"]) == (60, 0, 60)
    assert rotation["characters"][0]["final_energy"] == 30
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("offline export queried SQLite"))
    formula = build_formula_workbook(automatic_result)
    for kind in ("json", "csv", "xlsx"):
        assert result_bytes(automatic_result["result"], automatic_result["result_receipt"], kind)
    book = workbook_sheets(formula)
    source_page = book["动作资源来源"]
    assert all(not cell["formula"] for cell in source_page.values())
    page_text = " ".join(str(cell["value"]) for cell in source_page.values())
    assert "action_start" in page_text and "cast" in page_text
    assert automatic_result == original


def test_review_automatic_links_freeze_real_action_and_initial_state_not_fictional_events(automatic_result):
    content = build_formula_workbook(automatic_result)
    sheet = worksheet_xml(content, "参数")
    by_name = {row.find("s:c/s:is/s:t", NS).text: row
               for row in sheet.findall("s:sheetData/s:row", NS)
               if row.find("s:c/s:is/s:t", NS) is not None}
    for path in ("action_resource_sources[0].energy_delivery", "rotation_model.actions[0].start",
                 "rotation_model.actions[0].order", "rotation_model.actions[0].occupation_end",
                 "rotation_model.actions[0].repeat.mode", "rotation_model.characters[0].initial_energy",
                 "rotation_model.events[0].amounts.a"):
        cells = by_name[path].findall("s:c", NS)
        assert cells[2].get("s") == "5", path
        assert "<>" in cells[8].find("s:f", NS).text, path
    assert not any("@energy_spend:" in name for name in by_name)


@pytest.mark.parametrize("part", ["native_mode", "native_action_time", "source_links", "compiled_cost",
                                  "resolved_marker", "summary", "receipt_only"])
def test_review_full_workbook_rejects_each_single_stale_layer(automatic_result, part):
    data = copy.deepcopy(automatic_result)
    if part == "native_mode":
        data["plan"]["action_resource_sources"][0]["energy_delivery"] = "existing_events"
    elif part == "native_action_time":
        data["plan"]["rotation_model"]["actions"][0]["start"] += .01
    elif part == "source_links":
        data["report"]["action_resource_compilations"][0]["energy_delivery"]["links"][0]["action_id"] = "other"
    elif part == "compiled_cost":
        data["report"]["compiled_rotation_model"]["abilities"][0]["energy"]["amount"] = 59
    elif part == "resolved_marker":
        data["result"]["rotation_check"]["resolved_rotation_model"]["abilities"][0]["energy"].pop("delivery")
    elif part == "summary":
        data["result"]["rotation_check"]["automatic_energy_delivery"]["committed_actions"] += 1
    else:
        data["result_receipt"]["action_resources"].pop("automatic_energy_delivery")
    with pytest.raises((FormulaWorkbookError, ValueError)):
        build_formula_workbook(data)


@pytest.mark.parametrize("part", ["after", "after_and_parent", "amount", "phase", "parent", "extra_order", "move_before_parent",
                                  "delete", "duplicate"])
def test_review_outer_and_audit_rehash_cannot_repair_semantically_false_result_only_log(automatic_result, part):
    data = copy.deepcopy(automatic_result)
    result, receipt = data["result"], data["result_receipt"]
    rotation = result["rotation_check"]
    row = audits(rotation)[0]
    if part in ("after", "after_and_parent"):
        row["after"] = 30  # Later refund is not the admission after-state.
        if part == "after_and_parent":
            parent = next(r for r in rotation["event_log"] if r["run_id"] == row["parent_run_id"])
            parent["energy_admission"]["after"] = 30
    elif part == "amount":
        row["amount"] = 59
    elif part == "phase":
        row["phase"] = "action_post"
    elif part == "parent":
        row["parent_run_id"] = "0:nonexistent"
    elif part == "extra_order":
        row["order"] = 1
    elif part == "move_before_parent":
        rotation["event_log"].remove(row)
        rotation["event_log"].insert(0, row)
    elif part == "delete":
        rotation["event_log"].remove(row)
    else:
        rotation["event_log"].append(copy.deepcopy(row))
    audit_fp = fingerprint(audits(rotation))
    rotation["automatic_energy_delivery"]["receipt_fingerprint"] = audit_fp
    proof = receipt["action_resources"]["automatic_energy_delivery"]
    proof["audit_fingerprint"] = audit_fp
    proof["summary_fingerprint"] = fingerprint(rotation["automatic_energy_delivery"])
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(PlanError):
        result_bytes(result, receipt, "json")


def test_review_removing_all_optional_proof_headers_cannot_hide_actual_automatic_model(automatic_result):
    data = copy.deepcopy(automatic_result)
    result, receipt = data["result"], data["result_receipt"]
    result.pop("action_resource_compilations")
    receipt["action_resources"].pop("automatic_energy_delivery")
    result["rotation_check"].pop("automatic_energy_delivery")
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(PlanError):
        result_bytes(result, receipt, "json")


def test_review_cycle_proof_cannot_hide_an_already_checked_parent_as_future_unreported(resource_db):
    plan = automatic_native()
    rotation = plan["rotation_model"]
    rotation["period"] = plan["manual_template"]["loop_duration"] = 20
    rotation["max_cycles"] = 5
    rotation["actions"][0]["repeat"] = {"mode": "each_cycle"}
    rotation["events"] = [{"id": "refund", "type": "energy_direct", "t": 1,
        "order": 1, "amounts": {"a": 60}, "repeat": {"mode": "each_cycle"}, **EVIDENCE}]
    data = run_plan_calc(accept_resource_sources(plan, resource_db), resource_db)
    result, receipt = data["result"], data["result_receipt"]
    check = result["rotation_check"]
    assert check["energy_check"]["proven_cycle"] == 2
    assert len(audits(check)) == 3
    # Removing a whole balanced energy cycle leaves scalar final energy intact;
    # the known checked interval must still make this omission detectable.
    check["event_log"] = [r for r in check["event_log"] if r["source_cycle"] != 0]
    summary = check["automatic_energy_delivery"]
    summary.update(planned_actions=2, committed_actions=2, receipt_count=2, unreported_actions=3)
    rows = audits(check)
    summary["receipt_fingerprint"] = fingerprint(rows)
    import json
    summary["receipt_bytes"] = len(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                                             separators=(",", ":"), allow_nan=False).encode("utf8"))
    proof = receipt["action_resources"]["automatic_energy_delivery"]
    proof["audit_fingerprint"] = fingerprint(rows)
    proof["summary_fingerprint"] = fingerprint(summary)
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(PlanError):
        result_bytes(result, receipt, "json")
