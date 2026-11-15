"""Technical confirmation cycles must not masquerade as changed game facts."""
import copy

import pytest

from genshin_app.equipment import compose_equipment_plan
from genshin_app.equipment.agent_execution_materialization import (
    lint_agent_execution_materialization,
    new_agent_execution_materialization,
    plan_binding_fingerprint,
)
from genshin_app.equipment.files import context_view, fingerprint, input_view
from tests.test_agent_execution_materialization import (
    _runtime_base,
    _runtime_document,
    _runtime_topology,
)
from tests.test_mechanics_execution import add, rule


def case():
    plan, _, _, _ = _runtime_base()
    plan['plan_id'] = '00000000-0000-4000-8000-000000000004'
    plan['direct_energy_policy'] = 'energy-direct-base-er-v1'
    add(plan['mechanics_model'], rule('manual', owner='actor', source_id='source.manual:ordinary',
        trigger={'event': 'action_start', 'phase': 'pre', 'action_ids': ['action.skill']}))
    plan['effect_bindings'] = [{'binding_id': 'source.manual', 'provider_character_id': 'actor',
        'owner': {'kind': 'manual', 'note': 'Anonymous reviewed source'},
        'source_refs': [{'kind': 'manual', 'note': 'Anonymous game facts'}], 'source_fingerprint': None,
        'coverage': {'state': 'complete', 'reviewed_by': 'engineering reviewer',
            'source_fingerprint': None, 'note': 'Complete explicit source audited'},
        'parts': [{'part_id': 'ordinary', 'label': 'Ordinary boost', 'state': 'resolved',
            'rule': {'kind': 'mechanics', 'rule_id': 'manual'}, 'delivery': {'kind': 'mechanics'},
            'conditions': [], 'dependencies': {'mode': 'manual_context', 'context_fingerprint': None,
                'note': 'Actual effect and source audited'}, 'reason': None}]}]
    topology = _runtime_topology()
    document = _runtime_document(plan, topology)
    plan['extensions']['agent_execution_materialization'] = document
    return plan, topology


def review(plan, report):
    for binding in plan['effect_bindings']:
        source = next(row for row in report['effect_requirements'] if row['binding_id'] == binding['binding_id'])
        binding['source_fingerprint'] = source['source_fingerprint']
        binding['coverage']['source_fingerprint'] = source['source_fingerprint']
        for part in binding['parts']:
            part['dependencies']['context_fingerprint'] = report['context_fingerprint']


def rebind(plan, topology):
    document = plan['extensions']['agent_execution_materialization']
    scaffold = new_agent_execution_materialization(plan, topology, document['materialization_id'])
    document['plan_binding'] = scaffold['plan_binding']


def ready(tmp_path):
    plan, topology = case()
    first = compose_equipment_plan(plan, tmp_path / 'absent.db')
    review(plan, first)
    rebind(plan, topology)
    second = compose_equipment_plan(plan, tmp_path / 'absent.db')
    assert not any(row['code'] == 'dependency_stale' for row in second['missing_inputs']), second['missing_inputs']
    assert second['agent_execution_materialization_receipt']['status'] == 'ready'
    assert second['state'] == 'ready', second['missing_inputs']
    return plan, topology, second


def test_public_compose_manual_review_and_same_semantics_rebind_close_without_ping_pong(tmp_path):
    plan, topology, report = ready(tmp_path)
    before = copy.deepcopy(plan)
    for _ in range(3):
        review(plan, report)
        rebind(plan, topology)
        report = compose_equipment_plan(plan, tmp_path / 'absent.db')
        assert report['state'] == 'ready', report['missing_inputs']
    assert plan == before


def test_technical_binding_change_does_not_change_source_context_but_full_input_still_binds_it():
    plan, _ = case()
    before = fingerprint(context_view(input_view(plan)))
    raw = fingerprint(input_view(plan))
    plan['extensions']['agent_execution_materialization']['plan_binding']['input_fingerprint'] = 'f' * 64
    assert fingerprint(context_view(input_view(plan))) == before
    assert fingerprint(input_view(plan)) != raw


