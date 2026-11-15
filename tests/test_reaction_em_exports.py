import copy
import sqlite3

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook
from tests.test_reaction_benefits_exports import NS, sheets
from tests.test_reaction_em_app import em_plan as em_plan  # noqa: PLC0414
from tests.test_reaction_em_app import prepared


def test_three_exports_freeze_source_and_actual_reaction_proof(em_plan, tmp_path, monkeypatch):
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    monkeypatch.setattr(sqlite3, 'connect', lambda *a, **k: pytest.fail('Exports use captured source; no live DB'))
    formula = sheets(build_formula_workbook(out))
    snapshot = sheets(result_bytes(out['result'], out['result_receipt'], 'xlsx'))
    path = tmp_path / 'reaction-em.xlsx'
    export_result(
        out['result'], 'xlsx', path, receipt=out['result_receipt'],
    )
    for workbook in (formula, snapshot, sheets(path.read_bytes())):
        sheet = workbook['反应触发精通']
        assert sheet.find('s:sheetProtection', NS) is not None
        assert not sheet.findall('.//s:f', NS)
        text = ''.join(sheet.itertext())
        assert '120' in text and '0:h1:reaction:0' in text and 'final_actual_model_checked' in text
    parameters = formula['参数'].findall('s:sheetData/s:row', NS)
    values = [row for row in parameters if 'mechanics_model.rules[' in ''.join(row.itertext()) and '.value' in ''.join(row.itertext())]
    assert values and all(row.findall('s:c', NS)[2].get('s') == '5' for row in values)


@pytest.mark.parametrize('fmt', ['json', 'csv', 'xlsx'])
def test_receipt_backed_export_rejects_changed_occurrence(em_plan, fmt):
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    out['result']['reaction_em_execution']['activations'][0]['owner'] = 'other'
    with pytest.raises(PlanError):
        result_bytes(out['result'], out['result_receipt'], fmt)


def test_formula_export_rejects_changed_native_target(em_plan):
    plan, db, _, _ = em_plan
    out = run_plan_calc(prepared(plan, db), db)
    bad = copy.deepcopy(out)
    bad['plan']['effect_bindings'][0]['upstream_recipe']['scenario']['target_id'] = 'other'
    with pytest.raises(FormulaWorkbookError):
        build_formula_workbook(bad)
