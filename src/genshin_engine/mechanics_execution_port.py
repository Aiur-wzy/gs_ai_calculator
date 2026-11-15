"""Structural execution port for Mechanics' shared gameplay state and services.

The existing root runtime implements this port directly. Leaf executors receive
that same object, so resource/attribute callbacks keep their original identity
and all mutable data remains in one execution graph. Public service delegates are
the only transition seam allowed to reach the older root helper names.
"""

from __future__ import annotations

from typing import Any, Protocol


class MechanicsExecutionPort(Protocol):
    event_emitter: Any
    state_runtime: Any
    queue: Any
    cancelled_actions: Any
    captures_by_action: Any
    pending_captures: Any
    snapshot_ids: Any
    attribute_callbacks: Any
    resource_reactions: Any
    issues: Any
    attribute_runtime: Any
    data: Any
    frontline: Any
    template: Any
    model: Any
    state: Any
    rules: Any
    windows: Any
    captures: Any
    snapshots: Any
    counter_views: Any
    samples: Any
    elemental: Any
    fixed_sources: Any
    matches: Any
    target_condition_checks: Any
    target_condition_trace: Any
    target_condition_trace_bytes: Any
    target_state_samples: Any
    target_status_runtime: Any
    attribute_scaling_receipts: Any
    hit_rows: Any
    projected_buffs: Any
    shared_pools: Any
    threshold_classic_rule_ids: Any
    trace: Any
    time: Any
    conditions: Any
    special_damage_groups: Any
    special_damage_echoes: Any
    ordinary_bloom_damage_windows: Any
    target_condition_check_limit: Any
    target_condition_trace_byte_limit: Any
    capture_bytes: Any
    capture_entries: Any
    capture_receipts: Any
    capture_trace: Any

    def snapshot(self, *args: Any, **kwargs: Any) -> Any: ...

    def manual_buffs(self, *args: Any, **kwargs: Any) -> Any: ...

    def filter(self, *args: Any, **kwargs: Any) -> Any: ...

    def value(self, *args: Any, **kwargs: Any) -> Any: ...

    def phase(self, *args: Any, **kwargs: Any) -> Any: ...

    def hit(self, *args: Any, **kwargs: Any) -> Any: ...

    def capture_reference(self, *args: Any, **kwargs: Any) -> Any: ...

    def ordinary_panel(self, *args: Any, **kwargs: Any) -> Any: ...

    def match_effect(self, *args: Any, **kwargs: Any) -> Any: ...

    def fixed_effects(self, *args: Any, **kwargs: Any) -> Any: ...

    def sample_stellar_superconduct(self, *args: Any, **kwargs: Any) -> Any: ...

    def stellar_superconduct_effects(self, *args: Any, **kwargs: Any) -> Any: ...

    def project_direct_lunar_damage(self, *args: Any, **kwargs: Any) -> Any: ...

    def project_direct_stellar_damage(self, *args: Any, **kwargs: Any) -> Any: ...

    def count_event(self, *args: Any, **kwargs: Any) -> Any: ...

    def record_special_event(self, *args: Any, **kwargs: Any) -> Any: ...

    def special_runtime(self, *args: Any, **kwargs: Any) -> Any: ...

    def emit_event(self, *args: Any, **kwargs: Any) -> Any: ...

    def verify_swirl_provider(self, *args: Any, **kwargs: Any) -> Any: ...

    def capture_event(self, *args: Any, **kwargs: Any) -> Any: ...

    def observed_snapshot(self, *args: Any, **kwargs: Any) -> Any: ...

    def apply_elemental(self, *args: Any, **kwargs: Any) -> Any: ...

    def check_reaction_package(self, *args: Any, **kwargs: Any) -> Any: ...

    def reaction_event(self, *args: Any, **kwargs: Any) -> Any: ...

    def project_swirl_damage(self, *args: Any, **kwargs: Any) -> Any: ...

    def drain_special_events(self, *args: Any, **kwargs: Any) -> Any: ...

    def activate_or_delay(self, *args: Any, **kwargs: Any) -> Any: ...

    def activate(self, *args: Any, **kwargs: Any) -> Any: ...

    def freeze_panel(self, *args: Any, **kwargs: Any) -> Any: ...

    def new_attribute_layers(self, *args: Any, **kwargs: Any) -> Any: ...

    def reserve_capture(self, *args: Any, **kwargs: Any) -> Any: ...

    def reserve_events(self, *args: Any, **kwargs: Any) -> Any: ...


__all__ = ["MechanicsExecutionPort"]

