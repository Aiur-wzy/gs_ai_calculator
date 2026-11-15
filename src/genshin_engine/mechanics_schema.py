"""D20 的有限声明契约。游戏事实由调用者提供，不执行自然语言或任意代码。"""
from __future__ import annotations

import copy

from genshin_engine.attribute_budget import OPTIONS_SCHEMA
from genshin_engine.attribute_callback_schema import (
    attribute_callback_capabilities,
    attribute_callback_schema,
    attribute_multistat_callback_capabilities,
)
from genshin_engine.attribute_contract import attribute_model_schema
from genshin_engine.attribute_schema import attribute_ledger_capabilities
from genshin_engine.constants import ENEMY_ZONES
from genshin_engine.elemental_schema import (
    APPLICATION,
    CONTACT,
    ELEMENTAL_SCHEMA,
    elemental_capabilities,
)
from genshin_engine.particle_quantities import PARTICLE_COUNT, PARTICLE_MODE_FIELDS
from genshin_engine.reaction_event_capabilities import reaction_listener_capabilities
from genshin_engine.registry import BENEFIT_TYPES
from genshin_engine.resource_counter_schema import (
    COUNTER_LIMITS,
    RESOURCE_COUNTER_SCHEMA,
)
from genshin_engine.resource_counter_view_schema import RESOURCE_COUNTER_VIEW_SCHEMA
from genshin_engine.resource_reaction_program import REACTION_ELEMENTS, REACTION_TYPES
from genshin_engine.resource_source_identity import TRIGGER_SOURCE_SCHEMA
from genshin_engine.resource_threshold_schema import RESOURCE_THRESHOLD_SCHEMA
from genshin_engine.resource_threshold_trigger_schema import (
    RESOURCE_THRESHOLD_TRIGGER_SCHEMA,
    RESOURCE_THRESHOLD_TRIGGER_V2_SCHEMA,
    RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA,
    RESOURCE_THRESHOLD_TRIGGER_V4_SCHEMA,
    THRESHOLD_TRIGGER,
)
from genshin_engine.resource_threshold_v2_schema import RESOURCE_THRESHOLD_V2_SCHEMA
from genshin_engine.rotation_schema import ELEMENTS, EVIDENCE, REPEAT, STRING, arr, obj
from genshin_engine.runtime_event_match import RUNTIME_EVENT_REF_V2_SCHEMA
from genshin_engine.shared_pool_schema import (
    SHARED_POOLS_SCHEMA,
    shared_pool_capabilities,
)
from genshin_engine.shared_pool_v2_schema import (
    SCALED_POOLS_SCHEMA,
    scaled_shared_pool_capabilities,
)
from genshin_engine.snapshot_capture import (
    CAPTURE_SCHEMA,
    snapshot_capture_capabilities,
)
from genshin_engine.special_damage_echo import SCHEMA as SPECIAL_DAMAGE_ECHO_SCHEMA
from genshin_engine.special_damage_echo import (
    capabilities as special_damage_echo_capabilities,
)
from genshin_engine.special_reaction_schema import (
    PARTY_CAPABILITY_SCHEMA,
    SPECIAL_REACTION_EVENT_SCHEMA,
    SPECIAL_REACTION_MODEL_SCHEMA,
    special_reaction_input_capabilities,
)
from genshin_engine.state_runtime_mechanics_schema import (
    state_runtime_mechanics_capabilities,
)
from genshin_engine.state_runtime_schema import (
    STATE_RUNTIME_SCHEMA,
    STATE_RUNTIME_V2_SCHEMA,
)
from genshin_engine.target_state import (
    GENERAL_TARGET_PREDICATE_SCHEMA,
    GENERAL_TARGET_PROFILE,
    TARGET_PREDICATE_SCHEMA,
    TARGET_STATE_PROFILE,
    target_state_capabilities,
)
from genshin_engine.target_status import (
    TARGET_STATUS_EVENT_SCHEMA,
    TARGET_STATUS_RULE_SCHEMA,
)

NUM = {"type": "number"}
NONNEG = {"type": "number", "minimum": 0}
INT = {"type": "integer"}
ACTION_ADMISSION_FILTER = obj({
    "contract": {"const": "action-admission-execution-v1"},
}, ["contract"])
FILTER = obj({
    "event": {"enum": ["action_start", "action_end", "switch", "hit", "condition", "elemental_reaction", "reaction_damage"]},
    "phase": {"enum": ["pre", "post"]},
    "action_ids": arr(STRING), "event_ids": arr(STRING), "characters": arr(STRING),
    "event_refs": arr(RUNTIME_EVENT_REF_V2_SCHEMA),
    "action_type_ids": arr(STRING), "damage_type_ids": arr(STRING),
    "elements": arr({"enum": [*ELEMENTS, "physical"]}), "condition": STRING,
    "reaction_types": arr({"enum": [
        "vaporize", "melt", "swirl", "freeze", "crystallize",
        "stellar_superconduct", "stellar_swirl",
        "lunar_electro", "lunar_bloom", "lunar_crystallize",
    ]}),
    "reaction_elements": arr({"enum": [*ELEMENTS]}),
    "owner_presence": {"enum": ["frontline", "any"]},
    "action_admission": ACTION_ADMISSION_FILTER,
}, ["event", "phase"])
EXPR = {"oneOf": [NUM, obj({
    "kind": {"const": "stat"}, "subject": {"enum": ["owner", "recipient"]},
    "stat": {"enum": ["atk", "hp", "def", "em", "base_atk", "base_hp", "base_def", "energy_recharge"]},
    "factor": NUM, "add": NUM, "min": NUM, "max": NUM,
    "sample": {"enum": ["current", "trigger", "event"]}, "event_id": STRING,
    "basis": {"enum": ["total", "nonextra", "base"]},
}, ["kind", "subject", "stat", "factor", "sample"])]}
EXPR["oneOf"].append(obj({
    "kind": {"const": "counter_view"}, "view_id": STRING,
    "factor": NUM, "add": NUM, "min": NUM, "max": NUM,
    "sample": {"enum": ["current", "self_frame"]},
}, ["kind", "view_id", "factor", "sample"]))
EXPR["oneOf"][-1]["x-label"] = "当前累计资源读数"
for _field, _label in {"kind": "数值类型", "view_id": "资源读数标识", "factor": "换算系数",
                        "add": "额外加算", "min": "收益下限", "max": "收益上限", "sample": "取样时点"}.items():
    EXPR["oneOf"][-1]["properties"][_field] = {**EXPR["oneOf"][-1]["properties"][_field], "x-label": _label}
