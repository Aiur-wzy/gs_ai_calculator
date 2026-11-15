"""Engineering App source/execute/save tests, not game-source certification."""
import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.resource_reactions import finalize_resource_reactions
from genshin_app.equipment.schema import PlanError
from tests.test_mechanics_execution import add, rule
from tests.test_resource_lifecycle_actual import values
from tests.test_resource_reaction_app import reaction_plan
from tests.test_resource_reaction_receipt import resign, validate
from tests.test_shared_pool_app_delivery import authorize


def gated_plan():
    plan = reaction_plan()
    candidate = values()[2]
    for key in ('state_runtime', 'resource_reaction_contract'):
        plan['mechanics_model'][key] = copy.deepcopy(candidate[key])
    return plan


def calculated(tmp_path):
    db = tmp_path/'absent.db'
    plan = authorize(gated_plan(), db)
    out = run_plan_calc(plan, db)
    assert not db.exists()
    return out


def test_actual_app_preserves_establishment_and_end_qualification(tmp_path):
    out = calculated(tmp_path)
    model, adoption = out['report']['mechanics_model'], out['report']['resource_reaction_source_adoption']
    assert model['resource_reaction_contract']['schema_version'] == 2
    assert adoption['contract'] == 'ordinary-reaction-resource-adoption-v2'
    assert adoption['lifecycle_gates'][0]['status'] == 'ready'
    operations = out['result']['resource_reactions']['operations']
    assert [r['state'] for r in operations] == ['blocked_lifecycle', 'committed', 'blocked_lifecycle']
    assert [r['lifecycle_qualification']['reason'] for r in operations] == ['not_established', 'eligible', 'ended']
    assert out['result_receipt']['resource_reactions']['contract'] == 'ordinary-reaction-resource-receipt-v2'
    validate(out, full=False)
    validate(out, full=True)


@pytest.mark.parametrize('part', ['benefit', 'refill'])
def test_excluded_source_removes_grant_without_removing_original_gate_evidence(tmp_path, part):
    plan = gated_plan()
    next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id'] == part).update(state='excluded', reason='engineering exclusion')
    db = tmp_path/'absent.db'
    out = run_plan_calc(authorize(plan, db), db)
    adoption = out['report']['resource_reaction_source_adoption']
    assert adoption['status'] == 'excluded'
    assert adoption['original_declaration']['lifecycle_gates']
    assert adoption['projected_declaration'] is None
    assert 'resource_reaction_contract' not in out['report']['mechanics_model']
    assert not out['report']['mechanics_model'].get('shared_pools', {}).get('operations')
    if part == 'refill':
        assert out['report']['mechanics_model']['state_runtime']['lifecycles']
    validate(out, full=False)
    validate(out, full=True)


@pytest.mark.parametrize('change', ['stale_context', 'source_only', 'pending_life', 'missing_part'])
def test_unreviewed_lifecycle_cannot_leave_unconditional_grant(tmp_path, change):
    db = tmp_path/'absent.db'
    plan = authorize(gated_plan(), db)
    part = next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id'] == 'benefit')
    if change == 'stale_context':
        part['dependencies']['context_fingerprint'] = 'old'
    elif change == 'source_only':
        part['dependencies']['mode'] = 'source_only'
    elif change == 'pending_life':
        plan['mechanics_model']['state_runtime']['lifecycles'][0]['status'] = 'pending'
        plan = authorize(plan, db)
    else:
        plan['effect_bindings'][0]['parts'].remove(part)
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)


@pytest.mark.parametrize('change', ['wrong_life', 'duplicate', 'wrong_policy'])
def test_invalid_original_gate_cannot_be_hidden_by_excluding_every_source(tmp_path, change):
    plan = gated_plan()
    gates = plan['mechanics_model']['resource_reaction_contract']['lifecycle_gates']
    if change == 'wrong_life': gates[0]['lifecycle_id'] = 'not-present'
    elif change == 'duplicate': gates.append(copy.deepcopy(gates[0]))
    else: gates[0]['window'] = 'forever'
    for part in plan['effect_bindings'][0]['parts']:
        part.update(state='excluded', reason='engineering exclusion')
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, tmp_path/'absent.db')


