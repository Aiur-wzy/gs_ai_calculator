"""Runtime-owned immutable EM frames; no game/source authority is inferred here."""
from __future__ import annotations

import copy
import hashlib
import json

from genshin_engine.attribute_budget import (
    declaration_capacity_issue,
    validate_calculation_options,
)
from genshin_engine.attribute_graph import GRAPH_CONTRACT, AttributeGraphLedger
from genshin_engine.attribute_ledger import (
    AttributeLedger,
    AttributeLedgerError,
    _bounded_text,
    _component_key,
    _issue,
    attribute_schema_error,
)
from genshin_engine.attribute_multistat_graph import AttributeMultiStatProjectionLedger
from genshin_engine.attribute_multistat_native import (
    prepare_initials as prepare_multistat_initials,
)
from genshin_engine.attribute_stat_native import effect_output, prepare_initials
from genshin_engine.attribute_stat_projection import AttributeStatProjectionLedger
from genshin_engine.attribute_stats import CONTRACT as MULTISTAT_CONTRACT
from genshin_engine.attribute_stats import STATS, canonical_read
from genshin_engine.attribute_stats import effect_output as multistat_effect_output
from genshin_engine.beneficiary_selection import effective_recipient
from genshin_engine.registry import BENEFIT_TYPES


def _key(ref):
    return (ref['kind'], ref.get('rule_id', ref.get('buff_id')), ref['effect_id'])


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class _RuntimeGraphLedger(AttributeGraphLedger):
    """Runtime missing-source guards apply throughout recursively read closures."""
    def __init__(self, declaration, missing, *, limits=None):
        self.runtime_missing = missing
        super().__init__(declaration, limits=limits)

    def _context_issue(self, query, frame):
        issue = super()._context_issue(query, frame)
        if issue:
            return issue
        if query['basis'] != 'base':
            missing = [item for item in self.runtime_missing.get(frame['id'], []) if item['actor'] == query['actor'] and item.get('stat', 'em') == query['stat']]
            if missing:
                active = self._active.get((frame['id'], query['actor'], query['stat']), [])
                included = self._initials.get((query['actor'], query['stat']), {}).get('included_components', [])
                if not self._charge_query(len(missing) + len(active) + len(included)):
                    return _issue('attribute_work_limit', '属性来源资格核对超过累计工作量限制', frame['id'], status='pending')
                static = {_component_key(item['component']) for item in included}
                identities = {(_component_key(item['component']), item['instance_id'], item['layer_id']) for item in active}
                # Unsupported values still retain source/layer identity: legal
                # NONEXTRA pruning cannot hide a second delivery of a baseline.
                for item in missing:
                    if 'component' not in item:
                        continue
                    identity = (_component_key(item['component']), item['instance_id'], item['layer_id'])
                    if identity[0] in static or identity in identities:
                        return _issue('attribute_duplicate_delivery', '未支持的写者仍不能重复交付已含组件或实例层', frame['id'])
                    identities.add(identity)
            for item in missing:
                if item['actor'] == query['actor']:
                    if query['basis'] == 'nonextra' and item.get('inclusion') == 'excluded':
                        continue
                    return _issue(item.get('code', 'attribute_component_missing'),
                                  '属性闭包包含缺少来源分类或本片不支持的属性写入', frame['id'], status='pending')


class _RuntimeStatGraphLedger(_RuntimeGraphLedger, AttributeStatProjectionLedger):
    """Use the same lazy missing-source guard with the v3 projection graph."""


