"""Actual multistat App/query/Excel independent review after candidate migration."""
import copy
import io
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook
from tests.test_attribute_stat_app import adopt, stat_plan


def test_percentage_receipt_keeps_real_immutable_recipient_base_under_saved_query(tmp_path):
    out = run_plan_calc(adopt(stat_plan(percent=True), tmp_path / 'absent.db'), tmp_path / 'absent.db')
    query = review_calculation({key:out[key] for key in ('result','result_receipt')},
                              {'view': 'attributes', 'filters': {'stat': 'atk', 'basis': 'base'}})
    assert query['items'] and any(row['value'] == 50 for row in query['items'])
    assert out['result']['hits'][0]['expected'] == 51.75
    original = copy.deepcopy(out)
    query['items'][0]['value'] = 999
    assert out == original


@pytest.mark.parametrize('field', ['atk', 'base_atk', 'em'])
def test_formula_cannot_reuse_a_saved_numeric_attribute_receipt_after_native_panel_change(tmp_path, field):
    out = run_plan_calc(adopt(stat_plan(percent=True), tmp_path / 'absent.db'), tmp_path / 'absent.db')
    changed = copy.deepcopy(out)
    changed['plan']['manual_template']['characters'][0]['stats'][field] += 1
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(changed)


def test_actual_three_routes_protect_v3_ledger_with_no_computed_cells(tmp_path):
    out = run_plan_calc(adopt(stat_plan(percent=True), tmp_path / 'absent.db'), tmp_path / 'absent.db')
    target = tmp_path / 'v3.xlsx'
    export_result(out['result'], 'xlsx', target, receipt=out['result_receipt'])
    payloads = [target.read_bytes(), result_bytes(out['result'], out['result_receipt'], 'xlsx'), build_formula_workbook(out)]
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for payload in payloads:
        with zipfile.ZipFile(io.BytesIO(payload)) as workbook:
            sheets = ET.fromstring(workbook.read('xl/workbook.xml')).findall('s:sheets/s:sheet', ns)
            index = next(i + 1 for i, sheet in enumerate(sheets) if sheet.get('name') == '属性取样账')
            tree = ET.fromstring(workbook.read(f'xl/worksheets/sheet{index}.xml'))
            assert tree.find('s:sheetProtection', ns) is not None
            assert not tree.findall('.//s:f', ns)
            assert '实际比例（1为100%）' in ''.join(tree.itertext())

@pytest.mark.parametrize('field', ['mechanics_model', 'mechanics_input_template', 'matches', 'compiled_rotation_model', 'declarations', 'source_adoption'])
def test_full_formula_receipt_binds_actual_execution_components(tmp_path, field):
    out = run_plan_calc(adopt(stat_plan(percent=True), tmp_path / 'absent.db'), tmp_path / 'absent.db')
    changed = copy.deepcopy(out)
    if field == 'mechanics_model':
        changed['report']['mechanics_model']['rules'][0]['effects'][0]['value']['factor'] += .001
    elif field == 'mechanics_input_template':
        changed['report'][field]['characters'][0]['stats']['base_atk'] += 1
    elif field == 'matches':
        changed['report']['mechanics_matches'].append({'unreviewed': 'entry'})
    elif field == 'compiled_rotation_model':
        changed['report'][field] = copy.deepcopy(changed['plan']['rotation_model'])
        changed['report'][field]['period'] += 1
    elif field == 'declarations':
        changed['result']['attribute_ledger']['declarations']['initial_basis'][0]['stat'] = 'atk'
    else:
        changed['result']['attribute_ledger']['source_adoption']['context_fingerprint'] = 'different-context'
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(changed)


@pytest.mark.parametrize('field', ['model_fingerprint', 'template_fingerprint', 'matches_fingerprint'])
def test_result_only_receipt_rejects_compiled_identity_metadata_disagreement(tmp_path, field):
    from genshin_app.equipment.attribute_receipt import (
        validate_attribute_result_receipt,
    )
    out = run_plan_calc(adopt(stat_plan(percent=True), tmp_path / 'absent.db'), tmp_path / 'absent.db')
    out['result_receipt']['attribute_ledger'][field] = '0' * 64
    with pytest.raises(ValueError):
        validate_attribute_result_receipt(out['result'], out['result_receipt'])
