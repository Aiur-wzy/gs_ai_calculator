"""Independent EC23 provider and offline queue checks; isolated sources only."""
import copy

import pytest

from genshin_app.equipment.mechanism_cast_scope import qualify_equipped_provider
from genshin_app.equipment.mechanism_contact_receipt import (
    _matches,
    verify_contact_execution_scope,
)
from genshin_app.equipment.mechanism_contact_scope import qualify_weapon
from genshin_db.runtime_queries import query_runtime
from genshin_engine.mechanics import execute_mechanics
from tests.mechanism_contact_source_fixtures import (  # noqa: F401
    IRON,
    approved_contact_db,
    contact_db,
    contact_repo,
    fake_repo,
)
from tests.test_freeze_runtime import inputs as elemental_inputs
from tests.test_mechanics_execution import add, fixture, rule

# ruff: noqa: F811


def weapon_case(db, rank=1):
    """Real approved fixture material, independent minimal curated-join inputs."""
    material = query_runtime(db, {'action': 'mechanism_profile', 'mechanism_id': IRON[rank - 1]})
    assert material['status'] == 'ready'
    provider = material['compiler_result']['execution_qualification']['provider']
    nodes = {row['id']: row['payload'] for row in material['source_proof']['nodes']}
    raw, affix = nodes[provider['weapon_node_id']], nodes[provider['affix_node_id']]
    index = raw['skillAffix'].index(affix['id'])
    selection = {'source_index': index, 'affix_group_id': affix['id'], 'refinement': rank}
    owner = {'kind': 'weapon', 'weapon_id': raw['id'], 'source_index': index, 'affix_id': provider['affix_id']}
    plan = {'equipment_inputs': {'carrier': {'character_ref': {'db_character_id': 90000001},
        'weapon': {'weapon_id': raw['id'], 'refinements': [selection]},
        'data_version': {'version_line': 'release', 'upstream_commit': material['snapshot']['upstream_commit']}}}}
    binding = {'actor': 'carrier', 'version_line': 'release'}
    evidence = {'carrier': {'weapon': {'weapon': {'id': raw['id'], 'weapon_type': raw['weaponType']},
        'effects': [{'source_index': index, 'affix_group_id': affix['id'], 'refinements': [{
            'affix_id': provider['affix_id'], 'source_level': rank - 1, 'refinement': rank}]}]}}}
    requirements = [{'provider_character_id': 'carrier', 'owner': owner,
        'effect': {'affix_id': provider['affix_id'], 'source_level': rank - 1},
        'binding_id': 'real-weapon-slot', 'source_fingerprint': 'a' * 64}]
    return plan, binding, material, evidence, requirements


@pytest.mark.parametrize('rank', [1, 5])
def test_review_real_weapon_source_does_not_substitute_for_actual_actor(approved_contact_db, rank):
    args = weapon_case(approved_contact_db, rank)
    before = copy.deepcopy(args)
    result = qualify_weapon(*args)
    assert result['provider'] == 'carrier'
    assert result['owner'] == {'kind': 'weapon', 'weapon_id': 11407, 'source_index': 0,
                               'affix_id': 1114070 + rank - 1}
    assert (result['refinement'], result['source_level']) == (rank, rank - 1)
    assert args == before
    result['parts'].clear()
    assert args == before


@pytest.mark.parametrize('change', ['native_rank', 'curated_rank', 'raw_slot', 'duplicate_slot',
                                  'duplicate_requirement', 'other_actor', 'other_line', 'weapon_type'])
