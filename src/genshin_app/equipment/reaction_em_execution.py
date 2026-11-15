"""Join actual source activations to reaction occurrences and application parents."""
from __future__ import annotations

import copy
import math
from collections import defaultdict

from genshin_engine.elemental_schema import DYNAMIC_SWIRL_RULESET
from genshin_engine.runtime_reaction_em_compiler import PROFILE_ID

from .files import fingerprint
from .schema import PlanError


def _pending(message, row=None):
    raise PlanError(message, 'reaction_em_pending', {'result': None, 'issues': [
        {'code': 'reaction_em_occurrence_mismatch', 'message': message, 'record': copy.deepcopy(row)}]})


def _key(row, identity):
    cycle, instant, value = row.get('cycle'), row.get('t'), row.get(identity)
    try:
        finite = type(instant) in (int, float) and math.isfinite(instant) and instant >= 0
    except OverflowError:
        finite = False
    if type(cycle) is not int or cycle < 0 or not finite or not isinstance(value, str) or not value:
        _pending('实际反应流水缺少精确轮次、时刻或事件身份', row)
    return cycle, value, instant


def verify_reaction_em_execution(executed, compilations):
    records = [r for r in compilations if r.get('profile_id') == PROFILE_ID and r.get('status') == 'ready']
    if not records:
        return None
    trace = executed.get('mechanics_trace')
    if not isinstance(trace, list):
        _pending('反应精通执行缺少实际机制流水')
    rules = {}
    for record in records:
        for rule in record['rules']:
            if rule['id'] in rules:
                _pending('同一反应精通规则由多个来源重复交付')
            rules[rule['id']] = record
    reactions, parents = defaultdict(list), defaultdict(list)
    queue = (executed.get('rotation_check') or {}).get('event_log')
    complete_window = ((executed.get('result') or {}).get('mechanics') or {}).get('complete_first_cycle') is True
    if not isinstance(queue, list):
        _pending('反应精通执行缺少真实动作队列记录')
    parent_events = defaultdict(list)
    for index, event in enumerate(queue):
        if not isinstance(event, dict):
            _pending('实际动作队列条目不是对象')
        if event.get('type') == 'mechanics_hit':
            key = _key({**event, 'cycle': event.get('source_cycle')}, 'run_id')
            if complete_window and event['source_cycle'] == 0 and event.get('status') != 'passed':
                _pending('完整输入伤害不能借待核或失败父命中免除反应触发检查', event)
            parent_events[key].append((index, event))

    def actual_parent_event(reaction):
        key = (reaction['cycle'], reaction.get('parent_run_id'), reaction['t'])
        candidates = parent_events[key]
        if len(candidates) != 1:
            _pending('反应父命中须对应唯一实际队列发生，不能按时刻或稳定名称猜测', reaction)
        index, event = candidates[0]
        if (event.get('id') != reaction.get('parent_event_id')
                or event.get('status') not in ('passed', 'pending', 'failed')
                or not isinstance(event.get('frontline'), str) or not event['frontline']):
            _pending('实际反应父命中的身份、执行状态或前台记录缺失', event)
        return index, event

    reaction_run_ids = set()
    for index, row in enumerate(trace):
        if not isinstance(row, dict):
            _pending('机制流水条目不是对象')
        if row.get('type') == 'elemental_reaction':
            run_id = row.get('run_id')
            if not isinstance(run_id, str) or not run_id or run_id in reaction_run_ids:
                _pending('实际反应发生标识缺失或重复', row)
            reaction_run_ids.add(run_id)
            reactions[_key(row, 'event_id')].append((index, row))
        if row.get('type') == 'elemental_application':
            parents[_key(row, 'run_id')].append((index, row))
    for candidates in parents.values():
        if len(candidates) != 1:
            _pending('同一实际附着发生存在重复记录')
        _, parent = candidates[0]
        nested = parent.get('reaction')
        if not isinstance(nested, dict):
            continue
        occurrence = {**nested, 'cycle': parent['cycle'], 't': parent['t']}
        _, parent_event = actual_parent_event(occurrence)
        if parent_event['status'] != 'passed':
            continue
        matches = reactions[(parent['cycle'], nested.get('id'), parent['t'])]
        if (len(matches) != 1 or matches[0][1].get('run_id') != nested.get('run_id')
                or matches[0][1].get('parent_run_id') != parent['run_id']):
            _pending('已完成的附着父记录产生了反应，但缺少唯一对应的实际反应回调', parent)
    rows, used = [], set()
    for index, activation in enumerate(trace):
        if activation.get('type') != 'activation' or activation.get('rule_id') not in rules:
            continue
        record = rules[activation['rule_id']]
        candidates = reactions[_key(activation, 'event_id')]
        if len(candidates) != 1:
            _pending('每次精通激活须对应唯一实际反应，不能借用同名事件或猜测发生身份', activation)
        reaction_index, reaction = candidates[0]
        owner = record['source_identity']['provider']
        target = record['app_scope']['target_id']
        queue_index, parent_event = actual_parent_event(reaction)
        if (reaction.get('owner') != owner or reaction.get('target_id') != target
                or parent_event['frontline'] != owner
                or reaction.get('ruleset') != DYNAMIC_SWIRL_RULESET or reaction.get('status') != 'executed'
                or reaction.get('reaction_type') not in ('melt', 'vaporize', 'swirl')
                or reaction.get('reaction_element', reaction.get('aura_element')) not in ('pyro', 'hydro', 'cryo')
                or not isinstance(reaction.get('run_id'), str) or not reaction['run_id']
                or not isinstance(reaction.get('parent_run_id'), str) or not reaction['parent_run_id']):
            _pending('精通激活的实际反应提供者、目标或发生范围不符', reaction)
        key = (reaction['cycle'], reaction['parent_run_id'], reaction['t'])
        if len(parents[key]) != 1:
            _pending('实际反应缺少唯一原始附着父记录', reaction)
        parent_index, parent = parents[key][0]
        original = parent.get('reaction')
        identity_fields = ('id', 'run_id', 'parent_event_id', 'parent_run_id', 'owner', 'target_id',
                           'action_id', 'ruleset', 'trigger_element', 'aura_element', 'reaction_element')
        if (not isinstance(original, dict) or any(original.get(k) != reaction.get(k) for k in identity_fields)
                or original.get('type') != reaction['reaction_type'] or original.get('id') != reaction['event_id']
                or parent.get('stable_event_id') != reaction.get('parent_event_id')
                or parent.get('character') != owner or parent.get('target_id') != target
                or parent.get('allowed') is not True or parent.get('status') != 'executed'
                or parent.get('outcome') != 'reaction' or parent.get('ruleset') != DYNAMIC_SWIRL_RULESET
                or not parent_index < reaction_index < index):
            _pending('反应与实际附着父记录不一致，不能接受此激活为自动来源执行', reaction)
        unique = activation['rule_id'], reaction['run_id']
        if unique in used:
            _pending('同一来源重复消费同一个实际反应发生', activation)
        used.add(unique)
        rows.append({'binding_id': record['binding_id'], 'rule_id': activation['rule_id'],
            'activation_trace_index': index, 'reaction_trace_index': reaction_index,
            'application_trace_index': parent_index, 'source_window': reaction['cycle'], 't': reaction['t'],
            'parent_queue_index': queue_index, 'parent_execution_fingerprint': fingerprint(parent_event),
            'reaction_event_id': reaction['event_id'], 'reaction_run_id': reaction['run_id'],
            'parent_event_id': reaction['parent_event_id'], 'parent_run_id': reaction['parent_run_id'],
            'reaction_type': reaction['reaction_type'], 'owner': owner, 'target_id': target,
            'activation_fingerprint': fingerprint(activation), 'reaction_fingerprint': fingerprint(reaction),
            'parent_fingerprint': fingerprint(parent)})
    # The forward join rejects fabricated activations; the reverse check also
    # detects omitted ones. Only a completed parent guarantees its whole post
    # callback phase ran. A pending parent may have a valid committed prefix.
    expected = set()
    for candidates in reactions.values():
        if len(candidates) != 1:
            _pending('同一实际反应发生存在重复记录')
        _, reaction = candidates[0]
        _, parent_event = actual_parent_event(reaction)
        for rid, record in rules.items():
            owner = record['source_identity']['provider']
            if reaction.get('owner') != owner or parent_event['frontline'] != owner:
                continue
            if (reaction.get('ruleset') != DYNAMIC_SWIRL_RULESET or reaction.get('status') != 'executed'
                    or reaction.get('target_id') != record['app_scope']['target_id']
                    or reaction.get('reaction_type') not in ('melt', 'vaporize', 'swirl')
                    or reaction.get('reaction_element', reaction.get('aura_element')) not in ('pyro', 'hydro', 'cryo')):
                _pending('来源持有者的实际反应超出已认证范围，不能静默漏掉触发', reaction)
            if parent_event['status'] == 'passed':
                identity = rid, reaction['run_id']
                expected.add(identity)
                if identity not in used:
                    _pending('已完成的在场反应缺少应有的唯一精通激活记录', reaction)
    return {'contract': 'reaction-em-actual-occurrences-v1', 'activations': rows,
            'verified_completed_parent_activations': len(expected),
            'qualification': 'actual_trace_identity_consistency_within_scoped_ordinary_model',
            'order_authority': 'no_cross_listener_order',
            'frontline_authority': 'actual_parent_queue_frontline_and_engine_owner_presence_filter',
            'compilations_fingerprint': fingerprint(records)}
