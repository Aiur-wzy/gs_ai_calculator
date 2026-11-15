"""Actual typed CAST receipts plus queue-only formula dependency controls."""
import copy
import sys
from pathlib import Path

import pytest

from genshin_app.equipment import execution_verification as verification
from genshin_app.equipment import snapshot_capture_receipt as captures
from genshin_app.equipment.execution_verification import ExecutionVerificationSession
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanism_consumption_receipt import (
    verify_mechanism_consumption,
)
from genshin_app.equipment.mechanism_receipt import (
    make_mechanism_receipt,
    mechanism_ledger_fingerprint,
    mechanism_summary,
    validate_mechanism_result_receipt,
)
from genshin_app.equipment.mechanism_sources import make_replay_context
from genshin_app.rotation_check import describe_rotation_check, resolve_rotation_model
from genshin_engine.mechanics import execute_mechanics
from tests.test_elemental_execution import setup as amplify_setup
from tests.test_engine_v2 import add_buff, effect
from tests.test_mechanics_execution import add, fixture, rule
from tests.test_mechanics_swirl import setup as swirl_setup
from tests.test_mechanism_cast_integration import compose, scene
from tests.test_mechanism_cast_receipt_review import executed_case
from tests.test_target_state_review import inputs as fixed_inputs


def test_candidate_only_cold_imports():
    candidate = Path(__file__).resolve().parents[1] / 'src'
    for name, module in list(sys.modules.items()):
        if name.startswith(('genshin_app', 'genshin_engine', 'genshin_db', 'genshin_runtime')) and getattr(module, '__file__', None):
            assert Path(module.__file__).resolve().is_relative_to(candidate), (name, module.__file__)


def describe(output, template, rotation, model, matches):
    result = output['result']
    assert result is not None, output['issues']
    rotation, bindings = resolve_rotation_model(template, rotation)
    result.update(rotation_check=describe_rotation_check(output['rotation_check'], template, rotation, bindings,
        mechanics_model=model, matches=matches), mechanics_trace=output['mechanics_trace'],
        sampling_trace=output['sampling_trace'], mechanics_status=output['status'], mechanics_issues=output['issues'])
    if not result.get('mechanics', {}).get('complete_first_cycle', False):
        result['summary']['dps'] = None
    return result


def cast_case(kind='classic', *, template_tuning=False):
    plan, template, material, requirements = scene()
    if kind in ('def', 'hp', 'em'):
        for hit in template['hits']:
            hit['scaling_stat'] = kind
        template['characters'][0]['stats'].update(hp=1000, base_hp=1000, **{'def': 200, 'base_def': 200})
    elif kind == 'transformative':
        for hit in template['hits']:
            hit.update(damage_system='transformative', reaction={'type': 'swirl'})
    elif kind == 'swirl':
        _, _, elemental = swirl_setup()
        plan['mechanics_model']['elemental_model'] = elemental['elemental_model']
        for hit, event in zip(template['hits'], plan['mechanics_model']['hit_events'], strict=True):
            hit['element'] = 'anemo'
            event['application'] = {'profile_id': 'anemo', 'target_id': 'enemy', 'landed': True}
    if template_tuning:
        add_buff(template, [effect('bonus')])
        plan['rotation_tuning'] = {'buff_hit_matches': [{'id': 'template-choice', 'state': 'confirmed',
            'mode': 'replace', 'hit_ids': ['live'], 'effect_ref': {'buff_id': 'bf', 'effect_id': 'bonus'}}]}
    _, records, _, _ = compose(plan, template, material, requirements)
    plan['mechanism_sources'][0].update(records[0]['required_confirmation'])
    model, records, _, issues = compose(plan, template, material, requirements)
    assert model and not issues, issues
    context = make_replay_context(plan, template, records=records, action_event_compilations=[],
        attack_metadata_compilations=[], parameter_compilations=[], equipment_requirements=requirements)
    if template_tuning:
        template = copy.deepcopy(template)
        template['buffs'][0]['effects'][0]['applies_to'] = {'mode': 'hits', 'hit_ids': ['live']}
    rotation, _ = resolve_rotation_model(template, plan['rotation_model'])
    output = execute_mechanics(template, rotation, model)
    result = describe(output, template, rotation, model, [])
    result.update(mechanism_compilations=records, mechanism_replay_context=context,
                  mechanism_execution={'model': model, 'template': template})
    result['mechanism_summary'] = mechanism_summary(result)
    report = {'mechanics_model': model, 'mechanics_input_template': template}
    receipt = {'mechanisms': make_mechanism_receipt(plan, report, result),
               'result_fingerprint': fingerprint(result), 'output_fingerprint': fingerprint(output['resolved_template'])}
    return result, receipt


def rehash(result, receipt):
    receipt['mechanisms']['ledger_fingerprint'] = mechanism_ledger_fingerprint(result)
    result['mechanism_summary'] = mechanism_summary(result)
    receipt['mechanisms']['summary_fingerprint'] = fingerprint(result['mechanism_summary'])
    receipt['result_fingerprint'] = fingerprint(result)


