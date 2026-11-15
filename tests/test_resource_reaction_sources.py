"""Source closure only; current context is engineering review, not game fact."""
import copy

import pytest

from genshin_app.equipment.resource_reactions import (
    finalize_resource_reactions,
    prepare_resource_reactions,
)
from genshin_app.equipment.schema import PlanError
from tests.test_mechanics_execution import add, rule
from tests.test_reaction_operation_capabilities import setup
from tests.test_reaction_resource_queue import enable


def fixture():
    model = enable(setup())[2]
    add(model, rule('grant-authority', value=0, source_id='binding:refill'))
    delivered = copy.deepcopy(model)
    rows = [{'kind': 'operations', 'id': 'refill', 'pool_id': 'pool', 'source_id': 'binding:refill',
             'authority_rule_id': 'grant-authority', 'status': 'ready'}]
    reviews = {'binding:refill': {'mode': 'manual_context', 'context_fingerprint': 'context', 'note': 'engineering'}}
    return model, delivered, rows, reviews


def prepare(values):
    original, delivered, records, reviews = values
    issues = []
    declaration, adoption = prepare_resource_reactions(original, delivered, records, lambda *row: issues.append(row),
        characters=['a', 'b'], source_reviews=reviews, context_fingerprint='context')
    return declaration, adoption, issues


def test_ready_source_and_final_whole_program_without_mutations():
    args = fixture(); before = copy.deepcopy(args)
    declaration, adoption, issues = prepare(args)
    assert issues == [] and adoption['status'] == 'ready'
    assert adoption['source_authority'] == 'caller_assumed' and 'program' not in adoption
    args[1]['rules'][0]['effects'][0]['value'] = 123
    final_before = copy.deepcopy(args[1])
    result = finalize_resource_reactions(adoption, args[1], lambda *r: issues.append(r), characters=['a', 'b'],
        source_fingerprint='source', context_fingerprint='context')
    assert result['program']['program_fingerprint'] == result['program_fingerprint']
    assert args[1] == final_before and args[0] == before[0] and args[2:] == before[2:]
    assert declaration == args[0]['resource_reaction_contract'] and issues == []


@pytest.mark.parametrize('status', ['excluded', 'dependency_excluded'])
def test_only_actual_explicit_exclusion_removes_contract(status):
    args = fixture(); args[2][0]['status'] = status
    args[1]['shared_pools']['operations'] = []
    declaration, adoption, issues = prepare(args)
    assert declaration is None and adoption['status'] == 'excluded' and issues == []
    args[1].pop('resource_reaction_contract')
    final = finalize_resource_reactions(adoption, args[1], lambda *r: issues.append(r), characters=['a', 'b'],
        source_fingerprint='source', context_fingerprint='context')
    assert 'program' not in final and final['operations'][0]['status'] == status


@pytest.mark.parametrize('failure', ['pending', 'missing', 'not_live', 'source_only', 'stale', 'empty_note'])
def test_unapproved_cannot_disappear_into_legacy_execution(failure):
    args = fixture()
    if failure == 'pending': args[2][0]['status'] = 'pending'
    elif failure == 'missing': args[2].clear()
    elif failure == 'not_live': args[1]['shared_pools']['operations'].clear()
    elif failure == 'source_only': args[3]['binding:refill']['mode'] = 'source_only'
    elif failure == 'stale': args[3]['binding:refill']['context_fingerprint'] = 'old'
    else: args[3]['binding:refill']['note'] = ' '
    declaration, adoption, issues = prepare(args)
    assert declaration == args[0]['resource_reaction_contract']
    assert adoption['status'] == 'pending' and issues and 'program' not in adoption


def test_exclusion_cannot_hide_invalid_native_reference():
    args = fixture(); args[0]['resource_reaction_contract']['operation_ids'] = ['missing']
    args[2][0]['status'] = 'excluded';args[1]['shared_pools']['operations'] = []
    with pytest.raises(PlanError): prepare(args)


@pytest.mark.parametrize('kind', ['source_id', 'duplicate_record', 'live_excluded', 'changed_operation'])
def test_receipt_and_delivery_cannot_override_actual_native_identity(kind):
    args = fixture()
    if kind == 'source_id': args[2][0]['source_id'] = 'other'
    elif kind == 'duplicate_record': args[2].append(copy.deepcopy(args[2][0]))
    elif kind == 'live_excluded': args[2][0]['status'] = 'excluded'
    else: args[1]['shared_pools']['operations'][0]['amount'] += 1
    with pytest.raises(PlanError): prepare(args)


def test_no_contract_is_no_record_and_no_mutation():
    args = fixture(); args[0].pop('resource_reaction_contract'); before = copy.deepcopy(args)
    assert prepare(args) == (None, None, []) and args == before
