"""Native fixed-source ownership, fresh tuning and protected Excel integration."""
from __future__ import annotations

import copy
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest
from test_target_state_review import inputs

from genshin_app.equipment import (
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
    validate_plan,
)
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    _Projection,
    build_formula_workbook,
    formula_workbook_support,
)

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def native(tmp_path):
    template, rotation, mechanics = inputs(("cryo", "hydro", "physical"))
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    mechanics["fixed_effect_sources"][0]["source_id"] = "manual-source:fixed"
    plan["effect_bindings"] = [{"binding_id": "manual-source", "provider_character_id": "a",
        "owner": {"kind": "manual", "note": "控制变量实验"}, "source_refs": [{"kind": "manual", "note": "独立固定条件测试"}],
        "source_fingerprint": None, "coverage": {"state": "complete", "reviewed_by": "test", "source_fingerprint": None,
                                                 "note": "仅本测试声明的两段条件"},
        "parts": [{"part_id": "fixed", "label": "固定条件来源", "state": "resolved", "conditions": [],
                   "rule": {"kind": "fixed_effect_source", "fixed_source_id": "review-fixed"},
                   "delivery": {"kind": "fixed_effect_source"}, "reason": None,
                   "dependencies": {"mode": "source_only", "context_fingerprint": None, "note": "独立测试依据"}}]}]
    db = tmp_path / "absent.db"
    report = compose_equipment_plan(plan, db)
    fp = report["effect_requirements"][0]["source_fingerprint"]
    plan["effect_bindings"][0]["source_fingerprint"] = fp
    plan["effect_bindings"][0]["coverage"]["source_fingerprint"] = fp
    return plan, db


def test_native_fixed_source_preserved_with_live_conditions_and_receipt(tmp_path):
    plan, db = native(tmp_path)
    before = copy.deepcopy(plan)
    calculated = run_plan_calc(plan, db)
    assert plan == before
    assert [h["expected"] for h in calculated["result"]["hits"]] == pytest.approx([45, 54, 63])
    assert calculated["result_receipt"]["target_conditions"]["sample_phase"] == "before_application"
    assert calculated["result_receipt"]["target_conditions"]["fixed_sources_fingerprint"] == fingerprint(
        calculated["report"]["mechanics_model"]["fixed_effect_sources"])
    assert calculated["plan"]["mechanics_model"]["initial_state"]["rules"] == []


@pytest.mark.parametrize("mutation", ["unlinked", "interval", "projection-coverage"])
def test_fixed_sources_do_not_bypass_missing_source_review(tmp_path, mutation):
    plan, db = native(tmp_path)
    binding = plan["effect_bindings"][0]
    if mutation == "unlinked":
        binding["parts"] = []
    elif mutation == "interval":
        binding["parts"][0]["conditions"] = [{"condition_id": "time", "description": "错误时间区间", "state": "confirmed",
            "scope": "intervals", "intervals": [{"start": 0, "end": 10}], "evidence": [{"kind": "manual"}]}]
    else:
        binding["coverage"]["state"] = "projection"
    out = compose_equipment_plan(plan, db)
    assert out["state"] == "pending"
    assert out["mechanics_model"]["fixed_effect_sources"] == []


@pytest.mark.parametrize("mutation", ["wrong-owner", "wrong-source", "duplicate-part", "mixed-reference"])
def test_fixed_identity_conflicts_rejected(tmp_path, mutation):
    plan, db = native(tmp_path)
    if mutation == "wrong-owner":
        plan["mechanics_model"]["fixed_effect_sources"][0]["owner"] = "unknown"
    elif mutation == "wrong-source":
        plan["mechanics_model"]["fixed_effect_sources"][0]["source_id"] = "wrong"
    elif mutation == "duplicate-part":
        part = copy.deepcopy(plan["effect_bindings"][0]["parts"][0])
        part["part_id"] = "duplicate"
        plan["effect_bindings"][0]["parts"].append(part)
    else:
        plan["rotation_tuning"]["buff_hit_matches"] = [tuning()]
        plan["rotation_tuning"]["buff_hit_matches"][0]["effect_ref"]["buff_id"] = "also-buff"
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, db)


