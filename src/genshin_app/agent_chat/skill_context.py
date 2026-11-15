"""Advisory work-card routing from shared facts; DSH owns context and history."""
from __future__ import annotations

import hashlib

FOCUS_RESOURCES = {
    "loadout_draft": "references/workflow/focus/loadout.md",
    "action_checkpoint": "references/workflow/focus/actions.md",
    "event_topology": "references/workflow/focus/events.md",
    "numeric_model": "references/workflow/focus/numbers.md",
    "calculation_closure": "references/workflow/focus/closure.md",
}


def input_change_paths(before, after):
    """Keep section-level edit evidence in the existing committed change record."""
    from genshin_app.equipment.files import input_view
    before, after = input_view(before), input_view(after)
    paths = []
    for key in sorted(before.keys() | after.keys()):
        if key == "revision" or before.get(key) == after.get(key):
            continue
        path = "/" + key.replace("~", "~0").replace("/", "~1")
        left, right = before.get(key), after.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            paths.extend(path + "/" + child.replace("~", "~0").replace("/", "~1")
                         for child in sorted(left.keys() | right.keys()) if left.get(child) != right.get(child))
        else:
            paths.append(path)
    return paths


def _mapping(value):
    return value if isinstance(value, dict) else {}


def failure_codes(outline):
    """Routing facts from the actual outline, including a rejected trace event."""
    outline = _mapping(outline)
    rejected = _mapping(outline.get("first_rejected_event"))
    rows = [_mapping(outline.get("primary_reported_issue")),
            *outline.get("additional_reported_issues", [])]
    return sorted({code for code in [*[row.get("code") for row in rows if isinstance(row, dict)],
                                    rejected.get("reason"), rejected.get("type")]
                   if isinstance(code, str) and code})


def _path_focus(paths):
    # These are input sections, never entity IDs or inferred game mechanics.
    groups = (
        ("action_checkpoint", ("/rotation_model", "/actions", "/duration", "/manual_template/loop_duration")),
        ("event_topology", ("/event_topology", "/extensions/event_topology")),
        ("numeric_model", ("/manual_template/hits", "/manual_template/buffs", "/mechanics_model",
                           "/elemental_model", "/effect_bindings", "/special_damage")),
        ("loadout_draft", ("/team", "/equipment_inputs", "/manual_template/characters")),
    )
    return next((focus for focus, prefixes in groups for path in paths if isinstance(path, str)
                 and any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)), None)


def recommend_focus(context):
    """Pure, stable recommendation shared by native context and CLI receipts.

    Accept the real binding envelope as well as a candidate/domain summary.
    Intent remains separately visible; this function never changes permissions,
    task records, candidate state, or the host's stop/continue decision.
    """
    candidate = _mapping(context.get("candidate")) or context
    stage = _mapping(context.get("stage"))
    intent = stage.get("focus", context.get("focus", context.get("workflow_stage")))
    feedback = _mapping(context.get("execution_feedback"))
    attempt = _mapping(candidate.get("latest_attempt")) or _mapping(feedback.get("latest_attempt"))
    review = _mapping(candidate.get("task_review"))
    focus, reason, reads = intent, "manual_focus", []
    current = attempt.get("matches_current_input") is True
    if current and attempt.get("state") == "failed":
        outline = _mapping(attempt.get("failure_summary"))
        problem = (_mapping(attempt.get("problem")) or
                   _mapping(outline.get("primary_reported_issue")))
        codes = [problem.get("code"), attempt.get("reason"), *attempt.get("problem_codes", []),
                 *failure_codes(outline)]
        action_failure = any(word in str(code) for code in codes for word in ("energy", "rotation", "cooldown", "action_validation"))
        focus = (_path_focus([problem.get("path")]) or
                 ("action_checkpoint" if action_failure else "calculation_closure"))
        reason, reads = "current_failure", ["calculation_attempt"]
    else:
        differences = [row for row in review.get("differences", []) if isinstance(row, dict)
                       and row.get("actionable") is True and row.get("id") != "system.task_review_not_declared"]
        if differences:
            focus, reason, reads = "calculation_closure", "task_differences", ["task_evidence"]
        elif current and attempt.get("state") == "succeeded" and attempt.get("operation") in {
                "calculate", "gs_calculate", "calc"}:
            focus, reason, reads = "calculation_closure", "calculation_completed", ["calculated"]
        elif current and attempt.get("state") == "succeeded" and attempt.get("operation") == "gs_compose":
            focus, reason, reads = "numeric_model", "composition_completed", ["report"]
        else:
            changes = candidate.get("changes") or []
            change = _mapping(changes[-1]) if changes else {}
            paths = change.get("input_paths", ["/" + field for field in change.get("fields", [])])
            edit_focus = _path_focus(paths) if change.get("revision") == candidate.get("revision") else None
            if edit_focus:
                focus, reason, reads = edit_focus, "input_edited", ["candidate"]
    resource = FOCUS_RESOURCES.get(focus)
    return {"focus": focus if resource else None, "intent_focus": intent,
            "reason": reason, "resource": resource, "next_resources": reads,
            "focus_is_authorization": False}


def current_focus_card(context):
    """Project the same bundled page the read tool exposes, without a read ledger."""
    from .tools import _skill
    # Artifact stage disclosure uses the full review, while binding context may
    # compact its differences. Reuse that same projection, avoiding truncation
    # changing the selected card. Legacy/standalone envelopes use the router.
    recommendation = (_mapping(_mapping(context.get("stage")).get("work_card_recommendation"))
                      or recommend_focus(context))
    resource = recommendation["resource"]
    if resource is None or context.get("workflow") not in {"artifact", "staged"}:
        return None
    page = _skill(None, {"resource": resource})
    return {**recommendation, "text": page["text"],
            "content_hash": page["content_hash"], "page_hash": hashlib.sha256(page["text"].encode("utf-8")).hexdigest(),
            "next_offset": page["next_offset"], "focus_is_authorization": False}
