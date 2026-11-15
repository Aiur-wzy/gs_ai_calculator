from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_graph import AttributeGraphLedger
from genshin_engine.attribute_ledger import AttributeLedgerError
from tests.test_attribute_graph import expr, frame, model, node, put, read


def footprint(service, actor='a', basis='total', fid='f0'):
    return service.dependency_footprint(actor, 'em', basis, fid)


def test_current_chain_and_historical_inputs_are_distinct_without_evaluating():
    data = model()
    data['frames'].append(frame('f1', 1, 1))
    put(data, node('current', value=expr('b', sample='f1'), inclusion='included'), fid='f1')
    put(data, node('historical', value=expr('c', sample='f0'), inclusion='included'), fid='f1')
    service = AttributeGraphLedger(data)
    out = footprint(service, fid='f1')
    assert out['status'] == 'ready' and 'value' not in out and 'baseline' not in out
    assert out['current_dependencies'] == [{'actor': 'a', 'stat': 'em', 'basis': 'total'},
        {'actor': 'b', 'stat': 'em', 'basis': 'nonextra'}]
    assert {q['relation_to_root'] for q in out['queries']} == {'current', 'historical'}
    assert len(out['queries']) == 3 and len(out['edges']) == 2
    assert service._published == {}  # No mathematical read receipt was created.


def test_overflow_is_a_numerical_failure_not_a_dependency_failure():
    data = model()
    data['initials'][0]['total'] = 1e308
    put(data, node('large', value=1e308, inclusion='included'))
    service = AttributeGraphLedger(data)
    assert footprint(service)['status'] == 'ready'
    assert read(service)['issues'][0]['code'] == 'attribute_value_invalid'
    data = model()
    put(data, node('product', value=expr(factor=1e308), inclusion='included'))
    service = AttributeGraphLedger(data)
    assert footprint(service)['status'] == 'ready'
    assert read(service)['issues'][0]['code'] == 'attribute_value_invalid'


def test_nonextra_pruning_and_base_still_check_the_right_identity_scope():
    data = model()
    value = node('excluded', value=expr(sample='missing'), inclusion='excluded')
    put(data, value)
    service = AttributeGraphLedger(data)
    assert footprint(service, basis='nonextra')['status'] == 'ready'
    assert footprint(service)['issues'][0]['code'] == 'attribute_frame_missing'
    data['initials'][0]['included_components'] = [{'component': value['component'], 'value': 1, 'nonextra_inclusion': 'excluded'}]
    service = AttributeGraphLedger(data)
    assert footprint(service, basis='nonextra')['issues'][0]['code'] == 'attribute_duplicate_delivery'
    assert footprint(service, basis='base')['current_dependencies'] == []


@pytest.mark.parametrize('change,expected', [('unknown', 'attribute_classification_unknown'),
    ('filter', 'attribute_context_unsupported'), ('future', 'attribute_forward_frame'),
    ('cycle', 'attribute_dependency_cycle')])
def test_same_qualification_errors_as_math_read(change, expected):
    data = model()
    value = node('value', value=expr(), inclusion='included')
    if change == 'unknown': value['nonextra_inclusion'] = 'unknown'
    elif change == 'filter': value['constraints'] = ['filter']
    elif change == 'future':
        data['frames'].append(frame('later', 1, 1))
        value['value']['input']['frame_id'] = 'later'
    else: value['value'] = expr('a')
    put(data, value)
    a, b = AttributeGraphLedger(data), AttributeGraphLedger(data)
    assert footprint(a, basis='nonextra')['issues'][0]['code'] == expected
    assert read(b, basis='nonextra')['issues'][0]['code'] == expected


def test_no_pending_cache_after_missing_frame_adoption():
    data = model()
    data['frames'][0].update(state_epoch=2, t_us=2)
    put(data, node('missing', value=expr(sample='old'), inclusion='included'))
    service = AttributeGraphLedger(data)
    assert footprint(service)['status'] == 'pending'
    service.adopt_frame(frame('old', 1, 1))
    assert footprint(service)['status'] == 'ready'


def test_diamond_metadata_is_deduplicated_and_detached():
    data = model()
    for i in range(8): put(data, node('part'+str(i), value=expr('b'), inclusion='included'))
    service = AttributeGraphLedger(data)
    first = footprint(service)
    assert len(first['queries']) == 2 and len(first['edges']) == 8
    first['queries'][0]['query']['actor'] = 'forged'
    assert all(row['query']['actor'] != 'forged' for row in footprint(service)['queries'])


def test_work_and_output_are_shared_with_the_existing_ledger():
    service = AttributeGraphLedger(model())
    before = service.usage()
    footprint(service)
    after = service.usage()
    assert after['query_work'] > before['query_work'] and after['output_bytes'] > before['output_bytes']
    service._limits['execution_work'] = after['execution_work']
    with pytest.raises(AttributeLedgerError, match='工作量'):
        footprint(service)
    service = AttributeGraphLedger(model())
    service._graph_limits['output_bytes'] = 1
    with pytest.raises(AttributeLedgerError, match='输出'):
        footprint(service)


