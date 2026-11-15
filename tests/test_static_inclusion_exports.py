"""Actual App output at all read-only static inclusion entry points."""
import copy
import json
import sqlite3

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    FormulaWorkbookError,
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_freeze_export_review import workbook_sheets
from tests.test_reaction_benefits_exports import NS, sheets
from tests.test_static_inclusion_app import calculated as calculated  # noqa: PLC0414
from tests.test_static_inclusion_app import (
    snapshot_session as snapshot_session,  # noqa: PLC0414
)
from tests.test_static_inclusion_app import version as version  # noqa: PLC0414


def saved(out):
    return {key: out[key] for key in ('result', 'result_receipt')}


def test_actual_static_query_has_exact_identity_and_no_fake_event_filters(calculated):
    record = calculated['result']['static_inclusion_compilations'][0]
    page = review_calculation(saved(calculated), {'view': 'static_inclusions', 'limit': 1,
        'filters': {'character': record['provider_character_id'], 'source_id': record['id']}})
    assert page['total_rows'] == 1 and page['next_offset'] is None
    row = page['items'][0]
    assert row['origin'] == 'already_in_given_panel'
    assert row['declaration']['numeric_application'] is False
    assert row['json_pointer'] == '/result/static_inclusion_compilations/0'
    assert not {'t', 'event_id', 'expected'} & row.keys()
    assert not review_calculation(saved(calculated), {'view': 'static_inclusions',
        'filters': {'source_id': 'unrelated'}})['items']
    for filters in ({'start': 0}, {'event_id': 'h1'}, {'damage_type_id': 'damage.normal_attack'}):
        with pytest.raises(PlanError):
            review_calculation(saved(calculated), {'view': 'static_inclusions', 'filters': filters})


def test_three_actual_exports_preserve_static_evidence_without_extra_formula(calculated, tmp_path, monkeypatch):
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('Saved export must not re-query DB'))
    output = tmp_path / 'direct.xlsx'
    export_result(calculated['result'], 'xlsx', output, receipt=calculated['result_receipt'])
    formula = build_formula_workbook(calculated)
    for raw in (output.read_bytes(), result_bytes(calculated['result'], calculated['result_receipt'], 'xlsx'), formula):
        sheet = sheets(raw)['面板已含来源']
        assert sheet.find('s:sheetProtection', NS) is not None
        assert not sheet.findall('.//s:f', NS)
        cells = workbook_sheets(raw)['面板已含来源']
        marker = next(int(pos[1:]) for pos, cell in cells.items()
                      if pos.startswith('A') and cell['value'] == '完整静态来源记录（按序拼接JSON）')
        chunks = sorted((int(pos[1:]), cell['value']) for pos, cell in cells.items()
                        if pos.startswith('A') and int(pos[1:]) > marker)
        assert json.loads(''.join(text for _, text in chunks)) == calculated['result']['static_inclusion_compilations']
    support = formula_workbook_support(calculated)
    assert support['editable']['character_stats'] == 'regenerate_required'
    params = sheets(formula)['参数'].findall('s:sheetData/s:row', NS)
    character_rows = [row for row in params if row.findall('s:c', NS)
                      and ''.join(row.findall('s:c', NS)[0].itertext()).startswith('characters[')]
    assert character_rows and all(row.findall('s:c', NS)[2].get('s') == '5' for row in character_rows)


@pytest.mark.parametrize('route', ['query', 'direct', 'equipment', 'formula'])
def test_partial_proof_stripping_is_rejected_by_actual_entrypoint(calculated, tmp_path, route):
    out = copy.deepcopy(calculated)
    out['result'].pop('static_inclusion_compilations')
    out['result_receipt'].pop('static_inclusions')
    out['result_receipt']['result_fingerprint'] = fingerprint(out['result'])
    with pytest.raises((ValueError, PlanError, FormulaWorkbookError)):
        if route == 'query': review_calculation(saved(out), {'view': 'static_inclusions'})
        elif route == 'direct': export_result(out['result'], 'xlsx', tmp_path / 'bad.xlsx', receipt=out['result_receipt'])
        elif route == 'equipment': result_bytes(out['result'], out['result_receipt'], 'xlsx')
        else: build_formula_workbook(out)


def test_formula_rejects_changed_native_panel_before_using_old_proof(calculated):
    out = copy.deepcopy(calculated)
    out['plan']['manual_template']['characters'][0]['stats']['atk'] += 1
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(out)
