"""Actual v3 App, saved result, GUI API and three Excel representations."""
import copy
import io
import json
import xml.etree.ElementTree as ET
import zipfile

import pytest

from genshin_app.calculation_review import review_calculation
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
from tests.test_attribute_graph_runtime import classify
from tests.test_attribute_stat_runtime import setup, value
from tests.test_formula_workbook import read_sheet
from tests.test_mechanics_execution import add, rule
from tests.test_plan_mechanics import native, review


def stat_plan(percent=False):
    plan=native();skeleton=copy.deepcopy(plan['effect_bindings'][0]);t,r,m=setup()
    for actor in t['characters']:actor['stats']['energy_recharge']=1
    if percent:
        add(m,rule('percent','benefit.stat_pct.atk',value(factor=.002,max=.3),domain='provider'))
        classify(m,'percent','a','unknown')
    else:
        add(m,rule('to_atk','benefit.stat_add.atk',value(),domain='provider'),
            rule('to_b_em','benefit.em.all',value('atk',.2),domain='provider',
                beneficiaries={'mode':'characters','characters':['b'],'scope':'locked'}),
            rule('read_b','benefit.base_add.all',value('em',1),owner='b',domain='provider'))
        classify(m,'to_atk','a','unknown');classify(m,'to_b_em','a','included')
    plan.update(manual_template=t,rotation_model=r,mechanics_model=m,effect_bindings=[])
    for source in m['rules']:
        bid='bind_'+source['id'];source['source_id']=bid+':part'
        binding=copy.deepcopy(skeleton);binding.update(binding_id=bid,provider_character_id=source['owner'])
        binding['parts'][0]['rule']['rule_id']=source['id'];plan['effect_bindings'].append(binding)
    for row in m['attribute_model']['effect_classifications']:
        row['component']['part_id']='bind_'+row['effect_ref']['rule_id']+':part'
    return plan


def adopt(plan,db):
    review(plan,db)
    result=prepare_attribute_review(plan,'工程场景：初始面板与来源分类明确；ATK比例以受益者白值应用一次。',db)
    assert result['state']=='ready',result['missing_inputs']
    return result['plan']


def test_actual_cross_stat_chain_gui_reopen_and_saved_query(tmp_path):
    db=tmp_path/'absent.db';plan=adopt(stat_plan(),db);before=copy.deepcopy(plan)
    out=run_plan_calc(plan,db)
    assert [h['expected'] for h in out['result']['hits']]==[117]
    assert GuiApi(tmp_path).run_plan_calc(json.loads(json.dumps(out['plan'])))['data']['result']==out['result']
    query=review_calculation({key:out[key] for key in ('result','result_receipt')},
                             {'view':'attributes','filters':{'stat':'atk','basis':'total'}})
    assert query['items'] and all(x['stat']=='atk' and x['basis']=='total' for x in query['items'])
    assert any(x['value']==200 for x in query['items'])
    assert plan==before


@pytest.mark.parametrize('field,new_value,expected',[('atk',150,51.75+22.5),('base_atk',100,58.5)])
def test_panel_and_base_changes_require_fresh_review_then_recompute(tmp_path,field,new_value,expected):
    db=tmp_path/'absent.db';plan=adopt(stat_plan(percent=True),db)
    plan['manual_template']['characters'][0]['stats'][field]=new_value
    assert compose_equipment_plan(plan,db)['state']=='pending'
    with pytest.raises(PlanError):run_plan_calc(plan,db)
    out=run_plan_calc(adopt(plan,db),db)
    assert out['result']['hits'][0]['expected']==pytest.approx(expected)


def test_three_excels_keep_stat_and_ratio_base_flat_separate_with_raw_ledger(tmp_path):
    db=tmp_path/'absent.db';out=run_plan_calc(adopt(stat_plan(percent=True),db),db)
    path=tmp_path/'actual.xlsx';export_result(out['result'],'xlsx',path,receipt=out['result_receipt'])
    payloads=[path.read_bytes(),result_bytes(out['result'],out['result_receipt'],'xlsx'),build_formula_workbook(out)]
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    for payload in payloads:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            sheets=ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet',ns)
            index=next(i+1 for i,s in enumerate(sheets) if s.attrib['name']=='属性取样账')
        cells=read_sheet(payload,index)
        values={x['value'] for x in cells.values()}
        assert {'攻击力','元素精通','实际比例（1为100%）','使用的基础攻击力','乘后平值'}<=values
        header=next(int(ref[1:]) for ref,c in cells.items() if ref.startswith('A') and c['value']=='总量回执')
        assert [cells[f'{col}{header+1}']['value'] for col in ('H','I','J')]==[.3,50,15]
        chunks=sorted((int(c['value'].split()[-1]),cells['B'+ref[1:]]['value']) for ref,c in cells.items()
            if ref.startswith('A') and isinstance(c['value'],str) and c['value'].startswith('原文块 '))
        assert json.loads(''.join(v for _,v in chunks))==out['result']['attribute_ledger']
        assert all(c['formula'] is None for c in cells.values())


def test_formula_book_blocks_panel_or_rule_changes_from_reusing_old_attribute_receipts(tmp_path):
    db=tmp_path/'absent.db';out=run_plan_calc(adopt(stat_plan(percent=True),db),db)
    support=formula_workbook_support(out)
    for name in ('character_stats','numeric_buff_values','declared_stat_expression'):
        assert support['editable'][name]=='regenerate_required'
    cells=read_sheet(build_formula_workbook(out),2)
    for path in ('characters[0].stats.atk','characters[0].stats.base_atk','characters[0].stats.em',
                 'mechanics_model.rules[0].effects[0].value.factor'):
        row=next(int(ref[1:]) for ref,c in cells.items() if ref.startswith('A') and c['value']==path)
        assert cells[f'E{row}']['value']=='回 App 重新生成'
        assert cells[f'I{row}']['formula']==f'IF(C{row}<>D{row},1,0)'
