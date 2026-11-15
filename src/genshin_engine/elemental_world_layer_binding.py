"""Independent finite layer/target receipt proof shared by preview and commit."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

from genshin_engine.elemental_world_star_planning_port import WorldStarPlanningPort

if TYPE_CHECKING:
    from genshin_engine.elemental import ElementalRuntime


def require_stellar_layer_binding(
    world: WorldStarPlanningPort,
    runtime: ElementalRuntime,
    receipt: dict,
    layer_event: dict | None,
    *,
    receipt_before: dict,
    trace_before: dict,
    target_snapshot_before: dict,
    target_id: str,
    event_id: str,
    issue_code: str,
) -> None:
    """Independently verify the public layer/receipt/trace binding.

    The mutation helper is intentionally observable for integration
    instrumentation, so the consumer cannot also trust that same
    helper as its verifier.  This local finite contract makes a
    replaced or no-op helper visible before any target or shared
    field state can commit.
    """
    field = "stellar_superconduct_application"
    trace_row = runtime.trace[-1] if runtime.trace else None
    receipt_value = receipt.get(field)
    trace_value = (
        trace_row.get(field) if isinstance(trace_row, dict) else None)
    receipt_without_layer = copy.deepcopy(receipt)
    receipt_without_layer.pop(field, None)
    trace_without_layer = copy.deepcopy(trace_row)
    if isinstance(trace_without_layer, dict):
        trace_without_layer.pop(field, None)
    target_snapshot_without_layer = world.target_commit_snapshot(
        runtime)
    if target_snapshot_without_layer["trace"]:
        target_snapshot_without_layer["trace"][-1].pop(field, None)
    if layer_event is None:
        valid = field not in receipt and (
            not isinstance(trace_row, dict) or field not in trace_row)
    else:
        valid = (
            receipt_value == layer_event
            and trace_value == layer_event
            and trace_row == receipt
        )
    valid = bool(
        valid
        and receipt_without_layer == receipt_before
        and trace_without_layer == trace_before
        and target_snapshot_without_layer == target_snapshot_before
    )
    if not valid:
        raise world.reaction_pending(
            issue_code,
            "风旋冰附着的星超导计层没有完整绑定到目标回执与轨迹。",
            {"target_id": target_id, "event_id": event_id,
             "expected_layer_event": copy.deepcopy(layer_event)},
        )