@pytest.mark.parametrize('change', ['drop_gate', 'duration', 'source', 'owner', 'authority'])
def test_final_graph_cannot_borrow_prior_lifecycle_review(tmp_path, change):
    out = calculated(tmp_path)
    model = copy.deepcopy(out['report']['mechanics_model'])
    adoption = out['report']['resource_reaction_source_adoption']
    life = model['state_runtime']['lifecycles'][0]
    if change == 'drop_gate': model['resource_reaction_contract']['lifecycle_gates'] = []
    elif change == 'duration': life['duration_us'] += 1
    elif change == 'source': life['source_id'] = 'binding:refill'
    elif change == 'owner': life['owner'] = 'other'
    else: next(r for r in model['rules'] if r['id'] == adoption['lifecycle_gates'][0]['authority_rule_id'])['source_id'] = 'changed'
    with pytest.raises(PlanError):
        finalize_resource_reactions(adoption, model, lambda *v: None, characters=['a'],
            source_fingerprint=adoption['source_fingerprint'], context_fingerprint=adoption['context_fingerprint'])


@pytest.mark.parametrize('full', [False, True])
@pytest.mark.parametrize('change', ['v1_claim', 'drop_gate', 'qualification', 'life_time'])
def test_rehashed_saved_gate_or_runtime_mutation_is_rejected(tmp_path, full, change):
    out = calculated(tmp_path)
    result = out['result']
    if change == 'v1_claim':
        result['resource_reaction_source_adoption']['contract'] = 'ordinary-reaction-resource-adoption-v1'
    elif change == 'drop_gate':
        result['resource_reaction_execution']['mechanics_model']['resource_reaction_contract']['lifecycle_gates'] = []
    elif change == 'qualification':
        result['resource_reactions']['operations'][0]['lifecycle_qualification']['eligible'] = True
    else:
        result['resource_reaction_execution']['mechanics_model']['state_runtime']['lifecycles'][0]['duration_us'] += 1
    resign(out)
    with pytest.raises(ValueError):
        validate(out, full=full)


def test_an_ungated_operation_survives_exclusion_of_another_operations_lifecycle(tmp_path):
    plan = gated_plan()
    model = plan['mechanics_model']
    operation = copy.deepcopy(model['shared_pools']['operations'][0])
    operation.update(id='independent-income', source_id='binding:independent', order=2)
    model['shared_pools']['operations'].append(operation)
    model['resource_reaction_contract']['operation_ids'].append(operation['id'])
    add(model, rule('independent-authority', value=0, source_id=operation['source_id']))
    part = copy.deepcopy(next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id'] == 'refill'))
    part.update(part_id='independent')
    part['rule']['rule_id'] = 'independent-authority'
    plan['effect_bindings'][0]['parts'].append(part)
    next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id'] == 'benefit').update(state='excluded', reason='engineering exclusion')
    db = tmp_path/'absent.db'
    out = run_plan_calc(authorize(plan, db), db)
    contract = out['report']['mechanics_model']['resource_reaction_contract']
    assert contract == {'schema_version': 2, 'operation_ids': ['independent-income'], 'lifecycle_gates': []}
    adoption = out['result']['resource_reaction_source_adoption']
    assert adoption['original_declaration']['lifecycle_gates']
    assert adoption['lifecycle_gates'][0]['status'] == 'dependency_excluded'
    assert [r['operation_id'] for r in out['result']['resource_reactions']['operations']] == ['independent-income']*3
    validate(out, full=True)
    validate(out, full=False)


def test_pending_lifecycle_is_not_hidden_by_excluded_resource_operation(tmp_path):
    plan = gated_plan()
    plan['mechanics_model']['state_runtime']['lifecycles'][0]['status'] = 'pending'
    next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id'] == 'refill').update(state='excluded', reason='engineering exclusion')
    db = tmp_path/'absent.db'
    report = compose_equipment_plan(authorize(plan, db), db)
    assert report['state'] == report['resource_reaction_source_adoption']['status'] == 'pending'
    assert report['mechanics_model']['resource_reaction_contract'] == plan['mechanics_model']['resource_reaction_contract']


def test_explicit_empty_v2_keeps_saved_version_without_inventing_a_lifecycle(tmp_path):
    plan = reaction_plan()
    plan['mechanics_model']['resource_reaction_contract'].update(schema_version=2, lifecycle_gates=[])
    db = tmp_path/'absent.db'
    out = run_plan_calc(authorize(plan, db), db)
    assert out['report']['mechanics_model']['resource_reaction_contract']['schema_version'] == 2
    assert out['result']['resource_reaction_source_adoption']['lifecycle_gates'] == []
    assert all(r['state'] == 'committed' for r in out['result']['resource_reactions']['operations'])
    validate(out, full=False)
    validate(out, full=True)
