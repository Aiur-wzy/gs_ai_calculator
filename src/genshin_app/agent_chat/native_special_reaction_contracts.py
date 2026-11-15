"""Read-only native navigation for the engine's existing special-reaction inputs.

The engine remains the sole validator.  These detached examples describe the
current generic catalog and never attest a party, register knowledge or execute
a replacement.
"""
from __future__ import annotations

import copy

from genshin_engine.special_damage_echo import (
    capabilities as special_damage_echo_capabilities,
)
from genshin_engine.special_reaction_schema import (
    QUALIFICATION_SCHEMA,
    special_reaction_input_capabilities,
)
from genshin_engine.special_reactions import (
    special_reaction_capabilities as engine_special_reaction_capabilities,
)


def _model_example(catalog, family, *, assumed=False):
    profile = catalog["profiles"][family]
    mechanics = {
        **{name: copy.deepcopy(profile[name]) for name in (
            "profile_id", "profile_revision", "profile_content_hash")},
        **copy.deepcopy(profile["parameters"]),
        "status": "assumed" if assumed else "confirmed" if profile["confirmed_ready"] else "pending",
        "source": "只读格式示例：参数取自当前引擎目录；不证明本场景或队伍资格。",
    }
    if assumed:
        mechanics["override_reason"] = "只展示显式假设的必填形式；实际使用须替换为本场景证据、差异与不确定性。"
    handler = next(row for row in catalog["handler_registry"]["handlers"] if row["family"] == family)
    return {
        "schema_version": catalog["schema_version"],
        "replacements": {family: {
            "enabled": True,
            "qualification": {
                "status": "pending", "provider_ids": ["example.provider"],
                "capability_tag": catalog["qualification"]["required_capability_tags"][family],
                "source": "格式占位：替换为实际队伍成员及已核实的资格来源；此例不提供资格。",
            },
            "mechanics": mechanics,
        }},
        "handler_bindings": {family: copy.deepcopy(handler)},
    }


def special_reaction_capabilities():
    """Project public engine producers; no DB lookup, mutation or approval."""
    catalog = engine_special_reaction_capabilities()
    families = list(catalog["profiles"])
    return {
        "contract": "genshin-native-special-reaction-capabilities-v1",
        "extra_damage_contract": special_damage_echo_capabilities(),
        "engine_catalog": catalog,
        "input_contract": copy.deepcopy(special_reaction_input_capabilities()),
        "qualification_schema": copy.deepcopy(QUALIFICATION_SCHEMA),
        "filling": {
            "model_path": "/mechanics_model/special_reaction_model",
            "events_path": "/mechanics_model/special_reaction_events",
            "catalog_identity": "mechanics逐字使用对应profile的profile_id/profile_revision/profile_content_hash，不自行编造身份。",
            "parameters": "将该profile.parameters的每个键值扁平填入mechanics，不能把parameters对象原样嵌套其中。",
            "qualification": "按qualification_schema填实际provider_ids、精确capability_tag及来源；目录和示例不证明成员在队或具有该能力。",
            "confirmed": "仅confirmed_ready=true且参数完全未改时可用mechanics.status=confirmed；不能靠改哈希消除参数差异。",
            "pending": "qualification或mechanics为pending不执行；保留真实未知值，不自动补默认参数。",
            "assumed": "mechanics.status=assumed须有非空override_reason并通过原验证器；不能放宽引擎固定合同。",
            "disabled": "enabled=false仍允许稀疏草稿；读取这些说明不会开启替换。",
            "handler_bindings": "模型v2从engine_catalog.handler_registry复制当前family的完整绑定；它只选择通用实现，不批准游戏事实。",
            "direct_damage": "完整能力直伤schema在input_contract.schemas；反应成立或资源消费不自动产生直伤。v2 ability_binding须指向实际动作能力，示例提供者不能用于正式方案。",
            "direct_status": "直伤confirmed还须与真实受审能力目录完整匹配；技术哈希不授予该资格。assumed必须如实记录来源与假设。",
            "hash": {
                "contract_ref": {"resource": "tool_contracts", "pointer": "/tools/gs_hash_input"},
                "kinds": ["direct_stellar_damage_profile", "direct_lunar_damage_profile"],
                "arguments": "kind选择相应能力profile种类；value传完整能力profile对象，精确字段以工具合同为准。",
                "result": "将返回data.content_hash填入同一profile.profile_content_hash；该字段不参与自身哈希，任何其他内容改变都须重算。",
                "authority": "none",
                "note": "替换机制目录的哈希直接沿用目录；canonical_json技术指纹和能力profile专用哈希不能混用，也不是游戏事实验证。",
            },
            "execution_authority": "none",
        },
        "examples": {
            "notice": "仅输入格式示例，非队伍配置、实测或执行凭据；全部资格保留pending，须按当前场景替换并核验。",
            "disabled_sparse": {
                "schema_version": catalog["schema_version"],
                "replacements": {family: {"enabled": False} for family in families},
                "handler_bindings": {},
            },
            "by_family": {family: {
                "catalog_model": _model_example(catalog, family),
                **({"assumed_syntax": _model_example(catalog, family, assumed=True)}
                   if catalog["profiles"][family]["confirmed_ready"] else {}),
            } for family in families},
        },
    }
