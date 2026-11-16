"""CLI projection of the production DSH Controller and App document ports.

No WebView/window is started. DSH owns the model loop, messages and history.
This one-shot client submits a user task, observes public follow frames, and
may apply the exact reviewed candidate to its own isolated document. It never
reads a private Session directory or manufactures a calculation package.
"""
from __future__ import annotations

import copy
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

from genshin_app.app_system_fault import (
    is_domain_exception,
    safe_exception_diagnostic,
    system_failure,
)
from genshin_app.gui.headless_document_client import GuiDocumentClient

from .native_app_action_ledger import sanitize_suggested_basename


class GuiWorkflowError(RuntimeError):
    def __init__(self, message, *, reason, details=None):
        super().__init__(message)
        self.reason, self.details = reason, details


def _failure_receipt(error, *, stage, data_dir):
    if is_domain_exception(error):
        # Preserve typed producer settlement, including its original details.
        return {"reason": getattr(error, "reason", getattr(error, "code", None)),
                "details": getattr(error, "details", None)}
    from genshin_app.app_fault_store import fault_scope
    with fault_scope(data_dir):
        failure = system_failure(error, component="app_domain_binding", operation="gui_workflow." + stage)
    return {"reason": failure["reason"], "details": {
        "workflow_stage": stage, "system_failure": failure,
        "diagnostic": safe_exception_diagnostic(error)}}


def read_prompt_file(path, *, maximum_bytes=1 << 20):
    from genshin_app.equipment.files import read_input_bytes
    raw = read_input_bytes(path, maximum_bytes=maximum_bytes, field="/prompt_file")
    if len(raw) > maximum_bytes:
        raise GuiWorkflowError("任务文本超过1 MiB", reason="gui_workflow_prompt_too_large")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeError:
        raise GuiWorkflowError("任务文本必须为UTF-8", reason="gui_workflow_prompt_invalid") from None


def _bare_document(api, method, request):
    value = json.loads(api.document_json_call(method, json.dumps([request], ensure_ascii=False, allow_nan=False)))
    if value.get("code") == 1:
        raise GuiWorkflowError(value.get("message", "App领域操作失败"), reason=value["reason"], details=value.get("details"))
    return value


def _remote(api, endpoint, args):
    value = api.dsh_client_rpc({"request_id": str(uuid.uuid4()), "channel": "/api", "endpoint": endpoint,
        "payload": {"args": args}})
    if value.get("ok") is not True:
        failure = value.get("error", {})
        raise GuiWorkflowError(failure.get("message", "DSH请求失败"),
            reason=failure.get("code", "dsh_rpc_failed"), details=failure.get("details"))
    return value.get("value")


def _rpc(api, endpoint, request=None):
    return _remote(api, endpoint, {} if request is None else {"request": request})


def _goal_continuation(api, session_id, ended, receipt):
    """Observe upstream's live Goal, never create or schedule a continuation.

    Durable phase alone is insufficient: active goals restored from disk may
    be disarmed, and a completed goal's current Turn can still be producing
    its final reply. The public live Goal and Session observations own both
    decisions; no App-owned goal state or round retry is introduced here.
    """
    goal = _remote(api, "goals/get", {"agentId": session_id})
    if goal is None and "dsh_goal" not in receipt:
        return False  # Preserve the ordinary no-Goal single-Turn behavior.
    if goal is not None and (type(goal) is not dict or goal.get("phase") not in {
            "active", "paused", "blocked", "complete"} or goal.get("activation") not in {"armed", "disarmed"}):
        raise GuiWorkflowError("官方目标状态无法核对", reason="dsh_goal_observation_invalid")
    receipt["dsh_goal"] = copy.deepcopy(goal)
    status = api.dsh_client_session_status({"dsh_session_id": session_id})
    if status.get("code") == 1:
        raise GuiWorkflowError(status.get("message", "无法核对官方会话状态"),
            reason=status.get("reason", "dsh_session_observation_failed"), details=status.get("details"))
    if status.get("session_id") != session_id or type(status.get("agent_running")) is not bool:
        raise GuiWorkflowError("官方会话运行状态无法核对", reason="dsh_session_observation_invalid")
    # A newer Turn may already have finished before the follow reader caught
    # up. Consume its real final reply/end instead of exporting the earlier
    # round's candidate merely because the live goal is now complete.
    return (status["agent_running"] or status.get("last_turn") != ended
        or goal is not None and goal["phase"] == "active" and goal["activation"] == "armed")


