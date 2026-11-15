"""Own only charges, after a fresh complete model and actual input agreement."""
from __future__ import annotations

import copy
import math
import sqlite3

from genshin_app.task_capability_producer import emit_task_missing
from genshin_runtime.action_root_semantics import bounded

from .action_capacity_inputs import actual_selection
from .action_capacity_schema import validate_capacity_bindings
from .files import fingerprint
from .schema import PlanError

COMPILER = "reviewed-action-capacity-compiler-v1"
MARKER = "action-capacity-source-v1"


def capabilities():
    return {"compiler": COMPILER, "field": "action_capacity_sources", "max_bindings": 64,
            "owned_fields": ["cooldown.charges"], "variant": "tap", "initial_state": "explicit_ready_in",
            "scope": "受审静态总容量；实际装备和场景须匹配，冷却参数和初始状态另行明确",
            "native_dispatch": "unproven", "confirmation": "accepted_fingerprint"}


def _reject(message, reason="capacity_ownership"):
    raise PlanError(message, reason)


def _semantic_ability(ability):
    return {k: copy.deepcopy(v) for k, v in ability.items() if k not in ("name", "label", "source", "description")}


def _typed_targets(plan, binding):
    """Cross typed parent links; a damage kind or a display name is not identity."""
    target, owned = binding["target"], set(binding["ability_ids"])
    rotation = plan.get("rotation_model") or {}
    actions = {x["id"]: x["ability_id"] for x in rotation.get("actions", [])}
    profiles = {x["id"]: x for x in plan.get("action_event_profiles", [])}
    linked_hits, linked_profiles = set(), set()
    for row in plan.get("action_event_bindings", []):
        if row["ability_id"] in owned:
            p = profiles[row["profile_id"]]
            if p["variant"] != target["variant"]:
                _reject("次数目标与动作派发变体矛盾", "capacity_scope")
            linked_profiles.add(p["id"])
            if p.get("prototype_hit_id"):
                linked_hits.add(p["prototype_hit_id"])
            linked_hits.update(x["prototype_hit_id"] for x in p.get("channels", []))
    for field in ("hit_events", "hit_patterns"):
        for row in (plan.get("mechanics_model") or {}).get(field, []):
            if actions.get(row.get("action_id")) in owned:
                linked_hits.add(row.get("hit_id", row.get("prototype_hit_id")))
    relevant = [s for s in plan.get("hit_parameter_sources", []) if s["hit_id"] in linked_hits]
    relevant.extend(s for s in plan.get("action_resource_sources", []) if s["ability_id"] in owned)
    relevant.extend(s for field in ("attack_metadata_sources", "periodic_attack_sources")
                    for s in plan.get(field, []) if s.get("action_event_profile_id") in linked_profiles)
    for source in relevant:
        selection = source["selection"]
        if (source["version_line"] != binding["version_line"]
                or selection.get("character_id") != target["character_id"]
                or selection.get("skill_depot_id", target["skill_depot_id"]) != target["skill_depot_id"]
                or selection.get("skill_id", target["skill_id"]) != target["skill_id"]
                or selection.get("slot", "skill") != "skill"):
            _reject("次数目标与从属动作的技能来源矛盾", "capacity_scope")


