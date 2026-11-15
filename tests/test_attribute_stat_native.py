"""A2b.0 native fact bridge only; no queue/source authority."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.attribute_stat_graph import AttributeStatGraphLedger
from genshin_engine.attribute_stat_native import effect_output, prepare_initials
from tests.test_attribute_graph import frame
from tests.test_attribute_ledger import contribution


def fixture():
    t={'characters':[{'id':'a','stats':{'atk':100,'base_atk':50,'em':200}},
                     {'id':'b','stats':{'atk':300,'base_atk':80,'em':20}}]}
    m={'contract':'attribute-ledger-v3','initial_basis':[
        {'actor':'a','stat':'atk','included_components':[]},
        {'actor':'a','stat':'em','included_components':[],
         'nonextra':{'mode':'panel_total','status':'confirmed','source':'Synthetic ordinary baseline'}}],
       'effect_classifications':[]}
    return t,m


def service(t,m):
    return AttributeStatGraphLedger({'contract':m['contract'],'initials':prepare_initials(t,m),
                                    'contributions':[],'frames':[frame()]})


def test_panel_total_and_white_base_have_single_canonical_owner():
    t,m=fixture()
    result=service(t,m)
    assert result.read_stat('a','atk','total','f0')['value']==100
    assert result.read_stat('a','atk','base','f0')['value']==50
    assert result.read_stat('a','em','nonextra','f0')['value']==200
    assert result.read_stat('b','em','nonextra','f0')['status']=='pending'
    t['characters'][0]['stats'].update(atk=125,base_atk=75)
    new=service(t,m)
    assert new.read_stat('a','atk','total','f0')['value']==125
    assert new.read_stat('a','atk','base','f0')['value']==75
    assert result.read_stat('a','atk','base','f0')['value']==50


def test_missing_white_base_is_not_zero_and_does_not_block_total_or_em():
    t,m=fixture()
    del t['characters'][0]['stats']['base_atk']
    result=service(t,m)
    assert result.read_stat('a','atk','total','f0')['value']==100
    assert result.read_stat('a','em','total','f0')['value']==200
    assert result.read_stat('a','atk','base','f0')['issues'][0]['code']=='attribute_basis_missing'


@pytest.mark.parametrize('change', ['base_override','atk_nonextra','duplicate','missing_actor','missing_stat',
                                   'classification_atk_included','stat_in_component','extra_total'])
def test_native_closed_grammar_and_identity_reject_wrong_initial_basis(change):
    t,m=fixture()
    component={k:v for k,v in contribution('x',10)['component'].items() if k!='recipient'}
    if change=='base_override': m['initial_basis'][0]['base']={'mode':'explicit','value':999,'status':'assumed','source':'bad'}
    elif change=='atk_nonextra': m['initial_basis'][0]['nonextra']={'mode':'panel_total','status':'assumed','source':'bad'}
    elif change=='duplicate': m['initial_basis'].append(copy.deepcopy(m['initial_basis'][0]))
    elif change=='missing_actor': m['initial_basis'][0]['actor']='none'
    elif change=='missing_stat': del m['initial_basis'][0]['stat']
    elif change=='classification_atk_included': m['initial_basis'][0]['included_components']=[{'component':component,'value':10,'nonextra_inclusion':'included'}]
    elif change=='stat_in_component':
        component['stat']='atk'
        m['initial_basis'][0]['included_components']=[{'component':component,'value':10,'nonextra_inclusion':'unknown'}]
    else: m['initial_basis'][0]['total']=999
    with pytest.raises(AttributeLedgerError): prepare_initials(t,m)


@pytest.mark.parametrize('value',[True,-1,float('nan'),float('inf')])
def test_canonical_panel_must_have_finite_nonnegative_numeric_values(value):
    t,m=fixture(); t['characters'][0]['stats']['base_atk']=value
    with pytest.raises(AttributeLedgerError): prepare_initials(t,m)


def test_already_included_components_are_explanatory_and_deep_copied():
    t,m=fixture()
    component={k:v for k,v in contribution('x',10)['component'].items() if k!='recipient'}
    component['provider']='a'
    m['initial_basis'][0]['included_components']=[{'component':component,'value':10,'nonextra_inclusion':'unknown'}]
    initials=prepare_initials(t,m)
    assert next(x for x in initials if x['actor']=='a' and x['stat']=='atk')['total']==100
    initials[0]['included_components'][0]['value']=999
    assert m['initial_basis'][0]['included_components'][0]['value']==10


@pytest.mark.parametrize('benefit,stat,unit,application',[
    ('benefit.em.all','em','flat','flat'),('benefit.stat_add.atk','atk','flat','flat'),
    ('benefit.stat_pct.atk','atk','ratio','recipient_base_fraction')])
def test_output_operator_is_registry_derived(benefit,stat,unit,application):
    result=effect_output({'benefit_type_id':benefit},kind='rule')
    assert result=={'stat':stat,'value_unit':unit,'application':{'kind':application}}
    result['application']['kind']='forged'
    assert effect_output({'benefit_type_id':benefit},kind='rule')['application']['kind']==application


def test_damage_flat_and_other_properties_do_not_become_atk_or_accept_false_manual_unit():
    assert effect_output({'benefit_type_id':'benefit.base_add.all'},kind='rule') is None
    assert effect_output({'benefit_type_id':'benefit.stat_add.hp'},kind='rule') is None
    with pytest.raises(AttributeLedgerError):
        effect_output({'benefit_type_id':'benefit.stat_pct.atk','zone':'stat_add','stat':'atk'},kind='manual_buff')


def test_model_required_arrays_and_unknown_kind_fail_controlled():
    t,m=fixture(); del m['effect_classifications']
    with pytest.raises(AttributeLedgerError): prepare_initials(t,m)
    with pytest.raises(AttributeLedgerError): effect_output({},kind='unrecognized')
