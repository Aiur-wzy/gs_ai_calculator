"""Bounded, runtime-owned post-hit order for explicitly assumed callback edges."""
from __future__ import annotations

import copy
import hashlib
import json
from itertools import pairwise

from genshin_engine import attribute_callback_multistat as multistat_callback
from genshin_engine.attribute_callback_schema import (
    ATTRIBUTE_CALLBACK_SCHEMA,
    ATTRIBUTE_CALLBACK_SCHEMA_V2,
    MULTISTAT_CONTRACT,
)
from genshin_engine.attribute_causality import (
    AttributeCallbackPlanner,
    AttributeCausalityStopped,
)
from genshin_engine.attribute_ledger import AttributeLedgerError, _component_key
from genshin_engine.attribute_stats import canonical_read
from genshin_engine.registry import BENEFIT_TYPES
from genshin_engine.rotation import _schema_errors

MAX_WORK = 500000
MAX_BYTES = 16 * 1024 * 1024


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf8')


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _fail(code, text, path='attribute_callbacks', *, status='pending'):
    raise AttributeLedgerError(code, text, path, status=status)


def _supported(rule):
    if (rule['trigger'].get('event') not in ('hit', 'elemental_reaction') or rule['trigger'].get('phase') != 'post'
            or rule['timing']['delay'] or rule['stacking'] != {'max': 1, 'add': 1, 'policy': 'refresh'}
            or any(rule.get(k) for k in ('resources', 'quota', 'resource_gate'))
            or rule['beneficiaries']['mode'] == 'next_switch' or rule['beneficiaries']['scope'] != 'locked'):
        return False
    for effect in rule['effects']:
        if BENEFIT_TYPES.get(effect['benefit_type_id'], {}).get('zone') not in ('em', 'dmg_bonus'):
            return False
        if BENEFIT_TYPES[effect['benefit_type_id']]['zone'] == 'em' and 'filter' in effect:
            return False
        value = effect['value']
        if isinstance(value, dict) and (value.get('kind'), value.get('stat'), value.get('sample')) != ('stat', 'em', 'trigger'):
            return False
    return True


