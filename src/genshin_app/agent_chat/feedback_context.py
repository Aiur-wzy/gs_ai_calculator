"""Small current App facts for DSH; exact numbers stay in normal tool results."""
from __future__ import annotations

import copy
import math

from genshin_app.diagnostic_facts import diagnostic_facts


def _prompt_facts(value):
    """Keep exact numeric text in prompt metadata and filter sensitive keys."""
    from .native_prompt_v2 import _FORBIDDEN_KEY

    if isinstance(value, dict):
        return {key: _prompt_facts(item) for key, item in value.items()
                if isinstance(key, str) and key and len(key) <= 128 and not _FORBIDDEN_KEY.search(key)}
    if isinstance(value, list):
        return [_prompt_facts(item) for item in value]
    if type(value) is float:
        return repr(value) if math.isfinite(value) else None
    if type(value) is int and abs(value) > 2**53 - 1:
        return str(value)
    if isinstance(value, str):
        return value.replace("\0", "\ufffd") or None
    return value


def feedback_context(summary):
    result = {key: copy.deepcopy(summary[key]) for key in ("current_input", "saved_calculation")
              if key in summary}
    # The same committed edit facts used by the shared work-card router.
    if summary.get("changes"):
        change = summary["changes"][-1]
        result["changes"] = [{key: copy.deepcopy(change[key]) for key in (
            "revision", "fields", "input_paths") if key in change}]
    latest = summary.get("latest_attempt")
    if isinstance(latest, dict):
        result["latest_attempt"] = {key: copy.deepcopy(latest[key]) for key in (
            "attempt_id", "operation", "state", "phase", "engine_execution", "identity",
            "reason", "matches_current_input", "details_ref", "user_action", "does_not_change_acceptance",
            "diagnostic_storage_warning") if key in latest}
        outline = latest.get("failure_summary") or {}
        primary = outline.get("primary_reported_issue") or {}
        result["latest_attempt"]["problem"] = {key: primary[key] for key in (
            "code", "path", "message", "repair_hint") if isinstance(primary.get(key), str) and primary[key]}
        # Already compacted by failure_outline. A second whitelist used to lose
        # the exact expected binding, conflicting IDs and recovery operation.
        result["latest_attempt"]["problem"].update(_prompt_facts(diagnostic_facts(primary)))
        if primary.get("details_truncated"):
            result["latest_attempt"]["problem"]["details_truncated"] = True
        from .skill_context import failure_codes
        result["latest_attempt"]["problem_codes"] = failure_codes(outline)
        result["latest_attempt"]["reported_issue_count"] = outline.get("reported_issue_count", 0)
        repetition = latest.get("repetition")
        if isinstance(repetition, dict):
            result["latest_attempt"]["repetition"] = {key: repetition[key] for key in (
                "previous_attempt_id", "same_input", "same_request", "same_primary_problem", "retry_limit")}
            sections = repetition.get("changed_input_sections", [])
            result["latest_attempt"]["repetition"]["changed_input_sections"] = sections[:8]
            result["latest_attempt"]["repetition"]["changed_input_section_count"] = len(sections)
    assistance = summary.get("default_assistance")
    if isinstance(assistance, dict):
        energy = assistance.get("energy") or {}
        result["assistance"] = {"authority": "advisory_only", "acceptance_effect": "none",
            "energy": {key: energy[key] for key in (
                "planned_burst_count", "observed_burst_count", "declared_income_event_count") if key in energy},
            "detail_ref": {"resource": "default_assistance"}}
    scope = summary.get("validation_scope") or (assistance or {}).get("validation_scope")
    if isinstance(scope, dict):
        result["validation_scope"] = {key: copy.deepcopy(scope[key]) for key in (
            "acceptance_effect", "summary", "execution_available", "actions", "energy",
            "elemental", "special_reactions", "prefilled_reaction_hit_count",
            "game_mechanism_completeness") if key in scope}
    if isinstance(summary.get("task_review"), dict):
        from genshin_app.task_evidence import compact_review
        result["task_review"] = compact_review(summary["task_review"])
    return result
