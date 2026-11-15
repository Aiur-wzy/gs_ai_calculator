"""Read-only action-admission discovery and native-plan fresh preview.

The catalog never returns gate values or grants execution authority.  The
separate preview accepts only an exact model reference and target, rebuilds a
detached native proposal, and lets the ordinary composer issue the only
confirmation fingerprint that can later become executable.
"""
from __future__ import annotations

import copy
import math
import re

from genshin_db.runtime_action_admission import (
    CATALOG_ORDER,
    CATALOG_PAGE_CONTRACT,
    CATALOG_QUERY_CONTRACT,
    DEFAULT_CATALOG_PAGE_SIZE,
    MAX_CATALOG_PAGE_SIZE,
    get_action_admission_profile,
    list_action_admission_model_candidates,
)
from genshin_runtime.action_admission_model import ref, validate_selection

from .action_admission_schema import CONTEXT
from .composer import compose_equipment_plan
from .schema import PlanError, validate_plan

PREVIEW_REQUEST_CONTRACT = "action-admission-plan-preview-request-v1"
PREVIEW_CONTRACT = "action-admission-plan-preview-v1"
_CATALOG_PATHS = {
    "domain.action_type_id",
    "domain.constellation",
    "domain.variant",
    "target.skill_depot_id",
    "target.skill_id",
    "target.slot",
}


def _fail(message, reason="action_admission_catalog_input", path=None):
    raise PlanError(message, reason, {"path": path} if path is not None else [])


def _text(value, name):
    if type(value) is not str or not value.strip() or len(value) > 128:
        _fail(f"{name}需要1至128字符的非空文本", path=name)


def _selected_ability(plan, actor_id, ability_id):
    _text(actor_id, "actor_id")
    _text(ability_id, "ability_id")
    rotation = plan.get("rotation_model")
    if type(rotation) is not dict:
        _fail("动作接纳目录需要完整动作模型", path="rotation_model")
    abilities = [row for row in rotation.get("abilities", []) if row.get("id") == ability_id]
    if len(abilities) != 1:
        _fail("动作接纳目录能力必须存在且唯一", path=f"rotation_model.abilities.{ability_id}")
    ability = abilities[0]
    if ability.get("character") != actor_id:
        _fail("动作接纳能力不属于所选角色", path=f"rotation_model.abilities.{ability_id}.character")
    if ability.get("action_type_id") != "action.elemental_burst" or ability.get("variant") != "default":
        _fail("动作接纳首片只支持default元素爆发", path=f"rotation_model.abilities.{ability_id}")
    if len([row for row in rotation.get("characters", []) if row.get("id") == actor_id]) != 1:
        _fail("动作接纳角色必须在动作模型中存在且唯一", path=f"rotation_model.characters.{actor_id}")
    return ability


def _character_identity(plan, actor_id, fallback=None):
    roster = [row for row in plan["manual_template"]["characters"] if row.get("id") == actor_id]
    if len(roster) != 1:
        _fail("动作接纳角色必须在伤害模板中存在且唯一", path=f"manual_template.characters.{actor_id}")
    gear = plan.get("equipment_inputs", {}).get(actor_id)
    ref_ = gear.get("character_ref") if type(gear) is dict else None
    if (type(ref_) is not dict or type(ref_.get("db_character_id")) is not int
            or ref_.get("version_line") != "release"):
        if (type(fallback) is dict and type(fallback.get("character_id")) is int
                and fallback.get("version_line") == "release"):
            return fallback["character_id"], "release", fallback.get("constellation")
        _fail("动作接纳目录需要release资料线的精确数据库角色ID或已有typed动作来源",
              path=f"equipment_inputs.{actor_id}.character_ref")
    progression = (gear.get("progression") if type(gear) is dict
                   and type(gear.get("progression")) is dict else {})
    constellation = progression.get("constellation")
    if constellation is not None and (type(constellation) is not int or not 0 <= constellation <= 6):
        _fail("角色命座必须为0至6的精确整数", path=f"equipment_inputs.{actor_id}.progression.constellation")
    return ref_["db_character_id"], ref_["version_line"], constellation


def _merge_identity(known, field, value, path):
    if value is None:
        return
    if field in known and known[field] != value:
        _fail("动作接纳typed技能身份彼此矛盾", path=path)
    known[field] = value


