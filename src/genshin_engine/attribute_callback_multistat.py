"""V2 callback footprint adapter; no second scheduler or source authority."""
from __future__ import annotations

from genshin_engine.attribute_ledger import _component_key
from genshin_engine.attribute_stats import STATS, canonical_read, effect_output


def supported(rule):
    if (rule['trigger'].get('event') not in ('hit', 'elemental_reaction') or rule['trigger'].get('phase') != 'post'
            or rule['timing']['delay'] or rule['stacking'] != {'max': 1, 'add': 1, 'policy': 'refresh'}
            or any(rule.get(k) for k in ('resources', 'quota', 'resource_gate'))
            or rule['beneficiaries']['mode'] == 'next_switch' or rule['beneficiaries']['scope'] != 'locked'):
        return False
    for effect in rule['effects']:
        output = effect_output(effect, kind='rule')
        if output is None and effect['benefit_type_id'] != 'benefit.dmg_bonus.all':
            from genshin_engine.registry import BENEFIT_TYPES
            if BENEFIT_TYPES.get(effect['benefit_type_id'], {}).get('zone') != 'dmg_bonus':
                return False
        if output is not None and 'filter' in effect:
            return False
        value = effect['value']
        if isinstance(value, dict) and (value.get('kind') != 'stat' or canonical_read(value.get('stat'))[0] not in STATS):
            return False
    return True


def node(runtime, candidate, snapshot, *, member, reads_enabled):
    from genshin_engine.attribute_callback_runtime import _digest, _fail
    rule = runtime.executor.rules[candidate['rule_id']]
    event = candidate['event']
    reads, writes = [], []
    for effect in rule['effects']:
        runtime._charge(1 + len(candidate['beneficiaries']))
        value = effect['value']
        output = effect_output(effect, kind='rule')
        for actor in candidate['beneficiaries']:
            if reads_enabled and isinstance(value, dict) and value.get('kind') == 'stat':
                stat, basis = canonical_read(value['stat'], value.get('basis', 'total'))
                subject = rule['owner'] if value['subject'] == 'owner' else actor
                sample = snapshot
                historical = value['sample'] == 'event'
                if historical:
                    sample = runtime.executor.snapshots.get((event['origin'], value['event_id']))
                    if sample is None:
                        _fail('attribute_frame_missing', '指定回调属性事件尚未执行', value['event_id'])
                receipt = runtime._footprint(subject, basis, sample, stat=stat)
                reads.append({'id': _digest([effect['id'], actor, 'read']), 'actor': subject, 'stat': stat, 'basis': basis,
                              'frame_id': sample['attribute_frame']['frame']['id'],
                              'sample_scope': 'historical' if historical else 'current',
                              'current_dependencies': [] if historical else receipt['current_dependencies']})
            if output is None:
                continue
            stat = output['stat']
            classified = runtime.executor.attribute_runtime.classifications.get(('rule', rule['id'], effect['id']))
            if classified is None and member:
                _fail('attribute_component_missing', '因果写者缺少真实属性组件身份', rule['id'])
            if classified is None:
                classified = {'component': {'source_fingerprint': 'missing', 'provider': rule['owner'],
                    'part_id': rule['source_id'], 'effect_id': effect['id']}, 'status': 'pending', 'nonextra_inclusion': 'unknown'}
            component = {**classified['component'], 'recipient': actor}
            if member:
                included = runtime.executor.attribute_runtime.ledger._initials.get((actor, stat), {}).get('included_components', [])
                if any(_component_key(c['component']) == _component_key(component) for c in included):
                    _fail('attribute_duplicate_delivery', '回调写者重复交付已含面板组件', rule['id'], status='invalid')
                if any(m.get('rule_id', m.get('effect_ref', {}).get('buff_id')) == rule['id']
                       and m.get('effect_id', m.get('effect_ref', {}).get('effect_id')) == effect['id']
                       for m in runtime.executor.matches):
                    _fail('causal_member_scope', '命中专属属性写者不能作为普通属性回调', rule['id'])
            writes.append({'id': _digest([effect['id'], actor, 'write']), 'actor': actor, 'stat': stat,
                           'component': component,
                           'nonextra_inclusion': classified['nonextra_inclusion'] if classified['status'] != 'pending' else 'unknown'})
    return {'id': _digest([event['run_id'], rule['id']]), 'rule_id': rule['id'],
            'callback': {'kind': 'hit_post' if event['event'] == 'hit' else 'reaction_post', 'origin': event['origin'],
                         'actual_run_id': event['run_id'], 'parent_actual_run_id': runtime.active_parent['run_id'], 'event_id': event['id']},
            'reads': reads, 'writes': writes, 'activation_keys': [_digest([rule['owner'], rule['source_id']])]}


def potential_reads(runtime, actor, basis, snapshot, outsiders, *, stat):
    """Include future ordinary writers; past explicit samples cannot read them."""
    token = snapshot['attribute_frame']
    fid = token['frame']['id']
    links = []
    for item in token['contributions']:
        runtime._charge()
        value = item['value']
        if isinstance(value, dict) and value['input']['frame_id'] == fid:
            source = value['input']
            links.append((item['component']['recipient'], item['stat'], item['nonextra_inclusion'],
                          source['actor'], source['stat'], source['basis']))
    sources = [(m['actor'], m['source']) for m in token['missing']]
    for candidate in outsiders:
        rule = runtime.executor.rules[candidate['rule_id']]
        for effect in rule['effects']:
            runtime._charge(1 + len(candidate['beneficiaries']))
            if effect_output(effect, kind='rule'):
                sources.extend((a, ['rule', rule['id'], effect['id']]) for a in candidate['beneficiaries'])
    for recipient, key in sources:
        runtime._charge()
        if key[0] != 'rule':
            continue
        rule = runtime.executor.rules[key[1]]
        effect = next(e for e in rule['effects'] if e['id'] == key[2])
        value, output = effect['value'], effect_output(effect, kind='rule')
        if output and isinstance(value, dict) and value.get('kind') == 'stat' and value.get('sample') == 'current':
            classified = runtime.executor.attribute_runtime.classifications.get(tuple(key))
            inclusion = classified['nonextra_inclusion'] if classified and classified['status'] != 'pending' else 'unknown'
            subject = rule['owner'] if value['subject'] == 'owner' else recipient
            source_stat, source_basis = canonical_read(value['stat'], value.get('basis', 'total'))
            links.append((recipient, output['stat'], inclusion, subject, source_stat, source_basis))
    stat, basis = canonical_read(stat, basis)
    seen, pending = set(), [(actor, stat, basis)]
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        runtime._charge(1 + len(links))
        seen.add(current)
        if current[2] == 'base':
            continue
        for recipient, output_stat, inclusion, subject, source_stat, source_basis in links:
            if (recipient, output_stat) == current[:2] and not (current[2] == 'nonextra' and inclusion == 'excluded'):
                pending.append((subject, source_stat, source_basis))
    return [{'actor': a, 'stat': s, 'basis': b} for a, s, b in sorted(seen) if b != 'base']
