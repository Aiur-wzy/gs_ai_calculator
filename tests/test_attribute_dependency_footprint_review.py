"""Independent qualification and dependency-footprint contract tests."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_graph import AttributeGraphLedger
from genshin_engine.attribute_ledger import AttributeLedgerError
from tests.test_attribute_graph import expr, frame, model, node, put, read


def scan(service, actor='a', basis='total', fid='f0'):
    return service.dependency_footprint(actor, 'em', basis, fid)


def test_scan_never_uses_numeric_read_or_node_evaluation(monkeypatch):
    data = model()
    put(data, node('linear', value=expr(factor=1e308), inclusion='included'))
    service = AttributeGraphLedger(data)
    monkeypatch.setattr(service, '_node_value', lambda *args: pytest.fail('Dependency scan evaluated a value'))
    monkeypatch.setattr(service, 'read_stat', lambda *args, **kwargs: pytest.fail('Dependency scan called numeric read'))
    receipt = scan(service)
    assert receipt['status'] == 'ready'
    assert receipt['current_dependencies'] == [{'actor': 'a', 'stat': 'em', 'basis': 'total'},
                                               {'actor': 'b', 'stat': 'em', 'basis': 'nonextra'}]


@pytest.mark.parametrize('basis', ['total', 'nonextra', 'base'])
def test_missing_requested_basis_keeps_math_reader_qualification_code(basis):
    data = model()
    if basis == 'total':
        data['initials'].pop(0)  # total is required on every declared seed.
    else:
        del data['initials'][0][basis]
    structure = scan(AttributeGraphLedger(data), basis=basis)
    numeric = read(AttributeGraphLedger(data), basis=basis)
    assert structure['status'] == 'pending'
    assert structure['issues'][0]['code'] == numeric['issues'][0]['code'] == 'attribute_basis_missing'
    assert 'current_dependencies' not in structure and structure['prefix_only']


def test_historical_nested_current_input_remains_historical_relative_to_root():
    data = model()
    put(data, node('old-b-to-c', recipient='c', value=expr('b'), inclusion='included'))
    data['frames'].append(frame('now', 2, 2))
    put(data, node('old-c-to-a', value=expr('c', basis='total', sample='f0'), inclusion='included'), fid='now')
    receipt = scan(AttributeGraphLedger(data), fid='now')
    assert receipt['status'] == 'ready'
    assert receipt['current_dependencies'] == [{'actor': 'a', 'stat': 'em', 'basis': 'total'}]
    assert len(receipt['queries']) == 3
    assert sum(q['relation_to_root'] == 'historical' for q in receipt['queries']) == 2


def test_excluded_node_is_pruned_after_static_identity_check_and_unrelated_actor_stays_closed():
    data = model()
    active = node('bad', value=expr(sample='unexecuted'), inclusion='excluded')
    put(data, active)
    data['initials'][0]['included_components'] = [{'component': copy.deepcopy(active['component']),
                                                   'value': 5, 'nonextra_inclusion': 'excluded'}]
    service = AttributeGraphLedger(data)
    assert scan(service, basis='nonextra')['issues'][0]['code'] == 'attribute_duplicate_delivery'
    assert scan(service, actor='b')['status'] == 'ready'
    assert scan(service, basis='base')['status'] == 'ready'


def test_missing_frame_recheck_does_not_depend_on_cached_numeric_pending():
    data = model()
    data['frames'][0].update(state_epoch=2, t_us=2)
    put(data, node('future-missing', value=expr('b', sample='old'), inclusion='included'))
    service = AttributeGraphLedger(data)
    assert read(service)['status'] == 'pending'
    assert scan(service)['status'] == 'pending'
    service.adopt_frame(frame('old', 1, 1))
    assert scan(service)['status'] == 'ready'
    assert read(service)['status'] == 'known'


def test_repeated_structural_queries_share_output_budget_with_numeric_exports():
    service = AttributeGraphLedger(model())
    numeric = read(service)
    first_bytes = service.usage()['output_bytes']
    receipt = scan(service)
    assert numeric['status'] == 'known' and receipt['status'] == 'ready'
    charged = service.usage()['output_bytes']
    assert charged > first_bytes > 0
    service._graph_limits['output_bytes'] = charged
    with pytest.raises(AttributeLedgerError) as raised:
        scan(service)
    assert raised.value.issue['code'] == 'attribute_output_limit'
    assert service.usage()['output_bytes'] == charged


@pytest.mark.parametrize('invalid', [None, [], 10**400, ' '])
def test_invalid_query_identity_returns_controlled_issue_not_python_error(invalid):
    receipt = scan(AttributeGraphLedger(model()), actor=invalid)
    assert receipt['status'] == 'invalid' and receipt['issues'][0]['code'] == 'attribute_query'
    assert 'current_dependencies' not in receipt


@pytest.mark.parametrize('basis,inclusion', [('total', 'included'), ('nonextra', 'excluded')])
def test_constant_source_identity_is_bound_even_without_dependency_edge(basis, inclusion):
    data = model()
    put(data, node('constant', value=50, inclusion=inclusion))
    original = scan(AttributeGraphLedger(data), basis=basis)
    assert original['edges'] == []
    data['contributions'][0]['component']['source_fingerprint'] = 'independently-changed-source'
    changed = scan(AttributeGraphLedger(data), basis=basis)
    assert original['current_dependencies'] == changed['current_dependencies']
    assert original['receipt_id'] != changed['receipt_id']


def test_qualified_membership_ignores_unrelated_actor_but_binds_actual_constant_value():
    data = model()
    put(data, node('own', value=50, inclusion='included'),
        node('other', recipient='c', value=75, inclusion='included'))
    original = scan(AttributeGraphLedger(data))
    data['contributions'][1]['value'] = 100
    assert scan(AttributeGraphLedger(data)) == original
    data['contributions'][0]['value'] = 60
    assert scan(AttributeGraphLedger(data))['receipt_id'] != original['receipt_id']


def test_base_identity_binds_requested_seed_without_scanning_dynamic_members():
    data = model()
    put(data, node('unrelated', value=expr(sample='not-executed'), inclusion='unknown'))
    original = scan(AttributeGraphLedger(data), basis='base')
    original_work = AttributeGraphLedger(model())
    scan(original_work, basis='base')
    dynamic_work = AttributeGraphLedger(data)
    assert scan(dynamic_work, basis='base') == original
    assert dynamic_work.usage()['query_work'] == original_work.usage()['query_work']
    data['contributions'][0]['component']['source_fingerprint'] = 'changed-dynamic'
    data['initials'][1]['base'] = 10
    assert scan(AttributeGraphLedger(data), basis='base') == original
    data['initials'][0]['base'] = 1
    assert scan(AttributeGraphLedger(data), basis='base')['receipt_id'] != original['receipt_id']


def test_permuted_static_membership_and_active_rows_keep_same_fingerprint():
    data = model()
    put(data, node('own1', value=1, inclusion='included'), node('own2', value=2, inclusion='included'))
    original = scan(AttributeGraphLedger(data))
    data['frames'][0]['contribution_ids'].reverse()
    data['contributions'].reverse()
    assert scan(AttributeGraphLedger(data)) == original