def tuning():
    return {"id": "tune", "effect_ref": {"fixed_source_id": "review-fixed", "effect_id": "ice"},
            "mode": "adjust", "include_hit_ids": ["h0"], "exclude_hit_ids": ["h1"], "state": "pending",
            "basis_fingerprint": None, "reason": "独立人为覆盖反例", "source_refs": [{"kind": "manual"}]}


def test_fixed_tuning_requires_fresh_basis_and_keeps_automatic_false(tmp_path):
    plan, db = native(tmp_path)
    match = tuning()
    plan["rotation_tuning"]["buff_hit_matches"] = [match]
    preview = compose_equipment_plan(plan, db)
    assert preview["state"] == "pending"
    p = preview["match_previews"][0]
    assert p["kind"] == "fixed_effect_source"
    match.update(state="confirmed", basis_fingerprint=p["basis_fingerprint"])
    result = run_plan_calc(plan, db)["result"]
    assert [h["expected"] for h in result["hits"]] == pytest.approx([54, 45, 63])
    trace = result["target_condition_trace"][0]
    assert trace["automatic_value"] is False and trace["override"] == "include" and trace["applied"]
    plan["mechanics_model"]["hit_events"][0]["order"] += 1
    assert compose_equipment_plan(plan, db)["state"] == "pending"


def test_fixed_values_and_conditions_are_protected_in_formula_and_snapshot_exports(tmp_path):
    plan, db = native(tmp_path)
    calculated = run_plan_calc(plan, db)
    assert formula_workbook_support(calculated)["status"] == "supported"
    with ExecutionVerificationSession() as session:
        projection = _Projection(calculated, _session=session).finish()
    assert projection.summary.rows[5][1].value == pytest.approx(162)
    content = build_formula_workbook(calculated)
    snapshots = result_bytes(calculated["result"], calculated["result_receipt"], "xlsx")
    for data in (content, snapshots):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            book = ET.fromstring(archive.read("xl/workbook.xml"))
            sheets = book.findall("s:sheets/s:sheet", NS)
            index = next(i + 1 for i, sheet in enumerate(sheets) if sheet.get("name") == "目标条件收益")
            sheet = ET.fromstring(archive.read(f"xl/worksheets/sheet{index}.xml"))
            assert sheet.find("s:sheetProtection", NS) is not None
    path = "mechanics_model.fixed_effect_sources[0].effects[0].value"
    row = projection.rows_by_path[path]
    assert projection.inputs.rows[row - 1][2].style != 2  # editable cells use a distinct unlocked style
    assert projection.inputs.rows[row - 1][8].formula  # structural invalidation


@pytest.mark.parametrize("field", ["model", "trace", "input"])
def test_formula_rejects_stale_target_proof(tmp_path, field):
    plan, db = native(tmp_path)
    calculated = run_plan_calc(plan, db)
    if field == "model":
        calculated["report"]["mechanics_model"]["fixed_effect_sources"][0]["effects"][0]["value"] = .4
    elif field == "trace":
        calculated["result"]["target_condition_trace"][0]["automatic_value"] = True
    else:
        calculated["plan"]["mechanics_model"]["hit_events"][0]["order"] += 1
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(calculated)


def test_legacy_plan_cannot_silently_ignore_fixed_match(tmp_path):
    plan, _ = native(tmp_path)
    plan["plan_version"] = 2
    del plan["mechanics_model"]
    plan["effect_bindings"] = []
    plan["rotation_tuning"]["buff_hit_matches"] = [tuning()]
    with pytest.raises(PlanError, match="版本3"):
        validate_plan(plan)
