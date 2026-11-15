"""Independent particle provenance and arithmetic consistency in offline exports."""
from __future__ import annotations

import copy
import sqlite3

import pytest
from test_action_resource_export import NS, sheets
from test_energy_generation_sources import confirm_energy, energy_plan
from test_mechanics_execution import EVIDENCE, rule
from test_pipeline import fake_repo  # noqa: F401
from test_runtime_energy_dispatch import trigger_db, trigger_repo  # noqa: F401

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook

# ruff: noqa: F811


@pytest.fixture
def actual(trigger_db):
    return run_plan_calc(confirm_energy(energy_plan(trigger_db), trigger_db), trigger_db)


@pytest.mark.parametrize("field,value", [
    ("before", 77), ("raw", 100), ("gain", 0), ("after", 100),
    ("overflow", 10), ("energy_recharge", 2), ("on_field", False), ("character", "ghost"),
])
def test_review_credit_numeric_contradiction_rejected_after_outer_rehash(actual, field, value):
    credit = next(r for r in actual["result"]["rotation_check"]["event_log"] if r["type"] == "energy_credit")
    credit["energy"][0][field] = value
    actual["result_receipt"]["result_fingerprint"] = fingerprint(actual["result"])
    # This changes one arithmetic fact only; it is not a complete self-consistent forgery.
    with pytest.raises(ValueError):
        result_bytes(actual["result"], actual["result_receipt"], "xlsx")
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(actual)


@pytest.mark.parametrize("mutation", ["missing_gain", "duplicate_row", "empty", "remove_field"])
def test_review_credit_requires_exact_character_coverage(actual, mutation):
    credit = next(r for r in actual["result"]["rotation_check"]["event_log"] if r["type"] == "energy_credit")
    if mutation == "duplicate_row":
        credit["energy"].append(copy.deepcopy(credit["energy"][0]))
    elif mutation == "empty":
        credit["energy"].clear()
    elif mutation == "missing_gain":
        credit["energy"][0].pop("gain")
    else:
        credit.pop("energy")
    actual["result_receipt"]["result_fingerprint"] = fingerprint(actual["result"])
    with pytest.raises(ValueError):
        result_bytes(actual["result"], actual["result_receipt"], "xlsx")


@pytest.mark.parametrize("where", ["report", "result", "receipt", "native", "rules", "ownership", "summary", "parent"])
def test_review_single_layer_changes_cannot_export(actual, where):
    if where == "report":
        actual["report"]["energy_generation_compilations"][0]["whole_source_status"] = "ready"
    elif where == "result":
        actual["result"]["energy_generation_compilations"][0]["trigger_certificate"]["owner_presence"] = "any"
    elif where == "receipt":
        actual["result_receipt"]["energy_generation"]["execution_rotation_model_fingerprint"] = "a" * 64
    elif where == "native":
        actual["plan"]["energy_generation_sources"][0]["scenario"]["hitlag_policy"] = "unknown"
    elif where == "rules":
        actual["report"]["mechanics_model"]["rules"][0]["resources"][0]["count"] = 20
    elif where == "ownership":
        actual["report"]["mechanics_model"]["resource_ownership"].clear()
    elif where == "summary":
        actual["result"]["energy_generation_summary"]["credited_batches"] = 0
    else:
        actual["result_receipt"].pop("action_events")
    actual["result_receipt"]["result_fingerprint"] = fingerprint(actual["result"])
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(actual)


def test_review_offline_books_preserve_independent_damage_and_energy(actual, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("offline export reopened DB"))
    multiplier = 3.4560000896453857  # Raw C0/10 scalar, independent of the compiler result.
    expected = 100 * multiplier * .5 * .9
    result = actual["result"]
    assert [h["expected"] for h in result["hits"]] == pytest.approx([expected] * 3)
    credits = [r for r in result["rotation_check"]["event_log"] if r["type"] == "energy_credit"]
    assert [r["energy"][0]["after"] for r in credits] == [9, 18, 27]
    for content in (build_formula_workbook(actual), result_bytes(result, actual["result_receipt"], "xlsx")):
        book = sheets(content)
        assert "能量流水" in book and "产球来源" in book
        assert all(cell.get("t") != "e" for page in book.values() for cell in page.findall(".//s:c", NS))
    book = sheets(build_formula_workbook(actual))
    cells = {cell.get("r"): cell for cell in book["逐事件"].findall(".//s:c", NS)}
    for row in (5, 6, 7):
        assert cells[f"V{row}"].find("s:f", NS) is not None
        assert float(cells[f"V{row}"].find("s:v", NS).text) == pytest.approx(expected)