def pool_material(plan, binding):
    rotation = plan.get("rotation_model") or {}
    bounded(rotation)
    _typed_targets(plan, binding)
    values = rotation.get("abilities", [])
    abilities = {a["id"]: a for a in values}
    if len(abilities) != len(values) or any(aid not in abilities for aid in binding["ability_ids"]):
        _reject("次数绑定能力缺失或重复")
    first = abilities[binding["ability_ids"][0]]
    actor, cooldown = binding["actor"], first.get("cooldown")
    if not isinstance(cooldown, dict) or not cooldown.get("group"):
        _reject("次数来源需要明确冷却组和冷却参数")
    group = cooldown["group"]
    members = [a for a in values if a.get("character") == actor and (a.get("cooldown") or {}).get("group") == group]
    if {a["id"] for a in members} != set(binding["ability_ids"]):
        _reject("冷却组含未绑定能力，普通攻击或爆发也不能借用受审次数池")
    for ability in members:
        cd = ability["cooldown"]
        if ability.get("action_type_id") != "action.elemental_skill" or ability.get("variant") != "tap" or binding["target"]["variant"] != "tap":
            _reject("当前容量模型只支持明确点按战技 tap", "capacity_scope")
        if "charges" in cd or "capacity_source" in cd:
            _reject("次数已被手工或另一来源拥有，即使数值相同也需先移交")
        if (any(k not in cd for k in ("duration", "start", "recovery"))
                or type(cd["duration"]) not in (int, float) or not math.isfinite(cd["duration"]) or cd["duration"] < 0
                or cd["start"] not in ("action_start", "occupation_end") or cd["recovery"] not in ("sequential", "parallel")):
            _reject("次数来源不补冷却秒数、起点或恢复方式", "capacity_scope")
        if any(cd[k] != cooldown[k] for k in ("group", "duration", "start", "recovery")):
            _reject("同一点按的本地表示须采用同组冷却规范")
    if any(a.get("character") == actor and a.get("action_type_id") == "action.elemental_skill" and a["id"] not in binding["ability_ids"] for a in values):
        _reject("存在未归属或分裂资源池的战技动作", "capacity_scope")
    if any(e.get("type") == "cooldown_change" and e.get("character") == actor and e.get("group") == group for e in rotation.get("events", [])):
        _reject("当前静态容量模型不覆盖冷却重置、恢复或动态修改", "capacity_scope")
    for effect in plan.get("effect_bindings", []):
        if effect.get("provider_character_id") != actor:
            continue
        for ref in effect.get("upstream_recipe", {}).get("ability_refs", []):
            if ref["ability_id"] in binding["ability_ids"] and ref["skill_id"] != binding["target"]["skill_id"]:
                _reject("次数目标与已有上游能力引用矛盾", "capacity_scope")
            if ref["skill_id"] == binding["target"]["skill_id"] and ref["ability_id"] not in binding["ability_ids"]:
                _reject("存在同源目标未完整列入的能力", "capacity_scope")
    initial = [x for x in rotation.get("initial_cooldowns", []) if x.get("character") == actor and x.get("group") == group]
    if len(initial) != 1 or type(initial[0].get("ready_in")) is not list:
        _reject("请显式填写本组开场恢复队列；[]才表示全部可用", "capacity_initial_unknown")
    queue = initial[0]["ready_in"]
    if any(type(x) not in (int, float) or not math.isfinite(x) or x < 0 for x in queue) or queue != sorted(queue):
        _reject("开场恢复队列必须为有限非负且有序的秒数", "capacity_initial_unknown")
    return {"actor": actor, "group": group, "target": copy.deepcopy(binding["target"]),
            "members": [_semantic_ability(a) for a in sorted(members, key=lambda a: a["id"])], "initial": copy.deepcopy(initial[0])}


def projection(profile, actual, pool, weapon_stats):
    bounded([profile, actual, pool, weapon_stats])
    if profile["status"] != "ready" or profile["capacity"] is None:
        _reject("完整容量模型尚未就绪", "capacity_pending")
    capacity = profile["capacity"]
    if len(pool["initial"]["ready_in"]) > capacity:
        _reject("初始恢复队列长度超过受审最大次数", "capacity_initial_unknown")
    return {"compiler": COMPILER, "profile": copy.deepcopy(profile), "actual": copy.deepcopy(actual),
            "pool": copy.deepcopy(pool), "weapon_stats": copy.deepcopy(weapon_stats), "capacity": capacity}


def compile_action_capacities(plan, db_path, missing):
    validate_capacity_bindings(plan)
    bindings = plan.get("action_capacity_sources", [])
    if not bindings:
        return None, [], []
    from genshin_db.equipment_queries import equipment_read_session
    with equipment_read_session(db_path):
        return _compile(plan, db_path, missing)


