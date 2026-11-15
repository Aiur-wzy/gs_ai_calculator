"""Pure v4 units, source qualification and exact dependency path tests."""
import copy

import pytest

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.attribute_multistat_graph import AttributeMultiStatGraphLedger
from genshin_engine.attribute_stat_graph import AttributeStatGraphLedger
from tests.test_attribute_graph import frame
from tests.test_attribute_stat_graph import expr, node, put


def model():
    return {'contract': 'attribute-ledger-v4', 'initials': [
        {'actor': actor, 'stat': stat, 'total': total, 'base': base, 'nonextra': total,
         'included_components': [], 'source': 'Synthetic immutable five-stat baseline'}
        for actor in ('a', 'b') for stat, total, base in (
            ('atk', 100, 50), ('hp', 30000, 15000), ('def', 1000, 500), ('em', 200, 0), ('energy_recharge', 1, 0))],
        'contributions': [], 'frames': [frame()]}


@pytest.mark.parametrize('stat,expected', [('atk', 110), ('hp', 33000), ('def', 1100)])
def test_percentage_on_resolved_recipient_base_not_source_or_total(stat, expected):
    data = model()
    put(data, node('percent', .2, stat=stat, percent=True))
    receipt = AttributeMultiStatGraphLedger(data).read_stat('a', stat, 'total', 'f0')
    assert receipt['status'] == 'known' and receipt['value'] == expected


def test_er_ratio_flat_is_not_a_percentage_application():
    data = model()
    gain = node('er', .2, stat='energy_recharge')
    gain['value_unit'] = 'ratio'
    put(data, gain)
    assert AttributeMultiStatGraphLedger(data).read_stat('a', 'energy_recharge', 'total', 'f0')['value'] == 1.2
    data['contributions'][0]['application']['kind'] = 'recipient_base_fraction'
    with pytest.raises(AttributeLedgerError, match='属性输出单位'):
        AttributeMultiStatGraphLedger(data)


def test_def_em_atk_chain_is_allowed_and_returned_footprint_is_transitive():
    data = model()
    put(data, node('def_em', expr(stat='def', factor=.1), stat='em'),
        node('em_atk', expr(stat='em', factor=.5), stat='atk'))
    service = AttributeMultiStatGraphLedger(data)
    assert service.read_stat('a', 'atk', 'total', 'f0')['value'] == 250
    footprint = service.dependency_footprint('a', 'atk', 'total', 'f0')
    assert footprint['status'] == 'ready'
    assert {'atk', 'em', 'def'} <= {d['stat'] for d in footprint['current_dependencies']}


def test_percentage_base_is_a_real_dependency_and_missing_base_does_not_use_total():
    data = model()
    put(data, node('hp', .4, stat='hp', percent=True))
    service = AttributeMultiStatGraphLedger(data)
    footprint = service.dependency_footprint('a', 'hp', 'total', 'f0')
    assert footprint['status'] == 'ready'
    assert any(edge.get('role') == 'application_base' for edge in footprint['edges'])
    del data['initials'][1]['base']
    missing = AttributeMultiStatGraphLedger(data).read_stat('a', 'hp', 'total', 'f0')
    assert missing['issues'][0]['code'] == 'attribute_basis_missing'


def test_v4_does_not_expand_v3_stats_or_nonextra_semantics():
    data = model()
    old = copy.deepcopy(data)
    old['contract'] = 'attribute-ledger-v3'
    with pytest.raises(AttributeLedgerError):
        AttributeStatGraphLedger(old)


def test_finite_input_can_overflow_only_the_consumed_closure():
    data = model()
    put(data, node('large1', 1e308, stat='hp', recipient='b'), node('large2', 1e308, stat='hp', recipient='b'))
    service = AttributeMultiStatGraphLedger(data)
    assert service.read_stat('a', 'atk', 'total', 'f0')['value'] == 100
    assert service.read_stat('b', 'hp', 'total', 'f0')['issues'][0]['code'] == 'attribute_value_invalid'
