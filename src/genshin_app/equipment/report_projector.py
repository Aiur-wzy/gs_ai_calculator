"""Final projection for an admitted equipment-composition result.

Business decisions are made before this boundary.  The projector only places
already-frozen artifacts into the long-standing public report shape.
"""

from __future__ import annotations

from typing import Any

from genshin_app.presentation import presentation_issues

from .summary import build_equipment_summary


def project_equipment_report(
    *,
    original_plan: dict[str, Any],
    resolution: dict[str, Any],
    template: dict[str, Any],
    state: str,
    attribute_adoption: Any,
    shared_pool_adoptions: list[Any],
    state_runtime_adoptions: list[Any],
    resource_counter_adoptions: list[Any],
    resource_counter_view_adoptions: list[Any],
    attribute_callback_adoptions: list[Any],
    threshold_observer_adoptions: list[Any],
    threshold_trigger_adoptions: list[Any],
    mechanism_replay_context: Any,
    admission_compilations: list[Any],
    resource_compilations: list[Any],
    capacity_compilations: list[Any],
    agent_execution_runtime_receipt: Any,
    override_execution: dict[str, Any],
    compiled_rotation: Any,
    mechanics_model: Any,
    mechanics_matches: Any,
) -> dict[str, Any]:
    plan = original_plan
    plan["resolution"] = resolution
    plan["resolved_template"] = template if state == "ready" else None
    report = {"plan": plan, **resolution}
    if attribute_adoption is not None:
        report["attribute_source_adoption"] = attribute_adoption
    if shared_pool_adoptions:
        report["shared_pool_source_adoptions"] = shared_pool_adoptions
    if state_runtime_adoptions:
        report["state_runtime_source_adoptions"] = state_runtime_adoptions
    if resource_counter_adoptions:
        report["resource_counter_source_adoptions"] = resource_counter_adoptions
    if resource_counter_view_adoptions:
        report["resource_counter_view_adoptions"] = resource_counter_view_adoptions
    if attribute_callback_adoptions:
        report["attribute_callback_source_adoption"] = attribute_callback_adoptions[0]
    if threshold_observer_adoptions:
        report["resource_threshold_observer_adoptions"] = threshold_observer_adoptions
    if threshold_trigger_adoptions:
        report["resource_threshold_trigger_adoptions"] = threshold_trigger_adoptions
    if mechanism_replay_context is not None:
        report["mechanism_replay_context"] = mechanism_replay_context
    if (
        admission_compilations
        or resource_compilations
        or capacity_compilations
        or agent_execution_runtime_receipt is not None
        or override_execution["status"] == "applied"
        or compiled_rotation is not None
    ):
        report["compiled_rotation_model"] = compiled_rotation
    report["presentation_issues"] = presentation_issues(plan)
    if plan["plan_version"] == 3:
        report["mechanics_model"] = mechanics_model
        report["mechanics_matches"] = mechanics_matches
    report["equipment_summary"] = build_equipment_summary(plan, report)
    return report


__all__ = ["project_equipment_report"]
