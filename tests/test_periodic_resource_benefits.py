import copy

import pytest
from test_direct_stellar_damage_contract import direct_stellar_profile
from test_mechanics_execution import EVIDENCE, ability, action, add, rule
from test_stellar_resource_public_operations import fixture, trial_event

from genshin_engine.mechanics import execute_mechanics


def damage_rule(m, *, every=5):
    value=rule('consumption-damage', effects=[], duration=0,
        trigger={'event':'resource_change','phase':'post',
                 'resource_operations':['consumed','overflow_consumed'],
                 'resource_every':{'counter':'consumed','amount':every}},
        resource_damage=[{'id':'threshold','action_id':'cast','target_id':'enemy','area_id':'battlefield.primary',
            'ability_profile':direct_stellar_profile(character_id='a',ability_id='e',component='consumption-threshold')}])
    add(m,value)
    return value


def resource_hits(out):
    return [e for e in out.get('special_reaction_trace', ())
            if e.get('type')=='special_reaction_damage_request' and 'resource_trigger' in e]


def execute(t,r,m):
    out=execute_mechanics(t,r,m)
    assert out['status'] in ('passed','unproven'),out['issues']
    return out


def test_actual_creation_triggers_stacks_and_shared_cooldown_energy():
    t,r,m=fixture()
    r['characters'][0].update(energy_capacity=100,initial_energy=0)
    base=copy.deepcopy(m['special_reaction_events'][0])
    m['special_reaction_events'].extend([
        {**base,'id':'core','t':.1,'operation':'set_mode','mode':'extended_generation','duration_seconds':10},
        {**base,'id':'late-grant','t':8.31,'amount':1},
    ])
    del m['special_reaction_events'][-2]['amount']
    selector={'event':'resource_change','phase':'post','characters':['a'],'resource_operations':['created'],
              'resource_modes':['extended_generation']}
    stacking=rule('creation-crit',benefit='benefit.crit_rate.all',value=.05,trigger=selector,duration=15,
                  stacking={'max':4,'add':1,'policy':'add_and_refresh'})
    energy=rule('creation-energy',trigger=selector,duration=0,effects=[],
                timing={'delay':0,'duration':0,'cooldown':8,'cooldown_anchor':'success'},
                resources=[{'id':'energy','kind':'energy_direct','delay':0,'amounts':{'a':10},'source_part':'creation-recovery'}])
    add(m,stacking,energy)
    m['resource_ownership']=[{'rule_id':energy['id'],'source_part':'creation-recovery',
        'source_ref':{'contract':'resource-trigger-source-v1','kind':'current_trigger'},'replaces_event_ids':[]}]
    before=copy.deepcopy((t,r,m))
    out=execute(t,r,m)
    credits=[e for e in out['rotation_check']['event_log'] if e['type']=='energy_direct' and e['status']=='passed']
    assert [e['t'] for e in credits]==[.3,8.31]
    assert out['rotation_check']['characters'][0]['final_energy']==20
    activations=[e for e in out['mechanics_trace'] if e['type']=='activation' and e['rule_id']=='creation-crit']
    assert [e['layers'] for e in activations[:3]]==[1,2,3]
    assert max(e['layers'] for e in activations)==4
    assert (t,r,m)==before


def test_real_consumption_threshold_ignores_refund_creation_and_expiry():
    t,r,m=fixture()
    trial_event(m,outcome=True)
    base=copy.deepcopy(m['special_reaction_events'][0])
    m['special_reaction_events'].extend([
        {**base,'id':'extra-grant','t':.6,'amount':1},
        {**base,'id':'extra-consume','t':.7,'operation':'consume','amount':3},
    ])
    add(m,rule('consumption-threshold',trigger={'event':'resource_change','phase':'post',
        'resource_operations':['consumed','overflow_consumed'],'resource_every':{'counter':'consumed','amount':5}}))
    out=execute(t,r,m)
    activations=[e for e in out['mechanics_trace'] if e['type']=='activation' and e['rule_id']=='consumption-threshold']
    assert len(activations)==1 and activations[0]['t']==.7
    counters=[e['resource_counters']['consumed'] for e in out['mechanics_trace'] if e.get('event')=='resource_change' and e['resource_operation']=='consumed']
    assert counters==[1,2,3,4,5]


def test_threshold_cannot_repeat_from_unchanged_counter_on_other_operations():
    t,r,m=fixture()
    add(m,rule(trigger={'event':'resource_change','phase':'post','resource_operations':['created'],
                       'resource_every':{'counter':'consumed','amount':5}}))
    out=execute_mechanics(t,r,m)
    assert out['status']=='invalid'
    assert any(e['code']=='resource_change_counter_scope' for e in out['issues'])


def test_threshold_emits_real_damage_twice_at_same_time_and_replays():
    t,r,m=fixture()
    config=m['special_reaction_model']['replacements']['stellar_superconduct']['mechanics']['periodic_resource']
    config['capacity']=10
    m['special_reaction_events'][0]['amount']=10
    m['special_reaction_events'][1]['amount']=10
    damage_rule(m)
    out=execute(t,r,m)
    requests=resource_hits(out)
    assert len(requests)==2
    assert [e['resource_trigger']['ordinal'] for e in requests]==[5,10]
    assert [e['t'] for e in requests]==[.4,.4]
    assert len({e['delivery_identity'] for e in requests})==2
    ids={e['event_id'] for e in requests}
    hits=[h for h in out['resolved_template']['hits']
          if h.get('extensions',{}).get('special_reaction',{}).get('request_id') in ids]
    assert len(hits)==2 and all(h['multiplier']==2.4 for h in hits)
    assert execute(*copy.deepcopy((t,r,m)))['result']==out['result']


