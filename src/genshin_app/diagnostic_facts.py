"""Producer-owned diagnostic facts shared by collectors and read projections.

These fields describe a failure. They never change admission or turn an author's
claim into a trusted fact. Transport/display budgets belong to the consumer.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping

DIAGNOSTIC_FACT_FIELDS = (
    "actual", "expected", "input_ref", "expected_ref", "differences",
    "matched_fields", "conflicts", "occurrences", "source_refs", "recovery",
    "contract_ref", "checked_scope", "not_checked", "detail_ref",
    "engine_execution", "user_action", "ability_id", "does_not_prove",
)


def diagnostic_facts(source):
    """Select known facts without aliasing the producer's mutable payload."""
    if not isinstance(source, Mapping):
        return {}
    return {key: copy.deepcopy(source[key]) for key in DIAGNOSTIC_FACT_FIELDS if key in source}
