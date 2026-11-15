"""Native GUI bridge, file reload and XLSX share the new elemental calculation."""
from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest
from test_elemental_execution import setup

from genshin_app.agent_chat.calculation_qualification import qualify_calculation
from genshin_app.equipment import new_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi


def native():
    template, rotation, mechanics = setup()
    template["characters"][0]["stats"]["energy_recharge"] = 1
    plan = enable_mechanics(new_plan(template))
    plan.update(rotation_model=rotation, mechanics_model=mechanics)
    return plan


def test_native_save_reload_gui_and_excel_use_simulated_reactions(tmp_path):
    plan = native()
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan), encoding="utf-8")
    db = tmp_path / "genshin.db"
    calculated = run_plan_calc(read_plan(path)["plan"], db)
    response = GuiApi(tmp_path).run_plan_calc(plan)
    assert response["code"] == 0
    assert response["data"]["result"] == calculated["result"]
    assert calculated["result"]["elemental"]["trace"][1]["reaction"]["type"] == "vaporize"
    interpretation = calculated["result"]["rotation_check"]["damage_interpretation"]
    reaction_verification = interpretation["reaction_verification"]
    assert reaction_verification["status"] == "passed"
    assert reaction_verification["coverage_status"] == "complete_input_window"
    assert reaction_verification["mode"] == "elemental_runtime_simulated"
    assert reaction_verification["evaluated_application_count"] > 0
    assert reaction_verification["reaction_event_count"] > 0
    application_verification = interpretation["application_verification"]
    assert application_verification["status"] == "passed"
    assert application_verification["coverage_status"] == "complete_input_window"
    assert application_verification["mode"] == "elemental_runtime_simulated"
    assert application_verification["executed_application_count"] > 0
    acceptance = qualify_calculation(
        calculated,
        topology_receipt={
            "status": "numeric_ready",
            "counts": {"active_nodes": 3},
            "active_node_kinds": ["action_anchor", "application", "reaction_candidate"],
            "active_node_kind_counts": {
                "action_anchor": 1, "application": 1, "reaction_candidate": 1,
            },
        },
        topology_binding_receipt={"status": "complete"},
        energy_intent="multi_round_legal",
    )
    assert acceptance["status"] == "accepted"
    assert "未模拟元素底" not in calculated["result"]["rotation_check"]["damage_interpretation"]["message"]
    assert formula_workbook_support(calculated)["editable"]["elemental_profiles_initial_aura_icd_reactions"] == "regenerate_required"
    workbook = build_formula_workbook(calculated)
    with zipfile.ZipFile(io.BytesIO(workbook)) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        cells = {c.attrib["r"]: c for c in root.findall(".//s:c", ns)}
        assert [float(cells[f"V{i}"] .find("s:v", ns).text) for i in range(5, 9)] == [45, 67.5, 67.5, 45]
        assert all(cells[f"V{i}"].find("s:f", ns) is not None for i in range(5, 9))


def test_native_pending_aura_does_not_allow_exportable_result(tmp_path):
    plan = native()
    plan["mechanics_model"]["elemental_model"]["target"]["knowledge"] = "unknown"
    with pytest.raises(PlanError) as error:
        run_plan_calc(plan, tmp_path / "genshin.db")
    assert error.value.reason == "mechanics_pending"
    assert error.value.details["result"] is None
    response = GuiApi(tmp_path).run_plan_calc(plan)
    assert response["code"] != 0
