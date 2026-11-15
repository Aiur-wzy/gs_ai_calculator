"""Independent full-callback coverage checks from fresh dispatch receipts."""
import copy

from genshin_app.equipment.mechanism_contact_graph import build_mechanism_contact_graph
from tests.test_action_events import confirm_action_events
from tests.test_attack_metadata_app import metadata_native
from tests.test_mechanism_contact_graph import compiled
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_attack_metadata import metadata_db  # noqa: F401

# ruff: noqa: F811


def test_review_known_raw_skill_cannot_escape_callback_coverage_by_declared_normal(metadata_db):
    plan = metadata_native(metadata_db)
    profile = copy.deepcopy(plan['action_event_profiles'][0])
    profile.update(id='misclassified', prototype_hit_id='misclassified.proto',
                   application_profile_id='misclassified.app')
    hit = copy.deepcopy(plan['manual_template']['hits'][0])
    hit.update(id=profile['prototype_hit_id'], damage_type_ids=['damage.normal_attack'])
    source = copy.deepcopy(plan['attack_metadata_sources'][0])
    source['action_event_profile_id'] = profile['id']
    ability = copy.deepcopy(plan['rotation_model']['abilities'][0])
    ability['id'] = 'misclassified.e'
    plan['action_event_profiles'].append(profile)
    plan['manual_template']['hits'].append(hit)
    plan['attack_metadata_sources'].append(source)
    plan['rotation_model']['abilities'].append(ability)
    plan['rotation_model']['actions'][1]['ability_id'] = ability['id']
    plan['action_event_bindings'].append({'ability_id': ability['id'], 'profile_id': profile['id']})
    confirm_action_events(plan, metadata_db)
    work, template, args = compiled(plan, metadata_db)
    assert len(args['metadata_records']) == 2
    # Both records come from the same exact source candidate. Only the human
    # damage category differs; another valid E must not hide this known E hit.
    assert len({r['candidate_id'] for r in args['metadata_records']}) == 1
    args['scope']['damage_type_ids'] = ['damage.elemental_skill', 'damage.elemental_burst']
    result = build_mechanism_contact_graph(work, template, **args)
    assert result['status'] == 'pending', result
    assert result['parents'] == result['dependencies'] == []
    assert any('类别' in r['message'] for r in result['issues']), result['issues']
