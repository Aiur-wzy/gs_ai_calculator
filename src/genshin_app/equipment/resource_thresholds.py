"""Validate complete threshold declarations before source-based pruning."""
from __future__ import annotations

import copy

from genshin_engine.mechanics import validate_mechanics_model
from genshin_engine.resource_counters import CounterProgram
from genshin_engine.resource_threshold_dispatch import ThresholdTriggerProgram
from genshin_engine.resource_thresholds import ThresholdObserverProgram
from genshin_engine.shared_pools import SharedPoolError

from .schema import PlanError


def inspect_original_resource_thresholds(model, character_ids):
    """Validate before exclusion and return only checked dependency identities."""
    if not any(field in model for field in ('resource_threshold_observers', 'resource_threshold_triggers')):
        return {'issues': [], 'operation_origins': {}}
    checked = copy.deepcopy(model)
    for row in checked.get('rules', []):
        if row.get('status') == 'pending': row['status'] = 'assumed'
    for field, groups in (('shared_pools', ('pools', 'operations', 'consumers')),
                          ('state_runtime', ('lifecycles', 'timers')),
                          ('resource_counters', ('counters',)),
                          ('resource_threshold_observers', ('observers',)),
                          ('resource_threshold_triggers', ('bindings',))):
        for group in groups:
            for row in (checked.get(field) or {}).get(group, []):
                if row.get('status') == 'pending': row['status'] = 'assumed'
    validation = validate_mechanics_model(checked)
    if validation['status'] == 'invalid':
        raise PlanError('资源阈值完整原图结构无效，不能通过排除来源隐藏错误',
                        'invalid_resource_threshold_source', validation['issues'])
    try:
        counter = None
        if any(row['input']['kind'] == 'counter_value_key' for row in checked['resource_threshold_observers']['observers']):
            counter = CounterProgram(checked.get('resource_counters'), checked.get('shared_pools'),
                                     checked.get('state_runtime'), character_ids=sorted(character_ids))
        ThresholdObserverProgram(checked['resource_threshold_observers'], checked['shared_pools'],
                                 character_ids=sorted(character_ids), counter_program=counter)
        origins = {}
        if 'resource_threshold_triggers' in checked:
            program = ThresholdTriggerProgram(checked, character_ids=sorted(character_ids))
            origins = program.operation_origins
    except SharedPoolError as exc:
        raise PlanError('资源阈值完整原图的来源、目标或依赖关系无效',
                        'invalid_resource_threshold_source', [copy.deepcopy(exc.issue)]) from exc
    return {'issues': validation['issues'], 'operation_origins': origins}


def validate_original_resource_thresholds(model, character_ids):
    return inspect_original_resource_thresholds(model, character_ids)['issues']
