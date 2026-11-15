"""Full native/compiled and result-only v3 receipt coherence, without DB replay."""
import copy

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import FormulaWorkbookError, build_formula_workbook
from tests.test_attribute_stat_app import adopt, stat_plan


@pytest.fixture
def out(tmp_path):
    db=tmp_path/'absent.db'
    return run_plan_calc(adopt(stat_plan(percent=True),db),db)


@pytest.mark.parametrize('target',['native_atk','native_base','native_em','model','rotation','matches','template','adoption','ledger','proof'])
def test_full_package_rejects_native_or_compiled_attribute_drift(out,target):
    changed=copy.deepcopy(out)
    if target.startswith('native_'):
        key={'native_atk':'atk','native_base':'base_atk','native_em':'em'}[target]
        changed['plan']['manual_template']['characters'][0]['stats'][key]+=1
    elif target=='model':changed['report']['mechanics_model']['rules'][0]['effects'][0]['value']['factor']+=.01
    elif target=='rotation':changed['report']['compiled_rotation_model']=copy.deepcopy(changed['plan']['rotation_model']);changed['report']['compiled_rotation_model']['actions'][0]['start']+=.01
    elif target=='matches':changed['report']['mechanics_matches']=[{'unsupported':'changed'}]
    elif target=='template':changed['report']['mechanics_input_template']['characters'][0]['stats']['em']+=1
    elif target=='adoption':changed['report']['attribute_source_adoption']['review_status']='pending'
    elif target=='ledger':changed['result']['attribute_ledger']['receipts'][0]['value']+=1;changed['result_receipt']['result_fingerprint']=fingerprint(changed['result'])
    else:changed['result_receipt'].pop('attribute_ledger',None)
    with pytest.raises(FormulaWorkbookError):build_formula_workbook(changed)


@pytest.mark.parametrize('target',['ledger','sampling','hits','execution','proof','proof_model','proof_template','proof_matches'])
def test_result_only_routes_require_coherent_attribute_proof(out,tmp_path,target):
    result,receipt=copy.deepcopy(out['result']),copy.deepcopy(out['result_receipt'])
    if target=='ledger':result['attribute_ledger']['receipts'][0]['value']+=1
    elif target=='sampling':result['sampling_trace']=[]
    elif target=='hits':result['hits'][0]['expected']+=1
    elif target=='execution':result['rotation_check']['input_fingerprint']='1'*64
    elif target.startswith('proof_'):receipt['attribute_ledger'][target[6:]+'_fingerprint']='1'*64
    else:receipt.pop('attribute_ledger',None)
    receipt['result_fingerprint']=fingerprint(result)
    with pytest.raises(PlanError):result_bytes(result,receipt,'xlsx')
    with pytest.raises(ValueError):export_result(result,'xlsx',tmp_path/'bad.xlsx',receipt=receipt)


def test_actual_v3_proof_is_bounded_consistency_not_replay_or_authority(out,monkeypatch):
    import genshin_engine.mechanics
    monkeypatch.setattr(genshin_engine.mechanics,'execute_mechanics',lambda *a,**k:pytest.fail('No replay'))
    proof=out['result_receipt']['attribute_ledger']
    assert proof['verification_scope']=='saved_attribute_execution_consistency_no_replay'
    assert build_formula_workbook(out)
    assert result_bytes(out['result'],out['result_receipt'],'xlsx')
