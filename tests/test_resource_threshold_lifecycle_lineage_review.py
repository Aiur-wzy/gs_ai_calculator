"""Actual clock2 threshold receipt provenance; private contract, no game authority."""
import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime import StateRuntime, StateRuntimeError
from tests.test_mechanics_execution import condition
from tests.test_resource_threshold_lifecycle_execution import life_case


@pytest.mark.parametrize('phase', ['pre', 'post'])
def test_native_actual_source_phase_and_resource_span_are_preserved(phase):
    out = execute_mechanics(*life_case(native=phase))
    assert out['result'] is not None, out['issues']
    row, = out['resource_threshold_triggers']['trace']
    source = next(r for r in out['shared_pool_trace'] if r['run_id'] == row['source_run_id'])
    start = next(r for r in out['state_runtime']['issued_trace']
                 if r['type'] == 'lifecycle_start' and r['lifecycle_id'] == 'field_b')
    assert row['phase'] == phase and 'source_origin' not in row
    assert row['source_event_id'] == source['event_id']
    assert row['target_generation_uid'] == start['generation_uid']
    assert row['before'] == 8 and row['after'] == 16
    assert row['receipt_span'] == [2, 2]


def test_false_qualification_retains_source_but_has_no_target_generation():
    args = life_case()
    args[2]['resource_threshold_triggers']['bindings'][0]['qualification'] = {'condition': 'allow'}
    condition(args[2], 'allow', 0, 2)
    args[2]['condition_events'][-1]['value'] = False
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    row, = out['resource_threshold_triggers']['trace']
    assert row['outcome'] == 'not_activated' and row['target_generation_uid'] is None
    assert row['source_origin']['timer_id'] == 'refresh'
    assert row['before'] == 8 and row['after'] == 16
    assert row['counter_observation']['counter_id'] == 'count'


def test_failed_target_retains_true_source_lineage_and_resource_prefix(monkeypatch):
    original = StateRuntime._finish

    def finish(clock, draft, time):
        if any(r['type'] == 'lifecycle_start' and r['lifecycle_id'] == 'field_b' for r in draft['trace']):
            raise StateRuntimeError('state_event_limit', 'independent target reserve failure', severity='pending')
        return original(clock, draft, time)

    monkeypatch.setattr(StateRuntime, '_finish', finish)
    out = execute_mechanics(*life_case())
    assert out['status'] == 'pending' and out['result'] is None
    row = next(r for r in out['resource_threshold_triggers']['trace'] if r['type'] == 'threshold_dispatch_failed')
    assert row['outcome'] == 'pending' and row['lifecycle_id'] == 'field_b'
    assert row['before'] == 8 and row['after'] == 16 and row['receipt_span'] == [2, 2]
    assert row['source_origin']['timer_id'] == 'refresh'
    assert row['counter_observation']['counter_id'] == 'count'
    assert row['source_run_id'] == out['shared_pool_trace'][-1]['run_id']
    assert out['shared_pool_trace'][-1]['after'] == 3
    assert out['resource_counters']['applied_state']['values']['count']['total'] == 16
