"""Independent private replay-session compatibility/identity tests."""
from __future__ import annotations

import copy
import threading
from types import SimpleNamespace

import pytest

from genshin_app.equipment import execution_verification as candidate
from genshin_engine import mechanics
from tests.test_mechanics_execution import add, fixture, rule


def material(delayed=False):
    template, rotation, model = fixture()
    if delayed:
        writer = rule()
        writer['timing']['delay'] = 12
        add(model, writer)
    return {'template': template, 'rotation_model': rotation, 'mechanics_model': model, 'matches': None}


def tuples(value):
    if type(value) is tuple:
        return True
    if type(value) is dict:
        return any(tuples(child) for child in value.values())
    if type(value) is list:
        return any(tuples(child) for child in value)
    return False


def numerical_engine_output(value):
    # This private proof cache is not a complete-artifact reader. Keep the
    # independent real-engine oracle and remove only the explicitly separated
    # optional observation family; all tuples, transitions and numerical data
    # continue to participate in exact equality.
    value = copy.deepcopy(value)
    result = value['result']
    for key in ('damage_explanations', 'damage_explanation_fingerprint', 'damage_explanation_contract'):
        result.pop(key, None)
    for hit in result['hits']:
        hit.pop('explanation_ref', None)
    return value


def test_actual_engine_native_pending_tail_tuple_is_preserved():
    value = material(delayed=True)
    expected = mechanics.execute_mechanics(**value)
    assert tuples(expected), 'real legacy boundary must actually contain native tuple'
    assert expected['result'] is not None
    with candidate.ExecutionVerificationSession() as session:
        actual = session.replay(value)
        assert actual == numerical_engine_output(expected) and tuples(actual)
        assert session.replay(copy.deepcopy(value)) == numerical_engine_output(expected)
        assert 'damage_explanations' in expected['result'], 'real engine output remains complete and unmodified'
        assert session.report()['usage']['executions'] == 1


def test_numeric_thread_id_reuse_does_not_authorize_another_thread(monkeypatch):
    # Deterministically simulate an OS-recycled numeric ID, while keeping real
    # Python thread objects separate. Do not replace global threading internals.
    monkeypatch.setattr(candidate, 'threading', SimpleNamespace(
        get_ident=lambda: 23, current_thread=threading.current_thread))
    session = candidate.ExecutionVerificationSession()
    errors, returned = [], []

    def other_thread():
        try:
            returned.append(session.report())
        except candidate.ExecutionVerificationError as exc:
            errors.append(exc)

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join()
    assert len(errors) == 1 and not returned
    assert session.report()['usage']['requests'] == 0


def test_real_same_input_replay_is_detached_and_calls_engine_once(monkeypatch):
    original = mechanics.execute_mechanics
    calls = []

    def count(**value):
        calls.append(value)
        return original(**value)

    monkeypatch.setattr(mechanics, 'execute_mechanics', count)
    value = material()
    expected = original(**value)
    with candidate.ExecutionVerificationSession() as session:
        first = session.replay(value)
        first['result']['hits'][0]['expected'] = -100
        second = session.replay(copy.deepcopy(value))
        assert second == numerical_engine_output(expected) and len(calls) == 1
        assert 'damage_explanations' in expected['result']


def test_optional_matches_material_is_not_silently_normalized(monkeypatch):
    original = mechanics.execute_mechanics
    calls = []

    def count(**value):
        calls.append(value['matches'])
        return original(**value)

    monkeypatch.setattr(mechanics, 'execute_mechanics', count)
    with candidate.ExecutionVerificationSession() as session:
        value = material()
        session.replay(value)
        value['matches'] = []
        session.replay(value)
    assert calls == [None, []]


def test_source_review_result_is_not_a_session_cache_verdict():
    # The service returns only actual engineering output. It has no acceptance
    # registry or adoption argument, even when the engine bytes are identical.
    with candidate.ExecutionVerificationSession() as session:
        value = material()
        actual = session.replay(value)
        assert session.report()['scope'] == 'same_operation_execution_replay_only_no_source_authority'
        value['source_accepted'] = True
        with pytest.raises(candidate.ExecutionVerificationError):
            session.replay(value)
        assert actual['result'] is not None
