"""Actual-queue adapter for qualified counter output observations."""
from __future__ import annotations

import copy
import hashlib

from genshin_engine.resource_counter_frames import FrameError, FrameStore
from genshin_engine.resource_observation_authority import (
    ObservationAuthority,
    ObservationError,
)
from genshin_engine.shared_pools import _canonical
from genshin_engine.snapshot_capture import (
    MAX_SNAPSHOT_CAPTURE_BYTES,
    SnapshotCapturePending,
)

FRAME_RUNTIME_LIMITS = {'verification_bytes': 256*1024*1024,
                        'snapshot_bytes': MAX_SNAPSHOT_CAPTURE_BYTES}


def pending(code, **details):
    raise SnapshotCapturePending(code, '资源输出帧缺失、身份或有限执行预算未满足。',
                                 'mechanics_model.resource_counter_views', **details)


def gate_qualification(rule, inst, event):
    """Read only after hit() accepted the current receipt for this instance."""
    result = {'kind': 'not_required', 'instance_id': inst['instance_id'], 'event_run_id': event['run_id']}
    if rule.get('resource_gate'):
        result.update(kind='shared_pool', consumer_id=rule['resource_gate']['consumer_id'])
    elif rule.get('quota'):
        result.update(kind='quota', rule_id=rule['id'])
    return result


