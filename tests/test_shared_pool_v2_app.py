"""Scaled resource operations survive native App delivery without invented units."""
import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result, shared_pool_rows
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_engine.state_runtime_mechanics import route_event_id
from tests.test_formula_workbook import read_sheet
from tests.test_plan_mechanics import native
from tests.test_shared_pool_app_delivery import authorize
from tests.test_shared_pool_v2 import scaled
from tests.test_state_runtime import config


def scaled_plan():
    plan = native()
    plan['rotation_model']['actions'][0]['repeat'] = {'mode': 'once'}
    model = plan['mechanics_model']
    pools = scaled(initial=19)
    pools['consumers'] = []
    for pool in pools['pools']:
        pool['source_id'] = 'binding:part'
    pools['operations'][0].update(source_id='binding:part', trigger={'event':'condition','phase':'post',
        'event_ids':[route_event_id('resource_operation','refill')]})
    state = config(duration=400_000, first=100_000, interval=100_000)
    state['lifecycles'][0].update(source_id='binding:part')
    state['lifecycles'][0]['start']['action_ids'] = ['cast']
    state['timers'][0].update(source_id='binding:part', max_ticks=3,
        payload={'kind':'resource_operation','ref_id':'refill'})
    model.update(shared_pools=pools, state_runtime=state)
    return plan


def test_scaled_app_reopens_and_all_workbooks_preserve_actual_atom_ledger(tmp_path):
    db = tmp_path/'absent.db'; plan = authorize(scaled_plan(), db); before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    ledger = out['result']['shared_pools']
    trace = [r for r in ledger['trace'] if r['cycle']==0]
    assert [(r['accepted'],r['after']) for r in trace] == [(8,11),(8,3),(3,0)]
    assert trace[-1]['unfulfilled'] == 5
    assert ledger['schema_version'] == 2 and ledger['state']['unit_scales'] == {'pool':10}
    assert GuiApi(tmp_path).run_plan_calc(json.loads(json.dumps(out['plan'])))['data']['result'] == out['result']
    rows = shared_pool_rows(out['result'])
    assert rows[5][3] == '扣除资源' and rows[5][12:15] == [10,5,None]
    assert formula_workbook_support(out)['editable']['shared_pools_and_resource_gates'] == 'regenerate_required'
    direct = tmp_path/'scaled.xlsx'; export_result(out['result'],'xlsx',direct,receipt=out['result_receipt'])
    ns = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for payload in (direct.read_bytes(),result_bytes(out['result'],out['result_receipt'],'xlsx'),build_formula_workbook(out)):
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            assert z.testzip() is None
            sheets = ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns)
            index = next(i+1 for i,s in enumerate(sheets) if s.attrib['name']=='共享资源流水')
        cells = read_sheet(payload,index)
        chunks = sorted((int(c['value'].split()[-1]),cells['B'+r[1:]]['value']) for r,c in cells.items()
            if r.startswith('A') and isinstance(c['value'],str) and c['value'].startswith('原文块 '))
        assert json.loads(''.join(v for _,v in chunks)) == ledger
        assert all(c['formula'] is None for c in cells.values())
    assert plan == before


@pytest.mark.parametrize('edit',['scale','policy'])
def test_scaled_input_edit_requires_current_context_approval(tmp_path,edit):
    db = tmp_path/'absent.db'; plan = authorize(scaled_plan(),db)
    pools = plan['mechanics_model']['shared_pools']
    if edit=='scale': pools['pools'][0]['unit_scale']=100
    else: pools['operations'][0]['spend_policy']='all_or_nothing'
    assert compose_equipment_plan(plan,db)['state']=='pending'
    with pytest.raises(PlanError): run_plan_calc(plan,db)


def test_scaled_source_exclusion_removes_periodic_spend_and_pool_together(tmp_path):
    db = tmp_path/'absent.db'; plan = authorize(scaled_plan(),db)
    plan['effect_bindings'][0]['parts'][0].update(state='excluded',reason='合成情景明确不包含此资源来源')
    out = run_plan_calc(plan,db)
    assert 'shared_pools' not in out['result'] and 'state_runtime' not in out['result']
    assert [h['expected'] for h in out['result']['hits']] == [45,45,45]
