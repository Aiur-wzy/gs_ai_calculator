from __future__ import annotations

import pytest

from genshin_app.agent_chat.native_app_action_tools import REQUEST_ACTION_TOOL
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.gui.api import GuiApi
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def workspace():
    return {"document_kind": "whiteboard", "display_name": "测试白板", "current_view": "whiteboard",
        "dirty": True, "has_path": False, "can_save": True, "can_open": True, "can_export": False}


def queue(scope, action):
    with scope.action_controller.agent_call():
        return scope.action_controller.call_model_tool(REQUEST_ACTION_TOOL,
            {"action": action, "reason": "请用户保存文档", "suggested_name": "测试.json"})


def test_visible_workspace_not_candidate_controls_save_and_one_shot_file_effect(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path)
    registry.prepare_document("sid", None, None)
    scope = registry.activate("sid")
    assert registry._context(scope)["workspace"]["can_save"] is False
    registry.update_workspace_state(scope.conversation_id, current_document_id=None, workspace=workspace())
    assert registry._context(scope)["workspace"]["can_save"] is False
    registry.update_workspace_state(scope.conversation_id,
        current_document_id=scope.binding.domain_checkpoint()["document_id"], workspace=workspace())
    result = queue(scope, "save_as")
    action_id = result["card"]["action_id"]
    card = registry.app_action(scope.conversation_id, "present", {"action_id": action_id})["card"]
    arguments = {"action_id": action_id, "ui_receipt": card["ui_receipt"]}
    assert registry.app_action(scope.conversation_id, "claim", arguments)["status"] == "claimed_for_external_execution"
    listed = registry.app_actions(scope.conversation_id)["actions"]
    assert listed[0]["execution_outcome_unknown"] is True
    recovered = registry.app_action(scope.conversation_id, "recover", arguments)
    assert recovered["card"]["status"] == "failed"
    repeated = registry.app_action(scope.conversation_id, "claim", arguments)
    assert repeated["status"] == "already_settled"
    assert "execution_request" not in repeated
    registry.close()


def test_document_switch_expires_queued_file_card(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path)
    registry.prepare_document("sid", None, None)
    scope = registry.activate("sid")
    registry.update_workspace_state(scope.conversation_id, current_document_id="first", workspace=workspace())
    action_id = queue(scope, "save_as")["card"]["action_id"]
    registry.update_workspace_state(scope.conversation_id, current_document_id="second", workspace=workspace())
    card = registry.app_action(scope.conversation_id, "present", {"action_id": action_id})["card"]
    assert card["status"] == "expired"
    registry.close()


def test_native_file_picker_is_user_only_cancel_safe_and_never_starts_agent(tmp_path):
    calls = []
    api = GuiApi(tmp_path, dsh_file_dialog=lambda request: calls.append(request) or None)
    result = api.dsh_client_file_dialog({"action": "save", "document_kind": "whiteboard",
                                         "suggested_name": "bad/name"})
    assert result == {"code": 0, "data": {"cancelled": True, "path": None}}
    assert calls[0]["suggested_name"] == "bad_name.json"
    assert api._agent_manager is None and api._dsh_shell is None
    with pytest.raises(NativeDshMuxError, match="request_invalid"):
        api.dsh_client_file_dialog({"action": "save", "document_kind": "whiteboard", "path": "bad"})


def test_formal_result_exact_payload_survives_unrelated_candidate_edits(tmp_path, db_path, monkeypatch):
    from genshin_app.agent_chat.native_candidate_transaction_contracts import (
        _application_payload_digest,
    )
    from genshin_app.whiteboard import new_whiteboard

    board = new_whiteboard()
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path)
    registry.prepare_document("sid", "whiteboard", board)
    scope = registry.activate("sid")
    payload = {"kind": "whiteboard", "board": board, "calculated": {"result": {"summary": {"total_damage": 123}}}}
    digest = _application_payload_digest("whiteboard", board, payload)
    proof = {"kind": "whiteboard", "revision": 2, "receipt_fingerprint": "a" * 64,
             "application_payload_digest": digest}
    other = {**proof, "revision": 6, "receipt_fingerprint": "b" * 64, "application_payload_digest": "c" * 64}
    proofs = [proof, other]
    monkeypatch.setattr(registry.application_backend, "_read_applied_proofs", lambda *_: {"records": proofs})
    visible = {**workspace(), "can_export": True}
    registry.update_workspace_state(scope.conversation_id, current_document_id=board["board_id"],
                                    workspace=visible, current_payload=payload)
    before = registry._context(scope)
    assert before["workspace"]["can_export"]
    assert before["binding"]["candidate_revision"] == 2  # exact payload, never last proof
    assert before["binding"]["applied_receipt_fingerprint"] == "a" * 64
    action_id = queue(scope, "export_result")["card"]["action_id"]
    changed = scope.candidate_session.call_tool("gs_patch_whiteboard", {"expected_revision": 0,
        "reason": "只修改隔离候选", "operations": [{"op": "replace", "path": "/title", "value": "下一份草稿"}]})
    assert changed["code"] == 0
    after = registry._context(scope)
    assert after == before
    # Another receipt for the identical actual result is not ambiguity about
    # the result, and later proof retention must not expire that same object.
    same_result = {**proof, "revision": 9, "receipt_fingerprint": "d" * 64}
    proofs.append(same_result)
    assert registry._context(scope) == before
    proofs.remove(proof)
    assert registry.app_action(scope.conversation_id, "present", {"action_id": action_id})["status"] == "presented_for_user"
    # A missing or unmatched actual payload must never inherit the latest proof.
    registry.update_workspace_state(scope.conversation_id, current_document_id=board["board_id"], workspace=visible)
    assert registry._context(scope)["workspace"]["can_export"] is False
    registry.close()


def test_result_workspace_rejects_payload_of_another_formal_document(tmp_path):
    from genshin_app.agent_chat.native_dsh_file_actions import workspace_state
    with pytest.raises(NativeDshMuxError, match="dsh_workspace_result_payload_invalid"):
        workspace_state({"current_document_id": "one", "workspace": {**workspace(), "can_export": True},
                         "current_payload": {"kind": "whiteboard", "board": {"board_id": "other"}, "calculated": None}})
