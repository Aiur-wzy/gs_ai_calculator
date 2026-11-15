"""Off-tree bounded audit slots; caller-supplied contexts, no queue authority.

The sole integration point after ledger return only publishes scalar state. This
module neither selects an operation nor calls the ledger's mutation methods.
"""
from __future__ import annotations

import copy
import math

from genshin_engine.elemental_schema import DYNAMIC_SWIRL_RULESET
from genshin_engine.execution_control import (
    ExecutionCancelled,
    check_execution_cancelled,
)
from genshin_engine.resource_budget import normalize_resource_budget
from genshin_engine.resource_reaction_program import (
    REACTION_ELEMENTS,
    REACTION_TYPES,
    ReactionResourceError,
    _bounded,
    _canonical,
)

LIMITS = {'work': 200000, 'slots': 20000, 'bytes': 8 * 1024 * 1024, 'terminal_bytes': 16384}
IDENTITIES = ('parent_event_id', 'parent_run_id', 'reaction_event_id', 'reaction_run_id',
              'target_id', 'owner', 'frontline')
DIGESTS = ('application_fingerprint', 'reaction_fingerprint', 'parent_fingerprint')
CONTEXT = frozenset((*IDENTITIES, *DIGESTS, 'origin', 'time_us', 'reaction_type', 'trigger_element', 'aura_element'))


class AuditStopped(RuntimeError):
    pass


class _Scope:
    pass


class _Slot:
    pass