class _RuntimeMultiStatGraphLedger(_RuntimeGraphLedger, AttributeMultiStatProjectionLedger):
    """V4 uses the same qualified source guard without widening old v3."""

    def __init__(self, *args, **kwargs):
        self.condition_nodes = {}
        super().__init__(*args, **kwargs)

    def _node_qualification_issue(self, node, query):
        reference = node.get('condition_ref')
        if reference is None:
            return super()._node_qualification_issue(node, query)
        expected = self.condition_nodes.get(reference)
        if (query['purpose'] != 'damage_projection' or query['basis'] != 'total'
                or query['frame'].get('frame_kind') != 'damage_projection'):
            return _issue('attribute_context_unsupported', '目标条件属性只准当前命中读取，不能成为普通可分享面板', node['id'], status='pending')
        if (expected is None or {k: v for k, v in node.items() if k != 'id'} != expected['node']
                or query['actor'] != expected['actor']
                or query['frame']['context']['actual_event_run_id'] != expected['actual_event_run_id']):
            return _issue('attribute_condition_source', '条件属性缺少本次真实执行回执或内容不符', node['id'])
        return None

    @staticmethod
    def _application_base(node, query):
        if node.get('condition_ref') is not None:
            return {'actor': node['component']['recipient'], 'stat': node['stat'], 'basis': 'base',
                    'frame_id': query['frame']['context']['current_frame_id']}
        return AttributeMultiStatProjectionLedger._application_base(node, query)


