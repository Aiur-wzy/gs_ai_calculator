"""The external CLI owner cannot revive retired native inference paths."""
from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from genshin_app.agent_chat.manager import RETIRED_NATIVE_REASON, AgentManager
from genshin_app.equipment.schema import PlanError
from genshin_app.gui.api import GuiApi, _default_agent_manager_factory


def _old_native_history(tmp_path, session_id="a" * 32, *, legacy_provider=False):
    path = tmp_path / "agent_sessions" / session_id / "chat.json"
    path.parent.mkdir(parents=True)
    value = {
        "session_id": session_id, "state": "running", "seq": 1,
        "provider_id": "deepseek-api", "title": "历史原生对话", "kind": "whiteboard",
        "model": "deepseek-flash", "effort": "high", "workflow": "staged",
        "events": [{"seq": 1, "type": "status", "state": "running"}],
        "messages": [{"message_id": "m", "role": "user", "message": "已有要求"}],
        "candidate_summary": {"revision": 5, "ready": True},
    }
    if not legacy_provider:
        value["connection_kind"] = "native_api"
    raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)
    return session_id, path, raw


def test_default_factory_uses_active_database_and_does_not_probe_model_or_proxy(
    tmp_path, monkeypatch,
):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("CLI owner must not initialize native runtime")

    monkeypatch.setattr("genshin_app.gui.api._release_native_runtime_environment", forbidden)
    database = tmp_path / "active" / "release.db"
    manager = _default_agent_manager_factory(tmp_path / "user", database_path=database)
    assert type(manager) is AgentManager
    assert manager.db_path == database.resolve()
    assert manager._transport is None
    manager.shutdown()


def test_cli_detection_keeps_all_supported_cli_candidates_without_native_catalog(
    tmp_path, monkeypatch,
):
    expected = {"candidates": [{"id": "one", "kind": "cli", "supported": True},
                               {"id": "two", "kind": "cli", "supported": True}]}
    monkeypatch.setattr("genshin_app.agent_chat.transport.detect_cli", lambda: expected)
    assert AgentManager(tmp_path).detect() is expected


@pytest.mark.parametrize("connection", [
    "deepseek-api", "deepseek-official",
    {"id": "any-native", "kind": "native_api", "api_key": "secret"},
])
def test_native_connect_rejects_before_discovery_or_replacing_cli(tmp_path, connection):
    manager = AgentManager(tmp_path)
    manager.detect = lambda: pytest.fail("native connect must reject before discovery")
    transport = SimpleNamespace(close=lambda: pytest.fail("existing CLI must stay open"))
    manager._transport = transport
    with pytest.raises(PlanError) as caught:
        manager.connect(connection)
    assert caught.value.reason == RETIRED_NATIVE_REASON
    assert manager._transport is transport
    assert not manager.root.exists()


def test_discovery_cannot_smuggle_a_native_transport_into_cli_owner(tmp_path):
    manager = AgentManager(tmp_path)
    manager.detect = lambda: {"candidates": [{
        "id": "fake-cli", "kind": "native_api", "supported": True,
    }]}
    with pytest.raises(PlanError) as caught:
        manager.connect("fake-cli")
    assert caught.value.reason == RETIRED_NATIVE_REASON
    assert manager._transport is None


@pytest.mark.parametrize("legacy_provider", [False, True])
def test_old_native_history_is_read_only_without_repair_or_rewrite(tmp_path, legacy_provider):
    sid, path, raw = _old_native_history(tmp_path, legacy_provider=legacy_provider)
    manager = AgentManager(tmp_path)
    manager._transport = SimpleNamespace(close=lambda: None)
    manager._connection = {"connection": {"id": "cli"}, "state": "ready"}
    state = manager.poll(sid)
    assert state["messages"][0]["message"] == "已有要求"
    assert state["read_only"] and not state["active"]
    assert state["can_continue"] is state["can_resume"] is False
    assert state["candidate"]["ready"] is False
    assert state["candidate"]["revision"] == 5
    assert manager.candidate(sid) == state["candidate"]
    assert manager.sessions()["sessions"][0]["read_only_reason"] == RETIRED_NATIVE_REASON
    manager.shutdown()
    assert path.read_bytes() == raw
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("operation", ["send", "resume", "accept", "candidate_read",
                                       "resolve_app_action", "forget_credential"])
def test_old_native_mutation_paths_fail_without_touching_candidate(tmp_path, operation):
    sid, path, raw = _old_native_history(tmp_path)
    manager = AgentManager(tmp_path, candidate_factory=lambda *_a, **_k: pytest.fail("candidate"))
    calls = {
        "send": lambda: manager.send(sid, "继续", "old-native-request"),
        "resume": lambda: manager.resume(sid, {}),
        "accept": lambda: manager.accept(sid, "whiteboard", {}, 5, "receipt"),
        "candidate_read": lambda: manager.candidate_read(sid, "candidate"),
        "resolve_app_action": lambda: manager.resolve_app_action(sid, "action", "completed"),
        "forget_credential": lambda: manager.forget_credential("deepseek-api"),
    }
    with pytest.raises(PlanError) as caught:
        calls[operation]()
    assert caught.value.reason == RETIRED_NATIVE_REASON
    assert path.read_bytes() == raw
    assert manager._worker is None


def test_old_native_record_can_still_be_explicitly_deleted(tmp_path):
    sid, path, _raw = _old_native_history(tmp_path)
    manager = AgentManager(tmp_path)
    assert manager.forget(sid) == {"deleted": True}
    assert not path.parent.exists()


def test_retired_gui_operation_returns_a_stable_error_instead_of_attribute_error(tmp_path):
    api = GuiApi(tmp_path, agent_manager_factory=lambda root: AgentManager(root))
    result = api._agent_call("prepare_runtime")
    assert result["code"] == 1 and result["reason"] == RETIRED_NATIVE_REASON


def test_production_import_graph_does_not_load_retired_native_owner():
    script = """
import sys
from genshin_app.gui.api import _default_agent_manager_factory
from genshin_app.agent_chat import native_dsh_mux_process
from genshin_app.agent_chat import native_dsh_production
from genshin_app.agent_chat import native_candidate_application_backend
from genshin_app.agent_chat import native_candidate_application_port
from genshin_app.agent_chat import native_candidate_projection_port
from genshin_app.agent_chat import native_error_diagnostics
blocked = {
    'gui_router_v2', 'native_production_v2', 'native_service_v2',
    'native_connection_v2', 'native_runtime_process', 'native_transport',
}
assert not [name for name in sys.modules
            if name.startswith('genshin_app.agent_chat.')
            and name.rsplit('.', 1)[-1] in blocked]
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