@pytest.mark.parametrize('kind', ['classic', 'def', 'hp', 'em', 'transformative', 'swirl'])
def test_typed_source_actual_scaling_and_derived_swirl(kind):
    result, receipt = cast_case(kind)
    before = copy.deepcopy((result, receipt))
    validate_mechanism_result_receipt(result, receipt)
    assert (result, receipt) == before
    assert any(row['type'] == 'effect_applied' for row in result['mechanics_trace'])
    if kind in ('def', 'hp', 'em', 'transformative'):
        assert all(not h['matched_effect_refs'] for h in result['hits'])
    elif kind == 'swirl':
        derived = [h for h in result['hits'] if h['damage_system'] == 'transformative']
        assert derived and all(not h['matched_effect_refs'] for h in derived)
        assert any(h['matched_effect_refs'] for h in result['hits'] if h['damage_system'] == 'classic')
    else:
        assert [bool(h['matched_effect_refs']) for h in result['hits']] == [True, False, True]


@pytest.mark.parametrize('fault', ['unused', 'missing', 'duplicate', 'wrong_type', 'unknown', 'projection', 'snapshot', 'input_pin', 'output_pin'])
def test_public_saved_receipt_rejects_resigned_corruption(fault):
    result, receipt = cast_case('transformative' if fault == 'unused' else 'classic')
    first = result['hits'][0]
    if fault == 'unused':
        classic, _ = cast_case()
        first['matched_effect_refs'] = classic['hits'][0]['matched_effect_refs']
    elif fault == 'missing':
        first['matched_effect_refs'] = []
    elif fault == 'duplicate':
        first['matched_effect_refs'].append(copy.deepcopy(first['matched_effect_refs'][0]))
    elif fault in ('wrong_type', 'unknown'):
        first['matched_effect_refs'][0]['benefit_type_id' if fault == 'wrong_type' else 'effect_id'] = 'wrong'
    elif fault == 'projection':
        result['mechanics_trace'] = [r for r in result['mechanics_trace'] if r['type'] != 'effect_applied']
    elif fault == 'snapshot':
        result['mechanics_trace'] = [r for r in result['mechanics_trace'] if r['type'] != 'snapshot_capture']
    elif fault == 'input_pin':
        result['rotation_check']['input_fingerprint'] = '0' * 64
    else:
        receipt['output_fingerprint'] = '0' * 64
    rehash(result, receipt)
    with pytest.raises(ValueError):
        validate_mechanism_result_receipt(result, receipt)


def test_current_consumption_contract_rejects_resigned_default_weight_omission():
    result, receipt = cast_case()
    assert receipt['mechanisms']['consumption_ledger_contract'] == 'mechanism-consumption-ledger-v2'
    del result['hits'][0]['result_weight']
    rehash(result, receipt)
    with pytest.raises(ValueError):
        validate_mechanism_result_receipt(result, receipt)


def test_same_private_session_reuses_capture_replay_but_not_next_operation():
    result, receipt = executed_case(include_capture_proof=True)
    for _ in range(2):
        with ExecutionVerificationSession() as session:
            captures.validate_snapshot_capture_result_receipt(result, receipt, _session=session)
            validate_mechanism_result_receipt(result, receipt, _session=session)
            usage = session.report()['usage']
            assert usage['executions'] == 1 and usage['cache_hits'] == 1


def queue_case(values, benefits, *, matches=None, tuning=None):
    """Actual queue control only: these synthetic rules do not claim source approval."""
    template, rotation, model = values
    for character in template['characters']:
        character['stats']['energy_recharge'] = 1
    add(model, *benefits)
    rotation, _ = resolve_rotation_model(template, rotation)
    output = execute_mechanics(template, rotation, model, matches=matches or [])
    result = describe(output, template, rotation, model, matches or [])
    context = {'plan': {'rotation_model': rotation, 'rotation_tuning': {'buff_hit_matches': tuning or []}}}
    execution = {'model': model, 'template': template}
    receipt = {'output_fingerprint': fingerprint(output['resolved_template'])}
    verify_mechanism_consumption(context, execution, result, receipt)
    return context, execution, result, receipt


@pytest.mark.parametrize('value', [0, .2, {'kind': 'stat', 'subject': 'owner', 'stat': 'atk', 'factor': .001, 'sample': 'current', 'max': 0}])
def test_zero_and_capped_formula_dependencies_remain_required(value):
    benefit = 'benefit.dmg_bonus.all' if isinstance(value, dict) else 'benefit.stat_pct.atk'
    context, execution, result, receipt = queue_case(fixture((.2,)), [rule('auto.mechanism.test', benefit, value)])
    assert result['hits'][0]['matched_effect_refs']
    result['hits'][0]['matched_effect_refs'] = []
    with pytest.raises(ValueError):
        verify_mechanism_consumption(context, execution, result, receipt)


@pytest.mark.parametrize('benefit,expected', [('benefit.res_shred.pyro', False), ('benefit.res_shred.cryo', True),
    ('benefit.reaction_bonus.swirl', True), ('benefit.reaction_bonus.overload', False)])