@pytest.mark.parametrize('mutation', ['number', 'timing', 'source_reference', 'withdrawn', 'covered_part', 'condition', 'mode'])
def test_game_fact_or_authority_change_is_still_stale_and_cannot_borrow_old_confirmation(tmp_path, mutation):
    plan, topology, _ = ready(tmp_path)
    baseline = plan_binding_fingerprint(plan)
    binding = plan['effect_bindings'][0]
    if mutation == 'number':
        plan['mechanics_model']['rules'][0]['effects'][0]['value'] += .1
    elif mutation == 'timing':
        plan['rotation_model']['actions'][0]['occupation_end'] += .1
    elif mutation == 'source_reference':
        binding['source_refs'][0]['note'] += ' changed'
    elif mutation == 'withdrawn':
        binding['coverage']['state'] = 'unreviewed'
    elif mutation == 'covered_part':
        binding['parts'][0]['state'] = 'pending'
        binding['parts'][0]['reason'] = 'Withdrawn source part'
    elif mutation == 'condition':
        binding['parts'][0]['conditions'] = [{'condition_id': 'condition', 'scope': 'always',
            'state': 'pending', 'description': 'Not confirmed', 'intervals': [],
            'evidence': [{'kind': 'manual', 'note': 'Anonymous condition awaiting confirmation'}]}]
    elif mutation == 'mode':
        binding['parts'][0]['dependencies']['mode'] = 'source_only'
    assert plan_binding_fingerprint(plan) != baseline
    receipt = lint_agent_execution_materialization(plan, topology, plan['extensions']['agent_execution_materialization'])
    assert any(row['code'] == 'agent_materialization_plan_fingerprint_stale' for row in receipt['issues'])
    report = compose_equipment_plan(plan, tmp_path / 'absent.db')
    assert report['state'] == 'pending'


@pytest.mark.parametrize('field', ['context_fingerprint', 'source_fingerprint', 'basis_fingerprint', 'input_fingerprint'])
def test_same_named_hash_outside_known_confirmation_role_remains_materialization_fact(field):
    plan, _ = case()
    plan['extensions']['other_source'] = {field: 'a' * 64}
    before = plan_binding_fingerprint(plan)
    plan['extensions']['other_source'][field] = 'b' * 64
    assert plan_binding_fingerprint(plan) != before


def test_materialization_payload_and_source_fingerprint_remain_source_context_facts():
    plan, _ = case()
    before = fingerprint(context_view(input_view(plan)))
    document = plan['extensions']['agent_execution_materialization']
    document['sources'][0]['fingerprint'] = 'd' * 64
    assert fingerprint(context_view(input_view(plan))) != before
    prior = fingerprint(context_view(input_view(plan)))
    document['entries'][0]['payload']['steps'][0]['offset_seconds'] += .01
    assert fingerprint(context_view(input_view(plan))) != prior


@pytest.mark.parametrize('mutation', ['missing_source_hash', 'wrong_source_hash', 'missing_reviewer',
    'changed_source_reference', 'changed_condition', 'changed_coverage'])
def test_rebinding_is_not_source_approval_and_never_launders_a_withdrawal(tmp_path, mutation):
    plan, topology, _ = ready(tmp_path)
    binding = plan['effect_bindings'][0]
    if mutation == 'missing_source_hash':
        binding['source_fingerprint'] = None
    elif mutation == 'wrong_source_hash':
        binding['coverage']['source_fingerprint'] = 'f' * 64
    elif mutation == 'missing_reviewer':
        binding['coverage']['reviewed_by'] = None
    elif mutation == 'changed_source_reference':
        binding['source_refs'][0]['note'] = 'New, not yet reviewed source'
    elif mutation == 'changed_condition':
        binding['parts'][0]['conditions'] = [{'condition_id': 'condition', 'scope': 'always',
            'state': 'pending', 'description': 'Source now uncertain', 'intervals': [],
            'evidence': [{'kind': 'manual', 'note': 'Review withdrawn'}]}]
    elif mutation == 'changed_coverage':
        binding['coverage']['state'] = 'unreviewed'
    rebind(plan, topology)
    report = compose_equipment_plan(plan, tmp_path / 'absent.db')
    assert report['agent_execution_materialization_receipt']['status'] == 'ready'
    assert report['state'] == 'pending'
    assert any(row['code'] == 'effect_pending' for row in report['missing_inputs'])


@pytest.mark.parametrize('field', ['context_fingerprint', 'source_fingerprint', 'basis_fingerprint',
    'input_fingerprint', 'coverage', 'state'])
