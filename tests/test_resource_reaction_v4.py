"""Real v4 reaction callbacks and their resource deliveries."""

import copy
import json
import uuid

import pytest

from genshin_app.agent_chat.artifact_candidate import ArtifactCandidateSession
from genshin_app.cli import main
from genshin_app.equipment import run_plan_calc
from genshin_engine.elemental_schema import MULTI_AURA_RULESET
from genshin_engine.mechanics import execute_mechanics
from genshin_engine.resource_reaction_runtime import Runtime
from tests.test_elemental_multiaura_v4 import aura, model
from tests.test_mechanics_execution import ability, action, add, rule, second_character
from tests.test_resource_lifecycle_app_gates import gated_plan
from tests.test_resource_reaction_app import reaction_plan
from tests.test_resource_reaction_receipt import resign, validate
from tests.test_shared_pool_app_delivery import authorize
from tests.test_shared_pools import config
from tests.test_special_reaction_mechanics_projection import (
    _application_fixture,
    _enable,
)


def add_direct_energy(mechanics, reaction_type, element):
    add(mechanics, rule('refund', source_id='binding:refund',
        trigger={'event': 'elemental_reaction', 'phase': 'post',
                 'reaction_types': [reaction_type], 'elements': [element], 'characters': ['a']},
        resources=[{'id': 'income', 'kind': 'energy_direct', 'delay': 0,
                    'amounts': {'a': 3}, 'source_part': 'refund'}]))
    mechanics['resource_ownership'] = [{
        'rule_id': 'refund', 'source_part': 'refund', 'replaces_event_ids': [],
        'source_ref': {'contract': 'resource-trigger-source-v1', 'kind': 'current_trigger'},
    }]


def direct_energy_case(*, initial, landed=True):
    template, rotation, mechanics = _application_fixture('electro', initial=initial)
    rotation['characters'][0].update(initial_energy=0, energy_recharge=2)
    mechanics['hit_events'][0]['application']['landed'] = landed
    _enable(mechanics, 'stellar_superconduct')
    add_direct_energy(mechanics, 'stellar_superconduct', 'electro')
    return template, rotation, mechanics


@pytest.mark.parametrize(('with_aura', 'landed'), [(True, True), (False, True), (True, False)])
def test_v4_stellar_reaction_drives_existing_direct_energy(with_aura, landed):
    initial = [aura('cryo', .8, 'a')] if with_aura else []
    out = execute_mechanics(*direct_energy_case(initial=initial, landed=landed))
    assert out['result'] is not None, out['issues']
    reactions = [row for row in out['mechanics_trace'] if row['type'] == 'elemental_reaction']
    income = [row for row in out['rotation_check']['event_log'] if row['type'] == 'energy_direct']
    if not with_aura or not landed:
        assert reactions == income == []
        return
    reaction, = reactions
    row, = income
    assert reaction['reaction_type'] == 'stellar_superconduct'
    assert reaction['target_id'] == 'enemy'
    assert row['resource_cause']['run_id'] == reaction['run_id']
    assert row['resource_cause']['event_id'] == reaction['event_id']
    assert row['resource_cause']['action_id'] == reaction['action_id']
    assert row['t'] == reaction['t'] == .2
    gain, = row['energy']
    assert (gain['base_amount'], gain['energy_recharge'], gain['raw'], gain['gain']) == (3, 2, 6, 6)


def test_v4_direct_energy_id_is_not_a_shared_pool_operation():
    template, rotation, mechanics = direct_energy_case(initial=[aura('cryo', .8, 'a')])
    mechanics['resource_reaction_contract'] = {'schema_version': 1, 'operation_ids': ['income']}
    out = execute_mechanics(template, rotation, mechanics)
    assert out['result'] is None
    assert any(row['code'] == 'resource_reaction_reference' for row in out['issues'])
    assert out['rotation_check']['event_log'] == []


