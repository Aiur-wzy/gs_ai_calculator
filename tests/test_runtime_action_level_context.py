"""Source-context tests. No engine dispatch permission is asserted."""
import copy
import hashlib
import json

import pytest

from genshin_db import runtime_action_level as module
from genshin_db.runtime_action_facts import get_runtime_action_facts
from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_talent_level import level_db  # noqa: F401

# ruff: noqa: F811


def context(db, **kw):
    return module.get_level_action_context(db, character_id=90000001, slot='skill', **kw)


def read(db, **kw):
    return module.project_level_action_context(context(db, **kw))


def change(db, repo, apply):
    path = repo/'BinOutput/Talent/AvatarTalents/FixtureLevel.json'
    raw = json.loads(path.read_text('utf8'))
    apply(raw)
    path.write_text(json.dumps(raw), 'utf8')
    fixture_builder.build_database(db.parent, force=True)


def test_fresh_context_level_and_old_api_exact(level_db):
    before = hashlib.file_digest(level_db.open('rb'), 'sha256').hexdigest()
    old = query_runtime(level_db, {'action': 'action_parameter', 'character_id':90000001,
                                  'slot':'skill', 'param_index':0})
    result = read(level_db, constellation=6)
    assert result['static_level_status'] == result['source_assembly_status'] == 'ready'
    assert result['footprint_status'] == 'target_identified'
    assert result['level']['base_level'] == 10 and result['level']['effective_level'] == 13
    assert result['facts']['records']['proud_skill']['fields']['level'] == 13
    assert len(result['footprints']) == 6
    assert {r['target_domain'] for r in result['footprints']} == {'ability_creation','skill_group_level'}
    assert all(r['selected_dependency_status'] == 'not_evaluated' for r in result['footprints'])
    assert result['action_dispatch'] == 'pending' and result['source_authority'] == 'not_granted'
    assert result['whole_skill_coverage'] == 'partial'
    assert query_runtime(level_db, {'action':'action_parameter','character_id':90000001,
                                   'slot':'skill','param_index':0}) == old
    assert hashlib.file_digest(level_db.open('rb'), 'sha256').hexdigest() == before


def test_same_context_parameter_projection_and_public_mutation(level_db):
    ctx = context(level_db, constellation=3)
    projected = module.project_level_action_parameter(ctx, 0)
    assert projected['scalar_projection']['value'] == 3.3
    public = module.project_level_action_context(ctx)
    original = copy.deepcopy(public)
    public['level']['effective_level'] = 1
    public['footprints'].clear()
    assert module.project_level_action_context(ctx) == original
    assert module.project_level_action_parameter(ctx, 0) == projected
    with pytest.raises(ValueError):
        module.project_level_action_context(original)
    with pytest.raises(ValueError):
        module.project_level_action_parameter(original, 0)


@pytest.mark.parametrize('fields', [{'base_level':13},{'base_level':True},{'constellation':True},
                                  {'talent_level':13},{'effective_level':13}])
def test_no_effective_level_input_or_guess(level_db, fields):
    with pytest.raises((TypeError, ValueError)):
        read(level_db, **fields)


def test_other_group_level_is_retained_without_addition(level_db, fake_repo):
    change(level_db, fake_repo, lambda r: r['LevelC3'][0].update(talentIndex=1))
    result = read(level_db, constellation=3)
    assert result['level']['effective_level'] == 10
    target = result['footprints'][2]['target']
    assert target['skill_id'] == 90011 and target['group_id'] == 91001
    assert target['matches_selected_group'] is False and target['extra_level'] == 3


def test_add_and_unlock_only_all_discovered(level_db, fake_repo):
    change(level_db, fake_repo, lambda r: r['LevelC1'].append(
        {'$type':'UnlockTalentParam','CLGIKOPIKBC':True,'abilityName':'Fixture_Ability','talentParam':'ControlX'}))
    result = read(level_db, constellation=1)
    assert result['facts']['ability_candidates'] == []
    assert len(result['footprints']) == 2
    assert [r['target_domain'] for r in result['footprints']] == ['ability_creation','ability_control']
    assert all(r['status'] == 'target_identified' for r in result['footprints'])
    assert len(result['target_material']) == 1


@pytest.mark.parametrize('expression,located', [('%1',True),('-%2',True),('%0',False),
                                               ('%9',False),('0-%1',False)])
def test_parameter_locations_never_execute_or_deduplicate(level_db, fake_repo, expression, located):
    path = fake_repo/'ExcelBinOutput/AvatarTalentExcelConfigData.json'
    raw = json.loads(path.read_text('utf8')); raw[0]['paramList']=[2,5]
    path.write_text(json.dumps(raw), 'utf8')
    op = {'$type':'ModifyAbility','CLGIKOPIKBC':True,'abilityName':'Fixture_Ability',
          'paramSpecial':'Rate','paramDelta':expression}
    change(level_db, fake_repo, lambda r: r['LevelC1'].extend([op,copy.deepcopy(op)]))
    result = read(level_db, constellation=1)
    assert len(result['footprints']) == 3
    rows = result['footprints'][1:]
    assert rows[0]['source'] != rows[1]['source']
    for row in rows:
        assert row['parameter_location']['execution'] == 'not_evaluated'
        assert (row['parameter_location']['status'] == 'table_input_located') is located
        assert 'value' not in row['parameter_location']
    if expression == '-%2':
        assert result['facts']['status'] == result['source_assembly_status'] == 'pending'
        assert rows[0]['parameter_location']['raw_input'] == 5
        assert rows[0]['parameter_location']['sign'] == -1


