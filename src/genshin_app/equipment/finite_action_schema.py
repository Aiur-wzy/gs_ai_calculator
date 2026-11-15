"""Native, source-backed finite action sequences.

``rotation.cast_sequence`` describes repeated player inputs.  This contract
instead expands one semantic input action into a bounded, ordered set of
damage/application occurrences.  Components own database material; steps may
reuse a component without duplicating that ownership.
"""
from __future__ import annotations

import copy

from .periodic_schema import ID, KNOWN_BOOL, _object

KIND = "calibrated_finite_application_v1"
COMPILER = "calibrated-finite-actions-v1"
INTERRUPTION_POLICIES = ("none", "cancel_with_source_action", "scenario_required")

OFFSET = {"type": "number", "minimum": 0, "maximum": 3600,
          "multipleOf": .000001}
ORDER = {"type": "integer", "minimum": -10**9, "maximum": 10**9}

COMPONENT = _object({
    "channel_id": ID,
    "prototype_hit_id": ID,
    "application_profile_id": ID,
    "default_landed": KNOWN_BOOL,
    "self_sampling": {"enum": ["current", "action_start", "action_capture"]},
})

STEP = _object({
    "step_id": ID,
    "ordinal": {"type": "integer", "minimum": 1, "maximum": 256},
    "component_id": ID,
    "offset_seconds": OFFSET,
    "order": ORDER,
})

PROFILE = _object({
    "id": ID,
    "kind": {"enum": [KIND]},
    "variant": {"enum": ["default", "tap", "hold"]},
    "interruption_policy": {"enum": list(INTERRUPTION_POLICIES)},
    "channels": {"type": "array", "minItems": 1, "maxItems": 64,
                 "items": COMPONENT},
    "steps": {"type": "array", "minItems": 1, "maxItems": 256,
              "items": STEP},
    "terminal_step_id": {"anyOf": [ID, {"type": "null"}]},
    "accepted_profile_fingerprint": {"anyOf": [
        {"type": "null"}, {"type": "string", "pattern": r"^[0-9a-f]{64}$"}]},
})


def validate_finite_profiles(plan):
    """Validate topology invariants that JSON Schema cannot express."""
    from .schema import PlanError

    for profile in plan.get("action_event_profiles", []):
        if profile.get("kind") != KIND:
            continue
        channels = profile["channels"]
        channel_ids = [row["channel_id"] for row in channels]
        if len(channel_ids) != len(set(channel_ids)):
            raise PlanError("有限序列组件身份不能重复", "finite_action_schema")
        prototypes = [row["prototype_hit_id"] for row in channels]
        applications = [row["application_profile_id"] for row in channels]
        if (len(prototypes) != len(set(prototypes))
                or len(applications) != len(set(applications))):
            raise PlanError("有限序列组件须唯一拥有原型和附着槽位", "finite_action_schema")
        steps = profile["steps"]
        step_ids = [row["step_id"] for row in steps]
        ordinals = [row["ordinal"] for row in steps]
        if (len(step_ids) != len(set(step_ids))
                or len(ordinals) != len(set(ordinals))
                or set(ordinals) != set(range(1, len(steps) + 1))):
            raise PlanError("有限序列步骤身份须唯一且ordinal须从1连续", "finite_action_schema")
        if any(row["component_id"] not in set(channel_ids) for row in steps):
            raise PlanError("有限序列步骤引用不存在的组件", "finite_action_schema")
        if {row["component_id"] for row in steps} != set(channel_ids):
            raise PlanError("有限序列每个组件必须至少被一个步骤使用", "finite_action_schema")
        positions = [(round(row["offset_seconds"] * 1_000_000), row["order"])
                     for row in steps]
        if len(positions) != len(set(positions)):
            raise PlanError("有限序列同微秒步骤必须使用不同order", "finite_action_schema")
        chronological = sorted(steps, key=lambda row: (
            round(row["offset_seconds"] * 1_000_000), row["order"]))
        if [row["ordinal"] for row in chronological] != list(range(1, len(steps) + 1)):
            raise PlanError("有限序列ordinal必须遵循微秒时点与order顺序", "finite_action_schema")
        terminal = profile["terminal_step_id"]
        if terminal is not None:
            by_id = {row["step_id"]: row for row in steps}
            if terminal not in by_id:
                raise PlanError("有限序列终结步骤不存在", "finite_action_schema")
            last = max(steps, key=lambda row: (
                round(row["offset_seconds"] * 1_000_000), row["order"], row["ordinal"]))
            if terminal != last["step_id"]:
                raise PlanError("有限序列终结步骤必须是最后一个有序步骤", "finite_action_schema")


def finite_action_capabilities():
    return {
        "compiler": COMPILER,
        "profile_kind": KIND,
        "profile_schema": copy.deepcopy(PROFILE),
        "max_components": 64,
        "max_steps": 256,
        "supported_interruption_policy": "none",
        "component_semantics": "unique source ownership",
        "step_semantics": (
            "ordered occurrence; components may repeat; step.order is local topology order "
            "within one action and one offset, never a scenario-global queue order"
        ),
        "generated_order_fields": {
            "topology_order": "reviewed local step.order",
            "order": "compiler-assigned scenario-global queue order",
        },
    }
