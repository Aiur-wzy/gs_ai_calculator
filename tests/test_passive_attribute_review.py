"""Independent database attribute projection checks; no game authority claims."""
from __future__ import annotations

import copy

import pytest

from genshin_engine import runtime_passive_attribute_compiler as compiler
from genshin_engine.mechanics import execute_mechanics
from genshin_runtime.attribute_source_semantics import verify_attribute_facts
from genshin_runtime.mechanism_semantics import fingerprint
from tests.test_attribute_source_semantics import material
from tests.test_passive_attribute_runtime import case
from tests.test_passive_attribute_source import compile_source
from tests.test_passive_benefits_review import (
    compile_source as legacy_compile,
)
from tests.test_passive_benefits_review import (
    profile,
    rehash,
)


@pytest.mark.parametrize('status', ['pending', 'revoked', 'stale'])
def test_packaged_fact_status_change_cannot_be_overridden_by_source_confirmation(monkeypatch, status):
    package = copy.deepcopy(compiler._profile())
    next(iter(package['reviews'].values()))['status'] = status
    monkeypatch.setattr(compiler, '_profile', lambda: package)
    out = compile_source()
    assert out['status'] != 'ready' and out['rules'] == [] and out['attribute_evidence'] is None
    assert legacy_compile()['status'] == 'ready'


@pytest.mark.parametrize('mutation', ['source-pending', 'frontier', 'bad-talent-param', 'snapshot'])
def test_full_parent_changes_cannot_be_authorized_by_rehash(mutation):
    data = profile()
    if mutation == 'source-pending':
        data['source_status'] = 'pending'
    elif mutation == 'frontier':
        data['source_bundle']['frontier'] = [{'unresolved': 'parameter'}]
    elif mutation == 'snapshot':
        data['source_bundle']['snapshot']['snapshot_id'] = 'f'*64
    else:
        # Remove a real parameter writer rather than changing the final coefficient.
        data['source_bundle']['talent']['operations'] = []
    out = compile_source(rehash(data))
    assert out['status'] != 'ready' and out['parts'] == out['rules'] == []
    assert out['attribute_evidence'] is None


def test_pure_consistency_receipt_cannot_mutate_packaged_source_authority():
    baseline = compile_source()
    data = material()
    for review in data['reviews'].values():
        review['reviewer'] = 'Caller supplied consistency claim'
    proof = verify_attribute_facts(**data)
    assert all(r['review']['reviewer'] == 'Caller supplied consistency claim' for r in proof['facts'])
    assert compile_source() == baseline
    assert 'execution_fingerprint' not in proof and 'rules' not in proof
    assert baseline['gameplay_semantics_status'] == 'declared_not_verified'
    assert baseline['coverage_certificate']['actual_parent_eligibility'] == 'checked_by_app'


def test_parameter_complete_parent_digest_is_checked_even_with_local_rehash():
    data = material()
    p = data['parameters'][0]
    p['raw_parent_hashes']['promote'] = 'f'*64
    p['fingerprint'] = fingerprint({k: v for k, v in p.items() if k != 'fingerprint'})
    with pytest.raises(ValueError, match='父节点'):
        verify_attribute_facts(**data)


def test_runtime_requires_real_nonextra_seed_without_total_fallback():
    t, r, m = case()
    del m['attribute_model']['initial_basis'][0]['nonextra']
    out = execute_mechanics(t, r, m)
    assert out['result'] is None and out['status'] == 'pending'
    assert any(i['code'] == 'attribute_basis_missing' and i['path'] == 'initials.a.em.nonextra'
               for i in out['issues'])