@pytest.mark.parametrize('mutation', [{'CLGIKOPIKBC':False},{'unknown_condition':True},{'$type':'UnknownOp'},{'$type':[]}])
def test_unknown_operation_preserved_and_no_level_fallback(level_db, fake_repo, mutation):
    change(level_db, fake_repo, lambda r: r['LevelC1'][0].update(mutation))
    result = read(level_db, constellation=6)
    assert result['static_level_status'] == result['source_assembly_status'] == 'pending'
    assert result['facts'] is None
    assert result['level']['effective_level'] is None
    assert len(result['footprints']) == 6
    assert result['footprints'][0]['status'] == 'pending'
    assert result['footprints'][0]['target_domain'] == 'unknown'
    assert result['footprints'][5]['status'] == 'target_identified'


def test_missing_ability_does_not_hide_static_ready(level_db, fake_repo):
    change(level_db, fake_repo, lambda r: r['LevelC1'][0].update(abilityName='Absent'))
    result = read(level_db, constellation=1)
    assert result['static_level_status'] == 'ready'
    assert result['footprint_status'] == 'pending'
    assert result['footprints'][0]['target']['status'] == 'missing'
    assert result['source_assembly_status'] == 'pending'


def test_ambiguous_ability_keeps_every_target_and_pending(level_db, fake_repo):
    path = fake_repo/'BinOutput/Ability/Temp/EquipAbilities/Fixture.json'
    raw = json.loads(path.read_text('utf8'))
    raw[0]['Alternate'] = copy.deepcopy(raw[0]['Default'])
    path.write_text(json.dumps(raw), 'utf8')
    fixture_builder.build_database(level_db.parent, force=True)
    result = read(level_db, constellation=1)
    target = result['footprints'][0]['target']
    assert target['status'] == 'ambiguous' and len(target['candidate_ids']) == 2
    assert len(result['target_material']) == 2
    assert result['footprint_status'] == result['source_assembly_status'] == 'pending'


@pytest.mark.parametrize('kind,field,number', [('ModifySkillPoint','pointDelta',1),('ModifySkillCD','cdRatio',0.5)])
@pytest.mark.parametrize('skill_id', [90012,99999])
def test_action_constraint_targets_do_not_grant_action_availability(level_db, fake_repo,kind,field,number,skill_id):
    change(level_db,fake_repo,lambda r:r['LevelC1'].append(
        {'$type':kind,'CLGIKOPIKBC':True,'skillID':skill_id,field:number}))
    result = read(level_db,constellation=1)
    row = result['footprints'][1]
    assert row['status'] == ('target_identified' if skill_id == 90012 else 'pending')
    assert result['action_dispatch'] == 'pending'
    assert row['operation_execution'] == 'not_evaluated'


@pytest.mark.parametrize('field', ['snapshot','proud','params','selection'])
def test_inconsistent_source_sides_rejected(level_db, monkeypatch, field):
    from genshin_db import runtime_action_facts as facts_module
    def altered(*args, **kw):
        result = get_runtime_action_facts(*args, **kw)
        if field == 'snapshot': result['snapshot']['snapshot_id'] = 'forged'
        if field == 'proud': result['records']['proud_skill']['definition']['node_sha256'] = '0'*64
        if field == 'params': result['records']['proud_skill']['fields']['paramList'] = [999]
        if field == 'selection': result['selection']['active_constellation_ids'] = []
        return result
    monkeypatch.setattr(facts_module, 'get_runtime_action_facts', altered)
    with pytest.raises(RuntimeConfigError, match='不同|不一致|不是'):
        read(level_db, constellation=3)


def test_budget_before_copy_and_response_hash(level_db, monkeypatch):
    original = read(level_db, constellation=3)
    monkeypatch.setitem(module.LIMITS, 'max_bytes', 1000)
    with pytest.raises(RuntimeConfigError) as info:
        read(level_db, constellation=3)
    assert info.value.reason == 'response_too_large'
    assert original['action_dispatch'] == 'pending'


def test_fingerprint_and_metadata_are_in_response_budget(level_db, monkeypatch):
    public = read(level_db)
    cap = len(module.dumps(public).encode('utf8')) - 20
    checked_final = []
    original = module._bounded
    def measured(value):
        if isinstance(value,dict) and value.get('contract') == 'level-action-context-v1':
            checked_final.append('content_fingerprint' in value)
        return original(value)
    monkeypatch.setitem(module.LIMITS,'max_bytes',cap)
    monkeypatch.setattr(module,'_bounded',measured)
    with pytest.raises(RuntimeConfigError) as info:
        read(level_db)
    assert info.value.reason == 'response_too_large'
    assert checked_final == [False,True]


@pytest.mark.parametrize('value', [float('inf'),float('nan'),2**5000,{'bad':{1:2}}])
def test_pathological_material_is_bounded(value):
    with pytest.raises(RuntimeConfigError):
        module._bounded(value)
