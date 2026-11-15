"""AgentGuiPort: concrete domain adapter; no GUI Facade callbacks."""
from __future__ import annotations

from pathlib import Path
from threading import Lock

from .responses import GuiResponses


class AgentGuiPort(GuiResponses):
    def __init__(self, data_dir, database_path, *, manager_factory, default_manager_factory):
        self.data_dir = Path(data_dir)
        self._database_path = Path(database_path)
        self._agent_manager_factory = manager_factory
        self._default_manager_factory = default_manager_factory
        self._agent_manager = None
        self._agent_lock = Lock()

    def _agent_call(self, method, *args, **kwargs):
        from genshin_app.agent_chat.transport import TransportError
        from genshin_app.equipment.schema import PlanError

        try:
            manager = self._get_agent_manager()
            operation = getattr(manager, method, None)
            if not callable(operation):
                raise PlanError(
                    "此旧原生 Agent 操作已停用，请使用 AI 助手的 DSH 会话。",
                    "agent_native_backend_retired",
                )
            return self._ok(operation(*args, **kwargs))
        except PlanError as exc:
            return {"code": 1, "message": str(exc), "reason": exc.reason, "details": exc.details}
        except TransportError as exc:
            return {"code": 1, "message": exc.message, "reason": exc.code}
        except (OSError, ValueError, TypeError, KeyError):
            return {"code": 1, "message": "Agent操作失败，记录仍保留，请检查输入或重新连接", "reason": "agent_operation"}

    def _get_agent_manager(self):
        with self._agent_lock:
            if self._agent_manager is None:
                if self._agent_manager_factory is None:
                    self._agent_manager = self._default_manager_factory(
                        self.data_dir,
                        database_path=self._database_path,
                    )
                else:
                    self._agent_manager = self._agent_manager_factory(self.data_dir)
            return self._agent_manager

    def agent_detect(self):
        return self._agent_call("detect")

    def agent_connect(self, connection):
        """Cross the one secret-bearing GUI boundary and drop its references.

        pywebview supplies a mutable dictionary for the native API request.
        Mutating it after the synchronous bridge hand-off also removes the key
        from a renderer-held proxy object.  Python strings cannot be wiped in
        place, but neither a returned error nor an exported traceback retains
        this boundary's copy.
        """

        from genshin_app.agent_chat.transport import TransportError
        from genshin_app.equipment.schema import PlanError

        manager = None
        result = None
        try:
            manager = self._get_agent_manager()
            result = manager.connect(connection)
            return self._ok(result)
        except PlanError as exc:
            return {
                "code": 1,
                "message": str(exc),
                "reason": exc.reason,
                "details": exc.details,
            }
        except TransportError as exc:
            return {"code": 1, "message": exc.message, "reason": exc.code}
        except (OSError, ValueError, TypeError, KeyError):
            return {
                "code": 1,
                "message": "Agent操作失败，记录仍保留，请检查输入或重新连接",
                "reason": "agent_operation",
            }
        finally:
            if isinstance(connection, dict) and "api_key" in connection:
                connection["api_key"] = ""
            connection = None
            manager = None
            result = None

    def agent_forget_credential(self, provider_id):
        return self._agent_call("forget_credential", provider_id)

    def agent_budget_settings(self):
        """Read the App-owned policy without initializing a provider runtime."""

        from genshin_app.agent_chat.native_turn_budget import (
            load_native_turn_budget_policy,
        )
        from genshin_app.equipment.schema import PlanError

        try:
            return self._ok(load_native_turn_budget_policy(self.data_dir))
        except PlanError as exc:
            return {
                "code": 1,
                "message": str(exc),
                "reason": exc.reason,
                "details": exc.details,
            }

    def agent_update_budget_settings(self, overrides, expected_generation=None):
        """Replace user overrides; the new snapshot applies from the next turn."""

        from genshin_app.agent_chat.native_turn_budget import (
            update_native_turn_budget_policy,
        )
        from genshin_app.equipment.schema import PlanError

        try:
            return self._ok(update_native_turn_budget_policy(
                self.data_dir,
                overrides,
                expected_generation=expected_generation,
                source="gui",
            ))
        except PlanError as exc:
            return {
                "code": 1,
                "message": str(exc),
                "reason": exc.reason,
                "details": exc.details,
            }

    def agent_reset_budget_settings(self, expected_generation=None):
        """Return to the maintained defaults from the next turn onward."""

        from genshin_app.agent_chat.native_turn_budget import (
            reset_native_turn_budget_policy,
        )
        from genshin_app.equipment.schema import PlanError

        try:
            return self._ok(reset_native_turn_budget_policy(
                self.data_dir,
                expected_generation=expected_generation,
                source="gui",
            ))
        except PlanError as exc:
            return {
                "code": 1,
                "message": str(exc),
                "reason": exc.reason,
                "details": exc.details,
            }

    def agent_dsh_settings(self):
        """Read pinned DSH defaults and sparse overrides without provider I/O."""

        from genshin_app.agent_chat.native_dsh_settings import (
            default_dsh_profile_root,
            load_native_dsh_settings,
        )
        from genshin_app.equipment.schema import PlanError

        try:
            return self._ok(load_native_dsh_settings(
                default_dsh_profile_root(self.data_dir)
            ))
        except PlanError as exc:
            return {
                "code": 1,
                "message": str(exc),
                "reason": exc.reason,
                "details": exc.details,
            }

    def agent_update_dsh_settings(self, overrides, expected_generation=None):
        """Persist DSH module overrides for the next local runtime process."""

        from genshin_app.agent_chat.native_dsh_settings import (
            default_dsh_profile_root,
            update_native_dsh_settings,
        )
        from genshin_app.equipment.schema import PlanError

        try:
            return self._ok(update_native_dsh_settings(
                default_dsh_profile_root(self.data_dir),
                overrides,
                expected_generation=expected_generation,
                source="gui",
            ))
        except PlanError as exc:
            return {
                "code": 1,
                "message": str(exc),
                "reason": exc.reason,
                "details": exc.details,
            }

    def agent_reset_dsh_settings(self, expected_generation=None):
        """Clear DSH module overrides for the next local runtime process."""

        from genshin_app.agent_chat.native_dsh_settings import (
            default_dsh_profile_root,
            reset_native_dsh_settings,
        )
        from genshin_app.equipment.schema import PlanError

        try:
            return self._ok(reset_native_dsh_settings(
                default_dsh_profile_root(self.data_dir),
                expected_generation=expected_generation,
                source="gui",
            ))
        except PlanError as exc:
            return {
                "code": 1,
                "message": str(exc),
                "reason": exc.reason,
                "details": exc.details,
            }

    def agent_start(self, request):
        return self._agent_call("start", request)

    def agent_send(self, session_id, message, client_request_id=None, current_document=None,
                   workspace=None, current_payload=None):
        # ``current_payload`` belongs to the V2 native router.  Keep the GUI
        # bridge compatible with the retained CLI/board manager when the
        # renderer has no richer payload to send; passing a trailing ``None``
        # would otherwise turn a valid legacy call into a TypeError.
        if current_payload is None:
            return self._agent_call(
                "send", session_id, message, client_request_id, current_document,
                workspace)
        return self._agent_call(
            "send", session_id, message, client_request_id, current_document, workspace,
            current_payload)

    def agent_resume(self, session_id, current_document, workspace=None, current_payload=None):
        if current_payload is None:
            return self._agent_call(
                "resume", session_id, current_document, workspace)
        return self._agent_call(
            "resume", session_id, current_document, workspace, current_payload)

    def agent_poll(self, session_id, after_seq=0):
        return self._agent_call("poll", session_id, after_seq)

    def agent_wait_events(
        self,
        session_id,
        after_seq=0,
        subscription_generation=0,
        timeout_ms=12000,
    ):
        return self._agent_call(
            "wait_events",
            session_id,
            after_seq,
            subscription_generation,
            timeout_ms,
        )

    def agent_turn_outline(self, session_id, request=None):
        if request is None:
            return self._agent_call("turn_outline", session_id)
        return self._agent_call("turn_outline", session_id, request)

    def agent_search_conversation(self, session_id, request):
        return self._agent_call("search_conversation", session_id, request)

    def agent_load_turn_window(self, session_id, request):
        return self._agent_call("load_turn_window", session_id, request)

    def agent_telemetry_bundle(self, session_id, request=None):
        if request is None:
            return self._agent_call("telemetry_bundle", session_id)
        return self._agent_call("telemetry_bundle", session_id, request)

    def agent_workbench_projection(self, session_id):
        return self._agent_call("workbench_projection", session_id)

    def agent_submit_question_batch_answer(self, session_id, request):
        return self._agent_call(
            "submit_question_batch_answer",
            session_id,
            request,
        )

    def agent_enqueue_workbench_inbox(self, session_id, request):
        return self._agent_call("enqueue_workbench_inbox", session_id, request)

    def agent_revise_workbench_inbox(self, session_id, request):
        return self._agent_call("revise_workbench_inbox", session_id, request)

    def agent_cancel_workbench_inbox(self, session_id, request):
        return self._agent_call("cancel_workbench_inbox", session_id, request)

    def agent_reorder_workbench_inbox(self, session_id, request):
        return self._agent_call("reorder_workbench_inbox", session_id, request)

    def agent_promote_workbench_inbox(self, session_id, request):
        return self._agent_call("promote_workbench_inbox", session_id, request)

    def agent_list_document_checkpoints(self, session_id, offset=0, limit=50):
        return self._agent_call(
            "list_document_checkpoints",
            session_id,
            offset=offset,
            limit=limit,
        )

    def agent_document_restore_status(self, session_id):
        return self._agent_call("document_restore_status", session_id)

    def agent_create_document_checkpoint(self, session_id, request):
        return self._agent_call("create_document_checkpoint", session_id, request)

    def agent_preview_document_checkpoint(
        self,
        session_id,
        checkpoint_id,
        from_checkpoint_id=None,
    ):
        return self._agent_call(
            "preview_document_checkpoint",
            session_id,
            checkpoint_id,
            from_checkpoint_id,
        )

    def agent_rename_document_checkpoint(self, session_id, request):
        return self._agent_call("rename_document_checkpoint", session_id, request)

    def agent_delete_document_checkpoint(self, session_id, request):
        return self._agent_call("delete_document_checkpoint", session_id, request)

    def agent_prepare_document_restore(self, session_id, request):
        return self._agent_call("prepare_document_restore", session_id, request)

    def agent_mark_document_restore_applied(self, session_id, request):
        return self._agent_call(
            "mark_document_restore_renderer_applied", session_id, request
        )

    def agent_advance_document_restore_baseline(self, session_id, request):
        return self._agent_call(
            "advance_document_restore_baseline", session_id, request
        )

    def agent_commit_document_restore(self, session_id, request):
        return self._agent_call("commit_document_restore", session_id, request)

    def agent_abort_document_restore(self, session_id, request):
        return self._agent_call("abort_document_restore", session_id, request)

    def agent_mark_document_restore_recovery_required(self, session_id, request):
        return self._agent_call(
            "mark_document_restore_recovery_required", session_id, request
        )

    def agent_reconcile_document_restore(self, session_id, request):
        return self._agent_call("reconcile_document_restore", session_id, request)

    def agent_runtime_status(self, after_seq=0, subscription_generation=0):
        return self._agent_call(
            "runtime_status", after_seq, subscription_generation
        )

    def agent_wait_runtime_status(
        self,
        after_seq=0,
        subscription_generation=0,
        timeout_ms=12000,
    ):
        return self._agent_call(
            "wait_runtime_status",
            after_seq,
            subscription_generation,
            timeout_ms,
        )

    def agent_notify_system_resume(self):
        return self._agent_call("notify_system_resume")

    def agent_prepare_runtime(self, purpose="send"):
        return self._agent_call("prepare_runtime", purpose)

    def agent_runtime_prepare_status(self, operation_id):
        return self._agent_call("runtime_prepare_status", operation_id)

    def agent_wait_runtime_prepare(self, operation_id, timeout_ms=12000):
        return self._agent_call("wait_runtime_prepare", operation_id, timeout_ms)

    def agent_cancel_runtime_prepare(self, operation_id):
        return self._agent_call("cancel_runtime_prepare", operation_id)

    def agent_stop(self, session_id):
        return self._agent_call("stop", session_id)

    def agent_candidate(self, session_id):
        return self._agent_call("candidate", session_id)

    def agent_accept(self, session_id, kind, document, expected_revision, expected_receipt,
                     baseline_payload=None):
        if baseline_payload is None:
            return self._agent_call(
                "accept", session_id, kind, document, expected_revision,
                expected_receipt)
        return self._agent_call(
            "accept", session_id, kind, document, expected_revision, expected_receipt,
            baseline_payload)

    def agent_candidate_applied(self, session_id, kind, document,
                                expected_revision, expected_receipt,
                                workspace=None, actual_payload=None):
        return self._agent_call(
            "candidate_applied", session_id, kind, document,
            expected_revision, expected_receipt, workspace, actual_payload)

    def agent_abort_candidate_application(self, session_id, current_document,
                                           workspace=None, current_payload=None):
        return self._agent_call(
            "abort_candidate_application", session_id, current_document,
            workspace, current_payload)

    def agent_recover_candidate_application(self, session_id, current_document,
                                             workspace=None, current_payload=None,
                                             discard_current=False):
        return self._agent_call(
            "recover_candidate_application", session_id, current_document,
            workspace, current_payload, discard_current)

    def agent_recover_last_applied_whiteboard_projection(
            self, session_id, current_document, workspace=None,
            current_payload=None, allow_unsaved_target=False):
        return self._agent_call(
            "recover_last_applied_whiteboard_projection",
            session_id,
            current_document,
            workspace,
            current_payload,
            allow_unsaved_target,
        )

    def agent_candidate_read(self, session_id, resource, pointer="", offset=0, limit=50):
        return self._agent_call("candidate_read", session_id, resource, pointer, offset, limit)

    def agent_whiteboard_apply_equipment(self, session_id, delivery_capability, board):
        return self._agent_call(
            "whiteboard_apply_equipment", session_id, delivery_capability, board)

    def agent_publish_committed_whiteboard_result(
        self,
        session_id,
        delivery_capability,
        output_path,
        expected_revision,
        expected_receipt,
    ):
        """Publish a committed native-Agent package through the GUI Host.

        This method exists for reviewed GUI acceptance projections, including
        the real WebView smoke and the faster headless Host-chain runner.  The
        capability and applied candidate identity are checked again by the
        native service; no calculated payload crosses this bridge from the
        caller.
        """

        return self._agent_call(
            "publish_committed_whiteboard_result",
            session_id,
            delivery_capability,
            output_path,
            expected_revision,
            expected_receipt,
        )

    def agent_resolve_app_action(self, session_id, action_id, status, workspace=None,
                                 message=None):
        return self._agent_call(
            "resolve_app_action", session_id, action_id, status, workspace, message)

    def agent_present_app_action(self, session_id, action_id, workspace=None):
        return self._agent_call(
            "present_app_action", session_id, action_id, workspace)

    def agent_claim_app_action(self, session_id, action_id, ui_receipt,
                               workspace=None):
        return self._agent_call(
            "claim_app_action", session_id, action_id, ui_receipt, workspace)

    def agent_settle_app_action(self, session_id, action_id, ui_receipt, outcome,
                                workspace=None):
        return self._agent_call(
            "settle_app_action", session_id, action_id, ui_receipt, outcome,
            workspace)

    def agent_recover_app_action_without_replay(self, session_id, action_id,
                                                ui_receipt):
        return self._agent_call(
            "recover_app_action_without_replay", session_id, action_id,
            ui_receipt)

    def agent_sessions(self):
        return self._agent_call("sessions")

    def agent_rename_session(self, session_id, request):
        return self._agent_call("rename_session", session_id, request)

    def agent_archive_session(self, session_id, request):
        return self._agent_call("archive_session", session_id, request)

    def agent_mark_session_read(self, session_id, request):
        return self._agent_call("mark_session_read", session_id, request)

    def agent_forget(self, session_id):
        return self._agent_call("forget", session_id)

    def agent_shutdown(self):
        if self._agent_manager is None:
            return self._ok({"closed": True})
        return self._agent_call("shutdown")

