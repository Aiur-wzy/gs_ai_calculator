import copy

import pytest
from test_direct_stellar_damage_contract import direct_stellar_profile
from test_elemental_multiaura_v4 import aura
from test_special_reaction_mechanics_projection import _application_fixture, _enable
from test_stellar_periodic_resource import CONFIG

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.mechanics_validation import validate_mechanics_model


@pytest.mark.parametrize(("count", "exceeds_limit"), [(284, False), (2048, False), (2049, True)])
def test_long_stellar_timeline_admission(count, exceeds_limit):
    _, _, mechanics = fixture()
    grant = mechanics["special_reaction_events"][0]
    mechanics["special_reaction_events"] = [
        dict(grant, id=f"grant.{index}", t=index / 10) for index in range(count)
    ]
    issues = validate_mechanics_model(mechanics)["issues"]
    if exceeds_limit:
        assert any(issue["code"] == "limit" and issue["path"] == "mechanics_model.special_reaction_events"
                   for issue in issues)
    else:
        assert not issues


def fixture():
    template, rotation, mechanics = _application_fixture('electro', initial=[aura('cryo', .8, 'a')], times=(.2,1.0))
    rotation['abilities'][0].update(variant='tap',effective_level=10)
    _enable(mechanics,'stellar_superconduct')
    template['hits'][1]['element']='cryo'
    mechanics['hit_events'][1]['application']={'profile_id':'cryo','target_id':'enemy','landed':True,'area_id':'battlefield.primary'}
    profile=mechanics['special_reaction_model']['replacements']['stellar_superconduct']['mechanics']
    profile.update(status='assumed',override_reason='explicit periodic resource test',
                   periodic_resource={**CONFIG,'owner_id':'a','area_id':'battlefield.primary'})
    base={'action_id': 'cast','target_id': 'enemy','area_id': 'battlefield.primary','order': 20,
              'status': 'assumed','source': 'explicit resource input test'}
    mechanics['special_reaction_events']=[
        dict(base,id='grant',kind='stellar_resource_operation',operation='grant',amount=3,t=.3),
        dict(base,id='consume',kind='stellar_resource_operation',operation='consume',amount=2,t=.4),
        dict(base,id='damage',kind='direct_stellar_damage',t=.5,
             ability_profile=direct_stellar_profile(character_id='a',ability_id='e'),
             resource_consumption={'event_id':'consume','mode':'scale_multiplier'}),
    ]
    return template,rotation,mechanics


def trial_event(mechanics, *, outcome=True, waive=False, grant=1, cost=2):
    event=mechanics['special_reaction_events'][1]
    event.pop('amount')
    event.update(operation='trial',outcome=outcome,
                 trial={'cost': cost,'base_grant': 0,'success_grant': grant,'success_waives_cost': waive,'chance': .34})
    return event


@pytest.mark.parametrize('outcome,balance',[(True,2),(False,1)])
def test_custom_refund_keeps_actual_consumption_and_calculated_damage(outcome,balance):
    template,rotation,mechanics=fixture()
    trial_event(mechanics,outcome=outcome)
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status'] in ('passed','unproven'),out['issues']
    receipt=next(r for r in out['special_reaction_trace'] if r.get('type')=='stellar_resource_receipt' and r['event_id']=='0:consume')
    assert receipt['amount']==2 and receipt['count']==balance
    assert receipt['trial']['mode']=='custom'
    assert receipt['trial']['created_units']==int(outcome)
    assert out['special_state_timeline']['status']=='complete',out['special_state_timeline']['issues']
    states=out['special_state_timeline']['states']
    assert any(s['kind']=='resource_inventory' and s['count']==balance for s in states)
    assert all(s['knowledge']=='known' for s in states)
    hit=next(h for h in out['resolved_template']['hits'] if h.get('extensions',{}).get('special_reaction',{}).get('request_id')=='0:damage')
    assert hit['multiplier']==4.8


def test_waived_cost_executes_without_fabricating_consumption():
    template,rotation,mechanics=fixture()
    trial_event(mechanics,waive=True,grant=0)
    mechanics['condition_events']=[{'id': 'check-waiver','t': .45,'order': 21,'condition': 'did-execute','repeat': {'mode':'once'},
        'stellar_resource': {'event_id':'consume','measure':'executed_units','amount':2},'status': 'assumed','source': 'custom trial test'}]
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status'] in ('passed','unproven'),out['issues']
    receipt=next(r for r in out['special_reaction_trace'] if r.get('type')=='stellar_resource_receipt' and r['event_id']=='0:consume')
    assert receipt['count']==3 and receipt['amount']==0
    assert receipt['trial']['executed_units']==2
    assert next(r for r in out['mechanics_trace'] if r.get('type')=='stellar_resource_condition')['value']
    assert not any(h.get('extensions',{}).get('special_reaction',{}).get('request_id')=='0:damage' for h in out['resolved_template']['hits'])


def test_trial_shortage_cannot_be_rescued_by_success_or_refund():
    template,rotation,mechanics=fixture()
    trial_event(mechanics,waive=True,grant=3,cost=4)
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status'] in ('passed','unproven'),out['issues']
    receipt=next(r for r in out['special_reaction_trace'] if r.get('type')=='stellar_resource_receipt' and r['event_id']=='0:consume')
    assert receipt['status']=='blocked' and receipt['count']==3
    assert receipt['trial']['created_units']==0
    assert receipt['trial']['executed'] is False
    assert receipt['trial']['eligible_count']==0


