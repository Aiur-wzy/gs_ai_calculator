"""Independent EC24 assembly checks against the pre-existing handwritten axis."""
from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from genshin_app.equipment import run_plan_calc
from genshin_app.equipment.files import fingerprint, input_view
from genshin_app.equipment.schema import PlanError, validate_plan
from tests.test_mechanics_execution import EVIDENCE, hit
from tests.test_periodic_actions_review import accept_periodic, source_bound_plan
from tests.test_pipeline import fake_repo, stub_fetcher  # noqa: F401
from tests.test_runtime_periodic_attack import (
    SOURCE,
    change,
    periodic_db,  # noqa: F401
)


@pytest.fixture(autouse=True)
def _runtime_database_already_admitted(monkeypatch):
    monkeypatch.setattr(
        "genshin_app.cli._require_runtime_database",
        lambda _data_dir: {"can_start": True, "state": "test_ready"},
    )

# ruff: noqa: F811


def assemble(plan, request, db):
    # Deferred import permits review fixture preparation before the author module lands.
    from genshin_app.equipment.periodic_source_assembly import assemble_periodic_source

    return assemble_periodic_source(plan, request, db)


def inputs_from_manual(manual):
    """Extract declarations only; baseline is never constructed by the new API."""
    plan = copy.deepcopy(manual)
    profile = plan['action_event_profiles'][0]
    source = plan['periodic_attack_sources'][0]
    mappings = {row['channel_id']: row for row in source['channels']}
    channels = []
    for channel in profile['channels']:
        channels.append({key: copy.deepcopy(channel[key]) for key in
                         ('schedule', 'contact_profile_id', 'default_landed', 'self_sampling')})
        channels[-1].update(candidate_id=mappings[channel['channel_id']]['candidate_id'],
                            counter_tag=mappings[channel['channel_id']]['counter_tag'],
                            application_mode=mappings[channel['channel_id']]['application_mode'],
                            verification=copy.deepcopy(mappings[channel['channel_id']]['verification']))
    request = {'schema_version': 1, 'profile_id': profile['id'],
               'ability_ids': [row['ability_id'] for row in plan['action_event_bindings']],
               'version_line': source['version_line'], 'selection': copy.deepcopy(source['selection']),
               'origin_reference_id': source['origin_reference_id'],
               'damage_type_ids': ['damage.elemental_burst'], 'scaling_stat': 'atk',
               'lifecycle': copy.deepcopy(profile['lifecycle']), 'channels': channels,
               'status': source['status'], 'source': source['source']}
    owned = {row['prototype_hit_id'] for row in profile['channels']}
    plan['manual_template']['hits'] = [row for row in plan['manual_template']['hits'] if row['id'] not in owned]
    for key in ('action_event_profiles', 'action_event_bindings', 'action_event_overrides', 'periodic_attack_sources'):
        plan.pop(key)
    plan['resolution'] = {'state': 'pending'}
    plan['resolved_template'] = None
    validate_plan(plan)
    return plan, request


def projections(output):
    result = output['result']
    return {
        'hits': [(row['t'], row['expected']) for row in result['hits']],
        'applications': [(row['t'], row['allowed']) for row in result['elemental']['trace']
                         if row['type'] == 'elemental_application'],
        'total': result['summary']['total_damage'],
    }


def test_review_two_channels_preserve_axis_and_match_handwritten_fifteen_contacts(periodic_db):
    manual = source_bound_plan(periodic_db)
    plan, request = inputs_from_manual(manual)
    before = copy.deepcopy((plan, request))
    proposal = assemble(plan, request, periodic_db)
    native = proposal['plan']
    assert (plan, request) == before
    assert proposal['assembly']['created'] == {'hits': 2, 'profiles': 1, 'bindings': 1, 'sources': 1, 'channels': 2}
    assert proposal['report']['state'] == 'pending'
    assert native['action_event_profiles'][0]['accepted_profile_fingerprint'] is None
    assert proposal['report']['action_event_compilations'][0]['generated'] == []
    for field in ('rotation_model', 'mechanics_model', 'equipment_inputs'):
        assert native[field] == before[0][field]
    assert native['resolution'] == {'state': 'pending'} and native['resolved_template'] is None
    assert all(not ({'element', 'multiplier'} & row.keys()) for row in native['manual_template']['hits'])
    assert proposal['assembly']['base_input_fingerprint'] == fingerprint(input_view(plan))
    assert proposal['assembly']['input_fingerprint'] == fingerprint(input_view(native))
    baseline = run_plan_calc(accept_periodic(manual, periodic_db), periodic_db)
    actual = run_plan_calc(accept_periodic(native, periodic_db), periodic_db)
    assert projections(actual) == projections(baseline)
    expected = [float(Decimal(45) * Decimal(value)) for value in
                ['2.021399974822998'] * 4 + ['3.032099962234497']] * 3
    assert [row['expected'] for row in actual['result']['hits']] == pytest.approx(expected, rel=1e-13)
    assert [row['t'] for row in actual['result']['hits']] == [
        start + offset for start in (0, 10, 20) for offset in (.2, .45, .7, .95, 1.1)]
    assert actual['report']['action_event_compilations'][0]['profile_fingerprint'] == \
        baseline['report']['action_event_compilations'][0]['profile_fingerprint']


