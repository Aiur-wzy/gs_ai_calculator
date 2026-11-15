"""Explicit fixed-source ownership and axis tuning, separate from timed rules."""
from __future__ import annotations

import copy

from .files import context_view, fingerprint, input_view
from .schema import PlanError


def target_projection_scope(plan, binding, requirement):
    """Validate the native scenario against current equipment and actual inputs."""
    from genshin_engine.elemental import validate_elemental_model
    from genshin_engine.elemental_schema import FREEZE_RULESET

    cid = binding["provider_character_id"]
    gear = plan["equipment_inputs"][cid]
    owner = requirement["owner"]
    model = plan.get("mechanics_model") or {}
    elemental = model.get("elemental_model") or {}
    issues = []
    if (binding["owner"] != owner or owner["kind"] != "artifact_set" or owner.get("required_count") != 4
            or not any(s["set_id"] == owner.get("set_id") and s["count"] >= 4 for s in gear.get("artifact_sets", []))):
        issues.append("固定套装投影需要当前角色实际启用的同一四件套来源")
    if gear.get("mode") != "panel" or gear.get("panel_policy") != "static_included":
        issues.append("当前目标条件投影仅支持已含常驻属性的给定面板")
    if elemental.get("ruleset") != FREEZE_RULESET:
        issues.append("目标条件收益需要已接受的普通可冻结目标参考模型")
    elif validate_elemental_model(elemental):
        issues.append("元素参考模型尚未通过结构、目标范围或假设确认检查")
    hits = [h for h in plan["manual_template"]["hits"] if h["character"] == cid]
    if any(h["damage_system"] not in ("classic", "transformative") for h in hits):
        issues.append("此来源的月曜/星烁分支尚未支持，不能给出包含这些命中的完整计算")
    if binding["upstream_recipe"].get("ability_refs"):
        issues.append("固定装备来源不使用爆发动作引用，请移除旧触发来源的ability_refs")
    proof = {"owner": owner, "provider": cid, "equipment": gear,
             "target_ruleset": elemental.get("ruleset"), "target": elemental.get("target"),
             "reference_acceptance": elemental.get("reference_acceptance"),
             "owner_hit_systems": [{"id": h["id"], "system": h["damage_system"]} for h in hits]}
    return issues, proof