@pytest.mark.parametrize('burst_order', [9, 11])
def test_v4_reaction_credit_respects_same_tick_burst_order(burst_order):
    template, rotation, mechanics = direct_energy_case(initial=[aura('cryo', .8, 'a')])
    burst_ability = ability('q', kind='action.elemental_burst',
                           energy={'delivery': 'action_start', 'required': 6, 'amount': 6})
    burst_ability['cooldown'] = {'group': 'burst', 'duration': 15, 'charges': 1,
                                'start': 'action_start', 'recovery': 'parallel'}
    rotation['abilities'].append(burst_ability)
    rotation['initial_cooldowns'].append({'character': 'a', 'group': 'burst', 'ready_in': []})
    rotation['actions'].append({**action('burst', 'q', .2, .25, order=burst_order),
                                'repeat': {'mode': 'once'}})
    out = execute_mechanics(template, rotation, mechanics)
    rows = out['rotation_check']['event_log']
    assert rows, out['issues']
    burst = next(row for row in rows if row['id'] == 'burst')
    credits = [row for row in rows if row['type'] == 'energy_direct']
    if burst_order > 10:
        credit, = credits
        assert credit['t'] == burst['t'] == .2 and credit['order'] < burst['order']
        assert burst['energy_admission']['before'] == 6
        assert burst['status'] == 'passed'
    else:
        assert burst['status'] == 'failed' and burst['before'] == 0
        assert credits == []


def audited_pool(mechanics, reaction_types, *, cooldown=0):
    pools = config()
    pools['pools'][0]['initial'] = {'team': 0}
    pools['consumers'] = []
    operation = pools['operations'][0]
    operation['trigger']['reaction_types'] = reaction_types
    operation['cooldown']['seconds'] = cooldown
    mechanics['shared_pools'] = pools
    mechanics['resource_reaction_contract'] = {'schema_version': 1, 'operation_ids': ['refill']}
    return operation


def test_v4_same_hit_audits_both_amplifying_branches_in_order():
    template, rotation, mechanics = _application_fixture('pyro', initial=[aura('hydro', .2), aura('cryo', .3)])
    audited_pool(mechanics, ['vaporize', 'melt'], cooldown=1)
    out = execute_mechanics(template, rotation, mechanics)
    assert out['result'] is not None, out['issues']
    audit = out['resource_reactions']
    assert audit['verification_status'] == 'verified'
    assert audit['program']['elemental_ruleset'] == MULTI_AURA_RULESET
    scopes = audit['occurrences']
    assert [row['reaction_type'] for row in scopes] == ['vaporize', 'melt']
    assert len({row['parent_run_id'] for row in scopes}) == 1
    assert len({row['reaction_run_id'] for row in scopes}) == 2
    assert all(row['callback_complete'] and row['resource_scan_complete'] for row in scopes)
    receipts = out['shared_pool_trace']
    assert [row['run_id'] for row in receipts] == [row['reaction_run_id'] for row in scopes]
    assert [(row['reason'], row['accepted']) for row in receipts] == [('granted', 3), ('cooldown', 0)]
    assert [(row['ledger_begin'], row['actual_end']) for row in audit['operations']] == [(0, 1), (1, 2)]


def test_v4_rejects_copied_second_branch_and_keeps_first_grant(monkeypatch):
    template, rotation, mechanics = _application_fixture('pyro', initial=[aura('hydro', .2), aura('cryo', .3)])
    audited_pool(mechanics, ['vaporize', 'melt'])
    begin = Runtime.begin

    def copy_melt(runtime, event, hit, reaction_event, parent, application, reaction):
        if reaction['type'] == 'melt':
            reaction = copy.deepcopy(reaction)
        return begin(runtime, event, hit, reaction_event, parent, application, reaction)

    monkeypatch.setattr(Runtime, 'begin', copy_melt)
    out = execute_mechanics(template, rotation, mechanics)
    assert out['status'] == 'pending' and out['result'] is None
    assert any(row['code'] == 'resource_reaction_actual_identity' for row in out['issues'])
    assert [row['accepted'] for row in out['shared_pool_trace']] == [3]
    assert out['resource_reactions']['verified_window'] == 'executed_prefix'


