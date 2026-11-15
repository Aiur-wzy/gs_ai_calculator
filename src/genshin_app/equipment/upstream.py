"""Fresh, bounded runtime assembly into an ephemeral D20 composition plan."""
from __future__ import annotations

import copy
import sqlite3

from genshin_db import queries
from genshin_db.equipment import EquipmentError

from .files import fingerprint
from .schema import PlanError

RESERVED_PREFIX = "upstream."


def _affix_ref(ref):
    if ref.get("table") == "EquipAffixExcelConfigData":
        return (ref.get("key") or {}).get("affixId") if isinstance(ref.get("key"), dict) else None
    return ref.get("affix_id")


def _inactive_equipment_bindings(plan, requirements):
    """Absence from requirements is necessary, but not proof after a DB failure."""
    active = {r["binding_id"] for r in requirements}
    inactive = set()
    for binding in plan["effect_bindings"]:
        if binding["binding_id"] in active or "upstream_recipe" not in binding:
            continue
        owner = binding["owner"]
        gear = plan["equipment_inputs"].get(binding["provider_character_id"])
        if gear is None:
            continue
        if owner["kind"] == "artifact_set" and gear.get("mode") == "panel":
            count = sum(s["count"] for s in gear["artifact_sets"] if s["set_id"] == owner.get("set_id"))
            if count < owner["required_count"]:
                inactive.add(binding["binding_id"])
        elif owner["kind"] == "weapon" and "weapon" in gear:
            weapon = gear["weapon"]
            if weapon is None or weapon["weapon_id"] != owner.get("weapon_id"):
                inactive.add(binding["binding_id"])
    return inactive


def _burst_actions(plan, binding, db_path, missing, *, resource_compilations=None):
    """Validate typed local Q identities against the provider's actual DB character."""
    cid, bid = binding["provider_character_id"], binding["binding_id"]
    gear = plan["equipment_inputs"][cid]
    ref = gear.get("character_ref")
    path = f"effect_bindings.{bid}.upstream_recipe.ability_refs"
    abilities = [a for a in (plan.get("rotation_model") or {}).get("abilities", [])
                 if a["character"] == cid and a["action_type_id"] == "action.elemental_burst"]
    if not ref:
        missing("missing_input", path, "爆发触发需要提供者绑定数据库角色及精确技能引用", binding_id=bid)
        return None, None
    fresh = {r["ability_id"]: r for r in resource_compilations or [] if r.get("actor") == cid}
    # Fresh resource bindings use the same materialized values as the executor.
    # Legacy unbound abilities retain their prior query and compatibility rules.
    need_legacy = any(a["id"] not in fresh for a in abilities) or not abilities
    detail = queries.get_character_detail(db_path, ref["db_character_id"], ref["version_line"]) if need_legacy else {"skills": []}
    burst_ids = {s["id"] for s in detail["skills"] if s["slot"] == "burst"}
    burst_ids.update(r["resolved_selection"]["skill_id"] for r in fresh.values()
                     if "burst" in r.get("resolved_selection", {}).get("matching_slots", []))
    refs = {r["ability_id"]: r["skill_id"] for r in binding["upstream_recipe"].get("ability_refs", [])}
    if not abilities or set(refs) != {a["id"] for a in abilities} or not set(refs.values()) <= burst_ids:
        missing("missing_input", path, "请将提供者的每种爆发动作能力映射到该角色数据库中的爆发技能；不能以名称或耗能代替身份", binding_id=bid)
        return None, None
    cooldown_by_skill = {}
    for ability in abilities:
        resource = fresh.get(ability["id"])
        if resource:
            selected = resource.get("resolved_selection", {})
            if (resource["status"] != "ready" or selected.get("skill_id") != refs[ability["id"]]
                    or selected.get("character_id") != ref["db_character_id"] or resource["version_line"] != ref["version_line"]):
                missing("source_rule_mismatch", path, "基础动作资源来源尚未就绪或与爆发来源身份不符", binding_id=bid)
                return None, None
            p = resource["projection"]
            cost, cooldown, charges = (p["base_energy_cost"]["value"], p["base_cooldown_seconds"]["value"],
                                       p["base_charge_count"]["value"])
        else:
            skill = next(s for s in detail["skills"] if s["id"] == refs[ability["id"]])
            cost, cooldown, charges = skill.get("energy_cost"), skill.get("cooldown"), skill.get("max_charge_num") or 1
        declared_energy, declared_cd = ability.get("energy") or {}, ability.get("cooldown") or {}
        if cost is None or cooldown is None or any(declared_energy.get(k) != cost for k in ("required", "amount")) or (
                declared_cd.get("duration") != cooldown or declared_cd.get("charges") != charges):
            missing("source_rule_mismatch", path,
                    "爆发耗能/冷却/次数须与数据库基础技能一致；修改这些规则的特殊机制尚未接入自动核验", binding_id=bid)
            return None, None
        skill_id = refs[ability["id"]]
        if skill_id in cooldown_by_skill and cooldown_by_skill[skill_id] != declared_cd:
            missing("source_rule_mismatch", path,
                    "同一数据库爆发技能的动作别名必须共享同一冷却组与恢复规则，不能按本地名称拆开冷却", binding_id=bid)
            return None, None
        cooldown_by_skill[skill_id] = declared_cd
    actions = [a["id"] for a in plan["rotation_model"]["actions"] if a["ability_id"] in refs]
    if not actions:
        missing("missing_input", path, "爆发来源没有对应的实际动作；请明确本窗口的动作轴", binding_id=bid)
        return None, None
    proof = {"character_ref": ref, "ability_refs": refs,
             "skills": [s for s in detail["skills"] if s["id"] in refs.values()]}
    if fresh:
        proof["action_resource_compilations"] = [{k: r[k] for k in ("ability_id", "resolved_selection", "resource_fingerprint", "compilation_fingerprint")}
                                                for r in fresh.values() if r["status"] == "ready"]
    return actions, proof


