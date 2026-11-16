"""Native presentation ownership; conversations and execution remain owned by DSH.

The detached window receives its own DSH Peer, never another GuiApi/application.
Domain requests which edit a document are presented to the main editor. No code
or arbitrary method name supplied by a renderer is evaluated across windows.
"""
from __future__ import annotations

import copy
import logging
import threading
import uuid
from pathlib import Path

_SHELL_METHODS = frozenset({"dsh_client_boot", "dsh_client_rpc", "dsh_client_request_cancel",
    "dsh_client_stream_open", "dsh_client_stream_read", "dsh_client_stream_send", "dsh_client_stream_cancel",
    "dsh_client_bundle", "dsh_client_resource_read", "dsh_client_resource_release"})
_COMMANDS = frozenset({"open_candidate", "review_candidate", "file_action", "show_page", "reveal_timeline_event"})
_CLEANUP_METHODS = frozenset({"dsh_client_request_cancel", "dsh_client_stream_cancel", "dsh_client_resource_release"})
_DOMAIN_METHODS = frozenset({
    "dsh_client_credential_status", "dsh_client_credential_save", "dsh_client_credential_delete",
    "dsh_client_runtime_status", "dsh_client_session_status", "dsh_client_bind_document",
    "dsh_client_candidate", "dsh_client_incompatible_applications", "dsh_client_abandon_incompatible_application",
    "dsh_client_candidate_read", "dsh_client_candidate_command", "dsh_client_domain_approvals",
    "dsh_client_domain_approval_decide", "dsh_client_workspace_state", "dsh_client_app_actions",
    "dsh_client_app_action", "dsh_client_publish_candidate_input_draft", "dsh_client_request_candidate_input_draft",
    "dsh_client_open_candidate_input_draft", "dsh_client_import_candidate_input_draft",
    "dsh_client_candidate_input_draft_preview_read", "dsh_client_file_dialog", "dsh_client_whiteboard_projection",
    "dsh_client_saved_whiteboard", "dsh_client_whiteboard_apply_equipment", "dsh_client_publish_whiteboard_result",
    "dsh_client_save_whiteboard_result", "dsh_client_domain_binding", "agent_budget_settings",
    "agent_update_budget_settings", "agent_reset_budget_settings", "agent_dsh_settings",
    "agent_update_dsh_settings", "agent_reset_dsh_settings",
})


class AgentWindowError(ValueError):
    pass


class AgentWindowBridge:
    """Only reviewed domain methods plus a connection-scoped official DSH Peer."""

    def __init__(self, owner, peer, identity):
        self._owner, self._peer, self._identity = owner, peer, identity
        for name in _SHELL_METHODS | _DOMAIN_METHODS:
            if callable(getattr(owner.api, name, None)):
                setattr(self, name, self._forward(name))

    def _forward(self, name):
        def invoke(*args, **kwargs):
            if name in _CLEANUP_METHODS and self._owner.is_retired_client(self._identity):
                # Only this window's old Peer: its idempotent cleanup validates
                # request identities and cannot cancel another window's work.
                return getattr(self._peer, name)(*args, **kwargs)
            self._owner.require_client(self._identity)
            target = self._peer if name in _SHELL_METHODS else self._owner.api
            if name in {"dsh_client_file_dialog", "dsh_client_save_whiteboard_result", "dsh_client_open_candidate_input_draft"}:
                self._owner.focus_main()
            return getattr(target, name)(*args, **kwargs)
        return invoke

    def dsh_window_state(self):
        return self._owner.state(self._identity)

    def dsh_window_ready(self, request):
        return self._owner.ready(self._identity, request)

    def dsh_window_return(self, request):
        return self._owner.return_to_main(self._identity, request)

    def dsh_window_document(self):
        return self._owner.main_document(self._identity)

    def document_json_call(self, method, args_text):
        """Use the document wire without exposing the main editor's other APIs."""
        self._owner.require_client(self._identity)
        if method not in _DOMAIN_METHODS:
            raise AgentWindowError("agent_window_document_method_unavailable")
        from .document_transport import dispatch
        # Dispatch through this Peer facade, preserving its retirement and
        # focus guards; do not turn the main GuiApi into a general RPC escape.
        return dispatch(self, method, args_text)

    def dsh_window_command(self, request):
        return self._owner.command(self._identity, request)

    def dsh_window_draft(self, request):
        return self._owner.update_draft(self._identity, request)

    def dsh_window_selection_ready(self, request):
        return self._owner.selection_ready(self._identity, request)


