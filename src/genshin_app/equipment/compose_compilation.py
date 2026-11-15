"""Marshal finalized compilation artifacts for admission and public projection.

This is a read-only wiring boundary. Formula work, source reads, materialization,
override execution and graph validation have already run in their own passes.
"""

from __future__ import annotations

from .compose_actions import ActionPreparation
from .compose_admission import ComposeAdmissionInputs, ComposeCompilationArtifacts
from .compose_baseline import MechanicsBaseline
from .compose_context import ComposeContext
from .compose_effective import EffectiveExecutionGraph, EffectiveGraphValidation
from .compose_post_materialization import PostMaterialization
from .compose_sources import SourceCompilation


def build_compilation_artifacts(
    context: ComposeContext, actions: ActionPreparation,
    compiled_sources: SourceCompilation, baseline: MechanicsBaseline,
    post: PostMaterialization, graph: EffectiveExecutionGraph,
    validation: EffectiveGraphValidation,
) -> ComposeCompilationArtifacts:
    plan = context.plan
    original_plan = context.original_plan
    template = graph.template
    issues = context.issues
    warnings = context.warnings
    requirements = context.requirements
    sources = context.sources
    versions = context.versions
    preview = context.preview
    context_fp = context.context_fp
    agent_materialization_receipt = actions.agent_materialization_receipt
    agent_execution_effective_input = actions.agent_execution_effective_input
    agent_execution_runtime_receipt = post.overlay.agent_execution_runtime_receipt
    agent_execution_runtime_graph_fingerprints = post.overlay.agent_execution_runtime_graph_fingerprints
    compiled_rotation = graph.compiled_rotation
    admission_compilations = actions.admission_compilations
    resource_compilations = actions.resource_compilations
    capacity_compilations = actions.capacity_compilations
    dispatch = actions.dispatch
    metadata = actions.metadata
    periodic = actions.periodic
    parameter_compilations = actions.parameter_compilations
    upstream_compilations = compiled_sources.upstream_compilations
    elemental_compilations = compiled_sources.elemental_compilations
    reaction_compilations = compiled_sources.reaction_compilations
    event_compilations = baseline.event_compilations
    passive_compilations = baseline.passive_compilations
    energy_compilations = baseline.energy_compilations
    benefit_compilations = baseline.benefit_compilations
    mechanism_compilations = baseline.mechanism_compilations
    mechanism_replay_context = baseline.mechanism_replay_context
    source_fp = baseline.source_fp
    manual_dependency_previews = baseline.manual_dependency_previews
    shared_pool_adoptions = baseline.shared_pool_adoptions
    state_runtime_adoptions = baseline.state_runtime_adoptions
    resource_counter_adoptions = baseline.resource_counter_adoptions
    resource_counter_view_adoptions = baseline.resource_counter_view_adoptions
    attribute_callback_adoptions = baseline.attribute_callback_adoptions
    threshold_observer_adoptions = baseline.threshold_observer_adoptions
    threshold_trigger_adoptions = baseline.threshold_trigger_adoptions
    mechanics_model = graph.mechanics_model
    mechanics_matches = graph.mechanics_matches
    counter_binding_verification_tasks = post.counter_binding_verification_tasks
    attribute_adoption = post.attribute_adoption
    match_previews = post.match_previews
    static_inclusion_final = post.static_inclusion_final
    resource_reaction_adoption = post.resource_reaction_adoption
    override_execution = graph.override_execution
    computed_value_overrides = graph.computed_value_overrides
    external_components = validation.external_components
    external_review = validation.external_review

    admission_inputs = ComposeAdmissionInputs(
        plan=plan,
        original_plan=original_plan,
        template=template,
        mechanics_model=mechanics_model,
        mechanics_matches=mechanics_matches,
        computed_value_overrides=computed_value_overrides,
        issues=issues,
        agent_materialization_receipt=agent_materialization_receipt,
        agent_execution_effective_input=agent_execution_effective_input,
        agent_execution_runtime_receipt=agent_execution_runtime_receipt,
        override_execution=override_execution,
        agent_execution_runtime_graph_fingerprints=agent_execution_runtime_graph_fingerprints,
        source_fp=source_fp,
        warnings=warnings,
        preview=preview,
        context_fp=context_fp,
        requirements=requirements,
        match_previews=match_previews,
        manual_dependency_previews=manual_dependency_previews,
        versions=versions,
        sources=sources,
        resource_reaction_adoption=resource_reaction_adoption,
        static_inclusion_final=static_inclusion_final,
        external_review=external_review,
        external_components=external_components,
        elemental_compilations=elemental_compilations,
        counter_binding_verification_tasks=counter_binding_verification_tasks,
        reaction_compilations=reaction_compilations,
        parameter_compilations=parameter_compilations,
        capacity_compilations=capacity_compilations,
        admission_compilations=admission_compilations,
        compiled_rotation=compiled_rotation,
        resource_compilations=resource_compilations,
        event_compilations=event_compilations,
        dispatch=dispatch,
        metadata=metadata,
        passive_compilations=passive_compilations,
        energy_compilations=energy_compilations,
        benefit_compilations=benefit_compilations,
        mechanism_compilations=mechanism_compilations,
        periodic=periodic,
        upstream_compilations=upstream_compilations,
    )
    projection_inputs = {
        "original_plan": original_plan,
        "template": template,
        "attribute_adoption": attribute_adoption,
        "shared_pool_adoptions": shared_pool_adoptions,
        "state_runtime_adoptions": state_runtime_adoptions,
        "resource_counter_adoptions": resource_counter_adoptions,
        "resource_counter_view_adoptions": resource_counter_view_adoptions,
        "attribute_callback_adoptions": attribute_callback_adoptions,
        "threshold_observer_adoptions": threshold_observer_adoptions,
        "threshold_trigger_adoptions": threshold_trigger_adoptions,
        "mechanism_replay_context": mechanism_replay_context,
        "admission_compilations": admission_compilations,
        "resource_compilations": resource_compilations,
        "capacity_compilations": capacity_compilations,
        "agent_execution_runtime_receipt": agent_execution_runtime_receipt,
        "override_execution": override_execution,
        "compiled_rotation": compiled_rotation,
        "mechanics_model": mechanics_model,
        "mechanics_matches": mechanics_matches,
    }
    compiled = ComposeCompilationArtifacts(admission_inputs, projection_inputs)
    return compiled


__all__ = ["build_compilation_artifacts"]

