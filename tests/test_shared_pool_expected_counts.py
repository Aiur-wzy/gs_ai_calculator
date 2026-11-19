import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from genshin_engine.shared_pools import SharedPoolLedger, validate_shared_pools
from tests.test_shared_pool_execution import setup
from tests.test_shared_pool_v2 import scaled, spend


def expected_config():
    pools=scaled(amount=100,policy='all_or_nothing',initial=300)
    pools['pools'][0].update(unit_scale=100,capacity=500)
    pools['operations'][0]['random_effect']={'kind':'waive_spend','chance':.34}
    return pools


def test_default_expected_waiver_keeps_fractional_display_units_in_atoms():
    pools=expected_config()
    ledger=SharedPoolLedger(pools,character_ids=['a','b'])
    row=spend(ledger)[0]
    assert row['accepted']==66 and row['after']==234
    assert row['probability']['mode']=='expected'
    assert row['probability']['expected_count']==.34
    assert row['probability']['outcome'] is None
    assert row['probability']['consumed_units']==66


def test_expected_cooldown_and_insufficient_resource_do_not_add_fake_counts():
    pools=expected_config()
    pools['operations'][0]['cooldown']={'seconds':1,'anchor':'success'}
    ledger=SharedPoolLedger(pools,character_ids=['a','b'])
    first=spend(ledger)[0]
    second=spend(ledger,run='0:next',t=1)[0]
    assert first['probability']['expected_count']==.34
    assert second['reason']=='cooldown' and second['probability']['expected_count']==0
    pools['pools'][0]['initial']={'team':0}
    blocked=spend(SharedPoolLedger(pools,character_ids=['a','b']))[0]
    assert blocked['reason']=='insufficient' and blocked['probability']['expected_count']==0


def test_precision_error_has_scaling_guidance_and_does_not_round():
    pools=expected_config()
    pools['operations'][0]['amount']=1
    issues=validate_shared_pools(pools,character_ids=['a','b'])
    assert any('unit_scale' in i['message'] and '50' in i['message'] for i in issues),issues


@pytest.mark.parametrize('mode,outcomes', [('expected',[]),('custom',None)])
def test_expected_and_custom_inputs_cannot_be_ambiguously_mixed(mode,outcomes):
    pools=expected_config()
    effect=pools['operations'][0]['random_effect']
    effect['mode']=mode
    if outcomes is not None:effect['outcomes']=outcomes
    assert any(i['code']=='pool_probability_invalid' for i in validate_shared_pools(pools,character_ids=['a','b']))


def test_expected_extra_grant_uses_ordinary_damage_and_survives_replay():
    t,r,m=setup(initial=0,times=(.2,.3,.4))
    r['actions'][0]['repeat']={'mode':'once'}
    pools=m['shared_pools'];pools['schema_version']=2
    pools['pools'][0].update(unit_scale=100,capacity=500)
    pools['consumers'][0]['amount']=100
    pools['operations'][0].update(amount=100,random_effect={'kind':'extra_grant','extra_amount':100,'chance':.5})
    before=copy.deepcopy((t,r,m))
    out=execute_mechanics(t,r,m)
    assert out['status'] in ('passed','unproven'),out['issues']
    grant=next(row for row in out['shared_pool_trace'] if row['type']=='operation')
    assert grant['requested']==150 and grant['probability']['expected_count']==.5
    assert [h['expected'] for h in out['result']['hits']]==[45,90,45]
    assert out['result']['shared_pools']['probability_mode']=='expected'
    assert execute_mechanics(*copy.deepcopy(before))==out
    assert (t,r,m)==before
