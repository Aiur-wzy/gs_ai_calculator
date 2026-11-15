"""General constellation targets, exact table selection and unknown boundaries."""
import copy
import hashlib
import json

import pytest

from genshin_db import runtime_talent_level as module
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401

# ruff: noqa: F811


@pytest.fixture
def level_db(tmp_path, fake_repo, stub_fetcher, monkeypatch):
    def read(name):
        path=fake_repo/f'ExcelBinOutput/{name}ExcelConfigData.json'
        return path,json.loads(path.read_text('utf8'))
    path,rows=read('AvatarSkillDepot'); rows[0].update(skills=[90011,90012],energySkill=90013,talents=list(range(9901,9907)))
    path.write_text(json.dumps(rows),'utf8')
    path,rows=read('AvatarSkill')
    template=rows[0]
    path.write_text(json.dumps([{**template,'id':90010+i,'proudSkillGroupId':91000+i} for i in (1,2,3)]),'utf8')
    path,_=read('AvatarTalent')
    path.write_text(json.dumps([{'talentId':9900+i,'openConfig':f'LevelC{i}','paramList':[],'descTextMapHash':202} for i in range(1,7)]),'utf8')
    path,_=read('ProudSkill')
    path.write_text(json.dumps([{'proudSkillId':(91000+slot)*100+level,'proudSkillGroupId':91000+slot,'level':level,
                               'paramList':[slot+level/10],'paramDescList':[201]} for slot in (1,2,3) for level in (10,12,13,15)]),'utf8')
    ops={f'LevelC{i}':[{'$type':'AddAbility','CLGIKOPIKBC':True,'abilityName':'Fixture_Ability'}] for i in range(1,7)}
    for rank,index in [(3,2),(5,9)]:
        ops[f'LevelC{rank}']=[{'$type':'AddTalentExtraLevel','talentType':'AvatarSkill','talentIndex':index,'extraLevel':3,'CLGIKOPIKBC':True}]
    path=fake_repo/'BinOutput/Talent/AvatarTalents/FixtureLevel.json';path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(ops),'utf8')
    profile=copy.deepcopy(module._recipe()); profile['commit']='a'*40
    monkeypatch.setattr(module,'_recipe',lambda:profile)
    fixture_builder.build_database(tmp_path)
    return tmp_path/'genshin.db'


def lookup(db,**kwargs):
    return module.get_runtime_talent_level(db,character_id=90000001,slot='skill',**kwargs)


def mutate(db,repo,field,value):
    path=repo/'BinOutput/Talent/AvatarTalents/FixtureLevel.json';raw=json.loads(path.read_text('utf8'))
    raw['LevelC3'][0][field]=value; path.write_text(json.dumps(raw),'utf8');fixture_builder.build_database(db.parent,force=True)


@pytest.mark.parametrize('c,level',[(0,10),(1,10),(2,10),(3,13),(5,13),(6,13)])
def test_exact_active_ranks_and_target_table_are_source_bound(level_db,c,level):
    before=hashlib.sha256(level_db.read_bytes()).hexdigest()
    result=lookup(level_db,constellation=c)
    assert result['status']=='ready',result['issues']
    assert result['level']['effective_level']==level
    assert result['records']['proud_skill']['record']['level']==level
    assert result['execution_support']=='static_level_only'
    assert len(result['selection']['active_constellation_ids'])==c
    assert hashlib.sha256(level_db.read_bytes()).hexdigest()==before


def test_normal_attack_target_is_general_not_c3_skill_assumption(level_db,fake_repo):
    mutate(level_db,fake_repo,'talentIndex',1)
    skill=lookup(level_db,constellation=3)
    normal=module.get_runtime_talent_level(level_db,character_id=90000001,slot='normal',constellation=3)
    assert skill['level']['effective_level']==10
    assert normal['level']['effective_level']==13


def test_extra_amount_is_read_not_fixed_three(level_db,fake_repo):
    mutate(level_db,fake_repo,'extraLevel',2)
    assert lookup(level_db,constellation=3)['level']['effective_level']==12