EXPR["oneOf"][-1]["properties"]["kind"]["x-enum-labels"] = {"counter_view": "累计资源读数"}
EXPR["oneOf"][-1]["properties"]["sample"]["x-enum-labels"] = {
    "current": "提供者当前累计", "self_frame": "自身当前累计或输出快照"}
EFFECT = obj({"id": STRING, "benefit_type_id": {"enum": [*BENEFIT_TYPES, "benefit.energy_recharge.all"]},
              "value": EXPR, "domain": {"enum": ["self", "provider", "enemy"]},
              "filter": FILTER}, ["id", "benefit_type_id", "value", "domain"])
QUOTA = obj({"capacity": {"type": "integer", "minimum": 1, "maximum": 10000},
             "scope": {"enum": ["per_beneficiary", "team"]},
             "refresh": {"enum": ["reset", "add", "keep"]}, "consume": FILTER},
            ["capacity", "scope", "refresh", "consume"])
RESOURCE = obj({"id": STRING, "kind": {"enum": ["energy_direct", "particles"]},
                "delay": NONNEG, "amounts": {"type": "object", "additionalProperties": NONNEG},
                "count": PARTICLE_COUNT, **PARTICLE_MODE_FIELDS,
                "particle_type": {"enum": ["particle", "orb"]},
                "element": {"enum": [*ELEMENTS, "clear"]}, "travel_time": NONNEG,
                "source_part": STRING}, ["id", "kind", "delay", "source_part"])
ELIGIBILITY = obj({
    "contract": {"const": "beneficiary-eligibility-v1"},
    "exclude_owner": {"type": "boolean", "enum": [True]},
    "characters": {**arr(STRING), "minItems": 1, "uniqueItems": True},
}, ["contract"])
ELIGIBILITY["x-label"] = "受益者资格"
ELIGIBILITY["description"] = "触发时限定接收角色；不是伤害筛选。锁定名单切走后保留至到期。"
RULE = obj({
    "id": STRING, "name_zh": STRING, "owner": STRING, "source_id": STRING,
    "trigger": {"oneOf": [FILTER, THRESHOLD_TRIGGER]},
    "beneficiaries": obj({"mode": {"enum": ["self", "team", "characters", "frontline", "next_switch"]},
                          "characters": arr(STRING), "scope": {"enum": ["locked", "current_frontline"]},
                          "eligibility": ELIGIBILITY}, ["mode", "scope"]),
    "timing": obj({"delay": NONNEG, "duration": NONNEG, "cooldown": NONNEG,
                   "cooldown_anchor": {"enum": ["success", "attempt"]}},
                  ["delay", "duration", "cooldown", "cooldown_anchor"]),
    "stacking": obj({"max": {"type": "integer", "minimum": 1, "maximum": 1000},
                     "add": {"type": "integer", "minimum": 1, "maximum": 1000},
                     "policy": {"enum": ["refresh", "independent", "replace", "keep", "extend", "add_and_refresh"]}},
                    ["max", "add", "policy"]),
    "quota": QUOTA, "resource_gate": obj({"consumer_id": STRING}, ["consumer_id"]),
    "effects": arr(EFFECT), "resources": arr(RESOURCE), **EVIDENCE,
}, ["id", "name_zh", "owner", "source_id", "trigger", "beneficiaries", "timing", "stacking", "effects", "status", "source"])
HIT_COMMON = {"action_id": STRING, "target_id": STRING, "report": {"type": "boolean"}, "self_snapshot_event": STRING,
              "application": APPLICATION, "contact": CONTACT}
CRYSTALLIZE_STATE_EVENT = obj({
    "id": STRING,
    "kind": {"const": "crystallize_shard_pickup"},
    "t": NONNEG,
    "order": INT,
    "target_id": STRING,
    "shard_id": STRING,
    "character": STRING,
    "repeat": REPEAT,
    **EVIDENCE,
}, ["id", "kind", "t", "order", "target_id", "shard_id", "character",
    "repeat", "status", "source"])
BLOOM_CORE_OPERATION_FIELDS = {"id": STRING, "t": NONNEG, "order": INT,
    "target_id": STRING, "area_id": STRING, "character": STRING, "action_id": STRING,
    "repeat": REPEAT, **EVIDENCE}
BLOOM_CORE_CONVERSION_EVENT = obj({**BLOOM_CORE_OPERATION_FIELDS,
    "kind": {"const": "bloom_core_conversion_open"},
    "duration_seconds": {"type": "number", "minimum": .000001, "maximum": 1_000_000_000},
    "replacement_lifetime_seconds": {"type": "number", "minimum": .000001, "maximum": 1_000_000_000},
    "convert_existing": {"type": "boolean"}},
    [*BLOOM_CORE_OPERATION_FIELDS, "kind", "duration_seconds", "replacement_lifetime_seconds", "convert_existing"])