def test_review_dynamic_er_and_unrelated_split_manual_pickups_remain_exportable(trigger_db):
    from test_plan_mechanics import native, review
    plan = energy_plan(trigger_db)
    plan["effect_bindings"] = copy.deepcopy(native()["effect_bindings"])
    er = rule("boost", "benefit.energy_recharge.all", 1, duration=.7, source_id="binding:part",
              trigger={"event": "action_start", "phase": "post", "action_ids": ["cast0"]})
    plan["mechanics_model"]["rules"].append(er)
    plan["mechanics_model"]["initial_state"]["rules"].append({"rule_id": er["id"], "cooldown_remaining": 0, "instances": []})
    plan["rotation_model"]["events"] = [
        {"id": "enemy", "type": "energy_spawn", "t": .7, "order": 40, "count": 3, "element": "anemo",
         "particle_type": "particle", "source_kind": "enemy", "repeat": {"mode": "once"}, **EVIDENCE},
        {"id": "pickup-one", "type": "energy_credit", "credit_at": .8, "order": 41, "spawn_id": "enemy",
         "count": 1, "repeat": {"mode": "once"}, **EVIDENCE},
        {"id": "pickup-two", "type": "energy_credit", "credit_at": .9, "order": 42, "spawn_id": "enemy",
         "count": 2, "repeat": {"mode": "once"}, **EVIDENCE},
    ]
    from genshin_app.equipment import compose_equipment_plan
    review(plan, trigger_db)
    preview = compose_equipment_plan(plan, trigger_db)
    plan["action_event_profiles"][0].update(preview["action_event_compilations"][0]["required_confirmation"])
    confirm_energy(plan, trigger_db)
    report = compose_equipment_plan(plan, trigger_db)
    assert report["state"] == "ready", report["missing_inputs"]
    data = run_plan_calc(plan, trigger_db)
    credits = [r for r in data["result"]["rotation_check"]["event_log"] if r["type"] == "energy_credit"]
    assert [r["energy"][0]["gain"] for r in credits] == [18, 3, 6, 9, 9]
    assert [r["energy"][0]["after"] for r in credits] == [18, 21, 27, 36, 45]
    # Three source batches; the separate manual split pickup is not attributed to this source.
    assert data["result"]["energy_generation_summary"]["credited_particles"] == 9
    assert result_bytes(data["result"], data["result_receipt"], "xlsx")
    assert build_formula_workbook(data)


@pytest.mark.parametrize("mutation", ["local_gain", "linked_before", "final_balance", "duplicate_receiver", "false_number"])
def test_review_arithmetic_still_rejects_when_both_envelope_hashes_recomputed(actual, mutation):
    from genshin_app.equipment.energy_generation_receipt import (
        energy_generation_resource_ledger_fingerprint,
    )
    result = actual["result"]
    credit = next(r for r in result["rotation_check"]["event_log"] if r["type"] == "energy_credit")
    value = credit["energy"][0]
    if mutation == "local_gain":
        value["gain"] = 0
    elif mutation == "linked_before":
        value.update(before=5, after=14)  # Internally local gain9, but contradicts known initial0.
    elif mutation == "final_balance":
        result["rotation_check"]["characters"][0]["final_energy"] = 100
    elif mutation == "duplicate_receiver":
        credit["energy"].append(copy.deepcopy(value))
    else:
        value["before"] = False
    ledger = energy_generation_resource_ledger_fingerprint(result)
    actual["result_receipt"]["energy_generation"]["resource_ledger_fingerprint"] = ledger
    actual["report"]["energy_generation_resource_ledger_fingerprint"] = ledger
    actual["result_receipt"]["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        result_bytes(result, actual["result_receipt"], "xlsx")
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(actual)
