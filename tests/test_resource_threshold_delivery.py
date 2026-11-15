"""Actual threshold observations and activated benefits remain separate in delivery."""
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
from tests.test_freeze_export_review import workbook_sheets
from tests.test_resource_threshold_app import threshold_plan
from tests.test_shared_pool_app_delivery import authorize


def actual(tmp_path, phase='pre'):
    db = tmp_path / 'absent.db'
    return run_plan_calc(authorize(threshold_plan(phase), db), db, capture_display=True)


def saved_result(out):
    """Explicit saved reporting scope, without claiming a full execution package."""
    return {'result': out['result'], 'result_receipt': out['result_receipt']}


@pytest.mark.parametrize('phase,damage', [('pre', [90, 90]), ('post', [45, 90])])
def test_real_threshold_queries_and_duration_use_committed_execution(tmp_path, phase, damage):
    out = actual(tmp_path, phase)
    before = copy.deepcopy(out)
    assert [h['expected'] for h in out['result']['hits']] == damage
    for suffix, source in [('observers', 'binding:observer'), ('triggers', 'binding:part')]:
        field = 'resource_threshold_' + suffix
        page = review_calculation(out, {'view': field, 'filters': {'event_id': 'h0', 'source_id': source, 'start': .2, 'end': .3}})
        assert page['validation'] == 'full_calculation_package'
        assert page['total_rows'] == 1
        row = page['items'][0]
        assert row['t'] == .2 and row['phase'] == phase
        assert row['event_id'] == 'h0' and row['source_id'] == source and row['owner'] == 'a'
        assert row['before'] == 49 and row['after'] == 50 and row['threshold_atoms'] == 50
        assert row['origin'] == 'engine'
        assert row['json_pointer'] == '/result/' + field + '/trace/0'
        if suffix == 'triggers':
            assert row['outcome'] == 'activated' and row['source_window'] == 0
        else:
            assert row['crossed'] is True and 'source_window' not in row
        assert review_calculation(out, {'view': field, 'filters': {'source_id': 'unknown'}})['total_rows'] == 0
    state, = out['result']['buff_state_timeline']['states']
    assert state['start'] == .2 and state['end'] == 1.2
    assert state['rule_id'] == 'boost' and state['effects'][0]['value'] == 1
    assert out == before


def test_dispatch_issued_and_claimed_alone_do_not_become_executed_rows(tmp_path):
    out = actual(tmp_path)
    assert review_calculation(out, {'view': 'resource_threshold_triggers'})['total_rows'] == 1
    out['result']['resource_threshold_triggers']['trace'] = []
    out['result_receipt']['result_fingerprint'] = fingerprint(out['result'])
    assert out['result']['resource_threshold_triggers']['authority']['issued']
    page = review_calculation(saved_result(out), {'view': 'resource_threshold_triggers'})
    assert page['validation'] == 'saved_result_fingerprint'
    assert page['total_rows'] == 0


def test_threshold_receipts_are_protected_and_exact_in_three_workbooks(tmp_path):
    out = actual(tmp_path)
    support = formula_workbook_support(out)
    assert support['editable']['resource_thresholds_and_sources'] == 'regenerate_required'
    target = tmp_path / 'thresholds.xlsx'
    export_result(out['result'], 'xlsx', target, receipt=out['result_receipt'])
    for raw in (target.read_bytes(), result_bytes(out['result'], out['result_receipt'], 'xlsx'), build_formula_workbook(out)):
        sheets = workbook_sheets(raw)
        for title, field in [('资源阈值观察', 'resource_threshold_observers'), ('阈值收益执行', 'resource_threshold_triggers')]:
            cells = sheets[title]
            chunks = sorted((int(c['value'].split()[-1]), cells['B' + ref[1:]]['value'])
                for ref, c in cells.items() if ref.startswith('A') and isinstance(c['value'], str) and c['value'].startswith('原文块 '))
            assert json.loads(''.join(text for _, text in chunks)) == out['result'][field]
            assert all(not c['formula'] for c in cells.values())
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
                meta = ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns)
                index = next(i + 1 for i, s in enumerate(meta) if s.attrib['name'] == title)
                assert ET.fromstring(archive.read(f'xl/worksheets/sheet{index}.xml')).find('s:sheetProtection', ns) is not None
        assert '收益已激活' in {c['value'] for c in sheets['阈值收益执行'].values()}


@pytest.mark.parametrize('field,time_key', [('resource_threshold_observers', 't_us'), ('resource_threshold_triggers', 'time_us')])
@pytest.mark.parametrize('value', [True, 10 ** 400, '200000'])
def test_bad_saved_threshold_time_is_controlled(tmp_path, field, time_key, value):
    out = actual(tmp_path)
    positive = review_calculation(saved_result(out), {'view': field, 'filters': {'start': .2, 'end': .3}})
    assert positive['validation'] == 'saved_result_fingerprint'
    assert positive['total_rows'] == 1
    assert positive['items'][0]['event_id'] == 'h0' and positive['items'][0]['t'] == .2
    original = out['result'][field]['trace'][0]
    assert original[time_key] == 200000
    if field == 'resource_threshold_observers':
        assert original['type'] == 'observer_evaluation'
        assert original['execution'] == 'ledger_committed_audit'
    else:
        assert original['type'] == 'threshold_dispatch_completed' and original['outcome'] == 'activated'
    out['result'][field]['trace'][0][time_key] = value
    out['result_receipt']['result_fingerprint'] = fingerprint(out['result'])
    with pytest.raises(PlanError) as exc:
        review_calculation(saved_result(out), {'view': field})
    assert exc.value.reason == 'invalid_review_input'
    assert '阈值流水时间' in str(exc.value)


def test_ambiguous_adoption_does_not_guess_source_identity(tmp_path):
    out = actual(tmp_path)
    request = {'view': 'resource_threshold_triggers', 'filters': {'source_id': 'binding:part'}}
    assert review_calculation(saved_result(out), request)['total_rows'] == 1
    adoption = out['result']['resource_threshold_triggers']['source_adoptions']
    adoption.append(dict(adoption[0], source_id='other'))
    out['result_receipt']['result_fingerprint'] = fingerprint(out['result'])
    page = review_calculation(saved_result(out), request)
    assert page['validation'] == 'saved_result_fingerprint'
    assert page['total_rows'] == 0


def test_absent_threshold_data_adds_no_executed_rows(tmp_path):
    out = actual(tmp_path)
    for field in ('resource_threshold_observers', 'resource_threshold_triggers'):
        positive = review_calculation(saved_result(out), {'view': field, 'filters': {'start': .2, 'end': .3}})
        assert positive['validation'] == 'saved_result_fingerprint'
        assert positive['total_rows'] == 1
        assert positive['items'][0]['event_id'] == 'h0' and positive['items'][0]['t'] == .2
    for field in ('resource_threshold_observers', 'resource_threshold_triggers'):
        del out['result'][field]
    out['result_receipt']['result_fingerprint'] = fingerprint(out['result'])
    for field in ('resource_threshold_observers', 'resource_threshold_triggers'):
        page = review_calculation(saved_result(out), {'view': field})
        assert page['validation'] == 'saved_result_fingerprint'
        assert page['total_rows'] == 0
