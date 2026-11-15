"""Numeric source delivery keeps templates immutable and spreadsheet claims honest."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

from test_elemental_app import native
from test_runtime_action_facts import action_db, fake_repo, stub_fetcher  # noqa: F401

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.files import read_plan
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_db.runtime_queries import query_runtime

# ruff: noqa: F811


def native_source(db):
    plan = native()
    hit = plan["manual_template"]["hits"][0]
    hit.pop("multiplier")
    hit["scaling_stat"] = "atk"
    query = {"character_id": 90000001, "slot": "normal", "constellation": 0}
    value = query_runtime(db, {"action": "action_parameter", "param_index": 0, **query})
    plan["hit_parameter_sources"] = [{"hit_id": "h0", "version_line": "release", "selection": query, "param_index": 0,
        "scaling_stat": "atk", "purpose": "single_stat_damage_multiplier", "status": "assumed",
        "source": "工程夹具：明确选择此标量作为攻击力倍率；并非资料自动证明公式",
        "expected_snapshot_id": value["snapshot"]["snapshot_id"], "expected_node_sha256": value["source"]["node_sha256"]}]
    return plan


def test_save_reload_and_gui_recompile_without_writing_multiplier_to_input(action_db):
    plan = native_source(action_db)
    before = copy.deepcopy(plan)
    result = run_plan_calc(plan, action_db)
    assert plan == before
    assert "multiplier" not in result["plan"]["manual_template"]["hits"][0]
    assert result["result"]["hits"][0]["expected"] == 22.5
    source = result["result"]["hit_parameter_sources"][0]
    assert source["gameplay_interpretation"] == "declared_not_verified"
    assert source["source"]["parameter_pointer"] == "/0/paramList/0"
    assert source["parameter_descriptions"]
    assert result["result_receipt"]["hit_parameter_sources"][0]["compilation_fingerprint"] == source["compilation_fingerprint"]
    path = action_db.parent / "numeric-native.json"
    path.write_text(json.dumps(result["plan"]), "utf8")
    loaded = read_plan(path)["plan"]
    loaded["resolved_template"]["hits"][0]["multiplier"] = 999
    gui = GuiApi(action_db.parent).run_plan_calc(loaded)
    assert gui["code"] == 0
    assert gui["data"]["result"] == result["result"]


def test_prototype_binding_expands_existing_pattern_with_same_parameter(action_db):
    plan = native_source(action_db)
    plan["manual_template"]["hits"] = plan["manual_template"]["hits"][:1]
    m = plan["mechanics_model"]
    m.pop("elemental_model")
    m["hit_events"] = []
    m["hit_patterns"] = [{"id": "pulse", "action_id": "cast", "prototype_hit_id": "h0",
                          "offsets": [.2, .3, .4], "orders": [10, 11, 12]}]
    result = run_plan_calc(plan, action_db)
    assert [h["id"] for h in result["result"]["hits"]] == ["pulse:1", "pulse:2", "pulse:3"]
    assert [h["expected"] for h in result["result"]["hits"]] == [22.5] * 3
    assert result["plan"]["rotation_model"] == plan["rotation_model"]
    assert "multiplier" not in result["plan"]["manual_template"]["hits"][0]
    _assert_workbook_cells(result, 3)


def _assert_workbook_cells(result, fixed_count):
    support = formula_workbook_support(result)
    assert support["editable"]["hit_multiplier"] == "manual_only"
    assert support["editable"]["database_hit_parameters"] == "regenerate_required"
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(build_formula_workbook(result))) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet2.xml"))
        multiplier_cells = []
        for row in root.findall(".//s:row", ns):
            cells = list(row)
            key = "".join(cells[0].itertext()) if cells else ""
            if key.startswith("hits[") and key.endswith(".multiplier"):
                multiplier_cells.append(cells[2])
        assert sum(c.attrib.get("s") == "5" for c in multiplier_cells) == fixed_count
        events = ET.fromstring(archive.read("xl/worksheets/sheet3.xml"))
        cells = {c.attrib["r"]: c for c in events.findall(".//s:c", ns)}
        assert cells["V5"].find("s:f", ns) is not None
        assert float(cells["V5"].find("s:v", ns).text) == 22.5


def test_excel_database_multiplier_is_fixed_but_manual_ones_remain_editable(action_db):
    _assert_workbook_cells(run_plan_calc(native_source(action_db), action_db), 1)


def test_context_cached_per_selection_within_compose_then_refetched(action_db, monkeypatch):
    from genshin_db import runtime_action_parameter
    plan = native_source(action_db)
    plan["manual_template"]["hits"][1].pop("multiplier")
    plan["manual_template"]["hits"][1]["scaling_stat"] = "atk"
    second = copy.deepcopy(plan["hit_parameter_sources"][0])
    second["hit_id"] = "h1"
    plan["hit_parameter_sources"].append(second)
    original, calls = runtime_action_parameter.get_action_parameter_context, []
    def count(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(runtime_action_parameter, "get_action_parameter_context", count)
    first = compose_equipment_plan(plan, action_db)
    assert first["state"] == "ready" and len(calls) == 1
    compose_equipment_plan(plan, action_db)
    assert len(calls) == 2
