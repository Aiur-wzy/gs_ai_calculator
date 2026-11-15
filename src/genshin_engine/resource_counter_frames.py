"""Pure immutable counter frames, separate from game/queue authority.

The supplied counter state, snapshot and occurrence remain caller facts. This
store proves its own issued-object binding and atomic publication only. The
mechanics adapter selects the actual committed ledger and observation.
"""
from __future__ import annotations

import copy
import hashlib
import math

from genshin_engine.attribute_graph import linear_value
from genshin_engine.mechanics_schema import EXPR
from genshin_engine.registry import BENEFIT_TYPES
from genshin_engine.resource_counter_views import CounterViewProgram
from genshin_engine.shared_pool_runtime import ALLOWED_ZONES
from genshin_engine.shared_pools import _canonical, _schema

LIMITS = {'frames': 4096, 'members': 32768, 'frame_bytes': 8*1024*1024,
          'reads': 40000, 'trace_bytes': 8*1024*1024}
CURRENT_EXPRESSION = copy.deepcopy(next(branch for branch in EXPR['oneOf']
    if branch.get('properties', {}).get('kind', {}).get('const') == 'counter_view'))
SELF_EXPRESSION = copy.deepcopy(CURRENT_EXPRESSION)
SELF_EXPRESSION['properties']['sample'] = {'const': 'self_frame'}


class FrameError(ValueError):
    pass


def canonical(value):
    try:
        return _canonical(value)
    except (TypeError, ValueError, RecursionError, OverflowError) as exc:
        raise FrameError('frame_json') from exc


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def number(value, *, minimum=0, maximum=10**15):
    if type(value) is not int or not minimum <= value <= maximum:
        raise FrameError('frame_integer')
    return value


def text(value):
    if type(value) is not str or not value:
        raise FrameError('frame_identity')
    return value


def member_key(member):
    if not isinstance(member, dict) or set(member) != {'rule_id', 'effect_id', 'instance_id', 'recipient'}:
        raise FrameError('frame_member')
    return tuple(text(member[k]) for k in ('rule_id', 'effect_id', 'instance_id', 'recipient'))


def validate_snapshot(snapshot, encode=canonical):
    if (type(snapshot) is not dict or type(snapshot.get('state')) is not dict
            or type(snapshot.get('time')) is not int or type(snapshot.get('frontline')) is not str):
        raise FrameError('frame_snapshot_shape')
    number(snapshot['time'])
    text(snapshot['frontline'])
    for rid, state in snapshot['state'].items():
        text(rid)
        if type(state) is not dict or type(state.get('instances')) is not list:
            raise FrameError('frame_snapshot_instances')
        seen = set()
        for inst in state['instances']:
            if (type(inst) is not dict or type(inst.get('beneficiaries')) is not list
                    or type(inst.get('expires')) is not list):
                raise FrameError('frame_snapshot_members')
            iid = text(inst.get('instance_id'))
            if iid in seen:
                raise FrameError('frame_snapshot_instance_duplicate')
            seen.add(iid)
            recipients = [text(cid) for cid in inst['beneficiaries']]
            if not recipients or len(recipients) != len(set(recipients)):
                raise FrameError('frame_snapshot_beneficiaries')
            if not inst['expires'] or any(number(end) <= snapshot['time'] for end in inst['expires']):
                raise FrameError('frame_snapshot_layers')
    encode(snapshot)


class _Prepared:
    """An opaque, single-use token; all mutable content remains issuer-private."""
    __slots__ = ()


