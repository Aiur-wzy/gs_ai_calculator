"""Ordered action preparation and attack-semantics compilation pass.

This boundary preserves admission -> resource -> capacity -> dispatch ->
metadata -> periodic -> hit-parameter order. Compilers keep their established
all-or-none contracts; this pass only assembles their already-owned artifacts.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .compose_context import ComposeContext

if TYPE_CHECKING:
    from .action_dispatch_sources import PreparedActionDispatch
    from .attack_metadata import PreparedAttackMetadata
    from .periodic_sources import PreparedPeriodicSources


@dataclass(frozen=True, slots=True, kw_only=True)
class ActionPreparation:
    agent_materialization_receipt: dict[str, Any] | None
    agent_execution_effective_input: dict[str, Any] | None
    agent_execution_runtime_receipt: dict[str, Any] | None
    agent_execution_runtime_graph_fingerprints: dict[str, Any] | None
    compiled_rotation: dict[str, Any] | None
    admission_compilations: list[Any]
    resource_compilations: list[Any]
    capacity_compilations: list[Any]
    dispatch: PreparedActionDispatch
    metadata: PreparedAttackMetadata
    periodic: PreparedPeriodicSources
    parameter_compilations: list[Any]


def prepare_compose_actions(context: ComposeContext, db_path: Path) -> ActionPreparation:
    plan, original_plan, template = context.plan, context.original_plan, context.template
    missing, sources, assumptions = context.missing, context.sources, context.assumptions

    # Runtime-only policy belongs to the compiled axis. Even the no-source
    # path must detach the root: context.original_plan may alias context.plan.
    from .energy_policy import compile_current_energy_rotation
    authored_rotation = plan.get("rotation_model")
    policy_rotation = compile_current_energy_rotation(authored_rotation)
    policy_compiled = policy_rotation != authored_rotation
    if isinstance(policy_rotation, dict):
        plan = {**plan, "rotation_model": policy_rotation}
        context.plan = plan

    agent_materialization_receipt = None
    agent_execution_effective_input = None
    agent_execution_runtime_receipt = None
    agent_execution_runtime_graph_fingerprints = None
    materialization_document = plan.get("extensions", {}).get(
        "agent_execution_materialization")
    if materialization_document is not None:
        from .agent_execution_materialization import (
            lint_agent_execution_materialization,
            materialize_agent_execution,
        )
        topology = plan.get("extensions", {}).get("event_topology")
        agent_materialization_receipt = lint_agent_execution_materialization(
            original_plan, topology, materialization_document)
        if agent_materialization_receipt["status"] == "ready":
            agent_execution_effective_input = materialize_agent_execution(
                original_plan, topology, materialization_document)
        else:
            missing(
                "agent_execution_materialization_not_ready",
                "extensions.agent_execution_materialization",
                "Agent执行材料化输入尚未通过当前方案、拓扑、来源与处理器校验。",
                "review_agent_materialization",
                category=("request_error" if agent_materialization_receipt["status"] in {
                    "invalid", "stale"} else "knowledge_missing"),
                consumer="agent_execution_materialization",
                required=True,
                consumed=True,
                impact="high",
                blocking_scope="agent_materialization",
                retryable=True,
                repair_hint="读取材料化回执，修复精确缺项后重新组合",
            )

    from .action_admissions import compile_action_admissions
    compiled_rotation, admission_compilations, admission_sources = compile_action_admissions(plan, db_path, missing)
    sources.extend(admission_sources)
    if admission_compilations and compiled_rotation is not None:
        plan["rotation_model"] = compiled_rotation
    for item in admission_compilations:
        if item["status"] == "ready":
            assumptions.append(f"{item['ability_id']}采用已审核静态动作门禁映射；命中、产球、动画派发与动态冷却仍未验证")

    from .action_resources import compile_action_resources
    resource_rotation, resource_compilations, resource_sources = compile_action_resources(
        plan, db_path, missing, native_plan=original_plan)
    sources.extend(resource_sources)
    if resource_compilations and resource_rotation is not None:
        compiled_rotation = resource_rotation
        plan["rotation_model"] = resource_rotation
    for item in resource_compilations:
        if item["status"] == "ready":
            assumptions.append(f"{item['ability_id']}按声明采用数据库基础CD、单次充能与固定耗能；"
                               f"用途{item['interpretation_status']}，系统未自动验证游戏适用性：{item['interpretation_basis']}")

    from .action_capacities import (
        compile_action_capacities,
        prospective_capacity_source,
    )
    capacity_rotation, capacity_compilations, capacity_sources = compile_action_capacities(plan, db_path, missing)
    sources.extend(capacity_sources)
    # A fresh pending projection contributes only its stable dependency
    # identity.  It does not add charges to the compiled rotation or grant
    # execution authority, but downstream confirmations can settle in one
    # round against the exact source that acceptance will deliver.
    sources.extend(source for record in capacity_compilations
                   if (source := prospective_capacity_source(record)) is not None)
    if capacity_compilations and capacity_rotation is not None:
        compiled_rotation = capacity_rotation
        plan["rotation_model"] = compiled_rotation

    from .action_dispatch_sources import prepare_action_dispatch_sources
    dispatch = prepare_action_dispatch_sources(plan, db_path, missing)
    sources.extend(dispatch.sources)
    # Dispatch materialization mutates only this private plan copy.  Rebuild
    # the working template before downstream source compilers so the newly
    # generated prototype hits and their metadata declarations are observed
    # in one transaction.  The public/native plan is restored from
    # ``original_plan`` before returning below.
    if dispatch.generated_profile_ids:
        template = copy.deepcopy(plan["manual_template"])

    from .attack_metadata import prepare_attack_metadata
    metadata = prepare_attack_metadata(plan, template, db_path, missing)
    sources.extend(metadata.sources)
    from .periodic_sources import prepare_periodic_sources
    periodic = prepare_periodic_sources(plan, template, db_path, missing)
    sources.extend(periodic.sources)
    from .hit_parameters import compile_hit_parameters
    parameter_compilations, parameter_sources = compile_hit_parameters(plan, template, db_path, missing,
        prepared_contexts={**metadata.parameter_contexts, **periodic.parameter_contexts})
    sources.extend(parameter_sources)
    for item in parameter_compilations:
        if item["status"] == "ready":
            declaration = "情景假设" if item["interpretation_status"] == "assumed" else "提供者声明已核实"
            assumptions.append(f"{item['hit_id']} 的数据库标量被声明为 {item['scaling_stat']} 单属性倍率（{declaration}）；"
                               f"系统核对数值来源，未自动验证该用途：{item['interpretation_basis']}")

    if compiled_rotation is None and policy_compiled:
        compiled_rotation = plan["rotation_model"]
    context.template = template
    return ActionPreparation(
        agent_materialization_receipt=agent_materialization_receipt,
        agent_execution_effective_input=agent_execution_effective_input,
        agent_execution_runtime_receipt=agent_execution_runtime_receipt,
        agent_execution_runtime_graph_fingerprints=agent_execution_runtime_graph_fingerprints,
        compiled_rotation=compiled_rotation,
        admission_compilations=admission_compilations,
        resource_compilations=resource_compilations,
        capacity_compilations=capacity_compilations,
        dispatch=dispatch,
        metadata=metadata,
        periodic=periodic,
        parameter_compilations=parameter_compilations,
    )


__all__ = ["ActionPreparation", "prepare_compose_actions"]

