"""Actual three exports must protect new execution ledgers, not merely label them."""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import build_formula_workbook
from tests.test_attribute_app_review import add_reader, adopt
from tests.test_attribute_app_sources import attribute_plan
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_resource_counter_view_sources import view_plan
from tests.test_runtime_talent_level import level_db  # noqa: F401
from tests.test_shared_pool_app_delivery import authorize
from tests.test_talent_level_app import level_plan


def protected_cells(content, sheet_name):
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sheets = ET.fromstring(archive.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns)
        index = next(i+1 for i, s in enumerate(sheets) if s.attrib['name'] == sheet_name)
        sheet = ET.fromstring(archive.read(f'xl/worksheets/sheet{index}.xml'))
        protection = sheet.find('s:sheetProtection', ns)
        assert protection is not None and protection.attrib.get('sheet') == '1', sheet_name
        styles = ET.fromstring(archive.read('xl/styles.xml')).findall('s:cellXfs/s:xf', ns)
        for cell in sheet.findall('.//s:sheetData/s:row/s:c', ns):
            style = styles[int(cell.attrib.get('s', '0'))]
            cell_protection = style.find('s:protection', ns)
            assert cell_protection is None or cell_protection.attrib.get('locked', '1') != '0', (sheet_name, cell.attrib['r'])


@pytest.mark.parametrize('route', ['direct', 'app', 'formula'])
@pytest.mark.parametrize('kind', ['resources', 'attributes'])
def test_new_actual_execution_ledger_sheets_are_protected_and_have_no_unlocked_cells(tmp_path, route, kind):
    db = tmp_path/'absent.db'
    if kind == 'resources':
        calculated = run_plan_calc(authorize(view_plan(), db), db)
        names = ('共享资源流水', '资源累计流水', '资源读数流水', '生命周期流水')
    else:
        plan = attribute_plan()
        add_reader(plan)
        calculated = run_plan_calc(adopt(plan, db), db)
        names = ('属性取样账',)
    if route == 'direct':
        target = tmp_path/'protected.xlsx'
        export_result(calculated['result'], 'xlsx', target, receipt=calculated['result_receipt'])
        content = target.read_bytes()
    elif route == 'app':
        content = result_bytes(calculated['result'], calculated['result_receipt'], 'xlsx')
    else:
        content = build_formula_workbook(calculated)
    for name in names:
        protected_cells(content, name)
    assert not db.exists()


def test_actual_level_parameter_source_sheet_is_protected_in_three_routes(level_db, tmp_path):  # noqa: F811
    calculated = run_plan_calc(level_plan(level_db), level_db)
    assert calculated['result']['hit_parameter_sources'][0]['level']['effective_level'] == 13
    path = tmp_path/'levels.xlsx'
    export_result(calculated['result'], 'xlsx', path, receipt=calculated['result_receipt'])
    for content in (path.read_bytes(), result_bytes(calculated['result'], calculated['result_receipt'], 'xlsx'),
                    build_formula_workbook(calculated)):
        protected_cells(content, '技能参数来源')
