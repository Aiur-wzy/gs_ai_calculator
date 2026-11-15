"""Exact, unaccepted diagnostics for a generated result's failed validation.

This is not a computation override, result receipt or new execution owner.
Only the producer's current private attempt cache permits a derived-only
failure settlement. Author edits and uncertain file writes keep their normal
unknown-outcome handling.
"""
from __future__ import annotations

import copy
import re
import uuid
from collections.abc import Mapping

from genshin_app.app_system_fault import system_failure, validate_system_failure
from genshin_app.equipment.files import fingerprint, json_bytes

from .native_text_safety import sanitize_public_text

CONTRACT = "calculation-output-validation-failure-v1"
PHASE = "generated_result_validation"
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class CalculationOutputValidationFailure(RuntimeError):
    def __init__(self, response):
        super().__init__(response["message"])
        self.response = response


def retain_output_validation_failure(session, error, *, candidate_fingerprint):
    """Retain code-only fault evidence, never the rejected generated result."""
    from genshin_app.app_fault_store import fault_scope

    from .operation_attempts import current_identity
    # The caller already owns the App profile diagnostic directory.  The DB
    # can be an unrelated read-only snapshot; never redirect an active scope
    # to its parent merely because this calculation reads that file.
    with fault_scope(session_id=session.session_id,
                     candidate_revision=session.revision):
        fault = system_failure(error, component="python_engine_runtime",
                               operation="gs_calculate.validate_generated_result")
    details = getattr(error, "details", None)
    rows = details if isinstance(details, list) else [details] if isinstance(details, dict) else []
    issues = []
    for row in rows[:16]:
        if not isinstance(row, dict):
            continue
        item = {key: sanitize_public_text(row[key][:512]) for key in ("path", "message", "code")
                if isinstance(row.get(key), str)}
        if item and len(json_bytes([*issues, item])) <= 8000:
            issues.append(item)
    reason = getattr(error, "reason", None)
    original = {"reason": reason[:128] if isinstance(reason, str) else type(error).__name__,
                "issues": issues}
    artifact = {"kind": "genshin-calculation-diagnostic", "schema_version": 1,
                "sealed": False, "accepted_result": False,
                "source": {"input_fingerprint": candidate_fingerprint},
                "diagnostic": {"contract": CONTRACT, "phase": PHASE,
                               "original_error": original, "system_fault": copy.deepcopy(fault["system_fault"])}}
    attempt_id = uuid.uuid4().hex
    session._diagnostic_cache = {"revision": session.revision, "attempt_id": attempt_id,
                                "candidate_fingerprint": candidate_fingerprint,
                                "input_identity": current_identity(session), "value": artifact}
    fault["details"] = {"resource": "diagnostic_error", "revision": session.revision,
                        "attempt_id": attempt_id, "candidate_fingerprint": candidate_fingerprint,
                        "diagnostic_fingerprint": fingerprint(artifact), "diagnostic": artifact,
                        "phase": PHASE}
    return CalculationOutputValidationFailure(fault)


