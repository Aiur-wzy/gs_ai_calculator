"""Independent plan description producers; no runtime capability attestation."""
from __future__ import annotations

import copy


def native_mechanics_schema():
    """Generic writer shape: hard ceilings, never a candidate's active budget."""
    from genshin_engine.attribute_budget import attribute_budget_capabilities
    from genshin_engine.attribute_contract import attribute_model_schema
    from genshin_engine.mechanics_schema import MECHANICS_SCHEMA

    budget = attribute_budget_capabilities()
    schema = copy.deepcopy(MECHANICS_SCHEMA)
    schema["properties"].pop("execution_options")
    schema["properties"]["attribute_model"] = attribute_model_schema(
        author=True, limits=budget["hard_limits"])
    schema["description"] = "原生作者机制输入；执行配置只填方案顶层calculation_options，不填mechanics_model.execution_options。属性数组上限是通用硬上限，不是本次生效预算。"
    schema["x-genshin-authoring-budget"] = {
        "schema_limits": "absolute_hard_limits_not_current_effective",
        "effective_rule": "validate_calculation_options(plan.get('calculation_options', {'schema_version':1}))['effective']；省略项取defaults，显式项覆盖，仍受hard_limits限制。",
        **budget,
    }
    return schema


def app_mechanics_capabilities():
    """Scope the engine view and publish the same actual native writer shape."""
    from genshin_app.presentation import buff_presentation_capabilities
    from genshin_engine.mechanics_schema import mechanics_capabilities

    value = mechanics_capabilities()
    schema = native_mechanics_schema()
    budget = copy.deepcopy(schema["x-genshin-authoring-budget"])
    value["schema_scope"] = "engine_execution"
    value["buff_state_timeline"]["presentation"] = buff_presentation_capabilities()
    value["native_authoring"] = {
        "contract_id": "native-mechanics-authoring-v1", "version": 1,
        "scope": "native_plan.mechanics_model", "schema": schema,
        "structural_schema_only": True,
        "execution_options_path": "calculation_options",
        "attribute_budget": budget,
        "validation": "实际接纳仍按当前方案calculation_options的effective预算及原有来源、绑定和机制规则检查；通用形状不代表已核实或可执行。",
    }
    attribute = value["attribute_ledger"]
    attribute["schema_scope"] = "schema为纯账格式；旧名native_schema为规范化引擎执行形态，原生作者使用author_schema。"
    attribute["native_schema_scope"] = "engine_normalized_execution"
    attribute["author_schema"] = copy.deepcopy(schema["properties"]["attribute_model"])
    attribute["author_schema_scope"] = "native_authoring_absolute_hard_limits_not_current_effective"
    attribute["author_budget"] = budget
    return value


def plan_description_schema():
    from .rotation_authoring import native_rotation_schema
    from .schema import PLAN_SCHEMA

    schema = copy.deepcopy(PLAN_SCHEMA)
    schema["properties"]["rotation_model"] = {"anyOf": [native_rotation_schema(), {"type": "null"}]}
    schema["properties"]["mechanics_model"] = {"anyOf": [native_mechanics_schema(), {"type": "null"}]}
    schema["allOf"] = [
        {"if": {"properties": {"plan_version": {"const": 1}}},
         "then": {"not": {"required": ["rotation_model"]}}},
        {"if": {"properties": {"plan_version": {"enum": [2, 3]}}},
         "then": {"required": ["rotation_model"]}},
        {"if": {"properties": {"plan_version": {"const": 3}}},
         "then": {"required": ["mechanics_model"]},
         "else": {"not": {"required": ["mechanics_model"]}}},
        {"if": {"properties": {"plan_version": {"enum": [1, 2]}}},
         "then": {"properties": {"effect_bindings": {"items": {"not": {"required": ["upstream_recipe"]}}}}}},
    ]
    return schema


def plan_semantic_constraints():
    return [
                "原生版本3明确mechanics_model和rotation_model；null可保存为待补草稿，不自动从原文生成规则",
                "mechanics来源部件rule={kind:mechanics,rule_id}、delivery={kind:mechanics}；规则source_id为binding_id:part_id",
                "版本3手工Buff须显式声明extensions.owner及source_id；缺来源身份为pending，独立手工来源也须明确命名；旧版本不变",
                "新机制match使用effect_ref.buff_id=规则ID；只修候选，仍受触发/CD/次数消费限制",
                "固定条件来源分项使用rule={kind:fixed_effect_source,fixed_source_id}和同kind交付；source_id=binding_id:part_id",
                "固定条件微调使用effect_ref.fixed_source_id；include可覆盖已知false，不能越过未知状态/owner/classic/filter范围；记录自动值和人工覆盖",
                "原生方案版本 2 显式携带 rotation_model；null 和旧版本均为未检查，不从伤害组反推动作",
                "原生动作模型的各角色 energy_recharge 由本次已组合面板覆盖；回执记录声明值与采用值，面板缺值保持未知",
                "伤害计算可保留动作或能量失败的纸面结果，但不能将该数值称为已验证可执行循环 DPS",
                "stat_scale 规则必须配 dependencies.mode=stat_rule；即使保留在 excluded/pending 部件中也检查这一关系",
                "excluded 不再使用规则时 rule/delivery 可设 null，但必须填写 reason；空 parts 不能自动表示覆盖完整",
                "percent 面板贡献仅支持 atk/hp/def；比例均为小数，其余静态贡献用 flat 加算",
                "完整配装 base/static 转化提供者必须启用可拆分基础；panel 模式 static 取给定面板，base 仅取显式基础值；未声明模式的旧最终面板不能充当基数",
                "panel 输入不得混入 basis/artifacts/manual_contributions，给定面板已包含常驻属性，不由装备选择回算",
                "applies_to.none 在原生计划中需 effect.extensions.exclusion_reason 或已确认的空 replace 理由",
                "完整数据版本、来源/条件覆盖和依赖指纹由 compose 重验；validate 结构合法不表示 ready",
            ]