@pytest.mark.parametrize('aura_element', ['electro', 'cryo'])
def test_v4_audits_electro_swirl_and_real_nonmatches(aura_element):
    template, rotation, mechanics = _application_fixture('anemo', initial=[aura(aura_element, .7)])
    audited_pool(mechanics, ['swirl'])['trigger']['reaction_elements'] = ['electro']
    out = execute_mechanics(template, rotation, mechanics)
    assert out['result'] is not None, out['issues']
    audit = out['resource_reactions']
    assert audit['verification_status'] == 'verified'
    scope, = audit['occurrences']
    operation, = audit['operations']
    assert (scope['reaction_type'], scope['aura_element']) == ('swirl', aura_element)
    if aura_element == 'electro':
        assert operation['state'] == 'committed'
        assert [row['accepted'] for row in out['shared_pool_trace']] == [3]
    else:
        assert operation['state'] == 'no_match'
        assert out['shared_pool_trace'] == []


def test_v4_stellar_callback_keeps_direct_energy_and_excludes_audited_pool():
    template, rotation, mechanics = direct_energy_case(initial=[aura('cryo', .8, 'a')])
    operation = audited_pool(mechanics, ['melt'])
    # This operation has no reaction-resource audit contract. Its existing
    # ordinary callback semantics remain available alongside the audited pool.
    other = {**copy.deepcopy(operation), 'id': 'other', 'source_id': 'binding:other', 'order': 1}
    other['trigger'].pop('reaction_types')
    mechanics['shared_pools']['operations'].append(other)
    out = execute_mechanics(template, rotation, mechanics)
    assert out['result'] is not None, out['issues']
    audit = out['resource_reactions']
    assert audit['verification_status'] == 'verified'
    assert audit['occurrences'] == audit['operations'] == []
    assert [row['operation_id'] for row in out['shared_pool_trace']] == ['other']
    assert [row['energy'][0]['gain'] for row in out['rotation_check']['event_log']
            if row['type'] == 'energy_direct'] == [6]


@pytest.mark.parametrize('detonation_has_pyro', [False, True])
def test_v4_swirl_replacement_cannot_grant_ordinary_pool_resource(detonation_has_pyro):
    times = (.2, .4) if detonation_has_pyro else (.2,)
    template, rotation, mechanics = _application_fixture('anemo', initial=[aura('cryo', .5, 'b')], times=times)
    second_character(template, rotation, 'b')
    if detonation_has_pyro:
        template['hits'][1]['element'] = 'pyro'
        mechanics['hit_events'][1]['application'] = {
            'profile_id': 'pyro', 'target_id': 'enemy', 'landed': True, 'area_id': 'battlefield.primary',
        }
    audited_pool(mechanics, ['swirl', 'melt'])
    _enable(mechanics, 'stellar_swirl')
    out = execute_mechanics(template, rotation, mechanics)
    assert out['result'] is not None, out['issues']
    replacement, *derived = [row for row in out['mechanics_trace'] if row['type'] == 'elemental_reaction']
    assert (replacement['reaction_type'], replacement['replaces']) == ('stellar_swirl', 'swirl')
    assert replacement['ordinary_side_effects_committed'] is False
    application, = [row for row in out['elemental_trace'] if row.get('generated_application_request_id')]
    if detonation_has_pyro:
        melt, = derived
        assert (melt['reaction_type'], melt['parent_run_id']) == ('melt', application['run_id'])
    else:
        assert derived == []
    audit = out['resource_reactions']
    assert audit['verification_status'] == 'verified'
    assert audit['occurrences'] == audit['operations'] == out['shared_pool_trace'] == []


def v4_app_plan(*, gated=False):
    plan = gated_plan() if gated else reaction_plan()
    mechanics = plan['mechanics_model']
    mechanics['elemental_model'] = model([aura('electro', 2, 'a')], freezable=False)
    mechanics['shared_pools']['operations'][0]['trigger']['reaction_elements'] = ['electro']
    return plan


