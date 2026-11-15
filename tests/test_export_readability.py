import json
import xml.etree.ElementTree as ET

from genshin_app.export import _sheet_xml, readable_equipment_rows

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def test_verbose_sources_are_losslessly_relocatable_and_material_cells_unchanged():
    source = json.dumps({'原始资料': '中文依据'*300, 'value': 3.14}, ensure_ascii=False)
    rows = [['角色', '项目', '属性/部位', '内容', '来源/说明'], ['角色甲', '武器', '受审武器', 1234.56, source]]
    view, details = readable_equipment_rows(rows)
    assert view[1][:4] == rows[1][:4]
    assert ''.join(r[2] for r in details[1:]) == source
    assert all(r[3] == '角色与装备!E2' and len(r[2]) <= 480 for r in details[1:])
    assert [r[4] for r in details[1:]] == list(range(1, len(details)))
    assert rows[1][4] == source
    assert '第 2' in view[1][4]


def test_numeric_values_stay_numeric_and_precision_is_not_lost_to_display():
    values = [3453550.9695627615, 0, .00000001234, 90]
    xml = ET.fromstring(_sheet_xml([['伤害', '零', '微量', '等级'], values]))
    cells = xml.findall('.//s:row[@r="2"]/s:c', NS)
    assert [float(c.find('s:v', NS).text) for c in cells] == values
    assert all(c.attrib.get('t') is None for c in cells)
    assert cells[2].attrib['s'] == '5'
    assert len(xml.findall('s:cols/s:col', NS)) == 4


def test_wrapped_source_chunks_fit_excel_row_height_and_readonly_protection_remains():
    rows = [['字段', '原文'], ['来源', '可读中文'*120]]
    xml = ET.fromstring(_sheet_xml(rows, readonly=True))
    assert float(xml.find('.//s:row[@r="2"]', NS).attrib['ht']) < 409
    assert xml.find('s:sheetProtection', NS) is not None
