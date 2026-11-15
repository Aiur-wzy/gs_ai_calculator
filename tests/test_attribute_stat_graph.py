"""Pure opt-in multi-stat acceptance; no runtime/source authority."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.attribute_stat_graph import AttributeStatGraphLedger
from tests.test_attribute_graph import frame
from tests.test_attribute_ledger import contribution


def model():
    return {'contract': 'attribute-ledger-v3', 'initials': [
        {'actor': actor, 'stat': stat, 'total': total, 'base': base, 'included_components': [],
         'source': 'Synthetic immutable initial', **({'nonextra': total} if stat == 'em' else {})}
        for actor, stat, total, base in [('a', 'atk', 100, 50), ('a', 'em', 200, 0),
                                        ('b', 'atk', 300, 80), ('b', 'em', 20, 0)]],
        'contributions': [], 'frames': [frame()]}


def expr(actor='a', stat='em', basis='total', factor=.5, sample='f0', **kw):
    return {'kind': 'linear_stat', 'input': {'actor': actor, 'stat': stat, 'basis': basis, 'frame_id': sample},
            'factor': factor, **kw}


def node(ident, value, stat='atk', recipient='a', *, percent=False, **kw):
    return {**contribution(ident, value, 'unknown' if stat == 'atk' else 'included', recipient, **kw),
            'stat': stat, 'value_unit': 'ratio' if percent else 'flat',
            'application': {'kind': 'recipient_base_fraction' if percent else 'flat'}}


def put(data, *nodes, fid='f0'):
    data['contributions'].extend(nodes)
    next(f for f in data['frames'] if f['id'] == fid)['contribution_ids'].extend(n['id'] for n in nodes)


def read(service, actor='a', stat='atk', basis='total', fid='f0'):
    return service.read_stat(actor, stat, basis, fid)


def test_cross_stat_chain_and_unrelated_cycle():
    data = model()
    put(data, node('to_atk', expr()), node('to_em', expr(stat='atk', factor=.2), 'em', 'b'))
    service = AttributeStatGraphLedger(data)
    assert read(service)['value'] == 200
    assert read(service, 'b', 'em')['value'] == 60
    data = model()
    put(data, node('unrelated', expr('b', 'atk'), recipient='b'))
    service = AttributeStatGraphLedger(data)
    assert read(service)['value'] == 100
    assert read(service, 'b')['issues'][0]['code'] == 'attribute_dependency_cycle'


@pytest.mark.parametrize('factor', [0, .5])
def test_cross_stat_current_cycle_cannot_be_solved_by_zero_or_cap(factor):
    data = model()
    put(data, node('atk', expr(factor=factor, max=0)), node('em', expr(stat='atk'), 'em'))
    result = read(AttributeStatGraphLedger(data))
    assert result['status'] == 'pending' and 'value' not in result
    assert result['issues'][0]['code'] == 'attribute_dependency_cycle'


def test_percentage_clamps_ratio_then_multiplies_recipient_base_with_two_receipts():
    data = model()
    put(data, node('pct', expr(factor=.002, max=.3), percent=True))
    service = AttributeStatGraphLedger(data)
    result = read(service)
    assert result['value'] == 115
    row = result['contributions'][0]
    assert row['raw_evaluated_value'] == .3 and row['application_base_value'] == 50
    assert row['evaluated_value'] == 15
    assert row['input_receipt_id'] != row['application_base_receipt_id']
    ids = {r['receipt_id'] for r in service.receipt_catalog()}
    assert {row['input_receipt_id'], row['application_base_receipt_id']} <= ids


def test_constant_percentage_and_missing_base_do_not_use_total():
    data = model()
    put(data, node('pct', .2, percent=True))
    assert read(AttributeStatGraphLedger(data))['value'] == 110
    del data['initials'][0]['base']
    result = read(AttributeStatGraphLedger(data))
    assert result['issues'][0]['code'] == 'attribute_basis_missing'
    data['contributions'][0].update(value_unit='flat', application={'kind': 'flat'})
    assert read(AttributeStatGraphLedger(data))['value'] == 100.2


def test_zero_base_does_not_hide_nonfinite_ratio_intermediate():
    data = model()
    data['initials'][0]['base'] = 0
    put(data, node('pct', expr(factor=1e308, max=1), percent=True))
    result = read(AttributeStatGraphLedger(data))
    assert result['status'] == 'invalid' and 'value' not in result


@pytest.mark.parametrize('edit', [
    lambda d: d['contributions'][0].update(value_unit='ratio'),
    lambda d: d['contributions'][0].update(stat='em', application={'kind': 'recipient_base_fraction'}),
    lambda d: d['initials'][0].update(nonextra=100),
    lambda d: d['contributions'][0].update(stat='hp'),
    lambda d: d['contributions'][0].update(value=True),
    lambda d: d['contributions'][0].update(value=float('nan')),
    lambda d: d['contributions'][0]['application'].update(base=999),
    lambda d: d['contributions'][0].update(projection_of={'contribution_id': 'other'}),
])
def test_closed_units_stats_and_ordinary_only_schema(edit):
    data = model()
    put(data, node('x', 10))
    edit(data)
    with pytest.raises(AttributeLedgerError):
        AttributeStatGraphLedger(data)


def test_nonextra_atk_is_not_total_and_base_prunes_dynamic_unknown():
    data = model()
    put(data, node('bad', expr('b', 'atk'), constraints=['quota']))
    service = AttributeStatGraphLedger(data)
    assert read(service, basis='nonextra')['status'] == 'pending'
    assert read(service, basis='base')['value'] == 50


def test_static_same_stat_duplicate_rejected_but_distinct_stat_delivery_is_independent():
    data = model()
    atk = node('atk', 20)
    em = node('em', 30, stat='em', component=copy.deepcopy(atk['component']),
              instance_id=atk['instance_id'], layer_id=atk['layer_id'])
    put(data, atk, em)
    service = AttributeStatGraphLedger(data)
    assert read(service)['value'] == 120
    assert read(service, stat='em')['value'] == 230
    data['initials'][0]['included_components'].append({'component': copy.deepcopy(atk['component']),
        'value': 20, 'nonextra_inclusion': 'unknown'})
    service = AttributeStatGraphLedger(data)
    assert read(service)['issues'][0]['code'] == 'attribute_duplicate_delivery'
    assert read(service, stat='em')['value'] == 230


def test_historical_em_layers_and_percentage_use_distinct_bases():
    data = model()
    data['frames'].append(frame('later', 1, 1))
    put(data, node('emchange', 100, stat='em'), fid='later')
    put(data, node('oldpct', expr(factor=.001), percent=True), fid='later')
    result = read(AttributeStatGraphLedger(data), fid='later')
    assert result['value'] == 110  # Old EM200 ratio, not current EM300.


@pytest.mark.parametrize('value', [.2, expr(factor=.001)])
def test_footprint_includes_application_base_without_evaluating(value):
    data = model()
    put(data, node('pct', value, percent=True))
    service = AttributeStatGraphLedger(data)
    result = service.dependency_footprint('a', 'atk', 'total', 'f0')
    assert result['status'] == 'ready'
    assert {e['role'] for e in result['edges']} == ({'application_base', 'value_input'} if isinstance(value, dict) else {'application_base'})
    assert {'actor': 'a', 'stat': 'atk', 'basis': 'base'} not in result['current_dependencies']
    assert not service.receipt_catalog()


def test_order_independent_deepcopy_and_shared_work_budget():
    data = model()
    put(data, node('pct', .2, percent=True), node('flat', 20))
    first = AttributeStatGraphLedger(data)
    expected = read(first)
    data['contributions'].reverse()
    data['frames'][0]['contribution_ids'].reverse()
    assert read(AttributeStatGraphLedger(data)) == expected
    expected['value'] = 0
    assert read(first)['value'] == 130
    first._limits['execution_work'] = first.usage()['execution_work']
    with pytest.raises(AttributeLedgerError, match='累计'):
        first.dependency_footprint('a', 'atk', 'total', 'f0')