@pytest.mark.parametrize('key,value',[
    ('CLGIKOPIKBC',False),('talentType','ProudSkill'),('talentIndex',7),('extraLevel',True),
    ('extraLevel',0),('extraLevel',2.5),('extraLevel',6),('unknown',1),('$type',[]),
])
def test_unreviewed_or_missing_level_never_becomes_ready(level_db,fake_repo,key,value):
    mutate(level_db,fake_repo,key,value)
    result=lookup(level_db,constellation=3)
    assert result['status']=='pending' and result['level']['effective_level'] is None


def test_multi_sources_sum_only_current_group(level_db,fake_repo):
    path=fake_repo/'BinOutput/Talent/AvatarTalents/FixtureLevel.json';ops=json.loads(path.read_text('utf8'))
    ops['LevelC5'][0].update(talentIndex=2,extraLevel=2);path.write_text(json.dumps(ops),'utf8');fixture_builder.build_database(level_db.parent,force=True)
    result=lookup(level_db,constellation=5)
    assert result['status']=='ready' and result['level']['effective_level']==15
    assert len([r for r in result['contributions'] if r['matches_selected_group']])==2


def test_missing_or_duplicate_active_ids_are_pending(level_db,fake_repo):
    path=fake_repo/'ExcelBinOutput/AvatarSkillDepotExcelConfigData.json';rows=json.loads(path.read_text('utf8'))
    rows[0]['talents']=[9903,9903];path.write_text(json.dumps(rows),'utf8');fixture_builder.build_database(level_db.parent,force=True)
    result=lookup(level_db,constellation=3)
    assert {'duplicate_constellation_id','active_constellation_missing'}<={i['code'] for i in result['issues']}
    assert result['level']['effective_level'] is None


def test_public_query_requires_unambiguous_base_level(level_db):
    result=query_runtime(level_db,{'action':'talent_level_profile','character_id':90000001,'slot':'skill','constellation':3})
    assert result['level']['effective_level']==13
    with pytest.raises(ValueError):query_runtime(level_db,{'action':'talent_level_profile','character_id':90000001,'slot':'skill','talent_level':13})
    with pytest.raises(RuntimeConfigError,match='snapshot'):lookup(level_db,snapshot_id='wrong')


@pytest.mark.parametrize('value',[True,0,11,13,None])
def test_base_level_is_separate_and_bounded(level_db,value):
    with pytest.raises(ValueError):lookup(level_db,base_level=value)


def test_level_parameter_v2_uses_same_snapshot_and_preserves_legacy_limit(level_db):
    request={'action':'action_parameter_at_level','character_id':90000001,'slot':'skill','constellation':3,'param_index':0}
    result=query_runtime(level_db,request)
    assert result['status']=='ready' and result['scalar_projection']['value']==3.3
    assert result['level']['effective_level']==13
    assert result['operation_execution']=='not_evaluated' and result['action_dispatch']['status']=='pending'
    legacy=query_runtime(level_db,{**request,'action':'action_parameter','talent_level':13})
    assert legacy['status']=='pending' and legacy['scalar_projection']['value']==3.3
    assert any(r['code']=='automatic_level_unverified' for r in legacy['scalar_projection']['issues'])
    batch=query_runtime(level_db,{'action':'batch','items':[{'id':'new','request':request}]})
    assert batch['items'][0]['data']==result


def test_legacy_parameter_only_closes_level_operation_for_selected_skill(level_db,fake_repo):
    mutate(level_db,fake_repo,'talentIndex',1)
    unrelated=query_runtime(level_db,{'action':'action_parameter','character_id':90000001,'slot':'skill',
                                     'constellation':3,'talent_level':13,'param_index':0})
    assert unrelated['status']=='ready' and unrelated['scalar_projection']['value']==3.3
    assert not any(r['code']=='automatic_level_unverified' for r in unrelated['scalar_projection']['issues'])

    selected=query_runtime(level_db,{'action':'action_parameter','character_id':90000001,'slot':'normal',
                                    'constellation':3,'talent_level':13,'param_index':0})
    assert selected['status']=='pending' and selected['scalar_projection']['value']==2.3
    assert any(r['code']=='automatic_level_unverified' for r in selected['scalar_projection']['issues'])


