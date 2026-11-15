import copy

import pytest

from genshin_engine import mechanics
from genshin_engine import resource_counter_frame_runtime as runtime
from genshin_engine.mechanics import execute_mechanics
from tests.test_counter_self_frame_execution import basic
from tests.test_mechanics_execution import action
from tests.test_shared_pools import config


def delayed(*, old_gate=False, new_gate=False):
    args = basic(times=(.45,))
    args[0]['loop_duration'] = args[1]['period'] = .4
    args[1]['max_cycles'] = 3
    args[2]['rules'][0]['timing']['duration'] = 1
    if old_gate or new_gate:
        consumer = config()['consumers'][0]
        consumer.update(eligibility_rule_id='view-benefit', source_id='view-benefit', actor_relation='party_member', amount=1)
        args[2]['shared_pools']['consumers'] = [consumer]
        args[2]['state_runtime']['timers'][0]['max_ticks'] = 1
        args[2]['rules'][0]['resource_gate'] = {'consumer_id': consumer['id']}
    if new_gate:
        args[2]['rules'][0]['timing']['duration'] = .2
        args[2]['rules'][0]['trigger']['action_ids'].append('again')
        args[1]['actions'].append(action('again', start=.25, end=.3, order=5, repeat={'mode': 'once'}))
    return args


def signature(out):
    boundary = out['rotation_check']['cycles'][0]['boundary_state']['mechanics']
    rows = boundary['pending_samples']
    return next(r['counter_self_frame'] for r in rows if r['reference_id'] == 'output')


def test_boundary_keeps_capture_eight_not_later_raw_nineteen_or_absolute_ids():
    out = execute_mechanics(*delayed())
    assert out['result'] is not None, out['issues']
    # The .45 hit belongs to a later check window, not the .4 report window.
    assert out['resource_counter_views']['trace'][0]['reading']['raw_atoms'] == 8
    value = signature(out)
    assert value[0]['raw_atoms'] == 8
    assert value[0]['layer_count'] == 1
    assert not any(text in repr(value) for text in ('candidate-frame:', '0:cast', 'counter_cursor'))


def test_pending_consumer_boundary_remembers_gate_same_instance_vs_replacement():
    same = execute_mechanics(*delayed(old_gate=True))
    replaced = execute_mechanics(*delayed(new_gate=True))
    assert same['result'] is not None, same['issues']
    assert replaced['result'] is not None, replaced['issues']
    assert signature(same)[0]['current_gate_instance_ordinal'] == 0
    assert signature(replaced)[0]['current_gate_instance_ordinal'] is None


def test_boundary_cannot_silently_drop_missing_committed_frame(monkeypatch):
    original = mechanics._Mechanics.boundary
    def missing(ctx, time, cycle, pending=()):
        ctx.counter_views.frames.store._frames.clear()
        return original(ctx, time, cycle, pending)
    monkeypatch.setattr(mechanics._Mechanics, 'boundary', missing)
    out = execute_mechanics(*delayed())
    assert out['status'] == 'pending' and out['result'] is None
    assert any(i['code'] == 'counter_frame_boundary' for i in out['issues'])


@pytest.mark.parametrize('which', ['ledger', 'counter_program', 'definition'])
def test_real_context_binding_rejects_replacement_or_mutated_program(monkeypatch, which):
    original = runtime.CounterFrameRuntime.observe
    def altered(self, event, snapshot):
        if which == 'ledger':
            self.pools.ledger = copy.deepcopy(self.ledger)
        elif which == 'counter_program':
            self.pools.counter_program = copy.deepcopy(self.counter_program)
        else:
            self.counter_program._config['counters'][0]['reset'] = 'never'
        return original(self, event, snapshot)
    monkeypatch.setattr(runtime.CounterFrameRuntime, 'observe', altered)
    out = execute_mechanics(*basic())
    assert out['status'] == 'pending' and out['result'] is None, out['issues']
    assert any(i['code'] in ('counter_frame_ledger_binding', 'counter_frame_definition_binding') for i in out['issues'])
    assert out['resource_counter_views']['historical_capture']['usage']['frames'] == 0
