"""External values remain separate from executable damage and require fresh review."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.delivery import build_calculation_delivery
from genshin_app.equipment.external_damage import (
    validate_external_damage_receipt,
    validate_external_damage_result_receipt,
)
from genshin_app.equipment.files import context_view, fingerprint, input_view
from genshin_app.equipment.schema import PlanError, validate_plan
from genshin_app.equipment.service import export_resolved_plan, result_bytes
from tests.test_calculation_delivery import delivery_plan


def supplement(**changes):
    return {"id": "extra.1", "label": "独立计算的工程额外事件", "character": "a", "skill": "外部工程伤害",
            "t": 1.5, "expected": 12, "action_id": None, "coverage_key": "external.contact.1",
            "event_id": "external.event.1", "reason": "演示未建模的独立新增机制",
            "calculation_note": "工程常量3乘4等于12，不是游戏事实", "non_overlap_note": "不包括已有h命中",
            "source_refs": ["虚构工程校验，非游戏数值"], "accepted_fingerprint": None, **changes}


def external_plan():
    plan = delivery_plan()
    plan["external_damage_supplements"] = [supplement()]
    return plan


def confirm(plan, db):
    report = compose_equipment_plan(plan, db)
    for item, row in zip(plan["external_damage_supplements"], report["external_damage_review"]["items"], strict=True):
        assert row["required_confirmation"] is not None, row["issues"]
        item.update(row["required_confirmation"])
    return plan


@pytest.fixture
def calculated(tmp_path):
    db = tmp_path / "absent.db"
    return run_plan_calc(confirm(external_plan(), db), db, capture_display=True)


def validate(calculated):
    validate_external_damage_receipt(calculated["plan"], calculated["result"], calculated["report"], calculated["result_receipt"])


def test_fresh_review_and_actual_calc_do_not_modify_engine_or_inputs(tmp_path):
    db, plan = tmp_path / "absent.db", external_plan()
    original = copy.deepcopy(plan)
    pending = compose_equipment_plan(plan, db)
    assert plan == original and pending["state"] == "pending"
    assert {i["code"] for i in pending["missing_inputs"]} == {"external_damage_confirmation_required"}
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)
    baseline = run_plan_calc(delivery_plan(), db)
    confirm(plan, db)
    before = copy.deepcopy(plan)
    actual = run_plan_calc(plan, db)
    assert plan == before
    engine_fields = {
        "external_damage", "calculation_totals", "calculation_ownership_proof",
        "damage_explanations",
        "damage_explanation_fingerprint",
    }
    assert {
        k: v for k, v in actual["result"].items() if k not in engine_fields
    } == {
        k: v for k, v in baseline["result"].items() if k not in engine_fields
    }
    assert actual["result"]["summary"]["total_damage"] == 45
    assert actual["result"]["calculation_totals"] == {
        "total_damage": 57, "dps": 28.5, "by_character": {"a": 57},
        "by_skill": {"手工工程命中": 45, "外部工程伤害": 12}, "engine_total_damage": 45,
        "external_total_damage": 12, "external_count": 1, "evidence_tier": "includes_external_calculation"}
    validate(actual)


def test_approximate_single_event_propagates_precision_without_becoming_state_logic(tmp_path):
    db, plan = tmp_path / "absent.db", external_plan()
    plan["external_damage_supplements"][0]["precision"] = {
        "kind": "approximate",
        "method": "公开帧数与独立公式折算，按一次事件取中值",
        "uncertainty": "内部触发次数不可直接观察，约有正负一次命中的不确定性",
        "state_effects": "none",
    }
    actual = run_plan_calc(confirm(plan, db), db)
    item = actual["result"]["external_damage"]["items"][0]
    totals = actual["result"]["calculation_totals"]
    assert item["precision"]["kind"] == "approximate"
    assert totals["precision"] == "approximate"
    assert totals["approximate_event_count"] == 1
    assert totals["approximate_by_character"] == ["a"]
    assert totals["approximate_by_skill"] == ["外部工程伤害"]
    assert totals["approximation_disclosures"] == [{
        "event_id": "external.event.1",
        "coverage_key": "external.contact.1",
        "character": "a",
        "skill": "外部工程伤害",
        "method": plan["external_damage_supplements"][0]["precision"]["method"],
        "uncertainty": plan["external_damage_supplements"][0]["precision"]["uncertainty"],
        "state_effects": "none",
    }]
    validate(actual)


@pytest.mark.parametrize("precision", [
    {},
    {"kind": "approximate", "method": "", "uncertainty": "未知", "state_effects": "none"},
    {"kind": "approximate", "method": "近似", "uncertainty": "未知", "state_effects": "materialized_elsewhere"},
])
def test_approximate_single_event_requires_method_uncertainty_and_no_hidden_state(precision):
    plan = external_plan()
    plan["external_damage_supplements"][0]["precision"] = precision
    with pytest.raises(PlanError):
        validate_plan(plan)


def test_independent_item_does_not_invalidate_earlier_acceptance(tmp_path):
    db = tmp_path / "absent.db"
    plan = confirm(external_plan(), db)
    first = plan["external_damage_supplements"][0]["accepted_fingerprint"]
    plan["external_damage_supplements"].append(supplement(id="extra.2", event_id="external.event.2", coverage_key="external.contact.2"))
    rows = compose_equipment_plan(plan, db)["external_damage_review"]["items"]
    assert rows[0]["status"] == "ready" and rows[0]["accepted_fingerprint"] == first
    assert rows[1]["status"] == "pending"


@pytest.mark.parametrize("mutation", ["panel", "enemy", "window", "value", "note"])
def test_business_or_item_change_invalidates_confirmation(tmp_path, mutation):
    db = tmp_path / "absent.db"
    plan = confirm(external_plan(), db)
    if mutation == "panel":
        plan["manual_template"]["characters"][0]["stats"]["atk"] = 101
    elif mutation == "enemy":
        plan["manual_template"]["enemy"]["level"] = 91
    elif mutation == "window":
        plan["manual_template"]["loop_duration"] = 3
    else:
        plan["external_damage_supplements"][0]["expected" if mutation == "value" else "calculation_note"] = 13 if mutation == "value" else "changed"
    assert compose_equipment_plan(plan, db)["external_damage_review"]["status"] == "pending"


@pytest.mark.parametrize(("key", "value"), [("expected", -1), ("expected", True), ("expected", float("inf")),
    ("t", .0000001), ("t", False), ("label", ""), ("source_refs", []), ("source_refs", [{}]),
    ("event_id", "x" * 129), ("accepted_fingerprint", "fake")])
def test_invalid_declarations_are_rejected(key, value):
    plan = external_plan()
    plan["external_damage_supplements"][0][key] = value
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize("key", ["id", "event_id", "coverage_key"])
def test_duplicate_identities_rejected(key):
    plan = external_plan()
    second = supplement(id="extra.2", event_id="external.event.2", coverage_key="external.contact.2")
    second[key] = plan["external_damage_supplements"][0][key]
    plan["external_damage_supplements"].append(second)
    with pytest.raises(PlanError):
        validate_plan(plan)


@pytest.mark.parametrize(("key", "value"), [("t", 2), ("character", "missing"), ("event_id", "h"),
    ("coverage_key", "h"), ("action_id", "missing")])
def test_invalid_scope_is_pending_not_confirmable(tmp_path, key, value):
    plan = external_plan()
    plan["external_damage_supplements"][0][key] = value
    row = compose_equipment_plan(plan, tmp_path / "absent.db")["external_damage_review"]["items"][0]
    assert row["status"] == "pending" and row["required_confirmation"] is None


@pytest.mark.parametrize("target", ["item", "total", "engine", "proof", "ledger", "basis"])
def test_outer_rehash_cannot_hide_missing_or_changed_evidence(calculated, target):
    result, receipt = calculated["result"], calculated["result_receipt"]
    if target == "item":
        result["external_damage"]["items"][0]["expected"] += 1
    elif target == "total":
        result["calculation_totals"]["total_damage"] += 1
    elif target == "engine":
        result["hits"][0]["expected"] += 1
    elif target == "proof":
        receipt.pop("external_damage")
    elif target == "ledger":
        result.pop("external_damage")
    else:
        result["external_damage"]["basis"]["source_fingerprint"] = "a" * 64
    receipt["result_fingerprint"] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_external_damage_result_receipt(result, receipt)


def test_full_validation_rejects_deleted_every_external_output(calculated):
    calculated["result"].pop("external_damage")
    calculated["result"].pop("calculation_totals")
    calculated["result_receipt"].pop("external_damage")
    calculated["result_receipt"]["result_fingerprint"] = fingerprint(calculated["result"])
    with pytest.raises(ValueError):
        validate(calculated)


def test_offline_json_and_delivery_validate_and_unsupported_exports_refuse(calculated, tmp_path, monkeypatch):
    monkeypatch.setattr("sqlite3.connect", lambda *a, **k: pytest.fail("offline opened DB"))
    assert json.loads(result_bytes(calculated["result"], calculated["result_receipt"], "json"))["result"] == calculated["result"]
    built = build_calculation_delivery(calculated, input_file_sha="a" * 64)
    assert built["summary"]["total_damage"] == 57
    with pytest.raises(PlanError, match="外部补充"):
        result_bytes(calculated["result"], calculated["result_receipt"], "csv")
    dest = tmp_path / "must-not-exist.json"
    with pytest.raises(PlanError, match="外部补充"):
        export_resolved_plan(calculated["plan"], tmp_path / "none.db", dest)
    assert not dest.exists()


def test_empty_and_absent_keep_old_result_and_no_review(tmp_path):
    db = tmp_path / "none.db"
    old = delivery_plan()
    a = run_plan_calc(old, db)
    old["external_damage_supplements"] = []
    b = run_plan_calc(old, db)
    assert a["result"] == b["result"]
    assert "external_damage_review" not in b["report"] and "external_damage" not in b["result_receipt"]
    assert "external_damage_supplements" not in input_view(a["plan"])


def test_manual_context_excludes_only_root_external_account():
    plan = delivery_plan()
    before = fingerprint(context_view(input_view(plan)))
    plan["external_damage_supplements"] = [supplement()]
    assert fingerprint(context_view(input_view(plan))) == before
    plan["external_damage_supplements"][0]["expected"] = 99
    assert fingerprint(context_view(input_view(plan))) == before
    plan["manual_template"]["characters"][0]["stats"]["atk"] = 101
    assert fingerprint(context_view(input_view(plan))) != before
    assert context_view({"external_damage_supplements": [1]}) == {"external_damage_supplements": [1]}


def test_basis_preference_is_excluded_only_at_editor_paths_without_mutating_input():
    plan = delivery_plan()
    plan["equipment_inputs"] = {"a": {"mode": "panel", "panel_basis_mode": "manual"}}
    plan["extensions"] = {"whiteboard_handoff": {"board": {
        "team": [{"id": "a", "panel_basis_mode": "manual", "panel": {"base_atk": 200}}]}}}
    original = copy.deepcopy(plan)
    normalized = context_view(input_view(plan))
    assert "panel_basis_mode" not in normalized["equipment_inputs"]["a"]
    assert "panel_basis_mode" not in normalized["extensions"]["whiteboard_handoff"]["board"]["team"][0]
    assert plan == original and input_view(plan)["equipment_inputs"]["a"]["panel_basis_mode"] == "manual"
    before = fingerprint(normalized)
    plan["equipment_inputs"]["a"]["panel_basis_mode"] = "database"
    plan["extensions"]["whiteboard_handoff"]["board"]["team"][0]["panel_basis_mode"] = "database"
    assert fingerprint(context_view(input_view(plan))) == before
    plan["extensions"]["whiteboard_handoff"]["board"]["team"][0]["panel"]["base_atk"] = 201
    assert fingerprint(context_view(input_view(plan))) != before
    unrelated = {"panel_basis_mode": "manual", "nested": {"mode": "panel", "panel_basis_mode": "manual"},
                 "equipment_inputs": {"a": {"mode": "build", "panel_basis_mode": "manual"}}}
    assert context_view(unrelated) == unrelated


def test_explicit_limits_and_finite_aggregate(tmp_path):
    plan = external_plan()
    plan["external_damage_supplements"] = [supplement(id=f"extra.{i}", event_id=f"event.{i}", coverage_key=f"cover.{i}") for i in range(513)]
    with pytest.raises(PlanError, match="512") as item_limit:
        validate_plan(plan)
    assert item_limit.value.details == []
    plan["external_damage_supplements"] = [supplement(id=f"extra.{i}", event_id=f"event.{i}", coverage_key=f"cover.{i}",
        reason="长" * 4096, calculation_note="长" * 4096, non_overlap_note="长" * 4096) for i in range(8)]
    with pytest.raises(PlanError, match="256KiB") as byte_limit:
        validate_plan(plan)
    assert byte_limit.value.details == []
    plan = external_plan()
    plan["external_damage_supplements"][0]["expected"] = 1e308
    plan["external_damage_supplements"].append(supplement(id="other", event_id="other.event", coverage_key="other.cover", expected=1e308))
    db = tmp_path / "none.db"
    with pytest.raises(PlanError):
        run_plan_calc(confirm(plan, db), db)


def test_only_explicit_mechanism_execution_dependency_is_normalized():
    from genshin_app.equipment.external_damage import _basis
    plan = external_plan()
    components = [{"kind": "mechanism", "id": "m", "source_fingerprint": "a" * 64,
                   "accepted_fingerprint": "b" * 64, "execution_fingerprint": "c" * 64},
                  {"kind": "other", "execution_fingerprint": "original", "nested": {"execution_fingerprint": "keep"}},
                  {"version_line": "release", "upstream_commit": "v1"}]
    original = _basis(plan, components)
    components[0]["execution_fingerprint"] = "d" * 64
    assert _basis(plan, components) == original
    for index, key in ((0, "source_fingerprint"), (0, "accepted_fingerprint"), (1, "execution_fingerprint"), (2, "upstream_commit")):
        changed = copy.deepcopy(components)
        changed[index][key] = "changed"
        assert _basis(plan, changed) != original
    components[1]["nested"]["execution_fingerprint"] = "changed"
    assert _basis(plan, components) != original
