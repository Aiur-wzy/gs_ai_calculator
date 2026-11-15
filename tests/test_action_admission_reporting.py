"""Read-only action-admission report fidelity; no database or game authority."""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

from genshin_app.action_admission_reporting import action_admission_sheets
from genshin_app.export import _write_xlsx


def result_fixture():
    return {
        "action_admission_compilations": [{
            "id": "admission.q", "status": "ready", "actor": "a", "ability_id": "q",
            "selection": {"character_id": 1, "skill_depot_id": 2, "skill_id": 3},
            "action_type_id": "action.elemental_burst", "variant": "default",
            "model_ref": {"id": "model.q", "revision": 1}, "context": {"mode": "ordinary_open_world"},
            "issues": [], "source_fingerprint": "a" * 64,
            "compiled_values": {"cooldown": {"group": "skill:2:3", "duration": 20, "charges": 1},
                                "energy": {"required": 80, "amount": 80, "delivery": "action_start"}},
            "static_certificate": {"static_certificate_fingerprint": "b" * 64,
                "initial_cooldown": {"ready_in": []}, "field_authority": {"cooldown.duration": "fresh_upstream"},
                "declared_not_verified": ["hit_sequence"]},
        }],
        "rotation_check": {
            "action_admission_execution": {
                "planned_actions": 2, "reported_actions": 2, "unreported_actions": 0,
                "committed_actions": 1, "rejected_actions": 1, "not_committed_actions": 0,
                "not_verified_actions": 0, "receipt_count": 1,
                "receipts": [{"t": 0, "actor": "a", "action_id": "cast", "ability_id": "q",
                    "status": "committed", "energy_before": 80, "energy_after": 0,
                    "charges_before": 1, "charges_after": 0, "next_ready_at": 20,
                    "gates": {"frontline": "passed"}, "binding_id": "admission.q",
                    "model_ref": {"id": "model.q", "revision": 1}, "source_fingerprint": "a" * 64,
                    "static_certificate_fingerprint": "b" * 64, "id": "cert", "run_id": "receipt-run",
                    "parent_run_id": "parent-1"}]},
            "event_log": [
                {"type": "action", "t": 0, "run_id": "parent-1", "action_id": "cast", "ability_id": "q",
                 "character": "a", "action_admission": {"status": "committed"}},
                {"type": "action", "t": 1, "run_id": "parent-2", "action_id": "cast2", "ability_id": "q",
                 "character": "a", "action_admission": {"status": "rejected", "reason": "insufficient_energy",
                     "binding_id": "admission.q", "source_fingerprint": "a" * 64,
                     "static_certificate_fingerprint": "b" * 64}},
            ],
        },
    }


def test_action_admission_sheets_preserve_values_status_and_unknowns():
    sheets = action_admission_sheets(result_fixture(), {"characters": {"a": "工程角色"}})
    sources, execution = [rows for _, rows in sheets]
    assert sources[2][1:4] == ["已确认", "工程角色", "q"]
    assert sources[2][8] == "[]"
    assert "20 秒 / 1 次" in sources[2][9]
    assert "hit_sequence" in sources[2][12]
    assert execution[2][4:9] == ["已接纳并提交", "", "80 → 0", "1 → 0", 20]
    assert execution[3][4:7] == ["游戏门禁拒绝", "动作开始时能量不足", ""]

    stream = io.BytesIO()
    _write_xlsx(stream, sheets)
    with zipfile.ZipFile(io.BytesIO(stream.getvalue())) as book:
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        xml = ET.fromstring(book.read("xl/worksheets/sheet2.xml"))
        cells = {node.attrib["r"]: node for node in xml.findall(".//s:c", ns)}
        assert cells["I3"].find("s:v", ns).text == "20"
        assert "只读历史" in book.read("xl/worksheets/sheet1.xml").decode()


def test_no_action_admission_signal_keeps_legacy_sheet_list_empty():
    assert action_admission_sheets({"rotation_check": {"event_log": []}}) == []
