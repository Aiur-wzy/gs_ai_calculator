"""Source closure for explicit callback assumptions, before App execution wiring."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.attribute_callbacks import prepare_attribute_callbacks
from genshin_app.equipment.files import context_view, fingerprint
from genshin_app.equipment.schema import PlanError
from tests.test_attribute_callback_runtime import callback_case
from tests.test_mechanics_execution import add, rule


def closure(model=None, *, excluded=(), pending=(), missing_delivery=(), reviews=None):
    model = copy.deepcopy(model if model is not None else callback_case()[2])
    identities = {r['id'] for r in model['rules']}
    source_active = identities - set(excluded) - set(pending)
    active = source_active - set(missing_delivery)
    # The core fixture has bare source IDs. App's already-validated linking
    # directory carries separate binding/part IDs rather than parsing a source.
    linked = {r['id']: ('fixture', r['id']) for r in model['rules']}
    reviews = reviews if reviews is not None else {
        r['source_id']: {'mode': 'manual_context', 'context_fingerprint': 'current', 'note': 'Explicit synthetic ordering'}
        for r in model['rules']}
    errors = []
    before = copy.deepcopy((model, source_active, active, linked, reviews))
    result = prepare_attribute_callbacks(model, source_active, active, linked, set(excluded),
        lambda *args: errors.append(args), source_reviews=reviews, context_fingerprint='current')
    assert (model, source_active, active, linked, reviews) == before
    return (*result, errors)


def test_absent_contract_is_a_noop():
    model = callback_case()[2]
    model.pop('attribute_callbacks')
    assert closure(model) == (None, None, [])


def test_reviewed_members_retain_explicit_order_and_assumed_authority():
    model = callback_case()[2]
    declaration, adoption, errors = closure(model)
    assert declaration == model['attribute_callbacks']
    assert errors == [] and adoption['status'] == 'ready'
    assert adoption['source_authority'] == 'caller_assumed_order_not_game_source_approval'
    assert adoption['declaration_fingerprint'] == fingerprint(declaration)
    declaration['members'].clear()
    assert len(model['attribute_callbacks']['members']) == 2


@pytest.mark.parametrize('excluded', [('ordinary',), ('share',)])
def test_one_excluded_member_preserves_new_contract_even_without_edges(excluded):
    declaration, adoption, errors = closure(excluded=excluded)
    assert declaration['members'] == list({'ordinary', 'share'} - set(excluded))
    assert declaration['edges'] == declaration['assumptions'] == []
    assert adoption['excluded_rule_ids'] == list(excluded)
    assert adoption['status'] == 'ready' and not errors


def test_all_explicitly_excluded_removes_execution_declaration_with_receipt():
    declaration, adoption, errors = closure(excluded=('ordinary', 'share'))
    assert declaration is None and adoption['status'] == 'excluded' and not errors
    assert all(r['own_source_status'] == r['effective_status'] == 'excluded' for r in adoption['members'])


def test_pending_and_excluded_is_order_independent_and_never_ready():
    model = callback_case()[2]
    first = closure(model, excluded=('ordinary',), pending=('share',))
    model['rules'].reverse()
    model['attribute_callbacks']['members'].reverse()
    second = closure(model, excluded=('ordinary',), pending=('share',))
    for declaration, adoption, errors in (first, second):
        assert adoption['status'] == 'pending' and errors
        assert declaration['members'] == ['share']
    assert first[1]['members'] == second[1]['members']


def test_prior_helper_removal_is_pending_not_an_exclusion():
    declaration, adoption, errors = closure(missing_delivery=('ordinary',))
    member = next(r for r in adoption['members'] if r['rule_id'] == 'ordinary')
    assert member['own_source_status'] == 'ready' and member['effective_status'] == 'pending'
    assert set(declaration['members']) == {'share', 'ordinary'}
    assert adoption['status'] == 'pending' and errors


@pytest.mark.parametrize('review', [None, {'mode': 'source_only'},
    {'mode': 'manual_context', 'context_fingerprint': 'old', 'note': 'Old source'},
    {'mode': 'manual_context', 'context_fingerprint': 'current', 'note': ' '}])
def test_identity_or_old_approval_cannot_approve_order(review):
    model = callback_case()[2]
    reviews = {r['source_id']: review for r in model['rules']}
    _, adoption, errors = closure(model, reviews=reviews)
    assert adoption['status'] == 'pending' and len(errors) == 2


def test_shared_assumption_survives_removing_only_one_dependent_edge():
    model = callback_case()[2]
    add(model, rule('third', 'benefit.em.all', 10, trigger={'event': 'hit', 'phase': 'post'},
                    beneficiaries={'mode': 'self', 'scope': 'locked'}))
    native = model['attribute_callbacks']
    native['members'].append('third')
    native['edges'].append({'before_rule_id': 'ordinary', 'after_rule_id': 'third', 'assumption_id': 'fixture-order'})
    declaration, adoption, errors = closure(model, excluded=('share',))
    assert len(declaration['edges']) == len(declaration['assumptions']) == 1
    assert not adoption['excluded_assumption_ids'] and not errors


@pytest.mark.parametrize('mutation', ['unknown_member', 'unknown_edge', 'unused_evidence', 'malformed_member'])
def test_exclusion_cannot_hide_invalid_original_graph(mutation):
    model = callback_case()[2]
    native = model['attribute_callbacks']
    if mutation == 'unknown_member': native['members'].append('ghost')
    elif mutation == 'unknown_edge': native['edges'][0]['after_rule_id'] = 'ghost'
    elif mutation == 'unused_evidence': native['assumptions'].append({'id': 'unused', 'note': 'No edge'})
    else: native['members'] = [True]
    with pytest.raises(PlanError):
        closure(model, excluded=('ordinary', 'share', 'ghost'))


def test_exclusion_cannot_hide_unsupported_member_scope():
    model = callback_case()[2]
    model['rules'][0]['timing']['delay'] = .1
    declaration, adoption, errors = closure(model, excluded=('ordinary', 'share'))
    assert declaration == model['attribute_callbacks']
    assert adoption['status'] == 'pending' and errors
    assert any(i['code'] == 'causal_member_scope' for i in adoption['validation_issues'])


@pytest.mark.parametrize('part', ['member', 'edge', 'assumption', 'source'])
def test_native_context_changes_invalidate_order_review_without_derived_cycles(part):
    model = callback_case()[2]
    context = fingerprint(context_view({'mechanics_model': model}))
    declaration = model['attribute_callbacks']
    if part == 'member': declaration['members'].reverse()
    elif part == 'edge': declaration['edges'][0]['before_rule_id'] = 'share'
    elif part == 'assumption': declaration['assumptions'][0]['note'] += ' Changed.'
    else: model['rules'][0]['source'] += ' Changed.'
    assert fingerprint(context_view({'mechanics_model': model})) != context
