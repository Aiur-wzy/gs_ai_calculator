"""Saved-input consistency routes keep source and actual-event signals."""
import copy

import pytest

from genshin_app.equipment import resource_reaction_receipt as proof
from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint
from tests.test_resource_reaction_app import reaction_plan
from tests.test_shared_pool_app_delivery import authorize


def calculated(tmp_path, excluded=False):
    db=tmp_path/'absent.db';plan=reaction_plan()
    if excluded:
        next(p for p in plan['effect_bindings'][0]['parts'] if p['part_id']=='refill').update(
            state='excluded',reason='engineering omission')
    return run_plan_calc(authorize(plan,db),db)


def validate(value, *, full=True):
    if full:proof.validate_resource_reaction_receipt(value['plan'],value['result'],value['report'],value['result_receipt'])
    else:proof.validate_resource_reaction_result_receipt(value['result'],value['result_receipt'])


def resign(value):
    value['result_receipt']['resource_reactions']=proof._proof(value['result'],value['result_receipt'])
    value['result_receipt']['result_fingerprint']=fingerprint(value['result'])


@pytest.mark.parametrize('excluded',[False,True])
def test_full_and_result_only_use_actual_saved_inputs_without_mutation(tmp_path,excluded):
    value=calculated(tmp_path,excluded);before=copy.deepcopy(value)
    validate(value);validate(value,full=False)
    assert value==before


@pytest.mark.parametrize('field',['native_panel','native_rotation','model','template','matches','report_adoption'])
def test_full_rejects_changed_native_or_final_execution(tmp_path,field):
    value=calculated(tmp_path)
    if field=='native_panel':value['plan']['manual_template']['characters'][0]['stats']['atk']+=1
    elif field=='native_rotation':value['plan']['rotation_model']['actions'][0]['occupation_end']+=.01
    elif field=='model':value['report']['mechanics_model']['shared_pools']['operations'][0]['amount']+=1
    elif field=='template':value['report']['mechanics_input_template']['characters'][0]['stats']['atk']+=1
    elif field=='matches':value['report']['mechanics_matches'].append({'unapproved':True})
    else:value['report'][proof.ADOPTION]['source_authority']='approved'
    with pytest.raises(ValueError):validate(value)


@pytest.mark.parametrize('field',['program','proof','execution','adoption'])
def test_stripped_required_proof_cannot_degrade_to_legacy(tmp_path,field):
    value=calculated(tmp_path)
    if field=='program':value['result']['resource_reactions'].pop('program')
    elif field=='proof':value['result_receipt'].pop('resource_reactions')
    elif field=='execution':value['result'].pop(proof.EXECUTION)
    else:value['result'].pop(proof.ADOPTION)
    value['result_receipt']['result_fingerprint']=fingerprint(value['result'])
    with pytest.raises(ValueError):validate(value)
    with pytest.raises(ValueError):validate(value,full=False)


@pytest.mark.parametrize('residue',['native','native_resolution','report','report_plan_resolution','other_execution'])
def test_full_requires_evidence_when_only_one_known_signal_remains(tmp_path,residue):
    value=calculated(tmp_path)
    source=copy.deepcopy(value)
    for container in (value['result'],value['report'],value['plan']['resolution'],value['report']['plan']['resolution']):
        for key in ('resource_reactions',proof.ADOPTION,proof.EXECUTION):container.pop(key,None)
    value['result_receipt'].pop('resource_reactions')
    for container in (value['plan'],value['report'],value['report']['plan']):
        container.get('mechanics_model',{}).pop(proof.FIELD,None)
    if residue=='native':value['plan']['mechanics_model'][proof.FIELD]={'schema_version':1,'operation_ids':['refill']}
    elif residue=='native_resolution':value['plan']['resolution'][proof.ADOPTION]=source['result'][proof.ADOPTION]
    elif residue=='report':value['report'][proof.ADOPTION]=source['result'][proof.ADOPTION]
    elif residue=='report_plan_resolution':value['report']['plan']['resolution'][proof.ADOPTION]=source['result'][proof.ADOPTION]
    else:value['result']['snapshot_capture_execution']={'mechanics_model':{proof.FIELD:None}}
    value['result_receipt']['result_fingerprint']=fingerprint(value['result'])
    with pytest.raises(ValueError):validate(value)


@pytest.mark.parametrize('change',['drop_chain','income','actual_parent','wrong_source'])
def test_resigning_local_hashes_does_not_replace_same_engine_consistency(tmp_path,change):
    value=calculated(tmp_path);result=value['result'];audit=result['resource_reactions']
    if change=='drop_chain':
        scope=audit['occurrences'].pop(0)
        audit['operations']=[r for r in audit['operations'] if r['occurrence_id']!=scope['id']]
        result['mechanics_trace']=[r for r in result['mechanics_trace'] if r.get('run_id') not in (scope['parent_run_id'],scope['reaction_run_id'])]
    elif change=='income':audit['operations'][0]['actual_end']+=1
    elif change=='actual_parent':audit['occurrences'][0]['parent_run_id']='foreign'
    else:result[proof.ADOPTION]['operations'][0]['source_owner']='b'
    resign(value)
    with pytest.raises(ValueError):validate(value,full=False)


def test_actual_delayed_rule_boundary_keeps_valid_engine_output(tmp_path):
    db=tmp_path/'absent.db';plan=reaction_plan()
    next(r for r in plan['mechanics_model']['rules'] if r['id']=='grant-authority')['timing']['delay']=12
    out=run_plan_calc(authorize(plan,db),db)
    validate(out);validate(out,full=False)