class AgentWindowController:
    def __init__(self, api, webview, resources: Path):
        self.api, self.webview, self.resources = api, webview, Path(resources)
        self._lock = threading.RLock()
        self._window = None
        self._peer = None
        self._identity = None
        self._retired_identities = set()
        self._generation = 0
        self._presentation = "main"
        self._handoff = None
        self._commands = []
        self._command_result = None
        self._closed = False
        self._return_busy = False
        self._error = None
        self._closing = False
        self._selected_session_id = None
        self._selection_revision = 0
        self._selection_handoff = None
        self._geometry_path = Path(api.data_dir) / "agent-window-geometry.json" if getattr(api, "data_dir", None) else None

    def state(self, identity="main"):
        with self._lock:
            if identity != "main": self.require_client(identity)
            return {"role": "main" if identity == "main" else "detached", "window_id": identity,
                    "generation": self._generation, "presentation": self._presentation,
                    "handoff": copy.deepcopy(self._handoff) if self._presentation in {"opening", "returning"} else None,
                    "return_source_id": self._identity if self._presentation == "returning" else None,
                    "error": self._error, "closing": self._closing,
                    "selected_session_id": self._selected_session_id, "selection_revision": self._selection_revision,
                    "selection_handoff": copy.deepcopy(self._selection_handoff) if identity != "main" else None,
                    "command_result": copy.deepcopy(self._command_result)}

    def require_client(self, identity):
        with self._lock:
            if self._closed or identity != self._identity:
                raise AgentWindowError("agent_window_client_retired")

    def is_retired_client(self, identity):
        with self._lock:
            return identity in self._retired_identities

    @staticmethod
    def _read_handoff(request):
        if not isinstance(request, dict) or not isinstance(request.get("session_id"), str):
            raise AgentWindowError("agent_window_session_required")
        # Only unsent UI content. Chat, task and candidate state are never copied.
        handoff = {"session_id": request["session_id"], "draft": copy.deepcopy(request.get("draft"))}
        if "drafts" in request:
            if not isinstance(request["drafts"], dict):
                raise AgentWindowError("agent_window_drafts_invalid")
            handoff["drafts"] = copy.deepcopy(request["drafts"])
        return handoff

    def open(self, request):
        handoff = self._read_handoff(request)
        with self._lock:
            if self._closed or self._closing: raise AgentWindowError("agent_window_application_closing")
            if self._presentation != "main":
                if self._window: self._window.show(); self._window.restore()
                return self.state()
            self._generation += 1
            self._error = None
            self._handoff, self._presentation = handoff, "opening"
            self._selected_session_id = handoff["session_id"]
            self._selection_revision += 1
            self._selection_handoff = None
            identity = str(uuid.uuid4())
            self._identity = identity
            generation = self._generation
        peer = window = None
        try:
            peer = self.api._get_dsh_shell().create_client(identity, interactions="answer")
            bridge = AgentWindowBridge(self, peer, identity)
            from .window_geometry import load_geometry, track_normal_geometry
            geometry = load_geometry(self._geometry_path, list(self.webview.screens)) if self._geometry_path else {"width": 620, "height": 820}
            window = self.webview.create_window("原神拉表 · AI 助手", url=str(self.resources / "agent-window.html"),
                js_api=bridge, **geometry, min_size=(400, 420), text_select=True, hidden=True)
            track_normal_geometry(window, geometry)
            with self._lock:
                if self._closed or self._closing or self._identity != identity or self._generation != generation:
                    raise AgentWindowError("聊天窗口的打开操作已取消。")
                self._peer, self._window = peer, window
            window.events.closing += lambda: self._on_closing(identity)
            return self.state()
        except Exception:
            with self._lock:
                self._retired_identities.add(identity)
                if self._identity == identity and self._generation == generation:
                    self._presentation, self._identity = "main", None
                    self._peer, self._window = None, None
            try:
                if peer is not None: peer.close()
            finally:
                if window is not None: window.destroy()
            raise

    def recover_main(self):
        with self._lock:
            if self._closing: raise AgentWindowError("应用正在确认退出，请先完成或取消退出。")
            identity = self._identity
            opening = self._presentation == "opening"
            if self._presentation == "returning": return self.state()
        if not identity: return self.state()
        if opening:
            return self._commit_main(identity)
        with self._lock:
            window = self._window
        try:
            request = self._capture_detached()
            return self.return_to_main(identity, request)
        except Exception:
            if window is not None:
                try:
                    window.evaluate_js("window.GenshinWindowPresentation?.releaseCapture?.()")
                except Exception:
                    logging.getLogger(__name__).warning("恢复聊天草稿输入未成功", exc_info=True)
            raise

    def _capture_detached(self):
        with self._lock:
            window, generation = self._window, self._generation
        done = threading.Event()
        result = []
        def receive(value):
            result.append(value)
            done.set()
        if window is None: raise AgentWindowError("agent_window_unavailable")
        window.evaluate_js("Promise.resolve().then(() => window.GenshinWindowPresentation.capture()).then(value => ({ok:true,value}), error => ({ok:false,error:String(error)}))", callback=receive)
        if not done.wait(30):
            raise AgentWindowError("未能确认未发送草稿，聊天窗口已保留，请在窗口内重试返回。")
        if not result[0].get("ok"):
            raise AgentWindowError(result[0].get("error", "未发送草稿尚未就绪，请重试。"))
        request = self._read_handoff(result[0]["value"])
        return {**request, "generation": generation}

    def ready(self, identity, request):
        with self._lock:
            self.require_client(identity)
            if request.get("generation") != self._generation or self._presentation != "opening":
                raise AgentWindowError("agent_window_handoff_stale")
            self._presentation = "detached"
            self._handoff = None
            window = self._window
        window.show()
        return self.state(identity)

    def update_draft(self, identity, request):
        handoff = self._read_handoff(request)
        with self._lock:
            self.require_client(identity)
            if request.get("generation") != self._generation:
                raise AgentWindowError("agent_window_handoff_stale")
            self._handoff = handoff
        return {"saved": True}

    def return_to_main(self, identity, request=None):
        with self._lock:
            self.require_client(identity)
            if self._closing: raise AgentWindowError("agent_window_application_closing")
            if request is not None: self.update_draft(identity, request)
            if self._presentation == "returning": return self.state(identity)
            if self._handoff is None:
                raise AgentWindowError("未取得可交接的草稿，原聊天窗口已保留，请重新点击返回。")
            self._generation += 1
            self._presentation = "returning"
            self._error = None
        self.focus_main()
        return self.state(identity)

    def main_ready(self, request):
        with self._lock:
            if self._presentation != "returning" or request.get("generation") != self._generation:
                raise AgentWindowError("agent_window_handoff_stale")
            identity = self._identity
        return self._commit_main(identity, expected_generation=request["generation"])

    def cancel_return(self, request):
        """Cancel only an unacknowledged presentation transfer, never a task.

        The main renderer offers this after restoring every destination draft.
        The source remains authoritative until main_ready commits. Generation
        and source identity make delayed ACKs and stale cancel clicks harmless.
        """
        if (not isinstance(request, dict) or type(request.get("generation")) is not int
                or not isinstance(request.get("source_window_id"), str)):
            raise AgentWindowError("agent_window_handoff_stale")
        generation, identity = request["generation"], request["source_window_id"]

        def require_pending():
            if self._closed or self._closing:
                raise AgentWindowError("agent_window_application_closing")
            if (self._presentation != "returning" or generation != self._generation
                    or identity != self._identity):
                raise AgentWindowError("agent_window_handoff_stale")
            if self._peer is None or self._window is None or self._handoff is None:
                raise AgentWindowError("agent_window_return_source_unavailable")

        with self._lock:
            require_pending()
            window = self._window
        # Do not retire the transfer until the original window can be reached.
        # Native window calls stay outside the state lock.
        try:
            window.show()
            window.restore()
        except Exception as error:
            raise AgentWindowError("agent_window_return_source_unavailable") from error
        with self._lock:
            require_pending()
            if window is not self._window:
                raise AgentWindowError("agent_window_handoff_stale")
            self._generation += 1
            self._presentation = "detached"
            self._handoff = None
            self._error = None
            return self.state()

    def _commit_main(self, identity, *, expected_generation=None):
        with self._lock:
            if expected_generation is not None and (
                    self._presentation != "returning" or self._generation != expected_generation):
                raise AgentWindowError("agent_window_handoff_stale")
            self.require_client(identity)
            self._presentation = "main"
            self._handoff = None
            self._selection_handoff = None
            peer, window = self._peer, self._window
            self._retired_identities.add(identity)
            self._peer, self._window, self._identity = None, None, None
        # Closing only this Peer cannot stop the Agent or the main interaction receiver.
        try:
            if peer is not None: peer.close()
        finally:
            if window is not None:
                self._save_geometry(window)
                window.destroy()
        self.focus_main()
        return self.state()

    def _on_closing(self, identity):
        with self._lock:
            if identity != self._identity or self._closed: return True
            if self._return_busy: return False
            self._return_busy = True
        # Do not wait for browser/DSH from the native UI closing callback.
        def finish():
            try:
                self.recover_main()
            except Exception as error:
                logging.getLogger(__name__).exception("聊天窗口交接未完成，原窗口已保留")
                with self._lock: self._error = str(error)
                self.focus_main()
            finally:
                with self._lock: self._return_busy = False
        threading.Thread(target=finish, daemon=True, name="agent-window-return").start()
        return False

    def focus_main(self):
        if self.api._window is not None:
            self.api._window.show()
            self.api._window.restore()

    def main_document(self, identity):
        self.require_client(identity)
        if self.api._window is None: raise AgentWindowError("agent_window_main_unavailable")
        # evaluate_js serializes objects through ordinary JSON. Transport the
        # lossless document as a string so 0.0/1.0 and source-bound receipts
        # have exactly the same identity in both windows.
        encoded = self.api._window.evaluate_js(
            "window.JSONTransport.stringify(window.GenshinEditorBridge.document())"
        )
        self.require_client(identity)
        if not isinstance(encoded, str):
            raise AgentWindowError("agent_window_document_transport_invalid")
        return encoded

    def command(self, identity, request):
        if not isinstance(request, dict) or request.get("command") not in _COMMANDS:
            raise AgentWindowError("agent_window_command_invalid")
        if not isinstance(request.get("session_id"), str): raise AgentWindowError("agent_window_session_required")
        with self._lock:
            self.require_client(identity)
            if self._closing: raise AgentWindowError("agent_window_application_closing")
            if request.get("generation") != self._generation:
                raise AgentWindowError("agent_window_handoff_stale")
            command = {key: copy.deepcopy(request[key]) for key in ("command", "session_id", "conversation_id", "revision", "action_id", "page", "selection") if key in request}
            command["command_id"] = str(uuid.uuid4())
            self._commands.append(command)
        self.focus_main()
        return {"status": "queued_for_main_window", "command_id": command["command_id"]}

    def poll(self):
        with self._lock:
            return {**self.state(), "commands": copy.deepcopy(self._commands)}

    def select_session(self, request):
        """Main-window presentation request; official DSH still owns sessions."""
        if not isinstance(request, dict) or not isinstance(request.get("session_id"), str) or not request["session_id"].strip():
            raise AgentWindowError("agent_window_session_required")
        with self._lock:
            if self._closed or self._closing:
                raise AgentWindowError("agent_window_application_closing")
            if request.get("generation") != self._generation:
                raise AgentWindowError("agent_window_handoff_stale")
            if self._presentation not in {"opening", "detached"}:
                return self.state()
            if self._selected_session_id != request["session_id"]:
                self._selected_session_id = request["session_id"]
                self._selection_handoff = self._read_handoff(request)
                self._selection_revision += 1
            return self.state()

    def selection_ready(self, identity, request):
        with self._lock:
            self.require_client(identity)
            if request.get("generation") != self._generation or request.get("selection_revision") != self._selection_revision:
                raise AgentWindowError("agent_window_handoff_stale")
            self._selection_handoff = None
            return {"acknowledged": True}

    def acknowledge(self, request):
        with self._lock:
            command = next((item for item in self._commands if item["command_id"] == request.get("command_id")), None)
            if command is None:
                return {"acknowledged": False}
            if isinstance(request.get("message"), str) and request.get("status") in {"located", "stale", "failed"}:
                self._command_result = {"command_id": command["command_id"], "status": request["status"], "message": request["message"]}
            self._commands = [item for item in self._commands if item["command_id"] != request.get("command_id")]
        return {"acknowledged": True}

    def shutdown(self):
        with self._lock:
            self._closed = True
            if self._identity:
                self._retired_identities.add(self._identity)
            window, peer = self._window, self._peer
            self._window, self._peer, self._identity = None, None, None
        try:
            if peer is not None: peer.close()
        finally:
            if window is not None:
                self._save_geometry(window)
                window.destroy()

    @staticmethod
    def _exit_snapshot(window):
        if window is None:
            return {"unsent_count": 0, "sending": False}
        completed, values = threading.Event(), []
        def receive(value):
            values.append(value)
            completed.set()
        window.evaluate_js("Promise.resolve().then(() => {window.__genshinExitInert ??= {value:document.body.inert};document.body.inert=true;return window.GenshinWindowPresentation?.prepareExit?.() || ({unsent_count:0,sending:false,unknown:true});}).then(value => ({ok:true,value}), error => ({ok:false,error:String(error)}))", callback=receive)
        if not completed.wait(30) or not values or not values[0].get("ok"):
            raise AgentWindowError("无法核对未发送消息，已取消退出。请先保存输入再重试。")
        value = values[0]["value"]
        if type(value.get("unsent_count")) is not int or type(value.get("sending")) is not bool:
            raise AgentWindowError("聊天关闭检查未返回完整状态，已取消退出。")
        return value

    def prepare_application_close(self):
        """Only explain/confirm. Official Host drain remains the shutdown owner."""
        with self._lock:
            detached = self._window
            if self._closed:
                return True
            if self._closing:
                return False
            self._closing = True
        approved = False
        try:
            snapshots = [self._exit_snapshot(window) for window in (self.api._window, detached) if window is not None]
            if any(row["sending"] for row in snapshots):
                raise AgentWindowError("消息正在提交，尚未确认是否已接纳。请等待提交结束再关闭。")
            shell = getattr(self.api, "_dsh_shell", None)
            runtime = shell.runtime_status() if shell is not None else {"agents": []}
            unknown_runtime = not isinstance(runtime.get("agents"), list)
            unknown = unknown_runtime or any(row.get("unknown") for row in snapshots)
            active = any(isinstance(item, dict) and item.get("status") == "running" for item in (runtime["agents"] if not unknown_runtime else []))
            unsent = sum(item["unsent_count"] for item in snapshots)
            if not active and not unsent and not unknown:
                approved = True
                return True
            message = []
            if active:
                message.append("助手仍在运行。退出会停止当前任务；已保存的消息和候选会保留。")
            if unknown:
                message.append("暂时无法确认助手或聊天草稿的完整状态。退出会关闭本次连接，未能读取的输入可能丢失；操作结果将保留待核实状态。")
            if unsent:
                message.append("还有未发送内容。文字草稿会按会话保存，未发送附件可能需要重新添加。")
            message.append("现在退出整个应用吗？取消将保持当前窗口和任务。")
            self.focus_main()
            approved = bool(self.api._window.create_confirmation_dialog("退出原神拉表", "\n\n".join(message)))
            return approved
        finally:
            if not approved:
                self.cancel_close()

    def cancel_close(self):
        with self._lock:
            self._closing = False
            windows = (self.api._window, self._window)
        for window in windows:
            if window is not None:
                try:
                    window.evaluate_js("if(window.__genshinExitInert){document.body.inert=window.__genshinExitInert.value;delete window.__genshinExitInert;}")
                except Exception:
                    logging.getLogger(__name__).warning("恢复窗口输入未成功", exc_info=True)

    def _save_geometry(self, window):
        from .window_geometry import save_geometry
        try:
            save_geometry(self._geometry_path, window)
        except (OSError, ValueError):
            logging.getLogger(__name__).warning("未能保存聊天窗口位置", exc_info=True)
