"""Caller-qualified projection grammar; actual queue authority is a runtime duty."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_graph import AttributeGraphLedger
from genshin_engine.attribute_ledger import AttributeLedgerError
from tests.test_attribute_graph import expr, frame, model, node, put, read


def projection_fixture():
    data = model()
    data['initials'][0].update(total=0, nonextra=0)
    old = node('old_share', value=expr(sample='missing_event'), output_domain='self',
               sample_binding={'mode': 'event', 'event_id': 'observed', 'origin_scope': 'evaluation_event'})
    put(data, old)
    observed = frame('observed', 1, 1)
    observed['observation'] = {'kind': 'executed_event', 'event_id': 'observed', 'origin': 0,
                               'actual_event_run_id': '0:observed'}
    data['frames'].extend([observed, frame('current', 2, 2)])
    projected = copy.deepcopy(old)
    projected.update(id='projection_share', projection_of={'frame_id': 'f0', 'contribution_id': 'old_share'})
    projected['value']['input']['frame_id'] = 'observed'
    projection = frame('damage', 3, 3, [projected['id']])
    projection.update(frame_kind='damage_projection', phase='damage_projection',
                      context={'actor': 'a', 'actual_event_run_id': '0:damage',
                               'self_frame_id': 'f0', 'current_frame_id': 'current'})
    data['contributions'].append(projected)
    data['frames'].append(projection)
    return data


def project(service):
    return service.read_stat('a', 'em', 'total', 'damage', purpose='damage_projection')


def test_later_executed_event_uses_old_qualification_without_rewriting_old_property():
    service = AttributeGraphLedger(projection_fixture())
    assert read(service)['issues'][0]['code'] == 'attribute_frame_missing'
    result = project(service)
    assert result['value'] == 40
    assert result['contributions'][0]['projection_of']['frame_id'] == 'f0'
    assert result['contributions'][0]['evaluated_value'] == 40
    assert read(service)['issues'][0]['code'] == 'attribute_frame_missing'


@pytest.mark.parametrize('basis,actor,purpose', [('nonextra', 'a', 'damage_projection'),
                                                ('total', 'b', 'damage_projection'),
                                                ('total', 'a', 'ordinary')])
def test_projection_is_only_matching_actor_total_root(basis, actor, purpose):
    result = AttributeGraphLedger(projection_fixture()).read_stat(actor, 'em', basis, 'damage', purpose=purpose)
    assert result['status'] == 'pending' and 'value' not in result
    assert result['issues'][0]['code'] == 'attribute_projection_context'


def test_expression_may_never_consume_projection_even_when_later():
    data = projection_fixture()
    data['frames'].append(frame('later', 4, 4))
    put(data, node('invalid', 'c', expr('a', 'total', 'damage')), fid='later')
    service = AttributeGraphLedger(data)
    assert read(service, 'c', fid='later')['issues'][0]['code'] == 'attribute_projection_dependency'
    # Only the actually consumed closure is prohibited: an excluded branch is harmless.
    assert read(service, 'c', 'nonextra', fid='later')['value'] == 10


@pytest.mark.parametrize('change', ['nonmember', 'domain', 'frame', 'constant', 'factor', 'basis', 'cap', 'actor', 'authorization'])
def test_projection_cannot_invent_membership_or_change_original_facts(change):
    data = projection_fixture()
    original, projected = data['contributions']
    if change == 'nonmember':
        data['frames'][0]['contribution_ids'] = []
    elif change == 'domain':
        projected['output_domain'] = 'provider'
    elif change == 'frame':
        projected['projection_of']['frame_id'] = 'current'
    elif change == 'constant':
        projected['value'] = 40
    elif change == 'factor':
        projected['value']['factor'] = .3
    elif change == 'basis':
        projected['value']['input']['basis'] = 'base'
    elif change == 'cap':
        projected['value']['max'] = 5
    elif change == 'actor':
        projected['value']['input']['actor'] = 'c'
    else:
        del original['sample_binding']
    if change == 'constant':
        with pytest.raises(AttributeLedgerError) as error:
            AttributeGraphLedger(data)
        assert error.value.issue['code'] == 'attribute_projection_source'
        return
    result = project(AttributeGraphLedger(data))
    assert result['status'] == 'invalid' and 'value' not in result
    assert result['issues'][0]['code'] == 'attribute_projection_source'


@pytest.mark.parametrize('change', ['unobserved', 'other_event', 'other_origin', 'future'])
def test_event_rebind_requires_matching_executed_observation(change):
    data = projection_fixture()
    observed = data['frames'][1]
    if change == 'unobserved':
        del observed['observation']
    elif change == 'other_event':
        observed['event_id'] = observed['observation']['event_id'] = 'other'
    elif change == 'other_origin':
        observed['origin'] = observed['observation']['origin'] = 1
        observed['run_id'] = observed['observation']['actual_event_run_id'] = '1:observed'
    else:
        observed['state_epoch'] = 4
        observed['t_us'] = 4
    result = project(AttributeGraphLedger(data))
    assert result['status'] == 'pending' and 'value' not in result
    assert result['issues'][0]['code'] in {'attribute_event_observation_missing', 'attribute_forward_frame'}


def test_current_and_trigger_inputs_cannot_be_rebound_as_event():
    data = projection_fixture()
    for row in data['contributions']:
        del row['sample_binding']
    data['contributions'][0]['value']['input']['frame_id'] = 'f0'
    result = project(AttributeGraphLedger(data))
    assert result['issues'][0]['code'] == 'attribute_projection_source'


def test_constants_cannot_change_and_duplicate_delivery_still_rejected():
    data = projection_fixture()
    for row in data['contributions']:
        del row['sample_binding']
        row['value'] = 30
    projected = data['contributions'][1]
    assert project(AttributeGraphLedger(data))['value'] == 30
    projected['value'] = 31
    assert project(AttributeGraphLedger(data))['issues'][0]['code'] == 'attribute_projection_source'
    projected['value'] = 30
    duplicate = copy.deepcopy(projected)
    duplicate['id'] = 'duplicate'
    data['contributions'].append(duplicate)
    data['frames'][-1]['contribution_ids'].append('duplicate')
    assert project(AttributeGraphLedger(data))['issues'][0]['code'] == 'attribute_duplicate_delivery'


def test_static_included_projection_is_still_duplicate():
    data = projection_fixture()
    data['initials'][0]['included_components'] = [
        {'component': copy.deepcopy(data['contributions'][0]['component']), 'value': 40, 'nonextra_inclusion': 'excluded'}]
    assert project(AttributeGraphLedger(data))['issues'][0]['code'] == 'attribute_duplicate_delivery'


def test_incremental_projection_registration_and_context_deep_copy():
    data = projection_fixture()
    projected = data['contributions'].pop()
    projection = data['frames'].pop()
    service = AttributeGraphLedger(data)
    service.adopt_frame(projection, contributions=[projected])
    projection['context']['actor'] = 'c'
    assert project(service)['value'] == 40
