"""Full dependency closure, including removed causes and source feedback."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.resource_source_graph import prepare_resource_source_graph
from genshin_app.equipment.schema import PlanError
from tests.test_resource_threshold_app import threshold_plan
from tests.test_shared_pool_app_delivery import authorize


def raw(*, excluded=(), pending=(), removed_rules=(), removed_operations=(), reverse=False, mutate=None):
    model = threshold_plan()['mechanics_model']
    if mutate: mutate(model)
    if reverse:
        model['rules'].reverse()
        model['shared_pools']['operations'].reverse()
        model['resource_threshold_observers']['observers'].reverse()
        model['resource_threshold_triggers']['bindings'].reverse()
    rules = {r['id']: r for r in model['rules']}
    source_active = set(rules) - set(excluded) - set(pending)
    actual = source_active - set(removed_rules)
    pools = copy.deepcopy(model['shared_pools'])
    pools['operations'] = [r for r in pools['operations'] if r['id'] not in removed_operations]
    records = [{'kind': group, 'id': row['id'], 'source_id': row['source_id'], 'status': 'ready'}
               for group in ('pools', 'operations', 'consumers') for row in model['shared_pools'][group]]
    reviews = {r['source_id']: {'mode': 'manual_context', 'context_fingerprint': 'now', 'note': 'Synthetic source graph'}
               for r in rules.values()}
    before = copy.deepcopy((model, records, pools))
    issues = []
    out = prepare_resource_source_graph(model, rules, source_active, actual, set(rules), set(excluded),
        lambda *args: issues.append(args), source_reviews=reviews, context_fingerprint='now',
        pools=pools, state=None, counters=None, pool_adoptions=records, state_adoptions=[], counter_adoptions=[], character_ids={'a'})
    assert (model, records, pools) == before
    return out, issues


def test_mutually_referenced_binding_and_target_cannot_revive_missing_actual_target():
    out, issues = raw(removed_rules={'boost'})
    assert out['triggers'] is None and 'boost' in out['disabled_rules'] and issues
    row = out['trigger_adoptions'][0]
    assert row['own_source_status'] == 'ready' and row['effective_status'] == 'dependency_pending'
    assert out['pools'] and out['observers']


def test_missing_one_actual_cause_closes_whole_observer_instead_of_shrinking_causes():
    out, issues = raw(removed_operations={'refill'})
    assert out['observers'] is None and out['triggers'] is None and issues
    assert 'boost' in out['disabled_rules']
    assert out['observer_adoptions'][0]['effective_status'] == 'dependency_pending'


def test_pending_cause_dominates_explicit_observer_exclusion_and_is_permutation_stable():
    left, issues = raw(excluded={'observer'}, pending={'pool'})
    right, _ = raw(excluded={'observer'}, pending={'pool'}, reverse=True)
    assert left == right and issues
    row = left['observer_adoptions'][0]
    assert row['own_source_status'] == 'excluded' and row['effective_status'] == 'dependency_pending'
    assert left['trigger_adoptions'][0]['effective_status'] == 'dependency_pending'


def test_target_source_that_owns_pool_participates_in_feedback_closure():
    def feedback(model):
        model['shared_pools']['pools'][0]['source_id'] = 'binding:part'
    out, issues = raw(excluded={'observer'}, mutate=feedback)
    assert not issues
    assert out['pools'] is None and out['triggers'] is None and out['observers'] is None
    assert 'boost' in out['disabled_rules']


def test_excluded_trigger_preserves_independent_operation_and_observer():
    out, issues = raw(excluded={'boost'})
    assert out['triggers'] is None and out['observers'] and not issues
    assert [r['id'] for r in out['pools']['operations']] == ['refill']


def test_native_pending_target_never_becomes_ready_by_reference_cycle():
    out, issues = raw(mutate=lambda model: model['rules'][0].update(status='pending'))
    assert out['triggers'] is None and issues
    assert out['trigger_adoptions'][0]['effective_status'] == 'dependency_pending'


def test_empty_threshold_evidence_is_not_approved_by_valid_source_identity():
    with pytest.raises(PlanError):
        raw(mutate=lambda model: model['resource_threshold_observers']['observers'][0].update(source=' '))


def test_actual_app_closes_resource_feedback_without_reviving_pool(tmp_path):
    plan = threshold_plan()
    plan['mechanics_model']['shared_pools']['pools'][0]['source_id'] = 'binding:part'
    plan['effect_bindings'][0]['parts'][2].update(state='excluded', reason='Explicit observer exclusion')
    db = tmp_path / 'absent.db'
    plan = authorize(plan, db)
    out = run_plan_calc(plan, db)
    assert [h['expected'] for h in out['result']['hits']] == [45, 45]
    assert 'shared_pools' not in out['report']['mechanics_model']
    assert 'resource_threshold_triggers' not in out['report']['mechanics_model']
    assert all(r['status'] == 'dependency_excluded' for r in out['report']['shared_pool_source_adoptions'])
    assert compose_equipment_plan(plan, db)['state'] == 'ready'
