import json
from pathlib import Path

import pytest

from genshin_app.agent_chat import native_provider_proxy, native_runtime_environment
from genshin_app.agent_chat.native_provider_proxy import NativeProviderProxyError
from genshin_app.agent_chat.transport import TransportError
from genshin_app.gui import api as gui_api


class _Manager:
    def detect(self):
        return {"candidates": [{"id": "deepseek-api", "kind": "native_api"}]}

    def whiteboard_apply_equipment(self, session_id, capability, board):
        return {
            "session_id": session_id,
            "capability": capability,
            "board": board,
        }

    def recover_app_action_without_replay(
        self, session_id, action_id, ui_receipt
    ):
        return {
            "session_id": session_id,
            "action_id": action_id,
            "ui_receipt": ui_receipt,
            "request": None,
        }

    def runtime_status(self, after_seq, subscription_generation):
        return {
            "contract": "native-runtime-status-v1",
            "requested_after_seq": after_seq,
            "next_seq": after_seq,
            "subscription_generation": subscription_generation,
            "events": [],
        }

    def wait_runtime_status(self, after_seq, subscription_generation, timeout_ms):
        return {
            **self.runtime_status(after_seq, subscription_generation),
            "timed_out": timeout_ms == 0,
        }

    def notify_system_resume(self):
        return {"system_resume_generation": 2, "automatic_provider_retry": False}

    def prepare_runtime(self, purpose):
        return {"operation_id": "runtime-prepare-test", "purpose": purpose, "status": "preparing"}

    def runtime_prepare_status(self, operation_id):
        return {"operation_id": operation_id, "status": "ready"}

    def wait_runtime_prepare(self, operation_id, timeout_ms):
        return {"operation_id": operation_id, "status": "ready", "timeout_ms": timeout_ms}

    def cancel_runtime_prepare(self, operation_id):
        return {"operation_id": operation_id, "status": "cancelling"}

    def turn_outline(self, session_id):
        return {"contract": "agent-turn-outline-v1", "session_id": session_id}

    def search_conversation(self, session_id, request):
        return {
            "contract": "agent-search-results-v1",
            "session_id": session_id,
            "request": request,
        }

    def load_turn_window(self, session_id, request):
        return {
            "window": {
                "contract": "agent-history-window-v1",
                "session_id": session_id,
                "anchor": request,
            }
        }

    def telemetry_bundle(self, session_id):
        return {
            "trajectory": {"contract": "agent-trajectory-v1", "session_id": session_id},
            "context": {"contract": "agent-context-metrics-v1", "session_id": session_id},
            "performance": {"contract": "agent-performance-metrics-v1", "session_id": session_id},
        }

    def workbench_projection(self, session_id):
        return {"contract": "workbench", "session_id": session_id}

    def submit_question_batch_answer(self, session_id, request):
        return {"command": "answer", "session_id": session_id, "request": request}

    def enqueue_workbench_inbox(self, session_id, request):
        return {"command": "enqueue", "session_id": session_id, "request": request}

    def revise_workbench_inbox(self, session_id, request):
        return {"command": "revise", "session_id": session_id, "request": request}

    def cancel_workbench_inbox(self, session_id, request):
        return {"command": "cancel", "session_id": session_id, "request": request}

    def reorder_workbench_inbox(self, session_id, request):
        return {"command": "reorder", "session_id": session_id, "request": request}

    def promote_workbench_inbox(self, session_id, request):
        return {"command": "promote", "session_id": session_id, "request": request}

    def list_document_checkpoints(self, session_id, *, offset=0, limit=50):
        return {
            "contract": "agent-document-checkpoint-list-v1",
            "session_id": session_id,
            "offset": offset,
            "limit": limit,
            "items": [],
        }

    def rename_session(self, session_id, request):
        return {"command": "rename", "session_id": session_id, "request": request}

    def archive_session(self, session_id, request):
        return {"command": "archive", "session_id": session_id, "request": request}

    def mark_session_read(self, session_id, request):
        return {"command": "read", "session_id": session_id, "request": request}


