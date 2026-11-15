"""Preserved original half-open and interrupted-prefix window regressions."""
import copy
import json
from pathlib import Path

import pytest

from genshin_app.export import beneficiary_eligibility_rows

HERE = Path(__file__).resolve().parent


def result():
    data = json.loads((HERE / 'fixtures/beneficiary_three_cycle_trace.json').read_text('utf8'))
    return {'buff_state_timeline': {**data['report_window'], 'states': []},
            'mechanics_trace': data['qualified_trace_records']}


def decision_rows(data):
    return [r for r in beneficiary_eligibility_rows(data) if r and r[0] == '规则']


def test_actual_cycle_diagnostics_are_not_current_window_decisions():
    value = result()
    before = copy.deepcopy(value)
    decisions = decision_rows(value)
    assert [r[3] for r in decisions] == [.12, .5, 1, 1.3]
    assert value == before


@pytest.mark.parametrize('time,shown', [(0, True), (9.999999, True), (10, False), (10.000001, False), (-.1, False)])
def test_processing_time_uses_half_open_report_window(time, shown):
    value = result()
    row = copy.deepcopy(value['mechanics_trace'][1])
    row['t'] = time
    row['beneficiary_selection']['selection_t'] = .5
    value['mechanics_trace'] = [row]
    assert bool(decision_rows(value)) is shown


def test_recorded_limit_event_is_not_discarded_by_strict_limit_cutoff():
    value = result()
    value['buff_state_timeline']['execution_limit'] = .5
    value['buff_state_timeline']['execution_status'] = 'pending'
    value['mechanics_trace'] = [row for row in value['mechanics_trace'] if row['t'] <= .5]
    assert [r[3] for r in decision_rows(value)] == [.12, .5]
