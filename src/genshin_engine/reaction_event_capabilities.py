"""Existing reaction producer/listener capabilities, without source authority.

These checks describe dispatch requirements, not whether any reaction occurs in
the input window. A damage effect's pre filter is not a dispatched pre listener.
Full model/schema validation remains the caller's responsibility.
"""
from __future__ import annotations

from genshin_engine.elemental_schema import ELEMENTAL_SCHEMA, SWIRL_RULESETS

OBSERVABLE_REACTION_TYPES = (
    "vaporize", "melt", "swirl", "freeze", "crystallize", "stellar_superconduct", "stellar_swirl",
    "lunar_electro", "lunar_bloom", "lunar_crystallize",
)


def reaction_event_available(model, event):
    if event == "elemental_reaction":
        return "elemental_model" in model
    if event == "reaction_damage":
        return model.get("elemental_model", {}).get("ruleset") in SWIRL_RULESETS
    return True


def reaction_listener_phase_supported(selector):
    return selector.get("event") not in ("elemental_reaction", "reaction_damage") or selector["phase"] == "post"


def reaction_listener_capabilities():
    return {
        "contract": "reaction-listener-capabilities-v1",
        "applies_to": ["mechanics_model.rules[*].trigger", "mechanics_model.shared_pools.operations[*].trigger"],
        "events": {
            "elemental_reaction": {"phases": ["post"], "requires": "mechanics_model.elemental_model",
                "rulesets": list(ELEMENTAL_SCHEMA["properties"]["ruleset"]["enum"])},
            "reaction_damage": {"phases": ["post"], "requires": "mechanics_model.elemental_model",
                "rulesets": list(SWIRL_RULESETS)},
        },
        "effect_filter": "收益筛选是独立用途；reaction_damage/pre可用于合法出伤收益筛选，不是pre监听。",
        "quota_consumption": "quota.consume支持hit/pre或实际reaction_damage/pre；消费在真实伤害入口执行，不新增pre触发监听。",
        "zero_occurrences": "完整模型本窗口没有实际反应是合法零触发；缺模型不能借手填反应倍率代替。",
        "authority": "仅声明实际事件产生与监听能力；不证明反应必然发生、数据库来源或游戏资格。",
    }
