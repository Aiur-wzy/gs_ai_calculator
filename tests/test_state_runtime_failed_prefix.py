"""Failed native actions must not become a fictitious full clock boundary."""
import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_state_runtime_execution import field_case


def failure_case(interval=100000, delay=300000):
    template, rotation, mechanics = field_case()
    template['switches'] = []
    rotation['actions'] = [a for a in rotation['actions'] if a['id'] != 'switch-b']
    next(a for a in rotation['actions'] if a['id'] == 'cast-b')['start'] = .8
    state = mechanics['state_runtime']
    state['lifecycles'][0].update(establishment_delay_us=delay, duration_us=20000000)
    state['timers'][0].update(first_offset_us=interval, interval_us=interval, max_ticks=200)
    return template, rotation, mechanics


@pytest.mark.parametrize('interval,delay', [(100000, 300000), (70000, 300001), (100001, 299999)])
def test_native_failure_retains_real_prefix_instead_of_advancing_clock_to_boundary(interval, delay):
    inputs = failure_case(interval, delay)
    before = copy.deepcopy(inputs)
    out = execute_mechanics(*inputs)
    assert out['status'] == 'failed', out['issues']
    assert 'not_frontline' in {i['code'] for i in out['issues']}
    assert 'state_due_event' not in {i['code'] for i in out['issues']}
    assert out['rotation_check']['cycles'][0]['boundary_state'] is None
    assert out['rotation_check']['cycles'][0]['incomplete'] is True
    assert all(row['time_us'] < 800000 for row in out['state_runtime_trace'])
    assert inputs == before


def test_failed_prefix_does_not_commit_future_timer_resource_operations():
    from genshin_app.calculation_completion import calculation_completion
    from genshin_engine.state_runtime_mechanics import route_event_id
    from tests.test_shared_pools import config
    inputs = failure_case()
    pools = config()
    pools['consumers'] = []
    pools['operations'][0]['trigger'] = {'event': 'condition', 'phase': 'post',
        'event_ids': [route_event_id('resource_operation', 'refill')]}
    inputs[2]['shared_pools'] = pools
    inputs[2]['rules'] = []
    inputs[2]['initial_state']['rules'] = []
    inputs[2]['state_runtime']['timers'][0]['source_id'] = 'binding:refill'
    inputs[2]['state_runtime']['timers'][0]['payload'] = {'kind': 'resource_operation', 'ref_id': 'refill'}
    out = execute_mechanics(*inputs)
    assert out['status'] == 'failed', out['issues']
    operations = [r for r in out['shared_pool_trace'] if r['type'] == 'operation']
    assert [r['t_us'] for r in operations] == [400000, 500000, 600000, 700000]
    assert calculation_completion(result=out['result'])['task_admission'] != 'accepted'
