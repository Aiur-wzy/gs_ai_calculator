"""Independent saved-artifact and actual App scope checks, output-only."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from genshin_app.damage_explanation_reporting import validate_damage_explanations
from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment import resource_reaction_receipt as proof
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.resource_reactions import finalize_resource_reactions
from genshin_app.equipment.schema import COMPOSER_VERSION, PlanError
from tests.test_mechanics_execution import ability, action, second_character
from tests.test_mechanics_scheduling import pre_observation_output
from tests.test_resource_reaction_app import reaction_plan
from tests.test_resource_reaction_sources import fixture as source_fixture
from tests.test_resource_reaction_sources import prepare
from tests.test_shared_pool_app_delivery import authorize

HERE = Path(__file__).resolve().parent
SAVED = HERE / 'fixtures/resource_reactions'
ABSENT_DB = HERE / 'never-created-rq1-review.db'


def saved(name='income'):
    return json.loads((SAVED / f'{name}.calculated.json').read_text(encoding='utf-8-sig'))


def current_saved(tmp_path, name='income'):
    """Fresh-calculate the unchanged historical author plan into a new file.

    The frozen result is deliberately not repaired or marked current. Its raw
    author inputs still pass today's ordinary source checks and engine path.
    """
    historical = saved(name)
    before = copy.deepcopy(historical)
    database = tmp_path / 'absent.db'
    current = run_plan_calc(historical['plan'], database)
    assert historical == before and not database.exists()
    assert current['result']['summary'] == historical['result']['summary']
    if name != 'excluded':
        assert 'budget_policy' not in historical['result']['resource_reactions']
        assert current['result']['resource_reactions']['budget_policy']['contract'] == 'resource-execution-budget-v1'
        # Actual resource occurrences, balances, targets and ordering remain
        # identical; new policy/program identities and metered work are real
        # current evidence, not quantities copied onto the old result.
        for key in ('operations', 'contributors'):
            if key in historical['result']['resource_reactions']:
                assert current['result']['resource_reactions'][key] == historical['result']['resource_reactions'][key]
        assert validate_damage_explanations(
            current['result'], current['result_receipt'], report=current['report']) == 'complete'
        # Reuse the independently audited exact NEXT-01/04 observation view.
        # A sealed App result additionally requires its genuine receipt above.
        observed = pre_observation_output(
            {'result': current['result'], 'mechanics_trace': current['result']['mechanics_trace'],
             'resolved_template': current['plan']['resolved_template']})
        resolution = observed['resolved_template']['meta']['equipment_resolution']
        assert resolution['composer_version'] == COMPOSER_VERSION
        resolution['composer_version'] = historical['plan']['resolved_template']['meta']['equipment_resolution']['composer_version']
        assert observed['resolved_template'] == historical['plan']['resolved_template']
        assert observed['mechanics_trace'] == historical['result']['mechanics_trace']
        assert current['result']['elemental'] == historical['result']['elemental']
    path = tmp_path / f'{name}.current.calculated.json'
    path.write_text(json.dumps(current, ensure_ascii=False), encoding='utf8')
    restored = json.loads(path.read_text(encoding='utf8'))
    # Every mutation negative starts from an independently accepted current
    # saved result, so a stale legacy policy cannot make the negative vacuous.
    proof.validate_resource_reaction_result_receipt(restored['result'], restored['result_receipt'])
    proof.validate_resource_reaction_receipt(restored['plan'], restored['result'], restored['report'], restored['result_receipt'])
    return restored


def resign(value):
    result, receipt = value['result'], value['result_receipt']
    receipt['resource_reactions'] = proof._proof(result, receipt)
    receipt['result_fingerprint'] = fingerprint(result)


@pytest.mark.parametrize('name', ['income', 'zero', 'no-match', 'excluded'])
def test_actual_saved_full_and_result_only_positive(tmp_path, name):
    value = current_saved(tmp_path, name)
    before = copy.deepcopy(value)
    proof.validate_resource_reaction_result_receipt(value['result'], value['result_receipt'])
    proof.validate_resource_reaction_receipt(value['plan'], value['result'], value['report'], value['result_receipt'])
    assert value == before


@pytest.mark.parametrize('name', ['income', 'zero', 'no-match'])
def test_old_saved_resource_ledger_cannot_be_silently_upgraded(name):
    path = SAVED / f'{name}.calculated.json'
    raw = path.read_bytes()
    value = saved(name)
    before = copy.deepcopy(value)
    assert 'budget_policy' not in value['result']['resource_reactions']
    with pytest.raises(ValueError, match='重新计算'):
        proof.validate_resource_reaction_result_receipt(value['result'], value['result_receipt'])
    with pytest.raises(ValueError, match='重新计算'):
        proof.validate_resource_reaction_receipt(value['plan'], value['result'], value['report'], value['result_receipt'])
    assert value == before and path.read_bytes() == raw


def test_old_explicitly_excluded_source_without_resource_execution_remains_readable():
    value = saved('excluded')
    before = copy.deepcopy(value)
    assert 'resource_reactions' not in value['result']
    proof.validate_resource_reaction_result_receipt(value['result'], value['result_receipt'])
    proof.validate_resource_reaction_receipt(value['plan'], value['result'], value['report'], value['result_receipt'])
    assert value == before


@pytest.mark.parametrize('field', ['authority_rule_id', 'review_mode'])
def test_result_only_claims_must_match_actual_source_rule(tmp_path, field):
    value = current_saved(tmp_path)
    row = value['result'][proof.ADOPTION]['operations'][0]
    row[field] = 'pool-authority' if field == 'authority_rule_id' else 'source_only'
    resign(value)
    with pytest.raises(ValueError):
        proof.validate_resource_reaction_result_receipt(value['result'], value['result_receipt'])


def test_result_only_completed_claim_cannot_carry_pending_issues(tmp_path):
    value = current_saved(tmp_path)
    value['result'][proof.ADOPTION]['validation_issues'].append(
        {'code': 'resource_reaction_source_pending', 'severity': 'pending', 'path': 'source', 'message': 'not reviewed'})
    resign(value)
    with pytest.raises(ValueError):
        proof.validate_resource_reaction_result_receipt(value['result'], value['result_receipt'])


@pytest.mark.parametrize('section', ['elemental', 'shared_pools', 'rotation_check'])
def test_self_rehash_cannot_delete_actual_execution_evidence(tmp_path, section):
    value = current_saved(tmp_path)
    if section == 'rotation_check':
        value['result'][section]['event_log'] = []
    else:
        value['result'][section]['trace'] = []
    resign(value)
    with pytest.raises(ValueError):
        proof.validate_resource_reaction_result_receipt(value['result'], value['result_receipt'])


def test_app_original_scope_error_not_hidden_by_source_exclusion():
    plan = reaction_plan()
    plan['mechanics_model']['shared_pools']['operations'][0]['trigger']['phase'] = 'pre'
    for row in plan['effect_bindings'][0]['parts']:
        row.update(state='excluded', reason='engineering exclusion')
    with pytest.raises(PlanError):
        compose_equipment_plan(plan, ABSENT_DB)
    assert not ABSENT_DB.exists()


def test_app_stale_whole_context_preserves_pending_without_execution():
    plan = authorize(reaction_plan(), ABSENT_DB)
    before = copy.deepcopy(plan)
    plan['manual_template']['characters'][0]['stats']['atk'] += 1
    report = compose_equipment_plan(plan, ABSENT_DB)
    assert report['state'] == report[proof.ADOPTION]['status'] == 'pending'
    assert report['mechanics_model'][proof.FIELD] == plan['mechanics_model'][proof.FIELD]
    with pytest.raises(PlanError):
        run_plan_calc(plan, ABSENT_DB)
    assert before['manual_template']['characters'][0]['stats']['atk'] + 1 == plan['manual_template']['characters'][0]['stats']['atk']
    assert not ABSENT_DB.exists()


@pytest.mark.parametrize('field,value', [('source_id', 'binding:pool'), ('owner', 'b')])
def test_final_graph_cannot_keep_ready_adoption_after_authority_identity_changed(field, value):
    args = source_fixture()
    _, adoption, issues = prepare(args)
    assert adoption['status'] == 'ready' and not issues
    authority = next(r for r in args[1]['rules'] if r['id'] == 'grant-authority')
    authority[field] = value
    try:
        final = finalize_resource_reactions(adoption, args[1], lambda *r: issues.append(r),
            characters=['a', 'b'], source_fingerprint='source', context_fingerprint='context')
    except PlanError:
        return
    assert final['status'] == 'pending' and issues


@pytest.mark.parametrize('reverse', [False, True])
def test_mixed_pending_and_excluded_members_do_not_disappear_into_legacy(reverse):
    args = source_fixture()
    original, delivered, records, _reviews = args
    extra = copy.deepcopy(original['shared_pools']['operations'][0])
    extra.update(id='refill2', source_id='binding:refill2', order=1)
    original['shared_pools']['operations'].append(extra)
    original[proof.FIELD]['operation_ids'].append('refill2')
    delivered['shared_pools']['operations'] = [copy.deepcopy(extra)]
    records[0]['status'] = 'excluded'
    records.append({'kind': 'operations', 'id': 'refill2', 'pool_id': 'pool',
                    'source_id': 'binding:refill2', 'status': 'pending'})
    if reverse:
        records.reverse()
        original[proof.FIELD]['operation_ids'].reverse()
    declaration, adoption, issues = prepare(args)
    assert adoption['status'] == 'pending' and issues
    assert declaration == original[proof.FIELD]
    assert set(adoption['declared_operation_ids']) == {'refill', 'refill2'}
    assert {r['status'] for r in adoption['operations']} == {'excluded', 'pending'}


def test_real_later_action_failure_keeps_verified_income_prefix_without_dps():
    plan = reaction_plan()
    second_character(plan['manual_template'], plan['rotation_model'])
    plan['manual_template']['characters'][-1]['stats']['energy_recharge'] = 1
    plan['rotation_model']['abilities'].append(ability('offfield_attempt', 'b'))
    plan['rotation_model']['actions'].append(action('later_failure', 'offfield_attempt', .5, .6, 30,
                                                   repeat={'mode': 'once'}))
    value = run_plan_calc(authorize(plan, ABSENT_DB), ABSENT_DB)
    assert value['result']['mechanics']['complete_first_cycle'] is False
    assert value['result']['summary']['dps'] is None
    assert len(value['result']['resource_reactions']['operations']) == 3
    proof.validate_resource_reaction_result_receipt(value['result'], value['result_receipt'])
    proof.validate_resource_reaction_receipt(value['plan'], value['result'], value['report'], value['result_receipt'])
