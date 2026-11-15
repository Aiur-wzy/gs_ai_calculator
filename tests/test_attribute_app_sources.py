"""App source adoption binds actual component identities without classifying by syntax."""
import copy
import json

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_app.equipment.attributes import prepare_attribute_review
from genshin_app.equipment.schema import PlanError
from tests.test_plan_mechanics import native, review


@pytest.fixture(autouse=True)
def _runtime_database_already_admitted(monkeypatch):
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "state": "test_ready"},
    )


def attribute_plan():
    p = native()
    p['manual_template']['characters'][0]['stats']['em'] = 100
    p['mechanics_model']['rules'][0]['effects'][0].update(benefit_type_id='benefit.em.all', value=50)
    p['mechanics_model']['attribute_model'] = {'contract':'attribute-ledger-v1',
        'initial_basis':[{'actor':'a', 'included_components':[],
                         'nonextra':{'mode':'panel_total','status':'assumed','source':'Synthetic ordinary initial EM'}}],
        'effect_classifications':[]}
    return p


def known_classification(p):
    effect = p['mechanics_model']['rules'][0]['effects'][0]
    return {'effect_ref':{'kind':'rule','rule_id':'boost','effect_id':effect['id']},
            'component':{'provider':'a','part_id':'binding:part','effect_id':effect['id'],'source_fingerprint':'stale'},
            'nonextra_inclusion':'included','status':'assumed','source':'Synthetic ordinary EM gain, reviewed explicitly'}


def test_optin_needs_whole_scope_review_not_old_rule_approval(tmp_path):
    p = attribute_plan(); db = tmp_path/'absent.db'; review(p, db)
    report = compose_equipment_plan(p, db)
    assert report['state'] == 'pending'
    assert any(r['code']=='attribute_scope_unreviewed' for r in report['missing_inputs'])
    with pytest.raises(PlanError): run_plan_calc(p, db)


def test_app_fills_unknown_identity_without_promoting_classification_or_native(tmp_path):
    p = attribute_plan(); db = tmp_path/'absent.db'; review(p, db)
    before = copy.deepcopy(p)
    report = prepare_attribute_review(p, 'Reviewed the explicit initial basis; dynamic classification remains unknown', db)
    assert p == before and report['state'] == 'ready', report['missing_inputs']
    assert report['plan']['mechanics_model']['attribute_model']['effect_classifications'] == []
    row = report['mechanics_model']['attribute_model']['effect_classifications'][0]
    assert row['nonextra_inclusion']=='unknown' and row['status']=='pending'
    assert row['component']['provider']=='a' and row['component']['part_id']=='binding:part'
    fresh = compose_equipment_plan(json.loads(json.dumps(report['plan'])), db)
    assert fresh['state']=='ready' and fresh['mechanics_model']==report['mechanics_model']


def test_explicit_adoption_fills_expected_hash_once_and_source_change_invalidates(tmp_path):
    p = attribute_plan(); p['mechanics_model']['attribute_model']['effect_classifications']=[known_classification(p)]
    db = tmp_path/'absent.db'; review(p, db)
    report = prepare_attribute_review(p, 'Reviewed basis and ordinary gain', db)
    assert report['state']=='ready', report['missing_inputs']
    adopted = report['plan']; fp = adopted['mechanics_model']['attribute_model']['effect_classifications'][0]['component']['source_fingerprint']
    assert len(fp)==64 and fp!='stale'
    assert adopted['revision']==p['revision']+1
    adopted['manual_template']['characters'][0]['stats']['em']=200
    stale = compose_equipment_plan(adopted, db)
    assert stale['state']=='pending'
    assert {'attribute_scope_unreviewed','attribute_source_stale'} <= {r['code'] for r in stale['missing_inputs']}


