"""Independent pure-store boundary checks; no actual queue/source authority claim."""
import copy

import pytest

from genshin_engine.resource_counter_frames import FrameError, FrameStore
from tests.test_counter_frames import capture, fixture, read
from tests.test_resource_counter_views import setup


def test_read_requires_declared_consumer_id():
    data = fixture()
    capture(data)
    store, _, contract, _, snapshot, _, members = data
    before = store.usage()
    with pytest.raises(FrameError):
        store.read(snapshot, members[0], {'run_id': '0:unrelated', 'event_id': 'unrelated',
            'origin': 0, 'time_us': 500000, 'event': 'hit', 'damage_system': 'classic'},
            layers=1, current_contract=contract, reserve=lambda _: None)
    assert store.usage() == before


@pytest.mark.parametrize('beneficiaries', ['a', {'a': True}])
def test_malformed_beneficiaries_are_not_accepted_as_membership(beneficiaries):
    data = fixture()
    data[4]['state']['gain']['instances'][0]['beneficiaries'] = beneficiaries
    with pytest.raises(FrameError):
        capture(data)
    assert data[0].usage()['frames'] == 0


@pytest.mark.parametrize('field,value', [('rules', None), ('consumers', None)])
def test_contract_bad_container_is_controlled(field, value):
    _, views, contract, _, _, _, _ = fixture()
    _, program, _ = setup()
    contract[field] = value
    with pytest.raises(FrameError):
        FrameStore(views, program, contract)


def test_capture_token_cannot_commit_twice_through_reserve_reentry():
    data = fixture()
    store, _, contract, state, snapshot, context, members = data
    token = store.prepare(context, snapshot, state, members, current_contract=contract)
    calls = []

    def reserve(work):
        calls.append(work)
        with pytest.raises(FrameError):
            store.commit(token, lambda _: None)

    store.commit(token, reserve)
    assert calls == [3]
    assert store.usage()['frames'] == 1
    assert store.usage()['members'] == 1
    assert store.usage()['capture_work'] == 3


def test_read_cannot_publish_twice_through_reserve_reentry():
    data = fixture()
    capture(data)
    calls = []

    def reserve(work):
        calls.append(work)
        with pytest.raises(FrameError):
            read(data)

    read(data, reserve=reserve)
    assert calls == [1]
    assert data[0].usage()['reads'] == 1
    assert data[0].usage()['read_work'] == 1


def test_returned_receipt_mutation_cannot_change_stored_read():
    data = fixture()
    capture(data)
    receipt = read(data)
    receipt['reading']['raw_atoms'] = 0
    assert read(data)['reading']['raw_atoms'] == 300


def test_snapshot_late_mutation_is_rejected_without_read_charge():
    data = fixture()
    capture(data)
    before = copy.deepcopy(data[0].usage())
    data[4]['frontline'] = 'b'
    with pytest.raises(FrameError):
        read(data)
    assert data[0].usage() == before


@pytest.mark.parametrize('field,value', [('view_id', []), ('factor', []), ('benefit_type_id', [])])
def test_reader_bad_typed_field_raises_controlled_error(field, value):
    _, views, contract, state, snapshot, context, members = fixture()
    _, program, _ = setup()
    if field == 'benefit_type_id':
        contract['rules'][0]['effects'][0][field] = value
    else:
        contract['rules'][0]['effects'][0]['value'][field] = value
    with pytest.raises(FrameError):
        store = FrameStore(views, program, contract)
        token = store.prepare(context, snapshot, state, members, current_contract=contract)
        store.commit(token, lambda _: None)
        store.read(snapshot, members[0], {'run_id': '0:later', 'event_id': 'later',
            'origin': 0, 'time_us': 500000, 'event': 'hit', 'damage_system': 'classic'},
            layers=1, current_contract=contract, reserve=lambda _: None)
