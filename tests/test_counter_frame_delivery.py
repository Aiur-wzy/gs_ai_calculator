"""Actual App calculation, query and all three spreadsheet paths."""
import copy
import io
import json
import zipfile

import pytest

from genshin_app.calculation_review import review_calculation
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import build_formula_workbook
from genshin_app.gui.api import GuiApi
from tests.test_counter_self_frame_app import self_plan
from tests.test_shared_pool_app_delivery import authorize


def calculated(tmp_path, *, captured=True):
    db = tmp_path/'absent.db'
    plan = authorize(self_plan(captured=captured), db)
    return plan, run_plan_calc(plan, db, capture_display=True)


@pytest.mark.parametrize('captured,damage', [(True,[81,81,81]),(False,[81,117,130.5])])
def test_three_exports_and_gui_keep_actual_sampling(tmp_path,captured,damage):
    plan,out = calculated(tmp_path,captured=captured)
    original = copy.deepcopy((plan,out))
    result,receipt = out['result'],out['result_receipt']
    assert 'counter_self_frames' in receipt and 'counter_frame_execution' in result
    assert [h['expected'] for h in result['hits']] == damage
    gui = GuiApi(tmp_path).run_plan_calc(json.loads(json.dumps(plan)))
    assert gui['code']==0 and gui['data']['result']==result
    path=tmp_path/'plain.xlsx'
    export_result(result,'xlsx',path,receipt=receipt)
    payloads=[path.read_bytes(),result_bytes(result,receipt,'xlsx'),build_formula_workbook(out)]
    for payload in payloads:
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            names=z.read('xl/workbook.xml').decode('utf8')
            assert '资源读数流水' in names and '累计输出快照' in names
            text=''.join(z.read(p).decode('utf8') for p in z.namelist() if p.endswith('.xml'))
            assert ('捕获时冻结层数' if captured else '当前累计（未指定输出快照）') in text
            assert z.testzip() is None
    assert (plan,out)==original


def test_query_separates_captures_and_reads_with_real_pointers(tmp_path):
    _,out=calculated(tmp_path)
    before=copy.deepcopy(out)
    captures=review_calculation(out,{'view':'resource_counter_frames','limit':1})
    reads=review_calculation(out,{'view':'resource_counter_views','limit':1})
    assert captures['selection_count']==1 and captures['next_offset'] is None
    assert reads['selection_count']==3 and reads['next_offset']==1
    capture,read=captures['items'][0],reads['items'][0]
    assert capture['frame_id']==read['frame_id']
    assert capture['context']==read['capture_context']
    assert capture['json_pointer']=='/result/resource_counter_views/historical_capture/capture_trace/0'
    assert read['frame_selection']=='historical_reference'
    assert read['gate_qualification']['event_run_id']==read['run_id']
    assert read['reading']['qualification']=='frozen_member_current_gate_accepted'
    filtered=review_calculation(out,{'view':'resource_counter_frames','filters':{'event_id':'counter-output','start':.14,'end':.16}})
    assert filtered['selection_count']==1
    assert out==before


@pytest.mark.parametrize('entry', ['result_bytes','export','formula','query'])
def test_all_delivery_paths_reject_stripped_capture_even_if_result_rehashed(tmp_path,entry):
    _,out=calculated(tmp_path)
    del out['result']['resource_counter_views']['historical_capture']
    out['result_receipt']['result_fingerprint']=fingerprint(out['result'])
    with pytest.raises((PlanError,ValueError)):
        if entry=='result_bytes':result_bytes(out['result'],out['result_receipt'],'xlsx')
        elif entry=='export':export_result(out['result'],'xlsx',tmp_path/'bad.xlsx',receipt=out['result_receipt'])
        elif entry=='formula':build_formula_workbook(out)
        else:review_calculation(out,{'view':'resource_counter_frames'})
