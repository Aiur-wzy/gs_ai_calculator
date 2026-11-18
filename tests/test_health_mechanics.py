"""Health inputs execute through the shared mechanics clock and damage rules."""
import copy

import pytest
from test_mechanics_execution import EVIDENCE, add, damages, fixture, rule

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.health_runtime import HealthRuntime
from genshin_engine.mechanics import execute_mechanics


def health_fixture(times=(.2, .4, .6)):
    t, r, m = fixture(times)
    t['characters'][0]['stats'].update(hp=10000, base_hp=10000)
    r['actions'][0]['repeat'] = {'mode':'once'}
    m['health_model'] = {'schema_version':1, 'characters':[
        {'id':'a', 'initial_hp_fraction':.5, 'initial_bond_fraction':.2,
         'max_hp_change_policy':'preserve_fraction', 'bond_max_hp_policy':'preserve_amount', **EVIDENCE}],
        'events':[], 'conditions':[]}
    return t, r, m


def operation(eid, time, op, amount, **kw):
    return {'id':eid, 't':time, 'order':5, 'repeat':{'mode':'once'},
            'character':'a', 'operation':op, 'amount':{'basis':'flat', 'factor':amount}, **EVIDENCE, **kw}


def expression(quantity, **kw):
    return {'kind':'health', 'quantity':quantity, 'subject':'owner', 'factor':1, 'sample':'current', **kw}


def test_heal_bond_and_damage_share_clock_and_actual_hp_buff():
    t, r, m = health_fixture()
    m['health_model']['events'] = [operation('heal1', .15, 'heal', 1000),
        operation('heal2', .35, 'heal', 3000), operation('loss', .55, 'hp_loss', 1000)]
    add(m, rule(value=expression('hp_change_fraction')))
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'unproven' and not out['issues']  # finite once-only axis, no cyclic claim
    assert damages(out) == pytest.approx([45,54,58.5])
    state = out['health_ledger']['final_state']['a']
    assert state['current_hp'] == 6000
    assert state['bond'] == 0
    assert state['bond_cleared'] == 2000
    assert state['hp_gained'] == 2000
    assert state['hp_lost'] == 1000


def test_health_trigger_and_max_hp_resize_do_not_invent_healing():
    t, r, m = health_fixture((.2,))
    m['health_model']['events'] = [operation('heal', .15, 'heal', 3000)]
    add(m, rule(benefit='benefit.stat_pct.hp', value=1,
        trigger={'event':'health_change', 'phase':'post', 'event_ids':['heal']}, duration=100))
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'unproven' and not out['issues']
    state = out['health_ledger']['final_state']['a']
    assert (state['max_hp'], state['current_hp'], state['hp_gained']) == (20000,12000,1000)
    assert state['hp_change_fraction'] == .1


def test_current_health_as_ordinary_attack_bonus():
    t, r, m = health_fixture((.2,))
    add(m, rule(benefit='benefit.stat_add.atk', value=expression('current_hp',factor=.01)))
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'unproven' and not out['issues']
    assert damages(out) == pytest.approx([67.5])


def test_health_state_and_actual_changes_are_inherited_across_cycles():
    t, r, m = health_fixture((.2,))
    r['actions'][0]['repeat'] = {'mode':'each_cycle'}
    m['health_model']['events'] = [operation('loss', .15, 'hp_loss', 1000, repeat={'mode':'each_cycle'})]
    add(m, rule(value=expression('hp_fraction')))
    out = execute_mechanics(t, r, m)
    assert out['status'] == 'unproven' and not out['issues']  # HP changes, so no stationary cycle
    changes = [x for x in out['health_ledger']['trace'] if x['operation']=='hp_loss']
    assert [x['after']['current_hp'] for x in changes] == [4000,3000,2000]