class _PayloadManager(_Manager):
    def __init__(self):
        self.calls = []

    def _record(self, name, *args):
        self.calls.append((name, args))
        return {"name": name, "args": args}

    def send(self, *args):
        return self._record("send", *args)

    def resume(self, *args):
        return self._record("resume", *args)

    def accept(self, *args):
        return self._record("accept", *args)

    def candidate_applied(self, *args):
        return self._record("candidate_applied", *args)

    def abort_candidate_application(self, *args):
        return self._record("abort_candidate_application", *args)

    def recover_candidate_application(self, *args):
        return self._record("recover_candidate_application", *args)

    def recover_last_applied_whiteboard_projection(self, *args):
        return self._record("recover_last_applied_whiteboard_projection", *args)


def test_gui_api_constructs_default_agent_router_lazily(tmp_path, monkeypatch):
    created = []

    def factory(data_dir, *, database_path):
        assert database_path == tmp_path / "genshin.db"
        created.append(Path(data_dir))
        return _Manager()

    monkeypatch.setattr(gui_api, "_default_agent_manager_factory", factory)
    api = gui_api.GuiApi(tmp_path)
    assert created == []
    assert api.agent_detect()["data"]["candidates"][0]["id"] == "deepseek-api"
    assert created == [tmp_path]
    api.agent_detect()
    assert created == [tmp_path]


def test_gui_api_forwards_host_session_lifecycle_requests(tmp_path):
    api = gui_api.GuiApi(tmp_path, agent_manager_factory=lambda _root: _Manager())
    rename = {"title": "新名称", "expected_revision": 3, "mutation_id": "rename-one"}
    archive = {"archived": True, "expected_revision": 4, "mutation_id": "archive-one"}
    read = {"expected_revision": 5, "mutation_id": "read-one"}
    assert api.agent_rename_session("conversation-one", rename)["data"] == {
        "command": "rename", "session_id": "conversation-one", "request": rename,
    }
    assert api.agent_archive_session("conversation-one", archive)["data"] == {
        "command": "archive", "session_id": "conversation-one", "request": archive,
    }
    assert api.agent_mark_session_read("conversation-one", read)["data"] == {
        "command": "read", "session_id": "conversation-one", "request": read,
    }


def test_gui_api_forwards_explicit_database_only_to_default_router(tmp_path, monkeypatch):
    captured = {}

    def factory(data_dir, *, database_path=None):
        captured.update(data_dir=Path(data_dir), database_path=Path(database_path))
        return _Manager()

    shared = tmp_path / "source" / "genshin.db"
    monkeypatch.setattr(gui_api, "_default_agent_manager_factory", factory)
    api = gui_api.GuiApi(tmp_path / "profile", database_path=shared)
    assert api.agent_detect()["code"] == 0
    assert captured == {
        "data_dir": tmp_path / "profile",
        "database_path": shared.resolve(),
    }


def test_gui_api_routes_opaque_native_whiteboard_recalculation(tmp_path):
    api = gui_api.GuiApi(tmp_path, agent_manager_factory=lambda _root: _Manager())
    board = {"board_id": "board-one"}
    result = api.agent_whiteboard_apply_equipment(
        "conversation-one", "wdc_v1_test", board
    )
    assert result == {
        "code": 0,
        "data": {
            "session_id": "conversation-one",
            "capability": "wdc_v1_test",
            "board": board,
        },
    }


def test_gui_api_routes_exact_unknown_action_recovery(tmp_path):
    api = gui_api.GuiApi(tmp_path, agent_manager_factory=lambda _root: _Manager())
    result = api.agent_recover_app_action_without_replay(
        "conversation-one",
        "action_" + "a" * 32,
        "ui_" + "b" * 43,
    )
    assert result == {
        "code": 0,
        "data": {
            "session_id": "conversation-one",
            "action_id": "action_" + "a" * 32,
            "ui_receipt": "ui_" + "b" * 43,
            "request": None,
        },
    }
    transported = json.loads(
        api.document_json_call(
            "agent_recover_app_action_without_replay",
            json.dumps(
                [
                    "conversation-one",
                    "action_" + "a" * 32,
                    "ui_" + "b" * 43,
                ]
            ),
        )
    )
    assert transported == result


