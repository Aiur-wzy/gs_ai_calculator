"""Complete native App plans; source review is separate from runtime capture."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from tests.test_mechanics_execution import condition
from tests.test_resource_counter_view_sources import view_plan
from tests.test_shared_pool_app_delivery import authorize


def self_plan(*, captured=True):
    plan = view_plan()
    model = plan['mechanics_model']
    model['resource_counter_views']['schema_version'] = 2
    effect = model['rules'][1]['effects'][0]
    effect.update(domain='self')
    effect['value']['sample'] = 'self_frame'
    if captured:
        condition(model, 'counter-output', .14, 5)
        for descriptor in model['hit_events']:
            descriptor['self_snapshot_event'] = 'counter-output'
    return plan


def test_source_reviewed_historical_plan_really_computes_and_preserves_native(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(self_plan(), db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == before and not db.exists()
    assert [h['expected'] for h in out['result']['hits']] == [81, 81, 81]
    assert out['report']['resource_counter_view_adoptions'][0]['status'] == 'ready'
    ledger = out['result']['resource_counter_views']
    assert ledger['source_adoptions'] == out['report']['resource_counter_view_adoptions']
    assert [r['reading']['raw_atoms'] for r in ledger['trace']] == [8, 8, 8]
    assert all(r['capture_context']['event_id'] == 'counter-output' for r in ledger['trace'])


def test_source_reviewed_no_reference_stays_current_with_explicit_selection(tmp_path):
    db = tmp_path / 'absent.db'
    out = run_plan_calc(authorize(self_plan(captured=False), db), db)
    assert [h['expected'] for h in out['result']['hits']] == [81, 117, 130.5]
    assert all(r['frame_selection'] == 'current_no_self_reference' for r in out['result']['resource_counter_views']['trace'])
    assert out['result']['resource_counter_views']['historical_capture']['usage']['frames'] == 0


@pytest.mark.parametrize('edit', ['reference', 'time', 'factor', 'scale', 'layers'])
def test_changed_sampling_or_math_requires_new_whole_context_review(tmp_path, edit):
    db = tmp_path / 'absent.db'
    plan = authorize(self_plan(), db)
    model = plan['mechanics_model']
    if edit == 'reference':
        model['hit_events'][0]['self_snapshot_event'] = 'cast'
    elif edit == 'time':
        model['condition_events'][0]['t'] = .13
    elif edit == 'factor':
        model['rules'][1]['effects'][0]['value']['factor'] = 2
    elif edit == 'scale':
        model['shared_pools']['pools'][0]['unit_scale'] = 20
    else:
        model['rules'][1]['stacking'].update(max=2, add=2)
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert not any(r['id'] == 'view' for r in report['mechanics_model']['rules'])
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


def test_source_only_cannot_approve_self_frame_domain(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(self_plan(), db)
    plan['effect_bindings'][0]['parts'][1]['dependencies']['mode'] = 'source_only'
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert 'resource_counter_views' not in report['mechanics_model']
    assert any(i['code'] == 'counter_view_source_scope_unreviewed' for i in report['missing_inputs'])


def test_excluding_view_preserves_independent_spend_counter_and_has_no_frame_service(tmp_path):
    db = tmp_path / 'absent.db'
    plan = self_plan()
    plan['effect_bindings'][0]['parts'][1].update(state='excluded', reason='engineering omitted view')
    out = run_plan_calc(authorize(plan, db), db)
    assert [h['expected'] for h in out['result']['hits']] == [45, 45, 45]
    assert 'resource_counter_views' not in out['result']
    assert out['result']['resource_counters']['state']['values'] == {'count': {'total': 19}}
    assert [r['accepted'] for r in out['result']['shared_pools']['trace']] == [8, 8, 3]


@pytest.mark.parametrize('edit', ['owner', 'counter', 'sample', 'zone'])
def test_exclusion_cannot_hide_invalid_original_self_reader(tmp_path, edit):
    plan = self_plan()
    model = plan['mechanics_model']
    plan['effect_bindings'][0]['parts'][1].update(state='excluded', reason='does not authorize malformed graph')
    if edit == 'owner':
        model['resource_counter_views']['views'][0]['owner'] = 'b'
    elif edit == 'counter':
        model['resource_counter_views']['views'][0]['counter_id'] = 'missing'
    elif edit == 'sample':
        model['rules'][1]['effects'][0]['value']['sample'] = 'current'
    else:
        model['rules'][1]['effects'][0]['benefit_type_id'] = 'benefit.em.all'
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, tmp_path / 'absent.db')


def test_unexecuted_capture_cannot_create_fresh_App_result(tmp_path):
    db = tmp_path / 'absent.db'
    plan = self_plan()
    plan['mechanics_model']['condition_events'][0]['t'] = .4
    plan = authorize(plan, db)
    with pytest.raises(PlanError) as caught:
        run_plan_calc(plan, db)
    assert caught.value.reason == 'mechanics_pending'
    execution = caught.value.details
    assert execution['result'] is None
    assert execution['resource_counters']['applied_state']['values']['count']['total'] == 8


def test_shared_view_lists_both_actual_sampling_channels_without_merging_values(tmp_path):
    db = tmp_path / 'absent.db'
    plan = self_plan()
    other = copy.deepcopy(plan['mechanics_model']['rules'][1]['effects'][0])
    other.update(id='live', domain='provider')
    other['value']['sample'] = 'current'
    plan['mechanics_model']['rules'][1]['effects'].append(other)
    out = run_plan_calc(authorize(plan, db), db)
    assert [h['expected'] for h in out['result']['hits']] == pytest.approx([117, 153, 166.5])
    adoption = out['report']['resource_counter_view_adoptions'][0]
    assert adoption['sampling_contract'] == 'counter-self-frame-v2'
    assert adoption['readers'] == [
        {'rule_id': 'view', 'effect_id': 'live', 'domain': 'provider', 'sample': 'current'},
        {'rule_id': 'view', 'effect_id': 'value', 'domain': 'self', 'sample': 'self_frame'}]
    records = out['result']['resource_counter_views']['trace']
    assert [r['reading']['raw_atoms'] for r in records if r['sample'] == 'self_frame'] == [8, 8, 8]
    assert [r['reading']['raw_atoms'] for r in records if r['sample'] == 'current'] == [8, 16, 19]


def test_pattern_expansion_registers_each_actual_self_frame_consumer(tmp_path):
    plan = self_plan()
    plan['manual_template']['hits'] = [plan['manual_template']['hits'][0]]
    model = plan['mechanics_model']
    model['hit_events'] = []
    model['hit_patterns'] = [{'id': 'pattern', 'prototype_hit_id': 'h0', 'action_id': 'cast',
                             'offsets': [.15, .25, .35], 'orders': [10, 11, 12],
                             'self_snapshot_event': 'counter-output'}]
    db = tmp_path / 'absent.db'
    out = run_plan_calc(authorize(plan, db), db)
    assert [h['expected'] for h in out['result']['hits']] == [81, 81, 81]
    assert [r['event_id'] for r in out['result']['resource_counter_views']['trace']] == ['pattern:1', 'pattern:2', 'pattern:3']


def test_v1_adoption_has_no_new_sampling_shape(tmp_path):
    db = tmp_path / 'absent.db'
    out = run_plan_calc(authorize(view_plan(), db), db)
    adoption = out['report']['resource_counter_view_adoptions'][0]
    assert 'sampling_contract' not in adoption and 'readers' not in adoption
