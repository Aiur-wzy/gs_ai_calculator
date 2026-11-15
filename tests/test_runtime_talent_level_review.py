"""Independent static-level boundaries; fixture databases only are mutated."""
import json

import pytest

from genshin_db.runtime_catalog import RuntimeConfigError
from genshin_db.runtime_queries import query_runtime
from tests import fixture_candidate_builder as fixture_builder
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_talent_level import level_db, lookup  # noqa: F401

# ruff: noqa: F811


def replace_operation(db, repo, rank, operation):
    path = repo / 'BinOutput/Talent/AvatarTalents/FixtureLevel.json'
    rows = json.loads(path.read_text('utf8'))
    rows[f'LevelC{rank}'] = [operation]
    path.write_text(json.dumps(rows), 'utf8')
    fixture_builder.build_database(db.parent, force=True)


def test_inactive_unknown_operation_is_not_executed_or_used_for_levels(level_db, fake_repo):
    replace_operation(level_db, fake_repo, 4, {'$type': 'UnreviewedControl', 'CLGIKOPIKBC': True})
    assert lookup(level_db, constellation=3)['level']['effective_level'] == 13
    active = lookup(level_db, constellation=4)
    assert active['status'] == 'pending' and active['level']['effective_level'] is None
    scalar = query_runtime(level_db, {'action': 'action_parameter_at_level', 'character_id': 90000001,
                                    'slot': 'skill', 'constellation': 4, 'param_index': 0})
    assert scalar['source'] is None and scalar['scalar_projection']['value'] is None


def test_alias_collision_does_not_silently_select_a_level_target(level_db, fake_repo):
    replace_operation(level_db, fake_repo, 3, {'$type': 'AddTalentExtraLevel', 'talentType': 'AvatarSkill',
                      'talentIndex': 2, 'JOAIKFKCEJH': 9, 'extraLevel': 3, 'CLGIKOPIKBC': True})
    result = lookup(level_db, constellation=3)
    assert result['status'] == 'pending'
    assert 'talent_field_collision' in {row['code'] for row in result['issues']}


@pytest.mark.parametrize('extra', [False, .1])
def test_numeric_nonlevel_operation_is_not_a_generic_finite_payload(level_db, fake_repo, extra):
    # pointDelta has a specific integer shape, even though it is outside level execution.
    replace_operation(level_db, fake_repo, 1, {'$type': 'ModifySkillPoint', 'skillID': 90012,
                                           'pointDelta': extra, 'CLGIKOPIKBC': True})
    result = lookup(level_db, constellation=3)
    assert result['status'] == 'pending' and result['level']['effective_level'] is None


def test_nonfinite_nonlevel_source_is_rejected_before_query(level_db, fake_repo):
    with pytest.raises(RuntimeConfigError) as exc:
        replace_operation(level_db, fake_repo, 1, {'$type': 'ModifySkillCD', 'skillID': 90012,
                                               'cdRatio': float('inf'), 'CLGIKOPIKBC': True})
    assert exc.value.reason == 'dataset_invalid'


def test_nonlevel_cd_unrepresentable_integer_returns_pending_not_python_overflow(level_db, fake_repo):
    replace_operation(level_db, fake_repo, 1, {'$type': 'ModifySkillCD', 'skillID': 90012,
                                           'cdRatio': 10 ** 400, 'CLGIKOPIKBC': True})
    result = lookup(level_db, constellation=3)
    assert result['status'] == 'pending' and result['level']['effective_level'] is None


def test_parameter_record_level_must_be_exact_integer_not_numerically_equal_float(level_db, fake_repo):
    path = fake_repo / 'ExcelBinOutput/ProudSkillExcelConfigData.json'
    rows = json.loads(path.read_text('utf8'))
    for row in rows:
        if row['proudSkillGroupId'] == 91002 and row['level'] == 13:
            row['level'] = 13.0
    path.write_text(json.dumps(rows), 'utf8')
    fixture_builder.build_database(level_db.parent, force=True)
    result = lookup(level_db, constellation=3)
    assert result['status'] == 'pending' and result['level']['effective_level'] is None


def test_parameter_v2_returns_independent_result_and_source_bound_fingerprint(level_db):
    request = {'action': 'action_parameter_at_level', 'character_id': 90000001,
               'slot': 'skill', 'constellation': 3, 'param_index': 0}
    first = query_runtime(level_db, request)
    saved = first['content_fingerprint']
    first['level_profile']['records']['skill']['record']['proudSkillGroupId'] = 1
    second = query_runtime(level_db, request)
    assert second['content_fingerprint'] == saved
    assert second['scalar_projection']['value'] == 3.3
    assert second['snapshot'] == second['level_profile']['snapshot']
    assert second['action_dispatch']['status'] == 'pending'


@pytest.mark.parametrize('action,level', [('action_parameter_at_level', 13), ('action_parameter', 10)])
def test_unrepresentable_parameter_is_pending_on_both_scalar_entry_points(level_db, fake_repo, action, level):
    path = fake_repo / 'ExcelBinOutput/ProudSkillExcelConfigData.json'
    rows = json.loads(path.read_text('utf8'))
    for row in rows:
        if row['proudSkillGroupId'] == 91002 and row['level'] == level:
            row['paramList'][0] = 10 ** 400
    path.write_text(json.dumps(rows), 'utf8')
    fixture_builder.build_database(level_db.parent, force=True)
    request = {'action': action, 'character_id': 90000001, 'slot': 'skill', 'param_index': 0}
    request.update({'constellation': 3} if action == 'action_parameter_at_level' else {'talent_level': 10})
    result = query_runtime(level_db, request)
    assert result['status'] == 'pending'
    assert result['scalar_projection']['status'] == 'pending'
    assert result['scalar_projection']['value'] is None
