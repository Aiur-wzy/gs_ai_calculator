"""Independent actual-source resolver boundaries; no native/runtime certification."""
import copy

import pytest

from genshin_app.equipment.files import context_view, fingerprint, input_view
from genshin_app.equipment.schema import PlanError
from tests.test_attribute_stat_sources import classification, plan, resolve


def reviewed(p):
    _,adoption,_=resolve(p)
    row=classification(p,'atk-ratio')
    row['component']=next(r['component'] for r in adoption['source_effects'] if r['effect_ref']['effect_id']=='atk-ratio')
    p['mechanics_model']['attribute_model']['effect_classifications']=[row]
    p['attribute_review']={'mode':'manual_context','note':'Independent source facts reviewed', 'context_fingerprint':None}
    p['attribute_review']['context_fingerprint']=fingerprint(context_view(input_view(p)))
    return p

@pytest.mark.parametrize('field,value',[('atk',2500),('base_atk',950)])
def test_panel_or_canonical_base_changes_invalidate_both_source_and_context(field,value):
    p=reviewed(plan())
    assert resolve(p)[1]['review_status']=='ready'
    p['manual_template']['characters'][0]['stats'][field]=value
    _,adoption,issues=resolve(p)
    assert adoption['review_status']=='pending'
    assert {'attribute_scope_unreviewed','attribute_source_stale'} <= {i[0] for i in issues}

def test_zone_change_keeps_real_ratio_identity_and_invalidates_confirmation():
    p=reviewed(plan())
    effect=p['mechanics_model']['rules'][0]['effects'][-1]
    effect['benefit_type_id']='benefit.stat_add.atk'
    _,adoption,issues=resolve(p)
    record=next(r for r in adoption['components'] if r['path'].endswith('.component'))
    assert record['value_unit']=='flat' and record['application']=={'kind':'flat'}
    assert adoption['review_status']=='pending' and any(i[0]=='attribute_source_stale' for i in issues)

def test_flat_atk_source_does_not_require_missing_white_value_or_inject_zero():
    p=plan(); p['manual_template']['characters'][0]['stats'].pop('base_atk',None)
    before=copy.deepcopy(p)
    _,adoption,issues=resolve(p)
    assert p==before
    assert any(r['output_stat']=='atk' for r in adoption['source_effects'])
    assert not any(i[0]=='missing_input' for i in issues)

def test_wrong_provider_cannot_supply_same_effect_initial_claim():
    p=plan(); other=copy.deepcopy(p['manual_template']['characters'][0]);other['id']='b'
    p['manual_template']['characters'].append(other)
    row=classification(p,'atk-flat');row['component']['provider']='b'
    p['mechanics_model']['attribute_model']['initial_basis'][1]['included_components']=[{'component':row['component'],'value':200,'nonextra_inclusion':'unknown'}]
    with pytest.raises(PlanError):resolve(p)

def test_non_atk_manual_output_is_not_admitted_by_component_claim():
    p=plan(); p['manual_template']['buffs'].append({'id':'hp-only','target':'a','extensions':{'owner':'a','source_id':'manual:hp'},'effects':[{'id':'x','zone':'stat_add','stat':'hp','value':200}]})
    row=classification(p,'x');row['effect_ref']={'kind':'manual_buff','buff_id':'hp-only','effect_id':'x'};row['component']['part_id']='manual:hp'
    p['mechanics_model']['attribute_model']['effect_classifications']=[row]
    with pytest.raises(PlanError):resolve(p)

def test_returned_dimensions_and_auto_unknowns_do_not_mutate_native_or_next_resolution():
    p=plan();before=copy.deepcopy(p)
    model,adoption,_=resolve(p)
    adoption['source_effects'][-1]['application']['kind']='flat'
    model['effect_classifications'].clear()
    _,new,_=resolve(p)
    assert p==before
    assert any(r['application']['kind']=='recipient_base_fraction' for r in new['source_effects'])
    assert len(new['automatic_unknown'])==3
