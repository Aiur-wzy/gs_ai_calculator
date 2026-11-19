"""Finite input schema for native Stellar/Lunar reaction routing.

The numerical/profile semantics remain owned by :mod:`special_reactions`.
This module only exposes the bounded JSON surface that can be embedded in a
mechanics model without opening an arbitrary extension object.
"""
from __future__ import annotations

from genshin_engine.handler_binding_contract import (
    BINDING_CONTRACT,
    BINDING_FIELDS,
)
from genshin_engine.probabilistic_resource_contract import TRIAL_SCHEMA
from genshin_engine.rotation_schema import EVIDENCE, STRING, arr, obj
from genshin_engine.special_reaction_capabilities import (
    SPECIAL_CAPABILITY_CATALOG_ID,
    SPECIAL_CAPABILITY_CATALOG_REVISION,
    SPECIAL_CAPABILITY_CATALOG_TRACK,
    SPECIAL_CAPABILITY_SNAPSHOT_MATCH,
    SPECIAL_CAPABILITY_VERSION_LINE,
    special_reaction_capability_catalog,
)
from genshin_engine.special_reaction_contracts import (
    DIRECT_ABILITY_BINDING_KEYS,
    DIRECT_LUNAR_ELEMENTS,
    DIRECT_LUNAR_SCALING_STATS,
    DIRECT_STELLAR_SCALING_STATS,
    REQUIRED_CAPABILITY_TAGS,
    SPECIAL_REACTION_SCHEMA_VERSION,
    SPECIAL_REACTION_TYPES,
)

QUALIFICATION_SCHEMA = obj({
    "status": {"enum": ["confirmed", "assumed", "pending"]},
    "provider_ids": {**arr(STRING), "minItems": 1, "maxItems": 4, "uniqueItems": True},
    "capability_tag": {"enum": sorted(REQUIRED_CAPABILITY_TAGS.values())},
    "source": STRING,
}, ["status", "provider_ids", "capability_tag", "source"])

# A disabled switch is intentionally allowed to retain an incomplete draft.
# Enabled entries receive their exact profile-field validation from
# validate_special_reaction_model; keeping that authority in one place avoids
# two schemas drifting when a reviewed profile is revised.
REPLACEMENT_SCHEMA = obj({
    "enabled": {"type": "boolean"},
    "qualification": {"type": "object"},
    "mechanics": {"type": "object"},
}, ["enabled"])

_REPLACEMENTS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {reaction_type: REPLACEMENT_SCHEMA for reaction_type in SPECIAL_REACTION_TYPES},
}
_BINDING_PROPERTIES = {
    "schema_version": {"type": "integer", "minimum": 1},
    "contract_id": STRING, "family": {"enum": list(SPECIAL_REACTION_TYPES)},
    "contract_version": {"type": "integer", "minimum": 1},
    "implementation_version": {"type": "integer", "minimum": 1},
    "profile_schema_version": {"type": "integer", "minimum": 1},
    "capabilities": {**arr(STRING), "minItems": 1, "maxItems": 32, "uniqueItems": True},
    "handler_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "registry_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
}
RESOLVED_HANDLER_BINDING_SCHEMA = obj(_BINDING_PROPERTIES, sorted(BINDING_FIELDS))
SPECIAL_REACTION_MODEL_SCHEMA = {"oneOf": [obj({
    "schema_version": {"const": 1},
    "replacements": _REPLACEMENTS_SCHEMA,
}, ["schema_version", "replacements"]), obj({
    "schema_version": {"const": SPECIAL_REACTION_SCHEMA_VERSION},
    "replacements": _REPLACEMENTS_SCHEMA,
    "handler_bindings": {
        "type": "object", "additionalProperties": False,
        "properties": {family: RESOLVED_HANDLER_BINDING_SCHEMA for family in SPECIAL_REACTION_TYPES},
    },
}, ["schema_version", "replacements", "handler_bindings"])]}

