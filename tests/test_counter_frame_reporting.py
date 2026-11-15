"""Display arithmetic remains the executed result, including frozen layer count."""
import copy
import json
from pathlib import Path

import pytest

from genshin_app.counter_frame_reporting import (
    capture_index,
    capture_label,
    counter_frame_rows,
    counter_view_rows,
    gate_label,
)


def sample(name):
    path = Path(__file__).parent/'fixtures/counter_self_frame_reporting.json'
    return json.loads(path.read_text('utf8'))[name]


def raw_chunks(rows):
    return json.loads(''.join(row[1] for row in rows if len(row) == 2 and str(row[0]).startswith('原文块 ')))


@pytest.mark.parametrize('name,values,layers,combined', [
    ('historical',[.8,.8,.8],[1,1,1],[.8,.8,.8]),
    ('current_no_reference',[.8,1.6,1.9],[1,1,1],[.8,1.6,1.9]),
    ('frozen_two_layers',[.8],[2],[1.6]),
])
def test_real_ledger_rows_do_not_recompute_or_double_multiply(name,values,layers,combined):
    ledger = sample(name)
    original = copy.deepcopy(ledger)
    rows = counter_view_rows(ledger)
    numbers = rows[2:2+len(ledger['trace'])]
    assert [r[9] for r in numbers] == values
    assert [r[12] for r in numbers] == layers
    assert [r[13] for r in numbers] == combined
    assert all(r[3] == ('当前累计（未指定输出快照）' if name == 'current_no_reference' else '输出快照') for r in numbers)
    assert all(r[11] == ('当前层数' if name == 'current_no_reference' else '捕获时冻结层数') for r in numbers)
    assert raw_chunks(rows) == ledger
    frames = counter_frame_rows(ledger)
    assert raw_chunks(frames) == ledger['historical_capture']
    assert ledger == original


def test_zero_capture_time_and_missing_duplicate_relations():
    ledger = sample('historical')
    row = copy.deepcopy(ledger['trace'][0])
    capture = copy.deepcopy(ledger['historical_capture']['capture_trace'][0])
    row['capture_context']['time_us'] = capture['context']['time_us'] = 0
    assert capture_label(row,capture_index([capture])).startswith('0.0秒 /')
    assert capture_label(row,{}) == '捕获回执缺失'
    assert capture_label(row,capture_index([capture,capture])) == '捕获回执不唯一'
    capture['context']['event_id'] = 'different'
    assert capture_label(row,capture_index([capture])) == '捕获关系不一致'


def test_mixed_provider_and_new_self_report_keeps_actual_type():
    ledger = sample('historical')
    provider = copy.deepcopy(ledger['trace'][0])
    for field in ('frame_selection','frame_id','capture_context','gate_qualification'):
        provider.pop(field)
    provider.update(domain='provider',sample='current')
    ledger['trace'].append(provider)
    row = counter_view_rows(ledger)[2+len(ledger['trace'])-1]
    assert row[3] == '提供者当前累计' and row[4] == '无需历史捕获'
    assert row[5] == '提供者当前取样（资格见原资源读数）'
    assert row[11] == '当前层数'


@pytest.mark.parametrize('extra', [
    {'gate_qualification': None}, {'gate_qualification': {}}, {'gate_qualification': False},
    {'gate_qualification': []}, {'frame_selection': None}, {'frame_selection': False},
    {'domain': 'self'}, {'sample': 'self_frame'},
])
def test_provider_legacy_label_never_hides_explicit_or_self_frame_gaps(extra):
    row = {'domain': 'provider', 'sample': 'current', **extra}
    assert gate_label(row) == '门槛回执不完整'


def test_unknown_gates_do_not_claim_accepted_and_no_extra_effect():
    row = {'gate_qualification':{'kind':'shared_pool'}}
    assert gate_label(row) == '门槛回执不完整'
    row['gate_qualification'].update(event_run_id='0:h',instance_id='i',kind='unknown')
    row.update(run_id='0:h',instance_id='i')
    assert gate_label(row) == '门槛未说明'
    row['gate_qualification'].update(kind='shared_pool',consumer_id='c')
    assert gate_label(row) == '当前共享资源消费已通过：c'


def test_old_report_does_not_enter_new_formatter_and_empty_capture_is_readonly():
    assert counter_view_rows({'contract':'resource-counter-views-current-v1'}) == []
    assert counter_frame_rows({'contract':'resource-counter-views-current-v1'}) == []
    ledger = sample('current_no_reference')
    assert ledger['historical_capture']['capture_trace'] == []
    rows = counter_frame_rows(ledger)
    assert raw_chunks(rows)['capture_trace'] == []
