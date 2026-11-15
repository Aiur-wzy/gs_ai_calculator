"""Independent A2b queue checks; no source authority is inferred."""
import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_graph_runtime import classify
from tests.test_attribute_stat_runtime import setup, value
from tests.test_mechanics_execution import add, condition, damages, rule


def test_trigger_percentage_layers_keep_ratio_and_recipient_base_separate():
    t,r,m=setup((.2,.4,.6));t['characters'][0]['stats']['base_atk']=70
    condition(m,'increase',.3)
    add(m,rule('gain','benefit.stat_add.atk',200,owner='b',beneficiaries={'mode':'self','scope':'locked'},trigger={'event':'condition','phase':'pre','event_ids':['increase']}),
        rule('pct','benefit.stat_pct.atk',value('atk',.001,sample='trigger'),owner='b',domain='provider',beneficiaries={'mode':'characters','characters':['a'],'scope':'locked'},trigger={'event':'hit','phase':'pre','event_ids':['h0','h1']},stacking={'max':2,'add':1,'policy':'independent'}))
    classify(m,'gain','b','unknown');classify(m,'pct','b','unknown')
    out=execute_mechanics(t,r,m)
    assert out['result'] is not None,out['issues']
    assert damages(out)==pytest.approx([51.3,63.9,63.9])
    rows=[c for rec in out['attribute_ledger']['receipts'] for c in rec['contributions'] if c.get('application_base_value') is not None]
    assert {c['raw_evaluated_value'] for c in rows}=={.2,.4}
    assert all(c['application_base_value']==70 for c in rows)
    assert {c['evaluated_value'] for c in rows}=={14,28}


def test_parent_post_atk_changes_same_time_sibling_but_not_parent():
    t,r,m=setup((.2,.2,.4))
    add(m,rule('gain','benefit.stat_add.atk',100,domain='provider',trigger={'event':'hit','phase':'post','event_ids':['h0']}))
    classify(m,'gain','a','unknown')
    out=execute_mechanics(t,r,m)
    assert out['result'] is not None,out['issues']
    assert damages(out)==[45,90,90]
    assert [h['attribute_inputs']['scaling'] for h in out['result']['hits']]==[100,200,200]


@pytest.mark.parametrize('factor',[0,1])
def test_clamp_to_zero_does_not_remove_cross_stat_dependency_cycle(factor):
    t,r,m=setup()
    add(m,rule('atk','benefit.stat_add.atk',value('em',factor,max=0),domain='provider'),
        rule('em','benefit.em.all',value('atk',.2),domain='provider'))
    classify(m,'atk','a','unknown');classify(m,'em','a','included')
    out=execute_mechanics(t,r,m)
    assert out['result'] is None and any(i['code']=='attribute_dependency_cycle' for i in out['issues'])


def test_intermediate_percent_product_overflow_cannot_be_hidden_in_final_scaling():
    t,r,m=setup()
    add(m,rule('pct','benefit.stat_pct.atk',1e308,domain='provider'))
    classify(m,'pct','a','unknown')
    out=execute_mechanics(t,r,m)
    assert out['result'] is None
    assert any(i['code']=='attribute_value_invalid' for i in out['issues']),out['issues']


def test_explicit_match_include_cannot_authorize_filtered_atk_attribute_source():
    t,r,m=setup()
    add(m,rule('limited','benefit.stat_add.atk',20,domain='provider'))
    m['rules'][0]['effects'][0]['filter']={'event':'hit','phase':'pre','damage_type_ids':['damage.elemental_burst']}
    classify(m,'limited','a','unknown')
    matches=[{'rule_id':'limited','effect_id':'value','hit_id':'h0','operation':'include'}]
    out=execute_mechanics(t,r,m,matches)
    assert out['result'] is None and any(i['code']=='attribute_context_unsupported' for i in out['issues'])


def test_pct_does_not_use_provider_base_when_recipient_base_missing():
    t,r,m=setup();del t['characters'][0]['stats']['base_atk']
    t['characters'][1]['stats']['base_atk']=500
    add(m,rule('pct','benefit.stat_pct.atk',.2,owner='b',domain='provider',beneficiaries={'mode':'characters','characters':['a'],'scope':'locked'}))
    classify(m,'pct','b','unknown')
    out=execute_mechanics(t,r,m)
    assert out['result'] is None and any(i['code']=='attribute_basis_missing' for i in out['issues'])


def test_missing_classification_only_blocks_actual_matching_actor():
    t,r,m=setup()
    add(m,rule('unclassified','benefit.stat_add.atk',100,owner='b',domain='provider',beneficiaries={'mode':'self','scope':'locked'}))
    out=execute_mechanics(t,r,m)
    assert out['result'] is not None and damages(out)==[45],out['issues']
    m['rules'][0]['beneficiaries']={'mode':'characters','characters':['a'],'scope':'locked'}
    out=execute_mechanics(t,r,m)
    assert out['result'] is None and any(i['code']=='attribute_component_missing' for i in out['issues'])


def test_unused_manual_percent_missing_base_does_not_block_hp_formula():
    t,r,m=setup();del t['characters'][0]['stats']['base_atk']
    t['characters'][0]['stats']['hp']=1000;t['hits'][0]['scaling_stat']='hp'
    t['buffs']=[{'id':'manual-pct','start':0,'end':1,'scope':'team','target':'a','extensions':{'owner':'a'},'effects':[{'id':'pct','benefit_type_id':'benefit.stat_pct.atk','zone':'stat_pct','stat':'atk','value':.2,'applies_to':{'mode':'all'}}]}]
    m['attribute_model']['effect_classifications'].append({'effect_ref':{'kind':'manual_buff','buff_id':'manual-pct','effect_id':'pct'},'component':{'source_fingerprint':'manual','provider':'a','part_id':'manual-pct','effect_id':'pct'},'nonextra_inclusion':'unknown','status':'assumed','source':'Synthetic'})
    out=execute_mechanics(t,r,m)
    assert out['result'] is not None and damages(out)==[450],out['issues']
    assert not any(rec['query']['stat']=='atk' for rec in out['attribute_ledger']['receipts'])


def test_hp_formula_still_rejects_actual_baseadd_reader_of_atk_cycle():
    t,r,m=setup();t['characters'][0]['stats']['hp']=1000;t['hits'][0]['scaling_stat']='hp'
    add(m,rule('atk-cycle','benefit.stat_add.atk',value('atk',1),domain='provider'),rule('reader','benefit.base_add.all',value('atk',.2),domain='provider'))
    classify(m,'atk-cycle','a','unknown')
    out=execute_mechanics(t,r,m)
    assert out['result'] is None and any(i['code']=='attribute_dependency_cycle' for i in out['issues'])


def test_later_check_origin_gaining_atk_cannot_replace_report_origin_audit():
    t,r,m=setup()
    condition(m,'late',.3);m['condition_events'][0]['repeat']={'mode':'once'}
    add(m,rule('gain','benefit.stat_add.atk',100,domain='provider',duration=40,trigger={'event':'condition','phase':'pre','event_ids':['late']}))
    classify(m,'gain','a','unknown')
    out=execute_mechanics(t,r,m)
    assert out['result'] is not None and damages(out)==[45],out['issues']
    assert out['result']['hits'][0]['attribute_inputs']['scaling']==100
    assert any(rec['query']['frame']['origin']>0 and rec['query']['purpose']=='damage_projection' and rec.get('value')==200 for rec in out['attribute_ledger']['receipts'])
