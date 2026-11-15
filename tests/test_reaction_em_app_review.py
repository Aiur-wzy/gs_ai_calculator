"""Independent actual C3 App source/receipt checks; no live model connection."""
import copy

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.reaction_em_receipt import validate_reaction_em_receipt
from genshin_app.equipment.schema import PlanError
from tests.test_reaction_em_app import em_plan as em_plan  # noqa: PLC0414
from tests.test_reaction_em_app import prepared


def test_one_adoption_has_stable_source_context_and_component_on_recompose(em_plan):
    plan, db, _, _ = em_plan
    plan = prepared(plan, db)
    first = run_plan_calc(plan, db)
    again = compose_equipment_plan(first['plan'], db)
    assert again['state'] == 'ready', again['missing_inputs']
    for field in ('source_fingerprint', 'mechanics_model', 'upstream_compilations'):
        assert again[field] == first['report'][field]
    assert not first['plan']['mechanics_model']['attribute_model']['effect_classifications']


def test_removed_equipment_does_not_reanimate_saved_dynamic_rule_or_classification(em_plan):
    plan, db, _, _ = em_plan
    first = run_plan_calc(prepared(plan, db), db)
    native = copy.deepcopy(first['plan'])
    native['equipment_inputs']['a']['artifact_sets'] = []
    report = compose_equipment_plan(native, db)
    assert not report['mechanics_model']['rules']
    assert not report['mechanics_model']['attribute_model']['effect_classifications']
    assert report['upstream_compilations'][0]['status'] == 'inactive'
    native = prepare_attribute_review(native, '重新核对移除套装后的给定初始面板', db)['plan']
    out = run_plan_calc(native, db)
    assert not out['result'].get('reaction_em_compilations')
    assert not out['result'].get('reaction_em_execution')
    validate_reaction_em_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])


def test_offfield_holder_has_executable_source_but_zero_actual_activation(em_plan):
    plan, db, _, _ = em_plan
    from tests.test_mechanics_execution import ability, action
    plan['rotation_model']['abilities'].append(ability('switch_b', 'a', 'action.switch'))
    plan['rotation_model']['actions'].append(action('leave', 'switch_b', .15, .16, 20, switch_to='b', repeat={'mode': 'once'}))
    out = run_plan_calc(prepared(plan, db), db)
    assert out['result']['reaction_em_compilations'][0]['status'] == 'ready'
    assert out['result']['reaction_em_execution']['activations'] == []
    assert not any(r['type'] == 'activation' for r in out['result']['mechanics_trace'])
    validate_reaction_em_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])


def test_final_gate_checks_dynamic_reader_even_with_no_declared_output_use(em_plan):
    from tests.test_mechanics_execution import rule
    plan, db, _, _ = em_plan
    reader = rule('reader', 'benefit.base_add.all', {'kind': 'stat', 'subject': 'owner', 'stat': 'em', 'basis': 'nonextra', 'sample': 'current', 'factor': 0})
    plan['mechanics_model']['rules'].append(reader)
    plan['mechanics_model']['initial_state']['rules'].append({'rule_id': 'reader', 'cooldown_remaining': 0, 'instances': []})
    report = compose_equipment_plan(prepared(plan, db), db)
    assert report['state'] == 'pending'
    assert any(i['code'] == 'reaction_em_reader_scope' for i in report['missing_inputs'])
    compiled = report['upstream_compilations'][0]
    assert compiled['status'] == 'pending' and compiled['rules'] == compiled['attribute_writes'] == []


def test_changed_actual_parent_is_rejected_after_all_local_occurrence_hashes_updated(em_plan):
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    result, receipt = out['result'], out['result_receipt']
    parent = next(r for r in result['mechanics_trace'] if r['type'] == 'elemental_application' and r['outcome'] == 'reaction')
    parent['allowed'] = False
    receipt['result_fingerprint'] = fingerprint(result)
    receipt['reaction_em']['actual_occurrences_fingerprint'] = fingerprint(result['reaction_em_execution'])
    with pytest.raises(ValueError):
        validate_reaction_em_receipt(out['plan'], result, out['report'], receipt)

@pytest.mark.parametrize('reaction_case', ['none', 'unsupported_freeze'])
def test_no_reaction_and_unsupported_reaction_have_distinct_execution_outcomes(em_plan, reaction_case):
    plan, db, _, _ = em_plan
    elemental = plan['mechanics_model']['elemental_model']
    if reaction_case == 'none':
        elemental['target']['initial_aura'] = None
    else:
        elemental['target']['initial_aura']['element'] = 'hydro'
        for hit, descriptor in zip(plan['manual_template']['hits'], plan['mechanics_model']['hit_events'], strict=True):
            hit['element'] = 'cryo'
            descriptor['application']['profile_id'] = 'cryo'
    plan = prepared(plan, db)
    if reaction_case == 'none':
        out = run_plan_calc(plan, db)
        assert out['result']['reaction_em_execution']['activations'] == []
        assert [hit['expected'] for hit in out['result']['hits']] == [45, 45]
        validate_reaction_em_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])
    else:
        with pytest.raises(PlanError) as error:
            run_plan_calc(plan, db)
        assert error.value.reason == 'mechanics_pending'
        assert error.value.details['result'] is None