class FrameStore:
    def __init__(self, views, counters, contract, *, limits=None, encode=None):
        self._encode = encode or canonical
        if type(views) is not dict or type(views.get('schema_version')) is not int or views['schema_version'] != 2:
            raise FrameError('frame_version')
        if (type(contract) is not dict or set(contract) != {'rules', 'consumers', 'captures', 'matches'}
                or any(type(contract[key]) is not list for key in contract)):
            raise FrameError('frame_contract')
        math_config = copy.deepcopy(views)
        math_config['schema_version'] = 1  # Reuse reviewed arithmetic; no public v2 schema is opened.
        self._program = CounterViewProgram(math_config, counters)
        self._contract = copy.deepcopy(contract)
        self._contract_hash = self._digest(contract)
        if len(self._canonical(contract)) > 2*1024*1024 or set(contract) != {'rules', 'consumers', 'captures', 'matches'}:
            raise FrameError('frame_contract')
        self._rules, self._effects = {}, {}
        for rule in contract['rules']:
            if (type(rule) is not dict or not {'id', 'owner', 'effects', 'beneficiaries'} <= set(rule)
                    or type(rule.get('effects')) is not list
                    or type(rule.get('beneficiaries')) is not dict
                    or rule['beneficiaries'].get('scope') not in ('locked', 'current_frontline')):
                raise FrameError('frame_reader_shape')
            rid = text(rule['id'])
            text(rule.get('owner'))
            if rid in self._rules:
                raise FrameError('frame_duplicate_rule')
            self._rules[rid] = copy.deepcopy(rule)
            for effect in rule['effects']:
                if type(effect) is not dict or not {'id', 'value', 'domain', 'benefit_type_id'} <= set(effect):
                    raise FrameError('frame_effect_shape')
                if 'filter' in effect and type(effect['filter']) is not dict:
                    raise FrameError('frame_effect_filter')
                text(effect['id'])
                text(effect['benefit_type_id'])
                text(effect['domain'])
                expr = effect['value']
                if isinstance(expr, dict) and (expr.get('kind') == 'counter_view' or expr.get('sample') == 'self_frame'):
                    issues = []
                    schema = SELF_EXPRESSION if expr.get('sample') == 'self_frame' else CURRENT_EXPRESSION
                    _schema(expr, schema, 'counter_self_expression', issues)
                    if issues:
                        raise FrameError('frame_expression_schema')
                if not isinstance(expr, dict) or expr.get('sample') != 'self_frame':
                    continue
                view = self._program._views.get(expr.get('view_id'))
                if (effect['domain'] != 'self' or expr.get('kind') != 'counter_view'
                        or view is None or view['owner'] != rule['owner']
                        or BENEFIT_TYPES.get(effect['benefit_type_id'], {}).get('zone') not in ALLOWED_ZONES
                        or effect.get('filter', {}).get('event', 'hit') != 'hit'):
                    raise FrameError('frame_reader_scope')
                key = (rid, text(effect['id']))
                if key in self._effects:
                    raise FrameError('frame_duplicate_effect')
                self._effects[key] = copy.deepcopy(effect)
        consumers = set()
        for consumer in contract['consumers']:
            if type(consumer) is not dict or not {'reference_id', 'character', 'damage_system', 'hit_id'} <= set(consumer):
                raise FrameError('frame_consumer_shape')
            for key in ('reference_id', 'character', 'damage_system', 'hit_id'):
                text(consumer[key])
            if consumer['hit_id'] in consumers:
                raise FrameError('frame_consumer_duplicate')
            consumers.add(consumer['hit_id'])
        self._limits = dict(LIMITS)
        if limits is not None:  # Unit-test engineering caps, never a native input field.
            if type(limits) is not dict or set(limits)-set(LIMITS):
                raise FrameError('frame_limits')
            for key, value in limits.items():
                self._limits[key] = number(value, maximum=LIMITS[key])
        self._frames, self._occurrences, self._reads = {}, set(), {}
        self._pending, self._pending_record, self._version = None, None, 0
        self._members, self._frame_bytes, self._trace_bytes = 0, 2, 2
        self._capture_work, self._read_work = 0, 0
        self._publishing = False

    def _not_publishing(self):
        if self._publishing:
            raise FrameError('frame_reentrant_publication')

    def _canonical(self, value):
        return self._encode(value)

    def _digest(self, value):
        return hashlib.sha256(self._canonical(value)).hexdigest()

    def _validate_snapshot(self, value):
        validate_snapshot(value, self._canonical)

    def capture_preview(self, candidate):
        if candidate is not self._pending or self._pending_record is None:
            raise FrameError('frame_candidate_authority')
        return copy.deepcopy(self._pending_record['return_value'])

    def _contract_ok(self, contract):
        if self._digest(contract) != self._contract_hash:
            raise FrameError('frame_program_mismatch')

    def demand(self, context, snapshot):
        """Conservative facts-only demand; includes filter overrides by not pruning filters."""
        if type(context) is not dict:
            raise FrameError('frame_context')
        self._validate_snapshot(snapshot)
        ref = text(context.get('event_id'))
        recipients = {text(c['character']) for c in self._contract['consumers']
                      if c['reference_id'] == ref and c['damage_system'] == 'classic'}
        found = []
        for (rid, eid), _effect in sorted(self._effects.items()):
            rule = self._rules[rid]
            for inst in snapshot['state'].get(rid, {}).get('instances', []):
                for recipient in sorted(recipients.intersection(inst['beneficiaries'])):
                    if rule['beneficiaries']['scope'] == 'current_frontline' and snapshot['frontline'] != recipient:
                        continue
                    found.append({'rule_id': rid, 'effect_id': eid, 'instance_id': inst['instance_id'], 'recipient': recipient})
        return sorted(found, key=member_key)

    def prepare(self, context, snapshot, state, members, *, current_contract):
        self._not_publishing()
        self._contract_ok(current_contract)
        if type(context) is not dict or set(context) != {'origin', 'run_id', 'event_id', 'time_us'}:
            raise FrameError('frame_context')
        origin = number(context['origin'])
        number(context['time_us'])
        text(context['run_id'])
        text(context['event_id'])
        self._validate_snapshot(snapshot)
        if snapshot['time'] != context['time_us']:
            raise FrameError('frame_time')
        if type(members) is not list:
            raise FrameError('frame_members_shape')
        keys = [member_key(m) for m in members]
        if len(keys) != len(set(keys)) or sorted(keys) != [member_key(m) for m in self.demand(context, snapshot)]:
            raise FrameError('frame_demand_incomplete')
        if not members:
            return None
        occurrence = (origin, context['event_id'])
        if occurrence in self._occurrences or id(snapshot) in self._frames:
            raise FrameError('frame_duplicate_occurrence')
        rows, readings = [], {}
        for key in sorted(keys):
            rid, eid, iid, recipient = key
            effect = self._effects[(rid, eid)]
            instances = [i for i in snapshot['state'][rid]['instances'] if i['instance_id'] == iid]
            if len(instances) != 1:
                raise FrameError('frame_instance')
            inst = instances[0]
            if not inst['expires'] or any(number(end) <= snapshot['time'] for end in inst['expires']):
                raise FrameError('frame_inactive_layer')
            view_id = effect['value']['view_id']
            view = self._program._views[view_id]
            kind = view['key']['kind']
            resource_key = ('total' if kind == 'total' else view['owner'] if kind == 'owner' else
                            view['key']['character_id'] if kind == 'character' else recipient)
            reading_key = (view_id, resource_key)
            if reading_key not in readings:
                readings[reading_key] = self._program.read(view_id, state, recipient=recipient)
            reading = readings[reading_key]
            rows.append({'member': list(key), 'reading': copy.deepcopy(reading),
                         'instance_ordinal': snapshot['state'][rid]['instances'].index(inst),
                         'layer_count': len(inst['expires']), 'expression': copy.deepcopy(effect['value']),
                         'benefit_type_id': effect['benefit_type_id']})
        receipt = {'type': 'counter_frame_capture', 'authority': 'mechanics_committed_ledger_observation',
                   'frame_id': f'counter-frame:{self._version+1}', 'context': copy.deepcopy(context),
                   'cycle': context['origin'], 'time_us': context['time_us'], 't': context['time_us']/1_000_000,
                   'event_id': context['event_id'], 'run_id': context['run_id'],
                   'program_fingerprint': self._program._fingerprint, 'reader_program_fingerprint': self._contract_hash,
                   'member_count': len(rows), 'reading_count': len(readings)}
        payload = {'receipt': receipt, 'rows': rows, 'occurrence': list(occurrence),
                   'work': 1+len(rows)+len(readings)}
        size = len(self._canonical(payload))+len(self._canonical(receipt))+bool(self._frames)
        payload['size'] = size
        # Account for the size field itself with a fixed-point exact byte count.
        for _ in range(8):
            updated = len(self._canonical(payload))+len(self._canonical(receipt))+bool(self._frames)
            if updated == payload['size']:
                break
            payload['size'] = updated
        else:
            raise FrameError('frame_size')
        if (len(self._frames)+1 > self._limits['frames'] or self._members+len(rows) > self._limits['members']
                or self._frame_bytes+payload['size'] > self._limits['frame_bytes']):
            raise FrameError('frame_limit')
        candidate = _Prepared()
        self._pending_record = {'payload': payload, 'snapshot': snapshot,
                                'snapshot_signature': self._digest(snapshot), 'version': self._version,
                                'return_value': copy.deepcopy(receipt)}
        self._pending = candidate
        return candidate

    def commit(self, candidate, reserve):
        self._not_publishing()
        if candidate is None:
            return None
        prepared = self._pending_record
        if (candidate is not self._pending or prepared is None or prepared['version'] != self._version
                or self._digest(prepared['snapshot']) != prepared['snapshot_signature']):
            raise FrameError('frame_candidate_authority')
        payload, snapshot = prepared['payload'], prepared['snapshot']
        frame = {'snapshot': snapshot, 'snapshot_signature': prepared['snapshot_signature'],
                 'payload': payload, 'rows': {tuple(row['member']): row for row in payload['rows']}}
        result = copy.deepcopy(prepared['return_value'])
        object_key, occurrence = id(snapshot), tuple(payload['occurrence'])
        member_count, size, work = len(payload['rows']), payload['size'], payload['work']
        # This is the only callback. All potentially failing data work is above.
        self._publishing = True
        try:
            reserve(work)
            self._frames[object_key] = frame
            self._occurrences.add(occurrence)
            self._members += member_count
            self._frame_bytes += size
            self._capture_work += work
            self._version += 1
            self._pending = None
            self._pending_record = None
        finally:
            self._publishing = False
        return result

    def _frame(self, snapshot):
        frame = self._frames.get(id(snapshot))
        if frame is None or frame['snapshot'] is not snapshot or self._digest(snapshot) != frame['snapshot_signature']:
            raise FrameError('frame_snapshot_authority')
        return frame

    def read(self, snapshot, member, hit, *, layers, current_contract, reserve, prepare_receipt=None):
        self._not_publishing()
        self._contract_ok(current_contract)
        if (type(hit) is not dict or not {'run_id', 'event_id', 'origin', 'time_us'} <= set(hit)
                or hit.get('event') != 'hit' or hit.get('damage_system') != 'classic'):
            raise FrameError('frame_damage_scope')
        frame = self._frame(snapshot)
        payload = frame['payload']
        if type(hit.get('origin')) is not int or hit['origin'] != payload['receipt']['context']['origin']:
            raise FrameError('frame_origin')
        number(hit.get('time_us'))
        if hit['time_us'] < snapshot['time']:
            raise FrameError('frame_time')
        key = member_key(member)
        if not any(c['hit_id'] == hit.get('event_id') and c['character'] == key[3]
                   and c['reference_id'] == payload['receipt']['context']['event_id']
                   and c['damage_system'] == 'classic' for c in self._contract['consumers']):
            raise FrameError('frame_consumer_reference')
        row = frame['rows'].get(key)
        if row is None or number(layers, minimum=1) != row['layer_count']:
            raise FrameError('frame_member_layers')
        run = text(hit['run_id'])
        text(hit['event_id'])
        identity = (run, *key)
        request_fingerprint = self._digest([hit, key, payload['receipt']['frame_id'], layers])
        prior = self._reads.get(identity)
        if prior:
            if request_fingerprint != prior['request_fingerprint']:
                raise FrameError('frame_read_identity')
            return copy.deepcopy(prior['receipt'])
        try:
            reading = row['reading']
            value = linear_value(reading['numerator']/reading['denominator'], row['expression'])
            combined = (value**layers if BENEFIT_TYPES[row['benefit_type_id']]['value_semantics'] == 'multiply'
                        else value*layers)
            if not math.isfinite(combined):
                raise ArithmeticError('nonfinite layers')
        except ArithmeticError as exc:
            raise FrameError('frame_value') from exc
        receipt = {'type': 'counter_frame_read', 'authority': 'candidate_only', 'frame_id': payload['receipt']['frame_id'],
                   'domain': 'self', 'sample': 'self_frame', 'frame_selection': 'historical_reference',
                   'context': copy.deepcopy(hit), 'capture_context': copy.deepcopy(payload['receipt']['context']),
                   'member': list(key), 'reading': copy.deepcopy(reading),
                   'value': value, 'single_value': value, 'layer_count': layers, 'combined_value': combined}
        extra = len(self._canonical(receipt))+bool(self._reads)
        if len(self._reads)+1 > self._limits['reads'] or self._trace_bytes+extra > self._limits['trace_bytes']:
            raise FrameError('frame_read_limit')
        stored = {'request_fingerprint': request_fingerprint, 'receipt': receipt}
        result = copy.deepcopy(receipt)
        if prepare_receipt is not None:
            prepare_receipt(result)
        self._publishing = True
        try:
            reserve(1)
            self._reads[identity] = stored
            self._trace_bytes += extra
            self._read_work += 1
        finally:
            self._publishing = False
        return result

    def signature(self, snapshot):
        frame = self._frame(snapshot)
        result = [{'member': [row['member'][0], row['member'][1], row['instance_ordinal'], row['member'][3]],
                 'raw_atoms': row['reading']['raw_atoms'],
                 'numerator': row['reading']['numerator'], 'denominator': row['reading']['denominator'],
                 'transform': copy.deepcopy(row['reading']['transform']), 'unit_scale': row['reading']['unit_scale'],
                 'layer_count': row['layer_count'], 'expression': copy.deepcopy(row['expression'])}
                for row in copy.deepcopy(frame['payload']['rows'])]
        return sorted(result, key=lambda row: tuple(row['member']))

    def usage(self):
        return {'frames': len(self._frames), 'members': self._members, 'frame_bytes': self._frame_bytes,
                'reads': len(self._reads), 'trace_bytes': self._trace_bytes,
                'capture_work': self._capture_work, 'read_work': self._read_work}
