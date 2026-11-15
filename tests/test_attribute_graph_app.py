"""Actual v2 conversion receipts survive App review and read-only exports."""
import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import (
    build_formula_workbook,
    formula_workbook_support,
)
from genshin_app.gui.api import GuiApi
from tests.test_attribute_graph_runtime import mutual
from tests.test_attribute_runtime import reader
from tests.test_formula_workbook import read_sheet
from tests.test_mechanics_execution import add
from tests.test_plan_mechanics import native, review


def graph_plan(basis='nonextra'):
    plan=native(); model=plan['mechanics_model']; skeleton=copy.deepcopy(plan['effect_bindings'][0])
    model['rules']=[]; model['initial_state']['rules']=[]; plan['effect_bindings']=[]
    mutual(plan['manual_template'],plan['rotation_model'],model,basis)
    for actor in plan['manual_template']['characters']:
        actor['stats']['energy_recharge']=1
    add(model,reader())
    for rule in model['rules']:
        bid='bind_'+rule['id']; rule['source_id']=bid+':part'
        binding=copy.deepcopy(skeleton); binding.update(binding_id=bid,provider_character_id=rule['owner'])
        binding['parts'][0]['rule']['rule_id']=rule['id']; plan['effect_bindings'].append(binding)
    for row in model['attribute_model']['effect_classifications']:
        row['component']['part_id']='bind_'+row['effect_ref']['rule_id']+':part'
    return plan


def adopt(plan,db):
    review(plan,db)
    return prepare_attribute_review(plan,'工程互授：基础精通100/200；按非额外精通20%转换，转换量不再次进入NONEXTRA。',db)['plan']


def test_conversion_graph_app_gui_and_excel_preserve_real_samples(tmp_path):
    db=tmp_path/'absent.db'; plan=adopt(graph_plan(),db); before=copy.deepcopy(plan)
    out=run_plan_calc(plan,db); ledger=out['result']['attribute_ledger']
    assert [h['expected'] for h in out['result']['hits']]==pytest.approx([108]*3)
    assert any(r['query']['actor']=='a' and r['query']['basis']=='total' and r['value']==140 for r in ledger['receipts'])
    assert all(r['contract']=='attribute-ledger-v2' for r in ledger['receipts'])
    assert GuiApi(tmp_path).run_plan_calc(json.loads(json.dumps(out['plan'])))['data']['result']==out['result']
    support=formula_workbook_support(out)
    assert support['editable']['attribute_model_and_basis']=='regenerate_required'
    path=tmp_path/'conversion.xlsx'; export_result(out['result'],'xlsx',path,receipt=out['result_receipt'])
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for payload in (path.read_bytes(),result_bytes(out['result'],out['result_receipt'],'xlsx'),build_formula_workbook(out)):
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            assert z.testzip() is None
            sheets=ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns)
            index=next(i+1 for i,s in enumerate(sheets) if s.attrib['name']=='属性取样账')
        cells=read_sheet(payload,index)
        chunks=sorted((int(c['value'].split()[-1]),cells['B'+r[1:]]['value']) for r,c in cells.items()
            if r.startswith('A') and isinstance(c['value'],str) and c['value'].startswith('原文块 '))
        assert json.loads(''.join(v for _,v in chunks))==ledger
        assert all(c['formula'] is None for c in cells.values())
    assert plan==before


def test_changed_provider_panel_requires_review_and_regenerates_conversion(tmp_path):
    db=tmp_path/'absent.db'; plan=adopt(graph_plan(),db)
    plan['manual_template']['characters'][1]['stats']['em']=300
    assert compose_equipment_plan(plan,db)['state']=='pending'
    with pytest.raises(PlanError):run_plan_calc(plan,db)
    out=run_plan_calc(adopt(plan,db),db)
    assert [h['expected'] for h in out['result']['hits']]==pytest.approx([117]*3)


def test_total_conversion_cycle_cannot_be_authorized_into_a_damage_result(tmp_path):
    db=tmp_path/'absent.db'; plan=adopt(graph_plan('total'),db)
    with pytest.raises(PlanError) as exc:run_plan_calc(plan,db)
    assert 'attribute_dependency_cycle' in str(exc.value.details)
