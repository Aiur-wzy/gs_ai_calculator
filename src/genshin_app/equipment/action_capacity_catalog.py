"""Read-only capacity-model discovery derived from a validated native plan.

This layer owns no capacity value and grants no execution authority.  It only
turns fields already present in the complete plan into the typed, partial
catalog query understood by :mod:`genshin_db.runtime_action_capacity`.
"""
from __future__ import annotations

import copy

from genshin_db.runtime_action_capacity import (
    CATALOG_PAGE_CONTRACT,
    CATALOG_QUERY_CONTRACT,
    DEFAULT_CATALOG_PAGE_SIZE,
    MAX_CATALOG_PAGE_SIZE,
    list_action_capacity_model_candidates,
)
from genshin_runtime.action_capacity_model import validate_context

from .action_capacity_schema import SLOTS
from .schema import PlanError, validate_plan

_CATALOG_PATHS = {
    "artifacts",
    "context",
    "progression.ascension",
    "progression.base_level",
    "progression.character_level",
    "progression.constellation",
    "target.skill_depot_id",
    "target.skill_id",
    "target.variant",
    "team",
    "weapon.id",
    "weapon.level",
    "weapon.refinement",
}
_VARIANTS = {"default", "tap", "hold"}


def _fail(message, path=None):
    details = {"path": path} if path is not None else []
    raise PlanError(message, "capacity_catalog_input", details)


def _observed_paths(observed):
    paths = set()
    for group in ("target", "progression", "weapon"):
        paths.update(f"{group}.{key}" for key in observed.get(group, {}))
    paths.update(key for key in ("artifacts", "team", "context") if key in observed)
    return paths


def _selected_abilities(plan, actor_id, ability_ids):
    if type(actor_id) is not str or not actor_id.strip():
        _fail("容量目录需要非空的本地角色ID", "actor_id")
    if (type(ability_ids) is not list or not ability_ids or len(ability_ids) > 64
            or any(type(value) is not str or not value.strip() for value in ability_ids)):
        _fail("容量目录需要1至64个非空本地能力ID", "ability_ids")
    if len(set(ability_ids)) != len(ability_ids):
        _fail("容量目录能力ID不能重复", "ability_ids")
    rotation = plan.get("rotation_model")
    if type(rotation) is not dict:
        _fail("容量目录需要完整动作模型", "rotation_model")
    indexed = {}
    for ability in rotation.get("abilities", []):
        ident = ability.get("id")
        if ident in indexed:
            _fail("动作模型能力ID不唯一", f"rotation_model.abilities.{ident}")
        indexed[ident] = ability
    selected = []
    for ident in ability_ids:
        ability = indexed.get(ident)
        if ability is None:
            _fail("容量目录所选能力不存在", f"rotation_model.abilities.{ident}")
        if ability.get("character") != actor_id:
            _fail("容量目录所选能力必须属于所选角色", f"rotation_model.abilities.{ident}.character")
        if ability.get("action_type_id") != "action.elemental_skill":
            _fail("容量目录只接受元素战技能力", f"rotation_model.abilities.{ident}.action_type_id")
        selected.append(ability)
    return selected


