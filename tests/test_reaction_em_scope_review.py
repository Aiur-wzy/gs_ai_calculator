"""Independent C3 pre/post scope closure, not a complete App route."""
import copy

import pytest

from genshin_app.equipment.reaction_em_scope import (
    final_scope,
    scope_fingerprint,
    source_scope,
)
from tests.test_reaction_em_app_scope import fixture_scope


def test_binding_identity_uses_declared_binding_id_not_python_object_alias():
    p,_,binding,req,_=fixture_scope()
    assert source_scope(p,copy.deepcopy(binding),req)[0]==[]


@pytest.mark.parametrize('change',['value','duration','provider'])
def test_final_generated_rule_must_match_fresh_source_not_only_same_id(change):
    p,t,b,_,compiled=fixture_scope()
    live=p['mechanics_model']['rules'][0]
    if change=='value':live['effects'][0]['value']=999
    elif change=='duration':live['timing']['duration']=99
    else:live['owner']='b'
    assert final_scope(p,t,b,compiled)[0]


@pytest.mark.parametrize('field',['provider','effect_id','part_id'])
def test_semantic_classification_identity_changes_fingerprint(field):
    p,t,b,_,compiled=fixture_scope()
    baseline=scope_fingerprint(final_scope(p,t,b,compiled)[1])
    p['mechanics_model']['attribute_model']['effect_classifications'][0]['component'][field]='different'
    assert scope_fingerprint(final_scope(p,t,b,compiled)[1])!=baseline


def test_immutable_base_reader_does_not_acquire_dynamic_em_approval():
    p,t,b,_,compiled=fixture_scope()
    reader=copy.deepcopy(p['mechanics_model']['rules'][0]);reader['id']='base-reader'
    reader['effects'][0]['benefit_type_id']='benefit.base_add.all'
    reader['effects'][0]['value']={'kind':'stat','subject':'owner','stat':'em','basis':'base','factor':1,'sample':'current'}
    p['mechanics_model']['rules'].append(reader)
    assert final_scope(p,t,b,compiled)[0]==[]
    reader['effects'][0]['value']['basis']='nonextra'
    assert final_scope(p,t,b,compiled)[0]

@pytest.mark.parametrize('change',['missing','duplicate','trigger','domain','benefit','new_effect','resource','source_id'])
def test_complete_final_rule_identity_remains_exact_to_new_compile(change):
    p,t,b,_,compiled=fixture_scope();rules=p['mechanics_model']['rules'];live=rules[0]
    if change=='missing':rules.clear()
    elif change=='duplicate':rules.append(copy.deepcopy(live))
    elif change=='trigger':live['trigger']['owner_presence']='any'
    elif change=='domain':live['effects'][0]['domain']='self'
    elif change=='benefit':live['effects'][0]['benefit_type_id']='benefit.stat_add.atk'
    elif change=='new_effect':live['effects'].append({'id':'hidden','benefit_type_id':'benefit.em.all','value':120,'domain':'provider'})
    elif change=='resource':live['resources']=[{'operation_id':'hidden'}]
    else:live['source_id']='other-source'
    assert final_scope(p,t,b,compiled)[0]