def test_gui_api_routes_checkpoint_paging_through_exact_document_bridge(tmp_path):
    api = gui_api.GuiApi(tmp_path, agent_manager_factory=lambda _root: _Manager())

    transported = json.loads(
        api.document_json_call(
            "agent_list_document_checkpoints",
            json.dumps(["conversation-one", 50, 25]),
        )
    )

    assert transported == {
        "code": 0,
        "data": {
            "contract": "agent-document-checkpoint-list-v1",
            "session_id": "conversation-one",
            "offset": 50,
            "limit": 25,
            "items": [],
        },
    }


def test_checkpoint_paging_prewrite_failure_is_not_reported_as_submitted(tmp_path):
    class RejectingManager(_Manager):
        def list_document_checkpoints(self, session_id, *, offset=0, limit=50):
            raise TypeError("invalid checkpoint pagination")

    api = gui_api.GuiApi(
        tmp_path,
        agent_manager_factory=lambda _root: RejectingManager(),
    )
    transported = json.loads(
        api.document_json_call(
            "agent_list_document_checkpoints",
            json.dumps(["conversation-one", 0, 50]),
        )
    )

    assert transported["code"] == 1
    assert transported["reason"] == "agent_operation"
    assert "operation_may_have_completed" not in transported
    assert "已提交" not in transported["message"]


def test_gui_api_routes_independent_runtime_status_and_prepare_contracts(tmp_path):
    api = gui_api.GuiApi(tmp_path, agent_manager_factory=lambda _root: _Manager())

    assert api.agent_runtime_status(4, 7)["data"]["requested_after_seq"] == 4
    assert api.agent_wait_runtime_status(4, 7, 0)["data"]["timed_out"] is True
    assert api.agent_notify_system_resume()["data"] == {
        "system_resume_generation": 2,
        "automatic_provider_retry": False,
    }
    started = api.agent_prepare_runtime("send")["data"]
    assert started == {
        "operation_id": "runtime-prepare-test",
        "purpose": "send",
        "status": "preparing",
    }
    assert api.agent_runtime_prepare_status(started["operation_id"])["data"]["status"] == "ready"
    assert api.agent_wait_runtime_prepare(started["operation_id"], 12000)["data"]["status"] == "ready"
    assert api.agent_cancel_runtime_prepare(started["operation_id"])["data"]["status"] == "cancelling"

    transported = json.loads(api.document_json_call(
        "agent_runtime_status",
        json.dumps([4, 7]),
    ))
    assert transported["data"]["subscription_generation"] == 7


def test_gui_api_routes_workbench_outline_search_and_history_window(tmp_path):
    api = gui_api.GuiApi(tmp_path, agent_manager_factory=lambda _root: _Manager())

    assert api.agent_turn_outline("conversation-one")["data"] == {
        "contract": "agent-turn-outline-v1",
        "session_id": "conversation-one",
    }
    request = {"query": "反应", "query_digest": "a" * 64, "limit": 20}
    assert api.agent_search_conversation("conversation-one", request)["data"][
        "request"
    ] == request
    locator = {
        "turn_id": "turn-one",
        "source_seq": 1,
        "stable_node_id": "message:one",
    }
    assert api.agent_load_turn_window("conversation-one", locator)["data"][
        "window"
    ]["anchor"] == locator
    assert api.agent_telemetry_bundle("conversation-one")["data"][
        "trajectory"
    ]["contract"] == "agent-trajectory-v1"


def test_gui_api_routes_structured_question_and_inbox_commands(tmp_path):
    api = gui_api.GuiApi(tmp_path, agent_manager_factory=lambda _root: _Manager())
    request = {"mutation_id": "mutation-one"}

    assert api.agent_workbench_projection("conversation-one")["data"] == {
        "contract": "workbench",
        "session_id": "conversation-one",
    }
    methods = {
        "agent_submit_question_batch_answer": "answer",
        "agent_enqueue_workbench_inbox": "enqueue",
        "agent_revise_workbench_inbox": "revise",
        "agent_cancel_workbench_inbox": "cancel",
        "agent_reorder_workbench_inbox": "reorder",
        "agent_promote_workbench_inbox": "promote",
    }
    for method, expected in methods.items():
        response = getattr(api, method)("conversation-one", request)
        assert response["data"] == {
            "command": expected,
            "session_id": "conversation-one",
            "request": request,
        }