def catalog_filter_from_plan(plan, actor_id, ability_id):
    """Derive only typed observations; labels never become skill identity."""
    validate_plan(plan)
    ability = _selected_ability(plan, actor_id, ability_id)
    admissions = [row for row in plan.get("action_admission_sources", [])
                  if row.get("ability_id") == ability_id]
    if len(admissions) > 1:
        _fail("同一能力存在多个动作接纳来源", path="action_admission_sources")
    resources = [row for row in plan.get("action_resource_sources", [])
                 if row.get("ability_id") == ability_id]
    if len(resources) > 1:
        _fail("同一能力存在多个动作资源typed来源", path="action_resource_sources")
    fallback = None
    if admissions:
        fallback = {"character_id": admissions[0]["selection"]["character_id"],
                    "version_line": admissions[0]["version_line"],
                    "constellation": admissions[0]["selection"]["constellation"]}
    elif resources:
        fallback = {"character_id": resources[0]["selection"]["character_id"],
                    "version_line": resources[0]["version_line"],
                    "constellation": resources[0]["selection"].get("constellation")}
    character_id, version_line, constellation = _character_identity(plan, actor_id, fallback)
    known = {}
    if admissions:
        binding = admissions[0]
        if binding["version_line"] != version_line or binding["selection"]["character_id"] != character_id:
            _fail("已有动作接纳来源与角色资料身份不一致", path=f"action_admission_sources.{binding['id']}")
        for field in ("skill_depot_id", "skill_id", "slot"):
            _merge_identity(known, field, binding["selection"][field],
                            f"action_admission_sources.{binding['id']}.selection.{field}")
        _merge_identity(known, "constellation", binding["selection"]["constellation"],
                        f"action_admission_sources.{binding['id']}.selection.constellation")
    if resources:
        source = resources[0]
        selection = source["selection"]
        if source["version_line"] != version_line or selection["character_id"] != character_id:
            _fail("动作资源typed身份与角色资料不一致", path="action_resource_sources")
        for field in ("skill_depot_id", "skill_id"):
            _merge_identity(known, field, selection.get(field), f"action_resource_sources.selection.{field}")
        _merge_identity(known, "constellation", selection.get("constellation"),
                        "action_resource_sources.selection.constellation")
        if source.get("variant") != "default":
            _fail("动作资源variant与动作接纳首片不一致", path="action_resource_sources.variant")
    _merge_identity(known, "constellation", constellation,
                    f"equipment_inputs.{actor_id}.progression.constellation")
    observed = {
        "target": {**{key: known[key] for key in ("skill_depot_id", "skill_id") if key in known},
                   "slot": "burst"},
        "domain": {**({"constellation": known["constellation"]} if "constellation" in known else {}),
                   "variant": ability["variant"], "action_type_id": ability["action_type_id"]},
    }
    observed_paths = {f"{group}.{key}" for group in ("target", "domain") for key in observed[group]}
    return {"version_line": version_line, "character_id": character_id,
            "observed": copy.deepcopy(observed),
            "unknown_paths": sorted(_CATALOG_PATHS - observed_paths)}


def action_admission_model_candidates(plan, actor_id, ability_id, db_path, *,
                                      cursor=None, page_size=DEFAULT_CATALOG_PAGE_SIZE,
                                      view="all"):
    query = catalog_filter_from_plan(plan, actor_id, ability_id)
    request = {"contract": CATALOG_QUERY_CONTRACT, **query, "view": view,
               "order": CATALOG_ORDER, "page_size": page_size, "cursor": cursor}
    return list_action_admission_model_candidates(db_path, request)


def catalog_filter_from_whiteboard(board, actor_id, action_id):
    """Derive a catalog filter only from typed v3 whiteboard fields."""
    from genshin_app.whiteboard import validate_whiteboard

    validate_whiteboard(board)
    _text(actor_id, "actor_id")
    _text(action_id, "action_id")
    actors = [row for row in board["team"] if row.get("id") == actor_id]
    actions = [row for row in board["actions"] if row.get("id") == action_id]
    if len(actors) != 1 or len(actions) != 1:
        _fail("动作接纳目录需要唯一白板角色和动作", path="whiteboard")
    actor, action = actors[0], actions[0]
    if (board["version_line"] != "release" or actor["constellation"] != 0
            or action["character"] != actor_id
            or action["action_type_id"] != "action.elemental_burst"
            or action["variant"] != "default"):
        _fail("动作接纳首片只支持release、C0角色的default元素爆发动作",
              "action_admission_whiteboard_scope", "actions." + action_id)
    intents = [row for row in board["action_admission_intents"]
               if row.get("action_id") == action_id]
    if len(intents) > 1:
        _fail("一个白板动作不能重复绑定动作接纳意图", path="action_admission_intents")
    target = {"slot": "burst"}
    if intents:
        selection = intents[0]["selection"]
        if (intents[0]["actor"] != actor_id
                or selection["character_id"] != actor["db_character_id"]):
            _fail("动作接纳意图与白板角色不一致", path="action_admission_intents")
        target.update(skill_depot_id=selection["skill_depot_id"], skill_id=selection["skill_id"])
    observed = {
        "target": target,
        "domain": {"constellation": 0, "variant": "default",
                   "action_type_id": "action.elemental_burst"},
    }
    observed_paths = {f"{group}.{key}" for group in ("target", "domain")
                      for key in observed[group]}
    return {"version_line": "release", "character_id": actor["db_character_id"],
            "observed": observed, "unknown_paths": sorted(_CATALOG_PATHS - observed_paths)}


