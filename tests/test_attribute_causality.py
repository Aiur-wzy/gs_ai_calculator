"""Pure callback partial-order oracles; no game source authority is supplied."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_causality import (
    AttributeCallbackPlanner,
    AttributeCausalityStopped,
    attribute_causality_capabilities,
)


def node(ident, *, read=None, write=None, inclusion='included', dependencies=(), activation_keys=()):
    out = {'id': ident, 'rule_id': ident, 'callback': {'kind': 'hit_post', 'origin': 0,
        'actual_run_id': '0:hit', 'parent_actual_run_id': '0:hit', 'event_id': 'hit'},
        'reads': [], 'writes': [], 'activation_keys': list(activation_keys)}
    if read:
        actor, basis = read
        out['reads'].append({'id': 'read', 'actor': actor, 'stat': 'em', 'basis': basis,
            'current_dependencies': [{'actor': a, 'stat': 'em', 'basis': b}
                for a, b in (([(actor, basis)] if basis != 'base' else []) + list(dependencies))]})
    if write:
        out['writes'].append({'id': 'write', 'actor': write, 'stat': 'em',
            'nonextra_inclusion': inclusion, 'component': {'source_fingerprint': 'source-'+ident,
                'provider': 'a', 'part_id': ident, 'effect_id': 'value', 'recipient': write}})
    return out


def model(*nodes, edges=()):
    return {'contract': 'attribute-callback-order-v1',
        'parent': {'origin': 0, 'actual_run_id': '0:hit', 'event_id': 'hit', 't_us': 200000,
                   'ruleset': 'gauge-swirl-dynamic-v3'},
        'nodes': list(nodes),
        'edges': [{'before': a, 'after': b, 'evidence_ref': f'{a}-before-{b}'} for a, b in edges],
        'evidence': [{'id': f'{a}-before-{b}', 'status': 'assumed', 'fingerprint': '1'*64,
                     'note': 'Synthetic ordering assumption only'} for a, b in edges]}


def run(data, **kwargs):
    return AttributeCallbackPlanner(**kwargs).plan(data)


def code(receipt):
    return receipt['issues'][0]['code']


def pair(*, inclusion='included', basis='nonextra', edges=()):
    return model(node('writer', write='a', inclusion=inclusion), node('reader', read=('a', basis)), edges=edges)


def test_before_and_after_are_distinct_explicit_plans():
    before = run(pair(edges=[('writer', 'reader')]))
    after = run(pair(edges=[('reader', 'writer')]))
    assert before['status'] == after['status'] == 'ready'
    assert before['batches'] == [['writer'], ['reader']]
    assert after['batches'] == [['reader'], ['writer']]
    assert before['fingerprint'] != after['fingerprint']
    assert before['verification_scope'] == 'materialized_causal_consistency_no_source_authority'
    assert before['footprint_authority'] == 'caller_supplied_not_verified'
    assert not {'rules', 'execution_fingerprint'} & before.keys()


@pytest.mark.parametrize('inclusion', ['included', 'unknown'])
def test_missing_related_order_is_pending_even_for_unknown_inclusion(inclusion):
    out = run(pair(inclusion=inclusion))
    assert out['status'] == 'pending' and code(out) == 'causal_order_missing'
    assert 'batches' not in out


def test_excluded_prunes_nonextra_but_does_not_prune_total():
    assert run(pair(inclusion='excluded'))['batches'] == [['reader', 'writer']]
    assert code(run(pair(inclusion='excluded', basis='total'))) == 'causal_order_missing'


def test_base_and_unrelated_actor_do_not_depend_on_writes():
    assert run(pair(basis='base'))['status'] == 'ready'
    assert run(model(node('writer', write='b'), node('reader', read=('a', 'total'))))['status'] == 'ready'


def test_indirect_current_dependency_needs_order_but_frozen_dependency_does_not():
    indirect = model(node('writer', write='b'), node('reader', read=('a', 'nonextra'), dependencies=[('b', 'total')]))
    assert code(run(indirect)) == 'causal_order_missing'
    historical = model(node('writer', write='b'), node('reader', read=('a', 'nonextra')))
    assert run(historical)['status'] == 'ready'


def test_transitive_order_is_proven_and_list_order_is_irrelevant():
    data = model(node('writer', write='a'), node('middle'), node('reader', read=('a', 'nonextra')),
                 edges=[('writer', 'middle'), ('middle', 'reader')])
    first = run(data)
    second_data = copy.deepcopy(data)
    for key in ('nodes', 'edges', 'evidence'):
        second_data[key].reverse()
    assert first == run(second_data)
    assert first['batches'] == [['writer'], ['middle'], ['reader']]


@pytest.mark.parametrize('edges', [[('a', 'a')], [('a', 'b'), ('b', 'a')], [('a', 'b'), ('b', 'c'), ('c', 'a')]])
def test_declared_cycles_are_invalid_even_when_no_em_conflict(edges):
    out = run(model(node('a'), node('b'), node('c'), edges=edges))
    assert out['status'] == 'invalid' and code(out) == 'causal_cycle'


def test_own_read_samples_prestate_and_independent_constant_writes_commute():
    own = run(model(node('one', read=('a', 'total'), write='a')))
    assert own['status'] == 'ready'
    writes = run(model(node('one', write='a'), node('two', write='a')))
    assert writes['status'] == 'ready' and writes['batches'] == [['one', 'two']]


@pytest.mark.parametrize('kind', ['rule', 'activation', 'component'])
def test_state_or_unique_component_conflict_is_not_mistaken_for_disjoint_em(kind):
    a, b = node('a', write='a'), node('b', write='a')
    if kind == 'rule':
        b['rule_id'] = a['rule_id']
    elif kind == 'activation':
        a['activation_keys'] = b['activation_keys'] = ['unique:common-modifier']
    else:
        b['writes'][0]['component'] = copy.deepcopy(a['writes'][0]['component'])
    out = run(model(a, b, edges=[('a', 'b')]))
    assert out['status'] == 'pending' and code(out) == 'causal_mutation_conflict'


@pytest.mark.parametrize('status', ['pending', 'revoked', 'stale'])
def test_unusable_evidence_never_orders_the_nodes(status):
    data = pair(edges=[('writer', 'reader')])
    data['evidence'][0]['status'] = status
    out = run(data)
    assert out['status'] == 'pending' and code(out) == 'causal_evidence_unusable'


@pytest.mark.parametrize('change', ['origin', 'parent', 'ruleset', 'nan', 'bool', 'extra', 'recipient', 'missing_root_dependency'])
def test_closed_contract_parent_identity_and_dependency_validation(change):
    data = pair(edges=[('writer', 'reader')])
    if change == 'origin': data['nodes'][0]['callback']['origin'] = 1
    elif change == 'parent': data['nodes'][0]['callback']['parent_actual_run_id'] = '0:other'
    elif change == 'ruleset': data['parent']['ruleset'] = 'freeze-reference-target-v1'
    elif change == 'nan': data['parent']['t_us'] = float('nan')
    elif change == 'bool': data['parent']['origin'] = True
    elif change == 'extra': data['nodes'][0]['pretend_no_writes'] = True
    elif change == 'recipient': data['nodes'][0]['writes'][0]['component']['recipient'] = 'b'
    else: data['nodes'][1]['reads'][0]['current_dependencies'] = []
    out = run(data)
    assert out['status'] == 'invalid' and 'batches' not in out


def test_same_time_other_parent_changes_fingerprint_and_cannot_reuse_occurrence():
    data = pair(edges=[('writer', 'reader')])
    first = run(data)
    data['parent']['actual_run_id'] = '0:other-hit'
    assert code(run(data)) == 'causal_parent_identity'
    for item in data['nodes']:
        item['callback'].update(actual_run_id='0:other-hit', parent_actual_run_id='0:other-hit')
    assert run(data)['fingerprint'] != first['fingerprint']


def test_inputs_and_receipts_do_not_mutate_service_or_later_plans():
    data = pair(edges=[('writer', 'reader')])
    original = copy.deepcopy(data)
    service = AttributeCallbackPlanner()
    first = service.plan(data)
    assert data == original
    first['batches'][0].append('forged')
    assert service.plan(data)['batches'] == [['writer'], ['reader']]


def test_cumulative_work_and_output_budgets_fail_without_executable_plan():
    service = AttributeCallbackPlanner(limits={'execution_work': 3})
    one = service.plan(model(node('a')))
    assert one['status'] == 'pending' and one['terminal']
    with pytest.raises(AttributeCausalityStopped):
        service.plan(model(node('a')))
    assert service.usage()['execution_work'] <= 3
    exhausted = run(model(node('a')), limits={'receipt_bytes': 1})
    assert exhausted['status'] == 'pending' and code(exhausted) == 'causal_output_limit'
    assert 'batches' not in exhausted


def test_depth_limit_is_longest_declared_chain_not_input_order():
    data = model(*(node(str(i)) for i in range(4)), edges=[('0', '1'), ('1', '2'), ('2', '3')])
    assert code(run(data, limits={'dependency_depth': 3})) == 'causal_depth_limit'
    assert run(data, limits={'dependency_depth': 4})['status'] == 'ready'


def test_reaction_occurrence_is_distinct_from_parent_and_opaque_run_ids_are_legal():
    data = pair(edges=[('writer', 'reader')])
    callback = data['nodes'][0]['callback']
    callback['kind'] = 'reaction_post'
    assert code(run(data)) == 'causal_parent_identity'
    callback.update(actual_run_id='opaque-reaction-token', event_id='hit:reaction')
    assert run(data)['status'] == 'ready'


@pytest.mark.parametrize('mutation', ['missing_ref', 'duplicate_node', 'duplicate_edge', 'unused_evidence', 'bad_hash'])
def test_graph_materialization_is_closed_and_identity_unique(mutation):
    data = pair(edges=[('writer', 'reader')])
    if mutation == 'missing_ref': data['edges'][0]['after'] = 'not-executed'
    elif mutation == 'duplicate_node': data['nodes'].append(copy.deepcopy(data['nodes'][0]))
    elif mutation == 'duplicate_edge': data['edges'].append(copy.deepcopy(data['edges'][0]))
    elif mutation == 'unused_evidence': data['edges'].clear()
    else: data['evidence'][0]['fingerprint'] = 'not-a-hash'
    out = run(data)
    assert out['status'] == 'invalid' and 'batches' not in out


def test_capabilities_are_detached_and_do_not_claim_runtime_or_sources():
    caps = attribute_causality_capabilities()
    assert caps['runtime_integration'] == 'not_connected_in_c1'
    assert caps['execution'] == 'none'
    caps['schema']['properties'].clear()
    assert attribute_causality_capabilities()['schema']['properties']


def test_failed_attempts_share_work_budget_and_cannot_resume_with_fresh_parent():
    service = AttributeCallbackPlanner(limits={'execution_work': 500})
    failures = []
    for _ in range(10):
        bad = pair()
        failures.append(service.plan(bad))
        if failures[-1].get('terminal'):
            break
    assert code(failures[-1]) == 'causal_work_limit'
    with pytest.raises(AttributeCausalityStopped):
        service.plan(model(node('different-parent')))


def test_single_parent_cannot_combine_distinct_reaction_occurrences():
    a, b = node('a'), node('b')
    for item, suffix in ((a, 'one'), (b, 'two')):
        item['callback'].update(kind='reaction_post', actual_run_id='reaction:'+suffix, event_id='hit:reaction:'+suffix)
    out = run(model(a, b))
    assert code(out) == 'causal_reaction_scope'
    b['callback'] = copy.deepcopy(a['callback'])
    assert run(model(a, b))['status'] == 'ready'


def test_nonterminal_diagnostics_count_toward_output_budget_and_stop_once():
    service = AttributeCallbackPlanner(limits={'receipt_bytes': 1000})
    first = service.plan(pair())
    assert first['status'] == 'pending' and not first.get('terminal')
    assert service.usage()['receipt_bytes'] > 0
    for _ in range(10):
        out = service.plan(pair())
        if out.get('terminal'):
            break
    assert out['terminal'] and code(out) == 'causal_output_limit'
    assert service.usage()['receipt_bytes'] <= 1000
    assert 0 < service.usage()['terminal_diagnostic_bytes'] <= 2048
    with pytest.raises(AttributeCausalityStopped):
        service.plan(pair())
