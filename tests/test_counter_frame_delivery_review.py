"""Independent actual hook/readonly checks against the private App overlay."""
from __future__ import annotations

import copy
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment.counter_frame_receipt import _proof
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_counter_frame_delivery import calculated

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def payload(tmp_path, out, entry):
    result, receipt = out['result'], out['result_receipt']
    if entry == 'service':
        return result_bytes(result, receipt, 'xlsx')
    if entry == 'direct':
        path = tmp_path / 'direct.xlsx'
        export_result(result, 'xlsx', path, receipt=receipt)
        return path.read_bytes()
    return build_formula_workbook(out)


@pytest.mark.parametrize('entry', ['service', 'direct', 'formula'])
def test_actual_new_capture_sheet_is_readonly_and_not_an_excel_formula(tmp_path, entry):
    _, out = calculated(tmp_path)
    with zipfile.ZipFile(io.BytesIO(payload(tmp_path, out, entry))) as archive:
        book = ET.fromstring(archive.read('xl/workbook.xml'))
        sheets = book.findall('s:sheets/s:sheet', NS)
        number = next(i for i, sheet in enumerate(sheets, 1) if sheet.get('name') == '累计输出快照')
        sheet = ET.fromstring(archive.read(f'xl/worksheets/sheet{number}.xml'))
        assert sheet.find('s:sheetProtection', NS) is not None
        assert sheet.findall('.//s:f', NS) == []
        text = ''.join(sheet.itertext())
        assert 'counter-output' in text


@pytest.mark.parametrize('entry', ['service', 'direct', 'formula', 'query'])
def test_ordinary_proof_rehash_does_not_bypass_actual_hook_replay(tmp_path, entry):
    _, out = calculated(tmp_path)
    out['result']['resource_counter_views']['trace'][0]['capture_context']['event_id'] = 'forged'
    receipt = out['result_receipt']
    receipt['counter_self_frames'] = _proof(out['result'], receipt)
    receipt['result_fingerprint'] = fingerprint(out['result'])
    with pytest.raises((ValueError, PlanError)):
        if entry == 'query':
            review_calculation(out, {'view': 'resource_counter_frames'})
        else:
            payload(tmp_path, out, entry)


def test_formula_permission_requires_regeneration_for_resource_math(tmp_path):
    _, out = calculated(tmp_path)
    support = formula_workbook_support(out)
    assert support['editable']['resource_counter_views_and_expressions'] == 'regenerate_required'
    assert support['editable']['shared_pools_and_resource_gates'] == 'regenerate_required'
    assert any(row['code'] == 'counter_view_sample_fixed' for row in support['limitations'])


def test_formula_full_hook_rejects_changed_compiled_rotation(tmp_path):
    _, out = calculated(tmp_path)
    out['report']['compiled_rotation_model'] = copy.deepcopy(
        out['result']['rotation_check']['resolved_rotation_model'])
    out['report']['compiled_rotation_model']['actions'][0]['start'] += .01
    with pytest.raises((ValueError, PlanError)):
        build_formula_workbook(out)
