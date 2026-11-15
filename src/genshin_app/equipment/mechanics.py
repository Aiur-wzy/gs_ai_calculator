"""Bind explicit mechanisms to reviewed source parts; compilation never guesses game rules."""
from __future__ import annotations

import copy

from genshin_app.task_capability_producer import emit_task_missing

from .files import context_view, fingerprint, input_view
from .schema import PlanError


def prepare_mechanics(plan, applicable, template, source_fingerprint, missing, *, context_plan=None, passive_compilations=(), energy_generation_compilations=(), limited_hit_benefit_compilations=(), mechanism_compilations=(), shared_pool_adoptions=None, state_runtime_adoptions=None, resource_counter_adoptions=None, resource_counter_view_adoptions=None, attribute_callback_adoptions=None, resource_threshold_observer_adoptions=None, resource_threshold_trigger_adoptions=None, resource_reaction_adoptions=None):
    def task_missing(code, path, message, action, *, consumer,
                     category="knowledge_missing",
                     blocking_scope="agent_materialization",
                      retryable=True, repair_hint, diagnostics=None):
        emit_task_missing(
            missing, code, path, message, action,
            consumer=consumer, category=category,
            required=True, consumed=True, impact="high",
            blocking_scope=blocking_scope, retryable=retryable,
            repair_hint=repair_hint,
            diagnostics=diagnostics,
        )

    model = plan.get("mechanics_model")
    if plan["plan_version"] < 3:
        return None, [], [], plan.get("rotation_tuning", {}).get("buff_hit_matches", [])
    if model is None or plan.get("rotation_model") is None:
        for field in ("rotation_model", "mechanics_model"):
            if plan.get(field) is not None:
                continue
            hint = (
                '用gs_read_context读取mechanics_capabilities的/empty_model及/schema，'
                '并查plan_schema的/properties/mechanics_model；用gs_patch_candidate补入/mechanics_model。'
                '保留已有动作/rotation_model，空骨架只供继续材料化，不代表事件、绑定或能量已完整。'
                if field == "mechanics_model" else
                '用gs_read_context读取capabilities的/schema/properties/rotation_model及'
                'plan_schema的/properties/rotation_model；用gs_patch_candidate补入/rotation_model。'
                '保留已有机制/其他输入，继续材料化实际动作及其时间，不虚构数据库或游戏事实。'
            )
            task_missing(
                "missing_input", field, f"当前方案的{field}尚未由Agent填写，计算输入不完整。",
                "edit_mechanics", consumer="mechanics.execution_graph", repair_hint=hint,
            )
        return None, [], [], []
    if "special_reaction_model" in model:
        from genshin_engine.special_reaction_capabilities import (
            project_special_reaction_party_capabilities_detailed,
        )
        from genshin_engine.special_reactions import REQUIRED_CAPABILITY_TAGS

        model = copy.deepcopy(model)
        from genshin_engine.special_handler_registry import (
            freeze_special_reaction_handler_bindings,
        )

        model["special_reaction_model"] = freeze_special_reaction_handler_bindings(model["special_reaction_model"])
        party = {character["id"] for character in template["characters"]}
        projection = project_special_reaction_party_capabilities_detailed(
            plan.get("equipment_inputs", {}), actor_ids=party)
        projected_capabilities = projection["rows"]
        model["special_reaction_party_capabilities"] = projected_capabilities
        tags_by_actor = {
            row["character_id"]: set(row["capability_tags"])
            for row in projected_capabilities
            if row.get("status") == "confirmed"
        }
        for reaction_type, replacement in (
                model["special_reaction_model"].get("replacements") or {}).items():
            if not isinstance(replacement, dict) or not replacement.get("enabled"):
                continue
            qualification = replacement.get("qualification") or {}
            required = REQUIRED_CAPABILITY_TAGS.get(reaction_type)
            providers = qualification.get("provider_ids") or []
            if not any(actor_id in party and required in tags_by_actor.get(actor_id, set()) for actor_id in providers):
                reasons = [row for row in projection["diagnostics"] if row["character_id"] in providers]
                for actor_id in providers:
                    if actor_id not in party:
                        reasons.append({"code": "special_capability_provider_not_in_party",
                            "character_id": actor_id, "category": "request_error",
                            "actual": actor_id, "expected": sorted(party),
                            "message": "资格提供者不在当前队伍，请使用真实队伍角色ID。"})
                    elif actor_id in tags_by_actor and required not in tags_by_actor[actor_id]:
                        reasons.append({"code": "special_capability_tag_mismatch",
                            "character_id": actor_id, "category": "request_error",
                            "actual": sorted(tags_by_actor[actor_id]), "expected": required,
                            "message": "该提供者的受信资格不包含当前开启的反应类型。"})
                category = ("request_error" if any(row.get("category") == "request_error" for row in reasons)
                            else "engine_capability_missing")
                input_ref = "/mechanics_model/special_reaction_model/replacements/" + reaction_type + "/qualification"
                task_missing(
                    "special_capability_projection_pending",
                    f"mechanics_model.special_reaction_model.replacements.{reaction_type}.qualification",
                    "已开启的星月反应没有匹配当前队伍数据库角色资格；请核对角色资料引用与提供者。",
                    "review_special_reaction",
                    consumer=f"special_reaction.{reaction_type}",
                    category=category, blocking_scope="task_capability",
                    repair_hint="根据 differences 核对提供者、角色身份、技能组及来源版本，再重新组合。资格由App投影，不直接手填；不要改变用户命座或改成静态反应迁就此错误。",
                    diagnostics={"input_ref": input_ref, "actual": providers,
                        "expected": {"required_capability_tag": required}, "differences": reasons,
                        "does_not_prove": ["数据库缺少角色", "动态星月反应整体不支持"],
                        "checked_scope": "本次开启的反应与所声明提供者的资格投影",
                        "recovery": {"action": "inspect_selected_character_identity", "retry_same_request": False}},
                )
    positioned_original = copy.deepcopy(model) if (model.get("state_runtime", {}).get("schema_version") == 2
        and model.get("resource_threshold_triggers", {}).get("schema_version") == 3) else None
    rules = {r["id"]: r for r in model["rules"]}
    actors = {c["id"] for c in template["characters"]}
    from .resource_lifecycle_sources import has_lifecycle_gates, validate_original
    lifecycle_reactions = has_lifecycle_gates(model)
    if model.get('resource_reaction_contract', {}).get('schema_version') == 2:
        validate_original(model, actors, missing)
    if lifecycle_reactions and model.get('state_runtime', {}).get('schema_version') == 2:
        positioned_original = copy.deepcopy(model)
    for index, buff in enumerate(plan["manual_template"].get("buffs", [])):
        identity = buff.get("extensions", {})
        if (not isinstance(identity.get("owner"), str) or identity["owner"] not in actors or not isinstance(identity.get("source_id"), str)
                or not identity["source_id"].strip()):
            task_missing(
                "missing_input", f"manual_template.buffs[{index}].extensions",
                "机制方案的手工增益需要提供者 owner 和来源分项 source_id；独立手工来源也须明确声明，避免重复交付", "edit_buff",
                consumer=f"buff.source.{buff.get('id', index)}",
                repair_hint=(f"用gs_patch_candidate补齐/manual_template/buffs/{index}/extensions的owner（当前队伍角色ID）"
                             "及source_id（唯一来源分项）；核对实际来源并避免重复交付，这不是数据库资料缺失。"),
            )
    existing_buffs = {b["id"] for b in template.get("buffs", [])}
    if existing_buffs & rules.keys():
        raise PlanError("机制规则ID与手工增益ID冲突", details=sorted(existing_buffs & rules.keys()))
    linked, active, explicitly_excluded, pool_source_reviews = {}, set(), set(), {}
    qualification_context = (fingerprint(context_view(input_view(context_plan or plan)))
        if any("eligibility" in r["beneficiaries"] for r in rules.values()) else None)
    applicable_ids = {(b["binding_id"], p["part_id"]) for b, p in applicable}
    for binding in plan["effect_bindings"]:
        for part in binding["parts"]:
            if (part.get("rule") or {}).get("kind") != "mechanics":
                continue
            rid = part["rule"]["rule_id"]
            if rid not in rules:
                raise PlanError(f"来源部件引用不存在的机制规则：{rid}")
            if rid in linked:
                raise PlanError(f"机制规则由多个部件重复交付：{rid}")
            linked[rid] = (binding["binding_id"], part["part_id"])
            if part["state"] == "excluded":
                explicitly_excluded.add(rid)
            rule = rules[rid]
            expected_source = f"{binding['binding_id']}:{part['part_id']}"
            pool_source_reviews[expected_source] = part["dependencies"]
            if rule["owner"] != binding["provider_character_id"] or rule["source_id"] != expected_source:
                raise PlanError("机制的提供者或来源部件不一致", details={"rule_id": rid,
                                "expected_owner": binding["provider_character_id"],
                                "expected_source_id": expected_source})
            if linked[rid] in applicable_ids:
                review = part["dependencies"]
                if ("eligibility" in rule["beneficiaries"]
                        and (review.get("mode") != "manual_context"
                             or review.get("context_fingerprint") != qualification_context
                             or not review.get("note", "").strip())):
                    task_missing(
                        "beneficiary_source_scope_unreviewed",
                        f"mechanics_model.rules.{rid}.beneficiaries",
                        "受益者资格需要完整当前上下文的人工来源确认；旧source_only或复制激活名单不能批准。",
                        "review_mechanics", consumer=f"mechanics.rule.{rid}",
                        repair_hint="材料化当前上下文中的受益者资格并重新审核该规则",
                    )
                elif any(c["scope"] != "always" for c in part["conditions"]):
                    task_missing(
                        "unsupported_effect",
                        f"effect_bindings.{binding['binding_id']}.parts.{part['part_id']}",
                        "机制的区间条件应在机制事件/过滤中声明，不能被来源部件区间静默忽略",
                        "edit_mechanics", consumer=f"mechanics.rule.{rid}",
                        category="engine_capability_missing",
                        blocking_scope="engine_capability",
                        repair_hint="把区间条件材料化为明确事件或过滤输入后重新组合",
                    )
                else:
                    active.add(rid)
    pending_passive_rules = set()
    for compilation in passive_compilations:
        if compilation["status"] != "ready":
            pending_passive_rules.update(r["id"] for r in compilation.get("preview_rules", []))
            continue
        declared = {item["id"]: item for item in plan.get("passive_sources", [])}.get(compilation["id"])
        if not declared or declared["actor"] != compilation["actor"]:
            raise PlanError("固有规则登记与原生来源身份不一致")
        for part in compilation["parts"]:
            rid = part["rule"]["rule_id"]
            if rid not in rules or rid in linked or rid not in compilation["rule_ids"]:
                raise PlanError("固有规则缺失或由多个来源重复交付")
            rule = rules[rid]
            if "eligibility" in rule["beneficiaries"]:
                raise PlanError("固有自动来源尚未批准受益者资格契约", "beneficiary_source_scope_unsupported")
            if (rule["owner"] != declared["actor"] or rule["source_id"] != f"{declared['id']}:{part['part_id']}"
                    or part["rule"]["kind"] != "mechanics" or part["delivery"]["kind"] != "mechanics"):
                raise PlanError("固有规则提供者或来源分项不一致")
            linked[rid] = (declared["id"], part["part_id"])
            active.add(rid)
    for compilation in [*energy_generation_compilations, *limited_hit_benefit_compilations]:
        if compilation["status"] != "ready":
            continue
        field = ("limited_hit_benefit_sources" if compilation.get("compiler") == "source-bound-limited-hit-benefit-v1"
                 else "energy_generation_sources")
        declared = next((r for r in plan.get(field, []) if r["id"] == compilation["id"]), None)
        if not declared:
            raise PlanError("自动规则登记缺少原生来源")
        for part in compilation["parts"]:
            rid = part["rule"]["rule_id"]
            rule = rules.get(rid)
            if rule and "eligibility" in rule["beneficiaries"]:
                raise PlanError("产能或有限收益自动来源尚未批准受益者资格契约", "beneficiary_source_scope_unsupported")
            if (not rule or rid in linked or rule not in compilation["rules"]
                    or rule["owner"] != compilation["actor"]
                    or rule["source_id"] != declared["id"] + ":" + part["part_id"]
                    or part["rule"]["kind"] != "mechanics" or part["delivery"]["kind"] != "mechanics"):
                raise PlanError("自动规则身份不符、缺失或重复交付")
            linked[rid] = (declared["id"], part["part_id"])
            active.add(rid)
    from .mechanism_source_schema import (
        CAST_COMPILER,
        COMPILER,
        CONTACT_COMPILER,
        PREFIX,
        mechanism_rule_id,
    )
    group_ready = bool(mechanism_compilations) and all(c.get("status") == "ready" for c in mechanism_compilations)
    for compilation in mechanism_compilations if group_ready else ():
        declared = next((r for r in plan.get("mechanism_sources", []) if r["id"] == compilation["id"]), None)
        if not declared or declared["actor"] != compilation["actor"] or compilation.get("compiler") not in (COMPILER, CAST_COMPILER, CONTACT_COMPILER):
            raise PlanError("通用机制登记与原生来源不一致")
        parts = compilation["parts"]
        if (len({p["part_id"] for p in parts}) != len(parts)
                or len(parts) != len(compilation["rules"])):
            raise PlanError("通用机制规则与来源部件必须一一对应")
        for part in parts:
            rid, pid = part["rule"]["rule_id"], part["part_id"]
            rule = rules.get(rid)
            if rule and "eligibility" in rule["beneficiaries"]:
                raise PlanError("通用机制自动来源尚未批准受益者资格契约", "beneficiary_source_scope_unsupported")
            if (not pid.startswith("mechanism.") or rid != mechanism_rule_id(declared["id"], pid[len("mechanism."):])
                    or not rule or rid in linked or rule not in compilation["rules"]
                    or rule["owner"] != declared["actor"] or rule["source_id"] != declared["id"] + ":" + pid
                    or part["rule"]["kind"] != "mechanics" or part["delivery"]["kind"] != "mechanics"):
                raise PlanError("通用机制规则身份不符、缺失或重复交付")
            linked[rid] = (declared["id"], pid)
            active.add(rid)
    for rid in sorted(rules.keys() - linked.keys()):
        task_missing(
            "effect_pending", f"mechanics_model.rules.{rid}.source_id",
            "机制需要唯一的来源部件：rule.kind=mechanics、rule_id及delivery.kind=mechanics",
            "edit_mechanics", consumer=f"mechanics.rule.{rid}",
            repair_hint="通过受审来源或Agent强类型材料化交付该规则的唯一来源部件",
        )
    source_active = set(active)
    if model.get('special_damage_echoes'):
        current_echo_context = fingerprint(context_view(input_view(context_plan or plan)))
        for item in model['special_damage_echoes']:
            source = pool_source_reviews.get(item['source_id'], {})
            if (item['source_rule_id'] in active and
                    (source.get('mode') != 'manual_context'
                     or source.get('context_fingerprint') != current_echo_context
                     or not source.get('note', '').strip())):
                task_missing('special_echo_source_scope_unreviewed',
                    f"mechanics_model.special_damage_echoes.{item['id']}",
                    '额外雷击的有限分支与未知范围需要当前完整上下文的来源审核。',
                    'review_mechanics', consumer='mechanics.extra_damage',
                    repair_hint='核实父自然雷击、来源有效窗口与明确近似范围，再按当前上下文审核来源部件；不修改伤害结果。')
    if "resource_counter_views" in model:
        from .resource_counter_views import validate_original_counter_views
        validate_original_counter_views(model, actors)
    has_threshold = any(field in model for field in ('resource_threshold_observers', 'resource_threshold_triggers'))
    if has_threshold:
        from .resource_thresholds import validate_original_resource_thresholds
        validate_original_resource_thresholds(model, actors)
    from .shared_pools import prepare_shared_pools
    pools, pool_disabled_rules, pool_adoptions = prepare_shared_pools(
        model, rules, active, linked, explicitly_excluded, missing, source_reviews=pool_source_reviews,
        context_fingerprint=fingerprint(context_view(input_view(context_plan or plan))))
    from .state_runtime import prepare_state_runtime
    state_config, pools, disabled_rules, state_adoptions, pool_adoptions = prepare_state_runtime(
        model, rules, active, linked, explicitly_excluded, missing, source_reviews=pool_source_reviews,
        context_fingerprint=fingerprint(context_view(input_view(context_plan or plan))), pools=pools,
        pool_adoptions=pool_adoptions, pool_disabled_rules=pool_disabled_rules,
        rotation_model=plan["rotation_model"], character_ids=actors)
    state_disabled_rules = disabled_rules - pool_disabled_rules
    pool_disabled_rules = disabled_rules
    active.difference_update(disabled_rules)
    from .resource_counters import prepare_resource_counters
    counters, counter_adoptions = prepare_resource_counters(
        model, rules, active, linked, explicitly_excluded, missing, source_reviews=pool_source_reviews,
        context_fingerprint=fingerprint(context_view(input_view(context_plan or plan))),
        pools=pools, state=state_config, pool_adoptions=pool_adoptions, state_adoptions=state_adoptions,
        character_ids=actors)
    views = None
    observers, triggers = None, None
    if "resource_counter_views" in model or has_threshold or lifecycle_reactions:
        from .resource_source_graph import prepare_resource_source_graph
        closed = prepare_resource_source_graph(
            model, rules, source_active, active, linked, explicitly_excluded, missing,
            source_reviews=pool_source_reviews,
            context_fingerprint=fingerprint(context_view(input_view(context_plan or plan))),
            pools=pools, state=state_config, counters=counters, pool_adoptions=pool_adoptions,
            state_adoptions=state_adoptions, counter_adoptions=counter_adoptions, character_ids=actors)
        pools, state_config, counters, views = (closed[key] for key in ("pools", "state", "counters", "views"))
        pool_adoptions, state_adoptions, counter_adoptions = (closed[key] for key in ("pool_adoptions", "state_adoptions", "counter_adoptions"))
        pool_disabled_rules.update(closed["disabled_rules"])
        active.difference_update(closed["disabled_rules"])
        if resource_counter_view_adoptions is not None:
            resource_counter_view_adoptions.extend(closed["view_adoptions"])
        if has_threshold:
            observers, triggers = closed['observers'], closed['triggers']
            if resource_threshold_observer_adoptions is not None:
                resource_threshold_observer_adoptions.extend(closed['observer_adoptions'])
            if resource_threshold_trigger_adoptions is not None:
                resource_threshold_trigger_adoptions.extend(closed['trigger_adoptions'])
    if resource_counter_adoptions is not None:
        resource_counter_adoptions.extend(counter_adoptions)
    if shared_pool_adoptions is not None:
        shared_pool_adoptions.extend(pool_adoptions)
    if state_runtime_adoptions is not None:
        state_runtime_adoptions.extend(state_adoptions)
    callbacks = None
    if 'attribute_callbacks' in model:
        from .attribute_callbacks import prepare_attribute_callbacks
        callbacks, callback_adoption = prepare_attribute_callbacks(
            model, source_active, active, linked, explicitly_excluded, missing,
            source_reviews=pool_source_reviews,
            context_fingerprint=fingerprint(context_view(input_view(context_plan or plan))))
        if attribute_callback_adoptions is not None:
            attribute_callback_adoptions.append(callback_adoption)
    resolved = copy.deepcopy(model)
    if has_threshold:
        for field, config in (('resource_threshold_observers', observers), ('resource_threshold_triggers', triggers)):
            if config is None: resolved.pop(field, None)
            else: resolved[field] = config
    if 'attribute_callbacks' in model:
        if callbacks is None: resolved.pop('attribute_callbacks', None)
        else: resolved['attribute_callbacks'] = callbacks
    if "resource_counter_views" in model:
        if views is None: resolved.pop("resource_counter_views", None)
        else: resolved["resource_counter_views"] = views
    if counters is None:
        resolved.pop('resource_counters', None)
    else:
        resolved['resource_counters'] = counters
    if state_config is None:
        resolved.pop("state_runtime", None)
    else:
        resolved["state_runtime"] = state_config
    if pools is None:
        resolved.pop("shared_pools", None)
    else:
        resolved["shared_pools"] = pools
    resolved["rules"] = [r for r in resolved["rules"] if r["id"] in active]
    if 'special_damage_echoes' in resolved:
        # A pending/unapproved source is already a consumed missing input above;
        # do not leave executable children behind after its source is excluded.
        resolved['special_damage_echoes'] = [entry for entry in resolved['special_damage_echoes']
                                             if entry['source_rule_id'] in active]
        for item in resolved['special_damage_echoes']:
            template.setdefault('meta', {}).setdefault('assumptions', []).append(
                f"额外特殊伤害{item['id']}为显式有限近似分支：{item['precision']['method']}；"
                f"未知/排除范围：{item['precision']['uncertainty']}；不证明完整角色机制已还原。")
    resolved["initial_state"]["rules"] = [r for r in resolved["initial_state"]["rules"] if r["rule_id"] in active]
    if "resource_ownership" in resolved:
        resolved["resource_ownership"] = [r for r in resolved["resource_ownership"] if r["rule_id"] in active]
    if 'resource_reaction_contract' in model:
        from .resource_reactions import prepare_resource_reactions
        reaction_contract, reaction_adoption = prepare_resource_reactions(
            model, resolved, pool_adoptions, missing, characters=actors,
            source_reviews=pool_source_reviews, context_fingerprint=fingerprint(context_view(input_view(context_plan or plan))),
            state_adoptions=state_adoptions)
        if reaction_contract is None:
            resolved.pop('resource_reaction_contract', None)
        else:
            resolved['resource_reaction_contract'] = reaction_contract
        if resource_reaction_adoptions is not None:
            resource_reaction_adoptions.append(reaction_adoption)
    if has_threshold or lifecycle_reactions:
        # Pending preview deliberately retains the original gated contract while
        # unavailable execution nodes are pruned. It is not an executable graph;
        # the original graph was validated above before any exclusion.
        if positioned_original is not None and not (lifecycle_reactions and reaction_adoption['status'] == 'pending'):
            from .state_runtime_v2 import verify_positioned_delivery
            verify_positioned_delivery(positioned_original, resolved, actors)
        from genshin_engine.mechanics import validate_mechanics_model
        validation = validate_mechanics_model(resolved)
        if validation['status'] != 'valid':
            for issue in validation['issues']:
                task_missing(
                    'threshold_execution_graph_pending', issue['path'], issue['message'],
                    'review_mechanics', consumer="mechanics.execution_graph",
                    category="engine_capability_missing",
                    blocking_scope="engine_capability",
                    repair_hint="修复完整资源阈值依赖图后重新组合",
                )
        # Reference validation uses the final delivery, never reconstructs
        # removed rules, initial states or causes to make it executable.
        if observers is not None:
            from genshin_engine.shared_pool_runtime import validate_bindings
            for issue in validate_bindings(resolved, characters=sorted(actors)):
                task_missing(
                    'threshold_execution_graph_pending', issue['path'], issue['message'],
                    'review_mechanics', consumer="mechanics.execution_graph",
                    category="engine_capability_missing",
                    blocking_scope="engine_capability",
                    repair_hint="修复资源阈值与共享池的最终绑定后重新组合",
                )
    known_hits = {h["hit_id"] for h in model["hit_events"]}
    for pattern in model["hit_patterns"]:
        known_hits.update(f"{pattern['id']}:{i + 1}" for i in range(len(pattern["offsets"])))
    translated, previews, legacy = [], [], []
    from .fixed_effects import prepare_fixed_sources
    fixed, fixed_matches, fixed_previews = prepare_fixed_sources(
        plan, applicable, template, source_fingerprint, missing, context_plan=context_plan)
    if "fixed_effect_sources" in resolved:
        resolved["fixed_effect_sources"] = fixed
    translated.extend(fixed_matches)
    previews.extend(fixed_previews)
    for match in plan.get("rotation_tuning", {}).get("buff_hit_matches", []):
        if "fixed_source_id" in match["effect_ref"]:
            continue
        rid = match["effect_ref"]["buff_id"]
        if rid.startswith(PREFIX) and (not group_ready or rid not in active):
            task_missing(
                "mechanism_pending", f"rotation_tuning.buff_hit_matches.{match['id']}",
                "通用机制来源尚未就绪，不能用轴微调绕过来源、资格或整组确认",
                "review_mechanism", consumer=f"mechanics.match.{match['id']}",
                repair_hint="先完成通用机制来源材料化，再重新审核该轴关联",
            )
            continue
        if rid not in rules:
            if rid in {"auto.hit-benefit." + r["id"] for r in plan.get("limited_hit_benefit_sources", [])}:
                task_missing(
                    "limited_hit_benefit_pending",
                    f"rotation_tuning.buff_hit_matches.{match['id']}",
                    "有限次数收益来源尚未就绪，不能用轴微调绕过来源资格",
                    "review_limited_hit_benefit",
                    consumer=f"mechanics.match.{match['id']}",
                    repair_hint="先补齐有限次数收益来源，再审核该命中关联",
                )
                continue
            if rid in pending_passive_rules:
                task_missing(
                    "passive_pending", f"rotation_tuning.buff_hit_matches.{match['id']}",
                    "固有来源尚未就绪，不能用微调绕过来源与父链资格",
                    "review_match", consumer=f"mechanics.match.{match['id']}",
                    repair_hint="先补齐固有来源父链，再审核该命中关联",
                )
                continue
            if rid.startswith("upstream.") and any("upstream_recipe" in b for b in plan["effect_bindings"]):
                task_missing(
                    "upstream_pending", f"rotation_tuning.buff_hit_matches.{match['id']}",
                    "上游来源尚未生成此规则；复核当前来源后再确认微调",
                    "review_match", consumer=f"mechanics.match.{match['id']}",
                    repair_hint="核对当前来源，必要时材料化该规则后再确认微调",
                )
                previews.append({"id": match["id"], "effect_ref": match["effect_ref"], "kind": "mechanics",
                                 "basis_fingerprint": None, "base_hit_ids": None, "final_hit_ids": None})
                continue
            legacy.append(match)
            continue
        eid = match["effect_ref"]["effect_id"]
        if not any(e["id"] == eid for e in rules[rid]["effects"]):
            raise PlanError(f"轴微调引用不存在的机制收益：{rid}/{eid}")
        requested = set(match.get("hit_ids", []) + match.get("include_hit_ids", []) + match.get("exclude_hit_ids", []))
        if not requested <= known_hits:
            raise PlanError("机制轴微调引用不存在的实际命中", details=sorted(requested - known_hits))
        if rid in pool_disabled_rules:
            previews.append({"id": match["id"], "effect_ref": match["effect_ref"], "kind": "mechanics",
                             "basis_fingerprint": None, "base_hit_ids": [], "final_hit_ids": [],
                             "status": "state_dependency_unavailable" if rid in state_disabled_rules else "resource_dependency_unavailable",
                             "note": "生命周期或共享资源依赖不可用，此规则及其微调不交付；查看来源采用回执。"})
            continue
        basis = fingerprint({"source_fingerprint": source_fingerprint,
                             "context": context_view(input_view(context_plan or plan)), "match_id": match["id"]})
        previews.append({"id": match["id"], "effect_ref": match["effect_ref"], "kind": "mechanics",
                         "basis_fingerprint": basis, "base_hit_ids": None,
                         "final_hit_ids": None, "requested_hit_ids": sorted(requested),
                         "added_hit_ids": sorted(match.get("include_hit_ids", [])),
                         "removed_hit_ids": sorted(match.get("exclude_hit_ids", [])),
                         "note": "这里只确认候选关联；实际收益还要通过触发、时间和次数消费检查。"})
        if rid not in active:
            task_missing(
                "effect_pending", f"rotation_tuning.buff_hit_matches.{match['id']}",
                "机制所属来源部件未启用或未核对，不能应用微调",
                "review_match", consumer=f"mechanics.match.{match['id']}",
                repair_hint="启用并核对规则来源后重新审核该命中关联",
            )
            continue
        if match["state"] != "confirmed" or match["basis_fingerprint"] != basis:
            task_missing(
                "dependency_stale", f"rotation_tuning.buff_hit_matches.{match['id']}",
                "机制微调需根据当前规则、动作和来源重新核对",
                "review_match", consumer=f"mechanics.match.{match['id']}",
                repair_hint="按当前规则、动作与来源重新确认该关联",
            )
            continue
        if match['mode'] == 'runtime_adjust':
            translated.extend({'rule_id': rid, 'effect_id': eid, **copy.deepcopy(row)} for row in match['runtime_events'])
            continue
        include = set(match.get("include_hit_ids", []))
        exclude = set(match.get("exclude_hit_ids", []))
        if match["mode"] == "replace":
            include = set(match["hit_ids"])
            exclude = known_hits - include
        for operation, ids in (("include", include), ("exclude", exclude)):
            translated.extend({"rule_id": rid, "effect_id": eid, "hit_id": hid,
                               "operation": operation} for hid in sorted(ids))
    return resolved, translated, previews, legacy


def enable_mechanics(plan):
    """An explicit editor operation; preserves the old plan's actions and inputs."""
    from .schema import validate_plan

    validate_plan(plan)
    updated = copy.deepcopy(plan)
    updated["plan_version"] = 3
    updated.setdefault("rotation_model", None)
    updated.setdefault("mechanics_model", {"schema_version": 1, "rules": [], "hit_events": [],
        "hit_patterns": [], "condition_events": [], "initial_state": {"rules": []}})
    updated["resolution"], updated["resolved_template"] = {"state": "pending"}, None
    validate_plan(updated)
    return updated
