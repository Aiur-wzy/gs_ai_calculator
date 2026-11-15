"""Finite validation and repeated steady state are separate user-facing facts."""
import pytest

from genshin_app.formula_workbook import _rotation_summary_label


@pytest.mark.parametrize(('required', 'label'), [('complete', '通过'), ('pending', '待验证'), ('failed', '未通过')])
def test_finite_completion_never_borrows_the_overall_unproven_label(required, label):
    value = {'result': {'summary': {'total_damage': 1}, 'rotation_check': {'status': 'unproven'},
        'damage_report_window': {'contract': 'damage-report-window-v1', 'status': 'complete',
            'start': 0, 'end': 1, 'execution_period': 2, 'boundary': 'inclusive_time',
            'required_validation': {'status': required}, 'optional_audit': {'status': 'complete'}}}}
    assert _rotation_summary_label(value) == f'本窗{label}；稳态未证明'


@pytest.mark.parametrize(('status', 'label'), [('passed', '通过'), ('not_checked', '未检查'), ('unproven', '尚未证明')])
def test_legacy_without_finite_window_keeps_its_actual_check_status(status, label):
    assert _rotation_summary_label({'result': {'rotation_check': {'status': status}}}) == '导出时状态：' + label
