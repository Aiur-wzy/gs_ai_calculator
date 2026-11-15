"""Bound optional observations before a fresh result leaves its producer.

The callback only constructs private proof candidates from one already-executed
draft. It must never execute the rotation or publish a new user approval. A
delivery/restore operation never enters this path and cannot repair a sealed
package. At most three candidates are encoded, independent of event count.
"""
from __future__ import annotations

from .calculation_wire import calculation_wire_sizes
from .files import json_bytes
from .schema import PlanError


def finish_fresh_result(build_candidate):
    from genshin_app.calculation_delivery_files import LIMITS

    maximum = LIMITS["calculated_bytes"]
    complete = build_candidate(None)
    complete_sizes = calculation_wire_sizes(complete)
    if max(complete_sizes.values()) <= maximum:
        return complete

    # The second private candidate measures all real mandatory proofs and the
    # minimum partial header, not a guessed fixed reserve. It is also a safe
    # final fallback if the single interpolation attempt does not fit.
    minimum = build_candidate(0)
    minimum_sizes = calculation_wire_sizes(minimum)
    minimum_size = max(minimum_sizes.values())
    if minimum_size > maximum:
        raise PlanError("完整计算及必需证明超过产物字节预算", "delivery_size_limit", {
            "role": max(minimum_sizes, key=minimum_sizes.get),
            "bytes": minimum_size, "limit": maximum,
            "minimum_partial_bytes": minimum_sizes,
            "complete_bytes": complete_sizes,
            "optional_explanation_reduction_exhausted": True})

    full_document = len(json_bytes(complete["result"]["damage_explanations"]))
    min_document = len(json_bytes(minimum["result"]["damage_explanations"]))
    growth = max(complete_sizes.values()) - minimum_size
    available = maximum - minimum_size
    if growth <= 0 or available <= 0 or full_document <= min_document:
        return minimum
    document_target = min_document + (full_document - min_document) * available // growth
    reduced = build_candidate(document_target)
    # Shared source tables, pretty-print indentation and proof metadata need
    # not grow linearly. The real writers, not the interpolation, decide fit.
    return reduced if max(calculation_wire_sizes(reduced).values()) <= maximum else minimum
