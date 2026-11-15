"""Build the automatic mechanics baseline before any Agent/override overlay.

All event/source compilation and fingerprint closure retain their historical
ordering. This pass uses the caller's existing context and database read scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compose_actions import ActionPreparation
from .compose_context import ComposeContext
from .compose_sources import SourceCompilation
from .files import fingerprint
from .schema import PlanError
from .tuning import tuning_source_fingerprint


@dataclass(frozen=True, slots=True, kw_only=True)
class MechanicsBaseline:
    applicable: list[Any]
    event_compilations: Any
    passive_compilations: Any
    energy_compilations: Any
    benefit_compilations: Any
    mechanism_compilations: Any
    mechanism_replay_context: Any
    reaction_em_attribute_facts: Any
    static_inclusion_batch: Any
    source_fp: Any
    tuning_source_fp: Any
    manual_dependency_previews: Any
    shared_pool_adoptions: Any
    state_runtime_adoptions: Any
    resource_counter_adoptions: Any
    resource_counter_view_adoptions: Any
    attribute_callback_adoptions: Any
    resource_reaction_adoptions: Any
    threshold_observer_adoptions: Any
    threshold_trigger_adoptions: Any
    mechanics_model: Any
    mechanics_matches: Any
    mechanics_previews: Any
    legacy_matches: Any


def prepare_compose_baseline(
    context: ComposeContext, actions: ActionPreparation,
    compiled_sources: SourceCompilation, db_path: Path,
) -> MechanicsBaseline:
    plan = context.plan
    original_plan = context.original_plan
    template = context.template
    issues = context.issues
    sources = context.sources
    versions = context.versions
    preview = context.preview
    requirements = context.requirements
    missing = context.missing
    context_fp = context.context_fp
    admission_compilations = actions.admission_compilations
    resource_compilations = actions.resource_compilations
    parameter_compilations = actions.parameter_compilations
    dispatch = actions.dispatch
    metadata = actions.metadata
    periodic = actions.periodic
    upstream_compilations = compiled_sources.upstream_compilations
    elemental_compilations = compiled_sources.elemental_compilations
    reaction_compilations = compiled_sources.reaction_compilations
    applicable = compiled_sources.applicable

    from .action_events import compile_action_events
    if dispatch.generated_profile_ids:
        # Reviewed dispatch rows authorize only their own freshly reconstructed
        # semantic scope.  Run a pure preview to obtain that exact fingerprint;
        # manual profiles retain the ordinary explicit confirmation workflow.
        try:
            _, dispatch_preview, _ = compile_action_events(
                plan, template, parameter_compilations=parameter_compilations,
                elemental_compilations=elemental_compilations, reaction_compilations=reaction_compilations,
                attack_metadata_compilations=metadata.records, periodic_compilations=periodic.records,
                resource_compilations=resource_compilations, missing=lambda *args, **kwargs: None)
            from .action_dispatch_sources import authorize_materialized_profiles
            dispatch.automatically_confirmed_profile_ids = authorize_materialized_profiles(
                plan, dispatch_preview, dispatch.generated_profile_ids)
        except PlanError:
            dispatch.automatically_confirmed_profile_ids = []
    event_model, event_compilations, event_sources = compile_action_events(
        plan, template, parameter_compilations=parameter_compilations,
        elemental_compilations=elemental_compilations, reaction_compilations=reaction_compilations,
        attack_metadata_compilations=metadata.records, periodic_compilations=periodic.records,
        resource_compilations=resource_compilations, missing=missing)
    if event_model is not None:
        plan["mechanics_model"] = event_model
    sources.extend(event_sources)
    from .passive_sources import compile_passive_sources
    passive_model, passive_compilations, passive_sources = compile_passive_sources(
        plan, template, db_path, attack_metadata_compilations=metadata.records,
        action_event_compilations=event_compilations, missing=missing)
    if passive_model is not None:
        plan["mechanics_model"] = passive_model
    sources.extend(passive_sources)
    from .energy_generation_sources import compile_energy_generation_sources
    energy_model, energy_compilations, energy_sources = compile_energy_generation_sources(
        plan, template, db_path, action_event_compilations=event_compilations,
        attack_metadata_compilations=metadata.records, parameter_compilations=parameter_compilations,
        missing=missing)
    if energy_model is not None:
        plan["mechanics_model"] = energy_model
    sources.extend(energy_sources)
    from .limited_hit_benefit_sources import compile_limited_hit_benefit_sources
    benefit_model, benefit_compilations, benefit_sources = compile_limited_hit_benefit_sources(
        plan, template, db_path, action_event_compilations=event_compilations,
        attack_metadata_compilations=metadata.records, parameter_compilations=parameter_compilations,
        missing=missing)
    if benefit_model is not None:
        plan["mechanics_model"] = benefit_model
    sources.extend(benefit_sources)
    mechanism_compilations, mechanism_replay_context = [], None
    if plan.get("mechanism_sources"):
        from .mechanism_sources import compile_mechanism_sources, make_replay_context
        mechanism_model, mechanism_compilations, mechanism_sources = compile_mechanism_sources(
            plan, template, db_path, action_event_compilations=event_compilations,
            attack_metadata_compilations=metadata.records, parameter_compilations=parameter_compilations,
            action_resource_compilations=resource_compilations,
            action_admission_compilations=admission_compilations,
            periodic_compilations=periodic.records, elemental_compilations=elemental_compilations,
            reaction_compilations=reaction_compilations,
            equipment_evidence=preview['equipment_sources'], equipment_requirements=requirements,
            missing=missing)
        mechanism_replay_context = make_replay_context(plan, template, records=mechanism_compilations,
            action_event_compilations=event_compilations, attack_metadata_compilations=metadata.records,
            parameter_compilations=parameter_compilations, action_resource_compilations=resource_compilations,
            action_admission_compilations=admission_compilations,
            equipment_evidence=preview['equipment_sources'], equipment_requirements=requirements,
            periodic_compilations=periodic.records, elemental_compilations=elemental_compilations,
            reaction_compilations=reaction_compilations)
        if mechanism_model is not None:
            from .mechanism_cast_scope import reconcile_equipment_coverage
            reconcile_equipment_coverage(mechanism_compilations, requirements, issues)
            plan["mechanics_model"] = mechanism_model
        sources.extend(mechanism_sources)
    from .reaction_em_sources import finalize_projections
    applicable, reaction_em_scope_sources, reaction_em_attribute_facts = finalize_projections(
        plan, template, upstream_compilations, applicable, missing)
    sources.extend(reaction_em_scope_sources)
    from .static_inclusion_sources import prepare_static_inclusions
    static_inclusion_batch = prepare_static_inclusions(
        plan, requirements, issues, db_path, context_plan=original_plan)
    sources.extend(static_inclusion_batch.source_components)
    # Query evidence is a set, unlike ordered source arrays or same-time switches.
    unique_versions = {fingerprint(version): version for version in versions}
    versions = [unique_versions[key] for key in sorted(unique_versions)]
    source_fp = fingerprint(sorted(sources + versions, key=fingerprint))
    tuning_source_fp = tuning_source_fingerprint(
        sources, versions, mechanism_compilations, mechanism_replay_context, source_fp)
    # Confirmation of legacy/manual constants is explicit whenever equipment is managed.
    manual_dependency_previews = []
    manual_refs = {(b["id"], e["id"]) for b in plan["manual_template"].get("buffs", []) for e in b["effects"]}
    dependencies = plan.get("manual_effect_dependencies", [])
    if len({(d["effect_ref"]["buff_id"], d["effect_ref"]["effect_id"]) for d in dependencies}) != len(dependencies):
        raise PlanError("手工效果依赖确认重复")
    for dependency in dependencies:
        if (dependency["effect_ref"]["buff_id"], dependency["effect_ref"]["effect_id"]) not in manual_refs:
            raise PlanError("手工效果依赖引用不存在")
    if plan["equipment_inputs"]:
        for buff_id, effect_id in sorted(manual_refs):
            ref = {"buff_id": buff_id, "effect_id": effect_id}
            d = next((d for d in dependencies if d["effect_ref"] == ref), None)
            manual_dependency_previews.append({"effect_ref": ref, "context_fingerprint": context_fp})
            if (d is None or d["mode"] == "unreviewed" or not d["note"].strip() or
                d["mode"] == "manual_context" and d["context_fingerprint"] != context_fp):
                missing("dependency_stale", f"manual_effect_dependencies.{buff_id}.{effect_id}",
                        "需确认原手工效果是否依赖配装及当前上下文")
    from .mechanics import prepare_mechanics
    shared_pool_adoptions, state_runtime_adoptions, resource_counter_adoptions, resource_counter_view_adoptions = [], [], [], []
    attribute_callback_adoptions = []
    resource_reaction_adoptions = []
    threshold_observer_adoptions, threshold_trigger_adoptions = [], []
    mechanics_model, mechanics_matches, mechanics_previews, legacy_matches = prepare_mechanics(
        plan, applicable, template, tuning_source_fp, missing, context_plan=original_plan,
        passive_compilations=passive_compilations, energy_generation_compilations=energy_compilations,
        limited_hit_benefit_compilations=benefit_compilations,
        mechanism_compilations=mechanism_compilations, shared_pool_adoptions=shared_pool_adoptions,
        state_runtime_adoptions=state_runtime_adoptions, resource_counter_adoptions=resource_counter_adoptions,
        resource_counter_view_adoptions=resource_counter_view_adoptions,
        attribute_callback_adoptions=attribute_callback_adoptions,
        resource_threshold_observer_adoptions=threshold_observer_adoptions,
        resource_threshold_trigger_adoptions=threshold_trigger_adoptions,
        resource_reaction_adoptions=resource_reaction_adoptions)

    context.versions = versions
    return MechanicsBaseline(
        applicable=applicable,
        event_compilations=event_compilations,
        passive_compilations=passive_compilations,
        energy_compilations=energy_compilations,
        benefit_compilations=benefit_compilations,
        mechanism_compilations=mechanism_compilations,
        mechanism_replay_context=mechanism_replay_context,
        reaction_em_attribute_facts=reaction_em_attribute_facts,
        static_inclusion_batch=static_inclusion_batch,
        source_fp=source_fp,
        tuning_source_fp=tuning_source_fp,
        manual_dependency_previews=manual_dependency_previews,
        shared_pool_adoptions=shared_pool_adoptions,
        state_runtime_adoptions=state_runtime_adoptions,
        resource_counter_adoptions=resource_counter_adoptions,
        resource_counter_view_adoptions=resource_counter_view_adoptions,
        attribute_callback_adoptions=attribute_callback_adoptions,
        resource_reaction_adoptions=resource_reaction_adoptions,
        threshold_observer_adoptions=threshold_observer_adoptions,
        threshold_trigger_adoptions=threshold_trigger_adoptions,
        mechanics_model=mechanics_model,
        mechanics_matches=mechanics_matches,
        mechanics_previews=mechanics_previews,
        legacy_matches=legacy_matches,
    )


__all__ = ["MechanicsBaseline", "prepare_compose_baseline"]

