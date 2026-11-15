"""Independent actual App identity, paging and three-export callback boundaries."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_attribute_callback_app import authorize_callbacks, callback_plan
from tests.test_formula_workbook import read_sheet
from tests.test_freeze_export_review import workbook_sheets


def actual(tmp_path):
    db = tmp_path / 'never-created.db'
    plan = authorize_callbacks(callback_plan(), db)
    return plan, run_plan_calc(plan, db, capture_display=True), db


@pytest.mark.parametrize('change', ['assumption_note', 'member_source', 'constant_writer'])
def test_actual_native_scope_mutations_cannot_reuse_previously_adopted_sources(tmp_path, change):
    plan, out, db = actual(tmp_path)
    original = copy.deepcopy(out)
    if change == 'assumption_note':
        plan['mechanics_model']['attribute_callbacks']['assumptions'][0]['note'] += ' Changed.'
    elif change == 'member_source':
        plan['mechanics_model']['rules'][0]['source'] += ' Changed.'
    else:
        plan['mechanics_model']['rules'][1]['effects'][0]['value'] = 200
    report = compose_equipment_plan(plan, db)
    assert report['state'] == 'pending'
    assert report['attribute_callback_source_adoption']['status'] == 'pending'
    with pytest.raises(PlanError):
        run_plan_calc(plan, db)
    assert out == original and not db.exists()


def test_saved_query_page_pointers_exhaust_exact_trace_and_do_not_alias_output(tmp_path):
    _, out, _ = actual(tmp_path)
    original = copy.deepcopy(out)
    trace = out['result']['attribute_callbacks']['trace']
    pages = [review_calculation(out, {'view': 'attribute_callbacks', 'limit': 2, 'offset': offset})
             for offset in range(0, len(trace), 2)]
    rows = [row for page in pages for row in page['items']]
    assert len(rows) == len(trace)
    assert [r['json_pointer'] for r in rows] == [f'/result/attribute_callbacks/trace/{i}' for i in range(len(trace))]
    assert [r['type'] for r in rows] == [r['type'] for r in trace]
    assert all(r['source_authority'] == 'caller_assumed_order_not_game_source_approval' for r in rows)
    rows[0]['parent_event_id'] = 'forged'
    assert out == original
    assert review_calculation(out, {'view': 'attribute_callbacks', 'offset': len(trace)})['items'] == []
    # Source/rule/event text is precise. An actual callback child UID cannot
    # silently become the parent's event filter.
    assert not review_calculation(out, {'view': 'attribute_callbacks', 'filters': {'event_id': '0:h0'}})['items']
    assert not review_calculation(out, {'view': 'attribute_callbacks', 'filters': {'source_id': 'bind_share'}})['items']


def test_callback_result_mutation_invalidates_saved_review_fingerprint(tmp_path):
    _, out, _ = actual(tmp_path)
    out['result']['attribute_callbacks']['trace'][0]['type'] = 'callback_committed'
    with pytest.raises(PlanError):
        review_calculation(out, {'view': 'attribute_callbacks'})


def test_three_exports_keep_zero_fields_stage_distinction_full_receipts_and_protection(tmp_path):
    _, out, _ = actual(tmp_path)
    target = tmp_path / 'callback-result.xlsx'
    export_result(out['result'], 'xlsx', target, receipt=out['result_receipt'])
    raws = [target.read_bytes(), result_bytes(out['result'], out['result_receipt'], 'xlsx'), build_formula_workbook(out)]
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for raw in raws:
        cells = workbook_sheets(raw)['属性回调执行']
        labels = [c['value'] for ref, c in cells.items() if ref.startswith('E')]
        assert {'计划已生成', '批次取样已准备', '回调已提交'} <= set(labels)
        assert any(c['value'] == 0 for ref, c in cells.items() if ref.startswith('B'))
        assert any(c['value'] == 0 for ref, c in cells.items() if ref.startswith('I'))
        chunks = sorted((int(c['value'].split()[-1]), cells['B' + ref[1:]]['value'])
            for ref, c in cells.items() if ref.startswith('A') and isinstance(c['value'], str)
            and c['value'].startswith('原文块 '))
        assert json.loads(''.join(value for _, value in chunks)) == out['result']['attribute_callbacks']
        assert all(c['formula'] is None for c in cells.values())
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            sheets = ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns)
            sheet = next(i + 1 for i, s in enumerate(sheets) if s.attrib['name'] == '属性回调执行')
            document = ET.fromstring(archive.read(f'xl/worksheets/sheet{sheet}.xml'))
            assert document.find('s:sheetProtection', ns) is not None


def test_formula_cache_retains_actual_samples_and_marks_em_edits_for_regeneration(tmp_path):
    _, out, _ = actual(tmp_path)
    support = formula_workbook_support(out)
    assert support['editable']['character_stats'] == 'regenerate_required'
    assert support['editable']['attribute_callback_order_and_sources'] == 'regenerate_required'
    assert any(i['code'] == 'attribute_ledger_sample_fixed' for i in support['limitations'])
    content = build_formula_workbook(out)
    params, events, summary = [read_sheet(content, i) for i in (2, 3, 1)]
    paths = {c['value']: ref[1:] for ref, c in params.items() if ref.startswith('A')}
    em_row = paths['characters[0].stats.em']
    assert params['G' + em_row]['formula'] == f'IF(C{em_row}<>D{em_row},1,0)'
    assert '需重新生成' in summary['B6']['formula']
    assert [events[f'V{i}']['value'] for i in range(5, 9)] == pytest.approx([h['expected'] for h in out['result']['hits']])
    sampled_ids = {row['projected_effect_id'] for row in out['result']['sampling_trace']
                   if row.get('attribute_receipt_id') and row.get('projected_effect_id')}
    fixed = []
    for bi, buff in enumerate(out['plan']['resolved_template']['buffs']):
        for ei, effect in enumerate(buff['effects']):
            if effect['id'] in sampled_ids:
                p = f'buffs[{bi}].effects[{ei}].value'
                row = paths[p]
                assert params['C' + row]['formula'] is None
                assert '<>' in params['G' + row]['formula']
                fixed.append(params['C' + row]['value'])
    assert fixed and 40 in fixed


def test_all_sources_explicitly_excluded_adds_no_execution_sheet(tmp_path):
    db = tmp_path / 'never-created.db'
    plan = callback_plan()
    for binding in plan['effect_bindings']:
        binding['parts'][0].update(state='excluded', reason='Explicitly absent in this synthetic scenario.')
    out = run_plan_calc(authorize_callbacks(plan, db), db)
    assert out['report']['attribute_callback_source_adoption']['status'] == 'excluded'
    assert 'attribute_callbacks' not in out['result']
    assert '属性回调执行' not in workbook_sheets(result_bytes(out['result'], out['result_receipt'], 'xlsx'))
