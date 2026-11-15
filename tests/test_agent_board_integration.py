"""Real whiteboard candidates and manager, using a local scripted provider only."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.agent_chat.board_candidate import BoardCandidateSession
from genshin_app.agent_chat.manager import (
    BUILD_INSTRUCTIONS,
    INSTRUCTIONS,
    AgentManager,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi
from genshin_app.whiteboard import load_whiteboard, new_whiteboard, save_whiteboard
from genshin_db import queries
from tests.test_agent_integration import ScriptedTransport
from tests.test_agent_manager import Transport, finish, request


class BuildTransport(ScriptedTransport):
    def start_thread(self, tools, instructions, model):
        self.thread_starts += 1
        self.tool_names = {tool["name"] for tool in tools}
        self.instructions = instructions
        return Transport.start_thread(self, tools, instructions, model)


@pytest.fixture
def manager(tmp_path, monkeypatch):
    # Engineering catalog only; basis remains honestly unknown without a DB.
    monkeypatch.setattr(queries, "get_character_detail", lambda *a, **kw: {"character": {
        "id": 1, "name": "工程角色", "weapon_type": "WEAPON_SWORD_ONE_HAND"}})
    value = AgentManager(tmp_path / "app-data", transport_factory=BuildTransport)
    value.detect = lambda: {"candidates": [{"id": "engineering", "supported": True}]}
    value.connect("engineering")
    yield value
    value.shutdown()


def add_role():
    return ("gs_add_character", {"expected_revision": 0, "reason": "用户要求先只加入一名角色", "db_character_id": 1,
                                  "actor_id": "role"})


def test_calculation_agent_instructions_expose_offline_elemental_research_handoff():
    assert "elemental_counter_verification_tasks" in INSTRUCTIONS
    assert "verification_task(s)" in INSTRUCTIONS
    assert "本受限会话没有互联网" in INSTRUCTIONS
    assert "保持pending" in INSTRUCTIONS and "research_report" in INSTRUCTIONS
    assert "不能代替confirmed verification取得执行权" in INSTRUCTIONS


def patch(revision, operations):
    return ("gs_patch_whiteboard", {"expected_revision": revision, "reason": "按用户本轮请求逐步编辑白板",
                                    "operations": operations})


def add_action():
    return patch(1, [{"op": "add", "path": "/actions/-", "value": {
        "id": "skill-1", "character": "role", "action_type_id": "action.elemental_skill", "label": "释放战技",
        "start": 1, "variant": "", "notes": "动作草稿，机制仍待计算完善"}}])


def start(manager, board, scripts):
    manager._transport.scripts = scripts
    sid = manager.start(request(kind="whiteboard", workflow="build", document=board))["session_id"]
    finish(manager)
    return sid


def test_empty_board_role_only_apply_then_same_chat_add_action_and_restore(manager, tmp_path):
    board = new_whiteboard()
    original = copy.deepcopy(board)
    sid = start(manager, board, [[add_role()], [add_action()]])
    candidate = manager._candidates[sid]
    assert isinstance(candidate, BoardCandidateSession)
    assert manager._transport.instructions == BUILD_INSTRUCTIONS != INSTRUCTIONS
    assert "gs_add_character" in manager._transport.tool_names and "gs_calculate" not in manager._transport.tool_names
    first = manager.candidate(sid)
    assert first["workflow"] == "build" and first["ready"] and not first["has_calculation"]
    assert any(item["code"] == "empty_actions" for item in first["issues"])
    accepted = manager.accept(sid, "whiteboard", board, 1, first["receipt_fingerprint"])
    applied = accepted["board"]
    assert applied["team"][0]["name"] == "工程角色" and applied["actions"] == []
    assert applied["team"][0]["panel"]["atk"] is None
    assert "result_path" not in accepted and candidate.document == original
    manager.send(sid, "再加一个战技动作", "step-0002", applied)
    finish(manager)
    second = manager.candidate(sid)
    assert second["revision"] == 2 and second["ready"] and not second["has_calculation"]
    assert candidate.document == applied
    assert {row["path"] for row in second["diff"]} == {"/actions"}
    assert all(result["code"] == 0 for _, result in manager._transport.replies)
    assert manager._transport.thread_starts == 1 and manager._transport.turns == 2
    assert manager.send(sid, "再加一个战技动作", "step-0002", applied)["session_id"] == sid
    assert manager._transport.turns == 2
    with pytest.raises(PlanError, match="同一请求"):
        manager.send(sid, "另一条消息", "step-0002", applied)
    final = manager.accept(sid, "whiteboard", applied, 2, second["receipt_fingerprint"])["board"]
    saved = save_whiteboard(tmp_path / "built.whiteboard.json", final)
    assert load_whiteboard(saved["path"])["board"] == final
    assert board == original
    manager.shutdown()
    restored = AgentManager(manager.data_dir)
    try:
        assert restored.poll(sid)["workflow"] == "build"
        assert restored.sessions()["sessions"][0]["workflow"] == "build"
        assert restored.candidate(sid)["receipt_fingerprint"] == second["receipt_fingerprint"]
        assert restored._transport is None
    finally:
        restored.shutdown()


def test_unapplied_chat_continues_candidate_and_stale_receipt_never_applies(manager):
    board = new_whiteboard()
    sid = start(manager, board, [[add_role()], [add_action()]])
    first = manager.candidate(sid)
    manager.send(sid, "先不要应用，继续补一个动作", "step-0002", board)
    finish(manager)
    candidate = manager._candidates[sid]
    assert candidate.document == board and len(candidate.candidate["actions"]) == 1
    with pytest.raises(PlanError) as error:
        manager.accept(sid, "whiteboard", board, 1, first["receipt_fingerprint"])
    assert error.value.reason == "agent_stale_revision"
    with pytest.raises(PlanError) as error:
        manager.accept(sid, "whiteboard", board, 2, first["receipt_fingerprint"])
    assert error.value.reason == "agent_stale_receipt"


def test_outside_edits_or_missing_current_board_block_before_new_turn(manager):
    board = new_whiteboard()
    sid = start(manager, board, [[add_role()]])
    changed = copy.deepcopy(board)
    changed["title"] = "用户手动改了标题"
    for document, reason in ((None, "agent_document_required"), (changed, "agent_document_changed")):
        with pytest.raises(PlanError) as error:
            manager.send(sid, "继续", "step-missing" if document is None else "step-changed", document)
        assert error.value.reason == reason
    assert manager._transport.turns == 1 and manager._active_id is None


def test_db_change_blocks_build_send_and_accept(manager, monkeypatch):
    board = new_whiteboard()
    sid = start(manager, board, [[add_role()]])
    view = manager.candidate(sid)
    monkeypatch.setattr(manager._candidates[sid], "_db_snapshot", lambda: {"state": "updated"})
    assert not manager.candidate(sid)["can_accept"]
    with pytest.raises(PlanError) as error:
        manager.send(sid, "继续", "step-dbchange", board)
    assert error.value.reason == "agent_database_changed"
    with pytest.raises(PlanError) as error:
        manager.accept(sid, "whiteboard", board, 1, view["receipt_fingerprint"])
    assert error.value.reason == "agent_database_changed"
    assert manager._transport.turns == 1


def test_build_native_tool_rejection_does_not_fabricate_calculation(manager):
    board = new_whiteboard()
    sid = start(manager, board, [[("gs_calculate", {"expected_revision": 0})]])
    assert manager._transport.replies[0][1]["reason"] == "agent_tool_forbidden"
    view = manager.candidate(sid)
    assert not view["has_calculation"] and not view["can_accept"]
    assert manager._candidates[sid].candidate == board


def test_gui_bridge_passes_current_document_for_same_chat_build(manager):
    board = new_whiteboard()
    sid = start(manager, board, [[add_role()], [add_action()]])
    view = manager.candidate(sid)
    applied = manager.accept(sid, "whiteboard", board, 1, view["receipt_fingerprint"])["board"]
    api = GuiApi(manager.data_dir)
    api._agent_manager = manager
    assert api.agent_send(sid, "继续加入动作", "bridge-0002", applied)["code"] == 0
    finish(manager)
    assert api.agent_poll(sid)["data"]["workflow"] == "build"
    assert api.agent_candidate(sid)["data"]["revision"] == 2


def test_history_workflow_disagreement_rejected(manager):
    sid = start(manager, new_whiteboard(), [[add_role()]])
    manager.shutdown()
    path = manager.root / sid / "chat.json"
    chat = json.loads(path.read_text(encoding="utf-8"))
    chat["workflow"] = "calculate"
    path.write_text(json.dumps(chat), encoding="utf-8")
    restored = AgentManager(manager.data_dir)
    try:
        with pytest.raises(PlanError) as error:
            restored.candidate(sid)
        assert error.value.reason == "agent_checkpoint_invalid"
        assert restored._transport is None
    finally:
        restored.shutdown()


@pytest.mark.parametrize("kind,workflow", [("native", "build"), ("whiteboard", None), ("whiteboard", "unknown")])
def test_invalid_workflow_never_starts_provider(manager, kind, workflow):
    with pytest.raises(PlanError) as error:
        manager.start(request(kind=kind, workflow=workflow, document=new_whiteboard()))
    assert error.value.reason == "agent_invalid_input"
    assert manager._transport.turns == 0