def _typed_target(plan, actor_id, ability_ids, character_id, version_line,
                  progression, ability_variants):
    """Validate every known fact; only complete coverage may fill an identity.

    Human labels in hits/actions deliberately do not enter this function.
    Partial typed sources are still conflict evidence, but are not evidence
    that all selected aliases share one skill.
    """
    declared = []
    selected_ids = set(ability_ids)
    intersecting = [
        binding for binding in plan.get("action_capacity_sources", [])
        if selected_ids & set(binding["ability_ids"])
    ]
    if intersecting:
        if (len(intersecting) != 1
                or set(intersecting[0]["ability_ids"]) != selected_ids):
            _fail("所选能力必须精确对应单一已有次数池", "ability_ids")
        binding = intersecting[0]
        if binding["actor"] != actor_id or binding["version_line"] != version_line:
            _fail("已有次数来源与目录角色或资料线不一致", f"action_capacity_sources.{binding['id']}")
        target = binding["target"]
        if target["character_id"] != character_id:
            _fail("已有次数来源与目录数据库角色不一致", f"action_capacity_sources.{binding['id']}.target")
        declared.append({key: target[key] for key in ("skill_depot_id", "skill_id", "variant")})

    known = {}

    def merge(field, value, path):
        if value is None:
            return
        if field in known and known[field] != value:
            message = ("容量目录共池能力变体不一致" if field == "variant"
                       else "容量目录已有typed来源彼此矛盾")
            _fail(message, path)
        known[field] = value

    capacity_target = declared[0] if declared else None
    if capacity_target:
        for field, value in capacity_target.items():
            merge(field, value, f"action_capacity_sources.target.{field}")

    sources = {row["ability_id"]: row for row in plan.get("action_resource_sources", [])}
    selected = [sources.get(ident) for ident in ability_ids]
    complete_resource_identity = all(selected)
    for source in selected:
        if source is None:
            continue
        selection = source["selection"]
        base_path = f"action_resource_sources.{source['ability_id']}"
        if source["version_line"] != version_line or selection["character_id"] != character_id:
            _fail("动作资源typed身份与目录角色不一致", base_path)
        if "constellation" in progression and selection["constellation"] != progression["constellation"]:
            _fail("动作资源typed命座与角色成长不一致", f"{base_path}.selection.constellation")
        if source["variant"] not in _VARIANTS:
            _fail("动作资源typed来源变体不在支持范围", f"{base_path}.variant")
        merge("skill_depot_id", selection.get("skill_depot_id"),
              f"{base_path}.selection.skill_depot_id")
        merge("skill_id", selection.get("skill_id"), f"{base_path}.selection.skill_id")
        merge("variant", source["variant"], f"{base_path}.variant")
        complete_resource_identity = complete_resource_identity and (
            "skill_depot_id" in selection and "skill_id" in selection)

    for index, variant in enumerate(ability_variants):
        merge("variant", variant, f"rotation_model.abilities.{ability_ids[index]}.variant")

    if capacity_target:
        return capacity_target
    if complete_resource_identity:
        return {key: known[key] for key in ("skill_depot_id", "skill_id", "variant")}
    return {}


def _artifact_state(gear):
    if gear.get("mode") == "panel":
        sets = gear.get("artifact_sets")
        presence = gear.get("artifact_presence")
        if type(sets) is list and sets:
            return "not_all_empty"
        if type(presence) is dict and set(presence) == set(SLOTS):
            states = list(presence.values())
            if any(state == "equipped" for state in states):
                return "not_all_empty"
            if not sets and all(state == "empty" for state in states):
                return "all_empty"
        return None
    artifacts = gear.get("artifacts")
    if type(artifacts) is not dict or set(artifacts) != set(SLOTS):
        return None
    states = [value.get("state") if type(value) is dict else None for value in artifacts.values()]
    if any(state == "equipped" for state in states):
        return "not_all_empty"
    if all(state == "empty" for state in states):
        return "all_empty"
    return None


