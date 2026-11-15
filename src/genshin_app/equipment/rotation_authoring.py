"""Native rotation authoring is a projection of the engine execution contract.

The three compiler-owned paths below are shared with the existing admission
guards. This module grants no source eligibility and compiles no game rule.
"""
from __future__ import annotations

import copy

ENERGY_DELIVERY = ("energy", "delivery")
ACTION_ADMISSION_SOURCE = ("action_admission_source",)
CAPACITY_SOURCE = ("cooldown", "capacity_source")
COMPILER_OWNED_PATHS = (ENERGY_DELIVERY, ACTION_ADMISSION_SOURCE, CAPACITY_SOURCE)
CONTRACT_ID = "native-rotation-authoring-v1"


def has_compiler_field(ability, path):
    """Presence, not truthiness: even null is not a native-owned marker."""
    value = ability
    for key in path[:-1]:
        if not isinstance(value, dict):
            return False
        value = value.get(key)
    return isinstance(value, dict) and path[-1] in value


def _remove_schema_path(schema, path):
    # Nested nullable objects use oneOf. Preserve all sibling constraints and
    # remove only properties already rejected by the native guards.
    for branch in schema.get("oneOf", []):
        _remove_schema_path(branch, path)
    properties = schema.get("properties", {})
    key = path[0]
    if len(path) > 1:
        if key in properties:
            _remove_schema_path(properties[key], path[1:])
    else:
        properties.pop(key, None)
        if key in schema.get("required", []):
            schema["required"].remove(key)


def native_rotation_schema(execution_schema=None):
    """Independent descriptive schema shared by every native-authoring view."""
    if execution_schema is None:
        from genshin_engine.rotation_schema import ROTATION_SCHEMA

        execution_schema = ROTATION_SCHEMA
    schema = copy.deepcopy(execution_schema)
    ability_schema = schema["properties"]["abilities"]["items"]
    for path in COMPILER_OWNED_PATHS:
        _remove_schema_path(ability_schema, path)
    schema["title"] = "原生方案动作与元素能量输入 v1（不含编译器私有字段）"
    return schema


def native_authoring_contract(execution_schema):
    return {
        "contract_id": CONTRACT_ID,
        "version": 1,
        "scope": "native_plan.rotation_model",
        "schema": native_rotation_schema(execution_schema),
        "structural_schema_only": True,
        "validation": "仍须通过原生方案验证与所选来源的范围、确认及字段所有权检查；结构合法不代表来源适用或机制完整。",
        "compiler_owned_fields": [
            {"path": "abilities[]." + ".".join(path), **_field_guidance(path)}
            for path in COMPILER_OWNED_PATHS
        ],
        "automatic_energy": {
            "native_input_field": "action_resource_sources[].energy_delivery",
            "values": ["existing_events", "action_start"],
            "default": "existing_events",
            "scope_reference": "plan_contract.action_resource_source_contract",
            "confirmation": "仅在既有基础资源来源范围内、当前来源与用途确认完成后编译；不扩大命座或变体范围。",
            "execution_field": "abilities[].energy.delivery",
            "native_preserved": True,
            "alternative_source_reference": "plan_contract.action_admission_source_contract",
            "duplicate_policy": "自动扣能的同一动作不得另交手工energy_spend。",
        },
        "manual_fallback": {
            "scope": "未选择自动来源，或当前情景超出已支持来源范围时",
            "ability_fields": ["abilities[].energy.required", "abilities[].energy.amount"],
            "spend_events": "events[]: type=energy_spend；明确action_id、character、t、order、repeat、required、amount及status/source。",
            "other_resource_events": ["energy_spawn", "energy_credit", "energy_direct", "cooldown_change"],
            "event_schema_pointer": "/properties/events/items/oneOf",
            "policy": "按已核实机制填写原生公开字段；显式维护产球、到账和固定回能。不得伪造来源适用性，也不得与同动作的自动交付重复扣能。",
        },
    }


def _field_guidance(path):
    if path == ENERGY_DELIVERY:
        return {"native_source": "action_resource_sources[].energy_delivery",
                "source_contract": "plan_contract.action_resource_source_contract",
                "recovery": "仅在该来源已支持并确认的范围内选择action_start；范围外用公开energy.required/amount与energy_spend事件。"}
    if path == ACTION_ADMISSION_SOURCE:
        return {"native_source": "action_admission_sources[]",
                "source_contract": "plan_contract.action_admission_source_contract",
                "recovery": "由已支持的动作接纳来源编译；不适用时按公开动作、冷却与能量字段输入，不写执行标记。"}
    if path == CAPACITY_SOURCE:
        return {"native_source": "action_capacity_sources[]",
                "source_contract": "plan_contract.action_capacity_source_contract",
                "recovery": "由已支持的次数来源编译；不适用时按已核实机制填写公开冷却与次数字段，不写执行标记。"}
    raise ValueError("Unknown compiler-owned path")


def compiler_field_error_details(path, index, ability_id):
    return {
        "ability_id": ability_id,
        "native_field_path": "/rotation_model/abilities/" + str(index) + "/" + "/".join(path),
        "native_authoring_contract": CONTRACT_ID,
        "schema_reference": "plan_contract.rotation_contract.native_authoring.schema",
        **_field_guidance(path),
    }


def app_rotation_capabilities():
    """Retain the independent engine schema and add the native writer view."""
    from genshin_engine.rotation import rotation_capabilities

    value = rotation_capabilities()
    value["schema_scope"] = "engine_execution_and_independent_rotation_check"
    value["native_authoring"] = native_authoring_contract(value["schema"])
    return value
