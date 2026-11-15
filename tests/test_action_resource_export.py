"""Resource provenance, immutable source receipts and workbook boundaries."""
from __future__ import annotations

import copy
import io
import sqlite3
import xml.etree.ElementTree as ET
import zipfile

import pytest
from formula_workbook_fixtures import envelope

from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.export import action_resource_rows, export_result
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_action_resources_app import confirm, resource_plan
from tests.test_runtime_action_resource import (  # noqa: F401
    fake_repo,
    resource_db,
    stub_fetcher,
)

# ruff: noqa: F811

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def resource_envelope():
    """Export-only synthetic receipt; compiler/service receive separate tests."""
    data = envelope()
    binding = {"ability_id": "q", "variant": "普通元素爆发", "source": "工程用途声明", "status": "assumed",
               "selection": {"character_id": 1, "slot": "burst", "constellation": 0}, "version_line": "release",
               "purpose": "ordinary_fixed_burst_base_resources", "expected_snapshot_id": "snapshot",
               "expected_resource_fingerprint": "resource", "expected_scope_fingerprint": "scope"}
    rotation = {"characters": [{"id": "a"}], "abilities": [{"id": "q", "character": "a",
        "variant": "普通元素爆发", "cooldown": {"group": "q", "start": "action_start", "recovery": "parallel"}, "energy": {}}],
        "actions": [{"id": "cast", "ability_id": "q", "start": 0}],
        "events": [{"id": "spend", "type": "energy_spend", "action_id": "cast", "character": "a", "t": 0}]}
    data["plan"].update(action_resource_sources=[binding], rotation_model=rotation)
    compiled = copy.deepcopy(rotation)
    values = {"cooldown": {"duration": 20, "charges": 1}, "energy": {"required": 80, "amount": 80}}
    compiled["abilities"][0]["cooldown"].update(values["cooldown"])
    compiled["abilities"][0]["energy"].update(values["energy"])
    compiled["events"][0].update(values["energy"])
    origin = {"json_pointer": "/13", "source_path": "ExcelBinOutput/AvatarSkillExcelConfigData.json",
              "source_sha256": "source-sha", "node_sha256": "node-sha", "raw_pointer": "/13/cdTime"}
    record = {"ability_id": "q", "actor": "a", "status": "ready", "compiler": "database-action-resource-v1",
        "interpretation_status": "assumed", "interpretation_basis": "工程用途声明",
        "purpose": binding["purpose"], "variant": binding["variant"], "identity_coverage": "explicit_sources_only",
        "gameplay_interpretation": "declared_not_verified", "resolved_selection": binding["selection"],
        "snapshot": {"snapshot_id": "snapshot"}, "resource_fingerprint": "resource", "scope_fingerprint": "scope",
        "required_confirmation": {k: v for k, v in binding.items() if k.startswith("expected_")},
        "descriptions": {"name": {"status": "ready", "text": "工程爆发"},
                         "description": {"status": "ready", "text": '=HYPERLINK("https://example.invalid","原文仅文本")'}},
        "projection": {"base_cooldown_seconds": {"status": "ready", "source_field": "cdTime", "raw_value": 20,
                                                  "value": 20, "origin": origin}},
        "identity_sources": {"skill": {**origin, "definition": {"definition_id": "exact-skill"}}},
        "action_spend_links": [{"action_id": "cast", "spend_id": "spend"}], "target_paths": ["rotation_model.events.spend.amount"],
        "known_aliases": [], "explicit_modifiers": [], "compiled_values": values, "issues": []}
    record["compilation_fingerprint"] = fingerprint(record)
    records = [record]
    data["report"] = {"compiled_rotation_model": compiled, "compiled_rotation_model_fingerprint": fingerprint(compiled),
                      "action_resource_compilations": copy.deepcopy(records)}
    data["result"]["action_resource_compilations"] = records
    executed = copy.deepcopy(compiled)
    executed["characters"][0]["energy_recharge"] = 1.5
    data["result"]["rotation_check"]["resolved_rotation_model"] = executed
    data["result_receipt"].update(input_fingerprint=fingerprint(input_view(data["plan"])),
        result_fingerprint=fingerprint(data["result"]), action_resources={
            "compiled_rotation_model_fingerprint": fingerprint(compiled), "compilations_fingerprint": fingerprint(records),
            "identity_coverage": "explicit_sources_only"})
    return data


