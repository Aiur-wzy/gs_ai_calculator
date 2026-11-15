"""Shared finite-task completion and exact saved receipt diagnostics."""
import copy
import json

import pytest

from genshin_app.calculation_completion import (
    calculation_completion,
    require_calculation_complete,
)
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.static_inclusion_receipt import (
    StaticInclusionMismatch,
    _same,
)


def finite():
    return {'result': {'summary': {'total_damage': 0, 'dps': 0},
        'mechanics_status': 'unproven', 'mechanics': {'complete_first_cycle': True},
        'rotation_check': {'status': 'unproven', 'action_check': {'status': 'passed'},
            'energy_check': {'status': 'unproven', 'opening_status': 'passed'}}}, 'report': {'state': 'ready'}}


def test_valid_zero_and_finite_unproven_are_complete_without_infinite_proof():
    out = calculation_completion(finite())
    assert out['task_admission'] == 'accepted' and out['steady_state'] == 'unproven'


@pytest.mark.parametrize('total', [float('nan'), float('inf'), -1, True, 10 ** 400])
def test_unavailable_numeric_result_is_a_typed_diagnostic(total):
    calc = finite(); calc['result']['summary']['total_damage'] = total
    out = calculation_completion(calc)
    assert out['calculation_status'] == 'diagnostic'
    assert 'numeric_result_unavailable' in out['reasons']


@pytest.mark.parametrize('required,expected', [('complete', 'accepted'), ('failed', 'failed'), ('pending', 'pending')])
def test_required_and_optional_scopes_have_separate_truth(required, expected):
    calc = finite(); result = calc['result']
    result['mechanics_status'] = 'failed'
    result['rotation_check']['status'] = 'failed'
    result['damage_report_window'] = {'contract': 'damage-report-window-v1', 'status': 'complete', 'end': 1,
        'start': 0, 'execution_period': 2, 'boundary': 'inclusive_time',
        'required_validation': {'status': required}, 'optional_audit': {'status': 'failed'}}
    out = calculation_completion(calc)
    assert out['task_admission'] == expected and out['optional_audit'] == 'failed'


def test_legacy_failure_prefix_cannot_publish_as_complete():
    calc = finite(); calc['result']['mechanics_status'] = 'failed'
    calc['result']['mechanics']['complete_first_cycle'] = False
    with pytest.raises(PlanError) as exc:
        require_calculation_complete(calc, files={'diagnostic': 'saved.json'})
    assert exc.value.reason == 'calculation_incomplete'
    assert exc.value.details['files']['diagnostic'] == 'saved.json'


def test_exact_difference_has_bounded_pointer_expected_and_actual():
    before = {'characters': [{'stats': {'atk': 100}}]}
    after = copy.deepcopy(before); after['characters'][0]['stats']['atk'] = 101
    _same(before, copy.deepcopy(before), '/graph')
    with pytest.raises(StaticInclusionMismatch) as exc:
        _same(after, before, '/graph', phase='current_panel')
    detail = exc.value.details
    assert detail['differences'][0] == {'path': '/graph/characters/0/stats/atk', 'kind': 'value', 'expected': 100, 'actual': 101}
    assert len(json.dumps(detail)) < 2000 and detail['phase'] == 'current_panel'


def test_cli_failed_prefix_writes_typed_diagnostic_then_exits_nonzero(tmp_path, monkeypatch, capsys):
    from genshin_app import equipment
    from genshin_app.cli import main
    from tests.test_calculation_delivery import delivery_plan
    plan = delivery_plan()
    source = tmp_path / 'plan.json'; source.write_text(json.dumps(plan), encoding='utf8')
    target = tmp_path / 'diagnostic.json'
    calculated = finite()
    calculated.update(plan=plan, result_receipt={}, equipment_summary={}, presentation_issues=[])
    calculated['result']['mechanics']['complete_first_cycle'] = False
    calculated['result']['mechanics_status'] = 'failed'
    monkeypatch.setattr('genshin_app.cli._require_runtime_database', lambda _: {})
    monkeypatch.setattr(equipment, 'run_plan_calc', lambda *a, **k: calculated)
    code = main(['--data-dir', str(tmp_path), 'equipment', 'calc', '--plan', str(source), '--output', str(target), '--summary'])
    assert code == 1
    error = json.loads(capsys.readouterr().err)
    assert error['reason'] == 'calculation_incomplete'
    assert error['details']['execution_feedback']['execution'] == 'diagnostic'
    recovery = error['details']['recovery']
    assert recovery['calculated_path'] == str(target)
    saved = json.loads(target.read_text(encoding='utf8'))
    assert saved['kind'] == 'genshin-calculation-diagnostic'
    assert saved['completion']['task_admission'] != 'accepted'
    assert 'diagnostic' in saved and 'result' not in saved
    request = tmp_path / 'request.json'
    request.write_text(json.dumps(recovery['request']), encoding='utf8')
    assert main(['--data-dir', str(tmp_path), 'equipment', 'diagnose', '--calculated', str(target),
                 '--plan', str(source), '--request', str(request)]) == 0
    response = json.loads(capsys.readouterr().out)
    assert response['code'] == 0 and response['data']['authority'] == 'advisory_only'
