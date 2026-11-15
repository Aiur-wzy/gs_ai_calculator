"""Bind reviewed parent qualifications to actual action receipts and axis routes.

This is an internal, pure graph check. The caller must obtain all source and
compiler receipts in its fresh compose session (or explicitly replay saved
materials offline). Passing this check does not authorize mechanism execution.
"""
from __future__ import annotations

import copy

from .energy_generation_sources import _one, _Pending, _ticks
from .files import fingerprint

MAX_PARENTS = 512
SLOT_ACTION = {"normal": "action.normal_attack", "skill": "action.elemental_skill",
               "burst": "action.elemental_burst"}
SNAPSHOT_FIELDS = ("version_line", "snapshot_id", "upstream_commit", "indexer_version", "normalization")


def _need(condition, message):
    if not condition:
        raise _Pending(message)


def _snapshot_equal(left, right):
    return all(left.get(k) == right.get(k) for k in SNAPSHOT_FIELDS)


def _hit_allowed(hit, expected):
    tags = hit.get("damage_type_ids")
    return (hit.get("damage_system") in expected["damage_systems"]
            and type(hit.get("count", 1)) is int and hit.get("count", 1) == expected["count"]
            and hit.get("scaling_stat", "none") in expected["scaling_stats"]
            and hit.get("element") in expected["elements"]
            and bool(tags) and set(tags) <= set(expected["damage_type_ids"]))


def _filter_possible(filter_, hit, ability):
    """Three-valued coverage: unavailable metadata cannot prove irrelevance."""
    values = {"action_type_ids": [ability.get("action_type_id")],
              "damage_type_ids": hit.get("damage_type_ids"), "elements": [hit.get("element")]}
    for key, allowed in filter_.items():
        known = [v for v in values[key] or [] if v is not None]
        if known and not set(known) & set(allowed):
            return False
    return True


def _related(coverage, hit, ability, application, applications):
    if coverage["action_filter"] is not None and _filter_possible(coverage["action_filter"], hit, ability):
        return True
    if not coverage["application_elements"]:
        return False
    if application:
        profile = applications.get(application.get("profile_id"))
        if profile is None or profile.get("application") == "unknown":
            return True
        if profile.get("application") == "none":
            return False
        return profile.get("element") in coverage["application_elements"]
    # A known unrelated hit without an application does not emit a reaction.
    return False


def _selection(actual, expected, profile, parameter):
    for key, value in expected.items():
        if key == "slot":
            _need(value in actual.get("matching_slots", []), "父来源技能槽不符合受审资格")
        elif key == "variant":
            _need(profile.get("variant") == value, "父动作变体不符合受审资格")
        elif key == "talent_level":
            _need(parameter is not None and parameter.get("level", {}).get("effective_level") == value,
                  "父来源有效天赋等级不符合受审资格")
        else:
            _need(type(actual.get(key)) is type(value) and actual.get(key) == value,
                  "父来源身份不符合受审资格：" + key)


def _parameter_origin(parameter, binding, source_proof):
    node = _one(source_proof["nodes"], "id", binding["node_id"], "受审父参数节点")
    origin = parameter.get("source") or {}
    _need(parameter.get("param_index") == binding["param_index"]
          and all(origin.get(k) == node[k] for k in ("source_path", "source_sha256", "node_sha256", "json_pointer"))
          and origin.get("parameter_pointer") == node["json_pointer"] + binding["pointer"],
          "父倍率来源必须匹配完整节点和精确参数位置，不能只比较数值")


