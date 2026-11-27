"""Stable business-error classification shared by App and native Agent tools.

The low-level ``reason`` remains the precise machine diagnostic.  ``category``
answers a different question: which project boundary must repair the failure.
Release identity/integrity failures intentionally sit outside the four task
categories because they invalidate the App/database execution contract itself.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

BUSINESS_ERROR_CATEGORIES = frozenset({
    "request_error",
    "core_data_missing",
    "knowledge_missing",
    "engine_capability_missing",
})

# Resource exhaustion is a deterministic execution-capacity boundary, not a
# malformed invocation. Explicit invalid limit values still use schema/request
# diagnostics and therefore never match this closed set.
_CAPACITY_CODES = frozenset({
    "attribute_declaration_limit", "attribute_work_limit",
    "attribute_registration_limit", "attribute_receipt_limit",
    "attribute_output_limit", "attribute_execution_limit",
    "attribute_capture_limit", "attribute_evidence_limit",
    "attribute_dependency_depth",
    "calculation_capacity_exceeded", "response_budget_exceeded",
    "calculation_verification_incomplete",
})


def _capacity_issue(details):
    """Scan complete typed metadata before display compaction, iteratively.

    App inputs already have JSON byte/work bounds. A second display-sized
    slice here would misclassify a genuine later issue. Identity tracking
    also keeps direct Python callers with aliased/cyclic containers finite.
    """
    pending, seen = [details], set()
    while pending:
        current = pending.pop()
        if not isinstance(current, (Mapping, list)) or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, Mapping):
            if current.get("code") in _CAPACITY_CODES or current.get("reason") in _CAPACITY_CODES:
                return current
            pending.extend(current.get(key) for key in ("missing_inputs", "details", "issues"))
        else:
            pending.extend(reversed(current))
    return None


def tool_failure_guidance(reason, *, operation=None, details=None):
    """Actionable projection, independent from admission/settlement authority."""
    from genshin_app.failure_analysis import failure_outline
    from genshin_app.recovery_descriptors import project_recovery

    first = details if isinstance(details, Mapping) else (
        details[0] if isinstance(details, list) and details and isinstance(details[0], Mapping) else {})
    outline = failure_outline(details)
    primary_issue = (outline or {}).get("primary_reported_issue") or {}
    contract_ref = first.get("contract_ref")
    if not isinstance(contract_ref, Mapping) and isinstance(operation, str):
        if operation.startswith("cli."):
            contract_ref = {"resource": "cli_capabilities", "command": operation[4:].replace(".", " ")}
        else:
            contract_ref = {"resource": "tool_contracts", "pointer": "/tools/" + operation}
    category = classify_domain_failure(reason, operation=operation, details=details)
    if reason in CANCELLATION_REASONS:
        return {"contract_ref": dict(contract_ref or {}),
            "message": "请求已取消；需要继续时可重新提出要求。若先前已有修改，请先核对当前状态。",
            "recovery": {"action": "inspect_state_then_resume", "retry_same_request": False}}
    if reason in DATABASE_BUSY_REASONS:
        return {"contract_ref": dict(contract_ref or {}),
                "recovery": {"action": "wait_for_database_access", "retry_same_request": True}}
    if reason in UNKNOWN_OUTCOME_REASONS:
        return {"contract_ref": dict(contract_ref or {}),
                "recovery": {"action": "check_original_operation", "retry_same_request": False}}
    recovery = first.get("recovery")
    if not isinstance(recovery, Mapping):
        recovery = primary_issue.get("recovery")
    if not isinstance(recovery, Mapping):
        conversion = next((row for row in (details if isinstance(details, list) else [details])
            if isinstance(row, Mapping) and row.get('code') == 'unsupported_dependency'
            and row.get('dependency_kind') == 'unsupported_conversion_closure'
            and row.get('known_cycle') is False), None)
        capacity = _capacity_issue(details)
        if capacity is None and reason in _CAPACITY_CODES:
            capacity = {"code": reason}
        if conversion is not None:
            recovery = {'action': 'preview_attribute_recovery', 'retry_same_request': False,
                        'tool': 'gs_preview_attribute_recovery', 'cli': 'equipment attribute-draft',
                        'authority': 'input_draft_only'}
        elif capacity is not None and category["category"] != "request_error":
            # Only a producer-declared supported configuration is actionable.
            # Legacy errors must not invent an input path that cannot be used.
            context = capacity.get("details", {})
            adjustable = capacity.get("adjustment")
            if not isinstance(adjustable, Mapping):
                adjustable = context.get("adjustment") if isinstance(context, Mapping) else None
            recovery = ({"action": "adjust_execution_budget", "retry_same_request": False,
                         "adjustment": dict(adjustable)} if isinstance(adjustable, Mapping)
                        else {"action": "report_capacity_boundary", "retry_same_request": False})
        elif category["category"] == RELEASE_ERROR_CATEGORY:
            recovery = {"action": "check_app_database_pair", "retry_same_request": False}
        elif reason in {"cli_connection_unavailable", "cli_connection_rejected", "cli_session_mismatch"} or "stale" in (reason or "") or "revision" in (reason or ""):
            recovery = {"action": "refresh_binding", "retry_same_request": False}
        elif "special_reaction_ruleset" in (reason, first.get("code"), primary_issue.get("code")):
            recovery = {"action": "complete_elemental_dependency", "retry_same_request": False,
                        "resource": "mechanics_capabilities", "pointer": "/elemental"}
        elif category["category"] == "request_error":
            recovery = {"action": "inspect_error_details" if category.get("classification_confidence") == "unknown"
                        else "correct_arguments", "retry_same_request": False}
        else:
            recovery = {"action": "fill_input", "retry_same_request": False}
    value = {"contract_ref": dict(contract_ref or {}), "recovery": project_recovery(recovery, operation)}
    for name in ("path", "field", "repair_hint"):
        if isinstance(first.get(name), str):
            text = first[name]
            if name == "path" and text.startswith("request."):
                text = "/" + re.sub(r"\[(\d+)\]", r".\1", text[8:]).replace(".", "/")
            value[name] = text[:2000]
    if "repair_hint" not in value and isinstance(first.get("message"), str):
        value["repair_hint"] = first["message"][:2000]
    if "repair_hint" not in value and isinstance(primary_issue.get("repair_hint"), str):
        value["repair_hint"] = primary_issue["repair_hint"][:2000]
    if outline is not None:
        if isinstance(primary_issue.get("recovery"), Mapping):
            primary_issue["recovery"] = project_recovery(primary_issue["recovery"], operation)
        value["failure_summary"] = outline
    return value
RELEASE_ERROR_CATEGORY = "release_identity_failure"
CANCELLATION_REASONS = frozenset({
    "dsh_mux_request_cancelled", "dsh_shell_request_cancelled", "cli_request_cancelled",
})
DATABASE_BUSY_REASONS = frozenset({"database_migration_busy", "database_access_busy"})
UNKNOWN_OUTCOME_REASONS = frozenset({
    "cli_operation_unknown", "dsh_domain_call_not_settled",
    "app_action_execution_outcome_unknown", "app_action_draft_publication_outcome_unknown",
})

_RELEASE_MARKERS = (
    "release_pair", "release_identity", "database_release", "pair_mismatch",
    "database_live_manifest", "database_live_receipt", "package_integrity",
    "database_integrity", "database_corrupt", "manifest_signature",
    "manifest_integrity", "database_special_capability_catalog_invalid",
)
_REQUEST_MARKERS = (
    "tool_input", "invalid_plan", "schema", "forbidden", "unauthorized",
    "permission", "stale_context", "revision", "file_conflict", "size_limit",
    "response_limit", "stage_skip", "stage_invalid", "unknown_tool",
)
_REQUEST_CODES = frozenset({"invalid_capability_query", "capability_pointer_not_found",
    "capability_command_not_found", "capability_section_not_found", "invalid_diagnostic_id",
    "system_diagnostic_not_found", "runtime_request_invalid", "attack_source_assembly_conflict",
    "periodic_source_assembly_conflict", "agent_diagnostic_view_unavailable",
    "invalid_query_request", "invalid_observation_request", "invalid_energy_trial_request",
    "damage_report_requires_causal_execution", "cli_request_invalid", "cli_session_request_invalid",
    "input_file_not_found", "input_file_not_readable", "input_file_invalid", "input_json_invalid",
    "input_text_invalid", "input_file_too_large", "semantic_record_request_invalid", "action_admission_catalog_query",
    "action_admission_catalog_cursor", "invalid_template", "invalid_patch",
    "invalid_rotation_check", "shortcut_request_invalid"})
_KNOWLEDGE_MARKERS = (
    "appendix", "knowledge", "semantic", "mechanism_source", "source_evidence",
    "research", "description_missing", "manual_materialization",
)
_ENGINE_CODES = frozenset({
    "engine_capability", "materialization_incomplete",
    "unresolved_component", "not_implemented", "unsupported_target_domain",
    "unsupported_mode", "unsupported_dependency", "engine_capability_missing",
})
_CORE_MARKERS = (
    "equipment_unavailable", "core_data", "character_not_found", "weapon_not_found",
    "artifact_not_found", "database_missing", "dataset_missing", "entity_not_found",
)


def _strings(value: Any) -> Iterable[str]:
    """Yield all typed facts without a display cutoff or recursive walker."""
    pending, seen = [value], set()
    keys = ("code", "reason", "category", "issues", "details", "missing_inputs")
    while pending:
        current = pending.pop()
        if isinstance(current, str):
            yield current.lower()
        elif isinstance(current, (Mapping, list)) and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, Mapping):
                pending.extend(current[key] for key in reversed(keys) if key in current)
            else:
                pending.extend(reversed(current))


def _contains(values: Iterable[str], markers: tuple[str, ...]) -> bool:
    return any(marker in value for value in values for marker in markers)


def classify_domain_failure(
    reason: str | None,
    *,
    operation: str | None = None,
    details: Any = None,
) -> dict[str, Any]:
    """Return a bounded, context-aware failure classification.

    A producer-provided valid category wins.  Otherwise the classifier uses
    both the operation and nested issue codes.  It deliberately never rewrites
    ``reason``; callers publish this projection alongside the original error.
    """

    if reason in CANCELLATION_REASONS:
        return {"category": "cancelled", "failure_kind": "cancelled",
                "blocking_scope": "none", "retryable": True}
    if reason in DATABASE_BUSY_REASONS:
        return {"category": "database_busy", "blocking_scope": "database_access",
                "retryable": True}
    if reason in UNKNOWN_OUTCOME_REASONS:
        return {"category": "outcome_unknown", "blocking_scope": "current_operation",
                "retryable": False}
    values = list(_strings(details))
    reason_text = reason.lower() if isinstance(reason, str) else ""
    operation_text = operation.lower() if isinstance(operation, str) else ""
    values.extend((reason_text, operation_text))

    declared = next((value for value in values if value in BUSINESS_ERROR_CATEGORIES), None)
    if declared:
        category = declared
    elif _contains(values, _RELEASE_MARKERS):
        category = RELEASE_ERROR_CATEGORY
    # Request/schema/permission failures describe an invalid invocation, not
    # an engine feature gap.  Keep this ahead of the broad ``unsupported``
    # compatibility marker so values such as ``unsupported_schema`` are not
    # accidentally reported as missing game-engine capability.
    elif any(value in _REQUEST_CODES for value in values) or _contains(values, _REQUEST_MARKERS):
        category = "request_error"
    elif reason_text in _CAPACITY_CODES or _capacity_issue(details) is not None:
        category = "engine_capability_missing"
    elif operation_text in {"gs_query_appendix", "gs_list_appendix_categories"}:
        category = "knowledge_missing"
    elif any(value in _ENGINE_CODES for value in values):
        category = "engine_capability_missing"
    elif _contains(values, _KNOWLEDGE_MARKERS):
        category = "knowledge_missing"
    elif _contains(values, _CORE_MARKERS):
        category = "core_data_missing"
    elif "not_found" in reason_text:
        category = (
            "knowledge_missing"
            if operation_text in {"gs_query_context", "gs_query_runtime"}
            else "core_data_missing"
        )
    else:
        # Compatibility category, not evidence that the Agent made a bad
        # request. Internal exceptions use the separate system_fault contract.
        return {"category": "request_error", "blocking_scope": "request", "retryable": False,
                "classification_basis": "legacy_fallback", "classification_confidence": "unknown"}

    if category == RELEASE_ERROR_CATEGORY:
        blocking_scope = "release_identity"
        retryable = False
    elif category == "request_error":
        blocking_scope = "request"
        retryable = "stale" in reason_text or "conflict" in reason_text
    elif category == "core_data_missing":
        blocking_scope = "task_capability"
        retryable = True
    elif category == "knowledge_missing":
        blocking_scope = "agent_materialization"
        retryable = True
    else:
        blocking_scope = "engine_capability"
        retryable = False
    return {
        "category": category,
        "blocking_scope": blocking_scope,
        "retryable": retryable,
    }


def normalize_issue(issue: Mapping[str, Any], *, operation: str | None = None) -> dict[str, Any]:
    """Copy one issue and add classification without discarding its detail."""

    result = dict(issue)
    classification = classify_domain_failure(
        str(issue.get("reason") or issue.get("code") or ""),
        operation=operation,
        details=issue,
    )
    result.setdefault("category", classification["category"])
    result.setdefault("blocking_scope", classification["blocking_scope"])
    result.setdefault("retryable", classification["retryable"])
    return result
