"""Finite, explicit resource ledger used by the stellar runtime.

The caller owns the clock, transactions, field and damage execution.  Resource
receipts do not invent damage or decode character descriptions.
"""
import copy
from dataclasses import asdict

from genshin_engine.particle_quantities import quantity
from genshin_engine.probabilistic_resource_contract import resource_trial, trial_mode
from genshin_engine.probability_counts import resource_change
from genshin_engine.special_reaction_contracts import _finite_number, _seconds_to_ticks
from genshin_engine.timed_resource_window import (
    ResourceWindowPolicy,
    ResourceWindowState,
    add_quantity,
    advance_resource_window,
    consume_resource,
    grant_resource,
    trigger_resource_window,
)

CONFIG_FIELDS = {'owner_id', 'area_id', 'interval_seconds', 'lifetime_seconds', 'capacity',
                 'window_seconds', 'extended_window_seconds', 'refresh_mode'}
COUNTERS = ('created', 'consumed', 'overflow_consumed', 'released', 'expired')


def as_number(value):
    return int(value) if value == int(value) else float(value)


def validate_config(value):
    if not isinstance(value, dict) or set(value) != CONFIG_FIELDS:
        raise ValueError('periodic_resource必须包含完整的owner、area、interval、lifetime、capacity、window、extended_window和refresh_mode字段')
    if any(not isinstance(value[k], str) or not value[k].strip() for k in ('owner_id', 'area_id')):
        raise ValueError('periodic_resource必须绑定实际角色与区域')
    for key in ('interval_seconds', 'lifetime_seconds', 'window_seconds', 'extended_window_seconds'):
        if not _finite_number(value[key], positive=True):
            raise ValueError(f'periodic_resource.{key}必须为正数')
        _seconds_to_ticks(value[key], key)
    if min(value['window_seconds'], value['extended_window_seconds']) < value['interval_seconds']:
        raise ValueError('periodic_resource生成窗口不得短于生成间隔')
    if type(value['capacity']) is not int or not 1 <= value['capacity'] <= 100:
        raise ValueError('periodic_resource.capacity必须为1至100的整数')
    if value['refresh_mode'] != 'extend_window_preserve_cadence':
        raise ValueError('periodic_resource仅支持延长窗口且保留生成节奏')


def policy(config):
    return ResourceWindowPolicy(_seconds_to_ticks(config['interval_seconds'], 'interval'),
                                _seconds_to_ticks(config['lifetime_seconds'], 'lifetime'), config['capacity'])


def initial_ledger(time):
    return {'resource': asdict(ResourceWindowState(time=time)), 'mode': 'none', 'mode_until': None,
            'counters': dict.fromkeys(COUNTERS, 0), 'claims': {}, 'sequence': 0, 'event_ids': []}


def next_deadline(ledger):
    state = ledger['resource']
    values = [v for v in (state['next_generation'], state['expires_at'], ledger['mode_until']) if v is not None]
    return min(values) if values else None


def _record(ledger, rows, *, operation, amount, time, cause):
    ledger['sequence'] += 1
    rows.append({'sequence': ledger['sequence'], 'operation': operation, 'amount': amount,
                 'time_tick': time, 'count': ledger['resource']['count'],
                 'expires_at_tick': ledger['resource']['expires_at'], 'cause': cause,
                 'mode': ledger['mode'], 'mode_until_tick': ledger['mode_until'],
                 'counters': copy.deepcopy(ledger['counters'])})
    if ledger.get('probability_mode') == 'expected':
        rows[-1]['count_mode'] = 'expected'


def _observations(ledger, observations, rows, cause):
    for observation in observations:
        op = observation.operation
        if op in ('created', 'consumed', 'expired'):
            ledger['counters'][op] = add_quantity(ledger['counters'][op], observation.amount)
        if op == 'overflow' and ledger['mode'] == 'overflow_consume':
            op = 'overflow_consumed'
            ledger['counters'][op] = add_quantity(ledger['counters'][op], observation.amount)
            ledger['counters']['consumed'] = add_quantity(ledger['counters']['consumed'], observation.amount)
        _record(ledger, rows, operation=op, amount=observation.amount, time=observation.time, cause=cause)
        rows[-1]['count'] = observation.count
        rows[-1]['expires_at_tick'] = observation.expires_at


def _release(ledger, config, time, rows, cause):
    count = ledger['resource']['count']
    if count and ledger['mode'] == 'overflow_consume':
        state, observations = consume_resource(ResourceWindowState(**ledger['resource']), policy(config), time, count)
        ledger['resource'] = asdict(state)
        _observations(ledger, observations, rows, cause)
        ledger['counters']['released'] = add_quantity(ledger['counters']['released'], count)
        _record(ledger, rows, operation='released', amount=count, time=time, cause=cause)
    ledger['mode'], ledger['mode_until'] = 'none', None


def advance(ledger, config, time):
    """Called at each shared deadline, or advanced chronologically in isolation."""
    ledger = copy.deepcopy(ledger)
    rows = []
    while (due := next_deadline(ledger)) is not None and due <= time:
        # At a shared endpoint: resource expiry, then generation, then core
        # teardown. Existing expired resources cannot fund the consumption.
        state, observations = advance_resource_window(ResourceWindowState(**ledger['resource']), policy(config), due)
        ledger['resource'] = asdict(state)
        if ledger['mode_until'] == due:
            # Generation at the endpoint still belongs to the expired mode for
            # resource creation, then teardown consumes the resulting inventory.
            _observations(ledger, observations, rows, 'deadline')
            _release(ledger, config, due, rows, 'mode_expired')
            _record(ledger, rows, operation='mode_ended', amount=0, time=due, cause='mode_expired')
        else:
            _observations(ledger, observations, rows, 'deadline')
    state, observations = advance_resource_window(ResourceWindowState(**ledger['resource']), policy(config), time)
    ledger['resource'] = asdict(state)
    _observations(ledger, observations, rows, 'deadline')
    return ledger, rows


