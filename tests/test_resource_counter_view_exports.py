"""Actual resource reads stay frozen across all workbook delivery routes."""
import json

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from tests.test_freeze_export_review import workbook_sheets
from tests.test_reaction_sources_review import evaluate_numeric_cell
from tests.test_resource_counter_view_sources import view_plan
from tests.test_shared_pool_app_delivery import authorize


@pytest.mark.parametrize('layers,cap',[(1,None),(3,16)])
def test_real_app_views_export_exact_ledger_and_final_values_once(tmp_path,layers,cap):
    plan=view_plan();model=plan['mechanics_model'];view=model['resource_counter_views']['views'][0]
    if cap is not None:view['transform']['max_atoms']=cap
    model['rules'][1]['stacking'].update(max=layers,add=layers)
    model['rules'][1]['effects'][0]['value']['factor']=.1
    db=tmp_path/'absent.db';out=run_plan_calc(authorize(plan,db),db)
    rows=out['result']['resource_counter_views']['trace']
    expected=[45*(1+.1*(min(v,cap) if cap is not None else v)/10*layers) for v in (8,16,19)]
    assert [r['expected'] for r in out['result']['hits']]==pytest.approx(expected)
    assert [r['combined_value'] for r in rows]==pytest.approx([.1*(min(v,cap) if cap else v)/10*layers for v in (8,16,19)])
    support=formula_workbook_support(out)
    assert support['editable']['resource_counter_views_and_expressions']=='regenerate_required'
    assert any(r['code']=='counter_view_sample_fixed' for r in support['limitations'])
    target=tmp_path/'result.xlsx';export_result(out['result'],'xlsx',target,receipt=out['result_receipt'])
    formula=build_formula_workbook(out)
    for raw in (target.read_bytes(),result_bytes(out['result'],out['result_receipt'],'xlsx'),formula):
        cells=workbook_sheets(raw)['资源读数流水']
        chunks=sorted((int(c['value'].split()[-1]),cells['B'+r[1:]]['value']) for r,c in cells.items()
                      if r.startswith('A') and isinstance(c['value'],str) and c['value'].startswith('原文块 '))
        assert json.loads(''.join(v for _,v in chunks))==out['result']['resource_counter_views']
        assert all(not c['formula'] for c in cells.values())
    sheets=workbook_sheets(formula);events=sheets['逐事件']
    hit_rows=[r[1:] for r,c in events.items() if r.startswith('A') and c['value'] in {h['id'] for h in out['result']['hits']}]
    assert [evaluate_numeric_cell(sheets,'逐事件','V'+r) for r in hit_rows]==pytest.approx(expected)
    params=sheets['参数']
    fixed=[r[1:] for r,c in params.items() if r.startswith('A') and isinstance(c['value'],str)
           and c['value'].startswith('buffs[') and c['value'].endswith('.value') and params['C'+r[1:]]['value'] in {row['combined_value'] for row in rows}]
    assert len(fixed)==3
    assert all(not params['C'+r]['formula'] and params['I'+r]['formula'] not in (None,'0') for r in fixed)
