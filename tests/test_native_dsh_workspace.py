"""Domain bindings do not become a second DSH Session/Turn owner."""
from __future__ import annotations

import copy
import json
import threading

import pytest

from genshin_app.agent_chat.native_dsh_domain_calls import NativeDshDomainCalls
from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi
from tests.test_calculation_delivery import delivery_plan
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def test_reserved_document_creates_no_candidate_or_chat_and_is_idempotent(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    document = delivery_plan()
    first = registry.prepare_document("session-one", "native", document)
    assert not first["existing"]
    assert registry.prepare_document("session-one", "native", document)["existing"]
    assert registry.prepare_document("session-one", None, None)["existing"]
    assert not list(tmp_path.rglob("checkpoint.json"))
    assert not list(tmp_path.rglob("native-session-v2.json"))
    assert not list(tmp_path.rglob("*.jsonl"))
    assert not registry._workspaces
    assert registry.lookup_binding("session-one") == first["binding"]
    changed = delivery_plan()
    changed["manual_template"]["meta"]["name"] += " changed"
    with pytest.raises(NativeDshMuxError, match="dsh_workspace_already_bound"):
        registry.prepare_document("session-one", "native", changed)
    registry.close()


def test_mux_routing_reads_only_existing_binding_and_never_hydrates(tmp_path, monkeypatch):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=tmp_path / "not-opened.db")
    prepared = registry.prepare_document("one", None, None)
    expected = prepared["binding"]["conversation_id"]
    assert registry.cached_domain_route("one") == expected
    reopened = NativeDshWorkspaceRegistry(tmp_path, database_path=tmp_path / "not-opened.db")
    monkeypatch.setattr(reopened, "activate", lambda *_: pytest.fail("routing must not activate a candidate"))
    assert reopened.cached_domain_route("one") is None
    assert reopened.resolve_domain_route("one") == expected
    monkeypatch.setattr(reopened, "_read", lambda *_: pytest.fail("cached routing must not read files"))
    assert reopened.cached_domain_route("one") == expected
    assert not reopened._workspaces
    registry.close()
    reopened.close()
    assert reopened.cached_domain_route("one") is None


def test_read_tool_full_state_sampling_holds_candidate_lease(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path)
    registry.prepare_document("one", None, None)
    scope = registry.activate("one")
    entered, release = threading.Event(), threading.Event()

    def reading():
        with registry._tool_boundary(scope, "gs_read_context"):
            entered.set()
            assert release.wait(3)

    thread = threading.Thread(target=reading)
    try:
        thread.start()
        assert entered.wait(1)
        assert scope.candidate_session._lock.acquire(blocking=False) is False
    finally:
        release.set()
        thread.join(3)
        registry.close()


def test_aged_cancel_receipt_comes_from_exact_durable_call_without_execution(tmp_path, db_path):
    from tests.test_native_dsh_domain_calls import request

    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path)
    registry.prepare_document("one", None, None)
    scope = registry.activate("one")
    original = request(scope.tool_host, session_id="one")
    result = NativeDshDomainCalls(registry.resolve_target)("dsh_app_call_request", original)
    assert result["status"] == "settled_success"
    receipt = registry.domain_operation_receipt(original)
    assert receipt["state"] == "settled_success"
    assert receipt["settlement_ref"].startswith("appcall.")
    assert len(scope.app_call_ledger.read()["entries"]) == 1
    with pytest.raises(NativeDshMuxError, match="target_mismatch"):
        registry.domain_operation_receipt(original | {"raw_arguments_hash": "b" * 64})
    registry.close()


def test_followup_preflight_preserves_bound_candidate_after_editor_changes_and_cold_restart(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path, workflow="calculate")
    api = GuiApi(tmp_path, database_path=db_path, dsh_workspace_registry=registry)
    original = delivery_plan()
    request = {"dsh_session_id": "one", "document_kind": "native", "document": original}
    first = api.dsh_client_bind_document(request)
    scope = registry.activate("one")
    candidate = copy.deepcopy(scope.candidate_session.candidate)
    changed = copy.deepcopy(original)
    changed["manual_template"]["meta"]["name"] += " 用户已修改"
    continued = api.dsh_client_bind_document({**request, "document": changed})
    assert continued == {"binding": first["binding"], "existing": True}
    assert scope.candidate_session.candidate == candidate
    visible_workspace = {"document_kind": "native", "display_name": "用户文档", "current_view": "advanced",
        "dirty": True, "has_path": False, "can_save": True, "can_open": True, "can_export": False}
    registry.update_workspace_state(scope.conversation_id, current_document_id=original["plan_id"],
        workspace=visible_workspace)
    assert "editor_document_notice" not in registry._model_metadata(scope)["runtime_context"]
    registry.update_workspace_state(scope.conversation_id, current_document_id="another-document",
        workspace=visible_workspace)
    notice = registry._model_metadata(scope)["runtime_context"]["editor_document_notice"]
    assert notice["bound_document_id"] == original["plan_id"]
    assert notice["visible_document_id"] == "another-document"
    assert scope.candidate_session.candidate == candidate
    # Explicit seed replacement remains prohibited; only prompt reservation
    # is idempotent. A later exact review/CAS remains independently necessary.
    with pytest.raises(NativeDshMuxError, match="dsh_workspace_already_bound"):
        registry.prepare_document("one", "native", changed)
    registry.close()
    replacement = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path, workflow="calculate")
    reopened = GuiApi(tmp_path, database_path=db_path, dsh_workspace_registry=replacement)
    assert reopened.dsh_client_bind_document({**request, "document": changed}) == continued
    assert not replacement._workspaces  # a prompt reservation does not wake a candidate/model
    replacement.close()


