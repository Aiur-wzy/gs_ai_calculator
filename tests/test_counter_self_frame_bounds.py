import copy

import pytest

from genshin_engine import mechanics
from genshin_engine import resource_counter_frame_runtime as runtime
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.shared_pools import SharedPoolError
from tests.test_counter_self_frame_execution import basic, rows
from tests.test_mechanics_execution import action, damages
from tests.test_snapshot_captures import capture


def frames(out):
    return out['resource_counter_views']['historical_capture']


def assert_prefix(out, count=8):
    assert out['status'] == 'pending', out['issues']
    assert out['result'] is None and out['resolved_template'] is None
    assert out['resource_counters']['applied_state']['values']['count']['total'] == count
    assert not frames(out)['capture_trace']
    assert frames(out)['usage']['frames'] == 0
    assert not rows(out)


@pytest.mark.parametrize('mode', ['snapshot', 'frame', 'verification', 'shared_budget'])
def test_capture_failure_keeps_real_resource_prefix_but_publishes_no_output_frame(monkeypatch, mode):
    original = runtime.CounterFrameRuntime.observe
    contexts = []
    def limited(self, event, snapshot):
        contexts.append(self.ctx)
        if mode == 'snapshot':
            monkeypatch.setitem(runtime.FRAME_RUNTIME_LIMITS, 'snapshot_bytes', 1)
        elif mode == 'frame':
            self.store._limits['frame_bytes'] = 1
        elif mode == 'verification':
            monkeypatch.setitem(runtime.FRAME_RUNTIME_LIMITS, 'verification_bytes', self.verification_bytes)
        return original(self, event, snapshot)
    monkeypatch.setattr(runtime.CounterFrameRuntime, 'observe', limited)
    old_reserve = mechanics._Mechanics._reserve_events
    if mode == 'shared_budget':
        def failed(ctx, amount, kind=None):
            if kind == 'resource_counter_frame':
                raise SharedPoolError('pool_event_limit', 'engineering failure', severity='pending')
            old_reserve(ctx, amount, kind)
        monkeypatch.setattr(mechanics._Mechanics, '_reserve_events', failed)
    out = execute_mechanics(*basic())
    assert_prefix(out)
    assert (0, 'output') not in contexts[-1].snapshots
    if mode == 'verification':
        assert frames(out)['verification_bytes'] > runtime.FRAME_RUNTIME_LIMITS['verification_bytes']


def capture_case():
    args = basic()
    args[1]['actions'][0]['occupation_end'] = .15
    args[2]['condition_events'] = []
    args[2]['snapshot_captures'] = [capture('output', offset=.15)]
    return args


def test_action_owned_capture_uses_same_committed_frame_and_only_one_snapshot_storage():
    out = execute_mechanics(*capture_case())
    assert out['result'] is not None, out['issues']
    assert damages(out) == pytest.approx([81, 81, 81])
    assert len(frames(out)['capture_trace']) == 1
    assert len([r for r in out['snapshot_capture_trace'] if r['type'] == 'snapshot_capture']) == 1
    assert frames(out)['usage']['capture_work'] == 3


def test_cancelled_action_capture_does_not_create_resource_frame():
    args = capture_case()
    args[1]['actions'].append(action('interrupt', start=.15, end=.16, order=-2))
    out = execute_mechanics(*args)
    assert_prefix(out)
    assert any(r.get('status') == 'cancelled' for r in out['snapshot_capture_trace'])


def test_false_condition_still_registers_actual_observation_before_early_return():
    args = basic()
    args[2]['condition_events'][0]['value'] = False
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert damages(out) == pytest.approx([81, 81, 81])


def test_copied_semantic_event_cannot_observe_actual_resource_frame(monkeypatch):
    original = mechanics._Mechanics._observed_snapshot
    def copied(ctx, event):
        return original(ctx, copy.deepcopy(event))
    monkeypatch.setattr(mechanics._Mechanics, '_observed_snapshot', copied)
    out = execute_mechanics(*basic())
    assert_prefix(out)
    assert any(i['code'] == 'counter_frame_capture' for i in out['issues'])


def test_static_damage_filter_cannot_hide_later_explicit_include_capture_demand():
    args = basic()
    rule = args[2]['rules'][0]
    rule['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre', 'damage_type_ids': ['damage.elemental_burst']}
    matches = [{'rule_id': rule['id'], 'effect_id': 'value', 'hit_id': 'h1', 'operation': 'include'}]
    out = execute_mechanics(*args, matches=matches)
    assert out['result'] is not None, out['issues']
    assert damages(out) == pytest.approx([45, 81, 45])
    assert len(rows(out)) == 1 and rows(out)[0]['reading']['raw_atoms'] == 8