class Audit:
    def __init__(self, program, ledger, reserve_events, *, limits=None):
        self.policy = normalize_resource_budget(limits)
        self.limits = self.policy['effective']
        manifest = program.report()
        self.program, self.ledger, self.reserve_events = program, ledger, reserve_events
        self._bound_program, self._bound_ledger = program, ledger
        self._manifest = manifest
        self._operations = {r['id']: r for r in manifest['operations']}
        self._material_usage = {r['id']: _bounded({'operation': r['operation'], 'pool': r['pool']}, byte_limit=256 * 1024)
                                for r in manifest['operations']}
        self._work = self._tickets = self._bytes = 0
        self._scopes, self._slots = {}, {}
        self._occurrences, self._rows = [], []
        self._seen = set()
        self._busy = self._stopped = self._exported = False
        self._termination = None
        self._pending_slot = None
        self._qualification_materials = {}
        self._work_limit = None
        self._bytes = len(_canonical(manifest)) + self.limits['terminal_bytes']
        if self._bytes > self.limits['bytes']:
            self._fail('resource_reaction_limit', '初始化目录超过审计字节预算。', stop=True)

    def _fail(self, code, message, *, stop=False):
        if stop:
            self._stopped = True
            self._termination = code
        raise ReactionResourceError(code, message, severity='pending' if stop else 'error')

    def _enter(self):
        if self._stopped:
            raise AuditStopped('反应资源审计已经停止。')
        if self._busy:
            self._fail('resource_reaction_reentry', '审计预算回调不能重入同一服务。')
        self._busy = True

    def _charge(self, work, *, tickets=0):
        try:
            check_execution_cancelled()
        except ExecutionCancelled:
            self._stopped, self._termination = True, 'resource_execution_cancelled'
            raise
        limit = self.limits['work'] if self._work_limit is None else self._work_limit
        if self._work + self._tickets + work + tickets > limit:
            self._fail('resource_reaction_limit', '反应资源审计工作预算不足。', stop=True)
        self._work += work
        self._tickets += tickets

    def _reserve(self, row, *, tickets, private_bytes=0):
        prototype = copy.deepcopy(row)
        if 'state' in prototype:
            prototype.update(state='committed_inconsistent', actual_end=10**15, failure_class='x' * 64)
        size = len(_canonical(prototype)) + 1 + private_bytes
        self._charge(1, tickets=tickets)
        if len(self._occurrences) + len(self._rows) >= self.limits['slots'] or self._bytes + size > self.limits['bytes']:
            self._fail('resource_reaction_limit', '反应资源审计槽或字节预算不足。', stop=True)
        # May throw/reenter: no new row has been published at this point.
        self.reserve_events(1)
        self._bytes += size

    def _scope(self, token):
        value = self._scopes.get(token) if type(token) is _Scope else None
        if value is None:
            self._fail('resource_reaction_authority', 'scope不是此服务签发的原始对象。')
        return value

    def _slot(self, token):
        value = self._slots.get(token) if type(token) is _Slot else None
        if value is None:
            self._fail('resource_reaction_authority', 'slot不是此服务签发的原始对象。')
        return value

    def _same_material(self, actual, expected):
        """Traverse only the bounded expected shape, charging rejected input too."""
        self._charge(1)
        if type(actual) is not type(expected):
            return False
        if type(expected) is dict:
            return (len(actual) == len(expected) and all(k in actual and self._same_material(actual[k], v)
                                                       for k, v in expected.items()))
        if type(expected) is list:
            return len(actual) == len(expected) and all(self._same_material(a, b) for a, b in zip(actual, expected, strict=True))
        if type(expected) is str:
            if len(actual) != len(expected):
                return False
            self._charge(math.ceil(len(expected) * 6 / 256))
        return actual == expected

    def begin(self, context):
        self._enter()
        try:
            self._charge(1)
            if type(context) is not dict or len(context) != len(CONTEXT) or set(context) != CONTEXT:
                self._fail('resource_reaction_context', '反应发生上下文字段不完整。')
            if any(type(context[k]) is not str or not context[k].strip() or len(context[k]) > 512 for k in IDENTITIES):
                self._fail('resource_reaction_context', '反应发生身份为空或超限。')
            if any(type(context[k]) is not str or len(context[k]) != 64 or any(c not in '0123456789abcdef' for c in context[k]) for k in DIGESTS):
                self._fail('resource_reaction_context', '反应发生材料指纹无效。')
            if any(type(context[k]) is not int or not 0 <= context[k] <= 10**15 for k in ('origin', 'time_us')):
                self._fail('resource_reaction_context', '反应发生时间或源轮无效。')
            if (context['owner'] not in self._manifest['characters'] or context['frontline'] not in self._manifest['characters']
                    or context['reaction_type'] not in REACTION_TYPES
                    or context['trigger_element'] not in ('anemo', 'pyro', 'hydro', 'cryo')
                    or context['aura_element'] not in REACTION_ELEMENTS[self._manifest.get('elemental_ruleset', DYNAMIC_SWIRL_RULESET)]
                    or context['reaction_run_id'] == context['parent_run_id']):
                self._fail('resource_reaction_context', '普通反应角色、元素或实际run身份无效。')
            if context['reaction_run_id'] in self._seen:
                self._fail('resource_reaction_duplicate', '同一实际反应不能登记两次。')
            _bounded(context, byte_limit=16384, charge_work=self._charge)
            row = {**copy.deepcopy(context), 'id': len(self._occurrences),
                   'resource_scan_complete': False, 'callback_complete': False}
            self._reserve(row, tickets=1)
            token = _Scope()
            self._scopes[token] = (row, set(), [])
            self._occurrences.append(row)
            self._seen.add(context['reaction_run_id'])
            return token
        finally:
            self._busy = False

    def prepare(self, scope, operation_id, decision, operation, pool, ledger, *, qualification=None):
        self._enter()
        try:
            self._charge(1)  # Failed/repeated registrations also consume work.
            occurrence, seen, row_ids = self._scope(scope)
            if self.program is not self._bound_program or self.ledger is not self._bound_ledger:
                self._fail('resource_reaction_program_changed', '审计绑定的完整Program或资源账对象被替换。')
            if ledger is not self.ledger:
                self._fail('resource_reaction_authority', '实际资源账不是绑定的同一对象。')
            if type(operation_id) is not str or operation_id not in self._operations:
                self._fail('resource_reaction_reference', '实际operation身份不属于此合同。')
            if occurrence['resource_scan_complete'] or operation_id in seen:
                self._fail('resource_reaction_duplicate', '扫描已结束或同一operation重复登记。')
            if decision not in ('match', 'no_match', 'unknown_condition'):
                self._fail('resource_reaction_decision', '必须观察既得selector决策。')
            if self._pending_slot is not None:
                self._fail('resource_reaction_incomplete', '上一资源事务仍为planned，不能并发准备第二项。')
            record = self._operations[operation_id]
            if not self._same_material(operation, record['operation']) or not self._same_material(pool, record['pool']):
                self._fail('resource_reaction_program_changed', '实际operation或pool与初始化合同不一致。')
            usage = self._material_usage[operation_id]
            self._charge(2 * usage['nodes'] + math.ceil(usage['bytes'] / 128))
            keys = self.program.verify_operation(operation_id, operation, pool)
            allowed = decision == 'match'
            if qualification is not None:
                if (self._manifest['contract'] != 'ordinary-reaction-resource-v2' or decision != 'match'
                        or type(qualification.get('qualified')) is not bool):
                    self._fail('resource_lifecycle_schema', '资格回执只在显式v2实际选择后接受。')
                _bounded(qualification, byte_limit=16384, charge_work=self._charge)
                allowed = qualification['qualified']
            try:
                begin = self._bound_ledger.receipt_cursor() if allowed else None
            except (ValueError, TypeError, RuntimeError, ArithmeticError, LookupError, AttributeError):
                self._fail('resource_reaction_cursor', '提交前无法读取资源回执游标，未开始资源事务。', stop=True)
            if allowed and (type(begin) is not int or not 0 <= begin <= 40000):
                self._fail('resource_reaction_cursor', '资源回执游标不在有界范围。', stop=True)
            row = {'id': len(self._rows), 'occurrence_id': occurrence['id'], 'operation_id': operation_id,
                   'pool_id': record['pool_id'], 'source_id': record['source_id'], 'source_owner': record['source_owner'],
                   'keys': keys, 'decision': decision, 'state': 'planned' if allowed else 'blocked_lifecycle' if qualification is not None else decision,
                   'ledger_begin': begin, 'actual_end': None, 'expected_count': len(keys) if allowed else 0,
                   'failure_class': None}
            if qualification is not None:
                row['lifecycle_qualification'] = copy.deepcopy(qualification)
            material = _canonical(qualification) if qualification is not None else None
            self._reserve(row, tickets=1 + len(keys), private_bytes=len(material) if material is not None else 0)
            token = _Slot()
            self._slots[token] = row
            self._rows.append(row)
            if material is not None: self._qualification_materials[row['id']] = material
            row_ids.append(row['id'])
            seen.add(operation_id)
            if allowed:
                self._pending_slot = token
            return token
        finally:
            self._busy = False

    def committed(self, slot):
        # No budget, raw material, copy, hash or JSON work after ledger return.
        if self._busy:
            self._fail('resource_reaction_reentry', '预留期间不能重入发布资源。')
        if self._exported:
            raise AuditStopped('审计目录已经导出。')
        self._busy = True
        try:
            row = self._slot(slot)
            if row['state'] != 'planned':
                self._fail('resource_reaction_duplicate', '只有planned槽可以发布已提交事务。')
            try:
                end = self._bound_ledger.receipt_cursor()
            except (ValueError, TypeError, RuntimeError, ArithmeticError, LookupError, AttributeError):
                row['state'], row['failure_class'] = 'committed_inconsistent', 'cursor_unavailable'
                self._pending_slot = None
                self._fail('resource_reaction_cursor', '资源提交后游标读取失败，保留已提交但待核状态。', stop=True)
            good_scalar = type(end) is int and 0 <= end <= 10**15
            row['actual_end'] = end if good_scalar else None
            self._pending_slot = None
            if not good_scalar or end != row['ledger_begin'] + row['expected_count']:
                row['state'] = 'committed_inconsistent'
                row['failure_class'] = 'cursor_mismatch'
                self._fail('resource_reaction_cursor', '资源已提交但实际span与准备目录不一致。', stop=True)
            row['state'] = 'committed'
        finally:
            self._busy = False

    def failed(self, slot, failure_class):
        if self._busy:
            self._fail('resource_reaction_reentry', '预留期间不能重入失败发布。')
        if self._exported:
            raise AuditStopped('审计目录已经导出。')
        row = self._slot(slot)
        if row['state'] != 'planned' or failure_class not in ('ledger_rejected', 'event_budget', 'ledger_budget'):
            self._fail('resource_reaction_state', '不能把已提交资源改写成failed。')
        row['state'], row['failure_class'] = 'failed', failure_class
        self._pending_slot = None

    def finish_scan(self, scope):
        self._enter()
        try:
            self._charge(1)
            row, seen, row_ids = self._scope(scope)
            self._charge(len(row_ids))
            if seen != set(self._operations):
                self._fail('resource_reaction_missing', '完整资源扫描缺少绑定operation决策。')
            if any(self._rows[index]['state'] in ('planned', 'failed', 'committed_inconsistent') for index in row_ids):
                self._fail('resource_reaction_incomplete', '资源扫描存在未完成事务。')
            row['resource_scan_complete'] = True
        finally:
            self._busy = False

    def finish_callback(self, scope):
        self._enter()
        try:
            self._charge(1)
            row, _, _ = self._scope(scope)
            if not row['resource_scan_complete'] or row['callback_complete']:
                self._fail('resource_reaction_incomplete', '回调未完成扫描或重复结束。')
            row['callback_complete'] = True
        finally:
            self._busy = False

    def report(self):
        if self._busy:
            self._fail('resource_reaction_reentry', '执行预留或提交时不能重入导出审计。')
        if self._exported:
            raise AuditStopped('审计目录已经导出，不能重新输出未计费副本。')
        self._exported = True
        self._stopped = True
        return {'contract': self._manifest['contract'], 'program_fingerprint': self._manifest['program_fingerprint'],
                'execution': 'not_connected', 'source_authority': 'not_evaluated',
                'verification_scope': 'caller_supplied_context_slots_only',
                'occurrences': copy.deepcopy(self._occurrences), 'operations': copy.deepcopy(self._rows),
                'termination': self._termination,
                'budget_policy': copy.deepcopy(self.policy),
                'usage': {'work_consumed': self._work, 'verification_work_reserved': self._tickets,
                          'catalog_bytes_reserved': self._bytes, 'slots': len(self._occurrences) + len(self._rows)}}
