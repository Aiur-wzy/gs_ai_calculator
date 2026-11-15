"""Run the desktop GUI native-Agent workflow without a WebView.

This is intentionally a projection of the production GUI route. It creates
the same ``GuiApi``, uses :class:`GuiDocumentClient` for every document-bearing
call, performs the normal two-phase candidate application, and finally asks a
Host-owned trusted method to publish the committed calculated package. It
never opens native session directories or constructs a calculated package.
"""

from __future__ import annotations

import copy
import re
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from genshin_app.gui.headless_document_client import (
    GuiDocumentCallError,
    GuiDocumentClient,
)

_FAILED_STATES = {"failed", "stopped", "cancelled", "error", "interrupted"}
_TERMINAL_STATES = {"completed", "ready", *_FAILED_STATES}
_PUBLISH_METHOD = "agent_publish_committed_whiteboard_result"
_WORKBENCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_PROVIDER_MODES = frozenset(
    {"saved_credential", "deterministic_provider_transport"}
)


class GuiWorkflowError(RuntimeError):
    """A stable, user-facing failure from the headless GUI workflow."""

    def __init__(self, message: str, *, reason: str, details: object | None = None):
        super().__init__(message)
        self.reason = reason
        self.details = copy.deepcopy(details)


def _direct(api: object, method: str, *args: object) -> Any:
    target = getattr(api, method, None)
    if not callable(target):
        raise GuiWorkflowError(
            "当前GUI Host缺少原生Agent接口",
            reason="gui_workflow_host_method_unavailable",
            details={"method": method},
        )
    response = target(*args)
    if not isinstance(response, Mapping) or type(response.get("code")) is not int:
        raise GuiWorkflowError(
            "GUI Host返回了无效包络",
            reason="gui_workflow_host_response_invalid",
            details={"method": method},
        )
    if response["code"] != 0:
        raise GuiWorkflowError(
            str(response.get("message") or "GUI Host操作失败"),
            reason=str(response.get("reason") or "agent_operation"),
            details=response.get("details"),
        )
    if "data" not in response:
        raise GuiWorkflowError(
            "GUI Host成功包络缺少data",
            reason="gui_workflow_host_response_invalid",
            details={"method": method},
        )
    return copy.deepcopy(response["data"])


