"""Independent callback source closure; no App execution claim or model connection."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment.attribute_callbacks import prepare_attribute_callbacks
from genshin_app.equipment.schema import PlanError
from tests.test_attribute_callback_app_closure import closure
from tests.test_attribute_callback_runtime import callback_case


def test_full_review_without_actual_linked_source_is_pending():
    model = callback_case()[2]
    identities = {r['id'] for r in model['rules']}
    reviews = {r['source_id']: {'mode': 'manual_context', 'context_fingerprint': 'current', 'note': 'Assumption'}
               for r in model['rules']}
    issues = []
    declaration, adoption = prepare_attribute_callbacks(model, identities, identities, {}, set(),
        lambda *args: issues.append(args), source_reviews=reviews, context_fingerprint='current')
    assert adoption['status'] == 'pending' and len(issues) == 2
    assert declaration == model['attribute_callbacks']
    assert all(row['own_source_status'] == 'pending' for row in adoption['members'])


@pytest.mark.parametrize('change', ['pending_status', 'blank_source'])
def test_part_applicability_does_not_launder_rule_source_declaration(change):
    model = callback_case()[2]
    if change == 'pending_status':
        model['rules'][0]['status'] = 'pending'
    else:
        model['rules'][0]['source'] = ' '
    declaration, adoption, issues = closure(model)
    assert adoption['status'] == 'pending' and issues
    share = next(row for row in adoption['members'] if row['rule_id'] == 'share')
    assert share['own_source_status'] == share['effective_status'] == 'pending'
    assert set(declaration['members']) == {'share', 'ordinary'}


def test_original_duplicate_source_graph_rejected_even_when_all_members_excluded():
    model = callback_case()[2]
    model['rules'][1]['source_id'] = model['rules'][0]['source_id']
    with pytest.raises(PlanError) as error:
        closure(model, excluded=('share', 'ordinary'))
    assert error.value.reason == 'invalid_attribute_callback_source'


def test_all_excluded_scope_pending_does_not_become_empty_execution_ready():
    model = callback_case()[2]
    model['rules'][0]['effects'][0]['value']['sample'] = 'current'
    declaration, adoption, issues = closure(model, excluded=('share', 'ordinary'))
    assert adoption['status'] == 'pending' and issues
    assert declaration == model['attribute_callbacks']
    assert adoption['retained_rule_ids'] == []
    assert all(row['effective_status'] == 'excluded' for row in adoption['members'])


def test_prior_dependency_removal_cannot_become_own_source_exclusion():
    declaration, adoption, issues = closure(excluded=('ordinary',), missing_delivery=('share',))
    assert declaration['members'] == ['share'] and declaration['edges'] == declaration['assumptions'] == []
    assert adoption['status'] == 'pending' and len(issues) == 1
    share = next(row for row in adoption['members'] if row['rule_id'] == 'share')
    assert share['own_source_status'] == 'ready' and share['effective_status'] == 'pending'
    assert adoption['excluded_rule_ids'] == ['ordinary']


def test_outputs_are_detached_and_cannot_change_source_model_or_next_adoption():
    model = callback_case()[2]
    original = copy.deepcopy(model)
    declaration, adoption, _ = closure(model)
    declaration['edges'][0]['assumption_id'] = 'changed'
    adoption['members'][0]['source_id'] = 'forged'
    adoption['validation_issues'].append({'code': 'forged'})
    assert model == original
    other = closure(model)
    assert other[0] == original['attribute_callbacks'] and not other[1]['validation_issues']
