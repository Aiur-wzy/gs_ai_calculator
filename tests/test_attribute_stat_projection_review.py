"""Independent A2a pure projection checks; no source authority."""
from __future__ import annotations

import copy

import pytest

from genshin_engine.attribute_ledger import AttributeLedgerError
from genshin_engine.attribute_stat_projection import AttributeStatProjectionLedger
from tests.test_attribute_graph import frame
from tests.test_attribute_stat_graph import expr, node, put
from tests.test_attribute_stat_projection import fixture, footprint, read


@pytest.mark.parametrize('method', [read, footprint])
def test_invalid_original_member_is_rejected_before_any_base_routing(monkeypatch, method):
    data = fixture()
    data['frames'][0]['contribution_ids'] = []
    service = AttributeStatProjectionLedger(data)
    def forbidden(*args, **kwargs):
        raise AssertionError('Unqualified projection attempted recipient BASE route')
    monkeypatch.setattr(service, '_application_base', forbidden)
    result = method(service)
    assert result['status'] == 'invalid'
    assert result['issues'][0]['code'] == 'attribute_projection_source'


def test_projection_query_actor_and_basis_do_not_authorize_other_recipient_base():
    service = AttributeStatProjectionLedger(fixture())
    for query in [('a', 'atk', 'base'), ('b', 'atk', 'total')]:
        for method in (service.read_stat, service.dependency_footprint):
            result = method(*query, 'damage', purpose='damage_projection')
            assert result['status'] == 'pending'
            assert result['issues'][0]['code'] == 'attribute_projection_context'
    assert read(service)['value'] == 115


def test_event_same_epoch_is_not_proven_executed_before_damage():
    data = fixture()
    data['frames'][1]['t_us'] = 3
    data['frames'][1]['state_epoch'] = 3
    service = AttributeStatProjectionLedger(data)
    for method in (read, footprint):
        result = method(service)
        assert result['status'] == 'pending'
        assert result['issues'][0]['code'] == 'attribute_frame_order_unknown'


def test_forward_qualification_frame_is_not_repaired_by_immutable_base():
    data = fixture()
    data['frames'][0]['t_us'] = data['frames'][0]['state_epoch'] = 4
    service = AttributeStatProjectionLedger(data)
    for method in (read, footprint):
        result = method(service)
        assert result['status'] == 'pending' and result['issues'][0]['code'] == 'attribute_forward_frame'


def test_cross_stat_expression_cannot_consume_damage_projection_frame():
    data = fixture()
    data['frames'].append(frame('later', 4, 4))
    put(data, node('read-atk-projection', expr(stat='atk', sample='damage'), stat='em', recipient='b'), fid='later')
    service = AttributeStatProjectionLedger(data)
    assert read(service)['value'] == 115
    result = service.read_stat('b', 'em', 'total', 'later')
    assert result['status'] == 'pending' and result['issues'][0]['code'] == 'attribute_projection_dependency'


def test_failed_adoption_and_external_declaration_mutation_preserve_projection_history():
    data = fixture()
    service = AttributeStatProjectionLedger(data)
    prior = read(service)
    data['initials'][0]['base'] = 999
    data['contributions'][1]['component']['recipient'] = 'b'
    assert read(service) == prior
    bad = copy.deepcopy(data['frames'][-1])
    bad.update(id='new-damage', contribution_ids=['missing'])
    with pytest.raises(AttributeLedgerError):
        service.adopt_frame(bad)
    assert read(service) == prior
