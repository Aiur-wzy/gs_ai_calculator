"""Observation is based on actual formula receipts, not matching names/times."""
import copy

import pytest

from genshin_app.damage_explanation_reporting import assemble_damage_explanations
from genshin_app.effect_observation import observe_effects
from genshin_engine.mechanics import execute_mechanics
from tests.test_attribute_multistat_runtime import hp_to_atk, run, setup
from tests.test_elemental_multiaura_v4 import aura
from tests.test_mechanics_execution import EVIDENCE
from tests.test_special_reaction_mechanics_projection import (
    _application_fixture,
    _enable,
)


def saved_result(output):
    return {**output['result'], **{k: v for k, v in output.items()
                                  if k not in ('result', 'resolved_template')}}


def attribute_fixture(times=(.2,), *, assembled=False):
    template, rotation, mechanics = setup(times)
    hp_to_atk(template, rotation, mechanics)
    output = run(template, rotation, mechanics)
    result = saved_result(output)
    plan = {'mechanics_model': mechanics, 'manual_template': template}
    if assembled:
        result['rotation_check'] = {**result['rotation_check'],
            'damage_interpretation': {'kind': result['rotation_check']['damage_interpretation']}}
        report = {'mechanics_model': mechanics, 'plan': {'resolved_template': output['resolved_template']},
                  'input_fingerprint': 'fixture', 'output_fingerprint': 'fixture', 'source_fingerprint': 'fixture'}
        assemble_damage_explanations(plan, result, report)
    return plan, result


def parent(output, name, kind='rule'):
    return next(row for row in output['effects'] if row['parent_id'] == name and row['kind'] == kind)


def test_stellar_field_formula_and_application_are_one_exact_effect():
    template, rotation, mechanics = _application_fixture('electro', initial=[aura('cryo', .8, 'a')], times=(.2, .2))
    _enable(mechanics, 'stellar_superconduct')
    mechanics['elemental_model']['profiles'].append({'id': 'none', 'element': 'cryo', 'application': 'none', **EVIDENCE})
    mechanics['hit_events'][1]['application'] = {'profile_id': 'none', 'target_id': 'enemy', 'landed': True}
    result = saved_result(execute_mechanics(template, rotation, mechanics))
    before = copy.deepcopy(result)
    observed = observe_effects({'mechanics_model': mechanics}, result)
    fields = [row for row in observed['effects'] if row['kind'] == 'special_reaction']
    assert len(fields) == 1
    assert fields[0]['state'] == 'formula_consumed'
    assert fields[0]['applied_count'] == fields[0]['formula_consumed_count'] == 1
    assert fields[0]['samples'][-1]['event_id'] == 'h1'
    assert not any(row['kind'] == 'buff' and row['effect_id'] == fields[0]['effect_id'] for row in observed['effects'])
    assert [hit['expected'] for hit in result['hits'][:2]] == pytest.approx([45, 54])
    assert result == before


@pytest.mark.parametrize('assembled', [False, True])
@pytest.mark.parametrize('times', [(.2,), (.2, .4)])
def test_provider_hp_dependency_is_consumed_even_when_not_recipient_direct_effect(times, assembled):
    plan, result = attribute_fixture(times, assembled=assembled)
    before = copy.deepcopy((plan, result))
    observed = observe_effects(plan, result)
    source, conversion = parent(observed, 'source_hp'), parent(observed, 'convert')
    assert source['formula_consumed_count'] == source['attribute_dependency_consumed_count'] == len(times)
    assert source['direct_formula_consumed_count'] == 0
    assert source['rejected_count'] == len(times)  # Audit cycles are not the reported window.
    assert source['rejection_reasons'] == {'recipient_mismatch': len(times)}
    assert conversion['formula_consumed_count'] == conversion['direct_formula_consumed_count'] == len(times)
    assert conversion['attribute_dependency_consumed_count'] == 0
    assert observed['attribute_dependencies']['unresolved_count'] == 0
    evidence = next(s for s in source['samples'] if s['stage'] == 'attribute_dependency_consumed')
    receipt = result['attribute_ledger']['receipts'][int(evidence['receipt_ref'].split('/')[-1])]
    assert receipt['query']['actor'] == 'b' and receipt['query']['stat'] == 'hp'
    assert receipt['query']['frame']['origin'] == 0
    assert evidence['evaluated_value'] == 6000
    assert [h['attribute_inputs']['scaling'] for h in result['hits']] == [460] * len(times)
    assert (plan, result) == before


@pytest.mark.parametrize('origin', ['fixed_override', 'external'])
def test_attribute_reads_are_not_final_formula_proof_after_external_override(origin):
    plan, result = attribute_fixture(assembled=True)
    for event in result['damage_explanations']['events'].values():
        event['outcome']['origin'] = origin
    observed = observe_effects(plan, result)
    assert all(row['formula_consumed_count'] == 0 for row in observed['effects'])
    assert observed['attribute_dependencies']['contribution_link_count'] == 0


def test_unrelated_ordinary_panel_read_does_not_prove_formula_consumption():
    plan, result = attribute_fixture()
    document = result['damage_explanations']
    event = next(iter(document['events'].values()))
    event['modifier_refs'] = []
    panel = document['panels'][event['panel_ref']]
    # Even a listed same-event/actor ordinary read is not a damage projection.
    receipt = copy.deepcopy(result['attribute_ledger']['receipts'][1])
    receipt['query']['actor'] = 'a'
    receipt['query']['stat'] = 'atk'
    receipt['receipt_id'] = 'unrelated-query'
    result['attribute_ledger']['receipts'].append(receipt)
    panel['attribute_receipt_refs'] = [f"/attribute_ledger/receipts/{len(result['attribute_ledger']['receipts']) - 1}"]
    observed = observe_effects(plan, result)
    assert all(row['formula_consumed_count'] == 0 for row in observed['effects'])


