"""Pure v3 ordinary EM/ATK graph; no runtime or source admission."""
from __future__ import annotations

import copy

from genshin_engine.attribute_budget import (
    DEFAULT_LIMITS,
    attribute_budget_capabilities,
    limits_for_schema,
)
from genshin_engine.attribute_graph import GRAPH_LIMITS, LINEAR
from genshin_engine.attribute_schema import CONTRIBUTION, FRAME, INITIAL, LIMITS
from genshin_engine.rotation_schema import arr, obj

STAT_GRAPH_CONTRACT = 'attribute-ledger-v3'
STATS = ('em', 'atk')
STAT_LINEAR = copy.deepcopy(LINEAR)
STAT_LINEAR['properties']['input']['properties']['stat'] = {'enum': list(STATS)}
ATK_INITIAL = copy.deepcopy(INITIAL)
ATK_INITIAL['properties']['stat'] = {'const': 'atk'}
ATK_INITIAL['properties'].pop('nonextra')
ATK_INITIAL['properties']['included_components']['items']['properties']['nonextra_inclusion'] = {'const': 'unknown'}
STAT_CONTRIBUTION = copy.deepcopy(CONTRIBUTION)
STAT_CONTRIBUTION['properties'].update(
    stat={'enum': list(STATS)},
    value={'oneOf': [{'type': 'number'}, STAT_LINEAR]},
    value_unit={'enum': ['flat', 'ratio']},
    application=obj({'kind': {'enum': ['flat', 'recipient_base_fraction']}}, ['kind']))
STAT_CONTRIBUTION['required'].extend(['value_unit', 'application'])
STAT_GRAPH_SCHEMA = obj({'contract': {'const': STAT_GRAPH_CONTRACT},
                         'initials': arr({'oneOf': [copy.deepcopy(INITIAL), ATK_INITIAL]}),
                         'contributions': arr(STAT_CONTRIBUTION), 'frames': arr(copy.deepcopy(FRAME))},
                        ['contract', 'initials', 'contributions', 'frames'])


def attribute_stat_capabilities():
    return {'contract': STAT_GRAPH_CONTRACT, 'schema': limits_for_schema(STAT_GRAPH_SCHEMA, DEFAULT_LIMITS),
            'stats': list(STATS), 'basis': {'em': ['total', 'nonextra', 'base'], 'atk': ['total', 'base']},
            'limits': {**LIMITS, **GRAPH_LIMITS}, 'budget':attribute_budget_capabilities(), 'runtime_integration': 'mechanics_explicit_attribute_model_v3_qualified_projection',
            'frame_authority': 'caller_supplied_frame', 'source_authority': 'none',
            'applications': ['flat', 'recipient_base_fraction'],
            'percentage_order': 'evaluate_and_clamp_ratio_then_multiply_immutable_recipient_initial_base',
            'projection': 'ordinary class rejects projection; qualified adapter supports matching actor TOTAL roots only',
            'unsupported': ['ordinary_reads_of_damage_projection', 'source_approval', 'atk_nonextra', 'hp', 'def', 'energy_recharge',
                            'damage_context', 'quota', 'resource_gate', 'hit_match']}
