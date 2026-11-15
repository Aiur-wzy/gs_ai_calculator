"""Versioned database NONEXTRA source oracles, written before implementation."""
from __future__ import annotations

import copy
import importlib

import pytest

from tests.test_passive_benefits_review import compile_source as legacy_compile
from tests.test_passive_benefits_review import profile, rehash


def compiler():
    return importlib.import_module('genshin_engine.runtime_passive_attribute_compiler')


def scenario():
    value = compiler().passive_attribute_compiler_caps()['default_scenario']
    value.update(target_id='enemy', origin_target_range='within_source_range',
        recipient_range='all_party_within_source_range', initial_source_effects='none',
        recipient_bonus_policy='not_included', stat_basis='attribute_ledger_nonextra',
        same_proc_em_policy='no_same_proc_em_changes',
        event_order_policy='declared_atomic_hit_order', status='assumed',
        source='Synthetic single original target; actual ability parents are an App obligation')
    return value


def compile_source(data=None, scope=None):
    return compiler().compile_passive_attribute_source(profile() if data is None else data,
        owner='a', source_id='attribute-source', scenario=scenario() if scope is None else scope)


def test_new_source_keeps_nonextra_and_sample_domains_explicit():
    before = legacy_compile()
    out = compile_source()
    assert out['status'] == 'ready', out['issues']
    assert out['compiler'] == 'database-inherent-swirl-attribute-v2'
    assert out['execution_binding']['ruleset'] == 'gauge-swirl-dynamic-v3'
    assert out['execution_binding']['attribute_contract'] == 'attribute-ledger-v2'
    assert {r['trigger']['reaction_elements'][0] for r in out['rules']} == {'hydro', 'cryo'}
    for rule in out['rules']:
        effect = rule['effects'][0]
        assert effect['domain'] == 'self'
        assert effect['value'] == {'kind': 'stat', 'subject': 'owner', 'stat': 'em',
            'basis': 'nonextra', 'factor': .00039999998989515007, 'sample': 'trigger'}
    proof = out['attribute_evidence']
    assert proof['contract'] == 'database-attribute-source-v1'
    assert proof['required_contract'] == 'attribute-ledger-v2'
    assert len(proof['reads']) == 2
    assert proof['writers'] == []
    assert len(proof['facts']) == 6
    assert all(x['basis'] == 'nonextra' and x['provider'] == 'a' for x in proof['reads'])
    assert all('proud_level' in x['source_selection'] for x in proof['parameters'])
    assert legacy_compile() == before


@pytest.mark.parametrize('field', ['origin_target_range', 'recipient_range',
    'initial_source_effects', 'recipient_bonus_policy', 'stat_basis', 'event_order_policy', 'same_proc_em_policy'])
def test_new_source_unknown_scene_cannot_be_confirmed_away(field):
    scope = scenario()
    scope[field] = 'unknown'
    scope['status'] = 'confirmed'
    out = compile_source(scope=scope)
    assert out['status'] in ('pending', 'invalid')
    assert out['rules'] == out['parts'] == []


@pytest.mark.parametrize('mutation', ['level', 'parameters', 'raw-stat', 'extra-control'])
def test_rehashed_source_changes_cannot_reuse_old_approval(mutation):
    data = profile()
    if mutation == 'level':
        data['selection']['proud_level'] += 1
        data['source_bundle']['source_identity']['proud_level'] += 1
    elif mutation == 'parameters':
        data['source_bundle']['param_list'][0] = .04
        data['source_bundle']['parameter_record']['record']['paramList'][0] = .04
        data['entry_records']['proud_skill']['record']['paramList'][0] = .04
    else:
        body = data['source_bundle']['abilities'][0]['payload']
        if mutation == 'extra-control':
            body['new_callback'] = {'effect': 'unreviewed'}
        else:
            def alter(value):
                if isinstance(value, list):
                    for i, x in enumerate(value):
                        if x == 'FIGHT_PROP_NONEXTRA_ELEMENT_MASTERY':
                            value[i] = 'FIGHT_PROP_ELEMENT_MASTERY'
                        else:
                            alter(x)
                elif isinstance(value, dict):
                    for x in value.values():
                        alter(x)
            alter(body)
    out = compile_source(rehash(data))
    assert out['status'] in ('pending', 'invalid')
    assert out['rules'] == out['parts'] == []


def test_new_source_does_not_mutate_source_or_scenario():
    data, scope = profile(), scenario()
    before = copy.deepcopy((data, scope))
    assert compile_source(data, scope)['status'] == 'ready'
    assert (data, scope) == before
