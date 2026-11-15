"""Final admission and audit boundary for equipment composition.

This stage consumes the one private working graph after all overlays and external
review. It owns task-capability admission and the existing receipt finalizers;
the report projector consumes its decision without performing these validations.
No database reads, ledger settlements, or new public result fields are introduced.
"""

from __future__ import annotations

import copy
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from genshin_app.task_capability import (
    build_trusted_effective_graph_scope,
    build_trusted_issue_resolution,
    classify_task_issues,
    issue_identity,
)

from .files import fingerprint, input_view
from .schema import COMPOSER_VERSION, PlanError

if TYPE_CHECKING:
    from .action_dispatch_sources import PreparedActionDispatch
    from .attack_metadata import PreparedAttackMetadata
    from .periodic_sources import PreparedPeriodicSources


@dataclass(frozen=True, slots=True, kw_only=True)
class ComposeAdmissionInputs:
    """Explicit view of finalized compilation artifacts, not a second plan owner.

    The dataclass freezes the binding of each artifact. Containers retain their
    historical aliases within the private compose call; this view neither copies
    them nor grants a new execution or database authority.
    """

    plan: dict[str, Any]
    original_plan: dict[str, Any]
    template: dict[str, Any]
    mechanics_model: dict[str, Any] | None
    mechanics_matches: Any
    computed_value_overrides: Any
    issues: list[dict[str, Any]]
    agent_materialization_receipt: dict[str, Any] | None
    agent_execution_effective_input: dict[str, Any] | None
    agent_execution_runtime_receipt: dict[str, Any] | None
    override_execution: dict[str, Any]
    agent_execution_runtime_graph_fingerprints: dict[str, Any] | None
    source_fp: str
    warnings: list[Any]
    preview: dict[str, Any]
    context_fp: str
    requirements: list[Any]
    match_previews: list[Any]
    manual_dependency_previews: list[Any]
    versions: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    resource_reaction_adoption: Any
    static_inclusion_final: dict[str, Any]
    external_review: dict[str, Any] | None
    external_components: list[Any]
    elemental_compilations: list[Any]
    counter_binding_verification_tasks: list[Any]
    reaction_compilations: list[Any]
    parameter_compilations: list[Any]
    capacity_compilations: list[Any]
    admission_compilations: list[Any]
    compiled_rotation: dict[str, Any] | None
    resource_compilations: list[Any]
    event_compilations: list[Any]
    dispatch: PreparedActionDispatch
    metadata: PreparedAttackMetadata
    passive_compilations: list[Any]
    energy_compilations: list[Any]
    benefit_compilations: list[Any]
    mechanism_compilations: list[Any]
    periodic: PreparedPeriodicSources
    upstream_compilations: list[Any]


@dataclass(frozen=True, slots=True)
class ComposeAdmissionDecision:
    """The final two-state execution decision and its established audit envelope."""

    state: str
    resolution: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ComposeCompilationArtifacts:
    """One compiled graph awaiting admission, plus read-only projection inputs."""

    admission_inputs: ComposeAdmissionInputs
    projection_inputs: dict[str, Any]


