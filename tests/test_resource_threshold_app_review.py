"""Independent full-source graph counterexamples and legacy extraction parity."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

from genshin_app.equipment.resource_source_graph import prepare_resource_source_graph
from tests import test_resource_counter_view_sources as legacy_cases
from tests.test_resource_threshold_app import threshold_plan


def raw_threshold(*, mutate=None, excluded=(), removed=(), reverse=False):
    plan = threshold_plan()
    model = plan['mechanics_model']
    if mutate:
        mutate(model)
    if reverse:
        model['rules'].reverse()
        model['shared_pools']['operations'].reverse()
        model['resource_threshold_observers']['observers'].reverse()
    rules = {r['id']: r for r in model['rules']}
    linked = {p['rule']['rule_id']: ('binding', p['part_id']) for p in plan['effect_bindings'][0]['parts']}
    active = set(rules) - set(excluded)
    reviews = {r['source_id']: {'mode': 'manual_context', 'context_fingerprint': 'current', 'note': 'Synthetic full graph'}
               for r in rules.values()}
    pool_records = [{'kind': group, 'id': row['id'], 'source_id': row['source_id'], 'status': 'ready'}
                    for group in ('pools', 'operations', 'consumers') for row in model['shared_pools'][group]]
    before = copy.deepcopy((model, pool_records, reviews))
    issues = []
    result = prepare_resource_source_graph(model, rules, active, active - set(removed), linked, set(excluded),
        lambda *args: issues.append(args), source_reviews=reviews, context_fingerprint='current',
        pools=model['shared_pools'], state=None, counters=None, pool_adoptions=pool_records,
        state_adoptions=[], counter_adoptions=[], character_ids={'a'})
    assert (model, pool_records, reviews) == before
    return result, issues


def test_pending_cause_cannot_be_deleted_to_allow_remaining_smaller_observer():
    def add_pending(model):
        op = copy.deepcopy(model['shared_pools']['operations'][0])
        op.update(id='second-cause', order=2, status='pending')
        model['shared_pools']['operations'].append(op)
        model['resource_threshold_observers']['observers'][0]['causes']['operation_ids'].append(op['id'])
    result, issues = raw_threshold(mutate=add_pending)
    assert result['observers'] is None and result['triggers'] is None
    assert result['observer_adoptions'][0]['own_source_status'] == 'ready'
    assert result['observer_adoptions'][0]['effective_status'] == 'dependency_pending'
    assert 'boost' in result['disabled_rules'] and issues
    # Independent valid refill is preserved; it isn't retroactively excluded.
    assert [r['id'] for r in result['pools']['operations']] == ['refill']


def test_pending_target_cannot_bootstrap_ready_from_its_binding_cycle():
    result, issues = raw_threshold(mutate=lambda m: m['rules'][0].update(status='pending'))
    assert result['triggers'] is None and result['observers'] is not None
    assert result['trigger_adoptions'][0]['effective_status'] == 'dependency_pending'
    assert result['pools'] is not None and issues


def test_previously_removed_target_is_not_recreated_from_original_ready_declaration():
    result, issues = raw_threshold(removed={'boost'})
    assert result['triggers'] is None and result['observers'] is not None
    assert result['trigger_adoptions'][0]['own_source_status'] == 'ready'
    assert result['trigger_adoptions'][0]['effective_status'] == 'dependency_pending' and issues


def test_excluded_target_as_pool_authority_closes_real_cycle_without_resurrection():
    def bind_pool_to_target(model):
        model['shared_pools']['pools'][0]['source_id'] = 'binding:part'
        model['shared_pools']['operations'][0]['source_id'] = 'binding:part'
    result, issues = raw_threshold(mutate=bind_pool_to_target, excluded={'boost'})
    reversed_result, reversed_issues = raw_threshold(mutate=bind_pool_to_target, excluded={'boost'}, reverse=True)
    assert result == reversed_result and issues == reversed_issues == []
    assert result['pools'] is result['observers'] is result['triggers'] is None
    assert result['trigger_adoptions'][0]['effective_status'] == 'excluded'


@pytest.mark.parametrize('options', [
    {}, {'excluded': {'view'}}, {'missing_pools': True}, {'missing_counters': True},
    {'removed_active': {'boost'}}, {'excluded': {'view'}, 'pending': {'boost'}, 'reverse': True}])
def test_legacy_counter_view_graph_results_match_saved_pre_extraction_helper_exactly(monkeypatch, options):
    path = Path(__file__).resolve().parents[1] / 'benchmarks/history/engine-automation/20261006/resource-design/counter-view-before-graph-extraction.py'
    spec = importlib.util.spec_from_file_location('genshin_app.equipment.independent_pre_graph', path)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)
    expected = legacy_cases.raw(**options)
    monkeypatch.setattr(legacy_cases, 'prepare_resource_counter_views', old.prepare_resource_counter_views)
    previous = legacy_cases.raw(**options)
    assert previous == expected
