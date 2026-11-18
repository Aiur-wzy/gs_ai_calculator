"""A2b real queue engineering oracles for explicit EM/ATK frames."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_graph_runtime import classify
from tests.test_mechanics_execution import (
    add,
    condition,
    damages,
    fixture,
    rule,
    second_character,
)
from tests.test_snapshot_captures import capture


def setup(times=(.2,)):
    t,r,m=fixture(times)
    second_character(t,r)
    t['characters'][0]['stats']['em']=200
    t['characters'][1]['stats']['em']=20
    m['attribute_model']={'contract':'attribute-ledger-v3','initial_basis':[
        {'actor':c['id'],'stat':s,'included_components':[],**({'nonextra':{'mode':'panel_total','status':'assumed','source':'Synthetic initial'}} if s=='em' else {})}
        for c in t['characters'] for s in ('em','atk')],'effect_classifications':[]}
    return t,r,m


def value(stat='em',factor=.5,**kw):
    return {'kind':'stat','subject':'owner','stat':stat,'sample':'current','factor':factor,**kw}


def run(t,r,m):
    out=execute_mechanics(t,r,m)
    assert out['result'] is not None,out['issues']
    return out


def test_cross_stat_chain_and_actual_classic_scaling_share_one_ledger():
    t,r,m=setup()
    add(m,rule('to_atk','benefit.stat_add.atk',value(),domain='provider'),
        rule('to_b_em','benefit.em.all',value('atk',.2),domain='provider',
             beneficiaries={'mode':'characters','characters':['b'],'scope':'locked'}),
        rule('read_b','benefit.base_add.all',value('em',1),owner='b',domain='provider'))
    classify(m,'to_atk','a','unknown'); classify(m,'to_b_em','a','included')
    out=run(t,r,m)
    assert damages(out)==[117]
    assert out['result']['hits'][0]['attribute_inputs']=={'scaling_stat':'atk','scaling':200,'em':200}
    assert any(x.get('value')==60 and x['query']['actor']=='b' and x['query']['stat']=='em' for x in out['attribute_ledger']['receipts'])


def test_percentage_ratio_clamp_base_receipt_and_core_are_not_double_applied():
    t,r,m=setup()
    add(m,rule('percent','benefit.stat_pct.atk',value(factor=.002,max=.3),domain='provider'))
    classify(m,'percent','a','unknown')
    out=run(t,r,m)
    assert damages(out)==pytest.approx([51.75])
    effect=out['resolved_template']['buffs'][0]['effects'][0]
    assert effect['zone']=='stat_pct' and effect['value']==.3
    assert out['result']['hits'][0]['attribute_inputs']['scaling']==115
    rows=[c for x in out['attribute_ledger']['receipts'] for c in x['contributions'] if c.get('raw_evaluated_value')==.3]
    assert rows and all(c['evaluated_value']==15 and c['application_base_value']==50 for c in rows)


def test_unrelated_bad_b_closure_does_not_block_a_scaling():
    t,r,m=setup()
    add(m,rule('unrelated','benefit.stat_add.atk',value('atk',1),owner='b',domain='provider',
               beneficiaries={'mode':'self','scope':'locked'}))
    classify(m,'unrelated','b','unknown')
    assert damages(run(t,r,m))==[45]


def test_actual_current_cross_stat_cycle_returns_precise_pending():
    t,r,m=setup()
    add(m,rule('atk','benefit.stat_add.atk',value(),domain='provider'),
        rule('em','benefit.em.all',value('atk',.2),domain='provider'))
    classify(m,'atk','a','unknown'); classify(m,'em','a','included')
    out=execute_mechanics(t,r,m)
    assert out['status']=='pending' and out['result'] is None
    assert any(x['code']=='attribute_dependency_cycle' for x in out['issues'])


def test_missing_base_is_precise_and_only_blocks_percentage():
    t,r,m=setup()
    del t['characters'][0]['stats']['base_atk']
    add(m,rule('flat','benefit.stat_add.atk',20,domain='provider'))
    classify(m,'flat','a','unknown')
    assert damages(run(t,r,m))==[54]
    m['rules'][0]['effects'][0]['benefit_type_id']='benefit.stat_pct.atk'
    out=execute_mechanics(t,r,m)
    assert out['result'] is None and any(x['code']=='attribute_basis_missing' for x in out['issues'])


def test_base_atk_alias_reads_immutable_base_not_buffed_total():
    t,r,m=setup()
    add(m,rule('atk','benefit.stat_add.atk',100,domain='provider'),
        rule('read_base','benefit.base_add.all',value('base_atk',1),domain='provider'))
    classify(m,'atk','a','unknown')
    out=run(t,r,m)
    assert damages(out)==[112.5]
    assert any(x['query']['stat']=='atk' and x['query']['basis']=='base' and x.get('value')==50 for x in out['attribute_ledger']['receipts'])


@pytest.mark.parametrize('reverse', [False, True])
def test_same_phase_trigger_and_current_atk_reads_keep_common_prestate(reverse):
    t,r,m=setup()
    sources=[rule('gain','benefit.stat_add.atk',200,owner='b',beneficiaries={'mode':'self','scope':'locked'}),
        rule('trigger','benefit.stat_add.atk',value('atk',.2,sample='trigger'),owner='b',domain='provider',
             beneficiaries={'mode':'characters','characters':['a'],'scope':'locked'}),
        rule('current','benefit.stat_add.atk',value('atk',.1),owner='b',domain='provider',
             beneficiaries={'mode':'characters','characters':['a'],'scope':'locked'})]
    add(m,*(reversed(sources) if reverse else sources))
    for rid in ('gain','trigger','current'):
        classify(m,rid,'b','unknown')
    original=copy.deepcopy((t,r,m))
    out=run(t,r,m)
    assert damages(out)==[81]  # 100 + old 200*.2 + current 400*.1
    rows={x['rule_id']:x for x in out['sampling_trace'] if x['cycle']==0}
    assert rows['trigger']['source_value']==200 and rows['current']['source_value']==400
    assert rows['trigger']['attribute_receipt_id']!=rows['current']['attribute_receipt_id']
    assert (t,r,m)==original


def test_independent_trigger_layers_keep_distinct_atk_input_frames():
    t,r,m=setup((.2,.4,.6))
    condition(m,'increase',.3)
    add(m,rule('gain','benefit.stat_add.atk',200,owner='b',beneficiaries={'mode':'self','scope':'locked'},
               trigger={'event':'condition','phase':'pre','event_ids':['increase']}),
        rule('share','benefit.stat_add.atk',value('atk',.2,sample='trigger'),owner='b',domain='provider',
             beneficiaries={'mode':'characters','characters':['a'],'scope':'locked'},
             trigger={'event':'hit','phase':'pre','event_ids':['h0','h1']},
             stacking={'max':2,'add':1,'policy':'independent'}))
    classify(m,'gain','b','unknown'); classify(m,'share','b','unknown')
    out=run(t,r,m)
    assert damages(out)==[63,99,99]
    rows=[x for x in out['sampling_trace'] if x['rule_id']=='share' and x['cycle']==0 and x['event_id']=='h1']
    assert [x['value'] for x in rows]==[40,80]
    assert len({x['attribute_receipt_id'] for x in rows})==2


@pytest.mark.parametrize('domain,damage', [('self',99),('provider',45)])
def test_expired_self_source_may_read_later_event_but_provider_cannot_borrow_old_eligibility(domain,damage):
    t,r,m=setup((.3,))
    r['actions'][0]['occupation_end']=.4
    m['snapshot_captures']=[capture(offset=.2)]
    m['hit_events'][0]['self_snapshot_event']='cast'
    condition(m,'increase',.1)
    add(m,rule('share','benefit.stat_add.atk',value('atk',.3,sample='event',event_id='sample'),
               owner='b',domain=domain,duration=.05,
               beneficiaries={'mode':'characters','characters':['a'],'scope':'locked'}),
        rule('gain','benefit.stat_add.atk',200,owner='b',beneficiaries={'mode':'self','scope':'locked'},
             trigger={'event':'condition','phase':'pre','event_ids':['increase']}))
    classify(m,'share','b','unknown'); classify(m,'gain','b','unknown')
    out=run(t,r,m)
    assert damages(out)==[damage]
    if domain=='self':
        top=next(x for x in out['attribute_ledger']['receipts'] if x['query']['purpose']=='damage_projection')
        row=next(x for x in top['contributions'] if x['decision']=='included')
        inp=next(x for x in out['attribute_ledger']['receipts'] if x['receipt_id']==row['input_receipt_id'])
        assert inp['query']['frame']['observation']['event_id']=='sample'
        assert row['projection_of']['frame_id']==top['query']['frame']['context']['self_frame_id']


def test_missing_atk_event_read_keeps_pending_prefix_not_current_fallback():
    t,r,m=setup((.05,.2))
    m['snapshot_captures']=[capture(offset=.1)]
    add(m,rule('share','benefit.stat_add.atk',value('atk',.2,sample='event',event_id='sample'),
               owner='b',domain='provider',beneficiaries={'mode':'characters','characters':['a'],'scope':'locked'}))
    classify(m,'share','b','unknown')
    out=execute_mechanics(t,r,m)
    assert out['status']=='pending' and out['result'] is None
    assert any(x['code'] in ('attribute_frame_missing','snapshot_event_unexecuted') for x in out['issues'])
    assert out['rotation_check']['event_log'][-1]['status']=='pending'


def test_self_pct_locks_ratio_to_old_frame_but_uses_recipient_base_once():
    t,r,m=setup((.3,))
    m['hit_events'][0]['self_snapshot_event']='cast'
    condition(m,'increase',.1)
    add(m,rule('share','benefit.stat_pct.atk',value('em',.001),owner='b',domain='self',
               beneficiaries={'mode':'characters','characters':['a'],'scope':'locked'}),
        rule('gain','benefit.em.all',200,owner='b',beneficiaries={'mode':'self','scope':'locked'},
             trigger={'event':'condition','phase':'pre','event_ids':['increase']}))
    classify(m,'share','b','unknown'); classify(m,'gain','b','included')
    out=run(t,r,m)
    assert damages(out)==[45.45]  # b at old frame=20EM -> .02 * a BASE50 =1
    rows=[x for rec in out['attribute_ledger']['receipts'] for x in rec['contributions']
          if x.get('raw_evaluated_value')==.02]
    assert rows and all(x['application_base_value']==50 and x['evaluated_value']==1 for x in rows)


def test_manual_pct_and_flat_share_canonical_source_and_core_projection():
    t,r,m=setup()
    t['buffs']=[{'id':'manual','start':0,'end':1,'scope':'team','target':'a','extensions':{'owner':'a'},'effects':[
        {'id':'flat','benefit_type_id':'benefit.stat_add.atk','zone':'stat_add','stat':'atk','value':20,'applies_to':{'mode':'all'}},
        {'id':'pct','benefit_type_id':'benefit.stat_pct.atk','zone':'stat_pct','stat':'atk','value':.2,'applies_to':{'mode':'all'}}]}]
    for eid in ('flat','pct'):
        m['attribute_model']['effect_classifications'].append({
            'effect_ref':{'kind':'manual_buff','buff_id':'manual','effect_id':eid},
            'component':{'source_fingerprint':'manual','provider':'a','part_id':'manual','effect_id':eid},
            'nonextra_inclusion':'unknown','status':'assumed','source':'Synthetic'})
    out=run(t,r,m)
    assert damages(out)==[58.5]
    assert out['result']['hits'][0]['attribute_inputs']['scaling']==130


@pytest.mark.parametrize('matched', [False,True])
def test_filters_do_not_get_waived_by_new_stat_projection(matched):
    t,r,m=setup()
    add(m,rule('limited','benefit.stat_add.atk',20,domain='provider'))
    m['rules'][0]['effects'][0]['filter']={'event':'hit','phase':'pre','damage_type_ids':[
        'damage.elemental_skill' if matched else 'damage.elemental_burst']}
    classify(m,'limited','a','unknown')
    out=execute_mechanics(t,r,m)
    if matched:
        assert out['status']=='pending' and out['result'] is None
        assert any(x['code']=='attribute_context_unsupported' for x in out['issues'])
    else:
        assert damages(out)==[45]


def test_static_included_atk_component_cannot_be_added_again():
    t,r,m=setup()
    add(m,rule('gain','benefit.stat_add.atk',20,domain='provider'))
    classify(m,'gain','a','unknown')
    basis=next(x for x in m['attribute_model']['initial_basis'] if x['actor']=='a' and x['stat']=='atk')
    basis['included_components']=[{'component':copy.deepcopy(m['attribute_model']['effect_classifications'][0]['component']),
        'value':20,'nonextra_inclusion':'unknown'}]
    out=execute_mechanics(t,r,m)
    assert out['result'] is None and any(x['code']=='attribute_duplicate_delivery' for x in out['issues']),out['issues']


@pytest.mark.parametrize('which', ['registration','output'])
def test_multistat_uses_one_execution_budget_and_keeps_actual_prefix(monkeypatch,which):
    t,r,m=setup()
    add(m,rule('gain','benefit.stat_add.atk',20,domain='provider'))
    classify(m,'gain','a','unknown')
    if which=='registration':
        m['execution_options'] = {'schema_version':1, 'attribute_limits':{'frames':1}}
    else:
        m['execution_options'] = {'schema_version':1, 'attribute_limits':{'output_bytes':2000}}
    out=execute_mechanics(t,r,m)
    assert out['status']=='pending' and out['result'] is None
    assert any('limit' in x['code'] for x in out['issues'])


def test_unrelated_finite_atk_overflow_is_lazy_but_actual_read_is_invalid():
    t,r,m=setup()
    for rid in ('huge1','huge2'):
        add(m,rule(rid,'benefit.stat_add.atk',1e308,owner='b',beneficiaries={'mode':'self','scope':'locked'}))
        classify(m,rid,'b','unknown')
    assert damages(run(t,r,m))==[45]
    add(m,rule('read','benefit.base_add.all',value('atk',.1),owner='b',domain='provider'))
    out=execute_mechanics(t,r,m)
    assert out['result'] is None and any(x['code']=='attribute_value_invalid' for x in out['issues'])


def test_swirl_reads_cross_stat_closure_at_reaction_created_not_parent_post():
    from tests.test_swirl_dynamic_v3 import check_sample_projection, sampled
    from tests.test_swirl_dynamic_v3 import setup as swirl_setup
    t,r,m=swirl_setup((.2,.3))
    second_character(t,r)
    m['attribute_model']=setup()[2]['attribute_model']
    add(m,rule('share','benefit.em.all',value('atk',.2),owner='b',domain='provider',
               beneficiaries={'mode':'characters','characters':['a'],'scope':'locked'}),
        rule('gain','benefit.stat_add.atk',200,owner='b',beneficiaries={'mode':'self','scope':'locked'},
             trigger={'event':'hit','phase':'post','event_ids':['h0']}))
    classify(m,'share','b','included'); classify(m,'gain','b','unknown')
    out=run(t,r,m)
    assert [x['value'] for x in sampled(out)]==[40,80]
    check_sample_projection(out)


def test_known_nonattribute_energy_benefit_is_not_misclassified_as_unknown_registry_entry():
    t,r,m=setup()
    add(m,rule('recharge','benefit.energy_recharge.all',.2))
    assert damages(run(t,r,m))==[45]


def test_explicit_base_atk_conflicting_basis_is_rejected():
    t,r,m=setup()
    add(m,rule('read','benefit.base_add.all',value('base_atk',1,basis='total')))
    out=execute_mechanics(t,r,m)
    assert out['result'] is None and any(x['code']=='attribute_basis_scope' for x in out['issues'])


def test_default_core_and_legacy_native_do_not_gain_new_audit_fields():
    from genshin_engine.core import calculate
    t,r,m=fixture((.2,))
    assert 'attribute_inputs' not in calculate(t)['hits'][0]
    assert 'attribute_inputs' not in run(t,r,m)['result']['hits'][0]


@pytest.mark.parametrize('system', ['hp_classic','transformative'])
def test_atk_not_consumed_by_current_damage_formula_does_not_force_its_bad_closure(system):
    t,r,m=setup()
    if system=='hp_classic':
        t['characters'][0]['stats']['hp']=1000
        t['hits'][0]['scaling_stat']='hp'
    else:
        t['hits'][0].update(damage_system='transformative',reaction={'type':'swirl'})
        t['hits'][0].pop('multiplier')
    add(m,rule('atk_cycle','benefit.stat_add.atk',value('atk',1),domain='provider'))
    classify(m,'atk_cycle','a','unknown')
    out=run(t,r,m)
    assert all(x['query']['stat']!='atk' for x in out['attribute_ledger']['receipts'])
    assert 'atk_cycle' not in [x['rule_id'] for x in out['sampling_trace']]
    if system=='hp_classic':
        assert damages(out)==[450]


def test_unconsumed_atk_output_still_participates_in_real_em_input_dependency():
    t,r,m=setup()
    t['hits'][0].update(damage_system='transformative',reaction={'type':'swirl'})
    t['hits'][0].pop('multiplier')
    add(m,rule('atk_cycle','benefit.stat_add.atk',value('atk',1),domain='provider'),
        rule('em_reads_atk','benefit.em.all',value('atk',.2),domain='provider'))
    classify(m,'atk_cycle','a','unknown'); classify(m,'em_reads_atk','a','included')
    out=execute_mechanics(t,r,m)
    assert out['result'] is None and any(x['code']=='attribute_dependency_cycle' for x in out['issues'])


def test_audit_uses_reported_origin_not_later_checked_cycle_same_stable_hit_id():
    t,r,m=setup()
    m['manual_buff_repeat']='once'
    t['buffs']=[{'id':'first_only','start':0,'end':1,'scope':'team','target':'a','extensions':{'owner':'a'},'effects':[
        {'id':'flat','benefit_type_id':'benefit.stat_add.atk','zone':'stat_add','stat':'atk','value':100,'applies_to':{'mode':'all'}}]}]
    m['attribute_model']['effect_classifications'].append({
        'effect_ref':{'kind':'manual_buff','buff_id':'first_only','effect_id':'flat'},
        'component':{'source_fingerprint':'first_only','provider':'a','part_id':'first_only','effect_id':'flat'},
        'nonextra_inclusion':'unknown','status':'assumed','source':'Synthetic'})
    out=run(t,r,m)
    assert damages(out)==[90]
    assert out['result']['hits'][0]['attribute_inputs']['scaling']==200
    assert any(x['query']['frame']['origin']>0 and x['query']['purpose']=='damage_projection' and x['value']==100
               for x in out['attribute_ledger']['receipts'])


def test_public_capabilities_expose_exact_native_stat_scope_without_source_authority():
    from genshin_engine.mechanics_schema import mechanics_capabilities
    cap=mechanics_capabilities()['attribute_ledger']
    assert cap['supported_contracts']==['attribute-ledger-v1','attribute-ledger-v2','attribute-ledger-v3','attribute-ledger-v4']
    assert cap['stats']==['em','atk','hp','def','energy_recharge']
    assert cap['native_schema']['oneOf'][1]['properties']['contract']['const']=='attribute-ledger-v3'
    assert cap['stat_graph']['basis']['atk']==['total','base']
    assert cap['stat_graph']['source_authority']=='none'
    assert cap['stat_graph']['runtime_integration']=='mechanics_explicit_attribute_model_v3_qualified_projection'