def _build(plan, template, *, actor, source_binding, source_proof, qualification,
           action_records, parameter_records, metadata_records):
    from genshin_runtime.mechanism_qualification import validate_execution_qualification

    q = validate_execution_qualification(qualification)
    subject, selected = source_binding["subject"], source_binding["selection"]
    _need(subject["target_type"] == "character", "当前父事件图尚不支持该主体类型")
    _need(selected.get("character_id") == subject["target_id"] and "skill_depot_id" in selected,
          "父事件图需要明确角色和技能组归属")
    model, rotation = plan["mechanics_model"], plan["rotation_model"]
    elemental = model.get("elemental_model") or {}
    target = (elemental.get("target") or {}).get("id")
    _need(bool(target), "父事件图需要明确附着目标")
    apps = {r["id"]: r for r in elemental.get("profiles", [])}
    contacts = {r["id"]: r for r in elemental.get("contact_profiles", [])}
    hits = {r["id"]: r for r in template["hits"]}
    actions = {r["id"]: r for r in rotation["actions"]}
    abilities = {r["id"]: r for r in rotation["abilities"]}
    patterns = {r["id"]: r for r in model.get("hit_patterns", [])}
    native_bindings = plan.get("action_event_bindings", [])
    horizon = _ticks(rotation["period"])
    links, dependencies, admitted_patterns, admitted_actions = [], [], {}, set()
    for profile in plan.get("action_event_profiles", []):
        hit = hits.get(profile["prototype_hit_id"], {})
        if hit.get("character") != actor:
            continue
        attached = [abilities.get(row["ability_id"], {}) for row in native_bindings if row["profile_id"] == profile["id"]]
        if not any(_related(q["coverage"], hit, ab, {"profile_id": profile.get("application_profile_id")}, apps)
                   for ab in attached):
            continue
        record = _one(action_records, "profile_id", profile["id"], "父派发回执")
        _need(profile.get("kind") == q["parent"]["profile_kind"]
              and record.get("status") == "ready" and record.get("actor") == actor
              and bool(record.get("profile_fingerprint"))
              and profile.get("accepted_profile_fingerprint") == record["profile_fingerprint"],
              "相关父动作须有已接受且就绪的派发凭证")
        _need(_hit_allowed(hit, q["parent"]["hit"]), "父命中形状不符合受审资格")
        parameter = next((r for r in parameter_records if r.get("hit_id") == hit["id"]), None)
        meta = next((r for r in metadata_records if r.get("action_event_profile_id") == profile["id"]), None)
        for kind, rows, key, value in (("hit_parameter", parameter_records, "hit_id", hit["id"]),
                                       ("attack_metadata", metadata_records, "action_event_profile_id", profile["id"])):
            found = [r for r in rows if r.get(key) == value]
            _need(len(found) <= 1, "父来源凭证重复：" + kind)
            if kind in q["parent"]["required_receipts"]:
                _need(len(found) == 1, "缺少父来源凭证：" + kind)
            if found:
                item = found[0]
                _need(item.get("status") == "ready" and item.get("actor") == actor
                      and _snapshot_equal(item.get("snapshot", {}), source_proof["snapshot"]),
                      "父来源凭证未就绪或快照/提供者不一致：" + kind)
        if parameter:
            parent_selection = parameter["resolved_selection"]
        else:
            parent_selection = ((meta or {}).get("query") or {}).get("selection", {})
        _need(all(parent_selection.get(k) == selected[k] for k in ("character_id", "skill_depot_id")),
              "父动作与机制来源不属于同一角色或技能组")
        _selection(parent_selection, q["parent"]["selection"], profile, parameter)
        if meta:
            meta_selection = (meta.get("query") or {}).get("selection", {})
            _need(all(meta_selection.get(k) == parent_selection.get(k)
                      for k in ("character_id", "skill_depot_id", "skill_id", "constellation"))
                  and meta.get("prototype_hit_id") == hit["id"]
                  and meta.get("application_profile_id") == profile["application_profile_id"],
                  "父攻击元信息与倍率/附着身份不一致")
        if q["parent"]["parameter_binding"] is not None:
            _parameter_origin(parameter, q["parent"]["parameter_binding"], source_proof)
        app = apps.get(profile.get("application_profile_id"))
        _need(app is not None and app.get("application") != "unknown"
              and profile.get("contact_profile_id") in contacts, "父附着或接触档案尚未明确")
        _need(meta is None or meta.get("compiled_fields", {}).get("element") == app.get("element"),
              "父攻击元信息元素与实际附着档案不一致")
        generated = record.get("generated") or []
        _need(bool(generated) and len(links) + len(generated) <= MAX_PARENTS, "父派发为空或超过关系预算")
        for item in generated:
            action, ab, pattern = actions.get(item["action_id"]), abilities.get(item["ability_id"]), patterns.get(item["pattern_id"])
            _need(bool(action and ab and pattern), "实际父动作、技能或命中模式缺失")
            _need(action["ability_id"] == ab["id"] and ab["character"] == actor
                  and ab["action_type_id"] == SLOT_ACTION[q["parent"]["selection"]["slot"]]
                  and ab.get("variant") == profile.get("variant") and action["repeat"] == {"mode": "once"}
                  and any(r["ability_id"] == ab["id"] and r["profile_id"] == profile["id"] for r in native_bindings),
                  "父技能派发与实际动作身份不一致")
            _need(pattern["action_id"] == action["id"] and pattern["prototype_hit_id"] == hit["id"]
                  and item.get("prototype_hit_id") == hit["id"]
                  and pattern["offsets"] == [profile["calibration"]["offset_seconds"]]
                  and pattern["orders"] == [item["order"]] and item["hit_id"] == pattern["id"] + ":1"
                  and type(item["landed"]) is bool
                  and pattern.get("application") == {"profile_id": app["id"], "target_id": target, "landed": item["landed"]}
                  and pattern.get("contact") == {"kind": "profile", "profile_id": profile["contact_profile_id"]},
                  "父派发、实际命中与附着/接触关系不一致")
            start, end, tick = _ticks(action["start"]), _ticks(action["occupation_end"]), _ticks(item["t"])
            _need(start <= end and start <= tick and tick == start + _ticks(pattern["offsets"][0])
                  and tick < horizon and action["id"] not in admitted_actions,
                  "父命中时间不属于派发时点或窗口，或同动作重复派发")
            admitted_actions.add(action["id"])
            admitted_patterns[pattern["id"]] = fingerprint(pattern)
            links.append({"profile_id": profile["id"], **copy.deepcopy(item),
                          "action_tick": start, "occupation_end_tick": end, "absolute_tick": tick,
                          "action_order": action["order"], "pattern_fingerprint": fingerprint(pattern)})
        dependencies.append({"profile_id": profile["id"], "profile_fingerprint": record["profile_fingerprint"],
            "source_dependency_fingerprint": record.get("source_dependency_fingerprint"),
            "parameter_fingerprint": (parameter or {}).get("compilation_fingerprint"),
            "metadata_dependency_fingerprint": (meta or {}).get("metadata_dependency_fingerprint"),
            "metadata_source_fingerprint": (meta or {}).get("source_fingerprint")})
    _need(bool(links), "没有满足受审资格的实际父动作")
    action_filter = q["coverage"]["action_filter"]
    if action_filter is not None and (not action_filter or "action_type_ids" in action_filter):
        for action in actions.values():
            ab = abilities[action["ability_id"]]
            if ab["character"] == actor and (not action_filter or ab["action_type_id"] in action_filter["action_type_ids"]):
                _need(action["id"] in admitted_actions, "整轴存在未覆盖的相关动作或另一变体")
    for field in ("hit_events", "hit_patterns", "application_events"):
        for row in model.get(field, []):
            action = actions.get(row.get("action_id"), {})
            ab = abilities.get(action.get("ability_id"), {})
            hit = hits.get(row.get("hit_id", row.get("prototype_hit_id")), {})
            if hit.get("character", ab.get("character")) != actor:
                continue
            if _related(q["coverage"], hit, ab, row.get("application"), apps):
                _need(field == "hit_patterns" and admitted_patterns.get(row["id"]) == fingerprint(row),
                      "整轴存在无受审父链的相关命中或附着：" + field)
    return {"status": "ready", "verification_scope": "parent_graph_only", "execution_status": "not_evaluated",
            "actor": actor, "target_id": target,
            "parents": sorted(links, key=lambda r: (r["action_tick"], r["action_order"], r["action_id"])),
            "dependencies": sorted(dependencies, key=lambda r: r["profile_id"]), "issues": []}


def build_mechanism_parent_graph(plan, template, **kwargs):
    """All-or-nothing parent graph; no generated rule, approval or plan mutation."""
    try:
        result = _build(plan, template, **kwargs)
        result["graph_fingerprint"] = fingerprint(result)
        return result
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        return {"status": "pending", "verification_scope": "parent_graph_only", "execution_status": "not_evaluated",
                "parents": [], "dependencies": [], "graph_fingerprint": None,
                "issues": [{"code": "mechanism_parent_pending", "message": str(exc)[:1024]}]}