def test_classic_melt_uses_historical_bonus_without_changing_amplify_formula():
    args = basic(times=(.25,))
    args[0]['hits'][0]['reaction'] = {'type': 'amplify', 'factor': 2}
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert damages(out) == pytest.approx([162])


def test_irrelevant_named_capture_does_not_scan_counter_views_or_charge_frame(monkeypatch):
    args = basic(captured=False)
    # A declared but unreferenced condition is not a counter output observation.
    original = runtime.CounterFrameRuntime._prepare
    def reject(*_):
        raise AssertionError('irrelevant capture must not enter _prepare')
    monkeypatch.setattr(runtime.CounterFrameRuntime, '_prepare', reject)
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert frames(out)['usage']['frames'] == 0
    monkeypatch.setattr(runtime.CounterFrameRuntime, '_prepare', original)


def test_v1_cannot_opt_into_self_frame_merely_by_changing_expression():
    args = basic()
    args[2]['resource_counter_views']['schema_version'] = 1
    out = execute_mechanics(*args)
    assert out['status'] == 'invalid'
    assert any(i['code'] == 'counter_view_scope' for i in out['issues'])


@pytest.mark.parametrize('failed', [False, True])
def test_actual_atk_em_attribute_frames_coexist_and_survive_later_counter_failure(monkeypatch, failed):
    args = basic()
    args[2]['attribute_model'] = {'contract': 'attribute-ledger-v3', 'initial_basis': [
        {'actor': 'a', 'stat': stat, 'included_components': [], **({'nonextra': {
            'mode': 'panel_total', 'status': 'assumed', 'source': 'engineering initial'}} if stat == 'em' else {})}
        for stat in ('atk', 'em')], 'effect_classifications': []}
    if failed:
        monkeypatch.setitem(runtime.FRAME_RUNTIME_LIMITS, 'snapshot_bytes', 1)
    out = execute_mechanics(*args)
    if failed:
        assert_prefix(out)
        assert 'attribute_ledger' in out
        assert out['attribute_ledger']['usage']['capture_work'] > 0
    else:
        assert out['result'] is not None, out['issues']
        assert damages(out) == pytest.approx([81, 81, 81])


def test_later_historical_read_failure_retains_already_completed_read_and_gate_prefix(monkeypatch):
    original = mechanics._Mechanics._reserve_events
    def fail_second(ctx, count, kind=None):
        if kind == 'resource_counter_view' and ctx.time == 250000:
            raise SharedPoolError('counter_view_limit', 'engineering read failure', severity='pending')
        return original(ctx, count, kind)
    monkeypatch.setattr(mechanics._Mechanics, '_reserve_events', fail_second)
    out = execute_mechanics(*basic())
    assert out['status'] == 'pending' and out['result'] is None
    assert [r['reading']['raw_atoms'] for r in rows(out)] == [8]
    assert out['resource_counters']['applied_state']['values']['count']['total'] == 16


def test_second_view_failure_does_not_publish_first_member_of_same_output_capture(monkeypatch):
    args = basic()
    other = copy.deepcopy(args[2]['resource_counter_views']['views'][0])
    other['id'] = 'second'
    args[2]['resource_counter_views']['views'].append(other)
    effect = copy.deepcopy(args[2]['rules'][0]['effects'][0])
    effect['id'] = 'zz_second'
    effect['value']['view_id'] = 'second'
    args[2]['rules'][0]['effects'].append(effect)
    from genshin_engine.resource_counter_views import CounterViewProgram
    original = CounterViewProgram.read
    requests = []
    def fail(program, view_id, state, *, recipient=None):
        requests.append(view_id)
        if view_id == 'second':
            raise SharedPoolError('counter_view_value', 'engineering second view failure', severity='pending')
        return original(program, view_id, state, recipient=recipient)
    monkeypatch.setattr(CounterViewProgram, 'read', fail)
    out = execute_mechanics(*args)
    assert_prefix(out)
    assert requests == ['read', 'second']


def test_action_capture_storage_failure_does_not_publish_either_receipt(monkeypatch):
    monkeypatch.setitem(runtime.FRAME_RUNTIME_LIMITS, 'snapshot_bytes', 1)
    out = execute_mechanics(*capture_case())
    assert_prefix(out)
    assert out['snapshot_capture_trace'] == []
