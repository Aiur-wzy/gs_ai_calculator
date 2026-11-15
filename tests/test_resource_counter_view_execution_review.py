"""Independent live reads, layer overflow and budget publication boundaries."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_mechanics_execution import condition, damages
from tests.test_resource_counter_view_execution import reads, view_case


@pytest.mark.parametrize('benefit,factor', [('benefit.base_mult.all', 1e200),
                                         ('benefit.dmg_bonus.all', 1.5e308)])
def test_finite_single_read_but_layer_overflow_is_pending_before_publication(benefit, factor):
    args = view_case(times=(.15,), benefit=benefit, factor=factor)
    args[2]['rules'][0]['stacking'] = {'max': 2, 'add': 2, 'policy': 'refresh'}
    out = execute_mechanics(*args)
    assert out['status'] == 'pending' and out['result'] is None
    assert out['resolved_template'] is None and reads(out) == []
    assert out['resource_counters']['applied_state']['values']['count']['total'] == 8
    assert any(i['code'] == 'counter_view_value' for i in out['issues'])


def test_known_false_effect_filter_does_not_read_or_activate_counter_value():
    args = view_case()
    args[2]['rules'][0]['effects'][0]['filter'] = {'event': 'hit', 'phase': 'pre', 'condition': 'inside'}
    condition(args[2], 'inside', 0, 2)
    args[2]['condition_events'][-1]['value'] = False
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    assert damages(out) == [45, 45, 45] and reads(out) == []
    assert out['resource_counters']['applied_state']['values']['count']['total'] == 19


def test_source_other_counter_lifetime_does_not_replace_current_rule_instance_identity():
    args = view_case(times=(.15, .25))
    args[2]['state_runtime']['lifecycles'][0]['duration_us'] = 200_000
    args[2]['resource_counters']['counters'][0]['reset_on'] = 'never'
    out = execute_mechanics(*args)
    assert out['result'] is not None, out['issues']
    samples = reads(out)
    assert [r['reading']['raw_atoms'] for r in samples] == [8, 8]
    assert samples[-1]['reading']['lifecycle']['phase'] == 'ended'
    assert samples[-1]['instance_id'] == samples[0]['instance_id']
    assert samples[-1]['reading']['qualification'] == 'current_rule_instance_accepted'
    assert damages(out) == [81, 81]


def test_no_copy_or_serialization_is_deferred_until_after_view_budget_reserve(monkeypatch):
    from genshin_engine import resource_counter_view_runtime as runtime
    from genshin_engine.mechanics import _Mechanics
    reading, reserve = runtime.CounterViewRuntime.read, _Mechanics._reserve_events
    canonical, deepcopy = runtime._canonical, copy.deepcopy
    committed = False

    def invoke(*args, **kwargs):
        nonlocal committed
        try:
            return reading(*args, **kwargs)
        finally:
            committed = False

    def guard_copy(*args, **kwargs):
        assert not committed, 'A view copied output after budget reserve'
        return deepcopy(*args, **kwargs)

    def guard_json(*args, **kwargs):
        assert not committed, 'A view serialized output after budget reserve'
        return canonical(*args, **kwargs)

    def commit(ctx, count, kind=None):
        nonlocal committed
        result = reserve(ctx, count, kind)
        if kind == 'resource_counter_view':
            committed = True
        return result

    monkeypatch.setattr(runtime.CounterViewRuntime, 'read', invoke)
    monkeypatch.setattr(_Mechanics, '_reserve_events', commit)
    monkeypatch.setattr(copy, 'deepcopy', guard_copy)
    monkeypatch.setattr(runtime, '_canonical', guard_json)
    out = execute_mechanics(*view_case())
    assert out['result'] is not None, out['issues']
    assert damages(out) == [81, 117, 130.5]
