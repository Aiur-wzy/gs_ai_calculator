"""Independent L1 constant accounting and closed-request boundary review."""
import copy

import pytest

from genshin_engine.attribute_ledger import AttributeLedger


def declaration():
    return {'contract':'attribute-ledger-v1',
        'initials':[{'actor':'角色甲','stat':'em','total':300,'nonextra':100,'base':0,
                     'included_components':[],'source':'independent synthetic panel'}],
        'contributions':[],
        'frames':[{'id':'f','origin':0,'run_id':'0:hit','event_id':'hit','phase':'pre',
                   'state_epoch':0,'t_us':200000,'contribution_ids':[]}]}


def node(ident='n',value=40,classification='included',**kw):
    return {'id':ident,'component':{'source_fingerprint':'review-source','provider':'b',
        'part_id':'em','effect_id':ident,'recipient':'角色甲'},'instance_id':'instance',
        'layer_id':'layer','stat':'em','value':value,'nonextra_inclusion':classification,
        'visibility':'attribute','constraints':[],**kw}


def attach(d,*nodes):
    d['contributions']+=list(nodes)
    d['frames'][0]['contribution_ids']+=[n['id'] for n in nodes]
    return d


def read(ledger,basis='total',frame='f'):
    return ledger.read_stat('角色甲','em',basis,frame)


def test_unknown_extra_tag_only_blocks_nonextra_and_explicit_base_remains_known():
    ledger=AttributeLedger(attach(declaration(),node(value=37,classification='unknown')))
    assert read(ledger)['value']==337
    unknown=read(ledger,'nonextra')
    assert unknown['status']=='pending' and 'value' not in unknown
    assert read(ledger,'base')['value']==0


@pytest.mark.parametrize('unsupported',[{'value':{'cycle':'unsupported'}},
                                      {'constraints':['resource_gate']},
                                      {'visibility':'damage_context'}])
def test_base_read_never_consumes_dynamic_context_or_expression(unsupported):
    ledger=AttributeLedger(attach(declaration(),node(**unsupported)))
    assert read(ledger,'base')['value']==0
    assert read(ledger)['status']=='pending'
    assert 'value' not in read(ledger)


def test_nonextra_prunes_explicitly_excluded_unknown_expression():
    ledger=AttributeLedger(attach(declaration(),node(value={'future':'not executable'},classification='excluded')))
    assert read(ledger,'nonextra')['value']==100
    assert read(ledger)['status']=='pending'


def test_same_component_independent_layer_values_stay_distinct():
    a=node('gain',20); b=copy.deepcopy(a)
    b.update(id='alias',layer_id='second',value=60)
    receipt=read(AttributeLedger(attach(declaration(),a,b)))
    assert receipt['value']==380
    assert len(receipt['contributions'])==2
    b['layer_id']=a['layer_id']
    rejected=read(AttributeLedger(attach(declaration(),a,b)))
    assert rejected['status']=='invalid' and 'value' not in rejected


def test_static_component_is_explanation_and_other_effect_same_source_is_allowed():
    d=declaration(); a=node('included',200,'excluded')
    d['initials'][0]['included_components']=[{k:copy.deepcopy(a[k]) for k in ('component','value','nonextra_inclusion')}]
    other=node('different',30,'excluded')
    ledger=AttributeLedger(attach(d,other))
    assert read(ledger)['value']==330
    assert read(ledger,'nonextra')['value']==100
    duplicated=copy.deepcopy(a); duplicated['id']='renamed-row'
    attach(d,duplicated)
    rejected=read(AttributeLedger(d))
    assert rejected['status']=='invalid' and 'value' not in rejected


def test_declaration_and_active_set_permutation_preserves_value_and_receipt_id():
    d=attach(declaration(),node('one',11),node('two',-7),node('three',23,'excluded'))
    changed=copy.deepcopy(d)
    changed['initials']=list(reversed(changed['initials']))
    changed['contributions']=list(reversed(changed['contributions']))
    changed['frames'][0]['contribution_ids'].reverse()
    changed=dict(reversed(list(changed.items())))
    a=AttributeLedger(d); b=AttributeLedger(changed)
    assert read(a)==read(b)
    assert read(a,'nonextra')==read(b,'nonextra')


def test_same_time_distinct_frames_and_mutations_do_not_rewrite_past_read():
    d=attach(declaration(),node(value=40))
    second=copy.deepcopy(d['frames'][0]); second.update(id='later',phase='post',state_epoch=1,contribution_ids=[])
    d['frames'].append(second)
    ledger=AttributeLedger(d); first=read(ledger)
    d['contributions'][0]['value']=9999
    first['value']=9999
    assert read(ledger)['value']==340
    assert read(ledger,frame='later')['value']==300
    assert read(ledger)['receipt_id']!=read(ledger,frame='later')['receipt_id']


def test_stable_sum_overflow_and_negative_final_never_publish_unknown_value():
    d=declaration(); d['initials'][0]['total']=1e16
    assert read(AttributeLedger(attach(d,node('minus',-1e16),node('tiny',1))))['value']==1
    for nodes in [(node('a',1e308),node('b',1e308)),(node('a',-301),)]:
        rejected=read(AttributeLedger(attach(declaration(),*nodes)))
        assert rejected['status']=='invalid' and 'value' not in rejected


def test_distinct_query_work_budget_is_not_reset_by_other_frame(monkeypatch):
    limits = {'query_work': 1}
    d=declaration(); second=copy.deepcopy(d['frames'][0]); second.update(id='later',state_epoch=1)
    d['frames'].append(second)
    ledger=AttributeLedger(d, limits=limits)
    assert read(ledger)['value']==300
    assert read(ledger)['value']==300
    later=read(ledger,frame='later')
    assert later['status']=='pending' and 'value' not in later
