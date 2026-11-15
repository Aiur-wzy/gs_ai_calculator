"""Internal admission gate regressions using an actual fresh database batch."""
import copy

import pytest

from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment import static_inclusion_sources as gate
from genshin_app.equipment.schema import PlanError
from tests.test_static_inclusion_app import (  # noqa: F401
    DB,
    snapshot_session,
    static_plan,
    version,
)


@pytest.fixture(scope='module')
def final_context(version):  # noqa: F811
    captured = []
    original = gate.finalize_static_inclusions
    def capture(*args, **kwargs):
        captured.append((copy.deepcopy(args), copy.deepcopy(kwargs)))
        return original(*args, **kwargs)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(gate, 'finalize_static_inclusions', capture)
        compose_equipment_plan(static_plan(version), DB)
    assert len(captured) == 1
    return captured[0]


def finish(context):
    args, kwargs = context
    return gate.finalize_static_inclusions(*args, **kwargs)


def test_finalization_is_read_only_and_adoption_changes_only_exact_own_issue(final_context):
    args, kwargs = copy.deepcopy(final_context)
    unrelated = [{'code': c, 'path': 'effect_bindings.other', 'binding_id': 'other', 'part_id': None}
                 for c in ['source_changed', 'dependency_stale', 'effect_pending']]
    args[5].extend(unrelated)
    before = copy.deepcopy((args, kwargs))
    result = finish((args, kwargs))
    assert args[0]._seal == before[0][0]._seal
    assert args[1:] == before[0][1:] and kwargs == before[1]
    assert result['records'][0]['status'] == 'ready'
    gate.apply_static_inclusion_adoptions(result, args[4], args[5])
    assert args[5] == unrelated
    assert args[4][0]['resolved_by'] == result['adoptions'][0]['resolved_by']


@pytest.mark.parametrize('alter', ['duplicate_issue', 'changed_issue', 'changed_requirement', 'duplicate_requirement'])
def test_changed_final_target_never_removes_issue(final_context, alter):
    args, kwargs = copy.deepcopy(final_context)
    if alter == 'duplicate_issue': args[5].append(copy.deepcopy(args[5][0]))
    if alter == 'changed_issue': args[5][0]['message'] = 'changed'
    if alter == 'changed_requirement': args[4][0]['effect']['name'] = 'changed'
    if alter == 'duplicate_requirement': args[4].append(copy.deepcopy(args[4][0]))
    out = finish((args, kwargs))
    assert not out['adoptions'] and not out['remove_issue_fingerprints']
    assert out['records'][0]['status'] == 'pending'


@pytest.mark.parametrize('route', ['binding_pending', 'binding_active', 'buff', 'rule', 'fixed',
    'panel', 'automatic_ready', 'automatic_pending', 'automatic_coverage', 'linked_rule'])
def test_exact_source_occupied_by_any_delivery_path_is_pending(final_context, route):
    args, kwargs = copy.deepcopy(final_context)
    req = args[4][0]; owner = req['owner']; actor = req['provider_character_id']
    ref = {'kind': 'equip_affix', 'affix_id': owner['affix_id']}
    binding = {'binding_id': 'alias', 'provider_character_id': actor, 'owner': copy.deepcopy(owner),
               'parts': [{'part_id': 'live', 'state': 'resolved' if route != 'binding_pending' else 'pending'}]}
    if route.startswith('binding_') or route == 'linked_rule':
        args[1]['effect_bindings'].append(binding)
        if route == 'binding_active': kwargs['applicable'] = [(binding, binding['parts'][0])]
    if route == 'buff': args[2]['buffs'] = [{'id': 'manual', 'extensions': {'owner': actor, 'source_refs': [ref]}}]
    if route in ['rule', 'fixed', 'linked_rule']:
        args = list(args)
        args[3] = {'rules' if route != 'fixed' else 'fixed_effect_sources': [
            {'id': 'duplicate', 'owner': actor, 'source_id': 'alias:live' if route == 'linked_rule' else 'other',
             'extensions': {} if route == 'linked_rule' else {'source_refs': [ref]}}]}
    if route == 'panel': kwargs['contribution_report'] = {'characters': {actor: {'contributions': [
        {'contribution_id': 'dup', 'source_refs': [ref], 'provider_character_id': actor}]}}}
    if route.startswith('automatic_'):
        claim = {'provider': actor, 'owner': owner}
        automatic = {'id': 'auto', 'status': 'pending' if route == 'automatic_pending' else 'ready'}
        if route == 'automatic_coverage': automatic['equipment_coverage'] = [claim]
        else: automatic['scope'] = {'identity': {'provider_proof': {'equipment': claim}}}
        kwargs['automatic_compilations'] = [automatic]
    out = finish((args, kwargs))
    assert out['records'][0]['status'] == 'pending' and not out['adoptions']
    assert any(i['code'] == 'static_inclusion_duplicate' for i in out['records'][0]['issues'])


