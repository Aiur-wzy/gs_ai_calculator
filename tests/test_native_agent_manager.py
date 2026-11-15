"""Historical App helpers remain testable without an executable native owner."""
from __future__ import annotations

import copy
import inspect
from types import SimpleNamespace
from typing import ClassVar

import pytest

from genshin_app.agent_chat.app_actions import (
    call as call_app_action,
)
from genshin_app.agent_chat.app_actions import (
    normalize_workspace,
    refresh_preconditions,
)
from genshin_app.agent_chat.manager import AgentManager
from genshin_app.equipment.schema import PlanError


class Candidate:
    storage: ClassVar[dict] = {}
    workflow = "calculate"

    def __init__(self, sid, root, db, kind, document, **_kwargs):
        self.sid = sid
        self.root = root
        self.kind = kind
        self.document = copy.deepcopy(document)
        self.storage[sid] = self

    @classmethod
    def load(cls, sid, *_args, **_kwargs):
        return cls.storage[sid]

    def tool_definitions(self):
        return [{"type": "function", "name": "gs_candidate_tool",
                 "description": "candidate", "inputSchema": {"type": "object"}}]

    def call_tool(self, name, args):
        return {"code": 0, "data": {"name": name, "args": args}}

    def summary(self):
        return {"revision": 0, "ready": True, "stale": False,
                "application_kind": self.kind, "receipt_fingerprint": "receipt"}

    def accept(self, kind, document, revision, receipt):
        assert kind == self.kind and document == self.document
        assert revision == 0 and receipt == "receipt"
        return {"kind": kind, "revision": revision, "receipt_fingerprint": receipt}

    def save(self):
        pass

    def close(self):
        pass


def test_workspace_capabilities_fail_closed_when_legacy_input_omits_them():
    workspace = normalize_workspace({}, kind="whiteboard", title="旧会话")
    assert workspace["can_save"] is False
    assert workspace["can_open"] is False
    assert workspace["can_export"] is False


def test_cli_owner_does_not_accept_native_factory_or_credential_configuration(tmp_path):
    def forbidden(*_args, **_kwargs):
        pytest.fail("retired native factory must never run")

    parameters = inspect.signature(AgentManager).parameters
    assert "native_transport_factory" not in parameters
    assert "credential_store" not in parameters
    manager = AgentManager(tmp_path)
    manager.detect = forbidden
    with pytest.raises(PlanError) as caught:
        manager.connect({"id": "deepseek-api", "kind": "native_api"})
    assert caught.value.reason == "agent_native_backend_retired"
    manager.shutdown()


class StagedCandidate(Candidate):
    workflow = "staged"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage = "loadout_draft"

    def tool_definitions(self):
        return [
            {"type": "function", "name": "gs_read_skill", "description": "read skill",
             "inputSchema": {"type": "object"}},
            {"type": "function", "name": "gs_future_tool", "description": "future",
             "inputSchema": {"type": "object"}},
        ]

    def active_tool_definitions(self):
        return [
            {"type": "function", "name": "gs_read_skill", "description": "read skill",
             "inputSchema": {"type": "object"}},
        ]

    def stage_context(self):
        skill = {
            "loadout_draft": "references/workflow/focus/loadout.md",
            "action_checkpoint": "references/workflow/focus/actions.md",
        }[self.stage]
        return {
            "workflow_stage": self.stage,
            "stage_revision": 0,
            "current_stage_skill": skill,
            "allowed_tools": ["candidate-only-placeholder"],
        }

    def summary(self):
        return {**super().summary(), "workflow_stage": self.stage,
                "stage_revision": 0}


def test_cli_staged_registration_keeps_workflow_tool_superset(tmp_path):
    manager = AgentManager(tmp_path, candidate_factory=StagedCandidate)
    manager._transport = SimpleNamespace(is_native_api=False)
    candidate = StagedCandidate(
        "b" * 32, tmp_path / "candidate", tmp_path / "db", "whiteboard",
        {"title": "CLI白板"})
    assert {row["name"] for row in manager._active_tools(candidate)} == {
        "gs_read_skill", "gs_future_tool"}


@pytest.mark.parametrize(("action_name", "requires_applied", "status"), [
    ("save_current", True, "blocked"),
    ("save_as", True, "blocked"),
    ("export_result", True, "blocked"),
    ("open_document", False, "ready"),
])
def test_file_action_contract_binds_candidate_and_exposes_gate(
        action_name, requires_applied, status):
    session = {
        "kind": "native", "title": "方案", "applied_candidate": None,
        "workspace": {
            "contract": "agent-app-workspace-v1", "document_kind": "native",
            "display_name": "方案", "current_view": "rotation", "dirty": True,
            "has_path": True, "can_save": True, "can_open": True, "can_export": True,
        },
        "app_actions": [],
    }
    summary = {"revision": 7, "receipt_fingerprint": "receipt-7", "ready": True}
    _data, action = call_app_action(
        session, "gs_app_request_action",
        {"action": action_name, "reason": "验证文件动作合同"}, summary)
    assert action["candidate_revision"] == 7
    assert action["receipt_fingerprint"] == "receipt-7"
    assert action["requires_applied_candidate"] is requires_applied
    assert action["preconditions"]["status"] == status

    changed = refresh_preconditions(
        session, {**summary, "revision": 8, "receipt_fingerprint": "receipt-8"})
    assert changed[0]["preconditions"]["status"] == "stale"
    assert changed[0]["preconditions"]["issues"][0]["code"] == "candidate_binding_stale"
