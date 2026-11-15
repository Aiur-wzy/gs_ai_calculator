"""Frozen original Mechanics entry contract, isolated from new execution passes.

This oracle checks the entry orchestration only. Its unchanged gameplay runtime
is the same implementation under test in the separate full-output golden cases.
It is never selected by production execution or used as an error fallback.
"""

from __future__ import annotations

import copy
from types import FunctionType
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from genshin_engine.mechanics import (  # noqa: TC004 - injected canonical globals
        ACTION_TYPES,
        DAMAGE_TYPES,
        MAX_MECHANICS_EVENTS,
        REFERENCE_CONTRACT,
        AttributeLedgerError,
        DamageReportingLimit,
        ElementalPending,
        SharedPoolError,
        SnapshotCapturePending,
        SpecialReactionPending,
        StateRuntimeError,
        StateRuntimeMechanics,
        TargetStatePending,
        TemplateError,
        _apply_special_damage_weights,
        _attach_attribute_report,
        _attach_buff_state_timeline,
        _attach_shared_pool_report,
        _attach_special_reaction_report,
        _attach_state_runtime_report,
        _base_result,
        _event_sort_key,
        _expand,
        _freeze_reference,
        _issue,
        _Mechanics,
        _project,
        _semantic,
        _simulate,
        _swirl_v2,
        _threshold_classic_rule_ids,
        _tick,
        _verified_empty_result,
        calculate,
        candidate_sources_known,
        capture_contract,
        classify_verified_empty_damage,
        collect_damage_candidates,
        counter_view_runtime,
        pool_runtime,
        possible_recipients,
        qualification_errors,
        reserved_id,
        route_event_id,
        validate_mechanics_model,
        validate_rotation_model,
        validate_special_reaction_bindings,
        validate_state_runtime_bindings,
        validate_template,
    )


