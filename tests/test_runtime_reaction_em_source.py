"""Isolated source compilation; no App or source adoption authority."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from genshin_engine import runtime_reaction_em_compiler as candidate
from genshin_engine.runtime_compiler import _hash

HERE = Path(__file__).resolve().parent


def bundle():
    return json.loads((HERE / 'fixtures/runtime-reaction-em-source-current.json').read_text(encoding='utf8'))


def old_bundle():
    return json.loads((HERE / 'fixtures/runtime-reaction-em-source.json').read_text(encoding='utf8'))


def compile_value(data=None, scenario=None, **kw):
    return candidate.compile_reaction_em_source(data or bundle(), owner=kw.pop('owner', 'owner'),
        source_id='test:upstream', period=20, scenario=scenario or candidate.default_scenario(), **kw)


def accepted(data=None):
    scene = candidate.default_scenario()
    scene.update(status='confirmed', source='Synthetic scoped source proof; App qualification still required', target_id='enemy')
    first = compile_value(data, scene)
    scene['accepted_assumptions_fingerprint'] = first['required_confirmation']['accepted_assumptions_fingerprint']
    return compile_value(data, scene), scene


def rehash(data):
    data['content_fingerprint'] = _hash({k:v for k,v in data.items() if k != 'content_fingerprint'})


def test_actual_bundle_compiles_param_lineage_flat_em_and_no_execution_authority():
    result, _ = accepted()
    assert result['status'] == 'ready', result['issues']
    assert result['execution_authority'] == 'requires_actual_app_scope'
    assert result['order_authority'] == 'no_cross_listener_order'
    rule, = result['rules']
    assert rule['effects'] == [{'id':'value','benefit_type_id':'benefit.em.all','value':120,'domain':'provider'}]
    assert rule['timing']['duration'] == 8
    assert rule['trigger']['characters'] == ['owner'] and rule['trigger']['owner_presence'] == 'frontline'
    assert rule['stacking']['max'] == 1 and rule['stacking']['policy'] == 'refresh'
    assert result['static_parts'] == []
    assert result['attribute_writes'][0]['nonextra_inclusion'] == 'included'
    assert result['provenance']['parameters']['ElemMastery']['param_index'] == 1
    assert result['whole_source_status'] == 'partial'
    assert any(r['raw_reaction'] == 'StarSwirlIce' and r['status'] == 'excluded_by_scope' for r in result['branches'])
    assert result['execution_requirements']['actual_equipment_owner'] == 'app_must_verify'


def test_previous_accepted_pair_is_rejected_without_resigning():
    historical = old_bundle()
    original = copy.deepcopy(historical)
    result, _ = accepted(historical)
    assert result['status'] == 'pending'
    assert result['rules'] == [] and result['execution_fingerprint'] is None
    assert historical == original


@pytest.mark.parametrize('change', ['raw_reaction','attach_type','missing_reference','parameter','wrong_level',
                                   'wrong_snapshot','partial_body','new_listener','new_property','wrong_normalization'])
def test_mutated_source_never_delivers_even_rehashing_bundle(change):
    data = bundle()
    body = data['abilities'][0]['payload']
    modifiers = body['GPPBBDEICKP']
    handler = next(v for v in modifiers.values() if 'LFIJIMIMBIH' in v)
    listener = handler['LFIJIMIMBIH'][0]
    if change == 'raw_reaction': listener['HDOJNFJLKAP'].append('UnknownFuture')
    elif change == 'attach_type': listener['KHJMNJNEADF'][1]['$type'] = 'BINBHGLANKO'
    elif change == 'missing_reference': data['references'].pop()
    elif change == 'parameter': data['param_list'][1] = data['affix']['record']['paramList'][1] = 121
    elif change == 'wrong_level': data['source']['source_level'] = data['affix']['record']['level'] = 2
    elif change == 'wrong_snapshot': data['snapshot']['snapshot_id'] = 'a'*64
    elif change == 'partial_body': del body['EMGBJJOIENB']
    elif change == 'new_listener': handler['LFIJIMIMBIH'].append(copy.deepcopy(listener))
    elif change == 'new_property': next(v for v in modifiers.values() if 'ILKPFPGAIGI' in v)['ILKPFPGAIGI']['Actor_AttackDelta'] = 1
    else: data['normalization']['mapping_fingerprint'] = 'a'*64
    rehash(data)
    result, _ = accepted(data)
    assert result['status'] in ('invalid','pending') and result['rules'] == []
    assert result['execution_fingerprint'] is None


@pytest.mark.parametrize('state', ['pending','revoked','stale'])
def test_fact_or_profile_state_cannot_be_laundered_by_acceptance(monkeypatch,state):
    original = copy.deepcopy(candidate._profile())
    for path in ('profile','fact'):
        profile = copy.deepcopy(original)
        if path == 'profile': profile['review_status'] = state
        else: profile['facts']['nonextra_inclusion']['status'] = state
        monkeypatch.setattr(candidate, '_profile', lambda profile=profile: copy.deepcopy(profile))
        result, _ = accepted()
        assert result['status'] == 'pending' and result['rules'] == []
        assert any(i['code'] == 'source_fact_not_reviewed' for i in result['issues'])


def test_stale_fact_evidence_hash_and_changed_profile_fact_value_rejected(monkeypatch):
    original = copy.deepcopy(candidate._profile())
    for field, value in [('evidence_fingerprint','f'*64),('value','excluded')]:
        profile=copy.deepcopy(original)
        profile['facts']['nonextra_inclusion'][field]=value
        monkeypatch.setattr(candidate,'_profile',lambda profile=profile:copy.deepcopy(profile))
        result,_=accepted()
        assert result['rules']==[] and result['status']=='pending'


def test_source_only_acceptance_does_not_assert_owner_parent_or_no_competitor():
    result, scene=accepted()
    # Another owner changes the acceptance fingerprint but remains just source compilation.
    changed=compile_value(scenario=scene,owner='other')
    assert changed['status']=='pending' and changed['rules']==[]
    assert result['execution_requirements']['actual_reaction_provider']=='app_must_verify'
    assert result['execution_requirements']['final_callback_dependency_closure']=='app_must_verify'
    scene['callback_policy']='caller_claimed_safe'
    assert compile_value(scenario=scene)['rules']==[]


def test_returns_are_isolated_and_unknown_scenario_never_ready():
    result, scene=accepted()
    result['rules'][0]['effects'][0]['value']=999
    assert compile_value(scenario=scene)['rules'][0]['effects'][0]['value']==120
    scene['extra']=True
    assert compile_value(scenario=scene)['status']!='ready'
    assert compile_value()['status']=='pending'


@pytest.mark.parametrize('change', ['big','deep','nan','bool_owner','infinite_period'])
def test_input_limits_and_invalid_values_produce_bounded_atomic_failure(change):
    data=bundle()
    args={'bundle':data,'owner':'owner','source_id':'test:upstream','period':20,'scenario':candidate.default_scenario()}
    if change=='big': data['extra']='x'*(candidate.MAX_BYTES+1)
    elif change=='deep':
        value={}
        data['extra']=value
        for _ in range(70):
            value['next']={}
            value=value['next']
    elif change=='nan': data['param_list'][1]=float('nan')
    elif change=='bool_owner': args['owner']=True
    else: args['period']=float('inf')
    result=candidate.compile_reaction_em_source(**args)
    assert result['status']=='invalid' and result['rules']==[] and result['execution_fingerprint'] is None
    assert len(json.dumps(result))<32768


def test_output_limit_discards_rules_and_classifications_atomically(monkeypatch):
    _,scene=accepted()
    data=bundle()
    raw_size=len(json.dumps([data,'owner','test:upstream',scene,20],ensure_ascii=False).encode())
    monkeypatch.setattr(candidate,'MAX_BYTES',raw_size+128)
    result=compile_value(data,scene)
    assert result['status']=='invalid' and result['issues'][0]['code']=='output_limit'
    assert result['rules']==result['attribute_writes']==result['parts']==[]
    assert result['execution_fingerprint'] is None


def prepared_queue(times=(.2,.3,8.3), *, owner='a'):
    from tests.test_attribute_graph_runtime import classify, enable_graph
    from tests.test_swirl_dynamic_v3 import setup

    t,r,m=setup(times)
    enable_graph(t,r,m,a=0,b=0)
    scene=candidate.default_scenario()
    scene.update(status='assumed',source='Engineering queue test; no actual App source authority',target_id='enemy')
    before=compile_value(scenario=scene,owner=owner)
    scene['accepted_assumptions_fingerprint']=before['required_confirmation']['accepted_assumptions_fingerprint']
    result=compile_value(scenario=scene,owner=owner)
    assert result['status']=='ready',result['issues']
    m['rules'].extend(copy.deepcopy(result['rules']))
    m['initial_state']['rules'].extend(copy.deepcopy(result['initial_rules']))
    classify(m,result['rules'][0]['id'],owner,'included')
    return t,r,m


def test_real_queue_parent_uses_old_em_refresh_single_layer_and_half_open_expiry():
    from genshin_engine.mechanics import execute_mechanics
    from tests.test_swirl_dynamic_v3 import check_sample_projection, sampled

    values=prepared_queue()
    out=execute_mechanics(*values)
    assert out['result'] is not None,out['issues']
    assert [x['value'] for x in sampled(out)] == [0,120,0]
    check_sample_projection(out)
    # Every directly reported parent retains its ordinary 100 ATK damage.
    parents=[x for x in out['result']['hits'] if x['damage_system']=='classic']
    assert [h['expected'] for h in parents] == [45,45,45]


def test_real_queue_offfield_source_cannot_refresh_but_granted_em_persists():
    from genshin_engine.mechanics import execute_mechanics
    from tests.test_mechanics_execution import ability, action
    from tests.test_swirl_dynamic_v3 import sampled

    t,r,m=prepared_queue((.2,.3,8.2))
    r['abilities'].append(ability('switch_b','a','action.switch'))
    r['actions'].append(action('leave','switch_b',.25,.26,20,switch_to='b',repeat={'mode':'once'}))
    out=execute_mechanics(t,r,m)
    assert out['result'] is not None,out['issues']
    assert [x['value'] for x in sampled(out)] == [0,120,0]


def test_real_queue_other_reaction_provider_never_procs_this_source():
    from genshin_engine.mechanics import execute_mechanics
    from tests.test_swirl_dynamic_v3 import sampled

    out=execute_mechanics(*prepared_queue((.2,.3),owner='b'))
    assert out['result'] is not None,out['issues']
    assert [x['value'] for x in sampled(out)] == [0,0]
