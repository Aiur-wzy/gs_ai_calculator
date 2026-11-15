"""Independent actual captures and retained resource prefix, private overlay only."""

import pytest

from genshin_engine.mechanics import _Mechanics, execute_mechanics
from genshin_engine.snapshot_capture import SnapshotCapturePending
from tests.test_counter_self_frame_actual_review import (
    actual,
    arguments,
    reads,
    replacement,
)
from tests.test_mechanics_execution import EVIDENCE


def owned_capture():
    args = arguments(historical=False)
    args[1]['actions'][0]['occupation_end'] = .4
    args[2]['snapshot_captures'] = [{
        'id': 'independent-action-output', 'action_id': 'cast', 'offset': .15,
        'precision': .000001, **EVIDENCE}]
    for hit in args[2]['hit_events']:
        hit['self_snapshot_event'] = 'independent-action-output'
    return args


def retained_six_atoms(output):
    assert output['status'] == 'pending', output['issues']
    assert output['result'] is None and output['resolved_template'] is None
    counter = output['resource_counters']['applied_state']
    assert counter['values']['count']['total'] == 6
    rows = [r for r in output['shared_pool_trace'] if r['type'] == 'operation']
    assert len(rows) == 1 and rows[0]['accepted'] == 6
    history = output['resource_counter_views']['historical_capture']
    assert history['usage']['frames'] == 0
    assert history['usage']['members'] == 0
    assert not history['capture_trace']
    assert not reads(output)


def test_action_owned_observation_is_actual_parent_capture_and_frozen_six():
    output = actual(owned_capture(), [50.4, 50.4, 50.4])
    assert [r['reading']['raw_atoms'] for r in reads(output)] == [6, 6, 6]
    rows = [r for r in output['snapshot_capture_trace'] if r['type'] == 'snapshot_capture']
    assert len(rows) == 1
    assert rows[0]['parent_run_id'] == '0:cast'
    assert rows[0]['status'] == 'captured'
    assert output['resource_counters']['state']['values']['count']['total'] == 18


@pytest.mark.parametrize('owned', [False, True])
def test_full_snapshot_storage_failure_keeps_real_spend_without_half_output(monkeypatch, owned):
    from genshin_engine.resource_counter_frame_runtime import FRAME_RUNTIME_LIMITS

    monkeypatch.setitem(FRAME_RUNTIME_LIMITS, 'snapshot_bytes', 2)
    output = execute_mechanics(*(owned_capture() if owned else arguments()))
    retained_six_atoms(output)
    assert any(i['code'] == 'counter_frame_snapshot_limit' for i in output['issues'])
    if owned:
        assert not any(r['status'] == 'captured' for r in output.get('snapshot_capture_trace', []))


def test_public_reserve_failure_keeps_actual_counter_but_no_frame_identity(monkeypatch):
    original = _Mechanics._reserve_events

    def reserve(ctx, count, kind=None):
        if kind == 'resource_counter_frame':
            raise SnapshotCapturePending('independent_capture_work_limit', '独立预算注入',
                                         'mechanics_model.resource_counter_views')
        return original(ctx, count, kind)

    monkeypatch.setattr(_Mechanics, '_reserve_events', reserve)
    output = execute_mechanics(*arguments())
    retained_six_atoms(output)
    assert any(i['code'] == 'independent_capture_work_limit' for i in output['issues'])


def delayed(args):
    args[0]['hits'] = args[0]['hits'][:1]
    args[0]['hits'][0]['t'] = .45
    args[2]['hit_events'] = args[2]['hit_events'][:1]
    args[0]['loop_duration'] = args[1]['period'] = .4
    args[1]['max_cycles'] = 3
    return args


def boundary_signature(output):
    assert output['result'] is not None, output['issues']
    rows = output['rotation_check']['cycles'][0]['boundary_state']['mechanics']['pending_samples']
    return next(r['counter_self_frame'] for r in rows
                if r['reference_id'] == 'independent-output')


def test_boundary_absolute_instance_rename_keeps_relative_history_equal(monkeypatch):
    args = delayed(arguments())
    normal = boundary_signature(execute_mechanics(*args))
    original = _Mechanics._observed_snapshot

    def renamed(ctx, event):
        if event['id'] == 'independent-output':
            for ordinal, inst in enumerate(ctx.state['independent']['instances']):
                inst['instance_id'] = f'opaque-independent-{ordinal}'
        return original(ctx, event)

    monkeypatch.setattr(_Mechanics, '_observed_snapshot', renamed)
    renamed_signature = boundary_signature(execute_mechanics(*args))
    assert renamed_signature == normal
    assert normal[0]['raw_atoms'] == 6
    assert 'opaque-independent' not in repr(normal)


def test_boundary_different_historical_math_is_not_equal():
    args = delayed(arguments())
    original = boundary_signature(execute_mechanics(*args))
    args[2]['shared_pools']['operations'][0]['amount'] = 7
    changed = boundary_signature(execute_mechanics(*args))
    assert original[0]['raw_atoms'] == 6 and changed[0]['raw_atoms'] == 7
    assert changed != original


def test_boundary_current_gate_relation_is_not_replaced_by_frozen_number():
    same = delayed(replacement(gated=True))
    same[1]['actions'] = same[1]['actions'][:1]
    same[2]['rules'][0]['timing']['duration'] = 1
    same[2]['rules'][0]['trigger']['action_ids'] = ['cast']
    replaced = delayed(replacement(gated=True))
    old_signature = boundary_signature(execute_mechanics(*same))
    new_signature = boundary_signature(execute_mechanics(*replaced))
    assert old_signature[0]['raw_atoms'] == new_signature[0]['raw_atoms'] == 6
    assert old_signature[0]['current_gate_instance_ordinal'] == 0
    assert new_signature[0]['current_gate_instance_ordinal'] is None
    assert old_signature != new_signature
