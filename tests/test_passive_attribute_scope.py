"""Database sampling qualification must not borrow graph arithmetic authority."""
import copy

from genshin_app.equipment.passive_attribute_scope import inspect_nonextra_sampling
from tests.test_attribute_app_sources import attribute_plan


def probe(*,phase='pre',event='condition',extra=False,unknown=False,value=50,owner='a'):
    plan=attribute_plan();rule=plan['mechanics_model']['rules'][0]
    rule['owner']=owner;rule['beneficiaries']={'mode':'self'}
    rule['effects'][0]['value']=value;rule['trigger'].update(event=event,phase=phase)
    ref={'kind':'rule','rule_id':rule['id'],'effect_id':rule['effects'][0]['id']}
    plan['mechanics_model']['attribute_model']['effect_classifications']=[{
        'effect_ref':ref,'status':'pending' if unknown else 'confirmed','nonextra_inclusion':'excluded' if extra else 'included',
        'component':{'provider':owner,'part_id':rule['source_id'],'effect_id':rule['effects'][0]['id'],'source_fingerprint':'f'*64},
        'source':'工程分类'}]
    before=copy.deepcopy(plan)
    out=inspect_nonextra_sampling(plan,plan['manual_template'],'a')
    assert plan==before
    return out


def test_constant_earlier_dynamic_writer_is_supported_but_post_proc_is_pending():
    assert not probe()['issues']
    for event in ('hit','elemental_reaction'):
        out=probe(event=event,phase='post')
        assert out['issues'][0]['code']=='attribute_sampling_causality_pending'


def test_extra_share_is_pruned_only_with_known_classification():
    expression={'kind':'stat','stat':'em','subject':'owner','sample':'current','basis':'total','factor':.2}
    assert not probe(extra=True,value=expression)['issues']
    assert probe(extra=True,unknown=True,value=expression)['issues']
    assert probe(value=expression)['issues']


def test_unrelated_actor_writer_does_not_block_provider():
    assert not probe(owner='b',phase='post',event='hit')['issues']
