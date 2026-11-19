"""Probabilistic choices preserve actual integer debits and normal consumers."""
import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.resource_counters import CounterProgram, ResourceCounterError
from genshin_engine.shared_pools import SharedPoolError, SharedPoolLedger, validate_shared_pools
from genshin_engine.state_runtime import StateRuntime
from tests.test_resource_counter_atomic import controls
from tests.test_resource_counters import definitions
from tests.test_resource_thresholds import declaration, program
from tests.test_shared_pool_execution import setup
from tests.test_shared_pool_v2 import scaled, spend
from tests.test_state_runtime import event


def choice(success=True, run_id='0:tick', key='team'):
    return {'run_id': run_id, 'key': key, 'success': success}


def effect(kind='waive_spend', outcomes=None):
    return {'mode':'custom', 'kind':kind, 'chance':.5,
            'outcomes':[choice()] if outcomes is None else outcomes,
            **({'extra_amount':1} if kind=='extra_grant' else {})}


def custom(success=True, initial=3):
    value=scaled(amount=2,policy='all_or_nothing',initial=initial)
    value['operations'][0]['random_effect']=effect(outcomes=[choice(success)])
    return value


@pytest.mark.parametrize('success,accepted,balance',[(True,0,3),(False,2,1)])
def test_cost_waiver_is_execution_without_fake_consumption(success,accepted,balance):
    value=custom(success)
    ledger=SharedPoolLedger(value,character_ids=['a','b'])
    row=spend(ledger)[0]
    assert (row['accepted'],row['after'])==(accepted,balance)
    assert row['succeeded'] and row['unfulfilled']==0
    assert row['probability']['executed_units']==2
    assert row['probability']['consumed_units']==accepted
    assert not ledger.unused_probability_outcomes()


def test_waiver_needs_full_balance_and_does_not_roll_on_shortage():
    value=custom(initial=1)
    value['operations'][0]['random_effect']['outcomes']=[]
    ledger=SharedPoolLedger(value,character_ids=['a','b'])
    row=spend(ledger)[0]
    assert row['reason']=='insufficient' and not row['succeeded']
    assert row['after']==1 and row['probability']['outcome'] is None
    assert not row['probability']['sampled']


def test_missing_custom_outcome_does_not_publish_partial_multikey_commit():
    value=custom()
    value['pools'][0].update(scope='per_character',initial={'a':3,'b':3})
    value['operations'][0]['random_effect']['outcomes']=[choice(key='a')]
    ledger=SharedPoolLedger(value,character_ids=['a','b'])
    before=copy.deepcopy((ledger.snapshot(),ledger.receipts(),ledger._cooldowns,ledger._operation_seen))
    with pytest.raises(SharedPoolError,match='key=b'):
        spend(ledger,keys=['a','b'])
    assert (ledger.snapshot(),ledger.receipts(),ledger._cooldowns,ledger._operation_seen)==before


def test_waived_success_starts_shared_cooldown_without_another_roll():
    value=custom()
    value['operations'][0]['cooldown']['seconds']=2
    ledger=SharedPoolLedger(value,character_ids=['a','b'])
    first=spend(ledger)[0]
    second=spend(ledger,t=1,run='0:next')[0]
    assert first['cooldown_until_us']==2_000_000
    assert second['reason']=='cooldown' and not second['probability']['sampled']
    assert second['after']==3


@pytest.mark.parametrize('kind',['duplicate','wrong_key','certain_conflict','partial_spend'])
def test_invalid_scenario_rejected_before_running(kind):
    value=custom()
    random=value['operations'][0]['random_effect']
    if kind=='duplicate':random['outcomes']*=2
    elif kind=='wrong_key':random['outcomes'][0]['key']='nobody'
    elif kind=='certain_conflict':random['chance']=0
    else:value['operations'][0]['spend_policy']='up_to_available'
    issues=validate_shared_pools(value,character_ids=['a','b'])
    assert any(i['code']=='pool_probability_invalid' for i in issues),issues


def test_extra_stack_flows_into_existing_real_damage_consumer():
    t,r,m=setup(initial=0,times=(.2,.3,.4))
    r['actions'][0]['repeat']={'mode':'once'}
    pools=m['shared_pools']
    pools['schema_version']=2
    pools['pools'][0]['unit_scale']=1
    pools['operations'][0].update(amount=1,random_effect=effect('extra_grant',[choice(run_id='0:h0')]))
    before=copy.deepcopy((t,r,m))
    out=execute_mechanics(t,r,m)
    assert out['status'] in ('passed','unproven'),out['issues']
    assert [h['expected'] for h in out['result']['hits']]==[45,90,90]
    grant=next(x for x in out['shared_pool_trace'] if x['type']=='operation')
    assert grant['requested']==grant['accepted']==2
    assert out['result']['shared_pools']['probability_mode']=='custom'
    assert (t,r,m)==before
    pools['operations'][0]['random_effect']['outcomes'][0]['success']=False
    other=execute_mechanics(t,r,m)
    assert [h['expected'] for h in other['result']['hits']]==[45,90,45]
    pools['operations'][0]['random_effect']['outcomes'].append(choice(run_id='typo'))
    extra=execute_mechanics(t,r,m)
    assert extra['status']=='invalid' and extra['result'] is None
    assert any(i['code']=='pool_probability_unused_outcomes' for i in extra['issues'])


def test_waiver_commits_with_counter_and_threshold_without_phantom_spend():
    counters,pools,lives=definitions()
    pools['operations'][0].update(spend_policy='all_or_nothing',random_effect=effect())
    counter=CounterProgram(counters,pools,lives,character_ids=['a','b'])
    observer=program(declaration(pools,down=True),pools)
    ledger=SharedPoolLedger(pools,character_ids=['a','b'],counter_program=counter,observer_program=observer)
    clock=StateRuntime(lives,character_ids=['a','b'],emit_ordered_steps=True)
    controls(ledger,clock,clock.start('field',event(),eligible=True))
    row=spend(ledger)[0]
    assert row['accepted']==0 and row['succeeded']
    assert ledger.counter_snapshot()['values']['count']['total']==0
    assert not ledger.observer_receipts()[0]['crossed']
    assert ledger.counter_receipts()[-1]['resource_accepted']==0


def test_extra_stack_capacity_and_threshold_use_actual_grant():
    pools=scaled(initial=49)
    op=pools['operations'][0]
    op.pop('spend_policy')
    op.update(operation='grant',amount=1,random_effect=effect('extra_grant'))
    pools['pools'][0]['capacity']=50
    observer=program(declaration(pools,down=False),pools)
    ledger=SharedPoolLedger(pools,character_ids=['a','b'],observer_program=observer)
    row=spend(ledger)[0]
    assert (row['requested'],row['accepted'],row['after'],row['wasted'])==(2,1,50,1)
    assert ledger.observer_receipts()[0]['crossed']
