"""Thin pywebview API for the DSH client transport and App-domain commands.

The production factory verifies the independently pinned release. Isolated CLI
launchers may inject the same Host carrier. Neither path falls back to V2 or
adds an App envelope around DSH's native responses.
"""
from __future__ import annotations

import copy
import re

from genshin_app.agent_chat.native_dsh_mux_client import NativeDshMuxError

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")


class DshShellApi:
    def _dsh_domain_result(self, operation, *args, **kwargs):
        """Preserve domain failures instead of relabeling them JSON failures."""
        from genshin_app.agent_chat.native_app_action_controller import (
            NativeAppActionControllerError,
        )
        from genshin_app.agent_chat.native_app_action_ledger import NativeAppActionError
        from genshin_app.agent_chat.native_candidate_application_port import (
            NativeCandidateApplicationError,
        )
        from genshin_app.agent_chat.native_candidate_projection_port import (
            NativeCandidateProjectionError,
        )
        from genshin_app.agent_chat.native_candidate_transaction_contracts import (
            NativeServiceV2Error,
        )
        from genshin_app.agent_chat.native_domain_session_registry import (
            NativeDomainSessionRegistryError,
        )
        from genshin_app.equipment.provisional_entities import ProvisionalEntityError
        from genshin_app.equipment.schema import PlanError

        try:
            return self._ok(operation(*args, **kwargs))
        except (NativeDshMuxError, NativeCandidateApplicationError, NativeCandidateProjectionError, NativeServiceV2Error, PlanError, ProvisionalEntityError,
                NativeAppActionControllerError, NativeAppActionError, NativeDomainSessionRegistryError) as error:
            reason = getattr(error, "code", getattr(error, "reason", "dsh_domain_failed"))
            messages = {
                "agent_database_changed": "资料库已更新，这份候选暂时不能继续计算或应用。请在新对话中让助手适配当前版本。",
                "native_app_binding_database_changed": "资料库已更新，这份候选暂时不能继续计算或应用。请在新对话中让助手适配当前版本。",
                "native_app_binding_compatibility_missing": "这份排轴候选缺少版本证明，暂不能继续计算或应用。聊天和历史仍可使用。",
                "native_app_binding_compatibility_changed": "这份排轴候选来自其他版本，暂不能继续计算或应用。聊天和历史仍可使用。",
                "native_candidate_application_global_busy": "另一次白板同步尚未核实。如果它来自旧版本，请在文件与 App 操作中查看并决定是否放弃恢复。",
                "native_incompatible_application_stale": "这次旧同步的记录已变化，请刷新后重新核对。",
                "native_incompatible_application_not_eligible": "这次同步不属于可放弃恢复的旧版本记录，请使用正常同步恢复流程。",
                "dsh_domain_approval_stale": "待确认的修正内容已经变化，请刷新后重新核对。",
                "dsh_candidate_application_pending": "这份候选正在同步，请先确认同步结果；其他对话仍可继续。",
                "dsh_saved_whiteboard_stale": "保存的方案已经变化，请重新打开预览后再恢复。",
                "native_app_action_busy": "本会话还有一项文件操作需要核对。请先处理该操作，再同步候选。",
                "dsh_domain_session_unbound": "原会话没有绑定对应的 App 工作区，请回到原会话重新发起操作。",
                "candidate_input_draft_destination_invalid": "所选位置或已有文件不能用于保存草稿，请另选文件名。",
                "candidate_input_draft_filename": "请选择以 .gencdraft.json 结尾的草稿文件名。",
                "file_conflict": "所选文件已变化或无法覆盖，请重新选择文件名。",
                "native_whiteboard_delivery_export_format": "保存的文件扩展名与所选格式不一致。",
                "native_whiteboard_delivery_integrity_changed": "正式白板的计算文件已发生变化，无法导出。请重新打开或计算白板。",
            }
            failure = getattr(error, "failure", None)
            details = getattr(error, "details", None)
            if details is None and isinstance(failure, dict):
                details = failure.get("details")
            return {"code": 1, "reason": reason,
                "message": messages.get(reason, "文档或候选已变化，请刷新后重试。" if reason in {
                    "dsh_candidate_projection_stale", "native_candidate_changed", "agent_document_changed", "agent_stale_revision", "agent_stale_receipt"}
                    else "该操作暂未完成，请展开详情查看具体原因。"),
                "details": details,
                "operation_may_have_completed": getattr(error, "outcome_unknown", False)}

    def _get_dsh_credentials(self):
        with self._dsh_credentials_lock:
            if self._dsh_credentials is None:
                from genshin_app.agent_chat.credential_generation import (
                    CredentialGenerationController,
                )
                from genshin_app.agent_chat.credential_store import (
                    default_credential_store,
                )

                self._dsh_credentials = CredentialGenerationController(default_credential_store())
            return self._dsh_credentials

    def _dsh_bare_domain_result(self, operation, *args, **kwargs):
        result = self._dsh_domain_result(operation, *args, **kwargs)
        return result["data"] if result["code"] == 0 else result

    def dsh_client_credential_status(self):
        # Checking settings does not start Node or establish an API request.
        return self._get_dsh_credentials().status()

    def _sync_saved_dsh_credentials(self, status):
        with self._dsh_shell_lock:
            shell = self._dsh_shell
        if shell is None:
            return {**status, "runtime_sync": {"state": "pending_runtime"}}
        try:
            sync = shell.synchronize_credentials()
        except Exception as error:  # noqa: BLE001 - durable save is already complete
            sync = {"state": "failed", "code": getattr(error, "code", "dsh_credential_sync_failed")}
        return {**status, "runtime_sync": sync}

    def dsh_client_credential_save(self, request):
        try:
            if type(request) is not dict or set(request) != {"api_key", "expected_generation"}:
                raise NativeDshMuxError("dsh_credential_request_invalid")
            status = self._get_dsh_credentials().save(request["api_key"],
                expected_generation=request["expected_generation"])
            return self._sync_saved_dsh_credentials(status)
        finally:
            if type(request) is dict:
                request.pop("api_key", None)
            request = None

    def dsh_client_credential_delete(self, request):
        if type(request) is not dict or set(request) != {"expected_generation"}:
            raise NativeDshMuxError("dsh_credential_request_invalid")
        status = self._get_dsh_credentials().delete(expected_generation=request["expected_generation"])
        return self._sync_saved_dsh_credentials(status)

    def dsh_client_runtime_status(self):
        with self._dsh_shell_lock:
            shell = self._dsh_shell
        return {"state": "stopped"} if shell is None else shell.runtime_status()

    def dsh_client_session_status(self, request):
        return self._dsh_bare_domain_result(self._read_dsh_session_status, request)

    def _read_dsh_session_status(self, request):
        if (type(request) is not dict or set(request) != {"dsh_session_id"}
                or type(request["dsh_session_id"]) is not str or _IDENTITY.fullmatch(request["dsh_session_id"]) is None):
            raise NativeDshMuxError("dsh_session_status_request_invalid")
        from genshin_app.agent_chat.native_dsh_budget_ledger import (
            NativeDshBudgetLedger,
        )

        sid = request["dsh_session_id"]
        status = self._get_dsh_shell().session_status(sid)
        if type(status) is not dict or status.get("session_id") != sid:
            raise NativeDshMuxError("dsh_session_status_identity_mismatch")
        return {**status, "accounting": NativeDshBudgetLedger(self.data_dir).summary(sid)}

    def _get_dsh_workspaces(self):
        with self._dsh_workspace_lock:
            if self._dsh_shell_closed:
                raise NativeDshMuxError("dsh_shell_closed")
            if self._dsh_workspace_registry is None:
                from genshin_app.agent_chat.native_dsh_workspace import (
                    NativeDshWorkspaceRegistry,
                )

                self._dsh_workspace_registry = NativeDshWorkspaceRegistry(
                    self.data_dir / "native-dsh-workspaces", database_path=self._database_path,
                    diagnostic_data_dir=self.data_dir,
                    session_status_reader=self._dsh_existing_session_status)
            return self._dsh_workspace_registry

    def _dsh_existing_session_status(self, session_id):
        # Reading state must not start a shell, resume a Turn or call a model.
        with self._dsh_shell_lock:
            if self._dsh_shell_closed:
                raise NativeDshMuxError("dsh_shell_closed")
            shell = self._dsh_shell
        if shell is None:
            return {"session_id": session_id, "agent_running": False, "admission": {"state": "idle"}}
        return shell.session_status(session_id)

    def dsh_client_bind_document(self, request):
        if type(request) is not dict or set(request) != {"dsh_session_id", "document_kind", "document"}:
            raise NativeDshMuxError("dsh_workspace_bind_request_invalid")
        if self._database_setup is not None:
            return {"code": 1, "reason": "database_not_installed",
                    "message": "安装数据库后，智能助手可以查询角色资料和计算。"}
        return self._dsh_bare_domain_result(self._get_dsh_workspaces().prepare_prompt_document,
            request["dsh_session_id"], request["document_kind"], request["document"])

    def dsh_client_candidate(self, request):
        if type(request) is not dict or set(request) != {"conversation_id"}:
            raise NativeDshMuxError("dsh_candidate_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().candidate_summary, request["conversation_id"])

    def dsh_client_incompatible_applications(self, request):
        if type(request) is not dict or request:
            raise NativeDshMuxError("dsh_incompatible_applications_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().incompatible_applications)

    def dsh_client_abandon_incompatible_application(self, request):
        fields = {"conversation_id", "expected_fingerprint", "decision"}
        if type(request) is not dict or set(request) != fields:
            raise NativeDshMuxError("dsh_incompatible_application_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().abandon_incompatible_application, **request)

    def dsh_client_candidate_read(self, request):
        if type(request) is not dict or set(request) != {"conversation_id", "resource", "pointer", "offset", "limit"}:
            raise NativeDshMuxError("dsh_candidate_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().candidate_read, **request)

    def dsh_client_candidate_command(self, request):
        return self._dsh_domain_result(self._get_dsh_workspaces().candidate_command, request)

    def dsh_client_domain_approvals(self, request):
        if type(request) is not dict or set(request) != {"conversation_id"}:
            raise NativeDshMuxError("dsh_domain_approval_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().domain_approvals, **request)

    def dsh_client_domain_approval_decide(self, request):
        fields = {"conversation_id", "question_id", "expected_candidate_revision", "expected_stage_revision", "decision"}
        if type(request) is not dict or set(request) != fields:
            raise NativeDshMuxError("dsh_domain_approval_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().decide_domain_approval, **request)

    def dsh_client_workspace_state(self, request):
        if (type(request) is not dict or not {"conversation_id", "current_document_id", "workspace"} <= set(request)
                or set(request) - {"conversation_id", "current_document_id", "workspace", "current_payload"}):
            raise NativeDshMuxError("dsh_workspace_state_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().update_workspace_state, **request)

    def dsh_client_app_actions(self, request):
        if type(request) is not dict or set(request) != {"conversation_id"}:
            raise NativeDshMuxError("dsh_app_action_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().app_actions, **request)

    def dsh_client_app_action(self, request):
        if type(request) is not dict or set(request) != {"conversation_id", "command", "arguments"}:
            raise NativeDshMuxError("dsh_app_action_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().app_action, **request)

    def dsh_client_publish_candidate_input_draft(self, request):
        """User-selected file port; not registered in the Agent tool catalogue."""
        fields = {"conversation_id", "action_id", "ui_receipt", "output_path", "expected_file_sha", "overwrite"}
        if (type(request) is not dict or set(request) != fields or type(request["output_path"]) is not str
                or type(request["overwrite"]) is not bool):
            raise NativeDshMuxError("candidate_input_draft_file_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().publish_candidate_input_draft, **request)

    def dsh_client_request_candidate_input_draft(self, request):
        if (type(request) is not dict or set(request) != {"conversation_id", "expected_revision"}
                or type(request["expected_revision"]) is not int or request["expected_revision"] < 0):
            raise NativeDshMuxError("candidate_input_draft_file_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().request_candidate_input_draft, **request)

    def dsh_client_open_candidate_input_draft(self, request):
        if (type(request) is not dict or set(request) != {"dsh_session_id"}
                or type(request["dsh_session_id"]) is not str or _IDENTITY.fullmatch(request["dsh_session_id"]) is None):
            raise NativeDshMuxError("candidate_input_draft_file_request_invalid")
        registry = self._get_dsh_workspaces()
        if registry.lookup_binding(request["dsh_session_id"]) is not None:
            return {"code": 1, "reason": "candidate_input_draft_new_conversation_required",
                    "message": "请先新建一个未开始的对话，再打开候选输入草稿。", "operation_may_have_completed": False}
        selected = self.dsh_client_file_dialog({"action": "open", "document_kind": "candidate_input_draft",
                                                "suggested_name": "候选输入草稿.gencdraft.json"})
        if selected["data"]["cancelled"]:
            return self._ok({"cancelled": True})
        return self._dsh_domain_result(self._open_candidate_input_draft_file,
            request["dsh_session_id"], selected["data"]["path"], expected_file_sha=selected["data"].get("file_sha"))

    def dsh_client_import_candidate_input_draft(self, request):
        """Same loader for a file already selected/inspected by the user UI."""
        fields = {"dsh_session_id", "path", "expected_file_sha"}
        if (type(request) is not dict or set(request) != fields or type(request["dsh_session_id"]) is not str
                or _IDENTITY.fullmatch(request["dsh_session_id"]) is None or type(request["path"]) is not str
                or type(request["expected_file_sha"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", request["expected_file_sha"]) is None):
            raise NativeDshMuxError("candidate_input_draft_file_request_invalid")
        return self._dsh_domain_result(self._import_selected_candidate_input_draft, **request)

    def _import_selected_candidate_input_draft(self, dsh_session_id, path, expected_file_sha):
        from genshin_app.agent_chat.candidate_input_draft_store import (
            load_candidate_input_draft_preview,
        )

        if self._get_dsh_workspaces().lookup_binding(dsh_session_id) is not None:
            raise NativeDshMuxError("candidate_input_draft_new_conversation_required")
        loaded = load_candidate_input_draft_preview(path)
        if loaded["file_sha"] != expected_file_sha:
            raise NativeDshMuxError("candidate_input_draft_file_changed")
        return self._open_candidate_input_draft_file(dsh_session_id, path, expected_file_sha=expected_file_sha)

    def _open_candidate_input_draft_file(self, session_id, path, *, expected_file_sha=None):
        import secrets

        from genshin_app.agent_chat.candidate_input_draft import preview_draft
        from genshin_app.agent_chat.candidate_input_draft_store import (
            load_candidate_input_draft_preview,
        )
        from genshin_app.agent_chat.native_dsh_workspace_compatibility import (
            MIGRATION_PROMPT,
            current_workspace_identity,
        )
        from genshin_app.equipment.files import fingerprint
        from genshin_db import queries

        loaded = load_candidate_input_draft_preview(path)
        if expected_file_sha is not None and loaded["file_sha"] != expected_file_sha:
            raise NativeDshMuxError("candidate_input_draft_file_changed")
        if "metadata" in loaded:
            metadata = loaded["metadata"]
            message = (f"此候选输入草稿使用较新的文件格式（V{metadata['schema_version']}），仅显示文件版本和来源信息。"
                       "版本信息是文件声明；未解释排轴输入，未创建候选。")
            token = "draftpreview_" + secrets.token_urlsafe(24)
            # Never retain the unknown payload for later pointer/page reads.
            self._dsh_draft_previews = {token: {"metadata_only": True, "metadata": metadata}}
            return {"cancelled": False, "preview_id": token,
                "preview": {"compatible": False, "read_only": True, "metadata_only": True,
                    "payload_interpreted": False, "metadata": metadata, "has_calculation": False,
                    "accepted": False, "can_apply": False, "message": message},
                "migration_prompt": "请保留原文件，使用支持该文件格式的 App 版本打开。当前版本不解释其中的排轴输入，"
                                    "不能通过猜测字段、改版本号或删除未知字段继续计算。"}
        preview = preview_draft(loaded["draft"], workspace_identity=current_workspace_identity(),
                                database_fingerprint=fingerprint(queries.get_meta(self._database_path)))
        if not preview["compatible"]:
            token = "draftpreview_" + secrets.token_urlsafe(24)
            # Only user-selected, parsed domain data; no path is exposed to Agent.
            # One current preview: switching files expires the old transient handle.
            self._dsh_draft_previews = {token: loaded["draft"]}
            return {"cancelled": False, "preview": preview, "preview_id": token, "migration_prompt": MIGRATION_PROMPT}
        prepared = self._get_dsh_workspaces().prepare_candidate_input_draft(session_id, loaded["draft"])
        return {"cancelled": False, "preview": preview, **prepared}

    def dsh_client_candidate_input_draft_preview_read(self, request):
        fields = {"preview_id", "resource", "pointer", "offset", "limit"}
        if (type(request) is not dict or set(request) != fields or request["resource"] not in {"source_document", "candidate", "evidence"}
                or type(request["preview_id"]) is not str or not request["preview_id"].startswith("draftpreview_")
                or type(request["pointer"]) is not str
                or type(request["offset"]) is not int or request["offset"] < 0
                or type(request["limit"]) is not int or not 1 <= request["limit"] <= 100):
            raise NativeDshMuxError("candidate_input_draft_preview_invalid")
        draft = getattr(self, "_dsh_draft_previews", {}).get(request["preview_id"])
        if draft is None:
            raise NativeDshMuxError("candidate_input_draft_preview_expired")
        from genshin_app.agent_chat.tools import page

        if draft.get("metadata_only") is True:
            value = {"metadata": draft["metadata"], "payload_interpreted": False, "authority": "none",
                "message": "仅显示文件声明的版本和来源信息；未知格式的原始意图、候选输入和资料正文未解释。"}
        else:
            value = draft["evidence"] if request["resource"] == "evidence" else draft["input"][request["resource"]]
        return self._ok(page(value, request["resource"], request["pointer"], request["offset"], request["limit"]))

    def dsh_client_file_dialog(self, request):
        """Trusted user UI only; never registered in the model's tool set."""
        from genshin_app.agent_chat.native_app_action_ledger import (
            sanitize_suggested_basename,
        )

        report_formats = {"report_json": "json", "report_xlsx": "xlsx", "report_csv": "csv"}
        if (type(request) is not dict or set(request) != {"action", "document_kind", "suggested_name"}
                or request["action"] not in {"open", "save", "export"}
                or request["document_kind"] not in {"whiteboard", "native", "calculated", "candidate_input_draft", *report_formats}
                or (request["document_kind"] in report_formats and request["action"] != "export")):
            raise NativeDshMuxError("dsh_file_dialog_request_invalid")
        suggested = sanitize_suggested_basename(request["suggested_name"]) or "排轴方案"
        if request["document_kind"] == "candidate_input_draft" and not suggested.lower().endswith(".gencdraft.json"):
            suggested = suggested.removesuffix(".json") + ".gencdraft.json"
        extension = "." + report_formats.get(request["document_kind"], "json")
        if not suggested.lower().endswith(extension):
            suggested += extension
        if self._dsh_file_dialog is not None:
            selected = self._dsh_file_dialog({**request, "suggested_name": suggested})
        else:
            if self._window is None:
                raise NativeDshMuxError("dsh_file_dialog_unavailable")
            import webview

            file_types = ("候选输入草稿 (*.gencdraft.json)",) if request["document_kind"] == "candidate_input_draft" else (f"{extension[1:].upper()} (*{extension})",)
            paths = self._window.create_file_dialog(
                webview.FileDialog.OPEN if request["action"] == "open" else webview.FileDialog.SAVE,
                allow_multiple=False, save_filename=suggested, file_types=file_types)
            selected = paths[0] if paths else None
        if selected is not None and (type(selected) is not str or not selected.lower().endswith(extension)):
            raise NativeDshMuxError("dsh_file_dialog_result_invalid")
        result = {"cancelled": selected is None, "path": selected}
        if selected is not None and request["document_kind"] in report_formats:
            from genshin_app.equipment.files import file_sha

            result["file_sha"] = file_sha(selected)
        if selected is not None and request["document_kind"] == "candidate_input_draft":
            from pathlib import Path

            from genshin_app.agent_chat.candidate_input_draft_store import (
                load_candidate_input_draft,
                load_candidate_input_draft_preview,
            )
            from genshin_app.equipment.schema import PlanError

            # Read after the user's overwrite decision, before the eventual CAS.
            loader = load_candidate_input_draft_preview if request["action"] == "open" else load_candidate_input_draft
            if request["action"] != "open" and not selected.lower().endswith(".gencdraft.json"):
                return {"code": 1, "reason": "candidate_input_draft_filename",
                        "message": "请选择以 .gencdraft.json 结尾的草稿文件名，未写入文件。"}
            try:
                result["file_sha"] = loader(selected)["file_sha"] if Path(selected).exists() else None
            except (OSError, PlanError):
                if request["action"] == "open":
                    raise
                return {"code": 1, "reason": "candidate_input_draft_destination_invalid",
                        "message": "已有文件无法作为候选输入草稿覆盖，请另选文件名，未写入文件。"}
        return self._ok(result)

    def dsh_client_whiteboard_projection(self, request):
        if type(request) is not dict or set(request) != {"conversation_id", "command", "arguments"}:
            raise NativeDshMuxError("dsh_whiteboard_projection_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().whiteboard_projection, **request)

    def dsh_client_saved_whiteboard(self, request):
        if type(request) is not dict or set(request) != {"conversation_id", "command", "arguments"}:
            raise NativeDshMuxError("dsh_saved_whiteboard_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().saved_whiteboard, **request)

    def dsh_client_whiteboard_apply_equipment(self, request):
        if type(request) is not dict or set(request) != {"conversation_id", "delivery_capability", "board"}:
            raise NativeDshMuxError("dsh_whiteboard_delivery_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().application_backend.apply_whiteboard_equipment_delivery,
            **request, db_path=self._database_path)

    def dsh_client_publish_whiteboard_result(self, request):
        fields = {"conversation_id", "delivery_capability", "output_path", "expected_revision", "expected_receipt"}
        report_fields = {"conversation_id", "delivery_capability", "output_path", "format", "expected_file_sha", "expected_result_fingerprint"}
        if type(request) is not dict or (set(request) != fields and
                (set(request) != report_fields or request["format"] not in ("csv", "xlsx"))):
            raise NativeDshMuxError("dsh_whiteboard_delivery_request_invalid")
        return self._dsh_domain_result(self._get_dsh_workspaces().application_backend.publish_committed_whiteboard_result, **request)

    def dsh_client_save_whiteboard_result(self, request):
        """User-only Save As: chosen paths never enter the model tool surface."""
        fields = {"conversation_id", "delivery_capability", "expected_revision", "expected_receipt"}
        if type(request) is not dict or set(request) != fields:
            raise NativeDshMuxError("dsh_whiteboard_delivery_request_invalid")
        if self._dsh_result_save_dialog is not None:
            selected = self._dsh_result_save_dialog("排轴计算结果.calculated.json")
        else:
            if self._window is None:
                raise NativeDshMuxError("dsh_file_dialog_unavailable")
            import webview

            paths = self._window.create_file_dialog(webview.FileDialog.SAVE,
                save_filename="排轴计算结果.calculated.json", file_types=("JSON (*.json)",))
            selected = paths[0] if paths else None
        if selected is None:
            return self._ok({"cancelled": True})
        if not isinstance(selected, str) or not selected.lower().endswith(".json"):
            raise NativeDshMuxError("dsh_result_filename_invalid")
        result = self._dsh_domain_result(
            self._get_dsh_workspaces().application_backend.publish_committed_whiteboard_result,
            **request, output_path=selected)
        if result["code"] == 0:
            result["data"] = {"cancelled": False, **result["data"]}
        return result

    def dsh_client_domain_binding(self, request):
        return self._dsh_bare_domain_result(self._read_dsh_domain_binding, request)

    def _read_dsh_domain_binding(self, request):
        """Project an explicit App binding, never infer one from chat content."""
        if (type(request) is not dict or set(request) != {"dsh_session_id"}
                or type(request["dsh_session_id"]) is not str
                or _IDENTITY.fullmatch(request["dsh_session_id"]) is None):
            raise NativeDshMuxError("dsh_domain_binding_request_invalid")
        if self._database_setup is not None:
            return {"binding": None, "reason": "database_not_installed"}
        resolver = self._dsh_domain_binding_resolver
        if resolver is None:
            resolver = self._get_dsh_workspaces().domain_projection
        value = resolver(request["dsh_session_id"])
        if value is None:
            return {"binding": None}
        if (type(value) is not dict or set(value) != {
                "dsh_session_id", "conversation_id", "projection"}
                or value["dsh_session_id"] != request["dsh_session_id"]
                or type(value["conversation_id"]) is not str
                or _IDENTITY.fullmatch(value["conversation_id"]) is None):
            raise NativeDshMuxError("dsh_domain_binding_identity_mismatch")
        projection = value["projection"]
        if (type(projection) is not dict
                or projection.get("contract") != "native-harness-projection-v3"
                or type(projection.get("projection_revision")) is not int
                or not 0 <= projection["projection_revision"] <= 2**53 - 1
                or type(projection.get("available_commands")) is not list):
            raise NativeDshMuxError("dsh_domain_binding_projection_invalid")
        return {"binding": copy.deepcopy(value)}

    def _get_dsh_shell(self):
        with self._dsh_shell_lock:
            if self._dsh_shell_closed:
                raise NativeDshMuxError("dsh_shell_closed")
            if self._dsh_shell is None:
                if self._dsh_shell_factory is None:
                    from genshin_app import version
                    from genshin_app.agent_chat.native_dsh_production import (
                        create_native_dsh_application,
                    )

                    from .api import _release_native_runtime_environment

                    self._dsh_shell = create_native_dsh_application(self.data_dir,
                        workspaces=self._get_dsh_workspaces(), credentials=self._get_dsh_credentials(),
                        expected_manifest_sha256=getattr(version, "NATIVE_AGENT_DSH_SHELL_MANIFEST_SHA256", None),
                        runtime_environment=_release_native_runtime_environment())
                else:
                    self._dsh_shell = self._dsh_shell_factory(self.data_dir)
            return self._dsh_shell

    def _close_dsh_shell(self):
        with self._dsh_shell_lock:
            self._dsh_shell_closed = True
            shell, self._dsh_shell = self._dsh_shell, None
        try:
            if shell is not None:
                shell.close()
        finally:
            with self._dsh_workspace_lock:
                registry, self._dsh_workspace_registry = self._dsh_workspace_registry, None
            if registry is not None:
                registry.close()

    def dsh_client_boot(self):
        return self._get_dsh_shell().dsh_client_boot()

    def dsh_client_rpc(self, request):
        return self._get_dsh_shell().dsh_client_rpc(request)

    def dsh_client_request_cancel(self, request):
        return self._get_dsh_shell().dsh_client_request_cancel(request)

    def dsh_client_stream_open(self, request):
        return self._get_dsh_shell().dsh_client_stream_open(request)

    def dsh_client_stream_read(self, request):
        return self._get_dsh_shell().dsh_client_stream_read(request)

    def dsh_client_stream_send(self, request):
        return self._get_dsh_shell().dsh_client_stream_send(request)

    def dsh_client_stream_cancel(self, request):
        return self._get_dsh_shell().dsh_client_stream_cancel(request)

    def dsh_client_bundle(self, request):
        return self._get_dsh_shell().dsh_client_bundle(request)

    def dsh_client_resource_read(self, request):
        return self._get_dsh_shell().dsh_client_resource_read(request)

    def dsh_client_resource_release(self, request):
        return self._get_dsh_shell().dsh_client_resource_release(request)
