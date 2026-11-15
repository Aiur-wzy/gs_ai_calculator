"""Counter source approval must precede actual queue execution."""
import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.resource_counters import prepare_resource_counters
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from tests.test_formula_workbook import read_sheet
from tests.test_resource_counters import definitions
from tests.test_shared_pool_app_delivery import authorize
from tests.test_shared_pool_v2_app import scaled_plan


def counter_plan():
    plan = scaled_plan()
    config = definitions()[0]
    config['counters'][0]['source_id'] = 'binding:part'
    plan['mechanics_model']['resource_counters'] = config
    return plan


def test_counter_app_actual_spend_reopens_and_records_own_approval(tmp_path):
    db = tmp_path/'absent.db'; plan = authorize(counter_plan(), db)
    before = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    ledger = out['result']['resource_counters']
    assert [r['after'] for r in ledger['trace'] if r['type']=='counter_accumulation'] == [8,16,19]
    assert ledger['source_adoptions'] == out['report']['resource_counter_source_adoptions']
    assert ledger['source_adoptions'][0]['effective_status'] == 'ready'
    reopened = GuiApi(tmp_path).run_plan_calc(json.loads(json.dumps(out['plan'])))
    assert reopened['code'] == 0 and reopened['data']['result'] == out['result']
    assert plan == before and not db.exists()


def test_counter_missing_source_cannot_execute_on_approved_pool_and_clock(tmp_path):
    plan = counter_plan(); plan['mechanics_model']['resource_counters']['counters'][0]['source_id'] = 'unknown:part'
    db = tmp_path/'absent.db'; authorize(plan, db)
    out = compose_equipment_plan(plan, db)
    assert out['state'] == 'pending'
    assert 'resource_counters' not in out['mechanics_model']
    assert out['resource_counter_source_adoptions'][0]['own_source_status'] == 'pending'
    with pytest.raises(PlanError): run_plan_calc(plan, db)


def test_counter_excluded_source_removes_accumulation_without_mutating_native(tmp_path):
    plan = counter_plan(); db=tmp_path/'absent.db'; authorize(plan, db)
    plan['effect_bindings'][0]['parts'][0].update(state='excluded',reason='工程情景排除')
    out = run_plan_calc(plan, db)
    assert 'resource_counters' not in out['result']
    assert out['report']['resource_counter_source_adoptions'][0]['effective_status']=='excluded'
    assert plan['mechanics_model']['resource_counters']['counters']


@pytest.mark.parametrize('change',['reset','unit','window'])
def test_counter_and_dependency_edits_invalidate_current_context(tmp_path, change):
    plan=counter_plan(); db=tmp_path/'absent.db'; authorize(plan,db)
    if change=='reset':plan['mechanics_model']['resource_counters']['counters'][0]['reset_on']='end'
    elif change=='window':plan['mechanics_model']['resource_counters']['counters'][0]['accumulate_window']='established_until_end'
    else:plan['mechanics_model']['shared_pools']['pools'][0]['unit_scale']=100
    report=compose_equipment_plan(plan,db)
    assert report['state']=='pending'
    assert report['resource_counter_source_adoptions'][0]['effective_status']!='ready'


def raw_invoke(*, change=None, excluded=(), pool_status='ready', state_status='ready'):
    model=counter_plan()['mechanics_model']
    if change:change(model)
    rules={r['id']:r for r in model['rules']}
    issues=[]
    pools=copy.deepcopy(model['shared_pools']); state=copy.deepcopy(model['state_runtime'])
    records=[{'kind':group,'id':r['id'],'source_id':r['source_id'],'status':pool_status}
        for group in ('pools','operations','consumers') for r in pools[group]]
    lives=[{'kind':group,'id':r['id'],'source_id':r['source_id'],'status':state_status}
        for group in ('lifecycles','timers') for r in state[group]]
    if pool_status!='ready':pools=None
    if state_status!='ready':state=None
    before=copy.deepcopy(model)
    out=prepare_resource_counters(model,rules,set(rules)-set(excluded),set(rules),set(excluded),lambda *a:issues.append(a),
        source_reviews={r['source_id']:{'mode':'manual_context','context_fingerprint':'current','note':'工程完整声明复核'} for r in rules.values()},
        context_fingerprint='current',pools=pools,state=state,pool_adoptions=records,state_adoptions=lives,character_ids={'a','b'})
    assert model==before
    return *out,issues,model


@pytest.mark.parametrize('dependency',['pending','dependency_excluded'])
def test_missing_delivered_dependency_is_not_confused_with_own_source(dependency):
    config,records,issues,_=raw_invoke(pool_status=dependency)
    assert config is None and records[0]['own_source_status']=='ready'
    assert records[0]['effective_status']==('dependency_pending' if dependency=='pending' else 'dependency_excluded')
    assert bool(issues)==(dependency=='pending')


@pytest.mark.parametrize('change',[
    lambda m:m['resource_counters']['counters'][0].update(owner='b'),
    lambda m:m['resource_counters']['counters'][0].update(operation_ids=['absent']),
    lambda m:m['shared_pools']['operations'][0].update(operation='grant'),
])
def test_exclusion_does_not_hide_invalid_original_counter_references(change):
    with pytest.raises(PlanError):raw_invoke(change=change,excluded={'boost'})


def test_partial_counter_exclusion_does_not_remove_independent_spend_or_ready_counter():
    def second(model):
        rule=copy.deepcopy(model['rules'][0]);rule.update(id='counter_only',source_id='other:counter')
        model['rules'].append(rule)
        counter=copy.deepcopy(model['resource_counters']['counters'][0]);counter.update(id='other_count',source_id='other:counter')
        model['resource_counters']['counters'].append(counter)
    kept,records,issues,original=raw_invoke(change=second,excluded={'counter_only'})
    assert not issues and [r['id'] for r in kept['counters']]==['count']
    assert [r['status'] for r in records]==['ready','excluded']
    assert original['shared_pools']['operations'] and original['state_runtime']['timers']


def test_counter_all_three_excel_routes_keep_actual_immutable_ledger(tmp_path):
    db=tmp_path/'absent.db';out=run_plan_calc(authorize(counter_plan(),db),db)
    assert formula_workbook_support(out)['editable']['resource_counter_windows_and_sources']=='regenerate_required'
    target=tmp_path/'counter.xlsx';export_result(out['result'],'xlsx',target,receipt=out['result_receipt'])
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for data in (target.read_bytes(),result_bytes(out['result'],out['result_receipt'],'xlsx'),build_formula_workbook(out)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert z.testzip() is None
            sheets=ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns)
            index=next(i+1 for i,s in enumerate(sheets) if s.attrib['name']=='资源累计流水')
        cells=read_sheet(data,index)
        chunks=sorted((int(c['value'].split()[-1]),cells['B'+r[1:]]['value']) for r,c in cells.items()
            if r.startswith('A') and isinstance(c['value'],str) and c['value'].startswith('原文块 '))
        assert json.loads(''.join(v for _,v in chunks))==out['result']['resource_counters']
        assert all(c['formula'] is None for c in cells.values())
