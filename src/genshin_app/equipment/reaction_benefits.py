"""Actual-plan scope for the bounded equipment reaction-benefit projection."""
from __future__ import annotations

import copy

from genshin_engine.elemental import validate_elemental_model
from genshin_engine.elemental_schema import FREEZE_RULESET

PROFILE_ID = "equipment-ordinary-swirl-benefits-v1"
CERTIFICATE_KIND = "ordinary_swirl_benefit_projection_certificate"


def _affix(ref):
    if not isinstance(ref, dict):
        return None
    if ref.get("table") == "EquipAffixExcelConfigData":
        key = ref.get("key")
        return key.get("affixId") if isinstance(key, dict) else None
    owner = ref.get("owner")
    return ref.get("affix_id", owner.get("affix_id") if isinstance(owner, dict) else None)


def reaction_projection_scope(plan, binding, requirement, *, inactive_binding_ids=()):
    """Do not infer game identities from Chinese names or matching numeric values."""
    cid = binding["provider_character_id"]
    gear = plan["equipment_inputs"][cid]
    owner = requirement["owner"]
    scenario = binding["upstream_recipe"]["scenario"]
    model = plan.get("mechanics_model") or {}
    elemental = model.get("elemental_model") or {}
    target = elemental.get("target") or {}
    issues = []
    if (binding["owner"] != owner or owner["kind"] != "artifact_set" or owner.get("required_count") != 4
            or not any(s["set_id"] == owner.get("set_id") and s["count"] >= 4 for s in gear.get("artifact_sets", []))):
        issues.append("反应收益投影需要当前提供者实际启用的同一四件套来源")
    if gear.get("mode") != "panel" or gear.get("panel_policy") != "static_included":
        issues.append("当前反应收益投影仅支持已含两件套常驻属性的给定面板")
    if any(g.get("mode") != "panel" or g.get("panel_policy") != "static_included"
           for g in plan["equipment_inputs"].values()):
        issues.append("当前固定面板场景不同时编译逐件装备属性；请使用明确的给定面板")
    if elemental.get("ruleset") != FREEZE_RULESET:
        issues.append("反应来源收益需要已接受的普通可冻结目标参考模型")
    elif validate_elemental_model(elemental):
        issues.append("元素参考模型尚未通过目标初态、范围或假设确认检查")
    if scenario.get("target_id") is None or scenario.get("target_id") != target.get("id"):
        issues.append("来源场景的原目标必须与当前元素模型唯一目标精确一致")
    targets = {d.get("application", {}).get("target_id") for key in ("hit_events", "hit_patterns", "application_events")
               for d in model.get(key, []) if d.get("application")}
    if targets - {target.get("id")}:
        issues.append("当前原目标收益投影不能交付到另一个目标的命中或附着")
    if binding["upstream_recipe"].get("ability_refs"):
        issues.append("扩散反应来源不使用爆发动作引用，请移除ability_refs")
    if model.get("manual_buff_repeat", "each_cycle") != "each_cycle":
        issues.append("普通扩散常驻加成须覆盖所有检查窗口；manual_buff_repeat=once不在当前范围")
    hits = plan["manual_template"]["hits"]
    if any(h["damage_system"] not in ("classic", "transformative") for h in hits):
        issues.append("本来源仅投影普通伤害；月曜、星烁或其他伤害系统尚未支持")
    for other_cid, other in plan["equipment_inputs"].items():
        if (other_cid != cid
                and any(s.get("set_id") == owner.get("set_id") and s.get("count", 0) >= 4
                        for s in other.get("artifact_sets", []))):
            issues.append("另一提供者实际装备同源四件套；唯一减抗的覆盖/竞争尚未支持")
    declared = []
    for other in plan["effect_bindings"]:
        if other is binding or other["binding_id"] in inactive_binding_ids:
            continue
        refs = other.get("source_refs", [])
        same_owner = other.get("owner") == owner
        same_affix = any(str(_affix(ref)) == str(owner.get("affix_id")) and _affix(ref) is not None for ref in refs)
        if same_owner or same_affix:
            issues.append("其他效果分项已经显式声明同一四件套来源，不能重复交付反应加成或减抗")
        declared.append({"kind": "binding", "id": other["binding_id"], "owner": other["owner"], "source_refs": refs})
    # Template buffs can carry typed source references in extensions. Free prose
    # and numerically identical benefits do not prove a shared equipment source.
    for buff in plan["manual_template"].get("buffs", []):
        ext = buff.get("extensions") or {}
        refs = ext.get("source_refs", [])
        if isinstance(refs, list) and any(isinstance(ref, dict) and _affix(ref) is not None
                and str(_affix(ref)) == str(owner.get("affix_id")) for ref in refs):
            issues.append("手工增益的精确来源引用与当前自动四件套来源重复")
        declared.append({"kind": "manual_buff", "id": buff["id"], "extensions": ext})
    proof = {"scope": "ordinary_hydro_cryo_original_target", "owner": owner, "provider": cid,
             "equipment": plan["equipment_inputs"], "target_ruleset": elemental.get("ruleset"), "target": target,
             "reference_acceptance": elemental.get("reference_acceptance"), "declared_target_ids": sorted(targets),
             "hit_systems": [{"id": h["id"], "character": h["character"], "system": h["damage_system"]} for h in hits],
             "manual_buff_repeat": model.get("manual_buff_repeat", "each_cycle"), "other_declared_sources": declared,
             "known_inactive_automatic_bindings": sorted(inactive_binding_ids),
             "identity_coverage": "explicit_typed_sources_only",
             "provider_presence_scope": "dynamic_resistance_shred_only_static_owner_bonus_remains",
             "scenario": scenario}
    return issues, copy.deepcopy(proof)
