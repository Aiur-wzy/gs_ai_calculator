"""Integration boundary between mechanics input and the special reaction runtime."""
from __future__ import annotations

import copy
from collections.abc import Iterable

from genshin_engine.elemental_schema import MULTI_AURA_RULESET
from genshin_engine.mechanics_validation import _tick
from genshin_engine.rotation_port import repeat_origins as _origins
from genshin_engine.special_reaction_actual import (
    DIRECT_LUNAR_ELEMENTS,
    REQUIRED_CAPABILITY_TAGS,
    SpecialReactionRuntime,
    validate_direct_lunar_damage_profile,
    validate_direct_stellar_damage_profile,
    validate_special_reaction_model,
)
from genshin_engine.special_reaction_capabilities import (
    load_reviewed_special_reaction_capabilities,
    special_reaction_capability_entry,
    verified_capability_tags,
)


def _issue(code: str, message: str, path: str, *, severity: str = "error", **details) -> dict:
    return {"code": code, "message": message, "path": path,
            "severity": severity, **details}


def validate_special_reaction_bindings(
    mechanics_model: dict,
    *,
    character_ids: Iterable[str] = (),
    action_ids: Iterable[str] = (),
    action_bindings: dict[str, dict] | None = None,
) -> list[dict]:
    """Validate roster, database-capability and operation references.

    Character names never enter this boundary.  A replacement is usable only
    when its explicit provider is in the real party and the database projection
    delivered the exact capability tag required by that reaction.
    """
    special = mechanics_model.get("special_reaction_model")
    rows = mechanics_model.get("special_reaction_party_capabilities", [])
    from genshin_engine.resource_damage_emission import validation_events
    event_declarations = [(f'mechanics_model.special_reaction_events.{i}', e)
                          for i, e in enumerate(mechanics_model.get('special_reaction_events', []))]
    event_declarations.extend(validation_events(mechanics_model))
    events = [event for _, event in event_declarations]
    dew_conditions = [event for event in mechanics_model.get("condition_events", ()) if "dew_consumption" in event]
    resource_conditions = [event for event in mechanics_model.get("condition_events", ()) if "stellar_resource" in event]
    if special is None:
        if rows or events or dew_conditions or resource_conditions:
            return [_issue(
                "special_reaction_model_missing",
                "星月资格或操作需要显式special_reaction_model；不会从队伍名称自动开启。",
                "mechanics_model.special_reaction_model",
            )]
        return []

    issues = [
        _issue(row["code"], row["message"], "mechanics_model."+row["path"],
               **{key: copy.deepcopy(value) for key, value in row.items()
                  if key not in {"code", "message", "path"}})
        for row in validate_special_reaction_model(special)
    ]
    elemental = mechanics_model.get("elemental_model", {})
    if elemental.get("ruleset") != MULTI_AURA_RULESET:
        issues.append(_issue(
            "special_reaction_ruleset",
            "星月替换必须接在gauge-multi-aura-v4的未提交普通反应提案之前。",
            "mechanics_model.elemental_model.ruleset",
        ))

    roster = set(character_ids)
    action_ids = set(action_ids)
    action_bindings = action_bindings or {}
    target_id = (elemental.get("target") or {}).get("id")
    seen: set[str] = set()
    tags_by_character: dict[str, set[str]] = {}
    for index, row in enumerate(rows):
        path = f"mechanics_model.special_reaction_party_capabilities.{index}"
        cid = row.get("character_id")
        if cid in seen:
            issues.append(_issue("duplicate_provider", "每名队员只能有一条星月能力投影。", path))
        seen.add(cid)
        if cid not in roster:
            issues.append(_issue("special_provider_absent", "星月能力投影引用的角色不在当前队伍。", path,
                                 character_id=cid))
        if row.get("status") == "pending":
            issues.append(_issue(
                "special_capability_pending", "数据库能力标签仍待核实，不能据此开启替换。",
                path, severity="pending", character_id=cid,
            ))
            continue
        commit = row.get("equipment_upstream_commit")
        catalog = load_reviewed_special_reaction_capabilities(commit)
        entry = special_reaction_capability_entry(row.get("db_character_id"), commit)
        tags = verified_capability_tags(row)
        if not tags:
            details = {
                "character_id": cid,
                "db_character_id": row.get("db_character_id"),
                "skill_depot_id": row.get("skill_depot_id"),
                "expected_catalog_id": catalog["collection_id"],
                "expected_catalog_revision": catalog["revision"],
                "expected_catalog_content_hash": catalog["content_hash"],
                "expected_catalog_entry_fingerprint": (
                    entry.get("entry_fingerprint") if entry else None),
            }
            if entry is None:
                issues.append(_issue(
                    "special_capability_character_unknown",
                    "数据库角色不在受审星月反应资格目录中。", path, **details,
                ))
            elif entry.get("runtime_support_status") != "supported":
                issues.append(_issue(
                    "special_capability_runtime_profile_pending",
                    "该角色的替换被动已确认，但其专属运行时变体尚未实现，不能套用通用规则。",
                    path, severity="pending",
                    runtime_profile_id=entry.get("runtime_profile_id"), **details,
                ))
            elif not set(row.get("capability_tags", [])) <= set(entry["capability_tags"]):
                issues.append(_issue(
                    "special_capability_forged",
                    "声明的星月能力标签不属于该数据库角色。", path,
                    catalog_capability_tags=copy.deepcopy(entry["capability_tags"]), **details,
                ))
            elif (row.get("skill_depot_id") != entry.get("required_skill_depot_id")
                  or row.get("runtime_profile_id") != entry.get("runtime_profile_id")
                  or row.get("catalog_entry_fingerprint") != entry.get("entry_fingerprint")):
                issues.append(_issue(
                    "special_capability_form_mismatch",
                    "角色技能形态或专属运行时档案与受审资格不一致。", path,
                    required_skill_depot_id=entry.get("required_skill_depot_id"),
                    required_runtime_profile_id=entry.get("runtime_profile_id"), **details,
                ))
            else:
                issues.append(_issue(
                    "special_capability_catalog_mismatch",
                    "能力投影未引用当前受审目录快照。", path, **details,
                ))
            continue
        tags_by_character[cid] = set(tags)

    for reaction_type, entry in special.get("replacements", {}).items():
        if not isinstance(entry, dict) or not entry.get("enabled"):
            continue
        qualification = entry.get("qualification") or {}
        expected = REQUIRED_CAPABILITY_TAGS.get(reaction_type)
        providers = qualification.get("provider_ids", [])
        proven = [cid for cid in providers if cid in roster and expected in tags_by_character.get(cid, set())]
        if not proven:
            issues.append(_issue(
                "special_capability_unproven",
                "已开启替换没有同时满足入队与数据库能力标签的提供者。",
                f"mechanics_model.special_reaction_model.replacements.{reaction_type}.qualification",
                severity="pending", provider_ids=copy.deepcopy(providers), expected_capability_tag=expected,
            ))
        if reaction_type == 'stellar_superconduct':
            resource = (entry.get('mechanics') or {}).get('periodic_resource')
            for row in rows:
                actor = row.get('character_id')
                if (actor not in roster or expected not in tags_by_character.get(actor, set())
                        or row.get('runtime_profile_id') != 'stellar_conduct.gradient_beacon_v1'):
                    continue
                if not isinstance(resource, dict) or resource.get('owner_id') != actor:
                    issues.append(_issue(
                        'special_capability_resource_profile_required',
                        '队伍包含定时信标变体；请在periodic_resource中声明该角色的生成、寿命、容量及区域，不能套用普通棱晶。',
                        'mechanics_model.special_reaction_model.replacements.stellar_superconduct.mechanics.periodic_resource',
                        severity='pending', character_id=actor,
                        required_owner_id=actor, runtime_profile_id=row['runtime_profile_id']))

    seen_events: set[str] = set()
    events_by_id = {event.get("id"): event for event in events}
    elemental_events_by_id = {event.get("id"): event for event in mechanics_model.get("elemental_state_events", ())}
    enabled_replacements = {
        reaction_type
        for reaction_type, entry in special.get("replacements", {}).items()
        if isinstance(entry, dict) and entry.get("enabled")
    }
    for path, event in event_declarations:
        event_id = event.get("id")
        if event_id in seen_events:
            issues.append(_issue("duplicate_id", "星月操作ID不得重复。", path))
        seen_events.add(event_id)
        if event.get("action_id") not in action_ids:
            issues.append(_issue("reference", "星月操作引用的动作不存在。", path,
                                 action_id=event.get("action_id")))
        if event.get("target_id") != target_id:
            issues.append(_issue("reference", "星月操作目标必须与元素运行时目标一致。", path,
                                 target_id=event.get("target_id"), elemental_target_id=target_id))
        kind = event.get("kind")
        action = action_bindings.get(event.get("action_id"))
        requires_action = (kind in {"lunar_bloom_reserve_grant", "stellar_resource_operation"}
                           or kind == "lunar_bloom_consume" and ("reserve_pool_ids" in event or "spend_policy" in event)
                           or "dew_consumption" in event or "bloom_core_absorption" in event or "resource_consumption" in event)
        if requires_action and action and "start" in action:
            if (_tick(event["t"]), event["order"]) <= (_tick(action["start"]), action["order"]):
                issues.append(_issue("causality", "月绽放资源或关联直伤必须位于来源动作之后。", path))
            if "repeat" in action and 0 not in _origins(action["repeat"], 1):
                issues.append(_issue("reference", "当前月绽放操作为单次事件，来源动作须在同一展开轮次执行。", path))
        if kind == "stellar_resource_operation":
            resource = special.get("replacements", {}).get("stellar_superconduct", {}).get("mechanics", {}).get("periodic_resource")
            if "stellar_superconduct" not in enabled_replacements or not resource:
                issues.append(_issue("stellar_resource_config_missing", "资源操作需要开启星超导并明确periodic_resource配置。", path))
            elif not action or action.get("character_id") != resource.get("owner_id") or event.get("area_id") != resource.get("area_id"):
                issues.append(_issue("stellar_resource_owner_mismatch", "资源操作必须属于配置中的实际角色和区域。", path))
            operation = event.get("operation")
            allowed = {"grant": {"amount"}, "consume": {"amount"}, "set_mode": {"mode", "duration_seconds"},
                       "end_mode": set(), "observe": {"counter", "claim_id", "divisor"}, "trial": {"trial", "outcome", "probability_mode"}}
            required = {"grant": {"amount"}, "consume": {"amount"}, "set_mode": {"mode", "duration_seconds"},
                        "end_mode": set(), "observe": {"counter"}, "trial": {"trial"}}
            parameters = set(event) & {"amount", "mode", "duration_seconds", "counter", "claim_id", "divisor", "trial", "outcome", "probability_mode"}
            if operation not in allowed or parameters - allowed[operation] or required[operation] - parameters:
                issues.append(_issue("stellar_resource_parameters", "资源操作缺少必需参数或包含不属于此操作的参数。", path))
            elif operation == "trial" and resource:
                from genshin_engine.probabilistic_resource_contract import (
                    resource_trial,
                    trial_mode,
                )
                try:
                    trial_mode(resource_trial(event["trial"], resource["capacity"]), event.get('probability_mode'), event.get('outcome'))
                except ValueError as exc:
                    issues.append(_issue("stellar_resource_trial_invalid", str(exc), path))
            elif operation == "observe":
                if event.get("counter") == "inventory":
                    if "claim_id" in event or event.get("divisor", 1) != 1:
                        issues.append(_issue("stellar_resource_observation", "库存观察不能使用计数游标或除数。", path))
                elif not event.get("claim_id"):
                    issues.append(_issue("stellar_resource_observation", "累计计数观察必须指定独立claim_id。", path))
        if kind in {"lunar_bloom_consume", "lunar_bloom_reserve_grant"} and "lunar_bloom" not in enabled_replacements:
            issues.append(_issue("special_switch_disabled", "草露资源操作需要显式开启月绽放替换。", path))
        elif kind == "direct_lunar_damage":
            ability_profile = event.get("ability_profile")
            profile_issues = validate_direct_lunar_damage_profile(ability_profile)
            for profile_issue in profile_issues:
                original_path = profile_issue.get("path", "ability_profile")
                suffix = (original_path[len("ability_profile"):]
                          if original_path.startswith("ability_profile")
                          else "."+original_path)
                issues.append(_issue(
                    "direct_lunar_profile_"+profile_issue["code"],
                    profile_issue["message"], path+".ability_profile"+suffix,
                    severity=("pending" if profile_issue["code"]
                              == "ability_binding_required" else "error"),
                    **{key: copy.deepcopy(value)
                       for key, value in profile_issue.items()
                       if key not in {"code", "message", "path", "severity"}},
                ))
            parameters = (ability_profile or {}).get("parameters") if isinstance(
                ability_profile, dict) else None
            reaction_type = parameters.get("reaction_type") if isinstance(parameters, dict) else None
            consumption = event.get("dew_consumption")
            if consumption is not None:
                consumed = events_by_id.get(consumption["event_id"])
                if (reaction_type != "lunar_bloom" or consumed is None
                        or consumed.get("kind") != "lunar_bloom_consume"
                        or any(consumed.get(key) != event.get(key) for key in ("action_id", "target_id", "area_id"))
                        or (_tick(consumed["t"]), consumed["order"]) >= (_tick(event["t"]), event["order"])
                        or (action and "start" in action and
                            (_tick(consumed["t"]), consumed["order"]) <= (_tick(action["start"]), action["order"]))):
                    issues.append(_issue("lunar_bloom_consumption_binding_invalid",
                        "耗露直伤需引用同动作、目标及区域的先前消费事件。", path+".dew_consumption"))
            absorption = event.get("bloom_core_absorption")
            if absorption is not None:
                absorbed = elemental_events_by_id.get(absorption["event_id"])
                if (reaction_type != "lunar_bloom" or absorbed is None
                        or absorbed.get("kind") != "bloom_core_absorb"
                        or any(absorbed.get(key) != event.get(key) for key in ("action_id", "target_id", "area_id"))
                        or (_tick(absorbed["t"]), absorbed["order"]) >= (_tick(event["t"]), event["order"])
                        or 0 not in _origins(absorbed["repeat"], 1)):
                    issues.append(_issue("bloom_core_absorption_binding_invalid",
                        "草原核直伤加成需引用当前计算中同动作、目标及区域的先前实际吸收。", path+".bloom_core_absorption"))
            if reaction_type in DIRECT_LUNAR_ELEMENTS and reaction_type not in enabled_replacements:
                issues.append(_issue(
                    "special_switch_disabled",
                    "显式直伤月曜事件需要开启其对应的月曜反应替换。",
                    path, reaction_type=reaction_type,
                ))
            profile_status = (ability_profile or {}).get("status") if isinstance(
                ability_profile, dict) else None
            if profile_status in ("confirmed", "assumed") and event.get("status") != profile_status:
                issues.append(_issue(
                    "direct_lunar_status_mismatch",
                    "直伤月曜事件与能力profile必须使用相同审计状态，避免隐藏假设。",
                    path+".status", event_status=event.get("status"),
                    ability_profile_status=profile_status,
                ))
            binding = (ability_profile or {}).get("ability_binding") if isinstance(
                ability_profile, dict) else None
            actual = action_bindings.get(event.get("action_id"))
            if binding is not None and (
                    not isinstance(actual, dict)
                    or binding.get("character_id") != actual.get("character_id")
                    or binding.get("ability_id") != actual.get("ability_id")
                    or binding.get("variant_id") != actual.get("variant")
                    or type(actual.get("effective_level")) is not int
                    or binding.get("effective_level")
                    != actual.get("effective_level")):
                issues.append(_issue(
                    "direct_special_ability_binding_mismatch",
                    "直伤月曜profile没有绑定当前动作的角色与技能。", path,
                    ability_binding=copy.deepcopy(binding),
                    action_binding=copy.deepcopy(actual),
                ))
            elif isinstance(binding, dict):
                required_tag = REQUIRED_CAPABILITY_TAGS.get(
                    (ability_profile.get("parameters") or {}).get("reaction_type"))
                if required_tag not in tags_by_character.get(
                        binding.get("character_id"), set()):
                    issues.append(_issue(
                        "direct_special_owner_capability_unproven",
                        "直伤月曜角色本人缺少对应数据库能力标签。", path,
                        character_id=binding.get("character_id"),
                        required_capability_tag=required_tag,
                    ))
        elif kind == "direct_stellar_damage":
            debit = event.get("resource_consumption")
            if debit:
                consumed = events_by_id.get(debit["event_id"])
                if (not consumed or consumed.get("kind") != "stellar_resource_operation"
                    or not (consumed.get("operation") in {"consume", "trial", "set_mode", "end_mode"} or
                            consumed.get("operation") == "observe" and consumed.get("counter") in {"consumed", "overflow_consumed", "released"})
                    or any(consumed.get(k) != event.get(k) for k in ("action_id", "area_id", "target_id"))
                    or (_tick(consumed["t"]), consumed["order"]) >= (_tick(event["t"]), event["order"])):
                    issues.append(_issue("stellar_resource_consumption_binding", "直伤需绑定同动作、区域、目标的先前实际消费或消费观察。", path+".resource_consumption"))
            ability_profile = event.get("ability_profile")
            parameters = ability_profile.get("parameters", {}) if isinstance(ability_profile, dict) else {}
            stellar_type = parameters.get("reaction_type") if isinstance(parameters, dict) else None
            profile_issues = validate_direct_stellar_damage_profile(ability_profile)
            for profile_issue in profile_issues:
                original_path = profile_issue.get("path", "ability_profile")
                suffix = (original_path[len("ability_profile"):]
                          if original_path.startswith("ability_profile")
                          else "."+original_path)
                issues.append(_issue(
                    "direct_stellar_profile_"+profile_issue["code"],
                    profile_issue["message"], path+".ability_profile"+suffix,
                    **{key: copy.deepcopy(value)
                       for key, value in profile_issue.items()
                       if key not in {"code", "message", "path"}},
                ))
            if stellar_type not in enabled_replacements:
                issues.append(_issue(
                    "special_switch_disabled",
                    "能力直伤星烁事件需要开启对应反应的资格模型。", path,
                    reaction_type=stellar_type,
                ))
            profile_status = (ability_profile or {}).get("status") if isinstance(
                ability_profile, dict) else None
            if profile_status in ("confirmed", "assumed") and event.get("status") != profile_status:
                issues.append(_issue(
                    "direct_stellar_status_mismatch",
                    "星烁能力直伤事件与能力profile必须使用相同审计状态。",
                    path+".status", event_status=event.get("status"),
                    ability_profile_status=profile_status,
                ))
            binding = (ability_profile or {}).get("ability_binding") if isinstance(
                ability_profile, dict) else None
            actual = action_bindings.get(event.get("action_id"))
            if (not isinstance(binding, dict) or not isinstance(actual, dict)
                    or binding.get("character_id") != actual.get("character_id")
                    or binding.get("ability_id") != actual.get("ability_id")
                    or binding.get("variant_id") != actual.get("variant")
                    or type(actual.get("effective_level")) is not int
                    or binding.get("effective_level")
                    != actual.get("effective_level")):
                issues.append(_issue(
                    "direct_special_ability_binding_mismatch",
                    "星烁能力直伤profile没有绑定当前动作的角色与技能。", path,
                    ability_binding=copy.deepcopy(binding),
                    action_binding=copy.deepcopy(actual),
                ))
            elif REQUIRED_CAPABILITY_TAGS.get(stellar_type) not in tags_by_character.get(
                    binding["character_id"], set()):
                issues.append(_issue(
                    "direct_special_owner_capability_unproven",
                    "星烁能力直伤角色本人缺少对应反应的数据库能力标签。", path,
                    character_id=binding["character_id"],
                ))
        elif kind not in (
                "stellar_resource_operation", "lunar_bloom_consume", "lunar_bloom_reserve_grant", "direct_lunar_damage", "direct_stellar_damage"):
            issues.append(_issue(
                "special_operation_kind", "不支持的星月显式操作类型。",
                path+".kind", kind=kind,
            ))
        if event.get("status") == "pending":
            issues.append(_issue("special_operation_pending", "星月操作来源仍待核实。", path,
                                 severity="pending"))
    for event in resource_conditions:
        observed = events_by_id.get(event["stellar_resource"]["event_id"])
        if (not observed or observed.get("kind") != "stellar_resource_operation"
                or (_tick(observed["t"]), observed["order"]) >= (_tick(event["t"]), event["order"])
                or (event["repeat"]["mode"] != "once" and event["repeat"].get("cycles") != [0])):
            issues.append(_issue("stellar_resource_condition_binding_invalid",
                "资源条件需引用同一展开轮次中先执行的实际资源操作。", "mechanics_model.condition_events", event_id=event["id"]))
        elif event["stellar_resource"].get("measure", "amount") != "amount" and observed.get("operation") != "trial":
            issues.append(_issue("stellar_resource_condition_measure_invalid",
                "executed_units、consumed_units和created_units仅用于trial实际回执。", "mechanics_model.condition_events", event_id=event["id"]))
        if event["status"] == "pending":
            issues.append(_issue("stellar_resource_condition_pending", "资源条件来源仍待核实。",
                "mechanics_model.condition_events", severity="pending", event_id=event["id"]))
    for event in dew_conditions:
        consumed = events_by_id.get(event["dew_consumption"]["event_id"])
        if (consumed is None or consumed.get("kind") != "lunar_bloom_consume"
                or (_tick(consumed["t"]), consumed["order"]) >= (_tick(event["t"]), event["order"])
                or (event["repeat"]["mode"] != "once" and event["repeat"].get("cycles") != [0])):
            issues.append(_issue("lunar_bloom_condition_binding_invalid",
                "扣露条件需绑定当前展开轮次中先于条件执行的实际消费事件。",
                "mechanics_model.condition_events", event_id=event["id"]))
        if event["status"] == "pending":
            issues.append(_issue("lunar_bloom_condition_pending", "扣露条件来源仍待核实。",
                "mechanics_model.condition_events", severity="pending", event_id=event["id"]))
    return issues


def build_special_reaction_runtime(mechanics_model: dict, party_member_ids: list[str]) -> SpecialReactionRuntime | None:
    special = mechanics_model.get("special_reaction_model")
    if special is None:
        return None
    tags: dict[str, list[str]] = {cid: [] for cid in party_member_ids}
    for row in mechanics_model.get("special_reaction_party_capabilities", []):
        if row.get("status") in ("confirmed", "assumed") and row.get("character_id") in tags:
            tags[row["character_id"]] = list(verified_capability_tags(row))
    return SpecialReactionRuntime(
        special,
        party_member_ids=party_member_ids,
        party_capability_tags=tags,
    )
