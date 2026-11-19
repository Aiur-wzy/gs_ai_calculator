import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_mechanics_execution import add, rule
from tests.test_periodic_resource_benefits import damage_rule, resource_hits
from tests.test_stellar_resource_public_operations import fixture, trial_event


def test_default_periodic_trial_uses_fractional_refund_and_normal_damage():
    t,r,m=fixture()
    event=trial_event(m,grant=1)
    event.pop('outcome')
    before=copy.deepcopy((t,r,m))
    out=execute_mechanics(t,r,m)
    assert out['status'] in ('passed','unproven'),out['issues']
    receipt=next(x for x in out['special_reaction_trace'] if x.get('event_id')=='0:consume')
    assert receipt['count']==1.34 and receipt['amount']==2
    assert receipt['trial']['expected_count']==.34
    assert receipt['trial']['created_units']==.34
    assert receipt['trial']['mode']=='expected'
    assert 'branches' not in receipt['trial']
    assert (t,r,m)==before
    assert execute_mechanics(*copy.deepcopy(before))==out


def test_expected_waiver_damage_scales_from_expected_debit_not_integer_rounding():
    t,r,m=fixture()
    event=trial_event(m,waive=True,grant=0)
    event.pop('outcome')
    out=execute_mechanics(t,r,m)
    assert out['status'] in ('passed','unproven'),out['issues']
    receipt=next(x for x in out['special_reaction_trace'] if x.get('event_id')=='0:consume')
    assert receipt['count']==1.68 and receipt['amount']==1.32
    assert receipt['trial']['consumed_units']==1.32
    assert any(h.get('extensions',{}).get('elemental',{}).get('resource_consumption',{}).get('actual_amount')==1.32
               for h in out['resolved_template']['hits'])


def test_expected_refund_energy_scales_by_quantity_without_random_draws():
    t,r,m=fixture()
    trial_event(m,grant=1).pop('outcome')
    r['characters'][0].update(initial_energy=0,energy_capacity=100)
    recovery=rule('recovery',effects=[],duration=0,
        trigger={'event':'resource_change','phase':'post','resource_operations':['created']},
        resources=[{'id':'energy','kind':'energy_direct','delay':0,'amounts':{'a':10},'source_part':'creation'}])
    add(m,recovery)
    m['resource_ownership']=[{'rule_id':'recovery','source_part':'creation',
        'source_ref':{'contract':'resource-trigger-source-v1','kind':'current_trigger'},'replaces_event_ids':[]}]
    out=execute_mechanics(t,r,m)
    assert out['status'] in ('passed','unproven'),out['issues']
    events=[e for e in out['rotation_check']['event_log'] if e['type']=='energy_direct' and e['t']==.4]
    assert len(events)==1
    assert any(e.get('resource_weight')==.34 for e in out['mechanics_trace'] if e.get('event')=='resource_change')
    # Initial three creations plus .34 refund, then ordinary generation.
    baseline=copy.deepcopy(m)
    baseline['special_reaction_events'][1]['outcome']=False
    other=execute_mechanics(t,r,baseline)
    assert out['rotation_check']['characters'][0]['final_energy']-other['rotation_check']['characters'][0]['final_energy']==pytest.approx(3.4)


def test_resource_damage_uses_fractional_quantity_while_whole_thresholds_stay_discrete():
    t,r,m=fixture()
    trial_event(m,waive=True,grant=0).pop('outcome')
    definition=damage_rule(m,every=1)
    definition['trigger'].pop('resource_every')
    out=execute_mechanics(t,r,m)
    assert out['status'] in ('passed','unproven'),out['issues']
    requests=resource_hits(out)
    assert len(requests)==2
    assert requests[1]['resource_trigger']['quantity_weight']==.32
    hits=[h for h in out['resolved_template']['hits'] if h.get('extensions',{}).get('elemental',{}).get('resource_trigger')]
    assert hits[1]['multiplier']==pytest.approx(hits[0]['multiplier']*.32)
    definition['trigger']['resource_every']={'counter':'consumed','amount':1}
    threshold=execute_mechanics(t,r,m)
    assert threshold['status'] in ('passed','unproven'),threshold['issues']
    assert len(resource_hits(threshold))==1


def test_default_discrete_buff_floors_cumulative_expected_count_and_replays():
    t,r,m=fixture()
    m['hit_events'][0]['application']['landed']=False  # No unrelated generation.
    trial_event(m,grant=1,cost=1).pop('outcome')
    trial=copy.deepcopy(m['special_reaction_events'][1])
    for i in range(1,4):
        m['special_reaction_events'].append({**trial,'id':f'refund-{i}','t':round(.4+i*.01,2)})
    add(m,rule('expected-stacks',benefit='benefit.crit_rate.all',value=.05,
        trigger={'event':'resource_change','phase':'post','resource_operations':['created']},
        duration=15,stacking={'max':10,'add':1,'policy':'add_and_refresh'}))
    out=execute_mechanics(t,r,m)
    assert out['status'] in ('passed','unproven'),out['issues']
    activations=[e for e in out['mechanics_trace'] if e['type']=='activation' and e['rule_id']=='expected-stacks']
    # Three deterministic initial creations + floor(4 * .34) = four layers.
    assert [e['layers'] for e in activations]==[1,2,3,4]
    rounding=[e for e in out['mechanics_trace'] if e['type']=='expected_discrete_count']
    assert rounding[-1]['expected_count']==1.36
    assert rounding[-1]['applied_count']==1
    assert [e['new_activations'] for e in rounding]==[0,0,1,0]
    assert execute_mechanics(*copy.deepcopy((t,r,m)))==out
