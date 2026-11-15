"""Finite, source-bound extra reaction damage; no probability or world mutation."""
from __future__ import annotations

import copy
import hashlib
import json
from decimal import Decimal

CONTRACT = 'special-damage-echo-v1'
FIELD = 'special_damage_echoes'
MAX_ENTRIES = 256


def obj(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 4096}
IDENTITY = {'type': 'string', 'minLength': 1, 'maxLength': 256}
PARENT = obj({'reaction_type': {'const': 'lunar_electro'}, 'component': {'const': 'thundercloud'},
    'target_id': IDENTITY, 'area_id': IDENTITY, 't': {'type': 'number', 'minimum': 0}})
SEMANTICS = obj({'contract': {'const': CONTRACT},
    'contributor_policy': {'const': 'actual_parent_contributors'},
    'attribute_sample': {'const': 'post_parent_current'},
    'reaction_world_effects': {'const': 'none'}, 'damage_consumers': {'const': 'normal'},
    'callbacks': {'const': 'none'}, 'max_per_parent_source': {'const': 1}})
ENTRY = obj({'id': IDENTITY, 'source_rule_id': IDENTITY, 'owner': IDENTITY, 'source_id': IDENTITY,
    'parent': PARENT, 'branch': obj({'outcome': {'const': 'triggered'},
        'probability': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1}}),
    'semantics': SEMANTICS, 'precision': obj({'kind': {'const': 'approximate'},
        'method': TEXT, 'uncertainty': TEXT}), 'status': {'const': 'assumed'}, 'source': TEXT})
SCHEMA = {'type': 'array', 'items': ENTRY, 'maxItems': MAX_ENTRIES}


def capabilities():
    return {'contract': CONTRACT, 'field': FIELD, 'schema': copy.deepcopy(SCHEMA),
        'parent_selection': 'exact actual natural lunar_electro/thundercloud request by area,target,t; input-window origin0 only',
        'source': 'existing active source_rule_id; exact owner/source_id and normal App source approval',
        'calculation': 'same reaction formula, actual parent contributors, post-parent current attributes, independent Crit ranking',
        'world_effects': 'none: no gauge consumption, application, cloud phase or recursive generation',
        'damage_consumers': 'normal: active Buff filtering, quotas and shared pools still execute',
        'callbacks': 'none: no pre/post trigger activation or generated resources from the extra event',
        'precision': 'assumed/approximate is mandatory; this is not verification of a character-specific extra strike',
        'randomness': 'explicit finite triggered branch only; no random draw or probability-weighted fractional hit',
        'missing_parent': 'pending at complete input boundary; no parent can be invented or silently selected',
        'authority': 'typed scenario input only; no direct result/summary edits'}


def parent_key(parent):
    return (parent['reaction_type'], parent['component'], parent['area_id'], parent['target_id'],
            int(Decimal(str(parent['t'])) * 1_000_000))


def validate_bindings(model, *, characters=None, period=None):
    rules = {r['id']: r for r in model.get('rules', [])}
    issues, ids, owners = [], set(), set()
    for index, item in enumerate(model.get(FIELD, [])):
        path = f'mechanics_model.{FIELD}[{index}]'
        def error(code, message, *, path=path):
            issues.append({'code': code, 'message': message, 'path': path, 'severity': 'error'})
        if item['id'] in ids:
            error('special_echo_duplicate', '额外特殊伤害声明ID重复。')
        ids.add(item['id'])
        if any(not value.strip() for value in (item['source'], item['precision']['method'], item['precision']['uncertainty'])):
            error('special_echo_precision', '额外伤害的来源、近似方法与未知范围必须明确填写。')
        rule = rules.get(item['source_rule_id'])
        if not rule or (rule['owner'], rule['source_id']) != (item['owner'], item['source_id']):
            error('special_echo_source', '额外伤害必须引用同提供者、同规范来源分项的现有规则。')
        if characters is not None and item['owner'] not in characters:
            error('special_echo_owner', '额外伤害来源提供者不在当前队伍。')
        key = (*parent_key(item['parent']), item['owner'], item['source_id'])
        if key in owners:
            error('special_echo_duplicate', '同一自然父包的同一提供者/规范来源只能交付一次额外伤害。')
        owners.add(key)
        time = item['parent']['t']
        scaled = Decimal(str(time)) * 1_000_000
        if scaled != scaled.to_integral_value():
            error('special_echo_time', '父包时点最多保留六位小数，不能猜测私有运行身份。')
        if period is not None and time > period:
            error('special_echo_time', '声明的父包时点超出完整执行窗口。')
    return issues


