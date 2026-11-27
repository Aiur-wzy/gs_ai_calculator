"""Private RQ1 bridge: observe one real selector and one real resource commit.

This runtime certifies engineering occurrence consistency only. It neither
approves game source facts nor changes ordinary elemental callback order.
"""
from __future__ import annotations

import copy
import hashlib
import math

from genshin_engine.elemental_schema import MULTI_AURA_RULESET
from genshin_engine.resource_definition_index import IndexError
from genshin_engine.resource_reaction_audit import Audit
from genshin_engine.resource_reaction_execution import verify
from genshin_engine.resource_reaction_program import (
    REACTION_TYPES,
    Program,
    ReactionResourceError,
    _bounded,
    _canonical,
    declaration,
    reaction_members,
    reaction_ruleset,
    valid_v4_reaction_member,
    validate_reaction_selector,
)


def validate_declaration(model):
    if 'resource_reaction_contract' not in model:
        return []
    try:
        _bounded(model)
        _, selected, _ = declaration(model)
        ruleset = reaction_ruleset(model)
        if model.get('application_events'):
            raise ReactionResourceError('resource_reaction_parent', '首片不接受独立无伤害附着父。', severity='pending')
        operations = {r['id']: r for r in model.get('shared_pools', {}).get('operations', [])}
        if not set(selected) <= set(operations):
            raise ReactionResourceError('resource_reaction_reference', '反应资源合同引用了不存在的operation。')
        for identity in selected:
            operation = operations[identity]
            selector = operation['trigger']
            if operation['operation'] != 'grant' or type(operation['amount']) is not int:
                raise ReactionResourceError('resource_reaction_scope', '首片需要常量grant及实际反应post筛选。')
            validate_reaction_selector(selector, ruleset, f'shared_pools.operations.{identity}.trigger')
    except ReactionResourceError as exc:
        return [copy.deepcopy(exc.issue)]
    return []


def digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def parent_material(event, frontline):
    return {'id': event['id'], 'run_id': event['run_id'], 'origin': event['origin'],
            'time_us': event['time'], 'order': event['order'], 'kind': event['kind'],
            'action_id': event['data']['action_id'], 'frontline': frontline}