PARTY_CAPABILITY_SCHEMA = obj({
    "character_id": STRING,
    "db_character_id": {"type": "integer", "minimum": 1},
    "skill_depot_id": {"type": "integer", "minimum": 1},
    "capability_tags": {
        **arr({"enum": sorted(REQUIRED_CAPABILITY_TAGS.values())}),
        "minItems": 1,
        "maxItems": len(SPECIAL_REACTION_TYPES),
        "uniqueItems": True,
    },
    "version_line": {"const": SPECIAL_CAPABILITY_VERSION_LINE},
    "catalog_id": {"const": SPECIAL_CAPABILITY_CATALOG_ID},
    "catalog_revision": {"enum": [5, SPECIAL_CAPABILITY_CATALOG_REVISION]},
    "catalog_content_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "catalog_track": {"const": SPECIAL_CAPABILITY_CATALOG_TRACK},
    "catalog_source_upstream_commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
    "equipment_version_line": {
        "type": ["string", "null"], "enum": ["release", "beta", None],
    },
    "equipment_upstream_commit": {
        "anyOf": [
            {"type": "string", "pattern": "^[0-9a-f]{40}$"},
            {"type": "null"},
        ],
    },
    "projection_reason_code": {"enum": [
        SPECIAL_CAPABILITY_SNAPSHOT_MATCH,
        "equipment_data_version_missing",
        "equipment_version_line_mismatch",
        "equipment_upstream_commit_mismatch",
    ]},
    "catalog_entry_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "runtime_profile_id": STRING,
    **EVIDENCE,
}, [
    "character_id", "db_character_id", "skill_depot_id", "capability_tags", "version_line",
    "catalog_id", "catalog_revision", "catalog_content_hash", "status", "source",
    "runtime_profile_id", "catalog_entry_fingerprint", "catalog_track",
    "catalog_source_upstream_commit", "equipment_version_line",
    "equipment_upstream_commit", "projection_reason_code",
])

# Lunar Bloom Dew consumption is a resource operation; it never implies a
# Lunar damage event.
LUNAR_BLOOM_CONSUME_EVENT_SCHEMA = obj({
    "id": STRING,
    "kind": {"const": "lunar_bloom_consume"},
    "action_id": STRING,
    "target_id": STRING,
    "area_id": STRING,
    "t": {"type": "number", "minimum": 0},
    "order": {"type": "integer"},
    "dew": {"type": "integer", "minimum": 1, "maximum": 3},
    "spend_policy": {"enum": ["all_or_nothing", "up_to_available"]},
    "reserve_pool_ids": {**arr(STRING), "minItems": 1, "maxItems": 16, "uniqueItems": True},
    **EVIDENCE,
}, ["id", "kind", "action_id", "target_id", "area_id", "t", "order", "dew", "status", "source"])

LUNAR_BLOOM_RESERVE_GRANT_SCHEMA = obj({
    "id": STRING, "kind": {"const": "lunar_bloom_reserve_grant"},
    "action_id": STRING, "target_id": STRING, "area_id": STRING,
    "t": {"type": "number", "minimum": 0}, "order": {"type": "integer"},
    "pool_id": {"type": "string", "minLength": 1, "maxLength": 128},
    "amount": {"type": "integer", "minimum": 1, "maximum": 100},
    "capacity": {"type": "integer", "minimum": 1, "maximum": 100},
    "duration_seconds": {"type": "number", "minimum": 0.000001, "maximum": 1_000_000_000},
    **EVIDENCE,
}, ["id", "kind", "action_id", "target_id", "area_id", "t", "order", "pool_id", "amount",
    "capacity", "duration_seconds", "status", "source"])

DIRECT_LUNAR_DAMAGE_PARAMETERS_SCHEMA = obj({
    "reaction_type": {"enum": sorted(DIRECT_LUNAR_ELEMENTS)},
    "element": {"enum": sorted(set(DIRECT_LUNAR_ELEMENTS.values()))},
    "multiplier": {"type": "number", "exclusiveMinimum": 0},
    "scaling_stat": {"enum": sorted(DIRECT_LUNAR_SCALING_STATS)},
    "count": {"type": "integer", "minimum": 1, "maximum": 100},
    "component": STRING,
}, ["reaction_type", "element", "multiplier", "scaling_stat", "count", "component"])
DIRECT_LUNAR_DAMAGE_PARAMETERS_SCHEMA["allOf"] = [
    {
        "if": {"properties": {"reaction_type": {"const": reaction_type}}},
        "then": {"properties": {"element": {"const": element}}},
    }
    for reaction_type, element in DIRECT_LUNAR_ELEMENTS.items()
]

