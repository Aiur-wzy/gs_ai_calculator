"""Durable, bounded code-only faults shared by CLI and native domain calls.

This is diagnostic storage, never a second operation or authorization ledger.
It accepts only producer-selected identities and the safe stack projection.
"""
from __future__ import annotations

import contextvars
import copy
import re
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

CONTRACT = "genshin-app-system-diagnostic-v1"
MAX_BYTES = 64 * 1024
_CONTEXT = contextvars.ContextVar("app_system_fault_context", default=None)
_IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
_IDENTITY_FIELDS = frozenset({"session_id", "conversation_id", "document_id", "turn_id",
    "step_id", "call_id", "operation_id", "request_id", "candidate_revision", "stage_revision"})


def _identities(values):
    return {key: value for key, value in values.items() if key in _IDENTITY_FIELDS
        and (type(value) is int and 0 <= value <= 2**53 - 1
             or isinstance(value, str) and _IDENTITY.fullmatch(value))}


@contextmanager
def fault_scope(data_dir=None, **identities):
    previous = _CONTEXT.get() or {}
    scope = {"data_dir": Path(data_dir) if data_dir is not None else previous.get("data_dir"),
             "identities": {**previous.get("identities", {}), **_identities(identities)}}
    token = _CONTEXT.set(scope)
    try:
        yield
    finally:
        _CONTEXT.reset(token)


@contextmanager
def fallback_fault_scope(data_dir=None, **identities):
    """Add local identities without replacing an owner's diagnostic profile.

    A database path is only a fallback for standalone callers.  Domain tools
    can read an external, read-only database while their owning App profile
    supplies the durable diagnostic directory in an outer scope.
    """
    previous = _CONTEXT.get() or {}
    with fault_scope(None if previous.get("data_dir") is not None else data_dir, **identities):
        yield


def current_fault_context():
    scope = _CONTEXT.get() or {}
    directory = scope.get("data_dir")
    if directory is None:
        from genshin_app.paths import default_data_dir
        directory = default_data_dir()
    return Path(directory), dict(scope.get("identities", {}))


def session_fault_context(function):
    """Add candidate facts for direct calls; an outer Host adds Turn/call facts."""
    @wraps(function)
    def invoke(session, name, arguments):
        database = getattr(session, "db_path", None)
        directory = Path(database).parent if database is not None else None
        with fallback_fault_scope(directory, session_id=getattr(session, "session_id", None),
                candidate_revision=getattr(session, "revision", None),
                stage_revision=getattr(session, "stage_state", {}).get("stage_revision")):
            return function(session, name, arguments)
    return invoke


def diagnostic_root(data_dir):
    return Path(data_dir).absolute() / "logs" / "app-system-faults"


def _diagnostic_id(value):
    if not isinstance(value, str):
        raise TypeError("diagnostic_id必须为规范UUID")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("diagnostic_id必须为规范UUID") from error
    if str(parsed) != value:
        raise ValueError("diagnostic_id必须为规范UUID")
    return value


def persist_system_diagnostic(*, component, operation, cause, diagnostic):
    from genshin_app.agent_chat.sessions import write_owned

    directory, identities = current_fault_context()
    identifier = str(uuid.uuid4())
    value = {"contract": CONTRACT, "diagnostic_id": identifier,
        "recorded_at": datetime.now(UTC).isoformat(), "component": component,
        "operation": operation, "cause_code": cause, "diagnostic": copy.deepcopy(diagnostic),
        "context": identities}
    # write_owned uses the existing fsync + atomic publication primitive. No
    # public ID is issued unless that write has really completed.
    write_owned(diagnostic_root(directory), identifier + ".json", value, MAX_BYTES)
    return identifier


def read_system_diagnostic(data_dir, diagnostic_id):
    from genshin_app.agent_chat.sessions import read_owned
    from genshin_app.equipment.schema import PlanError

    if isinstance(diagnostic_id, str) and re.fullmatch(r"[0-9a-f]{64}", diagnostic_id):
        return _read_host_diagnostic(data_dir, diagnostic_id)
    try:
        identifier = _diagnostic_id(diagnostic_id)
    except (ValueError, TypeError) as error:
        raise PlanError(str(error), "invalid_diagnostic_id") from None
    try:
        value = read_owned(diagnostic_root(data_dir), identifier + ".json", MAX_BYTES)
    except FileNotFoundError:
        raise PlanError("未找到此诊断编号的持久记录", "system_diagnostic_not_found") from None
    from genshin_app.app_system_fault import _OPERATION, CAUSES, COMPONENTS
    if (not isinstance(value, dict) or set(value) != {"contract", "diagnostic_id", "recorded_at",
            "component", "operation", "cause_code", "diagnostic", "context"}
            or value.get("contract") != CONTRACT or value.get("diagnostic_id") != identifier
            or value.get("component") not in COMPONENTS or value.get("cause_code") not in CAUSES
            or not isinstance(value.get("operation"), str) or not _OPERATION.fullmatch(value["operation"])
            or not isinstance(value.get("recorded_at"), str)
            or not isinstance(value.get("context"), dict) or _identities(value["context"]) != value["context"]):
        raise PlanError("诊断记录身份或结构损坏", "system_diagnostic_invalid")
    diagnostic = value["diagnostic"]
    # The stored projection is already code-only. Recheck it on read, so a
    # locally edited diagnostic cannot publish an arbitrary extra payload.
    from genshin_app.app_system_fault import validate_safe_diagnostic
    if not validate_safe_diagnostic(diagnostic):
        raise PlanError("诊断代码栈结构损坏", "system_diagnostic_invalid")
    return copy.deepcopy(value)


def _read_host_diagnostic(data_dir, identifier):
    from genshin_app.agent_chat.sessions import read_owned
    from genshin_app.app_system_fault import validate_safe_diagnostic
    from genshin_app.equipment.schema import PlanError
    try:
        value = read_owned(Path(data_dir) / "logs" / "native-dsh-host", identifier + ".json", MAX_BYTES)
    except FileNotFoundError:
        raise PlanError("未找到此诊断编号的持久记录", "system_diagnostic_not_found") from None
    required = {"contract", "diagnostic_id", "kind", "code", "error_type", "app_call_admission", "frames"}
    if (not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"context"}
            or value.get("contract") != "genshin-native-dsh-host-failure-v1"
            or value.get("diagnostic_id") != identifier
            or not isinstance(value.get("kind"), str) or not _IDENTITY.fullmatch(value["kind"])
            or not isinstance(value.get("code"), str) or not _IDENTITY.fullmatch(value["code"])
            or value.get("app_call_admission") not in {"not_recorded", "inflight", "settled_success", "settled_failure",
                                                       "unknown_after_interruption", "unknown"}
            or "context" in value and (not isinstance(value["context"], dict) or _identities(value["context"]) != value["context"])
            or not validate_safe_diagnostic({"exception_type": value.get("error_type"), "frames": value.get("frames")})):
        raise PlanError("诊断记录身份或结构损坏", "system_diagnostic_invalid")
    return copy.deepcopy(value)
