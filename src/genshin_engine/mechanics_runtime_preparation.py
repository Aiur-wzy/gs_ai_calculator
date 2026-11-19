"""mechanics runtime preparation in the single Mechanics execution pipeline."""

from __future__ import annotations

import copy

from genshin_engine.mechanics_execution_context import MechanicsExecutionContext
from genshin_engine.mechanics_execution_services import MechanicsExecutionServices


def construct_execution_runtime(run: MechanicsExecutionContext, services: MechanicsExecutionServices) -> bool:
    run.executor = services.create_executor(run.projected, run.rotation, run.request.mechanics_model, run.data, run.request.matches)
    run.executor.windows = run.windows
    run.executor.event_budget_count = len(run.expanded)
    if "state_runtime" in run.request.mechanics_model:
        external = {(e["time"], e["order"]): {"event_id": e["id"], "t_us": e["time"], "order": e["order"]} for e in run.expanded}
        run.executor.state_runtime = services.create_state_runtime(run.request.mechanics_model, run.executor, character_ids=run.chars,
            event_ids=run.all_ids, action_ids=set(run.data["actions"]), conditions=run.conditions,
            damage_type_ids=run.damage_types, action_type_ids=set(services.action_types), external_events=list(external.values()))
    if run.executor.resource_reactions:
        run.executor.resource_reactions.bind_state()
    deadline = run.executor.schedule_deadline()
    if deadline:
        run.expanded.append(deadline)
        run.expanded.sort(key=services.event_sort_key)
    return True


def simulate_execution(run: MechanicsExecutionContext, services: MechanicsExecutionServices) -> bool:
    run.simulation_result = services.simulate(run.rotation, run.data, run.expanded, run.windows, run.finite_until, run.rotation_context, observer=run.executor)
    return accept_simulation_result(run, services)


def accept_simulation_result(run: MechanicsExecutionContext, services: MechanicsExecutionServices) -> bool:
    """Shared completion handling for continuous and resumed simulation."""
    if run.simulation_result["status"] == "pending" and (run.simulation_result.get("automatic_energy_delivery")
                                          or run.simulation_result.get("action_capacity_consumption")
                                          or run.simulation_result.get("action_admission_execution")):
        run.output.update(status="pending", rotation_check=run.simulation_result, issues=run.simulation_result["issues"],
                      mechanics_trace=run.executor.trace, sampling_trace=run.executor.samples)
        if run.executor.elemental:
            run.output["elemental_trace"] = run.executor.elemental.trace
        if run.executor.fixed_sources:
            run.output.update(fixed_effect_sources=copy.deepcopy(list(run.executor.fixed_sources.values())),
                          target_condition_trace=run.executor.target_condition_trace)
            from genshin_engine.target_condition_evidence import (
                attach_target_observations,
            )
            attach_target_observations(run.output, run.executor)
        if run.executor.captures:
            run.output["snapshot_capture_trace"] = run.executor.capture_trace
        services.attach_shared_pool_report(run.output, run.executor, services.event_limit)
        services.attach_attribute_report(run.output, run.executor, services.event_limit)
        services.attach_state_runtime_report(run.output, run.executor, services.event_limit)
        services.attach_special_reaction_report(run.output, run.executor, services.event_limit)
        services.attach_buff_state_timeline(run.output, run.request.mechanics_model, run.projected, run.rotation["period"])
        if run.executor.resource_reactions:
            run.executor.resource_reactions.attach(run.output)
        return False
    return True

