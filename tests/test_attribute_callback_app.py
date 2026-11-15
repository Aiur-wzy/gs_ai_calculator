"""Actual App composition keeps causal assumptions bound to reviewed sources."""
from __future__ import annotations

import copy
import json

import pytest

from genshin_app.equipment import compose_equipment_plan, new_plan, run_plan_calc
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from tests.test_attribute_callback_runtime import callback_case
from tests.test_plan_mechanics import native, review


def callback_plan(order='write_before_read'):
    template, rotation, model = callback_case(order)
    for actor in template['characters']:
        actor['stats']['energy_recharge'] = 1
    plan = enable_mechanics(new_plan(template))
    plan['rotation_model'], plan['mechanics_model'] = rotation, model
    skeleton = native()['effect_bindings'][0]
    for rule in model['rules']:
        bid = 'bind_' + rule['id']
        rule['source_id'] = bid + ':part'
        binding = copy.deepcopy(skeleton)
        binding.update(binding_id=bid, provider_character_id=rule['owner'])
        part = binding['parts'][0]
        part['rule']['rule_id'] = rule['id']
        part['dependencies'] = {'mode': 'manual_context', 'context_fingerprint': None,
                                'note': 'Synthetic explicit callback order; not a certified game-source fact.'}
        plan['effect_bindings'].append(binding)
    for row in model['attribute_model']['effect_classifications']:
        row['component']['part_id'] = 'bind_' + row['effect_ref']['rule_id'] + ':part'
    return plan


def authorize_callbacks(plan, db):
    plan = copy.deepcopy(plan)
    review(plan, db)
    plan = prepare_attribute_review(plan, 'Synthetic EM basis and classifications explicitly reviewed.', db)['plan']
    current = compose_equipment_plan(plan, db)['context_fingerprint']
    for binding in plan['effect_bindings']:
        for part in binding['parts']:
            part['dependencies']['context_fingerprint'] = current
    return plan


def swirl_samples(calculated):
    return [hit['extensions']['elemental']['provider_stat_sample']['value']
            for hit in calculated['plan']['resolved_template']['hits']
            if hit['damage_system'] == 'transformative']


@pytest.mark.parametrize('order,expected', [('write_before_read', 240), ('read_before_write', 216)])
def test_actual_app_executes_reviewed_callback_order_and_reopens(order, expected, tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize_callbacks(callback_plan(order), db)
    original = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert out['report']['attribute_callback_source_adoption']['status'] == 'ready'
    callback = out['result']['attribute_callbacks']
    assert callback['source_adoption'] == out['report']['attribute_callback_source_adoption']
    assert any(row['type'] == 'callback_committed' for row in callback['trace'])
    assert swirl_samples(out) == [80, expected]
    assert run_plan_calc(json.loads(json.dumps(plan)), db)['result'] == out['result']
    assert plan == original


@pytest.mark.parametrize('excluded,expected', [(['ordinary'], 96), (['share'], 200), (['share', 'ordinary'], 80)])
def test_app_explicit_exclusion_closes_callbacks_without_changing_native(excluded, expected, tmp_path):
    db = tmp_path / 'absent.db'
    plan = callback_plan()
    for binding in plan['effect_bindings']:
        part = binding['parts'][0]
        if part['rule']['rule_id'] in excluded:
            part.update(state='excluded', reason='Explicitly absent in this synthetic scenario.')
    plan = authorize_callbacks(plan, db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    adoption = out['report']['attribute_callback_source_adoption']
    assert adoption['excluded_rule_ids'] == sorted(excluded)
    assert swirl_samples(out) == [80, expected]
    if len(excluded) == 2:
        assert adoption['status'] == 'excluded'
        assert 'attribute_callbacks' not in out['report']['mechanics_model']
    else:
        assert out['report']['mechanics_model']['attribute_callbacks']['edges'] == []
    assert plan == before


def test_source_only_and_exclusion_cannot_hide_unreviewed_order(tmp_path):
    db = tmp_path / 'absent.db'
    plan = callback_plan()
    plan['effect_bindings'][0]['parts'][0].update(state='excluded', reason='Excluded reader')
    part = plan['effect_bindings'][1]['parts'][0]
    part['dependencies'] = {'mode': 'source_only', 'context_fingerprint': None, 'note': 'Identity only'}
    plan = authorize_callbacks(plan, db)
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert report['attribute_callback_source_adoption']['status'] == 'pending'
    assert any(i['code'] == 'attribute_callback_source_scope_unreviewed' for i in report['missing_inputs'])
    with pytest.raises(PlanError) as exc:
        run_plan_calc(plan, db)
    assert exc.value.details['attribute_callback_source_adoption']['status'] == 'pending'


def test_order_change_invalidates_previous_scope_before_new_calculation(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize_callbacks(callback_plan(), db)
    run_plan_calc(plan, db)
    edge = plan['mechanics_model']['attribute_callbacks']['edges'][0]
    edge['before_rule_id'], edge['after_rule_id'] = edge['after_rule_id'], edge['before_rule_id']
    assert compose_equipment_plan(plan, db)['state'] == 'pending'
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)
    updated = run_plan_calc(authorize_callbacks(plan, db), db)
    assert swirl_samples(updated) == [80, 216]