def _canonical_execute_mechanics_v1(template, rotation_model, mechanics_model, matches=None):
    """Return a read-only execution receipt; old templates use calculate/check_rotation."""
    output = {"status": "invalid", "issues": [], "result": None, "resolved_template": None,
              "rotation_check": _base_result("not_checked"), "mechanics_trace": [], "sampling_trace": [], "capabilities_version": 1}
    validation = validate_mechanics_model(mechanics_model)
    output["issues"].extend(validation["issues"])
    rv = validate_rotation_model(rotation_model)
    output["issues"].extend(rv["issues"])
    if output["issues"]:
        output["status"] = "invalid" if any(i["severity"] == "error" for i in output["issues"]) else "pending"
        return output
    missing = []
    if rotation_model.get("period") is None or not rotation_model.get("initial_frontline"):
        missing.append("rotation_model.period/initial_frontline")
    missing.extend("rotation_model.actions."+a["id"] for a in rotation_model["actions"] if a.get("start") is None or a.get("occupation_end") is None)
    if missing:
        output.update(status="pending", issues=[_issue("missing_information", "动作投影需要明确周期、初始前台及动作开始/结束。", path, severity="pending") for path in missing])
        return output
    try:
        from . import action_capacity_audit as capacity_audit
        from . import automatic_energy as auto_energy
        if any(capacity_audit.bound(a) for a in rotation_model["abilities"]):
            for path in capacity_audit.namespace_conflicts({"rotation_model": rotation_model,
                    "mechanics_model": mechanics_model, "template": template, "matches": matches}):
                output["issues"].append(_issue("action_capacity_namespace", "容量消费审计不可用作输入身份或事件引用。", path))
            if output["issues"]:
                return output
        automatic_ids = {a["id"] for a in rotation_model["abilities"] if auto_energy.automatic(a)}
        if automatic_ids:
            for path in auto_energy.namespace_conflicts({"rotation_model": rotation_model,
                    "mechanics_model": mechanics_model, "template": template, "matches": matches}):
                output["issues"].append(_issue("automatic_energy_namespace", "自动扣能审计不可用作输入身份或事件引用。", path))
            actions_by_id = {a["id"]: a for a in rotation_model["actions"]}
            for event in rotation_model["events"]:
                if (event["type"] == "energy_spend"
                        and actions_by_id.get(event.get("action_id"), {}).get("ability_id") in automatic_ids):
                    output["issues"].append(_issue("duplicate_delivery", "自动扣能动作的旧事件不能通过资源接管移除后绕过双交付检查。", event["id"]))
            if output["issues"]:
                return output
        validate_template(template, allow_empty_hits=_swirl_v2(mechanics_model) and bool(mechanics_model.get("application_events")))
        captures = mechanics_model.get("snapshot_captures", [])
        actions = {a["id"]: a for a in rotation_model["actions"]}
        capture_ids = {c["id"] for c in captures}
        typed_ids = {r["id"] for r in mechanics_model["rules"]} | {s["id"] for s in mechanics_model.get("fixed_effect_sources", [])}
        typed_ids.update(b["id"] for b in template.get("buffs", []))
        typed_ids.update(p["id"] for p in mechanics_model["hit_patterns"])
        for capture in captures:
            action = actions.get(capture["action_id"])
            if action is None:
                output["issues"].append(_issue("reference", "捕获的实际父动作不存在。", capture["id"]))
                continue
            if capture["id"] in typed_ids or "~" in capture["id"] or capture["id"].startswith("@"):
                output["issues"].append(_issue("duplicate_id", "捕获ID占用已有或系统派生身份。", capture["id"]))
            at = _tick(action["start"]) + _tick(capture["offset"])
            if at > _tick(action["occupation_end"]) or at >= _tick(rotation_model["period"]):
                output["issues"].append(_issue("snapshot_scope_unsupported", "捕获须位于父动作占用内且早于窗口右端。", capture["id"], severity="pending"))
            if capture["status"] == "pending" or not capture["source"].strip():
                output["issues"].append(_issue("snapshot_calibration_pending", "捕获校准尚未明确。", capture["id"], severity="pending"))
        if output["issues"]:
            output["status"] = "invalid" if any(i["severity"] == "error" for i in output["issues"]) else "pending"
            return output
        projected, rotation, runtime = _project(template, rotation_model, mechanics_model, output["issues"])
        if output["issues"]:
            return output
        chars = {c["id"] for c in rotation["characters"]}
        output["issues"].extend(validate_special_reaction_bindings(
            mechanics_model,
            character_ids=sorted(chars),
            action_ids={action["id"] for action in rotation["actions"]},
            action_bindings={
                action["id"]: {
                    "ability_id": action["ability_id"],
                    "start": action["start"], "order": action["order"],
                    "repeat": action["repeat"],
                    "character_id": next(
                        ability["character"] for ability in rotation["abilities"]
                        if ability["id"] == action["ability_id"]),
                    "variant": next(
                        ability.get("variant") for ability in rotation["abilities"]
                        if ability["id"] == action["ability_id"]),
                    "effective_level": next(
                        ability.get("effective_level")
                        for ability in rotation["abilities"]
                        if ability["id"] == action["ability_id"]),
                }
                for action in rotation["actions"]
            },
        ))
        output["issues"].extend(pool_runtime.validate_bindings(mechanics_model, characters=sorted(chars)))
        output["issues"].extend(counter_view_runtime.validate_bindings(mechanics_model, characters=sorted(chars)))
        rule_ids = {r["id"] for r in mechanics_model["rules"]}
        for rule in mechanics_model["rules"]:
            for code, message in qualification_errors(rule, sorted(chars)):
                output["issues"].append(_issue(code, message, rule["id"] + ".beneficiaries"))
            if rule["owner"] not in chars or not set(rule["beneficiaries"].get("characters", [])).issubset(chars):
                output["issues"].append(_issue("reference", "规则角色不在队伍中。", rule["id"]))
            if rule["status"] == "pending" or not rule["source"].strip():
                output["issues"].append(_issue("missing_information", "规则依据待确认。", rule["id"], severity="pending"))
            for buff in projected.get("buffs", []):
                source = buff.get("extensions", {})
                if source.get("source_id") == rule["source_id"] and source.get("owner") == rule["owner"]:
                    output["issues"].append(_issue("duplicate_delivery", "手工 Buff 与声明式规则交付了相同来源部件。", rule["id"], buff_id=buff["id"]))
        fixed_sources = mechanics_model.get("fixed_effect_sources", [])
        for source in fixed_sources:
            if source["owner"] not in chars:
                output["issues"].append(_issue("reference", "固定来源提供者不在队伍中。", source["id"]))
            if source["status"] == "pending" or not source["source"].strip():
                output["issues"].append(_issue("missing_information", "固定来源依据待确认。", source["id"], severity="pending"))
            for buff in projected.get("buffs", []):
                identity = buff.get("extensions", {})
                if buff["id"] == source["id"]:
                    output["issues"].append(_issue("duplicate_id", "固定来源与手工Buff共用ID。", source["id"]))
                if identity.get("source_id") == source["source_id"] and identity.get("owner") == source["owner"]:
                    output["issues"].append(_issue("duplicate_delivery", "手工Buff与固定来源交付同一部件。", source["id"], buff_id=buff["id"]))
        for initial in mechanics_model["initial_state"]["rules"]:
            initial_rule = next(r for r in mechanics_model["rules"] if r["id"] == initial["rule_id"])
            if "eligibility" in initial_rule["beneficiaries"]:
                allowed = possible_recipients(initial_rule, sorted(chars))
                if any(not set(inst["beneficiaries"]).issubset(allowed) for inst in initial["instances"]):
                    output["issues"].append(_issue("beneficiary_initial_qualification", "初始实例包含没有资格的受益角色。", initial["rule_id"]))
            if any(not set(inst["beneficiaries"]).issubset(chars) for inst in initial["instances"]):
                output["issues"].append(_issue("reference", "初始实例受益者不在队伍中。", initial["rule_id"]))
        for event in mechanics_model["condition_events"]:
            if event["status"] == "pending" or not event["source"].strip():
                output["issues"].append(_issue("missing_information", "外部条件的依据待确认。", event["id"], severity="pending"))
        # Explicit source ownership is mandatory for generated resources.
        ownership = mechanics_model.get("resource_ownership", [])
        owned_parts = [(o["action_id"], o["source_part"]) for o in ownership]
        if len(set(owned_parts)) != len(owned_parts):
            output["issues"].append(_issue("duplicate_delivery", "同一动作的资源部件被多个规则接管。", "mechanics_model.resource_ownership"))
        replacements = set()
        original_events = {e["id"]: e for e in rotation["events"]}
        for owner in ownership:
            if owner["rule_id"] not in rule_ids:
                output["issues"].append(_issue("reference", "资源执行者规则不存在。", owner["rule_id"]))
            for eid in owner["replaces_event_ids"]:
                old = original_events.get(eid)
                source_action = old.get("action_id") if old else None
                if old and old["type"] == "energy_credit":
                    source_action = original_events.get(old["spawn_id"], {}).get("action_id")
                if old is None or source_action != owner["action_id"] or eid in replacements:
                    output["issues"].append(_issue("duplicate_delivery", "被接管的手工资源事件不存在、动作不符或重复接管。", eid))
                replacements.add(eid)
        for rule in mechanics_model["rules"]:
            for resource in rule.get("resources", []):
                owners = [o for o in ownership if o["rule_id"] == rule["id"] and o["source_part"] == resource["source_part"]]
                if not owners:
                    output["issues"].append(_issue("resource_ownership", "生成资源必须显式声明唯一执行者及接管的旧事件（可为空）。", rule["id"]))
                for owner in owners:
                    kind = "energy_spawn" if resource["kind"] == "particles" else "energy_direct"
                    collisions = [e["id"] for e in original_events.values() if e["type"] == kind and e.get("action_id") == owner["action_id"] and e["id"] not in replacements]
                    if collisions:
                        output["issues"].append(_issue("duplicate_delivery", "同一动作仍有未接管的同类手工资源；来源部件未证明独立，不能重复入账。", rule["id"], event_ids=collisions))
        for event in original_events.values():
            if event["type"] == "energy_credit" and event["spawn_id"] in replacements:
                replacements.add(event["id"])
        rotation["events"] = [e for e in rotation["events"] if e["id"] not in replacements]
        removed_rules = {}
        for action in rotation["actions"]:
            retained = []
            for outcome in action.get("rule_outcomes", []):
                old_ids = outcome.get("event_ids", [])
                if old_ids and all(eid in replacements for eid in old_ids):
                    removed_rules.setdefault(action["ability_id"], set()).add(outcome["rule_id"])
                else:
                    outcome["event_ids"] = [eid for eid in old_ids if eid not in replacements]
                    retained.append(outcome)
            if "rule_outcomes" in action:
                action["rule_outcomes"] = retained
        for ability in rotation["abilities"]:
            if ability["id"] in removed_rules:
                ability["rules"] = [r for r in ability.get("rules", []) if r["id"] not in removed_rules[ability["id"]]]
        for event in rotation["events"]:
            if event["type"] == "energy_spend":
                action = next((a for a in rotation["actions"] if a["id"] == event["action_id"]), None)
                if action and event.get("t") != action["start"]:
                    output["issues"].append(_issue("unsupported", "新机制路径一期仅支持动作开始时原子扣能；请把扣能事件放在 start 同刻的后续 order。", event["id"]))
        if output["issues"]:
            output["status"] = "invalid" if any(i.get("severity") == "error" for i in output["issues"]) else "pending"
            return output
        ctx, data = _semantic(rotation, projected)
        ctx.assumptions.extend(mechanics_model.get("assumptions", []))
        ctx.assumptions.extend(f"{r['id']}：{r['source']}" for r in mechanics_model["rules"] if r["status"] == "assumed")
        ctx.assumptions.extend(f"{s['id']}：{s['source']}" for s in fixed_sources if s["status"] == "assumed")
        ctx.assumptions.extend(f"{e['id']}：{e['source']}" for e in mechanics_model["condition_events"] if e["status"] == "assumed")
        ctx.assumptions.extend(f"捕获 {c['id']}：{c['source']}" for c in captures if c["status"] == "assumed")
        if "elemental_model" in mechanics_model:
            elemental = mechanics_model["elemental_model"]
            ctx.assumptions.extend(f"附着 {p.get('id', '目标初态')}：{p['source']}" for p in [elemental["target"], *elemental["profiles"]] if p["status"] == "assumed")
            if elemental.get("swirl_damage", {}).get("status") == "assumed":
                ctx.assumptions.append("普通扩散伤害：" + elemental["swirl_damage"]["source"])
        if ctx.issues:
            output["issues"].extend(ctx.issues)
            output["status"] = "invalid" if any(i.get("severity") == "error" for i in ctx.issues) else "pending"
            output["rotation_check"] = _base_result(output["status"], ctx.issues, ctx.assumptions)
            return output
        # Action ends have causal ordering separate from user events; ended actions
        # remain real even when later events extend beyond the cycle boundary.
        for action in rotation["actions"]:
            runtime.append({"id": action["id"]+":end", "kind": "mechanics_end", "time": _tick(action["occupation_end"]),
                            "order": action["order"], "causal": (-1,),
                            "data": {**action, "action_id": action["id"]}})
        for event in mechanics_model["condition_events"]:
            runtime.append({"id": event["id"], "kind": "mechanics_condition", "time": _tick(event["t"]),
                            "order": event["order"], "data": event})
        for event in mechanics_model.get("special_reaction_events", []):
            runtime.append({"id": event["id"], "kind": "special_reaction_operation",
                            "time": _tick(event["t"]), "order": event["order"],
                            "data": {**event, "repeat": {"mode": "once"}}})
        original_runtime = data["runtime"]
        data["runtime"] = [*original_runtime, *runtime]
        event_ids = [ev["id"] for ev in data["runtime"]]
        if len(set(event_ids)) != len(event_ids) or capture_ids & set(event_ids):
            output["issues"].append(_issue("duplicate_id", "动作、命中、结束与条件/资源事件必须使用唯一事件 ID。", "mechanics_model"))
            return output
        expanded, windows, finite_until = _expand(data, rotation, ctx)
        # A zero-length action end is a causal child, not ambiguous user ordering.
        zero_ends = {a["id"]+":end" for a in rotation["actions"] if a["start"] == a["occupation_end"]}
        ctx.issues = [i for i in ctx.issues if not (i["code"] == "ambiguous_order" and i["path"] in zero_ends)]
        expanded.sort(key=_event_sort_key)
        if ctx.issues:
            output["issues"].extend(ctx.issues)
            return output
        all_ids = {e["id"] for e in expanded} | capture_ids
        reaction_ids = {e["id"]+(":reaction:0" if _swirl_v2(mechanics_model) else ":reaction") for e in expanded if e["kind"] in ("mechanics_hit", "elemental_application")} if "elemental_model" in mechanics_model else set()
        reaction_damage_ids = {e["id"]+":reaction_damage:0" for e in expanded if e["kind"] in ("mechanics_hit", "elemental_application")} if _swirl_v2(mechanics_model) else set()
        if _freeze_reference(mechanics_model):
            parents = [e["id"] for e in expanded if e["kind"] in ("mechanics_hit", "elemental_application")]
            reaction_ids = {p+s for p in parents for s in (":reaction:freeze", ":reaction:swirl:hydro", ":reaction:swirl:cryo")}
            reaction_damage_ids = {p+":reaction_damage:swirl:"+el for p in parents for el in ("hydro", "cryo")}
            if any(eid.startswith("@elemental_state:") for eid in all_ids):
                output["issues"].append(_issue("reserved_id", "该前缀保留给元素状态到期。", "mechanics_model"))
        if (reaction_ids | reaction_damage_ids) & all_ids:
            output["issues"].append(_issue("duplicate_id", "输入事件 ID 占用了自动反应或派生伤害的保留标识。", "mechanics_model"))
        damage_types = set(DAMAGE_TYPES) | {r["id"] for r in projected["type_definitions"]["damage_types"]}
        conditions = {e["condition"] for e in mechanics_model["condition_events"]}
        state_rule_routes, state_operation_routes = {}, {}
        if any(reserved_id(eid) for eid in all_ids | reaction_ids | reaction_damage_ids):
            output["issues"].append(_issue("state_reserved_identity", "输入或隐式事件占用了生命周期内部身份。", "mechanics_model"))
        if "state_runtime" in mechanics_model:
            state_issues = validate_state_runtime_bindings(mechanics_model, character_ids=chars, event_ids=all_ids,
                action_ids=set(data["actions"]), conditions=conditions, damage_type_ids=damage_types, action_type_ids=set(ACTION_TYPES))
            output["issues"].extend(state_issues)
            if not state_issues:
                state_rule_routes = {r["id"]: route_event_id("dispatch_rule", r["id"]) for r in mechanics_model["rules"]
                                     if any(reserved_id(eid) for eid in r["trigger"].get("event_ids", []))}
                state_operation_routes = {r["id"]: route_event_id("resource_operation", r["id"]) for r in mechanics_model.get("shared_pools", {}).get("operations", [])
                                          if any(reserved_id(eid) for eid in r["trigger"].get("event_ids", []))}
        if "shared_pools" in mechanics_model:
            output["issues"].extend(pool_runtime.validate_references(mechanics_model["shared_pools"],
                all_ids=all_ids, action_ids=set(data["actions"]), character_ids=chars, damage_types=damage_types,
                action_types=set(ACTION_TYPES), conditions=conditions, reaction_ids=reaction_ids,
                reaction_damage_ids=reaction_damage_ids, capture_ids=capture_ids, state_operation_routes=state_operation_routes))
        for binding in mechanics_model.get("resource_threshold_triggers", {}).get("bindings", []):
            condition_id = binding.get("qualification", {}).get("condition")
            if condition_id is not None and condition_id not in conditions:
                output["issues"].append(_issue("threshold_condition_unknown", "阈值资格条件没有显式输入事件，不会推测为真。", binding["id"], severity="pending", condition=condition_id))
        for rule in [*mechanics_model["rules"], *fixed_sources]:
            selectors = [*([rule["trigger"]] if "event" in rule.get("trigger", {}) else []), *([rule["quota"]["consume"]] if rule.get("quota") else []),
                         *[e["filter"] for e in rule["effects"] if "filter" in e]]
            for selector in selectors:
                if capture_ids & set(selector.get("event_ids", [])):
                    output["issues"].append(_issue("unused_field", "捕获不能用作机制监听或收益过滤事件。", rule["id"]))
                for field, allowed in (("event_ids", reaction_ids if selector["event"] == "elemental_reaction" else reaction_damage_ids if selector["event"] == "reaction_damage" else all_ids), ("action_ids", set(data["actions"])), ("characters", chars),
                                       ("action_type_ids", set(ACTION_TYPES)), ("damage_type_ids", damage_types)):
                    if field == "event_ids" and selector is rule.get("trigger") and rule["id"] in state_rule_routes:
                        allowed = allowed | {state_rule_routes[rule["id"]]}
                    values = selector.get(field, [])
                    if (field in selector and not values) or len(values) != len(set(values)) or not set(values).issubset(allowed):
                        output["issues"].append(_issue("reference", "选择器引用不存在、重复或为空。", rule["id"], field=field, values=values))
                if "condition" in selector and selector["condition"] not in conditions:
                    output["issues"].append(_issue("missing_information", "条件没有显式输入事件，不会推测为真。", rule["id"], severity="pending", condition=selector["condition"]))
            for effect in rule["effects"]:
                if isinstance(effect["value"], dict) and effect["value"]["sample"] == "event" and effect["value"]["event_id"] not in all_ids:
                    output["issues"].append(_issue("reference", "属性采样事件不存在。", rule["id"], event_id=effect["value"]["event_id"]))
        matched_keys = set()
        for match in matches or []:
            ref = match.get("effect_ref", {})
            is_fixed = "fixed_source_id" in match or "fixed_source_id" in ref
            if is_fixed and ("rule_id" in match or "buff_id" in ref):
                output["issues"].append(_issue("reference", "fixed_source_id与rule_id/buff_id不可同时指定。", "matches"))
            rid = match.get("fixed_source_id", ref.get("fixed_source_id")) if is_fixed else match.get("rule_id", ref.get("buff_id"))
            rule = next((r for r in (fixed_sources if is_fixed else mechanics_model["rules"]) if r["id"] == rid), None)
            eid = match.get("effect_id", ref.get("effect_id"))
            key = (is_fixed, rid, eid, match.get("hit_id"))
            if key in matched_keys:
                output["issues"].append(_issue("duplicate_match", "一个规则收益与命中只能有一条例外。", "matches"))
            matched_keys.add(key)
            if not rule or eid not in {e["id"] for e in rule["effects"]} or match.get("hit_id") not in {h["id"] for h in projected["hits"]} or match.get("operation", match.get("mode")) not in ("include", "exclude"):
                output["issues"].append(_issue("reference", "match 规则、收益、命中或操作无效。", "matches"))
            elif match.get("operation", match.get("mode")) == "include":
                effect = next(e for e in rule["effects"] if e["id"] == eid)
                hit = next(h for h in projected["hits"] if h["id"] == match["hit_id"])
                if counter_view_runtime.is_counter_view(effect["value"]) and hit["damage_system"] != "classic":
                    output["issues"].append(_issue("counter_view_damage_scope", "显式微调不能把累计读数用于非经典伤害。", "matches"))
                if rule["id"] in _threshold_classic_rule_ids(mechanics_model) and hit["damage_system"] != "classic":
                    output["issues"].append(_issue("threshold_damage_scope", "显式微调不能把阈值收益用于非经典伤害。", "matches"))
        if output["issues"]:
            output["status"] = "invalid" if any(i["severity"] == "error" for i in output["issues"]) else "pending"
            return output
        executor = _Mechanics(projected, rotation, mechanics_model, data, matches)
        executor.windows = windows
        executor.event_budget_count = len(expanded)
        if "state_runtime" in mechanics_model:
            external = {(e["time"], e["order"]): {"event_id": e["id"], "t_us": e["time"], "order": e["order"]} for e in expanded}
            executor.state_runtime = StateRuntimeMechanics(mechanics_model, executor, character_ids=chars,
                event_ids=all_ids, action_ids=set(data["actions"]), conditions=conditions,
                damage_type_ids=damage_types, action_type_ids=set(ACTION_TYPES), external_events=list(external.values()))
        if executor.resource_reactions:
            executor.resource_reactions.bind_state()
        deadline = executor.schedule_deadline()
        if deadline:
            expanded.append(deadline)
            expanded.sort(key=_event_sort_key)
        result = _simulate(rotation, data, expanded, windows, finite_until, ctx, observer=executor)
        if result["status"] == "pending" and (result.get("automatic_energy_delivery")
                                              or result.get("action_capacity_consumption")
                                              or result.get("action_admission_execution")):
            output.update(status="pending", rotation_check=result, issues=result["issues"],
                          mechanics_trace=executor.trace, sampling_trace=executor.samples)
            if executor.elemental:
                output["elemental_trace"] = executor.elemental.trace
            if executor.fixed_sources:
                output.update(fixed_effect_sources=copy.deepcopy(list(executor.fixed_sources.values())),
                              target_condition_trace=executor.target_condition_trace)
            if executor.captures:
                output["snapshot_capture_trace"] = executor.capture_trace
            _attach_shared_pool_report(output, executor, MAX_MECHANICS_EVENTS)
            _attach_attribute_report(output, executor, MAX_MECHANICS_EVENTS)
            _attach_state_runtime_report(output, executor, MAX_MECHANICS_EVENTS)
            _attach_special_reaction_report(output, executor, MAX_MECHANICS_EVENTS)
            _attach_buff_state_timeline(output, mechanics_model, projected, rotation["period"])
            if executor.resource_reactions:
                executor.resource_reactions.attach(output)
            return output
        empty_derivation = _swirl_v2(mechanics_model) and not projected["hits"] and not executor.hit_rows
        if empty_derivation:
            executor.issues.append(_issue("no_reported_damage", "无输入命中且没有产生可计入伤害的派生事件，未发布伤害或DPS数字。",
                                          "mechanics_model.application_events", severity="pending"))
        resolved = copy.deepcopy(projected)
        resolved["hits"] = executor.hit_rows
        resolved["buffs"] = executor.projected_buffs
        resolved["loop_duration"] = rotation["period"]
        calculated = None
        if executor.hit_rows:
            include_attributes = bool(executor.attribute_runtime and executor.attribute_runtime.multistat)
            first_calculation = calculate(resolved, include_attribute_inputs=include_attributes)
            executor.special_damage_weighting = _apply_special_damage_weights(
                resolved, first_calculation, executor.special_damage_groups)
            executor.trace.extend(copy.deepcopy(executor.special_damage_weighting))
            executor.special_trace.extend(copy.deepcopy(executor.special_damage_weighting))
            calculated = (calculate(resolved, include_attribute_inputs=include_attributes)
                          if executor.special_damage_weighting else first_calculation)
        # The projection intentionally has no hand snapshot flag: every effect is
        # frozen to its correctly sampled numeric contribution for this hit.
        output.update(status=result["status"], rotation_check=result,
                      mechanics_trace=executor.trace, sampling_trace=executor.samples,
                      resolved_template=resolved if executor.hit_rows else None,
                      result=calculated if executor.hit_rows else {
                          "summary": {"total_damage": 0, "dps": None, "by_skill": {}, "by_character": {}},
                          "hits": [], "buffs_coverage": [], "assumptions": ["没有已验证的计入总伤命中；不能据此认为计划伤害为零。"]})
        for row in output["result"]["hits"]:
            expected_attribute = executor.attribute_scaling_receipts.get(row["id"])
            if expected_attribute is not None and row["attribute_inputs"]["scaling"] != expected_attribute["value"]:
                raise AttributeLedgerError("attribute_projection_mismatch", "攻击力账与真实经典伤害缩放输入不一致", row["id"])
        if fixed_sources:
            output.update(fixed_effect_sources=copy.deepcopy(fixed_sources), target_condition_trace=executor.target_condition_trace)
            output["result"].update(fixed_effect_sources=copy.deepcopy(fixed_sources),
                                    target_condition_trace=copy.deepcopy(executor.target_condition_trace))
        output["issues"].extend(result["issues"] + executor.issues)
        if executor.issues and output["status"] not in ("invalid", "failed"):
            output["status"] = "pending"
            result["status"] = "pending"
            result["energy_check"].update(status="not_verified", cycle_status="not_verified", proven_cycle=None)
            result["energy_check"]["proof_scope"]["kind"] = "checked_windows"
            result["action_check"]["status"] = "not_verified"
        if (not executor.hit_rows and not empty_derivation
                and output["status"] in ("passed", "unproven") and not output["issues"]
                and candidate_sources_known(expanded, mechanics_model, data["period"])):
            candidates = collect_damage_candidates(expanded, data["period"])
            coverage = classify_verified_empty_damage(candidates, result, period_ticks=data["period"],
                mechanics_model=mechanics_model, issues=output["issues"], reported_damage_events=0)
            if coverage is not None:
                output["resolved_template"] = resolved
                output["result"] = _verified_empty_result(resolved)
                output["result"]["damage_reporting"] = coverage
                if fixed_sources:
                    output["result"].update(fixed_effect_sources=copy.deepcopy(fixed_sources),
                        target_condition_trace=copy.deepcopy(executor.target_condition_trace))
        result["unchecked_constraints"] = [x for x in result["unchecked_constraints"] if x != "伤害稳态与 Buff 刷新"] + ["未声明的机制完整性", "后续轮次伤害汇总与实战 DPS"]
        result["damage_interpretation"] = "完整显式输入窗口（origin0）的已执行事件数值投影；内部检查窗口不是游戏第一轮，不自动复制动作，不证明实战稳态DPS。"
        output["result"]["mechanics"] = {"schema_version": 1, "scope": "verified_first_cycle_prefix",
                                          "complete_first_cycle": result["energy_check"]["completed_cycles"] >= 1,
                                          "execution_status": output["status"]}
        if executor.elemental:
            output["elemental_trace"] = executor.elemental.trace
            output["result"]["elemental"] = {"ruleset": executor.elemental.model["ruleset"], "mode": "simulated",
                "input_window_state": executor.elemental.window_state,
                "final_checked_state": executor.elemental.state(), "trace": executor.elemental.trace}
            if _freeze_reference(mechanics_model):
                output["result"]["elemental"].update(evidence_tier="experimental_reference",
                    reference_contract=copy.deepcopy(REFERENCE_CONTRACT),
                    reference_acceptance=copy.deepcopy(executor.elemental.model["reference_acceptance"]))
            if _swirl_v2(mechanics_model):
                output["result"]["elemental"]["event_counts"] = {"total_scheduled_including_synchronous": executor.event_budget_count,
                    "synchronous": dict(executor.synchronous_counts), "limit": MAX_MECHANICS_EVENTS}
        if empty_derivation:
            output["result"] = None
    except DamageReportingLimit as exc:
        output.update(status="pending", result=None, resolved_template=None)
        output["issues"].append(exc.issue)
    except AttributeLedgerError as exc:
        output.update(status=exc.issue["severity"], result=None, resolved_template=None)
        output["issues"].append(exc.issue)
        if hasattr(exc, "rotation_check_prefix"):
            output["rotation_check"] = exc.rotation_check_prefix
        if "executor" in locals():
            output.update(mechanics_trace=executor.trace, sampling_trace=executor.samples)
    except SharedPoolError as exc:
        output.update(status="pending" if exc.issue["severity"] == "pending" or "limit" in exc.issue["code"] or exc.issue["code"] == "counter_view_value" else "invalid", result=None, resolved_template=None)
        if output["status"] == "pending":
            exc.issue["severity"] = "pending"
        if hasattr(exc, "rotation_check_prefix"):
            output["rotation_check"] = exc.rotation_check_prefix
        output["issues"].append(exc.issue)
        if "executor" in locals():
            output.update(mechanics_trace=executor.trace, sampling_trace=executor.samples)
    except StateRuntimeError as exc:
        output.update(status="pending" if exc.issue["severity"] == "pending" else "invalid", result=None, resolved_template=None)
        output["issues"].append(exc.issue)
        if hasattr(exc, "rotation_check_prefix"):
            output["rotation_check"] = exc.rotation_check_prefix
        if "executor" in locals():
            output.update(mechanics_trace=executor.trace, sampling_trace=executor.samples)
    except (ElementalPending, SpecialReactionPending, TargetStatePending, SnapshotCapturePending) as exc:
        output.update(status="pending", result=None, resolved_template=None)
        if hasattr(exc, "rotation_check_prefix"):
            output["rotation_check"] = exc.rotation_check_prefix
        output["issues"].append(exc.issue)
        output["mechanics_trace"] = executor.trace
        if executor.counter_views and executor.counter_views.frames:
            output["sampling_trace"] = executor.samples
        if executor.elemental:
            output["elemental_trace"] = executor.elemental.trace
        if executor.fixed_sources:
            output.update(fixed_effect_sources=copy.deepcopy(list(executor.fixed_sources.values())),
                          target_condition_trace=executor.target_condition_trace)
    except (ValueError, KeyError, TypeError, ArithmeticError, TemplateError) as exc:
        output["issues"].append(_issue("mechanics_execution", str(exc), "mechanics_model"))
        output["status"] = "invalid"
    if "executor" in locals() and executor.captures:
        output["snapshot_capture_trace"] = executor.capture_trace
        if output.get("result") is not None:
            output["result"]["snapshot_captures"] = {
                "contract": "action-snapshot-capture-v1", "trace": copy.deepcopy(executor.capture_trace),
                "storage_bytes": executor.capture_bytes, "max_storage_bytes": capture_contract.MAX_SNAPSHOT_CAPTURE_BYTES,
                "total_scheduled_events": executor.event_budget_count, "max_events": MAX_MECHANICS_EVENTS}
    if "executor" in locals():
        _attach_shared_pool_report(output, executor, MAX_MECHANICS_EVENTS)
        _attach_attribute_report(output, executor, MAX_MECHANICS_EVENTS)
        _attach_state_runtime_report(output, executor, MAX_MECHANICS_EVENTS)
        _attach_special_reaction_report(output, executor, MAX_MECHANICS_EVENTS)
        _attach_buff_state_timeline(output, mechanics_model, projected, rotation["period"])
        if executor.resource_reactions:
            executor.resource_reactions.attach(output)
    return output


