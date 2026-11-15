"""Actual causal receipts stay separate from plans in queries and three exports."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_attribute_callback_app import authorize_callbacks, callback_plan
from tests.test_freeze_export_review import workbook_sheets


def actual(tmp_path):
    db = tmp_path / 'absent.db'
    return run_plan_calc(authorize_callbacks(callback_plan(), db), db)


def review_saved(value, request):
    """These query tests intentionally exercise the result-only saved scope."""
    return review_calculation({key:value[key] for key in ('result','result_receipt')}, request)


def test_actual_trace_queries_use_parent_identity_and_source_window(tmp_path):
    out = actual(tmp_path)
    before = copy.deepcopy(out)
    page = review_saved(out, {'view': 'attribute_callbacks', 'filters': {'event_id': 'h0', 'start': .2, 'end': .3}})
    assert page['total_rows'] > 0
    for row in page['items']:
        assert row['parent_event_id'] == 'h0' and row['t'] == .2
        assert row['source_window'] == 0 and row['origin'] == 'engine'
        index = int(row['json_pointer'].split('/')[-1])
        assert out['result']['attribute_callbacks']['trace'][index]['type'] == row['type']
    assert {'callback_plan', 'callback_batch_prepared', 'callback_committed'} <= {r['type'] for r in page['items']}
    filtered = review_saved(out, {'view': 'attribute_callbacks', 'filters': {'source_id': 'bind_share:part'}})
    assert filtered['total_rows'] == 1
    assert filtered['items'][0]['rule_id'] == 'share'
    assert filtered['items'][0]['frame_id']
    assert review_saved(out, {'view': 'attribute_callbacks', 'filters': {'event_id': 'absent'}})['total_rows'] == 0
    assert out == before


def test_actual_callback_ledger_is_protected_and_exactly_reconstructible_in_three_workbooks(tmp_path):
    out = actual(tmp_path)
    support = formula_workbook_support(out)
    assert support['editable']['attribute_callback_order_and_sources'] == 'regenerate_required'
    assert support['attribute_callback_execution'] == 'readonly_actual_commit_receipts'
    target = tmp_path / 'callbacks.xlsx'
    export_result(out['result'], 'xlsx', target, receipt=out['result_receipt'])
    formula = build_formula_workbook(out)
    for raw in (target.read_bytes(), result_bytes(out['result'], out['result_receipt'], 'xlsx'), formula):
        cells = workbook_sheets(raw)['属性回调执行']
        chunks = sorted((int(c['value'].split()[-1]), cells['B' + ref[1:]]['value'])
                        for ref, c in cells.items() if ref.startswith('A') and isinstance(c['value'], str)
                        and c['value'].startswith('原文块 '))
        assert json.loads(''.join(text for _, text in chunks)) == out['result']['attribute_callbacks']
        assert all(not c['formula'] for c in cells.values())
        assert {'计划已生成', '批次取样已准备', '回调已提交'} <= {
            c['value'] for ref, c in cells.items() if ref.startswith('E')}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            sheets = ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns)
            index = next(i + 1 for i, s in enumerate(sheets) if s.attrib['name'] == '属性回调执行')
            assert ET.fromstring(archive.read(f'xl/worksheets/sheet{index}.xml')).find('s:sheetProtection', ns) is not None


@pytest.mark.parametrize('value', [True, 10 ** 400, '200000'])
def test_invalid_saved_callback_time_is_controlled_not_guessed(tmp_path, value):
    out = actual(tmp_path)
    out['result']['attribute_callbacks']['trace'][0]['time_us'] = value
    out['result_receipt']['result_fingerprint'] = fingerprint(out['result'])
    with pytest.raises(PlanError) as exc:
        review_saved(out, {'view': 'attribute_callbacks'})
    assert exc.value.reason == 'invalid_review_input'


def test_old_result_has_empty_view_without_mutation_or_fake_execution(tmp_path):
    out = actual(tmp_path)
    out['result'].pop('attribute_callbacks')
    out['result_receipt']['result_fingerprint'] = fingerprint(out['result'])
    page = review_saved(out, {'view': 'attribute_callbacks'})
    assert page['total_rows'] == 0 and not page['items']
    assert 'attribute_callbacks' not in out['result']