def test_overheal_loss_floor_and_zero_hp_does_not_resurrect():
    t, _, m = health_fixture()
    runtime = HealthRuntime(m['health_model'], t)
    runtime.apply(operation('heal',0,'heal',20000),20000,event_id='heal',time=0)
    assert runtime.states['a']['overheal'] == 13000
    runtime.apply(operation('loss',0,'hp_loss',20000,remaining_hp_floor=1),20000,event_id='loss',time=1)
    assert runtime.states['a']['current_hp'] == 1
    runtime.apply(operation('dead',0,'hp_damage',1),1,event_id='dead',time=2)
    runtime.apply(operation('no-revive',0,'heal',20000),20000,event_id='no-revive',time=3)
    assert runtime.states['a']['current_hp'] == 0


def test_invalid_amount_does_not_mutate_ledger():
    t, _, m = health_fixture()
    runtime = HealthRuntime(m['health_model'], t)
    before = copy.deepcopy(runtime.states)
    with pytest.raises(AttributeLedgerError):
        runtime.apply(operation('bad',0,'heal',1),float('inf'),event_id='bad',time=0)
    assert runtime.states == before
    assert not runtime.trace


@pytest.mark.parametrize('sample,expected', [('current',[160,180]), ('trigger',[150,150]), ('event',[160,160])])
def test_health_attack_graph_uses_real_current_trigger_or_event_sample(sample,expected):
    from test_attribute_graph_runtime import classify
    from test_attribute_multistat_runtime import setup
    t,r,m = setup((.2,.4))
    for action in r['actions']:
        action['repeat'] = {'mode':'once'}
    _,_,hm = health_fixture()
    m['health_model'] = hm['health_model']
    t['characters'][0]['stats'].update(hp=10000,base_hp=10000)
    m['health_model']['characters'][0]['initial_bond_fraction'] = 0
    m['health_model']['events'] = [operation('heal1',.15,'heal',1000),operation('heal2',.35,'heal',2000)]
    extra = {'event_id':'heal1'} if sample=='event' else {}
    add(m,rule('hp_atk','benefit.stat_add.atk',expression('current_hp',factor=.01,sample=sample,**extra),domain='provider'))
    classify(m,'hp_atk','a','excluded')
    out=execute_mechanics(t,r,m)
    assert not out['issues'], out['issues']
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']] == expected


@pytest.mark.parametrize('mutation,code', [
    (lambda m:m['health_model']['events'].append(operation('x',.1,'heal',1,status='pending')), 'health_source_pending'),
    (lambda m:m['health_model']['events'].append(operation('x',.1,'heal',1,character='absent')), 'health_binding_invalid'),
    (lambda m:m['health_model']['events'].append(operation('x',.0000001,'heal',1)), 'time_precision'),
    (lambda m:add(m,rule(benefit='benefit.stat_add.hp',value=expression('current_hp'))), 'health_feedback_ambiguous'),
])
def test_invalid_health_declarations_fail_explicitly(mutation,code):
    t,r,m=health_fixture()
    mutation(m)
    out=execute_mechanics(t,r,m)
    assert out['result'] is None
    assert code in [i['code'] for i in out['issues']], out['issues']


def test_max_hp_graph_and_hp_threshold_change_after_heal():
    from test_attribute_graph_runtime import classify
    from test_attribute_multistat_runtime import setup
    t,r,m=setup((.1,.2))
    _,_,hm=health_fixture()
    m['health_model']=hm['health_model']
    m['health_model']['characters'][0]['initial_bond_fraction']=0
    m['health_model']['conditions']=[{'id':'high_hp','character':'a','quantity':'hp_fraction',
        'operator':'ge','threshold':.7,**EVIDENCE}]
    m['health_model']['events']=[operation('heal',.15,'heal',9000)]
    for a in r['actions']:
        a['repeat']={'mode':'once'}
    add(m,rule('hp_up','benefit.stat_pct.hp',1,trigger={'event':'health_change','phase':'post'},duration=100),
        rule('boost',value=1,trigger={'event':'hit','phase':'pre','condition':'high_hp'}))
    classify(m,'hp_up','a','included')
    out=execute_mechanics(t,r,m)
    assert not out['issues'],out['issues']
    assert damages(out)==pytest.approx([45,90])
    state=out['health_ledger']['final_state']['a']
    assert (state['max_hp'],state['current_hp'],state['hp_gained'])==(45000,36000,9000)


