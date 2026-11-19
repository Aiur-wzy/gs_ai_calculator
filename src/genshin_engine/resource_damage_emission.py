"""Rule-owned damage from actual resource changes, using normal damage delivery.

Declarations contain ability data, never precomputed damage or guessed trigger
times. The shared rule selector and cooldown decide which observations execute.
"""
from __future__ import annotations

import copy

from genshin_engine.rotation_schema import STRING, obj
from genshin_engine.special_reaction_schema import DIRECT_STELLAR_DAMAGE_PROFILE_SCHEMA

RESOURCE_DAMAGE_SCHEMA = obj({
    'id': STRING, 'action_id': STRING, 'target_id': STRING, 'area_id': STRING,
    'ability_profile': DIRECT_STELLAR_DAMAGE_PROFILE_SCHEMA,
}, ['id', 'action_id', 'target_id', 'area_id', 'ability_profile'])


def validation_events(model):
    """Reuse the ordinary ability/party admission contract; do not schedule these."""
    for rule_index, rule in enumerate(model.get('rules', ())):
        for index, declaration in enumerate(rule.get('resource_damage', ())):
            yield (f'mechanics_model.rules.{rule_index}.resource_damage.{index}', {
                **copy.deepcopy(declaration), 'id': f"@resource-damage:{rule['id']}:{declaration['id']}",
                'kind': 'direct_stellar_damage', 't': 0, 'order': 0,
                'status': rule['status'], 'source': rule['source'],
            })


def damage_identity(declaration):
    return (declaration['action_id'], declaration['target_id'], declaration['area_id'],
            declaration['ability_profile']['parameters']['component'])


def emit_resource_damage(runtime, rule, event, parent):
    for declaration in rule.get('resource_damage', ()):
        source = getattr(runtime, 'executed_source_actions', {}).get(declaration['action_id'])
        if source is None:
            runtime.trace.append({'type': 'resource_damage_suppressed', 't': runtime.time/1_000_000,
                'rule_id': rule['id'], 'emission_id': declaration['id'],
                'resource_event_id': event['source_resource_event_id'],
                'reason': 'source_action_not_executed', 'action_id': declaration['action_id']})
            continue
        # A receipt is an actual cause, not a second manually selected timestamp.
        trigger = {'event_id': event['source_resource_event_id'],
                   'ordinal': event['resource_ordinal']}
        if event.get('resource_weight',1) != 1 and not rule['trigger'].get('resource_every'):
            trigger['quantity_weight'] = event['resource_weight']
        payload = {**copy.deepcopy(declaration), 'kind': 'direct_stellar_damage',
                   'resource_trigger': trigger, 'source_action_run_id': source['run_id'],
                   'source_rule_id': rule['id'], 'status': rule['status'], 'source': rule['source']}
        runtime.emit_event(parent, 'mechanics_resource_damage', 0, payload,
                           f"resource-damage:{rule['id']}:{declaration['id']}")


def handle_resource_damage(runtime, ev, row, payload):
    # Import here to keep schema/validation independent from execution adapters.
    from genshin_engine.mechanics_event_execution import (
        handle_special_reaction_operation,
    )
    result = handle_special_reaction_operation(runtime, ev, row, payload)
    row['resource_trigger'] = copy.deepcopy(payload['resource_trigger'])
    row['source_rule_id'] = payload['source_rule_id']
    row['source_action_run_id'] = payload['source_action_run_id']
    return result