DIRECT_ABILITY_BINDING_SCHEMA = obj({
    "character_id": STRING,
    "ability_id": STRING,
    "component_id": STRING,
    "variant_id": STRING,
    "effective_level": {"type": "integer", "minimum": 1, "maximum": 20},
    "database_fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
}, sorted(DIRECT_ABILITY_BINDING_KEYS))

DIRECT_LUNAR_DAMAGE_PROFILE_SCHEMA = obj({
    "profile_id": STRING,
    "profile_revision": {"type": "integer", "minimum": 1},
    "profile_content_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "authority": {"enum": ["reviewed_database_projection", "explicit_assumption"]},
    "status": {"enum": ["confirmed", "assumed"]},
    "source": STRING,
    "source_refs": {
        **arr({"type": "object", "minProperties": 1}),
        "minItems": 1,
        "maxItems": 32,
    },
    "parameters": DIRECT_LUNAR_DAMAGE_PARAMETERS_SCHEMA,
    "ability_binding": DIRECT_ABILITY_BINDING_SCHEMA,
}, [
    "profile_id", "profile_revision", "profile_content_hash", "authority",
    "status", "source", "source_refs", "parameters",
])
DIRECT_LUNAR_DAMAGE_PROFILE_SCHEMA["$comment"] = (
    "ability_binding仅为兼容旧v1迁移诊断而保持结构可选；"
    "integration/runtime/projector执行统一要求v2绑定。"
)

DIRECT_STELLAR_DAMAGE_PARAMETERS_SCHEMA = obj({
    "reaction_type": {"enum": ["stellar_superconduct", "stellar_swirl"]},
    "element": {"enum": ["anemo", "cryo", "electro"]},
    "multiplier": {"type": "number", "exclusiveMinimum": 0},
    "scaling_stat": {"enum": sorted(DIRECT_STELLAR_SCALING_STATS)},
    "count": {"type": "integer", "minimum": 1, "maximum": 100},
    "component": STRING,
}, ["reaction_type", "element", "multiplier", "scaling_stat", "count", "component"])

DIRECT_STELLAR_DAMAGE_PROFILE_SCHEMA = obj({
    "profile_id": STRING,
    "profile_revision": {"type": "integer", "minimum": 1},
    "profile_content_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "authority": {"enum": ["reviewed_database_projection", "explicit_assumption"]},
    "status": {"enum": ["confirmed", "assumed"]},
    "source": STRING,
    "source_refs": {
        **arr({"type": "object", "minProperties": 1}),
        "minItems": 1,
        "maxItems": 32,
    },
    "parameters": DIRECT_STELLAR_DAMAGE_PARAMETERS_SCHEMA,
    "ability_binding": DIRECT_ABILITY_BINDING_SCHEMA,
}, [
    "profile_id", "profile_revision", "profile_content_hash", "authority",
    "status", "source", "source_refs", "parameters", "ability_binding",
])

# Direct Lunar damage is never inferred from merely creating a reaction or
# consuming Dew.  A reviewed ability projection or explicit assumption must
# own the exact type, element, multiplier, scaling stat, count and component.
DIRECT_LUNAR_DAMAGE_EVENT_SCHEMA = obj({
    "id": STRING,
    "kind": {"const": "direct_lunar_damage"},
    "action_id": STRING,
    "target_id": STRING,
    "area_id": STRING,
    "t": {"type": "number", "minimum": 0},
    "order": {"type": "integer"},
    "ability_profile": DIRECT_LUNAR_DAMAGE_PROFILE_SCHEMA,
    "dew_consumption": obj({
        "event_id": STRING,
        "mode": {"enum": ["require", "scale_multiplier"]},
    }, ["event_id", "mode"]),
    "bloom_core_absorption": obj({
        "event_id": STRING,
        "multiplier_bonus_per_core": {"type": "number", "minimum": 0, "maximum": 1000},
    }, ["event_id", "multiplier_bonus_per_core"]),
    **EVIDENCE,
}, [
    "id", "kind", "action_id", "target_id", "area_id", "t", "order",
    "ability_profile", "status", "source",
])