def _mapping(value: object, *, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GuiWorkflowError(
            f"{name}不是对象",
            reason="gui_workflow_response_invalid",
            details={"component": name},
        )
    return copy.deepcopy(dict(value))


def _harness_projection(snapshot: Mapping[str, Any]) -> dict[str, Any] | None:
    """Read the exact Host projection carried by the GUI polling route."""

    value = snapshot.get("harness_projection")
    if value is None:
        return None
    if (
        not isinstance(value, Mapping)
        or value.get("contract") != "native-harness-projection-v3"
        or type(value.get("projection_revision")) is not int
        or not isinstance(value.get("available_commands"), list)
    ):
        raise GuiWorkflowError(
            "GUI Host返回了无效的Agent工作投影",
            reason="gui_workflow_harness_projection_invalid",
        )
    revision = value["projection_revision"]
    for command in value["available_commands"]:
        if (
            not isinstance(command, Mapping)
            or not isinstance(command.get("command_id"), str)
            or command.get("expected_projection_revision") != revision
        ):
            raise GuiWorkflowError(
                "Agent工作投影中的命令版本不一致",
                reason="gui_workflow_harness_projection_invalid",
            )
    return copy.deepcopy(dict(value))


def _workspace(
    board: Mapping[str, Any], *, has_path: bool, dirty: bool
) -> dict[str, Any]:
    return {
        "contract": "agent-app-workspace-v1",
        "document_kind": "whiteboard",
        "display_name": str(board.get("title") or "未命名白板")[:256],
        "current_view": "whiteboard",
        "dirty": dirty,
        "has_path": has_path,
        "can_save": True,
        "can_open": True,
        "can_export": False,
    }


def _candidate_identity(value: Mapping[str, Any]) -> tuple[int, str]:
    revision = value.get("revision")
    receipt = value.get("receipt_fingerprint")
    if (
        type(revision) is not int
        or revision < 0
        or not isinstance(receipt, str)
        or not receipt
    ):
        raise GuiWorkflowError(
            "原生Agent候选缺少可核验身份",
            reason="gui_workflow_candidate_identity_invalid",
        )
    return revision, receipt


def _calculated_delivery(
    accepted: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    delivery = accepted.get("calculated_delivery")
    if not isinstance(delivery, Mapping):
        raise GuiWorkflowError(
            "候选没有Host核验的计算交付",
            reason="gui_workflow_calculated_delivery_missing",
        )
    if "path" in delivery or "file_sha" in delivery:
        raise GuiWorkflowError(
            "Host错误地暴露了原生会话私有文件路径",
            reason="gui_workflow_private_path_exposed",
        )
    calculated = delivery.get("calculated")
    capability = delivery.get("delivery_capability")
    if (
        not isinstance(calculated, Mapping)
        or not isinstance(capability, str)
        or not capability
    ):
        raise GuiWorkflowError(
            "候选计算交付缺少结果或受信发布凭证",
            reason="gui_workflow_calculated_delivery_invalid",
        )
    return copy.deepcopy(dict(calculated)), capability


def _unsettled_app_actions(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        copy.deepcopy(dict(row))
        for row in value
        if isinstance(row, Mapping)
        and (
            row.get("status") == "claimed_for_external_execution"
            or row.get("execution_outcome_unknown") is True
            or bool(row.get("pendingSettlement"))
            or row.get("externalExecutionClaimed") is True
        )
    ]


def _pending_user_questions(value: object) -> list[dict[str, Any]]:
    """Project only bounded identities for unanswered Host questions.

    Question prose and options are normal GUI-visible data, but this
    non-interactive acceptance runner does not need them.  Keeping only the
    durable batch/item identities makes its receipt safe for logs while still
    telling a human exactly which GUI question suspended the run.
    """

    if not isinstance(value, Mapping):
        return []
    questions = value.get("questions")
    if not isinstance(questions, list):
        return []
    pending: list[dict[str, Any]] = []
    for row in questions[:16]:
        if not isinstance(row, Mapping) or row.get("state") != "open":
            continue
        batch_id = row.get("batch_id") or row.get("question_id")
        if not isinstance(batch_id, str) or _WORKBENCH_ID.fullmatch(batch_id) is None:
            continue
        raw_items = row.get("items")
        item_ids: list[str] = []
        if isinstance(raw_items, list):
            for item in raw_items[:3]:
                item_id = (
                    item.get("question_id")
                    if isinstance(item, Mapping)
                    else None
                )
                if (
                    isinstance(item_id, str)
                    and _WORKBENCH_ID.fullmatch(item_id) is not None
                ):
                    item_ids.append(item_id)
        pending.append(
            {
                "batch_id": batch_id,
                "mode": (
                    row.get("mode")
                    if row.get("mode") in {"live", "suspended"}
                    else "unknown"
                ),
                "question_count": len(raw_items) if isinstance(raw_items, list) else 0,
                "question_ids": item_ids,
            }
        )
    return pending


def _runtime_waits_for_user(value: object) -> bool:
    if not isinstance(value, list):
        return False
    return any(
        isinstance(row, Mapping)
        and row.get("type") == "status"
        and row.get("state") == "awaiting_user_answer"
        for row in value
    )


def _user_question_suspension(
    api: object,
    *,
    session_id: str,
    events: object,
) -> dict[str, Any] | None:
    projection = _mapping(
        _direct(api, "agent_workbench_projection", session_id),
        name="Agent工作台",
    )
    pending = _pending_user_questions(projection)
    runtime_signal = _runtime_waits_for_user(events)
    if not pending and not runtime_signal:
        return None
    return {
        "session_id": session_id,
        "pending_question_count": sum(
            int(row["question_count"]) for row in pending
        ),
        "pending_question_batch_count": len(pending),
        "pending_questions": pending,
        "runtime_wait_signal": runtime_signal,
    }


def _shutdown(api: object | None) -> None:
    if api is None:
        return
    target = getattr(api, "agent_shutdown", None)
    if callable(target):
        try:
            target()
        except Exception:  # noqa: BLE001 - preserve the workflow result
            return


def _bounded_diagnostic(value: object, *, depth: int = 0) -> object:
    """Keep CLI failure receipts useful without echoing unbounded artifacts."""

    if depth >= 4:
        return "details_omitted"
    if value is None or type(value) in {bool, int, float}:
        return value
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, Mapping):
        rows = list(value.items())
        result = {
            str(key)[:128]: _bounded_diagnostic(item, depth=depth + 1)
            for key, item in rows[:32]
        }
        if len(rows) > 32:
            result["details_omitted"] = len(rows) - 32
        return result
    if isinstance(value, (list, tuple)):
        result = [_bounded_diagnostic(item, depth=depth + 1) for item in value[:16]]
        if len(value) > 16:
            result.append({"details_omitted": len(value) - 16})
        return result
    return type(value).__name__


def run_gui_agent_workflow(
    *,
    data_dir: str | Path,
    prompt: str,
    output_dir: str | Path,
    input_path: str | Path | None = None,
    workflow: str = "staged",
    model: str | None = None,
    effort: str = "high",
    timeout_seconds: int = 1800,
    allow_draft: bool = False,
    provider_mode: str = "saved_credential",
    api_factory: Callable[[Path], object] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Execute one full GUI-native Agent turn and verify reloaded files.

    ``timeout_seconds`` controls one Host observation wait.  It is not a model
    or task deadline; healthy observable work continues under the turn budget.

    The output directory must be new. A successful return proves that the
    candidate was committed, the whiteboard and calculated package were saved
    by reviewed Host methods, and a fresh ``GuiApi`` accepted both files.
    """

    if not isinstance(prompt, str) or not prompt.strip():
        raise GuiWorkflowError(
            "任务文本不能为空", reason="gui_workflow_prompt_required"
        )
    if workflow not in {"build", "calculate", "staged"}:
        raise GuiWorkflowError(
            "工作流类型不受支持", reason="gui_workflow_invalid_input"
        )
    if not isinstance(effort, str) or not effort:
        raise GuiWorkflowError(
            "推理档位不能为空", reason="gui_workflow_invalid_input"
        )
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise GuiWorkflowError(
            "超时时间必须为正整数", reason="gui_workflow_invalid_input"
        )
    if provider_mode not in _PROVIDER_MODES:
        raise GuiWorkflowError(
            "提供方验收模式不受支持", reason="gui_workflow_invalid_input"
        )
    injected_factory = api_factory is not None
    if provider_mode == "deterministic_provider_transport" and not injected_factory:
        raise GuiWorkflowError(
            "确定性提供方验收必须显式注入完整生产Agent composition",
            reason="gui_workflow_deterministic_composition_required",
        )
    observation_receipt = {
        "observation_window_seconds": timeout_seconds,
        "timeout_semantics": "host_observation_window",
    }

    directory = Path(data_dir).expanduser().resolve()
    target_dir = Path(output_dir).expanduser().resolve()
    if target_dir.exists():
        raise GuiWorkflowError(
            "输出目录已经存在；不会覆盖或混入旧结果",
            reason="gui_workflow_output_exists",
            details={"output_dir": str(target_dir)},
        )
    board_path = target_dir / "result.whiteboard.json"
    calculated_path = target_dir / "result.calculated.json"

    if api_factory is None:
        from genshin_app.gui.api import GuiApi

        api_factory = GuiApi

    api: object | None = None
    verifier: object | None = None
    session_id: str | None = None
    preserve_suspended_session = False
    failure_stage = "gui_api_create"
    verification_layers = {
        "gui_api_document_transport": "not_verified",
        "native_dsh_tool_runtime": "not_verified",
        "candidate_review_application": "not_verified",
        "whiteboard_save_reload": "not_verified",
        "calculated_publish_reload": "not_verified",
    }
    try:
        api = api_factory(directory)
        documents = GuiDocumentClient(api)

        if provider_mode == "deterministic_provider_transport":
            identity_ping = _mapping(_direct(api, "ping"), name="App身份")
            app_identity = identity_ping.get("app_identity")
            if not isinstance(app_identity, Mapping) or app_identity.get(
                "provider_transport"
            ) != "deterministic":
                raise GuiWorkflowError(
                    "确定性提供方验收没有使用受审的完整App composition",
                    reason="gui_workflow_deterministic_composition_required",
                )

        failure_stage = "provider_connect"
        detected = _mapping(
            _direct(api, "agent_detect"), name="Agent发现结果"
        )
        native = next(
            (
                row
                for row in detected.get("candidates", [])
                if isinstance(row, Mapping)
                and row.get("id") == "deepseek-api"
                and row.get("kind") == "native_api"
            ),
            None,
        )
        if native is None or native.get("supported") is not True:
            raise GuiWorkflowError(
                "当前App没有可用的DeepSeek原生Agent",
                reason=str(
                    native.get("error_code")
                    if isinstance(native, Mapping)
                    else "native_v2_not_ready"
                ),
            )
        if native.get("credential_saved") is not True:
            raise GuiWorkflowError(
                "Windows中没有已保存的DeepSeek API Key；请先在GUI设置中保存",
                reason="native_credential_required",
            )
        connected = _mapping(
            _direct(
                api,
                "agent_connect",
                {
                    "id": "deepseek-api",
                    "kind": "native_api",
                    "api_key": "",
                    "remember_credential": True,
                },
            ),
            name="Agent连接结果",
        )
        verification_layers["gui_api_document_transport"] = "verified"

        if input_path is None:
            opened = _mapping(
                documents.call("whiteboard_new"), name="新白板"
            )
            has_input_path = False
        else:
            opened = _mapping(
                documents.call(
                    "whiteboard_load",
                    str(Path(input_path).expanduser().resolve()),
                ),
                name="白板读取结果",
            )
            has_input_path = True
        board = _mapping(opened.get("board"), name="白板")
        baseline_payload = {
            "kind": "whiteboard",
            "board": copy.deepcopy(board),
            "calculated": None,
        }
        start_request: dict[str, Any] = {
            "kind": "whiteboard",
            "workflow": workflow,
            "document": copy.deepcopy(board),
            "workspace": _workspace(
                board, has_path=has_input_path, dirty=False
            ),
            "message": prompt.strip(),
            "effort": effort,
            "client_request_id": str(uuid.uuid4()),
        }
        if model is not None:
            start_request["model"] = model
        failure_stage = "turn_submit"
        started = _mapping(
            documents.call("agent_start", start_request), name="Agent启动结果"
        )
        session_id = started.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise GuiWorkflowError(
                "Agent启动结果缺少会话身份",
                reason="gui_workflow_session_invalid",
            )

        snapshot = started
        next_seq = snapshot.get("next_seq", 0)
        if type(next_seq) is not int or next_seq < 0:
            next_seq = 0
        generation = 1
        harness_projection = _harness_projection(snapshot)
        # ``timeout_seconds`` is an observation window, not a second task
        # budget.  The native runtime already freezes and enforces the
        # user-visible, versioned turn budget.  An independent
        # absolute CLI deadline would race that authority and could shut down
        # a healthy model while it is still reasoning or while the App is
        # completing deterministic work.  Every successful wait response is
        # current Host evidence that the session is still observable; genuine
        # transport/protocol failures are raised by ``documents.call`` and
        # terminal model/runtime failures are handled below.
        observation_wait_ms = max(1, min(12_000, timeout_seconds * 1_000))
        failure_stage = "turn_wait"
        while (
            snapshot.get("active") is True
            or snapshot.get("state") not in _TERMINAL_STATES
        ):
            snapshot = _mapping(
                documents.call(
                    "agent_wait_events",
                    session_id,
                    next_seq,
                    generation,
                    observation_wait_ms,
                ),
                name="Agent等待结果",
            )
            observed_harness = _harness_projection(snapshot)
            if observed_harness is not None:
                harness_projection = observed_harness
            received_seq = snapshot.get("next_seq")
            if type(received_seq) is int and received_seq >= next_seq:
                next_seq = received_seq
            generation += 1
            suspension = _user_question_suspension(
                api,
                session_id=session_id,
                events=snapshot.get("events"),
            )
            if suspension is not None:
                # This is a normal Agent suspension, not a failed turn.  Do
                # not accept a candidate, publish files, answer the question,
                # or explicitly shut down/cancel the resumable session.
                preserve_suspended_session = True
                return {
                    "contract": "gui-native-agent-workflow-receipt-v1",
                    **observation_receipt,
                    "status": "awaiting_user_answer",
                    "requires_user_input": True,
                    **suspension,
                    "connection": connected.get("connection"),
                    "provider_mode": provider_mode,
                    "workflow": workflow,
                    "model": model,
                    "effort": effort,
                    "board": None,
                    "calculated": None,
                    "files_published": False,
                    "reload_verified": False,
                    "continuation": "resume_same_session_after_user_answer",
                    "harness_projection": harness_projection,
                    "verification_layers": copy.deepcopy(verification_layers),
                }

        state = snapshot.get("state")
        if state in _FAILED_STATES:
            continuable = bool(harness_projection) and any(
                isinstance(row, Mapping)
                and row.get("enabled") is True
                and row.get("command_id") in {
                    "conversation.send",
                    "turn.retry",
                    "draft.continue",
                }
                for row in harness_projection["available_commands"]
            )
            if continuable:
                preserve_suspended_session = True
            raise GuiWorkflowError(
                "原生Agent运行失败；会话与事件仍保留供诊断",
                reason=str(
                    snapshot.get("failure_code")
                    or "gui_workflow_agent_failed"
                ),
                details={
                    "session_id": session_id,
                    "state": state,
                    "terminal_status": snapshot.get("terminal_status"),
                    "terminal_reason": snapshot.get("terminal_reason"),
                    "next_seq": next_seq,
                    "session_continuable": continuable,
                    "harness_projection": harness_projection,
                },
            )
        verification_layers["native_dsh_tool_runtime"] = "verified"
        suspension = _user_question_suspension(
            api,
            session_id=session_id,
            events=snapshot.get("events"),
        )
        if suspension is not None:
            preserve_suspended_session = True
            return {
                "contract": "gui-native-agent-workflow-receipt-v1",
                **observation_receipt,
                "status": "awaiting_user_answer",
                "requires_user_input": True,
                **suspension,
                "connection": connected.get("connection"),
                "provider_mode": provider_mode,
                "workflow": workflow,
                "model": model,
                "effort": effort,
                "board": None,
                "calculated": None,
                "files_published": False,
                "reload_verified": False,
                "continuation": "resume_same_session_after_user_answer",
                "harness_projection": harness_projection,
                "verification_layers": copy.deepcopy(verification_layers),
            }
        unsettled_actions = _unsettled_app_actions(snapshot.get("app_actions"))
        if unsettled_actions:
            raise GuiWorkflowError(
                "Agent要求用户完成App操作；CLI不会自动批准或代答",
                reason="gui_workflow_user_action_required",
                details={
                    "session_id": session_id,
                    "app_actions": unsettled_actions,
                },
            )

        if harness_projection is not None and not any(
            isinstance(row, Mapping)
            and row.get("command_id") == "candidate.review"
            and row.get("enabled") is True
            for row in harness_projection["available_commands"]
        ):
            raise GuiWorkflowError(
                "Host尚未开放候选审核；草稿仍可继续完善、重验或丢弃",
                reason="gui_workflow_candidate_not_reviewable",
                details={
                    "session_id": session_id,
                    "projection_revision": harness_projection[
                        "projection_revision"
                    ],
                },
            )

        failure_stage = "candidate_review"
        candidate_response = _mapping(
            documents.call("agent_candidate", session_id), name="Agent候选"
        )
        candidate = _mapping(
            candidate_response.get("candidate", candidate_response),
            name="候选内容",
        )
        if candidate.get("ready") is not True:
            raise GuiWorkflowError(
                "Agent候选尚未满足GUI应用条件",
                reason="gui_workflow_candidate_not_ready",
                details={
                    "session_id": session_id,
                    "revision": candidate.get("revision"),
                    "workflow_stage": candidate.get("workflow_stage"),
                    "stage_status": candidate.get("stage_status"),
                    "issues": candidate.get("issues"),
                },
            )
        revision, receipt = _candidate_identity(candidate)
        application_kind = candidate.get("application_kind")
        if application_kind not in {"whiteboard_draft", "whiteboard_calculated"}:
            raise GuiWorkflowError(
                "Agent候选类型不是可应用的白板结果",
                reason="gui_workflow_candidate_kind_invalid",
                details={
                    "session_id": session_id,
                    "application_kind": application_kind,
                },
            )
        is_draft = application_kind == "whiteboard_draft"
        if is_draft and not allow_draft:
            raise GuiWorkflowError(
                "Agent只生成了阶段草稿；正式GUI工作流不把草稿发布为计算结果",
                reason="gui_workflow_draft_not_publishable",
                details={"session_id": session_id, "revision": revision},
            )

        accepted = _mapping(
            documents.call(
                "agent_accept",
                session_id,
                "whiteboard",
                board,
                revision,
                receipt,
                baseline_payload,
            ),
            name="候选接纳结果",
        )
        accepted_board = _mapping(accepted.get("board"), name="接纳白板")
        if accepted_board.get("board_id") != board.get("board_id"):
            raise GuiWorkflowError(
                "候选返回了不同的白板身份",
                reason="gui_workflow_candidate_document_mismatch",
            )
        calculated: dict[str, Any] | None
        delivery_capability: str | None
        if is_draft:
            if accepted.get("calculated_delivery") is not None:
                raise GuiWorkflowError(
                    "阶段草稿错误地携带了计算交付",
                    reason="gui_workflow_draft_delivery_invalid",
                )
            calculated = None
            delivery_capability = None
        else:
            calculated, delivery_capability = _calculated_delivery(accepted)
        actual_payload = {
            "kind": "whiteboard",
            "board": copy.deepcopy(accepted_board),
            "calculated": copy.deepcopy(calculated),
        }
        failure_stage = "candidate_application"
        applied = _mapping(
            documents.call(
                "agent_candidate_applied",
                session_id,
                "whiteboard",
                accepted_board,
                revision,
                receipt,
                _workspace(accepted_board, has_path=False, dirty=True),
                actual_payload,
            ),
            name="候选应用回执",
        )
        if (
            applied.get("status") != "applied"
            or applied.get("revision") != revision
            or applied.get("receipt_fingerprint") != receipt
        ):
            raise GuiWorkflowError(
                "Host没有确认候选已精确应用",
                reason="gui_workflow_candidate_application_unconfirmed",
            )
        verification_layers["candidate_review_application"] = "verified"

        # Do not leave an empty "result" directory for provider, review, or
        # application failures.  Publication starts only after the exact
        # candidate-applied proof has committed.
        target_dir.mkdir(parents=True, exist_ok=False)
        failure_stage = "whiteboard_save"
        saved_board = _mapping(
            documents.call(
                "whiteboard_save", str(board_path), accepted_board, None
            ),
            name="白板保存回执",
        )
        if is_draft:
            verifier = api_factory(directory)
            verify_documents = GuiDocumentClient(verifier)
            failure_stage = "fresh_gui_reload"
            reloaded = _mapping(
                verify_documents.call("whiteboard_load", str(board_path)),
                name="重载白板",
            )
            reloaded_board = _mapping(
                reloaded.get("board"), name="重载白板内容"
            )
            if reloaded_board != accepted_board:
                raise GuiWorkflowError(
                    "重新打开后的白板与接纳候选不一致",
                    reason="gui_workflow_reload_mismatch",
                )
            verification_layers["whiteboard_save_reload"] = "verified"
            verification_layers["calculated_publish_reload"] = "not_applicable"
            return {
                "contract": "gui-native-agent-workflow-receipt-v1",
                **observation_receipt,
                "status": "draft_verified",
                "session_id": session_id,
                "connection": connected.get("connection"),
                "provider_mode": provider_mode,
                "workflow": workflow,
                "model": candidate.get("model") or model,
                "effort": effort,
                "revision": revision,
                "receipt_fingerprint": receipt,
                "board": {
                    "path": str(board_path),
                    "file_sha": saved_board.get("file_sha"),
                },
                "calculated": None,
                "reload_verified": True,
                "harness_projection": harness_projection,
                "verification_layers": copy.deepcopy(verification_layers),
            }
        assert delivery_capability is not None
        failure_stage = "calculated_publish"
        published = _mapping(
            documents.call(
                _PUBLISH_METHOD,
                session_id,
                delivery_capability,
                str(calculated_path),
                revision,
                receipt,
            ),
            name="计算包发布回执",
        )
        if Path(str(published.get("path", ""))).resolve() != calculated_path:
            raise GuiWorkflowError(
                "Host发布回执指向了不同的计算包路径",
                reason="gui_workflow_publish_receipt_invalid",
            )

        failure_stage = "fresh_gui_reload"
        verifier = api_factory(directory)
        verify_documents = GuiDocumentClient(verifier)
        reloaded = _mapping(
            verify_documents.call("whiteboard_load", str(board_path)),
            name="重载白板",
        )
        reloaded_board = _mapping(
            reloaded.get("board"), name="重载白板内容"
        )
        if reloaded_board != accepted_board:
            raise GuiWorkflowError(
                "重新打开后的白板与接纳候选不一致",
                reason="gui_workflow_reload_mismatch",
            )
        verification_layers["whiteboard_save_reload"] = "verified"
        verified = _mapping(
            verify_documents.call(
                "whiteboard_read_result",
                reloaded_board,
                str(calculated_path),
            ),
            name="计算包回流核验",
        )
        verified_calculated = verified.get("calculated")
        if not isinstance(verified_calculated, Mapping):
            raise GuiWorkflowError(
                "重新打开后没有得到完整计算包",
                reason="gui_workflow_reload_result_missing",
            )
        verification_layers["calculated_publish_reload"] = "verified"
        return {
            "contract": "gui-native-agent-workflow-receipt-v1",
            **observation_receipt,
            "status": "verified",
            "session_id": session_id,
            "connection": connected.get("connection"),
            "provider_mode": provider_mode,
            "workflow": workflow,
            "model": candidate.get("model") or model,
            "effort": effort,
            "revision": revision,
            "receipt_fingerprint": receipt,
            "board": {
                "path": str(board_path),
                "file_sha": saved_board.get("file_sha"),
            },
            "calculated": {
                "path": str(calculated_path),
                "file_sha": published.get("file_sha"),
                "result_receipt": verified_calculated.get("result_receipt"),
            },
            "reload_verified": True,
            "harness_projection": harness_projection,
            "verification_layers": copy.deepcopy(verification_layers),
        }
    except GuiWorkflowError as error:
        cause = _bounded_diagnostic(error.details)
        details = {
            "contract": "gui-native-agent-workflow-receipt-v1",
            "status": "failed",
            "provider_mode": provider_mode,
            "failure_stage": failure_stage,
            "session_id": session_id,
            "verification_layers": copy.deepcopy(verification_layers),
        }
        if isinstance(cause, Mapping):
            details.update(cause)
        elif cause is not None:
            details["cause"] = cause
        raise GuiWorkflowError(
            str(error), reason=error.reason, details=details
        ) from error
    except GuiDocumentCallError as error:
        raise GuiWorkflowError(
            str(error),
            reason=error.reason,
            details={
                "session_id": session_id,
                "operation_may_have_completed": (
                    error.operation_may_have_completed
                ),
                "transport_details": error.details,
                "automatic_retry_forbidden": (
                    error.operation_may_have_completed
                ),
                "contract": "gui-native-agent-workflow-receipt-v1",
                "status": "failed",
                "provider_mode": provider_mode,
                "failure_stage": failure_stage,
                "verification_layers": copy.deepcopy(verification_layers),
            },
        ) from error
    finally:
        # The verification GuiApi never opens an Agent manager; calling its
        # shutdown endpoint would lazily create a second runtime solely to
        # stop it.  It owns only stateless document reads and needs no close.
        if not preserve_suspended_session:
            _shutdown(api)


def read_prompt_file(path: str | Path, *, maximum_bytes: int = 1 << 20) -> str:
    source = Path(path).expanduser().resolve()
    with source.open("rb") as stream:
        raw = stream.read(maximum_bytes + 1)
    if len(raw) > maximum_bytes:
        raise GuiWorkflowError(
            "任务文本超过1 MiB上限",
            reason="gui_workflow_prompt_too_large",
        )
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise GuiWorkflowError(
            "任务文本必须为UTF-8",
            reason="gui_workflow_prompt_invalid",
        ) from error
