"""Actual lifecycle execution is distinct from threshold observation and Buff delivery."""
import copy

import pytest

from genshin_app.export import resource_threshold_rows, state_runtime_rows
from genshin_app.resource_threshold_reporting import threshold_outcome
from genshin_engine.mechanics import execute_mechanics
from tests.test_resource_threshold_lifecycle_execution import life_case


@pytest.mark.parametrize('end', [False, True])
def test_actual_threshold_lifecycle_reports_keep_source_and_target_identity(end):
    out = execute_mechanics(*life_case(end=end))
    assert out['result'] is not None, out['issues']
    before = copy.deepcopy(out)
    field = 'resource_threshold_triggers'
    rows = resource_threshold_rows(out, field)
    trace = out[field]['trace']
    assert '目标实例' in rows[1]
    actual = rows[2:2 + len(trace)]
    assert actual[0][3] == '领域已开始'
    assert actual[0][2].endswith(trace[0]['source_run_id'])
    assert actual[0][-2:] == ['field_b', trace[0]['target_generation_uid']]
    assert actual[0][9:12] == [8, 16, '事件后']
    if end:
        assert actual[1][3] == '领域已结束'
    assert all(row[3] != '收益已激活' for row in actual)
    assert out == before


def test_clock_control_completion_is_not_just_counter_or_buff_application():
    out = execute_mechanics(*life_case(counter=False))
    assert out['result'] is not None, out['issues']
    rows = state_runtime_rows(out)
    values = [value for row in rows for value in row]
    assert '领域控制已提交' in values and '控制批次已完成' in values
    assert '累计控制已提交' not in values
    assert '收益已激活' in values  # Separate actual timer activation exists.
    assert '控制序号' in values and '实例标识' in values


@pytest.mark.parametrize('outcome, label', [('lifecycle_kept', '保留原领域'),
    ('no_active_instance', '没有活动领域，无需结束')])
def test_lifecycle_noncreation_outcomes_are_not_presented_as_new_buffs(outcome, label):
    row = {'type': 'threshold_dispatch_completed', 'outcome': outcome}
    assert threshold_outcome(row, 'resource_threshold_triggers') == label
    assert threshold_outcome({**row, 'type': 'issued'}, 'resource_threshold_triggers') == '执行过程未知'


def test_failed_control_batch_retains_real_prefix_without_claiming_completed_batch():
    rows = state_runtime_rows({'state_runtime': {'contract': 'finite-state-mechanics-bridge-v2',
        'execution_trace': [{'type': 'state_delivery_failed', 'time_us': 3,
            'batch_id': 'batch-2', 'control_sequence': 7, 'applied_steps': 2,
            'complete': False, 'message': '目标缺少资格'}]}})
    row = next(row for row in rows if len(row) > 5 and row[1] == '控制交付失败')
    assert row[5] == '失败' and row[9:12] == ['目标缺少资格', 7, 'batch-2 / 2']


def test_lifecycle_failure_without_target_instance_still_reports_real_cause():
    row = {'type': 'threshold_dispatch_failed', 'outcome': 'pending',
        'lifecycle_id': 'field_b', 'message': '当前控制批尚未交付完'}
    rows = resource_threshold_rows({'resource_threshold_triggers': {'trace': [row]}},
        'resource_threshold_triggers')
    assert rows[2][3] == '派发失败：待核实；当前控制批尚未交付完'
    assert rows[2][-2:] == ['field_b', None]
