"""Projection-only reporting helpers for the mechanics runtime."""

from __future__ import annotations

import copy

from genshin_engine import resource_reaction_runtime as reaction_resource_runtime
from genshin_engine.buff_state_timeline import build_buff_state_timeline
from genshin_engine.special_state_timeline import build_special_state_timeline
from genshin_engine.state_runtime import StateRuntimeError
from genshin_runtime.diagnostics import project_reaction_diagnostics


def attach_special_reaction_report(output, executor, max_events):
    if executor.special_damage_echoes is not None:
        echoes = executor.special_damage_echoes.report()
        output['special_damage_echoes'] = echoes
        if output.get('result') is not None:
            output['result']['special_damage_echoes'] = copy.deepcopy(echoes)
    runtime = executor._special_runtime()
    if runtime is None:
        return
    report = {
        "schema_version": 1,
        "state": runtime.state(),
        "trace": copy.deepcopy(executor.special_trace),
        "damage_weighting": copy.deepcopy(executor.special_damage_weighting),
        "event_counts": {
            "total_scheduled_including_synchronous": executor.event_budget_count,
            "synchronous": dict(executor.synchronous_counts),
            "limit": max_events,
        },
    }
    output["special_reaction_state"] = copy.deepcopy(report["state"])
    output["special_reaction_trace"] = copy.deepcopy(report["trace"])
    project_reaction_diagnostics(output["special_reaction_trace"], bindings=lambda: runtime.model.get("handler_bindings"))
    output["special_reactions"] = report
    if output.get("result") is not None:
        output["result"]["special_reactions"] = copy.deepcopy(report)


def attach_state_runtime_report(output, executor, max_events):
    if not executor.state_runtime:
        return
    report = executor.state_runtime.report()
    report["engine_queue_integration"] = "mechanics_event_queue"
    try:
        report["state"] = executor.state_runtime.boundary(executor.time)
    except StateRuntimeError as exc:
        report["state"] = None
        report["state_issue"] = exc.issue
    except reaction_resource_runtime.ReactionResourceError as exc:
        report["state"] = None
        report["state_issue"] = copy.deepcopy(exc.issue)
        output.update(status="pending", result=None, resolved_template=None)
        if exc.issue not in output["issues"]:
            output["issues"].append(copy.deepcopy(exc.issue))
    report["execution_status"] = output["status"]
    report["event_counts"] = {"total": executor.event_budget_count, "limit": max_events}
    output["state_runtime_trace"] = report["execution_trace"]
    output["state_runtime_state"] = report["state"]
    output["state_runtime"] = report
    if output["status"] in ("pending", "invalid"):
        output.update(result=None, resolved_template=None)
    elif output.get("result") is not None:
        output["result"]["state_runtime"] = copy.deepcopy(report)


def attach_attribute_report(output, executor, max_events):
    if not executor.attribute_runtime:
        return
    report = executor.attribute_runtime.report()
    if report.get("export_issue"):
        output["issues"].append(report["export_issue"])
        output["status"] = (
            "pending" if report["export_issue"]["severity"] == "pending" else "invalid"
        )
    report["event_counts"] = {"total": executor.event_budget_count, "limit": max_events}
    output["attribute_ledger"] = report
    from genshin_engine.attribute_budget import (
        BINDING_CONTRACT,
        PRODUCER_CONTRACT,
        budget_fingerprint,
    )
    binding = {'contract': BINDING_CONTRACT,
               'policy': copy.deepcopy(executor.attribute_runtime.budget_policy),
               'model_contract': report['contract'],
               'usage_fingerprint': budget_fingerprint(report['usage']),
               'declaration_fingerprint': budget_fingerprint(report['declarations'])}
    binding['binding_fingerprint'] = budget_fingerprint(binding)
    output['attribute_execution_budget'] = binding
    if executor.attribute_callbacks:
        output["attribute_callbacks"] = executor.attribute_callbacks.report()
    if output["status"] in ("pending", "invalid"):
        output.update(result=None, resolved_template=None)
    elif output.get("result") is not None:
        output["result"]["attribute_ledger"] = report
        output['result']['attribute_execution_budget'] = binding
        output['result']['calculation_producer_contract'] = PRODUCER_CONTRACT
        if executor.attribute_callbacks:
            output["result"]["attribute_callbacks"] = output["attribute_callbacks"]


def attach_shared_pool_report(output, executor, max_events):
    if not executor.shared_pools:
        return
    report = executor.shared_pools.report(executor.time)
    output.update(mechanics_trace=executor.trace, sampling_trace=executor.samples)
    report["event_counts"] = {"total": executor.event_budget_count, "limit": max_events}
    output["shared_pool_trace"] = report["trace"]
    output["shared_pool_state"] = report["state"]
    counter_report = executor.shared_pools.counter_report(executor.time)
    if counter_report is not None:
        counter_report["event_counts"] = {
            "total": executor.event_budget_count,
            "limit": max_events,
        }
        output["resource_counters"] = counter_report
    view_report = executor.counter_views.report() if executor.counter_views else None
    observer_report = executor.shared_pools.observer_report()
    threshold_report = executor.shared_pools.threshold_report()
    if observer_report is not None:
        output["resource_threshold_observers"] = observer_report
    if threshold_report is not None:
        output["resource_threshold_triggers"] = threshold_report
    if view_report is not None:
        output["resource_counter_views"] = view_report
        if counter_report is not None:
            counter_report["damage_projection"] = (
                "separate_current_provider_classic_view_contract"
            )
    if output["status"] in ("pending", "invalid"):
        output.update(result=None, resolved_template=None)
    elif output.get("result") is not None:
        if view_report is not None:
            output["result"]["resource_counter_views"] = view_report
        if observer_report is not None:
            output["result"]["resource_threshold_observers"] = observer_report
        if threshold_report is not None:
            output["result"]["resource_threshold_triggers"] = threshold_report
        output["result"]["shared_pools"] = report
        if counter_report is not None:
            output["result"]["resource_counters"] = counter_report


def attach_buff_state_timeline(output, model, template, duration):
    receipt = build_buff_state_timeline(
        model,
        output["mechanics_trace"],
        output["rotation_check"],
        duration=duration,
        execution_status=output["status"],
        template=template,
        shared_pool_trace=output.get("shared_pool_trace"),
    )
    output["buff_state_timeline"] = receipt
    if output.get("result") is not None:
        output["result"]["buff_state_timeline"] = copy.deepcopy(receipt)
    if "special_reactions" in output:
        special = build_special_state_timeline(
            output["special_reactions"]["trace"], duration=duration,
            execution_limit=receipt["execution_limit"], execution_status=output["status"])
        output["special_state_timeline"] = special
        if output.get("result") is not None:
            output["result"]["special_state_timeline"] = copy.deepcopy(special)


__all__ = [
    "attach_attribute_report",
    "attach_buff_state_timeline",
    "attach_shared_pool_report",
    "attach_special_reaction_report",
    "attach_state_runtime_report",
]
