"""Independent source-approved view exports and fixed-value formula guards."""
from __future__ import annotations

import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_formula_workbook import read_sheet
from tests.test_reaction_sources_review import evaluate_numeric_cell
from tests.test_resource_counter_view_sources import view_plan
from tests.test_shared_pool_app_delivery import authorize


def test_scaled_cap_and_two_layers_are_frozen_exactly_once_in_three_exports(tmp_path):
    db = tmp_path/'absent.db'
    plan = view_plan()
    model = plan['mechanics_model']
    model['resource_counter_views']['views'][0]['transform']['max_atoms'] = 10
    model['rules'][1]['stacking'] = {'max': 2, 'add': 2, 'policy': 'refresh'}
    plan = authorize(plan, db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == before
    assert [h['expected'] for h in out['result']['hits']] == [117, 135, 135]
    ledger = out['result']['resource_counter_views']
    assert [r['reading']['raw_atoms'] for r in ledger['trace']] == [8, 16, 19]
    assert [r['source_value'] for r in ledger['trace']] == [.8, 1, 1]
    assert [r['single_value'] for r in ledger['trace']] == [.8, 1, 1]
    assert [r['combined_value'] for r in ledger['trace']] == [1.6, 2, 2]
    assert out['result']['resource_counters']['state']['values'] == {'count': {'total': 19}}
    support = formula_workbook_support(out)
    assert support['editable']['resource_counter_views_and_expressions'] == 'regenerate_required'
    assert support['editable']['character_stats'] == 'regenerate_required'
    assert len([r for r in support['limitations'] if r['code'] == 'counter_view_sample_fixed']) == 3
    target = tmp_path/'views.xlsx'
    export_result(out['result'], 'xlsx', target, receipt=out['result_receipt'])
    formula = build_formula_workbook(out)
    for content in (target.read_bytes(), result_bytes(out['result'], out['result_receipt'], 'xlsx'), formula):
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            sheets = ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns)
            index = next(i+1 for i, s in enumerate(sheets) if s.attrib['name'] == '资源读数流水')
        cells = read_sheet(content, index)
        chunks = sorted((int(c['value'].split()[-1]), cells['B'+ref[1:]]['value'])
                        for ref, c in cells.items() if ref.startswith('A') and isinstance(c['value'], str)
                        and c['value'].startswith('原文块 '))
        assert json.loads(''.join(text for _, text in chunks)) == ledger
        assert all(c['formula'] is None for c in cells.values())
    params = read_sheet(formula, 2)
    paths = {c['value']: ref[1:] for ref, c in params.items() if ref.startswith('A')}
    view_ids = {r['projected_effect_id'] for r in out['result']['sampling_trace']
                if r.get('expression', {}).get('kind') == 'counter_view'}
    frozen = []
    for bi, buff in enumerate(out['plan']['resolved_template']['buffs']):
        for ei, effect in enumerate(buff['effects']):
            if effect['id'] in view_ids:
                cell = params['C'+paths[f'buffs[{bi}].effects[{ei}].value']]
                assert cell['formula'] is None
                guard = params['I'+paths[f'buffs[{bi}].effects[{ei}].value']]['formula']
                assert guard not in (None, '0') and '<>' in guard
                frozen.append(cell['value'])
    assert frozen == [1.6, 2, 2]
    events = read_sheet(formula, 3)
    assert [events[f'V{i}']['value'] for i in range(5, 8)] == [117, 135, 135]
    sheets = {'参数': params, '逐事件': events}
    assert [evaluate_numeric_cell(sheets, '逐事件', f'V{i}') for i in range(5, 8)] == [117, 135, 135]
    atk_cell = 'C'+paths['characters[0].stats.atk']
    assert [evaluate_numeric_cell(sheets, '逐事件', f'V{i}', {('参数', atk_cell): 200})
            for i in range(5, 8)] == [234, 270, 270]
    summary = read_sheet(formula, 1)
    assert summary['B6']['value'] == 387 and '需重新生成' in summary['B6']['formula']
    assert not db.exists()


def test_scale_edit_requires_new_source_confirmation_then_uses_new_units(tmp_path):
    db = tmp_path/'absent.db'
    plan = authorize(view_plan(), db)
    plan['mechanics_model']['shared_pools']['pools'][0]['unit_scale'] = 20
    assert compose_equipment_plan(plan, db)['state'] == 'pending'
    out = run_plan_calc(authorize(plan, db), db)
    assert [h['expected'] for h in out['result']['hits']] == pytest.approx([63, 81, 87.75])
    assert [r['reading']['unit_scale'] for r in out['result']['resource_counter_views']['trace']] == [20, 20, 20]
    assert out['result']['resource_counters']['state']['values'] == {'count': {'total': 19}}