def catalog_filter_from_plan(plan, actor_id, ability_ids):
    """Return only document-observed catalog fields plus exact unknown paths."""
    validate_plan(plan)
    selected = _selected_abilities(plan, actor_id, ability_ids)

    roster = [row for row in plan["manual_template"]["characters"] if row.get("id") == actor_id]
    if len(roster) != 1:
        _fail("容量目录角色必须在完整队伍中存在且唯一", f"manual_template.characters.{actor_id}")
    gear = plan.get("equipment_inputs", {}).get(actor_id)
    if type(gear) is not dict:
        _fail("容量目录需要所选角色的实际配装声明", f"equipment_inputs.{actor_id}")
    character_ref = gear.get("character_ref")
    if (type(character_ref) is not dict
            or type(character_ref.get("db_character_id")) is not int
            or character_ref.get("version_line") not in ("release", "beta")):
        _fail("容量目录需要精确数据库角色ID与资料线", f"equipment_inputs.{actor_id}.character_ref")
    character_id = character_ref["db_character_id"]
    version_line = character_ref["version_line"]

    progression_input = gear.get("progression") if type(gear.get("progression")) is dict else {}
    progression = {"character_level": roster[0]["level"]}
    for key in ("ascension", "constellation"):
        if type(progression_input.get(key)) is int:
            progression[key] = progression_input[key]
    levels = progression_input.get("talent_base_levels")
    if type(levels) is dict and type(levels.get("skill")) is int:
        progression["base_level"] = levels["skill"]

    variants = [ability.get("variant") for ability in selected]
    if any(value is not None and value not in _VARIANTS for value in variants):
        _fail("容量目录能力变体不在支持范围", "rotation_model.abilities.variant")
    target = _typed_target(
        plan, actor_id, ability_ids, character_id, version_line, progression, variants)
    explicit_variants = {value for value in variants if value is not None}
    if target.get("variant") is not None:
        if target["variant"] not in _VARIANTS:
            _fail("容量目录typed来源变体不在支持范围", "target.variant")
        explicit_variants.add(target["variant"])
    if len(explicit_variants) > 1:
        _fail("容量目录共池能力变体不一致", "rotation_model.abilities.variant")
    if target.get("variant") is not None or (all(value is not None for value in variants) and explicit_variants):
        target["variant"] = next(iter(explicit_variants))

    observed = {"progression": progression, "team": "self_only" if len(plan["manual_template"]["characters"]) == 1 else "not_self_only"}
    if target:
        observed["target"] = target
    weapon_input = gear.get("weapon")
    if type(weapon_input) is dict:
        weapon = {}
        for source_key, output_key in (("weapon_id", "id"), ("level", "level"), ("refinement", "refinement")):
            if type(weapon_input.get(source_key)) is int:
                weapon[output_key] = weapon_input[source_key]
        if weapon:
            observed["weapon"] = weapon
    artifacts = _artifact_state(gear)
    if artifacts is not None:
        observed["artifacts"] = artifacts
    if "action_capacity_context" in plan:
        try:
            validate_context(plan["action_capacity_context"])
        except (ValueError, TypeError, KeyError) as exc:
            raise PlanError("容量目录场景合同无效", "capacity_catalog_input",
                            {"path": "action_capacity_context"}) from exc
        observed["context"] = copy.deepcopy(plan["action_capacity_context"])

    unknown = sorted(_CATALOG_PATHS - _observed_paths(observed))
    return {"version_line": version_line, "character_id": character_id,
            "observed": copy.deepcopy(observed), "unknown_paths": unknown}


def _selected_whiteboard_actions(board, actor_id, action_ids):
    if type(actor_id) is not str or not actor_id.strip():
        _fail("容量目录需要非空的本地角色ID", "actor_id")
    if (type(action_ids) is not list or not action_ids or len(action_ids) > 64
            or any(type(value) is not str or not value.strip() for value in action_ids)):
        _fail("容量目录需要1至64个非空白板动作ID", "action_ids")
    if len(set(action_ids)) != len(action_ids):
        _fail("容量目录动作ID不能重复", "action_ids")
    indexed = {action["id"]: action for action in board["actions"]}
    selected = []
    for ident in action_ids:
        action = indexed.get(ident)
        if action is None:
            _fail("容量目录所选白板动作不存在", f"actions.{ident}")
        if action["character"] != actor_id:
            _fail("容量目录所选动作必须属于所选角色", f"actions.{ident}.character")
        if action["action_type_id"] != "action.elemental_skill":
            _fail("容量目录只接受元素战技动作", f"actions.{ident}.action_type_id")
        selected.append(action)
    return selected


