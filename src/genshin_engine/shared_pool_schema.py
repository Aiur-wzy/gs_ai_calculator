"""Closed integer-pool contract; no scheduler, damage, or source approval."""
from __future__ import annotations

import copy

from genshin_engine.rotation_schema import ACTION_TYPES, ELEMENTS, arr, obj

LIMITS = {"pools": 64, "operations": 256, "consumers": 128, "characters": 16,
          "count": 1000000, "declaration_bytes": 256 * 1024, "receipts": 40000,
          "receipt_bytes": 32 * 1024 * 1024, "time_us": 10**15}
ID = {"type": "string", "minLength": 1, "maxLength": 128}
COUNT = {"type": "integer", "minimum": 0, "maximum": LIMITS["count"]}
CAPACITY = {"oneOf": [
    {**COUNT, "minimum": 1, "x-label": "有限玩法容量"},
    {"type": "string", "const": "unbounded", "x-label": "无玩法容量上限"},
]}
EVIDENCE = {"status": {"enum": ["confirmed", "assumed", "pending"]},
            "source": {"type": "string", "minLength": 1, "maxLength": 4096}}
SELECTOR = obj({
    "event": {"enum": ["action_start", "action_end", "switch", "hit", "condition", "elemental_reaction", "reaction_damage"]},
    "phase": {"enum": ["pre", "post"]},
    **{k: {**arr(ID), "maxItems": 128} for k in ("action_ids", "event_ids", "characters")},
    "action_type_ids": arr({"enum": list(ACTION_TYPES)}),
    "damage_type_ids": arr(ID),
    "elements": arr({"enum": [*ELEMENTS, "physical"]}),
    "reaction_types": arr({"enum": ["vaporize", "melt", "swirl", "freeze"]}),
    "reaction_elements": arr({"enum": ELEMENTS}), "condition": ID,
    "owner_presence": {"enum": ["frontline", "any"]},
}, ["event", "phase"])
POOL = obj({"id": ID, "name_zh": ID, "owner": ID, "source_id": ID,
            "scope": {"enum": ["team", "per_character"]}, "capacity": CAPACITY,
            "initial": {"type": "object", "additionalProperties": COUNT}, **EVIDENCE},
           ["id", "name_zh", "owner", "source_id", "scope", "capacity", "initial", "status", "source"])
OPERATION = obj({"id": ID, "pool_id": ID, "source_id": ID, "trigger": SELECTOR,
                 "order": {"type": "integer", "minimum": 0, "maximum": 1000000},
                 "operation": {"enum": ["set", "grant"]}, "amount": COUNT,
                 "targets": obj({"mode": {"enum": ["all_pool_keys", "characters"]},
                                 "characters": {**arr(ID), "minItems": 1, "maxItems": LIMITS["characters"]}}, ["mode"]),
                 "cooldown": obj({"seconds": {"type": "number", "minimum": 0, "maximum": 10**9},
                                  "anchor": {"enum": ["attempt", "success"]}}, ["seconds", "anchor"]),
                 **EVIDENCE},
                ["id", "pool_id", "source_id", "trigger", "order", "operation", "amount", "targets", "cooldown", "status", "source"])
CONSUMER = obj({"id": ID, "pool_id": ID, "source_id": ID, "filter": SELECTOR,
                "actor_relation": {"enum": ["owner", "frontline", "other_frontline", "party_member"]},
                "damage_system": {"const": "classic"}, "amount": {**COUNT, "minimum": 1},
                "eligibility_rule_id": ID, **EVIDENCE},
               ["id", "pool_id", "source_id", "filter", "actor_relation", "damage_system", "amount", "eligibility_rule_id", "status", "source"])
SHARED_POOLS_SCHEMA = obj({"schema_version": {"type": "integer", "const": 1},
                           "pools": {**arr(POOL), "maxItems": LIMITS["pools"]},
                           "operations": {**arr(OPERATION), "maxItems": LIMITS["operations"]},
                           "consumers": {**arr(CONSUMER), "maxItems": LIMITS["consumers"]}},
                          ["schema_version", "pools", "operations", "consumers"])
SHARED_POOLS_SCHEMA["x-label"] = "整数次数（v1）"
OPERATION["x-label"] = "设置或补充"


def shared_pool_capabilities():
    return {"contract": "shared-integer-pools-v1", "schema": copy.deepcopy(SHARED_POOLS_SCHEMA),
            "limits": dict(LIMITS), "unit": "integer_count", "upper_layer_qualification": "not_evaluated",
            "scope": "纯整数账；不会判断FILTER、前台关系、活跃收益、命中资格或数据库证据。",
            "run_id": "一次实际队列事件出现的唯一ID，不是轴轮次；同名事件跨轮须不同run_id。",
            "duplicate_policy": "同consumer和run_id重复读取返回同一回执；改变身份或资格拒绝；重复operation拒绝。",
            "order": "同池同phase操作order全局唯一；同实际事件按order递增调用，运行时复核。",
            "cooldown": "attempt对不合格尝试也起算；success在合格操作提交时起算，封顶零收益仍属于合格提交。",
            "capacity": "正整数表示玩法容量并截断set/grant；unbounded表示没有玩法容量上限，仍受工程整数安全界保护且越界显式失败。旧有限池回执缺省capacity_mode视为bounded。",
            "compatibility": "本函数描述纯账本接口；mechanics_model.shared_pools另由执行器核对资格并调度。旧quota无变化；scale和生命周期暂不接受。"}
