"""Close explicit callback declarations against reviewed, actual source delivery.

This adapter approves an execution assumption, never a game-source fact. A
missing delivery is pending unless the user explicitly excluded that source.
The complete native graph is checked before any member or edge is removed.
"""
from __future__ import annotations

import copy

from genshin_engine.mechanics import validate_mechanics_model

from .files import fingerprint
from .schema import PlanError

CONTRACT = 'attribute-callback-source-adoption-v1'
AUTHORITY = 'caller_assumed_order_not_game_source_approval'


def prepare_attribute_callbacks(model, source_active, active, linked, excluded, missing, *,
                                source_reviews, context_fingerprint):
    """Return an execution declaration and adoption; do not mutate inputs.

    Call after the source graph has reached its final delivery state. The
    original member set and references remain authoritative even when a prior
    source helper removed a rule. Pending members cannot fall back to old phases.
    """
    if 'attribute_callbacks' not in model:
        return None, None
    validation = validate_mechanics_model(model)
    if validation['status'] == 'invalid':
        raise PlanError('属性回调完整原图无效，不能通过排除来源隐藏错误',
                        'invalid_attribute_callback_source', validation['issues'])
    path = 'mechanics_model.attribute_callbacks'
    for issue in validation['issues']:
        missing('attribute_callback_scope_pending', issue.get('path', path),
                issue['message'], 'review_mechanics')
    native = model['attribute_callbacks']
    rules = {r['id']: r for r in model['rules']}
    records = []
    for rid in sorted(native['members']):
        rule = rules[rid]
        review = source_reviews.get(rule['source_id']) or {}
        reviewed = (review.get('mode') == 'manual_context'
                    and review.get('context_fingerprint') == context_fingerprint
                    and isinstance(review.get('note'), str) and bool(review['note'].strip()))
        own = ('excluded' if rid in excluded else
               'ready' if rid in linked and rid in source_active and rule.get('status') != 'pending'
               and bool(rule.get('source', '').strip()) and reviewed else 'pending')
        effective = 'pending' if own == 'ready' and rid not in active else own
        record = {'rule_id': rid, 'name_zh': rule['name_zh'], 'owner': rule['owner'], 'source_id': rule['source_id'],
                  'own_source_status': own, 'effective_status': effective}
        if rid in linked:
            record['binding_id'], record['part_id'] = linked[rid]
        records.append(record)
        if effective == 'pending':
            missing('attribute_callback_source_scope_unreviewed', path + '.members.' + rid,
                    '回调成员需要当前完整manual_context确认及实际来源交付；未交付不能当作明确排除',
                    'review_mechanics')
    pending = bool(validation['issues']) or any(r['effective_status'] == 'pending' for r in records)
    removed = {r['rule_id'] for r in records if r['effective_status'] == 'excluded'}
    kept = set(native['members']) - removed
    declaration = copy.deepcopy(native)
    declaration['members'] = [rid for rid in native['members'] if rid in kept]
    declaration['edges'] = [copy.deepcopy(edge) for edge in native['edges']
                            if edge['before_rule_id'] in kept and edge['after_rule_id'] in kept]
    used = {edge['assumption_id'] for edge in declaration['edges']}
    declaration['assumptions'] = [copy.deepcopy(row) for row in native['assumptions'] if row['id'] in used]
    # With pending validation, retain the declaration as a non-executable
    # preview. The caller must not run a pending composition.
    if not kept:
        declaration = copy.deepcopy(native) if pending else None
    adoption = {'contract': CONTRACT, 'source_authority': AUTHORITY,
                'context_fingerprint': context_fingerprint,
                'declaration_fingerprint': fingerprint(native),
                'status': 'pending' if pending else 'ready' if kept else 'excluded',
                'members': records, 'excluded_rule_ids': sorted(removed),
                'retained_rule_ids': sorted(kept),
                'excluded_edges': [copy.deepcopy(edge) for edge in native['edges']
                                   if edge['before_rule_id'] in removed or edge['after_rule_id'] in removed],
                'excluded_assumption_ids': sorted(row['id'] for row in native['assumptions'] if row['id'] not in used),
                'validation_issues': copy.deepcopy(validation['issues'])}
    return declaration, adoption
