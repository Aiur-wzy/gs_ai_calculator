"""Read-only operation evidence, separate from candidate/acceptance authority.

Preflight can reject without changing the business checkpoint. Its report is
still worth retaining. This journal never grants execution, approval or a valid
calculation; consumers must continue to use the existing candidate receipts.
"""
from __future__ import annotations

import copy
import re
import uuid
from datetime import UTC, datetime

from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError
from genshin_app.failure_analysis import failure_outline

from .sessions import MAX_CALCULATED, MAX_CHECKPOINT, read_owned, write_owned

CONTRACT = "genshin-operation-attempt-v1"
INDEX = "operation-attempt-latest.json"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ID = re.compile(r"[0-9a-f]{32}\Z")
_RESOURCE = re.compile(r"attempt-([0-9a-f]{32})-([0-9a-f]{64})\Z")


def current_identity(session):
    stage = getattr(session, "stage_state", {})
    return {"candidate_revision": session.revision,
        "input_fingerprint": fingerprint(input_view(session.candidate)),
        "database_fingerprint": fingerprint(session.database_snapshot),
        "policy_fingerprint": fingerprint({"workflow": session.workflow,
            "energy_intent": stage.get("energy_intent"),
            "stage_revision": stage.get("stage_revision")})}


def _input_section_fingerprints(session):
    # A bounded structural diff, not another copy of the input/trace. Lists
    # remain one collection: never imply which individual value changed.
    sections = {}
    def visit(value, path, depth):
        if isinstance(value, dict) and depth < 2 and value:
            for key, child in value.items():
                visit(child, path + "/" + str(key).replace("~", "~0").replace("/", "~1"), depth + 1)
        else:
            sections[path] = fingerprint(value)
    visit(input_view(session.candidate), "", 0)
    return sections


def _persist(session, value):
    try:
        digest = fingerprint(value)
        name = f"operation-attempt-{value['attempt_id']}-{digest}.json"
        write_owned(session.root, name, value, MAX_CALCULATED)
        summary = {key: copy.deepcopy(item) for key, item in value.items()
                   if key not in {"evidence", "diagnostic_payload"}}
        write_owned(session.root, INDEX, {"contract": CONTRACT, "file": name,
            "fingerprint": digest, "session_id": session.session_id,
            "summary": summary, "summary_fingerprint": fingerprint(summary)}, MAX_CHECKPOINT)
        session._attempt_storage_failure = None
        return True
    except (OSError, ValueError, TypeError) as error:
        # A diagnostic failure cannot change a successfully committed operation
        # into an unknown write, nor turn its original rejection into success.
        session._attempt_storage_failure = {"reason": "operation_diagnostic_not_saved",
            "cause": getattr(error, "reason", type(error).__name__), "attempt_id": value["attempt_id"]}
        return False


def _read_index(session):
    try:
        index = read_owned(session.root, INDEX, MAX_CHECKPOINT)
    except FileNotFoundError:
        return None
    if (not isinstance(index, dict) or index.get("contract") != CONTRACT
            or index.get("session_id") != session.session_id
            or not isinstance(index.get("fingerprint"), str) or not _HASH.fullmatch(index["fingerprint"])):
        raise PlanError("计算尝试记录的引用不完整", "agent_attempt_reference_invalid")
    header = index.get("summary")
    if (not isinstance(header, dict) or header.get("contract") != CONTRACT
            or header.get("session_id") != session.session_id
            or not isinstance(header.get("attempt_id"), str) or not _ID.fullmatch(header["attempt_id"])
            or fingerprint(header) != index.get("summary_fingerprint")
            or index.get("file") != f"operation-attempt-{header['attempt_id']}-{index['fingerprint']}.json"):
        raise PlanError("计算尝试记录与保存内容不一致", "agent_attempt_reference_invalid")
    return index


def _with_currency(session, value, index):
    with session._read_db(check=False):
        database_current = fingerprint(session._db_snapshot()) == fingerprint(session.database_snapshot)
    return {**value, "matches_current_input": value.get("identity") == current_identity(session) and database_current,
        "details_ref": {"resource": f"attempt-{value['attempt_id']}-{index['fingerprint']}", "pointer": "/evidence",
                        "attempt_id": value["attempt_id"], "fingerprint": index["fingerprint"]}}


def read_latest_attempt(session):
    index = _read_index(session)
    if index is None:
        return None
    value = read_owned(session.root, index["file"], MAX_CALCULATED)
    if (not isinstance(value, dict) or fingerprint(value) != index["fingerprint"]
            or {key: item for key, item in value.items() if key not in {"evidence", "diagnostic_payload"}}
            != index["summary"]):
        raise PlanError("计算尝试记录与保存内容不一致", "agent_attempt_reference_invalid")
    return _with_currency(session, value, index)


def read_attempt_resource(session, resource):
    """Read an immutable diagnostic from this Session only, even after retry."""
    match = _RESOURCE.fullmatch(resource)
    if match is None:
        raise PlanError("计算尝试引用不合法", "agent_attempt_reference_invalid")
    attempt_id, digest = match.groups()
    try:
        value = read_owned(session.root, f"operation-attempt-{attempt_id}-{digest}.json", MAX_CALCULATED)
    except FileNotFoundError as error:
        raise PlanError("此会话没有该计算尝试记录", "agent_attempt_reference_missing") from error
    if (not isinstance(value, dict) or fingerprint(value) != digest
            or value.get("contract") != CONTRACT or value.get("session_id") != session.session_id
            or value.get("attempt_id") != attempt_id):
        raise PlanError("计算尝试引用与原始记录不一致", "agent_attempt_reference_invalid")
    return _with_currency(session, value, {"fingerprint": digest})


