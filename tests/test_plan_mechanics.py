"""Native plans bind mechanical execution to one reviewed, durable source."""
from __future__ import annotations

import copy
import json

import pytest
from test_mechanics_execution import add, fixture, rule

from genshin_app.equipment import (
    compose_equipment_plan,
    new_plan,
    run_plan_calc,
    validate_plan,
)
from genshin_app.equipment.files import read_plan
from genshin_app.equipment.mechanics import enable_mechanics
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import export_resolved_plan
from genshin_app.export import coverage_rows
from genshin_app.gui.api import GuiApi
from genshin_app.rotation_check import check_document


def native():
    template, rotation, model = fixture()
    template['characters'][0]['stats']['energy_recharge'] = 1
    plan = enable_mechanics(new_plan(template))
    plan['rotation_model'], plan['mechanics_model'] = rotation, model
    add(model, rule(source_id='binding:part'))
    plan['effect_bindings'] = [{
        'binding_id': 'binding', 'provider_character_id': 'a',
        'owner': {'kind': 'manual', 'note': '独立工程机制'},
        'source_refs': [{'kind': 'manual', 'note': '确定性虚构案例，无游戏队伍答案'}],
        'source_fingerprint': None,
        'coverage': {'state': 'unreviewed', 'reviewed_by': None, 'source_fingerprint': None, 'note': None},
        'parts': [{'part_id': 'part', 'label': '独立工程收益', 'state': 'resolved',
                   'rule': {'kind': 'mechanics', 'rule_id': 'boost'}, 'delivery': {'kind': 'mechanics'},
                   'conditions': [], 'dependencies': {'mode': 'source_only', 'context_fingerprint': None, 'note': '显式声明'}, 'reason': None}]}]
    return plan


def review(plan, db):
    report = compose_equipment_plan(plan, db)
    for req in report['effect_requirements']:
        binding = next(b for b in plan['effect_bindings'] if b['binding_id'] == req['binding_id'])
        binding['source_fingerprint'] = req['source_fingerprint']
        binding['coverage'] = {'state': 'complete', 'reviewed_by': '工程测试', 'note': '已核对虚构输入范围',
                               'source_fingerprint': req['source_fingerprint']}


def test_native_run_reopen_and_export_share_the_executed_projection(tmp_path):
    db = tmp_path/'absent.db'
    plan = native()
    assert compose_equipment_plan(plan, db)['state'] == 'pending'
    review(plan, db)
    old = copy.deepcopy(plan)
    out = run_plan_calc(plan, db)
    assert plan == old
    assert [h['expected'] for h in out['result']['hits']] == pytest.approx([90, 90, 90])
    assert out['report']['mechanics_input_template']['buffs'] == []
    check = out['result']['rotation_check']
    assert check['damage_interpretation']['kind'] == 'planned_damage'
    assert check['input_fingerprint'] and check['resolved_rotation_model']
    assert out['result']['coverage_interpretation']['kind'] == 'hit_projection'
    assert coverage_rows(out['result'])[0] == ['覆盖率解释', '说明']
    assert out['plan']['resolved_template']['buffs']
    assert out['report']['equipment_summary'] == out['equipment_summary']
    assert out['equipment_summary']['context']['output_fingerprint'] == out['result_receipt']['output_fingerprint']
    path = tmp_path/'plan.json'
    path.write_text(json.dumps(out['plan']), encoding='utf-8')
    again = run_plan_calc(read_plan(path)['plan'], db)
    assert again['result'] == out['result']
    assert check_document(plan, db) == out['result']['rotation_check']
    resolved = tmp_path/'resolved.json'
    export_resolved_plan(plan, db, resolved)
    assert json.loads(resolved.read_text(encoding='utf-8')) == out['plan']['resolved_template']
    assert not db.exists()


def test_source_edit_requires_new_review_and_exclusion_disables_its_rule(tmp_path):
    plan = native(); db = tmp_path/'absent.db'; review(plan, db)
    plan['effect_bindings'][0]['source_refs'][0]['note'] = '修订后的来源'
    assert compose_equipment_plan(plan, db)['state'] == 'pending'
    review(plan, db)
    part = plan['effect_bindings'][0]['parts'][0]
    part.update(state='excluded', reason='虚构情景明确不具备触发条件')
    out = run_plan_calc(plan, db)
    assert out['report']['mechanics_model']['rules'] == []
    assert out['plan']['mechanics_model']['rules']
    assert [h['expected'] for h in out['result']['hits']] == pytest.approx([45,45,45])


