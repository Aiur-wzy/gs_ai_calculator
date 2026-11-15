"""Fresh skill identities qualify equipped effects across both dispatch shapes."""
import copy

import pytest

from genshin_app.equipment.mechanism_cast_identity import identity_provider
from genshin_app.equipment.mechanism_contact_graph import build_mechanism_contact_graph
from genshin_app.equipment.mechanism_contact_scope import qualify_contact_provider
from tests.test_mechanism_contact_graph import multi, single  # noqa: F401
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_attack_metadata import metadata_db  # noqa: F401
from tests.test_runtime_periodic_attack import periodic_db  # noqa: F401

# ruff: noqa: F811


def equipped(inputs):
    plan, template, args = copy.deepcopy(inputs)
    snapshot = args['source_snapshot']
    actor_id = args['parameter_records'][0]['resolved_selection']['character_id']
    binding = {'actor': 'a', 'version_line': 'release', 'calibration': None}
    plan['equipment_inputs']['a'] = {'character_ref': {'db_character_id': actor_id, 'version_line': 'release'},
        'artifact_sets': [{'set_id': 15025, 'count': 4}],
        'data_version': {'version_line': 'release', 'upstream_commit': snapshot['upstream_commit']}}
    qualification = {'schema_version': 3, 'parent': {
        'allowed_profile_kinds': ['calibrated_single_contact_v1', 'calibrated_multi_contact_v1'],
        'selection': {'constellation': 0, 'talent_level': 10},
        'hit': {'damage_systems': ['classic'], 'count': 1, 'scaling_stats': ['atk'],
                'elements': ['physical', 'cryo', 'hydro', 'anemo'],
                'damage_type_ids': ['damage.elemental_skill', 'damage.elemental_burst']},
        'callback_bridge': {'hit_filter': {'damage_type_ids': ['damage.elemental_skill', 'damage.elemental_burst']}}},
        'provider': {'relation': 'equipped_artifact_set', 'required_count': 4, 'affix_id': 2150251,
                     'source_level': 1, 'requirement_coverage': 'complete_selected_effect_reference'},
        'duplicate_identity': {'parts': ['four_piece']}}
    material = {'snapshot': snapshot, 'source_binding': {
        'subject': {'target_type': 'artifact_set', 'target_id': 15025}, 'selection': {}},
        'compiler_result': {'execution_qualification': qualification}}
    requirements = [{'provider_character_id': 'a', 'owner': {'kind': 'artifact_set', 'set_id': 15025,
        'required_count': 4, 'affix_id': 2150251}, 'effect': {'affix_id': 2150251, 'source_level': 1},
        'binding_id': 'set-four', 'source_fingerprint': 'd' * 64}]
    graph = build_mechanism_contact_graph(plan, template, **args)
    assert graph['status'] == 'ready', graph['issues']
    return plan, template, binding, material, graph, requirements


def qualify(inputs, db):
    plan, template, binding, material, graph, requirements = inputs
    return qualify_contact_provider(plan, template, binding, material, graph, {}, requirements, identity_provider(db))


def test_single_full_skill_nodes_and_equipped_provider_have_different_subjects(single, metadata_db):
    inputs = equipped(single)
    proof, coverage = qualify(inputs, metadata_db)
    assert proof['source_subject'] == {'target_type': 'artifact_set', 'target_id': 15025}
    assert proof['provider_proof']['character_ref']['db_character_id'] == 90000001
    assert len(proof['contact_identity']) == 1
    assert coverage[0]['binding_id'] == 'set-four'
    assert 'instance_fingerprint' not in proof['contact_identity'][0]


def test_multi_channels_share_actual_actor_and_retain_separate_parameter_origins(multi, periodic_db):
    inputs = equipped(multi)
    proof, coverage = qualify(inputs, periodic_db)
    assert len(proof['contact_identity']) == 2
    assert {r['channel_id'] for r in proof['contact_identity']} == {'slice', 'blast'}
    assert len(coverage) == 1


@pytest.mark.parametrize('fault', ['character', 'constellation', 'talent', 'origin', 'classification',
                                  'unsupported_hit', 'weapon_cast_binding', 'calibration', 'removed_set'])
def test_contact_source_cannot_promote_mismatched_identity_or_related_hit(single, metadata_db, fault):
    inputs = equipped(single)
    plan, _, binding, _, graph, _ = inputs
    dependency = graph['dependencies'][0]
    if fault == 'character':
        plan['equipment_inputs']['a']['character_ref']['db_character_id'] += 1
    elif fault == 'constellation':
        dependency['selection']['constellation'] = 1
    elif fault == 'talent':
        dependency['level']['effective_level'] = 9
    elif fault == 'origin':
        dependency['parameter']['entry_sources']['skill']['definition']['node_sha256'] = '0' * 64
    elif fault == 'classification':
        dependency['damage_classification']['source_proven'] = False
    elif fault == 'unsupported_hit':
        graph['parents'][0]['prototype']['element'] = 'electro'
    elif fault == 'weapon_cast_binding':
        binding['cast_binding'] = {'ability_ids': ['e'], 'selection': None}
    elif fault == 'calibration':
        binding['calibration'] = {'activation_offset_seconds': 0}
    else:
        plan['equipment_inputs']['a']['artifact_sets'] = []
    with pytest.raises(ValueError):
        qualify(inputs, metadata_db)
