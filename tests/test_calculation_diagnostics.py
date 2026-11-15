import copy

import pytest

from genshin_app.calculation_diagnostics import inspect_calculation_diagnostics
from genshin_app.equipment.schema import PlanError


def failed():
    return {'code': 1, 'reason': 'mechanics_pending', 'details': {'status': 'pending',
        'issues': [{'code': 'unknown_condition', 'path': 'benefit', 'event_id': f'h{i}'} for i in range(7)],
        'mechanics_trace': [], 'result': None, 'resolved_template': None}}


def test_no_damage_pending_error_can_be_inspected_without_promotion():
    value = failed(); before = copy.deepcopy(value)
    out = inspect_calculation_diagnostics(value)
    assert out['execution_status'] == 'pending'
    assert out['items'][0]['count'] == 7 and out['validation_scope'] == 'unverified_saved_execution_error'
    assert out['items'][0]['examples'][0]['json_pointer'] == '/details/issues/0'
    assert 'summary' not in out and value == before


def test_cursor_also_binds_full_saved_execution_not_only_same_issues():
    value = failed()
    first = inspect_calculation_diagnostics(value, {'view':'issues', 'limit':2})
    next_page = inspect_calculation_diagnostics(value, {'view':'issues', 'cursor':first['next_cursor']})
    assert next_page['items'][0]['index'] == 2
    value['details']['mechanics_trace'] = [{'type':'new'}]
    with pytest.raises(PlanError, match='输入已变化'):
        inspect_calculation_diagnostics(value, {'view':'issues', 'cursor':first['next_cursor']})


def test_malformed_calculated_package_cannot_fall_back_to_unverified_issues():
    value = failed()['details']; value['result_receipt'] = {'result_fingerprint':'fake'}
    with pytest.raises(PlanError): inspect_calculation_diagnostics(value)
    value = failed(); value['reason'] = 'arbitrary'
    with pytest.raises(PlanError): inspect_calculation_diagnostics(value)


def test_raw_invalid_execution_with_zero_issues_still_stays_invalid():
    value = {'status':'invalid', 'issues':[], 'mechanics_trace':[], 'result':None}
    out = inspect_calculation_diagnostics(value)
    assert out['execution_status'] == 'invalid' and out['raw_issue_count'] == 0
    assert out['validation_scope'] == 'unverified_saved_execution'
