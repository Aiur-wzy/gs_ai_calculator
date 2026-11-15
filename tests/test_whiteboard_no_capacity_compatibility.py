"""Legacy and v3 whiteboards without typed intents keep the same delivery.

The comparison deliberately ignores timestamps and receipt hashes.  Those values
identify the two different native inputs; the business result and workbook cells
must remain the same and must not acquire capacity-only sections.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.calculation_completion import require_calculation_complete
from genshin_app.equipment import service
from genshin_app.equipment.delivery import (
    build_calculation_delivery,
    validate_delivery_calculated,
)
from genshin_app.equipment.files import json_bytes
from genshin_app.whiteboard import (
    legacy_board_fingerprint,
    load_whiteboard,
    make_handoff,
    read_whiteboard_result,
)
from tests.test_whiteboard_review import real_package as _real_package

real_package = _real_package


@pytest.fixture
def complete_package(real_package):
    """Keep the shared diagnostic fixture; build a separate complete scenario.

    The legacy comparison predates reporting-window input, so construct the
    equivalent current scenario without that optional intent before handoff.
    Zero CD/cost are explicit engineering facts, not missing/unknown values.
    """
    source_board, source_plan, _, db = real_package
    board, plan = copy.deepcopy(source_board), copy.deepcopy(source_plan)
    board.pop("damage_report")
    plan["rotation_model"].pop("damage_report")
    plan["extensions"] = copy.deepcopy(make_handoff(board)["extensions"])
    plan["extensions"]["whiteboard_handoff"]["completion_notes"] = [
        "补齐未知面板、占用和工程敌人条件；没有移动用户动作"]
    rotation = plan["rotation_model"]
    ability = rotation["abilities"][0]
    ability.update(
        cooldown={"group": "engineering.e", "duration": 0,
                  "start": "action_start", "charges": 1, "recovery": "sequential"},
        energy={"required": 0, "amount": 0})
    rotation["initial_cooldowns"] = [
        {"character": "a", "group": "engineering.e", "ready_in": []}]
    evidence = {"status": "assumed", "source": "独立工程输入，不宣称游戏事实"}
    rotation["events"] = [{
        "id": "engineering.cost", "type": "energy_spend", "t": 1, "order": 1,
        "character": "a", "action_id": "e", "required": 0, "amount": 0,
        "repeat": {"mode": "once"}, **evidence,
    }]
    rotation["action_hit_links"] = [{"action_id": "e", "hit_id": "h"}]
    calculated = service.run_plan_calc(plan, db, capture_display=True)
    assert require_calculation_complete(calculated)["calculation_status"] == "complete"
    check = calculated["result"]["rotation_check"]
    assert check["action_check"]["status"] == "passed"
    assert check["energy_check"]["opening_status"] == "passed"
    return board, plan, calculated, db

_CAPACITY_FIELDS = {
    "action_capacity_sources",
    "action_capacity_context",
    "action_capacity_compilations",
    "action_capacity_execution",
    "action_capacity_consumption",
    "action_capacities",
}
_ACTION_ADMISSION_FIELDS = {
    "action_admission_sources",
    "action_admission_context",
    "action_admission_compilations",
    "action_admission_execution",
    "action_admission_execution_input",
}
_VOLATILE = re.compile(
    r"(?:[0-9a-f]{64})|(?:\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d))"
)
_CAPACITY_RUNTIME_TEXT = re.compile(
    r"(?:action_capacity|action_admission_(?!intents\b|context_state\b|proposals\b)|"
    r"capacity_(?!intents\b|context_state\b))",
    re.IGNORECASE,
)
_NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _legacy_plan(board, plan):
    assert "damage_report" not in board
    value = copy.deepcopy(plan)
    saved = copy.deepcopy(board)
    saved["schema_version"] = 1
    saved.pop("capacity_intents")
    saved.pop("action_admission_intents")
    handoff = value["extensions"]["whiteboard_handoff"]
    handoff.update(
        schema_version=1,
        board=saved,
        board_fingerprint=legacy_board_fingerprint(board),
    )
    handoff.pop("capacity_intents")
    handoff.pop("capacity_context_state")
    handoff.pop("action_admission_intents")
    handoff.pop("action_admission_context_state")
    handoff.pop("action_admission_proposals")
    return value


def _assert_no_capacity(value):
    if isinstance(value, dict):
        assert not ((_CAPACITY_FIELDS | _ACTION_ADMISSION_FIELDS) & value.keys())
        for child in value.values():
            _assert_no_capacity(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_capacity(child)


def _normalize_scalar(value):
    if isinstance(value, str):
        return _VOLATILE.sub("<volatile>", value)
    return value


def _normalize_tree(value):
    if isinstance(value, dict):
        return {key: _normalize_tree(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_normalize_tree(child) for child in value]
    return _normalize_scalar(value)


def _shared_strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.itertext()) for node in root.findall("s:si", _NS)]


def _workbook_cells(content, *, normalize=True):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        strings = _shared_strings(archive)
        relations = {
            node.attrib["Id"]: node.attrib["Target"]
            for node in ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        }
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = []
        for sheet in workbook.findall("s:sheets/s:sheet", _NS):
            name = sheet.attrib["name"]
            target = relations[sheet.attrib[_REL_ID]].lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            root = ET.fromstring(archive.read(target))
            cells = []
            for cell in root.findall(".//s:c", _NS):
                kind = cell.attrib.get("t")
                formula = cell.findtext("s:f", default="", namespaces=_NS)
                if kind == "inlineStr":
                    inline = cell.find("s:is", _NS)
                    value = "" if inline is None else "".join(inline.itertext())
                else:
                    value = cell.findtext("s:v", default="", namespaces=_NS)
                    if kind == "s" and value:
                        value = strings[int(value)]
                cells.append((cell.attrib["r"], formula,
                              _normalize_scalar(value) if normalize else value))
            sheets.append((name, cells))
        return sheets


def _business(calculated):
    result = copy.deepcopy(calculated["result"])
    # This proof binds the exact native-plan input.  A v1 and a v3 handoff are
    # deliberately different inputs even when their gameplay result is the
    # same, so the proof is audit identity rather than business output.
    result.pop("calculation_ownership_proof", None)
    return _normalize_tree({
        "result": result,
        "equipment_summary": calculated["equipment_summary"],
        "presentation_issues": calculated["presentation_issues"],
        "display_context": calculated["display_context"],
    })


def _column_a_rows(cells):
    return {
        int(re.search(r"\d+", coordinate).group()): value
        for coordinate, _, value in cells
        if coordinate.startswith("A")
    }


def _prefix_before_raw_plan(cells):
    labels = _column_a_rows(cells)
    rows = [row for row, value in labels.items() if value == "plan"]
    assert len(rows) == 1, rows
    return [
        cell for cell in cells
        if int(re.search(r"\d+", cell[0]).group()) < rows[0]
    ]


def _full_source_documents(content):
    """Read every original source chunk and verify the independently indexed SHA."""
    sheets = dict(_workbook_cells(content, normalize=False))

    def rows(cells):
        result = {}
        for coordinate, formula, value in cells:
            assert not formula
            column, number = re.fullmatch(r"([A-Z]+)(\d+)", coordinate).groups()
            result.setdefault(int(number), {})[column] = value
        return result

    index = {row.get("A"): row for row in rows(sheets["来源与范围"]).values()}
    grouped = {}
    for number, row in rows(sheets["完整来源JSON"]).items():
        if number == 1:
            continue
        grouped.setdefault(row["A"], []).append((int(row["B"]), row["C"]))
    sources = {}
    for key, parts in grouped.items():
        ordered = sorted(parts)
        assert [sequence for sequence, _ in ordered] == list(range(1, len(parts) + 1))
        text = "".join(chunk for _, chunk in ordered)
        raw = text.encode("utf-8")
        assert hashlib.sha256(raw).hexdigest() == index[key]["C"]
        assert len(raw) == int(index[key]["D"])
        value = json.loads(text)
        assert json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False) == text
        sources[key] = value
    assert "plan" in sources
    return sources


def test_v1_and_v3_empty_typed_intent_delivery_matrix_is_business_equivalent(complete_package, tmp_path):
    board, plan, _, db = complete_package
    assert board["schema_version"] == 3
    assert board["capacity_intents"] == []
    assert board["action_admission_intents"] == []
    legacy_board = copy.deepcopy(board)
    legacy_board["schema_version"] = 1
    legacy_board.pop("capacity_intents")
    legacy_board.pop("action_admission_intents")
    legacy_board_path = tmp_path / "legacy.whiteboard.json"
    legacy_board_path.write_bytes(json_bytes(legacy_board))
    upgraded_legacy_board = load_whiteboard(legacy_board_path)["board"]
    assert upgraded_legacy_board == board
    current = service.run_plan_calc(copy.deepcopy(plan), db, capture_display=True)
    legacy = service.run_plan_calc(_legacy_plan(board, plan), db, capture_display=True)

    # In-memory calculated results retain the same gameplay output.  The plan
    # and receipt identities legitimately differ because one handoff is v1.
    validate_delivery_calculated(current)
    validate_delivery_calculated(legacy)
    assert _business(current) == _business(legacy)
    _assert_no_capacity(current)
    _assert_no_capacity(legacy)

    current_result = json.loads(service.result_bytes(
        current["result"], current["result_receipt"], "json",
        equipment_summary=current["equipment_summary"],
    ))
    legacy_result = json.loads(service.result_bytes(
        legacy["result"], legacy["result_receipt"], "json",
        equipment_summary=legacy["equipment_summary"],
    ))
    current_payload = copy.deepcopy(current_result["result"])
    legacy_payload = copy.deepcopy(legacy_result["result"])
    current_payload.pop("calculation_ownership_proof", None)
    legacy_payload.pop("calculation_ownership_proof", None)
    # Explanation execution_binding and its document fingerprint also bind
    # the deliberately different handoff inputs. Keep every numerical and
    # source value in the comparison, applying the same established volatile
    # hash/timestamp rule used for the complete in-memory business view.
    assert _normalize_tree(current_payload) == _normalize_tree(legacy_payload)
    for value in (current, legacy):
        explanation = value["result"]["damage_explanations"]
        assert explanation["status"] == "complete"
        assert explanation["execution_binding"] == {
            key: value["report"][key]
            for key in ("input_fingerprint", "output_fingerprint", "source_fingerprint")
        }
        assert all(hit["explanation_ref"] in explanation["events"]
                   for hit in value["result"]["hits"])
    assert _normalize_tree(current_result["equipment_summary"]) == _normalize_tree(
        legacy_result["equipment_summary"]
    )
    _assert_no_capacity(current_result)
    _assert_no_capacity(legacy_result)

    built_current = build_calculation_delivery(current, input_file_sha="1" * 64)
    built_legacy = build_calculation_delivery(legacy, input_file_sha="2" * 64)
    assert set(built_current["artifacts"]) == set(built_legacy["artifacts"]) == {
        "native_plan", "calculated", "result_workbook", "formula_workbook",
    }
    for label, built in (("v3", built_current), ("v1", built_legacy)):
        assert built["metadata"]["calculation_status"] == "complete"
        assert built["diagnostic"] is False
        for artifact_role, content in built["artifacts"].items():
            artifact_path = tmp_path / f"{label}.{artifact_role}"
            artifact_path.write_bytes(content)
            assert artifact_path.read_bytes() == content
        package = json.loads(built["artifacts"]["calculated"])
        assert _business(package["calculated"]) == _business(current)
        _assert_no_capacity(package)
        path = tmp_path / f"{label}.calculated.json"
        path.write_bytes(json_bytes(package))
        returned = read_whiteboard_result(
            upgraded_legacy_board if label == "v1" else board, path
        )
        assert _business(returned["calculated"]) == _business(current)

    for role in ("result_workbook", "formula_workbook"):
        current_cells = _workbook_cells(built_current["artifacts"][role])
        legacy_cells = _workbook_cells(built_legacy["artifacts"][role])
        if role == "formula_workbook":
            current_sources = _full_source_documents(built_current["artifacts"][role])
            legacy_sources = _full_source_documents(built_legacy["artifacts"][role])
            assert current_sources["plan"] == current["plan"]
            assert legacy_sources["plan"] == legacy["plan"]
            projected_current = copy.deepcopy(current_sources)
            projected_current["plan"] = _legacy_plan(board, current_sources["plan"])
            assert _normalize_tree(projected_current) == _normalize_tree(legacy_sources)
        assert [name for name, _ in current_cells] == [name for name, _ in legacy_cells]
        for (name, current_sheet), (_, legacy_sheet) in zip(
            current_cells, legacy_cells, strict=True
        ):
            for sheet in (current_sheet, legacy_sheet):
                for coordinate, formula, value in sheet:
                    assert not _CAPACITY_RUNTIME_TEXT.search(f"{formula}\n{value}"), (
                        role, name, coordinate, value
                    )
            if role == "formula_workbook" and name == "来源与范围":
                # Compare the complete support/assumption/equipment/mechanics
                # section, then normalize only the deliberate v3 empty-intent
                # handoff additions inside the embedded raw native plan.
                assert _prefix_before_raw_plan(current_sheet) == _prefix_before_raw_plan(
                    legacy_sheet
                )
                continue
            if role == "formula_workbook" and name == "完整来源JSON":
                # Raw chunks and indexed digests were verified above, including
                # the complete source plan and exact legacy metadata projection.
                continue
            assert len(current_sheet) == len(legacy_sheet), (
                role, name, len(current_sheet), len(legacy_sheet)
            )
            different = [
                (left, right)
                for left, right in zip(current_sheet, legacy_sheet, strict=True)
                if left != right
            ]
            assert not different, (role, name, different[:10])
        names = {name for name, _ in current_cells}
        assert "技能次数来源" not in names and "技能次数消费" not in names
