"""Finite semantic selection of real damage occurrences, never guessed run IDs."""
from __future__ import annotations

import copy

FIELDS = {'contract', 'source_action_id', 'source_event_id', 'character', 'damage_system', 'origin', 'time_us',
          'reaction_type', 'element', 'target_id'}
REQUIRED = {'contract', 'character', 'damage_system', 'origin', 'time_us'}
RUNTIME_EVENT_REF_V1_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'contract': {'const': 'runtime-damage-match-v1'},
    **{k: {'type': 'string', 'minLength': 1} for k in FIELDS - {'contract', 'origin', 'time_us'}},
    'origin': {'type': 'integer', 'minimum': 0, 'maximum': 20},
    'time_us': {'type': 'integer', 'minimum': 0, 'maximum': 10**15}}, 'required': sorted(REQUIRED),
    'anyOf': [{'required': ['source_action_id']}, {'required': ['source_event_id']}]}

STABLE_FIELDS = (FIELDS - {'time_us'}) | {'component_id', 'occurrence'}
STABLE_REQUIRED = {'contract', 'source_event_id', 'component_id', 'occurrence', 'character', 'damage_system', 'origin'}
RUNTIME_EVENT_REF_V2_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'contract': {'const': 'runtime-damage-match-v2'},
    **{k: {'type': 'string', 'minLength': 1} for k in STABLE_FIELDS - {'contract', 'origin', 'occurrence'}},
    'origin': {'type': 'integer', 'minimum': 0, 'maximum': 20},
    'occurrence': {'type': 'integer', 'minimum': 0, 'maximum': 40000}}, 'required': sorted(STABLE_REQUIRED)}
RUNTIME_EVENT_REF_SCHEMA = {'oneOf': [RUNTIME_EVENT_REF_V1_SCHEMA, RUNTIME_EVENT_REF_V2_SCHEMA]}


def validate_reference(ref):
    if type(ref) is dict and ref.get('contract') == 'runtime-damage-match-v2':
        return (STABLE_REQUIRED <= set(ref) <= STABLE_FIELDS
            and type(ref['origin']) is int and 0 <= ref['origin'] <= 20
            and type(ref['occurrence']) is int and 0 <= ref['occurrence'] <= 40000
            and all(type(ref[k]) is str and 0 < len(ref[k]) <= 512 for k in set(ref) - {'origin', 'occurrence'}))
    return (type(ref) is dict and REQUIRED <= set(ref) <= FIELDS
            and bool({'source_action_id', 'source_event_id'} & set(ref))
            and ref['contract'] == 'runtime-damage-match-v1'
            and all(type(ref[k]) is int and 0 <= ref[k] <= (20 if k == 'origin' else 10**15) for k in ('origin', 'time_us'))
            and all(type(ref[k]) is str and 0 < len(ref[k]) <= 512 for k in set(ref) - {'origin', 'time_us'}))


def prepare_execution_reference(runtime, event, payload):
    """Expose an exact occurrence identity without relying on its timestamp."""
    if 'execution_ref' in event:
        return event['execution_ref']
    hit, descriptor = payload['hit'], payload.get('descriptor', {})
    extensions = hit.get('extensions', {})
    special, elemental = extensions.get('special_reaction', {}), extensions.get('elemental', {})
    source = event.get('source_event_id') or special.get('request_id') or elemental.get('parent_event_id')
    prefix = f"{event['origin']}:"
    if isinstance(source, str) and source.startswith(prefix):
        source = source[len(prefix):]
    component = special.get('component') or descriptor.get('prototype_hit_id') or hit['id']
    source = source or descriptor.get('id') or hit['id']
    ref = {'contract': 'runtime-damage-match-v2', 'source_event_id': source,
        'component_id': component, 'occurrence': descriptor.get('pattern_occurrence', 0),
        'character': hit['character'], 'damage_system': hit['damage_system'], 'origin': event['origin'],
        **({'source_action_id': event['action_id']} if event.get('action_id') else {}),
        **({'target_id': target} if (target := event.get('target_id') or elemental.get('target_id')) else {}),
        **({'reaction_type': event['reaction_type']} if event.get('reaction_type') else {}),
        **({'element': hit['element']} if hit.get('element') else {})}
    event['execution_ref'] = ref
    return ref