def trigger(ledger, config, time):
    ledger, rows = advance(ledger, config, time)
    window = config['extended_window_seconds'] if ledger['mode'] == 'extended_generation' else config['window_seconds']
    state, observations = trigger_resource_window(ResourceWindowState(**ledger['resource']), policy(config),
                                                   time, _seconds_to_ticks(window, 'window'))
    ledger['resource'] = asdict(state)
    _observations(ledger, observations, rows, 'reaction')
    _record(ledger, rows, operation='generation_refreshed', amount=0, time=time, cause='reaction')
    return ledger, rows


def operate(ledger, config, time, event_id, operation, *, amount=None, mode=None,
            duration_seconds=None, counter=None, claim_id=None, divisor=1, trial=None, outcome=None,
            probability_mode=None):
    ledger, rows = advance(ledger, config, time)
    if event_id in ledger['event_ids']:
        raise ValueError('重复的星超导资源操作ID')
    ledger['event_ids'].append(event_id)
    result_amount = 0
    if operation in ('grant', 'consume'):
        fn = grant_resource if operation == 'grant' else consume_resource
        state, observations = fn(ResourceWindowState(**ledger['resource']), policy(config), time, amount)
        ledger['resource'] = asdict(state)
        _observations(ledger, observations, rows, event_id)
        result_amount = amount
    elif operation == 'trial':
        spec = resource_trial(trial, config['capacity'])
        calculation_mode = trial_mode(spec, probability_mode, outcome)
        if calculation_mode == 'expected':
            ledger['probability_mode'] = 'expected'
        after = resource_change(quantity(ledger['resource']['count']), spec, mode=calculation_mode,
                                success_count=int(outcome) if calculation_mode=='custom' else None)
        if after.consumed:
            state, observations = consume_resource(ResourceWindowState(**ledger['resource']), policy(config), time, as_number(after.consumed))
            ledger['resource'] = asdict(state)
            _observations(ledger, observations, rows, event_id)
        if after.created:
            state, observations = grant_resource(ResourceWindowState(**ledger['resource']), policy(config), time, as_number(after.created))
            ledger['resource'] = asdict(state)
            _observations(ledger, observations, rows, event_id)
        if ledger['resource']['count'] != as_number(after.balance):
            raise ValueError('概率资源转移与实际库存不一致')
        _record(ledger, rows, operation='trial', amount=as_number(after.consumed), time=time, cause=event_id)
        rows[-1]['trial'] = {'contract':'probabilistic-resource-v1', 'mode':calculation_mode,
                            'parameters': copy.deepcopy(trial),
                            'outcome': outcome, 'executed_units': spec.cost if after.executed else 0,
                            'consumed_units': as_number(after.consumed), 'created_units': as_number(after.created),
                            'overflow_units':as_number(after.overflow), 'executed': after.executed,
                            'eligible_count':after.counts.opportunities,
                            'expected_count' if calculation_mode=='expected' else 'custom_count':as_number(after.counts.successes),
                            'interpretation':('期望次数近似；不是全场随机模拟。' if calculation_mode=='expected'
                                              else '用户指定触发情景。')}
        result_amount = as_number(after.consumed)
    elif operation in ('set_mode', 'end_mode'):
        _release(ledger, config, time, rows, event_id)
        if operation == 'set_mode':
            if mode not in ('overflow_consume', 'extended_generation') or not _finite_number(duration_seconds, positive=True):
                raise ValueError('资源模式及正持续时间必须明确提供')
            ledger['mode'], ledger['mode_until'] = mode, time + _seconds_to_ticks(duration_seconds, 'mode_duration')
        _record(ledger, rows, operation=operation, amount=0, time=time, cause=event_id)
        result_amount = sum(r['amount'] for r in rows if r['cause'] == event_id and r['operation'] == 'released')
    elif operation == 'observe':
        if counter == 'inventory':
            if claim_id is not None or divisor != 1:
                raise ValueError('库存观察不能消费计数游标或使用除数')
            result_amount = ledger['resource']['count']
        elif counter in COUNTERS and isinstance(claim_id, str) and claim_id and type(divisor) is int and divisor > 0:
            old = ledger['claims'].get(claim_id, {'counter': counter, 'divisor': divisor, 'value': 0})
            if old['counter'] != counter or old['divisor'] != divisor:
                raise ValueError('同一资源观察游标不得切换计数类别或除数')
            current = int(quantity(ledger['counters'][counter]) // divisor)
            result_amount = current - old['value']
            ledger['claims'][claim_id] = {'counter': counter, 'divisor': divisor, 'value': current}
        else:
            raise ValueError('资源观察必须引用有效计数、独立游标及正整数除数')
        _record(ledger, rows, operation='observed', amount=result_amount, time=time, cause=event_id)
    else:
        raise ValueError('未知星超导资源操作')
    return ledger, rows, result_amount
