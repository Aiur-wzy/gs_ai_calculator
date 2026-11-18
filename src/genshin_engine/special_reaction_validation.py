"""Pure model admission and capability descriptions, without event execution."""
from __future__ import annotations

import copy
from typing import Any

from genshin_engine.constants import LUNAR_FACTOR
from genshin_engine.special_handler_registry import (
    special_handler_registry_capabilities,
    validate_resolved_handler_binding,
)
from genshin_engine.special_reaction_contracts import (
    _MECHANICS_KEYS,
    _PROFILE_META_KEYS,
    _QUALIFICATION_KEYS,
    DIRECT_LUNAR_ELEMENTS,
    DIRECT_STELLAR_ELEMENT_BY_REACTION,
    LUNAR_STELLAR_WEIGHTS,
    REPLACEMENT_CONTRACTS,
    REQUIRED_CAPABILITY_TAGS,
    SPECIAL_REACTION_SCHEMA_VERSION,
    SPECIAL_REACTION_TYPES,
    _finite_number,
    _issue,
    _seconds_to_ticks,
)
from genshin_engine.special_reaction_profiles import (
    REVIEWED_SPECIAL_REACTION_PROFILES,
    load_reviewed_direct_special_damage_profiles,
)


def validate_special_reaction_model(model: Any) -> list[dict]:
    """Validate the explicit replacement model without treating pending as ready."""
    issues: list[dict] = []
    root = "special_reaction_model"
    if not isinstance(model, dict):
        return [_issue("schema", "星月反应模型必须是对象。", root)]
    version = model.get("schema_version")
    extra = set(model) - ({"schema_version", "replacements", "handler_bindings"}
                          if version == 2 else {"schema_version", "replacements"})
    if extra:
        issues.append(_issue("unknown_field", "模型包含未知字段。", root,
                             fields=sorted(extra)))
    if version not in (1, 2):
        issues.append(_issue("schema_version", "仅支持星月反应模型版本1或2。", root+".schema_version"))
    bindings = model.get("handler_bindings")
    if version == 2 and not isinstance(bindings, dict):
        issues.append(_issue("special_handler_bindings_missing", "版本2必须声明精确引擎绑定。", root+".handler_bindings"))
    if isinstance(bindings, dict) and set(bindings) - set(SPECIAL_REACTION_TYPES):
        issues.append(_issue("special_handler_family_unknown", "引擎绑定含有未知反应族。", root+".handler_bindings"))
    replacements = model.get("replacements")
    if not isinstance(replacements, dict):
        issues.append(_issue("schema", "replacements必须是对象；省略某项即默认关闭。", root+".replacements"))
        return issues
    unknown = set(replacements) - set(SPECIAL_REACTION_TYPES)
    if unknown:
        issues.append(_issue("unknown_reaction", "包含不受支持的星月反应类型。",
                             root+".replacements", reactions=sorted(unknown)))
    for reaction_type in SPECIAL_REACTION_TYPES:
        if reaction_type not in replacements:
            continue
        path = f"{root}.replacements.{reaction_type}"
        row = replacements[reaction_type]
        if not isinstance(row, dict):
            issues.append(_issue("schema", "替换项必须是对象。", path))
            continue
        extra = set(row) - {"enabled", "qualification", "mechanics"}
        if extra:
            issues.append(_issue("unknown_field", "替换项包含未知字段。", path,
                                 fields=sorted(extra)))
        if type(row.get("enabled")) is not bool:
            issues.append(_issue("schema", "enabled必须显式为布尔值。", path+".enabled"))
            continue
        # Disabled entries are intentionally inert.  A draft may keep partial
        # material without making the calculation pending.
        if not row["enabled"]:
            continue
        if version == 2 and isinstance(bindings, dict):
            for issue in validate_resolved_handler_binding(bindings.get(reaction_type), family=reaction_type):
                issues.append(_issue(issue["code"], issue["message"], root+".handler_bindings."+reaction_type,
                                     **{key: value for key, value in issue.items() if key not in {"code", "message"}}))
        qualification = row.get("qualification")
        if not isinstance(qualification, dict):
            issues.append(_issue("missing_qualification", "开启替换必须声明资格。", path+".qualification"))
        else:
            extra = set(qualification) - _QUALIFICATION_KEYS
            if extra:
                issues.append(_issue("unknown_field", "资格声明包含未知字段。",
                                     path+".qualification", fields=sorted(extra)))
            if qualification.get("status") not in ("confirmed", "assumed", "pending"):
                issues.append(_issue("status", "资格状态必须为confirmed/assumed/pending。",
                                     path+".qualification.status"))
            providers = qualification.get("provider_ids")
            if (not isinstance(providers, list) or not providers
                    or any(not isinstance(value, str) or not value.strip() for value in providers)):
                issues.append(_issue("qualification_provider", "开启替换必须声明至少一个资格提供者ID。",
                                     path+".qualification.provider_ids"))
            elif len(providers) != len(set(providers)):
                issues.append(_issue("duplicate_provider", "资格提供者ID不得重复。",
                                     path+".qualification.provider_ids"))
            capability_tag = qualification.get("capability_tag")
            expected_tag = REQUIRED_CAPABILITY_TAGS[reaction_type]
            if capability_tag != expected_tag:
                issues.append(_issue(
                    "qualification_capability",
                    f"资格必须引用数据库能力标签{expected_tag!r}。",
                    path+".qualification.capability_tag",
                    expected=expected_tag,
                ))
            if not isinstance(qualification.get("source"), str) or not qualification["source"].strip():
                issues.append(_issue("qualification_source", "资格声明必须保留来源。",
                                     path+".qualification.source"))
        mechanics = row.get("mechanics")
        if not isinstance(mechanics, dict):
            issues.append(_issue("missing_mechanics", "开启替换必须声明机制档案。", path+".mechanics"))
            continue
        extra = set(mechanics) - _MECHANICS_KEYS[reaction_type]
        if extra:
            issues.append(_issue("unknown_field", "机制档案包含未知字段。",
                                 path+".mechanics", fields=sorted(extra)))
        if mechanics.get("status") not in ("confirmed", "assumed", "pending"):
            issues.append(_issue("status", "机制状态必须为confirmed/assumed/pending。",
                                 path+".mechanics.status"))
        if not isinstance(mechanics.get("source"), str) or not mechanics["source"].strip():
            issues.append(_issue("mechanics_source", "机制档案必须保留来源。",
                                 path+".mechanics.source"))
        reviewed = REVIEWED_SPECIAL_REACTION_PROFILES[reaction_type]
        for field in ("profile_id", "profile_revision", "profile_content_hash"):
            if mechanics.get(field) != reviewed[field]:
                issues.append(_issue(
                    "profile_identity", "机制档案必须引用内置受审profile。",
                    path+".mechanics."+field, expected=reviewed[field],
                ))
        configured_parameters = {
            name: mechanics.get(name)
            for name in _MECHANICS_KEYS[reaction_type] - _PROFILE_META_KEYS
        }
        differences = {
            name: {"reviewed": reviewed["parameters"].get(name), "configured": value}
            for name, value in configured_parameters.items()
            if value != reviewed["parameters"].get(name)
        }
        mechanics_status = mechanics.get("status")
        if mechanics_status == "confirmed":
            if not reviewed["confirmed_ready"]:
                issues.append(_issue(
                    "profile_not_confirmed_ready",
                    "该受审profile仍含未决参数，不能标为confirmed。",
                    path+".mechanics.status",
                ))
            if differences:
                issues.append(_issue(
                    "profile_override_status",
                    "confirmed档案不得覆盖受审profile参数；覆盖项只能标为assumed。",
                    path+".mechanics", differences=differences,
                ))
            if mechanics.get("override_reason") not in (None, ""):
                issues.append(_issue(
                    "profile_override_reason", "confirmed档案不得声明override_reason。",
                    path+".mechanics.override_reason",
                ))
        elif mechanics_status == "assumed":
            if (not isinstance(mechanics.get("override_reason"), str)
                    or not mechanics["override_reason"].strip()):
                issues.append(_issue(
                    "profile_override_reason", "assumed档案必须说明覆盖或不确定性的原因。",
                    path+".mechanics.override_reason",
                ))
        elif mechanics_status == "pending":
            # Pending profiles are inert at runtime.  They may carry partial
            # observations while evidence is being collected.
            continue
        if reaction_type == "stellar_superconduct":
            for name in ("initial_grant_layers", "settlement_bonus_layers"):
                if name in mechanics and (type(mechanics[name]) is not int or not 0 <= mechanics[name] <= 12):
                    issues.append(_issue("value", f"{name}必须是0至12的整数。", path+".mechanics."+name))
            for name in ("field_duration_seconds", "settlement_interval_seconds"):
                value = mechanics.get(name)
                if not _finite_number(value, positive=True):
                    issues.append(_issue("value", f"{name}必须为有限正数。",
                                         path+".mechanics."+name))
                else:
                    try:
                        _seconds_to_ticks(value, path+".mechanics."+name)
                    except ValueError as exc:
                        issues.append(_issue("time_precision", str(exc), path+".mechanics."+name))
            if mechanics.get("settlement_interval_seconds") != 4:
                issues.append(_issue("mechanics_contract", "星超导按4秒窗口截取并清空层数。",
                                     path+".mechanics.settlement_interval_seconds"))
            if mechanics.get("refresh_mode") != "refresh_without_window_reset":
                issues.append(_issue("refresh_mode", "重复星超导刷新领域但不重置4秒监听窗口。",
                                     path+".mechanics.refresh_mode"))
        elif reaction_type == "stellar_swirl":
            if "initial_vortex_count" in mechanics:
                value = mechanics["initial_vortex_count"]
                if type(value) is not int or not 1 <= value < 6:
                    issues.append(_issue("value", "初始风旋计数必须是1至5的整数。", path+".mechanics.initial_vortex_count"))
            if "initial_upgrade_cooldown_seconds" in mechanics:
                try:
                    _seconds_to_ticks(mechanics["initial_upgrade_cooldown_seconds"], "initial_upgrade_cooldown_seconds")
                except ValueError as exc:
                    issues.append(_issue("value", str(exc), path+".mechanics.initial_upgrade_cooldown_seconds"))
            fixed = {
                "upgrade_threshold": 3,
                "early_detonation_threshold": 6,
                "detonation_delay_seconds": 3,
                "ice_application_gu": 1,
                "creation_batch_cap_once": True,
                "existing_vortex_counts_each_reaction": True,
            }
            for name, expected in fixed.items():
                if mechanics.get(name) != expected:
                    issues.append(_issue("mechanics_contract", f"{name}必须为{expected!r}。",
                                         path+".mechanics."+name))
        elif reaction_type == "lunar_electro":
            for name in ("first_tick_delay_seconds", "tick_interval_seconds", "duration_seconds"):
                value = mechanics.get(name)
                if not _finite_number(value, positive=True):
                    issues.append(_issue("value", f"{name}必须为有限正数。",
                                         path+".mechanics."+name))
                else:
                    try:
                        _seconds_to_ticks(value, path+".mechanics."+name)
                    except ValueError as exc:
                        issues.append(_issue("time_precision", str(exc), path+".mechanics."+name))
            if (all(_finite_number(mechanics.get(name), positive=True) for name in
                    ("first_tick_delay_seconds", "duration_seconds"))
                    and mechanics["first_tick_delay_seconds"] > mechanics["duration_seconds"]):
                issues.append(_issue("schedule", "首次雷暴云攻击不能晚于持续时间。",
                                     path+".mechanics"))
            if mechanics.get("refresh_mode") != "extend_without_phase_reset":
                issues.append(_issue("refresh_mode", "重复月感电只延长区域雷暴云，不得重置既有目标的攻击相位。",
                                     path+".mechanics.refresh_mode"))
            if (mechanics.get("first_tick_delay_seconds") != .25
                    or mechanics.get("tick_interval_seconds") != 2
                    or mechanics.get("duration_seconds") != 6):
                issues.append(_issue("mechanics_contract", "月感电雷暴云在0.25秒首次攻击、同目标2秒攻击冷却、持续6秒。",
                                     path+".mechanics"))
            if mechanics.get("aura_consumption_gu") != .4:
                issues.append(_issue("mechanics_contract", "每次月感电跳数分别消耗0.4GU水底和雷底。",
                                     path+".mechanics.aura_consumption_gu"))
        elif reaction_type == "lunar_bloom":
            if mechanics.get("dew_interval_seconds") != 2.5:
                issues.append(_issue("mechanics_contract", "草露发生器每2.5秒填充1点。",
                                     path+".mechanics.dew_interval_seconds"))
            if mechanics.get("dew_cap") != 3:
                issues.append(_issue("mechanics_contract", "草露上限为3点。",
                                     path+".mechanics.dew_cap"))
            if mechanics.get("backlog_per_reaction") != 1:
                issues.append(_issue("mechanics_contract", "每次月绽放只增加1个待填充额度。",
                                     path+".mechanics.backlog_per_reaction"))
            if mechanics.get("same_parent_backlog_cap_once") is not True:
                issues.append(_issue("mechanics_contract", "同一父级AoE对待填充额度最多计1次。",
                                     path+".mechanics.same_parent_backlog_cap_once"))
            if mechanics.get("full_cap_queue_policy") not in (
                    "pending", "discard_new", "retain_active_fill_window"):
                issues.append(_issue(
                    "mechanics_contract",
                    "满3点草露后的填充队列策略必须为pending、discard_new或retain_active_fill_window。",
                    path+".mechanics.full_cap_queue_policy"))
        elif reaction_type == "lunar_crystallize":
            if mechanics.get("harmony_threshold") != 3:
                issues.append(_issue("mechanics_contract", "月笼谐奏按每3次月结晶触发。",
                                     path+".mechanics.harmony_threshold"))
            if mechanics.get("common_cooldown_seconds") != 0:
                issues.append(_issue("mechanics_contract", "月结晶不使用普通结晶的公共晶片冷却。",
                                     path+".mechanics.common_cooldown_seconds"))
            if mechanics.get("cages_per_reaction") != 3:
                issues.append(_issue("mechanics_contract", "每次月结晶生成3个月笼。",
                                     path+".mechanics.cages_per_reaction"))
            if mechanics.get("harmony_counter_cap") != 4:
                issues.append(_issue("mechanics_contract", "月笼谐奏计数溢出上限为4。",
                                     path+".mechanics.harmony_counter_cap"))
            value = mechanics.get("cage_duration_seconds")
            if not _finite_number(value, positive=True):
                issues.append(_issue("value", "cage_duration_seconds必须为有限正数。",
                                     path+".mechanics.cage_duration_seconds"))
            else:
                try:
                    _seconds_to_ticks(value, path+".mechanics.cage_duration_seconds")
                except ValueError as exc:
                    issues.append(_issue("time_precision", str(exc),
                                         path+".mechanics.cage_duration_seconds"))
                if value != 9:
                    issues.append(_issue("mechanics_contract", "月笼按9秒未参与谐奏攻击销毁。",
                                         path+".mechanics.cage_duration_seconds"))
            cooldown = mechanics.get("harmony_attack_cooldown_seconds")
            cooldown_scope = mechanics.get("harmony_attack_cooldown_scope")
            if cooldown is None or cooldown_scope is None:
                if mechanics_status != "pending":
                    issues.append(_issue(
                        "mechanics_pending",
                        "月笼谐奏攻击冷却的精确值与作用域未核实；运行必须显式提供assumed覆盖。",
                        path+".mechanics.harmony_attack_cooldown_seconds"))
            else:
                if not _finite_number(cooldown, positive=True):
                    issues.append(_issue(
                        "value", "harmony_attack_cooldown_seconds必须为有限正数。",
                        path+".mechanics.harmony_attack_cooldown_seconds"))
                else:
                    try:
                        _seconds_to_ticks(cooldown,
                                          path+".mechanics.harmony_attack_cooldown_seconds")
                    except ValueError as exc:
                        issues.append(_issue(
                            "time_precision", str(exc),
                            path+".mechanics.harmony_attack_cooldown_seconds"))
                if cooldown_scope not in ("area", "global", "per_cage"):
                    issues.append(_issue(
                        "value", "harmony_attack_cooldown_scope必须为area、global或per_cage。",
                        path+".mechanics.harmony_attack_cooldown_scope"))
    return issues


