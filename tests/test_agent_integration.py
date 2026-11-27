"""Real candidates, manager lifecycle and engine receipts; provider protocol only is fake."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest

from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.agent_chat.manager import AgentManager
from genshin_app.agent_chat.tools import (
    NATIVE_V2_READ_POLICIES,
    NATIVE_V2_TOOL_POLICIES,
)
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError
from genshin_app.whiteboard import read_whiteboard_result
from genshin_app.whiteboard_equipment import apply_whiteboard_equipment
from tests.test_agent_manager import Transport, finish as finish_worker, request
from tests.test_calculation_delivery import delivery_plan
from tests.test_whiteboard_review import real_package as _real_package

real_package = _real_package


def finish(manager):
    # These scripted turns run real SQLite and archive publication, so they
    # need more time than tests using only the fake candidate.
    finish_worker(manager, timeout=15)

_CALCULATE_TOOL_NAMES = frozenset({
    "gs_hash_input",
    "gs_read_context", "gs_read_skill", "gs_query_context", "gs_query_runtime",
    "gs_patch_candidate", "gs_assemble_attack", "gs_assemble_periodic",
    "gs_refresh_data_refs", "gs_compose", "gs_calculate", "gs_review_result",
    "gs_review_result_batch", "gs_record_finding", "gs_diagnose_result",
    "gs_adopt_attributes", "gs_preview_attribute_recovery", "gs_character_panel_basis", "gs_search_catalog",
    "gs_weapon_stats", "gs_reference_panel",
})


class ScriptedTransport(Transport):
    quota = 30

    def __init__(self, *args):
        super().__init__(*args)
        self.scripts = []
        self.thread_starts = 0

    def start_thread(self, tools, instructions, model):
        # This provider is a CLI transport: Manager publishes the calculate
        # workflow's complete catalogue, not the native stage-only projection.
        # Assert an independent protocol surface rather than a stale total.
        by_name = {tool["name"]: tool for tool in tools}
        assert set(by_name) == _CALCULATE_TOOL_NAMES
        assert len(by_name) == len(tools)
        assert all(
            tool["type"] == "function"
            and isinstance(tool["description"], str) and tool["description"]
            and tool["inputSchema"]["type"] == "object"
            for tool in tools
        )
        growth = by_name["gs_weapon_stats"]["inputSchema"]
        assert set(growth["properties"]) == {"weapon_id", "level", "ascension"}
        assert set(growth["required"]) == {"weapon_id", "level", "ascension"}
        assert growth["additionalProperties"] is False
        assert all(prop["type"] == "integer" for prop in growth["properties"].values())
        assert NATIVE_V2_READ_POLICIES["gs_weapon_stats"] == "pure_read"
        assert dict(NATIVE_V2_TOOL_POLICIES["gs_weapon_stats"]) == {
            "policy": "database_read", "effect": "none",
        }
        hashing = by_name["gs_hash_input"]["inputSchema"]
        assert set(hashing["properties"]) == set(hashing["required"]) == {"kind", "value"}
        assert hashing["properties"]["kind"]["enum"] == [
            "canonical_json", "direct_stellar_damage_profile", "direct_lunar_damage_profile"]
        assert NATIVE_V2_READ_POLICIES["gs_hash_input"] == "pure_read"
        assert dict(NATIVE_V2_TOOL_POLICIES["gs_hash_input"]) == {
            "policy": "domain_read", "effect": "none",
        }
        recovery = by_name["gs_preview_attribute_recovery"]["inputSchema"]
        assert recovery["type"] == "object" and recovery["additionalProperties"] is False
        assert set(recovery["required"]) == {"expected_revision"}
        assert set(recovery["properties"]) == {"expected_revision", "operations"}
        assert recovery["properties"]["expected_revision"] == {"type": "integer", "minimum": 0}
        operations = recovery["properties"]["operations"]
        assert operations["type"] == "array" and operations["minItems"] == 1 and operations["maxItems"] == 128
        assert operations["items"]["properties"]["op"]["enum"] == ["add", "replace", "remove"]
        assert NATIVE_V2_READ_POLICIES["gs_preview_attribute_recovery"] == "pure_read"
        assert dict(NATIVE_V2_TOOL_POLICIES["gs_preview_attribute_recovery"]) == {
            "policy": "domain_read", "effect": "none",
        }
        self.thread_starts += 1
        return super().start_thread(tools, instructions, model)

    def start_turn(self, thread, message, model, effort):
        self.turns += 1
        tid = f"turn-{self.turns}"
        for index, (name, args) in enumerate(self.scripts.pop(0)):
            self.emit({"method": "item/tool/call", "id": self.turns * 100 + index,
                "params": {"threadId": thread, "turnId": tid, "callId": tid + ".call." + str(index), "tool": name, "arguments": copy.deepcopy(args)}})
        self.emit({"method": "item/completed", "params": {"threadId": thread, "turnId": tid,
            "item": {"type": "agentMessage", "id": tid + ".m", "text": "工程协议回合结束，请审核真实候选"}}})
        self.emit({"method": "turn/completed", "params": {"threadId": thread,
            "turn": {"id": tid, "status": "completed"}}})
        return tid


@pytest.fixture
def manager(tmp_path):
    instance = AgentManager(tmp_path / "app-data", transport_factory=ScriptedTransport)
    instance.detect = lambda: {"candidates": [{"id": "engineering-cli", "supported": True}]}
    instance.connect("engineering-cli")
    yield instance
    instance.shutdown()


def corrected_hit(revision, multiplier):
    return [("gs_patch_candidate", {"expected_revision": revision, "reason": "独立工程倍率补正，用户动作和初始面板保持原值",
            "operations": [{"op": "replace", "path": "/manual_template/hits/0/multiplier", "value": multiplier}]}),
        ("gs_calculate", {"expected_revision": revision + 1}),
        ("gs_review_result", {"expected_revision": revision + 1, "request": {"view": "hits"}})]


def test_real_native_patch_calculate_continue_and_apply_reviewed_revision(manager):
    original = delivery_plan()
    before = copy.deepcopy(original)
    transport = manager._transport
    transport.scripts = [corrected_hit(0, 2), corrected_hit(1, 3)]
    sid = manager.start(request(document=original))["session_id"]
    finish(manager)
    assert isinstance(manager._candidates[sid], CandidateSession)
    first = manager.candidate(sid)
    assert first["revision"] == 1 and first["ready"] and first["summary"]["total_damage"] == pytest.approx(90)
    manager.send(sid, "在冻结输入上继续检查剩余倍率", "continue-0001")
    finish(manager)
    second = manager.candidate(sid)
    assert second["revision"] == 2 and second["ready"] and second["summary"]["total_damage"] == pytest.approx(135)
    assert all(reply["code"] == 0 for _, reply in transport.replies), transport.replies
    assert transport.turns == 2 and transport.thread_starts == 1
    assert original == before
    with pytest.raises(PlanError) as stale:
        manager.accept(sid, "native", original, 1, first["receipt_fingerprint"])
    assert stale.value.reason == "agent_stale_revision"
    accepted = manager.accept(sid, "native", original, 2, second["receipt_fingerprint"])
    assert accepted["plan"]["manual_template"]["hits"][0]["multiplier"] == 3
    assert accepted["plan"]["manual_template"]["characters"] == original["manual_template"]["characters"]
    assert manager.poll(sid)["state"] == "completed"


def test_whiteboard_delivery_survives_manager_forget_and_is_valid_recalculation_baseline(manager, real_package):
    board, filled, _, db = real_package
    manager.db_path = db
    # All executable fields are submitted through the public patch tool; identity
    # and revision remain owned by CandidateSession created in manager.start.
    ignored = {"kind", "plan_version", "plan_id", "revision", "resolution", "resolved_template"}
    operations = [{"op": "add", "path": "/" + key, "value": value} for key, value in filled.items() if key not in ignored]
    manager._transport.scripts = [[
        ("gs_patch_candidate", {"expected_revision": 0, "reason": "补齐工程白板的未知条件和计算事件", "operations": operations}),
        ("gs_calculate", {"expected_revision": 1}),
        ("gs_review_result", {"expected_revision": 1, "request": {"view": "hits"}})]]
    sid = manager.start(request(kind="whiteboard", document=board))["session_id"]
    finish(manager)
    assert all(reply["code"] == 0 for _, reply in manager._transport.replies), manager._transport.replies
    summary = manager.candidate(sid)
    assert summary["ready"] and any(issue["code"] == "calculation_energy_check" for issue in summary["issues"])
    accepted = manager.accept(sid, "whiteboard", board, 1, summary["receipt_fingerprint"])
    result_path = Path(accepted["result_path"])
    assert result_path.parent == manager.data_dir / "agent_deliveries"
    assert manager.forget(sid) == {"deleted": True}
    assert not (manager.root / sid).exists() and result_path.is_file()
    assert manager.sessions()["sessions"] == []
    actual = read_whiteboard_result(board, result_path)
    assert actual["calculated"]["result"]["hits"][0]["expected"] == pytest.approx(82.4625)
    changed = copy.deepcopy(board)
    changed["team"][0]["panel"]["atk"] = 200
    recalculated = apply_whiteboard_equipment(changed, board, result_path, db_path=db)
    assert recalculated["state"] == "ready", recalculated.get("missing_inputs")
    assert recalculated["calculated"]["result"]["hits"][0]["expected"] == pytest.approx(164.925)
    assert recalculated["plan"]["rotation_model"]["actions"] == actual["calculated"]["plan"]["rotation_model"]["actions"]


def test_restored_manager_reads_actual_candidate_and_exact_receipt_without_starting_cli(manager):
    original = delivery_plan()
    manager._transport.scripts = [corrected_hit(0, 2)]
    sid = manager.start(request(document=original))["session_id"]
    finish(manager)
    prior = manager.candidate(sid)
    native_fingerprint = fingerprint(input_view(manager._candidates[sid].candidate))
    manager.shutdown()
    restored = AgentManager(manager.data_dir)
    try:
        assert restored.sessions()["sessions"][0]["session_id"] == sid
        assert restored._worker is None and restored._transport is None
        loaded = restored.candidate(sid)
        assert loaded["ready"] and loaded["receipt_fingerprint"] == prior["receipt_fingerprint"]
        assert loaded["diff"] == prior["diff"] and loaded["revision"] == 1
        assert restored.candidate_read(sid, "candidate", "/manual_template/hits/0/multiplier")["value"] == 2
        assert restored.candidate_read(sid, "baseline", "/manual_template/hits/0/multiplier")["value"] == 1
        accepted = restored.accept(sid, "native", original, 1, loaded["receipt_fingerprint"])
        assert fingerprint(input_view(accepted["plan"])) == native_fingerprint
        assert restored._transport is None and restored._worker is None
    finally:
        restored.shutdown()


def test_failed_tool_preserves_candidate_and_manager_records_actual_failure(manager):
    manager._transport.scripts = [[
        ("gs_patch_candidate", {"expected_revision": 0, "reason": "文档身份保持稳定", "operations": [
            {"op": "replace", "path": "/plan_id", "value": "different-document"}]}),
        ("gs_calculate", {"expected_revision": 0})]]
    sid = manager.start(request(document=delivery_plan()))["session_id"]
    finish(manager)
    replies = manager._transport.replies
    assert replies[0][1]["reason"] == "agent_patch_forbidden" and replies[1][1]["code"] == 0
    view = manager.poll(sid)
    assert view["candidate"]["revision"] == 0 and view["candidate"]["summary"]["total_damage"] == pytest.approx(45)
    assert any(e["type"] == "tool" and e.get("state") == "failed" for e in view["events"])
    assert manager.candidate_read(sid, "candidate", "/manual_template/characters/0/stats/atk")["value"] == 100


def test_native_requested_panel_change_apply_then_continue_same_chat(manager):
    original = delivery_plan()
    manager._transport.scripts = [[
        ("gs_patch_candidate", {"expected_revision": 0, "reason": "用户要求攻击力改为200", "operations": [
            {"op": "replace", "path": "/manual_template/characters/0/stats/atk", "value": 200}]}),
        ("gs_calculate", {"expected_revision": 1})], corrected_hit(1, 2)]
    sid = manager.start(request(document=original, message="攻击力改为200后重新计算"))["session_id"]
    finish(manager)
    first = manager.candidate(sid)
    assert first["ready"] and first["summary"]["total_damage"] == pytest.approx(90)
    applied = manager.accept(sid, "native", original, 1, first["receipt_fingerprint"])["plan"]
    manager.send(sid, "继续补正倍率", "native-applied-continue", input_view(applied))
    finish(manager)
    second = manager.candidate(sid)
    assert second["summary"]["total_damage"] == pytest.approx(180)
    assert manager.candidate_read(sid, "baseline", "/manual_template/characters/0/stats/atk")["value"] == 200
    manager.accept(sid, "native", applied, 2, second["receipt_fingerprint"])
    outside = copy.deepcopy(applied)
    outside["manual_template"]["characters"][0]["stats"]["atk"] = 300
    with pytest.raises(PlanError) as conflict:
        manager.send(sid, "拒绝覆盖聊天外改动", "native-outside-change", outside)
    assert conflict.value.reason == "agent_document_changed"
    assert manager._transport.thread_starts == 1 and manager._transport.turns == 2


def test_whiteboard_requested_axis_change_apply_then_continue_same_chat(manager, real_package):
    board, plan, _, db = real_package
    manager.db_path = db
    revised = plan["extensions"]["whiteboard_handoff"]["board"]
    revised["team"][0]["panel"]["atk"] = 200
    revised["actions"][0]["start"] = 1.5
    plan["manual_template"]["characters"][0]["stats"]["atk"] = 200
    plan["rotation_model"]["actions"][0].update(start=1.5, occupation_end=1.6, switch_allowed_at=1.6)
    plan["manual_template"]["hits"][0]["t"] = 1.7
    ignored = {"kind", "plan_version", "plan_id", "revision", "resolution", "resolved_template"}
    operations = [{"op": "add", "path": "/" + key, "value": value} for key, value in plan.items() if key not in ignored]
    manager._transport.scripts = [[
        ("gs_patch_candidate", {"expected_revision": 0, "reason": "用户要求调整面板和战技时点", "operations": operations}),
        ("gs_calculate", {"expected_revision": 1})], corrected_hit(1, 2)]
    sid = manager.start(request(kind="whiteboard", document=board, message="把战技移到1.5秒，攻击力改成200"))["session_id"]
    finish(manager)
    first = manager.candidate(sid)
    assert first["ready"], manager._transport.replies
    applied = manager.accept(sid, "whiteboard", board, 1, first["receipt_fingerprint"])
    assert applied["board"]["actions"][0]["start"] == 1.5
    read_whiteboard_result(applied["board"], applied["result_path"])
    with pytest.raises(PlanError):
        read_whiteboard_result(board, applied["result_path"])
    manager.send(sid, "继续检查倍率", "whiteboard-applied-continue", applied["board"])
    finish(manager)
    second = manager.candidate(sid)
    assert second["ready"] and second["revision"] == 2
    second_applied = manager.accept(sid, "whiteboard", applied["board"], 2, second["receipt_fingerprint"])
    assert second_applied["board"] == applied["board"]
    assert manager._transport.thread_starts == 1 and manager._transport.turns == 2
    assert all(reply["code"] == 0 for _, reply in manager._transport.replies)