class Runtime:
    def __init__(self, context):
        self.context = context
        self._pool_runtime = context.shared_pools
        self.program = Program(context.model, list(context.data['chars']))
        self.audit = Audit(self.program, context.shared_pools.ledger,
                           lambda count: context._reserve_events(count, 'resource_reaction_audit'),
                           limits=context.model.get('execution_options', {}).get('resource_limits'))
        usage = self.program.input_usage
        self.audit._charge(usage['nodes'] + math.ceil(usage['bytes'] / 128))
        manifest = self.program.report()
        self.operations = {r['id']: r for r in manifest['operations']}
        self.active = None
        self.slots = {}
        self._application_binding = None
        self.lifecycle = None

    def bind_state(self):
        if self.program._manifest.get('lifecycle_gates'):
            from genshin_engine.resource_lifecycle_runtime import (
                Runtime as LifecycleRuntime,
            )
            if self.lifecycle is not None:
                raise ReactionResourceError('resource_lifecycle_identity', '生命周期桥不能重复绑定。', severity='pending')
            self.lifecycle = LifecycleRuntime(self)
            self.context.state_runtime._resource_qualification = self.lifecycle
            self.context.state_runtime._resource_qualification_hooks = self.lifecycle.bind_hooks(self.context.state_runtime)

    def observe_application(self, event, hit, receipt):
        # ElementalRuntime deliberately returns a detached receipt. Bind that
        # exact returned object at the only real attempt hook, after comparing
        # the runtime-owned original. Later copies cannot be substituted.
        scene = getattr(self.context, "target_scene", None)
        actual = scene.actual_application(receipt) if scene else self.context.elemental.trace[-1]
        _bounded([actual, receipt], byte_limit=256 * 1024, charge_work=self.audit._charge)
        if digest(actual) != digest(receipt):
            raise ReactionResourceError('resource_reaction_actual_identity', '实际附着回执与元素账不一致。', severity='pending')
        self._application_binding = (event, hit, receipt)

    def begin(self, event, hit, reaction_event, reaction_parent, application, reaction):
        if self.active is not None:
            raise ReactionResourceError('resource_reaction_reentry', '同一反应资源桥不能重入。')
        ruleset = self.program.ruleset
        if ruleset == MULTI_AURA_RULESET and (event['kind'] != 'mechanics_hit' or reaction['type'] not in REACTION_TYPES):
            return None
        self.audit._charge(1)
        # The current elemental object owns the exact application and nested
        # reaction. A copied declaration cannot stand in for an actual attempt.
        bound = self._application_binding
        if (bound is None or event is not bound[0] or hit is not bound[1] or application is not bound[2]
                or not any(item is reaction and (ruleset != MULTI_AURA_RULESET
                    or valid_v4_reaction_member(application, item, index))
                    for index, item in enumerate(reaction_members(application, ruleset)))
                or event['kind'] != 'mechanics_hit' or hit['damage_system'] != 'classic'
                or application.get('allowed') is not True or application.get('outcome') != 'reaction'
                or application.get('ruleset') != ruleset
                or application.get('run_id') != event['run_id']
                or application.get('stable_event_id') != event['id']
                or application.get('character') != hit['character']
                or reaction.get('parent_run_id') != event['run_id']
                or reaction.get('parent_event_id') != event['id']
                or reaction.get('owner') != hit['character']
                or reaction.get('target_id') != application.get('target_id')
                or reaction_parent['run_id'] != reaction['run_id']
                or reaction_parent['id'] != reaction['id']
                or reaction_event['id'] != reaction['id']):
            raise ReactionResourceError('resource_reaction_actual_identity', '反应资源只能绑定当前实际命中产生的原始附着与反应对象。', severity='pending')
        if ruleset == MULTI_AURA_RULESET and reaction.get('action_id') != hit.get('action_id'):
            raise ReactionResourceError('resource_reaction_actual_identity', '多元素反应资源的动作来源不一致。', severity='pending')
        material = parent_material(event, self.context.frontline)
        _bounded([application, reaction, material], byte_limit=256 * 1024, charge_work=self.audit._charge)
        values = {'parent_event_id': event['id'], 'parent_run_id': event['run_id'],
            'reaction_event_id': reaction['id'], 'reaction_run_id': reaction['run_id'],
            'origin': event['origin'], 'time_us': self.context.time, 'target_id': reaction['target_id'],
            'owner': reaction['owner'], 'frontline': self.context.frontline,
            'reaction_type': reaction['type'], 'trigger_element': reaction['trigger_element'], 'aura_element': reaction['aura_element'],
            'application_fingerprint': digest(application), 'reaction_fingerprint': digest(reaction),
            'parent_fingerprint': digest(material)}
        token = self.audit.begin(values)
        self.active = (token, reaction_event, reaction_parent)
        self.slots = {}
        return token

    def excludes_operation(self, event, phase, parent, operation):
        # Other listeners keep seeing the actual callback. Contract operations
        # must never fall through to the unaudited path on an excluded branch.
        return (self.program.ruleset == MULTI_AURA_RULESET and operation['id'] in self.operations
                and event['event'] == 'elemental_reaction' and phase == 'post'
                and (parent['kind'] != 'mechanics_hit' or event['reaction_type'] not in REACTION_TYPES))

    def sink(self, event, phase, parent, operation, pool, ledger):
        if operation['id'] not in self.operations or event['event'] != 'elemental_reaction' or phase != 'post':
            return None
        if self.active is None or event is not self.active[1] or parent is not self.active[2]:
            raise ReactionResourceError('resource_reaction_actual_identity', '反应operation不在当前实际回调的身份范围内。', severity='pending')
        if (self.context.shared_pools is not self._pool_runtime or ledger is not self.audit._bound_ledger
                or self._pool_runtime.ledger is not ledger
                or not self.audit._same_material(ledger._operations.get(operation['id']), self.operations[operation['id']]['operation'])
                or not self.audit._same_material(ledger._pools.get(pool['id']), self.operations[operation['id']]['pool'])):
            raise ReactionResourceError('resource_reaction_program_changed', '实际资源账对象或其operation/pool定义与冻结图不一致。', severity='pending')
        token = self.active[0]

        def observe(decision):
            qualification = (self.lifecycle.prepare(operation) if decision == 'match' and self.lifecycle
                and operation['id'] in self.lifecycle.gates else None)
            try:
                self.slots[operation['id']] = self.audit.prepare(token, operation['id'], decision, operation, pool, ledger,
                    **({'qualification': qualification} if qualification is not None else {}))
            except Exception:
                if qualification is not None: self.lifecycle.release(operation['id'])
                raise
        return observe

    def admit_operation(self, operation):
        return self.lifecycle.admit(operation) if self.lifecycle and operation['id'] in self.lifecycle.gates else True

    def keys(self, operation_id):
        return self.operations[operation_id]['keys']

    def authorize_threshold_operation(self, event, phase, parent, operation_id):
        """Only the current original callback and committed audit span qualify."""
        if (self.active is None or event is not self.active[1] or parent is not self.active[2]
                or phase != 'post' or operation_id not in self.slots
                or self.audit._slot(self.slots[operation_id])['state'] != 'committed'):
            raise ReactionResourceError('resource_reaction_threshold_authority',
                '阈值派发缺少当前原始反应和已提交资源事务。', severity='pending')
        return True

    def committed(self, operation_id):
        try:
            self.audit.committed(self.slots[operation_id])
        finally:
            if self.lifecycle: self.lifecycle.release(operation_id)

    def failed(self, operation_id, error):
        code = error.issue['code']
        category = 'event_budget' if code in ('pool_event_limit', 'attribute_event_limit', 'state_event_limit') else 'ledger_budget' if 'limit' in code else 'ledger_rejected'
        try:
            self.audit.failed(self.slots[operation_id], category)
        finally:
            if self.lifecycle: self.lifecycle.release(operation_id)

    def finish_scan(self, event, phase, parent):
        if self.active is not None and event is self.active[1] and parent is self.active[2] and phase == 'post':
            self.audit.finish_scan(self.active[0])

    def finish_callback(self, token):
        if self.active is None or token is not self.active[0]:
            raise ReactionResourceError('resource_reaction_actual_identity', '回调结束身份与实际发生不一致。', severity='pending')
        self.audit.finish_callback(token)

    def leave(self, token):
        if self.active is None or token is not self.active[0]:
            raise ReactionResourceError('resource_reaction_actual_identity', '退出身份与实际发生不一致。', severity='pending')
        self.active = None
        self.slots = {}

    def attach(self, output):
        verification = None
        verification_issue = None
        try:
            if self.lifecycle: self.lifecycle.verify_report()
            self.program.verify_model(self.context.model, list(self.context.data['chars']), charge_work=self.audit._charge)
            event_log = output.get('rotation_check', {}).get('event_log', [])
            trace = self.context.trace
            elemental = self.context.elemental.trace
            resource = output['shared_pool_trace']
            raw_resource = self.audit._bound_ledger._receipts
            self.audit._charge(len(self.context.data['runtime']))
            if self.lifecycle:
                try:
                    self.lifecycle.budget.pay(storage=4096+64*len(self.context.data['runtime']))
                except IndexError as exc:
                    raise ReactionResourceError('resource_reaction_report_limit', str(exc),
                        'resource_reactions.verification', severity='pending') from exc
            runtime_events = [r for r in self.context.data['runtime'] if r['kind'] == 'mechanics_hit']
            lists = [event_log, trace, elemental, resource, raw_resource, runtime_events]
            # Input sizes are checked before hashing. This is the same one
            # execution budget, never a new service on each report/read.
            self.audit._charge(sum(map(len, lists)))
            if self.lifecycle:
                from genshin_engine.resource_execution_report import validate
                validate(self, [self.program._manifest, self.audit._occurrences, self.audit._rows, *lists])
            else:
                _bounded(lists, charge_work=self.audit._charge)
            verification = verify(self.program._manifest, self.audit._occurrences, self.audit._rows,
                                  event_log, trace, elemental, resource, digest,
                                  runtime_events=runtime_events, raw_resource_trace=raw_resource)
            self.audit._work += self.audit._tickets
            self.audit._tickets = 0
        except ReactionResourceError as exc:
            verification_issue = {**exc.issue, 'severity': 'pending'}
        except (ValueError, TypeError, KeyError, ArithmeticError):
            verification_issue = {'code': 'resource_reaction_verification',
                'message': '实际反应或资源前缀结构不完整，不能认证完整结果。',
                'path': 'resource_reaction_contract', 'severity': 'pending'}
        report = self.audit.report()
        report.update(execution='mechanics_event_queue', source_authority='caller_assumed',
            program=copy.deepcopy(self.audit._manifest),
            verification_scope='actual_runtime_occurrence_consistency_no_new_source_authority',
            verification_status='verification_pending' if verification_issue else 'verified',
            verification=verification, verification_issue=verification_issue,
            execution_status='pending' if verification_issue else output['status'],
            verified_window=('complete_reported_input' if not verification_issue
                and (output.get('result') or {}).get('mechanics', {}).get('complete_first_cycle') is True
                else 'executed_prefix'))
        output['resource_reactions'] = report
        if verification_issue:
            output['issues'].append(verification_issue)
            output.update(status='pending', result=None, resolved_template=None)
        if output.get('result') is not None:
            output['result']['resource_reactions'] = copy.deepcopy(report)
