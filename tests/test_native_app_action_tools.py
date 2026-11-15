from __future__ import annotations

import copy

import pytest

from genshin_app.agent_chat.native_app_action_ledger import NativeAppActionLedger
from genshin_app.agent_chat.native_app_action_tools import (
    GET_WORKSPACE_TOOL,
    REQUEST_ACTION_TOOL,
    NativeAppActionToolError,
    NativeAppActionToolHost,
    definitions,
)

HASH = "a" * 64


def _binding():
    return {
        "document_id": "document-1",
        "candidate_revision": 3,
        "stage_revision": 2,
        "receipt_fingerprint": HASH,
        "applied_receipt_fingerprint": HASH,
    }


def _workspace():
    return {
        "document_kind": "whiteboard",
        "display_name": "测试方案",
        "current_view": "whiteboard",
        "dirty": True,
        "has_path": False,
        "can_save": True,
        "can_open": True,
        "can_export": True,
    }


def _host(tmp_path, *, workspace=None, binding=None):
    ledger = NativeAppActionLedger(
        tmp_path,
        conversation_id="conversation-1",
        id_factory=lambda: "action_" + "1" * 32,
        clock=lambda: "2026-10-23T00:00:00Z",
    )
    current_workspace = workspace or _workspace()
    current_binding = binding or _binding()
    return NativeAppActionToolHost(
        ledger=ledger,
        workspace_reader=lambda: copy.deepcopy(current_workspace),
        binding_reader=lambda: copy.deepcopy(current_binding),
    ), ledger, current_workspace, current_binding


def test_definitions_are_fixed_fresh_and_path_free():
    first = definitions()
    first[0]["name"] = "tampered"
    second = definitions()

    assert [row["name"] for row in second] == [
        GET_WORKSPACE_TOOL,
        REQUEST_ACTION_TOOL,
    ]
    assert "path" not in str(second).lower()


def test_workspace_read_is_validated_and_expires_stale_cards(tmp_path):
    host, ledger, _, binding = _host(tmp_path)
    queued = host.call(
        REQUEST_ACTION_TOOL,
        {"action": "open_document", "reason": "打开另一份方案。"},
    )
    binding["stage_revision"] += 1

    assert host.call(GET_WORKSPACE_TOOL, {})["expired_action_ids"] == []
    binding["document_id"] = "another-document"

    result = host.call(GET_WORKSPACE_TOOL, {})

    assert result["workspace"]["display_name"] == "测试方案"
    assert result["expired_action_ids"] == [queued["card"]["action_id"]]
    assert ledger.read()["cards"][0]["status"] == "expired"


def test_request_returns_user_card_not_execution_claim(tmp_path):
    host, ledger, _, _ = _host(tmp_path)

    result = host.call(
        REQUEST_ACTION_TOOL,
        {
            "action": "save_as",
            "reason": "保存当前方案。",
            "suggested_name": "C:\\unsafe\\plan.json",
        },
    )

    assert result["status"] == "queued_for_user"
    assert result["card"]["status"] == "queued"
    assert result["card"]["suggested_name"] == "C_unsafe_plan.json"
    assert "binding" not in result["card"]
    assert "conversation_id" not in result["card"]
    assert "ui_receipt" not in result["card"]
    assert "receipt_fingerprint" not in str(result)
    assert "尚未执行" in result["warning"]
    assert ledger.read()["cards"][0]["action_id"] == result["card"]["action_id"]


def test_save_current_without_path_is_normalized_to_user_save_as(tmp_path):
    host, ledger, _, _ = _host(tmp_path)

    result = host.call(
        REQUEST_ACTION_TOOL,
        {
            "action": "save_current",
            "reason": "保存当前尚未命名的方案。",
            "suggested_name": "星超导两轮轴.json",
        },
    )

    assert result["status"] == "queued_for_user"
    assert result["notice_code"] == "save_as_required"
    assert "另存为" in result["notice"]
    assert result["card"]["action"] == "save_as"
    assert result["card"]["suggested_name"] == "星超导两轮轴.json"
    assert ledger.read()["cards"][0]["action"] == "save_as"


def test_workspace_path_or_unknown_tool_fails_before_model_visibility(tmp_path):
    workspace = _workspace()
    workspace["display_name"] = "C:\\Users\\name\\plan.json"
    host, _, _, _ = _host(tmp_path, workspace=workspace)

    with pytest.raises(Exception, match="app_action_workspace_invalid"):
        host.call(GET_WORKSPACE_TOOL, {})
    with pytest.raises(NativeAppActionToolError, match="app_action_tool_forbidden"):
        host.call("gs_shell", {})


def test_context_change_between_workspace_reads_fails_closed(tmp_path):
    ledger = NativeAppActionLedger(
        tmp_path,
        conversation_id="conversation-1",
        id_factory=lambda: "action_" + "1" * 32,
        clock=lambda: "2026-10-23T00:00:00Z",
    )
    calls = 0

    def binding_reader():
        nonlocal calls
        calls += 1
        value = _binding()
        if calls > 1:
            value["stage_revision"] += 1
        return value

    host = NativeAppActionToolHost(
        ledger=ledger,
        workspace_reader=_workspace,
        binding_reader=binding_reader,
    )

    with pytest.raises(NativeAppActionToolError, match="app_action_context_changed"):
        host.call(GET_WORKSPACE_TOOL, {})


def test_workspace_change_during_context_snapshot_fails_closed(tmp_path):
    ledger = NativeAppActionLedger(
        tmp_path,
        conversation_id="conversation-1",
        id_factory=lambda: "action_" + "1" * 32,
        clock=lambda: "2026-10-23T00:00:00Z",
    )
    calls = 0

    def workspace_reader():
        nonlocal calls
        calls += 1
        value = _workspace()
        if calls > 1:
            value["dirty"] = False
        return value

    host = NativeAppActionToolHost(
        ledger=ledger,
        workspace_reader=workspace_reader,
        binding_reader=_binding,
    )

    with pytest.raises(NativeAppActionToolError, match="app_action_context_changed"):
        host.call(GET_WORKSPACE_TOOL, {})


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"action": "save_as", "reason": ""},
        {"action": "save_as", "reason": "   \t"},
        {"action": "save_as", "reason": "ok", "path": "C:\\unsafe"},
        {"action": "unknown", "reason": "ok"},
    ],
)
def test_invalid_model_arguments_are_rejected(tmp_path, arguments):
    host, _, _, _ = _host(tmp_path)

    with pytest.raises(NativeAppActionToolError):
        host.call(REQUEST_ACTION_TOOL, arguments)


def test_reason_schema_uses_portable_bounds_and_python_keeps_authority():
    schema = next(
        row["inputSchema"]
        for row in definitions()
        if row["name"] == REQUEST_ACTION_TOOL
    )
    reason = schema["properties"]["reason"]
    assert reason == {"type": "string", "minLength": 1, "maxLength": 1000}