def test_three_real_exports_reconstruct_complete_source_and_occurrence_json(em_plan, tmp_path):
    import json

    from genshin_app.equipment.service import result_bytes
    from genshin_app.export import export_result
    from genshin_app.formula_workbook import build_formula_workbook
    from tests.test_reaction_benefits_exports import NS, sheets
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    target = tmp_path / 'c3-independent.xlsx'
    # Modern calculated results carry a formal ownership proof even when no
    # override claims were needed.  Keep the real export on the audited path:
    # the result receipt is part of that proof chain, not reconstructible
    # export metadata.
    export_result(
        out['result'], 'xlsx', target, receipt=out['result_receipt']
    )
    values = [build_formula_workbook(out), result_bytes(out['result'], out['result_receipt'], 'xlsx'), target.read_bytes()]
    expected = {'sources': out['result']['reaction_em_compilations'], 'execution': out['result']['reaction_em_execution']}
    for encoded in values:
        sheet = sheets(encoded)['反应触发精通']
        raw, collecting = [], False
        for row in sheet.findall('s:sheetData/s:row', NS):
            text = ''.join(row.itertext())
            if text == '完整来源及实际发生证明（按序拼接JSON）':
                collecting = True
            elif collecting:
                raw.append(text)
        assert raw and json.loads(''.join(raw)) == expected
        assert sheet.find('s:sheetProtection', NS) is not None
        assert not sheet.findall('.//s:f', NS)


def test_missing_frontline_activation_cannot_be_laundered_into_valid_empty_occurrence_proof(em_plan):
    from genshin_app.equipment.reaction_em_execution import verify_reaction_em_execution
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    result, receipt = out['result'], out['result_receipt']
    assert len(result['reaction_em_execution']['activations']) == 2
    rules = {r['id'] for record in result['reaction_em_compilations'] for r in record['rules']}
    result['mechanics_trace'] = [row for row in result['mechanics_trace'] if not (row['type'] == 'activation' and row.get('rule_id') in rules)]
    with pytest.raises(PlanError):
        verify_reaction_em_execution({'mechanics_trace': result['mechanics_trace']}, result['reaction_em_compilations'])
    with pytest.raises(PlanError):
        verify_reaction_em_execution({'mechanics_trace': result['mechanics_trace'], 'rotation_check': result['rotation_check']}, result['reaction_em_compilations'])
    result['reaction_em_execution']['activations'] = []
    receipt['result_fingerprint'] = fingerprint(result)
    receipt['reaction_em']['actual_occurrences_fingerprint'] = fingerprint(result['reaction_em_execution'])
    with pytest.raises(ValueError):
        validate_reaction_em_receipt(out['plan'], result, out['report'], receipt)


def test_determined_result_cannot_hide_missing_activations_by_marking_parent_queue_pending(em_plan):
    from genshin_app.equipment.reaction_em_execution import verify_reaction_em_execution
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    result, receipt = out['result'], out['result_receipt']
    rules = {r['id'] for record in result['reaction_em_compilations'] for r in record['rules']}
    result['mechanics_trace'] = [row for row in result['mechanics_trace'] if not (row['type'] == 'activation' and row.get('rule_id') in rules)]
    for row in result['rotation_check']['event_log']:
        if row['type'] == 'mechanics_hit':
            row['status'] = 'pending'
    # This is a determined saved result, not a diagnostic of a failed prefix.
    result['reaction_em_execution'] = verify_reaction_em_execution({'mechanics_trace': result['mechanics_trace'], 'rotation_check': result['rotation_check']}, result['reaction_em_compilations'])
    receipt['result_fingerprint'] = fingerprint(result)
    receipt['reaction_em']['actual_occurrences_fingerprint'] = fingerprint(result['reaction_em_execution'])
    with pytest.raises(ValueError):
        validate_reaction_em_receipt(out['plan'], result, out['report'], receipt)


def test_completed_application_cannot_lose_both_reaction_and_activation_records(em_plan):
    from genshin_app.equipment.reaction_em_execution import verify_reaction_em_execution
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    result, receipt = out['result'], out['result_receipt']
    rules = {r['id'] for record in result['reaction_em_compilations'] for r in record['rules']}
    result['mechanics_trace'] = [row for row in result['mechanics_trace'] if row['type'] != 'elemental_reaction' and not (row['type'] == 'activation' and row.get('rule_id') in rules)]
    assert any(row['type'] == 'elemental_application' and row.get('reaction') for row in result['mechanics_trace'])
    with pytest.raises(ValueError):
        result['reaction_em_execution'] = verify_reaction_em_execution({'mechanics_trace': result['mechanics_trace'], 'rotation_check': result['rotation_check']}, result['reaction_em_compilations'])
        receipt['result_fingerprint'] = fingerprint(result)
        receipt['reaction_em']['actual_occurrences_fingerprint'] = fingerprint(result['reaction_em_execution'])
        validate_reaction_em_receipt(out['plan'], result, out['report'], receipt)
