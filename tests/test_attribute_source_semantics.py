"""Closed raw property/parameter/sample contracts; no game/source shortcuts."""
from __future__ import annotations

import copy

import pytest

from genshin_engine import runtime_passive_attribute_compiler as compiler
from genshin_runtime.attribute_source_semantics import verify_attribute_facts
from genshin_runtime.mechanism_semantics import (
    fingerprint,
    validate_fact,
    verify_mechanism_source,
)
from tests.test_passive_attribute_source import compile_source


def material():
    out = compile_source()['attribute_evidence']
    return {'facts': [r['fact'] for r in out['facts']], 'reviews': {
        r['fact']['id'] + '@' + str(r['fact']['revision']): r['review'] for r in out['facts']},
        **{k: out[k] for k in ('nodes', 'parameters', 'evidence')}}


def test_materialized_facts_have_exact_parameter_and_frozen_receipts():
    data = material()
    before = copy.deepcopy(data)
    out = verify_attribute_facts(**data)
    assert len(out['reads']) == 2
    assert (out['reads'][0]['value']['basis'], out['reads'][0]['value']['sample']) == ('nonextra', 'trigger')
    out['reads'][0]['value']['factor'] = 100
    assert data == before
    assert verify_attribute_facts(**data)['reads'][0]['value']['factor'] < .001


@pytest.mark.parametrize('kind,field,value', [
    ('attribute_read', 'basis', 'total'), ('attribute_read', 'stat', 'atk'),
    ('attribute_read', 'subject', 'recipient'), ('attribute_read', 'unit', 'ratio'),
    ('attribute_read', 'grammar', 'general-rpn'), ('attribute_read', 'schema_version', True),
    ('attribute_output', 'benefit_type_id', 'benefit.em.all'),
    ('attribute_output', 'domain', 'provider'), ('attribute_output', 'nonextra_inclusion', 'included'),
    ('attribute_sample', 'phase', 'pre'), ('attribute_sample', 'freeze_scope', 'damage_snapshot'),
    ('attribute_sample', 'sample', 'current'),
])
def test_changed_fact_meaning_is_not_accepted(kind, field, value):
    data = material()
    fact = next(f for f in data['facts'] if f['kind'] == kind)
    fact['claim'][field] = value
    with pytest.raises(ValueError):
        verify_attribute_facts(**data)


@pytest.mark.parametrize('mutation', ['token', 'append', 'wrong-node', 'wrong-param', 'param-unit',
    'param-bool', 'param-nan', 'pending', 'no-primary-test', 'duplicate', 'missing', 'unknown-field'])
def test_raw_identity_grammar_parameter_and_review_fail_closed(mutation):
    data = material()
    fact = next(f for f in data['facts'] if f['kind'] == 'attribute_read')
    if mutation in ('token', 'append'):
        node = data['nodes'][0]
        raw = node['payload']
        for part in fact['claim']['pointer'][1:].split('/'):
            raw = raw[part]
        if mutation == 'token':
            raw[1] = 'FIGHT_PROP_ELEMENT_MASTERY'
        else:
            raw.append('ADD')
        # Defeat the outer content digest; the complete expression fact still rejects.
        node['node_sha256'] = fingerprint(node['payload'])
        data['evidence'][0]['sha256'] = node['node_sha256']
    elif mutation == 'wrong-node':
        fact['claim']['node_id'] = 'unreviewed'
    elif mutation.startswith('param') or mutation == 'wrong-param':
        p = next(p for p in data['parameters'] if p['unit'] == 'ratio_per_em')
        if mutation == 'wrong-param':
            p['symbol'] += '_other'
        elif mutation == 'param-unit':
            p['unit'] = 'ratio'
        else:
            p['value'] = True if mutation == 'param-bool' else float('nan')
        if mutation != 'param-nan':
            p['fingerprint'] = fingerprint({k: v for k, v in p.items() if k != 'fingerprint'})
    elif mutation == 'pending':
        next(iter(data['reviews'].values()))['status'] = 'pending'
    elif mutation == 'no-primary-test':
        next(f for f in data['facts'] if f['kind'] == 'attribute_sample')['evidence_refs'] = ['ability']
    elif mutation == 'duplicate':
        data['facts'].append(copy.deepcopy(fact))
    elif mutation == 'missing':
        data['facts'].remove(fact)
    else:
        fact['claim']['ignore_me'] = 'unknown'
    with pytest.raises(ValueError):
        verify_attribute_facts(**data)


def test_caller_review_cannot_change_packaged_source_admission(monkeypatch):
    profile = copy.deepcopy(compiler._profile())
    next(iter(profile['reviews'].values()))['status'] = 'revoked'
    monkeypatch.setattr(compiler, '_profile', lambda: profile)
    out = compile_source()
    assert out['status'] == 'invalid'
    assert out['attribute_evidence'] is None and out['rules'] == []


def test_legacy_descriptor_cannot_acquire_new_attribute_semantics():
    from tests.test_mechanism_descriptor_compiler import case
    descriptor, args = case()
    fact = material()['facts'][0]
    assert validate_fact(fact) == fact  # Valid as a data fact, not executable on the old contract.
    args['facts'].append(fact)
    with pytest.raises(ValueError, match='descriptor'):
        verify_mechanism_source(descriptor, **args)