def compile_bindings(plan, requirements, db_path, missing, *, resource_compilations=None):
    """Mutate only the composer's private copy; never trust serialized output."""
    automatic = [b for b in plan["effect_bindings"] if "upstream_recipe" in b]
    if not automatic:
        return [], []
    from genshin_db.runtime_queries import assemble_runtime_source
    from genshin_engine.runtime_compiler import compile_source

    model = plan.get("mechanics_model")
    manual_ids = {r["id"] for r in (model or {}).get("rules", [])}
    manual_ids |= {r["id"] for r in (model or {}).get("fixed_effect_sources", [])}
    manual_ids |= {b["id"] for b in plan["manual_template"].get("buffs", [])}
    if any(identity.startswith(RESERVED_PREFIX) for identity in manual_ids):
        raise PlanError("手工规则/增益不能占用上游编译的保留命名空间", "duplicate_delivery")
    automatic_ids = {b["binding_id"] for b in automatic}
    declared_sources = [r.get("source_id", "") for r in (model or {}).get("rules", [])]
    declared_sources += [r.get("source_id", "") for r in (model or {}).get("fixed_effect_sources", [])]
    declared_sources += [b.get("extensions", {}).get("source_id", "")
                         for b in plan["manual_template"].get("buffs", [])]
    if any(s.startswith(bid + ":") for s in declared_sources for bid in automatic_ids):
        raise PlanError("同一自动来源已有手工规则或增益交付", "duplicate_delivery")

    inactive_ids = _inactive_equipment_bindings(plan, requirements)
    reports, evidence, unique_providers = [], [], {}
    for binding in automatic:
        cid, bid = binding["provider_character_id"], binding["binding_id"]
        req = next((r for r in requirements if r["binding_id"] == bid), None)
        if req is None:
            reports.append({"binding_id": bid, "status": "inactive", "parts": [],
                            "issues": [], "gameplay_semantics_status": "unverified"})
            continue
        path = f"effect_bindings.{bid}.upstream_recipe"
        gear = plan["equipment_inputs"][cid]
        line = (gear.get("character_ref") or gear.get("data_version") or {}).get("version_line", "release")
        affix_id = req["owner"].get("affix_id")
        bundle = None
        try:
            if affix_id is None:
                raise ValueError("当前效果没有可装配的精确 affix 身份")
            bundle = assemble_runtime_source(db_path, affix_id, line)
            returned = bundle.get("source", {})
            if (returned.get("kind") != "equip_affix" or str(returned.get("affix_id")) != str(affix_id)
                    or returned.get("version_line") != line or returned.get("upstream_commit") != gear["data_version"]["upstream_commit"]):
                raise EquipmentError("运行装配包与当前装备请求的来源/版本不一致", "runtime_source_mismatch", 500)
            source_identity = {"version_line": line, "affix_id": str(affix_id), "provider": cid}
            # Explicit typed references can detect aliases; arbitrary prose cannot.
            for other in plan["effect_bindings"]:
                if other is binding or other["provider_character_id"] != cid or other["binding_id"] in inactive_ids:
                    continue
                if any(str(_affix_ref(ref)) == str(affix_id)
                       and ref.get("version_line", line) == line for ref in other["source_refs"]):
                    raise PlanError("自动来源与另一手工声明引用同一装备效果", "duplicate_delivery")
            scenario = binding["upstream_recipe"]["scenario"]
            target_projection = scenario.get("profile_id") == "equipment-target-state-classic-v1"
            from .reaction_benefits import PROFILE_ID, reaction_projection_scope
            reaction_projection = scenario.get("profile_id") == PROFILE_ID
            from genshin_engine.runtime_reaction_em_compiler import (
                PROFILE_ID as EM_PROFILE_ID,
            )
            reaction_em_projection = scenario.get("profile_id") == EM_PROFILE_ID
            projection = target_projection or reaction_projection or reaction_em_projection
            scope_proof = None
            if reaction_em_projection:
                from .reaction_em_sources import compile_projection
                compiled, scope_proof = compile_projection(bundle, plan, binding, req,
                    inactive_binding_ids=inactive_ids)
            elif reaction_projection:
                from genshin_engine.runtime_reaction_benefit_compiler import (
                    compile_reaction_benefit_source,
                )
                compiled = compile_reaction_benefit_source(bundle, owner=cid, source_id=bid + ":upstream",
                    scenario=scenario, period=(plan.get("rotation_model") or {}).get("period"))
                scope_issues, scope_proof = reaction_projection_scope(plan, binding, req, inactive_binding_ids=inactive_ids)
                if scope_issues:
                    compiled = copy.deepcopy(compiled)
                    compiled["status"] = compiled["execution_status"] = compiled["projection_status"] = "pending"
                    compiled.setdefault("issues", []).extend({"code": "reaction_projection_scope", "message": message}
                                                             for message in scope_issues)
            elif target_projection:
                from genshin_engine.runtime_target_state_compiler import (
                    compile_target_state_source,
                )

                from .fixed_effects import target_projection_scope
                compiled = compile_target_state_source(bundle, owner=cid, source_id=bid + ":upstream", scenario=scenario)
                scope_issues, scope_proof = target_projection_scope(plan, binding, req)
                if scope_issues:
                    compiled = copy.deepcopy(compiled)
                    compiled["status"] = compiled["execution_status"] = compiled["projection_status"] = "pending"
                    compiled.setdefault("issues", []).extend({"code": "target_projection_scope", "message": message}
                                                             for message in scope_issues)
            else:
                compiled = compile_source(bundle, owner=cid, source_id=bid + ":upstream", scenario=scenario,
                                          period=(plan.get("rotation_model") or {}).get("period"))
            compiled = copy.deepcopy(compiled)
            compiled.update(binding_id=bid, source_identity=source_identity)
            if reaction_projection:
                compiled["app_scope"] = {"status": "pending" if scope_issues else "ready",
                    "context_fingerprint": fingerprint(scope_proof), "owner": copy.deepcopy(req["owner"]),
                    "target_id": (scope_proof.get("target") or {}).get("id"),
                    "manual_buff_repeat": scope_proof["manual_buff_repeat"],
                    "identity_coverage": scope_proof["identity_coverage"],
                    "provider_presence_scope": scope_proof["provider_presence_scope"]}
            for issue in compiled.get("issues", []):
                missing(issue.get("code", "upstream_pending"), path,
                        issue.get("message", "上游结构尚不可执行"), binding_id=bid)
            proof = {"kind": "upstream_compilation", "source_identity": source_identity,
                     "bundle_fingerprint": bundle.get("content_fingerprint"),
                     "actual_bundle_fingerprint": fingerprint(bundle),
                     "execution_fingerprint": compiled.get("execution_fingerprint"),
                     "provenance": compiled.get("provenance", {}),
                     "recipe": binding["upstream_recipe"], "period": (plan.get("rotation_model") or {}).get("period")}
            if target_projection:
                proof["target_scope"] = scope_proof
            if reaction_projection:
                proof["reaction_scope"] = scope_proof
            if reaction_em_projection:
                proof['reaction_em_source_scope'] = scope_proof
            if compiled["status"] == "ready" and compiled.get("rules") and not (reaction_projection or reaction_em_projection):
                action_ids, skill_proof = _burst_actions(plan, binding, db_path, missing,
                                                       resource_compilations=resource_compilations)
                proof["skill_identity"] = skill_proof
                if action_ids is None:
                    compiled["status"] = compiled["execution_status"] = "pending"
                    compiled.setdefault("issues", []).append({"code": "needs_skill_identity", "message": "爆发技能身份待核对"})
                else:
                    for rule in compiled["rules"]:
                        rule["trigger"]["action_ids"] = action_ids
            evidence.append(proof)
            compiled["compilation_fingerprint"] = fingerprint(proof)
            if compiled["status"] == "ready" and model is None:
                missing("missing_input", "mechanics_model", "自动来源缺少机制模型；读取mechanics_capabilities的/empty_model并补入/mechanics_model，保留已有动作输入", binding_id=bid,
                        category="request_error")
                compiled["status"] = compiled["execution_status"] = "pending"
            if compiled["status"] == "ready":
                # A source-level unique modifier cannot be duplicated across providers.
                # Use this fresh compile's verified canonical keys; raw payloads
                # may have encoded field names and serialized reports are untrusted.
                for name in compiled["unique_keys"]:
                    key = (line, name)
                    for other in plan["effect_bindings"]:
                        if other is binding or other["provider_character_id"] == cid or other["binding_id"] in inactive_ids:
                            continue
                        if any(str(_affix_ref(ref)) == str(affix_id)
                               and ref.get("version_line", line) == line for ref in other["source_refs"]):
                            raise PlanError("另一提供者已声明同源唯一效果，当前场景不支持其覆盖或叠加判定",
                                            "unsupported_unique_scope", {"bindings": [bid, other["binding_id"]],
                                                                         "modifier": name})
                    if key in unique_providers:
                        raise PlanError("多个提供者争用同一唯一效果的优先级尚不支持", "unsupported_unique_scope",
                                        {"bindings": [unique_providers[key], bid], "modifier": name})
                    unique_providers[key] = bid
                parts = copy.deepcopy(compiled["parts"])
                binding["parts"] = parts
                binding["source_fingerprint"] = req["source_fingerprint"]
                binding["coverage"] = {"state": "projection" if projection else "complete",
                    "reviewed_by": ("system:runtime_reaction_em_compiler" if reaction_em_projection else
                                    "system:runtime_reaction_benefit_compiler" if reaction_projection else
                                    "system:runtime_target_state_compiler" if target_projection else "system:runtime_compiler"),
                    "source_fingerprint": req["source_fingerprint"],
                    "note": ("普通原目标反应精通来源已核对；仍须通过全部来源装配后的实际范围检查和执行记录核验"
                             if reaction_em_projection else
                             "仅普通冰水扩散原目标收益投影已核对；在场限制只约束动态减抗，退场仍保留本人常驻反应加成"
                             if reaction_projection else
                             "仅经典伤害投影已核对；整个来源仍部分支持，未执行分支见upstream_compilations"
                             if target_projection else "由上游结构编译器核验受限模型；语义假设与来源证明见 upstream_compilations")}
                rules = copy.deepcopy(compiled.get("rules", []))
                if any(r["id"] in manual_ids for r in rules):
                    raise PlanError("自动规则与手工规则身份冲突", "duplicate_delivery")
                model["rules"].extend(rules)
                model["initial_state"]["rules"].extend(copy.deepcopy(compiled.get("initial_rules", [])))
                fixed = copy.deepcopy(compiled.get("fixed_effect_sources", []))
                if fixed:
                    if any(s["id"] in manual_ids for s in fixed):
                        raise PlanError("自动固定来源与手工规则身份冲突", "duplicate_delivery")
                    model.setdefault("fixed_effect_sources", []).extend(fixed)
            else:
                compiled["parts"], compiled["rules"], compiled["static_parts"], compiled["initial_rules"] = [], [], [], []
                if "fixed_effect_sources" in compiled:
                    compiled["fixed_effect_sources"] = []
                if projection:
                    compiled["projection_status"] = compiled["status"]
                    compiled["coverage_certificate"].update(projection_complete=False, projection_status=compiled["status"])
                    compiled["gameplay_semantics_status"] = "unverified"
                missing("upstream_pending", path, "自动来源未完整通过，未采用旧缓存或部分规则", binding_id=bid)
            reports.append(compiled)
        except PlanError:
            raise
        except (EquipmentError, ValueError, OSError, sqlite3.Error) as exc:
            missing(getattr(exc, "reason", "runtime_unavailable"), path, str(exc), binding_id=bid)
            failure = {"binding_id": bid, "status": "pending", "parts": [], "rules": [],
                       "gameplay_semantics_status": "unverified", "issues": [{"message": str(exc)}]}
            if binding["upstream_recipe"]["scenario"].get("profile_id") == "equipment-ordinary-swirl-benefits-v1":
                failure.update(execution_status="pending", projection_status="pending", whole_source_status="partial",
                    static_parts=[], initial_rules=[], unique_keys=[], coverage_certificate={
                        "kind": "ordinary_swirl_benefit_projection_certificate", "source_complete": False,
                        "projection_complete": False, "projection_status": "pending", "gameplay_verified": False})
            if binding["upstream_recipe"]["scenario"].get("profile_id") == "ordinary-reaction-flat-team-em-reference-v1":
                from .reaction_em_scope import CERTIFICATE_KIND
                failure.update(profile_id="ordinary-reaction-flat-team-em-reference-v1", execution_status="pending",
                    projection_status="pending", whole_source_status="partial", static_parts=[], initial_rules=[],
                    unique_keys=[], attribute_writes=[], coverage_certificate={'kind': CERTIFICATE_KIND,
                        'source_complete': False, 'projection_complete': False,
                        'projection_status': 'pending', 'gameplay_verified': False})
            reports.append(failure)
            evidence.append({"kind": "upstream_compilation_unavailable", "binding_id": bid,
                             "recipe": binding["upstream_recipe"],
                             "bundle_fingerprint": (bundle or {}).get("content_fingerprint")})
    return reports, evidence
