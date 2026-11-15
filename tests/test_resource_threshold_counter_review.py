"""Independent R2a detached consistency and actual-ledger scope checks."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.resource_thresholds import ThresholdObserverProgram
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger
from tests.test_resource_counter_atomic import Budget
from tests.test_resource_counters import apply_controls, wrap
from tests.test_resource_threshold_counter import pure, setup
from tests.test_shared_pool_v2 import spend
from tests.test_state_runtime import event


@pytest.mark.parametrize('change', ['before_time', 'unit', 'resource_accept', 'reason', 'hidden_control'])
def test_counter_span_cannot_replace_window_or_resource_semantics(change):
    observer, candidate = pure()
    span = candidate['counter_span']
    if change == 'before_time':
        span['before']['last_time_us'] = candidate['receipts'][0]['t_us'] + 1
    elif change == 'unit':
        span['receipts'][0]['unit_scale'] *= 2
    elif change == 'resource_accept':
        span['receipts'][0]['resource_accepted'] += 1
    elif change == 'reason':
        span['receipts'][0]['reason'] = 'outside_window'
    else:
        span['after']['lifecycles']['field']['phase'] = 'ended'
    before = copy.deepcopy(candidate)
    with pytest.raises(SharedPoolError):
        observer.plan_commit(observer.initial_state(), candidate)
    assert candidate == before


def test_same_total_final_value_cannot_hide_wrong_first_key_accumulation():
    observer, counter, pools, clock = setup(multi=True)
    before, _ = apply_controls(counter, counter.initial_state(), clock, clock.start('field', event(), eligible=True))
    raw = SharedPoolLedger(pools, character_ids=['a', 'b'])
    rows = spend(raw, keys=['a', 'b'])
    after = counter.plan_spend(before, wrap(before, rows))
    candidate = {'receipt_start': 1, 'receipt_end': 2, 'receipts': rows,
        'counter_span': {'before': before, 'after': after['state'], 'receipts': after['counter_receipts']}}
    # Preserve the complete final total16 while falsifying the first per-key
    # step. It must not pass merely because the final observer reads 16.
    candidate['counter_span']['receipts'][0].update(accepted=4, after=4)
    candidate['counter_span']['receipts'][1].update(before=4, accepted=12)
    with pytest.raises(SharedPoolError):
        observer.plan_commit(observer.initial_state(), candidate)


def test_complete_lifecycle_binding_rejects_same_counter_ids_different_definition():
    observer, counter, pools, _ = setup()
    _, alternate, _, _ = setup(reset='never')
    with pytest.raises(SharedPoolError):
        SharedPoolLedger(pools, character_ids=['a', 'b'], counter_program=alternate,
            observer_program=observer, reserve_events=Budget().reserve)
    assert observer._counter_program._fingerprint == counter._fingerprint


def test_counter_threshold_above_pool_capacity_is_valid_and_raw_not_capped():
    observer, counter, pools, clock = setup()
    config = copy.deepcopy(observer._config)
    config['observers'][0]['threshold_atoms'] = pools['pools'][0]['capacity'] + 1
    program = ThresholdObserverProgram(config, pools, character_ids=['a', 'b'], counter_program=counter)
    initial, _ = apply_controls(counter, counter.initial_state(), clock, clock.start('field', event(), eligible=True))
    # This is a caller-provided pure seed, not authenticated live history.
    initial['values']['count']['total'] = pools['pools'][0]['capacity']
    raw = SharedPoolLedger(pools, character_ids=['a', 'b'])
    rows = spend(raw)
    after = counter.plan_spend(initial, wrap(initial, rows))
    plan = program.plan_commit(program.initial_state(), {'receipt_start': 1, 'receipt_end': 1, 'receipts': rows,
        'counter_span': {'before': initial, 'after': after['state'], 'receipts': after['counter_receipts']}})
    row = plan['observer_receipts'][0]
    assert row['before'] == pools['pools'][0]['capacity']
    assert row['after'] == row['before'] + 8 and row['crossed']
    assert row['authority'] == 'audit_no_dispatch_authority' and row['execution'] == 'candidate_only'
