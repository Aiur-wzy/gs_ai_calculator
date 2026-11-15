"""Live small-plan proof checks for the final readonly query and XLSX exits."""
import io
import json
import zipfile
from xml.etree import ElementTree as ET

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import build_formula_workbook
from tests.resource_reaction_review_helpers import bad_complete_prefix, live_case

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
CASES = ['income', 'zero', 'no-match', 'excluded', 'full-pool', 'cooldown', 'failed-prefix']


def readonly_page(content):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = [s.attrib['name'] for s in ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', NS)]
        xml = ET.fromstring(archive.read(f'xl/worksheets/sheet{names.index("反应资源联动") + 1}.xml'))
        assert xml.find('s:sheetProtection', NS) is not None
        assert not xml.findall('.//s:f', NS)
        chunks = []
        for row in xml.findall('s:sheetData/s:row', NS):
            texts = [''.join(c.itertext()) for c in row.findall('s:c', NS)]
            if texts and texts[0].startswith('原文块 '):
                chunks.append(texts[1])
        return json.loads(''.join(chunks)), ''.join(xml.find('.//s:c[@r="B4"]', NS).itertext())


@pytest.mark.parametrize('kind', CASES)
def test_three_live_exports_preserve_raw_scope_and_protection(tmp_path, kind):
    value = live_case(tmp_path, kind)
    result, receipt = value['result'], value['result_receipt']
    books = [result_bytes(result, receipt, 'xlsx'), build_formula_workbook(value)]
    path = export_result(result, 'xlsx', tmp_path / 'direct.xlsx', receipt=receipt)
    books.append(path.read_bytes())
    for content in books:
        raw, label = readonly_page(content)
        assert raw['resource_reactions'] == result.get('resource_reactions')
        assert raw['resource_reaction_source_adoption'] == result['resource_reaction_source_adoption']
        expected = ('无执行记录' if kind == 'excluded' else
                    '已执行前缀' if kind == 'failed-prefix' else '本次完整声明窗口')
        assert label == expected


def test_live_query_parent_and_half_open_window_are_exact(tmp_path):
    value = live_case(tmp_path)
    all_rows = review_calculation(value, {'view': 'resource_reactions'})
    assert all_rows['total_rows'] == 3
    first = all_rows['items'][0]
    assert review_calculation(value, {'view': 'resource_reactions', 'filters': {'end': first['t']}})['total_rows'] == 0
    assert review_calculation(value, {'view': 'resource_reactions', 'filters': {
        'event_id': first['parent_event_id'], 'source_id': first['source_id']}})['items'] == [first]
    prefix = review_calculation(live_case(tmp_path, 'failed-prefix'), {'view': 'resource_reactions'})
    assert all(row['display_execution_window'] == 'executed_prefix' for row in prefix['items'])


@pytest.mark.parametrize('route', ['json', 'xlsx', 'direct', 'formula', 'query'])
def test_all_live_exits_reject_resigned_false_complete_claim(tmp_path, route):
    value = bad_complete_prefix(live_case(tmp_path, 'failed-prefix'))
    with pytest.raises(ValueError):
        if route in ('json', 'xlsx'):
            result_bytes(value['result'], value['result_receipt'], route)
        elif route == 'direct':
            export_result(value['result'], 'xlsx', tmp_path / 'forbidden.xlsx', receipt=value['result_receipt'])
        elif route == 'formula':
            build_formula_workbook(value)
        else:
            review_calculation(value, {'view': 'resource_reactions'})
    assert not (tmp_path / 'forbidden.xlsx').exists()
