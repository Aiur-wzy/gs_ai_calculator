"""Opt-in finite capacity bindings and actual equipment declarations."""
from __future__ import annotations

from genshin_runtime.action_capacity_model import CONTEXT_CONTRACT

from .rotation_authoring import (
    CAPACITY_SOURCE,
    compiler_field_error_details,
    has_compiler_field,
)


def obj(fields, required=None):
    return {"type": "object", "properties": fields, "required": list(fields) if required is None else required, "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 128}
SHA = {"type": "string", "minLength": 64, "maxLength": 64, "pattern": "^[0-9a-f]{64}$"}
POS = {"type": "integer", "minimum": 1, "maximum": 2**53 - 1}
SOURCE_SCHEMA = obj({"id": TEXT, "actor": TEXT, "ability_ids": {"type": "array", "items": TEXT, "minItems": 1, "maxItems": 64},
    "version_line": {"enum": ["release", "beta"]}, "model_ref": obj({"id": TEXT, "revision": {"type": "integer", "minimum": 1, "maximum": 2**31 - 1}}),
    "target": obj({"character_id": POS, "skill_depot_id": POS, "skill_id": POS, "variant": {"enum": ["default", "tap", "hold"]}}),
    "accepted_fingerprint": {"anyOf": [SHA, {"type": "null"}]}})
SOURCES_SCHEMA = {"type": "array", "items": SOURCE_SCHEMA, "minItems": 0, "maxItems": 64}
CONTEXT_SCHEMA = obj({"contract": {"enum": [CONTEXT_CONTRACT]}, "mode": {"enum": ["ordinary_open_world"]},
                      "external_capacity_effects": {"enum": ["none"]}, "window_changes": {"enum": ["none"]}})
SLOTS = ("EQUIP_BRACER", "EQUIP_NECKLACE", "EQUIP_SHOES", "EQUIP_RING", "EQUIP_DRESS")
PRESENCE_SCHEMA = obj({slot: {"enum": ["empty", "equipped", "unknown"]} for slot in SLOTS})
PROGRESSION_ADDITIONS = {"constellation": {"type": "integer", "minimum": 0, "maximum": 6},
    "talent_base_levels": obj({key: {"type": "integer", "minimum": 1, "maximum": 10} for key in ("normal", "skill", "burst")}, [])}


def validate_capacity_bindings(plan):
    from genshin_runtime.action_root_semantics import bounded

    from .schema import PlanError
    native = plan.get("rotation_model") or {}
    for index, ability in enumerate(native.get("abilities", [])):
        if has_compiler_field(ability, CAPACITY_SOURCE):
            raise PlanError("capacity_source只允许编译器生成，不是原生输入字段；请按适用范围选择action_capacity_sources。",
                            details=compiler_field_error_details(CAPACITY_SOURCE, index, ability.get("id")))
    if "action_capacity_sources" not in plan:
        return
    bindings = plan["action_capacity_sources"]
    if plan["plan_version"] != 3 or type(bindings) is not list or len(bindings) > 64:
        raise PlanError("次数来源仅支持v3方案，最多64项")
    bounded(bindings)
    ids, abilities = set(), set()
    for binding in bindings:
        # The App's old generic walker does not enforce maxItems/pattern.
        from .action_event_schema import _check
        errors = _check(binding, SOURCE_SCHEMA, "action_capacity_sources")
        if errors:
            raise PlanError("次数来源结构不合法", details=errors)
        aids = binding["ability_ids"]
        if binding["id"] in ids or len(set(aids)) != len(aids) or abilities.intersection(aids):
            raise PlanError("次数来源身份或本地能力重复拥有")
        ids.add(binding["id"])
        abilities.update(aids)
    if bindings and "action_capacity_context" not in plan:
        raise PlanError("次数来源必须显式声明计算场景")


def validate_capacity_equipment(gear):
    """Contradictory optional input is invalid even before choosing a model."""
    from .schema import PlanError
    presence = gear.get("artifact_presence")
    if presence is not None:
        if gear.get("mode") != "panel" or type(presence) is not dict or set(presence) != set(SLOTS):
            raise PlanError("五槽存在状态只用于面板模式且须明确全部槽位")
        if any(x not in ("empty", "equipped", "unknown") for x in presence.values()):
            raise PlanError("圣遗物存在状态无效")
        count = sum(x["count"] for x in gear["artifact_sets"])
        if count > sum(x != "empty" for x in presence.values()):
            raise PlanError("套装件数与实际空槽声明冲突")
    weapon = gear.get("weapon") or {}
    if "refinement" in weapon:
        r = weapon["refinement"]
        if type(r) is not int or not 1 <= r <= 5 or any(item.get("refinement") not in (None, r) for item in weapon["refinements"]):
            raise PlanError("武器整体精炼与属性槽精炼冲突")
