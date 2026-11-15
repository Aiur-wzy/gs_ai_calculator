"""Input fidelity and rendered OOXML checks; no game evidence or real database."""
from __future__ import annotations

import copy
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.action_capacity_reporting import capacity_sheets
from genshin_app.equipment.action_capacity_schema import SLOTS
from genshin_app.equipment.schema import PlanError
from genshin_app.export import _write_xlsx
from genshin_app.rotation_check import check_sheets
from genshin_app.whiteboard import (
    board_fingerprint,
    default_panel,
    make_handoff,
    new_whiteboard,
    validate_result_intent,
    validate_whiteboard,
)


def board():
    value = new_whiteboard()
    value["team"] = [{"id": "actor", "db_character_id": 1, "name": "工程角色", "level": 90, "constellation": 1,
                      "weapon": {"weapon_id": 11, "level": 1, "ascension": 0, "refinement": 2, "name": "工程武器"},
                      "artifact_sets": [], "panel": default_panel(), "notes": ""}]
    value["actions"] = [{"id": "e", "character": "actor", "action_type_id": "action.elemental_skill", "label": "战技", "start": 0, "variant": "tap", "notes": ""}]
    return value


def test_explicit_equipment_fields_survive_no_passive_handoff_and_are_fingerprinted():
    value = board()
    actor = value["team"][0]
    actor.update(ascension=6, talent_base_levels={"normal": 10, "skill": 10, "burst": 10}, artifact_presence=dict.fromkeys(SLOTS, "empty"))
    plan = make_handoff(value)
    gear = plan["equipment_inputs"]["actor"]
    assert gear["weapon"]["refinement"] == 2 and gear["weapon"]["refinements"] == []
    assert gear["artifact_presence"] == actor["artifact_presence"]
    assert gear["progression"]["constellation"] == 1
    assert gear["progression"]["talent_base_levels"] == actor["talent_base_levels"]
    assert gear["progression"]["ascension"] == 6
    baseline = board_fingerprint(value)
    for key, replacement in (("artifact_presence", dict.fromkeys(SLOTS, "unknown")), ("talent_base_levels", {"skill": 9}), ("ascension", 5), ("constellation", 0)):
        changed = copy.deepcopy(value)
        changed["team"][0][key] = replacement
        assert board_fingerprint(changed) != baseline


def test_legacy_unknown_equipment_is_not_promoted_to_empty_or_r1():
    value = board()
    value["team"][0]["weapon"]["refinement"] = None
    gear = make_handoff(value)["equipment_inputs"]["actor"]
    assert "artifact_presence" not in gear and "refinement" not in gear["weapon"]
    assert "talent_base_levels" not in gear["progression"]
    assert gear["progression"]["ascension"] is None


@pytest.mark.parametrize("field,value", [("ascension", True), ("ascension", 7), ("talent_base_levels", {"skill": 13}), ("talent_base_levels", {"skill": 1.0}), ("talent_base_levels", {"other": 10}), ("artifact_presence", {}), ("artifact_presence", dict.fromkeys(SLOTS, "no"))])
def test_invalid_new_whiteboard_fields_rejected(field, value):
    value_board = board()
    value_board["team"][0][field] = value
    with pytest.raises(PlanError):
        validate_whiteboard(value_board)


def test_empty_slots_cannot_coexist_with_selected_set():
    value = board()
    value["team"][0].update(artifact_presence=dict.fromkeys(SLOTS, "empty"), artifact_sets=[{"set_id": 9, "name": "工程套装", "count": 2}])
    with pytest.raises(PlanError, match="冲突"):
        make_handoff(value)


@pytest.mark.parametrize("field", ["refinement", "constellation", "artifact_presence", "talent_base_levels"])
def test_return_cannot_drop_or_mutate_explicit_inputs_before_other_checks(field):
    value = board()
    value["team"][0].update(ascension=6, talent_base_levels={"skill": 10}, artifact_presence=dict.fromkeys(SLOTS, "empty"))
    plan = make_handoff(value)
    gear = plan["equipment_inputs"]["actor"]
    if field == "refinement":
        del gear["weapon"]["refinement"]
    elif field == "artifact_presence":
        gear[field][SLOTS[0]] = "unknown"
    else:
        del gear["progression"][field]
    with pytest.raises(PlanError) as exc:
        validate_result_intent(value, plan)
    assert exc.value.reason == "whiteboard_intent_mismatch"
    assert field in exc.value.details["path"]


def reporting_result():
    return {"action_capacity_compilations": [{"id": "cap", "status": "ready", "issues": [], "source_fingerprint": "a" * 64,
        "projection": {"capacity": 2, "actual": {"selection": {"constellation": 1}},
        "profile": {"model_record": {"id": "model", "revision": 1, "payload": {"claim": {"base": {"value": 1}}}}},
        "pool": {"actor": "a", "group": "skill", "initial": {"ready_in": [4]}, "members": [{"cooldown": {"duration": 10, "start": "action_start", "recovery": "sequential"}}]}}}],
        "rotation_check": {"action_capacity_consumption": {"planned_actions": 3, "max_cycles_planned_actions": 3, "unreported_actions": 0, "receipts": [
            {"t": 0, "character": "a", "action_id": "e1", "ability_id": "e", "status": "committed", "capacity": 2, "before": 1, "after": 0, "ready_at": 14, "next_ready_at": 4, "queue_before": [4], "queue_after": [4, 14], "binding_id": "cap", "run_id": "r1"},
            {"t": 1, "character": "a", "action_id": "e2", "ability_id": "e", "status": "rejected", "capacity": 2, "before": 0, "after": 0, "ready_at": None, "next_ready_at": 4, "queue_before": [4, 14], "queue_after": [4, 14], "binding_id": "cap", "run_id": "r2", "reason": "cooldown_unavailable"}]},
            "event_log": [{"type": "action", "t": 2, "character": "a", "action_id": "e3", "ability_id": "e", "capacity_admission": {"status": "not_verified", "binding_id": "cap"}}]}}


def test_capacity_workbook_values_preserve_actual_numbers_and_unknowns():
    result = reporting_result()
    sheets = capacity_sheets(result, {"characters": {"a": "工程角色"}})
    sources, consumption = [rows for _, rows in sheets]
    assert sources[2][2:6] == ["工程角色", 2, 1, 1]
    assert sources[2][8:10] == ["[4]", 1]
    assert consumption[2][5:10] == [2, 1, 0, 14, 4]
    assert consumption[3][4:10] == ["次数不足，拒绝施放", 2, 0, 0, None, 4]
    assert consumption[4][4:10] == ["后续未验证", None, None, None, None, None]
    stream = io.BytesIO()
    _write_xlsx(stream, sheets)
    with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as book:
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        xml = ET.fromstring(book.read("xl/worksheets/sheet2.xml"))
        cells = {node.attrib["r"]: node for node in xml.findall(".//s:c", ns)}
        assert cells["F3"].find("s:v", ns).text == "2"
        assert cells["G3"].find("s:v", ns).text == "1"
        assert cells["H3"].find("s:v", ns).text == "0"
        assert "App" in book.read("xl/worksheets/sheet1.xml").decode()


def test_no_capacity_signal_keeps_legacy_sheet_list_empty():
    assert capacity_sheets({"rotation_check": {"event_log": []}}) == []


def test_known_mechanical_rows_are_named_without_claiming_damage_or_unknown_authority():
    sheets = dict(check_sheets({"event_log": [{"type": key} for key in ("mechanics_hit", "mechanics_end", "unknown")]}))
    assert [row[2] for row in sheets["能量流水"][1:]] == ["机制命中处理", "动作结束处理", "待补充"]
