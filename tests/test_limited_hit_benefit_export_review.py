"""Independent offline EC18 corruption probes; no database at export time."""
from __future__ import annotations

import copy
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest
from test_limited_hit_benefit_sources import benefit_plan, confirm_benefit
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_limited_hit_benefit import benefit_db, benefit_repo  # noqa: F401

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.limited_hit_benefit_receipt import (
    limited_hit_benefit_ledger_fingerprint,
    validate_limited_hit_benefit_receipt,
    validate_limited_hit_benefit_result_receipt,
)
from genshin_app.equipment.service import result_bytes

# ruff: noqa: F811


@pytest.fixture
def calculated(benefit_db):
    return run_plan_calc(confirm_benefit(benefit_plan(benefit_db), benefit_db), benefit_db)


def resign(out, *, ledger=False):
    out["result_receipt"]["result_fingerprint"] = fingerprint(out["result"])
    if ledger:
        out["result_receipt"]["limited_hit_benefit"]["ledger_fingerprint"] = (
            limited_hit_benefit_ledger_fingerprint(out["result"]))


def own(out, kind):
    return next(row for row in out["result"]["mechanics_trace"]
                if row["type"] == kind and row.get("rule_id", "").startswith("auto.hit-benefit."))


@pytest.mark.parametrize("format_name", ["json", "csv", "xlsx"])
def test_review_result_exports_are_offline_and_preserve_native(calculated, monkeypatch, format_name):
    import sqlite3

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline export must not reopen a source database")

    before = copy.deepcopy(calculated)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    assert result_bytes(calculated["result"], calculated["result_receipt"], format_name)
    validate_limited_hit_benefit_receipt(calculated["plan"], calculated["result"],
        calculated["report"], calculated["result_receipt"])
    assert calculated == before


@pytest.mark.parametrize("field,value", [
    ("before", 8), ("after", 7), ("pool", "missing"), ("consumed", False),
    ("reported_damage", False), ("t", .200001), ("instance_id", "forged"),
])
def test_review_quota_transition_survives_outer_and_ledger_rehash(calculated, field, value):
    own(calculated, "quota")[field] = value
    resign(calculated, ledger=True)
    with pytest.raises(ValueError):
        validate_limited_hit_benefit_result_receipt(calculated["result"], calculated["result_receipt"])


@pytest.mark.parametrize("field,value", [
    ("base_value", 2500), ("source_value", 2500), ("value", 0),
    ("character", "b"), ("stat", "hp"), ("sample_t", 0), ("t", .1),
    ("dependencies", [{"stat": "atk", "zone": "stat_add", "value": 500}]),
])
def test_review_current_provider_sample_survives_rehash(calculated, field, value):
    calculated["result"]["sampling_trace"][0][field] = value
    resign(calculated, ledger=True)
    with pytest.raises(ValueError):
        validate_limited_hit_benefit_result_receipt(calculated["result"], calculated["result_receipt"])


@pytest.mark.parametrize("field,value", [
    ("quota", {"a": 8}), ("beneficiaries", ["a", "a"]),
    ("expires_at", [15]), ("t", 0), ("cycle", 1),
])
def test_review_activation_is_bound_to_actual_delayed_parent(calculated, field, value):
    own(calculated, "activation")[field] = value
    resign(calculated, ledger=True)
    with pytest.raises(ValueError):
        validate_limited_hit_benefit_result_receipt(calculated["result"], calculated["result_receipt"])


@pytest.mark.parametrize("remaining", ["hit_effect_ref", "action_event"])
def test_review_deleting_top_level_proofs_does_not_hide_reserved_delivery(calculated, remaining):
    result = calculated["result"]
    result.pop("limited_hit_benefit_compilations")
    result.pop("limited_hit_benefit_summary")
    calculated["result_receipt"].pop("limited_hit_benefit")
    result["mechanics_trace"] = [row for row in result["mechanics_trace"]
        if not row.get("rule_id", "").startswith("auto.hit-benefit.")]
    result["sampling_trace"] = []
    if remaining == "action_event":
        result["hits"][0]["matched_effect_refs"] = []
    resign(calculated)
    with pytest.raises(ValueError):
        result_bytes(result, calculated["result_receipt"], "json")


def test_review_ledger_budget_rejects_before_export_without_truncation(calculated, monkeypatch):
    from genshin_app.equipment import limited_hit_benefit_receipt as module
    monkeypatch.setattr(module, "MAX_LEDGER_BYTES", 1)
    with pytest.raises(ValueError):
        result_bytes(calculated["result"], calculated["result_receipt"], "json")


def test_review_formula_locks_only_provider_panel_and_potential_attack_contributions(benefit_db):
    from test_action_events_review import attach_mechanics_rules, review_manual_sources
    from test_action_resource_export import NS, sheets
    from test_freeze_export_review import workbook_sheets
    from test_limited_hit_benefit_app_review import add_recipient, normal_hits
    from test_mechanics_execution import rule
    from test_reaction_sources_review import evaluate_numeric_cell

    from genshin_app.formula_workbook import build_formula_workbook

    plan = benefit_plan(benefit_db)
    add_recipient(plan)
    normal_hits(plan, "b", "bn", [2.2])
    for cid, value in (("a", 500), ("b", 900)):
        attach_mechanics_rules(plan, rule("panel-" + cid, "benefit.stat_add.atk", value,
            duration=5, beneficiaries={"mode": "characters", "characters": [cid], "scope": "locked"}))
    review_manual_sources(plan, benefit_db)
    out = run_plan_calc(confirm_benefit(plan, benefit_db), benefit_db)
    content = build_formula_workbook(out)
    parsed, xml = workbook_sheets(content), sheets(content)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        styles = ET.fromstring(archive.read("xl/styles.xml")).findall("s:cellXfs/s:xf", NS)

    def input_cell(path):
        row = next(a[1:] for a, c in parsed["参数"].items() if a.startswith("A") and c["value"] == path)
        cell = xml["参数"].find(f".//s:c[@r='C{row}']", NS)
        protection = styles[int(cell.get("s", "0"))].find("s:protection", NS)
        locked = protection is None or protection.get("locked", "1") != "0"
        return row, locked

    for path in ("characters[0].stats.atk", "characters[0].stats.base_atk",
                 "mechanics_model.rules[0].effects[0].value"):
        row, locked = input_cell(path)
        assert locked and parsed["参数"]["E" + row]["value"] == "回 App 重新生成"
        assert parsed["参数"]["I" + row]["formula"] != "0"
    for path in ("characters[1].stats.atk", "characters[1].stats.base_atk",
                 "characters[0].stats.hp", "mechanics_model.rules[1].effects[0].value"):
        row, locked = input_cell(path)
        assert not locked and parsed["参数"]["E" + row]["value"] == "数值公式重算"
    assert evaluate_numeric_cell(parsed, "逐事件", "J6") == pytest.approx(2054.5199513435365)
    assert evaluate_numeric_cell(parsed, "逐事件", "V6") == pytest.approx(1374.533978104591425)
    row, _ = input_cell("characters[1].stats.atk")
    assert evaluate_numeric_cell(parsed, "逐事件", "V6", {("参数", "C" + row): 200}) == pytest.approx(1419.533978104591425)
    assert xml["有限次数收益"].find("s:sheetProtection", NS) is not None
    assert not xml["有限次数收益"].findall(".//s:f", NS)
