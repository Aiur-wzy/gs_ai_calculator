"""V4 closed five-scalar graph; older contracts retain their original meanings."""
from __future__ import annotations

import copy

from genshin_engine.attribute_budget import (
    DEFAULT_LIMITS,
    attribute_budget_capabilities,
    limits_for_schema,
)
from genshin_engine.attribute_graph import GRAPH_LIMITS, LINEAR
from genshin_engine.attribute_schema import CONTRIBUTION, FRAME, INITIAL, LIMITS
from genshin_engine.attribute_stats import CONTRACT, STATS, attribute_stat_definitions
from genshin_engine.rotation_schema import arr, obj

LINEAR_V4 = copy.deepcopy(LINEAR)
LINEAR_V4["properties"]["input"]["properties"]["stat"] = {"enum": list(STATS)}
INITIAL_V4 = copy.deepcopy(INITIAL)
INITIAL_V4["properties"]["stat"] = {"enum": list(STATS)}
CONTRIBUTION_V4 = copy.deepcopy(CONTRIBUTION)
CONTRIBUTION_V4["properties"].update(
    stat={"enum": list(STATS)}, value={"oneOf": [{"type": "number"}, LINEAR_V4]},
    value_unit={"enum": ["flat", "ratio"]},
    application=obj({"kind": {"enum": ["flat", "recipient_base_fraction"]}}, ["kind"]))
CONTRIBUTION_V4["required"].extend(["value_unit", "application"])
GRAPH_SCHEMA_V4 = obj({"contract": {"const": CONTRACT}, "initials": arr(INITIAL_V4),
                      "contributions": arr(CONTRIBUTION_V4), "frames": arr(copy.deepcopy(FRAME))},
                     ["contract", "initials", "contributions", "frames"])


def attribute_multistat_capabilities():
    return {**attribute_stat_definitions(), "schema": limits_for_schema(GRAPH_SCHEMA_V4, DEFAULT_LIMITS),
            "limits": {**LIMITS, **GRAPH_LIMITS}, "budget": attribute_budget_capabilities(),
            "runtime_integration": "mechanics_explicit_attribute_model_v4_qualified_projection",
            "frame_authority": "caller_supplied_frame", "applications": ["flat", "recipient_base_fraction"],
            "unsupported": ["ordinary_reads_of_damage_projection", "arbitrary_code", "piecewise", "source_approval"]}