def test_declared_reaction_is_reported_as_unverified_without_elemental_runtime(tmp_path):
    plan = native(); db = tmp_path/'absent.db'
    plan['manual_template']['hits'][0]['reaction'] = {'type': 'amplify', 'factor': 1.5}
    review(plan, db)
    out = run_plan_calc(plan, db)
    interpretation = out['result']['rotation_check']['damage_interpretation']
    verification = interpretation['reaction_verification']
    assert verification['mode'] == 'declared_not_simulated'
    assert verification['status'] == 'unverified'
    assert verification['declared_reaction_hit_count'] == 1
    assert verification['hit_ids'] == ['h0']
    assert verification['omitted_hit_count'] == 0
    assert 'elemental_application_eligibility' in verification['unverified_dimensions']
    assert '不能作为原生反应回放结果' in interpretation['message']


def test_mechanism_source_and_owner_cannot_drift_or_be_delivered_twice(tmp_path):
    plan=native(); db=tmp_path/'absent.db'
    plan['mechanics_model']['rules'][0]['source_id']='other:part'
    with pytest.raises(PlanError, match='来源部件'):
        compose_equipment_plan(plan,db)
    plan=native()
    duplicate=copy.deepcopy(plan['effect_bindings'][0]['parts'][0]);duplicate['part_id']='other'
    plan['effect_bindings'][0]['parts'].append(duplicate)
    with pytest.raises(PlanError,match='重复交付'):
        compose_equipment_plan(plan,db)


def test_upgrade_is_explicit_and_preserves_v2_actions():
    template,rotation,_=fixture()
    plan=new_plan(template);plan['plan_version']=2;plan['rotation_model']=rotation
    old=copy.deepcopy(plan)
    upgraded=enable_mechanics(plan)
    assert plan==old
    assert upgraded['rotation_model']==rotation
    assert upgraded['plan_version']==3
    validate_plan(upgraded)
    plan['mechanics_model']=upgraded['mechanics_model']
    with pytest.raises(PlanError):validate_plan(plan)


def test_mechanical_match_requires_review_and_rule_change_invalidates_it(tmp_path):
    plan=native();db=tmp_path/'absent.db';review(plan,db)
    match={'id':'exception','effect_ref':{'buff_id':'boost','effect_id':'value'},'mode':'adjust',
           'include_hit_ids':[],'exclude_hit_ids':['h0'],'reason':'工程情景排除首击收益',
           'source_refs':[{'kind':'manual','note':'虚构例外'}],'state':'pending','basis_fingerprint':None}
    plan['rotation_tuning']['buff_hit_matches']=[match]
    report=compose_equipment_plan(plan,db)
    assert report['state']=='pending'
    match.update(state='confirmed',basis_fingerprint=report['match_previews'][0]['basis_fingerprint'])
    out=run_plan_calc(plan,db)
    assert [h['expected'] for h in out['result']['hits']]==pytest.approx([45,90,90])
    plan['mechanics_model']['rules'][0]['timing']['duration']=3
    assert compose_equipment_plan(plan,db)['state']=='pending'


def test_gui_bridge_uses_engine_output_and_exports_formula_workbook(tmp_path):
    plan=native();db=tmp_path/'genshin.db';review(plan,db)
    api=GuiApi(tmp_path)
    output=api.run_plan_calc(plan)
    assert output['code']==0
    assert output['data']['result']==run_plan_calc(plan,db)['result']
    assert api.export_formula_workbook(plan,'工程')['code']==0
    assert len(list((tmp_path/'exports').glob('*.xlsx')))==1
    assert api.enable_mechanics(None)['code']==1
    assert api.validate_mechanics_model({'unexpected':True})['data']['status']=='invalid'


def test_chinese_metadata_issues_keep_exact_paths_and_never_change_ids(tmp_path):
    plan=native();db=tmp_path/'absent.db';review(plan,db)
    plan['mechanics_model']['rules'][0]['name_zh']='untranslated'
    before=copy.deepcopy(plan)
    output=run_plan_calc(plan,db)
    assert any(i['path']=='mechanics_model.rules[0].name_zh' for i in output['presentation_issues'])
    assert output['result']['summary']['total_damage']==270
    assert plan==before