BLOOM_CORE_ABSORPTION_EVENT = obj({**BLOOM_CORE_OPERATION_FIELDS,
    "kind": {"const": "bloom_core_absorb"}, "maximum": {"type": "integer", "minimum": 1, "maximum": 256}},
    [*BLOOM_CORE_OPERATION_FIELDS, "kind", "maximum"])
ELEMENTAL_STATE_EVENT = {"oneOf": [CRYSTALLIZE_STATE_EVENT, BLOOM_CORE_CONVERSION_EVENT, BLOOM_CORE_ABSORPTION_EVENT]}
CONDITION_EVENT = obj({
    "id": STRING, "t": NONNEG, "order": INT, "condition": STRING, "repeat": REPEAT,
    "value": {"type": ["boolean", "null"], "x-label": "已知条件结果"},
    "dew_consumption": {**obj({
        "event_id": STRING, "amount": {"type": "integer", "minimum": 1, "maximum": 3},
    }, ["event_id", "amount"]), "x-label": "根据实际扣露判定"},
    **EVIDENCE,
}, ["id", "t", "order", "condition", "repeat", "status", "source"])
FIXED_FILTER = obj({"event": {"const": "hit"}, "phase": {"const": "pre"},
                    **{k: copy.deepcopy(FILTER["properties"][k]) for k in
                       ("action_ids", "event_ids", "characters", "action_type_ids", "damage_type_ids", "elements")}},
                   ["event", "phase"])
FIXED_EFFECT = obj({"id": STRING, "benefit_type_id": {"const": "benefit.crit_rate.all"},
                    "domain": {"const": "self"}, "value": {"type": "number", "minimum": 0, "maximum": 1},
                    "filter": FIXED_FILTER, "target_predicate": TARGET_PREDICATE_SCHEMA},
                   ["id", "benefit_type_id", "domain", "value", "filter", "target_predicate"])
FIXED_SOURCE = obj({"id": STRING, "name_zh": STRING, "owner": STRING, "source_id": STRING,
                    "availability": {"const": "fixed"}, "damage_scope": {"const": "classic_hits_only"},
                    "target_state_profile": {"const": TARGET_STATE_PROFILE},
                    "effects": {**arr(FIXED_EFFECT), "minItems": 1, "maxItems": 8}, **EVIDENCE},
                   ["id", "name_zh", "owner", "source_id", "availability", "damage_scope",
                    "target_state_profile", "effects", "status", "source"])
GENERAL_FIXED_EFFECT = obj({"id": STRING, "benefit_type_id": {"enum": sorted(BENEFIT_TYPES)},
    "domain": {"enum": ["self", "enemy"]}, "value": {"type": "number"},
    "filter": {**copy.deepcopy(FIXED_FILTER), "properties": {
        **copy.deepcopy(FIXED_FILTER["properties"]), "event": {"enum": ["hit", "reaction_damage"]}}},
    "target_predicate": GENERAL_TARGET_PREDICATE_SCHEMA},
    ["id", "benefit_type_id", "domain", "value", "filter", "target_predicate"])
GENERAL_FIXED_EFFECT["allOf"] = [{"if": {"properties": {"benefit_type_id": {"enum": [
    key for key, info in BENEFIT_TYPES.items() if info["zone"] in ENEMY_ZONES]}}},
    "then": {"properties": {"domain": {"const": "enemy"}}},
    "else": {"properties": {"domain": {"const": "self"}}}}]
GENERAL_FIXED_SOURCE = obj({**copy.deepcopy(FIXED_SOURCE["properties"]),
    "target_state_profile": {"const": GENERAL_TARGET_PROFILE},
    "damage_scope": {"const": "declared_damage_systems"},
    "damage_systems": {**arr({"enum": ["classic", "transformative", "lunar", "stellar"]}),
                       "minItems": 1, "maxItems": 4, "uniqueItems": True},
    "recipient_ids": {**arr(STRING), "minItems": 1, "maxItems": 4, "uniqueItems": True},
    "effects": {**arr(GENERAL_FIXED_EFFECT), "minItems": 1, "maxItems": 8}},
    [*FIXED_SOURCE["required"], "damage_systems"])
