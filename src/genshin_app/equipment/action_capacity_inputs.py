"""Read capacity conditions from actual plan inputs, never from an override."""
from __future__ import annotations

import copy

from genshin_runtime.action_capacity_model import validate_context

from .action_capacity_schema import SLOTS, validate_capacity_equipment
from .files import fingerprint


def actual_selection(plan, binding):
    from .schema import PlanError
    actor, target = binding["actor"], binding["target"]
    roster = plan["manual_template"]["characters"]
    rotation = plan.get("rotation_model") or {}
    if len(roster) != 1 or roster[0]["id"] != actor or [x["id"] for x in rotation.get("characters", [])] != [actor]:
        raise PlanError("当前容量模型只覆盖实际单人队伍")
    gear = plan["equipment_inputs"].get(actor)
    if gear is None:
        raise PlanError("次数模型需要角色实际配装和成长声明")
    validate_capacity_equipment(gear)
    if gear.get("character_ref") != {"db_character_id": target["character_id"], "version_line": binding["version_line"]}:
        raise PlanError("次数目标与实际配装角色不一致")
    progress = gear.get("progression") or {}
    if progress.get("status") not in ("assumed", "confirmed") or not progress.get("source"):
        raise PlanError("次数模型需要明确突破、命座和技能基级来源")
    for name, limit in (("ascension", 6), ("constellation", 6)):
        if type(progress.get(name)) is not int or not 0 <= progress[name] <= limit:
            raise PlanError("次数模型的实际" + name + "尚未填写")
    base_level = (progress.get("talent_base_levels") or {}).get("skill")
    if type(base_level) is not int or not 1 <= base_level <= 10:
        raise PlanError("次数模型的实际战技基级尚未填写")
    level = roster[0]["level"]
    if "level" in gear and gear["level"] != level:
        raise PlanError("角色等级与配装等级不一致")
    weapon = gear.get("weapon") or {}
    if any(type(weapon.get(k)) is not int for k in ("weapon_id", "level", "ascension", "refinement")):
        raise PlanError("次数模型需要武器ID、等级、突破和整体精炼；空精炼槽不是精1")
    if gear.get("mode") == "panel":
        presence = gear.get("artifact_presence") or {}
        empty = set(presence) == set(SLOTS) and all(x == "empty" for x in presence.values()) and not gear["artifact_sets"]
    else:
        artifacts = gear["artifacts"]
        empty = set(artifacts) == set(SLOTS) and all(type(x) is dict and x.get("state") == "empty" for x in artifacts.values())
    if not empty:
        raise PlanError("次数模型要求五槽明确未装备；空套装列表不能证明无圣遗物")
    # Existing typed declarations cannot disagree with actual growth values.
    from .action_resources import _identity_evidence
    evidence, _ = _identity_evidence({**plan, "action_resource_sources": plan.get("action_resource_sources", [])},
                                         {a["id"]: a for a in rotation.get("abilities", [])})
    from .mechanism_execution_scope import identities
    evidence[actor] = [*evidence.get(actor, []), *identities(plan, actor)]
    for item in evidence.get(actor, []):
        if item.get("constellation") not in (None, progress["constellation"]):
            raise PlanError("实际命座与已有技能来源矛盾")
        if item.get("skill_depot_id") not in (None, target["skill_depot_id"]):
            raise PlanError("实际技能组与已有来源矛盾")
    for source in plan.get("hit_parameter_sources", []):
        hit = next((h for h in plan["manual_template"].get("hits", []) if h["id"] == source["hit_id"]), None)
        if hit and hit["character"] == actor:
            s = source["selection"]
            slot = s.get("slot")
            if s.get("skill_id") == target["skill_id"]:
                if slot not in (None, "skill"):
                    raise PlanError("战技目标与倍率来源技能槽矛盾")
                slot = "skill"
            declared = (progress.get("talent_base_levels") or {}).get(slot)
            if s.get("base_level") is not None and declared is not None and s["base_level"] != declared:
                raise PlanError("实际技能基级与对应槽倍率来源矛盾")
    board = plan.get("extensions", {}).get("whiteboard_handoff", {})
    if actor in board.get("constellations", {}) and board["constellations"][actor] != progress["constellation"]:
        raise PlanError("实际命座与白板交接矛盾")
    context = plan.get("action_capacity_context")
    validate_context(context)
    selected = {**{k: target[k] for k in ("character_id", "skill_depot_id", "skill_id", "variant")},
                "constellation": progress["constellation"], "character_level": level, "ascension": progress["ascension"],
                "base_level": base_level, "weapon": {"id": weapon["weapon_id"], "level": weapon["level"], "refinement": weapon["refinement"]},
                "artifacts": "all_empty", "team": "self_only"}
    actual = {"actor": actor, "selection": copy.deepcopy(selected), "context": copy.deepcopy(context),
              "weapon_ascension": weapon["ascension"], "presence": {slot: "empty" for slot in SLOTS}}
    return selected, context, {**actual, "fingerprint": fingerprint(actual)}
