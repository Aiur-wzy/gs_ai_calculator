"""Independent bounds and execution checks for additive external damage."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.equipment import (
    compose_equipment_plan,
    enable_mechanics,
    new_plan,
    run_plan_calc,
)
from genshin_app.equipment import external_damage as external
from genshin_app.equipment import external_damage_schema as declaration
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.equipment.service import result_bytes
from tests.test_calculation_delivery import delivery_plan
from tests.test_mechanics_execution import fixture


def item(**change):
    value = {"id": "review.extra", "label": "独立工程补充", "character": "a", "skill": "独立加项",
             "t": .5, "expected": 7, "action_id": None, "coverage_key": "review.coverage",
             "event_id": "review.event", "reason": "有限工程缺项，不代表游戏事实",
             "calculation_note": "独立常数7", "non_overlap_note": "与原h不同的独立事件",
             "source_refs": ["fixture://independent-seven"], "accepted_fingerprint": None}
    value.update(change)
    return value


def plan(*, mechanical=False):
    if mechanical:
        template, rotation, model = fixture((.2,))
        template["characters"][0]["stats"]["energy_recharge"] = 1
        rotation["actions"][0]["repeat"] = {"mode": "once"}
        result = enable_mechanics(new_plan(template))
        result.update(rotation_model=rotation, mechanics_model=model)
    else:
        result = delivery_plan()
    result["external_damage_supplements"] = [item(action_id="cast" if mechanical else None)]
    return result


def adopt(value, db):
    report = compose_equipment_plan(value, db)
    for claim, row in zip(value["external_damage_supplements"], report["external_damage_review"]["items"], strict=True):
        assert row["required_confirmation"] is not None, report["missing_inputs"]
        claim.update(row["required_confirmation"])
    return value


def test_review_parent_execution_and_zero_extra_leave_engine_queue_unchanged(tmp_path):
    db = tmp_path / "none.db"
    current = plan(mechanical=True)
    baseline = copy.deepcopy(current)
    baseline.pop("external_damage_supplements")
    a = run_plan_calc(baseline, db)
    b = run_plan_calc(adopt(current, db), db)
    assert b["result"]["summary"]["total_damage"] == 45
    assert b["result"]["calculation_totals"]["total_damage"] == 52
    assert b["result"]["rotation_check"] == a["result"]["rotation_check"]
    assert b["result"]["mechanics_trace"] == a["result"]["mechanics_trace"]
    assert b["result"]["sampling_trace"] == a["result"]["sampling_trace"]
    current["external_damage_supplements"][0]["expected"] = 0
    c = run_plan_calc(adopt(current, db), db)
    assert c["result"]["calculation_totals"]["total_damage"] == 45
    assert c["result"]["calculation_totals"]["external_count"] == 1
    assert not db.exists()


def test_review_failed_actual_parent_cannot_be_repaired_with_external_damage(tmp_path):
    db = tmp_path / "none.db"
    current = plan(mechanical=True)
    current["rotation_model"]["abilities"][0]["energy"] = {"required": 10, "amount": 10}
    current["rotation_model"]["characters"][0]["initial_energy"] = 0
    adopt(current, db)
    with pytest.raises(PlanError):
        run_plan_calc(current, db)


def test_review_malformed_existing_parent_is_diagnostic_not_key_error(tmp_path):
    current = plan(mechanical=True)
    current["rotation_model"]["actions"][0]["ability_id"] = "missing-ability"
    try:
        report = compose_equipment_plan(current, tmp_path / "none.db")
    except PlanError:
        return
    assert report["state"] == "pending"


@pytest.mark.parametrize("field", ["id", "event_id", "coverage_key"])
def test_review_existing_engine_identity_is_not_an_external_replacement(tmp_path, field):
    current = plan()
    current["external_damage_supplements"][0][field] = "h"
    report = compose_equipment_plan(current, tmp_path / "none.db")
    assert report["state"] == "pending"
    assert report["external_damage_review"]["items"][0]["required_confirmation"] is None


@pytest.mark.parametrize("change", ["stats", "actions", "source_note", "item_note"])
def test_review_changed_input_stales_then_can_be_readopted(tmp_path, change):
    db = tmp_path / "none.db"
    current = adopt(plan(mechanical=True), db)
    previous = current["external_damage_supplements"][0]["accepted_fingerprint"]
    if change == "stats":
        current["manual_template"]["characters"][0]["stats"]["atk"] = 150
    elif change == "actions":
        current["rotation_model"]["actions"][0]["occupation_end"] = .15
    elif change == "source_note":
        current["external_damage_supplements"][0]["source_refs"] = ["fixture://revised-evidence"]
    else:
        current["external_damage_supplements"][0]["non_overlap_note"] += "；补充核对"
    report = compose_equipment_plan(current, db)
    assert report["state"] == "pending"
    assert report["external_damage_review"]["items"][0]["accepted_fingerprint"] != previous
    value = run_plan_calc(adopt(current, db), db)
    assert value["result"]["calculation_totals"]["total_damage"] == (74.5 if change == "stats" else 52)


def test_review_count_and_exact_utf8_budget_do_not_coerce_booleans(monkeypatch):
    declaration.validate_items([item()])
    nbytes = len(json.dumps([item()], ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8"))
    monkeypatch.setattr(declaration, "MAX_BYTES", nbytes)
    declaration.validate_items([item()])
    monkeypatch.setattr(declaration, "MAX_BYTES", nbytes - 1)
    with pytest.raises(ValueError):
        declaration.validate_items([item()])
    monkeypatch.setattr(declaration, "MAX_BYTES", 256 * 1024)
    monkeypatch.setattr(declaration, "MAX_ITEMS", 1)
    with pytest.raises(ValueError):
        declaration.validate_items([item(), item(id="other", event_id="other.event", coverage_key="other.coverage")])
    for key in ("t", "expected"):
        with pytest.raises(ValueError):
            declaration.validate_items([item(**{key: True})])


def test_review_basis_budget_exact_boundary_and_input_unchanged(tmp_path, monkeypatch):
    current = plan()
    original = copy.deepcopy(current)
    components = []
    source = fingerprint(components)
    basis = external._basis(current, components)
    nbytes = len(external.json_bytes([basis, components]))
    monkeypatch.setattr(external, "MAX_BASIS_BYTES", nbytes)
    assert external.review_external_damage(current, source, source_components=components)["status"] == "pending"
    monkeypatch.setattr(external, "MAX_BASIS_BYTES", nbytes - 1)
    with pytest.raises(ValueError):
        external.review_external_damage(current, source, source_components=components)
    assert current == original


@pytest.mark.parametrize("key", ["expected", "t"])
def test_review_huge_integer_is_reported_as_invalid_declaration(key):
    current = plan()
    current["external_damage_supplements"][0][key] = 10 ** 400
    with pytest.raises((PlanError, ValueError)):
        validate_plan(current)


@pytest.mark.parametrize("part", ["items", "totals", "proof"])
def test_review_truncated_ledger_or_engine_relabel_cannot_pass_outer_resign(tmp_path, part):
    db = tmp_path / "none.db"
    result = run_plan_calc(adopt(plan(), db), db)
    body, receipt = result["result"], result["result_receipt"]
    if part == "items":
        body["external_damage"]["items"] = []
    elif part == "totals":
        body["calculation_totals"]["by_character"] = {"a": 45}
    else:
        receipt["external_damage"] = {}
    receipt["result_fingerprint"] = fingerprint(body)
    with pytest.raises(PlanError):
        result_bytes(body, receipt, "json")


def test_review_source_normalization_is_narrow_and_never_mutates_inputs():
    row = {"kind": "mechanism", "id": "local", "source_fingerprint": "a" * 64,
           "accepted_fingerprint": "b" * 64, "execution_fingerprint": "c" * 64}
    original = copy.deepcopy(row)
    first = external._basis(plan(), [row])
    row["execution_fingerprint"] = "d" * 64
    assert external._basis(plan(), [row]) == first
    row["source_fingerprint"] = "e" * 64
    assert external._basis(plan(), [row]) != first
    assert original["execution_fingerprint"] == "c" * 64
    for ordinary in ({"kind": "other", "execution_fingerprint": "c" * 64},
                     {"kind": "other", "nested": {"execution_fingerprint": "c" * 64}}):
        before = external._basis(plan(), [ordinary])
        target = ordinary.get("nested", ordinary)
        target["execution_fingerprint"] = "d" * 64
        assert external._basis(plan(), [ordinary]) != before
    row["unexpected"] = True
    with pytest.raises(ValueError):
        external._basis(plan(), [row])
