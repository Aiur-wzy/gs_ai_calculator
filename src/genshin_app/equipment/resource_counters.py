"""Approve complete counter declarations without changing independent pool work."""
from __future__ import annotations

import copy

from genshin_engine.resource_counters import CounterProgram
from genshin_engine.shared_pools import SharedPoolError

from .schema import PlanError


def prepare_resource_counters(model, rules, active, linked, excluded, missing, *,
                              source_reviews, context_fingerprint, pools, state,
                              pool_adoptions, state_adoptions, character_ids):
    config = model.get('resource_counters')
    if config is None:
        return None, []
    checked = copy.deepcopy(model)
    for field, groups in (('resource_counters', ('counters',)),
                          ('shared_pools', ('pools','operations','consumers')),
                          ('state_runtime', ('lifecycles','timers'))):
        for group in groups:
            for row in (checked.get(field) or {}).get(group, []):
                if row.get('status') == 'pending':
                    row['status'] = 'assumed'
    try:
        CounterProgram(checked['resource_counters'], checked.get('shared_pools'),
                       checked.get('state_runtime'), character_ids=sorted(character_ids))
    except SharedPoolError as error:
        raise PlanError('资源累计原图结构、计量或依赖归属无效', 'invalid_counter_source', error.issue) from error

    authority = {}
    for rid, rule in rules.items():
        if rid not in linked:
            continue
        if rule['source_id'] in authority:
            raise PlanError('累计来源部件由多条权威规则重复交付', 'duplicate_delivery')
        authority[rule['source_id']] = rule
    original = {('pools',r['id']):r for r in model['shared_pools']['pools']}
    original.update({('operations',r['id']):r for r in model['shared_pools']['operations']})
    original.update({('lifecycles',r['id']):r for r in model['state_runtime']['lifecycles']})
    delivered = {('pools',r['id']):r for r in (pools or {}).get('pools', [])}
    delivered.update({('operations',r['id']):r for r in (pools or {}).get('operations', [])})
    delivered.update({('lifecycles',r['id']):r for r in (state or {}).get('lifecycles', [])})
    approvals = {(r['kind'],r['id']):r for r in [*pool_adoptions,*state_adoptions]}
    records, kept = [], []
    for counter in config['counters']:
        path = f"mechanics_model.resource_counters.counters.{counter['id']}"
        rule = authority.get(counter['source_id'])
        record = {'id':counter['id'],'kind':'counters','source_id':counter['source_id'],
                  'owner':counter['owner'],'path':path,'own_source_status':'pending',
                  'effective_status':'pending','status':'pending','dependencies':[]}
        records.append(record)
        if rule is not None:
            record['authority_rule_id'] = rule['id']
            if rule['owner'] != counter['owner']:
                raise PlanError('累计与来源规则的提供者不一致', 'invalid_counter_source', record)
        review = source_reviews.get(counter['source_id']) or {}
        if rule and rule['id'] in excluded:
            record['own_source_status'] = 'excluded'
        elif rule is None:
            missing('counter_source_pending', path+'.source_id', '累计需要唯一已登记mechanics来源部件', 'review_mechanics')
        elif (review.get('mode') != 'manual_context' or review.get('context_fingerprint') != context_fingerprint
                or not isinstance(review.get('note'), str) or not review['note'].strip()):
            missing('counter_source_scope_unreviewed', path+'.source_id', '累计子图需要当前完整manual_context及复核说明，旧规则确认不能批准新增累计', 'review_mechanics')
        elif rule['id'] not in active or counter['status'] == 'pending':
            missing('counter_source_pending', path, '累计来源或声明仍待核验', 'review_mechanics')
        else:
            record['own_source_status'] = 'ready'
        keys = [('pools',counter['pool_id']), ('lifecycles',counter['lifecycle_id'])]
        keys += [('operations',ident) for ident in counter['operation_ids']]
        for key in keys:
            raw, actual, approval = original[key], delivered.get(key), approvals.get(key)
            status = 'pending'
            if approval and approval.get('source_id') == raw['source_id']:
                adopted = approval.get('effective_status', approval.get('status'))
                if adopted in ('excluded','dependency_excluded'):
                    status = 'excluded'
                elif (adopted == 'ready' and actual == raw and raw.get('status') != 'pending'):
                    status = 'ready'
            record['dependencies'].append({'kind':key[0],'id':key[1],'source_id':raw['source_id'],'status':status})
        own = record['own_source_status']
        dep_states = {r['status'] for r in record['dependencies']}
        if own == 'excluded':
            status, reason = 'excluded', 'source_part_excluded'
        elif 'excluded' in dep_states:
            status, reason = 'dependency_excluded', 'counter_dependency_excluded'
        elif 'pending' in dep_states:
            status, reason = 'dependency_pending', 'counter_dependency_pending'
            missing(reason, path, '累计依赖的池、生命周期或扣量操作尚未完整交付', 'review_mechanics')
        elif own == 'ready':
            status, reason = 'ready', 'current_context_reviewed'
        else:
            status, reason = 'pending', 'counter_source_pending'
        record.update(effective_status=status, status=status, reason=reason)
        if status == 'ready':
            kept.append(copy.deepcopy(counter))
    records.sort(key=lambda row: row['id'])
    return ({**copy.deepcopy(config),'counters':kept} if kept else None), records