def _freeze_container(value, memo):
    if id(value) in memo:
        return memo[id(value)]
    if type(value) is dict:
        result = {}
        memo[id(value)] = result
        result.update((_freeze_container(key, memo), _freeze_container(item, memo)) for key, item in value.items())
        return result
    if type(value) is list:
        result = []
        memo[id(value)] = result
        result.extend(_freeze_container(item, memo) for item in value)
        return result
    if type(value) is set:
        result = set()
        memo[id(value)] = result
        result.update(_freeze_container(item, memo) for item in value)
        return result
    if type(value) is tuple:
        result = tuple(_freeze_container(item, memo) for item in value)
        memo[id(value)] = result
        return result
    if type(value) is frozenset:
        result = frozenset(_freeze_container(item, memo) for item in value)
        memo[id(value)] = result
        return result
    return value


def build_reference_mechanics_entry_v1(namespace: dict[str, Any]) -> FunctionType:
    memo = {}
    frozen = {key: value if key == "__builtins__" else _freeze_container(value, memo)
              for key, value in namespace.items()}
    source = _canonical_execute_mechanics_v1
    clone = FunctionType(source.__code__, frozen, "execute_mechanics", source.__defaults__, source.__closure__)
    clone.__kwdefaults__ = copy.deepcopy(source.__kwdefaults__)
    clone.__annotations__ = copy.deepcopy(source.__annotations__)
    return clone