@pytest.mark.parametrize('change', ['provider','part_id','effect_id','duplicate','non_em'])
def test_bad_component_identity_is_not_corrected_by_adoption(tmp_path, change):
    p = attribute_plan(); row = known_classification(p)
    p['mechanics_model']['attribute_model']['effect_classifications']=[row]
    if change in ('provider','part_id','effect_id'): row['component'][change]='wrong'
    if change=='duplicate': p['mechanics_model']['attribute_model']['effect_classifications'].append(copy.deepcopy(row))
    if change=='non_em': p['mechanics_model']['rules'][0]['effects'][0]['benefit_type_id']='benefit.dmg_bonus.all'
    with pytest.raises(PlanError): prepare_attribute_review(p, 'Cannot legitimize wrong source identity', tmp_path/'absent.db')


def test_included_seed_cannot_forge_unregistered_identity(tmp_path):
    p=attribute_plan(); row=known_classification(p)
    row['component']['part_id']='invented_static_component'
    p['mechanics_model']['attribute_model']['initial_basis'][0]['included_components']=[
        {'component':row['component'],'value':50,'nonextra_inclusion':'included'}]
    with pytest.raises(PlanError, match='实际精通来源'):
        prepare_attribute_review(p, 'Seed cannot invent identity', tmp_path/'absent.db')


def test_included_and_dynamic_same_actual_source_get_exact_same_component(tmp_path):
    p=attribute_plan(); row=known_classification(p)
    model=p['mechanics_model']['attribute_model']; model['effect_classifications']=[row]
    model['initial_basis'][0]['included_components']=[{'component':copy.deepcopy(row['component']),'value':50,'nonextra_inclusion':'included'}]
    db=tmp_path/'absent.db'; review(p,db)
    report=prepare_attribute_review(p,'Same-source duplication must remain visible to engine',db)
    mapped=report['mechanics_model']['attribute_model']
    assert mapped['initial_basis'][0]['included_components'][0]['component']==mapped['effect_classifications'][0]['component']
    assert report['plan']['manual_template']['characters'][0]['stats']['em']==100


def test_explicit_exclusion_prunes_execution_classification_only(tmp_path):
    p=attribute_plan(); p['mechanics_model']['attribute_model']['effect_classifications']=[known_classification(p)]
    p['effect_bindings'][0]['parts'][0].update(state='excluded',reason='Engineering source absent')
    db=tmp_path/'absent.db'; review(p,db)
    report=prepare_attribute_review(p,'Reviewed excluded source',db)
    assert report['state']=='ready',report['missing_inputs']
    assert report['mechanics_model']['attribute_model']['effect_classifications']==[]
    assert report['plan']['mechanics_model']['attribute_model']['effect_classifications']
    assert report['attribute_source_adoption']['components'][0]['status']=='excluded'


@pytest.mark.parametrize('basis,classified', [('total',False),('nonextra',True)])
def test_real_app_current_em_read_includes_gain_once(tmp_path,basis,classified):
    p=attribute_plan()
    p['mechanics_model']['rules'][0]['effects'].append({'id':'conversion','benefit_type_id':'benefit.dmg_bonus.all',
        'value':{'kind':'stat','subject':'owner','stat':'em','basis':basis,'factor':.01,'sample':'current'},'domain':'self'})
    if classified:p['mechanics_model']['attribute_model']['effect_classifications']=[known_classification(p)]
    db=tmp_path/'absent.db'; review(p,db)
    adopted=prepare_attribute_review(p,'Engineering initial EM 100 plus one gain 50; ordinary classification only when declared',db)['plan']
    out=run_plan_calc(adopted,db)
    assert [h['expected'] for h in out['result']['hits']]==pytest.approx([112.5,112.5,112.5])
    assert run_plan_calc(json.loads(json.dumps(adopted)),db)['result']==out['result']


def test_unknown_dynamic_classification_blocks_only_actual_nonextra_read(tmp_path):
    p=attribute_plan()
    p['mechanics_model']['rules'][0]['effects'].append({'id':'conversion','benefit_type_id':'benefit.dmg_bonus.all',
        'value':{'kind':'stat','subject':'owner','stat':'em','basis':'nonextra','factor':.01,'sample':'current'},'domain':'self'})
    db=tmp_path/'absent.db'; review(p,db)
    report=prepare_attribute_review(p,'Ordinary initial basis known, dynamic classification intentionally unknown',db)
    assert report['state']=='ready'
    with pytest.raises(PlanError) as failure:run_plan_calc(report['plan'],db)
    assert failure.value.reason=='mechanics_pending'


