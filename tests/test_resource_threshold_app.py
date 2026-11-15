"""Threshold source adoption follows the complete original dependency graph."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from tests.test_mechanics_execution import add, rule
from tests.test_plan_mechanics import native
from tests.test_resource_threshold_execution import case
from tests.test_shared_pool_app_delivery import authorize


def threshold_plan(phase='pre'):
    plan = native()
    template, rotation, model = case(phase)
    template['characters'][0]['stats']['energy_recharge'] = 1
    plan.update(manual_template=template, rotation_model=rotation, mechanics_model=model)
    model['rules'][0]['source_id'] = 'binding:part'
    model['resource_threshold_triggers']['bindings'][0]['source_id'] = 'binding:part'
    model['resource_threshold_observers']['observers'][0]['source_id'] = 'binding:observer'
    for pool in model['shared_pools']['pools']:
        pool['source_id'] = 'binding:pool'
    for op in model['shared_pools']['operations']:
        op['source_id'] = 'binding:pool'
    parts = plan['effect_bindings'][0]['parts']
    for name in ('pool', 'observer'):
        authority = rule(name, source_id='binding:' + name)
        authority['effects'] = []
        add(model, authority)
        part = copy.deepcopy(parts[0])
        part.update(part_id=name, label='工程来源 ' + name)
        part['rule']['rule_id'] = name
        parts.append(part)
    return plan


@pytest.mark.parametrize('phase,expected', [('pre', [90, 90]), ('post', [45, 90])])
def test_reviewed_app_threshold_sources_have_actual_effect_and_separate_adoptions(tmp_path, phase, expected):
    db = tmp_path / 'absent.db'
    plan = authorize(threshold_plan(phase), db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert [h['expected'] for h in out['result']['hits']] == expected
    for suffix in ('observer', 'trigger'):
        records = out['report'][f'resource_threshold_{suffix}_adoptions']
        assert records and all(r['status'] == 'ready' for r in records)
        assert out['result'][f'resource_threshold_{suffix}s']['source_adoptions'] == records
    assert plan == before


@pytest.mark.parametrize('part_id', ['part', 'observer', 'pool'])
def test_exclusion_closes_only_actual_threshold_dependents(tmp_path, part_id):
    db = tmp_path / 'absent.db'
    plan = threshold_plan()
    part = next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id'] == part_id)
    part.update(state='excluded', reason='Explicitly absent in this synthetic scenario')
    out = run_plan_calc(authorize(plan, db), db)
    assert [h['expected'] for h in out['result']['hits']] == [45, 45]
    assert 'resource_threshold_triggers' not in out['result']
    assert all('excluded' in r['status'] for r in out['report']['resource_threshold_trigger_adoptions'])
    if part_id == 'pool':
        assert 'shared_pools' not in out['report']['mechanics_model']
    else:
        assert out['result']['shared_pools']['trace']
        assert 'shared_pools' in out['report']['mechanics_model']
    if part_id == 'part':
        assert out['result']['resource_threshold_observers']['trace']
    else:
        assert 'resource_threshold_observers' not in out['result']


def test_unreviewed_new_threshold_cannot_use_existing_pool_review(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(threshold_plan(), db)
    plan['effect_bindings'][0]['parts'][2]['dependencies']['mode'] = 'source_only'
    out = compose_equipment_plan(plan, db)
    assert out['state'] == 'pending'
    assert any(r['code'] == 'threshold_source_scope_unreviewed' for r in out['missing_inputs'])
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


def test_pending_observer_cannot_be_hidden_by_excluded_target(tmp_path):
    plan = threshold_plan()
    plan['mechanics_model']['resource_threshold_observers']['observers'][0]['status'] = 'pending'
    plan['effect_bindings'][0]['parts'][0].update(state='excluded', reason='Target explicitly absent')
    db = tmp_path / 'absent.db'
    report = compose_equipment_plan(authorize(plan, db), db)
    assert report['state'] == 'pending'
    row = report['resource_threshold_trigger_adoptions'][0]
    assert row['own_source_status'] == 'excluded' and row['effective_status'] == 'dependency_pending'


@pytest.mark.parametrize('field,value', [('owner', 'other'), ('observer_id', 'missing')])
def test_excluded_target_cannot_hide_original_threshold_reference_error(tmp_path, field, value):
    plan = threshold_plan()
    plan['mechanics_model']['resource_threshold_triggers']['bindings'][0][field] = value
    for part in plan['effect_bindings'][0]['parts']:
        part.update(state='excluded', reason='Not present')
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, tmp_path / 'absent.db')


def test_threshold_edit_requires_new_context_and_then_recalculates(tmp_path):
    db = tmp_path / 'absent.db'
    plan = authorize(threshold_plan(), db)
    before = run_plan_calc(plan, db)
    plan['mechanics_model']['resource_threshold_observers']['observers'][0]['threshold_atoms'] = 51
    assert compose_equipment_plan(plan, db)['state'] == 'pending'
    after = run_plan_calc(authorize(plan, db), db)
    assert [h['expected'] for h in before['result']['hits']] == [90, 90]
    assert [h['expected'] for h in after['result']['hits']] == [45, 45]
