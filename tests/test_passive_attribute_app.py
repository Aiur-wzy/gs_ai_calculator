"""Actual App database source, dynamic basis, receipts and legacy isolation."""
import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.schema import PlanError
from tests.test_elemental_swirl import swirl_model
from tests.test_mechanics_execution import ability, action, add, rule
from tests.test_passive_attribute_source import scenario
from tests.test_passive_sources_app import (  # noqa: F401
    accept,
    passive_attack_db,
    passive_plan,
)
from tests.test_plan_mechanics import native
from tests.test_shared_pool_app_delivery import authorize


@pytest.fixture
def dynamic_plan(request):
    plan,db=request.getfixturevalue('passive_plan')
    model=plan['mechanics_model']
    model.pop('contact',None)
    for field in ('hit_events','hit_patterns'):
        for row in model.get(field,[]):row.pop('contact',None)
    for profile in plan['action_event_profiles']:
        profile['kind']='calibrated_single_application_v2'
        profile.pop('contact_profile_id',None)
    elemental=swirl_model('cryo',2)
    elemental['ruleset']='gauge-swirl-dynamic-v3'
    elemental['profiles']=model['elemental_model']['profiles']
    model['elemental_model']=elemental
    plan['passive_sources'][0]['scenario']=scenario()
    plan['rotation_model']['abilities'].append(ability('switch.b','a','action.switch'))
    plan['rotation_model']['actions'].append(action('go.b','switch.b',.6,.7,switch_to='b',order=5,repeat={'mode':'once'}))
    plan['manual_template']['characters'][0]['stats']['em']=200
    model['attribute_model']={'contract':'attribute-ledger-v2','effect_classifications':[],
        'initial_basis':[{'actor':c['id'],'included_components':[],
            'nonextra':{'mode':'explicit','value':100 if c['id']=='a' else 0,'status':'assumed','source':'明确工程NONEXTRA'}}
            for c in plan['manual_template']['characters']]}
    for index,(amount,inclusion) in enumerate(((50,'included'),(100,'excluded'))):
        rid,bid=f'gain{index}',f'gain-source{index}'
        add(model,rule(rid=rid,benefit='benefit.em.all',value=amount,source_id=bid+':part'))
        binding=copy.deepcopy(native()['effect_bindings'][0]);binding['binding_id']=bid
        binding['parts'][0]['rule']['rule_id']=rid;plan['effect_bindings'].append(binding)
        model['attribute_model']['effect_classifications'].append({
            'effect_ref':{'kind':'rule','rule_id':rid,'effect_id':'value'},
            'component':{'provider':'a','part_id':bid+':part','effect_id':'value','source_fingerprint':'stale'},
            'nonextra_inclusion':inclusion,'status':'assumed','source':'工程明确分类'})
    plan['attribute_review']={'mode':'manual_context','context_fingerprint':None,'note':'复核精通来源与NONEXTRA'}
    return plan,db


def approve(plan,db):
    authorize(plan,db)
    accept(plan,db)
    result=prepare_attribute_review(plan,'复核精通来源与NONEXTRA',db)
    candidate=result['plan']
    authorize(candidate,db)
    return candidate


def test_db_dynamic_nonextra_uses_real_included_components_not_panel_total(dynamic_plan):
    plan,db=dynamic_plan;plan=approve(plan,db);before=copy.deepcopy(plan)
    out=run_plan_calc(plan,db)
    records=out['result']['passive_compilations']
    assert not [i for i in out['result']['rotation_check']['issues'] if i.get('severity')=='error']
    assert records[0]['compiler']=='database-inherent-swirl-attribute-v2'
    assert records[0]['attribute_evidence']['writers']==[]
    samples=[r for r in out['result']['sampling_trace'] if r.get('rule_id') in records[0]['rule_ids']]
    assert samples and all(r['value']==pytest.approx(150*.00039999998989515007) for r in samples)
    assert out['result_receipt']['passives']['compiler']=='passive-source-collection-v2'
    assert plan==before


def test_dynamic_action_event_omitted_progression_constellation_is_not_a_conflict(dynamic_plan):
    plan,db=dynamic_plan
    assert 'constellation' not in plan['equipment_inputs']['a']['progression']
    report=compose_equipment_plan(plan,db)
    assert not any(i['code']=='action_event_progression_mismatch' for i in report['missing_inputs'])


def test_dynamic_action_event_explicit_progression_constellation_mismatch_is_pending(dynamic_plan):
    plan,db=dynamic_plan
    plan['equipment_inputs']['a']['progression']['constellation']=1
    report=compose_equipment_plan(plan,db)
    assert report['state']=='pending'
    assert any(i['code']=='action_event_progression_mismatch' for i in report['missing_inputs'])


def test_dynamic_source_cannot_silently_upgrade_attribute_v1(dynamic_plan):
    plan,db=dynamic_plan;plan['mechanics_model']['attribute_model']['contract']='attribute-ledger-v1'
    report=compose_equipment_plan(plan,db)
    assert report['state']=='pending'
    assert any('attribute-ledger-v2' in i['message'] for i in report['missing_inputs'])


def test_same_proc_ordinary_writer_cannot_be_accepted_away(dynamic_plan):
    plan,db=dynamic_plan
    plan['mechanics_model']['rules'][0]['trigger']={'event':'elemental_reaction','phase':'post'}
    report=compose_equipment_plan(plan,db)
    assert report['state']=='pending'
    assert any(i['code']=='attribute_sampling_causality_pending' for i in report['missing_inputs'])
    assert report['passive_compilations'][0]['required_confirmation'] is None


def test_dynamic_basis_edit_requires_source_and_attribute_reconfirmation(dynamic_plan):
    plan,db=dynamic_plan;plan=approve(plan,db)
    plan['mechanics_model']['attribute_model']['initial_basis'][0]['nonextra']['value']=101
    report=compose_equipment_plan(plan,db)
    assert report['state']=='pending'
    assert any(i['code']=='passive_confirmation_required' for i in report['missing_inputs'])
    with pytest.raises(PlanError):run_plan_calc(plan,db)