def test_review_weapon_join_rejects_wrong_ownership_or_refinement(approved_contact_db, change):
    plan, binding, material, evidence, requirements = weapon_case(approved_contact_db)
    native = plan['equipment_inputs']['carrier']['weapon']
    slot = evidence['carrier']['weapon']['effects'][0]
    if change == 'native_rank':
        native['refinements'][0]['refinement'] = 2
    elif change == 'curated_rank':
        slot['refinements'][0]['source_level'] = 1
    elif change == 'raw_slot':
        slot['source_index'] = native['refinements'][0]['source_index'] = 1
        requirements[0]['owner']['source_index'] = 1
    elif change == 'duplicate_slot':
        evidence['carrier']['weapon']['effects'].append(copy.deepcopy(slot))
    elif change == 'duplicate_requirement':
        requirements.append(copy.deepcopy(requirements[0]))
    elif change == 'other_actor':
        requirements[0]['provider_character_id'] = 'other'
    elif change == 'other_line':
        plan['equipment_inputs']['carrier']['data_version']['version_line'] = 'beta'
    elif change == 'weapon_type':
        evidence['carrier']['weapon']['weapon']['weapon_type'] = 'WEAPON_CLAYMORE'
    with pytest.raises(ValueError):
        qualify_weapon(plan, binding, material, evidence, requirements)


def test_review_partial_weapon_coverage_retains_requirement(approved_contact_db):
    plan, binding, material, evidence, requirements = weapon_case(approved_contact_db)
    material['compiler_result']['execution_qualification']['provider']['requirement_coverage'] = 'partial'
    template = {'characters': [{'id': 'carrier', 'level': 90}]}
    proof, coverage = qualify_equipped_provider(plan, template, binding, material, evidence, requirements)
    assert proof['equipment']['binding_id'] == 'real-weapon-slot'
    assert coverage == []


def restriction(actor='a'):
    return [{'actor': actor, 'projection': {'execution_qualification': {
        'scenario_requirements': {'no_provider_transformative_damage': True}}}}]


def test_review_hidden_actual_swirl_is_not_hidden_by_report_or_hit_summary():
    template, rotation, model = elemental_inputs(('hydro', 'anemo'), (0, .2))
    model['hit_events'][1]['report'] = False
    out = execute_mechanics(template, rotation, model)
    assert out['result'] is not None
    assert [h['id'] for h in out['result']['hits']] == ['h0']
    assert any(e['type'] == 'reaction_damage_attempt' and e['allowed'] is True for e in out['elemental_trace'])
    with pytest.raises(ValueError, match='转化反应'):
        verify_contact_execution_scope(out, restriction())
    verify_contact_execution_scope(out, restriction('not-the-swirl-provider'))


@pytest.mark.parametrize('allowed', [True, None, 0])
def test_review_hidden_later_window_reaction_scope_requires_literal_blocked(allowed):
    # Scope reads the entire engine trace, never origin-0 reported damage totals.
    out = {'result': {'summary': {'total_expected': 0}}, 'elemental_trace': [
        {'type': 'reaction_damage_attempt', 'character': 'a', 'cycle': 2, 'origin': 1, 'allowed': allowed}]}
    with pytest.raises(ValueError):
        verify_contact_execution_scope(out, restriction())
    out['elemental_trace'][0]['allowed'] = False
    verify_contact_execution_scope(out, restriction())


def test_review_freeze_only_is_not_transformative_damage():
    out = execute_mechanics(*elemental_inputs(('hydro', 'cryo'), (0, .2)))
    assert out['result'] is not None
    assert any(r.get('type') == 'freeze' for e in out['elemental_trace'] for r in e.get('reactions', []))
    verify_contact_execution_scope(out, restriction())


def test_review_match_replace_expands_exact_pattern_ordinals_and_not_reaction_ids():
    _, _, model = fixture((.2,))
    add(model, rule())
    model['hit_patterns'] = [{'id': 'pulse', 'offsets': [0, .25, .5]}]
    match = {'effect_ref': {'buff_id': 'boost', 'effect_id': 'value'}, 'state': 'confirmed',
             'mode': 'replace', 'hit_ids': ['pulse:2'], 'include_hit_ids': [], 'exclude_hit_ids': []}
    plan = {'rotation_tuning': {'buff_hit_matches': [match]}}
    assert _matches(plan, model) == [
        {'rule_id': 'boost', 'effect_id': 'value', 'hit_id': 'pulse:2', 'operation': 'include'},
        *[{'rule_id': 'boost', 'effect_id': 'value', 'hit_id': h, 'operation': 'exclude'}
          for h in ['h0', 'pulse:1', 'pulse:3']]]
    match['hit_ids'] = ['pulse:2:reaction_damage:swirl:cryo']
    with pytest.raises(ValueError):
        _matches(plan, model)
