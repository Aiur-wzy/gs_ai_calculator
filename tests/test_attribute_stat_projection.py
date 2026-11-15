"""A2a isolated tests. Pure caller frames, not mechanics source authority."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.attribute_stat_graph import AttributeStatGraphLedger
from genshin_engine.attribute_stat_projection import AttributeStatProjectionLedger
from tests.test_attribute_graph import frame
from tests.test_attribute_stat_graph import expr, model, node, put


def fixture():
    data = model()
    original = node('pct', expr(sample='missing:event', factor=.002, max=.3), percent=True,
                    output_domain='self', sample_binding={'mode': 'event', 'event_id': 'observed',
                                                          'origin_scope': 'evaluation_event'})
    put(data, original)
    observed = frame('observed', 1, 1)
    observed['observation'] = {'kind': 'executed_event', 'event_id': 'observed', 'origin': 0,
                               'actual_event_run_id': '0:observed'}
    data['frames'].extend([observed, frame('current', 2, 2)])
    projected = copy.deepcopy(original)
    projected.update(id='projected', projection_of={'frame_id': 'f0', 'contribution_id': 'pct'})
    projected['value']['input']['frame_id'] = 'observed'
    target = frame('damage', 3, 3, [projected['id']])
    target.update(frame_kind='damage_projection', phase='damage_projection',
                  context={'actor': 'a', 'actual_event_run_id': '0:damage',
                           'self_frame_id': 'f0', 'current_frame_id': 'current'})
    data['contributions'].append(projected)
    data['frames'].append(target)
    return data


def read(service, stat='atk'):
    return service.read_stat('a', stat, 'total', 'damage', purpose='damage_projection')


def footprint(service, stat='atk'):
    return service.dependency_footprint('a', stat, 'total', 'damage', purpose='damage_projection')


def test_event_ratio_then_base_keeps_both_receipts_and_old_eligibility():
    service = AttributeStatProjectionLedger(fixture())
    result = read(service)
    assert result['value'] == 115
    row = result['contributions'][0]
    assert row['raw_evaluated_value'] == .3
    assert row['application_base_value'] == 50 and row['evaluated_value'] == 15
    catalog = {r['receipt_id']: r for r in service.receipt_catalog()}
    assert catalog[row['input_receipt_id']]['query']['frame']['id'] == 'observed'
    assert catalog[row['application_base_receipt_id']]['query']['frame']['id'] == 'f0'
    assert row['projection_of']['frame_id'] == 'f0'
    assert service.read_stat('a', 'atk', 'total', 'f0')['status'] == 'pending'


def test_footprint_routes_base_to_qualified_property_frame_without_numeric_read():
    service = AttributeStatProjectionLedger(fixture())
    result = footprint(service)
    assert result['status'] == 'ready'
    edges = result['edges']
    queries = {q['id']: q['query'] for q in result['queries']}
    assert any(e['role'] == 'application_base' and queries[e['to']]['frame']['id'] == 'f0'
               and queries[e['to']]['basis'] == 'base' for e in edges)
    assert any(e['role'] == 'value_input' and queries[e['to']]['frame']['id'] == 'observed' for e in edges)
    assert service.receipt_catalog() == []


def test_em_same_projection_does_not_read_unrelated_atk_missing_base():
    data = fixture()
    del data['initials'][0]['base']
    service = AttributeStatProjectionLedger(data)
    assert read(service, 'em')['value'] == 200
    assert footprint(service, 'em')['status'] == 'ready'
    assert read(service)['issues'][0]['code'] == 'attribute_basis_missing'


@pytest.mark.parametrize('change', ['stat', 'unit', 'application', 'recipient', 'provider',
                                   'frame', 'nonmember', 'input_actor', 'input_basis', 'constant'])
def test_projection_cannot_mutate_original_base_route_or_output_identity(change):
    data = fixture()
    _original, projected = data['contributions']
    if change == 'stat':
        projected.update(stat='em', nonextra_inclusion='included', value_unit='flat', application={'kind': 'flat'})
    elif change == 'unit':
        projected['value_unit'] = 'flat'
    elif change == 'application':
        projected.update(value_unit='flat', application={'kind': 'flat'})
    elif change == 'recipient':
        projected['component']['recipient'] = 'b'
        data['frames'][-1]['context']['actor'] = 'b'
    elif change == 'provider':
        projected['output_domain'] = 'provider'
    elif change == 'frame':
        projected['projection_of']['frame_id'] = 'current'
    elif change == 'nonmember':
        data['frames'][0]['contribution_ids'] = []
    elif change == 'input_actor':
        projected['value']['input']['actor'] = 'b'
    elif change == 'input_basis':
        projected['value']['input']['basis'] = 'base'
    else:
        del projected['sample_binding']
        projected['value'] = .3
    try:
        service = AttributeStatProjectionLedger(data)
    except AttributeLedgerError as exc:
        assert exc.issue['code'] in {'attribute_unit_scope', 'attribute_projection_source'}
        return
    actor = projected['component']['recipient']
    for method in (service.read_stat, service.dependency_footprint):
        result = method(actor, projected['stat'], 'total', 'damage', purpose='damage_projection')
        assert result['status'] == 'invalid' and result['issues'][0]['code'] == 'attribute_projection_source'


def test_a1_ordinary_service_remains_closed_and_projection_cannot_be_input():
    with pytest.raises(AttributeLedgerError):
        AttributeStatGraphLedger(fixture())
    data = fixture()
    data['frames'].append(frame('later', 4, 4))
    put(data, node('read_projection', expr(stat='atk', sample='damage'), recipient='b'), fid='later')
    service = AttributeStatProjectionLedger(data)
    result = service.read_stat('b', 'atk', 'total', 'later')
    assert result['issues'][0]['code'] == 'attribute_projection_dependency'


def test_constant_percentage_still_requires_qualified_base_and_static_identity():
    data = fixture()
    for value in data['contributions']:
        value.pop('sample_binding')
        value['value'] = .2
    service = AttributeStatProjectionLedger(data)
    assert read(service)['value'] == 110
    assert footprint(service)['status'] == 'ready'
    data['initials'][0]['included_components'] = [{'component': copy.deepcopy(data['contributions'][0]['component']),
                                                 'value': 10, 'nonextra_inclusion': 'unknown'}]
    service = AttributeStatProjectionLedger(data)
    assert read(service)['issues'][0]['code'] == 'attribute_duplicate_delivery'
    assert footprint(service)['issues'][0]['code'] == 'attribute_duplicate_delivery'


def test_same_component_double_stat_has_independent_identity():
    data = fixture()
    old = data['contributions'][0]
    em = node('em', 30, stat='em', component=copy.deepcopy(old['component']),
              instance_id=old['instance_id'], layer_id=old['layer_id'], output_domain='self')
    put(data, em)
    projected = copy.deepcopy(em)
    projected.update(id='projected_em', projection_of={'frame_id': 'f0', 'contribution_id': 'em'})
    put(data, projected, fid='damage')
    service = AttributeStatProjectionLedger(data)
    assert read(service)['value'] == 115 and read(service, 'em')['value'] == 230


def test_two_provider_layers_keep_their_individual_trigger_input_frames():
    data = fixture()
    first, projected = data['contributions']
    for row in (first, projected):
        row.pop('sample_binding')
        row['output_domain'] = 'provider'
        row['value'] = expr(sample='f0', factor=.001)
    data['frames'][0]['contribution_ids'] = []
    data['frames'][2]['contribution_ids'] = ['pct']
    projected['projection_of']['frame_id'] = 'current'
    extra = node('new_em', 100, stat='em')
    put(data, extra, fid='observed')
    second = copy.deepcopy(first)
    second.update(id='pct2', layer_id='second')
    second['value']['input']['frame_id'] = 'observed'
    put(data, second, fid='current')
    second_projection = copy.deepcopy(second)
    second_projection.update(id='projected2', projection_of={'frame_id': 'current', 'contribution_id': 'pct2'})
    put(data, second_projection, fid='damage')
    service = AttributeStatProjectionLedger(data)
    result = read(service)
    assert result['value'] == 125
    assert sorted(r['evaluated_value'] for r in result['contributions']) == [10, 15]
    assert len({r['input_receipt_id'] for r in result['contributions']}) == 2


def test_cached_subquery_still_counts_projection_longest_dependency_chain():
    data = fixture()
    for row in data['contributions']:
        row.pop('sample_binding')
        row['value']['input']['frame_id'] = 'f0'
    put(data, node('em_chain', expr(actor='b', factor=.5), stat='em'))
    service = AttributeStatProjectionLedger(data)
    service._graph_limits['dependency_depth'] = 2
    assert service.read_stat('a', 'em', 'total', 'f0')['status'] == 'known'
    assert read(service)['issues'][0]['code'] == 'attribute_dependency_depth'
    assert footprint(service)['issues'][0]['code'] == 'attribute_dependency_depth'


def test_footprint_and_numeric_reads_share_output_budget_and_keep_old_receipt():
    service = AttributeStatProjectionLedger(fixture())
    prior = read(service)
    service._graph_limits['output_bytes'] = service.usage()['output_bytes']
    with pytest.raises(AttributeLedgerError) as error:
        footprint(service)
    assert error.value.issue['code'] == 'attribute_output_limit'
    assert service._published[prior['receipt_id']] == prior
    failure = read(service)
    assert failure['status'] == 'pending' and failure['issues'][0]['code'] == 'attribute_output_limit'


@pytest.mark.parametrize('change', ['future', 'not_observed', 'wrong_origin'])
def test_event_cannot_be_future_or_merely_planned(change):
    data = fixture()
    observed = data['frames'][1]
    if change == 'future':
        observed['state_epoch'] = observed['t_us'] = 4
    elif change == 'not_observed':
        observed.pop('observation')
    else:
        observed['origin'] = observed['observation']['origin'] = 1
        observed['run_id'] = observed['observation']['actual_event_run_id'] = '1:observed'
    result = read(AttributeStatProjectionLedger(data))
    assert result['status'] == 'pending' and 'value' not in result