def special_reaction_capabilities() -> dict:
    """Self-description used by integration layers and Agent instructions."""
    return {
        "schema_version": SPECIAL_REACTION_SCHEMA_VERSION,
        "supported_model_versions": [1, 2],
        "handler_registry": special_handler_registry_capabilities(),
        "default": "all_replacements_disabled",
        "types": {
            name: {**copy.deepcopy(contract), "elements": sorted(contract["elements"])}
            for name, contract in REPLACEMENT_CONTRACTS.items()
        },
        "qualification": {
            "manual_enable_required": True,
            "provider_ids_required": True,
            "party_binding_required": True,
            "database_capability_tag_required": True,
            "required_capability_tags": copy.deepcopy(REQUIRED_CAPABILITY_TAGS),
            "character_name_dispatch": False,
            "statuses": ["confirmed", "assumed", "pending"],
        },
        "profiles": copy.deepcopy(REVIEWED_SPECIAL_REACTION_PROFILES),
        "routing": {
            "input": "uncommitted_ordinary_reaction_proposal",
            "commit_after_route_selection": True,
            "ordinary_and_replacement_side_effects_are_mutually_exclusive": True,
            "proposal_required_fields": [
                "id", "type", "commit_state=proposal", "aura_element",
                "trigger_element", "target_id", "parent_event_id",
            ],
            "proposal_source_fields": [
                "owner", "contributor_ids", "area_id",
                "application_packet_size",
            ],
            "gauge_commit_policy": "elemental_runtime_commits_selected_initial_reaction_consumption_exactly_once",
        },
        "gauge": {
            "initial_reaction": "use_uncommitted_elemental_proposal; special runtime does not mutate initial aura",
            "stellar_superconduct": "no extra GU; suppress ordinary reaction damage/debuff and establish or refresh area field",
            "stellar_swirl": "suppress ordinary Swirl damage/propagation; detonation requests 1U Cryo after damage",
            "lunar_electro": "each admitted cloud tick consumes 0.4GU Hydro and 0.4GU Electro from live coexisting aura",
            "lunar_bloom": "no extra GU beyond initial proposal; retain ordinary Dendro Core creation",
            "lunar_crystallize": (
                "replace only the Hydro-Crystallize proposal, with no extra GU; "
                "suppress its ordinary shard and GCD.  On a Hydro+Electro dual aura, "
                "the elemental runtime may also submit and commit an Electro ordinary "
                "Crystallize proposal from the same Geo application according to the "
                "remaining GU and ordinary shard GCD"
            ),
        },
        "damage": {
            "formula": "existing_lunar_stellar_damage",
            "slot_weights": list(LUNAR_STELLAR_WEIGHTS),
            "reaction_weighting_policies": {
                "lunar_electro": "independent_crit_then_global_actual_damage_desc",
                "lunar_crystallize": "independent_crit_then_global_actual_damage_desc",
                "stellar_swirl_anemo": {
                    "policy": "immediate_anemo_trigger_fixed_first",
                    "remaining_slots": "independent_crit_then_actual_damage_desc",
                    "missing_or_invalid_role_provenance": "pending",
                },
                "stellar_swirl_cryo": {
                    "reaction_types": [
                        "stellar_swirl_cryo_lv2", "stellar_swirl_cryo_lv3"],
                    "policy": "vortex_cryo_best_then_anemo_best",
                    "remaining_slots": "independent_crit_then_actual_damage_desc",
                    "missing_or_invalid_role_provenance": "pending",
                },
            },
            "sample_time": "generated_damage_event_time",
            "ordinary_dmg_bonus_and_defense": False,
            "direct_lunar_damage": {
                "supported_reaction_types": sorted(DIRECT_LUNAR_ELEMENTS),
                "elements_by_reaction_type": copy.deepcopy(DIRECT_LUNAR_ELEMENTS),
                "system_factors_by_reaction_type": {
                    name: LUNAR_FACTOR[name] for name in DIRECT_LUNAR_ELEMENTS
                },
                "reaction_creation_does_not_imply_direct_damage": True,
                "dew_consumption_emits_damage": False,
                "request_method": "request_direct_lunar_damage",
                "lunar_bloom_compatibility_wrapper": "request_direct_lunar_bloom_damage",
                "reaction_base": False,
                "single_owner": True,
                "contributor_weighting": False,
                "requires_content_hashed_ability_profile": True,
                "legacy_v1_profile_supported": False,
                "legacy_v1_profile_policy": "migration_pending_no_execution",
                "v2_ability_binding_supported": True,
                "requires_v2_ability_binding": True,
                "confirmed_requires_database_catalog_match": True,
                "reviewed_profile_count": len(
                    load_reviewed_direct_special_damage_profiles()),
            },
            "direct_stellar_damage": {
                "supported_reaction_types": sorted(DIRECT_STELLAR_ELEMENT_BY_REACTION),
                "supported_elements": sorted(set().union(*DIRECT_STELLAR_ELEMENT_BY_REACTION.values())),
                "elements_by_reaction": {key:sorted(values) for key,values in DIRECT_STELLAR_ELEMENT_BY_REACTION.items()},
                "factor_policy": "event_time_last_completed_four_second_window",
                "factor_policy_by_reaction": {"stellar_superconduct":"event_time_last_completed_four_second_window",
                    "stellar_swirl":"ability_coefficient_without_vortex_layers"},
                "caller_supplied_layers_or_factor": False,
                "reaction_creation_does_not_imply_direct_damage": True,
                "request_method": "request_direct_stellar_damage",
                "reaction_base": False,
                "single_owner": True,
                "contributor_weighting": False,
                "requires_content_hashed_ability_profile": True,
                "requires_v2_ability_binding": True,
                "confirmed_requires_database_catalog_match": True,
                "reviewed_profile_count": len(
                    load_reviewed_direct_special_damage_profiles()),
            },
        },
        "boundaries": {
            "stellar_superconduct": "area Polestar Field; optional initial_grant_layers grants only the first window buff, not application counts; settlement_bonus_layers adds at completed settlement capped at 12; refresh does not regrant",
            "stellar_swirl": "area packet runtime; optional initial_vortex_count upgrades fresh vortices under a party-shared initial_upgrade_cooldown_seconds; normal reaction counts, expiry and detonation remain active; direct ability damage uses its own coefficient without vortex level",
            "lunar_electro": (
                "one replaceable cloud per area; refresh extends six-second lifetime without "
                "resetting existing attack phases; first strike 0.25s, same-target 2s cooldown, "
                "each tick requires live Hydro+Electro and consumes 0.4GU each. The current "
                "ElementalRuntime executes one bound target; area entity scanning and dynamic "
                "multi-target admission require a world coordinator and remain pending"
            ),
            "lunar_bloom": (
                "creates an ordinary core, adds one party fill backlog per parent, "
                "and fills one Dew per 2.5s without parallel acceleration; the reviewed "
                "profile leaves full-cap queue behavior pending; reaction and Dew consumption "
                "emit no Lunar damage, so direct damage requires a hashed ability profile"
            ),
            "lunar_crystallize": (
                "party Harmony counter capped at four plus area Moondrift objects; a newly "
                "created set cannot immediately spend historical overflow; eligible Harmony "
                "dispatches up to three nearby attacks and refreshes only attacking objects. "
                "Exact attack cooldown and scope remain pending"
            ),
        },
    }
