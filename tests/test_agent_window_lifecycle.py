from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from genshin_app.gui.agent_window import AgentWindowController, AgentWindowError


class Event:
    def __init__(self): self.handlers = []
    def __iadd__(self, handler): self.handlers.append(handler); return self


def window_events():
    return SimpleNamespace(**{name: Event() for name in (
        "closing", "maximized", "minimized", "restored", "moved", "resized")})


@pytest.fixture
def fixture(tmp_path):
    peer = Mock()
    application = Mock(create_client=Mock(return_value=peer))
    api = SimpleNamespace(_get_dsh_shell=lambda: application, _window=Mock(), dsh_client_boot=Mock())
    window = Mock(events=window_events())
    webview = Mock(create_window=Mock(return_value=window))
    controller = AgentWindowController(api, webview, tmp_path)
    return controller, api, application, peer, window, webview


def test_detach_closes_only_the_peer_preserves_draft_and_host(fixture):
    controller, _, application, peer, window, webview = fixture
    pending = controller.open({"session_id": "existing-session", "draft": {"text": "未发送"}})
    assert pending["presentation"] == "opening"
    assert webview.create_window.call_args.kwargs["hidden"] is True
    identity = webview.create_window.call_args.kwargs["js_api"]._identity
    controller.ready(identity, {"generation": pending["generation"]})
    assert controller.state()["presentation"] == "detached"
    controller.update_draft(identity, {"generation": pending["generation"], "session_id": "existing-session", "draft": {"text": "更新后的草稿"}})
    returned = controller.return_to_main(identity)
    assert returned["handoff"]["draft"]["text"] == "更新后的草稿"
    assert returned["presentation"] == "returning"
    peer.close.assert_not_called()
    window.destroy.assert_not_called()
    controller.main_ready({"generation": returned["generation"]})
    peer.close.assert_called_once()
    application.close.assert_not_called()
    window.destroy.assert_called_once()
    with pytest.raises(AgentWindowError, match="retired"):
        controller.command(identity, {"command": "review_candidate", "session_id": "existing-session", "generation": pending["generation"]})


def test_failed_window_creation_releases_peer_but_keeps_original_draft(fixture):
    controller, _, application, peer, _, webview = fixture
    webview.create_window.side_effect = RuntimeError("window failed")
    with pytest.raises(RuntimeError): controller.open({"session_id": "s", "draft": "keep"})
    assert controller.state()["presentation"] == "main"
    assert controller._handoff["draft"] == "keep"
    peer.close.assert_called_once()
    application.close.assert_not_called()


def test_cross_window_commands_are_only_navigation_not_write_receipts(fixture):
    controller, _, _, _, _, webview = fixture
    state = controller.open({"session_id": "s"})
    bridge = webview.create_window.call_args.kwargs["js_api"]
    assert not hasattr(bridge, "confirm_close")
    with pytest.raises(AgentWindowError, match="command_invalid"):
        bridge.dsh_window_command({"command": "evaluate_js", "session_id": "s", "generation": state["generation"]})
    result = bridge.dsh_window_command({"command": "review_candidate", "session_id": "s", "generation": state["generation"]})
    assert result["status"] == "queued_for_main_window"
    assert controller.poll()["commands"][0]["command_id"] == result["command_id"]
    controller.acknowledge(result)
    assert not controller.poll()["commands"]


def test_new_unreviewed_api_is_not_exposed(fixture):
    controller, api, _, _, _, webview = fixture
    api.dsh_client_future_unreviewed_mutation = Mock()
    controller.open({"session_id": "s"})
    assert not hasattr(webview.create_window.call_args.kwargs["js_api"], "dsh_client_future_unreviewed_mutation")


def test_retired_window_cleanup_is_limited_to_its_peer(fixture):
    from genshin_app.gui.agent_window import AgentWindowBridge
    controller, api, _, peer, _, webview = fixture
    api.dsh_client_stream_cancel = Mock()
    api.dsh_client_resource_release = Mock()
    peer.dsh_client_stream_cancel.return_value = {"cancelled": True}
    peer.dsh_client_resource_release.return_value = {"released": False}
    controller.open({"session_id": "s"})
    bridge = webview.create_window.call_args.kwargs["js_api"]
    controller.recover_main()  # Cancel an opening window, retiring its Peer.
    assert bridge.dsh_client_stream_cancel({"stream_id": "old"}) == {"cancelled": True}
    assert bridge.dsh_client_resource_release({"resource_id": "old"}) == {"released": False}
    api.dsh_client_stream_cancel.assert_not_called()
    with pytest.raises(AgentWindowError, match="retired"):
        bridge.dsh_client_boot()
    unknown = AgentWindowBridge(controller, peer, "never-opened")
    with pytest.raises(AgentWindowError, match="retired"):
        unknown.dsh_client_stream_cancel({"stream_id": "old"})


