"""Native action-admission source schema and pre-query ownership validation."""
from __future__ import annotations

from genshin_runtime.action_admission_model import validate_selection

from .rotation_authoring import (
    ACTION_ADMISSION_SOURCE,
    compiler_field_error_details,
    has_compiler_field,
)


def obj(fields, required=None):
    return {"type": "object", "properties": fields,
            "required": list(fields) if required is None else required,
            "additionalProperties": False}


TEXT = {"type": "string", "minLength": 1, "maxLength": 128}
SHA = {"type": "string", "minLength": 64, "maxLength": 64, "pattern": "^[0-9a-f]{64}$"}
POS = {"type": "integer", "minimum": 1, "maximum": 2**53 - 1}
SELECTION_SCHEMA = obj({
    "character_id": POS,
    "skill_depot_id": POS,
    "skill_id": POS,
    "slot": {"enum": ["burst"]},
    "constellation": {"enum": [0]},
})
MODEL_REF_SCHEMA = obj({"id": TEXT, "revision": {"type": "integer", "minimum": 1, "maximum": 2**31 - 1}})
SOURCE_SCHEMA = obj({
    "id": TEXT,
    "ability_id": TEXT,
    "version_line": {"enum": ["release"]},
    "selection": SELECTION_SCHEMA,
    "variant": {"enum": ["default"]},
    "model_ref": MODEL_REF_SCHEMA,
    "accepted_fingerprint": {"anyOf": [SHA, {"type": "null"}]},
})
SOURCES_SCHEMA = {"type": "array", "items": SOURCE_SCHEMA, "minItems": 0, "maxItems": 64}
CONTEXT = {
    "contract": "static-action-admission-context-v1",
    "mode": "ordinary_open_world",
    "external_cooldown_effects": "none",
    "external_energy_cost_effects": "none",
}
CONTEXT_SCHEMA = obj({key: {"enum": [value]} for key, value in CONTEXT.items()})


def _reject(message, reason="action_admission_ownership", details=None):
    from .schema import PlanError

    raise PlanError(message, reason, details)


def validate_action_admission_bindings(plan):
    from genshin_runtime.action_root_semantics import bounded

    native = plan.get("rotation_model") or {}
    abilities = native.get("abilities", [])
    for index, ability in enumerate(abilities):
        if has_compiler_field(ability, ACTION_ADMISSION_SOURCE):
            _reject("action_admission_source只允许编译器生成，不是原生输入字段；请按适用范围选择action_admission_sources。",
                    details=compiler_field_error_details(ACTION_ADMISSION_SOURCE, index, ability.get("id")))
    bindings = plan.get("action_admission_sources", [])
    if not bindings:
        return
    if plan.get("plan_version") != 3 or type(bindings) is not list or len(bindings) > 64:
        _reject("动作接纳来源仅支持v3方案，最多64项")
    bounded(bindings)
    by_ability = {ability.get("id"): ability for ability in abilities}
    if len(by_ability) != len(abilities):
        _reject("动作接纳来源要求能力身份唯一")
    ids, owned, targets = set(), set(), set()
    old_resources = {item["ability_id"] for item in plan.get("action_resource_sources", [])}
    old_capacities = {aid for item in plan.get("action_capacity_sources", []) for aid in item["ability_ids"]}
    actions = native.get("actions", [])
    action_ids = {row["id"] for row in actions if row.get("ability_id") in {item.get("ability_id") for item in bindings}}
    if any(event.get("type") == "energy_spend" and event.get("action_id") in action_ids
           for event in native.get("events", [])):
        _reject("动作接纳来源自动扣能，不能保留同动作手工energy_spend", "duplicate_delivery")
    for binding in bindings:
        try:
            validate_selection(binding["selection"])
        except (KeyError, TypeError, ValueError) as exc:
            _reject(str(exc))
        ident, aid = binding["id"], binding["ability_id"]
        if ident in ids or aid in owned:
            _reject("动作接纳来源身份或能力重复拥有")
        ids.add(ident)
        owned.add(aid)
        target = (binding["version_line"], *[binding["selection"][key]
                  for key in ("character_id", "skill_depot_id", "skill_id", "slot", "constellation")], binding["variant"])
        if target in targets:
            _reject("动作接纳首片不支持同一技能的多能力别名", "action_admission_alias")
        targets.add(target)
        if aid in old_resources or aid in old_capacities:
            _reject("同一能力不能同时使用旧动作资源、旧容量来源与动作接纳v2", "duplicate_delivery")
        ability = by_ability.get(aid)
        if ability is None:
            _reject("动作接纳来源只能绑定已有能力", "reference")
        if ability.get("action_type_id") != "action.elemental_burst" or ability.get("variant") != binding["variant"]:
            _reject("原生能力动作类型或variant与动作接纳首片不符", "action_admission_scope")
        if ability.get("cooldown") is not None or ability.get("energy") is not None:
            _reject("动作接纳v2独占cooldown与energy；原生目标字段必须为null", "duplicate_delivery")


__all__ = [
    "CONTEXT",
    "CONTEXT_SCHEMA",
    "SOURCES_SCHEMA",
    "SOURCE_SCHEMA",
    "validate_action_admission_bindings",
]
