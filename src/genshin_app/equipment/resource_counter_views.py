"""Source-bound counter views and bounded, monotone execution-graph closure.

Legacy helpers still decide their own initial adoption. This optional closure
never reinterprets a dependency failure as a user's exclusion, and never revives
a component omitted by those helpers. Native plans and input receipts are read
only. Source identity alone does not authorize a new cap or unit conversion.
"""
from __future__ import annotations

import copy

from genshin_engine.resource_counter_view_runtime import (
    validate_bindings,
)

from .schema import PlanError


def validate_original_counter_views(model, character_ids):
    """Validate the complete view/counter graph before any execution pruning."""
    if "resource_counter_views" not in model:
        return
    checked = copy.deepcopy(model)
    for field, groups in (("resource_counter_views", ("views",)), ("resource_counters", ("counters",)),
                          ("shared_pools", ("pools", "operations", "consumers")),
                          ("state_runtime", ("lifecycles", "timers"))):
        for group in groups:
            for row in (checked.get(field) or {}).get(group, []):
                if row.get("status") == "pending": row["status"] = "assumed"
    issues = validate_bindings(checked, characters=sorted(character_ids))
    if issues:
        raise PlanError("资源读数完整原图结构、归属或收益引用无效", "invalid_counter_view_source", issues)


def prepare_resource_counter_views(model, rules, source_active, active, linked, excluded, missing, *,
                                   source_reviews, context_fingerprint, pools, state, counters,
                                   pool_adoptions, state_adoptions, counter_adoptions, character_ids):
    """Compatibility adapter; the shared graph retains the previous receipt shape."""
    from .resource_source_graph import prepare_resource_source_graph
    return prepare_resource_source_graph(model, rules, source_active, active, linked, excluded, missing,
        source_reviews=source_reviews, context_fingerprint=context_fingerprint, pools=pools, state=state,
        counters=counters, pool_adoptions=pool_adoptions, state_adoptions=state_adoptions,
        counter_adoptions=counter_adoptions, character_ids=character_ids)
