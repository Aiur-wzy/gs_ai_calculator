"""RQ2 actual queue adapter, private candidate; no game-source approval.

Only completed state2 delivery hooks provide facts. The mathematical service
never reads caller JSON as an applied lifecycle, and cannot schedule work.
"""
from __future__ import annotations

import copy
from functools import partial

from genshin_engine.resource_definition_index import Budget, IndexError
from genshin_engine.resource_lifecycle_index import (
    IndexedQualification,
    compile_index,
    roots_of,
)
from genshin_engine.resource_lifecycle_qualification import (
    QualificationError,
    _integer,
    _text,
)
from genshin_engine.resource_reaction_program import ReactionResourceError


def fail(reason):
    raise ReactionResourceError('resource_lifecycle_qualification', reason, severity='pending')


class Runtime:
    # Exploratory isolated measurement ceiling, not a released public limit.
    MEASUREMENT_WORK_LIMIT = 20_000_000

    def __init__(self, reaction):
        self.reaction = reaction
        self.ctx = reaction.context
        self.bridge = self.ctx.state_runtime
        self.clock = self.bridge.clock
        self.pool_runtime = self.ctx.shared_pools
        self.ledger = self.pool_runtime.ledger
        self.counter_program = self.pool_runtime.counter_program
        limits = reaction.audit.limits
        self.budget = Budget(work=limits['work'], storage=limits['index_storage'], encoding=limits['index_encoding'],
                            graph_bytes=limits['index_graph_bytes'], scope_references=limits['index_scope_references'],
                            containers=limits['index_containers'], edges=limits['index_edges'], charge=reaction.audit._charge,
                            consume=self.consume_reserved_index_work)
        from genshin_engine.resource_reaction_program import (
            LIMITS as declaration_limits,
        )
        # Fixed temporary scratch is prepaid before compiler containers exist.
        # One bounded scalar JSON encoding may retain Unicode and UTF-8 copies;
        # 128 B/codepoint plus 8 KiB/recursive level is a conservative private
        # CPython allowance, separate from the later complete-graph encoding.
        scratch = 128*(declaration_limits['string_chars']+2) + 8192*(declaration_limits['depth']+1)
        try:
            self.budget.pay(work=32, storage=scratch+16384)
            self.bootstrap_scratch_bytes = scratch
            self.budget.pay(work=len(reaction.program._manifest['lifecycle_gates']),
                            storage=512*len(reaction.program._manifest['lifecycle_gates']))
        except IndexError as exc:
            fail(str(exc))
        self.gates = {r['operation_id']: r for r in reaction.program._manifest['lifecycle_gates']}
        self.stack = []
        self.started = self.completed = 0
        self.completed_cursor = 0
        self.completed_batch_id = None
        self.lease = None
        self.pending = {}
        self._hook_guard = object()
        self._hooks_bound = False
        self._index_stage = None
        self._index_pending = {}
        self._actual_attempts = {}
        self._completed_lives = {}
        self.completion_updates = []
        self.clock_guard_visits = self.clock_guard_work = 0
        self.pending_scan_capacity = self.pending_scan_work = self.pending_scan_count = 0
        try:
            # The initial bridge fingerprint predates this index. Do not bind a
            # changed clock and then treat its new graph as the original one.
            if getattr(self.bridge, '_resource_qualification', None) is not None:
                fail('不能对已绑定状态桥重建时钟核验。')
            self.charge_clock_guard()
            self.bridge._verify_clock_definition()
            self.index = compile_index(self)
            self.service = IndexedQualification(self)
            self._compile_clock_guard()
            self.charge_clock_guard()
            self.bridge._verify_clock_definition()
            for gate in self.gates.values():
                lid = gate['lifecycle_id']
                self._completed_lives[lid] = (self.encode(self.life_state(lid), 16384), 0)
        except (QualificationError, IndexError) as exc:
            fail(str(exc))

    def consume_reserved_index_work(self, work):
        audit = self.reaction.audit
        if type(work) is not int or not 0 <= work <= audit._tickets:
            fail('私有索引工作预留不一致。')
        audit._tickets -= work
        audit._work += work

    def assert_indexed_operation(self, operation):
        if (self.lease is not operation or self._index_stage not in ('prepare', 'admit')
                or self.reaction.active is None):
            fail('索引数学核验不在实际反应当前提交段。')
        self.reaction.audit._scope(self.reaction.active[0])

    def charge_clock_guard(self):
        from genshin_engine.resource_reaction_program import _bounded
        before = self.reaction.audit._work
        _bounded({'config': self.clock.config, 'lives': self.clock.lives,
                  'timers': self.clock.timers, 'by_life': self.clock.by_life},
                 byte_limit=8*1024*1024, charge_work=self.reaction.audit._charge)
        self.clock_guard_work += self.reaction.audit._work - before
        self.clock_guard_visits += 1

    def reserve_pending_scan(self, count):
        # One synchronous scan exclusively owns this reusable scratch allowance.
        # No input slice is made; two reference vectors (candidate and slice
        # assignment scratch) need <= 128 bytes/entry, plus fixed token/frame
        # space. The result is immediately copied into the original queue and
        # discarded before another scan. No cached clock.pending set is added.
        if type(count) is not int or not 0 <= count <= 40000:
            fail('只读队列扫描超过现有实际事件上限。')
        self.identities()
        try:
            self.budget.pay(work=1, storage=1024+128*max(0, count-self.pending_scan_capacity))
        except IndexError as exc:
            fail(str(exc))
        self.pending_scan_capacity = max(count, self.pending_scan_capacity)
        self.pending_scan_count += 1

    def _compile_clock_guard(self):
        # Reuse only original root-reachable strong references, never a rebuilt
        # canonical graph. The vector includes every descendant and is included
        # in the same aggregate reference ceiling and prepaid storage account.
        self.budget.pay(work=1, storage=512)
        seeds = self.index._seeds((self.clock.config, self.clock.lives,
                                  self.clock.timers, self.clock.by_life))
        self._clock_guard_records = self.index._vector(self.index._closure(seeds, descendants=True))
        self.budget.pay(work=len(self._clock_guard_records), storage=64)
        self._clock_guard_cost = self.index._root_cost()+sum(
            self.index._record_cost(rid) for rid in self._clock_guard_records)
        self._clock_guard_index = self.index

    def verify_clock_guard(self, bridge):
        # This is a fresh complete structure check at EVERY original guard
        # boundary, not a cached success or an admission/qualification ticket.
        # An unchanged original edge to a finite immutable scalar proves its
        # entire value unchanged without serializing that value again.
        if (bridge is not self.bridge or self.index is not self._clock_guard_index
                or self.index._closed):
            fail('时钟完整核验不属于原本次尚未结束的索引。')
        before = self.reaction.audit._work
        try:
            self.budget.pay(work=self._clock_guard_cost)
            self.identities()
            self.index._check(self.material(), self._clock_guard_records)
        except IndexError as exc:
            from genshin_engine.state_runtime import StateRuntimeError
            bridge._failed = True
            raise StateRuntimeError('state_threshold_authority',
                '实际生命周期、定时器或分组索引已偏离完整核验图: '+str(exc)) from exc
        self.clock_guard_work += self.reaction.audit._work-before
        self.clock_guard_visits += 1

    def charge_pending_item(self):
        self.reaction.audit._charge(1)
        self.pending_scan_work += 1

    def life_state(self, lid):
        self.reaction.audit._charge(32)
        applied = self.bridge._applied_lifecycles.get(lid)
        clock = self.clock._states.get(lid)
        value = {'applied': {k: applied[k] for k in ('generation_uid', 'revision', 'phase', 'control_sequence', 'time_us')}
                 if applied else None,
                 'clock': {k: clock[k] for k in ('generation_uid', 'revision', 'phase', 'created_us', 'established_us', 'end_us')}
                 if clock else None}
        for name in ('applied', 'clock'):
            row = value[name]
            if row is None:
                continue
            try:
                _text(row['generation_uid'], name + ' generation')
                _integer(row['revision'], name + ' revision', 1)
                for field, item in row.items():
                    if field.endswith('_us') or field == 'control_sequence':
                        _integer(item, name + ' ' + field)
            except QualificationError as exc:
                fail(str(exc))
            phases = {'created', 'established', 'closing', 'ended'} if name == 'applied' else {'pending_establishment', 'active', 'closing', 'ended'}
            if type(row['phase']) is not str or row['phase'] not in phases:
                fail('生命周期登记阶段不是当前协议的精确类型。')
        if (applied is None) != (clock is None):
            fail('实际已应用记录与时钟缺失状态不一致。')
        if clock is not None:
            declaration = self.clock.lives[lid]
            matching = {'created': 'pending_establishment', 'established': 'active', 'closing': 'closing', 'ended': 'ended'}
            if (clock['established_us'] != clock['created_us'] + declaration['establishment_delay_us']
                    or clock['end_us'] != clock['established_us'] + declaration['duration_us']
                    or clock['phase'] != matching[applied['phase']]
                    or clock['generation_uid'] != applied['generation_uid']
                    or clock['revision'] != applied['revision']):
                fail('实际已应用状态、时点与原生命周期声明不一致。')
        if self.counter_program is not None:
            state = self.ledger._counter_state
            if state['definition_fingerprint'] != self.counter_program._fingerprint:
                fail('累计状态不属于当前定义。')
            row = state['lifecycles'][lid]
            value['counter'] = {k: row[k] for k in ('phase', 'generation_uid', 'revision', 'created_us',
                'established_us', 'end_us', 'established_observed', 'closing_us')}
            if type(row['established_observed']) is not bool:
                fail('累计建立状态不是精确布尔值。')
            if applied is None or clock is None:
                if (applied is not None or clock is not None or row['phase'] != 'inactive'
                        or row['established_observed'] is not False
                        or any(row[k] is not None for k in ('generation_uid', 'revision', 'created_us',
                            'established_us', 'end_us', 'closing_us'))):
                    fail('累计生命周期不能借用未实际创建状态。')
            elif (type(row['phase']) is not str or row['phase'] != applied['phase']
                    or any(type(row[k]) is not type(clock[k]) or row[k] != clock[k]
                           for k in ('generation_uid', 'revision', 'created_us', 'established_us', 'end_us'))
                    or row['phase'] == 'established' and row['established_observed'] is not True
                    or row['phase'] == 'created' and row['established_observed'] is not False
                    or row['phase'] in ('created', 'established') and row['closing_us'] is not None
                    or row['phase'] in ('closing', 'ended') and type(row['closing_us']) is not int):
                fail('累计生命周期与当前实际控制状态不一致。')
        return value

    def encode(self, value, limit):
        try:
            return self.service._encoded(value, limit)[0]
        except QualificationError as exc:
            fail(str(exc))

    def material(self):
        return roots_of(self)

    def state(self):
        result = {'applied': self.bridge._applied_lifecycles, 'states': self.clock._states,
                'issued': self.clock._control_sequence, 'applied_sequence': self.bridge._applied_control_sequence}
        if self.counter_program is not None:
            result['counter_control'] = {k: self.ledger._counter_state[k] for k in
                ('definition_fingerprint', 'last_control_sequence', 'lifecycles')}
        return result

    def identities(self):
        if (self.ctx.state_runtime is not self.bridge or self.bridge.clock is not self.clock
                or self.ctx.shared_pools is not self.pool_runtime or self.pool_runtime.ledger is not self.ledger
                or self.pool_runtime.counter_program is not self.counter_program
                or self.ledger._counter_program is not self.counter_program
                or getattr(self.bridge, '_resource_qualification', None) is not self):
            fail('实际生命周期桥、时钟或资源账身份改变。')

    def before_control(self):
        if self.lease is not None:
            fail('资源资格正在提交期间，不能重入生命周期控制。')
        # The bridge calls this before issuing a fresh clock mutation. The
        # begin hook runs after issuance and therefore must not use this check.
        # A dirty timer/start must not launder an earlier unauthorized state edit.
        if self.bridge._active_delivery is None and not self.stack:
            for lid, (digest, _) in self._completed_lives.items():
                if self.encode(self.life_state(lid), 16384) != digest:
                    fail('新实际控制前生命周期偏离其已完成登记。')

    def bind_hooks(self, bridge):
        if self._hooks_bound or bridge is not self.bridge or self.ctx.state_runtime is not bridge:
            fail('实际控制钩子只能向原桥绑定一次。')
        self._hooks_bound = True
        self.clock._pending_scan_guard = bridge._reject_pending_scan
        guard = self._hook_guard
        # Both bound owner and private guard follow the same copy memo when a
        # complete execution state is forked. JSON cannot reproduce either
        # authority or the per-delivery token returned by begin.
        return (partial(self.begin_batch, _authority=guard),
                partial(self.complete_batch, _authority=guard),
                partial(self.leave_batch, _authority=guard))

    def hook_identity(self, record, authority, token=None):
        self.identities()
        if authority is not self._hook_guard or not self._hooks_bound or self.bridge._active_delivery is not record:
            fail('生命周期完成通知必须来自原桥当前实际批。')
        if token is not None and (not self.stack or self.stack[-1]['record'] is not record
                                  or self.stack[-1]['token'] is not token):
            fail('生命周期完成通知的原批令牌或嵌套顺序不一致。')

    def begin_batch(self, record, *, _authority=None):
        self.hook_identity(record, _authority)
        self.before_control()
        if (record['complete'] is not False or record['applied'] != 0
                or self.stack and self.stack[-1]['completed'] is not True):
            fail('新控制批不能跳过前批真实完成，或重新开始已执行批。')
        self.reaction.audit._charge(1)
        self.index.budget.pay(storage=384)
        token = object()
        self.started += 1
        self.stack.append({'record': record, 'token': token, 'completed': False, 'serial': self.started})
        return token

    def complete_batch(self, record, token=None, *, _authority=None):
        self.hook_identity(record, _authority, token)
        self.reaction.audit._charge(1)
        if (token is None or self.stack[-1]['completed'] or record['complete'] is not True
                or record['applied'] != len(record['steps'])
                or self.clock._control_sequence != self.bridge._applied_control_sequence):
            fail('生命周期原控制批没有真实应用完。')
        if (self.bridge._counter_enabled and self.ledger._counter_state['last_control_sequence']
                != self.bridge._applied_control_sequence):
            fail('累计末游标未完成实际控制。')
        self.reaction.audit._charge(1 + len(record['steps']))
        dirty = {step['lifecycle_id'] for step in record['steps'] if step['lifecycle_id'] in self._completed_lives}
        self.index.budget.pay(storage=512 + len(dirty)*384)
        last_steps = {step['lifecycle_id']: step for step in record['steps'] if step['lifecycle_id'] in dirty}
        for lid, step in last_steps.items():
            applied = self.bridge._applied_lifecycles.get(lid)
            if applied is None or any(type(applied[key]) is not type(step[source]) or applied[key] != step[source]
                    for key, source in (('generation_uid', 'generation_uid'), ('revision', 'revision'),
                        ('time_us', 'time_us'), ('control_sequence', 'sequence'))):
                fail('完成登记不是本批实际最后应用步骤。')
        candidates = {lid: self.encode(self.life_state(lid), 16384) for lid in dirty}
        serial = self.stack[-1]['serial']
        for lid, digest in candidates.items():
            self._completed_lives[lid] = (digest, serial)
        self.reaction.audit._charge(1 + len(dirty))
        self.completion_updates.append({'batch_serial': serial, 'dirty': sorted(dirty)})
        self.completed_cursor = self.bridge._applied_control_sequence
        self.completed_batch_id = record['batch']['id']
        self.completed += 1
        self.stack[-1]['completed'] = True

    def leave_batch(self, record, token=None, *, _authority=None):
        self.hook_identity(record, _authority, token)
        if token is None:
            fail('生命周期控制批嵌套身份不一致。')
        if not self.stack[-1]['completed'] and not self.bridge._failed:
            fail('未完成且未失败的实际控制批不能提前退出。')
        self.stack.pop()

    def facts(self, operation_id):
        self.identities()
        bridge = self.bridge
        if (self.stack or self.started != self.completed or bridge._failed
                or self.completed_cursor != bridge._applied_control_sequence
                or self.clock._control_sequence != self.completed_cursor
                or self.counter_program is not None and self.ledger._counter_state['last_control_sequence'] != self.completed_cursor):
            fail('生命周期状态尚未实际完成或完成后被改变。')
        gate = self.gates[operation_id]
        lid = gate['lifecycle_id']
        if self.encode(self.life_state(lid), 16384) != self._completed_lives[lid][0]:
            fail('所选生命周期不属于原实际完成批，空批或其他生命批不能刷新授权。')
        applied = bridge._applied_lifecycles.get(lid)
        clock = self.clock._states.get(lid)
        return {'schema_version': 2, 'lifecycle_id': lid, 'owner': self.clock.lives[lid]['owner'],
            'bridge_failed': bridge._failed, 'batch_status': 'completed' if self.completed else 'not_started',
            'issued_sequence': self.clock._control_sequence, 'applied_sequence': bridge._applied_control_sequence,
            'counter_sequence': self.ledger._counter_state['last_control_sequence'] if bridge._counter_enabled else None,
            'applied': {k: applied[k] for k in ('generation_uid', 'revision', 'phase', 'control_sequence', 'time_us')} if applied else None,
            'clock': {k: clock[k] for k in ('generation_uid', 'revision', 'phase', 'created_us', 'established_us', 'end_us')} if clock else None}

    def context(self):
        scope = self.reaction.audit._scope(self.reaction.active[0])[0]
        return {k: scope[k] for k in ('reaction_run_id', 'parent_run_id', 'origin', 'time_us')}

    def prepare(self, operation):
        if self.lease is not None:
            fail('生命周期资源资格准备不能重入。')
        self.lease = operation
        try:
            self.identities()
            scope = self.reaction.active[0]
            self.reaction.audit._scope(scope)
            # Retained prepared facts, private permit bytes and attempt entries
            # are distinct from the public audit slot's own prepayment.
            self.budget.pay(storage=2*16384+4096)
            attempt = (id(scope), operation['id'])
            self.index.budget.pay(work=1, storage=256)
            if attempt in self._actual_attempts:
                fail('同一次实际反应的资源资格尝试不能重试。')
            self._actual_attempts[attempt] = (scope, operation)
            # The pure object is retained only under this actual scope/op; it
            # cannot be supplied through public JSON or substitute for scope.
            occurrence = object()
            ticket = self.index.prepare(self.material(), operation['id'], occurrence)
            self._index_pending[operation['id']] = (scope, occurrence, ticket)
            self._index_stage = 'prepare'
            facts = self.facts(operation['id'])
            prepared = self.service.prepare(self.reaction.active[0], operation, self.context(), facts)
            row = copy.deepcopy(prepared['decision'])
            row.update(authority='actual_completed_state2_current_generation',
                batch_sequence=self.completed, completed_batch_id=self.completed_batch_id,
                window=self.gates[operation['id']]['window'], generation_policy=self.gates[operation['id']]['generation_policy'],
                created_us=facts['clock']['created_us'] if facts['clock'] else None,
                **self.context(), facts_fingerprint=self.encode(facts, 16384))
            self.pending[operation['id']] = (prepared, facts)
            return row
        except (QualificationError, IndexError) as exc:
            self.lease = None
            fail(str(exc))
        except Exception:
            self.lease = None
            raise

    def admit(self, operation):
        prepared, old_facts = self.pending[operation['id']]
        if self.lease is not operation:
            fail('生命周期资源资格消费不在当前唯一提交段。')
        try:
            scope, _, ticket = self._index_pending.pop(operation['id'])
            if self.reaction.active is None or self.reaction.active[0] is not scope:
                fail('索引许可不属于当前真实反应。')
            self.identities()
            self.index.admit(ticket, self.material())
            self._index_stage = 'admit'
            facts = self.facts(operation['id'])
            if facts != old_facts:
                fail('准备后生命周期资格改变。')
            if not prepared['decision']['qualified']:
                self.lease = None
                return False
            self.service.consume(prepared['permit'], self.reaction.active[0], operation, self.context(), facts)
            return True
        except (QualificationError, IndexError) as exc:
            self.lease = None
            fail(str(exc))
        except Exception:
            self.lease = None
            raise

    def release(self, operation_id):
        if self.lease is not None and self.lease['id'] != operation_id:
            fail('资源提交与生命周期许可身份不一致。')
        self.lease = None
        self._index_stage = None

    def verify_report(self):
        self.identities()
        try:
            self.index_summary = self.index.finish(self.material())
            for lid, (digest, _) in self._completed_lives.items():
                if self.encode(self.life_state(lid), 16384) != digest:
                    fail('报告时生命周期状态偏离实际已完成登记。')
        except IndexError as exc:
            fail(str(exc))
        # The owned prepared bytes remain separate from mutable display rows.
        # They are reserved with the slot before the resource commit.
        from genshin_engine.resource_reaction_program import _bounded, _canonical
        for row in self.reaction.audit._rows:
            expected = self.reaction.audit._qualification_materials.get(row['id'])
            actual = row.get('lifecycle_qualification')
            if expected is None and actual is None:
                continue
            _bounded(actual, byte_limit=16384, charge_work=self.reaction.audit._charge)
            if expected is None or _canonical(actual) != expected:
                fail('报告中的生命周期资格与实际准备回执不一致。')
