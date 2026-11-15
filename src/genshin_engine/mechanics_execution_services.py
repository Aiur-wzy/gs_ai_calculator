"""Named read-only execution services supplied by the Mechanics owner.

Each property names an existing dependency. The view preserves late diagnostic
and fault hooks without importing or exposing the root runtime in leaf passes.
It stores no counters, buffers, clocks, game data or duplicate configuration.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MechanicsExecutionServices:
    _values: Mapping[str, Callable[[], Any]]

    @property
    def rotation_result(self) -> Any:
        return self._values["_base_result"]()

    @property
    def validate_mechanics_model(self) -> Any:
        return self._values["validate_mechanics_model"]()

    @property
    def validate_rotation_model(self) -> Any:
        return self._values["validate_rotation_model"]()

    @property
    def issue(self) -> Any:
        return self._values["_issue"]()

    @property
    def validate_template(self) -> Any:
        return self._values["validate_template"]()

    @property
    def is_swirl_v2(self) -> Any:
        return self._values["_swirl_v2"]()

    @property
    def tick(self) -> Any:
        return self._values["_tick"]()

    @property
    def project_inputs(self) -> Any:
        return self._values["_project"]()

    @property
    def validate_special_reaction_bindings(self) -> Any:
        return self._values["validate_special_reaction_bindings"]()

    @property
    def pool_runtime(self) -> Any:
        return self._values["pool_runtime"]()

    @property
    def counter_view_runtime(self) -> Any:
        return self._values["counter_view_runtime"]()

    @property
    def qualification_errors(self) -> Any:
        return self._values["qualification_errors"]()

    @property
    def possible_recipients(self) -> Any:
        return self._values["possible_recipients"]()

    @property
    def analyze_rotation(self) -> Any:
        return self._values["_semantic"]()

    @property
    def expand_rotation(self) -> Any:
        return self._values["_expand"]()

    @property
    def event_sort_key(self) -> Any:
        return self._values["_event_sort_key"]()

    @property
    def is_freeze_reference(self) -> Any:
        return self._values["_freeze_reference"]()

    @property
    def damage_types(self) -> Any:
        return self._values["DAMAGE_TYPES"]()

    @property
    def validate_state_runtime_bindings(self) -> Any:
        return self._values["validate_state_runtime_bindings"]()

    @property
    def action_types(self) -> Any:
        return self._values["ACTION_TYPES"]()

    @property
    def reserved_id(self) -> Any:
        return self._values["reserved_id"]()

    @property
    def route_event_id(self) -> Any:
        return self._values["route_event_id"]()

    @property
    def threshold_classic_rule_ids(self) -> Any:
        return self._values["_threshold_classic_rule_ids"]()

    @property
    def create_executor(self) -> Any:
        return self._values["_Mechanics"]()

    @property
    def create_state_runtime(self) -> Any:
        return self._values["StateRuntimeMechanics"]()

    @property
    def simulate(self) -> Any:
        return self._values["_simulate"]()

    @property
    def attach_shared_pool_report(self) -> Any:
        return self._values["_attach_shared_pool_report"]()

    @property
    def event_limit(self) -> Any:
        return self._values["MAX_MECHANICS_EVENTS"]()

    @property
    def attach_attribute_report(self) -> Any:
        return self._values["_attach_attribute_report"]()

    @property
    def attach_state_runtime_report(self) -> Any:
        return self._values["_attach_state_runtime_report"]()

    @property
    def attach_special_reaction_report(self) -> Any:
        return self._values["_attach_special_reaction_report"]()

    @property
    def attach_buff_state_timeline(self) -> Any:
        return self._values["_attach_buff_state_timeline"]()

    @property
    def calculate(self) -> Any:
        return self._values["calculate"]()

    @property
    def apply_special_damage_weights(self) -> Any:
        return self._values["_apply_special_damage_weights"]()

    @property
    def attribute_error(self) -> Any:
        return self._values["AttributeLedgerError"]()

    @property
    def candidate_sources_known(self) -> Any:
        return self._values["candidate_sources_known"]()

    @property
    def collect_damage_candidates(self) -> Any:
        return self._values["collect_damage_candidates"]()

    @property
    def classify_verified_empty_damage(self) -> Any:
        return self._values["classify_verified_empty_damage"]()

    @property
    def verified_empty_result(self) -> Any:
        return self._values["_verified_empty_result"]()

    @property
    def reference_contract(self) -> Any:
        return self._values["REFERENCE_CONTRACT"]()

    @property
    def damage_reporting_limit(self) -> Any:
        return self._values["DamageReportingLimit"]()

    @property
    def shared_pool_error(self) -> Any:
        return self._values["SharedPoolError"]()

    @property
    def state_runtime_error(self) -> Any:
        return self._values["StateRuntimeError"]()

    @property
    def elemental_pending(self) -> Any:
        return self._values["ElementalPending"]()

    @property
    def special_pending(self) -> Any:
        return self._values["SpecialReactionPending"]()

    @property
    def target_pending(self) -> Any:
        return self._values["TargetStatePending"]()

    @property
    def snapshot_pending(self) -> Any:
        return self._values["SnapshotCapturePending"]()

    @property
    def template_error(self) -> Any:
        return self._values["TemplateError"]()

    @property
    def capture_contract(self) -> Any:
        return self._values["capture_contract"]()