def permits_output_validation_failure(result, *, diagnostic_cache=None,
                                     require_cached_verification=False, candidate_state=None):
    """Check a closed diagnostic and, at the Host, exact live producer evidence."""
    try:
        validate_system_failure(result)
    except (TypeError, ValueError):
        return False
    if (result["system_fault"]["component"] != "python_engine_runtime"
            or result["system_fault"]["operation"] != "gs_calculate.validate_generated_result"):
        return False
    details = result.get("details")
    if not isinstance(details, Mapping) or set(details) != {
        "resource", "revision", "attempt_id", "candidate_fingerprint",
        "diagnostic_fingerprint", "diagnostic", "phase",
    }:
        return False
    artifact = details["diagnostic"]
    if (details["resource"] != "diagnostic_error" or details["phase"] != PHASE
            or type(details["revision"]) is not int or details["revision"] < 0
            or not isinstance(details["attempt_id"], str)
            or re.fullmatch(r"[0-9a-f]{32}", details["attempt_id"]) is None
            or not isinstance(details["candidate_fingerprint"], str)
            or not _HASH.fullmatch(details["candidate_fingerprint"])
            or not isinstance(artifact, dict) or set(artifact) != {
                "kind", "schema_version", "sealed", "accepted_result", "source", "diagnostic"}
            or artifact["kind"] != "genshin-calculation-diagnostic"
            or type(artifact["schema_version"]) is not int or artifact["schema_version"] != 1
            or artifact["sealed"] is not False or artifact["accepted_result"] is not False
            or artifact["source"] != {"input_fingerprint": details["candidate_fingerprint"]}):
        return False
    diagnostic = artifact["diagnostic"]
    if (not isinstance(diagnostic, dict) or set(diagnostic) != {
            "contract", "phase", "original_error", "system_fault"}
            or diagnostic["contract"] != CONTRACT or diagnostic["phase"] != PHASE
            or diagnostic["system_fault"] != result["system_fault"]):
        return False
    original = diagnostic["original_error"]
    if (not isinstance(original, dict) or set(original) != {"reason", "issues"}
            or not isinstance(original["reason"], str) or not 1 <= len(original["reason"]) <= 128
            or not isinstance(original["issues"], list) or len(original["issues"]) > 16
            or any(not isinstance(row, dict) or not row or set(row) - {"path", "message", "code"}
                   or any(not isinstance(value, str) or len(value) > 512 for value in row.values())
                   for row in original["issues"])):
        return False
    try:
        if len(json_bytes(artifact)) > 16000 or fingerprint(artifact) != details["diagnostic_fingerprint"]:
            return False
    except (TypeError, ValueError, RecursionError):
        return False
    if require_cached_verification:
        return (isinstance(diagnostic_cache, dict) and isinstance(candidate_state, Mapping)
                and diagnostic_cache.get("revision") == candidate_state.get("revision") == details["revision"]
                and diagnostic_cache.get("candidate_fingerprint") == candidate_state.get("fingerprint")
                    == details["candidate_fingerprint"]
                and diagnostic_cache.get("attempt_id") == details["attempt_id"]
                and diagnostic_cache.get("value") == artifact)
    return True


def output_validation_failure_view(value):
    """Read saved error facts without treating a file as producer authority."""
    from genshin_app.equipment.schema import PlanError

    artifact = value
    prefix = ""
    if value.get("failure_kind") == "system" and isinstance(value.get("details"), dict):
        artifact = value["details"].get("diagnostic")
        prefix = "/details/diagnostic"
    if not isinstance(artifact, dict) or not isinstance(artifact.get("diagnostic"), dict):
        return None
    diagnostic = artifact["diagnostic"]
    if diagnostic.get("contract") != CONTRACT:
        return None
    original = diagnostic.get("original_error")
    if (artifact.get("kind") != "genshin-calculation-diagnostic"
            or artifact.get("sealed") is not False or artifact.get("accepted_result") is not False
            or diagnostic.get("phase") != PHASE or not isinstance(original, dict)
            or not isinstance(original.get("issues"), list)
            or any(key in obj for obj in (value, artifact, diagnostic)
                   for key in ("result", "result_receipt", "calculated", "summary"))):
        raise PlanError("产物校验失败诊断不能混入计算结果或接纳回执", "invalid_diagnostic_input")
    issues = [{**row, "code": "generated_result_validation_failed", "severity": "error"}
              for row in original["issues"] if isinstance(row, dict)]
    if not issues:
        issues = [{"code": "generated_result_validation_failed", "severity": "error",
                   "path": "calculation_output", "message": "应用生成产物未通过内部校验；请报告故障编号。"}]
    return ({"issues": issues, "status": "invalid", "mechanics_trace": []},
            prefix + "/diagnostic/original_error/issues", "unverified_saved_output_validation_failure")
