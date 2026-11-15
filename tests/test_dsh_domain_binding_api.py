from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from genshin_app.gui.api import GuiApi


def test_unbound_domain_does_not_start_legacy_agent_or_guess_title(tmp_path):
    api = GuiApi(tmp_path)
    assert api.dsh_client_domain_binding({"dsh_session_id": "unbound"}) == {"binding": None}
    assert api._agent_manager is None
    assert api._dsh_shell is None


def test_explicit_host_binding_and_command_projection_are_copied_exactly(tmp_path):
    value = {"dsh_session_id": "dsh-one", "conversation_id": "app-one", "projection": {
        "contract": "native-harness-projection-v3", "projection_revision": 17,
        "available_commands": [{"command_id": "candidate.review", "enabled": True,
                                "scope": {"receipt": "a" * 64}}],
    }}
    before = copy.deepcopy(value)
    requests = []
    api = GuiApi(tmp_path, dsh_domain_binding_resolver=lambda sid: requests.append(sid) or value)
    result = api.dsh_client_domain_binding({"dsh_session_id": "dsh-one"})
    assert requests == ["dsh-one"]
    assert result == {"binding": before}
    result["binding"]["projection"]["available_commands"].clear()
    assert value == before


@pytest.mark.parametrize("payload", [None, {}, {"dsh_session_id": "../other"},
    {"dsh_session_id": True}, {"dsh_session_id": "one", "title": "guess"}])
def test_invalid_binding_request_cannot_discover_another_candidate(tmp_path, payload):
    api = GuiApi(tmp_path, dsh_domain_binding_resolver=lambda _: pytest.fail("must reject first"))
    result = api.dsh_client_domain_binding(payload)
    assert result["code"] == 1 and result["reason"] == "dsh_domain_binding_request_invalid"


def test_resolver_must_match_exact_dsh_session(tmp_path):
    api = GuiApi(tmp_path, dsh_domain_binding_resolver=lambda _: {
        "dsh_session_id": "other", "conversation_id": "app-one", "projection": {}})
    result = api.dsh_client_domain_binding({"dsh_session_id": "one"})
    assert result["code"] == 1 and result["reason"] == "dsh_domain_binding_identity_mismatch"


def test_database_change_is_domain_unavailability_not_document_corruption(tmp_path):
    from genshin_app.equipment.schema import PlanError

    def changed(_):
        raise PlanError("数据库已更新", "agent_database_changed")

    api = GuiApi(tmp_path, dsh_domain_binding_resolver=changed)
    result = json.loads(api.document_json_call("dsh_client_domain_binding", '[{"dsh_session_id":"one"}]'))
    assert result["code"] == 1 and result["reason"] == "agent_database_changed"
    assert result["operation_may_have_completed"] is False
    assert api._agent_manager is None


def test_session_status_reads_sdk_facts_without_inventing_completion_or_app_candidate(tmp_path):
    calls = []
    value = {"session_id": "one", "last_turn": None, "agent_running": False,
        "admission": {"state": "idle", "active": 0, "waiting": 0, "capacity": 2}}
    api = GuiApi(tmp_path, dsh_shell_factory=lambda _: SimpleNamespace(
        session_status=lambda sid: calls.append(sid) or copy.deepcopy(value), close=lambda: None))
    result = api.dsh_client_session_status({"dsh_session_id": "one"})
    assert result["last_turn"] is None and "completed" not in result
    assert result["accounting"]["provider_requests"] == 0
    assert not (tmp_path / "native-dsh-accounting").exists()
    assert api._dsh_workspace_registry is None and api._agent_manager is None
    assert calls == ["one"]
    value["last_turn"] = {"turn": 2, "reason": {"kind": "completed"}}
    assert api.dsh_client_session_status({"dsh_session_id": "one"})["last_turn"] == value["last_turn"]
    assert api.dsh_client_session_status({"dsh_session_id": "../elsewhere"})["code"] == 1
    assert calls == ["one", "one"]
    api._close_dsh_shell()
