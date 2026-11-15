"""Versioned static-level binding supplies raw multipliers, never full skill logic."""
import copy
import io
import json
import zipfile

import pytest

from genshin_app.equipment import compose_equipment_plan, run_plan_calc, validate_plan
from genshin_app.equipment.schema import PlanError
from genshin_app.equipment.service import result_bytes
from genshin_app.export import export_result
from genshin_app.formula_workbook import build_formula_workbook
from genshin_app.gui.api import GuiApi
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_plan_mechanics import native
from tests.test_runtime_talent_level import level_db, mutate  # noqa: F401

# ruff: noqa: F811


def level_plan(db,constellation=3):
    plan=native(); plan['mechanics_model']['rules']=[]; plan['mechanics_model']['initial_state']['rules']=[]; plan['effect_bindings']=[]
    hit=plan['manual_template']['hits'][0]; hit.pop('multiplier'); hit['scaling_stat']='atk'
    selection={'character_id':90000001,'slot':'skill','constellation':constellation,'base_level':10}
    projected=query_runtime(db,{'action':'action_parameter_at_level',**selection,'param_index':0})
    plan['hit_parameter_sources']=[{'hit_id':'h0','version_line':'release','selection':selection,
        'parameter_contract':'level-bound-parameter-v1','param_index':0,'scaling_stat':'atk',
        'purpose':'single_stat_damage_multiplier','status':'assumed','source':'工程夹具原始参数的明确单属性用途',
        'expected_snapshot_id':projected['snapshot']['snapshot_id'],'expected_node_sha256':projected['source']['node_sha256'],
        'expected_level_fingerprint':projected['level_profile']['content_fingerprint']}]
    return plan


@pytest.mark.parametrize('constellation,expected',[(0,135),(3,148.5)])
def test_level_binding_app_reopen_gui_and_exports(level_db,tmp_path,constellation,expected):
    plan=level_plan(level_db,constellation); before=copy.deepcopy(plan)
    out=run_plan_calc(plan,level_db)
    assert out['result']['hits'][0]['expected']==expected
    record=out['result']['hit_parameter_sources'][0]
    assert record['compiler']=='database-hit-parameter-v2'
    assert record['operation_execution']=='not_evaluated' and record['action_dispatch']['status']=='pending'
    assert record['level_profile']['execution_support']=='static_level_only'
    assert 'multiplier' not in out['plan']['manual_template']['hits'][0]
    assert GuiApi(level_db.parent).run_plan_calc(json.loads(json.dumps(out['plan'])))['data']['result']==out['result']
    direct=tmp_path/'level.xlsx'; export_result(out['result'],'xlsx',direct,receipt=out['result_receipt'])
    for payload in (direct.read_bytes(),result_bytes(out['result'],out['result_receipt'],'xlsx'),build_formula_workbook(out)):
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            assert z.testzip() is None
            assert '技能参数来源' in z.read('xl/workbook.xml').decode('utf8')
            assert any(b'expected_level_fingerprint' in z.read(name) or b'level_profile' in z.read(name)
                       for name in z.namelist() if name.startswith('xl/worksheets/'))
    assert plan==before


@pytest.mark.parametrize('edit',['legacy_level','missing_fingerprint','missing_contract'])
def test_level_native_versions_are_strictly_separated(level_db,edit):
    plan=level_plan(level_db); row=plan['hit_parameter_sources'][0]
    if edit=='legacy_level': row['selection']['talent_level']=13
    elif edit=='missing_fingerprint': row.pop('expected_level_fingerprint')
    else: row.pop('parameter_contract')
    with pytest.raises(PlanError):validate_plan(plan)


def test_same_parameter_node_does_not_approve_changed_constellation_dependencies(level_db,fake_repo):
    plan=level_plan(level_db); row=plan['hit_parameter_sources'][0]
    # A dependency changes while the selected skill's exact table node does not.
    path=fake_repo/'BinOutput/Talent/AvatarTalents/FixtureLevel.json'; ops=json.loads(path.read_text('utf8'))
    ops['LevelC1'][0]['abilityName']='Fixture_Ability_Changed'
    path.write_text(json.dumps(ops),'utf8'); fixture_builder.build_database(level_db.parent,force=True)
    current=compose_equipment_plan(plan,level_db)['hit_parameter_compilations'][0]
    assert row['expected_node_sha256']==current['required_confirmation']['expected_node_sha256']
    row.update(expected_snapshot_id=current['required_confirmation']['expected_snapshot_id'],
               expected_node_sha256=current['required_confirmation']['expected_node_sha256'])
    assert row['expected_level_fingerprint']!=current['required_confirmation']['expected_level_fingerprint']
    report=compose_equipment_plan(plan,level_db)
    assert report['state']=='pending' and any(r['code']=='source_changed' for r in report['missing_inputs'])


def test_pending_level_never_delivers_candidate_value(level_db,fake_repo):
    plan=level_plan(level_db); mutate(level_db,fake_repo,'unknown',1)
    report=compose_equipment_plan(plan,level_db); record=report['hit_parameter_compilations'][0]
    assert record['source'] is None and record['scalar_projection']['value'] is None
    assert record['level']['effective_level'] is None and record['status']=='pending'
    with pytest.raises(PlanError):run_plan_calc(plan,level_db)


def test_slot_alias_cannot_supply_conflicting_base_level(level_db):
    plan=level_plan(level_db); second=copy.deepcopy(plan['hit_parameter_sources'][0]); second['hit_id']='h1'
    second['selection'].pop('slot'); second['selection'].update(skill_id=90012,base_level=9)
    plan['manual_template']['hits'][1].pop('multiplier'); plan['manual_template']['hits'][1]['scaling_stat']='atk'
    plan['hit_parameter_sources'].append(second)
    with pytest.raises(PlanError,match='不同有效等级'):compose_equipment_plan(plan,level_db)


def test_context_is_loaded_once_for_same_level_selection(level_db,monkeypatch):
    from genshin_db import runtime_talent_level
    plan=level_plan(level_db); second=copy.deepcopy(plan['hit_parameter_sources'][0]); second['hit_id']='h1'
    plan['manual_template']['hits'][1].pop('multiplier'); plan['manual_template']['hits'][1]['scaling_stat']='atk'
    plan['hit_parameter_sources'].append(second)
    calls=[]; original=runtime_talent_level.get_level_parameter_context
    def counted(*args,**kwargs):calls.append(kwargs); return original(*args,**kwargs)
    monkeypatch.setattr(runtime_talent_level,'get_level_parameter_context',counted)
    assert compose_equipment_plan(plan,level_db)['state']=='ready' and len(calls)==1
    compose_equipment_plan(plan,level_db)
    assert len(calls)==2
