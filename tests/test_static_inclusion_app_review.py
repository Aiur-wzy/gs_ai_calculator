"""Independent static inclusion admission and saved-receipt regressions."""
from __future__ import annotations

import copy

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.static_inclusion_receipt import (
    validate_static_inclusion_receipt,
)
from tests.test_static_inclusion_app import DB, static_plan
from tests.test_static_inclusion_finalization import (
    final_context,  # noqa: F401
    finish,
    snapshot_session,  # noqa: F401
    version,  # noqa: F401
)


@pytest.mark.parametrize('drift', ['given_em', 'set_selection', 'set_count', 'resolved_em'])
def test_final_admission_cannot_attest_stale_selection_or_resolved_panel(final_context, drift):  # noqa: F811
    args, kwargs = copy.deepcopy(final_context)
    if drift == 'given_em':
        args[1]['manual_template']['characters'][0]['stats']['em'] += 1
    elif drift == 'set_selection':
        args[1]['equipment_inputs']['char1']['artifact_sets'][0]['set_id'] = 10001
    elif drift == 'set_count':
        args[1]['equipment_inputs']['char1']['artifact_sets'][0]['count'] = 4
    else:
        args[2]['characters'][0]['stats']['em'] += 1
    try:
        out = finish((args, kwargs))
    except PlanError:
        return
    assert not out['adoptions'], (drift, out['records'][0]['status'])
    assert out['records'][0]['status'] == 'pending'


def test_full_validation_sees_native_requirement_adoption_when_report_signal_is_removed(version):  # noqa: F811
    calculated = run_plan_calc(static_plan(version), DB)
    plan = calculated['plan']
    report = calculated['report']
    result = calculated['result']
    receipt = calculated['result_receipt']
    for value in (result, report, plan['resolution'], report['plan']['resolution']):
        value.pop('static_inclusion_compilations', None)
        value.pop('static_inclusion_adoption', None)
    receipt.pop('static_inclusions', None)
    report['effect_requirements'] = []
    assert any(r.get('resolved_by', {}).get('kind') == 'static_inclusion'
               for r in plan['resolution']['effect_requirements'])
    with pytest.raises(ValueError):
        validate_static_inclusion_receipt(plan, result, report, receipt)


@pytest.mark.parametrize('set_id', [10007, 10001, 15001])
def test_static_admission_changes_no_damage_or_given_panel_against_equipment_free_control(version, set_id):  # noqa: F811
    native = static_plan(version, set_id)
    control = copy.deepcopy(native)
    control['equipment_inputs']['char1']['artifact_sets'] = []
    actual = run_plan_calc(native, DB)
    base = run_plan_calc(control, DB)
    assert actual['result']['hits'] == base['result']['hits']
    assert actual['result']['summary'] == base['result']['summary']
    assert actual['plan']['resolved_template']['characters'] == base['plan']['resolved_template']['characters']
    assert actual['plan']['effect_bindings'] == []
    assert not actual['report'].get('mechanics_model')


@pytest.mark.parametrize('location', ['native', 'report_plan'])
def test_full_receipt_requires_proof_when_only_one_requirement_container_retains_signal(version, location):  # noqa: F811
    out = run_plan_calc(static_plan(version), DB)
    for value in (out['result'], out['report'], out['plan']['resolution'], out['report']['plan']['resolution']):
        value.pop('static_inclusion_compilations', None)
        value.pop('static_inclusion_adoption', None)
    out['result_receipt'].pop('static_inclusions', None)
    original = copy.deepcopy(out['plan']['resolution']['effect_requirements'])
    out['report']['effect_requirements'] = []
    out['plan']['resolution']['effect_requirements'] = []
    out['report']['plan']['resolution']['effect_requirements'] = []
    target = out['plan'] if location == 'native' else out['report']['plan']
    target['resolution']['effect_requirements'] = original
    with pytest.raises(ValueError):
        validate_static_inclusion_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])


@pytest.mark.parametrize('field', ['level', 'policy', 'count', 'mechanics_hit', 'bound_model', 'origin_requirement'])
def test_full_receipt_rejects_actual_input_or_graph_drift_with_same_saved_damage(version, field):  # noqa: F811
    out = run_plan_calc(static_plan(version), DB)
    if field == 'level':
        out['plan']['manual_template']['characters'][0]['level'] -= 1
    elif field == 'policy':
        out['plan']['equipment_inputs']['char1']['panel_policy'] = 'unknown'
    elif field == 'count':
        out['plan']['equipment_inputs']['char1']['artifact_sets'][0]['count'] = 4
    elif field == 'mechanics_hit':
        out['report']['mechanics_input_template'] = copy.deepcopy(out['plan']['resolved_template'])
        out['report']['mechanics_input_template']['hits'][0]['t'] += .1
    elif field == 'bound_model':
        out['report']['mechanics_model'] = {'rules': [], 'fixed_effect_sources': [{'id': 'unknown_source'}]}
    else:
        out['plan']['resolution']['effect_requirements'][0]['owner']['affix_id'] += 1
    with pytest.raises(ValueError):
        validate_static_inclusion_receipt(out['plan'], out['result'], out['report'], out['result_receipt'])
