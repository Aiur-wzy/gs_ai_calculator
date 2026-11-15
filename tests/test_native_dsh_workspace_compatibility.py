"""Cold and live App-domain guards never become a second chat state owner."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from genshin_app.agent_chat import native_dsh_workspace_compatibility as compatibility
from genshin_app.agent_chat.native_dsh_domain_calls import (
    DshDomainUnavailable,
    NativeDshDomainCalls,
)
from genshin_app.agent_chat.native_dsh_host_requests import NativeDshHostRequests
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.gui.api import GuiApi
from genshin_app.gui.document_transport import DOCUMENT_METHODS
from tests.test_calculation_delivery import delivery_plan
from tests.test_native_dsh_host_requests import research
from tests.test_queries import db_path as db_path  # noqa: PLC0414


@pytest.fixture
def current_identity(monkeypatch):
    original = compatibility.current_workspace_identity()
    value = copy.deepcopy(original)
    monkeypatch.setattr(compatibility, "current_native_compatibility_identity", lambda: value)
    return value


def snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*.json")}


def changed(value, component):
    if component == "native_shell_release":
        value[component]["state"] = "pinned"
        value[component]["manifest_sha256"] = "0" * 64
    else:
        value[component] = "0" * 64


@pytest.mark.parametrize("component", compatibility.COMPONENTS)
@pytest.mark.parametrize("cold", [False, True])
def test_old_calculated_domain_cannot_review_deliver_or_take_over_writer(
    tmp_path, db_path, current_identity, component, cold,
):
    domain = tmp_path / "domain"
    registry = NativeDshWorkspaceRegistry(domain, database_path=db_path, workflow="calculate")
    original = delivery_plan()
    first = registry.prepare_document("one", "native", original)["binding"]
    scope = registry.activate("one")
    with scope.binding.domain_write_boundary():
        scope.candidate_session.calculate(0)
    projection = registry.domain_projection("one")["projection"]
    assert "candidate.review" in {item["command_id"] for item in projection["available_commands"]}
    before = snapshot(domain)
    if cold:
        registry.close()
        registry = NativeDshWorkspaceRegistry(domain, database_path=db_path, workflow="calculate")
    changed(current_identity, component)
    api = GuiApi(tmp_path, database_path=db_path, dsh_workspace_registry=registry)
    results = [
        api.dsh_client_bind_document({"dsh_session_id": "one", "document_kind": "native", "document": original}),
        api.dsh_client_domain_binding({"dsh_session_id": "one"}),
        api.dsh_client_candidate({"conversation_id": first["conversation_id"]}),
        api.dsh_client_domain_approvals({"conversation_id": first["conversation_id"]}),
        api.dsh_client_app_actions({"conversation_id": first["conversation_id"]}),
        api.dsh_client_candidate_command({"conversation_id": first["conversation_id"],
            "command_id": "candidate.review", "expected_projection_revision": projection["projection_revision"],
            "arguments": {"kind": "native", "document": original, "expected_revision": 0,
                "expected_receipt": scope.candidate_session.summary()["receipt_fingerprint"]}}),
    ]
    for result in results:
        assert result["code"] == 1 and result["reason"] == "native_app_binding_compatibility_changed"
        assert result["operation_may_have_completed"] is False
        details = result["details"]
        assert details["state"] == "changed"
        assert [row["component"] for row in details["differences"]] == [component]
        assert "旧排轴文件" in details["migration_prompt"]
    target = registry.resolve_target("one")
    assert isinstance(target, DshDomainUnavailable)
    response = NativeDshDomainCalls(registry.resolve_target)("dsh_app_bind_request", {
        "contract": "genshin-native-dsh-domain-bind-v1", "request_id": "request", "session_id": "one"})
    assert response["binding_state"] == "unbound" and response["snapshot"] is None
    assert response["binding_issue"]["details"] == results[0]["details"]
    assert snapshot(domain) == before
    assert not cold or not registry._workspaces
    # A genuinely new Session gets current evidence and may calculate again.
    registry.prepare_document("new", "native", original)
    assert registry.activate("new").binding.current_writer_fence()
    registry.close()


def test_legacy_workspace_is_not_stamped_and_research_uses_separate_ledger(
    tmp_path, db_path, current_identity,
):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path, workflow="calculate")
    registry.prepare_document("one", "native", delivery_plan())
    root = registry._root("one")
    path = root / "dsh-workspace.json"
    row = json.loads(path.read_text("utf-8"))
    row["contract"] = "genshin-native-dsh-workspace-binding-v1"
    del row["compatibility_identity"]
    path.write_text(json.dumps(row), encoding="utf-8")
    before = snapshot(root)
    with pytest.raises(NativeDshMuxError, match="compatibility_missing"):
        registry.activate("one")
    target = registry.resolve_target("one")
    assert isinstance(target, DshDomainUnavailable)
    assert target.code == "native_app_binding_compatibility_missing"
    host = NativeDshHostRequests(registry, data_dir=tmp_path)
    result = host("dsh_research_register_request", research())
    assert result["result"]["status"] == "registered"
    assert host._research_ledger("one").root.is_relative_to(tmp_path / "native-dsh-research")
    assert snapshot(root) == before and not registry._workspaces
    assert not list(root.glob("checkpoint.json"))
    registry.close()


def test_changed_domain_keeps_research_and_unknown_faults_do_not_silently_downgrade(
    tmp_path, db_path, current_identity, monkeypatch,
):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path)
    registry.prepare_document("one", "native", delivery_plan())
    changed(current_identity, "skill_bundle_hash")
    host = NativeDshHostRequests(registry, data_dir=tmp_path)
    assert host("dsh_research_register_request", research())["result"]["status"] == "registered"
    monkeypatch.setattr(compatibility, "current_native_compatibility_identity", lambda: {"invalid": True})
    with pytest.raises(NativeDshMuxError, match="compatibility_identity_invalid"):
        registry.resolve_target("one")
    with pytest.raises(NativeDshMuxError, match="compatibility_identity_invalid"):
        host._research_ledger("one")
    registry.close()


def test_user_recovery_interfaces_do_not_boot_agent_or_become_model_tools(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path)
    api = GuiApi(tmp_path, database_path=db_path, dsh_workspace_registry=registry)
    methods = {"dsh_client_incompatible_applications", "dsh_client_abandon_incompatible_application"}
    assert methods <= DOCUMENT_METHODS
    response = json.loads(api.document_json_call("dsh_client_incompatible_applications", "[{}]"))
    assert response == {"code": 0, "data": {"items": []}}
    assert api._dsh_shell is None and not registry.root.exists()
    registry.prepare_document("one", "native", delivery_plan())
    tools = registry.activate("one").tool_host.current_snapshot()["tools"]
    assert not methods.intersection(row["name"] for row in tools)
    assert not any("abandon_incompatible" in row["name"] for row in tools)
    registry.close()