def test_gui_api_forwards_exact_application_payload_receipts(tmp_path):
    manager = _PayloadManager()
    api = gui_api.GuiApi(tmp_path, agent_manager_factory=lambda _root: manager)
    document = {"board_id": "board-one"}
    payload = {
        "kind": "whiteboard",
        "board": document,
        "calculated": {"result": {"summary": {"total_damage": 12}}},
    }
    workspace = {"document_kind": "whiteboard", "display_name": "测试"}

    assert api.agent_send(
        "conversation-one", "继续", "request-one", document, workspace, payload
    )["code"] == 0
    assert api.agent_resume(
        "conversation-one", document, workspace, payload
    )["code"] == 0
    assert api.agent_accept(
        "conversation-one", "whiteboard", document, 3, "a" * 64, payload
    )["code"] == 0
    assert api.agent_candidate_applied(
        "conversation-one", "whiteboard", document, 3, "a" * 64,
        workspace, payload,
    )["code"] == 0
    assert api.agent_abort_candidate_application(
        "conversation-one", document, workspace, payload
    )["code"] == 0
    assert api.agent_recover_candidate_application(
        "conversation-one", document, workspace, payload, True
    )["code"] == 0
    assert api.agent_recover_last_applied_whiteboard_projection(
        "conversation-one", document, workspace, payload, True
    )["code"] == 0

    assert manager.calls == [
        (
            "send",
            (
                "conversation-one", "继续", "request-one", document,
                workspace, payload,
            ),
        ),
        ("resume", ("conversation-one", document, workspace, payload)),
        (
            "accept",
            ("conversation-one", "whiteboard", document, 3, "a" * 64, payload),
        ),
        (
            "candidate_applied",
            (
                "conversation-one", "whiteboard", document, 3, "a" * 64,
                workspace, payload,
            ),
        ),
        (
            "abort_candidate_application",
            ("conversation-one", document, workspace, payload),
        ),
        (
            "recover_candidate_application",
            ("conversation-one", document, workspace, payload, True),
        ),
        (
            "recover_last_applied_whiteboard_projection",
            ("conversation-one", document, workspace, payload, True),
        ),
    ]


def test_default_external_cli_owner_does_not_create_native_production(tmp_path, monkeypatch):
    from genshin_app.agent_chat.manager import AgentManager

    def forbidden():
        pytest.fail("CLI initialization must not resolve native provider environment")

    monkeypatch.setattr(gui_api, "_release_native_runtime_environment", forbidden)
    database = tmp_path / "active.db"
    manager = gui_api._default_agent_manager_factory(tmp_path, database_path=database)
    assert type(manager) is AgentManager
    assert manager.db_path == database.resolve()
    manager.shutdown()


def test_release_environment_allows_explicit_direct_without_ambient_proxy(
    monkeypatch,
):
    monkeypatch.setattr(
        native_provider_proxy,
        "read_windows_native_provider_proxy_or_direct",
        dict,
    )
    monkeypatch.setattr(
        native_runtime_environment,
        "build_native_runtime_environment",
        lambda: {
            "SYSTEMROOT": r"C:\Windows",
            "DSH_TELEMETRY_DISABLED": "1",
            "NO_COLOR": "1",
        },
    )

    assert gui_api._release_native_runtime_environment() == {
        "SYSTEMROOT": r"C:\Windows",
        "DSH_TELEMETRY_DISABLED": "1",
        "NO_COLOR": "1",
    }


@pytest.mark.parametrize(
    "reason",
    [
        "native_provider_proxy_pac_unsupported",
        "native_provider_proxy_credentials_forbidden",
        "native_provider_proxy_system_format_unsupported",
        "native_provider_proxy_windows_read_failed",
    ],
)
def test_release_environment_reports_unsupported_proxy_without_fallback_or_value(
    tmp_path,
    monkeypatch,
    reason,
):
    def fail():
        raise NativeProviderProxyError(reason)

    monkeypatch.setattr(
        native_provider_proxy,
        "read_windows_native_provider_proxy_or_direct",
        fail,
    )
    with pytest.raises(TransportError) as caught:
        gui_api._release_native_runtime_environment()
    assert caught.value.code == reason
    assert "secret" not in caught.value.message
    assert "proxy.pac" not in caught.value.message
    assert "secret" not in repr(caught.value)
    assert not (tmp_path / "logs").exists()