def validate_bindings(model):
    declaration = model.get('attribute_callbacks')
    if declaration is None:
        return []
    errors = []
    multi_stat = declaration.get('contract') == MULTISTAT_CONTRACT if isinstance(declaration, dict) else False
    try:
        _canonical(declaration)
        _schema_errors(declaration, ATTRIBUTE_CALLBACK_SCHEMA_V2 if multi_stat else ATTRIBUTE_CALLBACK_SCHEMA, 'mechanics_model.attribute_callbacks', errors)
    except (TypeError, ValueError, ArithmeticError, RecursionError):
        errors.append({'code': 'causal_schema', 'message': '回调声明须是有限JSON。',
                       'path': 'mechanics_model.attribute_callbacks', 'severity': 'error'})
    if errors:
        return errors
    def issue(code, message, *, severity='error'):
        return [{'code': code, 'message': message, 'path': 'mechanics_model.attribute_callbacks', 'severity': severity}]
    if multi_stat and (model.get('attribute_model', {}).get('contract') != 'attribute-ledger-v4'
                      or model.get('elemental_model', {}).get('ruleset', 'none') not in ('none', 'gauge-swirl-dynamic-v3', 'gauge-multi-aura-v4')):
        return issue('causal_contract_scope', '五属性回调需要v4属性账及受支持的元素合同。')
    if not multi_stat and (model.get('elemental_model', {}).get('ruleset') != 'gauge-swirl-dynamic-v3'
            or model.get('attribute_model', {}).get('contract') != 'attribute-ledger-v2'):
        return issue('causal_contract_scope', '回调排序须显式普通v3和属性账v2。')
    if not multi_stat and model.get('application_events'):
        return issue('causal_parent_scope', '本片不将无伤害附着回调与成功父命中回调混用。', severity='pending')
    if not multi_stat and any(key in model for key in ('resource_threshold_observers', 'resource_threshold_triggers')):
        return issue('causal_observer_scope_pending', '资源阈值回调未纳入本版post取样闭合证明。', severity='pending')
    if (len(_canonical(declaration)) > 256 * 1024 or len(declaration['edges']) > 2048
            or len(declaration['assumptions']) > 2048):
        return issue('causal_declaration_limit', '回调声明超过字节上限。')
    members, rules = declaration['members'], {r['id']: r for r in model['rules']}
    assumptions = declaration['assumptions']
    if (not members or len(members) > 128 or len(set(members)) != len(members)
            or len({x['id'] for x in assumptions}) != len(assumptions)):
        return issue('causal_duplicate_identity', '成员为空、过多或成员/假设标识重复。')
    texts = [*members, *(x['id'] for x in assumptions)]
    if any(not value.strip() or len(value) > 128 for value in texts) or any(not x['note'].strip() or len(x['note']) > 4096 for x in assumptions):
        return issue('causal_declaration_limit', '回调标识或假设说明为空或过长。')
    if any(rid not in rules for rid in members):
        return issue('causal_rule_reference', '回调成员规则不存在。')
    if any(not (multistat_callback.supported(rules[rid]) if multi_stat else _supported(rules[rid])) for rid in members):
        return issue('causal_member_scope', '回调成员超出首片零延迟单层精通/增伤取样范围。', severity='pending')
    edges, used = set(), set()
    for edge in declaration['edges']:
        a, b, ref = edge['before_rule_id'], edge['after_rule_id'], edge['assumption_id']
        if a not in members or b not in members or ref not in {x['id'] for x in assumptions}:
            return issue('causal_reference', '因果边必须引用已声明成员和假设。')
        if a == b or (a, b) in edges:
            return issue('causal_duplicate_edge', '因果边自指或重复。')
        edges.add((a, b)); used.add(ref)
    if used != {x['id'] for x in assumptions}:
        return issue('causal_unused_evidence', '假设没有被声明边使用。')
    state = model.get('state_runtime')
    if state:
        payloads = []
        if state.get('schema_version') != 1:
            return issue('causal_observer_scope_pending', '生命周期版本未纳入本版因果证明。', severity='pending')
        for life in state['lifecycles']:
            if any(life[key]['event'] not in ('action_start', 'action_end', 'switch', 'condition')
                   for key in ('start', 'end_trigger') if key in life):
                return issue('causal_observer_scope_pending', '生命周期post回入未纳入本版因果证明。', severity='pending')
            payloads.extend(hook for hooks in life['hooks'].values() for hook in hooks)
        payloads.extend(timer['payload'] for timer in state['timers'])
        if any(payload['kind'] == 'dispatch_rule' and payload['ref_id'] in members for payload in payloads):
            return issue('causal_observer_scope_pending', '迁移成员不能同时作为生命周期派发目标。', severity='pending')
    pools = model.get('shared_pools')
    if pools and (pools['schema_version'] not in (1, 2) or any(not (type(o['amount']) is int or o['amount'] == 'all_available') for o in pools['operations'])):
        return issue('causal_observer_scope_pending', '资源表达式未纳入本版因果证明。', severity='pending')
    return []


class _Batch:
    pass