class AttributeRuntime:
    def __init__(self, executor):
        self.executor = executor
        model = executor.model['attribute_model']
        options = executor.model.get('execution_options', {'schema_version':1})
        try:
            self.budget_policy = validate_calculation_options(options)
        except ValueError as exc:
            raise AttributeLedgerError('attribute_budget_invalid', str(exc), 'execution_options') from exc
        limits = self.budget_policy['effective']
        self.five_stats = model['contract'] == MULTISTAT_CONTRACT
        self.multistat = self.five_stats or model['contract'] == 'attribute-ledger-v3'
        self.graph = self.multistat or model['contract'] == GRAPH_CONTRACT
        self.contract = model['contract']
        from genshin_engine.attribute_budget import limits_for_schema
        from genshin_engine.attribute_contract import attribute_model_schema
        from genshin_engine.rotation import _schema_errors
        errors = []
        _schema_errors(model, limits_for_schema(attribute_model_schema(), limits), 'attribute_model', errors)
        if errors:
            raise attribute_schema_error(errors)
        byte_count = len(json.dumps(model, ensure_ascii=False).encode())
        if byte_count > limits['declaration_bytes']:
            issue = declaration_capacity_issue('declaration_bytes',byte_count,limits['declaration_bytes'],'attribute_model')
            raise AttributeLedgerError(issue['code'],issue['message'],issue['path'],status=issue['severity'],
                **{k:v for k,v in issue.items() if k not in ('code','message','path','severity')})
        _bounded_text(model, max_depth=limits['declaration_depth'])
        self.declarations = copy.deepcopy(model)
        self.classifications, self.receipts, self.adopted, self.frame_routes = {}, {}, set(), {}
        self.epoch, self.context = 0, {'origin': 0, 'run_id': '0:initial', 'event_id': 'initial', 'phase': 'initial'}
        self.actual_event_run_id = '0:initial'
        self.condition_sources = {}
        self.observation, self.missing_frames = None, {}
        chars = {c['id']: c for c in executor.template['characters']}
        bases = {}
        if not self.multistat:
            for basis in model['initial_basis']:
                if basis['actor'] in bases or basis['actor'] not in chars:
                    raise AttributeLedgerError('attribute_basis_reference', '初始属性声明角色不存在或重复')
                bases[basis['actor']] = basis
        for item in model['effect_classifications']:
            key = _key(item['effect_ref'])
            if key in self.classifications:
                raise AttributeLedgerError('attribute_duplicate_classification', '属性贡献分类引用重复')
            kind, ident, eid = key
            source = (executor.rules.get(ident) if kind == 'rule' else
                      next((b for b in executor.template.get('buffs', []) if b['id'] == ident), None))
            effect = next((e for e in source['effects'] if e.get('id') == eid), None) if source else None
            zone = (BENEFIT_TYPES.get(effect['benefit_type_id'], {}).get('zone') if kind == 'rule' else effect.get('zone')) if effect else None
            owner = source.get('owner') if kind == 'rule' and source else source.get('extensions', {}).get('owner') if source else None
            output = self.output(effect, kind) if self.multistat and effect else None
            supported = output is not None if self.multistat else zone == 'em'
            if self.multistat and not self.five_stats and output and output['stat'] == 'atk' and item['nonextra_inclusion'] != 'unknown':
                raise AttributeLedgerError('attribute_classification_scope', '本片ATK不开放NONEXTRA分类', str(key))
            if not supported or item['component']['provider'] != owner or item['component']['effect_id'] != eid:
                raise AttributeLedgerError('attribute_component_reference', '属性分类须指向受支持真实收益并与来源提供者和收益标识一致', str(key))
            self.classifications[key] = copy.deepcopy(item)
        if self.five_stats:
            initials = prepare_multistat_initials(executor.template, model,
                energy_baselines={actor: facts['energy_recharge'] for actor, facts in executor.data['chars'].items()}, limits=limits)
        elif self.multistat:
            initials = prepare_initials(executor.template, model, limits=limits)
        else:
            initials = []
            for actor, char in chars.items():
                if 'em' not in char['stats']:
                    continue
                basis = bases.get(actor, {})
                components = [{**copy.deepcopy(c), 'component': {**c['component'], 'recipient': actor}}
                              for c in basis.get('included_components', [])]
                if any(c['component']['provider'] not in chars for c in components):
                    raise AttributeLedgerError('attribute_component_reference', '已含面板组件提供者不在当前队伍', actor)
                initial = {'actor': actor, 'stat': 'em', 'total': char['stats']['em'],
                           'source': '当前输入角色面板；其余基底依据单独声明', 'included_components': components}
                for name in ('nonextra', 'base'):
                    declaration = basis.get(name, {})
                    if declaration.get('status') == 'pending' or declaration.get('mode', 'unknown') == 'unknown':
                        continue
                    if declaration['mode'] == 'panel_total':
                        if any(c['nonextra_inclusion'] == 'unknown' or (c['nonextra_inclusion'] == 'excluded' and c['value'] != 0) for c in components):
                            raise AttributeLedgerError('attribute_panel_total_conflict', '已含面板组成不支持全额NONEXTRA声明', actor)
                        initial[name] = initial['total']
                    else:
                        initial[name] = declaration['value']
                initials.append(initial)
        self.actors = {i['actor'] for i in initials}
        self.initial_keys = {(i['actor'], i['stat']) for i in initials}
        declaration = {'contract': model['contract'], 'initials': initials, 'contributions': [], 'frames': []}
        self.ledger = (_RuntimeMultiStatGraphLedger(declaration, self.missing_frames, limits=limits) if self.five_stats else
                       _RuntimeStatGraphLedger(declaration, self.missing_frames, limits=limits) if self.multistat else
                       _RuntimeGraphLedger(declaration, self.missing_frames, limits=limits) if self.graph else AttributeLedger(declaration, limits=limits))

    def output(self, effect, kind):
        if self.five_stats:
            return multistat_effect_output(effect, kind=kind)
        if self.multistat:
            return effect_output(effect, kind=kind)
        zone = BENEFIT_TYPES.get(effect['benefit_type_id'], {}).get('zone') if kind == 'rule' else effect.get('zone')
        return {'stat': 'em'} if zone == 'em' else None

    def supports_read(self, stat):
        if self.five_stats:
            return canonical_read(stat)[0] in STATS
        return stat == 'em' or self.multistat and stat in ('atk', 'base_atk')

    def stage(self, event, phase, *, observation=False):
        self.epoch += 1
        origin = event.get('origin', 0)
        actual = event.get('run_id', f"{origin}:{event['id']}")
        # The queue's opaque dispatch UID is not its stable route/event name.
        # Keep the real token while qualifying pure-ledger identity by origin.
        qualified = actual if actual.startswith(f'{origin}:') else f'{origin}:{actual}'
        self.actual_event_run_id = actual
        self.context = {'origin': origin, 'run_id': qualified, 'event_id': event['id'], 'phase': phase}
        self.observation = ({'kind': 'executed_event', 'event_id': event['id'], 'origin': origin,
                             'actual_event_run_id': actual} if observation else None)

    def _expression(self, expression, actor, rule, sample, frame_id):
        subject = rule['owner'] if expression['subject'] == 'owner' else actor
        fid = frame_id
        if expression['sample'] == 'trigger':
            fid = sample['attribute_frame']['frame']['id'] if sample else 'missing:trigger:'+_digest([rule['id'], actor])
        elif expression['sample'] == 'event':
            observed = self.executor.snapshots.get((self.context['origin'], expression['event_id']))
            fid = observed['attribute_frame']['frame']['id'] if observed else 'missing:event:'+_digest([self.context['origin'], expression['event_id']])
        stat = expression['stat'] if self.multistat else 'em'
        basis = expression.get('basis', 'total')
        if self.five_stats:
            stat, basis = canonical_read(stat, basis)
        elif stat == 'base_atk':
            stat, basis = 'atk', 'base'
        return {'kind': 'linear_stat', 'input': {'actor': subject, 'stat': stat,
                'basis': basis, 'frame_id': fid},
                **{k: expression[k] for k in ('factor', 'add', 'min', 'max') if k in expression}}

    def capture(self, snapshot):
        """Capture source facts only; never evaluate an unrelated actor/basis."""
        nodes, missing = [], []
        frame_id = 'frame:'+str(self.epoch)
        self.ledger.reserve_capture_work(1)

        def append(key, actor, value, instance, layer, constraints, *, domain='self', rule=None, sample=None, output=None):
            self.ledger.reserve_capture_work(1)
            output = output or {'stat': 'em'}
            stat = output['stat']
            if (actor, stat) not in self.initial_keys:
                return
            classified = self.classifications.get(key)
            if classified is None:
                missing.append({'actor': actor, 'source': list(key), **({'stat': stat} if self.multistat else {})})
                return
            component = {**classified['component'], 'recipient': actor}
            metadata = {}
            if isinstance(value, dict) and value.get('kind') == 'health':
                from genshin_engine.health_mechanics import read_health_expression
                try:
                    value = read_health_expression(self.executor, value, rule['owner'], actor,
                        instance={'trigger_snapshot': sample}, snapshot=snapshot, event=self.context)
                except AttributeLedgerError as exc:
                    missing.append({'actor': actor, 'source': list(key), **({'stat': stat} if self.multistat else {}),
                        'code': exc.issue['code'], 'component': component, 'instance_id': instance, 'layer_id': layer,
                        'inclusion': classified['nonextra_inclusion'] if classified['status'] != 'pending' else 'unknown'})
                    return
            if self.graph:
                metadata['output_domain'] = domain
                if isinstance(value, dict):
                    if not self.supports_read(value.get('stat')):
                        missing.append({'actor': actor, 'source': list(key), **({'stat': stat} if self.multistat else {}), 'code': 'attribute_expression_unsupported',
                                        'component': component, 'instance_id': instance, 'layer_id': layer,
                                        'inclusion': classified['nonextra_inclusion'] if classified['status'] != 'pending' else 'unknown'})
                        return
                    if value['sample'] == 'event':
                        metadata['sample_binding'] = {'mode': 'event', 'event_id': value['event_id'], 'origin_scope': 'evaluation_event'}
                    value = self._expression(value, actor, rule, sample, frame_id)
            node = {'component': component, 'instance_id': instance, 'layer_id': layer, **output,
                    'value': value, 'nonextra_inclusion': classified['nonextra_inclusion'] if classified['status'] != 'pending' else 'unknown',
                    'visibility': 'attribute' if not constraints else 'damage_context', 'constraints': constraints, **metadata}
            node['id'] = ('attribute:' if self.multistat else 'em:') + _digest(node)
            nodes.append(node)

        for buff in self.executor.manual_buffs(snapshot['time']):
            for effect in buff['effects']:
                output = self.output(effect, 'manual_buff')
                if output is None:
                    continue
                targets = [buff['target']] if buff.get('target') else list(self.actors)
                constraints = ['filter'] if effect.get('applies_to', {}).get('mode', 'all') != 'all' else []
                for actor in targets:
                    if buff.get('scope', 'team') == 'frontline' and snapshot['frontline'] != actor:
                        continue
                    append(('manual_buff', buff['id'], effect['id']), actor, effect['value'],
                           'manual:'+_digest([buff['id'], buff['start'], buff['end']]), '0', constraints, output=output)
        for rid, state in snapshot['state'].items():
            rule = self.executor.rules[rid]
            for inst in state['instances']:
                for effect in rule['effects']:
                    output = self.output(effect, 'rule')
                    if output is None:
                        continue
                    constraints = [key for key in ('quota', 'resource_gate') if rule.get(key)]
                    if 'filter' in effect:
                        constraints.append('filter')
                    if any(m.get('rule_id', m.get('effect_ref', {}).get('buff_id')) == rid
                           and m.get('effect_id', m.get('effect_ref', {}).get('effect_id')) == effect['id'] for m in self.executor.matches):
                        constraints.append('hit_match')
                    for actor in inst['beneficiaries']:
                        if not effective_recipient(rule, inst, actor, snapshot['frontline']):
                            continue
                        for layer, sample in zip(inst['attribute_layers'], inst['layer_snapshots'], strict=True):
                            append(('rule', rid, effect['id']), actor, effect['value'], inst['instance_id'], layer, constraints,
                                   domain=effect['domain'], rule=rule, sample=sample, output=output)
        if self.five_stats:
            # Explicit rotation ER windows are ordinary, immutable source facts.
            # Unknown NONEXTRA qualification does not prevent a TOTAL read.
            for window in self.executor.windows:
                if not window['start'] <= snapshot['time'] < window['end']:
                    continue
                actor = window['character']
                node = {'component': {'provider': actor, 'recipient': actor,
                            'source_fingerprint': _digest(window), 'part_id': 'energy_window:'+window['id'], 'effect_id': 'delta'},
                        'instance_id': 'energy_window:'+window['id'], 'layer_id': '0', 'stat': 'energy_recharge',
                        'value': window['delta'], 'value_unit': 'ratio', 'application': {'kind': 'flat'},
                        'nonextra_inclusion': 'unknown', 'visibility': 'attribute', 'constraints': [], 'output_domain': 'self'}
                node['id'] = 'attribute:'+_digest(node)
                nodes.append(node)
        frame = {**self.context, 'state_epoch': self.epoch, 't_us': snapshot['time'],
                 'id': frame_id, 'contribution_ids': sorted(n['id'] for n in nodes)}
        if self.graph and self.observation:
            frame['observation'] = dict(self.observation)
        token = {'frame': frame, 'contributions': nodes, 'missing': missing, 'actual_event_run_id': self.actual_event_run_id}
        if self.graph:
            self._adopt(token)
        return token

    def _adopt(self, token):
        frame = token['frame']
        if self.graph or frame['id'] not in self.adopted:
            self.ledger.adopt_frame(frame, contributions=token['contributions'])
            self.adopted.add(frame['id'])
            self.missing_frames[frame['id']] = copy.deepcopy(token['missing'])
            self.frame_routes[frame['id']] = {'frame_id': frame['id'], 'origin': frame['origin'],
                                            'run_id': frame['run_id'], 'actual_event_run_id': token['actual_event_run_id']}

    def read(self, actor, basis, snapshot, *, stat='em'):
        if self.five_stats:
            stat, basis = canonical_read(stat, basis)
        elif stat == 'base_atk' and self.multistat:
            stat, basis = 'atk', 'base'
        token = snapshot['attribute_frame']
        frame = token['frame']
        self._adopt(token)
        if not self.graph and basis != 'base' and any(m['actor'] == actor for m in token['missing']):
            raise AttributeLedgerError('attribute_component_missing', '活跃精通贡献没有可审计的来源组件映射', actor, status='pending')
        receipt = self.ledger.read_stat(actor, stat, basis, frame['id'])
        self.receipts[receipt['receipt_id']] = receipt
        if receipt['status'] != 'known':
            issue = receipt['issues'][0]
            raise AttributeLedgerError(issue['code'], issue['message'], issue['path'], status=receipt['status'])
        return receipt

    def project(self, actor, event, self_snapshot, current_snapshot, jobs, *, stats=None):
        """Certify the selected hit members as one root-only damage projection."""
        nodes = []
        self_snapshot = self_snapshot or current_snapshot
        source_tokens = {'self': self_snapshot['attribute_frame'], 'provider': current_snapshot['attribute_frame']}
        for token in source_tokens.values():
            self._adopt(token)
        for job in jobs:
            self.ledger.reserve_capture_work(1)
            if job.get('condition'):
                node = self._condition_projection_node(actor, event, job)
                nodes.append(node)
                job['node_id'] = node['id']
                continue
            classified = self.classifications.get(job['key'])
            if classified is None:
                raise AttributeLedgerError('attribute_component_missing', '出伤精通贡献缺少来源组件声明', str(job['key']), status='pending')
            token = source_tokens[job['domain']]
            component = {**classified['component'], 'recipient': actor}
            matches = [n for n in token['contributions'] if n['component'] == component
                       and n['instance_id'] == job['instance'] and n['layer_id'] == job['layer']
                       and n['stat'] == job.get('stat', 'em')]
            if len(matches) != 1:
                unsupported = next((m for m in token['missing'] if m['actor'] == actor and m['source'] == list(job['key'])), None)
                if unsupported:
                    raise AttributeLedgerError(unsupported.get('code', 'attribute_component_missing'),
                                               '出伤属性层缺少支持的表达式或已审核来源组件', str(job['key']), status='pending')
                raise AttributeLedgerError('attribute_projection_source', '出伤层必须唯一对应来源属性帧中的活跃组件', str(job['key']), status='pending')
            original = matches[0]
            node = copy.deepcopy(original)
            node['projection_of'] = {'frame_id': token['frame']['id'], 'contribution_id': original['id']}
            if 'sample_binding' in node:
                ref = node['sample_binding']['event_id']
                if ref in self.executor.captures:
                    self.executor._capture_reference(event, ref, 'attribute_projection')
                observed = self.executor.snapshots.get((event['origin'], ref))
                node['value']['input']['frame_id'] = (observed['attribute_frame']['frame']['id'] if observed else
                    'missing:event:'+_digest([event['origin'], ref]))
            node['id'] = ('attribute_projection:' if self.multistat else 'emprojection:')+_digest(node)
            nodes.append(node)
            job['node_id'] = node['id']
        self.stage(event, 'damage_projection')
        frame = {**self.context, 'state_epoch': self.epoch, 't_us': self.executor.time,
                 'id': 'projection:'+str(self.epoch), 'contribution_ids': [n['id'] for n in nodes],
                 'frame_kind': 'damage_projection', 'context': {'actor': actor, 'actual_event_run_id': self.actual_event_run_id,
                    'self_frame_id': source_tokens['self']['frame']['id'], 'current_frame_id': source_tokens['provider']['frame']['id']}}
        self._adopt({'frame': frame, 'contributions': nodes, 'missing': [], 'actual_event_run_id': self.actual_event_run_id})
        projected = {}
        rows_by_stat = {}
        for stat in sorted(stats or ('em',)):
            receipt = self.ledger.read_stat(actor, stat, 'total', frame['id'], purpose='damage_projection')
            self.receipts[receipt['receipt_id']] = receipt
            if receipt['status'] != 'known':
                issue = receipt['issues'][0]
                raise AttributeLedgerError(issue['code'], issue['message'], issue['path'], status=receipt['status'])
            projected[stat] = receipt
            rows_by_stat[stat] = {n['id']: n for n in receipt['contributions'] if n['decision'] == 'included'}
        for job in jobs:
            stat = job.get('stat', 'em')
            row = rows_by_stat[stat].get(job['node_id'])
            if row is None:
                raise AttributeLedgerError('attribute_projection_source', '已认证回执缺少唯一出伤贡献行', job['node_id'])
            job['value'] = row.get('raw_evaluated_value', row['evaluated_value'])
            job['receipt'] = projected[stat]
            if self.multistat:
                job['applied_value'] = row['evaluated_value']
                job['application_base_receipt_id'] = row.get('application_base_receipt_id')
            if 'input_receipt_id' in row:
                job['input_receipt'] = self.ledger._published[row['input_receipt_id']]
        return projected if stats is not None else projected['em']

    def _condition_projection_node(self, actor, event, job):
        """Only an actual applied target-condition receipt may mint this root."""
        effect = job['effect']
        index = job.get('condition_trace_index')
        if type(index) is not int or not 0 <= index < len(self.executor.target_condition_trace):
            raise AttributeLedgerError('attribute_condition_source', '条件属性缺少本次真实执行位置', effect['id'])
        receipt = self.executor.target_condition_trace[index]
        source = self.executor.fixed_sources.get(receipt['fixed_source_id'])
        matches = [declared for declared in source['effects'] if declared['id'] == receipt['effect_id']] if source else []
        if len(matches) != 1:
            raise AttributeLedgerError('attribute_condition_source', '条件属性未定位唯一真实来源', effect['id'])
        declared = matches[0]
        ident = 'fixed:'+hashlib.sha256(json.dumps([source['id'], declared['id']], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
        if (receipt['event_id'] != event['id'] or receipt['cycle'] != event['origin'] or not receipt['applied']
                or effect['value'] != declared['value'] or effect['id'] != ident
                or effect['benefit_type_id'] != declared['benefit_type_id']):
            raise AttributeLedgerError('attribute_condition_source', '条件属性没有唯一已生效执行回执', effect['id'])
        output = multistat_effect_output(declared, kind='rule')
        if output is None or output['stat'] != job['stat']:
            raise AttributeLedgerError('attribute_condition_source', '条件属性实际输出维度不符', effect['id'])
        actual = event.get('run_id', f"{event['origin']}:{event['id']}")
        evidence = {'actor': actor, 'actual_event_run_id': actual, 'event_id': event['id'], 'origin': event['origin'],
                    'source_id': source['source_id'], 'fixed_source_id': source['id'], 'effect_id': declared['id'],
                    'source_fingerprint': _digest(source), 'condition_receipt_fingerprint': _digest(receipt)}
        reference = 'condition:'+_digest(evidence)
        node = {'component': {'provider': source['owner'], 'recipient': actor, 'part_id': source['source_id'],
                             'effect_id': declared['id'], 'source_fingerprint': evidence['source_fingerprint']},
                'instance_id': actual, 'layer_id': '0', **output, 'value': declared['value'],
                'nonextra_inclusion': 'unknown', 'visibility': 'attribute', 'constraints': [], 'condition_ref': reference}
        self.ledger.condition_nodes[reference] = {'node': copy.deepcopy(node), 'actor': actor, 'actual_event_run_id': actual}
        self.condition_sources[reference] = evidence
        return {**node, 'id': 'attribute_condition:'+_digest(node)}

    def report(self):
        export_issue = None
        try:
            receipts = self.ledger.receipt_catalog() if self.graph else copy.deepcopy(list(self.receipts.values()))
        except AttributeLedgerError as error:
            receipts, export_issue = [], error.issue
        return {'contract': self.contract, 'frame_authority': 'mechanics_execution',
                'qualification_scope': 'five_stat_graph_declared_sources' if self.five_stats else 'em_atk_graph_declared_sources' if self.multistat else 'em_graph_declared_sources' if self.graph else 'constant_em_declared_sources',
                'usage': self.ledger.usage(),
                'declarations': copy.deepcopy(self.declarations),
                'frame_routes': copy.deepcopy(list(self.frame_routes.values())),
                **({'condition_sources': copy.deepcopy(self.condition_sources)} if self.condition_sources else {}),
                'receipts': receipts, **({'export_issue': export_issue} if export_issue else {})}
