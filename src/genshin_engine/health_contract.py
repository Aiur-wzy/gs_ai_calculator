"""Explicit actor health inputs; no character knowledge or enemy AI."""
from genshin_engine.rotation_schema import EVIDENCE, REPEAT, STRING, arr, obj

NONNEG = {"type": "number", "minimum": 0}
RATIO = {"type": "number", "minimum": 0, "maximum": 1}
QUANTITIES = ["current_hp", "max_hp", "hp_fraction", "bond", "bond_fraction",
              "hp_gained", "hp_lost", "hp_change_fraction", "overheal", "bond_cleared"]
HEALTH_EXPR = obj({
    "kind": {"const": "health"}, "subject": {"enum": ["owner", "recipient", "team"]},
    "quantity": {"enum": QUANTITIES}, "factor": {"type": "number"},
    "add": {"type": "number"}, "min": {"type": "number"}, "max": {"type": "number"},
    "sample": {"enum": ["current", "trigger", "event"]}, "event_id": STRING,
}, ["kind", "subject", "quantity", "factor", "sample"])
HEALTH_AMOUNT = obj({
    "basis": {"enum": ["flat", "current_hp", "max_hp", "source_atk", "source_hp", "source_def", "source_em"]},
    "factor": NONNEG, "add": NONNEG,
}, ["basis", "factor"])
HEALTH_EVENT = obj({
    "id": STRING, "t": NONNEG, "order": {"type": "integer"}, "repeat": REPEAT,
    "character": STRING, "source_character": STRING, "action_id": STRING,
    "operation": {"enum": ["heal", "hp_loss", "hp_damage", "bond_grant", "bond_reduce", "reset_counters"]},
    "amount": HEALTH_AMOUNT, "remaining_hp_floor": NONNEG,
    "healing_multiplier": NONNEG, "bypass_bond": {"type": "boolean"},
    "allow_hp_restore": {"type": "boolean"}, **EVIDENCE,
}, ["id", "t", "order", "repeat", "character", "operation", "amount", *EVIDENCE])
HEALTH_SCHEMA = obj({
    "schema_version": {"const": 1},
    "characters": arr(obj({
        "id": STRING, "initial_hp_fraction": RATIO, "initial_bond_fraction": NONNEG,
        "max_hp_change_policy": {"enum": ["preserve_fraction", "preserve_amount"]},
        "bond_max_hp_policy": {"enum": ["preserve_fraction", "preserve_amount"]},
        **EVIDENCE,
    }, ["id", "initial_hp_fraction", "initial_bond_fraction", "max_hp_change_policy", "bond_max_hp_policy", *EVIDENCE])),
    "events": arr(HEALTH_EVENT),
    "conditions": arr(obj({
        "id": STRING, "character": STRING, "quantity": {"enum": QUANTITIES},
        "operator": {"enum": ["lt", "le", "eq", "ge", "gt"]}, "threshold": NONNEG,
        **EVIDENCE,
    }, ["id", "character", "quantity", "operator", "threshold", *EVIDENCE])),
}, ["schema_version", "characters", "events", "conditions"])


def health_capabilities():
    return {"contract": "actor-health-v1", "schema": HEALTH_SCHEMA,
            "expression": HEALTH_EXPR, "result_field": "health_ledger",
            "ordering": "shared_mechanics_queue_time_order",
            "notes": ["hp属性仍是最大生命，current_hp单独记账。",
                "按已声明数值处理治疗、生命之契抵扣、耗血与实际扣血；hp_damage是已完成护盾及减伤处理的HP扣除量。",
                "治疗先抵扣生命之契，再恢复存活角色生命；特殊效果显式声明bypass_bond/allow_hp_restore并注明来源。",
                "生命上限变化按显式policy同步，不冒充治疗或耗血，不增加生命变化累计。",
                "health_change触发可供通用增益及资源规则监听；health表达式可读取实际累计或生命比例。",
                "不生成敌方攻击、护盾、闪避、暴击随机分支、死亡复苏或角色专属规则。"]}
