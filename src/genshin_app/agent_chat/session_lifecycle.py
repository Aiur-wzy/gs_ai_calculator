"""Durable, bounded lifecycle records for resumable App Agent sessions.

This module implements the small subset of DeepSeek Harness session semantics
that the App can prove today: cold inspection, explicit activation, immutable
request epochs and a checkpoint around tool side effects.  ``chat.json`` is
still the owning snapshot; this is not the Harness append-only event store.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime

from genshin_app.equipment.schema import PlanError

CONTRACT = "genshin-agent-session-lifecycle-v1"
MAX_REQUEST_EPOCHS = 2048
MAX_TOOL_SETTLEMENTS = 512
MAX_RECOVERY_NOTICES = 64
_EPOCH_REASONS = {"initial", "resume", "change", "series"}


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _digest(value) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _invalid(message: str = "会话生命周期记录不合法") -> None:
    raise PlanError(message, "agent_checkpoint_invalid")


def _bounded_rows(value, maximum: int, name: str) -> list[dict]:
    if not isinstance(value, list) or len(value) > maximum:
        _invalid(f"会话{name}记录不合法")
    if any(not isinstance(row, dict) for row in value):
        _invalid(f"会话{name}记录不合法")
    return value


def normalize(session: dict) -> bool:
    """Validate and add lifecycle defaults, returning whether it changed."""
    changed = False
    session_id = session.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        _invalid("会话身份不合法")
    defaults = {
        "session_lifecycle_contract": CONTRACT,
        "conversation_id": session_id,
        "activation_generation": 0,
        "resume_pending": False,
        "request_epochs": [],
        "inflight_tool": None,
        "tool_settlements": [],
        "recovery_notices": [],
    }
    for key, value in defaults.items():
        if key not in session:
            session[key] = copy.deepcopy(value)
            changed = True
    if session.get("session_lifecycle_contract") != CONTRACT:
        _invalid("会话生命周期版本不受支持")
    if session.get("conversation_id") != session_id:
        _invalid("会话与对话身份不一致")
    generation = session.get("activation_generation")
    if type(generation) is not int or generation < 0:
        _invalid("会话激活代数不合法")
    if not isinstance(session.get("resume_pending"), bool):
        _invalid("会话恢复状态不合法")
    epochs = _bounded_rows(session.get("request_epochs"), MAX_REQUEST_EPOCHS, "请求时期")
    previous = 0
    for row in epochs:
        if (row.get("contract") != CONTRACT
                or type(row.get("epoch")) is not int
                or row["epoch"] != previous + 1
                or row.get("reason") not in _EPOCH_REASONS
                or not isinstance(row.get("epoch_id"), str)
                or not isinstance(row.get("instructions_hash"), str)
                or not isinstance(row.get("tool_schema_hash"), str)
                or not isinstance(row.get("tool_names"), list)
                or any(not isinstance(name, str) for name in row["tool_names"])):
            _invalid("会话请求时期记录不合法")
        previous = row["epoch"]
    inflight = session.get("inflight_tool")
    if inflight is not None and (not isinstance(inflight, dict)
                                 or not isinstance(inflight.get("tool_id"), str)
                                 or not isinstance(inflight.get("name"), str)):
        _invalid("会话工具检查点不合法")
    _bounded_rows(session.get("tool_settlements"), MAX_TOOL_SETTLEMENTS, "工具结算")
    notices = _bounded_rows(
        session.get("recovery_notices"), MAX_RECOVERY_NOTICES, "恢复诊断")
    for row in notices:
        if (not isinstance(row.get("code"), str)
                or not isinstance(row.get("marker"), str)
                or not isinstance(row.get("message"), str)
                or (row.get("acknowledged_at") is not None
                    and not isinstance(row.get("acknowledged_at"), str))
                or (row.get("acknowledged_epoch") is not None
                    and type(row.get("acknowledged_epoch")) is not int)
                or (row.get("acknowledged_run_id") is not None
                    and not isinstance(row.get("acknowledged_run_id"), str))):
            _invalid("会话恢复诊断记录不合法")
    return changed


def mark_resume(session: dict) -> dict:
    normalize(session)
    session["activation_generation"] += 1
    session["resume_pending"] = True
    session["last_resumed_at"] = _stamp()
    return activation_summary(session)


def append_request_epoch(
        session: dict, *, reason: str, candidate_summary: dict,
        tools: list[dict], instructions: str) -> dict:
    """Append an immutable request envelope before provider dispatch."""
    normalize(session)
    if reason not in _EPOCH_REASONS:
        _invalid("会话请求时期原因不合法")
    epochs = session["request_epochs"]
    if len(epochs) >= MAX_REQUEST_EPOCHS:
        raise PlanError("会话请求时期已达到保存上限，请新建会话", "agent_size_limit")
    names = sorted({row.get("name") for row in tools
                    if isinstance(row, dict) and isinstance(row.get("name"), str)})
    schema_projection = [{key: copy.deepcopy(row.get(key)) for key in (
        "type", "name", "description", "inputSchema")}
        for row in tools if isinstance(row, dict) and row.get("name") in names]
    messages = session.get("messages") if isinstance(session.get("messages"), list) else []
    last_message = messages[-1].get("message_id") if messages and isinstance(messages[-1], dict) else None
    row = {
        "contract": CONTRACT,
        "epoch": len(epochs) + 1,
        "epoch_id": hashlib.sha256(
            f"{session['session_id']}:{len(epochs) + 1}".encode()).hexdigest(),
        "reason": reason,
        "created_at": _stamp(),
        "activation_generation": session["activation_generation"],
        "provider_id": session.get("provider_id"),
        "connection_kind": session.get("connection_kind"),
        "model": session.get("model"),
        "effort": session.get("effort"),
        "workflow": session.get("workflow", "calculate"),
        "workflow_stage": candidate_summary.get("workflow_stage"),
        "stage_revision": candidate_summary.get("stage_revision"),
        "candidate_revision": candidate_summary.get("revision"),
        "instructions_hash": _digest(instructions),
        "tool_schema_hash": _digest(schema_projection),
        "tool_names": names,
        "history_boundary": {
            "message_count": len(messages),
            "last_message_id": last_message,
            "visible_through_stage": candidate_summary.get("workflow_stage"),
        },
    }
    # Do not mutate existing epochs: a new envelope always appends.
    epochs.append(row)
    session["resume_pending"] = False
    return copy.deepcopy(row)


def request_reason(session: dict, candidate_summary: dict, tools: list[dict],
                   instructions: str) -> str:
    normalize(session)
    if not session["request_epochs"]:
        return "initial"
    if session.get("resume_pending"):
        return "resume"
    previous = session["request_epochs"][-1]
    names = sorted({row.get("name") for row in tools
                    if isinstance(row, dict) and isinstance(row.get("name"), str)})
    schema_projection = [{key: copy.deepcopy(row.get(key)) for key in (
        "type", "name", "description", "inputSchema")}
        for row in tools if isinstance(row, dict) and row.get("name") in names]
    changed = (
        previous.get("workflow_stage") != candidate_summary.get("workflow_stage")
        or previous.get("stage_revision") != candidate_summary.get("stage_revision")
        or previous.get("candidate_revision") != candidate_summary.get("revision")
        or previous.get("instructions_hash") != _digest(instructions)
        or previous.get("tool_schema_hash") != _digest(schema_projection)
        or previous.get("model") != session.get("model")
        or previous.get("effort") != session.get("effort")
    )
    return "change" if changed else "series"


def begin_tool(session: dict, *, tool_id: str, name: str,
               candidate_summary: dict) -> dict:
    normalize(session)
    if session.get("inflight_tool") is not None:
        raise PlanError("上一工具调用尚未结算", "agent_tool_outcome_unknown")
    row = {
        "tool_id": tool_id,
        "name": name,
        "run_id": session.get("run_id"),
        "started_at": _stamp(),
        "workflow_stage": candidate_summary.get("workflow_stage"),
        "candidate_revision_before": candidate_summary.get("revision"),
    }
    session["inflight_tool"] = row
    return copy.deepcopy(row)


def settle_tool(session: dict, *, tool_id: str, result: dict,
                candidate_summary: dict) -> dict:
    normalize(session)
    inflight = session.get("inflight_tool")
    if not isinstance(inflight, dict) or inflight.get("tool_id") != tool_id:
        raise PlanError("工具结算与检查点不一致", "agent_checkpoint_invalid")
    settlements = session["tool_settlements"]
    if len(settlements) >= MAX_TOOL_SETTLEMENTS:
        # Settlements are diagnostic checkpoints, not the canonical transcript.
        # Keep a bounded tail without changing any retained row.
        del settlements[:len(settlements) - MAX_TOOL_SETTLEMENTS + 1]
    row = {
        **copy.deepcopy(inflight),
        "settled_at": _stamp(),
        "candidate_revision_after": candidate_summary.get("revision"),
        "result_code": result.get("code") if isinstance(result, dict) else 1,
        "result_reason": result.get("reason") if isinstance(result, dict) else "agent_tool_result",
        "result_hash": _digest(result),
    }
    settlements.append(row)
    session["inflight_tool"] = None
    return copy.deepcopy(row)


def repair_interrupted(session: dict, *, turn_interrupted: bool = False) -> dict | None:
    """Close an unowned turn/tool checkpoint; never retry it."""
    normalize(session)
    inflight = session.get("inflight_tool")
    if not isinstance(inflight, dict) and not turn_interrupted:
        return None
    marker = (f"{inflight.get('run_id')}:{inflight.get('tool_id')}"
              if isinstance(inflight, dict)
              else f"{session.get('run_id')}:{session.get('turn_id')}:turn")
    existing = next((row for row in session["recovery_notices"]
                     if row.get("marker") == marker), None)
    if existing is None:
        if len(session["recovery_notices"]) >= MAX_RECOVERY_NOTICES:
            del session["recovery_notices"][0]
        unknown_tool = isinstance(inflight, dict)
        existing = {
            "code": "agent_tool_outcome_unknown" if unknown_tool else "agent_turn_interrupted",
            "marker": marker,
            "run_id": inflight.get("run_id") if unknown_tool else session.get("run_id"),
            "tool_id": inflight.get("tool_id") if unknown_tool else None,
            "tool_name": inflight.get("name") if unknown_tool else None,
            "created_at": _stamp(),
            "acknowledged_at": None,
            "acknowledged_epoch": None,
            "acknowledged_run_id": None,
            "message": (("上次进程在工具调用结算前中断；候选检查点已保留，"
                         "下一轮必须先核验现状，不会自动重试该工具。")
                        if unknown_tool else
                        "上次模型轮次在进程退出前未结算；下一轮从持久检查点恢复，不会自动重发。"),
        }
        session["recovery_notices"].append(existing)
    if isinstance(inflight, dict):
        session["inflight_tool"] = None
    session["resume_pending"] = True
    return copy.deepcopy(existing)


def pending_recovery(session: dict) -> dict | None:
    """Return the newest recovery notice not closed by a successful turn."""
    normalize(session)
    return next((copy.deepcopy(row) for row in reversed(session["recovery_notices"])
                 if row.get("acknowledged_at") is None), None)


def acknowledge_recovery(session: dict) -> dict | None:
    """Close every warning visible to a later successful turn.

    More than one interruption can happen before the next successful turn.
    Closing only the newest notice would make an older, superseded warning
    reappear on the following poll.  One acknowledgement checkpoint therefore
    closes the whole pending recovery chain.
    """
    normalize(session)
    pending = [row for row in session["recovery_notices"]
               if row.get("acknowledged_at") is None]
    if not pending:
        return None
    epoch = session["request_epochs"][-1]["epoch"] if session["request_epochs"] else 0
    acknowledged_at = _stamp()
    for notice in pending:
        notice["acknowledged_at"] = acknowledged_at
        notice["acknowledged_epoch"] = epoch
        notice["acknowledged_run_id"] = session.get("run_id")
    return copy.deepcopy(pending[-1])


def activation_summary(session: dict) -> dict:
    normalize(session)
    last_epoch = session["request_epochs"][-1] if session["request_epochs"] else None
    recovery = pending_recovery(session)
    return {
        "contract": CONTRACT,
        "conversation_id": session["conversation_id"],
        "activation_generation": session["activation_generation"],
        "request_epoch": last_epoch.get("epoch") if last_epoch else 0,
        "last_request_reason": last_epoch.get("reason") if last_epoch else None,
        "resume_pending": session["resume_pending"],
        "recovery": copy.deepcopy(recovery),
    }


def runtime_recovery(session: dict) -> dict | None:
    notice = pending_recovery(session)
    if notice is None:
        return None
    return {key: copy.deepcopy(notice.get(key)) for key in (
        "code", "tool_name", "created_at", "message")}