def attempt_projection(session):
    try:
        index = _read_index(session)
        value = _with_currency(session, index["summary"], index) if index else None
    except (OSError, ValueError) as error:
        return {"state": "diagnostic_unavailable", "reason": getattr(error, "reason", type(error).__name__),
                "does_not_change_acceptance": True}
    if value is None:
        warning = getattr(session, "_attempt_storage_failure", None)
        return ({"state": "diagnostic_unavailable", "diagnostic_storage_warning": copy.deepcopy(warning),
                 "does_not_change_acceptance": True} if warning else None)
    result = {key: copy.deepcopy(value[key]) for key in (
        "attempt_id", "operation", "state", "phase", "engine_execution", "identity",
        "recorded_at", "finished_at", "reason", "failure_summary", "repetition",
        "matches_current_input", "details_ref", "user_action") if key in value}
    result["does_not_change_acceptance"] = True
    if getattr(session, "_attempt_storage_failure", None):
        result["diagnostic_storage_warning"] = copy.deepcopy(session._attempt_storage_failure)
    return result


def diagnostic_call(session, function, *args, **kwargs):
    """Optional observation cannot replace a business result with its own bug.

    This boundary is deliberately local to diagnostics; it never wraps domain
    execution or reports a successful operation on its behalf.
    """
    try:
        return function(session, *args, **kwargs)
    except Exception as error:  # noqa: BLE001 - diagnostic-only non-authority boundary
        session._attempt_storage_failure = {
            "reason": "operation_diagnostic_not_saved",
            "cause": getattr(error, "reason", type(error).__name__),
        }
        return None


def start_attempt(session, operation, arguments):
    try:
        index = _read_index(session)
        previous = index["summary"] if index else None
    except (OSError, ValueError):
        previous = None
    value = {"contract": CONTRACT, "session_id": session.session_id,
        "attempt_id": uuid.uuid4().hex, "operation": operation,
        "identity": current_identity(session), "request_fingerprint": fingerprint(arguments),
        "input_sections": _input_section_fingerprints(session),
        "recorded_at": datetime.now(UTC).isoformat(), "state": "started",
        "phase": "request", "engine_execution": "not_observed", "user_action": None,
        "evidence": None}
    _persist(session, value)
    return value, previous


def finish_attempt(session, attempt, *, error=None):
    value, previous = attempt
    value = copy.deepcopy(value)
    reason = getattr(error, "reason", None) or getattr(error, "code", None)
    if error is None:
        value.update(state="succeeded", phase="completed", engine_execution=(
            "completed" if value["operation"] == "gs_calculate" else "not_run"),
            evidence={"report_state": (session.report or {}).get("state"),
                "report_ref": {"resource": "report", "pointer": ""},
                "missing_inputs": copy.deepcopy((session.report or {}).get("missing_inputs", [])),
                "result_receipt_fingerprint": fingerprint(session.calculated["result_receipt"])
                if session.calculated else None})
    else:
        # Expected input failures are distinguishable from an exception after
        # execution. Unknown program failures are never described as no-op.
        details = getattr(error, "details", None)
        response = getattr(error, "response", None)
        if isinstance(response, dict):
            reason, details = response.get("reason"), response.get("details")
        phase = getattr(session, "_operation_phase", "operation")
        preflight = phase == "input_preflight"
        executed = reason in {"mechanics_pending", "agent_calculation_rejected", "calculation_verification_incomplete"}
        value.update(state="failed", reason=reason or type(error).__name__,
            phase=phase,
            engine_execution="not_run" if preflight or phase == "composition" else "rejected" if executed else "unknown",
            evidence={"code": 1, "reason": reason or type(error).__name__,
                "message": str(error)[:4096] if isinstance(error, PlanError) else "操作未完成，查看原错误记录。",
                "details": copy.deepcopy(details)})
        cached = getattr(session, "_diagnostic_cache", None)
        if (not preflight and (executed or isinstance(response, dict)) and isinstance(cached, dict)
                and cached.get("revision") == session.revision
                and cached.get("candidate_fingerprint") == fingerprint(session.candidate)):
            value["diagnostic_payload"] = copy.deepcopy(cached["value"])
        outline = failure_outline(details)
        if outline:
            value["failure_summary"] = outline
        if previous and previous.get("operation") == value["operation"] and previous.get("state") == "failed":
            old_problem = (previous.get("failure_summary") or {}).get("primary_reported_issue") or {}
            new_problem = (outline or {}).get("primary_reported_issue") or {}
            value["repetition"] = {"previous_attempt_id": previous["attempt_id"],
                "same_input": previous.get("identity") == value["identity"],
                "same_request": previous.get("request_fingerprint") == value["request_fingerprint"],
                "same_primary_problem": previous.get("reason") == value["reason"] and old_problem == new_problem,
                "previous_primary_problem": old_problem, "current_primary_problem": new_problem,
                "changed_input_sections": sorted(path for path in
                    set(previous.get("input_sections", {})) | set(value["input_sections"])
                    if previous.get("input_sections", {}).get(path) != value["input_sections"].get(path)),
                "change_resolution": "JSON Pointer sections; array item changes are not inferred",
                "retry_limit": None}
    value["finished_at"] = datetime.now(UTC).isoformat()
    _persist(session, value)
    return value
