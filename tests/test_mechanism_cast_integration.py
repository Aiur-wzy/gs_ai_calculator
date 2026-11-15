"""V2 App -> typed compiler -> actual D20 -> saved-material rebuild, no live DB."""
import copy

import pytest

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanism_receipt import (
    _replay,
    make_mechanism_receipt,
    mechanism_summary,
    validate_mechanism_result_receipt,
)
from genshin_app.equipment.mechanism_source_schema import DEFAULT_SOURCE
from genshin_app.equipment.mechanism_sources import (
    _compile_historical_mechanism_replay,
    make_replay_context,
)
from genshin_app.rotation_check import describe_rotation_check, resolve_rotation_model
from genshin_engine.mechanics import execute_mechanics
from tests.test_mechanics_execution import EVIDENCE, hit
from tests.test_mechanism_cast_graph import identity, inputs
from tests.test_mechanism_cast_qualification import cast_case
from tests.test_snapshot_captures import capture


def scene():
    plan, template, binding, _ = inputs(True)
    d, a = cast_case(True, 'post')
    snapshot = a['source_proof']['snapshot']
    material = {'status': 'ready', 'source_status': 'ready', 'review_status': 'approved', 'materialization_status': 'ready',
                'snapshot': snapshot, 'descriptor': d, 'source_proof': a['source_proof'], 'facts': a['facts'],
                'resolved_parameters': a['parameters'], 'authorization_fingerprint': 'a'*64,
                'record_payload_fingerprint': fingerprint(d),
                'source_binding': {'subject': a['source_proof']['subject'], 'selection': {}}}
    source = copy.deepcopy(DEFAULT_SOURCE)
    source.update(binding, id='set-source', mechanism_id=d['mechanism_id'])
    source['scenario']['values'] = copy.deepcopy(a['facts'][-1]['claim']['scenario_requirements'])
    plan['mechanism_sources'] = [source]
    gear = plan['equipment_inputs']['a']
    gear.update(data_version={'version_line': 'release', 'upstream_commit': snapshot['upstream_commit']},
                artifact_sets=[{'set_id': 15007, 'count': 4}])
    template['characters'][0]['stats']['base_atk'] = 100
    template['characters'][0]['stats']['energy_recharge'] = 1
    template['hits'] = [hit('live', .2), hit('old-snapshot', .2), hit('capture-snapshot', .2)]
    plan['rotation_model']['actions'][0]['occupation_end'] = 1
    plan['rotation_model']['events'][0].update(t=0, order=1, repeat={'mode': 'once'}, **EVIDENCE)
    plan['rotation_model']['initial_cooldowns'] = [{'character': 'a', 'group': 'q', 'ready_in': []}]
    model = plan['mechanics_model']
    model['hit_events'] = [{'hit_id': h['id'], 'action_id': 'cast', 'order': 10+i} for i,h in enumerate(template['hits'])]
    model['hit_events'][1]['self_snapshot_event'] = 'cast'
    model['hit_events'][2]['self_snapshot_event'] = 'sample'
    model['snapshot_captures'] = [capture(offset=.1)]
    req = {'binding_id': 'set-four', 'provider_character_id': 'a',
           'owner': {'kind': 'artifact_set', 'set_id': 15007, 'required_count': 4, 'affix_id': 2150071},
           'effect': {'affix_id': 2150071, 'source_level': 1}, 'source_fingerprint': 'b'*64}
    return plan, template, material, [req]


def compose(plan, template, material, requirements):
    issues = []
    result = _compile_historical_mechanism_replay(
        plan, template, None, equipment_requirements=requirements,
        missing=lambda *args: issues.append(args), source_provider=lambda b: copy.deepcopy(material),
        cast_identity_provider=identity)
    return (*result, issues)


def accepted():
    plan, template, material, requirements = scene()
    _, records, _, _ = compose(plan, template, material, requirements)
    assert records[0]['required_confirmation'], records[0]['issues']
    plan['mechanism_sources'][0].update(records[0]['required_confirmation'])
    model, records, _, issues = compose(plan, template, material, requirements)
    assert not issues and model is not None, issues
    return plan, template, material, requirements, model, records


def test_targetless_cast_equipment_and_three_sampling_modes_execute_then_rebuild_offline():
    plan, template, _, requirements, model, records = accepted()
    assert records[0]['compiler'] == 'typed-mechanism-app-v2'
    assert records[0]['equipment_coverage'][0]['binding_id'] == 'set-four'
    out = execute_mechanics(template, plan['rotation_model'], model)
    assert out['result'] is not None, out['issues']
    assert [r['expected'] for r in out['result']['hits']] == pytest.approx([54.00000013411045, 45, 54.00000013411045])
    context = make_replay_context(plan, template, records=records, action_event_compilations=[],
                                 attack_metadata_compilations=[], parameter_compilations=[], equipment_requirements=requirements)
    assert context['schema_version'] == 2
    assert _replay(records, context) == model