@pytest.mark.parametrize('gated', [False, True])
def test_v4_app_adopts_and_replays_proof_and_rejects_removed_occurrence(tmp_path, gated):
    plan = v4_app_plan(gated=gated)
    db = tmp_path/'absent.db'
    value = run_plan_calc(authorize(plan, db), db)
    audit = value['result']['resource_reactions']
    assert audit['program']['elemental_ruleset'] == MULTI_AURA_RULESET
    assert audit['verification_status'] == 'verified'
    assert len(audit['occurrences']) == 3
    if gated:
        assert [row['state'] for row in audit['operations']] == ['blocked_lifecycle', 'committed', 'blocked_lifecycle']
        assert [row['lifecycle_qualification']['reason'] for row in audit['operations']] == ['not_established', 'eligible', 'ended']
    validate(value)
    validate(value, full=False)
    occurrence = audit['occurrences'].pop(0)
    audit['operations'] = [row for row in audit['operations'] if row['occurrence_id'] != occurrence['id']]
    resign(value)
    with pytest.raises(ValueError):
        validate(value, full=False)


def test_v4_cli_and_candidate_share_energy_and_pool_proof(tmp_path, monkeypatch, capsys):
    plan = v4_app_plan()
    plan['manual_template']['characters'][0]['stats']['energy_recharge'] = 2
    plan['rotation_model']['characters'][0].update(initial_energy=0, energy_recharge=2)
    add_direct_energy(plan['mechanics_model'], 'swirl', 'anemo')
    parts = plan['effect_bindings'][0]['parts']
    part = copy.deepcopy(parts[0])
    part['part_id'] = 'refund'
    part['rule']['rule_id'] = 'refund'
    parts.append(part)
    db = tmp_path / 'genshin.db'
    current = ArtifactCandidateSession(str(uuid.uuid4()), tmp_path / 'candidate', db, 'native', plan)
    contract = current.call_tool('gs_read_context', {
        'resource': 'mechanics_capabilities', 'pointer': '/resource_trigger_source/direct_reaction_energy',
    })
    assert contract['code'] == 0
    assert {row['key'] for row in contract['data']['items']} >= {'ownership', 'settlement', 'separate_contract'}
    source = authorize(copy.deepcopy(current.candidate), db)
    current.replace_candidate(source, current.revision, 'Review the current synthetic resource graph')
    response = current.call_tool('gs_calculate', {'expected_revision': current.revision})
    assert response['code'] == 0, response.get('failure_summary', response)

    source_path = tmp_path / 'plan.json'
    result_path = tmp_path / 'result.json'
    source_path.write_text(json.dumps(current.candidate), encoding='utf-8')
    monkeypatch.setattr('genshin_app.cli._require_runtime_database', lambda _: {'can_start': True})
    assert main(['--data-dir', str(tmp_path), 'equipment', 'calc', '--plan', str(source_path),
                 '--output', str(result_path), '--summary']) == 0, capsys.readouterr().err
    capsys.readouterr()
    saved = json.loads(result_path.read_text(encoding='utf-8'))
    assert saved['result']['resource_reactions'] == current.calculated['result']['resource_reactions']
    assert saved['result']['rotation_check']['event_log'] == current.calculated['result']['rotation_check']['event_log']
    credits = [row for row in saved['result']['rotation_check']['event_log'] if row['type'] == 'energy_direct']
    assert [row['energy'][0]['raw'] for row in credits] == [6, 6, 6]
    validate(current.calculated)
    request_path = tmp_path / 'review.json'
    request_path.write_text(json.dumps({'view': 'resource_reactions'}), encoding='utf-8')
    assert main(['--data-dir', str(tmp_path), 'equipment', 'review', '--calculated', str(result_path),
                 '--request', str(request_path)]) == 0, capsys.readouterr().err
    review = json.loads(capsys.readouterr().out)['data']
    assert review['total_rows'] == 3
    assert [row['reaction_run_id'] for row in review['items']] == [row['resource_cause']['run_id'] for row in credits]
    assert all(row['verification_status'] == 'verified' for row in review['items'])