def _trusted_task_capability_resolutions(
    issues,
    *,
    original_plan,
    materialization_receipt,
    effective_input,
    runtime_receipt,
    override_execution,
    effective_graph,
    materialized_graph_fingerprints=None,
):
    """Project verified execution coverage into exact private resolutions.

    Coverage keys are declarations, not authority.  Materialization and
    override claims enter this function only after their complete receipts
    have been verified against the current Composer run.  The returned
    objects are private task-capability values; no JSON field can imitate
    them.  Legacy issues without an explicit producer-owned consumer remain
    conservatively unresolved.
    """

    graph_fingerprint = fingerprint({
        "template": effective_graph.get("template"),
        "rotation_model": effective_graph.get("rotation_model"),
        "mechanics_model": effective_graph.get("mechanics_model"),
        "mechanics_matches": effective_graph.get("mechanics_matches"),
    })
    claims = {}

    def register(coverage_key, *, authority, resolved_by, proof_fingerprint):
        if not isinstance(coverage_key, str) or not coverage_key.strip():
            raise PlanError(
                "任务能力修复回执包含无效范围",
                "task_capability_repair_scope_invalid",
            )
        coverage_key = coverage_key.strip()
        if coverage_key in claims:
            raise PlanError(
                "同一任务能力范围出现多个修复所有者",
                "task_capability_repair_owner_conflict",
                {
                    "consumer": coverage_key,
                    "owners": [claims[coverage_key]["resolved_by"], resolved_by],
                },
            )
        claims[coverage_key] = {
            "authority": authority,
            "resolved_by": resolved_by,
            # Bind a verified source receipt to this exact final graph. A
            # receipt copied from an earlier compose can never produce the
            # same private resolution for a changed effective graph.
            "proof_fingerprint": fingerprint({
                "authority": authority,
                "coverage_key": coverage_key,
                "source_proof_fingerprint": proof_fingerprint,
                "effective_graph_fingerprint": graph_fingerprint,
            }),
        }

    materialization_signals = (
        materialization_receipt, effective_input, runtime_receipt,
    )
    if any(value is not None for value in materialization_signals):
        partial_lint_only = (
            isinstance(materialization_receipt, dict)
            and materialization_receipt.get("status") != "ready"
            and effective_input is None
            and runtime_receipt is None
        )
        if partial_lint_only:
            # The lint diagnostic remains a normal blocking issue. It has no
            # execution authority and therefore contributes no repair claim.
            pass
        elif not all(isinstance(value, dict) for value in materialization_signals):
            raise PlanError(
                "Agent材料化修复链缺少完整验证回执",
                "agent_materialization_receipt_incomplete",
            )
        # Partial/unsupported adapters deliberately leave their diagnostics
        # blocking. Only the formal complete proof can own repair coverage.
        elif runtime_receipt.get("status") == "complete":
            from .agent_execution_receipt import build_agent_execution_proof

            try:
                proof = build_agent_execution_proof(
                    original_plan, materialization_receipt,
                    effective_input, runtime_receipt,
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise PlanError(
                    "Agent材料化修复回执未通过完整验证",
                    "agent_materialization_receipt_invalid",
                    {"error": str(exc)},
                ) from exc
            if (
                not isinstance(materialized_graph_fingerprints, dict)
                or runtime_receipt.get("effective_graph")
                != materialized_graph_fingerprints
            ):
                raise PlanError(
                    "材料化运行时回执没有绑定当前有效图快照",
                    "agent_materialization_receipt_stale",
                )
            for coverage_key in proof.get("installed_coverage_keys", []):
                register(
                    coverage_key,
                    authority="verified_materialization_receipt",
                    resolved_by="materialization:" + coverage_key,
                    proof_fingerprint=proof["proof_fingerprint"],
                )

    if override_execution.get("status") == "applied":
        from .calculation_overrides import validate_execution_bundle_receipt

        receipt = override_execution.get("receipt")
        try:
            validate_execution_bundle_receipt(receipt, effective_graph)
        except (KeyError, TypeError, ValueError) as exc:
            raise PlanError(
                "计算覆盖修复回执未绑定当前有效图",
                "calculation_override_receipt_invalid",
                {"error": str(exc)},
            ) from exc
        for row in receipt.get("coverage", []):
            register(
                row.get("coverage_key"),
                authority="verified_override_receipt",
                resolved_by="override:" + str(row.get("override_id") or "unknown"),
                proof_fingerprint=receipt["receipt_fingerprint"],
            )

    identity_counts = Counter(issue_identity(issue) for issue in issues)
    resolutions = []
    for issue in issues:
        # This explicit membership test is important: normalize_task_issue
        # gives legacy rows a conservative synthetic consumer, but a verified
        # claim must never silently repair an unmigrated producer.
        consumer = issue.get("consumer") if isinstance(issue, dict) else None
        claim = claims.get(consumer)
        if claim is None or identity_counts[issue_identity(issue)] != 1:
            continue
        resolutions.append(build_trusted_issue_resolution(issue, **claim))
    return resolutions


def finalize_compose_admission(inputs: ComposeAdmissionInputs) -> ComposeAdmissionDecision:
    """Run the terminal checks once, in their established exception order."""

    plan = inputs.plan
    original_plan = inputs.original_plan
    template = inputs.template
    mechanics_model = inputs.mechanics_model
    mechanics_matches = inputs.mechanics_matches
    computed_value_overrides = inputs.computed_value_overrides
    issues = inputs.issues
    agent_materialization_receipt = inputs.agent_materialization_receipt
    agent_execution_effective_input = inputs.agent_execution_effective_input
    agent_execution_runtime_receipt = inputs.agent_execution_runtime_receipt
    override_execution = inputs.override_execution
    agent_execution_runtime_graph_fingerprints = inputs.agent_execution_runtime_graph_fingerprints
    source_fp = inputs.source_fp
    warnings = inputs.warnings
    preview = inputs.preview
    context_fp = inputs.context_fp
    requirements = inputs.requirements
    match_previews = inputs.match_previews
    manual_dependency_previews = inputs.manual_dependency_previews
    versions = inputs.versions
    sources = inputs.sources
    resource_reaction_adoption = inputs.resource_reaction_adoption
    static_inclusion_final = inputs.static_inclusion_final
    external_review = inputs.external_review
    external_components = inputs.external_components
    elemental_compilations = inputs.elemental_compilations
    counter_binding_verification_tasks = inputs.counter_binding_verification_tasks
    reaction_compilations = inputs.reaction_compilations
    parameter_compilations = inputs.parameter_compilations
    capacity_compilations = inputs.capacity_compilations
    admission_compilations = inputs.admission_compilations
    compiled_rotation = inputs.compiled_rotation
    resource_compilations = inputs.resource_compilations
    event_compilations = inputs.event_compilations
    dispatch = inputs.dispatch
    metadata = inputs.metadata
    passive_compilations = inputs.passive_compilations
    energy_compilations = inputs.energy_compilations
    benefit_compilations = inputs.benefit_compilations
    mechanism_compilations = inputs.mechanism_compilations
    periodic = inputs.periodic
    upstream_compilations = inputs.upstream_compilations

    # Only migrated producers take part in effective-graph derivation. Legacy
    # producers remain conservatively required and consumed until they publish
    # an explicit consumer identity. The Agent cannot write this scope: it is
    # rebuilt here from the effective graph and App-owned producer metadata.
    consumer_states = {}
    for issue in issues:
        consumer = issue.get("consumer")
        if not isinstance(consumer, str):
            continue
        state = (issue.get("required"), issue.get("consumed"))
        if state not in {(True, True), (True, False), (False, True), (False, False)}:
            continue
        previous = consumer_states.setdefault(consumer, state)
        if previous != state:
            raise PlanError(
                "同一有效图消费者出现冲突的任务能力状态",
                "task_capability_consumer_conflict",
                {"consumer": consumer, "states": [previous, state]},
            )
    effective_graph_fingerprint = fingerprint({
        "template": template,
        "rotation_model": plan.get("rotation_model"),
        "mechanics_model": mechanics_model,
        "mechanics_matches": mechanics_matches,
    })
    effective_graph_scope = build_trusted_effective_graph_scope(
        [
            {"consumer": consumer, "required": state[0], "consumed": state[1]}
            for consumer, state in consumer_states.items()
        ],
        graph_fingerprint=effective_graph_fingerprint,
    )
    effective_execution_graph = {
        "template": template,
        "rotation_model": plan.get("rotation_model"),
        "mechanics_model": mechanics_model,
        "mechanics_matches": mechanics_matches,
        "computed_value_overrides": computed_value_overrides,
    }
    trusted_resolutions = _trusted_task_capability_resolutions(
        issues,
        original_plan=original_plan,
        materialization_receipt=agent_materialization_receipt,
        effective_input=agent_execution_effective_input,
        runtime_receipt=agent_execution_runtime_receipt,
        override_execution=override_execution,
        effective_graph=effective_execution_graph,
        materialized_graph_fingerprints=agent_execution_runtime_graph_fingerprints,
    )
    task_capability = classify_task_issues(
        issues, trusted_resolutions=trusted_resolutions,
        effective_graph_scope=effective_graph_scope,
    )
    # Keep the long-standing two-state execution contract.  ``degraded`` is
    # exposed by task_capability instead of becoming a third resolution state,
    # because existing GUI/delivery consumers correctly interpret ``ready`` as
    # "no blocking input".  Optional or unconsumed capabilities stay visible
    # without disabling an otherwise self-contained calculation.
    state = "pending" if task_capability["blocking"] else "ready"
    # Freeze no current-policy receipt for an adapter-damaged private axis.
    # This is an assertion, not a default stamp or a native-field exemption.
    from .energy_policy import require_current_energy_rotation
    require_current_energy_rotation(plan.get("rotation_model"),
        path="compose.working.rotation_model",
        allow_none=True)  # A missing draft axis remains a normal pending input.
    selected_rotation = (compiled_rotation if compiled_rotation is not None
                         else original_plan.get("rotation_model"))
    require_current_energy_rotation(selected_rotation,
        path="report.compiled_rotation_model",
        allow_none=True)  # No result/policy claim is issued at compose time.
    input_fp = fingerprint(input_view(original_plan))
    resolution = {"state": state, "composer_version": COMPOSER_VERSION, "input_fingerprint": input_fp,
                  "source_fingerprint": source_fp, "output_fingerprint": fingerprint(template) if state == "ready" else None,
                  "missing_inputs": issues, "task_capability": task_capability,
                  "warnings": warnings, "contribution_report": preview,
                  "context_fingerprint": context_fp, "effect_requirements": requirements,
                  "match_previews": match_previews, "manual_dependency_previews": manual_dependency_previews,
                  "data_versions": versions, "assumptions": template["meta"]["assumptions"],
                  "calculation_override_basis": override_execution["basis"],
                  "automatic_baseline_fingerprint": fingerprint(
                      override_execution["automatic"])}
    provisional_loadout_audit = [
        copy.deepcopy(row) for row in sources
        if isinstance(row, dict) and row.get("kind") == "provisional"
    ]
    resolution["result_fidelity"] = (
        "approximate_with_provisional_entities"
        if provisional_loadout_audit else "formal_database_or_manual_input"
    )
    if provisional_loadout_audit:
        resolution["provisional_loadout_audit"] = provisional_loadout_audit
    if override_execution["receipt"] is not None:
        resolution["calculation_override_receipt"] = override_execution["receipt"]
        if override_execution["status"] == "applied":
            resolution["effective_execution_fingerprint"] = fingerprint(
                override_execution["effective"])
            resolution["compiled_rotation_model_fingerprint"] = fingerprint(
                plan.get("rotation_model"))
            resolution["compiled_mechanics_model_fingerprint"] = fingerprint(
                mechanics_model)
            if computed_value_overrides:
                resolution["computed_value_overrides"] = computed_value_overrides
    if override_execution.get("ownership_audit") is not None:
        resolution["calculation_ownership_audit"] = copy.deepcopy(
            override_execution["ownership_audit"]
        )
    if agent_materialization_receipt is not None:
        resolution["agent_execution_materialization_receipt"] = copy.deepcopy(
            agent_materialization_receipt)
    if agent_execution_effective_input is not None:
        resolution["agent_execution_effective_input"] = copy.deepcopy(
            agent_execution_effective_input)
    if agent_execution_runtime_receipt is not None:
        resolution["agent_execution_runtime_receipt"] = copy.deepcopy(
            agent_execution_runtime_receipt)
        resolution["compiled_rotation_model_fingerprint"] = fingerprint(
            plan.get("rotation_model"))
        resolution["compiled_mechanics_model_fingerprint"] = fingerprint(
            mechanics_model)
    if resource_reaction_adoption is not None:
        resolution['resource_reaction_source_adoption'] = resource_reaction_adoption
    if static_inclusion_final['records']:
        resolution['static_inclusion_compilations'] = static_inclusion_final['records']
    if external_review is not None:
        resolution["external_damage_review"] = external_review
        resolution["external_damage_source_components"] = copy.deepcopy(external_components)
    if elemental_compilations:
        resolution["elemental_compilations"] = elemental_compilations
    if counter_binding_verification_tasks:
        resolution["elemental_counter_verification_tasks"] = counter_binding_verification_tasks
    if reaction_compilations:
        resolution["reaction_damage_compilations"] = reaction_compilations
    if parameter_compilations:
        resolution["hit_parameter_compilations"] = parameter_compilations
    if capacity_compilations:
        from .action_capacities import verify_final_pools
        verify_final_pools(original_plan, plan["rotation_model"], capacity_compilations)
        resolution["action_capacity_compilations"] = capacity_compilations
        resolution["compiled_rotation_model_fingerprint"] = fingerprint(plan["rotation_model"])
    if compiled_rotation is not None:
        resolution["compiled_rotation_model_fingerprint"] = fingerprint(compiled_rotation)
    if admission_compilations:
        resolution["action_admission_compilations"] = admission_compilations
        if compiled_rotation is not None:
            resolution["compiled_rotation_model_fingerprint"] = fingerprint(compiled_rotation)
    if resource_compilations:
        resolution["action_resource_compilations"] = resource_compilations
        resolution["compiled_rotation_model_fingerprint"] = fingerprint(compiled_rotation)
    if event_compilations:
        resolution["action_event_compilations"] = event_compilations
        resolution["action_event_compilation_fingerprint"] = fingerprint(event_compilations)
        resolution["compiled_mechanics_model_fingerprint"] = fingerprint(mechanics_model)
    from .action_automation_audit import build_action_automation_audit
    resolution["action_automation_audit"] = build_action_automation_audit(
        original_plan, event_compilations, dispatch.records, execution_plan=plan)
    if resolution["action_automation_audit"].get("materialization_receipt"):
        resolution["action_materialization_receipt"] = copy.deepcopy(
            resolution["action_automation_audit"]["materialization_receipt"])
        resolution["action_materialization_source"] = {
            "contract": "action-materialization-source-v1",
            "rotation_model": copy.deepcopy(plan["rotation_model"]),
            "action_dispatch_compilations": copy.deepcopy(dispatch.records),
        }
    if dispatch.records:
        resolution["action_dispatch_compilations"] = dispatch.records
        resolution["action_dispatch_compilation_fingerprint"] = fingerprint(dispatch.records)
        resolution["action_dispatch_automatic_confirmations"] = copy.deepcopy(
            dispatch.automatically_confirmed_profile_ids)
        generated_event_records = [record for record in event_compilations
                                   if record.get("profile_id") in dispatch.generated_profile_ids]
        generated_events_ready = (
            {record.get("profile_id") for record in generated_event_records}
            == dispatch.generated_profile_ids
            and all(record.get("status") == "ready" and not record.get("issues")
                    for record in generated_event_records)
        )
        if (dispatch.generated_profile_ids
                and set(dispatch.automatically_confirmed_profile_ids) == dispatch.generated_profile_ids
                and generated_events_ready):
            from .action_dispatch_sources import build_action_dispatch_materialization
            resolution["action_dispatch_materialization"] = build_action_dispatch_materialization(
                plan, dispatch)
    if metadata.records:
        resolution["attack_metadata_compilations"] = metadata.records
        resolution["attack_metadata_compilation_fingerprint"] = fingerprint(metadata.records)
    if passive_compilations:
        resolution["passive_compilations"] = passive_compilations
    if energy_compilations:
        resolution["energy_generation_compilations"] = energy_compilations
    if benefit_compilations:
        resolution["limited_hit_benefit_compilations"] = benefit_compilations
    if mechanism_compilations:
        resolution["mechanism_compilations"] = mechanism_compilations
        resolution["compiled_mechanics_model_fingerprint"] = fingerprint(mechanics_model)
    if periodic.records:
        resolution["periodic_attack_compilations"] = periodic.records
    if upstream_compilations:
        resolution["upstream_compilations"] = upstream_compilations
        resolution["upstream_semantics"] = {
            "execution_status": state,
            "gameplay_semantics_status": "assumed_in_scope" if any(r.get("status") == "ready" for r in upstream_compilations) else "unverified",
            "conditional_semantics": any(r.get("status") == "ready" for r in upstream_compilations),
            "message": ("仅在列明的场景和精确适配假设内执行；不代表完整游戏仿真。"
                        if any(r.get("status") == "ready" for r in upstream_compilations)
                        else "本次没有已执行的上游自动来源；未启用或待处理的来源不贡献收益。"),
            "compilation_fingerprints": [r.get("compilation_fingerprint") for r in upstream_compilations],
        }
        projections = [r for r in upstream_compilations if r.get("whole_source_status") == "partial"]
        if projections:
            resolution["upstream_semantics"]["partial_source_projections"] = [{k: copy.deepcopy(r.get(k)) for k in
                ("binding_id", "projection_status", "whole_source_status", "coverage_certificate", "branches")}
                for r in projections]
            resolution["upstream_semantics"]["message"] += " 含局部来源投影：仅列明分支被执行，整个来源仍未完整支持。"

    return ComposeAdmissionDecision(state=state, resolution=resolution)


__all__ = [
    "ComposeAdmissionDecision",
    "ComposeAdmissionInputs",
    "ComposeCompilationArtifacts",
    "finalize_compose_admission",
]

