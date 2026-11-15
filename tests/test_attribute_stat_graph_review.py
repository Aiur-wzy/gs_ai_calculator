"""Independent ordinary-only ATK v3 operator, identity and depth counterexamples."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.attribute_stat_graph import AttributeStatGraphLedger
from tests.test_attribute_graph import frame
from tests.test_attribute_stat_graph import expr, model, node, put, read


def test_percentage_uses_recipient_white_value_instead_of_owner_or_total():
    data = model()
    put(data, node('owner-em', expr(factor=.005, max=.4), recipient='b', percent=True))
    result = read(AttributeStatGraphLedger(data), actor='b')
    assert result['value'] == 332
    row = result['contributions'][0]
    assert (row['raw_evaluated_value'], row['application_base_value'], row['evaluated_value']) == (.4, 80, 32)


def test_same_base_used_as_input_and_application_retains_two_role_edges_without_false_cycle():
    data = model()
    put(data, node('pct', expr(stat='atk', basis='base', factor=.004), percent=True))
    service = AttributeStatGraphLedger(data)
    result = read(service)
    assert result['value'] == 110
    row = result['contributions'][0]
    assert row['input_receipt_id'] == row['application_base_receipt_id']
    footprint = service.dependency_footprint('a', 'atk', 'total', 'f0')
    assert footprint['status'] == 'ready' and len(footprint['edges']) == 2
    assert {e['role'] for e in footprint['edges']} == {'value_input', 'application_base'}
    assert len({e['to'] for e in footprint['edges']}) == 1


def test_missing_base_blocks_percentage_footprint_but_not_unrelated_flat_read():
    data = model()
    del data['initials'][2]['base']
    put(data, node('percent-b', .2, recipient='b', percent=True), node('flat-a', 5))
    service = AttributeStatGraphLedger(data)
    assert read(service)['value'] == 105
    assert read(service, actor='b')['issues'][0]['code'] == 'attribute_basis_missing'
    footprint = service.dependency_footprint('b', 'atk', 'total', 'f0')
    assert footprint['status'] == 'pending'
    assert footprint['issues'][0]['code'] == 'attribute_basis_missing'


def test_zero_percentage_cannot_hide_duplicate_same_stat_static_identity():
    data = model()
    pct = node('pct', 0, percent=True, constraints=['quota'])
    data['initials'][0]['included_components'] = [{'component': copy.deepcopy(pct['component']),
        'value': 0, 'nonextra_inclusion': 'unknown'}]
    put(data, pct)
    result = read(AttributeStatGraphLedger(data))
    assert result['status'] == 'invalid' and result['issues'][0]['code'] == 'attribute_duplicate_delivery'


def test_cross_output_stat_identity_does_not_hide_same_stat_duplicate_layers():
    data = model()
    atk = node('atk', 10)
    em = node('em', 20, stat='em', component=copy.deepcopy(atk['component']),
        instance_id=atk['instance_id'], layer_id=atk['layer_id'])
    duplicate = copy.deepcopy(atk)
    duplicate['id'] = 'atk-again'
    put(data, atk, em, duplicate)
    service = AttributeStatGraphLedger(data)
    assert read(service, stat='em')['value'] == 220
    assert read(service)['status'] == 'invalid'
    assert read(service)['issues'][0]['code'] == 'attribute_duplicate_delivery'


def test_prewarmed_input_and_base_receipts_do_not_bypass_longest_chain_depth():
    data = model()
    data['initials'].append({'actor': 'c', 'stat': 'atk', 'total': 20, 'base': 10,
                            'included_components': [], 'source': 'Synthetic c white value'})
    put(data, node('a-from-b', expr('b', 'atk'), percent=True),
        node('b-from-c', expr('c', 'atk'), recipient='b', percent=True),
        node('c-from-em', expr('a', 'em'), recipient='c', percent=True))
    service = AttributeStatGraphLedger(data)
    service._graph_limits['dependency_depth'] = 3
    assert read(service, actor='b')['status'] == 'known'
    assert read(service, actor='a', basis='base')['status'] == 'known'
    result = read(service)
    assert result['status'] == 'pending' and result['issues'][0]['code'] == 'attribute_dependency_depth'
    assert service.dependency_footprint('a', 'atk', 'total', 'f0')['status'] == 'pending'


def test_invalid_incremental_application_does_not_publish_frame_or_mutate_known_history():
    service = AttributeStatGraphLedger(model())
    original = read(service)
    bad = node('bad', 1)
    bad['application'] = {'kind': 'recipient_base_fraction'}  # flat/ratio mismatch
    with pytest.raises(AttributeLedgerError):
        service.adopt_frame(frame('new', 1, 1, ['bad']), contributions=[bad])
    assert read(service) == original
    assert read(service, fid='new')['status'] == 'pending'


def test_dependency_footprint_is_math_free_even_for_overflowing_percentage(monkeypatch):
    data = model()
    put(data, node('overflow', expr(factor=1e308), percent=True))
    service = AttributeStatGraphLedger(data)
    def no_math(*args, **kwargs):
        raise AssertionError('Footprint attempted a numerical stat read')
    monkeypatch.setattr(service, '_node_value', no_math)
    monkeypatch.setattr(service, 'read_stat', no_math)
    assert service.dependency_footprint('a', 'atk', 'total', 'f0')['status'] == 'ready'