def reference_matches(reference, event):
    actual = event.get('execution_ref', {})
    return all(actual.get(key) == value for key, value in reference.items())


def register_runtime_matches(runtime, event, payload):
    stable = prepare_execution_reference(runtime, event, payload)
    from genshin_engine.runtime_reference_audit import audit_of
    if (reference_audit := audit_of(runtime)) is not None:
        reference_audit.damage(event, payload, stable)
    if not hasattr(runtime, '_runtime_match_catalog'):
        authored = [m for m in runtime.matches if 'event_ref' in m]
        runtime._runtime_match_catalog = [(copy.deepcopy(m), set()) for m in authored]
        runtime.matches = [m for m in runtime.matches if 'event_ref' not in m]
    hit = payload['hit']
    special = hit.get('extensions', {}).get('special_reaction', {})
    source_event = event.get('source_event_id', special.get('request_id'))
    prefix = f"{event['origin']}:"
    if isinstance(source_event, str) and source_event.startswith(prefix):
        source_event = source_event[len(prefix):]
    actual = {'contract': 'runtime-damage-match-v1', 'source_action_id': event.get('action_id'),
        'source_event_id': source_event,
        'character': hit['character'], 'damage_system': hit['damage_system'],
        'origin': event['origin'], 'time_us': runtime.time, 'reaction_type': event.get('reaction_type'),
        'element': hit.get('element'), 'target_id': event.get('target_id')}
    uid = (event.get('run_id'), hit['id'])
    for match, candidates in runtime._runtime_match_catalog:
        reference = match['event_ref']
        selected = stable if reference['contract'] == 'runtime-damage-match-v2' else actual
        if all(selected.get(k) == v for k, v in reference.items()) and uid not in candidates:
            if len(candidates) >= 2:
                continue  # Two original candidates already prove ambiguity.
            candidates.add(uid)
            resolved = {k: v for k, v in match.items() if k != 'event_ref'}
            resolved['hit_id'] = hit['id']
            def effect_key(row):
                ref = row.get('effect_ref', {})
                return (row.get('fixed_source_id', ref.get('fixed_source_id')),
                        row.get('rule_id', ref.get('buff_id')), row.get('effect_id', ref.get('effect_id')), row.get('hit_id'))
            if any(effect_key(row) == effect_key(resolved) for row in runtime.matches):
                runtime.issues.append({'code': 'duplicate_match', 'message': '静态与运行期引用解析到同一收益/命中，不选择第一条。',
                    'path': 'matches.event_ref', 'severity': 'pending'})
            runtime.matches.append(resolved)


def finalize_runtime_matches(runtime):
    """Append typed diagnostics before formal result projection. Return receipts."""
    if not hasattr(runtime, '_runtime_match_catalog'):
        runtime._runtime_match_catalog = [(m, set()) for m in runtime.matches if 'event_ref' in m]
    receipts = []
    for match, candidates in runtime._runtime_match_catalog:
        count = len(candidates)
        receipts.append({'event_ref': copy.deepcopy(match['event_ref']), 'candidates': [list(c) for c in sorted(candidates)],
                         'status': 'resolved' if count == 1 else 'no_match' if count == 0 else 'ambiguous'})
        if count != 1:
            runtime.issues.append({'code': 'runtime_match_no_match' if count == 0 else 'runtime_match_ambiguous',
                'message': '运行期事件引用必须解析为唯一真实发生，未猜测候选。',
                'path': 'matches.event_ref', 'severity': 'pending', 'candidates': [list(c) for c in sorted(candidates)]})
    runtime.trace.extend({'type': 'runtime_match_resolution', **r} for r in receipts)
    return receipts
