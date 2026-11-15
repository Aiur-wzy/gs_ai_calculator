"""Source-bound App projection for explicit reaction/lifecycle dependencies."""
from __future__ import annotations

import copy

from genshin_engine.resource_reaction_program import Program, ReactionResourceError

from .files import fingerprint
from .schema import PlanError

FIELD = 'resource_reaction_contract'
CONTRACT = 'ordinary-reaction-resource-adoption-v2'
GATE_FIELDS = {'operation_id', 'lifecycle_id', 'window', 'generation_policy'}


def has_lifecycle_gates(model):
    declaration = model.get(FIELD) or {}
    return declaration.get('schema_version') == 2 and bool(declaration.get('lifecycle_gates'))


def validate_original(model, characters, missing):
    """Original full graph is checked before source exclusions can hide it."""
    try:
        Program(model, sorted(characters))
    except ReactionResourceError as exc:
        if exc.issue['severity'] == 'error':
            raise PlanError('领域反应资格完整原图无效，不能通过排除来源隐藏错误',
                            'invalid_resource_reaction_source', exc.issue) from exc
        missing('resource_reaction_scope_pending', exc.issue['path'], exc.issue['message'], 'review_mechanics')


def projected_declaration(original, active):
    return ({'schema_version': 2, 'operation_ids': sorted(active),
             'lifecycle_gates': sorted([copy.deepcopy(g) for g in original['lifecycle_gates']
                                       if g['operation_id'] in active], key=lambda g: g['operation_id'])}
            if active else None)


def gate_sources(original, delivered, state_adoptions, reviews, context):
    lives = {r['id']: r for r in original.get('state_runtime', {}).get('lifecycles', [])}
    current = {r['id']: r for r in delivered.get('state_runtime', {}).get('lifecycles', [])}
    rules = {r['id']: r for r in delivered['rules']}
    adoptions = {}
    for row in state_adoptions:
        if row['kind'] != 'lifecycles':
            continue
        if row['id'] in adoptions:
            raise PlanError('领域来源采用记录重复', 'duplicate_delivery')
        adoptions[row['id']] = row
    records = []
    for gate in sorted(original[FIELD]['lifecycle_gates'], key=lambda g: g['operation_id']):
        life = lives[gate['lifecycle_id']]
        source = adoptions.get(life['id'], {})
        row = {'gate': copy.deepcopy(gate), 'lifecycle_id': life['id'], 'source_id': life['source_id'],
               'source_owner': life['owner'], 'lifecycle_fingerprint': fingerprint(life),
               'authority_rule_id': source.get('authority_rule_id'), 'review_mode': None,
               'lifecycle_source_status': source.get('effective_status', source.get('status', 'pending')),
               'status': 'pending'}
        if source and (source.get('source_id') != life['source_id'] or source.get('lifecycle_id') != life['id']):
            raise PlanError('领域来源采用身份与原始声明不一致', 'invalid_resource_reaction_source')
        retained = current.get(life['id'])
        if retained is not None and fingerprint(retained) != fingerprint(life):
            raise PlanError('领域反应资格依赖的原始领域被改写', 'invalid_resource_reaction_source')
        authority = rules.get(row['authority_rule_id'])
        review = reviews.get(life['source_id']) or {}
        if (row['lifecycle_source_status'] == 'ready' and retained is not None and authority is not None
                and authority['source_id'] == life['source_id'] and authority['owner'] == life['owner']
                and review.get('mode') == 'manual_context' and review.get('context_fingerprint') == context
                and type(review.get('note')) is str and bool(review['note'].strip())):
            row.update(status='ready', review_mode='manual_context')
        records.append(row)
    return records


def validate_adoption(adoption, model):
    """Saved-input consistency only; no fresh DB or game-source approval."""
    original = adoption['original_declaration']
    if (type(original) is not dict or set(original) != {'schema_version', 'operation_ids', 'lifecycle_gates'}
            or type(original['schema_version']) is not int or original['schema_version'] != 2):
        raise ValueError('领域反应原始合同无效')
    ids, gates = original['operation_ids'], original['lifecycle_gates']
    if (type(ids) is not list or not 1 <= len(ids) <= 256
            or any(type(v) is not str or not v.strip() or len(v) > 128 for v in ids)
            or len(set(ids)) != len(ids) or type(gates) is not list or len(gates) > 256):
        raise ValueError('领域反应原始成员无效')
    seen = set()
    for gate in gates:
        if (type(gate) is not dict or set(gate) != GATE_FIELDS
                or any(type(v) is not str for v in gate.values())
                or gate['operation_id'] not in ids or gate['operation_id'] in seen
                or not gate['lifecycle_id'].strip()
                or gate['window'] != 'established_until_end'
                or gate['generation_policy'] != 'current_at_resource_attempt'):
            raise ValueError('领域反应原始资格无效')
        seen.add(gate['operation_id'])
    if fingerprint(original) != adoption['declaration_fingerprint'] or sorted(ids) != adoption['declared_operation_ids']:
        raise ValueError('领域反应原始声明与采用记录不一致')
    expected = projected_declaration(original, adoption['active_operation_ids'])
    if (fingerprint(expected) != fingerprint(adoption['projected_declaration'])
            or fingerprint(expected) != fingerprint(model.get(FIELD))):
        raise ValueError('领域反应投影丢失或改写资格')
    rows = adoption['lifecycle_gates']
    if type(rows) is not list or len(rows) != len(gates):
        raise ValueError('领域反应来源记录不完整')
    expected_gates = sorted(gates, key=lambda g: g['operation_id'])
    if fingerprint([r['gate'] for r in rows]) != fingerprint(expected_gates):
        raise ValueError('领域反应来源记录与原声明不一致')
    operations = {r['operation_id']: r for r in adoption['operations']}
    lives = {r['id']: r for r in model.get('state_runtime', {}).get('lifecycles', [])}
    rules = {r['id']: r for r in model['rules']}
    for row in rows:
        operation = operations[row['gate']['operation_id']]
        if row['lifecycle_id'] != row['gate']['lifecycle_id'] or row['status'] != operation['status']:
            raise ValueError('领域与资源采用状态不一致')
        life, authority = lives.get(row['lifecycle_id']), rules.get(row['authority_rule_id'])
        # Excluding an income operation does not necessarily remove the field.
        # Any retained lifecycle still executes and must keep its reviewed
        # identity and complete definition, even with no executable gate left.
        if row['status'] != 'ready' and life is None:
            continue
        if (life is None or authority is None or row['review_mode'] != 'manual_context'
                or row['lifecycle_source_status'] != 'ready'
                or fingerprint(life) != row['lifecycle_fingerprint']
                or life['source_id'] != row['source_id'] or life['owner'] != row['source_owner']
                or authority['source_id'] != row['source_id'] or authority['owner'] != row['source_owner']
                or operation['source_owner'] != row['source_owner']):
            raise ValueError('领域反应来源、提供者或领域定义不一致')
