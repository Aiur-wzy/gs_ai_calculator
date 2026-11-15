from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from genshin_app.agent_chat.native_app_action_controller import (
    NativeAppActionControllerError,
)
from genshin_app.agent_chat.native_dsh_host_requests import NativeDshHostRequests
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from tests.test_native_research_ledger import search_meta
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def research(**changes):
    arguments = {"queries": ["角色动作与持续机制"]}
    raw = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    return {"contract": "genshin-native-dsh-research-register-v1", "request_id": str(uuid.uuid4()),
        "session_id": "one", "turn": 1, "step": 1, "call_seq": 8,
        "call_id": "provider-call-1", "tool": "web_search", "arguments": arguments,
        "arguments_json": raw, "raw_arguments_hash": hashlib.sha256(raw.encode()).hexdigest(), **changes}


def test_unbound_research_is_auditable_without_creating_candidate_or_turn_owner(tmp_path, db_path):
    workspace = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    host = NativeDshHostRequests(workspace, data_dir=tmp_path)
    first = research()
    registered = host("dsh_research_register_request", first)
    assert registered["result"]["status"] == "registered"
    assert not workspace._workspaces
    assert host("dsh_research_register_request", first)["result"]["status"] == "already_registered"
    result = host("dsh_research_settle_request", {**first,
        "contract": "genshin-native-dsh-research-settle-v1", "request_id": str(uuid.uuid4()),
        "research_id": registered["result"]["research_id"], "status": "completed",
        "content_hash": "a" * 64, "presentation_meta": search_meta(), "resource_ids": []})
    assert result["result"]["status"] == "completed"
    assert host._research_ledger("one").read()["entries"][0]["turn_id"] == "dsh-turn-1"
    assert not list(tmp_path.rglob("native-session-v2.json"))
    workspace.close()


def test_bound_research_control_does_not_activate_candidate(tmp_path, db_path, monkeypatch):
    workspace = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    workspace.prepare_document("one", None, None)
    monkeypatch.setattr(workspace, "activate", lambda *_: pytest.fail("control path must not hydrate candidate"))
    host = NativeDshHostRequests(workspace, data_dir=tmp_path)
    result = host("dsh_research_register_request", research())
    assert result["result"]["status"] == "registered"
    assert not workspace._workspaces
    assert host._research_ledger("one").root == workspace._root("one")
    workspace.close()


def test_reused_provider_call_id_cannot_mix_sessions_or_turns(tmp_path, db_path):
    workspace = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    host = NativeDshHostRequests(workspace, data_dir=tmp_path)
    one = host("dsh_research_register_request", research())["result"]["research_id"]
    two = host("dsh_research_register_request", research(turn=2, call_seq=50))["result"]["research_id"]
    other = host("dsh_research_register_request", research(session_id="other"))["result"]["research_id"]
    assert len({one, two, other}) == 3
    assert len(host._research_ledger("one").read()["entries"]) == 2
    assert len(host._research_ledger("other").read()["entries"]) == 1
    workspace.close()


def test_changed_research_arguments_and_cross_turn_settlement_are_rejected(tmp_path, db_path):
    workspace = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    host = NativeDshHostRequests(workspace, data_dir=tmp_path)
    req = research()
    with pytest.raises(NativeDshMuxError, match="raw_arguments_mismatch"):
        host("dsh_research_register_request", {**req, "raw_arguments_hash": "f" * 64})
    registered = host("dsh_research_register_request", req)["result"]
    with pytest.raises(NativeDshMuxError, match="settlement_identity_mismatch"):
        host("dsh_research_settle_request", {**req, "turn": 2,
            "contract": "genshin-native-dsh-research-settle-v1", "research_id": registered["research_id"],
            "status": "cancelled", "content_hash": None, "presentation_meta": None, "resource_ids": []})
    workspace.close()


def test_pre_admission_failure_keeps_stable_cause_and_safe_durable_diagnostic(tmp_path, db_path, monkeypatch):
    workspace = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    workspace.prepare_document("one", None, None)
    workspace.activate("one")
    host = NativeDshHostRequests(workspace, data_dir=tmp_path)
    def fail(*_):
        raise NativeAppActionControllerError("app_action_serial_gate_busy")
    monkeypatch.setattr(host, "_dispatch", fail)
    with pytest.raises(NativeDshMuxError, match="app_action_serial_gate_busy") as caught:
        host("dsh_app_call_request", {"request_id": "one", "session_id": "one", "call_id": "unadmitted", "turn": 2, "step": 3,
             "candidate_revision": 5,
             "secret": "must-not-be-saved"})
    assert caught.value.outcome_unknown is False
    assert caught.value.failure["details"]["app_call_admission"] == "not_recorded"
    assert caught.value.failure["details"]["execution_state"] == "did_not_execute"
    assert caught.value.failure["details"]["recovery"]["action"] == "wait_resource"
    logs = list((tmp_path / "logs/native-dsh-host").glob("*.json"))
    assert len(logs) == 1
    saved = logs[0].read_text(encoding="utf-8")
    assert "must-not-be-saved" not in saved and str(tmp_path) not in saved
    assert json.loads(saved)["code"] == "app_action_serial_gate_busy"
    assert json.loads(saved)["context"] == {"request_id": "one", "session_id": "one", "call_id": "unadmitted",
                                            "turn_id": 2, "step_id": 3, "candidate_revision": 5}
    from genshin_app.app_fault_store import read_system_diagnostic
    diagnostic_id = caught.value.failure["details"]["diagnostic_id"]
    assert read_system_diagnostic(tmp_path, diagnostic_id)["code"] == "app_action_serial_gate_busy"
    workspace.close()


def test_diagnostic_receiver_failure_never_returns_an_unreadable_id(tmp_path, db_path, monkeypatch):
    workspace = NativeDshWorkspaceRegistry(tmp_path / "workspaces", database_path=db_path)
    host = NativeDshHostRequests(workspace, data_dir=tmp_path)
    def fail(*_):
        raise NativeAppActionControllerError("app_action_serial_gate_busy")
    def disk_failure(*_args, **_kwargs):
        raise OSError("private disk path")
    monkeypatch.setattr(host, "_dispatch", fail)
    monkeypatch.setattr("genshin_app.agent_chat.native_dsh_host_requests.write_owned", disk_failure)
    with pytest.raises(NativeDshMuxError) as caught:
        host("dsh_app_call_request", {"request_id": "one", "session_id": "one", "call_id": "unadmitted"})
    details = caught.value.failure["details"]
    assert details["diagnostic_id"] is None
    assert details["diagnostic_storage"] == {"state": "failed", "reason": "diagnostic_persistence_failed"}
    assert caught.value.code == "app_action_serial_gate_busy" and "private" not in str(caught.value.failure)
    workspace.close()
