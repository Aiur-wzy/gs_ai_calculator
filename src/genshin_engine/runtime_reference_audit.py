"""Prove deferred generated references from executed producer relationships.

The private graph stores only input roots, never copies elemental snapshots or
infers parents from an ID's spelling. A selector remains a filter; zero matches
are allowed once its source is proven. Explicit matches retain unique-use rules.
"""
from __future__ import annotations

import copy


class RuntimeReferenceAudit:
    def __init__(self, data):
        self.declarations = data.get('_deferred_runtime_references', [])
        self.inputs = {event['id'] for event in data['runtime']}
        self.roots = {}
        self.projected_damage = set()
        self.observed = [{**row, 'roots': set(), 'occurrences': set()} for row in self.declarations]
        self.by_source = {}
        for row in self.observed:
            ref = row['reference']
            self.by_source.setdefault((ref['origin'], ref['source_event_id']), []).append(row)

    def _bind(self, cycle, identities, roots):
        if not roots:
            return
        for identity in identities:
            if isinstance(identity, str) and identity:
                self.roots.setdefault((cycle, identity), set()).update(roots)

    def input_event(self, event):
        if event['id'] in self.inputs:
            self._bind(event['origin'], [event['id'], event.get('run_id')], {(event['origin'], event['id'])})

    def reactions(self, event, reactions):
        roots = self.roots.get((event['origin'], event['id']), set())
        for reaction in reactions:
            if reaction.get('parent_event_id') not in (event['id'], event.get('run_id')):
                continue
            self._bind(event['origin'], [reaction.get('id'), reaction.get('run_id')], roots)

    def generated(self, request, cycle):
        roots = set(self.roots.get((cycle, request.get('event_id')), ()))
        for key in ('parent_reaction_id', 'parent_damage_event_id'):
            roots.update(self.roots.get((cycle, request.get(key)), ()))
        self._bind(cycle, [request.get('event_id')], roots)

    def projected(self, event, parent_id):
        roots = self.roots.get((event['origin'], parent_id), set())
        self._bind(event['origin'], [event['id']], roots)
        if roots:
            self.projected_damage.add((event['origin'], event['id']))

    def damage(self, event, payload, reference):
        hit = payload['hit']
        roots = set()
        if ((event['origin'], event['id']) in self.projected_damage
                or reference['source_event_id'] in (event['id'], payload.get('descriptor', {}).get('id'))):
            roots.update(self.roots.get((event['origin'], event['id']), ()))
        # The renderer may expose an existing alias. Bind that exact emitted
        # value to the proven producer; never strip or parse its components.
        self._bind(event['origin'], [reference['source_event_id']], roots)
        for row in self.by_source.get((event['origin'], reference['source_event_id']), ()):
            ref = row['reference']
            row['roots'].update(roots)
            if roots and len(row['occurrences']) < 2 and all(reference.get(key) == value for key, value in ref.items()):
                row['occurrences'].add((event.get('run_id'), hit['id']))

    def finish(self, runtime, *, execution_complete=True):
        invalid = False
        for row in self.observed:
            count, roots = len(row['occurrences']), row['roots']
            status = ('source_unresolved' if execution_complete else 'source_not_reached') if not roots else 'ambiguous' if count > 1 else 'resolved' if count else 'no_match'
            receipt = {'type': 'runtime_reference_source_resolution', 'path': row['path'],
                'reference': copy.deepcopy(row['reference']), 'kind': row['kind'], 'status': status,
                'input_roots': [{'origin': origin, 'event_id': identity} for origin, identity in sorted(roots)],
                'candidates': [list(value) for value in sorted(row['occurrences'], key=lambda item: (item[0] or '', item[1]))]}
            runtime.trace.append(receipt)
            if not roots:
                invalid = execution_complete
                runtime.issues.append({'code': 'runtime_selector_reference' if row['kind'] == 'selector' else 'runtime_match_reference',
                    'severity': 'error' if execution_complete else 'pending', 'path': row['path'],
                    'message': ('该派生来源未由本轮真实执行追溯到当前输入；请读取重算后的精确引用。' if execution_complete else
                                '本轮执行提前停止，尚未走到该派生来源；先修复执行阻断再核对引用。'),
                    'reference': copy.deepcopy(row['reference']), 'resolution': status,
                    'recovery': 'read_current_execution_ref'})
            elif count > 1 and row['kind'] == 'selector':
                runtime.issues.append({'code': 'runtime_selector_ambiguous', 'severity': 'pending', 'path': row['path'],
                    'message': '精确派生引用对应多个真实发生；请补齐目标或发生身份。',
                    'reference': copy.deepcopy(row['reference']), 'candidates': receipt['candidates']})
        return not invalid


def audit_of(runtime):
    """Allocate nothing for the common path without deferred references."""
    if not hasattr(runtime, '_reference_source_audit'):
        data = getattr(runtime, 'data', {})
        runtime._reference_source_audit = (RuntimeReferenceAudit(data)
            if data.get('_deferred_runtime_references') else None)
    return runtime._reference_source_audit


def register_generated(runtime, request, cycle):
    audit = audit_of(runtime)
    if audit is not None:
        audit.generated(request, cycle)


def prepare_projected_damage(runtime, event, payload, parent_id):
    """Called by damage producers, never from author-provided extensions."""
    audit = audit_of(runtime)
    if audit is not None:
        from genshin_engine.runtime_event_match import prepare_execution_reference
        audit.projected(event, parent_id)
        prepare_execution_reference(runtime, event, payload)
