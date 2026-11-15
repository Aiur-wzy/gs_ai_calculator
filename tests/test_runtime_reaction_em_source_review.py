"""Independent off-tree source boundaries; no App authority or DB writes."""
import copy

import pytest
from test_runtime_reaction_em_source import accepted, bundle, compile_value

from genshin_engine import runtime_reaction_em_compiler as candidate


@pytest.mark.parametrize('field,value', [('profile_id','unreviewed-profile'), ('profile_version',3)])
def test_profile_identity_cannot_be_relabelled_by_fresh_confirmation(monkeypatch,field,value):
    profile=copy.deepcopy(candidate._profile())
    profile[field]=value
    monkeypatch.setattr(candidate,'_profile',lambda:copy.deepcopy(profile))
    result,_=accepted()
    assert result['status']!='ready' and result['rules']==[]

@pytest.mark.parametrize('failure', ['load','missing_evidence','missing_parent'])
def test_broken_packaged_profile_returns_bounded_invalid(monkeypatch,failure):
    if failure=='load':
        def load(): raise ValueError('invalid packaged profile JSON')
        monkeypatch.setattr(candidate,'_profile',load)
    else:
        profile=copy.deepcopy(candidate._profile())
        del profile['evidence' if failure=='missing_evidence' else 'parent_evidence']
        monkeypatch.setattr(candidate,'_profile',lambda:copy.deepcopy(profile))
    result=compile_value()
    assert result['status']=='invalid' and result['rules']==[]
    assert result['execution_fingerprint'] is None and result['issues']

@pytest.mark.parametrize('field',['single_player_fixed','all_recipients_alive','single_unique_provider'])
def test_numeric_flags_do_not_supply_scene_proof(field):
    _,scene=accepted()
    scene[field]=1
    first=compile_value(scenario=scene)
    scene['accepted_assumptions_fingerprint']=first['required_confirmation']['accepted_assumptions_fingerprint']
    result=compile_value(scenario=scene)
    assert result['status']!='ready' and result['rules']==[]

def test_cycle_input_controlled_and_no_partial_output():
    data=bundle(); data['cycle']=data
    result=compile_value(data)
    assert result['status']=='invalid' and result['rules']==[]

def test_parent_target_change_invalidates_confirmed_scope():
    _,scene=accepted(); scene['target_id']='other'
    result=compile_value(scenario=scene)
    assert result['status']=='pending' and result['rules']==[]

def test_returned_output_isolated_from_next_compile():
    result,scene=accepted(); result['rules'][0]['effects'][0]['value']=999
    result['provenance']['source_facts']['nonextra_inclusion']['value']='excluded'
    new=compile_value(scenario=scene)
    assert new['rules'][0]['effects'][0]['value']==120
    assert new['attribute_writes'][0]['nonextra_inclusion']=='included'