MECHANICS_SCHEMA = obj({
    "schema_version": {"const": 1}, "rules": arr(RULE),
    "attribute_model": attribute_model_schema(),
    "execution_options": OPTIONS_SCHEMA,
    "attribute_callbacks": attribute_callback_schema(),
    "shared_pools": {"oneOf": [SHARED_POOLS_SCHEMA, SCALED_POOLS_SCHEMA]},
    "state_runtime": {"oneOf": [STATE_RUNTIME_SCHEMA, STATE_RUNTIME_V2_SCHEMA]},
    "resource_counters": RESOURCE_COUNTER_SCHEMA,
    "resource_counter_views": RESOURCE_COUNTER_VIEW_SCHEMA,
    "resource_reaction_contract": {"oneOf": [obj({"schema_version": {"type": "integer", "enum": [1]},
        "operation_ids": {"type": "array", "minItems": 1, "maxItems": 256, "uniqueItems": True,
            "items": {"type": "string", "minLength": 1, "maxLength": 128}}}, ["schema_version", "operation_ids"]),
        obj({"schema_version": {"type": "integer", "enum": [2]},
            "operation_ids": {"type": "array", "minItems": 1, "maxItems": 256, "uniqueItems": True, "items": STRING},
            "lifecycle_gates": {"type": "array", "maxItems": 256, "items": obj({"operation_id": STRING,
                "lifecycle_id": STRING, "window": {"enum": ["established_until_end"]},
                "generation_policy": {"enum": ["current_at_resource_attempt"]}},
                ["operation_id", "lifecycle_id", "window", "generation_policy"]) }},
            ["schema_version", "operation_ids", "lifecycle_gates"])]},
    "resource_threshold_observers": {"oneOf": [RESOURCE_THRESHOLD_SCHEMA, RESOURCE_THRESHOLD_V2_SCHEMA]},
    "resource_threshold_triggers": {"oneOf": [RESOURCE_THRESHOLD_TRIGGER_SCHEMA, RESOURCE_THRESHOLD_TRIGGER_V2_SCHEMA,
                                              RESOURCE_THRESHOLD_TRIGGER_V3_SCHEMA, RESOURCE_THRESHOLD_TRIGGER_V4_SCHEMA]},
    "snapshot_captures": arr(CAPTURE_SCHEMA),
    "fixed_effect_sources": {**arr({"oneOf": [FIXED_SOURCE, GENERAL_FIXED_SOURCE]}), "maxItems": 64},
    "target_status_events": arr(TARGET_STATUS_EVENT_SCHEMA),
    "target_status_rules": arr(TARGET_STATUS_RULE_SCHEMA),
    "hit_events": arr(obj({"hit_id": STRING, "order": INT, **HIT_COMMON}, ["hit_id", "action_id", "order"])),
    "hit_patterns": arr(obj({"id": STRING, "prototype_hit_id": STRING, "offsets": arr(NONNEG),
                              "orders": arr(INT), **HIT_COMMON},
                             ["id", "prototype_hit_id", "action_id", "offsets", "orders"])),
    "condition_events": arr(CONDITION_EVENT),
    "initial_state": obj({"rules": arr(obj({"rule_id": STRING, "cooldown_remaining": NONNEG,
                                            "instances": arr(obj({"beneficiaries": arr(STRING),
                                                                  "remaining": arr(NONNEG),
                                                                  "quota": {"type": "object", "additionalProperties": NONNEG}},
                                                                 ["beneficiaries", "remaining"]))},
                                           ["rule_id", "cooldown_remaining", "instances"]))}, ["rules"]),
    "resource_ownership": arr(obj({"rule_id": STRING, "action_id": STRING, "source_part": STRING,
                                   "source_ref": TRIGGER_SOURCE_SCHEMA,
                                   "replaces_event_ids": arr(STRING)},
                                  ["rule_id", "source_part", "replaces_event_ids"])),
    "assumptions": arr(STRING),
    "manual_buff_repeat": {"enum": ["each_cycle", "once"]},
    "elemental_model": ELEMENTAL_SCHEMA,
    "special_reaction_model": SPECIAL_REACTION_MODEL_SCHEMA,
    "special_reaction_party_capabilities": {
        **arr(PARTY_CAPABILITY_SCHEMA), "maxItems": 4,
    },
    "special_reaction_events": {
        **arr(SPECIAL_REACTION_EVENT_SCHEMA), "maxItems": 256,
    },
    "special_damage_echoes": SPECIAL_DAMAGE_ECHO_SCHEMA,
    "elemental_state_events": {
        **arr(ELEMENTAL_STATE_EVENT), "maxItems": 256,
    },
    "application_events": arr(obj({"id": STRING, "action_id": STRING, "t": NONNEG,
                                    "order": INT, "application": APPLICATION, "contact": CONTACT},
                                   ["id", "action_id", "t", "order", "application"])),
}, ["schema_version", "rules", "hit_events", "hit_patterns", "condition_events", "initial_state"])
MECHANICS_SCHEMA.update({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "声明式机制执行模型 v1"})


def empty_mechanics_model():
    """Fresh input skeleton; does not attest execution dependencies or ABI."""
    return {"schema_version": 1, "rules": [], "hit_events": [], "hit_patterns": [],
            "condition_events": [], "initial_state": {"rules": []}}


