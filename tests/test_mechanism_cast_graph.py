"""Independent action identity/coverage without any damage or contact scaffold."""
import copy

import pytest

from genshin_app.equipment.files import fingerprint
from genshin_app.equipment.mechanism_cast_graph import build_mechanism_cast_graph
from genshin_app.equipment.mechanism_cast_identity import (
    query_cast_identity,
    verify_cast_identity,
)
from genshin_app.equipment.mechanism_cast_scope import (
    qualify_cast_provider,
    reconcile_equipment_coverage,
)
from genshin_app.equipment.mechanism_execution_scope import (
    qualify_activation,
    qualify_scene,
)
from genshin_db.runtime_action_resource import _resolve_action_identity
from genshin_db.runtime_queries import get_runtime_status
from tests.test_mechanics_execution import fixture
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_action_resource import resource_db  # noqa: F401

# ruff: noqa: F811

SNAPSHOT = {'version_line': 'release', 'snapshot_id': 'a'*64, 'upstream_commit': 'b'*40,
            'indexer_version': 6, 'normalization': {}}


def identity(query, snapshot):
    records = {'avatar': {'id': 71, 'skillDepotId': 81},
               'depot': {'id': 81, 'skills': [91, 92], 'energySkill': 93},
               'skill': {'id': query.get('skill_id', 93), 'cdTime': 15, 'maxChargeNum': 1,
                         'costElemVal': 10, 'costElemType': 'Ice'}}
    nodes = {}

    def exact(table, key, name, fields):
        raw = records[name]
        nodes[name] = {'source_path': f'ExcelBinOutput/{table}ExcelConfigData.json', 'json_pointer': '/0',
                       'source_sha256': fingerprint([raw]), 'node_sha256': fingerprint(raw), 'raw': raw}
        return raw

    selected = _resolve_action_identity(exact, **query)['selection']
    return {'schema_version': 1, 'snapshot': copy.deepcopy(snapshot), 'query': copy.deepcopy(query),
            'selection': selected, 'nodes': nodes}


def inputs(burst=False):
    template, rotation, model = fixture(())
    rotation['actions'][0]['repeat'] = {'mode': 'once'}
    rotation['abilities'][0]['variant'] = 'ordinary' if burst else 'hold'
    rotation['abilities'][0]['cooldown'] = {'duration': 15, 'charges': 1, 'group': 'e',
        'start': 'action_start', 'recovery': 'sequential'}
    rotation['initial_cooldowns'] = [{'character': 'a', 'group': 'e', 'ready_in': []}]
    slot = 'burst' if burst else 'skill'
    if burst:
        ab = rotation['abilities'][0]
        ab.update(action_type_id='action.elemental_burst', cooldown={'duration': 15, 'charges': 1, 'group': 'q',
                  'start': 'action_start', 'recovery': 'sequential'}, energy={'required': 10, 'amount': 10})
        rotation['events'] = [{'id': 'spend', 'type': 'energy_spend', 'action_id': 'cast', 'character': 'a',
                               'required': 10, 'amount': 10}]
    selection = {'slot': slot, 'constellation': 0}
    if not burst:
        selection.update(skill_id=92, talent_level=10, variant='hold')
    q = {'schema_version': 2, 'parent': {'profile_kind': 'successful_action_v1', 'selection': selection,
         'activation': {'offset_policy': 'calibrated'}}, 'coverage': {'action_type_ids': [rotation['abilities'][0]['action_type_id']]},
         'provider': {'relation': 'source_character'}, 'target_dependency': {'mode': 'none'}, 'scenario_requirements': {}}
    material = {'snapshot': SNAPSHOT, 'source_binding': {'subject': {'target_type': 'character', 'target_id': 71},
                'selection': {} if burst else {'character_id': 71, 'skill_id': 92, 'skill_depot_id': 81}},
                'compiler_result': {'execution_qualification': q}}
    plan = {'manual_template': template, 'rotation_model': rotation, 'mechanics_model': model,
            'effect_bindings': [], 'equipment_inputs': {'a': {'mode': 'panel', 'panel_policy': 'static_included',
             'character_ref': {'db_character_id': 71, 'version_line': 'release'}}}}
    binding = {'actor': 'a', 'version_line': 'release', 'cast_binding': {'ability_ids': ['e'],
               'selection': {'constellation': 0, **({} if burst else {'talent_level': 10})}},
               'scenario': {'target_id': None, 'values': {}, 'status': 'assumed', 'source': '工程范围'},
               'calibration': {'activation_offset_seconds': .05, 'precision_seconds': .001,
                               'status': 'assumed', 'source': '明确时点工程情景'}}
    return plan, template, binding, material


