"""Projection of declarative mechanics inputs into executable queue items."""

from __future__ import annotations

import copy

from genshin_engine.mechanics_validation import (
    _tick,
    validate_elemental_counter_bindings,
)
from genshin_engine.rotation_port import repeat_origins as _origins
from genshin_engine.rotation_port import rotation_issue as _issue


def project_mechanics_inputs(template, rotation, mechanics, issues):
    """Project explicit patterns and action switches without mutating user input."""
    t, model = copy.deepcopy(template), copy.deepcopy(rotation)
    if t.get("version") != 2:
        issues.append(_issue("unsupported", "声明式机制需要伤害模板 v2 的稳定命中 ID。", "template_version"))
        return t, model, []
    hits = {h["id"]: h for h in t["hits"]}
    actions = {a["id"]: a for a in model["actions"]}
    abilities = {a["id"]: a for a in model["abilities"]}
    runtime = []
    linked, projected = set(), []
    descriptors = []
    for descriptor in mechanics["hit_events"]:
        hid = descriptor["hit_id"]
        if hid not in hits or descriptor["action_id"] not in actions or hid in linked:
            issues.append(_issue("reference", "命中引用重复或不存在。", hid))
            continue
        linked.add(hid)
        descriptors.append((copy.deepcopy(hits[hid]), descriptor))
    prototypes = set()
    for pattern in mechanics["hit_patterns"]:
        aid, proto = pattern["action_id"], pattern["prototype_hit_id"]
        if aid not in actions or proto not in hits or proto in linked:
            issues.append(_issue("reference", "模式原型或动作不存在，或原型同时作为独立命中。", pattern["id"]))
            continue
        if not pattern["offsets"] or len(pattern["offsets"]) != len(pattern["orders"]):
            issues.append(_issue("missing_information", "偏移和 order 必须非空且逐项对应。", pattern["id"]))
            continue
        prototypes.add(proto)
        for index, (offset, order) in enumerate(zip(pattern["offsets"], pattern["orders"], strict=True), 1):
            hit = copy.deepcopy(hits[proto])
            hit.update(id=f"{pattern['id']}:{index}", t=round(actions[aid]["start"] + offset, 6), count=1)
            descriptors.append((hit, {**pattern, "order": order, "pattern_occurrence": index}))
    if set(hits) != linked | prototypes:
        issues.append(_issue("missing_information", "每个输入命中必须声明 hit_events 或用作 hit_patterns 原型。", "mechanics_model.hit_events",
                             missing_hit_ids=sorted(set(hits)-linked-prototypes)))
    ids = set()
    for hit, descriptor in descriptors:
        if hit["id"] in ids or hit.get("count", 1) != 1:
            issues.append(_issue("ambiguous_hits", "逐次机制不接受重复 ID 或 count 聚合，需明确偏移模式。", hit["id"]))
        ids.add(hit["id"])
        action = actions[descriptor["action_id"]]
        if hit.get("snapshot"):
            issues.append(_issue("unsupported", "新机制路径不沿用整组 snapshot/cast_t；请显式声明 self_snapshot_event 及收益域。", hit["id"]))
        if (_tick(hit["t"]), descriptor["order"]) <= (_tick(action["start"]), action["order"]):
            issues.append(_issue("causality", "命中必须排在来源动作之后，包括同刻 order。", hit["id"], action_id=action["id"]))
        projected.append(hit)
        runtime.append({"id": hit["id"], "kind": "mechanics_hit", "time": _tick(hit["t"]), "order": descriptor["order"],
                        "data": {"hit": hit, "descriptor": descriptor, "repeat": action["repeat"], "action_id": action["id"]}})
    elemental = mechanics.get("elemental_model")
    if elemental:
        issues.extend(validate_elemental_counter_bindings(t, model, mechanics))
        profiles = {p["id"]: p for p in elemental["profiles"]}

        def check_application(application, path, element=None):
            if application is None or application.get("profile_id") not in profiles or application.get("target_id") != elemental["target"]["id"]:
                issues.append(_issue("reference", "每个命中/附着事件需要有效档案和明确目标。", path))
            elif element is not None and profiles[application["profile_id"]]["element"] != element:
                issues.append(_issue("element_mismatch", "命中元素与附着档案不一致。", path))
        for hit, descriptor in descriptors:
            check_application(descriptor.get("application"), hit["id"], hit["element"])
            if hit.get("reaction") is not None or hit["damage_system"] != "classic":
                issues.append(_issue("duplicate_reaction", "元素模拟只接受未预填 reaction 的普通命中，不能重复交付反应答案。", hit["id"]))
        for attempt in mechanics.get("application_events", []):
            action = actions.get(attempt["action_id"])
            check_application(attempt["application"], attempt["id"])
            if action is None:
                issues.append(_issue("reference", "无伤害附着的来源动作不存在。", attempt["id"]))
                continue
            if (_tick(attempt["t"]), attempt["order"]) <= (_tick(action["start"]), action["order"]):
                issues.append(_issue("causality", "附着必须位于来源动作之后。", attempt["id"]))
            runtime.append({"id": attempt["id"], "kind": "elemental_application", "time": _tick(attempt["t"]),
                            "order": attempt["order"], "data": {**attempt, "repeat": action["repeat"]}})
        character_ids = {row["id"] for row in t["characters"]}
        for operation in mechanics.get("elemental_state_events", []):
            if operation["target_id"] != elemental["target"]["id"]:
                issues.append(_issue(
                    "reference", "元素状态操作目标必须与元素运行时目标一致。",
                    operation["id"], target_id=operation["target_id"],
                    elemental_target_id=elemental["target"]["id"]))
            if operation["character"] not in character_ids:
                issues.append(_issue(
                    "reference", "元素状态操作角色不在当前队伍。",
                    operation["id"], character=operation["character"]))
            if operation["kind"].startswith("bloom_core_"):
                action = actions.get(operation["action_id"])
                if (action is None or abilities[action["ability_id"]]["character"] != operation["character"]
                        or action["repeat"] != operation["repeat"]):
                    issues.append(_issue("bloom_operation_action_reference", "草原核操作须匹配实际来源动作的角色及重复范围。", operation["id"]))
                elif (_tick(operation["t"]), operation["order"]) <= (_tick(action["start"]), action["order"]):
                    issues.append(_issue("causality", "草原核操作必须晚于其来源动作。", operation["id"]))
            runtime.append({
                "id": operation["id"], "kind": "elemental_state_operation",
                "time": _tick(operation["t"]), "order": operation["order"],
                "data": copy.deepcopy(operation),
            })
    t["hits"] = projected
    model["action_hit_links"] = [{"action_id": x["data"]["action_id"], "hit_id": x["id"]} for x in runtime if x["kind"] == "mechanics_hit"]
    t["switches"] = [{"t": 0, "character": model["initial_frontline"]}] + [
        {"t": a["start"], "character": a["switch_to"]} for a in sorted(model["actions"], key=lambda a: (a["start"], a["order"]))
        if abilities.get(a["ability_id"], {}).get("action_type_id") == "action.switch" and 0 in _origins(a["repeat"], 1)]
    return t, model, runtime

__all__ = ["project_mechanics_inputs"]
