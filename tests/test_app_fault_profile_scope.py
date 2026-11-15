"""The owning App profile wins over an unrelated database's directory."""
from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from genshin_app.agent_chat.candidate import CandidateSession
from genshin_app.app_fault_store import (
    current_fault_context,
    fallback_fault_scope,
    fault_scope,
    read_system_diagnostic,
)
from genshin_app.equipment.schema import PlanError
from tests.test_calculation_delivery import delivery_plan
from tests.test_native_app_tool_host import call, host
from tests.test_queries import db_path as db_path  # noqa: PLC0414


def test_fallback_adds_identities_and_restores_the_explicit_owner(tmp_path):
    owner, other = tmp_path / "profile", tmp_path / "external-db"
    with fault_scope(owner, conversation_id="conversation-1"):
        with fallback_fault_scope(other, call_id="call-1"):
            assert current_fault_context() == (owner, {"conversation_id": "conversation-1", "call_id": "call-1"})
        assert current_fault_context() == (owner, {"conversation_id": "conversation-1"})
    with fallback_fault_scope(other):
        assert current_fault_context()[0] == other


@pytest.mark.parametrize("through_host", [False, True])
def test_generated_validation_tool_chain_keeps_actual_profile(tmp_path, monkeypatch, through_host):
    database_root, profile = tmp_path / "external-readonly-db", tmp_path / "profile"
    session = CandidateSession(uuid.uuid4().hex, tmp_path / "candidate", database_root / "absent.db",
                               "native", delivery_plan())

    def fail_output(_value):
        raise PlanError("invalid generated output", "invalid_plan",
                        [{"path": "generated.hit", "message": "unexpected output field"}])

    monkeypatch.setattr(session, "_validate_calculated", fail_output)
    with fault_scope(profile, conversation_id="conversation-1"):
        if through_host:
            result = call(host(tmp_path / "ledger", session, lambda s, n, a: s.call_tool(n, a)),
                          tool="gs_calculate", raw='{"expected_revision":0}')
            assert result["terminal_state"] == "settled_failure"
            response = result["result"]
        else:
            response = session.call_tool("gs_calculate", {"expected_revision": 0})
    assert response["diagnostic_storage"]["state"] == "persisted"
    record = read_system_diagnostic(profile, response["system_fault"]["diagnostic_id"])
    assert record["operation"] == "gs_calculate.validate_generated_result"
    assert record["context"]["session_id"] == session.session_id
    assert not (database_root / "logs").exists()
    assert not (tmp_path / "ledger/logs").exists()
    assert not CandidateSession.load(session.session_id, session.root, session.db_path).summary()["ready"]


def test_two_registry_profiles_share_readonly_database_without_crossed_diagnostics(tmp_path, db_path, monkeypatch):
    from genshin_app.agent_chat import tools
    from genshin_app.agent_chat.native_dsh_domain_calls import NativeDshDomainCalls
    from genshin_app.agent_chat.native_dsh_workspace import NativeDshWorkspaceRegistry
    from tests.test_native_dsh_domain_calls import request

    profiles = [tmp_path / "profile-a", tmp_path / "profile-b"]
    registries = [NativeDshWorkspaceRegistry(p / "workspaces", database_path=db_path, diagnostic_data_dir=p)
                  for p in profiles]
    database_before = db_path.read_bytes()
    for registry in registries:
        registry.prepare_document("dsh-one", None, None)
        registry.activate("dsh-one")

    def fail(_session, _arguments):
        raise TypeError("internal query fault")

    monkeypatch.setitem(tools.DISPATCH, "gs_read_context", fail)

    def execute(registry):
        scope = registry.activate("dsh-one")
        assert scope.tool_host.diagnostic_data_dir == registry.diagnostic_data_dir
        return NativeDshDomainCalls(registry.resolve_target)("dsh_app_call_request", request(scope.tool_host))

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(execute, registries))
        for index, (profile, result) in enumerate(zip(profiles, results, strict=True)):
            assert result["status"] == "settled_failure"
            identifier = result["result"]["system_fault"]["diagnostic_id"]
            assert read_system_diagnostic(profile, identifier)["operation"] == "gs_read_context"
            assert not (profiles[1-index] / "logs/app-system-faults" / (identifier + ".json")).exists()
        assert db_path.read_bytes() == database_before
        assert not (db_path.parent / "logs/app-system-faults").exists()
    finally:
        for registry in registries:
            registry.close()


def test_gui_factory_explicitly_passes_profile_even_with_external_database(tmp_path, db_path):
    from genshin_app.gui.api import GuiApi

    api = GuiApi(tmp_path / "app-profile", database_path=db_path)
    registry = api._get_dsh_workspaces()
    assert registry.diagnostic_data_dir == api.data_dir
    assert registry.diagnostic_data_dir != db_path.parent
    registry.close()


def test_domain_bind_fault_uses_injected_host_profile_without_outer_scope(tmp_path):
    from genshin_app.agent_chat.native_dsh_domain_calls import (
        DshDomainTarget,
        NativeDshDomainCalls,
    )
    from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError
    from tests.test_native_app_tool_host import Session

    session = Session()
    session.db_path = tmp_path / "external-database/genshin.db"
    current = host(tmp_path / "ledger", session, lambda *_: {"code": 0})
    current.diagnostic_data_dir = tmp_path / "app-profile"

    def broken_metadata():
        raise TypeError("invalid internal metadata")

    target = DshDomainTarget("dsh-one", "conversation-1", current, model_metadata_reader=broken_metadata)
    dispatcher = NativeDshDomainCalls(lambda _sid: target)
    with pytest.raises(NativeDshMuxError) as caught:
        dispatcher("dsh_app_bind_request", {"contract": "genshin-native-dsh-domain-bind-v1",
            "request_id": "bind-one", "session_id": "dsh-one"})
    response = caught.value.failure["details"]
    identifier = response["system_fault"]["diagnostic_id"]
    assert read_system_diagnostic(current.diagnostic_data_dir, identifier)["operation"] == "dsh_app_bind_request"
    assert not (session.db_path.parent / "logs").exists()