def mechanics_capabilities():
    from genshin_engine.attribute_graph import attribute_graph_capabilities
    from genshin_engine.attribute_multistat_schema import (
        attribute_multistat_capabilities,
    )
    from genshin_engine.attribute_stat_schema import attribute_stat_capabilities
    from genshin_engine.buff_state_timeline import buff_state_timeline_capabilities
    from genshin_engine.resource_budget import (
        RESOURCE_LIMITS_SCHEMA,
        normalize_resource_budget,
    )
    from genshin_engine.resource_execution_report import (
        LIMITS as EXECUTED_REPORT_LIMITS,
    )
    from genshin_engine.runtime_event_match import RUNTIME_EVENT_REF_SCHEMA
    from genshin_engine.special_state_timeline import (
        special_state_timeline_capabilities,
    )

    return {
        "mechanics_schema_version": 1, "schema": copy.deepcopy(MECHANICS_SCHEMA),
        'resource_execution_budget': {'policy': normalize_resource_budget(), 'override_schema': copy.deepcopy(RESOURCE_LIMITS_SCHEMA),
            'engine_path': 'execution_options.resource_limits', 'app_path': 'calculation_options.resource_limits',
            'file_envelopes_independent': True},
        'runtime_damage_match': {'contract': 'runtime-damage-match-v2', 'legacy_contract': 'runtime-damage-match-v1',
            'schema': copy.deepcopy(RUNTIME_EVENT_REF_SCHEMA), 'app_mode': 'runtime_adjust',
            'resolution': 'unique actual occurrence; zero/multiple are pending',
            'stable_identity': 'v2 uses an authored source or an actual producer identity, component and occurrence; timing edits do not change identity',
            'selectors': 'rules/fixed_effect_sources effect filters may use event_refs for exact derived damage; authored sources are checked before execution, generated sources are proven from actual producer lineage at completion; a proven source with no matching occurrence is an empty filter, unresolved sources are rejected and ambiguous generated occurrences are pending',
            'read': 'resolved_template.hits[].extensions.execution_ref and mechanics_trace hit/reaction_damage execution_ref'},
        'target_status': {'contract': 'declared-target-status-v1',
            'events_schema': copy.deepcopy(TARGET_STATUS_EVENT_SCHEMA),
            'rules_schema': copy.deepcopy(TARGET_STATUS_RULE_SCHEMA),
            'scope': 'explicit target/status identities; apply and refresh replace the current generation, remove ends it',
            'timing': 'same event queue order; duration is half-open; hit predicates sample before their own reaction',
            'authority': 'Agent-declared lifetime and meaning; actual reaction triggers do not turn these into independently verified game facts',
            'initial': 'declared statuses begin inactive; undeclared status is unknown',
            'output': 'target_status.trace and target_state_samples[*].status_observations'},
        'resource_trigger_source': {'schema': copy.deepcopy(TRIGGER_SOURCE_SCHEMA),
            'scope': 'current actual callback; immutable source occurrence retained across delayed delivery',
            'direct_reaction_energy': {
                'trigger': {'event': 'elemental_reaction', 'phase': 'post'},
                'resource': 'rules[].resources[kind=energy_direct]',
                'ownership': 'resource_ownership[].source_ref',
                'example_reaction_type': 'stellar_superconduct',
                'settlement': 'energy-direct-base-er-v1',
                'evidence': 'rotation_check.event_log.resource_cause.run_id joins the actual elemental reaction',
                'separate_contract': 'resource_reaction_contract.operation_ids only selects shared_pools.operations',
            }},
        "action_admission_filter": {
            "contract": "action-admission-execution-v1",
            "scope": "action_start only",
            "semantics": "仅匹配核心动作门禁已提交并携带可离线核对证书引用的动作。",
        },
        "beneficiary_eligibility": {"contract": "beneficiary-eligibility-v1",
            "supported_modes": ["self", "team", "characters", "frontline"],
            "unsupported_combinations": ["next_switch", "nonempty_resources", "enemy_effects"],
            "semantics": "先按规则选择，取角色白名单交集并排除提供者；locked保留触发名单至到期，current_frontline另判前台。",
            "empty_selection": "不激活、不取样、不刷新；attempt冷却计入，success冷却不计入。",
            "authority": "来源仍需独立核验；命中include不能越过实例受益者资格。"},
        "attribute_callbacks": {**attribute_callback_capabilities(),
                                "multistat": attribute_multistat_callback_capabilities()},
        "resource_reactions": {
            "contract": "ordinary-reaction-resource-v1", "execution": "mechanics_event_queue",
            "supported_contracts": ["ordinary-reaction-resource-v1", "ordinary-reaction-resource-v2"],
            "supported_versions": [1, 2],
            "input_field": "resource_reaction_contract", "ruleset": "gauge-swirl-dynamic-v3",
            "rulesets": {name: {"reaction_types": sorted(REACTION_TYPES),
                                 "reaction_elements": sorted(elements)}
                         for name, elements in REACTION_ELEMENTS.items()},
            "scope": "普通经典命中的实际附着/反应发生→一次资源grant选择→一次原账提交；仅elemental_reaction/post。",
            "selectors": ["reaction_types", "reaction_elements", "characters", "owner_presence"],
            "source_authority": "只证明当前声明实际执行的一致性；caller_assumed不升级游戏来源。",
            "ordering": "保持原reaction post位置；不追溯父击、输出快照或reaction_created精通取样。",
            "report": "resource_reactions含原始发生指纹、完整Program目录、选择/提交span、真实前缀与最终核验状态。",
            "limits": {"execution_work": 200000, "catalog_slots": 20000, "catalog_bytes": 8388608},
            "lifecycle_gates": {
                "requires": "显式resource_reaction_contract.schema_version=2、非空lifecycle_gates及state_runtime.schema_version=2",
                "binding": "每个operation_id最多绑定一个lifecycle_id；两者同提供者，来源分别核对",
                "window": "established_until_end", "generation_policy": "current_at_resource_attempt",
                "qualification": "只取实际完成控制后的当前代；已调度或只创建未建立不算生效，窗口包含建立、不含结束",
                "ordering": "同刻按实际控制执行顺序判断；不倒推或追溯补充父击资源",
                "rejection": "blocked_lifecycle表示未尝试收入；通过资格仍可能因冷却或封顶没有实际收入",
                "limits": {"execution_work": 20000000, "private_storage_prepaid_bytes": 67108864},
                "executed_report_limits": copy.deepcopy(EXECUTED_REPORT_LIMITS),
                "executed_report_scope": "一次完整核验累计program、发生/操作目录和六份实际流水；重复引用按实际出现量计入，共用原20m工作账。与输入声明200000节点及App输出文档限额分开，不拆包重置。",
                "limits_scope": "仅非空v2领域门控的累计工作与私有索引预扣；不是进程总内存、耗时或所有合法轴完成保证。耗尽保留真实前缀，不生成完整总伤。v1和空v2保留原限额。"},
            "unsupported": ["独立无伤害附着父", "freeze参考模型", "手填反应事件名单", "从来源ID自动推断游戏资格"]},
        "attribute_ledger": {**attribute_ledger_capabilities(),
            "supported_contracts": ["attribute-ledger-v1", "attribute-ledger-v2", "attribute-ledger-v3"],
            "stats": ["em", "atk"],
            "schema_scope": "schema保留纯v1常量账；实际原生输入使用native_schema，各图细节见graph/stat_graph。",
            "expressions": "v1仅常量；v2单EM输入线性式；v3单EM/ATK输入线性式及受益者基础攻击比例应用。",
            "graph": attribute_graph_capabilities(),
            "stat_graph": attribute_stat_capabilities(),
            "multistat_graph": attribute_multistat_capabilities(),
            "native_schema": copy.deepcopy(MECHANICS_SCHEMA["properties"]["attribute_model"]),
            "author_schema": attribute_model_schema(author=True),
            "normalization": "App作者输入可省精确effect_ref分类的component以预览；Engine执行必须使用规范化完整来源，未知分类不提升。",
            "frame_authority": "mechanics_execution",
            "execution": "显式v1启用常量EM账；v2支持普通EM线性转换；v3共用EM/ATK依赖图。总值来自当前面板，ATK基础值仅来自stats.base_atk；EM的NONEXTRA/base分别声明。",
            "reads": "已声明属性表达式与动态v3扩散读取真实current/trigger/event帧；v3可读取ATK与base_atk。旧v2和freeze资格不变。",
            "projection": "逐层证明self旧资格/provider当前资格；授权event可绑定实际已执行帧。出伤投影只作TOTAL根，不能共享为面板。v3比例收益先取样并封顶，再乘受益者基础攻击；核心只接收原比例并应用一次。",
            "numeric_audit": "v3经典ATK缩放与真实core输入逐击校验；attribute_inputs来自实际公式上下文，只绑定纳入结果的原始窗口。普通calculate默认输出不变。",
            "limits_scope": "一次执行的事实捕获、增量登记与读取共享execution_work；事件数另有限额。",
            "unsupported": ["v1表达式转换依赖", "v2的非EM转换闭包", "HP/DEF/ER属性图", "ATK的NONEXTRA", "任意多输入表达式", "命中筛选或限次属性读取", "共享资源门控属性读取", "从未知分类推导NONEXTRA", "v3属性账与旧C2回调合同组合"]},
        "elemental": elemental_capabilities(),
        "elemental_state_operations": {
            "contract": "elemental-state-operation-v1",
            "input_field": "elemental_state_events",
            "supported": ["crystallize_shard_pickup", "bloom_core_conversion_open", "bloom_core_absorb"],
            "execution": "mechanics_event_queue",
            "crystallize_shard_pickup": {
                "requires": ["gauge-multi-aura-v4", "真实shard_id", "明确拾取角色与目标"],
                "result": "消费晶片并把普通结晶护盾状态及替换护盾写入回执",
                "failure": "未知、过期、已拾取或跨目标晶片原子拒绝；不按角色名推断",
            },
            "bloom_core_conversion": {
                "scope": "显式动作、角色、目标及区域，半开有效期内将新普通核替换为独立不爆炸实体；可同时转化该范围内仍有效的旧核。",
                "absorb": "bloom_core_absorb按相同提供者、目标和区域实际扣除最多maximum个替换核，回执记录实际数量。",
                "lifetime": "转换窗口关闭后恢复普通生成；已转换的核仍遵守各自的替换有效期，过期不爆炸。",
            },
        },
        "special_reactions": special_reaction_input_capabilities(),
        "dew_consumption_condition": "条件事件可用 dew_consumption={event_id, amount} 代替 value。仅在所引用的先前消费实际扣除恰好 amount 时触发，可用不同条件区分收益档位。",
        "special_damage_echoes": special_damage_echo_capabilities(),
        "target_state_conditions": target_state_capabilities(),
        "snapshot_captures": snapshot_capture_capabilities(),
        "state_runtime": state_runtime_mechanics_capabilities(),
        "resource_counters": {
            "contract": "resource-counters-v1", "schema": copy.deepcopy(RESOURCE_COUNTER_SCHEMA),
            "execution": "mechanics_event_queue", "requires": ["shared_pools.schema_version=2", "state_runtime"],
            "limits": dict(COUNTER_LIMITS), "quantity_unit": "atoms",
            "accounting": "只累计指定spend实际accepted；与余额、冷却和资源回执一次提交。set下降、grant、consumer均不累计。",
            "controls": "仅由真实生命周期签发步骤依序执行；旧代end消费、结束reset、新代create reset和消费顺序明确。",
            "windows": "创建至结束或建立至结束；closing只给同代本次派发许可，普通同刻消费不借用。",
            "budget": "资源操作与控制自身及累计变化共用40000事件；两类回执共40000行与32MiB。",
            "boundary": "保留累计及当前阶段的相对时点；never跨轮保留，不伪造重置以获得稳态。",
            "source_approval": "引擎只核显式结构与事实一致性；App仍需当前完整来源审核。",
            "report": "resource_counters区分未截断实际消费账、真实已应用游标和流水，不把计数当伤害。",
            "projection": "通过独立resource_counter_views合同读取当前有界或阶梯读数，显式v2另支持实际输出快照；用于有资格的经典直伤，原始账不封顶。",
            "thresholds": "显式阈值v2可监听指定spend事务的固定累计键，并由独立触发合同激活常量provider经典直伤收益；重置不触发。",
            "unsupported": ["累计到普通属性收益投影", "重置或生命周期控制越界触发", "连续原生游戏时钟", "未由实际捕获签发的旧快照累计读数资格"]},
        "resource_counter_views": {
            "contract": "resource-counter-views-v1-v2", "supported_versions": [1, 2],
            "schema": copy.deepcopy(RESOURCE_COUNTER_VIEW_SCHEMA),
            "expression_schema": copy.deepcopy(EXPR["oneOf"][-1]), "execution": "mechanics_event_queue",
            "requires": ["resource_counters", "shared_pools.schema_version=2", "state_runtime"],
            "limits": {"views": 64, "reads": 40000, "trace_bytes": 8*1024*1024},
            "scope": "经典伤害base_add/dmg_bonus/crit_rate/crit_dmg/base_mult，包括其增幅反应。v1仅provider/current；显式v2另支持self/self_frame，不生成规则激活。",
            "sampling": "当前读数在命中pre资源与当前gate实际提交后读取。self_frame无显式self引用时读当前；有引用则读真实捕获的历史值、成员及冻结层数，当前消费门仍核同一实例。父post不追溯父命中。",
            "historical_capture": {
                "contract": "counter-self-frame-actual-v2", "capture": "真实父事件pre/命中处理后、post前；另支持已声明动作捕获。",
                "missing_reference": "缺失、未来、取消或借用捕获均拒绝，不回退当前值。",
                "limits": {"frames": 4096, "members": 32768, "frame_bytes": 8*1024*1024,
                           "verification_bytes": 256*1024*1024},
                "authority": "实际队列、最终规则、资源账与消费者关系绑定；公开JSON不授予执行资格。",
                "saved_proof": "App有界离线重演保存输入；不替代当前数据库来源审核。"},
            "transforms": "按显式unit_scale换算及原子封顶，或显式整数档位；raw账保留实际消费不截断。",
            "budget": "每次实际读取计1公共工作，无cache；单层及层合成有限性、读数与采样字节验证后才预留并发布。",
            "source_approval": "App逐view独立核当前完整manual_context，封顶和档位不借counter来源批准。",
            "failure": "已完成资源事务保留，失败读数不发布、不返回确定总伤；普通扩散明确跳过且不读取。",
            "unsupported": ["schema1的self取样", "trigger/event资源取样", "普通面板及EM/ER", "敌方与剧变伤害投影", "从换算读数触发阈值（阈值合同只读实际原子账）", "原生连续clock", "自动批准游戏事实"]},
        "resource_threshold_observers": {
            "contract": "resource-threshold-observers-v1-v2", "supported_versions": [1, 2],
            "schema": copy.deepcopy(MECHANICS_SCHEMA["properties"]["resource_threshold_observers"]),
            "execution": "mechanics_event_queue", "requires": ["shared_pools.schema_version=2"],
            "scope": "v1监听明确操作或命中消费者提交前后的固定资源余额键；v2另可监听指定spend事务的固定累计键。初态与reset不触发。",
            "counter_input": "v2 counter_value_key要求真实resource_counters；pool_total只读total键，pool_key显式选择既有角色键。一次多键事务仅比较一次最终累计。",
            "comparison": "enter_gte为before<T<=after；enter_lte为before>T>=after，包含等号方向显式。",
            "atomicity": "余额、既有累计、观察和可选签发共同预算，一次提交；候选失败不推进seen/CD/游标。",
            "authority": "公开审计JSON没有派发许可；真实账本对象签发与完整目标定义另行绑定。",
            "unsupported": ["隐式合计或换算视图", "重置触发", "连续游戏时钟", "自动批准来源事实"]},
        "resource_threshold_triggers": {
            "contract": "resource-threshold-trigger-v1-v2-v3-v4", "supported_versions": [1, 2, 3, 4],
            "schema": copy.deepcopy(MECHANICS_SCHEMA["properties"]["resource_threshold_triggers"]),
            "execution": "mechanics_event_queue", "requires": ["resource_threshold_observers", "shared_pools.schema_version=2"],
            "scope": "v1原生资源操作尾；v2另支持唯一普通timer及固定累计键越界；v3配合state_runtime v2可建立或结束有限生命周期；v4另接实际反应的已提交资源操作尾，需原反应对象与账本审计共同认证。其正偏移timer刷新常量provider经典直伤收益。typed触发不参加普通phase监听。",
            "timer_authority": "仅当前桥持有的原始event与parent对象及完整内容、已授权phase、唯一timer/lifecycle和operation范围；报告或复制对象无权触发。",
            "timing": "常量收益pre过线可影响同击、post仅后续；生命周期目标先实际应用控制，其正偏移timer再提供收益。多个目标显式order；结束取消未来timer，不抹去已发放收益的独立TTL。",
            "lifecycle_protocol": "显式v3/state2、typed端点双向同源同owner、完整life/timer目标图绑定；原批每步实际applied后才处理timer尾联动，每个新目标控制批完成后才处理下一目标。签发或领取不是完成。",
            "position": "原生事务尾与原生observe、普通timer外部批尾分别采用单调位置；源操作phase与外部position.phase分开。位置只能由实际引擎构造，输入/报告不能充当许可。",
            "sampling": "整条目标为限定常量，无面板/属性快照读取；旧self捕获不冻结当前provider。",
            "failure": "签发/领取不等于生效；目标失败保实际资源和此前目标，不返回确定总伤。",
            "source_approval": "引擎只验证声明图和真实事务；App须额外审核完整来源及依赖。",
            "unsupported": ["消费者触发", "生命周期hook或终止前末tick触发", "同一操作的多timer来源", "同批混合常量收益和生命周期目标", "阈值目标的零偏移或写资源timer", "资源递归", "动态表达式/普通属性/EM", "C2属性回调共存", "非经典伤害", "连续游戏时钟或自动来源批准"]},
        "shared_pools": {**shared_pool_capabilities(),
            "contract": "shared-resource-pools-v1-v2", "supported_versions": [1, 2],
            "schema": copy.deepcopy(MECHANICS_SCHEMA["properties"]["shared_pools"]),
            "unit": "v1整数次数；v2显式unit_scale下全部数量为原子整数",
            "compatibility": "v1整数次数及旧quota不变；显式v2支持原子计量与spend，可另启用resource_counters累计与resource_counter_views当前直伤投影。",
            "scaled_accounting": {**scaled_shared_pool_capabilities(), "execution": "mechanics_event_queue",
                "scope": "计量池与实际spend；累计由独立resource_counters契约接入，连续时钟与来源批准不由此证明。"},
            "execution": "mechanics_model.shared_pools接入同一队列；每池一个经典伤害消费者，按具体活跃实例门控，多收益仅消费一次。",
            "order": "机制pre触发→资源pre操作→实际命中消费→伤害计算→父事件post→反应post；不会追溯补强已结算父击。",
            "source_approval": "引擎只执行显式声明；App另要求当前上下文的完整来源审核。",
            "report": "shared_pools包含状态和消费回执；隐藏但实际命中仍消费，未命中与派生扩散不消费。"},
        "buff_state_timeline": buff_state_timeline_capabilities(),
        "special_state_timeline": special_state_timeline_capabilities(),
        "empty_model": empty_mechanics_model(),
        "labels": {"rules": "机制规则", "hit_events": "独立命中", "hit_patterns": "多段命中模式", "condition_events": "显式外部条件",
                   "fixed_effect_sources": "固定条件收益来源", "target_predicate": "命中前目标条件",
                   "target_state_profile": "目标状态解释档案", "availability": "来源可用性", "damage_scope": "伤害适用范围",
                   "initial_state": "初始机制状态", "owner": "提供者", "source_id": "来源部件标识", "trigger": "触发条件",
                   "beneficiaries": "受益者", "timing": "持续与冷却", "stacking": "层数与刷新", "quota": "有限次数",
                   "effects": "收益", "resources": "产球与回能", "self": "自身", "provider": "提供者当前", "enemy": "敌方",
                   "pre": "事件前置", "post": "事件后置", "refresh": "整体刷新", "independent": "逐层独立到期",
                   "current": "当前取值", "sample_trigger": "触发时取值", "event": "指定事件取值", "next_switch": "实际接棒角色"},
        "execution": "与动作/冷却/能量共用执行队列；先校验施放门槛，再触发动作机制。",
        "ordering": "输入按微秒时间和唯一 order；每事件先到期、前置、采样/求值、后置。同刻派生事件在父事件之后。",
        "swirl_ordering": "未声明属性回调的旧路径：父命中pre→附着/反应成立与提供者取样→父伤害→父post→reaction-post→同步reaction_damage→专用post；普通队列不得插入。显式回调v2按同一固定真实路由认领完整包，各批共享prestate，全部认领后一次提交；不授予作者自由改phase的权力。",
        "swirl_sampling": "扩散普通非enemy面板取反应成立时状态，enemy取reaction-post后状态；旧v2/冻结保留固定EM范围。动态v3与多元素v4可依实际声明的属性账求EM传递闭包，v4属性账扩展五属性来源；本击条件EM仅进入独立伤害根，不改写创建时可分享EM。trigger监听时点可能晚于反应产生。",
        "swirl_selectors": "v2 reaction_damage只用于post触发或pre收益筛选，不匹配hit或普通次数池；reaction_elements筛选反应元素，elements仍为当前事件元素；owner_presence仅用于trigger并检查rule.owner。",
        "reaction_event_listeners": reaction_listener_capabilities(),
        "swirl_counting": "v2输入展开事件、排队派生事件、实际反应与允许生成的反应伤害共用40000总量限制；拒绝的伤害ICD尝试保留流水但不生成伤害事件。",
        "hit_pattern_ids": "{pattern_id}:{1-based-index}；后续轮次内部加 {cycle}: 前缀，原始 ID 不依赖数组位置。",
        "snapshot_event_ids": "动作开始使用动作 ID，结束使用 {action_id}:end；命中/条件使用事件 ID，均引用同一来源轮。",
        "snapshot_phase": "指定事件的前置规则提交后、后置规则提交前取样；被切人取消的动作在实际取消时发出结束事件。",
        "result_scope": "result/resolved_template 汇总完整显式输入窗口（origin0）的已执行伤害；检查窗口不等于游戏第一轮，不自动复制动作。",
        "initial_state": "每条规则必须显式声明初始冷却与实例（空实例表示未触发）；未知不得填成已激活。",
        "manual_buff_repeat": "新机制路径手工 Buff 默认 each_cycle；可显式 once。旧 calculate 模板语义保持不变。",
        "beneficiaries": "frontline+locked 锁定触发时前台；frontline+current_frontline 随实际前台变化。明确角色集合+current_frontline 取集合与当前前台的交集。",
        "limits": {"max_rules": 128, "max_expanded_events": 40000, "max_layers": 1000},
        "supported": ["动作开始/结束、切人、命中、显式条件触发", "实际接棒者", "内部冷却与延迟", "整体刷新与独立层到期",
                      "每角色/全队次数池", "逐事件前后相位", "显式命中偏移展开", "自己冻结/提供者当前/敌方当前",
                      "常量与单层属性比例/上下限", "确定性产球与直接回能", "完整机制状态跨轮继承",
                      "固定来源的逐命中目标条件暴击率（旧冻结参考合同）；general合同按显式伤害系统及实际公式消费常量",
                      "显式v4属性账的ATK/最大HP/DEF/EM/ER来源、传递取样及消费证明；支持范围以版本合同为准"],
        "unsupported": ["从原文自动生成规则", "随机概率", "elemental 能力范围外的元素与反应", "多目标独立额度", "规则触发规则的递归链",
                        "未声明属性图的转换依赖、图合同范围外属性或表达式", "任意表达式", "初始已冻结属性的活跃实例", "非动作开始时刻扣能", "自动排轴", "数据库关系补漏"],
    }
