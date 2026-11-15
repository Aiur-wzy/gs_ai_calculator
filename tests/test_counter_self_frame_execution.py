"""Actual-engine acceptance red cases, before any private/public v2 integration."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics, validate_mechanics_model
from tests.test_mechanics_execution import action, condition, damages
from tests.test_resource_counter_view_execution import view_case
from tests.test_shared_pools import config


def basic(times=(.16, .25, .35), *, captured=True):
    args = view_case(times=times)
    model = args[2]
    model['resource_counter_views']['schema_version'] = 2
    effect = model['rules'][0]['effects'][0]
    effect['domain'] = 'self'
    effect['value']['sample'] = 'self_frame'
    if captured:
        condition(model, 'output', .15, 5)
        for descriptor in model['hit_events']:
            descriptor['self_snapshot_event'] = 'output'
    return args


def legacy(args):
    old = copy.deepcopy(args)
    old[2]['resource_counter_views']['schema_version'] = 1
    for row in old[2]['rules']:
        for effect in row['effects']:
            if isinstance(effect['value'], dict) and effect['value'].get('kind') == 'counter_view':
                effect['domain'] = 'provider'
                effect['value']['sample'] = 'current'
    validation = validate_mechanics_model(old[2])
    assert validation['status'] == 'valid', validation
    return execute_mechanics(*old)


def rows(out):
    return [r for r in out['resource_counter_views']['trace'] if r['cycle'] == 0]


def completed(args, expected):
    before = copy.deepcopy(args)
    out = execute_mechanics(*args)
    assert args == before
    assert out['result'] is not None, out['issues']
    assert damages(out) == pytest.approx(expected)
    return out


def test_legacy_control_really_executes_and_stays_current_despite_self_reference():
    out = legacy(basic())
    assert out['result'] is not None, out['issues']
    assert damages(out) == pytest.approx([81, 117, 130.5])
    assert [r['reading']['raw_atoms'] for r in rows(out)] == [8, 16, 19]


def test_no_reference_is_explicit_current_selection_not_missing_capture():
    args = basic(captured=False)
    assert legacy(args)['result'] is not None
    out = completed(args, [81, 117, 130.5])
    assert [r['reading']['raw_atoms'] for r in rows(out)] == [8, 16, 19]
    assert all(r['sample'] == 'self_frame' and r['frame_selection'] == 'current_no_self_reference' for r in rows(out))


def test_reference_keeps_executed_eight_atom_frame():
    args = basic()
    assert legacy(args)['result'] is not None
    out = completed(args, [81, 81, 81])
    assert [r['reading']['raw_atoms'] for r in rows(out)] == [8, 8, 8]
    assert all(r['frame_selection'] == 'historical_reference' for r in rows(out))


def test_declared_reference_not_yet_executed_never_falls_back():
    args = basic(times=(.16,))
    args[2]['condition_events'][0]['t'] = .4
    old = copy.deepcopy(args)
    old[2]['resource_counter_views']['schema_version'] = 1
    old[2]['rules'][0]['effects'][0]['domain'] = 'provider'
    old[2]['rules'][0]['effects'][0]['value']['sample'] = 'current'
    assert validate_mechanics_model(old[2])['status'] == 'valid'
    out = execute_mechanics(*args)
    assert out['status'] == 'pending' and out['result'] is None and out['resolved_template'] is None
    assert out['resource_counters']['applied_state']['values']['count']['total'] == 8
    assert not rows(out)


def test_same_parent_pre_observation_post_does_not_retroactively_capture_post():
    args = basic(times=(.2, .3), captured=False)
    model = args[2]
    model['state_runtime']['timers'] = []
    op = model['shared_pools']['operations'][0]
    op['trigger'] = {'event': 'hit', 'phase': 'pre', 'event_ids': ['h0']}
    later = copy.deepcopy(op)
    later.update(id='later', order=2)
    later['trigger']['phase'] = 'post'
    model['shared_pools']['operations'].append(later)
    model['resource_counters']['counters'][0]['operation_ids'].append('later')
    model['hit_events'][1]['self_snapshot_event'] = 'h0'
    assert damages(legacy(args)) == pytest.approx([81, 117])
    out = completed(args, [81, 81])
    assert [r['reading']['raw_atoms'] for r in rows(out)] == [8, 8]
    assert out['resource_counters']['state']['values']['count']['total'] == 16


def layer_case(*, grow):
    args = basic(times=(.25,))
    model = args[2]
    model['state_runtime']['timers'][0]['max_ticks'] = 1
    rule = model['rules'][0]
    rule['stacking'].update(max=2, policy='independent')
    initial = model['initial_state']['rules'][0]
    initial.update(cooldown_remaining=0 if grow else 100, instances=[{
        'beneficiaries': ['a'], 'remaining': [1] if grow else [.18, 1], 'quota': {}}])
    if grow:
        condition(model, 'grow', .2, 30)
        rule['trigger'] = {'event': 'condition', 'phase': 'pre', 'event_ids': ['grow']}
    return args


@pytest.mark.parametrize('grow,old_expected,new_expected', [(False, 81, 117), (True, 117, 81)])
def test_history_uses_capture_layers_not_changed_current_count(grow, old_expected, new_expected):
    args = layer_case(grow=grow)
    assert damages(legacy(args)) == pytest.approx([old_expected])
    out = completed(args, [new_expected])
    assert rows(out)[0]['layer_count'] == (1 if grow else 2)


def test_expired_capture_does_not_borrow_new_instance_gate_even_with_include():
    args = basic(times=(.16, .35))
    model = args[2]
    model['state_runtime']['timers'][0]['max_ticks'] = 1
    rule = model['rules'][0]
    rule['timing']['duration'] = .2
    rule['trigger']['action_ids'].append('again')
    args[1]['actions'].append(action('again', start=.25, end=.3, order=5))
    consumer = config()['consumers'][0]
    consumer.update(eligibility_rule_id=rule['id'], source_id=rule['source_id'], actor_relation='party_member', amount=1)
    model['shared_pools']['consumers'] = [consumer]
    rule['resource_gate'] = {'consumer_id': consumer['id']}
    assert legacy(args)['result'] is not None
    out = execute_mechanics(*args, matches=[{'rule_id': rule['id'], 'effect_id': 'value', 'hit_id': 'h1', 'operation': 'include'}])
    assert out['result'] is not None, out['issues']
    assert damages(out) == pytest.approx([81, 45])
    consumed = [r for r in out['shared_pool_trace'] if r['type'] == 'consume' and r['cycle'] == 0]
    assert [r['accepted'] for r in consumed] == [1, 1]
    assert len(rows(out)) == 1