def _compile(plan, db_path, missing):
    bindings = plan["action_capacity_sources"]
    from genshin_db.queries import get_weapon_stats
    from genshin_db.runtime_action_capacity import get_action_capacity_profile
    rotation, records, sources = copy.deepcopy(plan["rotation_model"]), [], []
    abilities = {a["id"]: a for a in rotation["abilities"]}
    for binding in bindings:
        record = {"id": binding["id"], "compiler": COMPILER, "status": "pending", "issues": [], "projection": None,
                  "required_confirmation": None, "source_fingerprint": None}
        records.append(record)
        pool = pool_material(plan, binding)  # Ownership conflicts are invalid, not acknowledgement prompts.
        try:
            selection, context, actual = actual_selection(plan, binding)
            stats = get_weapon_stats(db_path, selection["weapon"]["id"], selection["weapon"]["level"], actual["weapon_ascension"], binding["version_line"])
            profile = get_action_capacity_profile(db_path, binding["version_line"], selection=selection, context=context, model_ref=binding["model_ref"])
            if profile["status"] != "ready":
                raise PlanError("当前受审容量模型不可用：" + str(profile["reason"]), "capacity_pending")
            proj = projection(profile, actual, pool, stats)
        except (ValueError, OSError, sqlite3.Error) as exc:
            message = str(exc)
            record["issues"].append({"code": getattr(exc, "code", "capacity_pending"), "message": message})
            emit_task_missing(
                missing, "capacity_pending",
                "action_capacity_sources." + binding["id"], message,
                "review_action_capacity",
                consumer="action_capacity:" + binding["id"],
                category="core_data_missing",
                repair_hint="补齐当前容量模型、配装与初始队列后重新编译",
            )
            continue
        source_fp = fingerprint(proj)
        record.update(projection=proj, source_fingerprint=source_fp,
                      required_confirmation={"accepted_fingerprint": source_fp})
        if binding["accepted_fingerprint"] != source_fp:
            record["issues"].append({"code": "capacity_confirmation", "message": "请核对完整模型、实际配装、初始队列和全部当前事实后确认"})
            emit_task_missing(
                missing, "source_changed",
                "action_capacity_sources." + binding["id"],
                record["issues"][0]["message"], "review_action_capacity",
                consumer="action_capacity:" + binding["id"],
                category="knowledge_missing",
                repair_hint="复核当前模型、配装、初始队列和来源指纹后重新确认",
            )
            continue
        values = {"charges": profile["capacity"], "capacity_source": {
            "contract": MARKER, "binding_id": binding["id"], "source_fingerprint": source_fp}}
        for aid in binding["ability_ids"]:
            abilities[aid]["cooldown"].update(copy.deepcopy(values))
        record.update(status="ready", compiled_values=values)
        record["compilation_fingerprint"] = fingerprint(record)
        sources.append({"kind": "action_capacity", "id": binding["id"], "source_fingerprint": source_fp,
                        "compilation_fingerprint": record["compilation_fingerprint"]})
    return rotation, records, sources


def prospective_capacity_source(record):
    """Return a dependency identity without granting pending execution fields."""
    if (record.get("status") != "pending" or record.get("projection") is None
            or not record.get("source_fingerprint")
            or {issue.get("code") for issue in record.get("issues", [])} != {"capacity_confirmation"}):
        return None
    source_fp = record["source_fingerprint"]
    values = {"charges": record["projection"]["capacity"], "capacity_source": {
        "contract": MARKER, "binding_id": record["id"], "source_fingerprint": source_fp}}
    ready = copy.deepcopy(record)
    ready.update(status="ready", issues=[], compiled_values=values)
    return {"kind": "action_capacity", "id": record["id"], "source_fingerprint": source_fp,
            "compilation_fingerprint": fingerprint(ready)}


def verify_final_pools(plan, rotation, records):
    """Later compilers cannot silently add an unowned consumer of the same pool."""
    for record in records:
        if record["status"] != "ready":
            continue
        pool = record["projection"]["pool"]
        actual = [a for a in rotation["abilities"] if a["character"] == pool["actor"] and (a.get("cooldown") or {}).get("group") == pool["group"]]
        if {x["id"] for x in actual} != {x["id"] for x in pool["members"]}:
            _reject("最终编译冷却池成员与受审成员集合不一致")
        expected_members = {x["id"]: x for x in pool["members"]}
        for ability in actual:
            cd = ability["cooldown"]
            if any(cd.get(k) != v for k, v in record["compiled_values"].items()):
                _reject("最终动作次数拥有字段被其他编译修改")
            restored = _semantic_ability(ability)
            for key in record["compiled_values"]:
                restored["cooldown"].pop(key)
            if fingerprint(restored) != fingerprint(expected_members[ability["id"]]):
                _reject("最终冷却池能力或冷却规范偏离已确认输入")
        initial = [x for x in rotation.get("initial_cooldowns", []) if x.get("character") == pool["actor"] and x.get("group") == pool["group"]]
        if len(initial) != 1 or fingerprint(initial[0]) != fingerprint(pool["initial"]):
            _reject("最终冷却池开场状态偏离已确认输入")
        if any(e.get("type") == "cooldown_change" and e.get("character") == pool["actor"] and e.get("group") == pool["group"] for e in rotation.get("events", [])):
            _reject("最终冷却池存在未获审的动态修改")