STELLAR_RESOURCE_OPERATION_SCHEMA = obj({
    "id": STRING, "kind": {"const": "stellar_resource_operation"},
    "action_id": STRING, "target_id": STRING, "area_id": STRING,
    "t": {"type": "number", "minimum": 0}, "order": {"type": "integer"},
    "operation": {"enum": ["grant", "consume", "set_mode", "end_mode", "observe", "trial"]},
    "trial": TRIAL_SCHEMA, "outcome": {"type": "boolean"},
    "probability_mode": {"enum": ["expected", "custom"]},
    "amount": {"type": "integer", "minimum": 1, "maximum": 100},
    "mode": {"enum": ["overflow_consume", "extended_generation"]},
    "duration_seconds": {"type": "number", "exclusiveMinimum": 0},
    "counter": {"enum": ["inventory", "created", "consumed", "overflow_consumed", "released", "expired"]},
    "claim_id": STRING, "divisor": {"type": "integer", "minimum": 1},
    **EVIDENCE,
}, ["id", "kind", "action_id", "target_id", "area_id", "t", "order", "operation", "status", "source"])

DIRECT_STELLAR_DAMAGE_EVENT_SCHEMA = obj({
    "id": STRING,
    "kind": {"const": "direct_stellar_damage"},
    "action_id": STRING,
    "target_id": STRING,
    "area_id": STRING,
    "t": {"type": "number", "minimum": 0},
    "order": {"type": "integer"},
    "ability_profile": DIRECT_STELLAR_DAMAGE_PROFILE_SCHEMA,
    "resource_consumption": obj({"event_id": STRING, "mode": {"enum": ["require", "scale_multiplier"]}}, ["event_id", "mode"]),
    **EVIDENCE,
}, [
    "id", "kind", "action_id", "target_id", "area_id", "t", "order",
    "ability_profile", "status", "source",
])

SPECIAL_REACTION_EVENT_SCHEMA = {
    "oneOf": [
        STELLAR_RESOURCE_OPERATION_SCHEMA,
        LUNAR_BLOOM_CONSUME_EVENT_SCHEMA,
        LUNAR_BLOOM_RESERVE_GRANT_SCHEMA,
        DIRECT_LUNAR_DAMAGE_EVENT_SCHEMA,
        DIRECT_STELLAR_DAMAGE_EVENT_SCHEMA,
    ],
}


def special_reaction_input_schemas() -> dict:
    """Authoritative static input views, independent of implementation ABI."""
    return {
        "model": SPECIAL_REACTION_MODEL_SCHEMA,
        "party_capability": PARTY_CAPABILITY_SCHEMA,
        "event": SPECIAL_REACTION_EVENT_SCHEMA,
        "direct_lunar_damage_profile": DIRECT_LUNAR_DAMAGE_PROFILE_SCHEMA,
        "direct_stellar_damage_profile": DIRECT_STELLAR_DAMAGE_PROFILE_SCHEMA,
    }