def test_independent_candidates_and_cold_writer_takeover(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    for sid in ("session-one", "session-two"):
        registry.prepare_document(sid, "native", delivery_plan())
    first, second = registry.activate("session-one"), registry.activate("session-two")
    assert first.binding is not second.binding
    assert registry.activate("session-one") is first
    first_fence = first.binding.current_writer_fence()
    old_snapshot = first.tool_host.current_snapshot()
    assert "gs_calculate" in {item["name"] for item in old_snapshot["tools"]}
    assert second.candidate_session.revision == 0
    root = first.candidate_session.root
    registry.close()
    reopened = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    restored = reopened.activate("session-one")
    assert restored.binding.current_writer_fence() != first_fence
    assert restored.candidate_session.session_id == first.candidate_session.session_id
    assert restored.candidate_session.candidate == first.candidate_session.candidate
    row = json.loads((root / "dsh-workspace.json").read_text("utf-8"))
    assert row["writer_takeover"] is None
    assert set(row) == {"contract", "dsh_session_id", "conversation_id", "document_kind",
                       "document_id", "document_origin", "workflow", "seed_fingerprint", "writer_fence", "writer_takeover",
                       "compatibility_identity"}
    reopened.close()


def test_new_host_revokes_old_writer_instead_of_following_new_fence(tmp_path, db_path):
    original = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    original.prepare_document("session-one", "native", delivery_plan())
    first = original.activate("session-one")
    replacement = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    second = replacement.activate("session-one")
    assert first.binding._authority is not second.binding._authority
    with pytest.raises(PlanError, match="writer lease"):
        first.binding.current_writer_fence()
    assert replacement.resolve_target("session-one").tool_host is second.tool_host
    original.close()
    replacement.close()


def test_unknown_session_never_uses_current_document(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    registry.prepare_document("known", "native", delivery_plan())
    result = NativeDshDomainCalls(registry.resolve_target)("dsh_app_bind_request", {
        "contract": "genshin-native-dsh-domain-bind-v1", "request_id": "request", "session_id": "unknown"})
    assert result["binding_state"] == "unbound" and result["snapshot"] is None
    assert result["binding_issue"]["code"] == "dsh_document_not_bound"
    assert registry.lookup_binding("unknown") is None
    assert not registry._workspaces
    registry.close()


def test_concurrent_activation_has_one_domain_owner(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    registry.prepare_document("one", "native", delivery_plan())
    values = []
    threads = [threading.Thread(target=lambda: values.append(registry.activate("one"))) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert len(values) == 3 and all(value is values[0] for value in values)
    registry.close()


def test_closed_registry_cannot_be_revived(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    registry.close()
    with pytest.raises(NativeDshMuxError, match="dsh_workspace_closed"):
        registry.prepare_document("one", None, None)
    with pytest.raises(NativeDshMuxError, match="dsh_workspace_closed"):
        registry.resolve_target("one")


def test_gui_domain_calls_do_not_start_legacy_manager_or_infer_review(tmp_path, db_path):
    registry = NativeDshWorkspaceRegistry(tmp_path / "domain", database_path=db_path, workflow="calculate")
    def forbidden(_):
        raise AssertionError("legacy manager must not own the new domain")
    api = GuiApi(tmp_path, database_path=db_path, agent_manager_factory=forbidden,
                 dsh_workspace_registry=registry)
    reserved = api.dsh_client_bind_document({"dsh_session_id": "one", "document_kind": "native",
                                           "document": delivery_plan()})
    assert api.dsh_client_domain_binding({"dsh_session_id": "one"}) == {"binding": None}
    registry.resolve_target("one")
    binding = api.dsh_client_domain_binding({"dsh_session_id": "one"})["binding"]
    assert binding["conversation_id"] == reserved["binding"]["conversation_id"]
    projection = binding["projection"]
    assert projection["generic_owner"] == "dsh"
    assert not any(item["command_id"] in {"candidate.review", "conversation.send", "turn.retry"}
                   for item in projection["available_commands"])
    cid = binding["conversation_id"]
    assert api.dsh_client_candidate({"conversation_id": cid})["data"]["revision"] == 0
    page = api.dsh_client_candidate_read({"conversation_id": cid, "resource": "candidate",
                                        "pointer": "", "offset": 0, "limit": 50})
    assert page["code"] == 0 and page["data"]["kind"] == "object"
    calculated = api.dsh_client_candidate_read({"conversation_id": cid, "resource": "calculated",
        "pointer": "", "offset": 0, "limit": 50})
    assert calculated["code"] == 0 and calculated["data"]["value"] is None
    bad = api.dsh_client_candidate_read({"conversation_id": cid, "resource": "unavailable",
        "pointer": "", "offset": 0, "limit": 50})
    assert bad["code"] == 1 and bad["reason"] == "agent_resource_missing"
    assert bad["operation_may_have_completed"] is False
    api._close_dsh_shell()


def test_writer_takeover_recovers_crash_after_lease_commit(tmp_path, db_path, monkeypatch):
    registry = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    registry.prepare_document("one", "native", delivery_plan())
    original = registry.activate("one")
    old_fence = original.binding.current_writer_fence()
    registry.close()
    interrupted = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    save = interrupted._save
    def fail_after_transfer(root, row):
        if row["writer_fence"] != old_fence and row["writer_takeover"] is None:
            raise OSError("injected crash at final publication")
        save(root, row)
    monkeypatch.setattr(interrupted, "_save", fail_after_transfer)
    with pytest.raises(OSError, match="injected crash"):
        interrupted.activate("one")
    interrupted.close()
    final = NativeDshWorkspaceRegistry(tmp_path, database_path=db_path, workflow="calculate")
    recovered = final.activate("one")
    assert recovered.binding.current_writer_fence() != old_fence
    assert recovered.candidate_session.session_id == original.candidate_session.session_id
    final.close()