def test_document_wire_keeps_numbers_and_rejects_main_only_apis(fixture):
    import json
    controller, api, _, _, _, webview = fixture
    api.dsh_client_bind_document = Mock(side_effect=lambda value: value)
    api.load_plan = Mock()
    controller.open({"session_id": "s"})
    bridge = webview.create_window.call_args.kwargs["js_api"]
    result = json.loads(bridge.document_json_call("dsh_client_bind_document", '[{"value":1.0}]'))
    assert type(result["value"]) is float
    assert type(api.dsh_client_bind_document.call_args.args[0]["value"]) is float
    with pytest.raises(AgentWindowError, match="method_unavailable"):
        bridge.document_json_call("load_plan", '["not-opened"]')
    api.load_plan.assert_not_called()
    controller.recover_main()
    with pytest.raises(AgentWindowError, match="retired"):
        bridge.document_json_call("dsh_client_bind_document", '[{}]')


def test_cross_window_document_uses_lossless_string_and_checks_retirement(fixture):
    controller, api, _, _, _, webview = fixture
    controller.open({"session_id": "s"})
    bridge = webview.create_window.call_args.kwargs["js_api"]
    encoded = '{"document_kind":"native","document":{"value":1.0,"zero":0.0}}'
    api._window.evaluate_js.return_value = encoded
    assert bridge.dsh_window_document() == encoded
    api._window.evaluate_js.assert_called_once_with(
        "window.JSONTransport.stringify(window.GenshinEditorBridge.document())"
    )
    api._window.evaluate_js.return_value = {"document": {"value": 1}}
    with pytest.raises(AgentWindowError, match="transport_invalid"):
        bridge.dsh_window_document()
    def retire(_):
        controller.recover_main()
        return encoded
    api._window.evaluate_js.side_effect = retire
    with pytest.raises(AgentWindowError, match="retired"):
        bridge.dsh_window_document()


def test_session_selection_relay_keeps_official_drafts_and_generation(fixture):
    controller, _, application, _, _, webview = fixture
    pending = controller.open({"session_id": "s", "draft": {"semantic": {"text": "current"}},
        "drafts": {"other": {"semantic": {"text": "retain"}, "files": []}}})
    assert pending["handoff"]["drafts"]["other"]["semantic"]["text"] == "retain"
    bridge = webview.create_window.call_args.kwargs["js_api"]
    assert not hasattr(bridge, "dsh_window_select")
    selected = controller.select_session({"session_id": "other", "generation": pending["generation"]})
    assert selected["selection_revision"] == pending["selection_revision"] + 1
    assert controller.select_session({"session_id": "other", "generation": pending["generation"]}) == selected
    assert controller.state()["selection_handoff"] is None
    assert bridge.dsh_window_state()["selection_handoff"]["session_id"] == "other"
    bridge.dsh_window_selection_ready({"generation": pending["generation"], "selection_revision": selected["selection_revision"]})
    assert bridge.dsh_window_state()["selection_handoff"] is None
    with pytest.raises(AgentWindowError, match="stale"):
        controller.select_session({"session_id": "old", "generation": pending["generation"] - 1})
    application.create_client.assert_called_once()


def test_return_without_confirmed_draft_preserves_detached_window(fixture):
    controller, _, _, peer, window, webview = fixture
    state = controller.open({"session_id": "s"})
    bridge = webview.create_window.call_args.kwargs["js_api"]
    controller.ready(bridge._identity, {"generation": state["generation"]})
    with pytest.raises(AgentWindowError, match="未取得可交接"):
        controller.return_to_main(bridge._identity)
    assert controller.state()["presentation"] == "detached"
    peer.close.assert_not_called()
    window.destroy.assert_not_called()


def test_missing_browser_exit_snapshot_requires_confirmation(fixture):
    controller, api, _, _, _, _ = fixture
    api._dsh_shell = Mock(runtime_status=Mock(return_value={"agents": []}))
    api._window.evaluate_js.side_effect = lambda script, callback=None: callback({"ok": True, "value": {"unsent_count": 0, "sending": False, "unknown": True}}) if callback else None
    api._window.create_confirmation_dialog.return_value = False
    assert controller.prepare_application_close() is False
    assert "聊天草稿" in api._window.create_confirmation_dialog.call_args.args[1]


def test_return_waits_for_exact_receiving_generation_and_capture(fixture):
    controller, _, _, peer, window, webview = fixture
    state = controller.open({"session_id": "s", "draft": {"semantic": {"text": "old"}}})
    bridge = webview.create_window.call_args.kwargs["js_api"]
    controller.ready(bridge._identity, {"generation": state["generation"]})
    current = {"session_id": "s", "draft": {"semantic": {"text": "new", "quotes": [{"text": "quoted"}]}, "files": []}}
    window.evaluate_js.side_effect = lambda script, callback: callback({"ok": True, "value": current})
    returning = controller.recover_main()
    assert returning["handoff"] == current
    with pytest.raises(AgentWindowError, match="stale"):
        controller.main_ready({"generation": state["generation"]})
    peer.close.assert_not_called()
    window.destroy.assert_not_called()
    controller.main_ready({"generation": returning["generation"]})
    assert controller.state()["presentation"] == "main"
    assert controller.state()["handoff"] is None  # no repeated attachment copies in status polling