def special_reaction_input_capabilities() -> dict:
    from genshin_engine.special_handler_registry import (
        special_handler_registry_capabilities,
    )

    catalog = special_reaction_capability_catalog()
    return {
        "model_field": "special_reaction_model",
        "party_capability_field": "special_reaction_party_capabilities",
        "operation_field": "special_reaction_events",
        "manual_enable_required": True,
        "default": "all_replacements_disabled",
        "supported_replacements": list(SPECIAL_REACTION_TYPES),
        "model_versions": {"legacy_fixed_v1": 1, "explicit_handler_bindings": 2},
        "handler_bindings": {
            "field": "special_reaction_model.handler_bindings",
            "contract_id": BINDING_CONTRACT,
            "authority": "generic_engine_implementation_only; does not execute database knowledge",
            "selection": "copy exact family/version/capabilities/fingerprints; unknown binding stays pending when consumed",
            "registry": special_handler_registry_capabilities(),
        },
        "required_capability_tags": dict(REQUIRED_CAPABILITY_TAGS),
        "capability_catalog": {
            "catalog_id": catalog["collection_id"],
            "revision": catalog["revision"],
            "content_hash": catalog["content_hash"],
            "version_line": catalog["version_line"],
            "catalog_track": SPECIAL_CAPABILITY_CATALOG_TRACK,
            "source_upstream_commit": catalog["source_upstream_commit"],
            "source_target_version": catalog["source_target_version"],
            "entry_count": len(catalog["entries"]),
        },
        "operations": [
            "stellar_resource_operation", "lunar_bloom_consume", "lunar_bloom_reserve_grant", "direct_lunar_damage", "direct_stellar_damage"],
        "stellar_periodic_resources": {
            "configuration": "stellar_superconduct.mechanics.periodic_resource必须显式声明owner_id、area_id、interval_seconds、lifetime_seconds、capacity、window_seconds、extended_window_seconds，以及refresh_mode=extend_window_preserve_cadence；作为assumed覆盖保留来源。",
            "operations": "stellar_resource_operation支持grant、consume、set_mode、end_mode、observe、trial，绑定实际来源动作与所有者。set_mode指定overflow_consume或extended_generation及duration_seconds；普通consume不足报错并回滚。trial声明cost、base_grant、success_grant、success_waives_cost、chance；未指定outcome时默认expected期望次数，不枚举、不随机。用户指定情景使用probability_mode=custom及布尔outcome；旧方案已有outcome保留原情景。trial先检查完整成本，库存不足记录blocked，不用本次未来返还补成本。返还仍计消费，免消耗不计消费。",
            "observations": "observe可读inventory，或通过counter和独立claim_id读取created/consumed/overflow_consumed/released/expired累计计数的新增量；divisor允许读取已跨越的阈值次数，不重复领取。",
            "damage": "direct_stellar_damage.resource_consumption引用同动作先前消费或消费观察；require要求实际消费，scale_multiplier按实际数量乘能力倍率。获得、库存或过期不算消费。",
            "resource_benefits": "规则trigger可监听resource_change/post，明确resource_operations和可选resource_modes。resource_every按实际整数变化逐个判断；scope=global读取账本累计，scope=matching只累计满足该规则其他筛选条件的变化。创建/消费可触发已有Buff及具有source_ref所有权的回能规则，沿用共享冷却。",
            "resource_damage": "rules[].resource_damage按真实资源变化派生星烁能力直伤；填写id、已执行的来源action_id、target_id、area_id和完整ability_profile，不预填伤害时刻。与规则owner/资源区域/当前能力资格一致，同一动作组件不得另行手填重复伤害；同一规则各组件绑定同一来源动作，timing.delay须为0。",
            "condition": "condition_events.stellar_resource引用先执行的资源回执，amount为精确比较值；measure默认amount，trial还支持executed_units、consumed_units、created_units。免消耗时executed_units可为正而consumed_units为零。",
            "probability": "默认期望次数=符合条件次数×chance，保留小数库存与消费；离散Buff触发按累计期望次数向下取整并记录expected_discrete_count，额外动作由Agent安排。用户要特定情景由Agent询问确认，再提交custom输入。引擎不枚举情景、抽样或输出情景成功率；容量、阈值、冷却及后续连段按确定性期望数量近似处理，结果不代表全场随机机制精确期望或实战保证。",
        },
        "lunar_bloom_reserves": {
            "generation": "显式声明实际来源动作、时刻、数量、容量与有效期；同池新一次交付增加余额至容量并刷新有效期。",
            "consumption": "lunar_bloom_consume 可指定 reserve_pool_ids，先扣普通草露，再按列表顺序扣替代池。默认余额不足不扣除，spend_policy=up_to_available 时最多扣到所声明的 dew 上限。",
            "scope": "各池独立余额与到期；不推断来源条件，不把替代资源当成普通草露产出。",
            "direct_damage": "直伤月绽放可用 dew_consumption.event_id 绑定同动作的先前消费。require 仅确认已消费；scale_multiplier 将已核实的单位倍率乘实际扣量。bloom_core_absorption 绑定同动作的先前吸收，按 1 + 实际吸收数 × multiplier_bonus_per_core 调整基础倍率。",
            "condition": "用 condition_events 的 dew_consumption={event_id,amount} 引用实际扣除，为原有机制规则或持续状态提供对应耗露档位的条件。",
        },
        "schemas": special_reaction_input_schemas(),
    }
