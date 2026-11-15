"""Independent public v2 counter and ordinary-timer queue regressions."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime import StateRuntimeError
from genshin_engine.state_runtime_mechanics import StateRuntimeMechanics
from tests.test_mechanics_execution import action
from tests.test_resource_threshold_counter_execution import (
    counter_threshold_case as case,
)


@pytest.mark.parametrize('field', ['event_ordinal', 'parent_order'])
def test_original_timer_object_mutation_cannot_claim_authority(monkeypatch, field):
    original = StateRuntimeMechanics.authorize_threshold_operation
    seen = []
    def verify(bridge, event, phase, parent, operation_id, expected_origin):
        target, key = (event, 'ordinal') if field == 'event_ordinal' else (parent, 'order')
        old = target[key]
        before = copy.deepcopy(bridge.ctx.shared_pools.ledger.threshold_dispatch_report())
        target[key] += 1
        with pytest.raises(StateRuntimeError):
            original(bridge, event, phase, parent, operation_id, expected_origin)
        assert bridge.ctx.shared_pools.ledger.threshold_dispatch_report() == before
        target[key] = old
        seen.append(event['run_id'])
        return original(bridge, event, phase, parent, operation_id, expected_origin)
    monkeypatch.setattr(StateRuntimeMechanics, 'authorize_threshold_operation', verify)
    out = execute_mechanics(*case())
    assert out['result'] is not None, out['issues']
    assert len(seen) == 1 and [h['expected'] for h in out['result']['hits']] == [45, 90, 90]


def test_counter_reset_new_generation_does_not_reuse_old_observation_baseline():
    args = case(times=(.12, .3, .36))
    args[2]['shared_pools']['pools'][0]['initial'] = {'team': 100}
    args[1]['actions'].append(action('again', start=.15, end=.18, order=5, repeat={'mode': 'once'}))
    args[2]['state_runtime']['lifecycles'][0]['start']['action_ids'].append('again')
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    rows = out['resource_threshold_observers']['trace']
    assert [(r['before'], r['after']) for r in rows] == [(0, 8), (0, 8), (8, 16), (16, 24)]
    assert [h['expected'] for h in out['result']['hits']] == [45, 45, 90]
    assert out['resource_threshold_triggers']['trace'][0]['time_us'] == 350000


def test_native_post_counter_crossing_does_not_backdate_current_hit():
    args = case()
    args[2]['state_runtime']['timers'] = []
    args[2]['shared_pools']['operations'][0]['trigger'] = {'event': 'hit', 'phase': 'post'}
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert [h['expected'] for h in out['result']['hits']] == [45, 45, 90]
    assert out['resource_threshold_triggers']['trace'][0]['source_run_id'] == '0:h1'


def test_shared_operation_with_two_timer_routes_is_not_an_implicit_origin_alias():
    args = case()
    second = copy.deepcopy(args[2]['state_runtime']['timers'][0])
    second['id'] = 'timer-other'
    second['order'] += 1
    args[2]['state_runtime']['timers'].append(second)
    out = execute_mechanics(*args)
    assert out['status'] == 'invalid' and out['result'] is None
    assert any(i['code'] == 'threshold_dispatch_origin' for i in out['issues'])


def test_counter_pending_reason_is_not_laundered_as_dispatch_schema_error():
    args = case()
    args[2]['resource_counters']['counters'][0]['status'] = 'pending'
    out = execute_mechanics(*args)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(i['code'] == 'counter_pending' for i in out['issues']), out['issues']