def test_input_order_is_not_a_game_order_and_root_basis_is_bound():
    data = model()
    put(data, node('one', value=expr(), inclusion='included'), node('two', value=3, inclusion='included'))
    before = copy.deepcopy(data)
    first = footprint(AttributeGraphLedger(data))
    data['contributions'].reverse()
    data['frames'][0]['contribution_ids'].reverse()
    second = footprint(AttributeGraphLedger(data))
    assert first == second
    assert data != before  # Only our explicit permutation changed input.
    other = footprint(AttributeGraphLedger(data), basis='nonextra')
    assert first['receipt_id'] != other['receipt_id']


@pytest.mark.parametrize('field', ['actor', 'stat', 'basis', 'frame_id'])
def test_incomplete_linear_input_is_rejected_before_either_reader(field):
    data = model()
    value = expr()
    del value['input'][field]
    put(data, node('incomplete', value=value, inclusion='included'))
    with pytest.raises(AttributeLedgerError) as raised:
        AttributeGraphLedger(data)
    assert raised.value.issue['code'] == 'attribute_schema'


def test_returned_frame_observation_cannot_mutate_adopted_state():
    data = model()
    data['frames'][0]['observation'] = {'kind': 'executed_event', 'origin': 0, 'event_id': 'f0', 'actual_event_run_id': '0:f0'}
    service = AttributeGraphLedger(data)
    out = footprint(service)
    out['queries'][0]['query']['frame']['observation']['event_id'] = 'forged'
    assert footprint(service)['queries'][0]['query']['frame']['observation']['event_id'] == 'f0'


@pytest.mark.parametrize('early_value,math_issue', [(expr(sample='missing'), 'attribute_frame_missing'),
    (expr(factor=1e308), 'attribute_value_invalid')])
def test_old_math_keeps_row_by_row_error_priority(early_value, math_issue):
    data = model()
    first = node('a-first', value=early_value, inclusion='included')
    later = node('z-later', value=1, inclusion='excluded')
    put(data, first, later)
    data['initials'][0]['included_components'] = [{'component': later['component'], 'value': 1,
                                                  'nonextra_inclusion': 'excluded'}]
    actual = read(AttributeGraphLedger(data))
    assert actual['issues'][0]['code'] == math_issue
    structure = footprint(AttributeGraphLedger(data))
    expected = 'attribute_frame_missing' if math_issue == 'attribute_frame_missing' else 'attribute_duplicate_delivery'
    assert structure['issues'][0]['code'] == expected


def test_projection_cannot_become_a_property_footprint():
    from tests.test_attribute_projection import projection_fixture
    data = projection_fixture()
    service = AttributeGraphLedger(data)
    assert footprint(service, fid='damage')['issues'][0]['code'] == 'attribute_projection_context'
    data['frames'].append(frame('later', 4, 4))
    put(data, node('bad', 'c', expr('a', 'total', 'damage'), inclusion='included'), fid='later')
    assert footprint(AttributeGraphLedger(data), 'c', fid='later')['issues'][0]['code'] == 'attribute_projection_dependency'


def test_true_longest_chain_is_bounded_without_prewarming_loopholes():
    data = model()
    for i in range(1, 34):
        data['frames'].append(frame('f'+str(i), i, i))
        put(data, node('link'+str(i), value=expr('a', 'total', 'f'+str(i-1)), inclusion='included'), fid='f'+str(i))
    service = AttributeGraphLedger(data, limits={'dependency_depth':32})
    for i in range(30):
        assert footprint(service, fid='f'+str(i))['status'] == 'ready'
    out = footprint(service, fid='f33')
    assert out['status'] == 'pending' and out['issues'][0]['code'] == 'attribute_dependency_depth'


@pytest.mark.parametrize('change', ['source', 'value', 'classification', 'seed'])
def test_constant_and_basis_membership_remain_bound_without_numeric_read(change):
    data = model()
    put(data, node('constant', value=50, inclusion='included'))
    first = footprint(AttributeGraphLedger(data))
    if change == 'source': data['contributions'][0]['component']['source_fingerprint'] = 'different-source'
    elif change == 'value': data['contributions'][0]['value'] = 60
    elif change == 'classification': data['contributions'][0]['nonextra_inclusion'] = 'excluded'
    else: data['initials'][0]['total'] = 120
    second = footprint(AttributeGraphLedger(data))
    assert first['current_dependencies'] == second['current_dependencies']
    assert first['receipt_id'] != second['receipt_id']
    assert first['queries'][0]['qualified_membership_fingerprint'] != second['queries'][0]['qualified_membership_fingerprint']