def prepare_fixed_sources(plan, applicable, template, source_fingerprint, missing, *, context_plan=None):
    model = plan["mechanics_model"]
    sources = {s["id"]: s for s in model.get("fixed_effect_sources", [])}
    rules = model["rules"]
    buffs = template.get("buffs", [])
    other_ids = {r["id"] for r in rules} | {b["id"] for b in buffs}
    if other_ids & sources.keys():
        raise PlanError("固定条件来源ID与其他规则或手工增益冲突", "duplicate_delivery")
    identities = [(r["owner"], r["source_id"]) for r in rules]
    identities += [(b.get("extensions", {}).get("owner"), b.get("extensions", {}).get("source_id")) for b in buffs]
    identities += [(s["owner"], s["source_id"]) for s in sources.values()]
    fixed_identities = {(s["owner"], s["source_id"]) for s in sources.values()}
    if any(identities.count(identity) > 1 for identity in fixed_identities):
        raise PlanError("固定条件来源与其他交付重复使用同一提供者/来源", "duplicate_delivery")
    linked, active = {}, set()
    applicable_ids = {(b["binding_id"], p["part_id"]) for b, p in applicable}
    for binding in plan["effect_bindings"]:
        for part in binding["parts"]:
            rule = part.get("rule") or {}
            if rule.get("kind") != "fixed_effect_source":
                continue
            sid = rule["fixed_source_id"]
            if sid not in sources or sid in linked:
                raise PlanError("固定条件来源必须存在且只能由一个分项交付", details={"fixed_source_id": sid})
            linked[sid] = (binding["binding_id"], part["part_id"])
            src = sources[sid]
            if src["owner"] != binding["provider_character_id"] or src["source_id"] != f"{binding['binding_id']}:{part['part_id']}":
                raise PlanError("固定条件来源的提供者或来源分项不一致", details={"fixed_source_id": sid})
            if linked[sid] in applicable_ids:
                if any(c["scope"] != "always" for c in part["conditions"]):
                    missing("unsupported_effect", f"effect_bindings.{binding['binding_id']}.parts.{part['part_id']}",
                            "固定来源不能使用部件时间区间；目标条件由每次命中前的状态判断", "edit_mechanics")
                else:
                    active.add(sid)
    for sid in sorted(sources.keys() - linked.keys()):
        missing("effect_pending", f"mechanics_model.fixed_effect_sources.{sid}.source_id",
                "固定条件来源需要唯一的fixed_effect_source规则和交付分项", "edit_mechanics")
    known_hits = {h["hit_id"] for h in model["hit_events"]}
    for pattern in model["hit_patterns"]:
        known_hits.update(f"{pattern['id']}:{i + 1}" for i in range(len(pattern["offsets"])))
    translated, previews = [], []
    for match in plan.get("rotation_tuning", {}).get("buff_hit_matches", []):
        ref = match["effect_ref"]
        if "fixed_source_id" not in ref:
            continue
        sid, eid = ref["fixed_source_id"], ref["effect_id"]
        path = f"rotation_tuning.buff_hit_matches.{match['id']}"
        if sid not in sources:
            if sid.startswith("upstream.") and any("upstream_recipe" in b for b in plan["effect_bindings"]):
                missing("upstream_pending", path, "上游固定来源尚未生成；核对当前来源后重新确认微调", "review_match")
                previews.append({"id": match["id"], "effect_ref": ref, "kind": "fixed_effect_source",
                                 "basis_fingerprint": None, "base_hit_ids": None, "final_hit_ids": None})
                continue
            raise PlanError("微调引用不存在的固定条件来源")
        if not any(e["id"] == eid for e in sources[sid]["effects"]):
            raise PlanError("微调引用不存在的固定条件收益")
        requested = set(match.get("hit_ids", []) + match.get("include_hit_ids", []) + match.get("exclude_hit_ids", []))
        if not requested <= known_hits:
            raise PlanError("固定条件微调引用不存在的实际命中", details=sorted(requested - known_hits))
        basis = fingerprint({"source_fingerprint": source_fingerprint,
                             "context": context_view(input_view(context_plan or plan)), "match_id": match["id"]})
        previews.append({"id": match["id"], "effect_ref": ref, "kind": "fixed_effect_source", "basis_fingerprint": basis,
                         "base_hit_ids": None, "final_hit_ids": None, "requested_hit_ids": sorted(requested),
                         "added_hit_ids": sorted(match.get("include_hit_ids", [])),
                         "removed_hit_ids": sorted(match.get("exclude_hit_ids", [])),
                         "note": "逐击目标条件在运行时判断；人为增补不能将未知状态变为已知，也不能越过提供者和伤害系统范围。"})
        if sid not in active:
            missing("effect_pending", path, "固定条件来源未启用，不能应用微调", "review_match")
            continue
        if match["state"] != "confirmed" or match["basis_fingerprint"] != basis:
            missing("dependency_stale", path, "固定条件微调需要按当前来源、目标和动作重新核对", "review_match")
            continue
        if match['mode'] == 'runtime_adjust':
            translated.extend({'fixed_source_id': sid, 'effect_id': eid, **copy.deepcopy(row)} for row in match['runtime_events'])
            continue
        include, exclude = set(match.get("include_hit_ids", [])), set(match.get("exclude_hit_ids", []))
        if match["mode"] == "replace":
            include = set(match["hit_ids"])
            exclude = known_hits - include
        for operation, ids in (("include", include), ("exclude", exclude)):
            translated.extend({"fixed_source_id": sid, "effect_id": eid, "hit_id": hid, "operation": operation}
                              for hid in sorted(ids))
    return [copy.deepcopy(s) for s in sources.values() if s["id"] in active], translated, previews