def test_legacy_parameter_matches_level_target_by_group_for_explicit_subskill(level_db,fake_repo):
    path=fake_repo/'ExcelBinOutput/AvatarSkillDepotExcelConfigData.json';depots=json.loads(path.read_text('utf8'))
    depots[0]['subSkills']=[90014];path.write_text(json.dumps(depots),'utf8')
    path=fake_repo/'ExcelBinOutput/AvatarSkillExcelConfigData.json';skills=json.loads(path.read_text('utf8'))
    skills.append({**skills[1],'id':90014,'proudSkillGroupId':91002});path.write_text(json.dumps(skills),'utf8')
    fixture_builder.build_database(level_db.parent,force=True)

    result=query_runtime(level_db,{'action':'action_parameter','character_id':90000001,'skill_id':90014,
                                  'constellation':3,'talent_level':13,'param_index':0})
    assert result['selection']['matching_slots']==[]
    assert result['status']=='pending' and result['scalar_projection']['value']==3.3
    assert any(r['code']=='automatic_level_unverified' for r in result['scalar_projection']['issues'])


@pytest.mark.parametrize('failure', ['malformed_target','recipe_mismatch'])
def test_legacy_parameter_fails_closed_when_level_target_cannot_be_proved(level_db,fake_repo,monkeypatch,failure):
    if failure=='malformed_target':
        mutate(level_db,fake_repo,'talentIndex',7)
    else:
        profile=copy.deepcopy(module._recipe());profile['commit']='b'*40
        monkeypatch.setattr(module,'_recipe',lambda:profile)
    result=query_runtime(level_db,{'action':'action_parameter','character_id':90000001,'slot':'skill',
                                  'constellation':3,'talent_level':13,'param_index':0})
    assert result['status']=='pending' and result['scalar_projection']['value']==3.3
    assert any(r['code']=='automatic_level_unverified' for r in result['scalar_projection']['issues'])


def test_legacy_parameter_does_not_treat_unknown_nonlevel_operation_as_level_change(level_db,fake_repo):
    path=fake_repo/'BinOutput/Talent/AvatarTalents/FixtureLevel.json';rows=json.loads(path.read_text('utf8'))
    rows['LevelC3']=[{'$type':'UnreviewedControl','CLGIKOPIKBC':True}]
    path.write_text(json.dumps(rows),'utf8');fixture_builder.build_database(level_db.parent,force=True)
    result=query_runtime(level_db,{'action':'action_parameter','character_id':90000001,'slot':'skill',
                                  'constellation':3,'talent_level':13,'param_index':0})
    assert result['status']=='ready' and result['scalar_projection']['value']==3.3
    assert not any(r['code']=='automatic_level_unverified' for r in result['scalar_projection']['issues'])


def test_unverified_level_v2_never_adopts_candidate_numeric_table(level_db,fake_repo):
    mutate(level_db,fake_repo,'CLGIKOPIKBC',False)
    result=query_runtime(level_db,{'action':'action_parameter_at_level','character_id':90000001,'slot':'skill','constellation':3,'param_index':0})
    assert result['status']=='pending' and result['scalar_projection']['value'] is None and result['source'] is None
    assert result['level_profile']['records'].get('proud_skill') is not None


@pytest.mark.parametrize('operation',[
    {'$type':'ModifySkillPoint','skillID':90012,'pointDelta':1},
    {'$type':'ModifySkillCD','skillID':90012,'cdRatio':-.1},
])
def test_reviewed_nonlevel_numeric_operation_is_retained_without_execution(level_db,fake_repo,operation):
    path=fake_repo/'BinOutput/Talent/AvatarTalents/FixtureLevel.json';rows=json.loads(path.read_text('utf8'))
    rows['LevelC1']=[{**operation,'CLGIKOPIKBC':True}];path.write_text(json.dumps(rows),'utf8');fixture_builder.build_database(level_db.parent,force=True)
    result=lookup(level_db,constellation=3)
    assert result['status']=='ready' and result['level']['effective_level']==13
    assert result['other_operations'][0]['status']=='outside_static_level_scope'
    rows['LevelC1'][0]['skillID']=True;path.write_text(json.dumps(rows),'utf8');fixture_builder.build_database(level_db.parent,force=True)
    assert lookup(level_db,constellation=3)['status']=='pending'
