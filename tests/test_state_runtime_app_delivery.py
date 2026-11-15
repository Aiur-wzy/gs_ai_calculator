"""Actual reviewed App state graph must survive GUI and all Excel exporters."""
import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from genshin_engine.state_runtime_mechanics import route_event_id
from tests.test_formula_workbook import read_sheet
from tests.test_plan_mechanics import native, review
from tests.test_shared_pool_app_delivery import authorize
from tests.test_state_runtime import config


def state_plan():
    plan=native(); model=plan['mechanics_model']
    for action in plan['rotation_model']['actions']:
        action['repeat']={'mode':'once'}
    state=config()
    state['lifecycles'][0]['source_id']='binding:part'
    state['lifecycles'][0]['start']['action_ids']=['cast']
    state['timers'][0].update(source_id='binding:part',payload={'kind':'dispatch_rule','ref_id':'boost'})
    model['rules'][0]['trigger']={'event':'condition','phase':'pre','event_ids':[route_event_id('dispatch_rule','boost')]}
    model['rules'][0]['timing']['duration']=1.1
    model['state_runtime']=state
    return plan


def test_state_old_rule_review_does_not_approve_new_timer_graph(tmp_path):
    p=state_plan(); db=tmp_path/'absent.db'; review(p,db)
    report=compose_equipment_plan(p,db)
    assert report['state']=='pending'
    assert report['state_runtime_source_adoptions']
    with pytest.raises(PlanError):run_plan_calc(p,db)


def test_actual_state_app_gui_and_all_workbooks_preserve_full_receipt(tmp_path):
    db=tmp_path/'absent.db'; p=authorize(state_plan(),db); before=copy.deepcopy(p)
    out=run_plan_calc(p,db)
    assert p==before
    assert all(r['status']=='ready' for r in out['report']['state_runtime_source_adoptions'])
    assert out['result']['state_runtime']['engine_queue_integration']=='mechanics_event_queue'
    assert out['result']['state_runtime']['source_adoptions']==out['report']['state_runtime_source_adoptions']
    assert [r['time_us'] for r in out['result']['state_runtime']['execution_trace']]==[0,1000000]
    assert GuiApi(tmp_path).run_plan_calc(json.loads(json.dumps(p)))['data']['result']==out['result']
    support=formula_workbook_support(out)
    assert support['editable']['state_runtime_lifecycle_timers']=='regenerate_required'
    direct=tmp_path/'state.xlsx'; export_result(out['result'],'xlsx',direct,receipt=out['result_receipt'])
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for data in (direct.read_bytes(),result_bytes(out['result'],out['result_receipt'],'xlsx'),build_formula_workbook(out)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert z.testzip() is None
            sheets=ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns)
            index=next(i+1 for i,s in enumerate(sheets) if s.attrib['name']=='生命周期流水')
        cells=read_sheet(data,index)
        chunks=sorted((int(c['value'].split()[-1]),cells['B'+r[1:]]['value']) for r,c in cells.items()
            if r.startswith('A') and isinstance(c['value'],str) and c['value'].startswith('原文块 '))
        assert json.loads(''.join(v for _,v in chunks))==out['result']['state_runtime']
        assert all(c['formula'] is None for c in cells.values())
    assert p==before


def test_state_exclusion_and_edit_cannot_leave_timer_executable(tmp_path):
    p=state_plan(); db=tmp_path/'absent.db'; authorize(p,db)
    p['mechanics_model']['state_runtime']['timers'][0]['interval_us']=500000
    assert compose_equipment_plan(p,db)['state']=='pending'
    with pytest.raises(PlanError):run_plan_calc(p,db)
    authorize(p,db)
    p['effect_bindings'][0]['parts'][0].update(state='excluded',reason='No field in this scenario')
    out=run_plan_calc(p,db)
    assert 'state_runtime' not in out['report']['mechanics_model']
    assert 'state_runtime' not in out['result']
    assert out['report']['mechanics_model']['rules']==[]
    assert p['mechanics_model']['state_runtime']['timers']
