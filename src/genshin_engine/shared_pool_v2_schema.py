"""Scaled atom accounting extension; no game clock or public runner opt-in."""
from __future__ import annotations

import copy

from genshin_engine.rotation_schema import arr
from genshin_engine.shared_pool_probability import RANDOM_EFFECT_SCHEMA
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
SCALED_OPERATION = copy.deepcopy(OPERATION)
SCALED_OPERATION['properties']['random_effect'] = RANDOM_EFFECT_SCHEMA
SPEND_OPERATION['properties']['random_effect'] = RANDOM_EFFECT_SCHEMA
SCALED_POOLS_SCHEMA = copy.deepcopy(SHARED_POOLS_SCHEMA)
SCALED_POOLS_SCHEMA["x-label"] = "计量资源（v2）"
SCALED_POOLS_SCHEMA["properties"].update(
    schema_version={"type": "integer", "const": 2},
    pools={**arr(SCALED_POOL), "maxItems": LIMITS["pools"]},
    operations={**arr({"oneOf": [{**SCALED_OPERATION, "x-label": "设置或补充"}, SPEND_OPERATION]}), "maxItems": LIMITS["operations"]})


def scaled_shared_pool_capabilities():
    return {"contract": "shared-atom-pools-v2", "schema": copy.deepcopy(SCALED_POOLS_SCHEMA),
            "execution": "pure_ledger_only", "quantity_unit": "atoms",
            "unit_scale": "每一显示单位的原子数；操作、消费者、余额和容量都填写原子整数。",
            "spend": "只按实际accepted扣量；all_available逐目标在真实操作时读取。",
            "random_effect": "默认expected：符合条件次数×chance，固定all_or_nothing的spend支持waive_spend，grant支持extra_grant及extra_amount；不枚举或抽样。小数资源用unit_scale表达，余额、容量、操作和消费者同时按相同比例换算，不能四舍五入。例：每单位100原子，成本100、概率0.34的免消耗期望扣66原子。特定情景由Agent询问用户后使用custom，以run_id/key指定outcomes；缺少或多余结果明确报错。先检查完整成本，再计算数量；期望数量下的容量、阈值及后续连段属于近似情景，不是整场随机机制的精确期望。",
            "cooldown": "普通spend成功指至少一个目标实际扣量；明确声明waive_spend且本次免消耗成功也算执行成功，并占用success冷却，实际消费仍为0。attempt包括不合格及空余额的非冷却尝试。",
            "scope": "未包含counter、连续时钟、生命周期资格或来源批准；不得由此推断主队列已开放。"}
