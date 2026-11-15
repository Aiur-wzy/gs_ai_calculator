"""Object-protocol tests; actual engine wiring remains separately required."""
import copy

import pytest

from genshin_engine.resource_observation_authority import (
    ObservationAuthority,
    ObservationError,
)


def queue(kind='mechanics_condition'):
    return {'id': 'output', 'run_id': '0:output', 'origin': 0, 'time': 150000,
            'kind': kind, 'order': 5, 'causal': [], 'data': {}}


def semantic():
    return {'id': 'output', 'run_id': '0:output', 'origin': 0, 'event': 'condition',
            'conditions': {'ready': False}}


def setup():
    ledger = object()
    auth = ObservationAuthority(ledger)
    event, observed = queue(), semantic()
    auth.begin(event)
    auth.bind(observed)
    return auth, ledger, event, observed


def test_original_active_semantic_object_issues_once_and_publishes_exact_context():
    auth, ledger, event, observed = setup()
    permit = auth.issue(observed)
    work = []
    context = auth.publish(permit, ledger, 4, work.append)
    assert context == {'origin': 0, 'run_id': '0:output', 'event_id': 'output', 'time_us': 150000}
    assert work == [4]
    with pytest.raises(ObservationError):
        auth.publish(permit, ledger, 4, work.append)
    with pytest.raises(ObservationError):
        auth.issue(observed)
    auth.end(event)


def test_same_content_semantic_copy_has_no_observation_authority():
    auth, _, _, observed = setup()
    with pytest.raises(ObservationError):
        auth.issue(copy.deepcopy(observed))


def test_only_original_action_owned_capture_queue_object_is_permitted():
    ledger, event = object(), queue('mechanics_snapshot_capture')
    auth = ObservationAuthority(ledger)
    auth.begin(event)
    with pytest.raises(ObservationError):
        auth.issue(copy.deepcopy(event))
    permit = auth.issue(event)
    assert auth.publish(permit, ledger, 2, lambda n: None)['event_id'] == event['id']
    auth.end(event)


@pytest.mark.parametrize('kind', ['state_runtime_event', 'mechanics_activate', 'elemental_state_deadline'])
def test_internal_queue_objects_cannot_be_rebranded_as_native_observations(kind):
    auth = ObservationAuthority(object())
    event = queue(kind)
    auth.begin(event)
    with pytest.raises(ObservationError):
        auth.bind(semantic())
    with pytest.raises(ObservationError):
        auth.issue(event)
    auth.end(event)


def test_permit_cannot_move_between_authorities_or_ledger_objects():
    auth, ledger, _, observed = setup()
    permit = auth.issue(observed)
    second, _, _, _ = setup()
    with pytest.raises(ObservationError):
        second.publish(permit, ledger, 2, lambda n: None)
    with pytest.raises(ObservationError):
        auth.publish(permit, object(), 2, lambda n: None)
    assert auth.publish(permit, ledger, 2, lambda n: None)['run_id'] == '0:output'


@pytest.mark.parametrize('target', ['queue', 'semantic'])
def test_identity_mutation_before_publication_is_rejected_without_consuming_permit(target):
    auth, ledger, event, observed = setup()
    permit = auth.issue(observed)
    item = event if target == 'queue' else observed
    item['id'] = 'changed'
    with pytest.raises(ObservationError):
        auth.publish(permit, ledger, 2, lambda n: None)
    item['id'] = 'output'
    assert auth.publish(permit, ledger, 2, lambda n: None)['event_id'] == 'output'


def test_budget_failure_retries_same_opaque_permit_and_does_not_spend_authority():
    auth, ledger, _, observed = setup()
    permit = auth.issue(observed)
    def failed(_):
        raise RuntimeError('budget')
    with pytest.raises(RuntimeError, match='budget'):
        auth.publish(permit, ledger, 2, failed)
    assert auth.publish(permit, ledger, 2, lambda n: None)['time_us'] == 150000


def test_reserve_cannot_reenter_and_outer_publication_is_single():
    auth, ledger, _, observed = setup()
    permit = auth.issue(observed)
    calls = []
    def reserve(work):
        calls.append(work)
        with pytest.raises(ObservationError):
            auth.publish(permit, ledger, work, calls.append)
    auth.publish(permit, ledger, 3, reserve)
    assert calls == [3]


def test_end_closes_unpublished_permits_and_event_copy_cannot_close_real_occurrence():
    auth, ledger, event, observed = setup()
    permit = auth.issue(observed)
    with pytest.raises(ObservationError):
        auth.end(copy.deepcopy(event))
    auth.end(event)
    with pytest.raises(ObservationError):
        auth.publish(permit, ledger, 3, lambda n: None)


@pytest.mark.parametrize('field,value', [('origin', True), ('time', False), ('run_id', []), ('id', '')])
def test_strict_queue_identity(field, value):
    auth, event = ObservationAuthority(object()), queue()
    event[field] = value
    with pytest.raises(ObservationError):
        auth.begin(event)