def test_event_sample_does_not_borrow_previous_cycle():
    t,r,m=health_fixture((.2,))
    r['actions'][0]['repeat']={'mode':'each_cycle'}
    m['health_model']['events']=[operation('heal',.15,'heal',1000)]
    add(m,rule(value=expression('hp_fraction',sample='event',event_id='heal')))
    out=execute_mechanics(t,r,m)
    assert out['result'] is None
    assert 'health_frame_missing' in [i['code'] for i in out['issues']]


def test_event_sample_attack_panel_is_scoped_to_its_actual_cycle():
    t,r,m=health_fixture((.2,))
    r['actions'][0]['repeat']={'mode':'each_cycle'}
    m['health_model']['characters'][0]['initial_bond_fraction']=0
    m['health_model']['events']=[operation('heal',.15,'heal',1000,repeat={'mode':'each_cycle'})]
    m['health_model']['events'].append({**operation('attack_heal',.25,'heal',0,
        repeat={'mode':'each_cycle'}),'amount':{'basis':'source_atk','factor':1}})
    add(m,rule(benefit='benefit.stat_add.atk',value=expression('current_hp',factor=.01,sample='event',event_id='heal'),
               trigger={'event':'health_change','phase':'post','event_ids':['heal']}))
    out=execute_mechanics(t,r,m)
    assert not out['issues'],out['issues']
    # Reported hits are the first input period, while the inherited ledger
    # continues through all three cyclic checks.
    samples=[s for s in out['health_ledger']['trace'] if s['operation']=='heal']
    assert [s['after']['current_hp'] for s in samples]==pytest.approx([6000,6160,7160,7331.6,8331.6,8514.916])
    assert damages(out)==pytest.approx([72])


def test_party_max_hp_sync_failure_rolls_back_every_actor():
    from types import SimpleNamespace

    from genshin_engine.health_mechanics import sync_health
    t,_,m=health_fixture()
    second=copy.deepcopy(t['characters'][0]);second['id']='b';t['characters'].append(second)
    registration=copy.deepcopy(m['health_model']['characters'][0]);registration['id']='b'
    m['health_model']['characters'].append(registration)
    health=HealthRuntime(m['health_model'],t)
    before=health.snapshot()
    runtime=SimpleNamespace(health_runtime=health,attribute_runtime=None,trace=[],time=0,conditions={},
        ordinary_panel=lambda cid,snapshot,**kw:({'hp':20000 if cid=='a' else float('inf')},[]))
    with pytest.raises(AttributeLedgerError):
        sync_health(runtime,{'run_id':'bad'})
    assert health.states==before and not health.trace and not runtime.trace


def test_health_attack_stacks_keep_each_trigger_sample():
    t,r,m=health_fixture((.4,))
    m['health_model']['characters'][0]['initial_bond_fraction']=0
    m['health_model']['events']=[operation('heal1',.15,'heal',1000),operation('heal2',.35,'heal',2000)]
    m['health_model']['events'].append({**operation('atk_heal',.5,'heal',0),
        'amount':{'basis':'source_atk','factor':1}})
    add(m,rule(benefit='benefit.stat_add.atk',value=expression('current_hp',factor=.01,sample='trigger'),
        trigger={'event':'health_change','phase':'post','event_ids':['heal1','heal2']},
        stacking={'max':2,'add':1,'policy':'independent'}))
    out=execute_mechanics(t,r,m)
    assert not out['issues'],out['issues']
    assert damages(out)==pytest.approx([108])
    assert out['health_ledger']['final_state']['a']['current_hp']==8240


def test_lethal_damage_does_not_produce_damage_as_a_living_character():
    t,r,m=health_fixture((.2,))
    m['health_model']['events']=[operation('lethal',.15,'hp_damage',10000)]
    out=execute_mechanics(t,r,m)
    assert out['result'] is None
    assert 'health_depleted' in [i['code'] for i in out['issues']]
