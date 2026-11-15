"""Scaled atom accounting extension; no game clock or public runner opt-in."""
from __future__ import annotations

import copy

from genshin_engine.rotation_schema import arr
from genshin_engine.shared_pool_schema import (
    COUNT,
    LIMITS,
    OPERATION,
    POOL,
    SHARED_POOLS_SCHEMA,
)

SCALED_POOL = copy.deepcopy(POOL)
SCALED_POOL["properties"]["unit_scale"] = {"type": "integer", "minimum": 1, "maximum": 1000000}
SCALED_POOL["required"].append("unit_scale")
SPEND_OPERATION = copy.deepcopy(OPERATION)
SPEND_OPERATION["properties"].update(
    operation={"const": "spend"},
    amount={"oneOf": [{**COUNT, "minimum": 1, "x-label": "固定原子数"},
                      {"type": "string", "const": "all_available", "x-label": "全部可用资源"}]},
    spend_policy={"enum": ["all_or_nothing", "up_to_available"]})
SPEND_OPERATION["required"].append("spend_policy")
SPEND_OPERATION["x-label"] = "扣除资源"
SCALED_POOLS_SCHEMA = copy.deepcopy(SHARED_POOLS_SCHEMA)
SCALED_POOLS_SCHEMA["x-label"] = "计量资源（v2）"
SCALED_POOLS_SCHEMA["properties"].update(
    schema_version={"type": "integer", "const": 2},
    pools={**arr(SCALED_POOL), "maxItems": LIMITS["pools"]},
    operations={**arr({"oneOf": [{**copy.deepcopy(OPERATION), "x-label": "设置或补充"}, SPEND_OPERATION]}), "maxItems": LIMITS["operations"]})


def scaled_shared_pool_capabilities():
    return {"contract": "shared-atom-pools-v2", "schema": copy.deepcopy(SCALED_POOLS_SCHEMA),
            "execution": "pure_ledger_only", "quantity_unit": "atoms",
            "unit_scale": "每一显示单位的原子数；操作、消费者、余额和容量都填写原子整数。",
            "spend": "只按实际accepted扣量；all_available逐目标在真实操作时读取。",
            "cooldown": "spend成功仅指至少一个目标实际扣量；attempt包括不合格及空余额的非冷却尝试。",
            "scope": "未包含counter、连续时钟、生命周期资格或来源批准；不得由此推断主队列已开放。"}
