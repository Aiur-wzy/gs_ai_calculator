"""Bounded RQ2 caller-fact candidate. No actual state/RQ/ledger authority.

An opaque token here proves only this object's prepare/consume protocol. A real
adapter must independently obtain completed state batches and original reaction
objects. Native input must never be allowed to supply these facts directly.
"""
from __future__ import annotations

import hashlib
import json
import math


class QualificationError(ValueError):
    pass


class _Permit:
    __slots__ = ()


def _exact(value, keys, label):
    if type(value) is not dict or set(value) != set(keys):
        raise QualificationError('shape: '+label)


def _text(value, label):
    if type(value) is not str or not value or len(value) > 512:
        raise QualificationError('text: '+label)
    return value


def _integer(value, label, minimum=0):
    if type(value) is not int or not minimum <= value <= 10**15:
        raise QualificationError('integer: '+label)
    return value


class QualificationService:
    """Internal candidate service; ``reserve`` must be a trusted budget hook.

    Failed scans retain paid work. No game state, pool/CD, or source approval is
    written. Caller-fact snapshots are deliberately bounded and lack authority.
    """
    def __init__(self, gates, operations, pools, lives, *, reserve=None,
                 work_limit=200000, encoding_limit=256*1024*1024):
        self.reserve = reserve or (lambda _work: None)
        self.work_limit, self.encoding_limit = work_limit, encoding_limit
        self._usage = {'work': 0, 'encoding_bytes': 0, 'attempts': 0, 'permits': 0, 'published_bytes': 0}
        self._busy = True
        self._permits = {}
        self._attempts = {}
        self._operations = operations
        self._declarations = {'gates': gates, 'operations': operations, 'pools': pools, 'lives': lives}
        self._index = {}
        try:
            checked, _ = self._encoded(self._declarations, 256*1024)
            if type(gates) is not list or len(gates) > 256:
                raise QualificationError('gate_limit')
            if not all(type(x) is dict for x in (operations, pools, lives)):
                raise QualificationError('catalog_shape')
            for gate in gates:
                _exact(gate, ('operation_id', 'lifecycle_id', 'window', 'generation_policy'), 'gate')
                oid, lid = _text(gate['operation_id'], 'operation'), _text(gate['lifecycle_id'], 'lifecycle')
                if oid in self._index or oid not in operations or lid not in lives:
                    raise QualificationError('gate_reference')
                op = operations[oid]
                if type(op) is not dict or op.get('id') != oid:
                    raise QualificationError('operation_identity')
                pid = _text(op.get('pool_id'), 'pool')
                if pid not in pools or type(pools[pid]) is not dict or type(lives[lid]) is not dict:
                    raise QualificationError('pool_life_reference')
                owner = _text(pools[pid].get('owner'), 'pool owner')
                if owner != lives[lid].get('owner'):
                    raise QualificationError('owner_mismatch')
                if gate['window'] != 'established_until_end' or gate['generation_policy'] != 'current_at_resource_attempt':
                    raise QualificationError('unsupported_policy')
                _, op_bytes = self._encoded(op, 16*1024)
                self._index[oid] = {'operation': op, 'operation_bytes': op_bytes, 'pool_id': pid,
                                    'lifecycle_id': lid, 'owner': owner}
            self._program = checked
        finally:
            self._busy = False

    @property
    def usage(self):
        return dict(self._usage)

    def _charge(self, work=1, encoding_bytes=0):
        if self._usage['work']+work > self.work_limit or self._usage['encoding_bytes']+encoding_bytes > self.encoding_limit:
            raise QualificationError('qualification_limit')
        self.reserve(work)
        self._usage['work'] += work
        self._usage['encoding_bytes'] += encoding_bytes

    def _encoded(self, value, size_limit):
        """Pay before visiting each child and before encoding each string.

        The upper byte charge is intentionally conservative (6*codepoints),
        including JSON control-character escapes. The per-value upper bound is
        checked before any oversized string encode or whole JSON materialization.
        There is no rollback of actual failed/repeated validation work.
        """
        seen = set()
        local_bytes = 0
        def bytes_before_encoding(amount):
            nonlocal local_bytes
            self._charge(encoding_bytes=amount)
            local_bytes += amount
            if local_bytes > size_limit:
                raise QualificationError('qualification_bytes_limit')
        def visit(item, depth=0):
            self._charge()
            if depth > 24:
                raise QualificationError('qualification_depth_limit')
            kind = type(item)
            if item is None or kind is bool:
                bytes_before_encoding(5)
            elif kind is int:
                if abs(item) > 10**15:
                    raise QualificationError('integer_limit')
                bytes_before_encoding(17)
            elif kind is float:
                if not math.isfinite(item):
                    raise QualificationError('nonfinite')
                bytes_before_encoding(32)
            elif kind is str:
                bytes_before_encoding(6*len(item)+2)
                try: item.encode('utf-8')
                except UnicodeError as exc: raise QualificationError('encoding') from exc
            elif kind in (dict, list):
                if id(item) in seen:
                    raise QualificationError('cycle')
                seen.add(id(item))
                bytes_before_encoding(2+2*len(item))
                for key in item:
                    self._charge()
                    if kind is dict:
                        if type(key) is not str:
                            raise QualificationError('key_type')
                        visit(key, depth+1)
                        visit(item[key], depth+1)
                    else:
                        visit(key, depth+1)
                seen.remove(id(item))
            else:
                raise QualificationError('json_type')
        visit(value)
        # All inputs have now been paid and validated. Sorting/encoding is
        # bounded by that full traversal; no caller iterator can run here.
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
        if len(encoded) > size_limit:
            raise QualificationError('qualification_bytes_limit')
        return hashlib.sha256(encoded).hexdigest(), encoded

    def _operation(self, operation):
        self._charge()
        actual, _ = self._encoded(self._declarations, 256*1024)
        if actual != self._program:
            raise QualificationError('program_changed')
        if type(operation) is not dict:
            raise QualificationError('operation_identity')
        oid = _text(operation.get('id'), 'operation')
        target = self._index.get(oid)
        if target is None or operation is not target['operation'] or self._operations.get(oid) is not operation:
            raise QualificationError('operation_identity')
        _, encoded = self._encoded(operation, 16*1024)
        if encoded != target['operation_bytes']:
            raise QualificationError('operation_changed')
        return oid, target

    def _facts(self, context, facts, target):
        digest, _ = self._encoded({'context': context, 'facts': facts}, 16*1024)
        _exact(context, ('reaction_run_id', 'parent_run_id', 'origin', 'time_us'), 'context')
        _text(context['reaction_run_id'], 'reaction_run'); _text(context['parent_run_id'], 'parent_run')
        _integer(context['origin'], 'origin'); time = _integer(context['time_us'], 'time')
        _exact(facts, ('schema_version', 'lifecycle_id', 'owner', 'bridge_failed', 'batch_status', 'issued_sequence',
                      'applied_sequence', 'counter_sequence', 'applied', 'clock'), 'facts')
        if type(facts['schema_version']) is not int or facts['schema_version'] != 2 or type(facts['bridge_failed']) is not bool:
            raise QualificationError('state_schema')
        if (_text(facts['lifecycle_id'], 'fact life') != target['lifecycle_id']
                or _text(facts['owner'], 'fact owner') != target['owner']):
            raise QualificationError('fact_life_binding')
        for field in ('issued_sequence', 'applied_sequence'):
            _integer(facts[field], field)
        if facts['counter_sequence'] is not None:
            _integer(facts['counter_sequence'], 'counter_sequence')
        if facts['bridge_failed'] or facts['batch_status'] in ('failed', 'in_progress'):
            raise QualificationError('applied_barrier_pending')
        if facts['batch_status'] not in ('not_started', 'completed'):
            raise QualificationError('batch_status')
        cursor = facts['applied_sequence']
        if facts['issued_sequence'] != cursor or facts['counter_sequence'] not in (None, cursor):
            raise QualificationError('applied_cursor_pending')
        applied, clock = facts['applied'], facts['clock']
        if applied is None or clock is None:
            if applied is not None or clock is not None or facts['batch_status'] == 'not_started' and cursor:
                raise QualificationError('applied_identity_pending')
            return digest, {'qualified': False, 'reason': 'not_started', 'generation_uid': None,
                            'revision': None, 'phase': 'not_started', 'control_sequence': cursor,
                            'established_us': None, 'end_us': None}
        if facts['batch_status'] != 'completed':
            raise QualificationError('applied_barrier_pending')
        _exact(applied, ('generation_uid', 'revision', 'phase', 'control_sequence', 'time_us'), 'applied')
        _exact(clock, ('generation_uid', 'revision', 'phase', 'created_us', 'established_us', 'end_us'), 'clock')
        for item in (applied, clock):
            _text(item['generation_uid'], 'generation'); _integer(item['revision'], 'revision', 1)
        for field in ('created_us', 'established_us', 'end_us'):
            _integer(clock[field], field)
        _integer(applied['control_sequence'], 'life cursor', 1); _integer(applied['time_us'], 'control time')
        if (applied['generation_uid'] != clock['generation_uid'] or applied['revision'] != clock['revision']
                or applied['control_sequence'] > cursor or applied['time_us'] > time
                or not clock['created_us'] <= clock['established_us'] < clock['end_us']
                or applied['time_us'] < clock['created_us']):
            raise QualificationError('applied_identity_pending')
        phase = applied['phase']
        if phase not in ('created', 'established', 'closing', 'ended'):
            raise QualificationError('phase')
        matching = {'created': 'pending_establishment', 'established': 'active', 'closing': 'closing', 'ended': 'ended'}
        if matching[phase] != clock['phase'] or phase == 'established' and applied['time_us'] < clock['established_us']:
            raise QualificationError('applied_phase_pending')
        eligible = phase == 'established' and clock['established_us'] <= time < clock['end_us']
        reason = 'eligible' if eligible else 'not_established' if phase == 'created' and time < clock['end_us'] else 'ended'
        return digest, {'qualified': eligible, 'reason': reason, 'generation_uid': applied['generation_uid'],
                        'revision': applied['revision'], 'phase': phase, 'control_sequence': cursor,
                        'established_us': clock['established_us'], 'end_us': clock['end_us']}

    def prepare(self, scope, operation, context, facts):
        if self._busy:
            raise QualificationError('qualification_busy')
        self._busy = True
        try:
            if scope is None:
                raise QualificationError('scope_identity')
            oid, target = self._operation(operation)
            attempt_key = (id(scope), id(operation))
            if attempt_key in self._attempts:
                raise QualificationError('attempt_repeated')
            digest, decision = self._facts(context, facts, target)
            decision.update(authority='candidate_only_caller_facts', operation_id=oid,
                            lifecycle_id=target['lifecycle_id'], pool_id=target['pool_id'], program_fingerprint=self._program)
            _, encoded = self._encoded(decision, 16*1024)
            if self._usage['published_bytes']+len(encoded) > 8*1024*1024 or self._usage['attempts'] >= 20000:
                raise QualificationError('qualification_publish_limit')
            self._charge()
            permit = None
            if decision['qualified']:
                permit = _Permit()
                self._permits[permit] = (scope, operation, digest, encoded)
                self._usage['permits'] += 1
            self._attempts[attempt_key] = (scope, operation)
            self._usage['attempts'] += 1
            self._usage['published_bytes'] += len(encoded)
            return {'decision': decision, 'permit': permit}
        finally:
            self._busy = False

    def consume(self, permit, scope, operation, context, facts):
        if self._busy:
            raise QualificationError('qualification_busy')
        self._busy = True
        try:
            self._charge()
            if type(permit) is not _Permit or permit not in self._permits:
                raise QualificationError('permit_identity')
            old_scope, old_operation, old_digest, encoded = self._permits[permit]
            if old_scope is not scope or old_operation is not operation:
                raise QualificationError('permit_context')
            _, target = self._operation(operation)
            digest, decision = self._facts(context, facts, target)
            if digest != old_digest or not decision['qualified']:
                raise QualificationError('permit_stale')
            self._charge(work=1+len(encoded)//128, encoding_bytes=len(encoded))
            result = json.loads(encoded)
            self._charge()
            del self._permits[permit]
            return result
        finally:
            self._busy = False
