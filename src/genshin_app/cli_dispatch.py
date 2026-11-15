"""Same parsed domain handlers for one-shot CLI and explicit stdin sessions."""
from __future__ import annotations


def error_payload(message, *, reason=None, details=None, operation=None):
    payload = {"code": 1, "message": message}
    if reason is not None:
        from genshin_app.domain_errors import (
            classify_domain_failure,
            tool_failure_guidance,
        )
        payload.update(reason=reason, details=details)
        payload.update(classify_domain_failure(reason, operation=operation, details=details))
        payload.update(tool_failure_guidance(reason, operation=operation, details=details))
        if operation is not None:
            payload["operation"] = operation
        if operation == "cli.equipment.calc":
            from genshin_app.agent_chat.skill_context import recommend_focus
            feedback = details.get("execution_feedback", {}) if isinstance(details, dict) else {}
            feedback = feedback if isinstance(feedback, dict) else {}
            payload["work_card_recommendation"] = recommend_focus({"latest_attempt": {
                "operation": "calculate", "state": "failed", "matches_current_input": True,
                "reason": reason, "failure_summary": payload.get("failure_summary") or feedback.get("failure_summary")}})
    return payload


def exception_payload(exc, *, data_dir, operation):
    """Normalize typed domain failures; unknown faults are not user errors."""
    from genshin_app.app_fault_store import fault_scope
    from genshin_app.app_system_fault import domain_exception_reason, system_failure
    from genshin_engine.execution_control import ExecutionCancelled
    if isinstance(exc, KeyboardInterrupt):
        return error_payload("请求已取消。若此前启动过写入，请先核对目标文件和回执再继续。",
                             reason="cli_request_cancelled", operation=operation)
    if isinstance(exc, ExecutionCancelled):
        from genshin_app.execution_cancellation import cancellation_response
        return cancellation_response(exc, operation=operation)
    reason = domain_exception_reason(exc)
    if reason is not None:
        return error_payload(str(exc), reason=reason, details=getattr(exc, "details", None), operation=operation)
    with fault_scope(data_dir):
        return system_failure(exc, component="python_engine_runtime" if isinstance(exc, ImportError)
                              else "app_domain_tools", operation=operation)


def execute(args):
    """No subprocess, stdout parsing, implicit retries or alternative domain logic."""
    from genshin_app.app_fault_store import fault_scope
    from genshin_app.output_policy import cli_output_scope, validate_output_paths
    operation = "cli." + ".".join(args._command_path)
    try:
        with cli_output_scope(args), fault_scope(args.data_dir):
            if getattr(args, "output", None):
                validate_output_paths([args.output], check_inputs=False)
            data = args._handler(args)
        return None if data is None else {"code": 0, "message": "ok", "data": data}
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - shared public CLI fault boundary
        return exception_payload(exc, data_dir=args.data_dir, operation=operation)