@pytest.mark.parametrize('defect', ['missing', 'pending', 'duplicate', 'ambiguous_component', 'excluded'])
def test_unknown_or_excluded_attribute_evidence_is_not_invented(defect):
    plan, result = attribute_fixture()
    ledger = result['attribute_ledger']
    receipt = next(r for r in ledger['receipts'] if r.get('value') == 36000 and r['query']['frame']['origin'] == 0)
    if defect == 'missing':
        ledger['receipts'].remove(receipt)
    elif defect == 'pending':
        receipt['status'] = 'pending'
    elif defect == 'duplicate':
        ledger['receipts'].append(copy.deepcopy(receipt))
    elif defect == 'ambiguous_component':
        duplicate = copy.deepcopy(ledger['declarations']['effect_classifications'][0])
        duplicate['effect_ref']['rule_id'] = 'another'
        ledger['declarations']['effect_classifications'].append(duplicate)
    else:
        receipt['contributions'][0]['decision'] = 'excluded_from_nonextra'
    observed = observe_effects(plan, result)
    assert parent(observed, 'source_hp')['formula_consumed_count'] == 0
    assert parent(observed, 'convert')['formula_consumed_count'] == 1
    assert observed['attribute_dependencies']['status'] == ('complete' if defect == 'excluded' else 'partial')


def test_panel_projection_uses_consumed_stat_not_every_listed_receipt():
    plan, result = attribute_fixture(assembled=True)
    event = next(iter(result['damage_explanations']['events'].values()))
    event['modifier_refs'] = []
    observed = observe_effects(plan, result)
    assert parent(observed, 'source_hp')['formula_consumed_count'] == 1
    panel = result['damage_explanations']['panels'][event['panel_ref']]
    panel['scaling_stat'] = 'def'
    observed = observe_effects(plan, result)
    assert all(row['formula_consumed_count'] == 0 for row in observed['effects'])


def test_manual_attributes_keep_their_original_effect_identity():
    template, rotation, mechanics = setup()
    template['buffs'] = [{'id': 'manual', 'start': 0, 'end': 1, 'scope': 'team', 'target': 'a',
        'extensions': {'owner': 'a'}, 'effects': [
            {'id': 'flat', 'benefit_type_id': 'benefit.stat_add.atk', 'zone': 'stat_add', 'stat': 'atk',
             'value': 20, 'applies_to': {'mode': 'all'}}]}]
    mechanics['attribute_model']['effect_classifications'].append({
        'effect_ref': {'kind': 'manual_buff', 'buff_id': 'manual', 'effect_id': 'flat'},
        'component': {'source_fingerprint': 'manual', 'provider': 'a', 'part_id': 'manual', 'effect_id': 'flat'},
        'nonextra_inclusion': 'unknown', 'status': 'assumed', 'source': 'Synthetic'})
    result = saved_result(run(template, rotation, mechanics))
    observed = observe_effects({'mechanics_model': mechanics, 'manual_template': template}, result)
    record = parent(observed, 'manual', 'buff')
    assert record['formula_consumed_count'] == 1
    assert record['direct_formula_consumed_count'] == 1
    assert len(observed['effects']) == 1


def test_field_tail_outside_window_is_explained_without_fabricating_formula():
    result = {'mechanics_trace': [
        {'type': 'special_reaction_effect_applied', 'event_id': 'tail', 't': 120.1, 'cycle': 0,
         'area_id': 'area', 'effect_id': 'field', 'reaction_type': 'stellar_superconduct'},
        {'type': 'hit', 'event_id': 'tail', 't': 120.1, 'cycle': 0, 'report_disposition': 'outside_input_window'}],
        'damage_explanations': {'status': 'complete', 'events': {}, 'modifiers': {}}}
    record = observe_effects({}, result)['effects'][0]
    assert record['state'] == 'outside_report_window'
    assert record['formula_consumed_count'] == 0 and record['outside_report_count'] == 1
    assert record['samples'][0]['report_disposition'] == 'outside_input_window'


@pytest.mark.parametrize('wrong_run', [False, True])
def test_conditional_panel_receipt_keeps_exact_saved_source_link(wrong_run):
    from tests.test_attribute_condition_projection import case

    template, rotation, mechanics = case()
    output = run(template, rotation, mechanics)
    result = saved_result(output)
    result['rotation_check'] = {**result['rotation_check'],
        'damage_interpretation': {'kind': result['rotation_check']['damage_interpretation']}}
    plan = {'mechanics_model': mechanics, 'manual_template': template}
    report = {'mechanics_model': mechanics, 'plan': {'resolved_template': output['resolved_template']},
              'input_fingerprint': 'fixture', 'output_fingerprint': 'fixture', 'source_fingerprint': 'fixture'}
    assemble_damage_explanations(plan, result, report)
    if wrong_run:
        for source in result['attribute_ledger']['condition_sources'].values():
            source['actual_event_run_id'] = 'different'
    observed = observe_effects(plan, result)
    assert len(observed['effects']) == 1
    assert observed['effects'][0]['formula_consumed_count'] == 1
    assert observed['attribute_dependencies']['status'] == ('partial' if wrong_run else 'complete')
    assert observed['attribute_dependencies']['contribution_link_count'] == (0 if wrong_run else 1)
