"""L2a mathematical acceptance examples; no game-specific source assertions."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_graph import AttributeGraphLedger
from tests.test_attribute_ledger import contribution


def frame(ident='f0', epoch=0, time=0, nodes=()):
    return {'id': ident, 'origin': 0, 'run_id': '0:'+ident, 'event_id': ident,
            'phase': 'pre', 'state_epoch': epoch, 't_us': time, 'contribution_ids': list(nodes)}


def model():
    return {'contract': 'attribute-ledger-v2',
            'initials': [{'actor': actor, 'stat': 'em', 'total': value, 'nonextra': value, 'base': 0,
                          'included_components': [], 'source': 'Synthetic basis'}
                         for actor, value in [('a', 100), ('b', 200), ('c', 10)]],
            'contributions': [], 'frames': [frame()]}


def expr(actor='b', basis='nonextra', sample='f0', factor=.2, **kw):
    return {'kind': 'linear_stat', 'input': {'actor': actor, 'stat': 'em', 'basis': basis, 'frame_id': sample},
            'factor': factor, **kw}


def node(ident, recipient='a', value=None, inclusion='excluded', **kw):
    return contribution(ident, value if value is not None else expr(), inclusion, recipient, **kw)


def put(data, *nodes, fid='f0'):
    data['contributions'].extend(nodes)
    next(f for f in data['frames'] if f['id'] == fid)['contribution_ids'].extend(n['id'] for n in nodes)


def read(service, actor='a', basis='total', fid='f0'):
    return service.read_stat(actor, 'em', basis, fid)


def test_mutual_nonextra_conversions_form_no_false_cycle():
    data = model()
    put(data, node('b_to_a'), node('a_to_b', 'b', expr('a')))
    service = AttributeGraphLedger(data)
    assert read(service)['value'] == 140
    assert read(service, 'b')['value'] == 220
    assert read(service, basis='nonextra')['value'] == 100
    assert read(service, 'b', 'nonextra')['value'] == 200


def test_mutual_current_total_returns_cycle_path_without_algebraic_guess():
    data = model()
    put(data, node('b_to_a', value=expr(basis='total')), node('a_to_b', 'b', expr('a', 'total')))
    receipt = read(AttributeGraphLedger(data))
    assert receipt['status'] == 'pending' and 'value' not in receipt
    assert receipt['issues'][0]['code'] == 'attribute_dependency_cycle'
    path = receipt['issues'][0]['dependency_path']
    assert {'a', 'b'} <= {p['actor'] for p in path}


def test_directed_chain_and_graph_order_produce_same_receipt():
    data = model()
    put(data, node('b_to_a', value=expr(basis='total')), node('a_to_c', 'c', expr('a', 'total')))
    first = read(AttributeGraphLedger(data), 'c')
    data['contributions'].reverse()
    data['frames'][0]['contribution_ids'].reverse()
    second = read(AttributeGraphLedger(data), 'c')
    assert first['value'] == 38
    assert first == second


def test_independent_historical_layers_do_not_use_latest_provider():
    data = model()
    data['initials'][1].update(total=0, nonextra=0)
    data['frames'].extend([frame('f1', 1, 1), frame('f2', 2, 2), frame('f3', 3, 3)])
    put(data, node('provider_gain', value=200, inclusion='included'), fid='f1')
    first = node('layer1', 'b', expr('a', sample='f0'), component=contribution('shared')['component'], layer_id='one')
    second = node('layer2', 'b', expr('a', sample='f1'), component=contribution('shared')['component'], layer_id='two')
    first['component']['recipient'] = second['component']['recipient'] = 'b'
    put(data, first, second, fid='f2')
    put(data, node('latest_gain', value=400, inclusion='included'), fid='f3')
    service = AttributeGraphLedger(data)
    assert read(service, 'a', fid='f3')['value'] == 500
    assert read(service, 'b', fid='f2')['value'] == 80


def test_nonextra_prunes_future_dependency_but_total_does_not():
    data = model()
    data['frames'].append(frame('future', 1, 0))
    put(data, node('future', value=expr(sample='future')))
    service = AttributeGraphLedger(data)
    assert read(service, basis='nonextra')['value'] == 100
    receipt = read(service)
    assert receipt['status'] == 'pending' and 'value' not in receipt
    assert receipt['issues'][0]['code'] == 'attribute_forward_frame'


def test_zero_factor_cannot_certify_unknown_classification():
    data = model()
    put(data, node('unknown', value=expr(factor=0), inclusion='unknown'))
    service = AttributeGraphLedger(data)
    assert read(service)['value'] == 100
    assert read(service, basis='nonextra')['issues'][0]['code'] == 'attribute_classification_unknown'


def test_unrelated_cycle_and_bad_context_do_not_block_closed_query():
    data = model()
    put(data, node('b_to_c', 'c', expr('b', 'total')), node('c_to_b', 'b', expr('c', 'total')))
    service = AttributeGraphLedger(data)
    assert read(service)['value'] == 100
    assert read(service, 'b')['status'] == 'pending'


@pytest.mark.parametrize('constraint', ['filter', 'quota', 'resource_gate', 'hit_match'])
def test_expression_does_not_bypass_context_authorization(constraint):
    data = model()
    put(data, node('limited', constraints=[constraint], visibility='damage_context'))
    service = AttributeGraphLedger(data)
    assert read(service)['issues'][0]['code'] == 'attribute_context_unsupported'
    assert read(service, basis='nonextra')['value'] == 100


def test_later_registration_of_earlier_frame_does_not_poison_parent_cache():
    data = model()
    data['frames'] = [frame('f_now', 2, 2)]
    put(data, node('historical', value=expr(sample='past')), fid='f_now')
    service = AttributeGraphLedger(data)
    first = read(service, fid='f_now')
    assert first['status'] == 'pending'
    service.adopt_frame(frame('past', 1, 1))
    second = read(service, fid='f_now')
    assert second['value'] == 140
    assert first['status'] == 'pending' and 'value' not in first


def test_caps_negative_contributions_and_intermediate_overflow():
    data = model()
    put(data, node('capped', value=expr(factor=.2, add=10, min=0, max=25)))
    assert read(AttributeGraphLedger(data))['value'] == 125
    data['contributions'][0]['value'] = expr(factor=-.2)
    assert read(AttributeGraphLedger(data))['value'] == 60
    data['initials'][1]['total'] = 1e308
    data['contributions'][0]['value'] = expr(basis='total', factor=1e308, max=25)
    result = read(AttributeGraphLedger(data))
    assert result['status'] == 'invalid' and 'value' not in result


def test_depth_limit_is_bounded_even_across_immutable_history():
    data = model()
    for i in range(1, 35):
        data['frames'].append(frame(f'f{i}', i, i))
        put(data, node(f'layer{i}', value=expr('a', 'total', f'f{i-1}', .1)), fid=f'f{i}')
    receipt = read(AttributeGraphLedger(data, limits={'dependency_depth':32}), fid='f34')
    assert receipt['status'] == 'pending' and 'value' not in receipt
    assert receipt['issues'][0]['code'] == 'attribute_dependency_depth'


def test_dependency_receipts_are_referenced_once_and_deep_copied():
    data = model()
    put(data, *[node(f'layer{i}', value=expr(), layer_id=f'layer{i}') for i in range(8)])
    service = AttributeGraphLedger(data)
    top = read(service)
    assert top['value'] == 420
    references = {r['input_receipt_id'] for r in top['contributions']}
    assert len(references) == 1
    catalog = service.receipt_catalog()
    original = copy.deepcopy(catalog)
    assert references <= {r['receipt_id'] for r in catalog}
    catalog.clear()
    assert service.receipt_catalog() == original


def test_warmed_subquery_cache_does_not_bypass_longest_chain_limit():
    data = model()
    for i in range(1, 34):
        data['frames'].append(frame(f'f{i}', i, i))
        put(data, node(f'layer{i}', value=expr('a', 'total', f'f{i-1}', .1)), fid=f'f{i}')
    service = AttributeGraphLedger(data, limits={'dependency_depth':32})
    for i in range(32):
        result = read(service, fid=f'f{i}')
        assert result['status'] == 'known'
        assert result['dependency_depth'] == i+1
    result = read(service, fid='f32')
    assert result['status'] == 'pending'
    assert result['issues'][0]['code'] == 'attribute_dependency_depth'


def test_distinct_same_epoch_frames_do_not_imply_causal_order():
    data = model()
    data['frames'].append({**frame('other'), 'phase': 'post'})
    put(data, node('ambiguous', value=expr(sample='other')))
    result = read(AttributeGraphLedger(data))
    assert result['status'] == 'pending'
    assert result['issues'][0]['code'] == 'attribute_frame_order_unknown'


def test_output_budget_bounds_repeated_cached_reads_and_catalog(monkeypatch):
    from genshin_engine.attribute_ledger import AttributeLedgerError
    limits = {'output_bytes': 1200}
    service = AttributeGraphLedger(model(), limits=limits)
    assert read(service)['status'] == 'known'
    for _ in range(5):
        result = read(service)
    assert result['status'] == 'pending' and 'value' not in result
    assert result['issues'][0]['code'] == 'attribute_output_limit'
    with pytest.raises(AttributeLedgerError, match='输出'):
        service.receipt_catalog()


def test_incremental_invalid_expression_is_atomic_and_spends_attempt_work():
    from genshin_engine.attribute_ledger import AttributeLedgerError
    service = AttributeGraphLedger(model())
    before = read(service)
    invalid = node('bad', value=expr(min=5, max=1))
    old_work = service.usage()['registration_work']
    with pytest.raises(AttributeLedgerError, match='下限'):
        service.adopt_frame(frame('f1', 1, 1, ['bad']), contributions=[invalid])
    assert service.usage()['registration_work'] > old_work
    assert 'bad' not in service._nodes and 'f1' not in service._frames
    assert read(service) == before


def test_v1_does_not_inherit_graph_expressions():
    from genshin_engine.attribute_ledger import AttributeLedger
    data = model()
    data['contract'] = 'attribute-ledger-v1'
    put(data, node('conversion'))
    result = read(AttributeLedger(data))
    assert result['issues'][0]['code'] == 'attribute_expression_unsupported'


def test_static_component_duplicate_is_rejected_before_converting_it():
    data = model()
    convert = node('conversion')
    put(data, convert)
    data['initials'][0]['included_components'] = [{'component': copy.deepcopy(convert['component']),
                                                 'value': 40, 'nonextra_inclusion': 'excluded'}]
    result = read(AttributeGraphLedger(data))
    assert result['status'] == 'invalid'
    assert result['issues'][0]['code'] == 'attribute_duplicate_delivery'
