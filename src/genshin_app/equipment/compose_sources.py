"""Compile source bindings and static equipment effects before runtime assembly."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from genshin_db import queries

from .compose_actions import ActionPreparation
from .compose_context import ComposeContext
from .compose_diagnostics import EFFECT_BINDING_REPAIR_HINT
from .compose_loadout import LoadoutResolution
from .files import fingerprint
from .panel_calculation import calculate_static_panel
from .schema import PlanError
from .tuning import generated_id


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceCompilation:
    upstream_compilations: list[Any]
    elemental_compilations: list[Any]
    reaction_compilations: list[Any]
    applicable: list[Any]


def _approved_projection(compiled, binding):
    """A partial-source certificate is accepted only for an exact server recipe."""
    approved = {
        "equipment-target-state-classic-v1": (
            "persistent_equipment", "target_state_projection_certificate", 2
        ),
        "equipment-ordinary-swirl-benefits-v1": (
            "elemental_reaction", "ordinary_swirl_benefit_projection_certificate", 2
        ),
        "ordinary-reaction-flat-team-em-reference-v1": (
            "elemental_reaction", "reaction_flat_em_projection_certificate", 2
        ),
    }
    profile = binding.get("upstream_recipe", {}).get("scenario", {}).get("profile_id")
    if profile not in approved:
        return False
    kind, certificate, profile_version = approved[profile]
    coverage = compiled.get("coverage_certificate", {})
    provenance = compiled.get("provenance", {})
    return (compiled.get("status") == compiled.get("projection_status") == "ready"
            and compiled.get("whole_source_status") == "partial"
            and compiled.get("execution_binding", {}).get("kind") == kind
            and provenance.get("profile_id") == profile
            and provenance.get("profile_version") == profile_version
            and coverage.get("kind") == certificate and coverage.get("source_complete") is False
            and coverage.get("projection_complete") is True and coverage.get("projection_status") == "ready")


def compile_compose_sources(
    context: ComposeContext, actions: ActionPreparation,
    loadout: LoadoutResolution, db_path: Path,
) -> SourceCompilation:
    plan = context.plan
    template = context.template
    warnings = context.warnings
    assumptions = context.assumptions
    requirements = context.requirements
    sources = context.sources
    preview = context.preview
    bases = context.bases
    contributions = context.contributions
    fixed_panels = context.fixed_panels
    panel_bases = context.panel_bases
    context_fp = context.context_fp
    missing = context.missing
    resource_compilations = actions.resource_compilations
    metadata = actions.metadata
    periodic = actions.periodic
    meta = loadout.meta

    # The user input stays separate from server-derived bindings and D20 rules.
    from .upstream import compile_bindings
    upstream_compilations, upstream_sources = compile_bindings(plan, requirements, db_path, missing,
                                                             resource_compilations=resource_compilations)
    sources.extend(upstream_sources)
    from .elemental_sources import compile_elemental_sources
    elemental_compilations, elemental_sources = compile_elemental_sources(plan, db_path, missing,
        prepared_profiles={**metadata.attenuation_profiles, **periodic.attenuation_profiles},
        prepared_identities={**metadata.application_identities, **periodic.application_identities})
    sources.extend(elemental_sources)
    from .reaction_sources import compile_reaction_damage_sources
    reaction_compilations, reaction_sources = compile_reaction_damage_sources(plan, db_path, missing)
    sources.extend(reaction_sources)
    for item in reaction_compilations:
        if item["status"] == "ready":
            assumptions.append("普通冰水扩散伤害的二元序列与计时来自数据库；原目标、归属和次序采用有限扩散规则集，"
                               "未执行一般能力、Alter、Star或传播：" + item["binding_basis"])
    for compiled in upstream_compilations:
        if compiled["status"] == "ready":
            assumptions.append(f"{compiled['binding_id']}：采用受限上游适配模型，游戏语义包含显式假设，尚未完全验证")
    # Bindings are reconciled with the selected equipment before any output is generated.
    applicable = []
    for binding in plan["effect_bindings"]:
        bid, owner = binding["binding_id"], binding["owner"]
        req = next((r for r in requirements if r["binding_id"] == bid), None)
        if owner["kind"] in ("weapon", "artifact_set") and req is None:
            warnings.append({"code": "inactive_binding", "binding_id": bid,
                             "message": "所属装备/套装阈值当前未启用；旧输出已撤销"})
            continue
        if owner["kind"] in ("manual", "character_skill"):
            actual_source = {"owner": owner, "source_refs": binding["source_refs"]}
            if owner["kind"] == "character_skill":
                if meta is None:
                    missing("equipment_unavailable", f"effect_bindings.{bid}", "角色技能来源不可用", binding_id=bid)
                    continue
                detail = queries.get_character_detail(db_path, owner["character_id"], owner["version_line"])
                for field, collection in (("skill_id", "skills"), ("talent_id", "talents")):
                    if field in owner and not any(row["id"] == owner[field] for row in detail[collection]):
                        raise PlanError(f"{bid}: 技能来源身份不属于指定角色")
                if "proud_skill_group_id" in owner:
                    gid = owner["proud_skill_group_id"]
                    related = [*detail.get("skills", []), *detail.get("extra_info", [])]
                    if not any(row.get("proud_skill_group_id") == gid for row in related):
                        raise PlanError(f"{bid}: 参数组来源身份不属于指定角色")
                    if "skill_id" in owner and not any(row.get("id") == owner["skill_id"]
                            and row.get("proud_skill_group_id") == gid for row in detail.get("skills", [])):
                        raise PlanError(f"{bid}: 技能与参数组来源身份不一致")
                if not any(field in owner for field in ("skill_id", "talent_id", "proud_skill_group_id")):
                    raise PlanError(f"{bid}: 角色技能 owner 需要精确技能/天赋/参数组身份")
                actual_source["detail"] = detail
                sources.append(actual_source)
            req = {"binding_id": bid, "provider_character_id": binding["provider_character_id"],
                   "owner": owner, "source_refs": binding["source_refs"],
                   "source_fingerprint": fingerprint(actual_source), "effect": actual_source,
                   "proposed_binding": copy.deepcopy(binding)}
            requirements.append(req)
            sources.append(actual_source)
        source_fp = req["source_fingerprint"]
        coverage = binding["coverage"]
        projection = next((c for c in upstream_compilations if c["binding_id"] == bid
                           and _approved_projection(c, binding)), None)
        covered = coverage["state"] == "complete" or (coverage["state"] == "projection" and projection is not None)
        valid_coverage = (covered and coverage["source_fingerprint"] == source_fp
                          and binding["source_fingerprint"] == source_fp and coverage["reviewed_by"] and coverage["note"])
        if not valid_coverage or not binding["parts"]:
            missing("effect_pending", f"effect_bindings.{bid}.coverage", "完整机制覆盖待核对或来源已变",
                    binding_id=bid, repair_hint=EFFECT_BINDING_REPAIR_HINT)
        for part in binding["parts"]:
            pid, ppath = part["part_id"], f"effect_bindings.{bid}.parts.{part['part_id']}"
            if part["state"] == "excluded":
                assumptions.append(f"{bid}/{pid} 明确不计：{part['reason']}")
                continue
            if part["state"] == "pending":
                missing("effect_pending", ppath, part["reason"] or "效果部件待补齐", binding_id=bid, part_id=pid)
                continue
            if any(c["state"] != "confirmed" for c in part["conditions"]):
                missing("effect_pending", ppath + ".conditions", "条件尚未确认", binding_id=bid, part_id=pid)
                continue
            dependency = part["dependencies"]
            if dependency["mode"] == "manual_context" and dependency["context_fingerprint"] != context_fp:
                missing("dependency_stale", ppath + ".dependencies", "人工折算依赖已变化，需要复核当前摘要",
                        binding_id=bid, part_id=pid)
                continue
            delivery = part["delivery"]
            if delivery["kind"] == "panel" and delivery["target_character_id"] in fixed_panels:
                missing("unsupported_effect", ppath,
                        "给定面板已含常驻属性，不能再次交付面板贡献；已包含项请明确 excluded，条件效果请交付 Buff",
                        binding_id=bid, part_id=pid, category="request_error",
                        repair_hint="只排除已经计入面板的贡献；尚未计入的条件收益改用Buff或相应事件交付。")
                continue
            if delivery["kind"] == "panel" and any(c["scope"] != "always" for c in part["conditions"]):
                missing("unsupported_effect", ppath, "区间条件必须交付 Buff/明确事件", binding_id=bid, part_id=pid,
                        category="request_error", repair_hint="保留该条件收益，使用Buff或明确事件表达生效区间，不要追加常驻面板。")
                continue
            if not valid_coverage:
                continue
            applicable.append((binding, part))
            if part["rule"]["kind"] == "constant" and delivery["kind"] == "panel":
                target = delivery["target_character_id"]
                if target not in bases:
                    missing("missing_input", ppath, "面板目标必须有可拆分基础", "edit_basis", bid, pid)
                    continue
                contributions[target].append({"contribution_id": generated_id("panel", bid, pid),
                    "stat": delivery["stat"], "operation": delivery["operation"], "value": part["rule"]["value"],
                    "source_refs": binding["source_refs"]})
    static = {cid: calculate_static_panel(base, contributions[cid]) for cid, base in bases.items()}
    static.update(copy.deepcopy(fixed_panels))
    base_stages = {**bases, **panel_bases}
    for binding, part in applicable:
        bid, pid = binding["binding_id"], part["part_id"]
        rule, delivery = part["rule"], part["delivery"]
        path = f"effect_bindings.{bid}.parts.{pid}"
        if rule["kind"] == "mechanics":
            preview["generated_outputs"].append({"binding_id": bid, "part_id": pid,
                                                  "mechanics_rule_id": rule["rule_id"]})
            continue
        if rule["kind"] == "fixed_effect_source":
            preview["generated_outputs"].append({"binding_id": bid, "part_id": pid,
                                                  "fixed_source_id": rule["fixed_source_id"]})
            continue
        if rule["kind"] == "constant" and delivery["kind"] == "panel":
            continue
        if rule["kind"] == "stat_scale":
            provider = rule["from"]["character_id"]
            stage = base_stages if rule["from"]["stage"] == "base" else static
            if provider not in stage or rule["from"]["stat"] not in stage[provider]:
                missing("missing_input", path, "转化提供者缺少明确给定或已核实的 base/static 属性", "edit_basis", bid, pid)
                continue
            value = rule["factor"] * stage[provider][rule["from"]["stat"]] + rule.get("offset", 0)
            value = max(rule.get("min", -math.inf), min(rule.get("max", math.inf), value))
        else:
            value = rule.get("value")
        if value is not None and not math.isfinite(value):
            missing("unsupported_effect", path, "效果数值溢出", binding_id=bid, part_id=pid)
            continue
        if delivery["kind"] == "panel":
            target = delivery["target_character_id"]
            if target not in bases:
                missing("missing_input", path, "面板目标缺少可拆分基础", "edit_basis", bid, pid)
                continue
            contributions[target].append({"contribution_id": generated_id("panel", bid, pid),
                "stat": delivery["stat"], "operation": delivery["operation"], "value": value,
                "source_refs": binding["source_refs"]})
        elif delivery["kind"] == "hit":
            hit = copy.deepcopy(rule["hit"])
            hit["id"] = generated_id("hit", bid, pid)
            if any(c["scope"] == "intervals" and not any(i["start"] <= hit["t"] < i["end"] for i in c["intervals"])
                   for c in part["conditions"]):
                assumptions.append(f"{bid}/{pid} 给定事件不在明确条件区间内")
                continue
            if any(h["id"] == hit["id"] for h in template["hits"]):
                raise PlanError("生成事件 ID 与手工事件冲突")
            template["hits"].append(hit)
            preview["generated_outputs"].append({"binding_id": bid, "part_id": pid, "hit": hit})
        elif delivery["kind"] == "buff":
            intervals = [{"start": delivery["start"], "end": delivery["end"]}]
            for condition in part["conditions"]:
                if condition["scope"] == "intervals":
                    intervals = [{"start": max(a["start"], b["start"]), "end": min(a["end"], b["end"])}
                                 for a in intervals for b in condition["intervals"]
                                 if max(a["start"], b["start"]) < min(a["end"], b["end"])]
            # Merge overlapping intervals so one declared benefit is never counted twice.
            merged = []
            for interval in sorted(intervals, key=lambda i: (i["start"], i["end"])):
                if merged and interval["start"] <= merged[-1]["end"]:
                    merged[-1]["end"] = max(merged[-1]["end"], interval["end"])
                else:
                    merged.append(interval.copy())
            for i, interval in enumerate(merged):
                buff_id = generated_id("buff", bid, pid) + (f".interval.{i}" if len(merged) > 1 else "")
                buff = {"id": buff_id, "source": delivery.get("source", part["label"]), **interval,
                         "scope": delivery["scope"], "target": delivery["target"],
                         "effects": [{**copy.deepcopy(delivery["effect"]), "value": value}]}
                if plan["plan_version"] == 3:
                    buff["extensions"] = {"owner": binding["provider_character_id"], "source_id": f"{bid}:{pid}"}
                generated_effect = buff["effects"][0]
                if (generated_effect.get("applies_to") or {}).get("mode") == "none" and part.get("reason"):
                    generated_effect.setdefault("extensions", {}).setdefault("exclusion_reason", part["reason"])
                if any(b["id"] == buff_id for b in template.get("buffs", [])):
                    raise PlanError("生成 Buff ID 与手工 Buff 冲突")
                template.setdefault("buffs", []).append(buff)
                preview["generated_outputs"].append({"binding_id": bid, "part_id": pid, "buff": buff})
            if not merged:
                assumptions.append(f"{bid}/{pid} 明确条件交集为空，未生成 Buff")

    for cid, base in bases.items():
        final = calculate_static_panel(base, contributions[cid])
        char = next(c for c in template["characters"] if c["id"] == cid)
        char["stats"], char["level"] = final, plan["equipment_inputs"][cid]["level"]
        preview["characters"][cid] = {"base": base, "static": static[cid], "final": final,
                                       "contributions": contributions[cid]}
    for cid, panel in fixed_panels.items():
        preview["characters"][cid] = {"base": panel_bases[cid], "static": copy.deepcopy(panel),
                                       "final": copy.deepcopy(panel), "contributions": []}

    return SourceCompilation(
        upstream_compilations=upstream_compilations,
        elemental_compilations=elemental_compilations,
        reaction_compilations=reaction_compilations,
        applicable=applicable,
    )


__all__ = ["SourceCompilation", "compile_compose_sources"]