def _export_input_draft(documents, *, conversation_id, revision, output_dir):
    """User CLI request uses the same file card, claim, service and settlement."""
    card = documents.call("dsh_client_request_candidate_input_draft", {
        "conversation_id": conversation_id, "expected_revision": revision})["card"]
    def action(command, arguments):
        return documents.call("dsh_client_app_action", {"conversation_id": conversation_id,
            "command": command, "arguments": arguments})
    card = action("present", {"action_id": card["action_id"]})["card"]
    identity = {"action_id": card["action_id"], "ui_receipt": card["ui_receipt"]}
    claimed = action("claim", identity)
    if claimed.get("request", {}).get("target", {}).get("kind") != "candidate_input_draft":
        raise GuiWorkflowError("文件卡未指向当前候选输入草稿", reason="gui_workflow_draft_target_mismatch")
    path = output_dir / "result.gencdraft.json"
    published = documents.call("dsh_client_publish_candidate_input_draft", {
        "conversation_id": conversation_id, **identity, "output_path": str(path),
        "expected_file_sha": None, "overwrite": False})
    completed = action("settle", {**identity, "outcome": {"status": "completed",
        "message": "CLI投影已保存用户选择的候选输入草稿", "dirty_open_guard_passed": False}})
    if completed["card"]["status"] != "completed":
        raise GuiWorkflowError("候选草稿保存尚未完成结算", reason="gui_workflow_draft_not_settled")
    return path, published


def _resume_document(api, documents, session_id, target, input_path):
    """Recover the App's source document through its public snapshot service.

    The source is ``session.document`` captured by the ordinary draft store,
    never the Agent-editable candidate or a reconstruction from chat history.
    The final review/application still checks the entire original baseline.
    """
    from genshin_app.equipment.files import fingerprint

    from .candidate_input_draft_store import load_candidate_input_draft

    bound = _bare_document(api, "dsh_client_domain_binding", {"dsh_session_id": session_id})["binding"]
    if bound is None:
        raise GuiWorkflowError("原会话没有可恢复的App候选，请核对会话和资料目录", reason="gui_workflow_resume_unbound")
    cid = bound["conversation_id"]
    projection = bound["projection"]
    if projection["candidate"]["application_state"] in {"prepared", "baseline_advanced", "outcome_unknown"}:
        raise GuiWorkflowError("原会话仍有未结算的白板同步，请先核对或恢复该操作；不会自动重做",
            reason="gui_workflow_resume_application_unsettled")
    before = documents.call("dsh_client_candidate", {"conversation_id": cid})
    directory = target / "resume-baseline"
    directory.mkdir(exist_ok=False)
    path, published = _export_input_draft(documents, conversation_id=cid, revision=before["revision"], output_dir=directory)
    exported = load_candidate_input_draft(path)["draft"]
    source = exported["input"]["source_document"]
    if source["representation"] != "whiteboard":
        raise GuiWorkflowError("该会话的原始文档不是白板；此GUI白板投影入口不能猜测转换",
            reason="gui_workflow_resume_document_kind")
    board = source["payload"]
    if board["board_id"] != projection["document_id"]:
        raise GuiWorkflowError("原正式白板与会话绑定身份不一致", reason="gui_workflow_resume_baseline_mismatch")
    if input_path is not None:
        supplied = documents.call("whiteboard_load", str(input_path))["board"]
        if supplied != board:
            raise GuiWorkflowError("指定白板不是此会话保存的原正式基线；未发送新消息或替换候选",
                reason="gui_workflow_resume_baseline_mismatch")
    else:
        input_path = directory / "source.whiteboard.json"
        documents.call("whiteboard_save", str(input_path), board, None)
        if documents.call("whiteboard_load", str(input_path))["board"] != board:
            raise GuiWorkflowError("导出的原正式白板回读不一致", reason="gui_workflow_resume_baseline_mismatch")
    after = documents.call("dsh_client_candidate", {"conversation_id": cid})
    if before["revision"] != after["revision"] or exported["source"]["candidate_revision"] != after["revision"]:
        raise GuiWorkflowError("恢复读取期间候选已变化，请核对当前版本", reason="gui_workflow_resume_candidate_changed")
    return board, input_path, cid, {
        "candidate_revision_before": after["revision"],
        "candidate_fingerprint_before": exported["source"]["candidate_fingerprint"],
        "source_document_fingerprint": fingerprint(board),
        "source_document_id": board["board_id"], "baseline_path": str(input_path),
        "source_snapshot_path": str(path), "source_snapshot_fingerprint": published["input_snapshot_fingerprint"],
        "source": "app_saved_source_document_via_public_draft_export", "candidate_reinitialized": False,
    }