def catalog_filter_from_whiteboard(board, actor_id, action_ids):
    """Build a partial query from validated, explicit whiteboard fields only."""
    # Local import avoids the equipment-package/whiteboard import cycle.
    from genshin_app.whiteboard import validate_whiteboard

    validate_whiteboard(board)
    _selected_whiteboard_actions(board, actor_id, action_ids)
    actors = [actor for actor in board["team"] if actor["id"] == actor_id]
    if len(actors) != 1:
        _fail("容量目录角色必须在白板队伍中存在且唯一", f"team.{actor_id}")
    actor = actors[0]
    selected_ids = set(action_ids)
    intersecting = [intent for intent in board["capacity_intents"]
                    if selected_ids & set(intent["action_ids"])]
    if intersecting:
        if len(intersecting) != 1 or set(intersecting[0]["action_ids"]) != selected_ids:
            _fail("所选动作必须精确对应单一已有次数池", "action_ids")
        selected_intent = intersecting[0]
        if selected_intent["actor"] != actor_id:
            _fail("容量意图与目录角色不一致", f"capacity_intents.{selected_intent['id']}.actor")
    else:
        selected_intent = None

    progression = {"character_level": actor["level"], "constellation": actor["constellation"]}
    if type(actor.get("ascension")) is int:
        progression["ascension"] = actor["ascension"]
    levels = actor.get("talent_base_levels")
    if type(levels) is dict and type(levels.get("skill")) is int:
        progression["base_level"] = levels["skill"]
    observed = {
        "progression": progression,
        "team": "self_only" if len(board["team"]) == 1 else "not_self_only",
    }
    weapon_input = actor.get("weapon")
    if type(weapon_input) is dict:
        weapon = {}
        for source_key, output_key in (("weapon_id", "id"), ("level", "level"), ("refinement", "refinement")):
            if type(weapon_input.get(source_key)) is int:
                weapon[output_key] = weapon_input[source_key]
        if weapon:
            observed["weapon"] = weapon
    artifacts = _artifact_state({"mode": "panel", "artifact_sets": actor["artifact_sets"],
                                 "artifact_presence": actor.get("artifact_presence")})
    if artifacts is not None:
        observed["artifacts"] = artifacts
    if selected_intent is not None:
        observed["target"] = copy.deepcopy(selected_intent["target"])
        if selected_intent["context"] is not None:
            observed["context"] = copy.deepcopy(selected_intent["context"])

    unknown = sorted(_CATALOG_PATHS - _observed_paths(observed))
    return {"version_line": board["version_line"], "character_id": actor["db_character_id"],
            "observed": copy.deepcopy(observed), "unknown_paths": unknown}


def action_capacity_model_candidates(plan, actor_id, ability_ids, db_path, *,
                                     cursor=None, page_size=DEFAULT_CATALOG_PAGE_SIZE,
                                     view="all"):
    """Read one catalog page without modifying the plan or selecting a model."""
    query = catalog_filter_from_plan(plan, actor_id, ability_ids)
    request = {"contract": CATALOG_QUERY_CONTRACT, **query,
               "view": view, "page_size": page_size, "cursor": cursor}
    return list_action_capacity_model_candidates(db_path, request)


def whiteboard_action_capacity_model_candidates(board, actor_id, action_ids, db_path, *,
                                                cursor=None, page_size=DEFAULT_CATALOG_PAGE_SIZE,
                                                view="all"):
    """Read one catalog page for a whiteboard draft without selecting a model."""
    query = catalog_filter_from_whiteboard(board, actor_id, action_ids)
    request = {"contract": CATALOG_QUERY_CONTRACT, **query,
               "view": view, "page_size": page_size, "cursor": cursor}
    return list_action_capacity_model_candidates(db_path, request)


def action_capacity_catalog_capabilities():
    return {
        "contract": "action-capacity-plan-catalog-service-v1",
        "query_contract": CATALOG_QUERY_CONTRACT,
        "page_contract": CATALOG_PAGE_CONTRACT,
        "source_document": "validated_complete_native_plan_or_whiteboard_v2",
        "document_kinds": ["plan", "whiteboard"],
        "member_scope": {"plan": "explicit_local_elemental_skill_ability_ids",
                         "whiteboard": "explicit_local_elemental_skill_action_ids"},
        "typed_skill_identity": "exact_action_resource_or_existing_capacity_target",
        "human_label_inference": False,
        "unknown_fields": "omitted_from_observed_and_listed_in_unknown_paths",
        "pagination": {"views": ["all", "matching", "mismatching", "not_evaluated"],
                       "default_page_size": DEFAULT_CATALOG_PAGE_SIZE,
                       "max_page_size": MAX_CATALOG_PAGE_SIZE,
                       "cursor": "opaque_catalog_bound"},
        "execution_authority": False,
        "writes_plan": False,
        "returns_capacity_values": False,
        "whiteboard_intents": {
            "field": "capacity_intents", "context_default": None,
            "identity": "exact_existing_intent_only",
            "exact_pool_ownership": True,
            "writes_intent": False,
        },
        "selection": "reference_only_requires_fresh_compose_confirmation",
    }


__all__ = [
    "action_capacity_catalog_capabilities",
    "action_capacity_model_candidates",
    "catalog_filter_from_plan",
    "catalog_filter_from_whiteboard",
    "whiteboard_action_capacity_model_candidates",
]
