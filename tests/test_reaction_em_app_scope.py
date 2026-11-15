"""Final-source qualification uses the actual generated model and stable semantics."""
import copy

import pytest

from genshin_app.equipment.reaction_em_scope import (
    final_scope,
    scope_fingerprint,
    source_scope,
)
from tests.test_runtime_reaction_em_source import prepared_queue


def fixture_scope():
    template, rotation, model = prepared_queue((.2, .3))
    owner = {'kind': 'artifact_set', 'set_id': 10007, 'required_count': 4, 'affix_id': 2100071}
    binding = {'binding_id': 'test', 'provider_character_id': 'a', 'owner': owner,
               'source_refs': [], 'upstream_recipe': {'scenario': {'target_id': 'enemy',
               'accepted_assumptions_fingerprint': 'source-ack'}}}
    plan = {'equipment_inputs': {actor: {'mode': 'panel', 'panel_policy': 'static_included',
        'artifact_sets': [{'set_id': 10007, 'count': 4}] if actor == 'a' else []}
        for actor in ('a', 'b')}, 'effect_bindings': [binding], 'manual_template': template,
        'rotation_model': rotation, 'mechanics_model': model}
    compiled = {'rules': copy.deepcopy(model['rules']), 'attribute_writes': [
        {'effect_ref': {'kind': 'rule', 'rule_id': model['rules'][0]['id'], 'effect_id': 'value'},
         'provider': 'a', 'stat': 'em', 'nonextra_inclusion': 'included'}]}
    return plan, template, binding, {'owner': owner}, compiled


def test_current_equipment_and_final_source_model_are_checked_separately():
    p, t, b, req, compiled = fixture_scope()
    assert source_scope(p, b, req)[0] == []
    issues, proof = final_scope(p, t, b, compiled)
    assert issues == []
    assert proof['provider_authority'] == 'actual_elemental_reaction_occurrence_checked_after_execution'
    assert proof['order_authority'] == 'no_cross_listener_order'


@pytest.mark.parametrize('change', ['two_piece', 'other_owner', 'duplicate_gear', 'repeat_source', 'q_reference', 'build_mode'])
def test_equipment_and_typed_duplicate_boundaries(change):
    p, _, b, req, _ = fixture_scope()
    if change == 'two_piece': p['equipment_inputs']['a']['artifact_sets'][0]['count'] = 2
    elif change == 'other_owner': b['owner'] = {**b['owner'], 'set_id': 999}
    elif change == 'duplicate_gear': p['equipment_inputs']['b']['artifact_sets'] = [{'set_id': 10007, 'count': 4}]
    elif change == 'repeat_source': p['effect_bindings'].append({**b, 'binding_id': 'other'})
    elif change == 'q_reference': b['upstream_recipe']['ability_refs'] = [{'ability_id': 'q', 'skill_id': 1}]
    else: p['equipment_inputs']['b']['panel_policy'] = 'not_included'
    assert source_scope(p, b, req)[0]


@pytest.mark.parametrize('change', ['v1', 'freeze', 'attribute_v3', 'wrong_target', 'no_application',
                                  'application_only', 'snapshot', 'self_snapshot', 'manual_reaction', 'initial_instance'])
def test_final_scope_cannot_be_upgraded_by_source_confirmation(change):
    p, t, b, _, compiled = fixture_scope(); m = p['mechanics_model']
    if change in ('v1', 'freeze'): m['elemental_model']['ruleset'] = 'gauge-basic-v1' if change == 'v1' else 'gauge-freeze-reference-v1'
    elif change == 'attribute_v3': m['attribute_model']['contract'] = 'attribute-ledger-v3'
    elif change == 'wrong_target': b['upstream_recipe']['scenario']['target_id'] = 'other'
    elif change == 'no_application': m['hit_events'][0].pop('application')
    elif change == 'application_only': m['application_events'] = [{'id': 'extra'}]
    elif change == 'snapshot': t['hits'][0]['snapshot'] = True
    elif change == 'self_snapshot': m['hit_events'][0]['self_snapshot_event'] = 'old'
    elif change == 'manual_reaction': t['hits'][0]['reaction'] = 'melt'
    else: m['initial_state']['rules'][0]['instances'] = [{'engineered': True}]
    b['upstream_recipe']['scenario']['accepted_assumptions_fingerprint'] = 'fresh-but-not-authority'
    assert final_scope(p, t, b, compiled)[0]


def test_later_assembled_em_reader_is_included_in_scope():
    p, t, b, _, compiled = fixture_scope()
    assert not final_scope(p, t, b, compiled)[0]
    r = copy.deepcopy(p['mechanics_model']['rules'][0]); r.update(id='later-reader', owner='b')
    r['effects'] = [{'id': 'gain', 'benefit_type_id': 'benefit.dmg_bonus.all', 'domain': 'provider',
                    'value': {'kind': 'stat', 'stat': 'em', 'subject': 'owner', 'sample': 'trigger', 'factor': .01}}]
    p['mechanics_model']['rules'].append(r)
    issues, proof = final_scope(p, t, b, compiled)
    assert any('later-reader.gain' in i['path'] for i in issues)
    assert proof['readers'][0]['value']['sample'] == 'trigger'
    r['effects'][0]['value']['basis'] = 'base'
    assert not final_scope(p, t, b, compiled)[0]  # Immutable BASE is independent of this dynamic gain.


def test_scope_fingerprint_excludes_generated_acknowledgements_but_not_values():
    p, t, b, _, compiled = fixture_scope()
    _, proof = final_scope(p, t, b, compiled); baseline = scope_fingerprint(proof)
    p['mechanics_model']['attribute_model']['effect_classifications'][0]['component']['source_fingerprint'] = 'derived-later'
    b['upstream_recipe']['scenario']['accepted_assumptions_fingerprint'] = 'later-ack'
    assert scope_fingerprint(final_scope(p, t, b, compiled)[1]) == baseline
    p['mechanics_model']['rules'][0]['effects'][0]['value'] += 1
    assert scope_fingerprint(final_scope(p, t, b, compiled)[1]) != baseline
