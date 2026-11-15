"""Preserve explicit review gaps when a GUI build edit changes equipment."""
from __future__ import annotations

import copy

from .composer import compose_equipment_plan
from .schema import PlanError, validate_plan


def _selection(equipment):
    weapon = equipment.get("weapon")
    if weapon is not None:
        weapon = {key: weapon.get(key) for key in ("weapon_id", "level", "ascension", "refinements")}
        weapon["refinements"] = sorted(weapon.get("refinements") or [],
            key=lambda item: (item["source_index"], item["affix_group_id"]))
    return {"weapon": weapon,
            "artifact_sets": sorted(equipment.get("artifact_sets", []), key=lambda item: item["set_id"]),
            "artifacts": equipment.get("artifacts")}


def prepare_equipment_edit(plan, previous_plan):
    """Keep ambiguous manual equipment benefits pending in the saved candidate.

    Typed weapon/set bindings already deactivate through source matching. Manual
    sources cannot prove the same relationship, so only the affected provider's
    untyped benefits require review. Dependency fingerprints are never renewed.
    """
    validate_plan(plan)
    validate_plan(previous_plan)
    candidate = copy.deepcopy(plan)
    old_characters = {row["id"]: row for row in previous_plan["manual_template"]["characters"]}
    rotation_characters = {row["id"]: row for row in (candidate.get("rotation_model") or {}).get("characters", [])}
    for character in candidate["manual_template"]["characters"]:
        cid = character["id"]
        if candidate["equipment_inputs"].get(cid, {}).get("mode") != "panel" or cid not in old_characters:
            continue
        old_stats, stats = old_characters[cid]["stats"], character["stats"]
        cleared = [key for key, value in old_stats.items()
                   if key != "dmg_bonus" and value is not None and stats.get(key) is None]
        cleared.extend("dmg_bonus." + key for key, value in old_stats.get("dmg_bonus", {}).items()
                       if value is not None and stats.get("dmg_bonus", {}).get(key) is None)
        if cleared:
            raise PlanError("已清空原先填写的面板数值；未知不等于零。请填写新数值，明确没有该属性时填 0，或保留原值。",
                "unknown_panel", {"missing_inputs": [{"code": "unknown_panel", "action": "edit_panel",
                    "path": f"manual_template.characters.{cid}.stats.{key}",
                    "message": "此面板数值已清空，请填写明确数值后再应用。"} for key in cleared]})
        value = character["stats"].get("energy_recharge")
        if value != old_characters[cid]["stats"].get("energy_recharge") and cid in rotation_characters:
            rotation_characters[cid]["energy_recharge"] = value
    changed = {cid for cid in candidate["equipment_inputs"]
        if _selection(candidate["equipment_inputs"][cid]) != _selection(previous_plan["equipment_inputs"].get(cid, {}))}
    if not changed:
        validate_plan(candidate)
        return candidate
    reason = "换装后该手工效果未绑定具体武器或套装身份，无法自动确认是否仍然适用；请让 Agent 复核来源、条件和数值。"
    for binding in candidate["effect_bindings"]:
        if binding["provider_character_id"] not in changed or binding["owner"]["kind"] != "manual":
            continue
        for part in binding["parts"]:
            if part["state"] != "excluded":
                part.update(state="pending", reason=reason)
    dependencies = candidate.setdefault("manual_effect_dependencies", [])
    for buff in candidate["manual_template"].get("buffs", []):
        owner = buff.get("extensions", {}).get("owner")
        if owner not in changed and owner:
            continue
        for effect in buff["effects"]:
            ref = {"buff_id": buff["id"], "effect_id": effect["id"]}
            dependency = next((row for row in dependencies if row["effect_ref"] == ref), None)
            if dependency is None:
                dependency = {"effect_ref": ref, "context_fingerprint": None}
                dependencies.append(dependency)
            dependency.update(mode="unreviewed", note=reason)
    candidate.update(resolution={"state": "pending"}, resolved_template=None)
    validate_plan(candidate)
    return candidate


def preview_equipment_edit(plan, previous_plan, db_path):
    return compose_equipment_plan(prepare_equipment_edit(plan, previous_plan), db_path)