def sheets(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = ET.fromstring(archive.read("xl/workbook.xml")).findall("s:sheets/s:sheet", NS)
        return {item.get("name"): ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml")) for i, item in enumerate(names, 1)}


def test_resource_pages_are_readonly_literal_and_same_snapshot(tmp_path, monkeypatch):
    data = resource_envelope()
    before = copy.deepcopy(data)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("export accessed database"))
    formula = sheets(build_formula_workbook(data))
    path = export_result(data["result"], "xlsx", tmp_path / "snapshot.xlsx")
    snapshot = sheets(path.read_bytes())
    for book in (formula, snapshot):
        page = book["动作资源来源"]
        assert page.find("s:sheetProtection", NS) is not None
        assert page.findall(".//s:f", NS) == []
        text = "".join(page.itertext())
        assert all(v in text for v in ("工程爆发", "HYPERLINK", "declared_not_verified", "explicit_sources_only",
                                     "exact-skill", "/13/cdTime", "source-sha", "node-sha", "spend", "required"))
    assert fingerprint(data["report"]["compiled_rotation_model"]) != fingerprint(data["result"]["rotation_check"]["resolved_rotation_model"])
    assert data == before
    assert "duration" not in data["plan"]["rotation_model"]["abilities"][0]["cooldown"]


def test_resource_fixed_parameters_invalidate_live_results():
    data = resource_envelope()
    book = sheets(build_formula_workbook(data))
    rows = {r.find("s:c/s:is/s:t", NS).text: r for r in book["参数"].findall("s:sheetData/s:row", NS)
            if r.find("s:c/s:is/s:t", NS) is not None}
    for path in ("action_resource_sources[0].source", "action_resource_compilations[0].compiled_values.energy.amount",
                 "rotation_model.abilities[0].cooldown.group", "rotation_model.actions[0].start"):
        cells = rows[path].findall("s:c", NS)
        assert cells[2].get("s") == "5"
        assert "<>" in cells[8].find("s:f", NS).text
    support = formula_workbook_support(data)
    assert support["editable"]["database_action_resources"] == "regenerate_required"
    assert support["editable"]["action_resource_provenance"] == "readonly_snapshot"


@pytest.mark.parametrize("mutation", [
    lambda d: d["plan"]["action_resource_sources"][0].update(source="修改依据"),
    lambda d: d["plan"]["action_resource_sources"][0].update(purpose="unresolved_resource_mode"),
    lambda d: d["plan"]["action_resource_sources"][0].update(expected_scope_fingerprint="stale"),
    lambda d: d["plan"]["rotation_model"]["abilities"][0]["cooldown"].update(group="other"),
    lambda d: d["plan"]["rotation_model"]["actions"][0].update(start=1),
    lambda d: d["plan"]["rotation_model"]["events"][0].update(character="b"),
    lambda d: d["report"]["compiled_rotation_model"]["abilities"][0]["energy"].update(amount=1),
    lambda d: d["report"]["action_resource_compilations"][0].update(status="pending"),
    lambda d: d["result"]["action_resource_compilations"][0].update(interpretation_basis="changed"),
    lambda d: d["result_receipt"].pop("action_resources"),
])
def test_changed_inputs_or_compiled_receipts_reject(mutation):
    data = resource_envelope()
    mutation(data)
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(data)


def test_missing_projection_is_pending_not_zero_and_long_text_complete():
    record = resource_envelope()["result"]["action_resource_compilations"][0]
    text = "原文💧" * 16000
    record["descriptions"]["description"]["text"] = text
    rows = action_resource_rows({"action_resource_compilations": [record]})
    index = next(i for i, row in enumerate(rows) if row and row[0] == "技能原文")
    pieces = [rows[index][1]]
    for row in rows[index + 1:]:
        if row[0] != "续":
            break
        pieces.append(row[1])
    assert "".join(pieces) == text
    assert all(len(part.encode("utf-16-le")) // 2 <= 30000 for part in pieces)
    assert next(row for row in rows if row and row[0] == "基础元素能量")[1:3] == ["pending", "待补"]


def test_no_bindings_keep_legacy_export_shape(tmp_path):
    data = envelope()
    assert action_resource_rows(data["result"]) == []
    assert "database_action_resources" not in formula_workbook_support(data)["editable"]
    assert "动作资源来源" not in sheets(build_formula_workbook(data))
    assert "动作资源来源" not in sheets(export_result(data["result"], "xlsx", tmp_path / "legacy.xlsx").read_bytes())


def test_real_compiler_service_and_native_snapshot_use_same_resources(resource_db, monkeypatch):
    from genshin_app.equipment.service import result_bytes, run_plan_calc
    plan = confirm(resource_plan(), resource_db)
    before = copy.deepcopy(plan)
    data = run_plan_calc(plan, resource_db)
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("late database access"))
    formula = sheets(build_formula_workbook(data))
    native = sheets(result_bytes(data["result"], data["result_receipt"], "xlsx"))
    assert "动作资源来源" in formula and "动作资源来源" in native
    assert data["result"]["rotation_check"]["resolved_rotation_model"]["events"][0]["amount"] == 60
    assert data["result"]["action_resource_compilations"][0]["compiled_values"]["energy"]["amount"] == 60
    assert data["result"]["summary"]["total_damage"] == 135
    assert plan == before
    assert "amount" not in data["plan"]["rotation_model"]["events"][0]