class AttributeCallbackRuntime:
    def __init__(self, executor):
        self.executor = executor
        self.declaration = copy.deepcopy(executor.model['attribute_callbacks'])
        self.multi_stat = self.declaration['contract'] == MULTISTAT_CONTRACT
        self.members = set(self.declaration['members'])
        self.planner = AttributeCallbackPlanner(multi_stat=self.multi_stat)
        self.work, self.output_bytes, self.trace, self.plans, self.footprints = 0, 0, [], [], {}
        self.active = None
        self.model_fingerprint = self._model_hash()

    def _charge(self, count=1):
        if self.work + count > MAX_WORK:
            self.work = MAX_WORK
            _fail('causal_runtime_work_limit', '回调执行超过累计工作量上限。')
        self.work += count

    def _model_hash(self):
        raw = _canonical(self.executor.model)
        self._charge(1 + len(raw) // 1024)
        return hashlib.sha256(raw).hexdigest()

    def _record(self, row, *, collection=None):
        stored = self._prepare_record(row)
        (self.trace if collection is None else collection).append(stored)

    def _prepare_record(self, row):
        if 'type' in row and hasattr(self, 'active_parent'):
            row = {**row, 'parent_event_id': self.active_parent['id'], 'origin': self.active_parent['origin'],
                   'time_us': self.active_parent['time']}
        size = len(_canonical(row))
        if self.output_bytes + size > MAX_BYTES:
            _fail('causal_runtime_output_limit', '回调审计超过累计字节上限。')
        self.output_bytes += size
        return copy.deepcopy(row)

    def _footprint(self, actor, basis, snapshot, *, stat='em'):
        stat, basis = canonical_read(stat, basis)
        frame_id = snapshot['attribute_frame']['frame']['id']
        receipt = self.executor.attribute_runtime.ledger.dependency_footprint(actor, stat, basis, frame_id)
        if receipt['status'] != 'ready':
            issue = receipt['issues'][0]
            _fail(issue['code'], issue['message'], issue['path'], status=receipt['status'])
        if receipt['receipt_id'] not in self.footprints:
            size = len(_canonical(receipt))
            if self.output_bytes + size > MAX_BYTES:
                _fail('causal_runtime_output_limit', '回调依赖审计超过累计字节上限。')
            self.output_bytes += size
            self.footprints[receipt['receipt_id']] = receipt
        return receipt

    def _candidate(self, rule, event):
        self._charge()
        reason = self.executor._filter_decision(rule['trigger'], event, rule['id'])
        if reason == 'match' and self.executor.time < self.executor.state[rule['id']]['cooldown']:
            reason = 'internal_cooldown'
        return {'rule_id': rule['id'], 'event': copy.deepcopy(event), 'decision': reason,
                'beneficiaries': self.executor.beneficiaries(rule, event) if reason == 'match' else []}

    def _node(self, candidate, snapshot, *, member=True, reads_enabled=True):
        if self.multi_stat:
            return multistat_callback.node(self, candidate, snapshot, member=member, reads_enabled=reads_enabled)
        rule = self.executor.rules[candidate['rule_id']]
        event = candidate['event']
        reads, writes = [], []
        for effect in rule['effects']:
            self._charge(1 + len(candidate['beneficiaries']))
            zone = BENEFIT_TYPES.get(effect['benefit_type_id'], {}).get('zone')
            value = effect['value']
            for actor in candidate['beneficiaries']:
                if reads_enabled and isinstance(value, dict) and value.get('kind') == 'stat' and value.get('stat') == 'em':
                    if value['sample'] not in ('current', 'trigger'):
                        _fail('causal_nonmember_scope', '相关历史事件表达式尚未纳入回调资格证明。', rule['id'])
                    subject = rule['owner'] if value['subject'] == 'owner' else actor
                    receipt = self._footprint(subject, value.get('basis', 'total'), snapshot)
                    reads.append({'id': _digest([effect['id'], actor, 'read']), 'actor': subject, 'stat': 'em',
                                  'basis': value.get('basis', 'total'), 'current_dependencies': receipt['current_dependencies']})
                if zone == 'em':
                    classified = self.executor.attribute_runtime.classifications.get(('rule', rule['id'], effect['id']))
                    if classified is None and member:
                        _fail('attribute_component_missing', '因果写者缺少真实属性组件身份。', rule['id'])
                    if classified is None:
                        # A potential outsider is never handed to the planner.
                        # Its absent classification remains unknown, so it can
                        # only be proved disjoint by actor/attribute identity.
                        classified = {'component': {'source_fingerprint': 'missing', 'provider': rule['owner'],
                            'part_id': rule['source_id'], 'effect_id': effect['id']}, 'status': 'pending',
                            'nonextra_inclusion': 'unknown'}
                    component = {**classified['component'], 'recipient': actor}
                    if member:
                        included = self.executor.attribute_runtime.ledger._initials.get((actor, 'em'), {}).get('included_components', [])
                        if any(_component_key(c['component']) == _component_key(component) for c in included):
                            _fail('attribute_duplicate_delivery', '因果写者重复交付已包含在初始面板的组件。', rule['id'], status='invalid')
                        if any(match.get('rule_id', match.get('effect_ref', {}).get('buff_id')) == rule['id']
                               and match.get('effect_id', match.get('effect_ref', {}).get('effect_id')) == effect['id']
                               for match in self.executor.matches):
                            _fail('causal_member_scope', '具有命中微调资格的精通写者不能作为普通属性回调。', rule['id'])
                    writes.append({'id': _digest([effect['id'], actor, 'write']), 'actor': actor, 'stat': 'em',
                        'component': component,
                        'nonextra_inclusion': classified['nonextra_inclusion'] if classified['status'] != 'pending' else 'unknown'})
        callback = 'hit_post' if event['event'] == 'hit' else 'reaction_post'
        return {'id': _digest([event['run_id'], rule['id']]), 'rule_id': rule['id'],
                'callback': {'kind': callback, 'origin': event['origin'], 'actual_run_id': event['run_id'],
                    'parent_actual_run_id': self.active_parent['run_id'], 'event_id': event['id']},
                'reads': reads, 'writes': writes, 'activation_keys': [_digest([rule['owner'], rule['source_id']])]}

    def _potential_reads(self, actor, basis, snapshot, outsiders, *, stat='em'):
        """Conservative independence screen, never a qualified read receipt.

        Include even not-yet-created current conversions from other post rules.
        Missing classifications remain potential dependencies, never excluded.
        Historical references are not rebound to the present or evaluated here.
        """
        if self.multi_stat:
            return multistat_callback.potential_reads(self, actor, basis, snapshot, outsiders, stat=stat)
        token = snapshot['attribute_frame']
        fid = token['frame']['id']
        links = []
        for node in token['contributions']:
            self._charge()
            value = node['value']
            if isinstance(value, dict) and value['input']['frame_id'] == fid:
                links.append((node['component']['recipient'], node['nonextra_inclusion'],
                              value['input']['actor'], value['input']['basis']))
        sources = [(m['actor'], m['source']) for m in token['missing']]
        for candidate in outsiders:
            rule = self.executor.rules[candidate['rule_id']]
            for effect in rule['effects']:
                self._charge(1 + len(candidate['beneficiaries']))
                if BENEFIT_TYPES.get(effect['benefit_type_id'], {}).get('zone') == 'em':
                    sources.extend((recipient, ['rule', rule['id'], effect['id']]) for recipient in candidate['beneficiaries'])
        for recipient, key in sources:
            self._charge()
            if key[0] != 'rule':
                continue  # Manual ordinary EM currently has constant values.
            rule = self.executor.rules[key[1]]
            effect = next(e for e in rule['effects'] if e['id'] == key[2])
            value = effect['value']
            if isinstance(value, dict) and (value.get('kind'), value.get('stat'), value.get('sample')) == ('stat', 'em', 'current'):
                classified = self.executor.attribute_runtime.classifications.get(tuple(key))
                inclusion = classified['nonextra_inclusion'] if classified and classified['status'] != 'pending' else 'unknown'
                subject = rule['owner'] if value['subject'] == 'owner' else recipient
                links.append((recipient, inclusion, subject, value.get('basis', 'total')))
        seen, pending = set(), [(actor, basis)]
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            self._charge(1 + len(links))
            seen.add(current)
            if current[1] == 'base':
                continue
            for recipient, inclusion, subject, source_basis in links:
                if recipient == current[0] and not (current[1] == 'nonextra' and inclusion == 'excluded'):
                    pending.append((subject, source_basis))
        return [{'actor': a, 'stat': 'em', 'basis': b} for a, b in sorted(seen) if b != 'base']

    @staticmethod
    def _related(left, right):
        if set(left['activation_keys']) & set(right['activation_keys']):
            return True
        for reader, writer in ((left, right), (right, left)):
            for read in reader['reads']:
                for dep in read['current_dependencies']:
                    if dep['basis'] == 'base':
                        continue
                    for write in writer['writes']:
                        if dep['actor'] == write['actor'] and dep['stat'] == write['stat'] and not (dep['basis'] == 'nonextra' and write['nonextra_inclusion'] == 'excluded'):
                            return True
        return False

    def prepare(self, parent, event, reaction_event=None):
        return self.prepare_packet(parent, event, [reaction_event] if reaction_event is not None else [])

    def prepare_packet(self, parent, event, reaction_events):
        """Reserve the complete real packet; claim each actual phase, commit once."""
        if not isinstance(reaction_events, list) or (not self.multi_stat and len(reaction_events) > 1):
            _fail('causal_packet_scope', '旧回调合同不接收多反应包；请使用v2强类型合同。')
        if len(reaction_events) + 1 > 256:
            _fail('causal_packet_limit', '实际回调包超过有界发生数量。')
        if self.active is not None or self._model_hash() != self.model_fingerprint:
            _fail('causal_authority', '回调模型被替换或前一批尚未结束。', status='invalid')
        ex = self.executor
        self.active_parent = parent
        events = [event] + reaction_events
        if len({e['run_id'] for e in events}) != len(events):
            _fail('causal_duplicate_identity', '实际回调发生身份重复。', status='invalid')
        # Current admitted observers cannot re-enter EM callbacks at hit/post.
        if not self.multi_stat and any(ex.model.get(key) for key in ('resource_threshold_observers', 'resource_threshold_triggers')):
            _fail('causal_observer_scope_pending', '资源阈值回调未纳入本版post取样闭合证明。')
        ex.attribute_runtime.stage(event, 'trigger')
        snapshot = ex.snapshot()
        directory, others = [], []
        for callback in events:
            for rule in sorted(ex.rules.values(), key=lambda row: row['id']):
                self._charge()
                if rule['trigger'].get('phase') != 'post' or rule['trigger'].get('event') != callback['event']:
                    continue
                candidate = self._candidate(rule, callback)
                (directory if rule['id'] in self.members else others).append(candidate)
        for candidate in directory:
            if candidate['decision'] == 'unknown_condition':
                _fail('causal_qualification_unknown', '声明回调触发条件未知，不能省去节点。', candidate['rule_id'])
        candidates = [c for c in directory if c['decision'] == 'match']
        nodes = [self._node(c, snapshot) for c in candidates]
        potential = []
        for other in others:
            if other['decision'] not in ('match', 'unknown_condition'):
                continue
            # Use potential recipients for unknown qualification; never treat it
            # as a proved absent writer when its footprints could interfere.
            if other['decision'] == 'unknown_condition':
                other['beneficiaries'] = ex.beneficiaries(ex.rules[other['rule_id']], other['event'])
            potential.append(other)
        for other in potential:
            outsider = self._node(other, snapshot, member=False, reads_enabled=False)
            requested = {dep['actor'] for member in nodes for read in member['reads']
                         for dep in read['current_dependencies'] if dep['basis'] != 'base'}
            for write in outsider['writes']:
                if write['actor'] in requested:
                    included = ex.attribute_runtime.ledger._initials.get((write['actor'], write['stat']), {}).get('included_components', [])
                    if any(_component_key(row['component']) == _component_key(write['component']) for row in included):
                        _fail('attribute_duplicate_delivery', '相关非成员写者不能重复交付已含组件。', other['rule_id'], status='invalid')
            if any(self._related(outsider, member) for member in nodes):
                _fail('causal_nonmember_interference', '相关回调必须加入受支持成员，不能提前在旧phase执行。', other['rule_id'])
            rule = ex.rules[other['rule_id']]
            for effect in rule['effects']:
                value = effect['value']
                if not isinstance(value, dict) or value.get('kind') != 'stat' or value.get('sample') != 'trigger' or (not self.multi_stat and value.get('stat') != 'em'):
                    continue
                for actor in other['beneficiaries']:
                    subject = rule['owner'] if value['subject'] == 'owner' else actor
                    deps = self._potential_reads(subject, value.get('basis', 'total'), snapshot, potential, stat=value['stat'])
                    outsider['reads'].append({'current_dependencies': deps})
            if any(self._related(outsider, member) for member in nodes):
                _fail('causal_nonmember_interference', '非成员触发取样可能读取延期写者；须加入受审成员闭包。', other['rule_id'])
        if len(nodes) > self.planner._limits['nodes']:
            _fail('causal_input_limit', '真实回调发生节点超过本次因果预算。')
        ids = {}
        for item in nodes:
            ids.setdefault(item['rule_id'], []).append(item['id'])
        edges = []
        for edge in self.declaration['edges']:
            for before in ids.get(edge['before_rule_id'], []):
                for after in ids.get(edge['after_rule_id'], []):
                    self._charge()
                    if len(edges) >= self.planner._limits['edges']:
                        _fail('causal_input_limit', '规则关系展开超过本次因果边预算。')
                    edges.append({'before': before, 'after': after, 'evidence_ref': edge['assumption_id']})
        occurrence_evidence = None
        if self.multi_stat and any(len(values) > 1 for values in ids.values()):
            occurrence_evidence = {'id': 'runtime:actual_occurrence_order', 'status': 'assumed',
                'note': '同一规则的真实回调发生沿当前执行器给出的包内顺序；不推断游戏未知先后。',
                'fingerprint': _digest([self.model_fingerprint, parent['run_id'], [e['run_id'] for e in events]])}
            for values in ids.values():
                for before, after in pairwise(values):
                    self._charge()
                    if len(edges) >= self.planner._limits['edges']:
                        _fail('causal_input_limit', '实际发生顺序超过因果边预算。')
                    edges.append({'before': before, 'after': after, 'evidence_ref': occurrence_evidence['id']})
        used = {e['evidence_ref'] for e in edges}
        evidence = [{'id': item['id'], 'status': 'assumed', 'note': item['note'],
                     'fingerprint': _digest([self.model_fingerprint, parent['run_id'], item])}
                    for item in self.declaration['assumptions'] if item['id'] in used]
        if occurrence_evidence is not None:
            evidence.append(occurrence_evidence)
        batch = {'contract': self.planner.contract, 'parent': {'origin': event['origin'],
            'actual_run_id': parent['run_id'], 'event_id': event['id'], 't_us': ex.time,
            'ruleset': ex.model.get('elemental_model', {}).get('ruleset', 'none')}, 'nodes': nodes, 'edges': edges, 'evidence': evidence}
        try:
            plan = self.planner.plan(batch)
        except AttributeCausalityStopped as error:
            raise AttributeLedgerError('causal_runtime_stopped', str(error), status='pending') from error
        self._record(plan, collection=self.plans)
        if plan['status'] != 'ready':
            issue = plan['issues'][0]
            _fail(issue['code'], issue['message'], issue['path'], status=plan['status'])
        token = _Batch()
        token.parent, token.events, token.directory = copy.deepcopy(parent), copy.deepcopy(events), directory
        token.capture_fingerprint, token.plan = _digest(snapshot['attribute_frame']), plan
        token.candidates = {node['id']: candidate for node, candidate in zip(nodes, candidates, strict=True)}
        self.active_claimed, self.active_committed = set(), False
        self.active = token
        self.token_fingerprint = self._token_hash(token)
        self._record({'type': 'callback_plan', 'parent_run_id': parent['run_id'], 'fingerprint': plan['fingerprint'],
                      'capture_fingerprint': token.capture_fingerprint, 'directory': directory})
        return token

    def _token_hash(self, token):
        raw = _canonical({key: getattr(token, key) for key in
            ('parent', 'events', 'directory', 'capture_fingerprint', 'plan', 'candidates')})
        self._charge(1 + len(raw) // 1024)
        return hashlib.sha256(raw).hexdigest()

    def claim(self, token, event, phase, parent):
        if (token is not self.active or self.active_committed or phase != 'post'
                or self._model_hash() != self.model_fingerprint or self._token_hash(token) != self.token_fingerprint):
            _fail('causal_authority', '回调phase没有本次执行对象授权。', status='invalid')
        actual = next((e for e in token.events if e['id'] == event['id'] and e['run_id'] == parent['run_id']), None)
        if (actual is None or parent['run_id'] in self.active_claimed
                or _digest(actual) != _digest({**event, 'run_id': parent['run_id']})
                or _digest(parent) != _digest({**token.parent, 'id': actual['id'], 'run_id': actual['run_id']})):
            _fail('causal_authority', '回调phase身份不符或重复执行。', status='invalid')
        self.active_claimed.add(parent['run_id'])
        for candidate in token.directory:
            if candidate['event']['run_id'] == parent['run_id'] and candidate['decision'] == 'internal_cooldown':
                rid = candidate['rule_id']
                self.executor.trace.append({'type': 'trigger_rejected', 'reason': 'internal_cooldown', 'rule_id': rid,
                    'event_id': event['id'], 't': self.executor.time / 1000000,
                    'cooldown_until': self.executor.state[rid]['cooldown'] / 1000000})
        return self.members

    def commit(self, token):
        if (token is not self.active or self.active_committed or self._model_hash() != self.model_fingerprint
                or self._token_hash(token) != self.token_fingerprint
                or self.active_claimed != {e['run_id'] for e in token.events}):
            _fail('causal_authority', '回调提交身份、模型或已认领phase不完整。', status='invalid')
        ex = self.executor
        self.active_committed = True
        for index, batch in enumerate(token.plan['batches']):
            self._charge(1 + len(batch))
            ex.attribute_runtime.stage(token.events[0], 'trigger')
            snapshot = ex.snapshot()
            # All members consume this one immutable state. Prepare numerical
            # trigger reads before any member commits; execution prefix remains
            # explicit if a later non-numerical commit fails.
            empty_qualified_batch = all(
                not token.candidates[nid]['beneficiaries']
                and 'eligibility' in ex.rules[token.candidates[nid]['rule_id']]['beneficiaries']
                for nid in batch)
            frozen = None if empty_qualified_batch else ex.freeze_panel(snapshot)
            for nid in batch:
                candidate = token.candidates[nid]
                rule = ex.rules[candidate['rule_id']]
                for effect in rule['effects']:
                    if isinstance(effect['value'], dict):
                        for actor in candidate['beneficiaries']:
                            ex.value(effect, rule, {'trigger_snapshot': frozen}, actor, candidate['event'], frozen)
            self._record({'type': 'callback_batch_prepared', 'parent_run_id': token.parent['run_id'], 'batch': index,
                          'frame_id': snapshot['attribute_frame']['frame']['id'], 'members': list(batch)})
            committed_rows = [self._prepare_record({'type': 'callback_committed', 'parent_run_id': token.parent['run_id'],
                'batch': index, 'node_id': nid, 'rule_id': token.candidates[nid]['rule_id'],
                'frame_id': snapshot['attribute_frame']['frame']['id'],
                'source_id': ex.rules[token.candidates[nid]['rule_id']]['source_id'],
                'owner': ex.rules[token.candidates[nid]['rule_id']]['owner'],
                'callback_run_id': token.candidates[nid]['event']['run_id']}) for nid in batch]
            self._charge(len(batch))
            for nid, committed_row in zip(batch, committed_rows, strict=True):
                candidate = token.candidates[nid]
                rule = ex.rules[candidate['rule_id']]
                ex._commit_callback(rule, candidate['event'], candidate['beneficiaries'], snapshot, token.parent)
                self.trace.append(committed_row)
        self.active = None

    def report(self):
        return {'contract': self.declaration['contract'], 'source_authority': 'caller_assumed_order_not_game_source_approval',
                'frame_authority': 'mechanics_execution', 'trace': copy.deepcopy(self.trace),
                'plans': copy.deepcopy(self.plans), 'footprints': copy.deepcopy(list(self.footprints.values())),
                'usage': {'execution_work': self.work, 'output_bytes': self.output_bytes,
                          'limits': {'execution_work': MAX_WORK, 'output_bytes': MAX_BYTES}, 'planner': self.planner.usage()}}