class CounterFrameRuntime:
    def __init__(self, ctx, config, pools, reads):
        self.ctx, self.reads, self.ledger = ctx, reads, pools.ledger
        self.pools, self.counter_program = pools, pools.counter_program
        self.verification_bytes = 0
        self.verification_exhausted = False
        self.capture_trace = []
        self.contract = self._contract()
        self.references = {c['reference_id'] for c in self.contract['consumers'] if c['damage_system'] == 'classic'}
        self.store = FrameStore(config, pools.counter_program, self.contract, encode=self.encode)
        self.authority = ObservationAuthority(self.ledger, encode=self.encode)
        self.definition_bytes = self.encode(self._definitions())
        self._active = None

    def _definitions(self):
        def math_counter(program):
            return {'config': program._counters._config, 'pools': program._counters._pool_config,
                    'lives': program._counters._lives, 'fingerprint': program._fingerprint}
        return {'actual_rules': self.ctx.rules, 'model': {k: self.ctx.model.get(k) for k in (
                    'shared_pools', 'state_runtime', 'resource_counters', 'resource_counter_views')},
                'ledger_pool': self.ledger._config, 'counter': self.counter_program._config,
                'counter_pool': self.counter_program._pool_config,
                'counter_lives': self.counter_program._lives,
                'current_views': self.reads.program._views, 'capture_views': self.store._program._views,
                'current_math': math_counter(self.reads.program), 'capture_math': math_counter(self.store._program)}

    def validate_binding(self):
        if (self.ctx.shared_pools is not self.pools or self.pools.ledger is not self.ledger
                or self.pools.counter_program is not self.counter_program
                or self.ledger._counter_program is not self.counter_program):
            pending('counter_frame_ledger_binding')
        if self.encode(self._definitions()) != self.definition_bytes:
            pending('counter_frame_definition_binding')

    def _contract(self):
        model = self.ctx.model
        descriptors = {d['hit_id']: d for d in model['hit_events']}
        for pattern in model['hit_patterns']:
            descriptors.update({f"{pattern['id']}:{i}": pattern for i in range(1, len(pattern['offsets'])+1)})
        consumers = [{'reference_id': descriptors[h['id']]['self_snapshot_event'],
                      'character': h['character'], 'damage_system': h['damage_system'], 'hit_id': h['id']}
                     for h in self.ctx.template['hits'] if descriptors.get(h['id'], {}).get('self_snapshot_event')]
        return {'rules': model['rules'], 'consumers': consumers,
                'captures': model.get('snapshot_captures', []), 'matches': self.ctx.matches}

    def encode(self, value):
        if self.verification_exhausted:
            pending('counter_frame_verification_limit', bytes=self.verification_bytes)
        encoded = _canonical(value)
        self.verification_bytes += len(encoded)
        if self.verification_bytes > FRAME_RUNTIME_LIMITS['verification_bytes']:
            self.verification_exhausted = True
            pending('counter_frame_verification_limit', bytes=self.verification_bytes)
        return encoded

    def begin(self, event):
        if event['id'] not in self.references:
            return
        try:
            self.authority.begin(event)
            self._active = event
        except ObservationError as exc:
            pending('counter_frame_observation', reason=str(exc))

    def end(self, event):
        if self._active is event:
            self.authority.end(event)
            self._active = None

    def bind(self, event):
        if self._active is not None:
            try:
                self.authority.bind(event)
            except ObservationError as exc:
                pending('counter_frame_observation', reason=str(exc))

    def _prepare(self, event, snapshot):
        if event['id'] not in self.references:
            return None
        context = {'origin': event['origin'], 'run_id': event['run_id'],
                   'event_id': event['id'], 'time_us': snapshot['time']}
        members = self.store.demand(context, snapshot)
        if not members:
            return None
        self.validate_binding()
        permit = self.authority.issue(event)
        actual = self.authority.inspect(permit, self.ledger)
        if actual != context:
            pending('counter_frame_observation', reason='actual_context_mismatch')
        candidate = self.store.prepare(actual, snapshot, self.ledger.counter_snapshot(), members,
                                       current_contract=self._contract())
        receipt = self.store.capture_preview(candidate)
        receipt.update(authority='mechanics_committed_ledger_observation', cycle=actual['origin'],
                       t=actual['time_us']/1_000_000, time_us=actual['time_us'],
                       event_id=actual['event_id'], run_id=actual['run_id'])
        return candidate, permit, receipt

    def _snapshot_storage(self, snapshot, receipt=None):
        ctx = self.ctx
        # An explicit capture's existing {snapshot,receipt} counts its one real
        # snapshot object. A newly necessary named observation shares this same
        # cumulative budget, rather than adding a second copy of its bytes.
        value = {'snapshot': snapshot, **({'receipt': receipt} if receipt is not None else {})}
        size = ctx.capture_bytes + len(self.encode(value)) + bool(ctx.capture_entries)
        if size > FRAME_RUNTIME_LIMITS['snapshot_bytes']:
            pending('counter_frame_snapshot_limit', bytes=ctx.capture_bytes, attempted=size)
        return size

    def _reserve(self, permit, work):
        self.authority.publish(permit, self.ledger, work,
                               lambda n: self.ctx._reserve_events(n, 'resource_counter_frame'))

    def observe(self, event, snapshot):
        try:
            prepared = self._prepare(event, snapshot)
            if prepared is None:
                return
            candidate, permit, receipt = prepared
            size = self._snapshot_storage(snapshot)
            self.store.commit(candidate, lambda work: self._reserve(permit, work))
            # Prepared objects and scalar assignments only after shared reserve.
            self.ctx.capture_bytes = size
            self.ctx.capture_entries += 1
            self.capture_trace.append(receipt)
        except (FrameError, ObservationError) as exc:
            pending('counter_frame_capture', reason=str(exc))

    def capture(self, event, row, receipt):
        ctx = self.ctx
        if ctx.attribute_runtime and ctx.attribute_runtime.graph:
            ctx.attribute_runtime.stage(event, 'capture', observation=True)
        snapshot = ctx.snapshot()
        try:
            prepared = self._prepare(event, snapshot)
            preview = {'time': snapshot['time'], 'frontline': snapshot['frontline'], 'state': snapshot['state']}
            receipt['snapshot_fingerprint'] = hashlib.sha256(self.encode(preview)).hexdigest()
            size = self._snapshot_storage(snapshot, receipt)
            trace_receipt, row_receipt = copy.deepcopy(receipt), copy.deepcopy(receipt)
            key = (event['origin'], event['id'])
            if prepared is not None:
                candidate, permit, frame_receipt = prepared
                self.store.commit(candidate, lambda work: self._reserve(permit, work))
                self.capture_trace.append(frame_receipt)
            ctx.capture_bytes = size
            ctx.capture_entries += 1
            ctx.snapshots[key] = snapshot
            ctx.capture_receipts[key] = receipt
            ctx.pending_captures.pop(key)
            ctx.capture_trace.append(receipt)
            ctx.trace.append(trace_receipt)
            row['snapshot_capture'] = row_receipt
        except (FrameError, ObservationError) as exc:
            pending('counter_frame_capture', reason=str(exc))

    def read(self, snapshot, expr, rule, effect, inst, recipient, event):
        self.validate_binding()
        member = {'rule_id': rule['id'], 'effect_id': effect['id'],
                  'instance_id': inst['instance_id'], 'recipient': recipient}
        hit = {'event': event['event'], 'damage_system': event['damage_system'],
               'event_id': event['id'], 'run_id': event['run_id'], 'origin': event['origin'],
               'time_us': self.ctx.time}
        bundle = {}
        def prepare(frame):
            reading = copy.deepcopy(frame['reading'])
            reading.update(sampling_authority='mechanics_committed_ledger_observation',
                           qualification='frozen_member_current_gate_accepted')
            receipt = {'type': 'counter_view_read', 'receipt_id': f'counter-view:{len(self.reads._trace)+1}',
                       'event_id': event['id'], 'run_id': event['run_id'], 'cycle': event['origin'],
                       't': self.ctx.time/1_000_000, 'time_us': self.ctx.time,
                       'rule_id': rule['id'], 'effect_id': effect['id'], 'instance_id': inst['instance_id'],
                       'effect_source_id': rule['source_id'], 'domain': 'self', 'sample': 'self_frame',
                       'frame_selection': 'historical_reference', 'frame_id': frame['frame_id'],
                       'capture_context': copy.deepcopy(frame['capture_context']),
                       'character': recipient, 'expression': copy.deepcopy(expr), 'reading': reading,
                       'gate_qualification': gate_qualification(rule, inst, event),
                       'source_value': reading['numerator']/reading['denominator'], 'value': frame['value'],
                       'single_value': frame['single_value'], 'layer_count': frame['layer_count'],
                       'combined_value': frame['combined_value']}
            size = self.reads._bytes + len(self.encode(receipt)) + bool(self.reads._trace)
            from genshin_engine.resource_counter_view_runtime import READ_LIMITS
            if len(self.reads._trace) >= READ_LIMITS['reads'] or size > READ_LIMITS['trace_bytes']:
                pending('counter_frame_read_limit')
            bundle.update(receipt=receipt, sample=copy.deepcopy(receipt), size=size)
        try:
            frame = self.store.read(snapshot, member, hit, layers=len(inst['expires']),
                                    current_contract=self._contract(),
                                    reserve=lambda n: self.ctx._reserve_events(n, 'resource_counter_view'),
                                    prepare_receipt=prepare)
            if bundle:  # A same-request re-read of an issued frame is read-only.
                self.reads._bytes = bundle['size']
                self.reads._trace.append(bundle['receipt'])
                self.ctx.samples.append(bundle['sample'])
            return frame['value']
        except FrameError as exc:
            pending('counter_frame_read', reason=str(exc))

    def report(self):
        return {'contract': 'counter-self-frame-actual-v2',
                'capture_trace': copy.deepcopy(self.capture_trace),
                'usage': self.store.usage(), 'verification_bytes': self.verification_bytes,
                'snapshot_storage_bytes': self.ctx.capture_bytes,
                'limits': dict(FRAME_RUNTIME_LIMITS), 'source_approval': 'not_evaluated_by_engine'}

    def boundary(self, snapshot, event):
        descriptor = event['data']['descriptor']
        ref = descriptor.get('self_snapshot_event')
        cid = event['data']['hit']['character']
        if (not ref or event['data']['hit']['damage_system'] != 'classic'
                or not any(c['hit_id'] == event['id'] and c['reference_id'] == ref
                           for c in self.contract['consumers'])):
            return None
        self.validate_binding()
        context = {'event_id': ref}
        try:
            members = [m for m in self.store.demand(context, snapshot) if m['recipient'] == cid]
            if not members:
                return None
            self.store._contract_ok(self._contract())
            signature = [r for r in self.store.signature(snapshot) if r['member'][3] == cid]
            for item in signature:
                rid, _, ordinal, _ = item['member']
                rule = self.ctx.rules[rid]
                if rule.get('quota') or rule.get('resource_gate'):
                    iid = snapshot['state'][rid]['instances'][ordinal]['instance_id']
                    current = self.ctx.state[rid]['instances']
                    item['current_gate_instance_ordinal'] = next((i for i, inst in enumerate(current)
                                                                 if inst['instance_id'] == iid), None)
            return signature
        except FrameError as exc:
            pending('counter_frame_boundary', reason=str(exc))