def test_review_reordering_preserves_generated_identity_and_existing_native_order(periodic_db):
    plan, request = inputs_from_manual(source_bound_plan(periodic_db))
    first = assemble(plan, request, periodic_db)
    reversed_request = copy.deepcopy(request)
    reversed_request['channels'].reverse()
    second = assemble(plan, reversed_request, periodic_db)
    assert first['assembly']['channels'] == second['assembly']['channels']
    assert first['plan'] == second['plan']
    native = accept_periodic(first['plan'], periodic_db)
    native['manual_template']['hits'].reverse()
    native['action_event_profiles'][0]['channels'].reverse()
    native['periodic_attack_sources'][0]['channels'].reverse()
    before = copy.deepcopy(native)
    repeated = assemble(native, reversed_request, periodic_db)
    assert repeated['assembly']['status'] == 'existing'
    assert all(value == 0 for value in repeated['assembly']['created'].values())
    assert native == repeated['plan'] == before
    assert repeated['report']['state'] == 'ready', repeated['report']['missing_inputs']


@pytest.mark.parametrize('mutation', ['label', 'calibration', 'counter_tag', 'remove_candidate', 'add_ability'])
def test_review_existing_owned_content_is_never_overwritten(periodic_db, mutation):
    plan, request = inputs_from_manual(source_bound_plan(periodic_db))
    native = accept_periodic(assemble(plan, request, periodic_db)['plan'], periodic_db)
    if mutation == 'label':
        native['manual_template']['hits'][0]['hit_name'] = '用户独立备注'
    elif mutation == 'calibration':
        request['lifecycle']['source'] += ' revised'
    elif mutation == 'counter_tag':
        request['channels'][0]['counter_tag'] = 'different.group'
    elif mutation == 'remove_candidate':
        request['channels'].pop()
    else:
        native['rotation_model']['abilities'].append({**native['rotation_model']['abilities'][0], 'id': 'q.alias'})
        request['ability_ids'].append('q.alias')
    before = copy.deepcopy((native, request))
    with pytest.raises(PlanError) as error:
        assemble(native, request, periodic_db)
    assert 'conflict' in error.value.reason
    assert (native, request) == before


@pytest.mark.parametrize('unknown', ['origin', 'candidate', 'schedule', 'landed', 'kind'])
def test_review_unknown_or_mismatched_channel_keeps_complete_pending_draft(periodic_db, unknown):
    plan, request = inputs_from_manual(source_bound_plan(periodic_db))
    channel = next(row for row in request['channels'] if row['schedule']['kind'] == 'periodic')
    if unknown == 'origin':
        request['origin_reference_id'] = None
    elif unknown == 'candidate':
        channel['candidate_id'] = 'not-in-source'
    elif unknown == 'schedule':
        channel['schedule']['count'] = None
    elif unknown == 'landed':
        channel['default_landed'] = None
    else:
        channel['schedule'] = {'kind': 'single', 'offset_seconds': .2, 'precision_seconds': .01, **EVIDENCE}
    before = copy.deepcopy((plan, request))
    proposal = assemble(plan, request, periodic_db)
    assert (plan, request) == before
    assert len(proposal['plan']['manual_template']['hits']) == len(proposal['assembly']['channels']) == 2
    assert len(proposal['plan']['periodic_attack_sources'][0]['channels']) == 2
    assert proposal['report']['state'] == 'pending'
    assert proposal['report']['action_event_compilations'][0]['generated'] == []
    if unknown == 'origin':
        assert proposal['assembly']['source_summary']['omitted_candidate_ids'] is None
        assert proposal['assembly']['source_summary']['omission_status'] == 'not_evaluated'


@pytest.mark.parametrize('kind', ['hit', 'pattern', 'application', 'action_hit_link'])
def test_review_manual_parent_delivery_is_preserved_on_conflict(periodic_db, kind):
    plan, request = inputs_from_manual(source_bound_plan(periodic_db))
    plan['manual_template']['hits'] = [hit('manual', .2)]
    model = plan['mechanics_model']
    if kind == 'hit':
        model['hit_events'] = [{'hit_id': 'manual', 'action_id': 'q1', 'order': 0}]
    elif kind == 'pattern':
        model['hit_patterns'] = [{'id': 'manual.pattern', 'prototype_hit_id': 'manual',
                                  'action_id': 'q1', 'offsets': [.2], 'orders': [0]}]
    elif kind == 'application':
        model['elemental_model']['profiles'] = [{'id': 'manual.app', 'element': 'cryo', 'application': 'none', **EVIDENCE}]
        model['application_events'] = [{'id': 'manual.apply', 'action_id': 'q1', 't': .2, 'order': 0,
            'application': {'profile_id': 'manual.app', 'target_id': 'enemy', 'landed': True},
            'contact': {'kind': 'none', **EVIDENCE}}]
    else:
        plan['rotation_model']['action_hit_links'] = [{'action_id': 'q1', 'hit_id': 'manual'}]
    validate_plan(plan)
    before = copy.deepcopy((plan, request))
    with pytest.raises(PlanError) as error:
        assemble(plan, request, periodic_db)
    assert 'conflict' in error.value.reason
    assert (plan, request) == before