@pytest.mark.parametrize('command', ['workbook', 'enable-mechanics'])
def test_new_native_cli_outputs_cannot_overwrite_input_via_backup(
    tmp_path, capsys, command, isolated_cli_database_admission,
):
    from genshin_app.cli import main
    source=tmp_path/'delivery.previous'
    original=json.dumps(native()).encode()
    source.write_bytes(original)
    target=tmp_path/'delivery'
    target.write_text('existing delivery',encoding='utf-8')
    status=main(['--data-dir',str(tmp_path),'equipment',command,'--plan',str(source),
                 '--output',str(target),'--overwrite'])
    assert status!=0
    assert json.loads(capsys.readouterr().err)['reason']=='file_conflict'
    assert source.read_bytes()==original
    assert target.read_text(encoding='utf-8')=='existing delivery'


def test_v3_manual_buff_needs_explicit_identity_and_duplicate_delivery_is_rejected(tmp_path):
    plan=native();db=tmp_path/'absent.db';review(plan,db)
    buff={'id':'old-effect','name':'旧手工增伤','source':'binding:part','start':0,'end':2,
          'effects':[{'id':'old-value','zone':'dmg_bonus','stat':'all','benefit_type_id':'benefit.dmg_bonus.all','value':1,'applies_to':{'mode':'all'}}]}
    plan['manual_template']['buffs']=[buff]
    report=compose_equipment_plan(plan,db)
    assert report['state']=='pending'
    assert any(i['path']=='manual_template.buffs[0].extensions' for i in report['missing_inputs'])
    buff['extensions']={'owner':'a','source_id':'binding:part'}
    with pytest.raises(PlanError) as error:run_plan_calc(plan,db)
    assert any(i['code']=='duplicate_delivery' for i in error.value.details['issues'])
    buff['extensions']['source_id']='manual:independent-fixture'
    assert run_plan_calc(plan,db)['result']['summary']['total_damage']==405


def test_v3_composed_numeric_buff_carries_its_source_identity(tmp_path):
    plan=native();db=tmp_path/'absent.db'
    part=copy.deepcopy(plan['effect_bindings'][0]['parts'][0])
    part.update(part_id='numeric',rule={'kind':'constant','value':.5},delivery={
        'kind':'buff','start':0,'end':2,'scope':'team','target':'a','effect':{
            'id':'numeric-value','benefit_type_id':'benefit.dmg_bonus.all','zone':'dmg_bonus',
            'stat':'all','applies_to':{'mode':'all'}}})
    plan['effect_bindings'][0]['parts'].append(part);review(plan,db)
    output=run_plan_calc(plan,db)
    assert output['report']['mechanics_input_template']['buffs'][0]['extensions']=={'owner':'a','source_id':'binding:numeric'}
    assert output['result']['summary']['total_damage']==337.5


def test_failure_prefix_has_explicit_scope_and_no_cycle_dps(tmp_path):
    from test_mechanics_execution import EVIDENCE, REPEAT, ability, action
    plan=native();db=tmp_path/'absent.db';review(plan,db)
    plan['rotation_model']['characters'][0]['initial_energy']=0
    plan['rotation_model']['abilities'].append(ability('q',kind='action.elemental_burst',
        energy={'required':10,'amount':10}))
    plan['rotation_model']['actions'].append(action('burst','q',1,1.1,2))
    plan['rotation_model']['events'].append({'id':'spend','type':'energy_spend','t':1,'order':3,
        'action_id':'burst','character':'a','required':10,'amount':10,'repeat':REPEAT,**EVIDENCE})
    for index in (1,2):
        plan['manual_template']['hits'][index]['t']=round(1.1+index/10,1)
        plan['mechanics_model']['hit_events'][index]['action_id']='burst'
    out=run_plan_calc(plan,db)
    assert len(out['result']['hits'])==1
    assert out['result']['summary']['dps'] is None
    assert out['result']['rotation_check']['damage_interpretation']['kind']=='executed_prefix'
    import io
    import zipfile

    from genshin_app.equipment.service import result_bytes
    book=result_bytes(out['result'],out['result_receipt'],'xlsx')
    with zipfile.ZipFile(io.BytesIO(book)) as archive:
        assert any('不是完整轴总伤' in archive.read(n).decode('utf-8') for n in archive.namelist() if n.endswith('.xml'))
