"""mechanics binding admission in the single Mechanics execution pipeline."""

from __future__ import annotations

from genshin_engine.mechanics_execution_context import MechanicsExecutionContext
from genshin_engine.mechanics_execution_services import MechanicsExecutionServices
from genshin_engine.resource_source_identity import ownership_key


def project_execution_inputs(run: MechanicsExecutionContext, services: MechanicsExecutionServices) -> bool:
    run.projected, run.rotation, run.runtime_events = services.project_inputs(run.request.template, run.request.rotation_model, run.request.mechanics_model, run.output["issues"])
    if run.output["issues"]:
        if run.request.mechanics_model.get("target_scene") and all(
                issue["severity"] == "pending" for issue in run.output["issues"]):
            run.output["status"] = "pending"
        return False
    run.chars = {c["id"] for c in run.rotation["characters"]}
    return True


def admit_effect_and_resource_bindings(run: MechanicsExecutionContext, services: MechanicsExecutionServices) -> bool:
    from genshin_engine.special_damage_echo import (
        validate_bindings as validate_echo_bindings,
    )
    run.output["issues"].extend(validate_echo_bindings(run.request.mechanics_model,
        characters=run.chars, period=run.rotation["period"]))
    run.output["issues"].extend(services.validate_special_reaction_bindings(
        run.request.mechanics_model,
        character_ids=sorted(run.chars),
        action_ids={action["id"] for action in run.rotation["actions"]},
        action_bindings={
            action["id"]: {
                "ability_id": action["ability_id"],
                "start": action["start"], "order": action["order"],
                "repeat": action["repeat"],
                "character_id": next(
                    ability["character"] for ability in run.rotation["abilities"]
                    if ability["id"] == action["ability_id"]),
                "variant": next(
                    ability.get("variant") for ability in run.rotation["abilities"]
                    if ability["id"] == action["ability_id"]),
                "effective_level": next(
                    ability.get("effective_level")
                    for ability in run.rotation["abilities"]
                    if ability["id"] == action["ability_id"]),
            }
            for action in run.rotation["actions"]
        },
    ))
    run.output["issues"].extend(services.pool_runtime.validate_bindings(run.request.mechanics_model, characters=sorted(run.chars)))
    run.output["issues"].extend(services.counter_view_runtime.validate_bindings(run.request.mechanics_model, characters=sorted(run.chars)))
    rule_ids = {r["id"] for r in run.request.mechanics_model["rules"]}
    for rule in run.request.mechanics_model["rules"]:
        for code, message in services.qualification_errors(rule, sorted(run.chars)):
            run.output["issues"].append(services.issue(code, message, rule["id"] + ".beneficiaries"))
        if rule["owner"] not in run.chars or not set(rule["beneficiaries"].get("characters", [])).issubset(run.chars):
            run.output["issues"].append(services.issue("reference", "规则角色不在队伍中。", rule["id"]))
        if rule["status"] == "pending" or not rule["source"].strip():
            run.output["issues"].append(services.issue("missing_information", "规则依据待确认。", rule["id"], severity="pending"))
        for buff in run.projected.get("buffs", []):
            source = buff.get("extensions", {})
            if source.get("source_id") == rule["source_id"] and source.get("owner") == rule["owner"]:
                run.output["issues"].append(services.issue("duplicate_delivery", "手工 Buff 与声明式规则交付了相同来源部件。", rule["id"], buff_id=buff["id"]))
    run.fixed_sources = run.request.mechanics_model.get("fixed_effect_sources", [])
    for source in run.fixed_sources:
        if not set(source.get('recipient_ids', [])).issubset(run.chars):
            run.output['issues'].append(services.issue('reference', '固定来源受益者不在队伍中。', source['id'] + '.recipient_ids'))
        if source["owner"] not in run.chars:
            run.output["issues"].append(services.issue("reference", "固定来源提供者不在队伍中。", source["id"]))
        if source["status"] == "pending" or not source["source"].strip():
            run.output["issues"].append(services.issue("missing_information", "固定来源依据待确认。", source["id"], severity="pending"))
        for buff in run.projected.get("buffs", []):
            identity = buff.get("extensions", {})
            if buff["id"] == source["id"]:
                run.output["issues"].append(services.issue("duplicate_id", "固定来源与手工Buff共用ID。", source["id"]))
            if identity.get("source_id") == source["source_id"] and identity.get("owner") == source["owner"]:
                run.output["issues"].append(services.issue("duplicate_delivery", "手工Buff与固定来源交付同一部件。", source["id"], buff_id=buff["id"]))
    for initial in run.request.mechanics_model["initial_state"]["rules"]:
        initial_rule = next(r for r in run.request.mechanics_model["rules"] if r["id"] == initial["rule_id"])
        if "eligibility" in initial_rule["beneficiaries"]:
            allowed = services.possible_recipients(initial_rule, sorted(run.chars))
            if any(not set(inst["beneficiaries"]).issubset(allowed) for inst in initial["instances"]):
                run.output["issues"].append(services.issue("beneficiary_initial_qualification", "初始实例包含没有资格的受益角色。", initial["rule_id"]))
        if any(not set(inst["beneficiaries"]).issubset(run.chars) for inst in initial["instances"]):
            run.output["issues"].append(services.issue("reference", "初始实例受益者不在队伍中。", initial["rule_id"]))
    for event in run.request.mechanics_model["condition_events"]:
        if event["status"] == "pending" or not event["source"].strip():
            run.output["issues"].append(services.issue("missing_information", "外部条件的依据待确认。", event["id"], severity="pending"))
    # Explicit source ownership is mandatory for generated resources.
    ownership = run.request.mechanics_model.get("resource_ownership", [])
    resource_rules = {r['id']: r for r in run.request.mechanics_model['rules']}
    owned_parts = [ownership_key(o, resource_rules) for o in ownership]
    if len(set(owned_parts)) != len(owned_parts):
        run.output["issues"].append(services.issue("duplicate_delivery", "同一动作的资源部件被多个规则接管。", "mechanics_model.resource_ownership"))
    replacements = set()
    original_events = {e["id"]: e for e in run.rotation["events"]}
    for owner in ownership:
        if ('action_id' in owner) == ('source_ref' in owner):
            run.output['issues'].append(services.issue('resource_source', '资源所有权必须在动作引用和当前真实触发引用中选择一种。', 'mechanics_model.resource_ownership'))
        if owner["rule_id"] not in rule_ids:
            run.output["issues"].append(services.issue("reference", "资源执行者规则不存在。", owner["rule_id"]))
        for eid in owner["replaces_event_ids"]:
            old = original_events.get(eid)
            source_action = old.get("action_id") if old else None
            if old and old["type"] == "energy_credit":
                source_action = original_events.get(old["spawn_id"], {}).get("action_id")
            identity = (old or {}).get('resource_identity')
            source_rule = resource_rules.get(owner['rule_id'], {})
            expected_identity = {'source_id': source_rule.get('source_id'), 'source_part': owner['source_part']}
            same_source = (source_action == owner.get('action_id') if 'action_id' in owner else identity == expected_identity)
            if old is None or not same_source or eid in replacements:
                run.output["issues"].append(services.issue("duplicate_delivery", "被接管的手工资源事件不存在、动作不符或重复接管。", eid))
            replacements.add(eid)
    for rule in run.request.mechanics_model["rules"]:
        for resource in rule.get("resources", []):
            owners = [o for o in ownership if o["rule_id"] == rule["id"] and o["source_part"] == resource["source_part"]]
            if not owners:
                run.output["issues"].append(services.issue("resource_ownership", "生成资源必须显式声明唯一执行者及接管的旧事件（可为空）。", rule["id"]))
            for owner in owners:
                kind = "energy_spawn" if resource["kind"] == "particles" else "energy_direct"
                expected_identity = {'source_id': rule['source_id'], 'source_part': resource['source_part']}
                admitted_sources = {r['source_id'] for r in resource_rules.values()} | {s['source_id'] for s in run.fixed_sources}
                collisions = []
                for e in original_events.values():
                    if e['type'] != kind or e['id'] in replacements:
                        continue
                    identity = e.get('resource_identity')
                    if identity is not None and identity['source_id'] not in admitted_sources:
                        run.output['issues'].append(services.issue('resource_source', '手工资源身份引用了未接纳来源。', e['id']))
                    same_cause = 'source_ref' in owner or e.get('action_id') == owner.get('action_id')
                    if same_cause and (identity is None or identity == expected_identity):
                        collisions.append(e['id'])
                if collisions:
                    run.output["issues"].append(services.issue("duplicate_delivery", "同一动作仍有未接管的同类手工资源；来源部件未证明独立，不能重复入账。", rule["id"], event_ids=collisions))
    for event in original_events.values():
        if event["type"] == "energy_credit" and event["spawn_id"] in replacements:
            replacements.add(event["id"])
    run.rotation["events"] = [e for e in run.rotation["events"] if e["id"] not in replacements]
    removed_rules = {}
    for action in run.rotation["actions"]:
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
    for ability in run.rotation["abilities"]:
        if ability["id"] in removed_rules:
            ability["rules"] = [r for r in ability.get("rules", []) if r["id"] not in removed_rules[ability["id"]]]
    for event in run.rotation["events"]:
        if event["type"] == "energy_spend":
            action = next((a for a in run.rotation["actions"] if a["id"] == event["action_id"]), None)
            if action and event.get("t") != action["start"]:
                run.output["issues"].append(services.issue("unsupported", "新机制路径一期仅支持动作开始时原子扣能；请把扣能事件放在 start 同刻的后续 order。", event["id"]))
    if run.output["issues"]:
        run.output["status"] = "invalid" if any(i.get("severity") == "error" for i in run.output["issues"]) else "pending"
        return False
    return True