def test_actual_reaction_and_element_stat_filter(benefit, expected):
    domain = 'enemy' if '.res_shred.' in benefit else 'self'
    _, _, result, _ = queue_case(swirl_setup(), [rule('auto.mechanism.test', benefit, .2, domain=domain)])
    child = next(h for h in result['hits'] if h['damage_system'] == 'transformative')
    assert bool(child['matched_effect_refs']) is expected


def test_automatic_amplify_uses_actual_derived_reaction_not_pretemplate():
    _, execution, result, _ = queue_case(amplify_setup(('hydro', 'pyro'), (.2, .3)),
        [rule('auto.mechanism.test', 'benefit.em.all', 100)])
    assert all('reaction' not in h for h in execution['template']['hits'])
    assert not result['hits'][0]['matched_effect_refs']
    assert result['hits'][1]['matched_effect_refs']


def test_reconstructed_mechanics_matches_and_mutation_are_bound():
    tuning = [{'id': 'choose', 'state': 'confirmed', 'mode': 'replace', 'hit_ids': ['h1'],
        'effect_ref': {'buff_id': 'auto.mechanism.test', 'effect_id': 'value'}}]
    matches = [{'rule_id': 'auto.mechanism.test', 'effect_id': 'value', 'hit_id': h, 'operation': op}
               for h, op in [('h1', 'include'), ('h0', 'exclude')]]
    context, execution, result, receipt = queue_case(fixture((.2, .3)),
        [rule('auto.mechanism.test', 'benefit.stat_pct.atk', .2)], matches=matches, tuning=tuning)
    assert [bool(h['matched_effect_refs']) for h in result['hits']] == [False, True]
    context['plan']['rotation_tuning']['buff_hit_matches'][0]['hit_ids'] = ['h0']
    with pytest.raises(ValueError):
        verify_mechanism_consumption(context, execution, result, receipt)


def test_template_selection_is_rebuilt_without_becoming_a_mechanics_match():
    result, receipt = cast_case(template_tuning=True)
    validate_mechanism_result_receipt(result, receipt)
    assert [any(ref['effect_id'].startswith('manual:bf:bonus') for ref in h['matched_effect_refs'])
            for h in result['hits']] == [True, False, False]
    result['mechanism_replay_context']['plan']['rotation_tuning']['buff_hit_matches'][0]['hit_ids'] = ['old-snapshot']
    receipt['mechanisms']['replay_context_fingerprint'] = fingerprint(result['mechanism_replay_context'])
    rehash(result, receipt)
    with pytest.raises(ValueError):
        validate_mechanism_result_receipt(result, receipt)


def test_fixed_then_rule_match_order_reconstructed_despite_declaration_order():
    tuning = [
        {'id': 'rule', 'state': 'confirmed', 'mode': 'adjust', 'include_hit_ids': [], 'exclude_hit_ids': ['h1'],
         'effect_ref': {'buff_id': 'auto.mechanism.test', 'effect_id': 'value'}},
        {'id': 'fixed', 'state': 'confirmed', 'mode': 'adjust', 'include_hit_ids': ['h0'], 'exclude_hit_ids': ['h1'],
         'effect_ref': {'fixed_source_id': 'review-fixed', 'effect_id': 'ice'}}]
    matches = [{'fixed_source_id': 'review-fixed', 'effect_id': 'ice', 'hit_id': hid, 'operation': op}
        for hid, op in [('h0', 'include'), ('h1', 'exclude')]]
    matches.append({'rule_id': 'auto.mechanism.test', 'effect_id': 'value', 'hit_id': 'h1', 'operation': 'exclude'})
    context, execution, result, receipt = queue_case(fixed_inputs(('cryo', 'hydro', 'physical')),
        [rule('auto.mechanism.test', 'benefit.stat_pct.atk', .2)], matches=matches, tuning=tuning)
    assert result['target_condition_trace'][0]['override'] == 'include'
    context['plan']['rotation_tuning']['buff_hit_matches'][1]['exclude_hit_ids'] = []
    with pytest.raises(ValueError):
        verify_mechanism_consumption(context, execution, result, receipt)


def test_replay_budget_refusal_is_not_replaced_by_arithmetic_only(monkeypatch):
    result, receipt = cast_case('transformative')
    session = verification.ExecutionVerificationSession(budget={'policy': 'execution-verification-budget-v2', 'limits': {'executions': 0}})
    with pytest.raises(verification.ExecutionVerificationError) as failure:
        validate_mechanism_result_receipt(result, receipt, _session=session)
    assert failure.value.code == 'verification_budget'


@pytest.mark.parametrize('reaction', ['aggravate', 'spread'])
def test_explicit_additive_reaction_preserves_em_dependency(reaction):
    values = fixture((.2,))
    values[0]['hits'][0].update(element='electro' if reaction == 'aggravate' else 'dendro', reaction={'type': reaction})
    _, _, result, _ = queue_case(values, [rule('auto.mechanism.test', 'benefit.em.all', 100)])
    assert result['hits'][0]['matched_effect_refs']
