"""Advisory user-action facts from the App's existing approval and file cards."""
from __future__ import annotations

_PENDING = {"queued", "presented", "claimed_for_external_execution"}


def project_task_actions(context, scope):
    controller = getattr(scope, "action_controller", None)
    if controller is None:
        return
    try:
        from .native_dsh_domain_approvals import approval_cards

        cards = controller.list_cards(include_terminal=True)
        approvals = approval_cards(getattr(scope, "candidate_session", None))
        pending = [row for row in cards if row.get("status") in _PENDING]
        context["waiting_for_user"] = bool(pending)
        context["pending_user_approval"] = bool(approvals)
        basis = (context.get("candidate", {}).get("task_review") or {}).get("report_basis")
        if isinstance(basis, dict):
            basis["user_actions"] = [
                {**{key: row[key] for key in ("action_id", "action", "status") if key in row},
                 "user_step": ("请在当前对话的文件操作卡核对上次操作。" if row["status"] == "claimed_for_external_execution"
                               else "请在当前对话的文件操作卡点击「处理这项操作」。")}
                for row in pending[:8]
            ]
            basis["user_actions"].extend({"question_id": row["question_id"], "status": row["state"],
                "user_step": "请在当前对话的资料修正确认卡查看变更并选择。"} for row in approvals[:8])
            if cards and cards[-1].get("status") not in _PENDING:
                row = cards[-1]
                basis["last_file_action"] = {key: row[key] for key in ("action", "status") if row.get(key)}
                if context.get("workflow") == "artifact" and isinstance(row.get("result_message"), str) and row["result_message"]:
                    basis["last_file_action"]["result_message"] = row["result_message"][:512]
                publication = row.get("draft_publication", {})
                if context.get("workflow") == "artifact" and isinstance(publication.get("path"), str) and publication["path"]:
                    basis["last_file_action"]["artifact_path"] = publication["path"]
    except Exception as error:  # Advisory data must not revoke a valid App binding.
        from genshin_engine.execution_control import ExecutionCancelled
        if isinstance(error, ExecutionCancelled):
            raise
        context["task_action_projection"] = "unavailable"