def test_materialization_payload_names_are_not_technical_roles(field):
    plan, _ = case()
    payload = plan['extensions']['agent_execution_materialization']['entries'][0]['payload']
    payload[field] = 'fact one'
    before = fingerprint(context_view(input_view(plan)))
    payload[field] = 'fact two'
    assert fingerprint(context_view(input_view(plan))) != before


def test_only_known_materialization_document_role_gets_technical_projection():
    plan, _ = case()
    document = plan['extensions']['agent_execution_materialization']
    plan['extensions']['ordinary_fact'] = {'plan_binding': copy.deepcopy(document['plan_binding'])}
    before = fingerprint(context_view(input_view(plan)))
    plan['extensions']['ordinary_fact']['plan_binding']['input_fingerprint'] = 'f' * 64
    assert fingerprint(context_view(input_view(plan))) != before


def test_known_embedded_board_document_projects_only_its_own_binding():
    plan, _ = case()
    document = copy.deepcopy(plan['extensions']['agent_execution_materialization'])
    plan['extensions']['whiteboard_handoff'] = {'board': {'extensions': {
        'agent_execution_materialization': document}}}
    before = fingerprint(context_view(input_view(plan)))
    document['plan_binding']['input_fingerprint'] = 'f' * 64
    assert fingerprint(context_view(input_view(plan))) == before
    document['sources'][0]['fingerprint'] = 'e' * 64
    assert fingerprint(context_view(input_view(plan))) != before


def test_original_input_and_execution_views_still_bind_all_confirmation_bytes():
    from genshin_app.equipment.files import execution_input_view
    plan, topology = case()
    before = copy.deepcopy(plan)
    baseline = plan_binding_fingerprint(plan)
    review(plan, {'effect_requirements': [{'binding_id': 'source.manual',
        'source_fingerprint': 'd' * 64}], 'context_fingerprint': 'c' * 64})
    assert plan_binding_fingerprint(plan) == baseline
    assert fingerprint(input_view(plan)) != fingerprint(input_view(before))
    assert fingerprint(execution_input_view(plan)) != fingerprint(execution_input_view(before))
    rebind(plan, topology)


@pytest.mark.parametrize('field', ['context_fingerprint', 'source_fingerprint', 'basis_fingerprint'])
def test_same_hash_name_in_actual_effect_source_ref_still_stales_materialization(field):
    plan, _ = case()
    source = plan['effect_bindings'][0]['source_refs'][0]
    source[field] = 'a' * 64
    before = plan_binding_fingerprint(plan)
    source[field] = 'b' * 64
    assert plan_binding_fingerprint(plan) != before


def test_all_known_review_hashes_are_independent_but_their_modes_and_notes_remain_bound():
    plan, _ = case()
    plan['manual_effect_dependencies'] = [{'effect_ref': {'buff_id': 'manual', 'effect_id': 'value'},
        'mode': 'manual_context', 'context_fingerprint': 'a' * 64, 'note': 'Reviewed manual fact'}]
    plan['attribute_review'] = {'mode': 'manual_context', 'context_fingerprint': 'a' * 64,
        'note': 'Reviewed attribute facts'}
    plan['rotation_tuning'] = {'buff_hit_matches': [{'basis_fingerprint': 'a' * 64,
        'effect_ref': {'buff_id': 'manual', 'effect_id': 'value'}, 'mode': 'replace',
        'hit_ids': ['hit.fixture'], 'reason': 'Reviewed actual match'}]}
    before = plan_binding_fingerprint(plan)
    rows = [plan['manual_effect_dependencies'][0], plan['attribute_review'],
            plan['rotation_tuning']['buff_hit_matches'][0]]
    for row in rows:
        key = 'basis_fingerprint' if 'basis_fingerprint' in row else 'context_fingerprint'
        row[key] = 'b' * 64
    assert plan_binding_fingerprint(plan) == before
    for index, row in enumerate(rows):
        changed = copy.deepcopy(plan)
        target = [changed['manual_effect_dependencies'][0], changed['attribute_review'],
                  changed['rotation_tuning']['buff_hit_matches'][0]][index]
        target['mode'] = 'unreviewed'
        assert plan_binding_fingerprint(changed) != before
        changed = copy.deepcopy(plan)
        target = [changed['manual_effect_dependencies'][0], changed['attribute_review'],
                  changed['rotation_tuning']['buff_hit_matches'][0]][index]
        target['note' if index < 2 else 'reason'] += ' factual change'
        assert plan_binding_fingerprint(changed) != before
