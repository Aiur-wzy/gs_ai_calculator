"""Real composer and queue through current manual_context source graph."""
import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_engine.resource_reaction_program import Program
from tests.test_mechanics_execution import add, rule
from tests.test_plan_mechanics import native
from tests.test_reaction_operation_capabilities import setup
from tests.test_reaction_resource_queue import enable
from tests.test_shared_pool_app_delivery import authorize


def reaction_plan():
    plan=native(); template,rotation,model=enable(setup())
    template['characters'][0]['stats']['energy_recharge']=1
    add(model,rule('pool-authority',value=0,source_id='binding:pool'),
        rule('grant-authority',value=0,source_id='binding:refill'))
    original=plan['effect_bindings'][0]['parts'][0]
    plan.update(manual_template=template,rotation_model=rotation,mechanics_model=model)
    parts=[]
    for row in model['rules']:
        part=copy.deepcopy(original)
        part['part_id']=row['source_id'].split(':',1)[1]
        part['rule']['rule_id']=row['id']
        parts.append(part)
    plan['effect_bindings'][0]['parts']=parts
    return plan


def test_actual_app_calculation_binds_final_program_to_actual_sources(tmp_path):
    db=tmp_path/'absent.db';plan=authorize(reaction_plan(),db);before=copy.deepcopy(plan)
    out=run_plan_calc(plan,db)
    adoption=out['report']['resource_reaction_source_adoption']
    assert adoption['status']=='ready' and adoption['source_authority']=='caller_assumed'
    assert adoption['program']==Program(out['report']['mechanics_model'],['a']).report()
    assert out['result']['resource_reactions']['program']==adoption['program']
    assert [r['state'] for r in out['result']['resource_reactions']['operations']]==['committed']*3
    assert out['result']['resource_reaction_source_adoption']==adoption
    assert plan==before and not db.exists()


@pytest.mark.parametrize('part_id', ['refill','pool'])
def test_source_exclusion_projects_contract_without_reviving_operations(tmp_path,part_id):
    plan=reaction_plan();db=tmp_path/'absent.db'
    next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id']==part_id).update(state='excluded',reason='engineering exclusion')
    out=run_plan_calc(authorize(plan,db),db)
    adoption=out['report']['resource_reaction_source_adoption']
    assert adoption['status']=='excluded' and 'program' not in adoption
    assert 'resource_reaction_contract' not in out['report']['mechanics_model']
    assert not out['report']['mechanics_model'].get('shared_pools',{}).get('operations')
    assert 'resource_reactions' not in out['result']


@pytest.mark.parametrize('edit', ['source_only','old_context','amount','missing_part'])
def test_unreviewed_change_is_pending_never_legacy_execution(tmp_path,edit):
    db=tmp_path/'absent.db';plan=authorize(reaction_plan(),db)
    part=next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id']=='refill')
    if edit=='source_only':part['dependencies']['mode']='source_only'
    elif edit=='old_context':part['dependencies']['context_fingerprint']='old'
    elif edit=='amount':plan['mechanics_model']['shared_pools']['operations'][0]['amount']=4
    else:plan['effect_bindings'][0]['parts'].remove(part)
    report=compose_equipment_plan(plan,db)
    assert report['state']=='pending'
    assert report['resource_reaction_source_adoption']['status']=='pending'
    with pytest.raises(PlanError):run_plan_calc(plan,db)


def test_invalid_original_contract_cannot_be_hidden_by_excluding_source(tmp_path):
    plan=reaction_plan();plan['mechanics_model']['resource_reaction_contract']['operation_ids']=['unknown']
    for part in plan['effect_bindings'][0]['parts']:part.update(state='excluded',reason='not applicable')
    with pytest.raises(PlanError):compose_equipment_plan(plan,tmp_path/'absent.db')
