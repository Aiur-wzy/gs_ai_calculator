"""The dynamic ruleset cannot inherit a two-element source confirmation."""
import copy

import pytest

# ruff: noqa: F811
from genshin_app.equipment import compose_equipment_plan, run_plan_calc
from genshin_engine.elemental_schema import DYNAMIC_SWIRL_RULESET
from tests.test_reaction_sources_app import (  # noqa: F401
    confirmed,
    native_swirl,
    source_api,
)


@pytest.fixture
def three_sources(source_api):
    profiles, calls = source_api
    profiles['pyro'] = copy.deepcopy(profiles['cryo'])
    profiles['pyro'].update(selection={'element': 'pyro'}, content_fingerprint='pyro-fingerprint')
    return profiles, calls


def dynamic_plan():
    plan = native_swirl()
    model = plan['mechanics_model']['elemental_model']
    model['ruleset'] = DYNAMIC_SWIRL_RULESET
    return plan


def test_dynamic_confirmation_requires_all_three_sources(three_sources, tmp_path):
    plan = confirmed(dynamic_plan(), tmp_path / 'genshin.db')
    report = compose_equipment_plan(plan, tmp_path / 'genshin.db')
    record = report['reaction_damage_compilations'][0]
    assert record['compiler'] == 'database-swirl-damage-v2'
    assert record['status'] == 'ready'
    assert [p['selection']['element'] for p in record['profiles']] == ['hydro', 'cryo', 'pyro']
    assert [c['element'] for c in three_sources[1]] == ['hydro', 'cryo', 'pyro'] * 2
    result = run_plan_calc(plan, tmp_path / 'genshin.db')
    assert result['result']['summary']['total_damage'] == pytest.approx(1742.598)


def test_switching_ruleset_cannot_reuse_legacy_source_confirmation(three_sources, tmp_path):
    plan = confirmed(native_swirl(), tmp_path / 'genshin.db')
    old = copy.deepcopy(plan['reaction_damage_sources'][0])
    plan['mechanics_model']['elemental_model']['ruleset'] = DYNAMIC_SWIRL_RULESET
    report = compose_equipment_plan(plan, tmp_path / 'genshin.db')
    record = report['reaction_damage_compilations'][0]
    assert record['status'] == 'pending'
    assert record['required_confirmation']['expected_profile_fingerprint'] != old['expected_profile_fingerprint']
    assert any(i['code'] == 'source_changed' for i in report['missing_inputs'])


@pytest.mark.parametrize('field,value,code', [
    ('sequence', [1, 0], 'reaction_damage_group_conflict'),
    ('reset', 1, 'reaction_damage_group_conflict'),
    ('snapshot', 'other', 'reaction_source_snapshot_conflict'),
    ('element', 'cryo', 'reaction_source_element_conflict'),
    ('status', 'pending', 'unsupported_reaction_source'),
    ('fingerprint', 'changed', 'source_changed'),
])
def test_third_source_is_checked_equally(three_sources, tmp_path, field, value, code):
    plan = confirmed(dynamic_plan(), tmp_path / 'genshin.db')
    p = three_sources[0]['pyro']
    if field == 'sequence': p['projection']['damage_sequence'] = value
    elif field == 'reset': p['projection']['reset_cycle_seconds_rounded'] = value
    elif field == 'snapshot': p['snapshot']['snapshot_id'] = value
    elif field == 'element': p['selection']['element'] = value
    elif field == 'status': p['execution']['sequence_status'] = value
    else: p['content_fingerprint'] = value
    report = compose_equipment_plan(plan, tmp_path / 'genshin.db')
    assert report['reaction_damage_compilations'][0]['status'] == 'pending'
    assert any(i['code'] == code for i in report['missing_inputs'])