def _projected_model_selection(catalog, prior_selection, *, session_id, provider=None, model=None, effort=None):
    """Project route intent; DSH owns catalog validation and derived parameters."""
    selected = {**(prior_selection.get("next") or prior_selection.get("lastUsed") or catalog["default"]),
        "sessionId": session_id}
    if provider is not None:
        selected["provider"] = provider
    if model is not None:
        selected["model"] = model
    if provider is not None or model is not None:
        selected.pop("reasoningEffort", None)
    if effort is not None:
        selected["reasoningEffort"] = effort
    return selected


def run_gui_agent_workflow(*, data_dir, prompt, output_dir, input_path=None, workflow="artifact",
                           provider=None, model=None, effort=None, timeout_seconds=1800, allow_draft=False, resume_session_id=None,
                           provider_mode="saved_credential", api_factory=None):
    if workflow != "artifact":
        raise GuiWorkflowError("当前原生Agent使用自由工作重点与工件验证；请使用artifact工作流。",
            reason="gui_workflow_retired_phase_owner")
    if (provider_mode not in {"saved_credential", "deterministic_provider_transport"}
            or provider_mode == "deterministic_provider_transport" and
                getattr(api_factory, "dsh_projection_provider_mode", None) != provider_mode):
        raise GuiWorkflowError("确定性模型只能通过显式受审测试composition注入", reason="gui_workflow_provider_mode_invalid")
    if not isinstance(prompt, str) or not prompt.strip() or type(timeout_seconds) is not int or timeout_seconds < 1:
        raise GuiWorkflowError("任务或观察周期不合法", reason="gui_workflow_arguments_invalid")
    if resume_session_id is not None and (not isinstance(resume_session_id, str) or not resume_session_id.strip()):
        raise GuiWorkflowError("恢复会话ID不能为空", reason="gui_workflow_arguments_invalid")
    if provider is not None and (not isinstance(provider, str) or not provider.strip() or not model):
        raise GuiWorkflowError("指定 --provider 时请同时指定 --model；提供商和模型以DSH目录为准",
            reason="gui_workflow_model_route_required")
    if api_factory is None:
        from genshin_app.gui.api import GuiApi

        api_factory = GuiApi
    target = Path(output_dir).absolute()
    from genshin_app.output_policy import validate_database_outputs
    validate_database_outputs([target / "gui-workflow-receipt.json"], Path(data_dir) / "genshin.db",
                              input_paths=[input_path] if input_path else [])
    target.mkdir(parents=True, exist_ok=False)
    receipt = {"contract": "gui-native-agent-dsh-workflow-receipt-v2", "status": "starting",
        "workflow": workflow, "provider_mode": provider_mode, "generic_owner": "dsh",
        "real_gui_tested": False, "reload_verified": False,
        "timeout_semantics": "observation_interval_only_healthy_work_not_cancelled"}
    api = verifier = None
    stream_id = None
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dsh-gui-follow")
    stage = "app_factory"
    try:
        api = api_factory(Path(data_dir))
        documents = GuiDocumentClient(api)
        # This is the same idle transport as the GUI; boot never requests a model.
        stage = "transport_boot"
        api.dsh_client_boot()
        if resume_session_id is None:
            stage = "document_open"
            board = (documents.call("whiteboard_load", str(input_path))["board"] if input_path
                     else documents.call("whiteboard_new")["board"])
            stage = "session_create"
            sid = _rpc(api, "session/create", {})["sessionId"]
            stage = "document_bind"
            bound = _bare_document(api, "dsh_client_bind_document", {"dsh_session_id": sid,
                "document_kind": "whiteboard", "document": board})
            cid = bound["binding"]["conversation_id"]
        else:
            sid = resume_session_id
            stage = "session_resume_observation"
            previous = _rpc(api, "session/projections", {"sessionId": sid})
            if previous is None or previous.get("kind") != "sequenced":
                raise GuiWorkflowError("找不到可直接恢复的原会话，或其历史需要格式迁移", reason="gui_workflow_resume_session_unavailable")
            stage = "resume_source_document"
            board, input_path, cid, recovery = _resume_document(api, documents, sid, target, input_path)
            receipt["resume"] = {**recovery, "session_id": sid, "new_user_message": False,
                "model_selection_reinitialized": False, "goal_resumed_by_observer": False}
        receipt["session_id"] = sid
        receipt["conversation_id"] = cid
        documents.call("dsh_client_workspace_state", {"conversation_id": cid,
            "current_document_id": board["board_id"], "workspace": {
                "document_kind": "whiteboard", "display_name": sanitize_suggested_basename(board["title"]) or "排轴白板",
                "current_view": "whiteboard", "dirty": input_path is None, "has_path": input_path is not None,
                "can_save": True, "can_open": True, "can_export": False}})
        stage = "model_selection"
        if resume_session_id is None or provider is not None or model is not None or effort is not None:
            catalog = _rpc(api, "session/modelCatalog")
            prior_selection = previous.get("values", {}).get("modelSelection", {}) if resume_session_id is not None else {}
            selected = _projected_model_selection(catalog, prior_selection, session_id=sid,
                provider=provider, model=model, effort=effort)
            _rpc(api, "session/selectModel", selected)
            receipt["model_selection"] = {key: selected[key] for key in ("provider", "model", "reasoningEffort") if key in selected}
            if resume_session_id is not None:
                receipt["resume"]["model_selection_reinitialized"] = True
        else:
            selection = previous.get("values", {}).get("modelSelection", {})
            receipt["model_selection"] = copy.deepcopy(selection.get("next") or selection.get("lastUsed") or {})
            receipt["model_selection"]["mode"] = "preserved_from_official_session"
        stage = "follow_open"
        stream_id = str(uuid.uuid4())
        api.dsh_client_stream_open({"stream_id": stream_id, "channel": "/api", "endpoint": "session/follow",
            "payload": {"args": {"request": {"address": {"kind": "session", "sessionId": sid},
                "assistantStream": True}}}, "has_uplink": False})
        first = api.dsh_client_stream_read({"stream_id": stream_id, "cursor": 0})
        cursor = first["cursor"]
        if resume_session_id is not None:
            snapshot = next((frame for frame in first["items"] if frame.get("type") == "snapshot"), None)
            if snapshot is None or snapshot["header"]["id"] != sid:
                raise GuiWorkflowError("跟随流没有恢复到指定原会话", reason="gui_workflow_resume_session_mismatch")
            receipt["resume"].update(history_cursor_before=snapshot["cursor"],
                history_event_count_before=snapshot["cursor"] + 1,
                opening_history_records=len(snapshot["records"]), history_has_more=snapshot["hasMore"],
                goal_before=copy.deepcopy(snapshot.get("projections", {}).get("values", {}).get("goal")))
        stage = "prompt_submission"
        prompt_request_id = str(uuid.uuid4())
        receipt["prompt_request_id"] = prompt_request_id
        accepted = _rpc(api, "session/prompt", {"sessionId": sid, "requestId": prompt_request_id,
            "mode": "queue", "content": [{"type": "text", "text": prompt}]})
        if accepted.get("accepted") is not True:
            raise GuiWorkflowError("模型没有接纳用户消息", reason="dsh_prompt_not_accepted")
        if resume_session_id is not None:
            receipt["resume"]["new_user_message"] = True
        receipt.update(status="running", public_event_count=0, assistant_reply="")
        ended = None
        observing_goal = False
        stage = "follow_events"
        while True:
            future = pool.submit(api.dsh_client_stream_read, {"stream_id": stream_id, "cursor": cursor})
            batch = None
            while True:
                try:
                    # A short observation cadence after a Turn end is not a
                    # task timeout. It only notices upstream disarm/round-cap
                    # settlement which need not append another turn/end.
                    batch = future.result(timeout=min(timeout_seconds, 5) if observing_goal else timeout_seconds)
                    break
                except TimeoutError:
                    if future.done():
                        # A transport TimeoutError is not an observer timeout.
                        raise
                    # Observer windows do not cancel a healthy model/engine.
                    status = api.dsh_client_runtime_status()
                    if status.get("state") in {"failed", "stopped"}:
                        raise GuiWorkflowError("原生运行进程已停止", reason="dsh_runtime_stopped", details=status) from None
                    if ended is not None and not _goal_continuation(api, sid, ended, receipt):
                        break
            if batch is None:
                break
            if batch.get("error") is not None:
                raise GuiWorkflowError("DSH跟随流失败", reason="dsh_follow_failed", details=batch["error"])
            cursor = batch["cursor"]
            boundary = False
            for frame in batch["items"]:
                if frame.get("type") != "event":
                    continue
                event = frame["event"]
                receipt["public_event_count"] += 1
                if event["type"] == "user/message" and event["data"].get("source", {}).get("rpcId") == prompt_request_id:
                    receipt["submitted_user_message"] = {"event_seq": event["seq"], "session_id": sid,
                        "source": copy.deepcopy(event["data"]["source"]),
                        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()}
                if event["type"] == "request/header":
                    declared = event["data"]["header"].get("tools", [])
                    names = [tool["name"] for tool in declared]
                    headers = receipt.setdefault("request_tool_summaries", [])
                    headers.append({"event_seq": event["seq"], "tools": names,
                        "schema_sha256": hashlib.sha256(json.dumps(declared,
                            sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()})
                    if len(headers) == 1 and "gs_read_skill" not in names:
                        raise GuiWorkflowError("首次模型请求未收到App领域工具", reason="gui_first_request_domain_tools_missing")
                if event["type"] == "tool/call" and event["data"].get("name", "").startswith("gs_"):
                    counts = receipt.setdefault("app_tool_calls", {})
                    name = event["data"]["name"]
                    counts[name] = counts.get(name, 0) + 1
                if event["type"] == "assistant/message":
                    receipt["assistant_reply"] = "\n".join(part["text"] for part in event["data"]["message"]["content"]
                        if part["type"] == "text")
                if event["type"] == "turn/end":
                    ended = copy.deepcopy(event["data"])
                    receipt.setdefault("dsh_turn_ends", []).append(ended)
                    boundary = True
                elif event["type"] == "goal/change":
                    boundary = True
            if ended is not None and boundary:
                observing_goal = _goal_continuation(api, sid, ended, receipt)
                if not observing_goal:
                    break
            if batch.get("done") and ended is None:
                raise GuiWorkflowError("DSH流结束，但没有模型轮结束事实", reason="dsh_follow_ended_without_turn")
            if batch.get("done"):
                raise GuiWorkflowError("官方目标仍在继续，但跟随流已结束", reason="dsh_follow_ended_with_active_goal")
        receipt["dsh_turn_end"] = ended
        if ended.get("reason", {}).get("kind") != "completed":
            receipt.update(status="failed", reason="dsh_turn_not_completed")
            return receipt
        goal = receipt.get("dsh_goal")
        if goal is not None and goal["phase"] in {"paused", "blocked"}:
            receipt.update(status=goal["phase"], reason="dsh_goal_" + goal["phase"])
            return receipt
        if goal is not None and goal["phase"] == "active":
            receipt.update(status="incomplete", reason="dsh_goal_disarmed")
            return receipt
        stage = "candidate_read"
        domain = _bare_document(api, "dsh_client_domain_binding", {"dsh_session_id": sid})["binding"]
        summary = documents.call("dsh_client_candidate", {"conversation_id": cid})
        receipt["candidate"] = copy.deepcopy(summary)
        receipt.update(revision=summary["revision"], receipt_fingerprint=summary.get("receipt_fingerprint"))
        if summary.get("can_accept") is not True or summary.get("receipt_fingerprint") is None:
            if allow_draft:
                stage = "input_draft_export"
                path, published = _export_input_draft(documents, conversation_id=cid,
                    revision=summary["revision"], output_dir=target)
                # The CLI user's explicit output selection uses the same
                # selected-file import port, never an Agent filesystem tool.
                stage = "input_draft_reload"
                verifier = api_factory(Path(data_dir))
                fresh = GuiDocumentClient(verifier)
                file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
                opened = fresh.call("dsh_client_import_candidate_input_draft", {
                    "dsh_session_id": "draft-reload-" + uuid.uuid4().hex,
                    "path": str(path), "expected_file_sha": file_sha})
                preview = opened.get("preview", {})
                if (not preview.get("compatible") or preview.get("input_snapshot_fingerprint") !=
                        published["input_snapshot_fingerprint"] or preview.get("can_apply")):
                    raise GuiWorkflowError("全新App回读候选输入草稿不一致", reason="gui_workflow_draft_reload_mismatch")
                receipt.update(status="draft_verified", reload_verified=True, candidate=summary,
                    formal_applied=False, calculation_verified=False,
                    input_draft={"path": str(path), "file_sha": file_sha,
                        "artifact_id": published["artifact_id"],
                        "input_snapshot_fingerprint": published["input_snapshot_fingerprint"]})
                return receipt
            receipt.update(status="incomplete", candidate=summary, reason="candidate_not_reviewable")
            return receipt
        calculated = summary.get("application_kind") == "whiteboard_calculated"
        if not calculated and not allow_draft:
            receipt.update(status="incomplete", candidate=summary, reason="calculation_not_completed")
            return receipt
        base = {"kind": "whiteboard", "document": board, "expected_revision": summary["revision"],
            "expected_receipt": summary["receipt_fingerprint"]}

        def command(name, arguments, projection):
            return documents.call("dsh_client_candidate_command", {"conversation_id": cid, "command_id": name,
                "expected_projection_revision": projection["projection_revision"], "arguments": arguments})

        stage = "candidate_review"
        review = command("candidate.review", base, domain["projection"])
        reviewed = review["result"]
        formal = reviewed["board"]
        delivery = reviewed.get("calculated_delivery")
        payload = {"kind": "whiteboard", "board": formal,
            "calculated": delivery["calculated"] if delivery is not None else None}
        stage = "application_prepare"
        prepared = command("candidate.apply.prepare", {**base, "document": formal,
            "baseline_payload": {"kind": "whiteboard", "board": board, "calculated": None},
            "target_payload": payload}, review["projection"])
        from genshin_app.gui.frontend_document_projection import apply_frontend_document
        applied = apply_frontend_document(board=board, reviewed=reviewed, conversation_id=cid,
                                         path=str(input_path) if input_path else None)
        if applied is not None:
            actual = applied["application_payload"]
            receipt["document_application_scope"] = applied["scope"]
        else:
            # A frozen Host selfcheck has no development DOM dependencies.
            # This explicit protocol-only fixture is not frontend evidence.
            actual = copy.deepcopy(payload)
            receipt["document_application_scope"] = "host_protocol_only"
        receipt["production_frontend_document_tested"] = applied is not None
        stage = "application_ack"
        command("application.ack", {**base, "document": actual["board"], "actual_payload": actual}, prepared["projection"])
        board_path = target / "result.whiteboard.json"
        stage = "whiteboard_save"
        documents.call("whiteboard_save", str(board_path), actual["board"], None)
        receipt["board"] = {"path": str(board_path), "file_sha": hashlib.sha256(board_path.read_bytes()).hexdigest()}
        calculated_path = target / "result.calculated.json"
        if delivery is not None:
            stage = "calculated_save"
            documents.call("dsh_client_publish_whiteboard_result", {"conversation_id": cid,
                "delivery_capability": delivery["delivery_capability"], "output_path": str(calculated_path),
                "expected_revision": summary["revision"], "expected_receipt": summary["receipt_fingerprint"]})
            receipt["calculated"] = {"path": str(calculated_path), "file_sha": hashlib.sha256(calculated_path.read_bytes()).hexdigest()}
        stage = "saved_files_reload"
        verifier = api_factory(Path(data_dir))
        fresh = GuiDocumentClient(verifier)
        loaded = fresh.call("whiteboard_load", str(board_path))["board"]
        if loaded != actual["board"]:
            raise GuiWorkflowError("全新App接口回读白板不一致", reason="gui_workflow_reload_mismatch")
        if delivery is not None:
            reloaded = fresh.call("whiteboard_read_result", loaded, str(calculated_path))
            if reloaded["calculated"] != delivery["calculated"]:
                raise GuiWorkflowError("全新App接口回读计算包不一致", reason="gui_workflow_reload_result_mismatch")
        receipt.update(status="verified" if calculated else "draft_verified", reload_verified=True)
        return receipt
    except Exception as error:  # noqa: BLE001 - bounded CLI failure receipt
        receipt.update(status="failed", **_failure_receipt(error, stage=stage, data_dir=data_dir))
        return receipt
    finally:
        if api is not None and stream_id is not None:
            try:
                api.dsh_client_stream_cancel({"stream_id": stream_id})
            except Exception as error:  # noqa: BLE001 - transport may already be lost
                receipt["stream_cleanup_issue"] = getattr(error, "code", "dsh_stream_cleanup_failed")
        for instance in (verifier, api):
            if instance is not None:
                try:
                    instance._close_dsh_shell()
                except Exception as error:  # noqa: BLE001 - retain the primary failure
                    receipt["shutdown_issue"] = getattr(error, "code", "dsh_shutdown_failed")
        pool.shutdown(wait=False, cancel_futures=True)
        from genshin_app.output_policy import publish_ordinary_files
        receipt_path = target / "gui-workflow-receipt.json"
        receipt["receipt_publication"] = {"status": "published", "path": str(receipt_path)}
        try:
            publish_ordinary_files([(receipt_path, json.dumps(receipt,
                ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8"))],
                Path(data_dir) / "genshin.db", input_paths=[input_path] if input_path else [])
        except Exception as error:  # noqa: BLE001 - never erase already-settled document publications
            receipt["receipt_publication"] = {"status": "failed", "path": str(receipt_path),
                **_failure_receipt(error, stage="receipt_publication", data_dir=data_dir)}