@pytest.mark.parametrize('kind', ['other_provider', 'other_affix', 'excluded', 'prose_prefix'])
def test_occupancy_does_not_merge_distinct_or_non_executing_identity(final_context, kind):
    args, kwargs = copy.deepcopy(final_context)
    req = args[4][0]; owner = copy.deepcopy(req['owner'])
    if kind == 'other_affix': owner['affix_id'] += 1
    if kind == 'prose_prefix':
        args[2]['buffs'] = [{'id': 'free', 'extensions': {'source_id': str(owner['affix_id']) + ':source'}}]
    else:
        args[1]['effect_bindings'].append({'binding_id': 'other', 'provider_character_id': 'other' if kind == 'other_provider' else req['provider_character_id'],
            'owner': owner, 'parts': [{'part_id': 'part', 'state': 'excluded' if kind == 'excluded' else 'pending'}]})
    out = finish((args, kwargs))
    assert out['records'][0]['status'] == 'ready'
    if kind == 'prose_prefix': assert out['records'][0]['source_occupancy']['unresolved_sources']


def test_changed_private_batch_rejected_and_returned_component_cannot_change_batch(final_context):
    args, kwargs = copy.deepcopy(final_context)
    component = args[0].source_components
    component[0]['material_fingerprint'] = '0' * 64
    assert finish((args, kwargs))['records'][0]['status'] == 'ready'
    args[0]._entries[0]['declaration']['properties'][0]['value'] = 99
    with pytest.raises(PlanError): finish((args, kwargs))


@pytest.mark.parametrize('location', ['native_context', 'native_panel', 'native_equipment', 'actual_panel', 'actual_level'])
def test_final_given_panel_and_context_must_still_match_fresh_candidate(final_context, location):
    args, kwargs = copy.deepcopy(final_context)
    if location == 'native_context':
        kwargs['context_plan']['manual_template']['hits'][0]['multiplier'] += 1
        with pytest.raises(PlanError): finish((args, kwargs))
        return
    if location == 'native_panel': args[1]['manual_template']['characters'][0]['stats']['em'] += 1
    if location == 'native_equipment': args[1]['equipment_inputs']['char1']['artifact_sets'][0]['count'] = 4
    if location == 'actual_panel': args[2]['characters'][0]['stats']['em'] += 1
    if location == 'actual_level': args[2]['characters'][0]['level'] += 1
    out = finish((args, kwargs))
    assert out['records'][0]['status'] == 'pending' and not out['adoptions']


def test_pending_mechanism_before_scope_still_claims_typed_equipment_source(final_context):
    args, kwargs = copy.deepcopy(final_context)
    req = args[4][0]
    kwargs['automatic_compilations'] = [{'id': 'mechanism', 'actor': req['provider_character_id'], 'status': 'pending',
        'source_material': {'source_binding': {'subject': {'target_type': 'artifact_set', 'target_id': req['owner']['set_id']}},
            'compiler_result': {'execution_qualification': {'provider': {'relation': 'equipped_artifact_set',
                'affix_id': req['owner']['affix_id']}}}}}]
    out = finish((args, kwargs))
    assert out['records'][0]['status'] == 'pending' and not out['adoptions']


def test_final_output_budget_is_checked_before_publish(final_context, monkeypatch):
    monkeypatch.setattr(gate, 'MAX_RECORD_BYTES', 1)
    args, kwargs = copy.deepcopy(final_context); before = copy.deepcopy(args[4:6])
    with pytest.raises(PlanError, match='预算'): finish((args, kwargs))
    assert args[4:6] == before


def test_material_budget_failed_read_is_not_reset_per_candidate(version, monkeypatch):  # noqa: F811
    plan = static_plan(version, set_id=10001)
    plan['equipment_inputs']['char1']['artifact_sets'].append({'set_id': 15001, 'count': 2})
    actual = gate._read_material; reads = []
    def read(*args):
        reads.append(True)
        return actual(*args)
    monkeypatch.setattr(gate, '_read_material', read)
    monkeypatch.setattr(gate, 'MAX_MATERIAL_BYTES', 1)
    with pytest.raises(PlanError, match='预算'):
        compose_equipment_plan(plan, DB)
    assert len(reads) == 1