def test_axis_shift_keeps_acceptance_but_revalidates_execution():
    plan, template, material, requirements, model, records = accepted()
    before = records[0]
    plan['rotation_model']['actions'][0].update(start=1, occupation_end=2)
    plan['rotation_model']['events'][0]['t'] = 1
    model, records, _, issues = compose(plan, template, material, requirements)
    assert not issues and model is not None
    assert before['acceptance_fingerprint'] == records[0]['acceptance_fingerprint']
    assert before['execution_fingerprint'] != records[0]['execution_fingerprint']


@pytest.mark.parametrize('fault', ['node', 'missing_context', 'missing_identity', 'missing_equipment'])
def test_saved_context_cannot_drop_cast_or_equipment_proofs(fault):
    plan, template, _, requirements, _, records = accepted()
    context = make_replay_context(plan, template, records=records, action_event_compilations=[],
                                 attack_metadata_compilations=[], parameter_compilations=[], equipment_requirements=requirements)
    if fault == 'node':
        records[0]['parent_graph']['dependencies'][0]['identity_proof']['nodes']['depot']['raw']['energySkill'] = 92
    elif fault == 'missing_identity':
        del records[0]['parent_graph']['dependencies'][0]['identity_proof']
    elif fault == 'missing_context':
        del context['schema_version']
    else:
        context['equipment_requirements'] = []
    with pytest.raises((ValueError, KeyError)):
        _replay(records, context)


def cast_result():
    plan, template, material, requirements = scene()
    template['characters'][0]['stats']['energy_recharge'] = 1
    _, records, _, _ = compose(plan, template, material, requirements)
    plan['mechanism_sources'][0].update(records[0]['required_confirmation'])
    model, records, _, issues = compose(plan, template, material, requirements)
    assert not issues
    rotation, bindings = resolve_rotation_model(template, plan['rotation_model'])
    executed = execute_mechanics(template, rotation, model)
    result = executed['result']
    assert result is not None, executed['issues']
    result.update(rotation_check=describe_rotation_check(executed['rotation_check'], template, rotation, bindings,
                                                        mechanics_model=model, matches=[]),
                  mechanics_trace=executed['mechanics_trace'], sampling_trace=executed['sampling_trace'],
                  mechanism_compilations=records, mechanism_execution={'model': model, 'template': template})
    result['mechanism_replay_context'] = make_replay_context(plan, template, records=records,
        action_event_compilations=[], attack_metadata_compilations=[], parameter_compilations=[], equipment_requirements=requirements)
    result['mechanism_summary'] = mechanism_summary(result)
    report = {'mechanics_model': model, 'mechanics_input_template': template}
    receipt = {'mechanisms': make_mechanism_receipt(plan, report, result), 'result_fingerprint': fingerprint(result)}
    return result, receipt


def test_result_only_cast_receipt_does_not_require_old_contact_receipt():
    result, receipt = cast_result()
    assert 'action_events' not in receipt
    validate_mechanism_result_receipt(result, receipt)


@pytest.mark.parametrize('changed', ['rotation', 'template', 'model'])
def test_resigning_execution_wrapper_cannot_disconnect_cast_inputs(changed):
    result, receipt = cast_result()
    proof = receipt['mechanisms']
    if changed == 'rotation':
        result['rotation_check']['resolved_rotation_model']['actions'][0]['start'] = 2
        proof['execution_rotation_model_fingerprint'] = fingerprint(result['rotation_check']['resolved_rotation_model'])
    elif changed == 'template':
        result['mechanism_execution']['template']['characters'][0]['stats']['atk'] = 999
        proof['mechanics_input_template_fingerprint'] = fingerprint(result['mechanism_execution']['template'])
    else:
        result['mechanism_execution']['model']['snapshot_captures'][0]['offset'] = .2
        proof['compiled_mechanics_model_fingerprint'] = fingerprint(result['mechanism_execution']['model'])
    from genshin_app.equipment.mechanism_receipt import mechanism_ledger_fingerprint
    proof['ledger_fingerprint'] = mechanism_ledger_fingerprint(result)
    receipt['result_fingerprint'] = fingerprint(result)
    with pytest.raises(ValueError):
        validate_mechanism_result_receipt(result, receipt)
