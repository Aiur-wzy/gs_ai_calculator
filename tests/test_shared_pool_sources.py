"""A shared pool has no independent route around reviewed source ownership."""
import copy

import pytest

from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.shared_pools import prepare_shared_pools


def graph():
    rules = {
        'authority': {'id': 'authority', 'owner': 'a', 'source_id': 'binding:pool'},
        'benefit': {'id': 'benefit', 'owner': 'a', 'source_id': 'binding:benefit', 'resource_gate': {'consumer_id': 'consume'}},
        'grant': {'id': 'grant', 'owner': 'a', 'source_id': 'binding:grant'},
    }
    config = {'schema_version': 1,
        'pools': [{'id': 'pool', 'owner': 'a', 'source_id': 'binding:pool'}],
        'operations': [{'id': 'grant', 'pool_id': 'pool', 'source_id': 'binding:grant'}],
        'consumers': [{'id': 'consume', 'pool_id': 'pool', 'source_id': 'binding:benefit', 'eligibility_rule_id': 'benefit'}]}
    return {'shared_pools': config}, rules, set(rules), {rid: ('binding', rid) for rid in rules}


def invoke(model, rules, active, linked, excluded=(), reviews=None):
    issues = []
    if reviews is None:
        reviews = {r['source_id']: {'mode': 'manual_context', 'context_fingerprint': 'current', 'note': 'Reviewed synthetic graph'} for r in rules.values()}
    result = prepare_shared_pools(model, rules, active, linked, set(excluded), lambda *args: issues.append(args),
                                  source_reviews=reviews, context_fingerprint='current')
    return *result, issues


def test_reviewed_authorities_are_reused_without_mutating_native_input():
    args = graph()
    before = copy.deepcopy(args)
    config, disabled, records, issues = invoke(*args)
    assert args == before and config == args[0]['shared_pools']
    assert not disabled and not issues
    assert all(r['status'] == 'ready' for r in records)
    config['pools'][0]['owner'] = 'changed'
    assert args == before


def test_pending_source_is_not_misreported_as_excluded():
    model, rules, active, linked = graph()
    active.remove('grant')
    config, disabled, records, issues = invoke(model, rules, active, linked)
    assert config is None and disabled == {'benefit'}
    assert issues and all(r['status'] == 'pending' for r in records)


def test_unregistered_source_string_cannot_authorize_resource_execution():
    args = graph()
    args[0]['shared_pools']['operations'][0]['source_id'] = 'unchecked:grant'
    config, disabled, records, issues = invoke(*args)
    assert config is None and disabled == {'benefit'}
    assert issues[0][0] == 'pool_source_pending'
    assert records[1]['status'] == 'pending'


@pytest.mark.parametrize('mutation', ['owner', 'consumer_rule', 'consumer_source', 'gate', 'duplicate_authority'])
def test_resource_ownership_cannot_drift(mutation):
    args = graph(); rules = args[1]
    if mutation == 'owner': rules['grant']['owner'] = 'b'
    elif mutation == 'consumer_rule': args[0]['shared_pools']['consumers'][0]['eligibility_rule_id'] = 'authority'
    elif mutation == 'consumer_source': args[0]['shared_pools']['consumers'][0]['source_id'] = 'binding:pool'
    elif mutation == 'gate': rules['benefit']['resource_gate']['consumer_id'] = 'other'
    else:
        rules['duplicate'] = {**rules['authority'], 'id': 'duplicate'}
        args[3]['duplicate'] = ('binding', 'pool')
    with pytest.raises(PlanError):
        invoke(*args)


def test_explicitly_excluding_grant_keeps_existing_balance_and_benefit():
    model, rules, active, linked = graph(); active.remove('grant')
    config, disabled, records, issues = invoke(model, rules, active, linked, ['grant'])
    assert config['operations'] == [] and len(config['pools']) == len(config['consumers']) == 1
    assert disabled == set() and not issues
    assert records[1]['status'] == 'excluded'


def test_excluding_pool_removes_dependent_gates_and_their_resource_subgraph():
    model, rules, active, linked = graph()
    rules['next'] = {'id': 'next', 'owner': 'a', 'source_id': 'binding:next', 'resource_gate': {'consumer_id': 'next-consumer'}}
    active.add('next'); linked['next'] = ('binding', 'next'); active.remove('authority')
    config = model['shared_pools']
    config['pools'].append({'id': 'dependent', 'owner': 'a', 'source_id': 'binding:benefit'})
    config['consumers'].append({'id': 'next-consumer', 'pool_id': 'dependent', 'source_id': 'binding:next', 'eligibility_rule_id': 'next'})
    compiled, disabled, records, issues = invoke(model, rules, active, linked, ['authority'])
    assert compiled is None and disabled == {'benefit', 'next'} and not issues
    assert any(r['id'] == 'dependent' and r['status'] == 'dependency_excluded' for r in records)


def test_absent_contract_does_not_change_legacy_source_processing():
    assert invoke({}, {}, set(), {}) == (None, set(), [], [])


@pytest.mark.parametrize('dependency', [None,
    {'mode': 'source_only', 'context_fingerprint': None, 'note': 'Old rule review'},
    {'mode': 'manual_context', 'context_fingerprint': 'old', 'note': 'Stale'},
    {'mode': 'manual_context', 'context_fingerprint': 'current', 'note': ''},
])
def test_old_rule_identity_or_stale_context_cannot_approve_new_resource_graph(dependency):
    args = graph()
    reviews = {r['source_id']: dependency for r in args[1].values()} if dependency is not None else {}
    config, disabled, _records, issues = invoke(*args, reviews=reviews)
    assert config is None and disabled == {'benefit'}
    assert any(i[0] == 'pool_source_scope_unreviewed' for i in issues)