def whiteboard_action_admission_model_candidates(board, actor_id, action_id, db_path, *,
                                                 cursor=None,
                                                 page_size=DEFAULT_CATALOG_PAGE_SIZE,
                                                 view="all"):
    query = catalog_filter_from_whiteboard(board, actor_id, action_id)
    request = {"contract": CATALOG_QUERY_CONTRACT, **query, "view": view,
               "order": CATALOG_ORDER, "page_size": page_size, "cursor": cursor}
    return list_action_admission_model_candidates(db_path, request)


def _validate_preview_request(request):
    fields = {"contract", "actor_id", "ability_id", "binding_id", "model_ref", "selection",
              "context", "initial_ready_in", "takeover", "accepted_fingerprint"}
    if type(request) is not dict or set(request) != fields or request.get("contract") != PREVIEW_REQUEST_CONTRACT:
        _fail("动作接纳预览请求合同或字段无效", "action_admission_preview_input")
    for key in ("actor_id", "ability_id", "binding_id"):
        _text(request[key], key)
    try:
        ref(request["model_ref"])
        validate_selection(request["selection"])
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError) as exc:
        raise PlanError(str(exc), "action_admission_preview_input") from exc
    context = request["context"]
    if context is not None and context != CONTEXT:
        _fail("动作接纳场景必须为空或精确普通开放世界预设", "action_admission_preview_input", "context")
    ready = request["initial_ready_in"]
    if (ready is not None
            and (type(ready) is not list or len(ready) > 1
                 or any(type(value) not in (int, float) or isinstance(value, bool)
                        or not math.isfinite(value) or not 0 <= value <= 1e9 for value in ready)
                 or ready != sorted(ready))):
        _fail("首片初始恢复队列必须为null、显式空数组或一个有限非负秒数",
              "action_admission_preview_input", "initial_ready_in")
    if type(request["takeover"]) is not bool:
        _fail("takeover必须为布尔值", "action_admission_preview_input", "takeover")
    accepted = request["accepted_fingerprint"]
    if accepted is not None and (type(accepted) is not str or not re.fullmatch(r"[0-9a-f]{64}", accepted)):
        _fail("accepted_fingerprint必须为空或sha256", "action_admission_preview_input",
              "accepted_fingerprint")
    return copy.deepcopy(request)


def _proposal(plan, request, db_path):
    proposed = copy.deepcopy(plan)
    ability = _selected_ability(proposed, request["actor_id"], request["ability_id"])
    character_id, version_line, constellation = _character_identity(proposed, request["actor_id"], {
        "character_id": request["selection"]["character_id"], "version_line": "release",
        "constellation": request["selection"]["constellation"]})
    selection = request["selection"]
    if (version_line != "release" or selection["character_id"] != character_id
            or selection["slot"] != "burst" or selection["constellation"] != 0
            or constellation not in (None, 0)):
        _fail("动作接纳预览的角色、资料线、槽位或C0身份与方案不一致",
              "action_admission_preview_input", "selection")
    actions = [row for row in proposed["rotation_model"].get("actions", [])
               if row.get("ability_id") == request["ability_id"]]
    if not actions:
        _fail("动作接纳预览需要此能力至少有一个实际动作", "action_admission_preview_input", "ability_id")
    old_resources = [row for row in proposed.get("action_resource_sources", [])
                     if row.get("ability_id") == request["ability_id"]]
    old_capacities = [row for row in proposed.get("action_capacity_sources", [])
                      if request["ability_id"] in row.get("ability_ids", [])]
    action_ids = {row["id"] for row in actions}
    old_spends = [row for row in proposed["rotation_model"].get("events", [])
                  if row.get("type") == "energy_spend" and row.get("action_id") in action_ids]
    manual_owned = ability.get("cooldown") is not None or ability.get("energy") is not None
    if (manual_owned or old_resources or old_capacities or old_spends) and not request["takeover"]:
        _fail("现有冷却、耗能或旧来源占有字段；须明确允许动作接纳接管",
              "action_admission_takeover_required", "takeover")
    if any(len(row.get("ability_ids", [])) != 1 for row in old_capacities):
        _fail("旧次数池还拥有其他能力，不能由单能力动作接纳预览静默删除",
              "action_admission_takeover_shared_capacity", "action_capacity_sources")
    if request["takeover"]:
        ability["cooldown"] = None
        ability["energy"] = None
        proposed["action_resource_sources"] = [row for row in proposed.get("action_resource_sources", [])
                                               if row.get("ability_id") != request["ability_id"]]
        proposed["action_capacity_sources"] = [row for row in proposed.get("action_capacity_sources", [])
                                               if request["ability_id"] not in row.get("ability_ids", [])]
        proposed["rotation_model"]["events"] = [row for row in proposed["rotation_model"].get("events", [])
                                                  if not (row.get("type") == "energy_spend"
                                                          and row.get("action_id") in action_ids)]
    existing = [row for row in proposed.get("action_admission_sources", [])
                if row.get("ability_id") == request["ability_id"]]
    if len(existing) > 1:
        _fail("同一能力存在多个动作接纳来源", "action_admission_preview_input")
    binding = {"id": request["binding_id"], "ability_id": request["ability_id"],
               "version_line": "release", "selection": copy.deepcopy(selection), "variant": "default",
               "model_ref": copy.deepcopy(request["model_ref"]),
               "accepted_fingerprint": request["accepted_fingerprint"]}
    proposed["action_admission_sources"] = [row for row in proposed.get("action_admission_sources", [])
                                            if row.get("ability_id") != request["ability_id"]]
    proposed["action_admission_sources"].append(binding)
    proposed["action_admission_context"] = copy.deepcopy(request["context"])

    # The directory intentionally withholds the group.  Resolve it only after
    # the user selected an exact reviewed reference, then bind the explicit
    # opening queue into the detached proposal before ordinary composition.
    profile = get_action_admission_profile(
        db_path, "release", selection=selection, variant="default",
        action_type_id="action.elemental_burst", model_ref=request["model_ref"])
    if profile.get("status") == "ready":
        group = profile["compiled_values"]["cooldown"]["group"]
        proposed["rotation_model"]["initial_cooldowns"] = [
            row for row in proposed["rotation_model"].get("initial_cooldowns", [])
            if not (row.get("character") == request["actor_id"] and row.get("group") == group)]
        if request["initial_ready_in"] is not None:
            proposed["rotation_model"]["initial_cooldowns"].append({
                "character": request["actor_id"], "group": group,
                "ready_in": copy.deepcopy(request["initial_ready_in"]),
            })
    return proposed


