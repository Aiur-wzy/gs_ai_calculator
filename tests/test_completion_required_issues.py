"""Current required evidence outranks a stale window; optional audits stay visible."""
import copy

import pytest

from genshin_app.calculation_completion import calculation_completion
from genshin_app.whiteboard_equipment import _diagnostics
from tests.test_calculation_completion import finite


def scoped():
    value = finite()
    value['result']['damage_report_window'] = {
        'contract': 'damage-report-window-v1', 'status': 'complete',
        'start': 0, 'end': 1, 'execution_period': 2, 'boundary': 'inclusive_time',
        'required_validation': {'status': 'complete'},
        'optional_audit': {'status': 'not_requested'},
    }
    return value


@pytest.mark.parametrize('scope', [{}, {'cycle': 0}, {'origin': 0},
                                  {'cycle': None}, {'cycle': '1'}, {'cycle': True}, {'cycle': -1}])
def test_current_or_unknown_scope_cannot_hide_behind_complete_window(scope):
    value = scoped()
    row = {'code': 'actual_unknown', 'message': '真实必需输入未知', 'severity': 'pending', **scope}
    value['result'].update(mechanics_status='pending', mechanics_issues=[row])
    before = copy.deepcopy(value)
    completion = calculation_completion(value)
    assert completion['task_admission'] != 'accepted'
    assert 'required_execution_issue' in completion['reasons']
    assert value == before
    projected = next(item for item in _diagnostics(value, energy_intent='multi_round_legal')
                     if item['code'] == row['code'])
    assert projected['blocking'] and projected['required']


@pytest.mark.parametrize('scope', [{'cycle': 1}, {'origin': 2}, {'cycle': 1, 'origin': 0}])
def test_explicit_later_audit_failure_is_visible_without_rejecting_required_window(scope):
    value = scoped()
    value['result'].update(mechanics_status='failed', mechanics_issues=[
        {'code': 'later_audit', 'message': '后继检查失败', 'severity': 'error', **scope}])
    value['result']['rotation_check']['status'] = 'failed'
    before = copy.deepcopy(value)
    completion = calculation_completion(value)
    assert completion['task_admission'] == 'accepted'
    assert completion['required_validation'] == 'complete'
    assert completion['optional_audit'] == 'failed'
    assert completion['steady_state'] == 'unproven'
    rows = _diagnostics(value, energy_intent='multi_round_legal')
    assert not any(row['blocking'] for row in rows)
    audit = next(row for row in rows if row['code'] == 'later_audit')
    assert audit['audit_scope'] == 'optional' and audit['original_severity'] == 'error'
    assert audit['severity'] == 'warning' and '后继审计' in audit['message']
    assert value == before


def test_invalid_window_cannot_make_a_later_cycle_nonrequired():
    value = scoped()
    value['result']['damage_report_window']['boundary'] = 'unknown'
    value['result']['mechanics_issues'] = [{'code': 'bad', 'severity': 'error', 'cycle': 1}]
    assert calculation_completion(value)['task_admission'] != 'accepted'


@pytest.mark.parametrize('severity', ['warning', 'info', 'notice'])
def test_advisory_does_not_become_a_required_repair(severity):
    value = scoped()
    value['result']['mechanics_issues'] = [{'code': 'advisory', 'severity': severity, 'cycle': 0}]
    assert calculation_completion(value)['task_admission'] == 'accepted'


def test_time_after_cutoff_does_not_exempt_a_required_origin_zero_issue():
    value = scoped()
    value['result']['mechanics_issues'] = [{'code': 'required', 'severity': 'error', 't': 1.5, 'origin': 0}]
    assert calculation_completion(value)['task_admission'] != 'accepted'


def test_incomplete_first_cycle_cannot_be_disguised_as_optional_audit_failure():
    value = scoped()
    value['result']['mechanics']['complete_first_cycle'] = False
    value['result']['damage_report_window']['optional_audit']['status'] = 'failed'
    assert calculation_completion(value)['task_admission'] != 'accepted'
