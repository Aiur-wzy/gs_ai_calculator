"""One static admission/execution chain; early returns and fault scopes are fixed."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass

from genshin_engine.execution_control import ExecutionCancelled
from genshin_engine.mechanics_admission import (
    admit_basic_contract,
    admit_sources_and_captures,
)
from genshin_engine.mechanics_binding_admission import (
    admit_effect_and_resource_bindings,
    project_execution_inputs,
)
from genshin_engine.mechanics_execution_context import (
    MechanicsExecutionContext,
    MechanicsExecutionRequest,
)
from genshin_engine.mechanics_execution_services import MechanicsExecutionServices
from genshin_engine.mechanics_result_finalization import (
    attach_execution_reports,
    project_execution_result,
)
from genshin_engine.mechanics_rotation_preparation import (
    admit_event_references_and_matches,
    expand_execution_events,
    prepare_rotation_semantics,
)
from genshin_engine.mechanics_runtime_preparation import (
    construct_execution_runtime,
    simulate_execution,
)
from genshin_runtime.diagnostics import diagnostic_stage
from genshin_runtime.operation_timing import timed_call


@dataclass(frozen=True, slots=True)
class MechanicsExecutionPass:
    pass_id: str
    operation: Callable[[MechanicsExecutionContext, MechanicsExecutionServices], bool]
    requires: tuple[str, ...] = ()
    produces: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MechanicsPassReceipt:
    pass_id: str
    status: str


_BASIC_PASS = MechanicsExecutionPass("basic-admission-v1", admit_basic_contract, produces=("output",))
_EXECUTION_PASSES = (
    MechanicsExecutionPass("source-admission-v1", admit_sources_and_captures,
                           ("output",), ("captures", "capture_ids")),
    MechanicsExecutionPass("input-projection-v1", project_execution_inputs,
                           ("captures",), ("projected", "rotation", "runtime_events", "chars")),
    MechanicsExecutionPass("binding-admission-v1", admit_effect_and_resource_bindings,
                           ("projected", "rotation", "chars"), ("fixed_sources", "bound_sources")),
    MechanicsExecutionPass("rotation-semantics-v1", prepare_rotation_semantics,
                           ("bound_sources",), ("rotation_context", "data")),
    MechanicsExecutionPass("event-expansion-v1", expand_execution_events,
                           ("data", "runtime_events", "capture_ids"), ("expanded", "windows", "finite_until")),
    MechanicsExecutionPass("event-reference-admission-v1", admit_event_references_and_matches,
                           ("expanded", "fixed_sources"), ("all_ids", "damage_types", "conditions")),
    MechanicsExecutionPass("runtime-construction-v1", construct_execution_runtime,
                           ("all_ids", "windows", "conditions"), ("executor",)),
    MechanicsExecutionPass("simulation-v1", simulate_execution,
                           ("executor", "expanded"), ("simulation_result",)),
    MechanicsExecutionPass("result-projection-v1", project_execution_result,
                           ("executor", "simulation_result"), ("final_result",)),
)


def _run_pass(stage, run, services, trace):
    with diagnostic_stage(
        "mechanics", stage.pass_id, inputs=lambda: {"request": run.request},
        prestate=lambda: {"ready": sorted(run.ready), "output": run.output},
    ) as audit:
        missing = set(stage.requires) - run.ready
        if missing:
            raise RuntimeError(f"Mechanics pass prerequisites missing: {stage.pass_id}: {sorted(missing)}")
        run.current_pass = stage.pass_id
        try:
            complete = timed_call(stage.pass_id, stage.operation, run, services)
        except Exception:
            if trace is not None:
                trace.append(MechanicsPassReceipt(stage.pass_id, "raised"))
            raise
        if complete:
            run.ready.update(stage.produces)
        if trace is not None:
            trace.append(MechanicsPassReceipt(stage.pass_id, "completed" if complete else "returned"))
        audit.result(outputs={"output": run.output}, poststate={"ready": sorted(run.ready), "output": run.output},
                     changed_paths=stage.produces,
                     issue_codes=[row.get("code") for row in run.output.get("issues", []) if isinstance(row, dict)],
                     status="completed" if complete else run.output.get("status", "returned"))
        return complete


def execute_mechanics_pipeline(request: MechanicsExecutionRequest, services: MechanicsExecutionServices,
                               *, pass_trace: list[MechanicsPassReceipt] | None = None) -> dict:
    run = MechanicsExecutionContext(request)
    # Basic schema/time admission has always been outside the fault projector.
    if not _run_pass(_BASIC_PASS, run, services, pass_trace):
        return run.output
    try:
        for stage in _EXECUTION_PASSES:
            if not _run_pass(stage, run, services, pass_trace):
                return run.output
    except services.damage_reporting_limit as exc:
        run.output.update(status="pending", result=None, resolved_template=None)
        run.output["issues"].append(exc.issue)
    except services.attribute_error as exc:
        run.output.update(status=exc.issue["severity"], result=None, resolved_template=None)
        run.output["issues"].append(exc.issue)
        if hasattr(exc, "rotation_check_prefix"):
            run.output["rotation_check"] = exc.rotation_check_prefix
        if run.has_executor:
            run.output.update(mechanics_trace=run.executor.trace, sampling_trace=run.executor.samples)
    except services.shared_pool_error as exc:
        run.output.update(status="pending" if exc.issue["severity"] == "pending" or "limit" in exc.issue["code"] or exc.issue["code"] == "counter_view_value" else "invalid", result=None, resolved_template=None)
        if run.output["status"] == "pending":
            exc.issue["severity"] = "pending"
        if hasattr(exc, "rotation_check_prefix"):
            run.output["rotation_check"] = exc.rotation_check_prefix
        run.output["issues"].append(exc.issue)
        if run.has_executor:
            run.output.update(mechanics_trace=run.executor.trace, sampling_trace=run.executor.samples)
    except services.state_runtime_error as exc:
        run.output.update(status="pending" if exc.issue["severity"] == "pending" else "invalid", result=None, resolved_template=None)
        run.output["issues"].append(exc.issue)
        if hasattr(exc, "rotation_check_prefix"):
            run.output["rotation_check"] = exc.rotation_check_prefix
        if run.has_executor:
            run.output.update(mechanics_trace=run.executor.trace, sampling_trace=run.executor.samples)
    except (services.elemental_pending, services.special_pending, services.target_pending, services.snapshot_pending) as exc:
        run.output.update(status="pending", result=None, resolved_template=None)
        if hasattr(exc, "rotation_check_prefix"):
            run.output["rotation_check"] = exc.rotation_check_prefix
        run.output["issues"].append(exc.issue)
        if run.has_executor:
            run.output["mechanics_trace"] = run.executor.trace
            if run.executor.counter_views and run.executor.counter_views.frames:
                run.output["sampling_trace"] = run.executor.samples
            if run.executor.elemental:
                run.output["elemental_trace"] = run.executor.elemental.trace
            if run.executor.fixed_sources:
                run.output.update(fixed_effect_sources=copy.deepcopy(list(run.executor.fixed_sources.values())),
                              target_condition_trace=run.executor.target_condition_trace)
        else:
            # A pending constructor has no executor, runtime trace or sampled
            # state. Preserve its original issue rather than masking it with
            # an unbound-local error or fabricating an execution prefix.
            issue = copy.deepcopy(exc.issue)
            issue.setdefault("details", {})["mechanics_execution"] = {
                "phase": "preparation",
                "pass_id": run.current_pass,
                "executor_state": "not_initialized",
                "source": f"{type(exc).__module__}.{type(exc).__qualname__}",
            }
            run.output["issues"][-1] = issue
    except ExecutionCancelled:
        raise
    except (ValueError, KeyError, TypeError, ArithmeticError, services.template_error) as exc:
        run.output["issues"].append(services.issue("mechanics_execution", str(exc), "mechanics_model"))
        run.output["status"] = "invalid"
    # These report attachments also remain outside the preceding try scope.
    attach_execution_reports(run, services)
    return run.output

