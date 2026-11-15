"""Independent real queue threshold causality and failure-prefix checks."""
from __future__ import annotations

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.resource_threshold_runtime import ThresholdRuntime
from genshin_engine.shared_pools import SharedPoolError
from tests.test_resource_threshold_execution import case, independent_crossings


def crossings(args):
    return independent_crossings(args, second_id="again")

def test_same_parent_second_crossing_obeys_target_cooldown_and_preserves_each_receipt():
    args = crossings(case(times=(.2,)))
    args[2]['rules'][0]['timing']['cooldown'] = 1
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert out['result']['hits'][0]['expected'] == 90
    dispatch = [r for r in out['resource_threshold_triggers']['trace'] if r['origin'] == 0]
    assert [r['outcome'] for r in dispatch] == ['activated', 'not_activated']
    assert dispatch[1]['reason'] == 'internal_cooldown'
    assert dispatch[0]['dispatch_run_id'] != dispatch[1]['dispatch_run_id']
    assert len([r for r in out['resource_threshold_triggers']['authority']['claimed']
                if r['context']['run_id'] == '0:h0']) == 2


@pytest.mark.parametrize('phase,expected', [('pre', [90, 45]), ('post', [45, 45])])
def test_trigger_ttl_expiry_at_next_hit_cannot_be_extended_by_high_balance(phase, expected):
    args = case(phase, times=(.2, .3))
    args[2]['rules'][0]['timing']['duration'] = .1
    # Second event is above the threshold, but not a new entry.
    args[2]['shared_pools']['operations'][0]['trigger']['event_ids'] = ['h0', 'h1']
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert [h['expected'] for h in out['result']['hits']] == expected
    observations = [r for r in out['resource_threshold_observers']['trace'] if r['run_id'].startswith('0:')]
    assert [r['crossed'] for r in observations] == [True, False]
    assert len(out['resource_threshold_triggers']['trace']) == 1


def test_lower_entry_uses_real_spend_instead_of_requiring_grant():
    args = case(initial=51, times=(.2,))
    op = args[2]['shared_pools']['operations'][0]
    op.update(operation='spend', amount=2, spend_policy='all_or_nothing')
    obs = args[2]['resource_threshold_observers']['observers'][0]
    obs['comparison'] = 'enter_lte'
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert out['result']['hits'][0]['expected'] == 90
    assert out['shared_pool_trace'][0]['accepted'] == 2
    assert out['resource_threshold_observers']['trace'][0]['after'] == 49
    assert out['resource_threshold_triggers']['trace'][0]['outcome'] == 'activated'


def test_failure_after_first_of_multiple_operations_does_not_commit_later_balances(monkeypatch):
    args = crossings(case())
    original = ThresholdRuntime.after_operation
    def stop(self, ctx, event, phase, parent, operation_id, run_id):
        if operation_id == 'down':
            raise SharedPoolError('threshold_dispatch_limit', 'Synthetic later bridge failure', severity='pending')
        return original(self, ctx, event, phase, parent, operation_id, run_id)
    monkeypatch.setattr(ThresholdRuntime, 'after_operation', stop)
    out = execute_mechanics(*args)
    assert out['status'] == 'pending' and out['result'] is None
    assert [(r['operation_id'], r['after']) for r in out['shared_pool_trace']] == [('refill', 50), ('down', 48)]
    assert [r['rule_id'] for r in out['mechanics_trace'] if r['type'] == 'activation' and r.get('threshold_dispatch_run_id')] == ['boost']
    assert out['resource_threshold_triggers']['authority']['issued'][0]['context']['command_id'] == 'refill'
    assert not any(r['type'] == 'hit' for r in out['mechanics_trace'])


def test_initial_high_balance_with_target_value_does_not_implicitly_activate_rule():
    args = case(initial=50)
    args[2]['rules'][0]['effects'][0]['value'] = .5
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert [h['expected'] for h in out['result']['hits']] == [45, 45]
    assert out['resource_threshold_triggers']['authority']['issued'] == []
    assert not any(r['type'] == 'activation' for r in out['mechanics_trace'])