def test_capture_failure_preserves_live_window_and_host(fixture):
    controller, _, application, peer, window, webview = fixture
    state = controller.open({"session_id": "s"})
    identity = webview.create_window.call_args.kwargs["js_api"]._identity
    controller.ready(identity, {"generation": state["generation"]})
    window.evaluate_js.side_effect = lambda script, callback: callback({"ok": False, "error": "sending"})
    with pytest.raises(AgentWindowError, match="sending"):
        controller.recover_main()
    assert controller.state()["presentation"] == "detached"
    peer.close.assert_not_called()
    window.destroy.assert_not_called()
    application.close.assert_not_called()
    assert window.evaluate_js.call_args.args[0] == "window.GenshinWindowPresentation?.releaseCapture?.()"


def test_retired_open_cannot_replace_new_window(fixture):
    import threading
    controller, _, application, _, _, webview = fixture
    entered, resume = threading.Event(), threading.Event()
    old_peer, new_peer = Mock(), Mock()
    old_window = Mock(events=window_events())
    new_window = Mock(events=window_events())
    errors = []
    def create_peer(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert resume.wait(5)
            return old_peer
        return new_peer
    application.create_client.side_effect = create_peer
    webview.create_window.side_effect = [new_window, old_window]
    def old_open():
        try:
            controller.open({"session_id": "old"})
        except AgentWindowError as error:
            errors.append(error)
    thread = threading.Thread(target=old_open)
    thread.start()
    assert entered.wait(5)
    controller.recover_main()
    current = controller.open({"session_id": "new"})
    resume.set()
    thread.join(5)
    assert not thread.is_alive() and len(errors) == 1
    assert controller.state()["window_id"] == "main"
    assert controller.state()["generation"] == current["generation"]
    assert controller._window is new_window
    old_window.destroy.assert_called_once()
    old_peer.close.assert_called_once()
    new_window.destroy.assert_not_called()
    new_peer.close.assert_not_called()


def test_peer_close_error_still_destroys_window_without_closing_host(fixture):
    controller, _, application, peer, window, _ = fixture
    controller.open({"session_id": "s"})
    peer.close.side_effect = RuntimeError("peer gone")
    with pytest.raises(RuntimeError, match="peer gone"):
        controller.recover_main()
    window.destroy.assert_called_once()
    application.close.assert_not_called()
    assert controller.state()["presentation"] == "main"


@pytest.mark.parametrize("agents", [None, 42, {}])
def test_unknown_exit_status_can_be_cancelled(fixture, agents):
    controller, api, _, _, _, _ = fixture
    api._dsh_shell = Mock(runtime_status=Mock(return_value={"agents": agents}))
    api._window.evaluate_js.side_effect = lambda script, callback=None: callback({"ok": True, "value": {"unsent_count": 0, "sending": False}}) if callback else None
    api._window.create_confirmation_dialog.return_value = False
    assert controller.prepare_application_close() is False
    assert "暂时无法确认" in api._window.create_confirmation_dialog.call_args.args[1]
    assert controller.state()["closing"] is False


def test_cancel_opening_restores_original_without_waiting_for_browser(fixture):
    controller, _, _, peer, window, _ = fixture
    controller.open({"session_id": "s", "draft": {"semantic": {"text": "kept"}}})
    assert controller.recover_main()["presentation"] == "main"
    window.evaluate_js.assert_not_called()
    peer.close.assert_called_once()


def test_main_exit_uses_official_all_agent_status_and_respects_cancel(fixture):
    controller, api, _, _, _, _ = fixture
    api._dsh_shell = Mock(runtime_status=Mock(return_value={"agents": [{"session_id": "other", "status": "running"}]}))
    api._window.evaluate_js.side_effect = lambda script, callback: callback({"ok": True, "value": {"unsent_count": 0, "sending": False}})
    api._window.create_confirmation_dialog.return_value = False
    assert controller.prepare_application_close() is False
    api._dsh_shell.close.assert_not_called()
    assert "助手仍在运行" in api._window.create_confirmation_dialog.call_args.args[1]


def test_sending_exit_is_deferred_without_stopping_host(fixture):
    controller, api, _, _, _, _ = fixture
    api._window.evaluate_js.side_effect = lambda script, callback: callback({"ok": True, "value": {"unsent_count": 1, "sending": True}})
    with pytest.raises(AgentWindowError, match="消息正在提交"):
        controller.prepare_application_close()
    api._window.create_confirmation_dialog.assert_not_called()


def test_geometry_recovers_missing_monitor_and_constrains_zoomed_window():
    from genshin_app.gui.window_geometry import visible_geometry
    main = SimpleNamespace(x=0, y=0, width=1280, height=720)
    assert visible_geometry({"x": 4000, "y": -1000, "width": 1800, "height": 1200}, [main]) == {"x": 0, "y": 0, "width": 1280, "height": 720}
    left = SimpleNamespace(x=-1920, y=0, width=1920, height=1080)
    saved = {"x": -1800, "y": 40, "width": 620, "height": 820}
    assert visible_geometry(saved, [main, left]) == saved
