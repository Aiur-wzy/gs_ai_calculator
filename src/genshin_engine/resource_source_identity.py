"""Versioned resource identity derived from an admitted source and real trigger.

Labels, rule aliases and handler names are deliberately absent from the benefit
key. Trigger references are relative to the executing callback: authors cannot
provide a run id or borrow an unrelated parent's execution authority.
"""
from __future__ import annotations

TRIGGER_SOURCE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"contract": {"const": "resource-trigger-source-v1"},
                   "kind": {"const": "current_trigger"}},
    "required": ["contract", "kind"],
}
RESOURCE_IDENTITY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"source_id": {"type": "string", "minLength": 1},
                   "source_part": {"type": "string", "minLength": 1}},
    "required": ["source_id", "source_part"],
}


def ownership_key(owner, rules):
    rule = rules.get(owner['rule_id'], {})
    cause = ('trigger',) if 'source_ref' in owner else ('action', owner.get('action_id'))
    return (*cause, rule.get('trigger', {}).get('phase'), rule.get('owner'), rule.get('source_id'), owner['source_part'])


def owns_trigger(owner, rule, resource, event):
    return (owner['rule_id'] == rule['id'] and owner['source_part'] == resource['source_part']
            and ('source_ref' in owner or owner.get('action_id') == event.get('action_id')))


def causal_envelope(rule, resource, event, parent):
    if not parent.get('run_id') or type(parent.get('origin')) is not int:
        raise ValueError('Resource source requires a real executing queue occurrence')
    return {'contract': 'resource-cause-v1', 'event_id': event['id'],
            'run_id': parent['run_id'], 'origin': parent['origin'],
            'phase': rule['trigger'].get('phase', 'post'),
            'source_id': rule['source_id'], 'source_part': resource['source_part'],
            'owner': rule['owner'], 'target_id': event.get('target_id'),
            'action_id': event.get('action_id')}


def benefit_keys(cause, resource):
    recipients = sorted(resource['amounts']) if resource['kind'] == 'energy_direct' else ['particle_group']
    return {(cause['run_id'], cause['phase'], cause['owner'], cause['source_id'], cause['source_part'],
             resource['kind'], receiver) for receiver in recipients}