def test_resource_damage_not_created_by_waived_consumption():
    t,r,m=fixture()
    trial_event(m,waive=True,grant=0)
    damage_rule(m,every=1)
    out=execute(t,r,m)
    assert not resource_hits(out)


@pytest.mark.parametrize('problem,code',[
    ('wrong_owner','resource_damage_owner'),('wrong_source','direct_special_ability_binding_mismatch'),
    ('manual_duplicate','resource_damage_duplicate_owner'),('delay','resource_damage_scope'),
])
def test_resource_damage_rejects_wrong_binding_and_double_counting(problem,code):
    t,r,m=fixture()
    value=damage_rule(m,every=1)
    declaration=value['resource_damage'][0]
    if problem=='wrong_owner':
        declaration['ability_profile']=direct_stellar_profile(character_id='other',ability_id='e')
    elif problem=='wrong_source':
        declaration['ability_profile']=direct_stellar_profile(character_id='a',ability_id='wrong',component='consumption-threshold')
    elif problem=='manual_duplicate':
        declaration['ability_profile']=copy.deepcopy(m['special_reaction_events'][-1]['ability_profile'])
    else:
        value['timing']['delay']=1
    out=execute_mechanics(t,r,m)
    assert out['status']=='invalid',out['issues']
    assert any(e['code']==code for e in out['issues']),out['issues']


def test_matching_threshold_excludes_consumption_before_core_mode():
    t,r,m=fixture()
    base=copy.deepcopy(m['special_reaction_events'][0])
    mode={**base,'id':'core','t':.6,'operation':'set_mode','mode':'extended_generation','duration_seconds':5}
    del mode['amount']
    m['special_reaction_events'].extend([
        mode,{**base,'id':'grant2','t':.7,'amount':3},
        {**base,'id':'spend2','t':.8,'operation':'consume','amount':3},
        {**base,'id':'grant3','t':.9,'amount':2},
        {**base,'id':'spend3','t':.95,'operation':'consume','amount':2},
    ])
    value=damage_rule(m)
    value['trigger'].update(resource_modes=['extended_generation'])
    value['trigger']['resource_every']['scope']='matching'
    out=execute(t,r,m)
    assert [row['t'] for row in resource_hits(out)]==[.95]
    last=next(row for row in out['mechanics_trace'] if row.get('event')=='resource_change' and row['resource_counters']['consumed']==7)
    assert last['resource_rule_counts']['consumption-damage']==5


def test_resource_damage_waits_for_actual_source_action_without_spending_cooldown():
    t,r,m=fixture()
    r['actions'].append(action('later','e',start=.6,end=.65,repeat={'mode':'once'}))
    value=damage_rule(m,every=1)
    value['resource_damage'][0]['action_id']='later'
    value['timing']['cooldown']=10
    base=copy.deepcopy(m['special_reaction_events'][0])
    m['special_reaction_events'].append({**base,'id':'later-spend','t':.7,'operation':'consume','amount':1})
    out=execute(t,r,m)
    assert [row['t'] for row in resource_hits(out)]==[.7]


def test_actual_resource_creation_energy_pays_burst_and_absence_does_not():
    t,r,m=fixture()
    r['characters'][0]['initial_energy']=0
    r['abilities'].append(ability('q',energy={'required':10,'amount':10},kind='action.elemental_burst'))
    r['actions'].append(action('burst','q',start=.6,end=.7,repeat={'mode':'once'}))
    r['events'].append(dict(id='q-spend',type='energy_spend',t=.6,order=1,action_id='burst',character='a',
                           required=10,amount=10,repeat={'mode':'once'},**EVIDENCE))
    recharge=rule('recharge',effects=[],duration=0,trigger={'event':'resource_change','phase':'post','resource_operations':['created']},
                  timing={'delay':0,'duration':0,'cooldown':8,'cooldown_anchor':'success'},
                  resources=[{'id': 'recovery','kind': 'energy_direct','delay': 0,'amounts': {'a':10},'source_part': 'creation'}])
    add(m,recharge)
    m['resource_ownership']=[{'rule_id':'recharge','source_part':'creation',
        'source_ref':{'contract':'resource-trigger-source-v1','kind':'current_trigger'},'replaces_event_ids':[]}]
    out=execute(t,r,m)
    burst=next(e for e in out['rotation_check']['event_log'] if e['id']=='burst' and e['type']=='action')
    assert burst['status']=='passed'
    m['rules']=[]
    m['initial_state']['rules']=[]
    m['resource_ownership']=[]
    without=execute_mechanics(t,r,m)
    assert without['status']=='failed',without['issues']
    failed=next(e for e in without['rotation_check']['event_log'] if e['id']=='burst' and e['type']=='action')
    assert failed['status']=='failed'
