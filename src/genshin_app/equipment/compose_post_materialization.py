"""Finalize attribute, binding, inclusion and resource facts after materialization."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from .compose_actions import ActionPreparation
from .compose_agent_overlay import AgentExecutionOverlay
from .compose_baseline import MechanicsBaseline
from .compose_context import ComposeContext
from .compose_sources import SourceCompilation
from .files import fingerprint
from .schema import COMPOSER_VERSION
from .tuning import compile_matches


@dataclass(frozen=True, slots=True, kw_only=True)
class PostMaterialization:
    overlay: AgentExecutionOverlay
    counter_binding_verification_tasks: Any
    attribute_adoption: Any
    match_previews: Any
    static_inclusion_final: Any
    resource_reaction_adoption: Any


def finalize_post_materialization(
    context: ComposeContext, actions: ActionPreparation,
    compiled_sources: SourceCompilation, baseline: MechanicsBaseline,
    overlay: AgentExecutionOverlay,
) -> PostMaterialization:
    plan = context.plan
    original_plan = context.original_plan
    template = overlay.template
    issues = context.issues
    assumptions = context.assumptions
    requirements = context.requirements
    versions = context.versions
    preview = context.preview
    context_fp = context.context_fp
    missing = context.missing
    metadata = actions.metadata
    periodic = actions.periodic
    upstream_compilations = compiled_sources.upstream_compilations
    elemental_compilations = compiled_sources.elemental_compilations
    applicable = baseline.applicable
    passive_compilations = baseline.passive_compilations
    energy_compilations = baseline.energy_compilations
    benefit_compilations = baseline.benefit_compilations
    mechanism_compilations = baseline.mechanism_compilations
    reaction_em_attribute_facts = baseline.reaction_em_attribute_facts
    static_inclusion_batch = baseline.static_inclusion_batch
    source_fp = baseline.source_fp
    tuning_source_fp = baseline.tuning_source_fp
    resource_reaction_adoptions = baseline.resource_reaction_adoptions
    mechanics_model = overlay.mechanics_model
    mechanics_matches = overlay.mechanics_matches
    mechanics_previews = baseline.mechanics_previews
    legacy_matches = baseline.legacy_matches

    # Database compilation can replace an ``unknown`` ICD placeholder after
    # the native input schema was validated.  Revalidate concrete actor-local
    # counter bindings here, before the report can advertise readiness.
    from genshin_engine.elemental import validate_elemental_model
    from genshin_engine.mechanics_validation import validate_elemental_counter_bindings

    from .application_evidence import research_report_template
    counter_binding_verification_tasks = []
    binding_issues = validate_elemental_counter_bindings(
        template, plan.get("rotation_model") or {}, mechanics_model or {})
    elemental_model = (mechanics_model or {}).get("elemental_model")
    rule_signature_issues = ([issue for issue in validate_elemental_model(elemental_model)
                              if issue.get("code") == "icd_rule_group_conflict"]
                             if isinstance(elemental_model, dict) else [])
    for issue in [*binding_issues, *rule_signature_issues]:
        conflict = copy.deepcopy(issue)
        signature_bindings = copy.deepcopy(issue.get("bindings") or [])
        profile_ids = {value for value in (issue.get("profile_id"), issue.get("conflicting_profile_id")) if value}
        profile_ids.update(binding.get("profile_id") for binding in signature_bindings
                           if binding.get("profile_id"))
        related_tasks = []
        for record in elemental_compilations:
            if record.get("profile_id") in profile_ids and isinstance(record.get("verification_task"), dict):
                related_tasks.append(record["verification_task"])
        for record in metadata.records:
            if record.get("application_profile_id") in profile_ids and isinstance(record.get("verification_task"), dict):
                related_tasks.append(record["verification_task"])
        for record in periodic.records:
            related_tasks.extend(row for row in record.get("verification_tasks", [])
                                 if ((row.get("attack_identity") or {}).get("application_profile_id")
                                     in profile_ids))
        uses_by_fingerprint = {}
        for related in related_tasks:
            identity = related.get("attack_identity")
            application_profile_id = (identity or {}).get("application_profile_id") or (identity or {}).get("profile_id")
            concrete_uses = related.get("uses") if isinstance(related.get("uses"), list) else []
            if concrete_uses:
                for use in concrete_uses:
                    if isinstance(use, dict):
                        concrete = {**copy.deepcopy(use), "application_profile_id": application_profile_id}
                        uses_by_fingerprint[fingerprint(concrete)] = concrete
            elif isinstance(identity, dict):
                concrete = copy.deepcopy(identity)
                concrete.setdefault("application_profile_id", application_profile_id)
                uses_by_fingerprint[fingerprint(concrete)] = concrete
        rule_groups = sorted({value for value in
                              (issue.get("conflicting_rule_group"), issue.get("rule_group"),
                               *(binding.get("rule_group") for binding in signature_bindings)) if value})
        source_paths = list(dict.fromkeys(value for value in [
            *(issue.get("source_paths") or []), issue.get("path"), issue.get("conflicting_path")]
            if value))
        for binding in signature_bindings:
            binding_paths = [binding.get("profile_path"), *(binding.get("usage_paths") or [])]
            binding["source_paths"] = list(dict.fromkeys(value for value in binding_paths if value))
            profile_id = binding.get("profile_id")
            source_records = [record.get("source") for record in elemental_compilations
                              if record.get("profile_id") == profile_id and record.get("source")]
            if source_records:
                binding["database_sources"] = copy.deepcopy(source_records)
                binding["source_paths"].append(f"elemental_sources.{profile_id}")
            source_paths.extend(path for path in binding["source_paths"] if path not in source_paths)
        rule_signature_conflict = issue.get("code") == "icd_rule_group_conflict"
        task = {"kind": ("elemental_rule_signature_conflict" if rule_signature_conflict
                          else "elemental_counter_binding_conflict"),
            "attack_identity": {"actor": issue.get("actor"), "target_id": issue.get("target_id"),
                "profile_ids": sorted(profile_ids), "paths": source_paths},
            "uses": [uses_by_fingerprint[key] for key in sorted(uses_by_fingerprint)],
            "known_fields": {"counter_tag": issue.get("counter_tag"),
                "rule_groups": rule_groups},
            "signature_bindings": signature_bindings,
            "missing_fields": [], "conflicts": [conflict],
            "local_checks": [{"kind": ("rule_group_signature_consistency" if rule_signature_conflict
                                          else "actor_counter_rule_consistency"),
                "actor": issue.get("actor"), "target_id": issue.get("target_id"),
                "counter_tag": issue.get("counter_tag"), "profile_ids": sorted(profile_ids),
                "source_paths": source_paths}],
            "targeted_research_questions": ([
                "逐个核实同名attenuationGroup在列出来源中是否确实指向同一重置时间和完整计数序列。",
                "若签名确实不同，核实哪个档案绑定了错误规则组名称，并记录适用版本与具体攻击分量。",
            ] if rule_signature_conflict else [
                "逐个核实列出的具体攻击分量是否真的共享同一个attenuationTag；不得按普攻、战技、爆发等动作类别推断。",
                "逐个核实每个分量对应的attenuationGroup、重置时间和计数序列，并记录适用版本、目标与触发条件。",
            ]),
            "keywords": [str(value) for value in [issue.get("actor"), issue.get("target_id"),
                issue.get("counter_tag"), *sorted(profile_ids), *rule_groups,
                "attenuationTag", "attenuationGroup"] if value],
            "acceptance_required": ("所有同名rule_group来源均可审计，且最终只对应一个完全一致的重置时间和完整序列签名"
                if rule_signature_conflict else
                "所有冲突攻击分量均有正向来源；同一角色、目标与counter_tag最终只能对应一个完全一致的rule_group、重置时间和序列签名"),
            "database_patch_targets": ["attack_component.counter_tag", "attack_component.rule_group",
                "runtime_attack_metadata.application_identity", "runtime_periodic_attack.application_identity"],
            "research_report_template": research_report_template(),
            "next_action": ("按列出的档案路径、数据库来源与完整签名核实同名规则组；修正错误规则组绑定或数据库映射"
                if rule_signature_conflict else
                "逐个核实同一角色、目标与counter_tag引用的具体攻击分量；统一错误绑定或拆分真实不同的计数标签，不能按动作类别推断")}
        counter_binding_verification_tasks.append(task)
        # Attach the same complete two-sided conflict to every source-backed
        # component that produced either profile.  The top-level task remains
        # available when a profile came from a manual declaration.
        for record in elemental_compilations:
            if record.get("profile_id") in profile_ids:
                record.setdefault("verification_task", {}).setdefault("conflicts", []).append(copy.deepcopy(conflict))
                record["status"] = "pending"
                record.pop("compilation_fingerprint", None)
        for record in metadata.records:
            if record.get("application_profile_id") in profile_ids:
                record.setdefault("verification_task", {}).setdefault("conflicts", []).append(copy.deepcopy(conflict))
                record["status"] = "pending"
        for record in periodic.records:
            affected_channels = {row.get("channel_id") for row in record.get("channels", [])
                                 if row.get("application_profile_id") in profile_ids}
            if affected_channels:
                record["status"] = "pending"
                for verification_task in record.get("verification_tasks", []):
                    if verification_task.get("attack_identity", {}).get("channel_id") in affected_channels:
                        verification_task.setdefault("conflicts", []).append(copy.deepcopy(conflict))
        # Global rule-signature conflicts were already emitted by the source
        # compiler.  Actor-local conflicts are discovered only after concrete
        # dispatch assembly and therefore still need their own missing input.
        if not rule_signature_conflict:
            missing(issue["code"], issue["path"], issue["message"], "review_elemental_source")
    from .attributes import prepare_attribute_model
    if mechanics_model is not None:
        mechanics_model['execution_options'] = copy.deepcopy(plan.get('calculation_options', {'schema_version':1}))
    attribute_model, attribute_adoption = prepare_attribute_model(
        plan, mechanics_model, template, source_fp, missing, context_plan=original_plan,
        fresh_classifications=reaction_em_attribute_facts)
    if attribute_model is not None:
        mechanics_model['attribute_model'] = attribute_model
    # Later source compilers can add EM writers after the passive preview.
    # Check the final delivered model as well; an earlier acceptance cannot
    # approve a callback ordering that the final writer graph does not support.
    from .passive_attribute_scope import inspect_nonextra_sampling
    from .passive_sources import ATTRIBUTE_PROFILE
    for record in passive_compilations:
        if record.get('profile_id') != ATTRIBUTE_PROFILE or record.get('status') != 'ready':
            continue
        qualification = inspect_nonextra_sampling({**plan,'mechanics_model':mechanics_model},template,record['actor'])
        record['final_attribute_sampling_qualification'] = qualification
        record['final_attribute_sampling_fingerprint'] = fingerprint(qualification)
        if qualification['issues']:
            record['status'] = 'pending'
            for issue in qualification['issues']:
                missing(issue['code'], 'passive_sources.'+record['id'], issue['message'], 'review_passive_source')
                record['issues'].append(copy.deepcopy(issue))
    legacy_plan = {**plan, "rotation_tuning": {"buff_hit_matches": legacy_matches}}
    match_previews = compile_matches(template, legacy_plan, tuning_source_fp, missing, assumptions, context_plan=original_plan)
    match_previews += mechanics_previews
    from .static_inclusion_sources import (
        apply_static_inclusion_adoptions,
        finalize_static_inclusions,
    )
    static_inclusion_final = finalize_static_inclusions(
        static_inclusion_batch, plan, template, mechanics_model, requirements, issues, source_fp,
        contribution_report=preview, applicable=applicable, mechanics_matches=mechanics_matches, context_plan=original_plan,
        automatic_compilations=[*upstream_compilations, *passive_compilations, *energy_compilations,
                                *benefit_compilations, *mechanism_compilations])
    apply_static_inclusion_adoptions(static_inclusion_final, requirements, issues)
    template.setdefault("meta", {}).setdefault("assumptions", [])
    template["meta"]["assumptions"] += assumptions
    template["meta"]["equipment_resolution"] = {"composer_version": COMPOSER_VERSION,
                                               "source_fingerprint": source_fp, "data_versions": versions}
    from .resource_reactions import finalize_resource_reactions
    resource_reaction_adoption = finalize_resource_reactions(
        resource_reaction_adoptions[0] if resource_reaction_adoptions else None,
        mechanics_model, missing, characters=[c['id'] for c in template['characters']],
        source_fingerprint=source_fp, context_fingerprint=context_fp)

    return PostMaterialization(
        overlay=overlay,
        counter_binding_verification_tasks=counter_binding_verification_tasks,
        attribute_adoption=attribute_adoption,
        match_previews=match_previews,
        static_inclusion_final=static_inclusion_final,
        resource_reaction_adoption=resource_reaction_adoption,
    )


__all__ = ["PostMaterialization", "finalize_post_materialization"]

