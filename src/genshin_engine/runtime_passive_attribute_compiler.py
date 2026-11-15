"""Explicit v2 NONEXTRA sources, retaining complete legacy raw admission.

The shared parent checker is structural. Dynamic semantics have a separate
profile and fact review; this compiler never edits an old ready projection.
"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from importlib import resources

from genshin_runtime.attribute_source_semantics import verify_attribute_facts
from genshin_runtime.mechanism_semantics import finite_json

from .elemental_schema import DYNAMIC_SWIRL_RULESET
from .runtime_compiler import _escape, _hash, _Stop
from .runtime_passive_benefit_compiler import (
    _ELEMENTS,
    _PROPERTIES,
    MAX_BYTES,
    _PassiveCompiler,
    passive_benefit_compiler_caps,
)
from .runtime_passive_benefit_compiler import (
    _KNOWN as _LEGACY_KNOWN,
)

PROFILE_ID = 'inherent-ordinary-swirl-attribute-v2'
PROFILE_VERSION = 2
COMPILER_ID = 'database-inherent-swirl-attribute-v2'
COMPILER_VERSION = 2
_KNOWN = {**_LEGACY_KNOWN, 'stat_basis': 'attribute_ledger_nonextra',
          'same_proc_em_policy': 'no_same_proc_em_changes'}


@lru_cache(maxsize=1)
def _profile():
    return json.loads(resources.files('genshin_runtime').joinpath('resources/passive_attribute_profiles.json').read_text('utf8'))


def passive_attribute_compiler_caps():
    old = passive_benefit_compiler_caps()
    default = {**old['default_scenario'], 'profile_id': PROFILE_ID, 'profile_version': PROFILE_VERSION,
               'target_state_profile': DYNAMIC_SWIRL_RULESET, 'same_proc_em_policy': 'unknown'}
    fields = copy.deepcopy(old['scenario_schema']['properties'])
    for key in ('profile_id', 'profile_version', 'target_state_profile'):
        fields[key] = {'const': default[key]}
    fields.update({k: {'enum': ['unknown', v]} for k, v in _KNOWN.items()})
    return {**old, 'compiler': COMPILER_ID, 'compiler_version': COMPILER_VERSION,
        'profile_id': PROFILE_ID, 'profile_version': PROFILE_VERSION, 'default_scenario': default,
        'scenario_schema': {'type': 'object', 'required': list(default), 'additionalProperties': False, 'properties': fields},
        'max_input_nodes': 65536,
        'target_state_profile': DYNAMIC_SWIRL_RULESET, 'required_attribute_contract': 'attribute-ledger-v2',
        'supported': ['普通冰水扩散回调读取明确NONEXTRA属性账', '每次触发冻结转换值与受益者自身快照分域',
                      '完整原始父图、参数装配与逐部件属性事实'],
        'unsupported': ['同proc精通写者竞争先后', '未证NONEXTRA初始面板或动态分类', '来源不明风附着与武器扩散',
                        '火雷/星扩散或传播', 'EM共享来源装配与全局变量'],
        'evidence': copy.deepcopy(_profile()['semantics']), 'mapping_fingerprint': _hash(_profile()),
        'confirmation_policy': '新合同显式采用；App核验当前父链、v2属性合同与同proc依赖；来源确认不补全未知属性事实。'}


class _AttributeCompiler(_PassiveCompiler):
    def __init__(self, profile, owner, source_id, scenario):
        super().__init__(profile, owner, source_id, scenario)
        self.attribute_profile = copy.deepcopy(_profile())
        self.attribute_proof = None
        self.duration_symbols = set()

    def scenario_check(self):
        schema = passive_attribute_compiler_caps()['scenario_schema']
        self.fields(self.scenario, schema['required'], (), '/scenario')
        for key, spec in schema['properties'].items():
            value = self.scenario[key]
            if 'const' in spec:
                self.exact(value, spec['const'], '/scenario/' + key)
            elif 'enum' in spec and value not in spec['enum']:
                self.fail('invalid_scenario', '/scenario/' + key, '非法新来源场景枚举。', severity='error')
        if self.scenario['status'] == 'pending':
            self.fail('scenario_evidence_pending', '/scenario/status', '新来源场景尚未核实。')
        for key, expected in _KNOWN.items():
            if self.scenario[key] != expected:
                self.fail('scene_input_required', '/scenario/' + key, '新属性来源的范围、初态、账本或因果口径须明确。')
        for key, maximum in (('source', 4096), ('target_id', 128)):
            if type(self.scenario[key]) is not str or not self.scenario[key].strip() or len(self.scenario[key]) > maximum:
                self.fail('scenario_evidence_required', '/scenario/' + key, '缺少有限非空场景依据或目标。')

    def interpret(self, entry, body):
        self.fields(self.attribute_profile, ('schema_version', 'profile_id', 'profile_version',
            'legacy_mapping_fingerprint', 'facts', 'reviews', 'evidence', 'semantics'), (), '/attribute_profile')
        for key, expected in (('schema_version', 1), ('profile_id', PROFILE_ID), ('profile_version', PROFILE_VERSION)):
            self.exact(self.attribute_profile[key], expected, '/attribute_profile/' + key)
        self.exact(self.attribute_profile['legacy_mapping_fingerprint'], _hash(self.profile), '/attribute_profile/legacy_mapping_fingerprint')
        self.defaults(body['LKLPDIMKLKI'], '/ability/LKLPDIMKLKI')
        self.exact(body['isDynamicAbility'], True, '/ability/isDynamicAbility', '固定队伍/培养/装备，无外部移除、恢复或命中停滞。')
        attach = body['onAdded'][0]
        self.exact(len(body['onAdded']), 1, '/ability/onAdded/count')
        self.exact(attach['$type'], 'AttachModifier', '/ability/onAdded/0/$type')
        handler_name = attach['modifierName']
        handler = body['modifiers'][handler_name]
        hp = '/modifiers/' + _escape(handler_name)
        self.branch(entry, '/onAdded/0', 'attachment', status='projected', execution_status='ready',
                    reason='固定来源挂载；onAdded.doOffStage不等同于后续反应前台限制')
        self.assumptions.extend({'status': 'reference_or_declared_scope', 'assumption': value}
                               for value in self.attribute_profile['semantics'].values())
        seen = set()
        for i, listener in enumerate(handler['modifierMixins']):
            path = hp + f'/modifierMixins/{i}'
            self.exact(listener['$type'], 'DoActionByElementReactionMixin', path + '/$type')
            self.exact(listener['entityTypes'], ['Monster'], path + '/entityTypes')
            self.exact(listener['range'], 20, path + '/range', '原目标处于来源范围；不推断原字段单位。')
            reactions = listener['reactionTypes']
            ordinary = [r for r in reactions if r.startswith('Swirl')]
            if len(ordinary) != 1 or ordinary[0][5:] not in _ELEMENTS:
                self.fail('unsupported_reaction', path, '未知反应枚举不能投影。')
            raw_element = ordinary[0][5:]
            self.exact(reactions, [ordinary[0]] + (['StarSwirlIce'] if raw_element == 'Ice' else []), path + '/reactionTypes')
            actions = listener['actions']
            self.exact(len(actions), 2, path + '/actions/count')
            for j, action in enumerate(actions):
                ap = path + f'/actions/{j}'
                self.exact(action['$type'], 'RemoveUniqueModifier' if j == 0 else 'ApplyModifier', ap + '/$type')
                self.exact(action['target'], 'AllPlayerAvatars', ap + '/target', '单人固定全队且满足受益范围。')
                self.exact(action['doOffStage'], True, ap + '/doOffStage')
                self.exact(action['predicates'], [{'$type': 'ByTargetPositionToSelfPosition', 'target': 'Target',
                    'value': 40, 'logic': 'LesserOrEqual'}], ap + '/predicates', '全部实际受益者满足距离谓词。')
            name = actions[1]['modifierName']
            self.exact(actions[0]['modifierName'], name, path + '/actions/0/modifierName')
            modifier = body['modifiers'][name]
            self.exact(modifier['stacking'], 'Unique', path + '/modifier/stacking')
            self.exact(modifier['isUnique'], True, path + '/modifier/isUnique')
            self.exact(list(modifier['properties']), [_PROPERTIES[raw_element]], path + '/modifier/properties')
            expression = modifier['properties'][_PROPERTIES[raw_element]]
            if type(expression) is not list or len(expression) != 3 or expression[1:] != ['FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY', 'MUL']:
                self.fail('unsupported_expression', path, '原始完整NONEXTRA单层乘法未闭合。')
            factor = self.param(expression[0], path + '/factor')
            duration = self.time(self.param(modifier['duration'], path + '/duration'), path + '/duration')
            self.duration_symbols.add(modifier['duration'])
            element = _ELEMENTS[raw_element]
            if element in seen:
                self.fail('duplicate_element', path, '重复元素监听不能静默合并。')
            seen.add(element)
            supported = element in ('hydro', 'cryo')
            if supported:
                pointer = self.normalization.raw_pointer(entry['payload'],
                    '/modifiers/' + _escape(name) + '/properties/' + _PROPERTIES[raw_element], kind='ability')
                self.bonuses[element] = (factor, duration, path, pointer)
                self.unique_keys.append(name)
            self.branch(entry, path, 'reaction_listener', element=element,
                status='projected' if supported else 'excluded_by_scope', execution_status='ready' if supported else 'unsupported',
                excluded_reactions=[r for r in reactions if r != ordinary[0]], reason='普通冰水范围；原始其他分支完整留存')
            self.branch(entry, '/modifiers/' + _escape(name), 'recipient_modifier', element=element,
                value_factor=factor, duration=duration, status='projected' if supported else 'excluded_by_scope',
                execution_status='ready' if supported else 'unsupported', raw_stat='FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY',
                execution_stat='em', basis='nonextra', reason='显式v2属性账；触发冻结与受益者自身快照分别处理')
        if set(self.bonuses) != {'hydro', 'cryo'}:
            self.fail('incomplete_projection', hp, '必须原子取得两条已批准冰水分支。')
        self._attribute_facts(entry)

    def _attribute_facts(self, entry):
        claims = [f['claim'] for f in self.attribute_profile['facts'] if f['kind'] == 'attribute_read']
        ref_by_symbol = {c['parameter_symbol']: c['parameter_ref'] for c in claims}
        if len(ref_by_symbol) != 1 or len(self.duration_symbols) != 1 or set(ref_by_symbol) & self.duration_symbols:
            self.fail('attribute_parameter_roles', '/attribute_profile/facts', '参数用途必须唯一完整装配。')
        roles = {**{k: (v, 'ratio_per_em') for k, v in ref_by_symbol.items()},
                 **{k: ('passive.duration', 'seconds') for k in self.duration_symbols}}
        if set(roles) != set(self.parameters):
            self.fail('attribute_parameter_roles', '/parameters', '存在无用途或未装配参数。')
        parent_hashes = {r['kind']: r['node_sha256'] for r in self.raw_sources}
        parameters = []
        for symbol, (ref, unit) in sorted(roles.items()):
            row = {'ref': ref, 'symbol': symbol, 'value': self.parameters[symbol], 'unit': unit,
                **copy.deepcopy(self.parameter_sources[symbol]), 'source_selection': copy.deepcopy(self.bundle['selection']),
                'source_fingerprint': self.bundle['source_fingerprint'], 'unlock_fingerprint': self.bundle['unlock_fingerprint'],
                'raw_parent_hashes': parent_hashes}
            row['fingerprint'] = _hash(row)
            parameters.append(row)
        nodes = [{'id': row['kind'], **{k: copy.deepcopy(row[k]) for k in
            ('source_path', 'source_sha256', 'json_pointer', 'node_sha256')},
            'payload': copy.deepcopy(row['payload' if row['kind'] == 'ability' else 'operations' if row['kind'] == 'talent' else 'record'])}
            for row in self.raw_sources]
        self.attribute_proof = verify_attribute_facts(self.attribute_profile['facts'], nodes=nodes, parameters=parameters,
            evidence=self.attribute_profile['evidence'], reviews=self.attribute_profile['reviews'])
        if {(r['rule_key'], r['effect_id']) for r in self.attribute_proof['reads']} != {('hydro', 'value'), ('cryo', 'value')}:
            self.fail('attribute_fact_scope', '/attribute_profile/facts', '属性事实范围与有效监听分支不一致。')
        for row in self.attribute_proof['reads']:
            factor, _, _, pointer = self.bonuses[row['rule_key']]
            claim = next(c for c in claims if c['rule_key'] == row['rule_key'])
            if (claim['pointer'] != pointer or row['value']['factor'] != factor or row['value']['basis'] != 'nonextra'
                    or row['benefit_type_id'] != 'benefit.dmg_bonus.' + row['rule_key']):
                self.fail('attribute_fact_scope', '/attribute_profile/facts', '事实与当前实际分支/参数读取不一致。')

    def materialize(self):
        for read in self.attribute_proof['reads']:
            element = read['rule_key']
            _, duration, path, _ = self.bonuses[element]
            pid = 'passive.dmg_bonus.' + element
            rid = 'passive.attr.' + _hash({'source_id': self.source_id, 'path': path, 'profile': PROFILE_ID})[:24]
            rule = {'id': rid, 'name_zh': {'hydro': '扩散触发水元素增伤', 'cryo': '扩散触发冰元素增伤'}[element],
                'owner': self.owner, 'source_id': self.source_id + ':' + pid,
                'trigger': {'event': 'elemental_reaction', 'phase': 'post', 'characters': [self.owner], 'reaction_types': ['swirl'],
                            'reaction_elements': [element], 'owner_presence': 'any'},
                'beneficiaries': {'mode': 'team', 'scope': 'locked'},
                'timing': {'delay': 0, 'duration': duration, 'cooldown': 0, 'cooldown_anchor': 'success'},
                'stacking': {'max': 1, 'add': 1, 'policy': 'refresh'},
                'effects': [{'id': read['effect_id'], 'benefit_type_id': read['benefit_type_id'],
                             'domain': read['domain'], 'value': copy.deepcopy(read['value'])}],
                'status': self.scenario['status'], 'source': self.scenario['source']}
            self.rules.append(rule)
            self.initial.append({'rule_id': rid, 'cooldown_remaining': 0, 'instances': []})
            part = self.part({'kind': 'mechanics', 'rule_id': rid}, {'kind': 'mechanics'}, rule['name_zh'])
            part['part_id'] = pid
            part['conditions'][0]['evidence'][0].update(profile_id=PROFILE_ID, profile_version=PROFILE_VERSION)
            self.parts.append(part)


def compile_passive_attribute_source(profile, *, owner, source_id, scenario):
    """Pure fresh source projection. Only App can adopt the current context."""
    out = {'compiler': COMPILER_ID, 'compiler_version': COMPILER_VERSION, 'profile_id': PROFILE_ID,
        'profile_version': PROFILE_VERSION, 'status': 'pending', 'execution_status': 'pending', 'projection_status': 'pending',
        'whole_source_status': 'partial', 'gameplay_semantics_status': 'unverified', 'parts': [], 'rules': [],
        'initial_rules': [], 'unique_keys': [], 'branches': [], 'raw_sources': [], 'provenance': {}, 'issues': [],
        'semantic_assumptions': [], 'source_identity': {}, 'source_fingerprint': None, 'unlock_fingerprint': None,
        'assumptions_fingerprint': None, 'execution_fingerprint': None, 'attribute_evidence': None,
        'execution_binding': {'kind': 'elemental_reaction', 'ruleset': DYNAMIC_SWIRL_RULESET,
            'attribute_contract': 'attribute-ledger-v2', 'reaction_types': ['swirl'], 'reaction_elements': ['hydro', 'cryo'],
            'source_scope': 'original_target', 'provider_presence': 'any',
            'eligibility': 'fresh_calibrated_c0_skill_parent_required_by_app', 'acceptance': 'app_actual_parent_scope',
            'raw_stat': 'FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY', 'execution_stat': 'em', 'stat_basis': 'nonextra',
            'sample': 'trigger', 'beneficiary_domain': 'self', 'same_proc_em_policy': 'no_same_proc_em_changes'}}
    c = _AttributeCompiler(profile, owner, source_id, scenario)
    try:
        finite_json([profile, owner, source_id, scenario], MAX_BYTES)
        c.build()
        c.scenario_check()
        c.materialize()
        out.update(source_identity=copy.deepcopy(profile['source_bundle']['source_identity']),
            source_fingerprint=profile['source_fingerprint'], unlock_fingerprint=profile['unlock_fingerprint'])
        assumptions = {'compiler': COMPILER_ID, 'version': COMPILER_VERSION, 'profile': _hash(c.attribute_profile),
            'source': out['source_fingerprint'], 'unlock': out['unlock_fingerprint'], 'owner': owner,
            'source_id': source_id, 'scenario': scenario, 'attribute_proof': c.attribute_proof['fingerprint']}
        out['assumptions_fingerprint'] = _hash(assumptions)
        reads = []
        for rule in c.rules:
            element = rule['trigger']['reaction_elements'][0]
            proof = next(r for r in c.attribute_proof['reads'] if r['rule_key'] == element)
            row = {'rule_id': rule['id'], 'source_id': rule['source_id'], 'provider': owner,
                'part_id': 'passive.dmg_bonus.' + element, 'effect_id': 'value', 'stat': 'em', 'basis': 'nonextra',
                'sample': 'trigger', 'output_domain': 'self', 'fact_refs': proof['fact_refs'],
                'parameter_ref': proof['parameter_ref'], 'proof_fingerprint': c.attribute_proof['fingerprint']}
            row['fingerprint'] = _hash(row)
            reads.append(row)
        evidence = {'contract': 'database-attribute-source-v1', 'required_contract': 'attribute-ledger-v2',
            'verification_scope': c.attribute_proof['verification_scope'],
            'profile_id': PROFILE_ID, 'profile_version': PROFILE_VERSION, 'profile_fingerprint': _hash(c.attribute_profile),
            'proof_fingerprint': c.attribute_proof['fingerprint'], 'facts': c.attribute_proof['facts'],
            'parameters': c.attribute_proof['parameters'], 'evidence': c.attribute_proof['evidence'],
            'nodes': c.attribute_proof['nodes'], 'reads': reads, 'writers': []}
        out['attribute_evidence'] = evidence
        out['provenance'] = {'compiler': COMPILER_ID, 'compiler_version': COMPILER_VERSION,
            'profile_id': PROFILE_ID, 'profile_version': PROFILE_VERSION, 'source_fingerprint': out['source_fingerprint'],
            'unlock_fingerprint': out['unlock_fingerprint'], 'parameters': c.parameters,
            'parameter_bindings': c.parameter_sources, 'field_usage': c.usage,
            'local_mapping_fingerprint': _hash(c.profile), 'attribute_mapping_fingerprint': _hash(c.attribute_profile),
            'source_byte_evidence': copy.deepcopy(c.profile['files']), 'semantics': copy.deepcopy(c.attribute_profile['semantics']),
            'reference_sources': copy.deepcopy(c.profile['reference_sources'])}
        out['execution_binding']['target_id'] = scenario['target_id']
        out.update(parts=c.parts, rules=c.rules, initial_rules=c.initial, unique_keys=c.unique_keys,
            execution_fingerprint=_hash({'assumptions': out['assumptions_fingerprint'], 'parts': c.parts,
                'rules': c.rules, 'initial_rules': c.initial, 'attribute_evidence': evidence}))
    except _Stop:
        pass
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, RecursionError, StopIteration) as exc:
        c.issues.append({'code': 'invalid_attribute_source_or_scenario', 'path': '/', 'severity': 'error', 'message': str(exc)[:1024]})
    status = 'invalid' if any(i.get('severity') == 'error' for i in c.issues) else 'pending' if c.issues else 'ready'
    out.update(status=status, execution_status=status, projection_status=status, issues=c.issues[:128],
        branches=c.branches, raw_sources=c.raw_sources, semantic_assumptions=c.assumptions,
        gameplay_semantics_status='declared_not_verified' if status == 'ready' else 'unverified',
        coverage_certificate={'kind': 'ordinary_swirl_attribute_projection_certificate', 'source_complete': False,
            'projection_complete': status == 'ready', 'projection_status': status, 'gameplay_verified': False,
            'reaction_scope': 'ordinary_hydro_cryo_original_target', 'actual_parent_eligibility': 'checked_by_app'})
    if status != 'ready':
        out.update(parts=[], rules=[], initial_rules=[], unique_keys=[], execution_fingerprint=None, attribute_evidence=None)
    try:
        finite_json(out, MAX_BYTES)
    except (ValueError, TypeError, OverflowError, RecursionError):
        out.update(status='invalid', execution_status='invalid', projection_status='invalid', parts=[], rules=[], initial_rules=[],
            unique_keys=[], branches=[], raw_sources=[], provenance={}, attribute_evidence=None,
            source_identity={}, semantic_assumptions=[], execution_fingerprint=None,
            issues=[{'code': 'response_too_large', 'severity': 'error', 'message': '来源完整回执超限；未交付部分规则。'}])
        out['coverage_certificate'].update(projection_complete=False, projection_status='invalid')
    return copy.deepcopy(out)
