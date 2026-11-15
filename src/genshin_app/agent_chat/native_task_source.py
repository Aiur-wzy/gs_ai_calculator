"""Capture a DSH-owned user-message reference as optional domain evidence.

Only the trusted runtime bind port calls this adapter. This is neither a chat
history owner nor a user-approval mechanism: DSH retains the message/event, and
the finding is a source quote that an Agent can compare with its interpretation.
"""
from __future__ import annotations

import re

from genshin_engine.execution_control import ExecutionCancelled

_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")
_FIELDS = {"session_id", "event_seq", "message_id", "text"}


def capture_task_source(session, source, *, session_id):
    """Return an advisory receipt; failed notes never remove working tools."""
    if source is None:
        return None
    if (not isinstance(source, dict) or set(source) != _FIELDS
            or source.get("session_id") != session_id
            or type(source.get("event_seq")) is not int
            or not 0 <= source["event_seq"] <= 2**53 - 1
            or not isinstance(source.get("message_id"), str)
            or _IDENTITY.fullmatch(source["message_id"]) is None
            or not isinstance(source.get("text"), str) or not source["text"].strip()):
        return {"status": "unavailable", "code": "task_source_invalid",
                "message": "原始要求的辅助引用未能核对；仍可正常查询、编辑和计算。"}
    try:
        from genshin_app.task_evidence import record_task_source

        reference = f"dsh:{session_id}:{source['event_seq']}:{source['message_id']}"
        value = record_task_source(session, source["text"], reference,
                                   origin="dsh_user_message")
        return {"status": "captured", "reference": reference,
                "authority": "source_quote_only", "acceptance_effect": "none",
                "record_id": value.get("id") if isinstance(value, dict) else None}
    except ExecutionCancelled:
        raise
    except Exception as error:  # noqa: BLE001 - optional evidence cannot revoke working tools
        # Recording a quotation has no effect on the candidate or an already
        # settled operation. Do not replay either operation on this failure.
        return {"status": "unavailable", "code": "task_source_recording_failed",
                "reason": type(error).__name__,
                "message": "原始要求暂未加入工作资料；可以继续工作，不能因此声称原题已核对完整。"}
