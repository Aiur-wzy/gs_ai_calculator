"""Independent real queue atom-accounting oracles, with synthetic source facts."""
import copy

from genshin_engine.mechanics import execute_mechanics
from tests.test_mechanics_execution import add, condition, rule
from tests.test_shared_pool_v2_execution import operation_rows, periodic_case
from tests.test_shared_pools import config


def test_timer_spend_and_real_damage_gate_consume_one_actual_balance():
    t, r, m = periodic_case(times=(.15, .25))
    consumer = copy.deepcopy(config()['consumers'][0])
    consumer.update(amount=4, actor_relation='party_member')
    m['shared_pools']['consumers'] = [consumer]
    add(m, rule('benefit', 'benefit.base_add.all', 100, source_id='binding:benefit',
                resource_gate={'consumer_id': 'consumer'}))
    original = copy.deepcopy((t, r, m))
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    assert [row['expected'] for row in out['result']['hits']] == [90, 45]
    assert [(row['t_us'], row['accepted'], row['after']) for row in operation_rows(out)] == [
        (100_000, 8, 11), (200_000, 7, 0), (300_000, 0, 0)]
    consumers = [row for row in out['shared_pool_trace'] if row['type'] == 'consume' and row['cycle'] == 0]
    assert [(row['accepted'], row['after']) for row in consumers] == [(4, 7), (0, 0)]
    assert all(row['unit_scale'] == 10 and row['quantity_unit'] == 'atoms' for row in consumers)
    assert (t, r, m) == original


def test_false_filter_is_not_a_delivered_attempt_and_cannot_start_pool_cd():
    t, r, m = periodic_case(initial=40)
    operation = m['shared_pools']['operations'][0]
    operation['cooldown'] = {'seconds': 1, 'anchor': 'attempt'}
    operation['trigger']['condition'] = 'allowed'
    condition(m, 'off', 0, 2)
    m['condition_events'][-1].update(condition='allowed', value=False)
    condition(m, 'on', .15, 3)
    m['condition_events'][-1]['condition'] = 'allowed'
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    rows = operation_rows(out)
    assert [(row['t_us'], row['reason'], row['accepted']) for row in rows] == [
        (200_000, 'spent', 8), (300_000, 'cooldown', 0)]
    assert [row.get('outcome') for row in out['state_runtime_trace']] == [
        'not_activated', 'resource_operation', 'resource_operation']


def test_actual_tick_cd_has_distinct_occurrence_receipts_even_on_zero_debit():
    t, r, m = periodic_case(initial=40)
    m['shared_pools']['operations'][0]['cooldown'] = {'seconds': .15, 'anchor': 'success'}
    out = execute_mechanics(t, r, m)
    assert out['result'] is not None, out['issues']
    rows = operation_rows(out)
    assert [row['accepted'] for row in rows] == [8, 0, 8]
    assert [row['cooldown_until_us'] for row in rows] == [250_000, 250_000, 450_000]
    assert len({row['run_id'] for row in rows}) == len({row['id'] for row in rows}) == 3
    issued = {row['run_id'] for row in out['state_runtime_trace']}
    assert {row['run_id'] for row in rows} == issued