def test_review_idempotent_retry_still_reads_changed_complete_source(periodic_db, fake_repo):
    plan, request = inputs_from_manual(source_bound_plan(periodic_db))
    native = accept_periodic(assemble(plan, request, periodic_db)['plan'], periodic_db)
    before = copy.deepcopy(native)
    change(periodic_db, fake_repo, SOURCE, lambda rows: rows[2]['Default'].update(unreviewed_control=True))
    retried = assemble(native, request, periodic_db)
    assert retried['assembly']['status'] == 'existing'
    assert retried['plan'] == native == before
    assert retried['report']['state'] == 'pending'
    assert retried['report']['action_event_compilations'][0]['generated'] == []


def test_review_explicit_subset_reports_omission_without_inventing_second_channel(periodic_db):
    plan, request = inputs_from_manual(source_bound_plan(periodic_db))
    omitted = request['channels'].pop()['candidate_id']
    proposal = assemble(plan, request, periodic_db)
    summary = proposal['assembly']['source_summary']
    assert len(proposal['plan']['manual_template']['hits']) == len(proposal['assembly']['channels']) == 1
    assert summary['whole_source_status'] == 'partial'
    assert summary['action_creation_association'] == 'unproven'
    assert summary['dispatch_semantics_status'] == 'not_proven'
    assert summary['omission_status'] == 'known'
    assert omitted in summary['omitted_candidate_ids']
    assert set(summary['omitted_candidate_ids']).isdisjoint(summary['requested_candidate_ids'])


def test_review_shared_counter_tag_and_absolute_axis_are_not_rewritten(periodic_db):
    manual = source_bound_plan(periodic_db)
    manual['rotation_model']['period'] = manual['manual_template']['loop_duration'] = 5
    for action, spend, start in zip(manual['rotation_model']['actions'], manual['rotation_model']['events'],
                                     (0, 1.3, 2.6), strict=True):
        action.update(start=start, occupation_end=start + .5)
        spend['t'] = start
    plan, request = inputs_from_manual(manual)
    native = assemble(plan, request, periodic_db)['plan']
    assert native['rotation_model'] == plan['rotation_model']
    output = run_plan_calc(accept_periodic(native, periodic_db), periodic_db)
    applications = sorted((row for row in output['result']['elemental']['trace']
                           if row['type'] == 'elemental_application'), key=lambda row: row['t'])
    assert [row['allowed'] for row in applications] == [True, False, False, True, False,
        False, True, False, False, True, True, False, False, True, False]


def test_review_real_cli_fresh_preview_cannot_overwrite_concurrent_native_edit(periodic_db, tmp_path, monkeypatch, capsys):
    from genshin_app.cli import main
    from genshin_app.equipment import periodic_source_assembly as module

    plan, request = inputs_from_manual(source_bound_plan(periodic_db))
    path, req_path = tmp_path / 'original.plan.json', tmp_path / 'request.json'
    path.write_text(json.dumps(plan, ensure_ascii=False), 'utf8')
    req_path.write_text(json.dumps(request, ensure_ascii=False), 'utf8')
    args = ['--data-dir', str(periodic_db.parent), 'equipment', 'assemble-periodic',
            '--plan', str(path), '--request', str(req_path)]
    original = module.assemble_periodic_source
    calls = []
    external = copy.deepcopy(plan)
    external['manual_template'].setdefault('meta', {})['name'] = '同时完成的用户编辑'
    external_bytes = json.dumps(external, ensure_ascii=False).encode('utf8')

    def racing(*values):
        response = original(*values)
        calls.append(response['report']['state'])
        path.write_bytes(external_bytes)
        return response

    monkeypatch.setattr(module, 'assemble_periodic_source', racing)
    assert main([*args, '--output', str(req_path), '--overwrite']) == 1
    assert json.loads(capsys.readouterr().err)['reason'] == 'file_conflict'
    assert calls == []
    assert main([*args, '--output', str(path), '--overwrite']) == 1
    assert json.loads(capsys.readouterr().err)['reason'] == 'file_conflict'
    assert calls == ['pending']
    assert path.read_bytes() == external_bytes
    assert not path.with_name(path.name + '.previous').exists()