def test_cli_gui_and_agent_adoption_share_candidate_contract(tmp_path,capsys):
    import uuid

    from genshin_app.agent_chat.candidate import CandidateSession
    from genshin_app.cli import main
    from genshin_app.gui.api import GuiApi

    p=attribute_plan(); p['mechanics_model']['attribute_model']['effect_classifications']=[known_classification(p)]
    db=tmp_path/'genshin.db'; review(p,db)
    source,target=tmp_path/'original.json',tmp_path/'adopted.json'
    source.write_text(json.dumps(p),encoding='utf8'); old=source.read_bytes()
    args=['--data-dir',str(tmp_path),'equipment','adopt-attributes','--plan',str(source),'--note','Engineering explicit review']
    assert main(args+['--output',str(target)])==0
    result=json.loads(capsys.readouterr().out)['data']
    assert result['state']=='ready'
    saved=json.loads(target.read_text(encoding='utf8'))
    assert compose_equipment_plan(saved,db)['state']=='ready'
    assert main(args+['--output',str(source),'--overwrite'])==1
    assert json.loads(capsys.readouterr().err)['reason']=='file_conflict'
    assert source.read_bytes()==old
    gui=GuiApi(tmp_path).prepare_attribute_review(p,'Engineering explicit review')
    assert gui['code']==0 and gui['data']['plan']==saved
    s=CandidateSession(uuid.uuid4().hex,tmp_path/'session',db,'native',p)
    out=s.call_tool('gs_adopt_attributes',{'expected_revision':0,'note':'Engineering explicit review'})
    assert out['code']==0 and s.revision==1
    assert s.calculated is None and not s.summary()['can_accept']
    assert s.report['state']=='ready' and s.candidate['attribute_review']==saved['attribute_review']


def test_attribute_basis_export_keeps_calculated_receipt_and_requires_engine_regeneration(tmp_path):
    import io
    import zipfile

    from genshin_app.equipment.service import result_bytes
    from genshin_app.export import attribute_ledger_rows
    from genshin_app.formula_workbook import (
        build_formula_workbook,
        formula_workbook_support,
    )
    p=attribute_plan(); row=known_classification(p); row['nonextra_inclusion']='excluded'
    p['mechanics_model']['attribute_model']['effect_classifications']=[row]
    p['mechanics_model']['attribute_model']['initial_basis'][0]['nonextra'].update(mode='explicit',value=60)
    p['mechanics_model']['rules'][0]['effects'].append({'id':'conversion','benefit_type_id':'benefit.dmg_bonus.all',
        'value':{'kind':'stat','subject':'owner','stat':'em','basis':'nonextra','factor':.01,'sample':'current'},'domain':'self'})
    db=tmp_path/'absent.db'; review(p,db)
    out=run_plan_calc(prepare_attribute_review(p,'Engineering NONEXTRA 60 independent from panel total 100',db)['plan'],db)
    assert [h['expected'] for h in out['result']['hits']]==pytest.approx([72,72,72])
    workbook=build_formula_workbook(out)
    support=formula_workbook_support(out)
    assert support['editable']['attribute_model_and_basis']=='regenerate_required'
    assert any(r['code']=='attribute_ledger_sample_fixed' for r in support['limitations'])
    with zipfile.ZipFile(io.BytesIO(workbook)) as z:
        assert z.testzip() is None
        assert '属性取样账' in z.read('xl/workbook.xml').decode('utf8')
        assert any('attribute_ledger' in z.read(n).decode('utf8') for n in z.namelist() if n.startswith('xl/worksheets/'))
    exported = result_bytes(out['result'], out['result_receipt'], 'xlsx')
    with zipfile.ZipFile(io.BytesIO(exported)) as z:
        assert '属性取样账' in z.read('xl/workbook.xml').decode('utf8')
    chunks = [row[1] for row in attribute_ledger_rows(out['result'])
              if row and isinstance(row[0], str) and row[0].startswith('原文块 ')]
    assert json.loads(''.join(chunks)) == out['result']['attribute_ledger']
