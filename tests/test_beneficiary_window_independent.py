"""Independent public display boundary tests from the saved actual execution."""
import copy
import json
from pathlib import Path

import pytest

from genshin_app.export import beneficiary_eligibility_rows, coverage_rows

HERE = Path(__file__).resolve().parent


def result():
    saved = json.loads((HERE / 'fixtures/beneficiary_three_cycle_trace.json').read_text('utf8'))
    return {'coverage_interpretation': {'kind': 'hit_projection', 'message': 'actual fixture'},
            'buff_state_timeline': {**saved['report_window'], 'states': []},
            'mechanics_trace': copy.deepcopy(saved['qualified_trace_records'])}


def ordinary(rows):
    return [row[3] for row in rows if row and row[0] == '规则']


@pytest.mark.parametrize('timeline', [None, [], {}, True, {'version': 1}])
def test_present_invalid_receipt_never_adopts_explicit_period(timeline):
    data = result()
    data['buff_state_timeline'] = timeline
    data['rotation_check'] = {'resolved_rotation_model': {'period': 10}}
    rows = beneficiary_eligibility_rows(data)
    assert ordinary(rows) == []
    assert len([r for r in rows if r and r[0] == '诊断规则']) == 12


@pytest.mark.parametrize('processed,selected,included', [(9.9, 10.1, True), (10.1, 9.9, False), (.5, .8, True)])
def test_processing_time_is_authority_in_both_delay_directions(processed, selected, included):
    data = result()
    data['mechanics_trace'] = [data['mechanics_trace'][0]]
    data['mechanics_trace'][0]['t'] = processed
    data['mechanics_trace'][0]['beneficiary_selection']['selection_t'] = selected
    assert bool(ordinary(beneficiary_eligibility_rows(data))) is included


def test_zero_window_is_empty_and_not_unknown():
    data = result()
    data['buff_state_timeline'].update(duration=0, execution_limit=0)
    rows = beneficiary_eligibility_rows(data)
    assert not ordinary(rows)
    assert not any(r and r[0] == '诊断规则' for r in rows)
    assert any('窗口之外：12 条' in str(r) for r in rows)


def test_prefix_with_later_trace_does_not_render_future_entries():
    data = result()
    data['buff_state_timeline']['execution_limit'] = .5
    before = copy.deepcopy(data)
    assert ordinary(coverage_rows(data)) == [.12, .5]
    assert data == before


def test_declared_period_filter_ignores_cycle_labels_and_does_not_claim_state_proof():
    data = result()
    del data['buff_state_timeline']
    data['rotation_check'] = {'resolved_rotation_model': {'period': 10}}
    for row in data['mechanics_trace']:
        row['cycle'] = 0
    rows = beneficiary_eligibility_rows(data)
    assert ordinary(rows) == [.12, .5, 1, 1.3]
    assert '未提供持续状态证明' in str(rows)


@pytest.mark.parametrize('field,value', [('contract', 'wrong'), ('version', True), ('version', 1.0),
                                        ('execution_limit', 11), ('duration', -1)])
def test_public_coverage_also_hides_invalid_receipt_intervals(field, value):
    data = result()
    state = {'name_zh': '独立无效回执区间', 'owner': 'a', 'beneficiary': 'b',
             'beneficiaries_scope': {'eligibility': {'exclude_owner': True}},
             'slices': [{'start': 0, 'end': 10, 'layers': 1}]}
    data['buff_state_timeline']['states'] = [state]
    data['buff_state_timeline'][field] = value
    rows = coverage_rows(data)
    # This public helper feeds both direct and App-result XLSX exports.
    assert not any(r and r[0] == '独立无效回执区间' for r in rows)
    assert not any(r and r[0] == '开始（秒，含）' for r in rows)
    assert '范围未核实' in str(rows)