class SpecialDamageEchoRuntime:
    def __init__(self, declarations):
        self.declarations = copy.deepcopy(declarations)
        self.seen = {}
        self.trace = []

    def project(self, runtime, request, cycle, parent):
        if cycle != 0 or request.get('extra_damage_provenance') is not None:
            return
        if request.get('reaction_type') != 'lunar_electro' or request.get('component') != 'thundercloud':
            return
        from genshin_engine.elemental import ElementalPending
        from genshin_engine.mechanics_reaction_execution import project_special_damage
        for declaration in self.declarations:
            if parent_key(declaration['parent']) != parent_key(request):
                continue
            identity = declaration['id']
            if identity in self.seen:
                raise ElementalPending('special_echo_parent_ambiguous', '额外伤害声明匹配到多个自然父包。', identity,
                    {'parent_candidates': [self.seen[identity], request['event_id']]})
            rule = runtime.rules[declaration['source_rule_id']]
            active = runtime.state[rule['id']]['instances']
            if not any(any(expiry > runtime.time for expiry in instance['expires']) for instance in active):
                raise ElementalPending('special_echo_source_inactive', '自然父包时点的额外伤害来源规则未激活或已到期。', identity,
                    {'source_rule_id': rule['id'], 'parent_event_id': request['event_id']})
            self.seen[identity] = request['event_id']
            key = json.dumps([request['event_id'], declaration['owner'], declaration['source_id']],
                ensure_ascii=False, separators=(',', ':')).encode('utf8')
            extra = copy.deepcopy(request)
            extra.update(event_id=request['event_id'] + ':extra:' + hashlib.sha256(key).hexdigest()[:24],
                component='额外雷击', parent_reaction_id=request['event_id'],
                contributor_sample_policy='actual_parent_contributors_post_parent_current')
            provenance = {'contract': CONTRACT, 'declaration_id': identity,
                'source_rule_id': rule['id'], 'owner': declaration['owner'], 'source_id': declaration['source_id'],
                'parent_event_id': request['event_id'], 'parent': copy.deepcopy(declaration['parent']),
                'semantics': copy.deepcopy(declaration['semantics']), 'branch': copy.deepcopy(declaration['branch']),
                'precision': copy.deepcopy(declaration['precision']), 'status': declaration['status'], 'source': declaration['source']}
            extra['extra_damage_provenance'] = provenance
            project_special_damage(runtime, extra, cycle, parent, _callbacks=False)
            row = {'type': 'special_damage_echo', 't': request['t'], 'cycle': cycle, 'event_id': extra['event_id'],
                'parent_event_id': request['event_id'], 'contributor_ids': list(request['contributor_ids']),
                'provenance': provenance, 'status': 'executed'}
            self.trace.append(copy.deepcopy(row))
            runtime.trace.append(copy.deepcopy(row))

    def boundary(self, runtime, next_cycle):
        if next_cycle != 1:
            return
        for declaration in self.declarations:
            if declaration['id'] not in self.seen:
                runtime.issues.append({'code': 'special_echo_parent_missing', 'severity': 'pending',
                    'path': f"mechanics_model.{FIELD}.{declaration['id']}",
                    'message': '完整执行窗口没有匹配到声明的实际自然父雷击，未产生额外伤害。',
                    'parent': copy.deepcopy(declaration['parent'])})

    def report(self):
        return {'contract': CONTRACT, 'declarations': copy.deepcopy(self.declarations),
                'trace': copy.deepcopy(self.trace), 'precision': 'approximate',
                'scope': 'declared_input_once_no_reaction_world_side_effects'}
