"""Caller-declared callback order; neither footprints nor source authority."""
from __future__ import annotations

import copy

from genshin_engine.rotation_schema import arr, obj

CONTRACT = 'mechanics-attribute-callbacks-v1'
MULTISTAT_CONTRACT = 'mechanics-attribute-callbacks-v2'
ID = {'type': 'string', 'minLength': 1, 'maxLength': 128}
ATTRIBUTE_CALLBACK_SCHEMA = obj({
    'contract': {'const': CONTRACT},
    'members': {**arr(ID), 'minItems': 1, 'maxItems': 128},
    'edges': {**arr(obj({'before_rule_id': ID, 'after_rule_id': ID, 'assumption_id': ID},
        ['before_rule_id', 'after_rule_id', 'assumption_id'])), 'maxItems': 2048},
    'assumptions': {**arr(obj({'id': ID, 'note': {'type': 'string', 'minLength': 1, 'maxLength': 4096}},
        ['id', 'note'])), 'maxItems': 2048},
}, ['contract', 'members', 'edges', 'assumptions'])
ATTRIBUTE_CALLBACK_SCHEMA_V2 = copy.deepcopy(ATTRIBUTE_CALLBACK_SCHEMA)
ATTRIBUTE_CALLBACK_SCHEMA_V2['properties']['contract'] = {'const': MULTISTAT_CONTRACT}


def attribute_callback_schema():
    return {'oneOf': [copy.deepcopy(ATTRIBUTE_CALLBACK_SCHEMA), copy.deepcopy(ATTRIBUTE_CALLBACK_SCHEMA_V2)]}


def attribute_multistat_callback_capabilities():
    return {**attribute_callback_capabilities(), 'contract': MULTISTAT_CONTRACT,
            'schema': copy.deepcopy(ATTRIBUTE_CALLBACK_SCHEMA_V2), 'attribute_contract': 'attribute-ledger-v4',
            'runtime_integration': 'five_scalar_successful_hit_post_callbacks',
            'scope': '五属性来源／完整读写闭包；零延迟单层refresh；current/trigger/event取样；不携带资源。',
            'packet': {'scope': 'actual_hit_plus_all_actual_reactions', 'claim': 'every_actual_post_phase',
                       'commit': 'once_after_complete_packet_claim',
                       'occurrence_order': 'runtime_fixed_actual_reaction_route_not_author_phase',
                       'repeated_rule': 'ordered_refresh_occurrences_with_one_source_identity'}}


def attribute_callback_capabilities():
    return {'contract': CONTRACT, 'schema': copy.deepcopy(ATTRIBUTE_CALLBACK_SCHEMA),
            'source_authority': 'caller_assumed_order_not_game_source_approval',
            'runtime_integration': 'ordinary_v3_successful_hit_post_callbacks',
            'scope': '零延迟单层refresh；常量或触发时精通取样；真实成员与读写足迹由执行器确定。',
            'atomicity': 'shared_prestate_per_batch_with_actual_commit_prefix',
            'compatibility': '未声明本合同的模型保留旧phase与取样；不改变reaction_created。'}
