"""Shared public native-plan API. All functions return unwrapped JSON-compatible values."""
from .action_admission_catalog import (
    action_admission_catalog_capabilities,
    action_admission_model_candidates,
    action_admission_preview,
    whiteboard_action_admission_model_candidates,
)
from .action_admission_catalog import (
    catalog_filter_from_plan as action_admission_catalog_filter_from_plan,
)
from .action_capacity_catalog import (
    action_capacity_catalog_capabilities,
    action_capacity_model_candidates,
    catalog_filter_from_plan,
    catalog_filter_from_whiteboard,
    whiteboard_action_capacity_model_candidates,
)
from .agent_execution_materialization import (
    agent_execution_materialization_capabilities,
    lint_agent_execution_materialization,
    materialize_agent_execution,
    new_agent_execution_materialization,
    validate_agent_execution_materialization,
)
from .agent_execution_runtime import (
    adapt_agent_execution_runtime,
    agent_execution_runtime_capabilities,
)
from .attack_source_assembly import assemble_attack_source
from .attributes import prepare_attribute_review
from .composer import apply_data_update, compose_equipment_plan, prepare_character_input
from .delivery import deliver_plan_file
from .edit_review import preview_equipment_edit
from .external_damage import (
    validate_external_damage_receipt,
    validate_external_damage_result_receipt,
)
from .files import save_plan
from .mechanics import enable_mechanics
from .panel_basis import character_panel_basis
from .panel_reference import panel_reference_contract, reference_panel
from .periodic_source_assembly import assemble_periodic_source
from .provisional_entities import (
    approval_request as provisional_entity_approval_request,
)
from .provisional_entities import (
    attestation_request as provisional_entity_attestation_request,
)
from .provisional_entities import (
    build_usage_receipt as provisional_entity_usage_receipt,
)
from .provisional_entities import capabilities as provisional_entity_capabilities
from .provisional_entities import resolve_entity_ref
from .provisional_entities import (
    validate_usage_receipt as validate_provisional_entity_usage_receipt,
)
from .schema import PlanError, new_plan, validate_plan
from .service import (
    detach_character,
    export_plan_result,
    export_resolved_plan,
    load_plan,
    plan_capabilities,
    run_plan_calc,
)

__all__ = [
    "PlanError",
    "action_admission_catalog_capabilities",
    "action_admission_catalog_filter_from_plan",
    "action_admission_model_candidates",
    "action_admission_preview",
    "action_capacity_catalog_capabilities",
    "action_capacity_model_candidates",
    "adapt_agent_execution_runtime",
    "agent_execution_materialization_capabilities",
    "agent_execution_runtime_capabilities",
    "apply_data_update",
    "assemble_attack_source",
    "assemble_periodic_source",
    "catalog_filter_from_plan",
    "catalog_filter_from_whiteboard",
    "character_panel_basis",
    "compose_equipment_plan",
    "deliver_plan_file",
    "detach_character",
    "enable_mechanics",
    "export_plan_result",
    "export_resolved_plan",
    "lint_agent_execution_materialization",
    "load_plan",
    "materialize_agent_execution",
    "new_agent_execution_materialization",
    "new_plan",
    "panel_reference_contract",
    "plan_capabilities",
    "prepare_attribute_review",
    "prepare_character_input",
    "preview_equipment_edit",
    "provisional_entity_approval_request",
    "provisional_entity_attestation_request",
    "provisional_entity_capabilities",
    "provisional_entity_usage_receipt",
    "reference_panel",
    "resolve_entity_ref",
    "run_plan_calc",
    "save_plan",
    "validate_agent_execution_materialization",
    "validate_external_damage_receipt",
    "validate_external_damage_result_receipt",
    "validate_plan",
    "validate_provisional_entity_usage_receipt",
    "whiteboard_action_admission_model_candidates",
    "whiteboard_action_capacity_model_candidates",
]
