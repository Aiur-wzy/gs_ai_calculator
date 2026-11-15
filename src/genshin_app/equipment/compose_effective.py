"""Apply reviewed calculation overrides, then validate the actual execution graph."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genshin_engine import validate_template
from genshin_engine.template import TemplateError

from .compose_baseline import MechanicsBaseline
from .compose_context import ComposeContext
from .compose_post_materialization import PostMaterialization
from .files import fingerprint
from .schema import COMPOSER_VERSION


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectiveExecutionGraph:
    template: Any
    mechanics_model: Any
    mechanics_matches: Any
    compiled_rotation: Any
    override_execution: Any
    computed_value_overrides: Any


@dataclass(frozen=True, slots=True, kw_only=True)
class EffectiveGraphValidation:
    external_components: Any
    external_review: Any


def apply_compose_overrides(
    context: ComposeContext, post: PostMaterialization, db_path: Path,
) -> EffectiveExecutionGraph:
    plan = context.plan
    original_plan = context.original_plan
    template = post.overlay.template
    missing = context.missing
    agent_execution_runtime_receipt = post.overlay.agent_execution_runtime_receipt
    compiled_rotation = post.overlay.compiled_rotation
    mechanics_model = post.overlay.mechanics_model
    mechanics_matches = post.overlay.mechanics_matches


    # Keep the main composer oblivious to override modes: one facade freezes
    # the automatic graph, reviews ownership, and either returns one fully
    # validated effective graph or the untouched baseline.  It runs after all
    # automatic source compilers and before the graph is exposed to execution.
    from .calculation_overrides import (
        database_snapshot_fingerprint,
        materialize_calculation_overrides,
    )
    database_snapshot_fp = fingerprint({"state": "not_required"})
    if original_plan.get("calculation_event_overrides") is not None:
        try:
            database_snapshot_fp = database_snapshot_fingerprint(db_path)
        except ValueError as exc:
            missing(
                getattr(exc, "reason", "override_database_identity_unavailable"),
                "calculation_event_overrides.basis.database_snapshot_fingerprint",
                str(exc), "review_calculation_override",
            )
    materialization_ownership_claims = []
    if isinstance(agent_execution_runtime_receipt, dict):
        for runtime_entry in agent_execution_runtime_receipt.get("entries", []):
            if runtime_entry.get("status") != "installed":
                continue
            installed_targets = [
                row.get("target_identity")
                for row in runtime_entry.get("installed", [])
                if isinstance(row, dict)
                and isinstance(row.get("target_identity"), str)
                and row["target_identity"]
            ]
            target_identity = (
                "agent_materialization|entry_id=" + runtime_entry["entry_id"]
                + "|targets=" + fingerprint(sorted(installed_targets))
            )
            for coverage_key in runtime_entry.get("coverage_keys", []):
                materialization_ownership_claims.append({
                    "owner_id": (
                        "materialization:" + runtime_entry["entry_id"] + ":"
                        + fingerprint(coverage_key)[:16]
                    ),
                    "owner_kind": "materialization",
                    "coverage_key": coverage_key,
                    "target_identity": target_identity,
                    "source_fingerprint": runtime_entry["adapter_fingerprint"],
                })
    override_execution = materialize_calculation_overrides(
        original_plan, template, plan.get("rotation_model"), mechanics_model,
        mechanics_matches,
        database_snapshot_fingerprint=database_snapshot_fp,
        engine_contract_fingerprint=fingerprint({
            "composer_version": COMPOSER_VERSION,
            "plan_version": plan["plan_version"],
            "calculation_override_execution": "effective-execution-v1",
        }),
        additional_ownership_claims=materialization_ownership_claims,
    )
    computed_value_overrides = []
    if override_execution["status"] == "applied":
        effective = override_execution["effective"]
        template = effective["template"]
        mechanics_model = effective["mechanics_model"]
        mechanics_matches = effective["mechanics_matches"]
        plan["rotation_model"] = effective["rotation_model"]
        compiled_rotation = effective["rotation_model"]
        computed_value_overrides = copy.deepcopy(
            effective.get("computed_value_overrides", [])
        )
    elif override_execution["status"] == "pending":
        for issue in override_execution["issues"]:
            missing(
                issue["code"], issue.get("path", "calculation_event_overrides"),
                issue["message"], "review_calculation_override",
                category="engine_capability_missing",
                consumer=f"override:{issue.get('path', 'calculation_event_overrides')}",
                required=True,
                consumed=True,
                impact="high",
                blocking_scope="engine_capability",
                retryable=issue["code"] not in {"override_mode_not_implemented"},
                repair_hint="修复覆盖声明或使用当前已支持的通用输入后重新组合",
            )

    return EffectiveExecutionGraph(
        template=template,
        mechanics_model=mechanics_model,
        mechanics_matches=mechanics_matches,
        compiled_rotation=compiled_rotation,
        override_execution=override_execution,
        computed_value_overrides=computed_value_overrides,
    )


def validate_effective_execution_graph(
    context: ComposeContext, baseline: MechanicsBaseline, graph: EffectiveExecutionGraph,
) -> EffectiveGraphValidation:
    original_plan = context.original_plan
    template = graph.template
    issues = context.issues
    assumptions = context.assumptions
    sources = context.sources
    versions = context.versions
    missing = context.missing
    source_fp = baseline.source_fp
    mechanics_model = graph.mechanics_model

    # Validate the graph that will actually reach the executor.  A reviewed
    # replacement or suppression may intentionally repair a bad automatic
    # event, so diagnostics against the discarded baseline must not block it.
    for hit in template["hits"]:
        if hit.get("damage_type_ids") is None:
            missing(
                "missing_input", f"manual_template.hits.{hit['id']}.damage_type_ids",
                "伤害类别待明确", "select_hits",
                category="core_data_missing", consumer=f"damage.hit.{hit['id']}",
                required=True, consumed=True, impact="high",
                blocking_scope="task_capability", retryable=True,
                repair_hint="为当前有效命中补齐伤害类别并重新组合",
            )
    for buff in template.get("buffs", []):
        for effect in buff["effects"]:
            if effect.get("applies_to") is None:
                missing(
                    "effect_pending",
                    f"buffs.{buff['id']}.effects.{effect['id']}.applies_to",
                    "收益作用范围待明确", "select_hits",
                    category="knowledge_missing",
                    consumer=f"buff.effect.{buff['id']}.{effect['id']}",
                    required=True, consumed=True, impact="high",
                    blocking_scope="agent_materialization", retryable=True,
                    repair_hint="明确该收益在当前有效图中的作用范围并重新组合",
                )
            elif effect["applies_to"]["mode"] == "none":
                reason = effect.get("extensions", {}).get("exclusion_reason")
                if not isinstance(reason, str) or not reason.strip():
                    missing(
                        "effect_pending",
                        f"buffs.{buff['id']}.effects.{effect['id']}.extensions.exclusion_reason",
                        "明确不关联伤害事件需要记录本场景理由", "select_hits",
                        category="knowledge_missing",
                        consumer=f"buff.effect.{buff['id']}.{effect['id']}",
                        required=True, consumed=True, impact="high",
                        blocking_scope="agent_materialization", retryable=True,
                        repair_hint="说明该收益为何不影响当前有效图并重新组合",
                    )
                else:
                    assumption = f"{buff['id']}/{effect['id']} 明确不关联当前伤害事件：{reason}"
                    assumptions.append(assumption)
                    template["meta"]["assumptions"].append(assumption)
    if not issues:
        try:
            elemental = (mechanics_model or {}).get("elemental_model") or {}
            from genshin_engine.elemental_schema import SWIRL_RULESETS
            validate_template(template, allow_empty_hits=elemental.get("ruleset") in SWIRL_RULESETS
                              and bool((mechanics_model or {}).get("application_events")))
        except TemplateError as exc:
            missing(
                "unsupported_effect", "resolved_template", str(exc), "select_hits",
                category=exc.category, consumer="effective_execution_graph",
                required=True, consumed=True, impact="critical",
                blocking_scope=exc.blocking_scope, retryable=False,
                repair_hint="根据模板字段诊断修正当前有效执行图输入",
            )
    from .external_damage import review_external_damage
    external_components = sorted(sources + versions, key=fingerprint) if original_plan.get("external_damage_supplements") else []
    external_review = review_external_damage(original_plan, source_fp, source_components=external_components,
                                            template=template, model=mechanics_model)
    if external_review is not None:
        issues.extend(external_review["issues"])

    return EffectiveGraphValidation(
        external_components=external_components,
        external_review=external_review,
    )


__all__ = [
    "EffectiveExecutionGraph",
    "EffectiveGraphValidation",
    "apply_compose_overrides",
    "validate_effective_execution_graph",
]

