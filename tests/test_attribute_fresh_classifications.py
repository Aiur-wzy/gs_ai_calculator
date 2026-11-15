"""Fresh compiler facts must bind the exact active source; native intent remains explicit."""
import copy

import pytest

from genshin_app.equipment.attributes import prepare_attribute_model
from genshin_app.equipment.schema import PlanError
from tests.test_attribute_app_sources import attribute_plan, known_classification


def setup():
    plan=attribute_plan();plan['mechanics_model']['attribute_model']['contract']='attribute-ledger-v2'
    rule=plan['mechanics_model']['rules'][0]
    fact={'effect_ref':{'kind':'rule','rule_id':rule['id'],'effect_id':'value'},
          'part_id':rule['source_id'],'provider':rule['owner'],'stat':'em',
          'nonextra_inclusion':'included','classification_authority':'this_admitted_source_fact',
          'fact_fingerprint':'a'*64}
    return plan,fact


def resolve(plan,facts,execution=None):
    missing=[]
    model,adoption=prepare_attribute_model(plan,execution or copy.deepcopy(plan['mechanics_model']),
        plan['manual_template'],'current-source',lambda *args:missing.append(args),fresh_classifications=facts)
    return model,adoption,missing


def test_fresh_fact_uses_same_component_and_does_not_mutate_native_input():
    plan,fact=setup();before=copy.deepcopy(plan)
    model,adoption,_=resolve(plan,[fact])
    row=model['effect_classifications'][0]
    assert row['status']=='confirmed' and row['nonextra_inclusion']=='included'
    assert fact['fact_fingerprint'] in row['source']
    assert not adoption['automatic_unknown']
    assert adoption['automatic_source_classifications'][0]['component']==row['component']
    assert plan==before


@pytest.mark.parametrize('change',['provider','part','stat','authority','fingerprint','duplicate','inactive','nonem','contract','extra'])
def test_invalid_fresh_source_identity_is_rejected(change):
    plan,fact=setup();execution=copy.deepcopy(plan['mechanics_model'])
    if change=='provider':fact['provider']='b'
    elif change=='part':fact['part_id']='unrelated'
    elif change=='stat':fact['stat']='atk'
    elif change=='authority':fact['classification_authority']='caller_assumed'
    elif change=='fingerprint':fact['fact_fingerprint']=''
    elif change=='inactive':execution['rules']=[]
    elif change=='nonem':plan['mechanics_model']['rules'][0]['effects'][0]['benefit_type_id']='benefit.stat_add.atk'
    elif change=='contract':execution['attribute_model']['contract']='attribute-ledger-v3'
    elif change=='extra':fact['approved']=True
    facts=[fact,fact] if change=='duplicate' else [fact]
    with pytest.raises(PlanError):resolve(plan,facts,execution)


@pytest.mark.parametrize('inclusion,status,conflict',[('excluded','confirmed',True),('unknown','pending',True),('included','pending',False)])
def test_explicit_classification_is_never_overwritten_or_promoted(inclusion,status,conflict):
    plan,fact=setup();row=known_classification(plan)
    row.update(nonextra_inclusion=inclusion,status=status)
    plan['mechanics_model']['attribute_model']['effect_classifications']=[row]
    model,adoption,missing=resolve(plan,[fact])
    assert model['effect_classifications'][0]==row
    assert ('attribute_source_fact_conflict' in {x[0] for x in missing}) is conflict
    assert 'automatic_source_classifications' not in adoption


@pytest.mark.parametrize('field,value',[('rule_id',[]),('rule_id',{}),('effect_id',[]),('effect_id',None),('provider',[]),('part_id',None)])
def test_malformed_internal_fact_identity_is_controlled(field,value):
    plan,fact=setup()
    target=fact['effect_ref'] if field in ('rule_id','effect_id') else fact
    target[field]=value
    with pytest.raises(PlanError):resolve(plan,[fact])