def action_admission_preview(plan, request, db_path):
    """Fresh preview/confirmation without mutating the caller's native plan."""
    validate_plan(plan)
    request = _validate_preview_request(request)
    proposed = _proposal(plan, request, db_path)
    report = compose_equipment_plan(proposed, db_path)
    records = [row for row in report.get("action_admission_compilations", [])
               if row.get("id") == request["binding_id"]]
    if len(records) != 1:
        _fail("动作接纳预览没有得到唯一编译记录", "action_admission_preview_failed")
    native = report["plan"]
    result = {
        "contract": PREVIEW_CONTRACT,
        "state": report["state"],
        "compilation": copy.deepcopy(records[0]),
        "proposal": {
            "action_admission_sources": copy.deepcopy(native.get("action_admission_sources", [])),
            "action_admission_context": copy.deepcopy(native.get("action_admission_context")),
            "action_resource_sources": copy.deepcopy(native.get("action_resource_sources", [])),
            "action_capacity_sources": copy.deepcopy(native.get("action_capacity_sources", [])),
            "rotation_model": copy.deepcopy(native["rotation_model"]),
        },
        "execution_authority": False,
        "application": "replace_listed_native_fields_only_after_same_fingerprint_ready",
    }
    return result


def action_admission_catalog_capabilities():
    return {
        "contract": "action-admission-plan-catalog-service-v1",
        "query_contract": CATALOG_QUERY_CONTRACT,
        "page_contract": CATALOG_PAGE_CONTRACT,
        "preview_request_contract": PREVIEW_REQUEST_CONTRACT,
        "preview_contract": PREVIEW_CONTRACT,
        "order": CATALOG_ORDER,
        "groups": ["exact", "partial", "pending", "revoked", "stale", "conflict", "not_evaluated"],
        "selectable_groups": ["exact", "partial"],
        "page_size": {"default": DEFAULT_CATALOG_PAGE_SIZE, "max": MAX_CATALOG_PAGE_SIZE},
        "typed_skill_identity": "exact_native_binding_or_action_resource_source_else_partial_character_burst",
        "human_label_inference": False,
        "catalog_returns_compiled_cooldown_or_energy": False,
        "execution_authority": False,
        "catalog_execution_authority": False,
        "confirmation": "fresh_preview_accepted_fingerprint",
        "whiteboard": "v3_typed_release_c0_default_burst_catalog",
    }


__all__ = [
    "PREVIEW_CONTRACT", "PREVIEW_REQUEST_CONTRACT", "action_admission_catalog_capabilities",
    "action_admission_model_candidates", "action_admission_preview", "catalog_filter_from_plan",
    "catalog_filter_from_whiteboard",
    "whiteboard_action_admission_model_candidates",
]
