"""Independent R3 actual runner checks, output-only candidate review."""
import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.state_runtime_mechanics import StateRuntimeMechanics
from tests.test_resource_threshold_lifecycle_execution import life_case


@pytest.mark.parametrize('field,value', [('first_offset_us', 10000), ('interval_us', 20000), ('max_ticks', 3)])
def test_timer_definition_cannot_change_after_complete_graph_validation(monkeypatch, field, value):
    original = StateRuntimeMechanics._apply_lifecycle_task
    def apply(bridge, task, **kwargs):
        bridge.clock.timers['field_b_tick'][field] = value
        return original(bridge, task, **kwargs)
    monkeypatch.setattr(StateRuntimeMechanics, '_apply_lifecycle_task', apply)
    out = execute_mechanics(*life_case())
    assert out['result'] is None, 'Changed timer used old full-graph authority'
    assert any(i['code'] in ('state_threshold_authority', 'state_dispatch_target_changed') for i in out['issues'])
    assert not any(r['type'] == 'lifecycle_start' and r['lifecycle_id'] == 'field_b' for r in out['state_runtime']['issued_trace'])


@pytest.mark.parametrize('counter', [True, False])
def test_unknown_target_condition_preserves_real_operation_but_creates_no_life(counter):
    from tests.test_mechanics_execution import condition
    args = life_case(counter=counter)
    args[2]['resource_threshold_triggers']['bindings'][0]['qualification'] = {'condition': 'allow'}
    condition(args[2], 'allow', 0, 2)
    args[2]['condition_events'][-1]['value'] = None
    out = execute_mechanics(*args)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(i['code'] == 'threshold_condition_unknown' for i in out['issues'])
    assert out['shared_pool_trace'][-1]['after'] == 3
    assert not any(r['type'] == 'lifecycle_start' and r['lifecycle_id'] == 'field_b' for r in out['state_runtime']['issued_trace'])
    if counter:
        assert out['resource_counters']['applied_state']['values']['count']['total'] == 16


def test_empty_source_batch_claim_cannot_invent_unrelated_queued_target():
    args = life_case()
    args[2]['resource_threshold_observers']['observers'][0]['threshold_atoms'] = 100
    before = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert args == before
    assert out['result'] is not None, out['issues']
    assert [h['expected'] for h in out['result']['hits']] == [45] * 5
    assert out['resource_counters']['applied_state']['values']['count']['total'] == 19
    assert out['resource_threshold_triggers']['trace'] == []
    assert not any(r['type'] == 'lifecycle_start' and r['lifecycle_id'] == 'field_b' for r in out['state_runtime']['issued_trace'])


def test_missing_frontline_target_qualification_does_not_consume_position(monkeypatch):
    args = life_case()
    args[2]['resource_threshold_triggers']['bindings'][0]['qualification'] = {'owner_presence': 'frontline'}
    # Character a is the actual frontline in this fixture: test author-false hook
    # at the authenticated bridge boundary without manufacturing source receipts.
    original = StateRuntimeMechanics.accept_lifecycle_command
    captured = []
    def accept(bridge, *a, **kw):
        captured.append(bridge.clock._position_index.steps)
        kw['qualified'] = False
        result = original(bridge, *a, **kw)
        assert bridge.clock._position_index.steps == captured[-1]
        return result
    monkeypatch.setattr(StateRuntimeMechanics, 'accept_lifecycle_command', accept)
    out = execute_mechanics(*args)
    assert captured and out['result'] is not None, out['issues']
    assert out['resource_threshold_triggers']['trace'][0]['outcome'] == 'not_activated'
    assert not any(r['type'] == 'lifecycle_start' and r['lifecycle_id'] == 'field_b' for r in out['state_runtime']['issued_trace'])

@pytest.mark.parametrize('continuation_mode', [False, True])
def test_actual_context_event_id_must_equal_its_typed_position_identity(continuation_mode):
    from genshin_engine.state_runtime import StateRuntime, StateRuntimeError
    from tests.test_state_runtime_positions_v2 import (
        continuation,
        declarations,
        positioned,
        snapshot,
    )
    clock = StateRuntime(declarations(), character_ids=['a'], emit_ordered_steps=True)
    value = positioned(phase='pre')
    if continuation_mode:
        value = continuation(value)
    value['event_id'] = 'different-actual-event'
    before = snapshot(clock)
    with pytest.raises(StateRuntimeError):
        clock.start('field_b' if continuation_mode else 'field', value, eligible=True)
    assert snapshot(clock) == before


def test_actual_causal_path_rejects_bool_even_if_equal_to_integer_anchor():
    from genshin_engine.state_runtime import StateRuntime, StateRuntimeError
    from tests.test_state_runtime_positions_v2 import declarations, positioned, snapshot
    clock = StateRuntime(declarations(), character_ids=['a'], emit_ordered_steps=True)
    value = positioned(phase='pre')
    value['position']['anchor']['causal'] = [1]
    value['causal'] = [True]
    before = snapshot(clock)
    with pytest.raises(StateRuntimeError):
        clock.start('field', value, eligible=True)
    assert snapshot(clock) == before

@pytest.mark.parametrize('counter', [True, False])
def test_target_control_prefix_retained_if_its_batch_completion_report_fails(monkeypatch, counter):
    from genshin_engine.state_runtime import StateRuntimeError
    original = StateRuntimeMechanics._v2_trace_candidate
    def candidate(bridge, row):
        if (row['type'] == 'state_batch_completed'
                and bridge._applied_lifecycles.get('field_b', {}).get('phase') == 'established'):
            raise StateRuntimeError('state_trace_limit', 'independent tail limit', severity='pending')
        return original(bridge, row)
    monkeypatch.setattr(StateRuntimeMechanics, '_v2_trace_candidate', candidate)
    out = execute_mechanics(*life_case(counter=counter))
    assert out['status'] == 'pending' and out['result'] is None
    assert out['shared_pool_trace'][-1]['after'] == 3
    assert out['state_runtime']['applied_lifecycles']['field_b']['phase'] == 'established'
    assert any(r['type'] == 'state_delivery_failed' for r in out['state_runtime_trace'])
    assert not any(r['type'] == 'threshold_dispatch_completed' for r in out['resource_threshold_triggers']['trace'])
    assert out['resource_threshold_triggers']['unapplied_lifecycle_commands']


def test_target_execution_budget_failure_does_not_publish_clock_generation(monkeypatch):
    from genshin_engine.state_runtime import StateRuntimeError
    original = StateRuntimeMechanics._apply_lifecycle_task
    def apply(bridge, task, **kwargs):
        reserve = bridge.ctx._reserve_events
        def reject(count, module):
            if module == 'resource_threshold_dispatch':
                raise StateRuntimeError('state_event_limit', 'independent target reserve', severity='pending')
            return reserve(count, module)
        bridge.ctx._reserve_events = reject
        return original(bridge, task, **kwargs)
    monkeypatch.setattr(StateRuntimeMechanics, '_apply_lifecycle_task', apply)
    out = execute_mechanics(*life_case())
    assert out['status'] == 'pending' and out['result'] is None
    assert out['shared_pool_trace'][-1]['after'] == 3
    assert 'field_b' not in out['state_runtime']['applied_lifecycles']
    assert not any(r['type'] == 'lifecycle_start' and r['lifecycle_id'] == 'field_b' for r in out['state_runtime']['issued_trace'])
