"""Actual periodic-resource observations enter the shared causal event queue.

The adapter does not generate resources, advance time, or interpret character
identities. One unit observation is one possible trigger; rule-owned cooldowns
and source ownership remain authoritative.
"""
import copy
import math

from genshin_engine.timed_resource_window import add_quantity, resource_quantity

OPERATIONS = ('created', 'consumed', 'overflow_consumed', 'expired')


def enqueue_resource_changes(runtime, observation, cycle, parent):
    if (observation.get('resource_type') != 'periodic_resource'
            or observation.get('operation') not in OPERATIONS):
        return
    amount = observation.get('amount')
    if not resource_quantity(amount):
        raise ValueError('资源变化回执数量无效')
    if observation['time_tick'] != runtime.time or parent['time'] != runtime.time:
        raise ValueError('资源变化必须在所属的共享时钟边界交付，不能在后继事件补触发')
    # No listeners means no unnecessary per-unit queue expansion. The actual
    # aggregate resource receipt is still kept by the reporting bridge.
    if not any(r['trigger'].get('event') == 'resource_change' for r in runtime.rules.values()):
        return
    operation = observation['operation']
    changed = {operation}
    if operation == 'overflow_consumed':
        changed.add('consumed')
    for ordinal in range(1, math.ceil(amount)+1):
        weight = min(1, add_quantity(amount, -(ordinal-1)))
        counters = copy.deepcopy(observation['counters'])
        for counter in changed:
            counters[counter] = add_quantity(counters[counter], -add_quantity(amount, -min(ordinal, amount)))
        runtime.emit_event(parent, 'mechanics_resource_change', 0,
            {'observation':copy.deepcopy(observation), 'ordinal':ordinal, 'counters':counters, 'weight':weight},
            f"resource:{observation['event_id']}:{ordinal}")


def handle_resource_change(runtime, ev, row, payload):
    observed = payload['observation']
    event = {'event':'resource_change','id':ev['id'],'run_id':ev['run_id'],'origin':ev['origin'],
             'character':observed['owner'],'target_id':observed.get('target_id'),
             'area_id':observed['area_id'],'resource_id':'stellar_periodic_resource',
             'resource_operation':observed['operation'],'resource_counters':copy.deepcopy(payload['counters']),
             'resource_mode':observed['mode'],'source_resource_event_id':observed['event_id'],
             'resource_ordinal':payload['ordinal'],'conditions':dict(runtime.conditions)}
    if payload.get('weight', 1) != 1 or observed.get('count_mode') == 'expected':
        event['resource_weight'] = payload.get('weight', 1)
        event['count_mode'] = 'expected'
    for rule in runtime.rules.values():
        selector = rule['trigger']
        if selector.get('resource_every', {}).get('scope') != 'matching':
            continue
        eligible_selector = {key: value for key, value in selector.items() if key != 'resource_every'}
        if runtime.filter(eligible_selector, event, rule['id']):
            runtime.resource_trigger_counts[rule['id']] = add_quantity(
                runtime.resource_trigger_counts.get(rule['id'], 0), payload.get('weight',1))
    if runtime.resource_trigger_counts:
        event['resource_rule_counts'] = dict(runtime.resource_trigger_counts)
    runtime.trace.append({'type':'resource_change','t':runtime.time/1_000_000,**copy.deepcopy(event)})
    runtime.phase(event,'post',ev)
    row.update(status='executed',resource_change=copy.deepcopy(event))
    return runtime.queue