@pytest.mark.parametrize('change,code',[
    ({'outcome':None},'schema'),
    ({'trial':{'cost': 1,'base_grant': 0,'success_grant': 1,'success_waives_cost': False,'chance': 0}},'stellar_resource_trial_invalid'),
])
def test_trial_bad_inputs_are_rejected_before_execution(change,code):
    template,rotation,mechanics=fixture()
    trial_event(mechanics).update(change)
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status']=='invalid'
    assert not out.get('result')


def test_actual_resource_consumption_drives_public_damage_multiplier():
    template,rotation,mechanics=fixture()
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status'] in ('passed','unproven'),out['issues']
    hit=next(h for h in out['resolved_template']['hits'] if h.get('extensions',{}).get('special_reaction',{}).get('request_id')=='0:damage')
    assert hit['multiplier']==4.8
    assert hit['extensions']['elemental']['resource_consumption']['actual_amount']==2
    again=execute_mechanics(copy.deepcopy(template),copy.deepcopy(rotation),copy.deepcopy(mechanics))
    assert out['result']==again['result']


def test_grant_cannot_masquerade_as_consumption():
    template,rotation,mechanics=fixture()
    mechanics['special_reaction_events'][-1]['resource_consumption']['event_id']='grant'
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status']=='invalid'
    assert any(i['code']=='stellar_resource_consumption_binding' for i in out['issues'])


def test_wrong_owner_and_extra_operation_fields_rejected_before_execution():
    template,rotation,mechanics=fixture()
    mechanics['special_reaction_events'][0]['counter']='consumed'
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status']=='invalid'
    assert any(i['code']=='stellar_resource_parameters' for i in out['issues'])


def test_resource_condition_uses_executed_amount_and_rejects_forward_reference():
    template,rotation,mechanics=fixture()
    condition={'id': 'resource-condition','t': .45,'order': 21,'condition': 'resource-spent','repeat': {'mode':'once'},
                   'stellar_resource': {'event_id':'consume','amount':2},'status': 'assumed','source': 'actual consumption test'}
    mechanics['condition_events']=[condition]
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status'] in ('passed','unproven'),out['issues']
    receipts=[r for r in out['mechanics_trace'] if r.get('type')=='stellar_resource_condition']
    assert receipts and receipts[0]['value'] is True
    assert receipts[0]['event_id']=='resource-condition'
    assert receipts[0]['resource_event_id']=='0:consume'
    condition['stellar_resource']['amount']=1
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status'] in ('passed','unproven'),out['issues']
    assert next(r for r in out['mechanics_trace'] if r.get('type')=='stellar_resource_condition')['value'] is False
    condition['t']=.35
    out=execute_mechanics(template,rotation,mechanics)
    assert out['status']=='invalid'
    assert any(i['code']=='stellar_resource_condition_binding_invalid' for i in out['issues'])


@pytest.mark.parametrize('outcome',[None,False,True,'threshold_damage','expected'])
def test_app_plan_saved_cli_replay_preserves_resource_scaled_damage(tmp_path,monkeypatch,capsys,outcome):
    import json

    from test_plan_mechanics import native, review
    from test_special_reaction_integration import equipment_snapshot

    import genshin_engine.special_reaction_capabilities as capabilities
    from genshin_app.cli import main
    from genshin_app.equipment import run_plan_calc
    equipment=equipment_snapshot(10000150,skill_depot_id=15001)
    project=capabilities.project_special_reaction_party_capabilities_detailed
    monkeypatch.setattr(capabilities,'project_special_reaction_party_capabilities_detailed',
        lambda inputs,actor_ids=None:project({**inputs,'a':equipment},actor_ids=actor_ids))
    template,rotation,mechanics=fixture()
    if outcome == 'threshold_damage':
        from test_periodic_resource_benefits import damage_rule
        damage_rule(mechanics,every=1)
    elif outcome == 'expected':
        trial_event(mechanics).pop('outcome')
    elif outcome is not None:
        trial_event(mechanics,outcome=outcome)
    template['characters'][0]['stats']['energy_recharge']=1
    plan=native()
    bindings=plan['effect_bindings'] if outcome=='threshold_damage' else []
    if bindings:
        mechanics['rules'][0]['source_id']='binding:part'
        bindings[0]['parts'][0]['rule']['rule_id']='consumption-damage'
    plan.update(manual_template=template,rotation_model=rotation,mechanics_model=mechanics,effect_bindings=bindings)
    if bindings:
        review(plan,tmp_path/'absent.db')
    out=run_plan_calc(plan,tmp_path/'absent.db')
    assert out['result'],out['report']
    if outcome=='threshold_damage':
        generated=[h for h in out['plan']['resolved_template']['hits']
                   if h.get('extensions',{}).get('elemental',{}).get('resource_trigger')]
        assert len(generated)==2
    path=tmp_path/'resource.plan.json'
    path.write_text(json.dumps(out['plan']),encoding='utf8')
    monkeypatch.setattr('genshin_app.cli._require_runtime_database',lambda _: {'can_start':True})
    assert main(['--data-dir',str(tmp_path),'equipment','calc','--plan',str(path)])==0
    replay=json.loads(capsys.readouterr().out)['data']
    assert replay['result']==out['result']
