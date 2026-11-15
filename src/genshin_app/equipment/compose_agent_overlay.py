"""Install the Agent's strong-typed overlay over the automatic graph once."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .compose_actions import ActionPreparation
from .compose_baseline import MechanicsBaseline
from .compose_context import ComposeContext
from .files import fingerprint


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentExecutionOverlay:
    template: Any
    mechanics_model: Any
    mechanics_matches: Any
    compiled_rotation: Any
    agent_execution_runtime_receipt: Any
    agent_execution_runtime_graph_fingerprints: Any


def prepare_agent_execution_overlay(
    context: ComposeContext, actions: ActionPreparation, baseline: MechanicsBaseline,
) -> AgentExecutionOverlay:
    plan = context.plan
    original_plan = context.original_plan
    template = context.template
    missing = context.missing
    agent_execution_effective_input = actions.agent_execution_effective_input
    agent_execution_runtime_receipt = actions.agent_execution_runtime_receipt
    agent_execution_runtime_graph_fingerprints = actions.agent_execution_runtime_graph_fingerprints
    compiled_rotation = actions.compiled_rotation
    mechanics_model = baseline.mechanics_model
    mechanics_matches = baseline.mechanics_matches

    if agent_execution_effective_input is not None:
        from .agent_execution_runtime import adapt_agent_execution_runtime

        runtime_adaptation = adapt_agent_execution_runtime(
            original_plan,
            original_plan["extensions"]["event_topology"],
            agent_execution_effective_input,
            template,
            plan.get("rotation_model"),
            mechanics_model,
        )
        template = runtime_adaptation["template"]
        mechanics_model = runtime_adaptation["mechanics_model"]
        plan["rotation_model"] = runtime_adaptation["rotation_model"]
        compiled_rotation = runtime_adaptation["rotation_model"]
        agent_execution_runtime_receipt = runtime_adaptation["receipt"]
        agent_execution_runtime_graph_fingerprints = {
            "template_fingerprint": fingerprint(template),
            "rotation_model_fingerprint": fingerprint(plan.get("rotation_model")),
            "mechanics_model_fingerprint": fingerprint(mechanics_model),
        }
        for entry in agent_execution_runtime_receipt["entries"]:
            if entry["status"] == "installed":
                continue
            issue = entry["issues"][0] if entry["issues"] else {
                "code": "agent_materialization_runtime_adapter_partial",
                "message": "材料化输入尚未完整安装到有效执行图。",
            }
            for coverage_key in entry["coverage_keys"]:
                missing(
                    issue["code"],
                    f"extensions.agent_execution_materialization.entries.{entry['entry_id']}",
                    issue["message"],
                    "update_engine_capability",
                    category="engine_capability_missing",
                    consumer=coverage_key,
                    required=True,
                    consumed=True,
                    impact="high",
                    blocking_scope="agent_materialization",
                    retryable=entry["status"] == "partial",
                    repair_hint=(
                        "补齐当前通用适配器要求的强类型输入后重新组合"
                        if entry["status"] == "partial"
                        else "使用受支持的通用机制族，或等待新增强类型运行时适配器"
                    ),
                )

    return AgentExecutionOverlay(
        template=template,
        mechanics_model=mechanics_model,
        mechanics_matches=mechanics_matches,
        compiled_rotation=compiled_rotation,
        agent_execution_runtime_receipt=agent_execution_runtime_receipt,
        agent_execution_runtime_graph_fingerprints=agent_execution_runtime_graph_fingerprints,
    )


__all__ = ["AgentExecutionOverlay", "prepare_agent_execution_overlay"]

