"""Actual reactions survive all readonly query and workbook projections."""
import copy
import io
import json
import zipfile
from xml.etree import ElementTree as ET

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_mechanics_execution import ability, action, second_character
from tests.test_resource_reaction_app import reaction_plan
from tests.test_shared_pool_app_delivery import authorize

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def projected_sheet(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        positions = [i for i, sheet in enumerate(workbook.findall('s:sheets/s:sheet', NS), 1)
                     if sheet.attrib['name'] == '反应资源联动']
        assert len(positions) == 1
        xml = ET.fromstring(archive.read(f'xl/worksheets/sheet{positions[0]}.xml'))
        assert xml.find('s:sheetProtection', NS) is not None
        assert not xml.findall('.//s:f', NS)
        rows = []
        for row in xml.findall('s:sheetData/s:row', NS):
            cells = {}
            for cell in row.findall('s:c', NS):
                column = ''.join(c for c in cell.attrib['r'] if c.isalpha())
                if cell.attrib.get('t') == 'inlineStr':
                    cells[column] = ''.join(cell.itertext())
                else:
                    value = cell.find('s:v', NS)
                    cells[column] = float(value.text) if value is not None else None
            rows.append(cells)
        return rows


@pytest.mark.parametrize('kind', ['income', 'zero', 'no-match', 'excluded', 'full-pool', 'cooldown', 'failed-prefix'])
def test_actual_query_and_three_workbooks_keep_exact_readonly_resource_records(tmp_path, kind):
    plan = reaction_plan()
    model = plan['mechanics_model']
    if kind == 'zero':
        model['elemental_model']['target']['initial_aura'] = None
    elif kind == 'no-match':
        model['shared_pools']['operations'][0]['trigger']['reaction_types'] = ['melt']
    elif kind == 'excluded':
        next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id'] == 'refill').update(state='excluded', reason='engineering')
    elif kind == 'full-pool':
        model['shared_pools']['pools'][0].update(capacity=20, initial={'team': 20})
        model['shared_pools']['consumers'][0]['filter']['elements'] = ['hydro']
    elif kind == 'cooldown':
        model['shared_pools']['operations'][0]['cooldown']['seconds'] = 1
    elif kind == 'failed-prefix':
        second_character(plan['manual_template'], plan['rotation_model'])
        plan['manual_template']['characters'][-1]['stats']['energy_recharge'] = 1
        plan['rotation_model']['abilities'].append(ability('offfield_attempt', 'b'))
        plan['rotation_model']['actions'].append(action('later_failure', 'offfield_attempt', .5, .6, 30,
                                                       repeat={'mode': 'once'}))
    db = tmp_path / 'absent.db'
    plan = authorize(plan, db)
    before = copy.deepcopy(plan)
    calculated = run_plan_calc(plan, db, capture_display=True)
    assert plan == before and not db.exists()
    saved = copy.deepcopy(calculated)
    result, receipt = calculated['result'], calculated['result_receipt']
    query = review_calculation(calculated, {'view': 'resource_reactions'})
    assert query['total_rows'] == (0 if kind in ('zero', 'excluded') else 3)
    for row in query['items']:
        original = calculated
        for part in row['json_pointer'].strip('/').split('/'):
            original = original[int(part)] if isinstance(original, list) else original[part]
        assert original['operation_id'] == row['operation_id']
    if kind == 'income':
        first = query['items'][0]
        filtered = review_calculation(calculated, {'view': 'resource_reactions', 'filters': {
            'event_id': first['parent_event_id'], 'source_id': first['source_id'], 'start': first['t']}})
        assert filtered['items'] == [first]
        assert review_calculation(calculated, {'view': 'resource_reactions', 'filters': {'end': first['t']}})['total_rows'] == 0
        assert [r['receipts'][0]['accepted'] for r in query['items']] == [3, 3, 3]
    elif kind == 'no-match':
        assert all(r['state'] == 'no_match' and not r['receipts'] for r in query['items'])
    elif kind == 'full-pool':
        assert all(r['receipts'][0]['accepted'] == 0 and r['receipts'][0]['label'] == '已尝试，实际收入为0' for r in query['items'])
    elif kind == 'cooldown':
        assert [r['receipts'][0]['label'] for r in query['items']] == ['实际收入', '冷却拒绝', '冷却拒绝']
    elif kind == 'failed-prefix':
        assert result['summary']['dps'] is None
        assert result['resource_reactions']['verified_window'] == 'executed_prefix'
        assert all(r['display_execution_window'] == 'executed_prefix' for r in query['items'])
    support = formula_workbook_support(calculated)
    assert support['editable']['resource_reaction_rules_and_sources'] == 'regenerate_required'
    books = [result_bytes(result, receipt, 'xlsx')]
    path = tmp_path / 'result.xlsx'
    export_result(result, 'xlsx', path, receipt=receipt)
    books += [path.read_bytes(), build_formula_workbook(calculated)]
    rows = [projected_sheet(book) for book in books]
    assert rows[0] == rows[1] == rows[2]
    reconstructed = json.loads(''.join(row['B'] for row in rows[0] if str(row.get('A', '')).startswith('原文块 ')))
    assert reconstructed == {'resource_reactions': result.get('resource_reactions'),
                             'resource_reaction_source_adoption': result.get('resource_reaction_source_adoption')}
    assert calculated == saved
