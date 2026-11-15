"""True elemental/state/resource queue tests. No team/DB source assertions."""
import copy
import json
from pathlib import Path

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_reaction_operation_capabilities import setup
from tests.test_state_runtime import config


def values(*, delay=250000, duration=100000, gated=True):
    data = setup()
    model = data[2]
    model['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['swirl']
    state = config(delay=delay, duration=duration)
    state.update(schema_version=2, timers=[])
    state['lifecycles'][0]['source_id'] = 'binding:benefit'
    state['lifecycles'][0]['start']['action_ids'] = ['cast']
    model['state_runtime'] = state
    model['resource_reaction_contract'] = {'schema_version': 2, 'operation_ids': ['refill'],
        'lifecycle_gates': ([{'operation_id': 'refill', 'lifecycle_id': 'field',
                             'window': 'established_until_end', 'generation_policy': 'current_at_resource_attempt'}] if gated else [])}
    return data


def test_real_established_window_blocks_first_and_last_actual_reaction():
    data = values()
    out = execute_mechanics(*data)
    assert out['result'] is not None, out['issues']
    audit = out['resource_reactions']
    assert audit['verification_status'] == 'verified'
    assert [r['state'] for r in audit['operations']] == ['blocked_lifecycle', 'committed', 'blocked_lifecycle']
    assert [r['decision'] for r in audit['operations']] == ['match']*3
    assert [r['lifecycle_qualification']['reason'] for r in audit['operations']] == ['not_established', 'eligible', 'ended']
    assert len([r for r in out['shared_pool_trace'] if r['type'] == 'operation']) == 1


def test_false_life_never_invokes_ledger_attempt_or_starts_attempt_cd(monkeypatch):
    from genshin_engine.shared_pools import SharedPoolLedger
    data = values(delay=800000)
    data[2]['shared_pools']['operations'][0]['cooldown'] = {'seconds': 2, 'anchor': 'attempt'}
    def forbidden(*args, **kwargs):
        raise AssertionError('known-false life cannot call apply_operation')
    monkeypatch.setattr(SharedPoolLedger, 'apply_operation', forbidden)
    out = execute_mechanics(*data)
    assert out['result'] is not None, out['issues']
    assert all(r['state'] == 'blocked_lifecycle' for r in out['resource_reactions']['operations'])


def test_exact_end_is_half_open_even_before_expiry_queue():
    out = execute_mechanics(*values(delay=0, duration=300000))
    assert out['result'] is not None, out['issues']
    assert [r['state'] for r in out['resource_reactions']['operations']] == ['committed', 'blocked_lifecycle', 'blocked_lifecycle']


def test_failed_bridge_cannot_borrow_established_mirror(monkeypatch):
    from genshin_engine.resource_reaction_runtime import Runtime
    original = Runtime.begin
    def fail(self, *args):
        token = original(self, *args)
        self.context.state_runtime._failed = True
        return token
    monkeypatch.setattr(Runtime, 'begin', fail)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is None and out['status'] == 'pending'
    assert any(r['type'] == 'hit' for r in out.get('mechanics_trace', []))
    assert not [r for r in out.get('shared_pool_trace', []) if r['type'] == 'operation']


def test_empty_gate_only_keeps_existing_rq1_resource_path():
    data = values(gated=False)
    prior = copy.deepcopy(data)
    prior[2]['resource_reaction_contract'] = {'schema_version': 1, 'operation_ids': ['refill']}
    a, b = execute_mechanics(*data), execute_mechanics(*prior)
    assert a['result'] is not None and b['result'] is not None, (a['issues'], b['issues'])
    assert a['shared_pool_trace'] == b['shared_pool_trace']
    assert a['result']['hits'] == b['result']['hits']
    assert a['resource_reactions']['usage'] == b['resource_reactions']['usage']
    assert a['resource_reactions']['operations'] == b['resource_reactions']['operations']


@pytest.mark.parametrize('field', ['lifecycle_id', 'generation_policy'])
def test_invalid_gate_never_falls_back_to_legacy(field):
    data = values()
    data[2]['resource_reaction_contract']['lifecycle_gates'][0][field] = 'unsupported'
    out = execute_mechanics(*data)
    assert out['result'] is None and out['status'] in ('invalid', 'pending')


def test_selector_false_does_not_read_lifecycle_or_change_original_decision(monkeypatch):
    from genshin_engine.resource_lifecycle_runtime import Runtime
    data = values()
    data[2]['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['melt']
    def forbidden(*args):
        raise AssertionError('false selector must not query lifecycle')
    monkeypatch.setattr(Runtime, 'prepare', forbidden)
    out = execute_mechanics(*data)
    assert out['result'] is not None, out['issues']
    assert [r['state'] for r in out['resource_reactions']['operations']] == ['no_match']*3
    assert not any('lifecycle_qualification' in r for r in out['resource_reactions']['operations'])


@pytest.mark.parametrize('target', ['bridge', 'ledger', 'clock', 'timer_index', 'rule'])
def test_detached_actual_objects_or_graph_cannot_borrow_initial_authority(monkeypatch, target):
    from genshin_engine.resource_reaction_runtime import Runtime
    original = Runtime.begin
    def change(self, *args):
        token = original(self, *args)
        ctx = self.context
        if target == 'bridge': ctx.state_runtime = copy.copy(ctx.state_runtime)
        elif target == 'ledger': ctx.shared_pools.ledger = copy.copy(ctx.shared_pools.ledger)
        elif target == 'clock': ctx.state_runtime.clock = copy.copy(ctx.state_runtime.clock)
        elif target == 'timer_index': ctx.state_runtime.clock.by_life['field'].append({'invented': True})
        else:
            ctx.rules = copy.deepcopy(ctx.rules)
            ctx.rules['benefit']['owner'] = 'different'
        return token
    monkeypatch.setattr(Runtime, 'begin', change)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is None and out['status'] == 'pending', out['issues']
    assert not [r for r in out.get('shared_pool_trace', []) if r['type'] == 'operation']


@pytest.mark.parametrize('target', ['phase', 'generation', 'control'])
def test_applied_mirror_mutation_is_not_a_completed_batch(monkeypatch, target):
    from genshin_engine.resource_reaction_runtime import Runtime
    original = Runtime.begin
    def change(self, *args):
        token = original(self, *args)
        bridge = self.context.state_runtime
        if target == 'phase': bridge._applied_lifecycles['field']['phase'] = 'created'
        elif target == 'generation': bridge._applied_lifecycles['field']['generation_uid'] += '-copy'
        else: bridge._applied_control_sequence += 1
        return token
    monkeypatch.setattr(Runtime, 'begin', change)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['status'] == 'pending' and out['result'] is None
    assert not [r for r in out.get('shared_pool_trace', []) if r['type'] == 'operation']


def test_report_row_cannot_change_generation_and_keep_verified(monkeypatch):
    from genshin_engine.resource_reaction_runtime import Runtime
    original = Runtime.attach
    def change(self, output):
        self.audit._rows[0]['lifecycle_qualification']['generation_uid'] = 'different'
        return original(self, output)
    monkeypatch.setattr(Runtime, 'attach', change)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is None and out['resource_reactions']['verification_status'] == 'verification_pending'
    assert len([r for r in out['shared_pool_trace'] if r['type'] == 'operation']) == 3


def test_gate_budget_failure_preserves_previous_income_and_parent_damage(monkeypatch):
    from genshin_engine.resource_lifecycle_runtime import Runtime
    original = Runtime.prepare
    def limit(self, operation):
        if self.service.usage['attempts'] == 1:
            self.service.work_limit = self.service.usage['work']
        return original(self, operation)
    monkeypatch.setattr(Runtime, 'prepare', limit)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is None and out['status'] == 'pending'
    assert len([r for r in out['shared_pool_trace'] if r['type'] == 'operation']) == 1
    assert len([r for r in out['mechanics_trace'] if r['type'] == 'hit']) == 2


def test_control_cannot_reenter_during_slot_reserve(monkeypatch):
    from genshin_engine.mechanics import _Mechanics
    original = _Mechanics._reserve_events
    def reserve(self, count, kind):
        rq = getattr(self, 'resource_reactions', None)
        if rq and rq.lifecycle and rq.lifecycle.lease is not None and kind == 'resource_reaction_audit':
            self.state_runtime._ready()
        return original(self, count, kind)
    monkeypatch.setattr(_Mechanics, '_reserve_events', reserve)
    out = execute_mechanics(*values(delay=0, duration=700000))
    assert out['result'] is None and out['status'] == 'pending', out['issues']
    assert not [r for r in out.get('shared_pool_trace', []) if r['type'] == 'operation']


def test_new_generation_allows_new_reaction_from_old_parent_without_resetting_pool():
    from tests.test_mechanics_execution import action
    data = values(delay=0, duration=700000)
    data[1]['actions'].append(action('cast-again', 'e', .25, .26, 50, repeat={'mode': 'once'}))
    data[2]['state_runtime']['lifecycles'][0]['start']['action_ids'].append('cast-again')
    out = execute_mechanics(*data)
    assert out['result'] is not None, out['issues']
    rows = out['resource_reactions']['operations']
    generations = [r['lifecycle_qualification']['generation_uid'] for r in rows]
    assert generations[0] != generations[1] == generations[2]
    assert [r['parent_event_id'] for r in out['resource_reactions']['occurrences']] == ['h0', 'h1', 'h2']
    grants = [r for r in out['shared_pool_trace'] if r['type'] == 'operation']
    assert [r['before'] for r in grants] == [0, 2, 4]
    assert [r['after'] for r in grants] == [3, 5, 7]


def test_not_started_target_does_not_borrow_same_owner_other_lifecycle():
    from tests.test_mechanics_execution import action
    data = values(delay=0, duration=700000)
    data[1]['actions'].append(action('later', 'e', .8, .9, 50, repeat={'mode': 'once'}))
    second = copy.deepcopy(data[2]['state_runtime']['lifecycles'][0])
    second.update(id='field.extra', source_id='other-source', start_order=2, establishment_order=11, end_order=2)
    second['start']['action_ids'] = ['later']
    data[2]['state_runtime']['lifecycles'].append(second)
    data[2]['resource_reaction_contract']['lifecycle_gates'][0]['lifecycle_id'] = 'field.extra'
    out = execute_mechanics(*data)
    assert out['result'] is not None, out['issues']
    assert [r['lifecycle_qualification']['reason'] for r in out['resource_reactions']['operations']] == ['not_started']*3
    assert not [r for r in out['shared_pool_trace'] if r['type'] == 'operation']


def test_unfinished_dispatch_after_established_mirror_never_completes_batch(monkeypatch):
    from genshin_engine.state_runtime_mechanics import (
        StateRuntimeMechanics,
        route_event_id,
    )
    from tests.test_mechanics_execution import add, rule
    data = values(delay=0, duration=700000)
    model = data[2]
    model['state_runtime']['lifecycles'][0]['source_id'] = 'field-source'
    model['state_runtime']['lifecycles'][0]['hooks']['established'] = [
        {'kind': 'dispatch_rule', 'ref_id': 'hook-target', 'order': 10}]
    add(model, rule('hook-target', source_id='field-source', domain='provider',
        trigger={'event': 'condition', 'phase': 'pre', 'event_ids': [route_event_id('dispatch_rule', 'hook-target')]}))
    seen = []
    def pending(self, dispatch):
        seen.append((self._applied_lifecycles['field']['phase'], self._active_delivery['complete']))
        return 'pending'
    monkeypatch.setattr(StateRuntimeMechanics, '_dispatch', pending)
    out = execute_mechanics(*data)
    assert seen == [('established', False)], out['issues']
    assert out['result'] is None and out['status'] == 'pending', out['issues']
    assert any(r['code'] == 'state_dispatch_pending' for r in out['issues'])
    assert not [r for r in out.get('shared_pool_trace', []) if r['type'] == 'operation']


def timer_counter():
    path = Path(__file__).resolve().parent / 'fixtures/resource_lifecycle_old_timer.json'
    data = json.loads(path.read_text(encoding='utf-8'))['cases']['timer-counter']['input']
    data[2]['state_runtime']['schema_version'] = 2
    data[2]['resource_reaction_contract'] = {'schema_version': 2, 'operation_ids': ['reaction-income'],
        'lifecycle_gates': [{'operation_id': 'reaction-income', 'lifecycle_id': 'field',
            'window': 'established_until_end', 'generation_policy': 'current_at_resource_attempt'}]}
    return data


def test_real_periodic_spend_counter_and_lifecycle_gate_share_completed_control():
    data = timer_counter()
    prior = copy.deepcopy(data)
    prior[2]['resource_reaction_contract']['lifecycle_gates'] = []
    output, baseline = execute_mechanics(*data), execute_mechanics(*prior)
    assert output['result'] is not None, output['issues']
    assert output['shared_pool_trace'] == baseline['shared_pool_trace']
    assert output['resource_counters'] == baseline['resource_counters']
    assert all(r['lifecycle_qualification']['qualified'] is True for r in output['resource_reactions']['operations'])


def test_counter_control_cursor_cannot_lag_applied_lifecycle(monkeypatch):
    from genshin_engine.resource_reaction_runtime import Runtime
    original = Runtime.begin
    def lag(self, *args):
        token = original(self, *args)
        self.context.shared_pools.ledger._counter_state['last_control_sequence'] -= 1
        return token
    monkeypatch.setattr(Runtime, 'begin', lag)
    output = execute_mechanics(*timer_counter())
    assert output['result'] is None and output['status'] == 'pending', output['issues']
    assert not [r for r in output.get('shared_pool_trace', []) if r.get('operation_id') == 'reaction-income']
    assert [r for r in output['shared_pool_trace'] if r.get('operation_id') == 'refill']


@pytest.mark.parametrize('order,first', [(5, 'committed'), (50, 'blocked_lifecycle')])
def test_same_time_native_order_decides_real_start_before_reaction(order, first):
    from tests.test_mechanics_execution import action
    data = values(delay=0, duration=700000)
    data[1]['actions'].append(action('new-start', 'e', .2, .21, order, repeat={'mode': 'once'}))
    data[2]['state_runtime']['lifecycles'][0]['start']['action_ids'] = ['new-start']
    out = execute_mechanics(*data)
    assert out['result'] is not None, out['issues']
    assert [r['state'] for r in out['resource_reactions']['operations']] == [first, 'committed', 'committed']