def graph(plan, template, binding, material, **kw):
    return build_mechanism_cast_graph(plan, template, binding=binding, material=material, identity_provider=identity, **kw)


@pytest.mark.parametrize('burst', [False, True])
def test_cast_requires_no_hits_profiles_applications_or_target(burst):
    plan, template, binding, material = inputs(burst)
    out = graph(plan, template, binding, material)
    assert out['status'] == 'ready' and len(out['parents']) == 1
    assert template['hits'] == [] and 'elemental_model' not in plan['mechanics_model']
    assert qualify_scene(plan, binding, material['compiler_result']['execution_qualification']) == binding['scenario']
    value, coverage = qualify_cast_provider(plan, template, binding, material, out, {}, [])
    assert value['source_subject']['target_id'] == 71 and value['provider_proof']['actor'] == 'a' and coverage == []


@pytest.mark.parametrize('fault', ['missing_constellation', 'wrong_skill', 'wrong_depot', 'wrong_variant', 'ghost', 'uncovered'])
def test_cast_identity_or_action_gap_is_pending(fault):
    plan, template, binding, material = inputs()
    extra = binding['cast_binding']['selection']
    if fault == 'missing_constellation':
        extra.pop('constellation')
    elif fault == 'wrong_skill':
        extra['skill_id'] = 93
    elif fault == 'wrong_depot':
        extra['skill_depot_id'] = 82
    elif fault == 'wrong_variant':
        plan['rotation_model']['abilities'][0]['variant'] = 'press'
    elif fault == 'ghost':
        plan['rotation_model']['actions'] = []
    else:
        ab = copy.deepcopy(plan['rotation_model']['abilities'][0]); ab['id'] = 'alias'
        action = copy.deepcopy(plan['rotation_model']['actions'][0]); action.update(id='other', ability_id='alias', start=2)
        plan['rotation_model']['abilities'].append(ab); plan['rotation_model']['actions'].append(action)
    with pytest.raises(ValueError):
        graph(plan, template, binding, material)


def test_cast_delay_is_bounded_without_an_initial_hit():
    plan, template, binding, material = inputs()
    out = graph(plan, template, binding, material)
    projection = {**material['compiler_result'], 'rule_templates': [{'rule': {'trigger': {'event': 'action_start'}}}]}
    assert qualify_activation(plan, binding, out, projection)['parents'][0]['activation_tick'] == 50000
    binding['calibration']['activation_offset_seconds'] = .2
    with pytest.raises(ValueError, match='占用'):
        qualify_activation(plan, binding, out, projection)


def test_saved_identity_replays_from_complete_nodes_and_rejects_changed_relationship():
    query = {'character_id': 71, 'slot': 'burst'}
    proof = identity(query, SNAPSHOT)
    assert verify_cast_identity(proof, query, SNAPSHOT)['skill_id'] == 93
    proof['nodes']['depot']['raw']['energySkill'] = 92
    proof['nodes']['depot']['node_sha256'] = fingerprint(proof['nodes']['depot']['raw'])
    with pytest.raises(ValueError):
        verify_cast_identity(proof, query, SNAPSHOT)


def test_fresh_and_offline_identity_share_the_existing_resolver(resource_db):
    snapshot = get_runtime_status(resource_db)
    query = {'character_id': 90000001, 'slot': 'burst'}
    proof = query_cast_identity(resource_db, query, snapshot)
    assert verify_cast_identity(proof, query, snapshot)['skill_id'] == 90013
    assert set(proof['nodes']) == {'avatar', 'depot', 'skill'}


def test_equipment_claim_only_removes_its_exact_issue():
    owner = {'kind': 'artifact_set', 'set_id': 5, 'affix_id': 21, 'required_count': 4}
    req = {'binding_id': 'four', 'provider_character_id': 'a', 'owner': owner, 'source_fingerprint': 'abc'}
    claim = {'binding_id': 'four', 'provider': 'a', 'owner': owner, 'requirement_fingerprint': 'abc', 'parts': ['atk']}
    records = [{'id': 'source', 'status': 'ready', 'equipment_coverage': [claim]}]
    issues = [{'code': 'effect_pending', 'binding_id': bid, 'path': 'effect_bindings.'+bid} for bid in ('four', 'two')]
    issues.append({'code': 'source_changed', 'binding_id': 'four', 'path': 'effect_bindings.four'})
    reconcile_equipment_coverage(records, [req], issues)
    assert [(r['code'], r['binding_id']) for r in issues] == [('effect_pending', 'two'), ('source_changed', 'four')]
